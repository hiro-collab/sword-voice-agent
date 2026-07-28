import json
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.correlation_feedback_contract import (  # noqa: E402
    load_closed_loop_contract,
    materialize_closed_loop_event,
    materialize_closed_loop_output_ingress,
)
from thought_core.agentic_turn_runtime_provider import _decision_input_payload  # noqa: E402
from thought_core.event_journal import ThoughtCoreEventJournal  # noqa: E402
from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.operation_output_projection import (  # noqa: E402
    OperationOutputProjection,
)
from thought_core.server import create_server  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402
from sword_voice_agent.apps.watch_handoff_to_thought_core import (  # noqa: E402
    ClosedLoopOutputRecorder,
    dispatch_with_closed_loop_barrier,
)


class CapturingConversationProvider:
    def __init__(self) -> None:
        self.requests = []

    def decide(self, provider_request):  # type: ignore[no-untyped-def]
        self.requests.append(provider_request)
        return {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {
                "speech": "Bounded synthetic assistant response.",
                "display": "Bounded synthetic assistant response.",
            },
        }


class DirectJournalClosedLoopClient:
    def __init__(
        self,
        journal: ThoughtCoreEventJournal,
        projection: OperationOutputProjection,
    ) -> None:
        self.journal = journal
        self.projection = projection

    def append_closed_loop_event(self, candidate):  # type: ignore[no-untyped-def]
        event = materialize_closed_loop_output_ingress(candidate)
        entry = self.journal.append_closed_loop_event(event)
        self.projection.ingest(entry)
        return {
            "ok": True,
            "event_id": entry["event"]["event_id"],
            "journal_entry_id": entry["journal_entry_id"],
            "ingest_offset": entry["ingest_offset"],
        }


class CrashAfterDurableStartPoster:
    def __init__(self) -> None:
        self.post_calls = 0
        self.result_callbacks = 0
        self.result_callback_supplied = False
        self.network_sends = 0

    def post(  # type: ignore[no-untyped-def]
        self,
        _body,
        *,
        before_send=None,
        on_result=None,
    ) -> bool:
        self.post_calls += 1
        self.result_callback_supplied = on_result is not None
        if before_send is None or not before_send():
            return False
        # Deterministic crash boundary: no urlopen and no result callback.
        return True


def turn(turn_id: str, text: str) -> dict:
    return {
        "text": text,
        "turn_id": turn_id,
        "session_id": "session_closed_loop_integration",
        "locale": "ja-JP",
        "context_refs": {},
    }


def output_event_candidate(
    event_kind: str,
    *,
    profile_name: str,
    assistant_message_id: str,
) -> dict:
    return {
        "event_kind": event_kind,
        "session_id": "session_closed_loop_integration",
        "turn_id": "turn_closed_loop_first",
        "assistant_message_id": assistant_message_id,
        "source_authority": (
            "control_output_adapter"
            if event_kind == "output.dispatch_intent"
            else "playback_transport"
        ),
        "details": {
            **load_closed_loop_contract()["transition_profiles"][profile_name],
            "output_channel": "playback",
            "component": "deterministic_fake_playback",
        },
    }


def http_output_event_candidate(
    event_kind: str,
    *,
    profile_name: str,
    assistant_message_id: str,
    output_channel: str,
    causal_parent_event_id: str | None = None,
) -> dict:
    contract = load_closed_loop_contract()
    channel = contract["http_output_ingress"]["channels"][output_channel]
    candidate = {
        "event_kind": event_kind,
        "session_id": "session_closed_loop_integration",
        "turn_id": "turn_closed_loop_first",
        "assistant_message_id": assistant_message_id,
        "details": {
            **contract["transition_profiles"][profile_name],
            "output_channel": output_channel,
            "component": channel["component"],
        },
    }
    if causal_parent_event_id is not None:
        candidate["causal_parent_event_id"] = causal_parent_event_id
    return candidate


