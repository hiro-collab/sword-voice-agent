"""Summary-only Thought Core conversation runtime diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .events import ThoughtEvent, redact_secrets


CONVERSATION_RUNTIME_SUMMARY_VERSION = "thought_core_conversation_runtime_summary.v0"
TEST_OBSERVABILITY = "diagnostics_status"

SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
SECRET_TEXT_PATTERN = re.compile(
    r"(OPENAI_API_KEY=)[^\s]+|(sk-[A-Za-z0-9_-]{8,})|(Bearer\s+)[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "confirmation_token",
    "secret",
    "password",
    "credential",
)
DEFAULT_DOES_NOT_PROVE = (
    "provider_backed_quality_from_fallback",
    "durable_memory",
    "service_mode_pass",
    "live_stt_or_product_ui",
    "home_control_or_device_effect",
    "rr003_review_ready",
    "rr003_representative_pass",
)
ALLOWED_RETRIEVAL_DEPTHS = {
    "light_auto",
    "conditional_deep",
    "explicit_recall",
}


def build_thought_core_conversation_runtime_summary(
    events: list[ThoughtEvent | Mapping[str, Any]],
    *,
    scenario_id: str,
    input_mode: str,
    runtime_path: str,
    review_ready_impact: str | None = None,
    proof_ceiling: str | None = None,
    blocked_reason: str | None = None,
    relevance_coherence_class: str | None = None,
    turn_id: str | None = None,
    session_id_ref: str | None = None,
) -> dict[str, Any]:
    """Build a redacted diagnostic summary from a bounded Thought Core event stream."""

    event_dicts = [_event_mapping(event) for event in events]
    first_event = event_dicts[0] if event_dicts else {}
    safe_scenario = _safe_ref(scenario_id) or "scenario_unknown"
    safe_turn_id = _safe_ref(turn_id) or _safe_ref(first_event.get("turn_id"))
    safe_session_id = _safe_ref(session_id_ref) or _safe_ref(first_event.get("session_id"))
    response_route = _response_route(event_dicts, blocked_reason=blocked_reason)
    responder = _responder_completed(event_dicts)
    route_data = _route_data(event_dicts)
    fallback_used = bool(route_data.get("fallback_used")) or _status_is_fallback(
        _safe_ref(responder.get("status"))
    )
    used_llm = _safe_bool(responder.get("used_llm"))
    if used_llm is None:
        used_llm = _safe_bool(route_data.get("used_llm"))
    used_llm = bool(used_llm)
    provider_or_fallback = _provider_or_fallback(
        response_route=response_route,
        used_llm=used_llm,
        fallback_used=fallback_used,
        blocked_reason=blocked_reason,
    )
    provider_called = provider_or_fallback == "provider_backed"
    memory_context = _memory_context_summary(event_dicts, blocked_reason=blocked_reason)
    completion_status = _completion_status(event_dicts, blocked_reason=blocked_reason)
    visible_response_class = _visible_response_summary_class(event_dicts, blocked_reason)
    safe_proof_ceiling = (
        _safe_ref(proof_ceiling)
        or _default_proof_ceiling(provider_or_fallback, blocked_reason=blocked_reason)
    )

    summary = {
        "schema_version": CONVERSATION_RUNTIME_SUMMARY_VERSION,
        "summary_id": _summary_id(safe_scenario, safe_turn_id),
        "created_at": _timestamp(),
        "scenario_id": safe_scenario,
        "turn_id": safe_turn_id,
        "session_id_ref": safe_session_id,
        "input_mode": _safe_ref(input_mode) or "unknown",
        "runtime_path": _safe_ref(runtime_path) or "unknown",
        "response_route": response_route,
        "provider_or_fallback": provider_or_fallback,
        "provider_called": provider_called,
        "used_llm": used_llm,
        "fallback_reason": _fallback_reason(
            responder=responder,
            route_data=route_data,
            blocked_reason=blocked_reason,
        ),
        "trace_ref": _trace_ref(route_data, safe_turn_id),
        "event_refs": _event_refs(event_dicts),
        "completion_status": completion_status,
        "memory_context_used_class": memory_context["memory_context_used_class"],
        "memory_context_ref": memory_context["memory_context_ref"],
        "retrieval_depth": memory_context["retrieval_depth"],
        "must_revalidate_current_state": memory_context["must_revalidate_current_state"],
        "visible_response_summary_class": visible_response_class,
        "relevance_coherence_class": _safe_ref(relevance_coherence_class)
        or _default_relevance_class(
            provider_or_fallback=provider_or_fallback,
            blocked_reason=blocked_reason,
        ),
        "timing_bucket": "not_recorded",
        "test_observability": TEST_OBSERVABILITY,
        "raw_prompt_published": False,
        "raw_response_published": False,
        "raw_provider_payload_published": False,
        "raw_transcript_published": False,
        "raw_media_published": False,
        "private_path_published": False,
        "provider_payload_included": False,
        "proof_ceiling": safe_proof_ceiling,
        "does_not_prove": _does_not_prove(event_dicts, provider_or_fallback),
        "review_ready_impact": _safe_ref(review_ready_impact)
        or _default_review_ready_impact(blocked_reason=blocked_reason),
    }
    if blocked_reason:
        summary["blocker_code"] = _safe_ref(blocked_reason) or "blocked"
    return summary


def write_thought_core_conversation_runtime_summary(
    summary: Mapping[str, Any],
    path: str | Path,
) -> dict[str, Any]:
    """Append a local JSONL artifact and return local-only operational metadata."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(summary), ensure_ascii=False, sort_keys=True))
        stream.write("\n")
    return {
        "status": "ok",
        "written": True,
        "summary_id": summary.get("summary_id"),
        "local_only_metadata": True,
    }


