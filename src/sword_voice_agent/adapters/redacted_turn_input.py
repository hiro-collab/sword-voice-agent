from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any, Iterable, Mapping

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoff
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent


SCHEMA_VERSION = "redacted_turn_input.v0"
REDACTION_PROFILE = "redacted_turn_input_summary_v0"

SOURCE_LABELS = {
    "ai_talk_core_web",
    "manual_text",
    "gesture_voice_bridge",
    "test_fixture",
}
SOURCE_MODALITIES = {"speech", "text", "gesture_release", "mixed", "unknown"}
PROOF_LAYERS = {"source-static", "source-no-live"}

REQUIRED_NON_CLAIMS = [
    "not_live_camera_audio",
    "not_browser_runtime",
    "not_vb_cable_or_sample_audio_stt",
    "not_action_execution",
    "not_home_control_state",
    "not_motion_proof",
    "not_ordinary_conversation_quality",
    "not_robust_gesture_gate_green",
    "not_source_adoption_or_git",
    "not_rr003_representative_pass",
]

FALSE_ONLY_FLAGS = {
    "raw_transcript_included",
    "raw_audio_included",
    "raw_media_included",
    "prompt_text_included",
    "response_text_included",
    "provider_payload_included",
    "private_path_included",
    "private_endpoint_included",
    "secret_or_token_included",
    "home_control_device_detail_included",
    "input_gate_only_is_stt_proof",
    "stt_only_is_thought_core_completion_proof",
    "action_execution_claimed",
    "motion_proof_claimed",
    "ordinary_conversation_quality_claimed",
    "robust_gesture_green_claimed",
    "source_adoption_or_git_claimed",
    "rr003_representative_pass_claimed",
}

UNSAFE_VALUE_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]", re.IGNORECASE),
    re.compile(r"\\\\[A-Za-z0-9_.-]+\\"),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\.(mp3|mp4|wav|m4a|webm|png|jpg|jpeg)\b", re.IGNORECASE),
    re.compile(r"\b(secret|token|provider_payload)\b", re.IGNORECASE),
    re.compile(r"\b(entity_id|service_call|home_assistant)\b", re.IGNORECASE),
)

SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


class RedactedTurnInputError(ValueError):
    """Raised when a redacted turn input summary cannot be built safely."""


@dataclass(frozen=True)
class ThoughtCoreCompletionRef:
    turn_id: str
    completion_seen: bool
    completion_event_ref: str


