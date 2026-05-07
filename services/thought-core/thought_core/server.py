"""Dependency-free HTTP/SSE server for the thought-core experiment."""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .loop import ThoughtLoop


def create_server(
    host: str = "127.0.0.1",
    port: int = 18787,
    *,
    thought_loop: ThoughtLoop | None = None,
) -> ThreadingHTTPServer:
    loop = thought_loop or ThoughtLoop()

    class ThoughtCoreHandler(BaseHTTPRequestHandler):
        server_version = "thought-core/0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json({"status": "ok", "service": "thought-core"})
                return
            if parsed.path == "/turn/stream":
                params = parse_qs(parsed.query)
                payload = {
                    "text": _first(params, "text"),
                    "turn_id": _first(params, "turn_id", "turn_get_stream"),
                    "session_id": _first(params, "session_id", "default"),
                    "locale": _first(params, "locale", "ja-JP"),
                    "context_refs": {},
                }
                self._handle_turn(payload, stream=True)
                return
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            parsed = urlparse(self.path)
            if parsed.path not in {"/turn", "/turn/stream"}:
                self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
                return
            try:
                payload = self._read_json_body()
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

        def _handle_turn(self, payload: dict[str, Any], *, stream: bool) -> None:
            try:
                events = loop.run_dicts(payload)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            if stream:
                self._send_sse(events)
            else:
                self._send_json({"events": events})

        def _read_json_body(self) -> dict[str, Any]:
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
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

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
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


if __name__ == "__main__":
    raise SystemExit(main())
