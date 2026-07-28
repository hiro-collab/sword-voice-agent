import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.correlation_feedback_contract import (  # noqa: E402
    load_closed_loop_contract,
    materialize_closed_loop_event,
    materialize_closed_loop_output_ingress,
    validate_closed_loop_event,
)


def output_candidate(event_kind: str = "output.dispatch_intent") -> dict:
    profile_name = (
        "dispatch_intent_recorded"
        if event_kind == "output.dispatch_intent"
        else "possible_send_timeout"
    )
    profile = dict(load_closed_loop_contract()["transition_profiles"][profile_name])
    return {
        "event_kind": event_kind,
        "session_id": "session_contract_001",
        "turn_id": "turn_contract_001",
        "assistant_message_id": "msg_contract_001",
        "source_authority": "control_output_adapter",
        "details": {**profile, "output_channel": "playback"},
    }


class ClosedLoopCorrelationFeedbackContractTest(TestCase):
    def test_single_descriptor_pins_identity_issuers_and_event_kinds(self) -> None:
        contract = load_closed_loop_contract()

        self.assertEqual(contract["contract_version"], "closed-loop-correlation-feedback.v1")
        self.assertEqual(contract["activation"]["default"], "disabled")
        self.assertEqual(
            set(contract["identities"]),
            {
                "session_id",
                "input_attempt_id",
                "turn_id",
                "operation_id",
                "assistant_message_id",
                "event_id",
                "candidate_id",
                "memory_id",
                "journal_entry_id",
                "ingest_offset",
                "causal_parent_event_id",
                "as_of_ingest_offset",
                "responds_to_message_id",
                "target_operation_id",
                "operation_revision",
            },
        )
        self.assertEqual(contract["identities"]["turn_id"]["issuer"], "thought_core")
        self.assertTrue(
            contract["identities"]["turn_id"]["legacy_caller_compatibility"]
        )
        self.assertEqual(
            set(contract["event_kinds"]),
            {"operation.transition", "output.dispatch_intent", "output.feedback"},
        )
        self.assertEqual(
            set(contract["http_output_ingress"]["channels"]),
            {"display", "tts"},
        )
        self.assertEqual(
            contract["http_output_ingress"]["profiles"],
            {
                "output.dispatch_intent": ["dispatch_intent_recorded"],
                "output.feedback": [
                    "send_attempt_started_outcome_unknown",
                    "submission_ack_needs_feedback",
                    "possible_send_timeout",
                    "dispatch_rejected_before_send",
                ],
            },
        )

    def test_descriptor_schema_is_strict_at_top_level(self) -> None:
        schema = json.loads(
            (
                REPO_ROOT
                / "contracts"
                / "turn"
                / "closed-loop-correlation-feedback.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            set(load_closed_loop_contract()),
        )

    def test_thought_core_issues_event_id_distinct_from_assistant_message_id(self) -> None:
        event = materialize_closed_loop_event(
            output_candidate(),
            event_id="evt_contract_001",
            observed_at="2026-07-28T00:00:00Z",
        )

        self.assertEqual(event["event_id"], "evt_contract_001")
        self.assertEqual(event["assistant_message_id"], "msg_contract_001")
        self.assertNotEqual(event["event_id"], event["assistant_message_id"])
        self.assertEqual(event["schema_version"], "closed-loop-correlation-feedback.v1")

    def test_external_candidate_cannot_issue_event_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity_must_be_issued"):
            materialize_closed_loop_event(
                {**output_candidate(), "event_id": "evt_external"}
            )

    def test_http_output_ingress_derives_source_and_rejects_forged_success(self) -> None:
        contract = load_closed_loop_contract()
        intent = materialize_closed_loop_output_ingress(
            {
                "event_kind": "output.dispatch_intent",
                "session_id": "session_contract_001",
                "turn_id": "turn_contract_001",
                "assistant_message_id": "msg_contract_001",
                "details": {
                    **contract["transition_profiles"]["dispatch_intent_recorded"],
                    "output_channel": "display",
                    "component": "aituber_direct_send",
                },
            },
            event_id="evt_contract_http_intent",
            observed_at="2026-07-28T00:00:00Z",
        )

        self.assertEqual(intent["source_authority"], "control_output_adapter")
        send_attempt = materialize_closed_loop_output_ingress(
            {
                "event_kind": "output.feedback",
                "session_id": "session_contract_001",
                "turn_id": "turn_contract_001",
                "assistant_message_id": "msg_contract_001",
                "causal_parent_event_id": intent["event_id"],
                "details": {
                    **contract["transition_profiles"][
                        "send_attempt_started_outcome_unknown"
                    ],
                    "output_channel": "display",
                    "component": "aituber_direct_send",
                },
            },
            event_id="evt_contract_http_send_attempt",
            observed_at="2026-07-28T00:00:01Z",
        )
        self.assertEqual(send_attempt["source_authority"], "control_output_adapter")
        self.assertEqual(send_attempt["details"]["outcome_class"], "outcome_unknown")
        self.assertEqual(
            send_attempt["details"]["submission_class"],
            "may_have_submitted",
        )
        browser_display = materialize_closed_loop_output_ingress(
            {
                "event_kind": "output.feedback",
                "session_id": "session_contract_001",
                "turn_id": "turn_contract_001",
                "assistant_message_id": "msg_contract_001",
                "causal_parent_event_id": send_attempt["event_id"],
                "details": {
                    **contract["transition_profiles"][
                        "submission_ack_needs_feedback"
                    ],
                    "output_channel": "display",
                    "component": "aituber_message_store",
                },
            },
            event_id="evt_contract_browser_display",
            observed_at="2026-07-28T00:00:02Z",
        )
        self.assertEqual(browser_display["source_authority"], "display_transport")
        browser_tts = materialize_closed_loop_output_ingress(
            {
                "event_kind": "output.feedback",
                "session_id": "session_contract_001",
                "turn_id": "turn_contract_001",
                "assistant_message_id": "msg_contract_001",
                "causal_parent_event_id": send_attempt["event_id"],
                "details": {
                    **contract["transition_profiles"][
                        "submission_ack_needs_feedback"
                    ],
                    "output_channel": "tts",
                    "component": "aituber_tts_synthesis",
                },
            },
            event_id="evt_contract_browser_tts",
            observed_at="2026-07-28T00:00:03Z",
        )
        self.assertEqual(browser_tts["source_authority"], "tts_transport")
        unknown_component = {
            **browser_display,
            "event_kind": "output.feedback",
            "details": {
                **browser_display["details"],
                "component": "aituber_unknown_output",
            },
        }
        unknown_component.pop("event_id")
        unknown_component.pop("schema_version")
        unknown_component.pop("source_authority")
        unknown_component.pop("observed_at")
        with self.assertRaisesRegex(ValueError, "component_invalid"):
            materialize_closed_loop_output_ingress(unknown_component)
        forged = {
            "event_kind": "output.feedback",
            "session_id": "session_contract_001",
            "turn_id": "turn_contract_001",
            "assistant_message_id": "msg_contract_001",
            "causal_parent_event_id": intent["event_id"],
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
        with self.assertRaises(ValueError):
            materialize_closed_loop_output_ingress(forged)
        forged.pop("source_authority")
        with self.assertRaises(ValueError):
            materialize_closed_loop_output_ingress(forged)

    def test_raw_private_and_generic_id_sprawl_fail_closed(self) -> None:
        canonical = materialize_closed_loop_event(
            output_candidate(),
            event_id="evt_contract_private",
            observed_at="2026-07-28T00:00:00Z",
        )
        cases = (
            {**canonical, "feedback_id": "feedback_new"},
            {**canonical, "details": {**canonical["details"], "raw_payload": "secret"}},
            {**canonical, "details": {**canonical["details"], "local_ref": "C:/private/path"}},
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    validate_closed_loop_event(case)

    def test_secret_like_strings_fail_closed_without_identifier_mutation(self) -> None:
        canonical = materialize_closed_loop_event(
            output_candidate(),
            event_id="evt_contract_secret_boundary",
            observed_at="2026-07-28T00:00:00Z",
        )
        cases = (
            {
                **canonical,
                "session_id": "session_sk-syntheticEnvelopeSecret123",
            },
            {
                **canonical,
                "causal_parent_event_id": "evt_sk-syntheticCorrelationSecret123",
            },
            {
                **canonical,
                "details": {
                    **canonical["details"],
                    "component": "component_sk-syntheticDetailSecret123",
                },
            },
        )
        for case in cases:
            with self.subTest(case=case):
                before = json.dumps(case, sort_keys=True)
                with self.assertRaisesRegex(ValueError, "secret_like_string_rejected"):
                    validate_closed_loop_event(case)
                self.assertEqual(json.dumps(case, sort_keys=True), before)

    def test_intermediate_profiles_do_not_collapse_proof_layers(self) -> None:
        profiles = load_closed_loop_contract()["transition_profiles"]

        self.assertEqual(
            profiles["transient_external_mismatch"],
            {
                "phase": "observing",
                "outcome_class": "needs_feedback",
                "submission_class": "submitted",
                "receipt_class": "correlated_success",
                "cleanup_class": "not_required",
                "proof_layer": "physical_observation",
                "verification_class": "correlated_failure",
            },
        )
        self.assertEqual(
            profiles["projection_receipt_without_visible_pixels"]["outcome_class"],
            "needs_feedback",
        )
        self.assertEqual(
            profiles["projection_receipt_without_visible_pixels"]["cleanup_class"],
            "pending",
        )
        self.assertEqual(
            profiles["possible_send_timeout"]["submission_class"],
            "may_have_submitted",
        )
        self.assertEqual(
            profiles["possible_send_timeout"]["outcome_class"],
            "outcome_unknown",
        )
