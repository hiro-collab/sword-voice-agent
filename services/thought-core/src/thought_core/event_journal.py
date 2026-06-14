"""Redacted Event Journal writer for Thought Core turn events."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from .events import redact_secrets


SAFE_SUMMARY_STRING_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")
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

    def write_many(self, events: list[Mapping[str, Any]]) -> None:
        for event in events:
            self.write_event(event)

    def write_event(self, event: Mapping[str, Any]) -> None:
        path = self._target_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = journal_entry_from_event(event)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            stream.write("\n")

    def _target_path(self) -> Path:
        if self.path is not None:
            return self.path
        assert self.directory is not None
        date = datetime.now(UTC).date().isoformat()
        return self.directory / f"events-{date}.jsonl"


def journal_from_env() -> ThoughtCoreEventJournal | None:
    path = os.environ.get("THOUGHT_CORE_EVENT_JOURNAL_PATH", "").strip()
    directory = os.environ.get("THOUGHT_CORE_EVENT_JOURNAL_DIR", "").strip()
    enabled = _env_bool("THOUGHT_CORE_EVENT_JOURNAL_ENABLED")
    if not enabled and not path and not directory:
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
    if SECRET_TEXT_PATTERN.search(compact):
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
