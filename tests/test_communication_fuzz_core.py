from __future__ import annotations

import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import TestCase
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(SRC_ROOT))
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402

from sword_voice_agent.adapters.status_store import StatusStore  # noqa: E402
from sword_voice_agent.adapters.thought_core import (  # noqa: E402
    ThoughtCoreClientError,
    ThoughtCoreStreamEvent,
    iter_sse_json_payloads,
)
from sword_voice_agent.apps.console_server import format_sse_event  # noqa: E402


BASE_TURN = {
    "text": "turn on the light",
    "turn_id": "turn_fuzz_001",
    "session_id": "living_room",
    "locale": "ja-JP",
    "context_refs": {},
}


class CentralCommunicationFuzzTest(TestCase):
    def test_turn_payload_mutations_stop_before_tool_side_effects(self) -> None:
        mutated_payloads = [
            ("missing_text", {key: value for key, value in BASE_TURN.items() if key != "text"}),
            ("blank_text", {**BASE_TURN, "text": "   "}),
            ("array_text", {**BASE_TURN, "text": ["turn on"]}),
            ("blank_turn_id", {**BASE_TURN, "turn_id": "\n\t"}),
            ("object_turn_id", {**BASE_TURN, "turn_id": {"id": "turn"}}),
            ("missing_session_id", {key: value for key, value in BASE_TURN.items() if key != "session_id"}),
            ("array_locale", {**BASE_TURN, "locale": ["ja-JP"]}),
            ("array_context_refs", {**BASE_TURN, "context_refs": ["env"]}),
        ]
        tools = MockThoughtTools()
        loop = ThoughtLoop(tools=tools)

        for label, payload in mutated_payloads:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    loop.run_dicts(payload)

        self.assertEqual(tools.execute_calls, [])
        self.assertEqual(tools.state_query_feedback_calls, [])

    def test_sse_parser_mutations_are_rejected_or_ignored_without_partial_events(self) -> None:
        ignored_payloads = list(
            iter_sse_json_payloads(
                [
                    b": keep-alive\n",
                    b"data: [DONE]\n",
                    b"\n",
                    b"data:   \n",
                    b"\n",
                ]
            )
        )
        self.assertEqual(ignored_payloads, [])

        malformed_frames = [
            [b"data: [1, 2]\n", b"\n"],
            [b"data: {bad json}\n", b"\n"],
        ]
        for frame in malformed_frames:
            with self.subTest(frame=frame):
                with self.assertRaises(ThoughtCoreClientError):
                    list(iter_sse_json_payloads(frame))

        with self.assertRaises(ThoughtCoreClientError):
            ThoughtCoreStreamEvent.from_payload(
                {
                    "type": "assistant.message",
                    "turn_id": "turn-1",
                    "session_id": "living",
                    "data": ["not", "an", "object"],
                }
            )

    def test_state_query_feedback_idempotency_key_normalizes_mutated_ids(self) -> None:
        loop = ThoughtLoop()
        turn = TurnInput.from_mapping(
            {
                **BASE_TURN,
                "turn_id": "turn with spaces",
                "session_id": "living room\nmain",
            }
        )
        pending = {
            "snapshot_id": "../snap one\n<script>",
            "issue_id": "ticket-light-001",
            "action_id": "light_on",
        }

        key = loop._state_query_feedback_idempotency_key(  # noqa: SLF001
            turn,
            pending,
            " on\r\n",
        )
        duplicate_key = loop._state_query_feedback_idempotency_key(  # noqa: SLF001
            turn,
            pending,
            " on\r\n",
        )

        self.assertEqual(key, duplicate_key)
        self.assertLessEqual(len(key), 200)
        self.assertTrue(key.startswith("state-query-feedback:"))
        for unsafe in ("\n", "\r", "\t", "/", "<", ">"):
            self.assertNotIn(unsafe, key)

    def test_console_sse_formatter_prevents_event_header_injection(self) -> None:
        body = format_sse_event(
            {
                "event_id": "evt-1\nid: forged",
                "type": "thought_core.response\nevent: forged",
                "turn_id": "turn-1",
                "payload": {"detail": "line1\nline2"},
            }
        )

        self.assertIn("id: evt-1id: forged\n", body)
        self.assertIn("event: thought_core.responseevent: forged\n", body)
        self.assertNotIn("\nid: forged\n", body)
        self.assertNotIn("\nevent: forged\n", body)
        data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
        self.assertGreaterEqual(len(data_lines), 1)
        decoded = json.loads("".join(line.removeprefix("data: ") for line in data_lines))
        self.assertEqual(decoded["turn_id"], "turn-1")

    def test_status_event_cursor_tolerates_missing_duplicate_and_unknown_ids(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp, max_events=10)
            store.events_path.parent.mkdir(parents=True, exist_ok=True)
            events = [
                {"event_id": "evt-a", "type": "first", "turn_id": "turn-1"},
                {"type": "missing-id", "turn_id": "turn-1"},
                {"event_id": "evt-a", "type": "duplicate", "turn_id": "turn-1"},
                {"event_id": "evt-b", "type": "next", "turn_id": "turn-2"},
            ]
            store.events_path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )

            after_first = store.read_events_after("evt-a", limit=10)
            after_unknown = store.read_events_after("missing", limit=10)

            self.assertEqual(
                [event["type"] for event in after_first],
                ["missing-id", "duplicate", "next"],
            )
            self.assertEqual([event["type"] for event in after_unknown], ["first", "missing-id", "duplicate", "next"])


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
