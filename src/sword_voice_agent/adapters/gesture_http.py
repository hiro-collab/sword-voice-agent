from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from sword_voice_agent.adapters.auth import (
    headers_authorized,
    require_auth_token_for_bind,
)
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.adapters.gesture_gateway import (
    VoiceStateSink,
    build_gesture_response,
)
from sword_voice_agent.adapters.rate_limit import (
    FixedWindowRateLimiter,
    RateLimitExceeded,
)
from sword_voice_agent.protocol.messages import ProtocolError


DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_RATE_LIMIT_PER_MINUTE = 1800


class RequestBodyTooLarge(RuntimeError):
    pass


class GestureGateHttpHandler(BaseHTTPRequestHandler):
    gate: GestureInputGate
    voice_state_sink: VoiceStateSink | None = None
    turn_controller: VoiceTurnController | None = None
    auth_token: str = ""
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    rate_limiter: FixedWindowRateLimiter = FixedWindowRateLimiter.disabled()

    server_version = "SwordGestureHTTP/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"ok": True})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/gesture-state":
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._check_rate_limit():
            return
        if not headers_authorized(self.headers, self.auth_token):
            self._write_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": "unauthorized"},
            )
            return

        try:
            payload = self._read_json_body()
            response_payload = build_gesture_response(
                payload,
                self.gate,
                self.voice_state_sink,
                self.turn_controller,
            )
        except RequestBodyTooLarge:
            self._write_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "request_body_too_large"},
            )
            return
        except (json.JSONDecodeError, ProtocolError, ValueError, TypeError) as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": str(exc)},
            )
            return
        except RuntimeError as exc:
            self._write_json(
                HTTPStatus.BAD_GATEWAY,
                {"ok": False, "error": "upstream_error"},
            )
            return

        self._write_json(HTTPStatus.OK, response_payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Mapping[str, Any]:
        content_length = parse_content_length(
            self.headers.get("Content-Length", "0"),
            max_body_bytes=self.max_body_bytes,
        )
        body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            raise ProtocolError("request body must be a JSON object")
        return payload

    def _write_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_rate_limit(self) -> bool:
        try:
            self.rate_limiter.check(self.client_address[0])
        except RateLimitExceeded as exc:
            self._write_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "ok": False,
                    "error": "rate_limited",
                    "retry_after_s": round(exc.retry_after_s, 3),
                },
            )
            return False
        return True


def make_handler(
    gate: GestureInputGate,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
    auth_token: str = "",
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
) -> type[GestureGateHttpHandler]:
    class ConfiguredGestureGateHttpHandler(GestureGateHttpHandler):
        pass

    ConfiguredGestureGateHttpHandler.gate = gate
    ConfiguredGestureGateHttpHandler.voice_state_sink = voice_state_sink
    ConfiguredGestureGateHttpHandler.turn_controller = turn_controller
    ConfiguredGestureGateHttpHandler.auth_token = auth_token
    ConfiguredGestureGateHttpHandler.max_body_bytes = max_body_bytes
    ConfiguredGestureGateHttpHandler.rate_limiter = FixedWindowRateLimiter(
        rate_limit_per_minute,
    )
    return ConfiguredGestureGateHttpHandler


def create_server(
    host: str,
    port: int,
    gate: GestureInputGate,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
    auth_token: str = "",
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
) -> ThreadingHTTPServer:
    require_auth_token_for_bind(host, auth_token, "gesture HTTP receiver")
    return ThreadingHTTPServer(
        (host, port),
        make_handler(
            gate,
            voice_state_sink,
            turn_controller,
            auth_token,
            max_body_bytes=max_body_bytes,
            rate_limit_per_minute=rate_limit_per_minute,
        ),
    )


def parse_content_length(value: str, *, max_body_bytes: int) -> int:
    try:
        content_length = int(value)
    except ValueError as exc:
        raise ProtocolError("Content-Length must be an integer") from exc
    if content_length < 0:
        raise ProtocolError("Content-Length must be >= 0")
    if content_length > max_body_bytes:
        raise RequestBodyTooLarge
    return content_length
