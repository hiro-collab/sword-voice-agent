"""Redacted turn trace and memory-candidate artifacts for Thought Core."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .events import ThoughtEvent, redact_secrets
from .schema import TurnInput


TURN_TRACE_SCHEMA_VERSION = "thought_core_turn_trace.v0"
MEMORY_CANDIDATE_SCHEMA_VERSION = "memory_candidate.v0"

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
UNSAFE_KEY_PARTS = (
    "raw",
    "prompt",
    "transcript",
    "provider_payload",
    "screenshot",
    "frame",
    "media",
    "local_path",
    "path",
    "entity_id",
    "home_assistant",
)
TEXT_KEYS = {
    "delta",
    "display",
    "message",
    "request_text",
    "response_text",
    "speech",
    "text",
    "turn_text",
    "user_text",
}
SAFE_DATA_KEYS = {
    "action_id",
    "candidate_id",
    "contract_name",
    "derived_from_event_id",
    "deletion_or_forgetting_state",
    "durable_memory_claimed",
    "evidence_summary_ref",
    "item_count",
    "memory_type",
    "observation_ref",
    "observation_source",
    "protected_or_deletable",
    "redaction_state",
    "retention_class",
    "safe_to_act",
    "scope",
    "source",
    "stage",
    "status",
    "tool",
    "tool_call_id",
    "trace_id",
    "why_record",
    "write_status",
    "written",
}
WORKING_MEMORY_KEYS = (
    "context_id",
    "freshness",
    "staleness",
    "uncertainty",
    "state_authority",
    "safe_for_thought_core_use",
    "safe_to_act",
    "not_proven",
)


def build_thought_core_turn_trace(
    turn_input: TurnInput | Mapping[str, Any],
    events: list[ThoughtEvent | Mapping[str, Any]],
    *,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Build a redacted per-turn lifecycle trace from Thought Core events."""

    turn = _turn_input(turn_input)
    event_dicts = [_event_mapping(event) for event in events]
    safe_trace_id = _safe_ref(trace_id or turn.context_refs.get("trace_id"))
    if not safe_trace_id:
        safe_trace_id = _safe_ref(f"trace_{turn.turn_id}") or "trace_turn"

    summarized_events = [summarize_trace_event(event) for event in event_dicts]
    memory_candidate_ids = _memory_candidate_ids(summarized_events)
    working_memory_summary = _working_memory_summary_from_events(event_dicts)

    return {
        "schema_version": TURN_TRACE_SCHEMA_VERSION,
        "trace_id": safe_trace_id,
        "created_at": _timestamp(),
        "turn_id": _safe_ref(turn.turn_id),
        "session_id": _safe_ref(turn.session_id),
        "input_summary": {
            "text_present": bool(turn.text),
            "text_chars": len(turn.text),
            "locale": _safe_ref(turn.locale),
        },
        "prior_context_summary": _summarize_context_refs(turn.context_refs),
        "working_memory_summary": working_memory_summary,
        "events": summarized_events,
        "action_results": _action_results(summarized_events),
        "memory_retrieved": _memory_retrieved(summarized_events),
        "memory_candidate_ids": memory_candidate_ids,
        "degraded_state": _degraded_state(summarized_events),
        "redaction": {
            "level": "summary_only",
            "raw_text_stored": False,
            "raw_media_stored": False,
            "raw_secret_stored": False,
        },
        "non_claims": [
            "durable_memory",
            "os_wide_state_event_ingest",
            "live_device_proof",
            "rr003_representative_pass",
        ],
    }


