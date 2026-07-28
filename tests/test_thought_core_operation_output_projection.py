import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.correlation_feedback_contract import (  # noqa: E402
    load_closed_loop_contract,
    materialize_closed_loop_event,
)
from thought_core.event_journal import ThoughtCoreEventJournal  # noqa: E402
from thought_core.operation_output_projection import (  # noqa: E402
    OperationOutputProjection,
    empty_operation_output_projection,
    reduce_operation_output_projection,
    replay_operation_output_projection,
)


def event_candidate(
    event_kind: str,
    *,
    profile: str,
    message_id: str = "msg_projection_001",
    operation_id: str | None = None,
    operation_revision: int | None = None,
) -> dict:
    details = dict(load_closed_loop_contract()["transition_profiles"][profile])
    candidate = {
        "event_kind": event_kind,
        "session_id": "session_projection_001",
        "turn_id": "turn_projection_001",
        "source_authority": "control_output_adapter",
        "details": details,
    }
    if event_kind.startswith("output."):
        candidate["assistant_message_id"] = message_id
        details["output_channel"] = "playback"
    if operation_id is not None:
        candidate["operation_id"] = operation_id
    if operation_revision is not None:
        candidate["operation_revision"] = operation_revision
    return candidate


class ThoughtCoreOperationOutputProjectionTest(TestCase):
    def test_invalid_v1_journal_entry_fails_replay_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "closed-loop-invalid.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "closed-loop-event-journal-entry.v1",
                        "journal_entry_id": "jrn_invalid",
                        "ingest_offset": 1,
                        "recorded_at": "2026-07-28T00:00:00Z",
                        "event": {"schema_version": "closed-loop-correlation-feedback.v1"},
                        "redaction": {
                            "level": "fixed_safe_fields",
                            "raw_text_stored": False,
                            "raw_media_stored": False,
                            "raw_secret_stored": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            journal = ThoughtCoreEventJournal(path=path)

            with self.assertRaises(ValueError):
                journal.replay_closed_loop_entries()

    def test_journal_append_returns_exact_durable_entry_and_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "closed-loop.jsonl"
            journal = ThoughtCoreEventJournal(path=path)
            event = materialize_closed_loop_event(
                event_candidate(
                    "output.dispatch_intent",
                    profile="dispatch_intent_recorded",
                ),
                event_id="evt_projection_intent",
                observed_at="2026-07-28T00:00:00Z",
            )

            first = journal.append_closed_loop_event(event)
            second = journal.append_closed_loop_event(event)
            stored = json.loads(path.read_text(encoding="utf-8").strip())
            stored_line_count = len(path.read_text(encoding="utf-8").splitlines())

        self.assertEqual(first, second)
        self.assertEqual(first, stored)
        self.assertEqual(first["ingest_offset"], 1)
        self.assertTrue(first["journal_entry_id"].startswith("jrn_"))
        self.assertEqual(first["redaction"]["level"], "fixed_safe_fields")
        self.assertEqual(stored_line_count, 1)

    def test_live_ingest_and_replay_use_the_same_reducer(self) -> None:
        with TemporaryDirectory() as tmp:
            journal = ThoughtCoreEventJournal(path=Path(tmp) / "closed-loop.jsonl")
            intent = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    event_candidate(
                        "output.dispatch_intent",
                        profile="dispatch_intent_recorded",
                    ),
                    event_id="evt_projection_intent",
                    observed_at="2026-07-28T00:00:00Z",
                )
            )
            failure = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    event_candidate(
                        "output.feedback",
                        profile="possible_send_timeout",
                    ),
                    event_id="evt_projection_failure",
                    observed_at="2026-07-28T00:00:01Z",
                )
            )

            live = empty_operation_output_projection()
            live = reduce_operation_output_projection(live, intent)
            live = reduce_operation_output_projection(live, failure)
            replayed = replay_operation_output_projection(
                journal.replay_closed_loop_entries()
            )

        self.assertEqual(live, replayed)
        output = live["outputs"]["msg_projection_001"]
        self.assertEqual(output["phase"], "terminal")
        self.assertEqual(output["submission_class"], "may_have_submitted")
        self.assertEqual(output["outcome_class"], "outcome_unknown")

    def test_duplicate_reduction_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            journal = ThoughtCoreEventJournal(path=Path(tmp) / "closed-loop.jsonl")
            entry = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    event_candidate(
                        "output.feedback",
                        profile="possible_send_timeout",
                    ),
                    event_id="evt_projection_duplicate",
                    observed_at="2026-07-28T00:00:00Z",
                )
            )
        once = reduce_operation_output_projection(empty_operation_output_projection(), entry)
        twice = reduce_operation_output_projection(once, entry)

        self.assertEqual(once, twice)
        self.assertEqual(len(twice["recent_feedback"]), 1)

    def test_late_feedback_cannot_revive_terminal_operation(self) -> None:
        with TemporaryDirectory() as tmp:
            journal = ThoughtCoreEventJournal(path=Path(tmp) / "closed-loop.jsonl")
            terminal = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    event_candidate(
                        "operation.transition",
                        profile="possible_send_timeout",
                        operation_id="op_projection_001",
                        operation_revision=1,
                    ),
                    event_id="evt_operation_terminal",
                    observed_at="2026-07-28T00:00:00Z",
                )
            )
            late_candidate = event_candidate(
                "operation.transition",
                profile="submission_ack_needs_feedback",
                operation_id="op_projection_001",
                operation_revision=2,
            )
            late_candidate["details"]["late"] = True
            late = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    late_candidate,
                    event_id="evt_operation_late",
                    observed_at="2026-07-28T00:00:02Z",
                )
            )

        state = reduce_operation_output_projection(empty_operation_output_projection(), terminal)
        state = reduce_operation_output_projection(state, late)
        operation = state["operations"]["op_projection_001"]
        self.assertEqual(operation["phase"], "terminal")
        self.assertEqual(operation["submission_class"], "submitted")

    def test_provider_projection_is_bounded_and_session_scoped(self) -> None:
        with TemporaryDirectory() as tmp:
            journal = ThoughtCoreEventJournal(path=Path(tmp) / "closed-loop.jsonl")
            entries = []
            for index in range(12):
                candidate = event_candidate(
                    "output.feedback",
                    profile="possible_send_timeout",
                    message_id=f"msg_projection_{index:03d}",
                )
                entries.append(
                    journal.append_closed_loop_event(
                        materialize_closed_loop_event(
                            candidate,
                            event_id=f"evt_projection_{index:03d}",
                            observed_at=f"2026-07-28T00:00:{index:02d}Z",
                        )
                    )
                )
            projection = OperationOutputProjection(entries)

        sections = projection.provider_sections(session_id="session_projection_001")
        self.assertEqual(len(sections["feedback_context"]), 8)
        self.assertEqual(sections["feedback_context"][0]["assistant_message_id"], "msg_projection_011")
        self.assertEqual(
            projection.provider_sections(session_id="different_session")["feedback_context"],
            [],
        )

    def test_active_operation_shape_keeps_independent_state_classes(self) -> None:
        with TemporaryDirectory() as tmp:
            journal = ThoughtCoreEventJournal(path=Path(tmp) / "closed-loop.jsonl")
            candidate = event_candidate(
                "operation.transition",
                profile="dispatch_intent_recorded",
                operation_id="op_projection_active_001",
                operation_revision=1,
            )
            candidate["source_authority"] = "thought_core"
            entry = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    candidate,
                    event_id="evt_projection_active_operation",
                    observed_at="2026-07-28T00:00:00Z",
                )
            )
            projection = OperationOutputProjection((entry,))

        active = projection.provider_sections(
            session_id="session_projection_001"
        )["active_operations"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["operation_id"], "op_projection_active_001")
        self.assertEqual(active[0]["phase"], "dispatching")
        self.assertEqual(active[0]["submission_class"], "not_submitted")
        self.assertEqual(active[0]["receipt_class"], "none")
        self.assertEqual(active[0]["cleanup_class"], "not_required")