def build_redacted_turn_input_summary(
    handoff: AiTalkCoreHandoff,
    *,
    thought_core_events: Iterable[Mapping[str, Any] | ThoughtCoreStreamEvent] = (),
    thought_core_result: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
    proof_layer: str = "source-no-live",
    source_label: str = "ai_talk_core_web",
    source_modality: str = "speech",
    input_enabled: bool = True,
    mic_enabled: bool = True,
    input_gate_reason: str = "source_no_live_summary",
    input_gate_source: str = "redacted_turn_input_helper",
    gate_event_ref: str | None = None,
    stt_event_ref: str | None = None,
    turn_id: str | None = None,
    completion_seen: bool | None = None,
    completion_event_ref: str | None = None,
) -> dict[str, Any]:
    """Build a raw-free `redacted_turn_input.v0` summary.

    The helper may inspect a handoff object and Thought Core event metadata, but
    it never copies transcript text, prompt text, response text, file paths, or
    endpoint URLs into the returned payload.
    """

    _validate_enum(proof_layer, PROOF_LAYERS, "proof_layer")
    _validate_enum(source_label, SOURCE_LABELS, "source_label")
    _validate_enum(source_modality, SOURCE_MODALITIES, "source_modality")

    events = list(_coerce_events(thought_core_events))
    if thought_core_result is not None:
        events.extend(_events_from_result(thought_core_result))

    completion = resolve_thought_core_completion(
        events,
        thought_core_result=thought_core_result,
        requested_turn_id=turn_id or handoff.turn_id,
        completion_seen=completion_seen,
        completion_event_ref=completion_event_ref,
    )

    transcript_present = bool((handoff.transcript or "").strip())
    final_result = transcript_present
    if final_result and not completion.completion_seen:
        raise RedactedTurnInputError(
            "Thought Core completion metadata is required for a source-no-live "
            "STT-to-Thought-Core summary"
        )

    safe_turn_token = _safe_token(completion.turn_id, "turn_source_no_live")
    turn_id_value = _ensure_prefixed(safe_turn_token, "turn")
    handoff_token = _safe_token(f"{handoff.source}_{safe_turn_token}", "handoff")
    handoff_id = _ensure_prefixed(handoff_token, "handoff")
    gate_ref = gate_event_ref or f"gate:{safe_turn_token}_input_gate"
    stt_ref = stt_event_ref or f"stt:{safe_turn_token}_redacted_final"
    handoff_ref = f"handoff:{handoff_token}"

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "turn_input_id": _ensure_prefixed(safe_turn_token, "rti"),
        "generated_at": generated_at or _utc_now_text(),
        "proof_layer": proof_layer,
        "source_label": source_label,
        "source_modality": source_modality,
        "input_gate_state": {
            "input_enabled": _expect_bool(input_enabled, "input_enabled"),
            "mic_enabled": _expect_bool(mic_enabled, "mic_enabled"),
            "reason": _safe_lower_label(input_gate_reason, "source_no_live_summary"),
            "source": _safe_lower_label(input_gate_source, "redacted_turn_input_helper"),
            "gate_event_ref": gate_ref,
        },
        "stt": {
            "transcript_present": transcript_present,
            "final_result": final_result,
            "transcript_bucket": "redacted_final" if final_result else "none",
            "stt_event_ref": stt_ref,
            "raw_transcript_included": False,
            "raw_audio_included": False,
            "provider_payload_included": False,
        },
        "handoff": {
            "handoff_id": handoff_id,
            "handoff_source": "ai_talk_core",
            "handoff_field": "redacted_transcript_ref",
            "handoff_ref": handoff_ref,
        },
        "thought_core": {
            "turn_id": turn_id_value,
            "completion_seen": completion.completion_seen,
            "completion_event_ref": completion.completion_event_ref,
        },
        "evidence_refs": [
            gate_ref,
            stt_ref,
            handoff_ref,
            completion.completion_event_ref,
        ],
        "redaction": {
            "redaction_profile": REDACTION_PROFILE,
            "shareability_class": "source_no_live"
            if proof_layer == "source-no-live"
            else "source_static",
            "raw_transcript_included": False,
            "raw_audio_included": False,
            "raw_media_included": False,
            "prompt_text_included": False,
            "response_text_included": False,
            "provider_payload_included": False,
            "private_path_included": False,
            "private_endpoint_included": False,
            "secret_or_token_included": False,
            "home_control_device_detail_included": False,
        },
        "safety": {
            "input_gate_only_is_stt_proof": False,
            "stt_only_is_thought_core_completion_proof": False,
            "action_execution_claimed": False,
            "motion_proof_claimed": False,
            "ordinary_conversation_quality_claimed": False,
            "robust_gesture_green_claimed": False,
            "source_adoption_or_git_claimed": False,
            "rr003_representative_pass_claimed": False,
        },
        "does_not_prove": list(REQUIRED_NON_CLAIMS),
    }
    assert_redacted_turn_input_summary_safe(summary)
    return summary


def resolve_thought_core_completion(
    events: Iterable[Mapping[str, Any] | ThoughtCoreStreamEvent],
    *,
    thought_core_result: Mapping[str, Any] | None = None,
    requested_turn_id: str | None = None,
    completion_seen: bool | None = None,
    completion_event_ref: str | None = None,
) -> ThoughtCoreCompletionRef:
    event_payloads = list(_coerce_events(events))
    completion_event = next(
        (
            event
            for event in reversed(event_payloads)
            if _event_type(event) == "turn.completed"
        ),
        None,
    )
    resolved_turn_id = (
        requested_turn_id
        or _event_text(completion_event, "turn_id")
        or _result_response_text(thought_core_result, "conversation_id")
        or "turn_source_no_live_unknown"
    )
    safe_turn_id = _ensure_prefixed(_safe_token(resolved_turn_id, "source_no_live"), "turn")
    seen = bool(completion_seen) if completion_seen is not None else completion_event is not None
    if completion_event_ref:
        ref = _normalize_ref(completion_event_ref, "thought", f"{safe_turn_id}_completed")
    elif completion_event is not None:
        ref = _normalize_ref(
            _event_text(completion_event, "event_id") or f"{safe_turn_id}_completed",
            "thought",
            f"{safe_turn_id}_completed",
        )
    else:
        ref = f"thought:{safe_turn_id}_completion_missing"
    return ThoughtCoreCompletionRef(
        turn_id=safe_turn_id,
        completion_seen=seen,
        completion_event_ref=ref,
    )


