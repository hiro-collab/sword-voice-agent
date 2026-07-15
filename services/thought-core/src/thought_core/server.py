"""Dependency-free HTTP/SSE server for the thought-core experiment."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .event_journal import journal_from_env
from .execution_deadline import (
    TURN_DEADLINE_EXCEEDED,
    TURN_DEADLINE_INVALID,
    TurnDeadlineExceeded,
    TurnDeadlineInvalid,
    TurnExecutionDeadline,
    issue_turn_execution_deadline,
)
from .loop import ThoughtLoop
from .provenance_diagnostics import build_child_provenance_diagnostics
from .schema import TurnInput

DEFAULT_MAX_BODY_BYTES = 64 * 1024
ROUTE_DEADLINE_HEADER = "X-Sword-Route-Deadline-Monotonic"
MAX_ROUTE_DEADLINE_SECONDS = 10.0
MAX_ACCEPTED_CANDIDATE_RESERVATIONS = 4096

_ACCEPTED_SPEECH_ENVELOPE_KEYS = {
    "accepted_user_speech_candidate",
    "private_turn",
}
_OPAQUE_CONVERSATION_ATTEMPT_REF = re.compile(
    r"^m4\.prepared_sample_attempt:[0-9a-f]{32}$"
)
_ACCEPTED_CANDIDATE_REF = re.compile(r"^ausc_[A-Za-z0-9_.:-]{1,115}$")


class RequestBodyTooLarge(ValueError):
    pass


class TurnRequestRejected(ValueError):
    def __init__(self, result_class: str, status: HTTPStatus) -> None:
        super().__init__(result_class)
        self.result_class = result_class
        self.status = status


class _AcceptedCandidateRegistry:
    """Bounded server-lifetime replay authority without retaining raw ids."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._fingerprints: set[bytes] = set()
        self._lock = threading.Lock()

    def reserve(self, turn: TurnInput | Mapping[str, Any]) -> None:
        if not isinstance(turn, TurnInput):
            return
        candidate_ref = turn.context_refs.get(
            "accepted_user_speech_candidate_ref"
        )
        if (
            not isinstance(candidate_ref, str)
            or _ACCEPTED_CANDIDATE_REF.fullmatch(candidate_ref) is None
        ):
            raise TurnRequestRejected(
                "accepted_candidate_invalid",
                HTTPStatus.BAD_REQUEST,
            )
        fingerprint = hashlib.blake2s(
            candidate_ref.encode("utf-8"),
            key=self._key,
            digest_size=16,
        ).digest()
        candidate_ref = ""
        with self._lock:
            if fingerprint in self._fingerprints:
                raise TurnRequestRejected(
                    "accepted_candidate_duplicate",
                    HTTPStatus.CONFLICT,
                )
            if len(self._fingerprints) >= MAX_ACCEPTED_CANDIDATE_RESERVATIONS:
                raise TurnRequestRejected(
                    "accepted_candidate_registry_full",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            self._fingerprints.add(fingerprint)


def materialize_turn_input(payload: Mapping[str, Any]) -> TurnInput | Mapping[str, Any]:
    """Materialize the one allowed accepted-speech envelope before the loop."""

    candidate = payload.get("accepted_user_speech_candidate")
    private_turn = payload.get("private_turn")
    if candidate is None:
        if private_turn is not None:
            raise ValueError("private_turn requires accepted_user_speech_candidate")
        return payload

    unexpected = set(payload) - _ACCEPTED_SPEECH_ENVELOPE_KEYS
    if unexpected:
        raise ValueError("accepted speech envelope contains unexpected fields")
    if not isinstance(candidate, Mapping):
        raise ValueError("accepted_user_speech_candidate must be an object")
    if not isinstance(private_turn, Mapping):
        raise ValueError("private_turn must be an object")
    return TurnInput.from_accepted_speech_candidate(candidate, private_turn)


def _decorate_correlated_event_with_conversation_attempt_ref(
    event: dict[str, Any],
    turn: TurnInput | Mapping[str, Any],
) -> dict[str, Any]:
    """Decorate correlated events from the authoritative materialized turn."""
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return event

    is_assistant_event = event_type.startswith("assistant.")
    is_motion_request = event_type == "motion.requested"
    if not is_assistant_event and not is_motion_request:
        return event

    ref: str | None = None
    if isinstance(turn, TurnInput):
        candidate_ref = turn.context_refs.get("conversation_attempt_ref")
        if _is_opaque_conversation_attempt_ref(candidate_ref):
            ref = candidate_ref

    data = event.get("data")
    if is_assistant_event:
        decorated = dict(event)
        decorated.pop("conversation_attempt_ref", None)
        if not isinstance(data, dict):
            return decorated
        decorated_data = dict(data)
        decorated_data.pop("conversation_attempt_ref", None)
        if ref is not None:
            decorated_data["conversation_attempt_ref"] = ref
        decorated["data"] = decorated_data
        return decorated

    decorated = dict(event)
    decorated.pop("conversation_attempt_ref", None)
    if isinstance(data, dict):
        decorated_data = dict(data)
        decorated_data.pop("conversation_attempt_ref", None)
        decorated["data"] = decorated_data
    if ref is not None:
        decorated["conversation_attempt_ref"] = ref
    return decorated


def _is_opaque_conversation_attempt_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and _OPAQUE_CONVERSATION_ATTEMPT_REF.fullmatch(value) is not None
    )


