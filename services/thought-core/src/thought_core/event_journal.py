"""Redacted Event Journal writer for Thought Core turn events."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Mapping
from uuid import uuid4

from .correlation_feedback_contract import (
    SECRET_LIKE_STRING_PATTERN,
    closed_loop_enabled,
    load_closed_loop_contract,
    validate_closed_loop_event,
)
from .events import redact_secrets


SAFE_SUMMARY_STRING_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
CLOSED_LOOP_OUTPUT_CHAIN_MAX = 3
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
TEXT_SUMMARY_KEYS = {
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
PRESENCE_ONLY_KEYS = {
    "error",
    "preview_error",
    "reason",
    "review_basis",
}
SUMMARY_CODE_KEYS = {
    "action_id",
    "assistant_message_id",
    "contract_name",
    "error_code",
    "episode_id",
    "item_count",
    "issue_ticket_id",
    "memory_candidate_id",
    "observation_ref",
    "observation_source",
    "preview_error_code",
    "preview_status",
    "reason_code",
    "review_basis_code",
    "safe_to_act",
    "source",
    "stage",
    "status",
    "tool",
    "tool_call_id",
    "trace_id",
}


class ThoughtCoreEventJournal:
    """Append redacted Thought Core events to local JSONL journal files."""

    def __init__(self, *, path: str | Path | None = None, directory: str | Path | None = None) -> None:
        if path is None and directory is None:
            raise ValueError("path or directory is required")
        self.path = Path(path) if path is not None else None
        self.directory = Path(directory) if directory is not None else None
        self._lock = threading.Lock()
        self._closed_loop_index_loaded = False
        self._closed_loop_entries_by_event_id: dict[str, dict[str, Any]] = {}
        self._closed_loop_chain_entries: dict[
            tuple[str, str, str, str], list[dict[str, Any]]
        ] = {}
        self._authoritative_assistant_correlations: set[tuple[str, str, str]] = set()
        self._next_ingest_offset = 1

    def write_many(self, events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for event in events:
            entries.append(self.write_event(event))
        return entries

    def write_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        path = self._target_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = journal_entry_from_event(event)
        with self._lock:
            self._append_entry(path, entry)
            self._remember_authoritative_assistant_correlation(entry)
        return entry

    def append_closed_loop_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Durably append one safe v1 event and return the exact stored entry."""

        canonical_event = validate_closed_loop_event(event)
        with self._lock:
            self._load_closed_loop_index()
            return self._append_closed_loop_event_locked(canonical_event)

    def append_bound_closed_loop_output_event(
        self, event: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Append one HTTP output event only when its authoritative chain exists."""

        canonical_event = validate_closed_loop_event(event)
        with self._lock:
            self._load_closed_loop_index()
            self._validate_closed_loop_output_binding(canonical_event)
            return self._append_closed_loop_event_locked(canonical_event)

    def _append_closed_loop_event_locked(
        self, canonical_event: Mapping[str, Any]
    ) -> dict[str, Any]:
        event_id = str(canonical_event["event_id"])
        existing = self._closed_loop_entries_by_event_id.get(event_id)
        if existing is not None:
            if existing["event"] != canonical_event:
                raise ValueError("closed_loop_event_id_conflict")
            return dict(existing)
        chain_key = _closed_loop_chain_key(canonical_event)
        if (
            chain_key is not None
            and len(self._closed_loop_chain_entries.get(chain_key, []))
            >= CLOSED_LOOP_OUTPUT_CHAIN_MAX
        ):
            raise ValueError("closed_loop_output_chain_budget_exceeded")

        entry = {
            "schema_version": "closed-loop-event-journal-entry.v1",
            "journal_entry_id": f"jrn_{uuid4().hex}",
            "ingest_offset": self._next_ingest_offset,
            "recorded_at": _timestamp(),
            "event": dict(canonical_event),
            "redaction": {
                "level": "fixed_safe_fields",
                "raw_text_stored": False,
                "raw_media_stored": False,
                "raw_secret_stored": False,
            },
        }
        path = self._target_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._append_entry(path, entry, durable=True)
        self._closed_loop_entries_by_event_id[event_id] = entry
        self._remember_closed_loop_chain_entry(entry)
        self._next_ingest_offset += 1
        return dict(entry)

    def _validate_closed_loop_output_binding(
        self, event: Mapping[str, Any]
    ) -> None:
        correlation = _closed_loop_correlation(event)
        if correlation not in self._authoritative_assistant_correlations:
            raise ValueError("closed_loop_assistant_authority_missing")

        channel = str(event["details"]["output_channel"])
        component = str(event["details"]["component"])
        profile_name = _closed_loop_profile_name(event)
        chain = self._closed_loop_chain_entries.get(
            (*correlation, channel),
            [],
        )
        if len(chain) >= CLOSED_LOOP_OUTPUT_CHAIN_MAX:
            raise ValueError("closed_loop_output_chain_budget_exceeded")

        if profile_name == "dispatch_intent_recorded":
            if chain:
                raise ValueError("closed_loop_output_chain_duplicate")
            return

        parent_id = event.get("causal_parent_event_id")
        parent_entry = self._closed_loop_entries_by_event_id.get(str(parent_id))
        if parent_entry is None:
            raise ValueError("closed_loop_output_parent_missing")
        parent = parent_entry["event"]
        if (
            _closed_loop_correlation(parent) != correlation
            or parent["details"].get("output_channel") != channel
            or parent["details"].get("component") != component
        ):
            raise ValueError("closed_loop_output_parent_mismatch")
        if any(
            item.get("causal_parent_event_id") == parent_id for item in chain
        ):
            raise ValueError("closed_loop_output_parent_replayed")

        parent_profile = _closed_loop_profile_name(parent)
        permitted_next = {
            "dispatch_intent_recorded": {
                "send_attempt_started_outcome_unknown"
            },
            "send_attempt_started_outcome_unknown": {
                "submission_ack_needs_feedback",
                "possible_send_timeout",
                "dispatch_rejected_before_send",
            },
        }
        if profile_name not in permitted_next.get(parent_profile, set()):
            raise ValueError("closed_loop_output_transition_invalid")

    def replay_closed_loop_entries(self) -> list[dict[str, Any]]:
        """Read validated v1 entries only; historical v0 telemetry is ignored."""

        with self._lock:
            entries = self._read_closed_loop_entries()
        return [dict(entry) for entry in entries]

    def _append_entry(
        self,
        path: Path,
        entry: Mapping[str, Any],
        *,
        durable: bool = False,
    ) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
            if durable:
                stream.flush()
                os.fsync(stream.fileno())

    def _load_closed_loop_index(self) -> None:
        if self._closed_loop_index_loaded:
            return
        entries, correlations = self._read_closed_loop_index_snapshot()
        for entry in entries:
            event_id = entry["event"]["event_id"]
            existing = self._closed_loop_entries_by_event_id.get(event_id)
            if existing is not None and existing["event"] != entry["event"]:
                raise ValueError("closed_loop_event_id_conflict")
            if existing is None:
                self._closed_loop_entries_by_event_id[event_id] = entry
                self._remember_closed_loop_chain_entry(entry)
            self._next_ingest_offset = max(
                self._next_ingest_offset,
                int(entry["ingest_offset"]) + 1,
            )
        self._authoritative_assistant_correlations.update(correlations)
        self._closed_loop_index_loaded = True

    def _read_closed_loop_index_snapshot(
        self,
    ) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]]]:
        entries: list[dict[str, Any]] = []
        correlations: set[tuple[str, str, str]] = set()
        for path in self._journal_paths():
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError("event_journal_replay_invalid_json") from exc
                    if not isinstance(value, Mapping):
                        raise ValueError("event_journal_replay_entry_not_object")
                    if value.get("schema_version") == "thought-core.event-journal-entry.v0":
                        candidate = _authoritative_assistant_correlation(value)
                        if candidate is not None:
                            correlations.add(candidate)
                    elif value.get("schema_version") == "closed-loop-event-journal-entry.v1":
                        entries.append(_validate_closed_loop_journal_entry(value))
        entries.sort(key=lambda item: int(item["ingest_offset"]))
        return entries, correlations

    def _remember_authoritative_assistant_correlation(
        self, entry: Mapping[str, Any]
    ) -> None:
        candidate = _authoritative_assistant_correlation(entry)
        if candidate is not None:
            self._authoritative_assistant_correlations.add(candidate)

    def _remember_closed_loop_chain_entry(
        self, entry: Mapping[str, Any]
    ) -> None:
        event = entry.get("event")
        if not isinstance(event, Mapping):
            return
        key = _closed_loop_chain_key(event)
        if key is None:
            return
        chain = self._closed_loop_chain_entries.setdefault(key, [])
        if len(chain) >= CLOSED_LOOP_OUTPUT_CHAIN_MAX:
            raise ValueError("closed_loop_output_chain_budget_exceeded")
        chain.append(dict(event))

    def _read_closed_loop_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in self._journal_paths():
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError("event_journal_replay_invalid_json") from exc
                    if not isinstance(value, Mapping):
                        raise ValueError("event_journal_replay_entry_not_object")
                    if value.get("schema_version") != "closed-loop-event-journal-entry.v1":
                        continue
                    entries.append(_validate_closed_loop_journal_entry(value))
        entries.sort(key=lambda item: int(item["ingest_offset"]))
        return entries

    def _journal_paths(self) -> list[Path]:
        if self.path is not None:
            return [self.path] if self.path.exists() else []
        assert self.directory is not None
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("events-*.jsonl"))

    def _target_path(self) -> Path:
        if self.path is not None:
            return self.path
        assert self.directory is not None
        date = datetime.now(UTC).date().isoformat()
        return self.directory / f"events-{date}.jsonl"


def _closed_loop_correlation(event: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("session_id") or ""),
        str(event.get("turn_id") or ""),
        str(event.get("assistant_message_id") or ""),
    )


def _closed_loop_chain_key(
    event: Mapping[str, Any],
) -> tuple[str, str, str, str] | None:
    details = event.get("details")
    if not isinstance(details, Mapping):
        return None
    channel = details.get("output_channel")
    if not isinstance(channel, str) or not channel:
        return None
    return (*_closed_loop_correlation(event), channel)


def _authoritative_assistant_correlation(
    entry: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    if entry.get("event_type") not in {
        "assistant.message",
        "assistant.speech_delta",
    }:
        return None
    summary = entry.get("summary")
    if not isinstance(summary, Mapping):
        return None
    candidate = (
        entry.get("session_id"),
        entry.get("turn_id"),
        summary.get("assistant_message_id"),
    )
    if not all(
        isinstance(item, str) and SAFE_SUMMARY_STRING_PATTERN.fullmatch(item)
        for item in candidate
    ):
        return None
    return candidate  # type: ignore[return-value]


def _closed_loop_profile_name(event: Mapping[str, Any]) -> str:
    details = event.get("details")
    if not isinstance(details, Mapping):
        raise ValueError("closed_loop_output_details_invalid")
    contract = load_closed_loop_contract()
    event_kind = str(event.get("event_kind") or "")
    permitted = contract["http_output_ingress"]["profiles"].get(event_kind, [])
    profile_fields = {
        "phase",
        "outcome_class",
        "submission_class",
        "receipt_class",
        "cleanup_class",
        "proof_layer",
        "verification_class",
    }
    supplied = {key: details.get(key) for key in profile_fields}
    for profile_name in permitted:
        if supplied == contract["transition_profiles"][profile_name]:
            return str(profile_name)
    raise ValueError("closed_loop_output_profile_invalid")


def journal_from_env() -> ThoughtCoreEventJournal | None:
    path = os.environ.get("THOUGHT_CORE_EVENT_JOURNAL_PATH", "").strip()
    directory = os.environ.get("THOUGHT_CORE_EVENT_JOURNAL_DIR", "").strip()
    enabled = _env_bool("THOUGHT_CORE_EVENT_JOURNAL_ENABLED")
    if not enabled and not closed_loop_enabled() and not path and not directory:
        return None
    if path:
        return ThoughtCoreEventJournal(path=path)
    if not directory:
        directory = str(_repo_root_from_here() / ".cache" / "agent-os" / "events")
    return ThoughtCoreEventJournal(directory=directory)


def journal_entry_from_event(event: Mapping[str, Any]) -> dict[str, Any]:
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    return {
        "schema_version": "thought-core.event-journal-entry.v0",
        "journal_event_id": f"jrn_{uuid4().hex}",
        "recorded_at": _timestamp(),
        "source": "thought-core-api",
        "event_schema_version": _safe_top_level_ref(event.get("schema_version")),
        "event_id": _safe_top_level_ref(event.get("event_id")),
        "event_type": _safe_top_level_ref(event.get("type")),
        "event_timestamp": _safe_top_level_ref(event.get("timestamp")),
        "turn_id": _safe_top_level_ref(event.get("turn_id")),
        "session_id": _safe_top_level_ref(event.get("session_id")),
        "seq": event.get("seq") if isinstance(event.get("seq"), int) else None,
        "redaction": {
            "level": "summary_only",
            "raw_text_stored": False,
            "raw_media_stored": False,
            "raw_secret_stored": False,
        },
        "summary": summarize_event_data(data),
    }


def _validate_closed_loop_journal_entry(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "journal_entry_id",
        "ingest_offset",
        "recorded_at",
        "event",
        "redaction",
    }
    if set(value) != required:
        raise ValueError("closed_loop_journal_entry_shape_invalid")
    if value.get("schema_version") != "closed-loop-event-journal-entry.v1":
        raise ValueError("closed_loop_journal_entry_version_invalid")
    journal_entry_id = value.get("journal_entry_id")
    if not isinstance(journal_entry_id, str) or not SAFE_SUMMARY_STRING_PATTERN.fullmatch(
        journal_entry_id
    ):
        raise ValueError("closed_loop_journal_entry_id_invalid")
    ingest_offset = value.get("ingest_offset")
    if isinstance(ingest_offset, bool) or not isinstance(ingest_offset, int) or ingest_offset < 1:
        raise ValueError("closed_loop_journal_ingest_offset_invalid")
    recorded_at = value.get("recorded_at")
    if not isinstance(recorded_at, str) or not SAFE_SUMMARY_STRING_PATTERN.fullmatch(recorded_at):
        raise ValueError("closed_loop_journal_recorded_at_invalid")
    event = value.get("event")
    if not isinstance(event, Mapping):
        raise ValueError("closed_loop_journal_event_invalid")
    redaction = value.get("redaction")
    if redaction != {
        "level": "fixed_safe_fields",
        "raw_text_stored": False,
        "raw_media_stored": False,
        "raw_secret_stored": False,
    }:
        raise ValueError("closed_loop_journal_redaction_invalid")
    return {
        "schema_version": "closed-loop-event-journal-entry.v1",
        "journal_entry_id": journal_entry_id,
        "ingest_offset": ingest_offset,
        "recorded_at": recorded_at,
        "event": validate_closed_loop_event(event),
        "redaction": dict(redaction),
    }


def summarize_event_data(data: Mapping[str, Any]) -> dict[str, Any]:
    redacted = redact_secrets(dict(data))
    summary: dict[str, Any] = {
        "data_keys": _safe_key_names(redacted),
    }
    for key in SUMMARY_CODE_KEYS:
        if key not in redacted:
            continue
        safe_value = _safe_summary_scalar(redacted[key])
        if safe_value is not None:
            summary[key] = safe_value
        elif redacted[key] is not None:
            summary[f"{key}_present"] = True
    for key in PRESENCE_ONLY_KEYS:
        if key in redacted and redacted[key] is not None:
            summary[f"{key}_present"] = True
    for key in TEXT_SUMMARY_KEYS:
        if key in redacted:
            summary[f"{key}_present"] = bool(str(redacted.get(key) or ""))
            summary[f"{key}_chars"] = len(str(redacted.get(key) or ""))
    _summarize_nested(summary, redacted)
    return summary


def _summarize_nested(summary: dict[str, Any], data: Mapping[str, Any]) -> None:
    action = data.get("action") if isinstance(data.get("action"), Mapping) else {}
    if action:
        action_id = action.get("action_id")
        target = action.get("target")
        expected_state = action.get("expected_state")
        _add_summary_scalar(summary, "action_id", action_id)
        _add_summary_scalar(summary, "target", target)
        _add_summary_scalar(summary, "expected_state", expected_state)

    result = data.get("result") if isinstance(data.get("result"), Mapping) else {}
    if result:
        summary["result_keys"] = _safe_key_names(result)
        _add_summary_scalar(summary, "result_status", result.get("status"))

    working_memory = (
        data.get("working_memory_context")
        if isinstance(data.get("working_memory_context"), Mapping)
        else {}
    )
    if working_memory:
        for key in (
            "context_id",
            "freshness",
            "staleness",
            "uncertainty",
            "state_authority",
            "safe_for_thought_core_use",
            "safe_to_act",
            "not_proven",
        ):
            value = working_memory.get(key)
            _add_summary_scalar(summary, f"working_memory_{key}", value)


def _add_summary_scalar(summary: dict[str, Any], key: str, value: Any) -> None:
    safe_value = _safe_summary_scalar(value)
    if safe_value is not None:
        summary[key] = safe_value


def _safe_summary_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool | int | float):
        return value
    if not isinstance(value, str):
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    if SECRET_LIKE_STRING_PATTERN.search(compact):
        return None
    if not SAFE_SUMMARY_STRING_PATTERN.fullmatch(compact):
        return None
    return compact


def _safe_top_level_ref(value: Any) -> str:
    safe_value = _safe_summary_scalar(value)
    return safe_value if isinstance(safe_value, str) else ""


def _safe_key_names(value: Mapping[str, Any]) -> list[str]:
    return sorted(
        str(key)
        for key in value.keys()
        if not _is_sensitive_key(str(key))
    )[:40]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _env_bool(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "on", "yes"}


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[4]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
