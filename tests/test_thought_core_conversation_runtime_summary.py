import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.conversation_runtime_summary import (  # noqa: E402
    CONVERSATION_RUNTIME_SUMMARY_VERSION,
    build_thought_core_conversation_runtime_summary,
    write_thought_core_conversation_runtime_summary,
)
from thought_core.input_understanding import LocalInputUnderstanding  # noqa: E402
from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.reasoning import LocalActionReasoner  # noqa: E402
from thought_core.responders import LocalFallbackResponder, ResponderResult  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


TURN = {
    "text": "small talk synthetic fixture",
    "turn_id": "turn_conversation_summary_001",
    "session_id": "session_conversation_summary",
    "locale": "ja-JP",
    "context_refs": {"trace_id": "trace_conversation_summary_001"},
}


class SyntheticProviderResponder:
    adapter_kind = "synthetic_provider_adapter"
    provider = "synthetic-provider"
    model = "synthetic-model"

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        return ResponderResult(
            speech="Synthetic provider response must not be copied verbatim.",
            display="Synthetic provider response must not be copied verbatim.",
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={
                "provider_payload": {"raw": "provider payload must not leak"},
                "private_path": "X:\\synthetic-private\\provider.json",
            },
        )


class ThoughtCoreConversationRuntimeSummaryTest(TestCase):
    def test_schema_file_names_summary_shape(self) -> None:
        schema = json.loads(
            (
                REPO_ROOT
                / "contracts"
                / "diagnostics"
                / "thought-core-conversation-runtime-summary.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            CONVERSATION_RUNTIME_SUMMARY_VERSION,
        )
        self.assertIn("provider_called", schema["required"])
        self.assertIn("raw_response_published", schema["required"])
        self.assertIn("proof_ceiling", schema["required"])
        self.assertEqual(
            schema["properties"]["raw_provider_payload_published"]["const"],
            False,
        )

    def test_provider_backed_synthetic_summary_is_summary_only(self) -> None:
        events = self._run_general_turn(responder=SyntheticProviderResponder())
        events.append(
            self._event(
                "evt_provider_unsafe_001",
                "responder.completed",
                {
                    "used_llm": True,
                    "provider": "synthetic-provider",
                    "status": "llm_response",
                    "raw_prompt": "raw prompt must not leak",
                    "response_text": "raw response must not leak",
                    "provider_payload": {"raw": "provider payload must not leak"},
                    "private_path": "X:\\synthetic-private\\provider.json",
                    "entity_id": "light.synthetic_fixture",
                },
            )
        )

        summary = build_thought_core_conversation_runtime_summary(
            events,
            scenario_id="provider_backed_summary_only",
            input_mode="redacted_text_fixture",
            runtime_path="source_no_live_synthetic_provider",
            review_ready_impact="supports_review_ready_but_not_close",
        )
        serialized = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["schema_version"], CONVERSATION_RUNTIME_SUMMARY_VERSION)
        self.assertEqual(summary["provider_or_fallback"], "provider_backed")
        self.assertTrue(summary["provider_called"])
        self.assertTrue(summary["used_llm"])
        self.assertEqual(summary["proof_ceiling"], "provider_backed_summary_only")
        self.assertEqual(summary["visible_response_summary_class"], "present_nonempty_short")
        self.assertNotIn("Synthetic provider response", serialized)
        self.assertNotIn("raw prompt must not leak", serialized)
        self.assertNotIn("raw response must not leak", serialized)
        self.assertNotIn("provider payload must not leak", serialized)
        self.assertNotIn("X:\\synthetic-private", serialized)
        self.assertNotIn("light.synthetic_fixture", serialized)

    def test_local_fallback_summary_is_not_provider_quality(self) -> None:
        events = self._run_general_turn(responder=LocalFallbackResponder())
        summary = build_thought_core_conversation_runtime_summary(
            events,
            scenario_id="local_fallback_summary_only",
            input_mode="redacted_text_fixture",
            runtime_path="local_thought_core_http_runtime_post_turn_with_mock_tools",
        )
        serialized = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["provider_or_fallback"], "local_fallback")
        self.assertFalse(summary["provider_called"])
        self.assertFalse(summary["used_llm"])
        self.assertEqual(summary["fallback_reason"], "local_fallback")
        self.assertEqual(summary["proof_ceiling"], "local_fallback_runtime_support_only")
        self.assertIn("provider_backed_quality_from_fallback", summary["does_not_prove"])
        self.assertNotIn(TURN["text"], serialized)
        self.assertNotIn("受け取りました", serialized)

    def test_memory_loop_summary_classification_preserves_revalidation(self) -> None:
        events = [
            self._event(
                "evt_memory_ctx_001",
                "context.used",
                {
                    "memory_context": {
                        "schema_version": "memory_context_ref.v0",
                        "context_id": "memctx_summary_001",
                        "retrieval_depth": "conditional_deep",
                        "must_revalidate_current_state": True,
                        "items": [{"summary": "raw memory item must not leak"}],
                    }
                },
            ),
            self._event(
                "evt_route_001",
                "thought_core.response_route_classified",
                {
                    "schema_version": "thought_core_response_route.v0",
                    "trace_id": "trace_memory_loop_001",
                    "response_route": "ordinary_conversation",
                    "responder_status": "llm_response",
                    "fallback_used": False,
                    "provider_route": "synthetic-provider",
                    "used_llm": True,
                },
            ),
            self._event("evt_message_001", "assistant.message", {"speech": "raw answer"}),
            self._event("evt_completed_001", "turn.completed", {"status": "llm_response"}),
        ]

        summary = build_thought_core_conversation_runtime_summary(
            events,
            scenario_id="memory_loop_summary_classification",
            input_mode="redacted_text_fixture",
            runtime_path="source_no_live_memory_context",
        )
        serialized = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["memory_context_used_class"], "memory_core_retrieval")
        self.assertEqual(summary["memory_context_ref"], "memctx_summary_001")
        self.assertEqual(summary["retrieval_depth"], "conditional_deep")
        self.assertTrue(summary["must_revalidate_current_state"])
        self.assertNotIn("raw memory item must not leak", serialized)
        self.assertNotIn("raw answer", serialized)

    def test_service_mode_blocked_row_is_representable_without_events(self) -> None:
        summary = build_thought_core_conversation_runtime_summary(
            [],
            scenario_id="service_mode_blocked_row",
            input_mode="blocked_not_executed",
            runtime_path="service_mode_runtime",
            blocked_reason="service_mode_preflight_blocked",
            turn_id="turn_service_blocked",
            session_id_ref="session_service_blocked",
            review_ready_impact="blocking_gap_service_mode_launch_manager_conversation_path_not_tested",
        )

        self.assertEqual(summary["response_route"], "blocked")
        self.assertEqual(summary["provider_or_fallback"], "not_applicable")
        self.assertFalse(summary["provider_called"])
        self.assertEqual(summary["completion_status"], "blocked")
        self.assertEqual(summary["memory_context_used_class"], "blocked_not_executed")
        self.assertEqual(summary["visible_response_summary_class"], "blocked")
        self.assertEqual(summary["blocker_code"], "service_mode_preflight_blocked")

    def test_raw_publication_forbidden_from_dynamic_fields(self) -> None:
        events = [
            self._event(
                "evt_unsafe_dynamic_001",
                "assistant.message",
                {
                    "speech": "raw visible response must not leak",
                    "display": "raw display must not leak",
                    "raw_transcript": "raw transcript must not leak",
                    "provider_payload": {"raw": "provider payload must not leak"},
                    "raw_artifact_dynamic_hint": "artifact_raw_001",
                    "provider_payload_dynamic_hint": "provider_dynamic_001",
                    "private_path_dynamic_hint": "X:\\synthetic-private\\dynamic.json",
                    "entity_id_dynamic_hint": "light.synthetic_fixture",
                    "media_ref": "file://X:/synthetic-private/frame.png",
                    "authorization": "synthetic-token-value",
                },
            ),
            self._event("evt_completed_unsafe_001", "turn.completed", {"status": "ok"}),
        ]
        summary = build_thought_core_conversation_runtime_summary(
            events,
            scenario_id="raw_publication_forbidden",
            input_mode="redacted_text_fixture",
            runtime_path="source_no_live_unsafe_fixture",
        )
        serialized = json.dumps(summary, ensure_ascii=False)

        self.assertFalse(summary["raw_prompt_published"])
        self.assertFalse(summary["raw_response_published"])
        self.assertFalse(summary["raw_provider_payload_published"])
        self.assertFalse(summary["raw_transcript_published"])
        self.assertFalse(summary["raw_media_published"])
        self.assertFalse(summary["private_path_published"])
        self.assertFalse(summary["provider_payload_included"])
        self.assertNotIn("raw visible response must not leak", serialized)
        self.assertNotIn("raw display must not leak", serialized)
        self.assertNotIn("raw transcript must not leak", serialized)
        self.assertNotIn("provider payload must not leak", serialized)
        self.assertNotIn("synthetic-token-value", serialized)
        self.assertNotIn("X:\\synthetic-private", serialized)
        self.assertNotIn("file://X:/synthetic-private", serialized)
        self.assertNotIn("light.synthetic_fixture", serialized)
        self.assertNotIn("artifact_raw_001", serialized)
        self.assertNotIn("provider_dynamic_001", serialized)

    def test_writer_return_is_local_only_and_has_no_path(self) -> None:
        summary = build_thought_core_conversation_runtime_summary(
            [],
            scenario_id="writer_no_path",
            input_mode="blocked_not_executed",
            runtime_path="blocked",
            blocked_reason="provider_no_config",
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation-runtime-summary.jsonl"
            result = write_thought_core_conversation_runtime_summary(summary, path)
            persisted = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["local_only_metadata"])
        self.assertNotIn("path", result)
        self.assertEqual(persisted["summary_id"], summary["summary_id"])

    def _run_general_turn(self, *, responder) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
        return ThoughtLoop(
            tools=MockThoughtTools(),
            responder=responder,
            action_reasoner=LocalActionReasoner(),
            input_understanding=LocalInputUnderstanding(),
        ).run_dicts(dict(TURN))

    def _event(self, event_id: str, event_type: str, data: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": "thought-core.event.v0",
            "event_id": event_id,
            "turn_id": "turn_summary_synthetic",
            "session_id": "session_summary_synthetic",
            "seq": 1,
            "timestamp": "2026-06-14T00:00:00Z",
            "source": "thought-core",
            "type": event_type,
            "data": data,
        }