def _parse_route_deadline_header(value: str | None) -> float | None:
    if value is None:
        # Compatibility: the current ai-talk-core two-argument private-turn sink
        # cannot yet forward a deadline. Remove absence support after that named
        # consumer always sends ROUTE_DEADLINE_HEADER.
        return None
    try:
        deadline_monotonic = float(value)
    except (TypeError, ValueError) as exc:
        raise TurnRequestRejected(
            "turn_deadline_invalid",
            HTTPStatus.BAD_REQUEST,
        ) from exc
    if not math.isfinite(deadline_monotonic):
        raise TurnRequestRejected(
            "turn_deadline_invalid",
            HTTPStatus.BAD_REQUEST,
        )
    remaining_s = deadline_monotonic - time.monotonic()
    if remaining_s <= 0:
        raise TurnRequestRejected(
            "turn_deadline_expired",
            HTTPStatus.REQUEST_TIMEOUT,
        )
    if remaining_s > MAX_ROUTE_DEADLINE_SECONDS:
        raise TurnRequestRejected(
            "turn_deadline_invalid",
            HTTPStatus.BAD_REQUEST,
        )
    return deadline_monotonic


def _ensure_route_deadline_current(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is None:
        return
    if deadline_monotonic - time.monotonic() <= 0:
        raise TurnRequestRejected(
            "turn_deadline_expired",
            HTTPStatus.REQUEST_TIMEOUT,
        )


def create_server(
    host: str = "127.0.0.1",
    port: int = 18787,
    *,
    thought_loop: ThoughtLoop | None = None,
) -> ThreadingHTTPServer:
    loop = thought_loop or ThoughtLoop()
    event_journal = journal_from_env()
    allow_remote_api = _env_bool("THOUGHT_CORE_ALLOW_REMOTE_API")
    require_api_token = _env_bool("THOUGHT_CORE_REQUIRE_API_TOKEN")
    api_token = os.environ.get("THOUGHT_CORE_API_TOKEN", "").strip()
    max_body_bytes = _env_int("THOUGHT_CORE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)
    accepted_candidate_registry = _AcceptedCandidateRegistry()

    class ThoughtCoreHandler(BaseHTTPRequestHandler):
        server_version = "thought-core/0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_json(service_index_payload())
                return
            if parsed.path == "/health":
                self._send_json({"status": "ok", "service": "thought-core"})
                return
            if parsed.path == "/diagnostics/no-provider-child-provenance":
                if not self._diagnostics_api_allowed():
                    return
                params = parse_qs(parsed.query)
                self._send_json(
                    build_child_provenance_diagnostics(
                        selected_profile=_first(params, "selected_profile", ""),
                        ops_profile=_first(params, "ops_profile", ""),
                        top_level_text_present_class=_first(
                            params,
                            "top_level_text_present_class",
                            "",
                        ),
                        top_level_text_marker_class=_first(
                            params,
                            "top_level_text_marker_class",
                            "",
                        ),
                        context_ref_payload_class=_first(
                            params,
                            "context_ref_payload_class",
                            "",
                        ),
                    )
                )
                return
            if parsed.path == "/turn/stream":
                self._send_json(
                    {
                        "error": "method_not_allowed",
                        "message": "Use POST /turn/stream for turn execution.",
                    },
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                    headers={"Allow": "POST"},
                )
                return
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            parsed = urlparse(self.path)
            if parsed.path not in {"/turn", "/turn/stream"}:
                self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            if not self._turn_api_allowed():
                return
            try:
                payload = self._read_json_body()
            except RequestBodyTooLarge as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            params = parse_qs(parsed.query)
            accept = self.headers.get("Accept", "")
            stream = (
                parsed.path == "/turn/stream"
                or _first(params, "stream", "").lower() == "true"
                or "text/event-stream" in accept
            )
            try:
                deadline_monotonic = _parse_route_deadline_header(
                    self.headers.get(ROUTE_DEADLINE_HEADER)
                )
            except TurnRequestRejected as exc:
                self._send_json(
                    {"error": exc.result_class},
                    status=exc.status,
                )
                return
            self._handle_turn(
                payload,
                stream=stream,
                deadline_monotonic=deadline_monotonic,
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _turn_api_allowed(self) -> bool:
            return self._api_allowed()

        def _diagnostics_api_allowed(self) -> bool:
            return self._api_allowed()

        def _api_allowed(self) -> bool:
            local_request = self._local_request()
            if not local_request and not allow_remote_api:
                self._send_json(
                    {"error": "local_access_required"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False
            if not self._trusted_origin():
                self._send_json(
                    {"error": "untrusted_origin"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False

            token_required = require_api_token or (not local_request and allow_remote_api)
            if token_required and not api_token:
                self._send_json(
                    {"error": "api_token_required"},
                    status=HTTPStatus.FORBIDDEN,
                )
                return False
            if token_required and not self._authorized():
                self._send_json(
                    {"error": "unauthorized"},
                    status=HTTPStatus.UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                return False
            return True

        def _local_request(self) -> bool:
            host, _port = self.client_address
            try:
                return ipaddress.ip_address(host).is_loopback
            except ValueError:
                return host in {"localhost", ""}

        def _trusted_origin(self) -> bool:
            origin_header = self.headers.get("Origin", "")
            if not origin_header:
                return True
            if origin_header == "null":
                return False
            try:
                origin = urlparse(origin_header)
            except ValueError:
                return False
            if origin.scheme not in {"http", "https"}:
                return False
            host = _parse_host_header(self.headers.get("Host", ""))
            if host is None:
                return _is_loopback_host(origin.hostname or "")
            if origin.netloc == host.netloc:
                return True
            return (
                _is_loopback_host(origin.hostname or "")
                and _is_loopback_host(host.hostname or "")
                and _port_or_empty(origin) == _port_or_empty(host)
            )

        def _authorized(self) -> bool:
            actual = _extract_token(
                self.headers.get("Authorization"),
                self.headers.get("X-API-Token"),
            )
            return bool(actual) and hmac.compare_digest(actual, api_token)

        def _handle_turn(
            self,
            payload: dict[str, Any],
            *,
            stream: bool,
            deadline_monotonic: float | None,
        ) -> None:
            execution_deadline: TurnExecutionDeadline | None = None
            self._turn_response_started = False
            try:
                _ensure_route_deadline_current(deadline_monotonic)
                turn = materialize_turn_input(payload)
                _ensure_route_deadline_current(deadline_monotonic)
                accepted_candidate_registry.reserve(turn)
                _ensure_route_deadline_current(deadline_monotonic)
                execution_deadline = (
                    issue_turn_execution_deadline(
                        deadline_monotonic,
                        turn_key=(turn.turn_id, turn.session_id),
                    )
                    if deadline_monotonic is not None
                    else None
                )
                if stream:
                    self._send_sse_live(
                        turn,
                        execution_deadline=execution_deadline,
                    )
                    return
                run_kwargs = (
                    {"execution_deadline": execution_deadline}
                    if execution_deadline is not None
                    else {}
                )
                events = []
                for event in loop.run_dicts(turn, **run_kwargs):
                    if execution_deadline is not None:
                        execution_deadline.ensure_current()
                    events.append(
                        _decorate_correlated_event_with_conversation_attempt_ref(
                            event,
                            turn,
                        )
                    )
            except TurnDeadlineExceeded:
                if not self._turn_response_started:
                    self._send_json(
                        {"error": TURN_DEADLINE_EXCEEDED},
                        status=HTTPStatus.REQUEST_TIMEOUT,
                    )
                return
            except TurnDeadlineInvalid:
                if not self._turn_response_started:
                    self._send_json(
                        {"error": TURN_DEADLINE_INVALID},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                return
            except TurnRequestRejected as exc:
                self._send_json(
                    {"error": exc.result_class},
                    status=exc.status,
                )
                return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            _write_journal_safely(
                event_journal,
                events,
                execution_deadline=execution_deadline,
            )
            self._send_json(
                {"events": events},
                execution_deadline=execution_deadline,
            )

        def _read_json_body(self) -> dict[str, Any]:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length > max_body_bytes:
                raise RequestBodyTooLarge("JSON body too large")
            body = self.rfile.read(length)
            if not body:
                raise ValueError("empty JSON body")
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSON body") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_json(
            self,
            payload: dict[str, Any],
            status: HTTPStatus = HTTPStatus.OK,
            *,
            headers: dict[str, str] | None = None,
            execution_deadline: TurnExecutionDeadline | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if execution_deadline is not None:
                execution_deadline.ensure_current()
            self._turn_response_started = True
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if execution_deadline is not None:
                execution_deadline.ensure_current()
            self.wfile.write(body)

        def _send_sse(self, events: list[dict[str, Any]]) -> None:
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                self._write_sse_event(event)

        def _send_sse_live(
            self,
            turn: TurnInput | Mapping[str, Any],
            *,
            execution_deadline: TurnExecutionDeadline | None,
        ) -> None:
            if execution_deadline is not None:
                execution_deadline.ensure_current()
            self._turn_response_started = True
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def write_event(event: dict[str, Any]) -> None:
                if execution_deadline is not None:
                    execution_deadline.ensure_current()
                event = _decorate_correlated_event_with_conversation_attempt_ref(
                    event,
                    turn,
                )
                _write_journal_event_safely(
                    event_journal,
                    event,
                    execution_deadline=execution_deadline,
                )
                self._write_sse_event(
                    event,
                    execution_deadline=execution_deadline,
                )

            try:
                run_kwargs = (
                    {"execution_deadline": execution_deadline}
                    if execution_deadline is not None
                    else {}
                )
                loop.run_dicts(turn, event_sink=write_event, **run_kwargs)
            except TurnDeadlineExceeded:
                return
            except TurnDeadlineInvalid:
                return
            except TurnRequestRejected as exc:
                write_event(
                    _error_event(
                        {},
                        code=exc.result_class,
                        message=exc.result_class,
                    )
                )
            except ValueError as exc:
                write_event(
                    _error_event(
                        {},
                        code="bad_request",
                        message=str(exc),
                    )
                )
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                if execution_deadline is not None:
                    execution_deadline.cancel()
                return

        def _write_sse_event(
            self,
            event: dict[str, Any],
            *,
            execution_deadline: TurnExecutionDeadline | None = None,
        ) -> None:
            if execution_deadline is not None:
                execution_deadline.ensure_current()
            self.wfile.write(f"id: {event['event_id']}\n".encode("utf-8"))
            if execution_deadline is not None:
                execution_deadline.ensure_current()
            self.wfile.write(f"event: {event['type']}\n".encode("utf-8"))
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
            if execution_deadline is not None:
                execution_deadline.ensure_current()
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

    return ThreadingHTTPServer((host, port), ThoughtCoreHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the experimental thought-core server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18787)
    args = parser.parse_args(argv)

    try:
        server = create_server(args.host, args.port)
    except OSError as exc:
        print(
            f"failed to bind thought-core on http://{args.host}:{args.port}: {exc}. "
            "Choose another --port if this one is already used.",
            file=sys.stderr,
        )
        return 1
    print(f"thought-core listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    if not values:
        return default
    return values[0]


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _error_event(payload: dict[str, Any], *, code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "thought-core.event.v0",
        "event_id": "evt_bad_request",
        "turn_id": str(payload.get("turn_id") or ""),
        "session_id": str(payload.get("session_id") or ""),
        "seq": 1,
        "timestamp": "",
        "source": "thought-core",
        "type": "turn.error",
        "data": {"code": code, "message": message},
    }


def _write_journal_safely(
    event_journal: Any,
    events: list[dict[str, Any]],
    *,
    execution_deadline: TurnExecutionDeadline | None = None,
) -> None:
    if event_journal is None:
        return
    try:
        if execution_deadline is not None:
            execution_deadline.ensure_current()
        event_journal.write_many(events)
        if execution_deadline is not None:
            execution_deadline.ensure_current()
    except (OSError, TypeError, ValueError):
        return


def _write_journal_event_safely(
    event_journal: Any,
    event: dict[str, Any],
    *,
    execution_deadline: TurnExecutionDeadline | None = None,
) -> None:
    if event_journal is None:
        return
    try:
        if execution_deadline is not None:
            execution_deadline.ensure_current()
        event_journal.write_event(event)
        if execution_deadline is not None:
            execution_deadline.ensure_current()
    except (OSError, TypeError, ValueError):
        return


def _parse_host_header(host_header: str):
    if not host_header:
        return None
    try:
        return urlparse(f"http://{host_header}")
    except ValueError:
        return None


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.strip().lower()
    if normalized in {"localhost", "::1", "[::1]"} or normalized.startswith("127."):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _port_or_empty(parsed) -> str:
    try:
        port = parsed.port
    except ValueError:
        return ""
    return str(port or "")


def _extract_token(authorization: str | None, x_api_token: str | None) -> str | None:
    if x_api_token:
        return x_api_token.strip()
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def service_index_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "thought-core",
        "kind": "api",
        "note": "This is the thought-core API, not the Sword Voice Agent console UI.",
        "console_command": "uv run sword-console --ai-talk-core-root ..\\ai-talk-core",
        "endpoints": {
            "health": "GET /health",
            "no_provider_child_provenance": "GET /diagnostics/no-provider-child-provenance",
            "turn_json": "POST /turn",
            "turn_sse": "POST /turn?stream=true",
            "turn_sse_alias": "POST /turn/stream",
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