def assert_redacted_turn_input_summary_safe(summary: Mapping[str, Any]) -> None:
    """Validate the safety invariants that matter before sharing a summary."""

    def check(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in FALSE_ONLY_FLAGS and child is not False:
                    raise RedactedTurnInputError(f"{'.'.join(path + (str(key),))} must be false")
                check(child, path + (str(key),))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                check(child, path + (str(index),))
            return
        if isinstance(value, str):
            for pattern in UNSAFE_VALUE_PATTERNS:
                if pattern.search(value):
                    raise RedactedTurnInputError(
                        f"unsafe value in redacted turn input summary at {'.'.join(path)}"
                    )

    check(summary)


def _coerce_events(
    events: Iterable[Mapping[str, Any] | ThoughtCoreStreamEvent],
) -> Iterable[Mapping[str, Any]]:
    for event in events:
        if isinstance(event, ThoughtCoreStreamEvent):
            yield event.to_dict()
        elif isinstance(event, Mapping):
            yield event
        else:
            raise RedactedTurnInputError("Thought Core events must be mappings")


def _events_from_result(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    events = result.get("events")
    if events is None:
        return []
    if not isinstance(events, list):
        raise RedactedTurnInputError("thought_core_result events must be a list")
    return list(_coerce_events(events))


def _event_type(event: Mapping[str, Any] | None) -> str:
    if event is None:
        return ""
    return str(event.get("event_type") or event.get("type") or "").strip()


def _event_text(event: Mapping[str, Any] | None, key: str) -> str:
    if event is None:
        return ""
    value = event.get(key)
    if isinstance(value, str):
        return value.strip()
    return ""


def _result_response_text(result: Mapping[str, Any] | None, key: str) -> str:
    if result is None:
        return ""
    response = result.get("response")
    if not isinstance(response, Mapping):
        return ""
    value = response.get(key)
    return value.strip() if isinstance(value, str) else ""


def _validate_enum(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise RedactedTurnInputError(f"{label} must be one of: {', '.join(sorted(allowed))}")


def _expect_bool(value: bool, label: str) -> bool:
    if not isinstance(value, bool):
        raise RedactedTurnInputError(f"{label} must be a boolean")
    return value


def _normalize_ref(value: str, prefix: str, fallback: str) -> str:
    text = (value or "").strip()
    if ":" in text:
        candidate_prefix, candidate_value = text.split(":", 1)
        safe_prefix = candidate_prefix if candidate_prefix in {"event", "turn", "handoff", "gate", "stt", "thought", "summary", "source"} else prefix
        return f"{safe_prefix}:{_safe_token(candidate_value, fallback)}"
    return f"{prefix}:{_safe_token(text, fallback)}"


def _safe_lower_label(value: str, fallback: str) -> str:
    normalized = SAFE_TOKEN_RE.sub("_", (value or "").strip().lower())
    normalized = normalized.strip("._:-")
    if not normalized or not normalized[0].islower():
        return fallback
    return normalized[:120]


def _ensure_prefixed(value: str, prefix: str, *, max_length: int = 120) -> str:
    safe = _safe_token(value, prefix)
    expected = f"{prefix}_"
    prefixed = safe if safe.startswith(expected) else f"{expected}{safe}"
    return prefixed[:max_length].rstrip("._:-") or expected.rstrip("_")


def _safe_token(value: str | None, fallback: str) -> str:
    normalized = SAFE_TOKEN_RE.sub("_", (value or "").strip())
    normalized = normalized.strip("._:-")
    if not normalized:
        normalized = fallback
    return normalized[:120]


def _utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
