from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.adapters.console_status import (
    ConsoleStatusConfig,
    build_console_status,
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


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
