from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoffError
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent
from sword_voice_agent.apps.watch_handoff_to_thought_core import (
    build_parser,
    format_missing_handoff_message,
    format_watch_start_message,
    handoff_signature,
    resolve_handoff_json_path,
    run_once,
)
from sword_voice_agent.protocol.messages import AgentResponse


class FakeThoughtCoreClient:
    def __init__(self) -> None:
        self.turn_payloads = []

    def send_turn_streaming(self, turn_payload, *, on_event=None):
        self.turn_payloads.append(turn_payload)
        events = [
            ThoughtCoreStreamEvent(
                event_type="assistant.speech_delta",
                turn_id=turn_payload["turn_id"],
                session_id=turn_payload["session_id"],
                seq=1,
                data={"delta": "了解"},
            ),
            ThoughtCoreStreamEvent(
                event_type="assistant.message",
                turn_id=turn_payload["turn_id"],
                session_id=turn_payload["session_id"],
                seq=2,
                data={"speech": "了解です"},
            ),
            ThoughtCoreStreamEvent(
                event_type="turn.completed",
                turn_id=turn_payload["turn_id"],
                session_id=turn_payload["session_id"],
                seq=3,
                data={"status": "success"},
            ),
        ]
        for event in events:
            if on_event is not None:
                on_event(event)
        return AgentResponse(
            text="了解です",
            conversation_id=turn_payload["turn_id"],
            raw={"status": "success"},
        )


class WatchHandoffToThoughtCoreTest(TestCase):
    def test_run_once_sends_and_persists_thought_core_result(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="電気つけて", turn_id="turn-1")
            status_dir = root / ".cache" / "sword_voice_agent"
            client = FakeThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--session-id",
                    "living_room_main",
                    "--status-dir",
                    str(status_dir),
                ]
            )

            result = run_once(args, client=client)

            self.assertFalse(result["skipped"])
            self.assertEqual(client.turn_payloads[0]["text"], "電気つけて")
            self.assertEqual(client.turn_payloads[0]["turn_id"], "turn-1")
            self.assertEqual(result["response"]["text"], "了解です")
            cache_dir = root / ".cache" / "codex"
            saved = json.loads(
                (cache_dir / "web_thought_core_latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["response"]["text"], "了解です")
            self.assertEqual(
                (cache_dir / "web_thought_core_latest.txt").read_text(encoding="utf-8"),
                "了解です",
            )
            latest_status = json.loads(
                (status_dir / "latest_thought_core_response.json").read_text(
                    encoding="utf-8"
                )
            )
            status_events = [
                json.loads(line)
                for line in (status_dir / "events.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(latest_status["turn_id"], "turn-1")
            self.assertEqual(
                [event["type"] for event in status_events],
                [
                    "thought_core.first_message",
                    "thought_core.completed",
                    "thought_core.response",
                ],
            )
            self.assertEqual(status_events[0]["payload"]["speech"], "[redacted]")
            self.assertNotIn("了解です", json.dumps(status_events, ensure_ascii=False))

    def test_run_once_skips_no_speech_placeholder_by_default(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="音声を認識できませんでした。")
            client = FakeThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--status-dir",
                    "",
                ]
            )

            result = run_once(args, client=client)

            self.assertTrue(result["skipped"])
            self.assertEqual(result["skip_reason"], "no_speech_placeholder")
            self.assertEqual(client.turn_payloads, [])

    def test_handoff_signature_changes_when_content_changes(self) -> None:
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "handoff.json"
            path.write_text('{"command":"a"}', encoding="utf-8")
            first = handoff_signature(path)

            path.write_text('{"command":"b"}', encoding="utf-8")
            second = handoff_signature(path)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)

    def test_resolve_handoff_path_rejects_placeholder_root(self) -> None:
        args = build_parser().parse_args(
            [
                "--ai-talk-core-root",
                "<ai_talk_core_root>",
                "--source",
                "web",
            ]
        )

        with self.assertRaises(AiTalkCoreHandoffError):
            resolve_handoff_json_path(args)

    def test_status_messages_explain_watch_and_missing_handoff(self) -> None:
        path = Path("..") / "ai-talk-core" / ".cache" / "codex" / "web_latest.json"

        self.assertIn("監視中", format_watch_start_message(path, skip_existing=True))
        self.assertIn("新規handoffのみ", format_watch_start_message(path, skip_existing=True))
        missing = format_missing_handoff_message(path)
        self.assertIn("handoff JSON が見つかりません", missing)
        self.assertIn("sword-thought-core-handoff", missing)


def write_handoff(root: Path, *, command: str, turn_id: str | None = None) -> Path:
    cache_dir = root / ".cache" / "codex"
    cache_dir.mkdir(parents=True)
    payload = {
        "transcript": command,
        "command": command,
    }
    if turn_id:
        payload["turn_id"] = turn_id
    (cache_dir / "web_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return cache_dir


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
