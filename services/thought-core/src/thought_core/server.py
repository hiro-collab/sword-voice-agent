"""Dependency-free HTTP/SSE server for the thought-core experiment."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .loop import ThoughtLoop

DEFAULT_MAX_BODY_BYTES = 64 * 1024


class RequestBodyTooLarge(ValueError):
    pass


def create_server(
    host: str = "127.0.0.1",
    port: int = 18787,
    *,
    thought_loop: ThoughtLoop | None = None,
) -> ThreadingHTTPServer:
    loop = thought_loop or ThoughtLoop()
    allow_remote_api = _env_bool("THOUGHT_CORE_ALLOW_REMOTE_API")
    require_api_token = _env_bool("THOUGHT_CORE_REQUIRE_API_TOKEN")
    api_token = os.environ.get("THOUGHT_CORE_API_TOKEN", "").strip()
    max_body_bytes = _env_int("THOUGHT_CORE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)

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
            self._handle_turn(payload, stream=stream)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _turn_api_allowed(self) -> bool:
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

        def _handle_turn(self, payload: dict[str, Any], *, stream: bool) -> None:
            if stream:
                self._send_sse_live(payload)
                return
            try:
                events = loop.run_dicts(payload)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"events": events})

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
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_sse(self, events: list[dict[str, Any]]) -> None:
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                self._write_sse_event(event)

        def _send_sse_live(self, payload: dict[str, Any]) -> None:
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                loop.run_dicts(payload, event_sink=self._write_sse_event)
            except ValueError as exc:
                self._write_sse_event(
                    {
                        "schema_version": "thought-core.event.v0",
                        "event_id": "evt_bad_request",
                        "turn_id": str(payload.get("turn_id") or ""),
                        "session_id": str(payload.get("session_id") or ""),
                        "seq": 1,
                        "timestamp": "",
                        "source": "thought-core",
                        "type": "turn.error",
                        "data": {"code": "bad_request", "message": str(exc)},
                    }
                )
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def _write_sse_event(self, event: dict[str, Any]) -> None:
            self.wfile.write(f"id: {event['event_id']}\n".encode("utf-8"))
            self.wfile.write(f"event: {event['type']}\n".encode("utf-8"))
            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
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
            "turn_json": "POST /turn",
            "turn_sse": "POST /turn?stream=true",
            "turn_sse_alias": "POST /turn/stream",
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
