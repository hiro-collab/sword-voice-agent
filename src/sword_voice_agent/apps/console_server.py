from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import time
from typing import Any
from urllib import error as urlerror, request as urlrequest
from urllib.parse import parse_qs, urlparse

from sword_voice_agent.adapters.ai_talk_core import resolve_ai_talk_core_web_token
from sword_voice_agent.adapters.auth import (
    AuthError,
    headers_authorized,
    require_auth_token_for_bind,
    resolve_auth_token,
    validate_http_url,
)
from sword_voice_agent.adapters.console_status import (
    ConsoleStatusConfig,
    build_console_status,
    read_console_events_after,
    redact_event,
    resolve_tts_app_volume_file,
)
from sword_voice_agent.adapters.rate_limit import (
    FixedWindowRateLimiter,
    RateLimitExceeded,
)
from sword_voice_agent.adapters.status_store import StatusStore


STATIC_DIR = Path(__file__).resolve().parents[1] / "web"
DEFAULT_API_RATE_LIMIT_PER_MINUTE = 120
DEFAULT_EVENT_STREAM_LIMIT = 40
MAX_EVENT_STREAM_LIMIT = 200
CORS_ALLOW_HEADERS = (
    "Authorization, X-Sword-Agent-Token, Last-Event-ID, Content-Type, Accept"
)
CORS_ALLOW_METHODS = "GET, POST, OPTIONS"


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
        "--tts-status-dir",
        default=os.environ.get("TTS_OUTPUT_STATUS_DIR", ".cache/tts_service"),
        help="Directory containing latest_tts_state.json from tts_service.",
    )
    parser.add_argument(
        "--tts-app-volume-file",
        default=os.environ.get("TTS_SERVICE_APP_VOLUME_FILE", ""),
        help="JSON file read by tts_service for runtime app volume.",
    )
    parser.add_argument(
        "--tts-volume-url",
        default=os.environ.get("TTS_VOLUME_URL", ""),
        help="Optional tts-service /api/volume URL. Falls back to --tts-app-volume-file if unreachable.",
    )
    parser.add_argument(
        "--tts-volume-preview-url",
        default=os.environ.get("TTS_VOLUME_PREVIEW_URL", ""),
        help="Optional tts-service /api/volume/preview URL used for app volume preview tones.",
    )
    parser.add_argument("--tts-volume-timeout", type=float, default=1.5)
    parser.add_argument(
        "--input-gate-url",
        default=os.environ.get("AI_TALK_CORE_INPUT_GATE_URL", ""),
        help="Optional ai_talk_core input gate API URL for status polling.",
    )
    parser.add_argument("--input-gate-timeout", type=float, default=1.5)
    parser.add_argument(
        "--thought-core-base-url",
        default=os.environ.get("THOUGHT_CORE_BASE_URL", "http://127.0.0.1:18787"),
        help="Optional thought-core API base URL for readiness status.",
    )
    parser.add_argument("--thought-core-timeout", type=float, default=1.5)
    parser.add_argument(
        "--avatar-url",
        default=os.environ.get("AVATAR_SERVICE_URL", ""),
        help="Optional avatar-service URL for readiness status and browser bridge.",
    )
    parser.add_argument(
        "--avatar-model-url",
        default=os.environ.get("AVATAR_MODEL_URL", ""),
        help="Optional VRM model URL passed to avatar-service as the model query parameter.",
    )
    parser.add_argument("--avatar-timeout", type=float, default=1.5)
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Optional token required for /api/status. Defaults to SWORD_VOICE_AGENT_AUTH_TOKEN.",
    )
    parser.add_argument(
        "--api-rate-limit-per-minute",
        type=int,
        default=DEFAULT_API_RATE_LIMIT_PER_MINUTE,
        help="Per-client API request limit for /api/status. Use 0 to disable.",
    )
    parser.add_argument(
        "--redact-sensitive",
        action="store_true",
        default=env_flag("SWORD_VOICE_AGENT_REDACT_STATUS"),
        help="Hide transcript, command, Thought Core answer, IDs, and local paths in /api/status.",
    )
    return parser


