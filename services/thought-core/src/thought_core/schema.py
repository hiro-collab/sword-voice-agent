"""Input schema for thought-core turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class TurnInput:
    text: str
    turn_id: str
    session_id: str
    locale: str = "ja-JP"
    context_refs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TurnInput":
        blocked_audio_reason = _blocked_audio_observation_reason(value)
        if blocked_audio_reason:
            raise ValueError(
                "blocked or held audio observation cannot materialize TurnInput: "
                f"{blocked_audio_reason}"
            )
        text = _required_str(value, "text")
        turn_id = _required_str(value, "turn_id")
        session_id = _required_str(value, "session_id")
        locale = value.get("locale") or "ja-JP"
        if not isinstance(locale, str):
            raise ValueError("locale must be a string")
        context_refs = value.get("context_refs") or {}
        if not isinstance(context_refs, dict):
            raise ValueError("context_refs must be an object")
        return cls(
            text=text,
            turn_id=turn_id,
            session_id=session_id,
            locale=locale,
            context_refs=dict(context_refs),
        )

    @classmethod
    def from_accepted_speech_candidate(
        cls,
        candidate: Mapping[str, Any],
        private_turn: Mapping[str, Any],
    ) -> "TurnInput":
        blocked_reason = _accepted_speech_candidate_block_reason(candidate)
        if blocked_reason:
            raise ValueError(
                "accepted speech candidate cannot materialize TurnInput: "
                f"{blocked_reason}"
            )

        context_refs = private_turn.get("context_refs") or {}
        if not isinstance(context_refs, Mapping):
            raise ValueError("context_refs must be an object")
        materialized_refs = dict(context_refs)
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            materialized_refs.setdefault(
                "accepted_user_speech_candidate_ref",
                candidate_id,
            )
        materialized_refs.setdefault(
            "input_gate_contract",
            _ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA,
        )

        return cls.from_mapping(
            {
                "text": private_turn.get("text"),
                "turn_id": private_turn.get("turn_id"),
                "session_id": private_turn.get("session_id"),
                "locale": private_turn.get("locale") or "ja-JP",
                "context_refs": materialized_refs,
            }
        )


def _required_str(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item


_BLOCKED_TURN_ADOPTION_STATUSES = {
    "blocked_self_output",
    "blocked_cooldown",
    "blocked_ambiguous",
    "blocked_missing_session_join",
    "blocked_low_confidence",
    "blocked_capture_not_ready",
}

_BLOCKED_AUDIO_CLASSIFICATIONS = _BLOCKED_TURN_ADOPTION_STATUSES | {
    "system_self_output_candidate",
    "mixed_or_ambiguous_audio",
}

_NON_MATERIALIZING_GATE_DECISIONS = _BLOCKED_TURN_ADOPTION_STATUSES | {
    "candidate_user_turn_needs_ai_talk_core_acceptance",
    "recognition_low_confidence",
    "recognition_failed",
    "recognition_not_authorized",
}

_ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA = (
    "accepted_user_speech_candidate_input_gate.v0"
)

_ACCEPTED_USER_SPEECH_STATUSES = {"accepted_user_speech_candidate"}

_ACCEPTED_USER_SPEECH_SOURCE_KINDS = {
    "prepared_local_audio_sample",
    "user_speech_candidate",
}

_ACCEPTED_USER_SPEECH_CANDIDATE_ROUTES = {
    "prepared_sample_browser_stt",
    "local_offline_recognizer_redacted_summary",
    "private_user_speech_input_gate",
    "manual_reviewed_sample_candidate",
}

_SHARED_CANDIDATE_TEXT_KEYS = {
    "text",
    "recognized_text",
    "transcript",
    "prompt",
    "raw_text",
}

_CANDIDATE_REDACTION_GUARDS = {
    "raw_audio_included",
    "raw_media_included",
    "raw_transcript_included",
    "raw_recognized_text_included",
    "private_path_included",
    "provider_payload_included",
    "browser_storage_included",
    "token_or_secret_included",
    "home_control_action_authority_included",
}


def _blocked_audio_observation_reason(value: Mapping[str, Any]) -> str:
    """Keep diagnostic audio observations out of the normal user-turn schema."""

    schema_version = value.get("schema_version")
    if schema_version == _ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA:
        return "accepted_speech_candidate_requires_private_materialization"

    if schema_version == "audio_self_output_observation.v0":
        return "audio_self_output_observation"

    speaker_role = value.get("speaker_role")
    if speaker_role == "system_self_output":
        return "system_self_output"

    route = value.get("route")
    if route == "self_output_observation":
        return "self_output_observation"

    pre_turn_result = value.get("pre_turn_result")
    if isinstance(pre_turn_result, Mapping):
        if pre_turn_result.get("turn_input_materialized") is False:
            return "turn_input_materialized_false"
        if pre_turn_result.get("normal_turn_adoption_blocked") is True:
            return "normal_turn_adoption_blocked"
        if "thought_core_turn_input_ref" in pre_turn_result and (
            pre_turn_result.get("thought_core_turn_input_ref") is None
        ):
            return "thought_core_turn_input_ref_absent"

    if value.get("may_start_user_turn") is False:
        return "may_start_user_turn_false"

    if value.get("turn_adoption_authority") is False:
        return "turn_adoption_authority_false"

    turn_adoption_status = value.get("turn_adoption_status")
    if (
        isinstance(turn_adoption_status, str)
        and turn_adoption_status in _BLOCKED_TURN_ADOPTION_STATUSES
    ):
        return "blocked_turn_adoption_status"

    classification = value.get("classification")
    if isinstance(classification, str) and classification in _BLOCKED_AUDIO_CLASSIFICATIONS:
        return "blocked_audio_classification"

    gate_decision = value.get("self_output_gate_decision")
    if isinstance(gate_decision, str) and gate_decision in _NON_MATERIALIZING_GATE_DECISIONS:
        return "non_materializing_audio_gate_decision"

    return ""


def _accepted_speech_candidate_block_reason(candidate: Mapping[str, Any]) -> str:
    schema_version = candidate.get("schema_version")
    if schema_version != _ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA:
        return "unsupported_candidate_schema"

    for key in _SHARED_CANDIDATE_TEXT_KEYS:
        if key in candidate:
            return "shared_candidate_text_present"

    decision = candidate.get("acceptance_decision")
    if not isinstance(decision, Mapping):
        return "acceptance_decision_missing"

    acceptance_status = decision.get("acceptance_status")
    if acceptance_status not in _ACCEPTED_USER_SPEECH_STATUSES:
        return "acceptance_status_not_accepted"

    if candidate.get("speaker_role") != "user_candidate":
        return "speaker_role_not_user_candidate"

    if candidate.get("source_kind") not in _ACCEPTED_USER_SPEECH_SOURCE_KINDS:
        return "source_kind_not_user_speech_compatible"

    if candidate.get("candidate_route") not in _ACCEPTED_USER_SPEECH_CANDIDATE_ROUTES:
        return "candidate_route_not_user_speech_compatible"

    input_gate = candidate.get("input_gate")
    if not isinstance(input_gate, Mapping):
        return "input_gate_missing"

    if input_gate.get("input_gate_decision_owner") != "ai_talk_core_input_gate":
        return "input_gate_decision_owner_not_ai_talk_core"

    if input_gate.get("input_gate_decision_class") != "accepted_user_speech_candidate":
        return "input_gate_decision_class_not_accepted"

    if input_gate.get("normal_turn_block_reason") is not None:
        return "normal_turn_block_reason_not_null"

    if decision.get("may_materialize_thought_core_turninput") is not True:
        return "may_materialize_thought_core_turninput_false"

    if decision.get("private_text_handoff_required") is not True:
        return "private_text_handoff_required_false"

    text_publication = candidate.get("text_publication")
    if not isinstance(text_publication, Mapping):
        return "text_publication_missing"

    shared_artifact_contains_text = decision.get("shared_artifact_contains_text")
    if shared_artifact_contains_text is True:
        if (
            text_publication.get("text_publication_policy")
            != "prepared_sample_text_allowed"
        ):
            return "shared_text_without_prepared_sample_policy"
        if text_publication.get("text_provenance_class") != "prepared_local_sample_set":
            return "shared_text_without_prepared_sample_provenance"
        if (
            text_publication.get("non_sample_or_live_text_policy")
            != "protected_or_redacted"
        ):
            return "non_sample_or_live_text_policy_not_protected"
        expected_text = text_publication.get("expected_sample_text")
        recognized_text = text_publication.get("recognized_text")
        if not isinstance(expected_text, str) or not expected_text.strip():
            return "expected_sample_text_missing"
        if not isinstance(recognized_text, str) or not recognized_text.strip():
            return "recognized_text_missing"
    elif shared_artifact_contains_text is False:
        if (
            text_publication.get("text_publication_policy")
            != "text_redacted_or_absent"
        ):
            return "redacted_text_policy_missing"
        if text_publication.get("text_provenance_class") != "redacted_or_absent":
            return "redacted_text_provenance_missing"
        if (
            text_publication.get("non_sample_or_live_text_policy")
            != "protected_or_redacted"
        ):
            return "non_sample_or_live_text_policy_not_protected"
    else:
        return "shared_artifact_contains_text_not_boolean"

    if decision.get("thought_core_turninput_materialized") is not False:
        return "shared_candidate_already_materialized"

    if (
        decision.get("turn_materialization_route")
        != "separate_private_runtime_handoff_required"
    ):
        return "turn_materialization_route_not_private_handoff"

    redaction_guards = candidate.get("redaction_guards")
    if not isinstance(redaction_guards, Mapping):
        return "redaction_guards_missing"

    for key in _CANDIDATE_REDACTION_GUARDS:
        if redaction_guards.get(key) is not False:
            return f"{key}_not_false"

    if candidate.get("raw_private_publication_flags") is not False:
        return "raw_private_publication_flags_not_false"

    return ""

