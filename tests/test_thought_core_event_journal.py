import json
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.event_journal import journal_entry_from_event  # noqa: E402
from thought_core.execution_deadline import (  # noqa: E402
    TurnDeadlineExceeded,
    issue_turn_execution_deadline,
)
from thought_core.server import _write_journal_safely, create_server  # noqa: E402


TURN = {
    "text": "電気つけて",
    "turn_id": "turn_event_journal_001",
    "session_id": "event_journal_session",
    "locale": "ja-JP",
    "context_refs": {},
}


class ThoughtCoreEventJournalTest(TestCase):
    def test_expired_turn_deadline_blocks_journal_write(self) -> None:
        class RecordingJournal:
            def __init__(self) -> None:
                self.writes = 0

            def write_many(self, events):  # type: ignore[no-untyped-def]
                self.writes += 1

        clock = {"now": 10.0}
        journal = RecordingJournal()
        with patch(
            "thought_core.execution_deadline.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            deadline = issue_turn_execution_deadline(11.0)
            clock["now"] = 12.0
            with self.assertRaises(TurnDeadlineExceeded):
                _write_journal_safely(
                    journal,
                    [{"type": "assistant.message"}],
                    execution_deadline=deadline,
                )

        self.assertEqual(journal.writes, 0)

    def test_event_journal_entry_keeps_summary_without_raw_text(self) -> None:
        entry = journal_entry_from_event(
            {
                "schema_version": "thought-core.event.v0",
                "event_id": "evt_test_001",
                "turn_id": "turn_test_001",
                "session_id": "session_test",
                "seq": 1,
                "timestamp": "2026-06-09T00:00:00Z",
                "source": "thought-core",
                "type": "assistant.message",
                "data": {
                    "speech": "ここは保存しない発話本文",
                    "display": "ここも保存しない表示本文",
                    "status": "ok",
                    "reason": "raw user text 電気つけて Bearer should-not-leak",
                    "review_basis": "target_state",
                    "review_basis_code": "target_state",
                    "preview_error": "sk-testsecret12345678",
                    "authorization": "Bearer should-not-leak",
                    "result": {
                        "status": "failed",
                        "confirmation_token": "must-not-appear",
                    },
                },
            }
        )
        serialized = json.dumps(entry, ensure_ascii=False)

        self.assertEqual(entry["schema_version"], "thought-core.event-journal-entry.v0")
        self.assertEqual(entry["event_type"], "assistant.message")
        self.assertEqual(entry["summary"]["status"], "ok")
        self.assertNotIn("review_basis", entry["summary"])
        self.assertEqual(entry["summary"]["review_basis_code"], "target_state")
        self.assertTrue(entry["summary"]["review_basis_present"])
        self.assertEqual(entry["summary"]["result_status"], "failed")
        self.assertTrue(entry["summary"]["reason_present"])
        self.assertTrue(entry["summary"]["preview_error_present"])
        self.assertNotIn("confirmation_token", entry["summary"]["data_keys"])
        self.assertNotIn("confirmation_token", entry["summary"]["result_keys"])
        self.assertTrue(entry["summary"]["speech_present"])
        self.assertEqual(entry["summary"]["speech_chars"], len("ここは保存しない発話本文"))
        self.assertNotIn("ここは保存しない発話本文", serialized)
        self.assertNotIn("ここも保存しない表示本文", serialized)
        self.assertNotIn("電気つけて", serialized)
        self.assertNotIn("should-not-leak", serialized)
        self.assertNotIn("sk-testsecret", serialized)
        self.assertNotIn("must-not-appear", serialized)

    def test_top_level_refs_are_shape_filtered(self) -> None:
        entry = journal_entry_from_event(
            {
                "schema_version": "thought-core.event.v0",
                "event_id": "evt_test_unsafe_refs",
                "turn_id": "raw user text 電気つけて",
                "session_id": "Bearer should-not-leak",
                "seq": "not-an-int",
                "timestamp": "2026-06-09T00:00:00Z",
                "source": "thought-core",
                "type": "turn.completed",
                "data": {"status": "ok"},
            }
        )
        serialized = json.dumps(entry, ensure_ascii=False)

        self.assertEqual(entry["turn_id"], "")
        self.assertEqual(entry["session_id"], "")
        self.assertIsNone(entry["seq"])
        self.assertNotIn("電気つけて", serialized)
        self.assertNotIn("should-not-leak", serialized)

    def test_post_turn_appends_redacted_events_when_journal_path_is_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "thought-core-events.jsonl"
            with patch.dict(
                "os.environ",
                {"THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path)},
                clear=False,
            ):
                server = create_server("127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                req = request.Request(
                    f"http://127.0.0.1:{port}/turn",
                    data=json.dumps(TURN).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with request.urlopen(req, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            lines = journal_path.read_text(encoding="utf-8").splitlines()
            journal_events = [json.loads(line) for line in lines]
            journal_event_types = {event["event_type"] for event in journal_events}
            serialized = json.dumps(journal_events, ensure_ascii=False)

        self.assertGreater(len(payload["events"]), 0)
        self.assertEqual(len(journal_events), len(payload["events"]))
        self.assertIn("input.acknowledged", journal_event_types)
        self.assertIn("turn.completed", journal_event_types)
        self.assertNotIn("電気つけて", serialized)
        self.assertNotIn("了解", serialized)
        self.assertNotIn("confirmation_token", serialized)

    def test_post_turn_stream_appends_redacted_events_when_journal_path_is_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "thought-core-stream-events.jsonl"
            with patch.dict(
                "os.environ",
                {"THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path)},
                clear=False,
            ):
                server = create_server("127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                req = request.Request(
                    f"http://127.0.0.1:{port}/turn/stream",
                    data=json.dumps(TURN).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    method="POST",
                )

                with request.urlopen(req, timeout=5) as response:
                    sse_payload = response.read().decode("utf-8")
                    content_type = response.headers["Content-Type"]
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            lines = journal_path.read_text(encoding="utf-8").splitlines()
            journal_events = [json.loads(line) for line in lines]
            journal_event_types = {event["event_type"] for event in journal_events}
            serialized = json.dumps(journal_events, ensure_ascii=False)

        self.assertEqual(content_type, "text/event-stream; charset=utf-8")
        self.assertIn("event: input.acknowledged", sse_payload)
        self.assertIn("event: turn.completed", sse_payload)
        self.assertGreater(len(journal_events), 0)
        self.assertIn("input.acknowledged", journal_event_types)
        self.assertIn("turn.completed", journal_event_types)
        self.assertNotIn("電気つけて", serialized)
        self.assertNotIn("了解", serialized)
        self.assertNotIn("confirmation_token", serialized)

    def test_journal_write_failure_does_not_break_turn_response(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp)
            with patch.dict(
                "os.environ",
                {"THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path)},
                clear=False,
            ):
                server = create_server("127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                req = request.Request(
                    f"http://127.0.0.1:{port}/turn",
                    data=json.dumps(TURN).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with request.urlopen(req, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertGreater(len(payload["events"]), 0)
        self.assertEqual(payload["events"][-1]["type"], "turn.completed")

    def test_journal_write_failure_does_not_break_stream_response(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp)
            with patch.dict(
                "os.environ",
                {"THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path)},
                clear=False,
            ):
                server = create_server("127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                req = request.Request(
                    f"http://127.0.0.1:{port}/turn/stream",
                    data=json.dumps(TURN).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    method="POST",
                )

                with request.urlopen(req, timeout=5) as response:
                    sse_payload = response.read().decode("utf-8")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertIn("event: input.acknowledged", sse_payload)
        self.assertIn("event: turn.completed", sse_payload)