def summarize_trace_event(event: ThoughtEvent | Mapping[str, Any]) -> dict[str, Any]:
    value = _event_mapping(event)
    data = value.get("data") if isinstance(value.get("data"), Mapping) else {}
    data = redact_secrets(dict(data))
    summary: dict[str, Any] = {
        "event_id": _safe_ref(value.get("event_id")),
        "event_type": _safe_ref(value.get("type")),
        "seq": value.get("seq") if isinstance(value.get("seq"), int) else None,
        "data_keys": _safe_key_names(data),
    }
    for key in SAFE_DATA_KEYS:
        if key in data:
            _add_safe_scalar(summary, key, data.get(key))
    for key in TEXT_KEYS:
        if key in data:
            summary[f"{key}_present"] = bool(str(data.get(key) or ""))
            summary[f"{key}_chars"] = len(str(data.get(key) or ""))

    action = data.get("action") if isinstance(data.get("action"), Mapping) else {}
    if action:
        _add_safe_scalar(summary, "action_id", action.get("action_id"))
        _add_safe_target(summary, "target", action.get("target"))
        _add_safe_scalar(summary, "expected_state", action.get("expected_state"))

    result = data.get("result") if isinstance(data.get("result"), Mapping) else {}
    if result:
        summary["result_keys"] = _safe_key_names(result)
        _add_safe_scalar(summary, "result_status", result.get("status"))

    working_memory = (
        data.get("working_memory_context")
        if isinstance(data.get("working_memory_context"), Mapping)
        else {}
    )
    if working_memory:
        summary["working_memory_context"] = _working_memory_summary(working_memory)
    return summary


def memory_candidate_from_trace(
    trace: Mapping[str, Any],
    *,
    memory_type: str = "turn_trace_summary",
    scope: str = "turn_traces",
    why_record: str = "rr003_representative_trace",
    importance: str = "review_candidate",
) -> dict[str, Any]:
    """Create a generic memory_candidate.v0 envelope without committing memory."""

    trace_id = _safe_ref(trace.get("trace_id")) or "trace_unknown"
    candidate_id = _safe_ref(f"mcand_{trace_id}_001") or "mcand_trace_001"
    events = trace.get("events") if isinstance(trace.get("events"), list) else []
    derived_event_id = ""
    for event in reversed(events):
        if isinstance(event, Mapping):
            derived_event_id = _safe_ref(event.get("event_id"))
            if derived_event_id:
                break
    return {
        "schema_version": MEMORY_CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "trace_id": trace_id,
        "derived_from_trace_id": trace_id,
        "derived_from_event_id": derived_event_id,
        "memory_type": _safe_ref(memory_type) or "turn_trace_summary",
        "scope": _safe_ref(scope) or "turn_traces",
        "status": "candidate",
        "why_record": _safe_ref(why_record) or "rr003_representative_trace",
        "importance": _safe_ref(importance) or "review_candidate",
        "evidence_summary_ref": f"trace:{trace_id}",
        "retention_class": "candidate_ephemeral_review",
        "redaction_state": "summary_only_no_raw_evidence",
        "deletion_or_forgetting_state": "deletable_candidate",
        "protected_or_deletable": "deletable_unprotected",
        "safe_to_act": False,
        "safe_for_future_reasoning": {
            "allowed": True,
            "mode": "advisory_only",
            "must_revalidate_current_state": True,
        },
        "content_summary": {
            "event_count": len(events),
            "memory_candidate_ids": list(trace.get("memory_candidate_ids") or [])[:12],
            "working_memory_status": (
                trace.get("working_memory_summary", {}).get("status")
                if isinstance(trace.get("working_memory_summary"), Mapping)
                else ""
            ),
            "degraded_state": trace.get("degraded_state") or "none",
        },
        "source": {
            "service": "thought-core",
            "trace_id": trace_id,
            "turn_id": _safe_ref(trace.get("turn_id")),
            "session_id": _safe_ref(trace.get("session_id")),
        },
        "durable_memory_claimed": False,
        "created_at": _timestamp(),
    }


def write_thought_core_turn_trace(trace: Mapping[str, Any], path: str | Path) -> dict[str, Any]:
    return _append_jsonl_artifact(path, dict(trace), id_key="trace_id")


def write_memory_candidate(candidate: Mapping[str, Any], path: str | Path) -> dict[str, Any]:
    return _append_jsonl_artifact(path, dict(candidate), id_key="candidate_id")


def _append_jsonl_artifact(path: str | Path, payload: dict[str, Any], *, id_key: str) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
    return {
        "status": "ok",
        "written": True,
        id_key: payload.get(id_key),
    }


def _turn_input(value: TurnInput | Mapping[str, Any]) -> TurnInput:
    if isinstance(value, TurnInput):
        return value
    return TurnInput.from_mapping(value)


