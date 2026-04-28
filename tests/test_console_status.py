from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sword_voice_agent.adapters.console_status import (
    ConsoleStatusConfig,
    build_console_status,
    fetch_dify_api,
    fetch_input_gate,
    normalize_module_statuses,
)


class ConsoleStatusTest(TestCase):
    def test_builds_status_from_ai_talk_core_and_dify_cache(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            cache_dir = root / ".cache" / "codex"
            cache_dir.mkdir(parents=True)
            (cache_dir / "web_latest.json").write_text(
                json.dumps(
                    {
                        "transcript": "今日はいい天気ですね",
                        "command": "今日はいい天気ですね",
                        "turn_id": "turn-1",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (cache_dir / "web_dify_latest.json").write_text(
                json.dumps(
                    {
                        "request": {
                            "text": "今日はいい天気ですね",
                            "context": {"turn_id": "turn-1"},
                        },
                        "response": {
                            "text": "はい、いい天気ですね。",
                            "conversation_id": "conv-1",
                            "message_id": "msg-1",
                            "raw": {
                                "metadata": {
                                    "usage": {
                                        "total_tokens": 42,
                                        "total_price": "0.0001",
                                        "currency": "USD",
                                        "latency": 1.2,
                                    }
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            gesture_path = root / "gesture.json"
            gesture_path.write_text(
                json.dumps(
                    {
                        "sequence": 2,
                        "from": "127.0.0.1:55218",
                        "response": {
                            "voice_state": {
                                "phase": "armed",
                                "mic_enabled": True,
                            },
                            "gate_decision": {
                                "raw_active": True,
                                "confidence": 0.93,
                                "reason": "stable",
                            },
                            "voice_control_command": {
                                "action": "none",
                                "turn_id": "turn-1",
                            },
                            "input_gate_response": {"ok": True},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            status = build_console_status(
                ConsoleStatusConfig(
                    ai_talk_core_root=root,
                    gesture_status_json=gesture_path,
                    status_dir=None,
                )
            )

            self.assertTrue(status["health"]["handoff"])
            self.assertTrue(status["health"]["dify"])
            self.assertTrue(status["health"]["gesture"])
            self.assertEqual(status["voice"]["command"], "今日はいい天気ですね")
            self.assertEqual(status["dify"]["answer"], "はい、いい天気ですね。")
            self.assertEqual(status["dify"]["usage"]["total_tokens"], 42)
            self.assertEqual(status["dify"]["turn_id"], "turn-1")
            self.assertTrue(status["gesture"]["raw_active"])
            self.assertEqual(status["gesture"]["confidence"], 0.93)
            self.assertEqual(status["gesture"]["turn_id"], "turn-1")
            self.assertEqual(status["voice"]["turn_id"], "turn-1")

    def test_includes_status_store_events(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            cache_dir = root / ".cache" / "codex"
            cache_dir.mkdir(parents=True)
            (cache_dir / "web_latest.json").write_text(
                json.dumps({"transcript": "テスト", "command": "テスト"}),
                encoding="utf-8",
            )
            status_dir = root / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            (status_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "dify.response",
                        "timestamp": 1.0,
                        "source": "test",
                        "turn_id": "turn-1",
                        "data": {"response_text": "応答"},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (status_dir / "latest_voice_turn.json").write_text(
                json.dumps(
                    {
                        "type": "latest_voice_turn",
                        "timestamp": 1.0,
                        "turn_id": "turn-1",
                        "voice_state": {"mic_enabled": True},
                        "voice_control_command": {"turn_id": "turn-1"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            status = build_console_status(
                ConsoleStatusConfig(
                    ai_talk_core_root=root,
                    status_dir=status_dir,
                )
            )

            self.assertEqual(status["events"][0]["turn_id"], "turn-1")
            self.assertEqual(status["voice"]["turn_id"], "turn-1")

    def test_includes_module_statuses(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            status_dir = root / ".cache" / "sword_voice_agent"
            modules_dir = status_dir / "modules"
            modules_dir.mkdir(parents=True)
            (modules_dir / "gesture_udp_receiver.json").write_text(
                json.dumps(
                    {
                        "type": "module_status",
                        "name": "gesture_udp_receiver",
                        "label": "Gesture UDP receiver",
                        "state": "running",
                        "detail": "127.0.0.1:8765",
                        "timestamp": 9.0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            status = build_console_status(
                ConsoleStatusConfig(
                    ai_talk_core_root=root,
                    status_dir=status_dir,
                    module_stale_after_s=6.0,
                )
            )

            modules = {item["name"]: item for item in status["modules"]}
            self.assertEqual(modules["gesture_udp_receiver"]["state"], "stale")
            self.assertEqual(modules["gesture_udp_receiver"]["detail"], "127.0.0.1:8765")
            self.assertEqual(modules["dify_api"]["state"], "missing")
            self.assertEqual(modules["console"]["state"], "running")

    def test_dify_api_module_uses_reachability(self) -> None:
        modules = normalize_module_statuses(
            {},
            input_gate={"available": False},
            dify_api={
                "available": True,
                "url": "http://localhost:8080/v1",
                "status": 404,
                "error": None,
            },
            timestamp=10.0,
            stale_after_s=6.0,
        )

        by_name = {item["name"]: item for item in modules}
        self.assertEqual(by_name["dify_api"]["state"], "running")
        self.assertEqual(by_name["dify_api"]["detail"], "http://localhost:8080/v1")

    def test_dify_api_module_reports_unreachable_endpoint(self) -> None:
        modules = normalize_module_statuses(
            {},
            input_gate={"available": False},
            dify_api={
                "available": False,
                "url": "http://localhost:8080/v1",
                "status": None,
                "error": "connection refused",
            },
            timestamp=10.0,
            stale_after_s=6.0,
        )

        by_name = {item["name"]: item for item in modules}
        self.assertEqual(by_name["dify_api"]["state"], "error")
        self.assertIn("connection refused", by_name["dify_api"]["detail"])

    def test_input_gate_status_rejects_file_url(self) -> None:
        result = fetch_input_gate(
            ConsoleStatusConfig(
                ai_talk_core_root=Path("."),
                input_gate_url="file:///tmp/input-gate.json",
            )
        )

        self.assertFalse(result["available"])
        self.assertIn("http(s)", result["error"])

    @patch("sword_voice_agent.adapters.console_status.request.urlopen")
    def test_input_gate_status_sends_local_api_token_header(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"input_gate": {}}'
        urlopen.return_value = response

        result = fetch_input_gate(
            ConsoleStatusConfig(
                ai_talk_core_root=Path("."),
                input_gate_url="http://127.0.0.1:8000/api/input-gate",
                input_gate_token="local-token",
            )
        )

        self.assertTrue(result["available"])
        request_arg = urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request_arg.header_items()}
        self.assertEqual(headers["x-ai-core-token"], "local-token")

    def test_dify_status_rejects_plain_http_remote_url(self) -> None:
        result = fetch_dify_api(
            ConsoleStatusConfig(
                ai_talk_core_root=Path("."),
                dify_base_url="http://dify.example.test/v1",
            )
        )

        self.assertFalse(result["available"])
        self.assertIn("http only for loopback", result["error"])

    def test_redacts_sensitive_console_status(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            cache_dir = root / ".cache" / "codex"
            cache_dir.mkdir(parents=True)
            (cache_dir / "web_latest.json").write_text(
                json.dumps(
                    {
                        "transcript": "音声全文",
                        "command": "コマンド",
                        "turn_id": "turn-1",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (cache_dir / "web_dify_latest.json").write_text(
                json.dumps(
                    {
                        "request": {
                            "text": "コマンド",
                            "context": {"turn_id": "turn-1"},
                        },
                        "response": {
                            "text": "回答",
                            "conversation_id": "conv-1",
                            "message_id": "msg-1",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status_dir = root / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            (status_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "dify.response",
                        "timestamp": 1.0,
                        "source": "test",
                        "turn_id": "turn-1",
                        "payload": {
                            "request_text": "コマンド",
                            "response_text": "回答",
                            "conversation_id": "conv-1",
                            "message_id": "msg-1",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            status = build_console_status(
                ConsoleStatusConfig(
                    ai_talk_core_root=root,
                    status_dir=status_dir,
                    redact_sensitive=True,
                )
            )

            self.assertTrue(status["redacted"])
            self.assertEqual(status["paths"]["ai_talk_core_root"], "[redacted]")
            self.assertEqual(status["voice"]["transcript"], "[redacted]")
            self.assertEqual(status["voice"]["command"], "[redacted]")
            self.assertEqual(status["dify"]["answer"], "[redacted]")
            self.assertEqual(status["dify"]["conversation_id"], "[redacted]")
            self.assertEqual(status["events"][0]["turn_id"], "[redacted]")
            self.assertEqual(status["events"][0]["payload"]["response_text"], "[redacted]")


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
