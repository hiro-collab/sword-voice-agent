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


def _blocked_audio_observation_reason(value: Mapping[str, Any]) -> str:
    """Keep diagnostic audio observations out of the normal user-turn schema."""

    schema_version = value.get("schema_version")
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

