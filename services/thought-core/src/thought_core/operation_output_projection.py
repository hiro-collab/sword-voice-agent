"""Replay-derived operation/output state for bounded next-turn feedback."""

from __future__ import annotations

from copy import deepcopy
import threading
from typing import Any, Iterable, Mapping

from .correlation_feedback_contract import (
    load_closed_loop_contract,
    validate_closed_loop_event,
)


PROJECTION_SCHEMA_VERSION = "operation-output-projection.v1"
_TERMINAL_SETTLED_OUTCOMES = {"succeeded", "failed", "cancelled", "rejected_busy"}
_SUBMISSION_RANK = {"not_submitted": 0, "may_have_submitted": 1, "submitted": 2}
_RECEIPT_RANK = {
    "none": 0,
    "submission_ack": 1,
    "correlated_success": 2,
    "correlated_failure": 2,
}
_CLEANUP_RANK = {"not_required": 0, "pending": 1, "unproved": 1, "complete": 2}


def empty_operation_output_projection() -> dict[str, Any]:
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "as_of_ingest_offset": 0,
        "seen_event_ids": [],
        "operations": {},
        "outputs": {},
        "recent_feedback": [],
    }


def reduce_operation_output_projection(
    state: Mapping[str, Any],
    journal_entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure reducer used identically by live ingest and Journal replay."""

    current = _validated_state_copy(state)
    entry = _validated_entry(journal_entry)
    event = entry["event"]
    event_id = event["event_id"]
    if event_id in current["seen_event_ids"]:
        return current

    current["seen_event_ids"].append(event_id)
    current["as_of_ingest_offset"] = max(
        current["as_of_ingest_offset"],
        entry["ingest_offset"],
    )
    event_kind = event["event_kind"]
    if event_kind == "operation.transition":
        operation_id = event["operation_id"]
        existing = current["operations"].get(operation_id)
        candidate = _record_from_entry(entry, entity_id=operation_id)
        current["operations"][operation_id] = _merge_record(existing, candidate)
        if event["details"]["outcome_class"] != "none":
            _append_feedback(current, entry, item_type="operation_feedback")
    elif event_kind in {"output.dispatch_intent", "output.feedback"}:
        message_id = event["assistant_message_id"]
        existing = current["outputs"].get(message_id)
        candidate = _record_from_entry(entry, entity_id=message_id)
        current["outputs"][message_id] = _merge_record(existing, candidate)
        if event_kind == "output.feedback":
            _append_feedback(current, entry, item_type="output_feedback")
    else:  # contract validation should make this unreachable
        raise ValueError("closed_loop_projection_event_kind_invalid")
    return current


def replay_operation_output_projection(
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    state = empty_operation_output_projection()
    for entry in sorted(entries, key=lambda item: int(item.get("ingest_offset", 0))):
        state = reduce_operation_output_projection(state, entry)
    return state


class OperationOutputProjection:
    """Thread-safe in-memory projection; Event Journal remains durable authority."""

    def __init__(self, entries: Iterable[Mapping[str, Any]] = ()) -> None:
        self._lock = threading.Lock()
        self._state = replay_operation_output_projection(entries)

    @classmethod
    def from_journal(cls, journal: Any) -> "OperationOutputProjection":
        return cls(journal.replay_closed_loop_entries())

    def ingest(self, journal_entry: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._state = reduce_operation_output_projection(self._state, journal_entry)
            return deepcopy(self._state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def provider_sections(self, *, session_id: str) -> dict[str, Any]:
        with self._lock:
            state = deepcopy(self._state)
        contract = load_closed_loop_contract()
        active_limit = int(contract["limits"]["active_operations"])
        feedback_limit = int(contract["limits"]["feedback_context"])

        active = [
            _provider_operation(record)
            for record in state["operations"].values()
            if record.get("session_id") == session_id and record.get("phase") != "terminal"
        ]
        active.sort(key=lambda item: int(item["as_of_ingest_offset"]), reverse=True)
        feedback = [
            _provider_feedback(item)
            for item in state["recent_feedback"]
            if item.get("session_id") == session_id
        ]
        feedback.sort(key=lambda item: int(item["as_of_ingest_offset"]), reverse=True)
        return {
            "as_of_ingest_offset": state["as_of_ingest_offset"],
            "active_operations": active[:active_limit],
            "feedback_context": feedback[:feedback_limit],
        }


def _validated_state_copy(state: Mapping[str, Any]) -> dict[str, Any]:
    copied = deepcopy(dict(state))
    required = {
        "schema_version",
        "as_of_ingest_offset",
        "seen_event_ids",
        "operations",
        "outputs",
        "recent_feedback",
    }
    if set(copied) != required or copied.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        raise ValueError("closed_loop_projection_state_invalid")
    if not isinstance(copied["seen_event_ids"], list):
        raise ValueError("closed_loop_projection_seen_events_invalid")
    if not isinstance(copied["operations"], dict) or not isinstance(copied["outputs"], dict):
        raise ValueError("closed_loop_projection_entities_invalid")
    if not isinstance(copied["recent_feedback"], list):
        raise ValueError("closed_loop_projection_feedback_invalid")
    return copied


def _validated_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    if entry.get("schema_version") != "closed-loop-event-journal-entry.v1":
        raise ValueError("closed_loop_projection_entry_version_invalid")
    ingest_offset = entry.get("ingest_offset")
    if isinstance(ingest_offset, bool) or not isinstance(ingest_offset, int) or ingest_offset < 1:
        raise ValueError("closed_loop_projection_ingest_offset_invalid")
    event = entry.get("event")
    if not isinstance(event, Mapping):
        raise ValueError("closed_loop_projection_event_invalid")
    return {
        "schema_version": "closed-loop-event-journal-entry.v1",
        "journal_entry_id": str(entry.get("journal_entry_id") or ""),
        "ingest_offset": ingest_offset,
        "recorded_at": str(entry.get("recorded_at") or ""),
        "event": validate_closed_loop_event(event),
    }


def _record_from_entry(entry: Mapping[str, Any], *, entity_id: str) -> dict[str, Any]:
    event = entry["event"]
    record = {
        "entity_id": entity_id,
        "session_id": event["session_id"],
        "turn_id": event["turn_id"],
        "event_id": event["event_id"],
        "source_authority": event["source_authority"],
        "observed_at": event["observed_at"],
        "as_of_ingest_offset": entry["ingest_offset"],
        **event["details"],
    }
    for key in (
        "operation_id",
        "assistant_message_id",
        "causal_parent_event_id",
        "responds_to_message_id",
        "target_operation_id",
        "operation_revision",
        "stale_after",
    ):
        if key in event:
            record[key] = event[key]
    return record


def _merge_record(
    existing: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    if existing is None:
        return dict(candidate)
    old_revision = int(existing.get("operation_revision", 0))
    new_revision = int(candidate.get("operation_revision", 0))
    if new_revision < old_revision:
        return dict(existing)
    merged = {**existing, **candidate}
    if existing.get("phase") != "terminal":
        return merged

    merged["phase"] = "terminal"
    old_outcome = str(existing.get("outcome_class") or "none")
    new_outcome = str(candidate.get("outcome_class") or "none")
    if old_outcome in _TERMINAL_SETTLED_OUTCOMES and new_outcome != old_outcome:
        merged["outcome_class"] = old_outcome
    merged["submission_class"] = _ranked_value(
        str(existing.get("submission_class") or "not_submitted"),
        str(candidate.get("submission_class") or "not_submitted"),
        _SUBMISSION_RANK,
    )
    merged["receipt_class"] = _ranked_value(
        str(existing.get("receipt_class") or "none"),
        str(candidate.get("receipt_class") or "none"),
        _RECEIPT_RANK,
    )
    merged["cleanup_class"] = _ranked_value(
        str(existing.get("cleanup_class") or "not_required"),
        str(candidate.get("cleanup_class") or "not_required"),
        _CLEANUP_RANK,
    )
    return merged


def _ranked_value(old: str, new: str, ranks: Mapping[str, int]) -> str:
    return new if ranks.get(new, -1) >= ranks.get(old, -1) else old


def _append_feedback(
    state: dict[str, Any],
    entry: Mapping[str, Any],
    *,
    item_type: str,
) -> None:
    event = entry["event"]
    item = {
        "item_type": item_type,
        "session_id": event["session_id"],
        "turn_id": event["turn_id"],
        "event_id": event["event_id"],
        "source_authority": event["source_authority"],
        "observed_at": event["observed_at"],
        "as_of_ingest_offset": entry["ingest_offset"],
        **event["details"],
    }
    for key in ("operation_id", "assistant_message_id", "causal_parent_event_id"):
        if key in event:
            item[key] = event[key]
    state["recent_feedback"].append(item)
    limit = int(load_closed_loop_contract()["limits"]["recent_projection_entries"])
    state["recent_feedback"] = state["recent_feedback"][-limit:]


def _provider_operation(record: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "operation_id",
        "turn_id",
        "phase",
        "outcome_class",
        "submission_class",
        "receipt_class",
        "cleanup_class",
        "source_authority",
        "observed_at",
        "as_of_ingest_offset",
    )
    return {"item_type": "active_operation", **{key: record[key] for key in allowed if key in record}}


def _provider_feedback(record: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "operation_id",
        "assistant_message_id",
        "output_channel",
        "phase",
        "outcome_class",
        "submission_class",
        "receipt_class",
        "cleanup_class",
        "proof_layer",
        "verification_class",
        "source_authority",
        "observed_at",
        "as_of_ingest_offset",
        "late",
    )
    return {key: record[key] for key in allowed if key in record}