def _event_mapping(value: ThoughtEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ThoughtEvent):
        return value.to_dict()
    return dict(value)


def _response_route(
    events: list[dict[str, Any]],
    *,
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return "blocked"
    route = _route_data(events)
    safe_route = _safe_ref(route.get("response_route"))
    if safe_route:
        return safe_route
    return "unknown"


def _route_data(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        if event.get("type") == "thought_core.response_route_classified":
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            return redact_secrets(dict(data))
    return {}


def _responder_completed(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        if event.get("type") == "responder.completed":
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            return redact_secrets(dict(data))
    return {}


def _provider_or_fallback(
    *,
    response_route: str,
    used_llm: bool,
    fallback_used: bool,
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return "not_applicable"
    if used_llm and not fallback_used:
        return "provider_backed"
    if fallback_used:
        return "local_fallback"
    if response_route in {"audio_status_check", "blocked", "unknown"}:
        return "not_applicable"
    return "non_provider_local_route"


def _fallback_reason(
    *,
    responder: Mapping[str, Any],
    route_data: Mapping[str, Any],
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return _safe_ref(blocked_reason) or "blocked"
    for value in (route_data.get("responder_status"), responder.get("status")):
        safe = _safe_ref(value)
        if safe and (_status_is_fallback(safe) or safe not in {"llm_response"}):
            return safe
    return ""


def _trace_ref(route_data: Mapping[str, Any], turn_id: str) -> str:
    trace_id = _safe_ref(route_data.get("trace_id"))
    if trace_id:
        return f"trace:{trace_id}"
    if turn_id:
        return f"turn:{turn_id}"
    return ""


def _event_refs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for event in events:
        event_id = _safe_ref(event.get("event_id"))
        event_type = _safe_ref(event.get("type"))
        if event_id or event_type:
            refs.append({"event_id": event_id, "event_type": event_type})
    return refs[:40]


def _completion_status(
    events: list[dict[str, Any]],
    *,
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return "blocked"
    for event in reversed(events):
        if event.get("type") == "turn.completed":
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            return _safe_ref(data.get("status")) or "completed"
    return "not_observed"


def _memory_context_summary(
    events: list[dict[str, Any]],
    *,
    blocked_reason: str | None,
) -> dict[str, Any]:
    if blocked_reason:
        return {
            "memory_context_used_class": "blocked_not_executed",
            "memory_context_ref": "",
            "retrieval_depth": None,
            "must_revalidate_current_state": None,
        }
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        data = redact_secrets(dict(data))
        candidate = _memory_context_candidate(data)
        if candidate:
            return candidate
        if event.get("type") == "context.used":
            return {
                "memory_context_used_class": "working_memory_only",
                "memory_context_ref": _safe_ref(data.get("context_id")),
                "retrieval_depth": None,
                "must_revalidate_current_state": _safe_bool(
                    data.get("must_revalidate_current_state")
                ),
            }
        if event.get("type") == "memory.retrieved" and _safe_int(data.get("item_count")) > 0:
            return {
                "memory_context_used_class": "working_memory_only",
                "memory_context_ref": _safe_ref(data.get("source")) or "",
                "retrieval_depth": None,
                "must_revalidate_current_state": _safe_bool(
                    data.get("must_revalidate_current_state")
                ),
            }
    return {
        "memory_context_used_class": "none",
        "memory_context_ref": "",
        "retrieval_depth": None,
        "must_revalidate_current_state": None,
    }


def _memory_context_candidate(data: Mapping[str, Any]) -> dict[str, Any] | None:
    contexts: list[Mapping[str, Any]] = []
    for key in ("memory_context", "memory_context_ref", "working_memory_context"):
        value = data.get(key)
        if isinstance(value, Mapping):
            contexts.append(value)
    if _safe_ref(data.get("schema_version")) == "memory_context_ref.v0":
        contexts.append(data)
    for context in contexts:
        schema_version = _safe_ref(context.get("schema_version"))
        retrieval_depth = _safe_ref(context.get("retrieval_depth"))
        context_ref = (
            _safe_ref(context.get("context_id"))
            or _safe_ref(context.get("memory_context_id"))
            or _safe_ref(context.get("ref"))
        )
        must_revalidate = _safe_bool(context.get("must_revalidate_current_state"))
        if (
            schema_version == "memory_context_ref.v0"
            or retrieval_depth in ALLOWED_RETRIEVAL_DEPTHS
        ):
            return {
                "memory_context_used_class": "memory_core_retrieval",
                "memory_context_ref": context_ref,
                "retrieval_depth": (
                    retrieval_depth if retrieval_depth in ALLOWED_RETRIEVAL_DEPTHS else None
                ),
                "must_revalidate_current_state": must_revalidate,
            }
        if context_ref:
            return {
                "memory_context_used_class": "working_memory_only",
                "memory_context_ref": context_ref,
                "retrieval_depth": None,
                "must_revalidate_current_state": must_revalidate,
            }
    return None


def _visible_response_summary_class(
    events: list[dict[str, Any]],
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return "blocked"
    for event in reversed(events):
        if event.get("type") != "assistant.message":
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        speech = data.get("speech")
        display = data.get("display")
        text_len = max(
            len(speech) if isinstance(speech, str) else 0,
            len(display) if isinstance(display, str) else 0,
        )
        if text_len <= 0:
            return "absent"
        if text_len <= 280:
            return "present_nonempty_short"
        return "present_nonempty_long"
    return "absent"


def _does_not_prove(
    events: list[dict[str, Any]],
    provider_or_fallback: str,
) -> list[str]:
    values = list(DEFAULT_DOES_NOT_PROVE)
    for event in events:
        if event.get("type") != "thought_core.response_route_classified":
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        non_claims = data.get("non_claims")
        if not isinstance(non_claims, list):
            continue
        for item in non_claims:
            safe = _safe_ref(item)
            if safe and safe not in values:
                values.append(safe)
    if provider_or_fallback == "provider_backed":
        values = [value for value in values if value != "provider_backed_quality_from_fallback"]
        values.append("provider_quality_without_review")
    return values[:24]


def _default_proof_ceiling(
    provider_or_fallback: str,
    *,
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return "source_no_live_blocked_summary"
    if provider_or_fallback == "provider_backed":
        return "provider_backed_summary_only"
    if provider_or_fallback == "local_fallback":
        return "local_fallback_runtime_support_only"
    return "source_no_live_summary_only"


def _default_relevance_class(
    *,
    provider_or_fallback: str,
    blocked_reason: str | None,
) -> str:
    if blocked_reason:
        return "blocked_not_tested"
    if provider_or_fallback == "provider_backed":
        return "provider_relevant_limited"
    if provider_or_fallback == "local_fallback":
        return "fallback_relevant_limited"
    return "route_observed_unreviewed"


def _default_review_ready_impact(*, blocked_reason: str | None) -> str:
    if blocked_reason:
        return "blocking_gap"
    return "supports_review_ready_but_not_close"


def _status_is_fallback(status: str) -> bool:
    return status.startswith("local_fallback")


def _summary_id(scenario_id: str, turn_id: str) -> str:
    if turn_id:
        return f"tcrs_{turn_id}"
    return f"tcrs_{scenario_id}"


def _safe_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _safe_int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _safe_ref(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.strip().split())
    if not compact or SECRET_TEXT_PATTERN.search(compact):
        return ""
    if _unsafe_text(compact):
        return ""
    if not SAFE_REF_PATTERN.fullmatch(compact):
        return ""
    return compact


def _unsafe_text(value: str) -> bool:
    compact = " ".join(value.strip().split())
    lowered = compact.lower().replace("-", "_")
    if "://" in lowered or ":\\" in compact or "\\" in compact or "/" in compact:
        return True
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return True
    if "raw_artifact" in lowered or "provider_payload" in lowered:
        return True
    home_assistant_domains = (
        "light.",
        "switch.",
        "cover.",
        "climate.",
        "fan.",
        "vacuum.",
        "sensor.",
        "binary_sensor.",
        "media_player.",
    )
    return lowered.startswith(home_assistant_domains)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