def _event_mapping(value: ThoughtEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ThoughtEvent):
        return value.to_dict()
    return dict(value)


def _summarize_context_refs(refs: Mapping[str, Any]) -> dict[str, Any]:
    safe_keys = [str(key) for key in refs if not _is_sensitive_key(str(key))]
    working_memory = refs.get("working_memory_context")
    if not isinstance(working_memory, Mapping):
        working_memory = refs.get("prior_result_context")
    summary = {
        "ref_keys": sorted(safe_keys)[:40],
        "working_memory_present": isinstance(working_memory, Mapping),
    }
    if isinstance(working_memory, Mapping):
        summary["working_memory_context"] = _working_memory_summary(working_memory)
    return summary


def _working_memory_summary_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        working_memory = (
            data.get("working_memory_context")
            if isinstance(data.get("working_memory_context"), Mapping)
            else {}
        )
        if working_memory:
            summary = _working_memory_summary(working_memory)
            if "context_id" not in summary:
                _add_safe_scalar(summary, "context_id", data.get("context_id"))
            return summary
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        if event.get("type") == "context.used":
            summary = {"status": "used"}
            _add_safe_scalar(summary, "context_id", data.get("context_id"))
            return summary
        if event.get("type") == "context.received":
            summary = {"status": "received"}
            _add_safe_scalar(summary, "context_id", data.get("context_id"))
            return summary
    return {"status": "absent"}


def _working_memory_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    summary = {"status": "available"}
    for key in WORKING_MEMORY_KEYS:
        if key in value:
            _add_safe_scalar(summary, key, value.get(key))
    return summary


def _action_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {"action.reviewed", "action.proposed", "tool.result"}:
            continue
        result = {
            "event_id": event.get("event_id"),
            "event_type": event_type,
        }
        for key in ("action_id", "status", "result_status", "tool", "write_status"):
            if key in event:
                result[key] = event[key]
        results.append(result)
    return results[:20]


def _memory_retrieved(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        if event.get("event_type") == "memory.retrieved":
            return {
                "status": event.get("status") or "unknown",
                "item_count": event.get("item_count", 0),
                "source": event.get("source") or "",
            }
    return {"status": "absent", "item_count": 0, "source": ""}


def _memory_candidate_ids(events: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for event in events:
        for key in ("candidate_id", "memory_candidate_id"):
            value = _safe_ref(event.get(key))
            if value and value not in ids:
                ids.append(value)
    return ids[:12]


def _degraded_state(events: list[dict[str, Any]]) -> str:
    event_types = {str(event.get("event_type") or "") for event in events}
    if "turn.error" in event_types:
        return "turn_error"
    if "feedback.requested" in event_types:
        return "feedback_requested"
    for event in events:
        status = str(event.get("status") or event.get("result_status") or "")
        if status in {"failed", "error", "needs_feedback"}:
            return status
    return "none"


def _add_safe_scalar(target: dict[str, Any], key: str, value: Any) -> None:
    safe_value = _safe_scalar(value)
    if safe_value is not None:
        target[key] = safe_value


def _add_safe_target(target: dict[str, Any], key: str, value: Any) -> None:
    safe_value = _safe_scalar(value)
    if isinstance(safe_value, str) and not _unsafe_target_value(safe_value):
        target[key] = safe_value


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool | int | float):
        return value
    if not isinstance(value, str):
        return None
    return _safe_ref(value) or None


def _safe_ref(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.strip().split())
    if not compact or SECRET_TEXT_PATTERN.search(compact):
        return ""
    if not SAFE_REF_PATTERN.fullmatch(compact):
        return ""
    return compact


def _unsafe_target_value(value: str) -> bool:
    compact = " ".join(value.strip().split())
    lowered = compact.lower().replace("-", "_")
    if "://" in lowered or ":\\" in compact or "\\" in compact or "/" in compact:
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


def _safe_key_names(value: Mapping[str, Any]) -> list[str]:
    return sorted(str(key) for key in value.keys() if not _is_unsafe_key(str(key)))[:40]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _is_unsafe_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return _is_sensitive_key(lowered) or any(part in lowered for part in UNSAFE_KEY_PARTS)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
