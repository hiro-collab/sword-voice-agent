from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sword_voice_agent.adapters.auth import (
    AuthError,
    headers_authorized,
    require_auth_token_for_bind,
    resolve_auth_token,
)
from sword_voice_agent.adapters.console_status import (
    ConsoleStatusConfig,
    build_console_status,
)


STATIC_DIR = Path(__file__).resolve().parents[1] / "web"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the local Sword Voice Agent integration console."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument(
        "--ai-talk-core-root",
        default=os.environ.get("AI_TALK_CORE_ROOT", ""),
        help="Path to the ai_talk_core repository root.",
    )
    parser.add_argument("--source", default="web")
    parser.add_argument(
        "--gesture-status-json",
        default=os.environ.get(
            "SWORD_VOICE_AGENT_GESTURE_STATUS_JSON",
            ".cache/sword_voice_agent/gesture_latest.json",
        ),
        help="Path written by gesture_udp_receiver --status-json.",
    )
    parser.add_argument(
        "--status-dir",
        default=".cache/sword_voice_agent",
        help="Directory for latest status snapshots and events.jsonl.",
    )
    parser.add_argument(
        "--input-gate-url",
        default=os.environ.get("AI_TALK_CORE_INPUT_GATE_URL", ""),
        help="Optional ai_talk_core input gate API URL for status polling.",
    )
    parser.add_argument("--input-gate-timeout", type=float, default=1.5)
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Optional token required for /api/status. Defaults to SWORD_VOICE_AGENT_AUTH_TOKEN.",
    )
    return parser


def make_handler(
    config: ConsoleStatusConfig,
    auth_token: str = "",
) -> type[BaseHTTPRequestHandler]:
    class ConsoleRequestHandler(BaseHTTPRequestHandler):
        server_version = "SwordVoiceConsole/0.1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/status":
                if not headers_authorized(self.headers, auth_token):
                    self.send_json(
                        {"ok": False, "error": "unauthorized"},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                self.send_json(build_console_status(config))
                return
            if path == "/":
                self.send_static("index.html")
                return
            self.send_static(path.lstrip("/"))

        def log_message(self, format: str, *args: Any) -> None:
            print(
                f"{self.address_string()} - - {format % args}",
                flush=True,
            )

        def send_json(
            self,
            payload: dict[str, Any],
            *,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_static(self, relative_path: str) -> None:
            requested = (STATIC_DIR / relative_path).resolve()
            if not is_within_static_dir(requested) or not requested.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            body = requested.read_bytes()
            content_type = mimetypes.guess_type(str(requested))[0]
            if content_type is None:
                content_type = "application/octet-stream"
            if requested.suffix == ".js":
                content_type = "application/javascript"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ConsoleRequestHandler


def is_within_static_dir(path: Path) -> bool:
    try:
        path.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return False
    return True


def run_server(args: argparse.Namespace) -> ThreadingHTTPServer:
    if not args.ai_talk_core_root:
        raise ValueError(
            "set --ai-talk-core-root or AI_TALK_CORE_ROOT before starting console"
        )

    auth_token = resolve_auth_token(args.auth_token)
    require_auth_token_for_bind(args.host, auth_token, "console server")

    config = ConsoleStatusConfig(
        ai_talk_core_root=Path(args.ai_talk_core_root),
        source=args.source,
        gesture_status_json=Path(args.gesture_status_json)
        if args.gesture_status_json
        else None,
        status_dir=Path(args.status_dir) if args.status_dir else None,
        input_gate_url=args.input_gate_url or None,
        input_gate_timeout_s=args.input_gate_timeout,
    )
    return ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(config, auth_token=auth_token),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        server = run_server(args)
    except (AuthError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 1

    print(f"serving Sword Voice Agent console on http://{args.host}:{args.port}")
    print("press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
