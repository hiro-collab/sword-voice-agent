import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402
from thought_core.trace_artifacts import (  # noqa: E402
    MEMORY_CANDIDATE_SCHEMA_VERSION,
    TURN_TRACE_SCHEMA_VERSION,
    build_thought_core_turn_trace,
    memory_candidate_from_trace,
    write_memory_candidate,
    write_thought_core_turn_trace,
)


class ThoughtCoreTurnTraceTest(TestCase):
    def test_schema_file_names_turn_trace_shape(self) -> None:
        schema = json.loads(
            (
                REPO_ROOT
                / "contracts"
                / "trace"
                / "thought-core-turn-trace.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            TURN_TRACE_SCHEMA_VERSION,
        )
        self.assertIn("trace_id", schema["required"])
        self.assertIn("working_memory_summary", schema["required"])
        self.assertIn("memory_candidate_ids", schema["required"])
        self.assertEqual(
            schema["properties"]["redaction"]["properties"]["raw_text_stored"]["const"],
            False,
        )

    def test_representative_turn_uses_prior_context_and_writes_redacted_artifacts(self) -> None:
        tools = MockThoughtTools(
            execute_failures_before_success=3,
            include_secret_in_execute_result=True,
        )
        turn = {
            "text": "リビングの電気をつけて",
            "turn_id": "turn_r1_trace_memory_candidate_001",
            "session_id": "rr003_r1_session",
            "locale": "ja-JP",
            "context_refs": {
                "trace_id": "trace_r1_memory_001",
                "event_id": "evt_prior_context_001",
                "working_memory_context": {
                    "context_id": "wm_ctx_r1_prior_001",
                    "observed_at": "2026-06-10T00:00:00Z",
                    "stale_after": "2026-06-10T00:01:00Z",
                    "freshness": "fresh",
                    "staleness": "not_stale",
                    "uncertainty": "low",
                    "state_authority": "motion_driver_result",
                    "safe_for_thought_core_use": True,
                    "safe_to_act": False,
                    "not_proven": True,
                    "must_not_imply": [
                        "durable_memory",
                        "safe_to_act",
                        "live_device_proof",
                    ],
                },
            },
        }
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(turn)
        events.append(
            {
                "schema_version": "thought-core.event.v0",
                "event_id": "evt_unsafe_payload_001",
                "turn_id": turn["turn_id"],
                "session_id": turn["session_id"],
                "seq": 999,
                "timestamp": "2026-06-10T00:00:00Z",
                "source": "thought-core",
                "type": "tool.result",
                "data": {
                    "status": "failed",
                    "user_text": "リビングの電気をつけて",
                    "transcript": "raw transcript must not leak",
                    "provider_payload": {"raw": "provider payload must not leak"},
                    "media_ref": "file://X:/synthetic-private/frame.png",
                    "ha_entity_id": "light.synthetic_fixture",
                    "authorization": "Bearer unsafe-token",
                    "raw_artifact_dynamic_hint": "artifact_raw_001",
                    "provider_payload_dynamic_hint": "provider_dynamic_001",
                    "private_path_dynamic_hint": "X:\\synthetic-private\\dynamic.json",
                    "entity_id_dynamic_hint": "light.synthetic_fixture",
                    "action": {
                        "action_id": "light_on",
                        "target": "light.synthetic_fixture",
                        "expected_state": "on",
                    },
                    "result": {
                        "status": "failed",
                        "private_path": "X:\\synthetic-private\\trace.json",
                        "entity_id": "light.synthetic_fixture",
                    },
                },
            }
        )
        event_types = {event["type"] for event in events}

        trace = build_thought_core_turn_trace(turn, events)
        candidate = memory_candidate_from_trace(
            trace,
            memory_type="failure_pattern",
            scope="failure_patterns",
            why_record="retry_or_review_budget_exhausted",
        )

        with TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "turn-traces.jsonl"
            candidate_path = Path(tmp) / "memory-candidates.jsonl"
            trace_write = write_thought_core_turn_trace(trace, trace_path)
            candidate_write = write_memory_candidate(candidate, candidate_path)
            persisted_trace = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
            persisted_candidate = json.loads(
                candidate_path.read_text(encoding="utf-8").splitlines()[0]
            )

        serialized = json.dumps(
            {
                "trace": trace,
                "candidate": candidate,
                "persisted_trace": persisted_trace,
                "persisted_candidate": persisted_candidate,
            },
            ensure_ascii=False,
        )

        self.assertIn("memory.retrieved", event_types)
        self.assertIn("context.used", event_types)
        self.assertIn("memory.candidate_requested", event_types)
        self.assertIn("memory.candidate_recorded", event_types)
        self.assertEqual(trace["schema_version"], TURN_TRACE_SCHEMA_VERSION)
        self.assertEqual(trace["trace_id"], "trace_r1_memory_001")
        self.assertEqual(trace["working_memory_summary"]["context_id"], "wm_ctx_r1_prior_001")
        self.assertEqual(trace["working_memory_summary"]["freshness"], "fresh")
        self.assertIn("durable_memory", trace["non_claims"])
        self.assertIn("rr003_representative_pass", trace["non_claims"])
        self.assertTrue(trace["memory_candidate_ids"])
        self.assertEqual(candidate["schema_version"], MEMORY_CANDIDATE_SCHEMA_VERSION)
        self.assertEqual(candidate["trace_id"], trace["trace_id"])
        self.assertEqual(candidate["safe_to_act"], False)
        self.assertEqual(candidate["durable_memory_claimed"], False)
        self.assertEqual(
            candidate["safe_for_future_reasoning"]["must_revalidate_current_state"],
            True,
        )
        self.assertEqual(trace_write["status"], "ok")
        self.assertEqual(candidate_write["status"], "ok")
        self.assertEqual(persisted_trace["trace_id"], trace["trace_id"])
        self.assertEqual(persisted_candidate["candidate_id"], candidate["candidate_id"])
        self.assertNotIn("リビングの電気をつけて", serialized)
        self.assertNotIn("mock-token-that-must-not-leak", serialized)
        self.assertNotIn("unsafe-token", serialized)
        self.assertNotIn("Bearer", serialized)
        self.assertNotIn("confirmation_token", serialized)
        self.assertNotIn("raw transcript must not leak", serialized)
        self.assertNotIn("provider payload must not leak", serialized)
        self.assertNotIn("provider_payload", serialized)
        self.assertNotIn("provider_payload_dynamic_hint", serialized)
        self.assertNotIn("media_ref", serialized)
        self.assertNotIn("private_path", serialized)
        self.assertNotIn("private_path_dynamic_hint", serialized)
        self.assertNotIn("raw_artifact_dynamic_hint", serialized)
        self.assertNotIn("entity_id_dynamic_hint", serialized)
        self.assertNotIn("light.synthetic_fixture", serialized)
        self.assertNotIn("X:\\synthetic-private", serialized)
        self.assertNotIn("file://X:/synthetic-private", serialized)
        self.assertNotIn("path", trace_write)
        self.assertNotIn("path", candidate_write)
