from contextlib import contextmanager
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


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
