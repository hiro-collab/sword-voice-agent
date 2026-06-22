from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib import error, request
import shutil
import threading
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.adapters.auth import AuthError
from sword_voice_agent.apps.console_server import build_parser, run_server


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ConsoleServerTest(TestCase):
    def test_requires_auth_for_non_loopback_bind(self) -> None:
        args = build_parser().parse_args(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "0",
                "--ai-talk-core-root",
                str(FIXTURES / "ai_talk_core_root"),
            ]
        )

        with self.assertRaises(AuthError):
            run_server(args)

    def test_clear_status_endpoint_requires_auth_and_removes_status_files(self) -> None:
        with workspace_tempdir() as tmp:
            status_dir = Path(tmp) / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            events_path = status_dir / "events.jsonl"
            events_path.write_text(json.dumps({"type": "test"}) + "\n", encoding="utf-8")
            args = build_parser().parse_args(
                [
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--ai-talk-core-root",
                    str(FIXTURES / "ai_talk_core_root"),
                    "--status-dir",
                    str(status_dir),
                    "--auth-token",
                    "secret",
                ]
            )
            server = run_server(args)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/status/clear"
                unauthorized = request.Request(url, data=b"", method="POST")
                with self.assertRaises(error.HTTPError) as caught:
                    request.urlopen(unauthorized, timeout=2)
                self.assertEqual(caught.exception.code, 401)

                authorized = request.Request(
                    url,
                    data=b"",
                    method="POST",
                    headers={"Authorization": "Bearer secret"},
                )
                with request.urlopen(authorized, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertFalse(events_path.exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_status_endpoint_rate_limits_requests(self) -> None:
        args = build_parser().parse_args(
            [
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--ai-talk-core-root",
                str(FIXTURES / "ai_talk_core_root"),
                "--api-rate-limit-per-minute",
                "1",
            ]
        )
        server = run_server(args)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/status"
            with request.urlopen(url, timeout=2) as response:
                self.assertEqual(response.status, 200)
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(url, timeout=2)
            self.assertEqual(caught.exception.code, 429)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_events_endpoint_streams_existing_events_once(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            status_dir = root / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            event = {
                "event_id": "evt-1",
                "type": "thought_core.first_message",
                "timestamp": 1.0,
                "source": "test",
                "turn_id": "turn-1",
                "payload": {"elapsed_s": 0.2},
            }
            (status_dir / "events.jsonl").write_text(
                json.dumps(event, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--ai-talk-core-root",
                    str(FIXTURES / "ai_talk_core_root"),
                    "--status-dir",
                    str(status_dir),
                    "--tts-status-dir",
                    "",
                ]
            )
            server = run_server(args)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/events?once=1"
                with request.urlopen(url, timeout=2) as response:
                    body = response.read().decode("utf-8")
                    content_type = response.headers["Content-Type"]
                    cors_origin = response.headers["Access-Control-Allow-Origin"]
                self.assertIn("text/event-stream", content_type)
                self.assertEqual(cors_origin, "*")
                self.assertIn("id: evt-1", body)
                self.assertIn("event: thought_core.first_message", body)
                self.assertIn('"turn_id":"turn-1"', body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_api_options_allows_cross_origin_sse_auth_headers(self) -> None:
        args = build_parser().parse_args(
            [
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--ai-talk-core-root",
                str(FIXTURES / "ai_talk_core_root"),
            ]
        )
        server = run_server(args)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/events"
            preflight = request.Request(
                url,
                method="OPTIONS",
                headers={
                    "Origin": "http://127.0.0.1:5173",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
            with request.urlopen(preflight, timeout=2) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
                self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_tts_volume_endpoint_reads_and_writes_app_volume(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            tts_status_dir = root / ".cache" / "tts_service"
            args = build_parser().parse_args(
                [
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--ai-talk-core-root",
                    str(FIXTURES / "ai_talk_core_root"),
                    "--tts-status-dir",
                    str(tts_status_dir),
                ]
            )
            server = run_server(args)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/tts/volume"
                with request.urlopen(url, timeout=2) as response:
                    initial = json.loads(response.read().decode("utf-8"))
                self.assertEqual(initial["app_volume"], 1.0)
                self.assertFalse(initial["exists"])

                update = request.Request(
                    url,
                    data=json.dumps({"app_volume": 0.35}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with request.urlopen(update, timeout=2) as response:
                    updated = json.loads(response.read().decode("utf-8"))
                self.assertTrue(updated["ok"])
                self.assertEqual(updated["app_volume"], 0.35)

                volume_file = tts_status_dir / "app_volume.json"
                self.assertTrue(volume_file.exists())
                stored = json.loads(volume_file.read_text(encoding="utf-8"))
                self.assertEqual(stored["app_volume"], 0.35)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_tts_volume_endpoint_proxies_configured_volume_api(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            with volume_api_server() as volume_api:
                args = build_parser().parse_args(
                    [
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "0",
                        "--ai-talk-core-root",
                        str(FIXTURES / "ai_talk_core_root"),
                        "--tts-status-dir",
                        str(root / ".cache" / "tts_service"),
                        "--tts-volume-url",
                        volume_api["url"],
                    ]
                )
                server = run_server(args)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}/api/tts/volume"
                    update = request.Request(
                        url,
                        data=json.dumps({"app_volume": 0.25}).encode("utf-8"),
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    with request.urlopen(update, timeout=2) as http_response:
                        payload = json.loads(http_response.read().decode("utf-8"))

                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["app_volume"], 0.25)
                    self.assertEqual(payload["volume_url"], volume_api["url"])
                    self.assertEqual(volume_api["volume"], 0.25)
                    self.assertEqual(volume_api["last_method"], "POST")
                    self.assertFalse(
                        (root / ".cache" / "tts_service" / "app_volume.json").exists()
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_tts_volume_preview_endpoint_proxies_configured_preview_api(self) -> None:
        with volume_api_server() as volume_api:
            args = build_parser().parse_args(
                [
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--ai-talk-core-root",
                    str(FIXTURES / "ai_talk_core_root"),
                    "--tts-volume-preview-url",
                    volume_api["preview_url"],
                ]
            )
            server = run_server(args)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/tts/volume/preview"
                update = request.Request(
                    url,
                    data=json.dumps({"app_volume": 0.55}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with request.urlopen(update, timeout=2) as http_response:
                    payload = json.loads(http_response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertTrue(payload["preview"])
                self.assertEqual(payload["preview_volume"], 0.55)
                self.assertEqual(payload["volume_preview_url"], volume_api["preview_url"])
                self.assertEqual(volume_api["preview_volume"], 0.55)
                self.assertEqual(volume_api["last_method"], "PREVIEW")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_events_endpoint_includes_ai_talk_core_events(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            ai_root = root / "ai_talk_core"
            (ai_root / ".cache").mkdir(parents=True)
            (ai_root / ".cache" / "events.jsonl").write_text(
                json.dumps(
                    {
                        "turn_id": "turn-1",
                        "event": "stt_final",
                        "timestamp_wall": "2026-04-29T01:02:03Z",
                        "timestamp_monotonic": 12.3,
                        "source": "web",
                        "payload": {"chunk_count": 2},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--ai-talk-core-root",
                    str(ai_root),
                    "--status-dir",
                    str(root / ".cache" / "sword_voice_agent"),
                    "--tts-status-dir",
                    "",
                ]
            )
            server = run_server(args)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/events?once=1"
                with request.urlopen(url, timeout=2) as response:
                    body = response.read().decode("utf-8")
                self.assertIn("event: ai_core.stt_final", body)
                self.assertIn('"chunk_count":2', body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def volume_api_server():
    state = {
        "volume": 1.0,
        "preview_volume": None,
        "last_method": "",
        "url": "",
        "preview_url": "",
    }

    class VolumeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_volume()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/volume/preview":
                state["preview_volume"] = float(payload["app_volume"])
                state["last_method"] = "PREVIEW"
                self.send_preview()
                return
            state["volume"] = float(payload["app_volume"])
            state["last_method"] = "POST"
            self.send_volume()

        def send_preview(self) -> None:
            body = json.dumps(
                {
                    "ok": True,
                    "preview": True,
                    "preview_volume": state["preview_volume"],
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_volume(self) -> None:
            body = json.dumps(
                {
                    "ok": True,
                    "app_volume": state["volume"],
                    "app_volume_file": "memory",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), VolumeHandler)
    state["url"] = f"http://127.0.0.1:{server.server_port}/api/volume"
    state["preview_url"] = f"http://127.0.0.1:{server.server_port}/api/volume/preview"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
