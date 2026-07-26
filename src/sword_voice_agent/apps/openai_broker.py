"""Thin loopback-only executable surface for the OpenAI broker adapter."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer

from sword_voice_agent.adapters.openai_broker import (
    COMPLETIONS_PATH,
    LOOPBACK_HOST,
    MAX_BODY_BYTES,
    MAX_TIMEOUT_S,
    BrokerConfig,
    BrokerError,
    OpenAIBroker,
)

HEALTH_PATH = "/health"
BODY_READ_TIMEOUT_S = MAX_TIMEOUT_S
BODY_READ_CHUNK_BYTES = 4096


class SingleAdmissionHTTPServer(HTTPServer):
    """One synchronous handler admission with only one queued connection."""

    request_queue_size = 1


def _validated_content_length(
    content_lengths: list[str] | None,
    transfer_encoding: str | None,
) -> int:
    if transfer_encoding or content_lengths is None or len(content_lengths) != 1:
        raise BrokerError("invalid_request")
    raw_length = content_lengths[0]
    if not raw_length or not raw_length.isdecimal():
        raise BrokerError("invalid_request")
    length = int(raw_length)
    if length > MAX_BODY_BYTES:
        raise BrokerError("invalid_request")
    return length


def _read_exact_body(
    reader: object,
    length: int,
    *,
    clock: Callable[[], float] = time.monotonic,
    set_timeout: Callable[[float], object] | None = None,
    chunk_bytes: int = BODY_READ_CHUNK_BYTES,
) -> bytes:
    """Read exactly length bytes without allowing trickle progress past deadline."""

    deadline = clock() + BODY_READ_TIMEOUT_S
    remaining = length
    chunks = bytearray()
    try:
        read_one = reader.read1  # type: ignore[attr-defined]
    except AttributeError:
        raise BrokerError("body_unavailable") from None
    if not callable(read_one):
        raise BrokerError("body_unavailable")
    while remaining:
        remaining_seconds = deadline - clock()
        if remaining_seconds <= 0:
            raise BrokerError("body_unavailable")
        read_size = min(remaining, chunk_bytes)
        try:
            if set_timeout is not None:
                set_timeout(remaining_seconds)
            chunk = read_one(read_size)
        except (OSError, TimeoutError, ValueError):
            raise BrokerError("body_unavailable") from None
        if type(chunk) is not bytes or not chunk or len(chunk) > read_size:
            raise BrokerError("body_unavailable")
        chunks.extend(chunk)
        remaining -= len(chunk)
        if clock() > deadline:
            raise BrokerError("body_unavailable")
    return bytes(chunks)


def _body_error_response(failure: BrokerError) -> tuple[int, dict[str, object]]:
    if failure.code == "body_unavailable":
        return 408, {"error": {"code": "body_unavailable"}}
    return 400, {"error": {"code": "invalid_request"}}


def create_server(broker: OpenAIBroker, config: BrokerConfig) -> SingleAdmissionHTTPServer:
    """Create the sole broker listener on literal loopback."""

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(BODY_READ_TIMEOUT_S)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != HEALTH_PATH:
                self._send(404, {"error": {"code": "not_found"}})
                return
            self._send(200, {"status": "ok"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != COMPLETIONS_PATH:
                self._send(404, {"error": {"code": "not_found"}})
                return
            if self.headers.get("Content-Type") != "application/json":
                self._send(400, {"error": {"code": "invalid_request"}})
                return
            try:
                length = _validated_content_length(
                    self.headers.get_all("Content-Length"),
                    self.headers.get("Transfer-Encoding"),
                )
                body = _read_exact_body(
                    self.rfile,
                    length,
                    set_timeout=self.connection.settimeout,
                )
            except BrokerError as failure:
                self.close_connection = True
                status, payload = _body_error_response(failure)
                self._send(status, payload)
                return
            try:
                result = broker.complete(body)
            except BrokerError as failure:
                status = 503 if failure.code in {
                    "secret_unavailable",
                    "capacity_exhausted",
                    "request_budget_exhausted",
                    "upstream_unavailable",
                } else 400
                self._send(status, {"error": {"code": failure.code}})
                return
            self._send(200, result)

        def _send(self, status: int, payload: Mapping[str, object]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return SingleAdmissionHTTPServer((LOOPBACK_HOST, config.port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sword loopback OpenAI broker")
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout-s", type=float)
    parser.add_argument("--request-budget", type=int)
    args = parser.parse_args(argv)
    values = {key: value for key, value in vars(args).items() if value is not None}
    try:
        config = BrokerConfig(**values)
    except ValueError:
        return 2
    server = create_server(OpenAIBroker(config), config)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0