def post_closed_loop(server, candidate: dict) -> dict:  # type: ignore[no-untyped-def]
    req = request.Request(
        f"http://127.0.0.1:{server.server_address[1]}/feedback/closed-loop",
        data=json.dumps(candidate).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def seed_authoritative_assistant_event(
    journal_path: Path,
    assistant_message_id: str,
    *,
    session_id: str = "session_closed_loop_integration",
    turn_id: str = "turn_closed_loop_first",
) -> None:
    ThoughtCoreEventJournal(path=journal_path).write_event(
        {
            "schema_version": "thought-core.event.v1",
            "event_id": f"evt_authoritative_{assistant_message_id}",
            "type": "assistant.speech_delta",
            "timestamp": "2026-07-28T00:00:00Z",
            "session_id": session_id,
            "turn_id": turn_id,
            "seq": 1,
            "data": {"assistant_message_id": assistant_message_id},
        }
    )


class ThoughtCoreClosedLoopIntegrationTest(TestCase):
    def test_secret_like_strings_reject_before_journal_projection_and_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-secret-boundary.jsonl"
            journal = ThoughtCoreEventJournal(path=journal_path)
            projection = OperationOutputProjection()
            initial_projection = projection.snapshot()
            canonical = materialize_closed_loop_event(
                output_event_candidate(
                    "output.dispatch_intent",
                    profile_name="dispatch_intent_recorded",
                    assistant_message_id="msg_closed_loop_secret_boundary",
                ),
                event_id="evt_closed_loop_secret_boundary",
                observed_at="2026-07-28T00:00:00Z",
            )
            rejected_events = (
                {
                    **canonical,
                    "session_id": "session_sk-syntheticEnvelopeSecret123",
                },
                {
                    **canonical,
                    "details": {
                        **canonical["details"],
                        "component": "component_sk-syntheticDetailSecret123",
                    },
                },
            )
            for event in rejected_events:
                with self.subTest(location="journal"):
                    before = json.dumps(event, sort_keys=True)
                    with self.assertRaisesRegex(
                        ValueError,
                        "secret_like_string_rejected",
                    ):
                        journal.append_closed_loop_event(event)
                    self.assertEqual(json.dumps(event, sort_keys=True), before)

            ingress_candidate = http_output_event_candidate(
                "output.dispatch_intent",
                profile_name="dispatch_intent_recorded",
                assistant_message_id="msg_closed_loop_secret_ingress",
                output_channel="display",
            )
            ingress_candidate["session_id"] = (
                "session_sk-syntheticIngressSecret123"
            )
            with self.assertRaisesRegex(ValueError, "secret_like_string_rejected"):
                materialize_closed_loop_output_ingress(ingress_candidate)

            self.assertFalse(journal_path.exists())
            self.assertEqual(projection.snapshot(), initial_projection)
            provider = CapturingConversationProvider()
            loop = ThoughtLoop(
                tools=MockThoughtTools(),
                agentic_turn_provider=provider,
                operation_output_projection=projection,
            )
            loop.run_dicts(
                turn("turn_after_secret_rejection", "Continue with unchanged context.")
            )

        self.assertEqual(
            provider.requests[0].predecision_context.feedback_context.items,
            (),
        )

    def test_durable_send_attempt_survives_crash_replay_without_resend(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-crash-window.jsonl"
            journal = ThoughtCoreEventJournal(path=journal_path)
            live_projection = OperationOutputProjection()
            client = DirectJournalClosedLoopClient(journal, live_projection)
            recorder = ClosedLoopOutputRecorder(
                client,
                session_id="session_closed_loop_integration",
                turn_id="turn_closed_loop_crash_window",
            )
            poster = CrashAfterDurableStartPoster()

            accepted = dispatch_with_closed_loop_barrier(
                poster=poster,  # type: ignore[arg-type]
                recorder=recorder,
                body=b"{}",
                assistant_message_id="msg_closed_loop_crash_window",
                output_channel="display",
                component="aituber_direct_send",
                on_error=self.fail,
            )
            journal_before_replay = journal_path.read_bytes()
            entries = journal.replay_closed_loop_entries()

            self.assertTrue(accepted)
            self.assertEqual(len(entries), 2)
            send_attempt = entries[1]["event"]
            self.assertEqual(send_attempt["source_authority"], "control_output_adapter")
            self.assertEqual(send_attempt["details"]["outcome_class"], "outcome_unknown")
            self.assertEqual(
                send_attempt["details"]["submission_class"],
                "may_have_submitted",
            )
            self.assertEqual(poster.post_calls, 1)
            self.assertEqual(poster.network_sends, 0)
            self.assertEqual(poster.result_callbacks, 0)
            self.assertTrue(poster.result_callback_supplied)

            replay_projection = OperationOutputProjection.from_journal(journal)
            self.assertEqual(journal_path.read_bytes(), journal_before_replay)
            provider = CapturingConversationProvider()
            replay_loop = ThoughtLoop(
                tools=MockThoughtTools(),
                agentic_turn_provider=provider,
                operation_output_projection=replay_projection,
            )
            replay_loop.run_dicts(
                turn("turn_after_crash_window", "Continue without resending output.")
            )
            replay_feedback = dict(
                provider.requests[0].predecision_context.feedback_context.items[0]
            )

        self.assertEqual(replay_feedback["outcome_class"], "outcome_unknown")
        self.assertEqual(replay_feedback["submission_class"], "may_have_submitted")
        self.assertEqual(replay_feedback["phase"], "dispatching")
        self.assertEqual(replay_feedback["source_authority"], "control_output_adapter")
        self.assertEqual(poster.post_calls, 1)
        self.assertEqual(poster.network_sends, 0)
        self.assertEqual(poster.result_callbacks, 0)
        self.assertTrue(poster.result_callback_supplied)

    def test_failure_reaches_next_turn_and_replay_restores_identical_feedback(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop.jsonl"
            journal = ThoughtCoreEventJournal(path=journal_path)
            projection = OperationOutputProjection()
            provider = CapturingConversationProvider()
            loop = ThoughtLoop(
                tools=MockThoughtTools(),
                agentic_turn_provider=provider,
                operation_output_projection=projection,
            )

            first_events = loop.run_dicts(
                turn("turn_closed_loop_first", "Give a short synthetic response.")
            )
            self.assertEqual(provider.requests[0].predecision_context.feedback_context.items, ())
            assistant_event = next(
                event for event in first_events if event["type"] == "assistant.message"
            )
            assistant_message_id = assistant_event["data"]["assistant_message_id"]
            self.assertNotEqual(assistant_message_id, assistant_event["event_id"])

            intent = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    output_event_candidate(
                        "output.dispatch_intent",
                        profile_name="dispatch_intent_recorded",
                        assistant_message_id=assistant_message_id,
                    ),
                    event_id="evt_closed_loop_integration_intent",
                    observed_at="2026-07-28T00:00:00Z",
                )
            )
            projection.ingest(intent)
            failed_candidate = output_event_candidate(
                "output.feedback",
                profile_name="possible_send_timeout",
                assistant_message_id=assistant_message_id,
            )
            failed_candidate["causal_parent_event_id"] = intent["event"]["event_id"]
            failed = journal.append_closed_loop_event(
                materialize_closed_loop_event(
                    failed_candidate,
                    event_id="evt_closed_loop_integration_failure",
                    observed_at="2026-07-28T00:00:01Z",
                )
            )
            projection.ingest(failed)

            correction = "いや、応答はそのままで、再生未確認として扱って。"
            loop.run_dicts(turn("turn_closed_loop_second", correction))
            next_context = provider.requests[1].predecision_context
            self.assertEqual(next_context.latest_user_correction, correction)
            self.assertEqual(next_context.feedback_context.status, "available")
            self.assertEqual(len(next_context.feedback_context.items), 1)
            next_feedback = dict(next_context.feedback_context.items[0])
            self.assertEqual(next_feedback["assistant_message_id"], assistant_message_id)
            self.assertEqual(next_feedback["outcome_class"], "outcome_unknown")
            self.assertEqual(next_feedback["submission_class"], "may_have_submitted")
            self.assertNotEqual(next_feedback.get("outcome_class"), "succeeded")
            serialized_context = _decision_input_payload(provider.requests[1])[
                "predecision_context"
            ]
            self.assertEqual(
                serialized_context["feedback_context"]["items"],  # type: ignore[index]
                [next_feedback],
            )

            journal_bytes_before_replay = journal_path.read_bytes()
            replay_projection = OperationOutputProjection.from_journal(journal)
            self.assertEqual(journal_path.read_bytes(), journal_bytes_before_replay)
            replay_provider = CapturingConversationProvider()
            replay_loop = ThoughtLoop(
                tools=MockThoughtTools(),
                agentic_turn_provider=replay_provider,
                operation_output_projection=replay_projection,
            )
            replay_loop.run_dicts(
                turn("turn_closed_loop_after_restart", "Use the previous safe feedback.")
            )
            replay_feedback = tuple(
                dict(item)
                for item in replay_provider.requests[0].predecision_context.feedback_context.items
            )

        self.assertEqual(replay_feedback, (next_feedback,))

    def test_enabled_http_ingest_derives_display_and_tts_authority(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-http.jsonl"
            seed_authoritative_assistant_event(
                journal_path,
                "msg_closed_loop_http_display",
            )
            with patch.dict(
                "os.environ",
                {
                    "THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED": "1",
                    "THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path),
                    "THOUGHT_CORE_EVENT_JOURNAL_DIR": "",
                },
                clear=False,
            ):
                server = create_server(
                    "127.0.0.1",
                    0,
                    thought_loop=ThoughtLoop(
                        tools=MockThoughtTools(),
                        agentic_turn_provider=CapturingConversationProvider(),
                    ),
                )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                display_intent = post_closed_loop(
                    server,
                    http_output_event_candidate(
                        "output.dispatch_intent",
                        profile_name="dispatch_intent_recorded",
                        assistant_message_id="msg_closed_loop_http_display",
                        output_channel="display",
                    ),
                )
                display_feedback = post_closed_loop(
                    server,
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="send_attempt_started_outcome_unknown",
                        assistant_message_id="msg_closed_loop_http_display",
                        output_channel="display",
                        causal_parent_event_id=display_intent["event_id"],
                    ),
                )
                display_ack = post_closed_loop(
                    server,
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="submission_ack_needs_feedback",
                        assistant_message_id="msg_closed_loop_http_display",
                        output_channel="display",
                        causal_parent_event_id=display_feedback["event_id"],
                    ),
                )
                tts_intent = post_closed_loop(
                    server,
                    http_output_event_candidate(
                        "output.dispatch_intent",
                        profile_name="dispatch_intent_recorded",
                        assistant_message_id="msg_closed_loop_http_display",
                        output_channel="tts",
                    ),
                )
                tts_feedback = post_closed_loop(
                    server,
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="send_attempt_started_outcome_unknown",
                        assistant_message_id="msg_closed_loop_http_display",
                        output_channel="tts",
                        causal_parent_event_id=tts_intent["event_id"],
                    ),
                )
                tts_ambiguous = post_closed_loop(
                    server,
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="possible_send_timeout",
                        assistant_message_id="msg_closed_loop_http_display",
                        output_channel="tts",
                        causal_parent_event_id=tts_feedback["event_id"],
                    ),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            stored = [
                json.loads(line)
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if '"closed-loop-event-journal-entry.v1"' in line
            ]

        for payload in (
            display_intent,
            display_feedback,
            display_ack,
            tts_intent,
            tts_feedback,
            tts_ambiguous,
        ):
            self.assertEqual(
                set(payload),
                {"ok", "event_id", "journal_entry_id", "ingest_offset"},
            )
            self.assertTrue(payload["ok"])
        self.assertEqual(
            [entry["ingest_offset"] for entry in stored],
            [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(
            [entry["event"]["source_authority"] for entry in stored],
            [
                "control_output_adapter",
                "control_output_adapter",
                "display_transport",
                "control_output_adapter",
                "control_output_adapter",
                "tts_transport",
            ],
        )
        self.assertEqual(
            stored[1]["event"]["details"]["outcome_class"],
            "outcome_unknown",
        )
        self.assertEqual(
            stored[2]["event"]["details"]["outcome_class"],
            "needs_feedback",
        )
        self.assertEqual(
            stored[5]["event"]["details"]["outcome_class"],
            "outcome_unknown",
        )
        self.assertNotIn("feedback_id", json.dumps(stored))

    def test_bound_feedback_loads_authority_once_and_remembers_new_assistant_events(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-index-once.jsonl"
            message_id = "msg_closed_loop_indexed"
            seed_authoritative_assistant_event(journal_path, message_id)
            journal = ThoughtCoreEventJournal(path=journal_path)

            with patch.object(
                journal,
                "_read_closed_loop_index_snapshot",
                wraps=journal._read_closed_loop_index_snapshot,
            ) as index_scan:
                intent = journal.append_bound_closed_loop_output_event(
                    materialize_closed_loop_output_ingress(
                        http_output_event_candidate(
                            "output.dispatch_intent",
                            profile_name="dispatch_intent_recorded",
                            assistant_message_id=message_id,
                            output_channel="display",
                        )
                    )
                )
                sent = journal.append_bound_closed_loop_output_event(
                    materialize_closed_loop_output_ingress(
                        http_output_event_candidate(
                            "output.feedback",
                            profile_name="send_attempt_started_outcome_unknown",
                            assistant_message_id=message_id,
                            output_channel="display",
                            causal_parent_event_id=intent["event"]["event_id"],
                        )
                    )
                )
                journal.append_bound_closed_loop_output_event(
                    materialize_closed_loop_output_ingress(
                        http_output_event_candidate(
                            "output.feedback",
                            profile_name="submission_ack_needs_feedback",
                            assistant_message_id=message_id,
                            output_channel="display",
                            causal_parent_event_id=sent["event"]["event_id"],
                        )
                    )
                )

                new_message_id = "msg_closed_loop_incremental"
                journal.write_event(
                    {
                        "schema_version": "thought-core.event.v1",
                        "event_id": "evt_authoritative_incremental",
                        "type": "assistant.message",
                        "timestamp": "2026-07-28T00:00:01Z",
                        "session_id": "session_closed_loop_integration",
                        "turn_id": "turn_closed_loop_first",
                        "seq": 2,
                        "data": {"assistant_message_id": new_message_id},
                    }
                )
                journal.append_bound_closed_loop_output_event(
                    materialize_closed_loop_output_ingress(
                        http_output_event_candidate(
                            "output.dispatch_intent",
                            profile_name="dispatch_intent_recorded",
                            assistant_message_id=new_message_id,
                            output_channel="tts",
                        )
                    )
                )

            self.assertEqual(index_scan.call_count, 1)

    def test_bound_feedback_uses_only_the_bounded_target_chain_after_restart(self) -> None:
        class NoWholeIndexIteration(dict):
            def values(self):  # type: ignore[no-untyped-def]
                raise AssertionError("whole_closed_loop_index_iteration_forbidden")

        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-many-chains.jsonl"
            writer = ThoughtCoreEventJournal(path=journal_path)
            for index in range(64):
                message_id = f"msg_closed_loop_unrelated_{index:03d}"
                writer.write_event(
                    {
                        "schema_version": "thought-core.event.v1",
                        "event_id": f"evt_authoritative_unrelated_{index:03d}",
                        "type": "assistant.speech_delta",
                        "timestamp": "2026-07-28T00:00:00Z",
                        "session_id": "session_closed_loop_integration",
                        "turn_id": "turn_closed_loop_first",
                        "seq": index + 1,
                        "data": {"assistant_message_id": message_id},
                    }
                )
                writer.append_closed_loop_event(
                    materialize_closed_loop_output_ingress(
                        http_output_event_candidate(
                            "output.dispatch_intent",
                            profile_name="dispatch_intent_recorded",
                            assistant_message_id=message_id,
                            output_channel="display",
                        )
                    )
                )

            target_message_id = "msg_closed_loop_target_bounded"
            writer.write_event(
                {
                    "schema_version": "thought-core.event.v1",
                    "event_id": "evt_authoritative_target_bounded",
                    "type": "assistant.message",
                    "timestamp": "2026-07-28T00:00:01Z",
                    "session_id": "session_closed_loop_integration",
                    "turn_id": "turn_closed_loop_first",
                    "seq": 65,
                    "data": {"assistant_message_id": target_message_id},
                }
            )

            replay = ThoughtCoreEventJournal(path=journal_path)
            intent = replay.append_bound_closed_loop_output_event(
                materialize_closed_loop_output_ingress(
                    http_output_event_candidate(
                        "output.dispatch_intent",
                        profile_name="dispatch_intent_recorded",
                        assistant_message_id=target_message_id,
                        output_channel="tts",
                    )
                )
            )
            self.assertEqual(len(replay._closed_loop_chain_entries), 65)
            replay._closed_loop_entries_by_event_id = NoWholeIndexIteration(
                replay._closed_loop_entries_by_event_id
            )

            sent = replay.append_bound_closed_loop_output_event(
                materialize_closed_loop_output_ingress(
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="send_attempt_started_outcome_unknown",
                        assistant_message_id=target_message_id,
                        output_channel="tts",
                        causal_parent_event_id=intent["event"]["event_id"],
                    )
                )
            )
            replay.append_bound_closed_loop_output_event(
                materialize_closed_loop_output_ingress(
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="submission_ack_needs_feedback",
                        assistant_message_id=target_message_id,
                        output_channel="tts",
                        causal_parent_event_id=sent["event"]["event_id"],
                    )
                )
            )

            target_key = (
                "session_closed_loop_integration",
                "turn_closed_loop_first",
                target_message_id,
                "tts",
            )
            self.assertEqual(len(replay._closed_loop_chain_entries[target_key]), 3)
            self.assertTrue(
                all(
                    len(chain) <= 3
                    for chain in replay._closed_loop_chain_entries.values()
                )
            )

    def test_http_ingest_binds_authoritative_tuple_parent_order_and_budget(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-http-binding.jsonl"
            message_id = "msg_closed_loop_bound"
            seed_authoritative_assistant_event(journal_path, message_id)
            with patch.dict(
                "os.environ",
                {
                    "THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED": "1",
                    "THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path),
                    "THOUGHT_CORE_EVENT_JOURNAL_DIR": "",
                },
                clear=False,
            ):
                server = create_server(
                    "127.0.0.1",
                    0,
                    thought_loop=ThoughtLoop(
                        tools=MockThoughtTools(),
                        agentic_turn_provider=CapturingConversationProvider(),
                    ),
                )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def rejected(candidate: dict) -> None:
                with self.assertRaises(error.HTTPError) as raised:
                    post_closed_loop(server, candidate)
                self.assertEqual(raised.exception.code, 400)

            try:
                unknown = http_output_event_candidate(
                    "output.dispatch_intent",
                    profile_name="dispatch_intent_recorded",
                    assistant_message_id="msg_closed_loop_unknown",
                    output_channel="display",
                )
                rejected(unknown)
                swapped = http_output_event_candidate(
                    "output.dispatch_intent",
                    profile_name="dispatch_intent_recorded",
                    assistant_message_id=message_id,
                    output_channel="display",
                )
                swapped["turn_id"] = "turn_closed_loop_swapped"
                rejected(swapped)

                intent_candidate = http_output_event_candidate(
                    "output.dispatch_intent",
                    profile_name="dispatch_intent_recorded",
                    assistant_message_id=message_id,
                    output_channel="display",
                )
                intent = post_closed_loop(server, intent_candidate)
                rejected(intent_candidate)
                rejected(
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="send_attempt_started_outcome_unknown",
                        assistant_message_id=message_id,
                        output_channel="display",
                        causal_parent_event_id="evt_unknown_parent",
                    )
                )

                send_candidate = http_output_event_candidate(
                    "output.feedback",
                    profile_name="send_attempt_started_outcome_unknown",
                    assistant_message_id=message_id,
                    output_channel="display",
                    causal_parent_event_id=intent["event_id"],
                )
                sent = post_closed_loop(server, send_candidate)
                rejected(send_candidate)
                rejected(
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="submission_ack_needs_feedback",
                        assistant_message_id=message_id,
                        output_channel="tts",
                        causal_parent_event_id=sent["event_id"],
                    )
                )

                ack_candidate = http_output_event_candidate(
                    "output.feedback",
                    profile_name="submission_ack_needs_feedback",
                    assistant_message_id=message_id,
                    output_channel="display",
                    causal_parent_event_id=sent["event_id"],
                )
                post_closed_loop(server, ack_candidate)
                rejected(ack_candidate)
                rejected(
                    http_output_event_candidate(
                        "output.feedback",
                        profile_name="possible_send_timeout",
                        assistant_message_id=message_id,
                        output_channel="display",
                        causal_parent_event_id=sent["event_id"],
                    )
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            stored = [
                json.loads(line)
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if '"closed-loop-event-journal-entry.v1"' in line
            ]
        self.assertEqual(len(stored), 3)
        self.assertEqual(
            [entry["event"]["details"]["phase"] for entry in stored],
            ["dispatching", "dispatching", "observing"],
        )

    def test_http_ingest_rejects_forged_playback_success_before_all_consumers(self) -> None:
        with TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "closed-loop-http-forged.jsonl"
            provider = CapturingConversationProvider()
            loop = ThoughtLoop(
                tools=MockThoughtTools(),
                agentic_turn_provider=provider,
            )
            with patch.dict(
                "os.environ",
                {
                    "THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED": "1",
                    "THOUGHT_CORE_EVENT_JOURNAL_PATH": str(journal_path),
                    "THOUGHT_CORE_EVENT_JOURNAL_DIR": "",
                },
                clear=False,
            ):
                server = create_server("127.0.0.1", 0, thought_loop=loop)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            forged = {
                "event_kind": "output.feedback",
                "session_id": "session_closed_loop_integration",
                "turn_id": "turn_closed_loop_forged",
                "assistant_message_id": "msg_closed_loop_forged",
                "causal_parent_event_id": "evt_closed_loop_forged_parent",
                "source_authority": "playback_transport",
                "details": {
                    "phase": "terminal",
                    "outcome_class": "succeeded",
                    "submission_class": "submitted",
                    "receipt_class": "correlated_success",
                    "cleanup_class": "not_required",
                    "proof_layer": "user_observation",
                    "verification_class": "user_reported",
                    "output_channel": "playback",
                    "component": "deterministic_fake_playback",
                },
            }
            try:
                forged_without_source = {
                    key: value
                    for key, value in forged.items()
                    if key != "source_authority"
                }
                forged_display_success = {
                    **forged_without_source,
                    "details": {
                        **forged_without_source["details"],
                        "output_channel": "display",
                        "component": "aituber_direct_send",
                    },
                }
                operation_transition = {
                    "event_kind": "operation.transition",
                    "session_id": "session_closed_loop_integration",
                    "turn_id": "turn_closed_loop_forged",
                    "operation_id": "op_closed_loop_forged",
                    "source_authority": "thought_core",
                    "details": {
                        **load_closed_loop_contract()["transition_profiles"][
                            "cleanup_complete"
                        ]
                    },
                }
                rejected_candidates = (
                    forged,
                    forged_without_source,
                    forged_display_success,
                    operation_transition,
                )
                for candidate in rejected_candidates:
                    with self.subTest(source_supplied="source_authority" in candidate):
                        with self.assertRaises(error.HTTPError) as raised:
                            post_closed_loop(server, candidate)
                        self.assertEqual(raised.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            self.assertFalse(journal_path.exists())
            loop.run_dicts(turn("turn_after_forged_feedback", "Continue safely."))

        self.assertEqual(
            provider.requests[0].predecision_context.feedback_context.items,
            (),
        )