def make_handler(
    config: ConsoleStatusConfig,
    auth_token: str = "",
    rate_limit_per_minute: int = DEFAULT_API_RATE_LIMIT_PER_MINUTE,
) -> type[BaseHTTPRequestHandler]:
    rate_limiter = FixedWindowRateLimiter(rate_limit_per_minute)

    class ConsoleRequestHandler(BaseHTTPRequestHandler):
        server_version = "SwordVoiceConsole/0.1"

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_cors_headers()
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self) -> None:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            if path == "/api/status":
                if not self.check_api_rate_limit():
                    return
                if not headers_authorized(self.headers, auth_token):
                    self.send_json(
                        {"ok": False, "error": "unauthorized"},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                self.send_json(build_console_status(config))
                return
            if path == "/api/events":
                if not self.check_api_rate_limit():
                    return
                if not headers_authorized(self.headers, auth_token):
                    self.send_json(
                        {"ok": False, "error": "unauthorized"},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                self.send_event_stream(parse_qs(parsed_url.query))
                return
            if path == "/api/tts/volume":
                if not self.check_api_rate_limit():
                    return
                if not headers_authorized(self.headers, auth_token):
                    self.send_json(
                        {"ok": False, "error": "unauthorized"},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                try:
                    self.send_json(read_tts_app_volume(config))
                except ValueError as exc:
                    self.send_json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                return
            if path == "/":
                self.send_static("index.html")
                return
            self.send_static(path.lstrip("/"))

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/tts/volume":
                self.handle_tts_volume_post()
                return
            if path == "/api/tts/volume/preview":
                self.handle_tts_volume_preview_post()
                return
            if path != "/api/status/clear":
                self.send_json(
                    {"ok": False, "error": "not_found"},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            if not self.check_api_rate_limit():
                return
            if not headers_authorized(self.headers, auth_token):
                self.send_json(
                    {"ok": False, "error": "unauthorized"},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            if config.status_dir is None:
                self.send_json(
                    {"ok": False, "error": "status_dir_not_configured"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            StatusStore(config.status_dir).clear()
            self.send_json({"ok": True, "cleared": True})

        def handle_tts_volume_post(self) -> None:
            if not self.check_api_rate_limit():
                return
            if not headers_authorized(self.headers, auth_token):
                self.send_json(
                    {"ok": False, "error": "unauthorized"},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            try:
                payload = self.read_json_body()
                app_volume = validate_app_volume(payload.get("app_volume"))
                self.send_json(write_tts_app_volume(config, app_volume))
            except ValueError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )

        def handle_tts_volume_preview_post(self) -> None:
            if not self.check_api_rate_limit():
                return
            if not headers_authorized(self.headers, auth_token):
                self.send_json(
                    {"ok": False, "error": "unauthorized"},
                    status=HTTPStatus.UNAUTHORIZED,
                )
                return
            try:
                payload = self.read_json_body()
                app_volume = validate_app_volume(payload.get("app_volume"))
                self.send_json(preview_tts_app_volume(config, app_volume))
            except ValueError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )

        def read_json_body(self, max_body_bytes: int = 100_000) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 1:
                raise ValueError("empty request body")
            if length > max_body_bytes:
                raise ValueError("request body too large")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("invalid JSON body") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

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
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", CORS_ALLOW_METHODS)
            self.send_header("Access-Control-Allow-Headers", CORS_ALLOW_HEADERS)
            self.send_header("Access-Control-Max-Age", "600")

        def send_event_stream(self, query: dict[str, list[str]]) -> None:
            if config.status_dir is None:
                self.send_json(
                    {"ok": False, "error": "status_dir_not_configured"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            once = parse_boolish(first_query_value(query, "once"))
            limit = bounded_int(
                first_query_value(query, "limit"),
                default=DEFAULT_EVENT_STREAM_LIMIT,
                minimum=1,
                maximum=MAX_EVENT_STREAM_LIMIT,
            )
            poll_interval = bounded_float(
                first_query_value(query, "poll_interval"),
                default=0.25,
                minimum=0.05,
                maximum=5.0,
            )
            last_event_id = (
                first_query_value(query, "after")
                or self.headers.get("Last-Event-ID", "")
            ).strip()
            sent_event_ids: set[str] = set()

            self.send_response(HTTPStatus.OK)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close" if once else "keep-alive")
            self.end_headers()

            while True:
                events = read_console_events_after(
                    config,
                    last_event_id,
                    limit=limit,
                )
                for event in events:
                    event_id = str(event.get("event_id") or "")
                    if event_id and event_id in sent_event_ids:
                        continue
                    if event_id:
                        sent_event_ids.add(event_id)
                        last_event_id = event_id
                    output_event = redact_event(event) if config.redact_sensitive else event
                    if not self.write_sse_event(output_event):
                        return
                if once:
                    self.close_connection = True
                    return
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                time.sleep(poll_interval)

        def write_sse_event(self, event: dict[str, Any]) -> bool:
            body = format_sse_event(event).encode("utf-8")
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return False
            return True

        def check_api_rate_limit(self) -> bool:
            try:
                rate_limiter.check(self.client_address[0])
            except RateLimitExceeded as exc:
                self.send_json(
                    {
                        "ok": False,
                        "error": "rate_limited",
                        "retry_after_s": round(exc.retry_after_s, 3),
                    },
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                )
                return False
            return True

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


def format_sse_event(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "").replace("\n", "")
    event_type = str(event.get("type") or "message").replace("\n", "")
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    if event_type:
        lines.append(f"event: {event_type}")
    for data_line in data.splitlines() or [""]:
        lines.append(f"data: {data_line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def first_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return values[0] if values else ""


def parse_boolish(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def bounded_int(
    value: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def bounded_float(
    value: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


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
        tts_status_dir=Path(args.tts_status_dir) if args.tts_status_dir else None,
        tts_app_volume_file=Path(args.tts_app_volume_file)
        if args.tts_app_volume_file
        else None,
        tts_volume_url=args.tts_volume_url or None,
        tts_volume_preview_url=args.tts_volume_preview_url or None,
        tts_volume_timeout_s=args.tts_volume_timeout,
        input_gate_url=args.input_gate_url or None,
        input_gate_token=resolve_ai_talk_core_web_token(),
        input_gate_timeout_s=args.input_gate_timeout,
        thought_core_base_url=args.thought_core_base_url or None,
        thought_core_timeout_s=args.thought_core_timeout,
        avatar_url=args.avatar_url or None,
        avatar_model_url=args.avatar_model_url or None,
        avatar_timeout_s=args.avatar_timeout,
        redact_sensitive=args.redact_sensitive,
    )
    return ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(
            config,
            auth_token=auth_token,
            rate_limit_per_minute=args.api_rate_limit_per_minute,
        ),
    )


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def read_tts_app_volume(config: ConsoleStatusConfig) -> dict[str, Any]:
    if config.tts_volume_url:
        try:
            return proxy_tts_app_volume(config, method="GET")
        except ValueError as exc:
            payload = read_tts_app_volume_file(config)
            payload["volume_url"] = config.tts_volume_url
            payload["volume_url_error"] = str(exc)
            return payload
    return read_tts_app_volume_file(config)


def read_tts_app_volume_file(config: ConsoleStatusConfig) -> dict[str, Any]:
    path = resolve_tts_app_volume_file(config)
    if path is None:
        raise ValueError("tts_app_volume_file_not_configured")
    payload: dict[str, Any] = {
        "ok": True,
        "app_volume": 1.0,
        "app_volume_file": str(path),
        "exists": False,
        "updated_at": None,
    }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return payload
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read tts app volume: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("tts app volume payload must be an object")
    payload["app_volume"] = app_volume_from_payload(raw, default=1.0)
    payload["exists"] = True
    payload["updated_at"] = raw.get("updated_at")
    return payload


def write_tts_app_volume(config: ConsoleStatusConfig, app_volume: float) -> dict[str, Any]:
    if config.tts_volume_url:
        try:
            return proxy_tts_app_volume(
                config,
                method="POST",
                payload={"app_volume": app_volume},
            )
        except ValueError as exc:
            payload = write_tts_app_volume_file(config, app_volume)
            payload["volume_url"] = config.tts_volume_url
            payload["volume_url_error"] = str(exc)
            return payload
    return write_tts_app_volume_file(config, app_volume)


def preview_tts_app_volume(
    config: ConsoleStatusConfig,
    app_volume: float,
) -> dict[str, Any]:
    if not config.tts_volume_preview_url:
        raise ValueError("tts_volume_preview_url_not_configured")
    try:
        preview_url = validate_http_url(
            config.tts_volume_preview_url,
            label="TTS_VOLUME_PREVIEW_URL",
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    req = urlrequest.Request(
        preview_url,
        data=json.dumps({"app_volume": app_volume}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=config.tts_volume_timeout_s) as response:
            body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except OSError:
            detail = ""
        raise ValueError(f"tts volume preview API HTTP {exc.code}: {detail}") from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"tts volume preview API unreachable: {exc}") from exc

    try:
        response_payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"tts volume preview API returned invalid JSON: {exc}") from exc
    if not isinstance(response_payload, dict):
        raise ValueError("tts volume preview API payload must be an object")
    response_payload.setdefault("ok", True)
    response_payload["volume_preview_url"] = preview_url
    if "app_volume" in response_payload:
        response_payload["app_volume"] = validate_app_volume(
            response_payload["app_volume"]
        )
    if "preview_volume" in response_payload:
        response_payload["preview_volume"] = validate_app_volume(
            response_payload["preview_volume"]
        )
    return response_payload


def write_tts_app_volume_file(
    config: ConsoleStatusConfig,
    app_volume: float,
) -> dict[str, Any]:
    path = resolve_tts_app_volume_file(config)
    if path is None:
        raise ValueError("tts_app_volume_file_not_configured")
    path.parent.mkdir(parents=True, exist_ok=True)
    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "app_volume": app_volume,
        "updated_at": updated_at,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return {
        "ok": True,
        "app_volume": app_volume,
        "app_volume_file": str(path),
        "exists": True,
        "updated_at": updated_at,
    }


def proxy_tts_app_volume(
    config: ConsoleStatusConfig,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not config.tts_volume_url:
        raise ValueError("tts_volume_url_not_configured")
    try:
        volume_url = validate_http_url(config.tts_volume_url, label="TTS_VOLUME_URL")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlrequest.Request(
        volume_url,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urlrequest.urlopen(req, timeout=config.tts_volume_timeout_s) as response:
            body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except OSError:
            detail = ""
        raise ValueError(f"tts volume API HTTP {exc.code}: {detail}") from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"tts volume API unreachable: {exc}") from exc

    try:
        response_payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"tts volume API returned invalid JSON: {exc}") from exc
    if not isinstance(response_payload, dict):
        raise ValueError("tts volume API payload must be an object")
    response_payload.setdefault("ok", True)
    response_payload["volume_url"] = volume_url
    if "app_volume" in response_payload:
        response_payload["app_volume"] = validate_app_volume(
            response_payload["app_volume"]
        )
    return response_payload


def validate_app_volume(value: Any) -> float:
    try:
        app_volume = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("app_volume must be a number between 0.0 and 1.0") from exc
    if not 0.0 <= app_volume <= 1.0:
        raise ValueError("app_volume must be between 0.0 and 1.0")
    return app_volume


def app_volume_from_payload(payload: dict[str, Any], *, default: float) -> float:
    if payload.get("muted") is True:
        return 0.0
    for key in ("app_volume", "volume", "value"):
        if key in payload:
            return validate_app_volume(payload[key])
    if "app_volume_percent" in payload:
        return validate_app_volume(float(payload["app_volume_percent"]) / 100.0)
    return default


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
