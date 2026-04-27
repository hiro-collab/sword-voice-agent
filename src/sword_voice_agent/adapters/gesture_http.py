from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.protocol.messages import GestureState, ProtocolError


def build_gesture_response(
    payload: Mapping[str, Any],
    gate: GestureInputGate,
) -> dict[str, Any]:
    state = GestureState.from_dict(payload)
    decision = gate.update(state)
    return {
        "ok": True,
        "voice_state": decision.to_voice_state().to_dict(),
        "gate_decision": decision.to_dict(),
    }


class GestureGateHttpHandler(BaseHTTPRequestHandler):
    gate: GestureInputGate

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

        try:
            payload = self._read_json_body()
            response_payload = build_gesture_response(payload, self.gate)
        except (json.JSONDecodeError, ProtocolError, ValueError, TypeError) as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": str(exc)},
            )
            return

        self._write_json(HTTPStatus.OK, response_payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Mapping[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
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


def make_handler(gate: GestureInputGate) -> type[GestureGateHttpHandler]:
    class ConfiguredGestureGateHttpHandler(GestureGateHttpHandler):
        pass

    ConfiguredGestureGateHttpHandler.gate = gate
    return ConfiguredGestureGateHttpHandler


def create_server(
    host: str,
    port: int,
    gate: GestureInputGate,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(gate))

