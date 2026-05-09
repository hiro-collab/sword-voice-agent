from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from sword_voice_agent.system.access_control import PolicyStore


SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|bearer|password|secret|token)",
    re.IGNORECASE,
)
SECRET_TEXT_PATTERN = re.compile(
    r"(OPENAI_API_KEY=)[^\s]+|(sk-[A-Za-z0-9_-]{8,})|(Bearer\s+)[A-Za-z0-9._-]+",
    re.IGNORECASE,
)


class EventJournal:
    """Append-only JSONL event journal with minimal redaction."""

    def __init__(
        self,
        root: str | Path,
        *,
        policy: PolicyStore | None = None,
    ) -> None:
        self.root = Path(root)
        self.policy = policy

    @property
    def all_events_path(self) -> Path:
        return self.root / "all-events.jsonl"

    def service_events_path(self, service: str) -> Path:
        return self.root / f"{_safe_name(service)}.events.jsonl"

    def append_event(
        self,
        *,
        service_id: str,
        service: str,
        event: str,
        trace_id: str,
        payload: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        level: str = "info",
        layer: str | None = None,
    ) -> dict[str, Any]:
        if self.policy is not None:
            self.policy.require(service_id, "events.append")
        entry: dict[str, Any] = {
            "schema_version": "system.event.v0",
            "event_id": f"evt_{uuid4().hex}",
            "ts": _timestamp(),
            "trace_id": trace_id,
            "service": service,
            "event": event,
            "level": level,
            "payload": redact(payload or {}),
        }
        if turn_id:
            entry["turn_id"] = turn_id
        if layer:
            entry["layer"] = layer
        self._append_jsonl(self.service_events_path(service), entry)
        self._append_jsonl(self.all_events_path, entry)
        return entry

    def append_access_decision(
        self,
        *,
        subject: str,
        capability: str,
        allowed: bool,
        reason: str,
        trace_id: str,
    ) -> dict[str, Any]:
        return self.append_event(
            service_id="ops",
            service="access-control",
            event="access.decision",
            trace_id=trace_id,
            payload={
                "subject": subject,
                "capability": capability,
                "decision": "allowed" if allowed else "denied",
                "reason": reason,
            },
            layer="ops",
        )

    def read_events(self, path: str | Path | None = None) -> list[dict[str, Any]]:
        target = Path(path) if path is not None else self.all_events_path
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def _append_jsonl(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))
            stream.write("\n")


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = (
                "[REDACTED]"
                if SENSITIVE_KEY_PATTERN.search(key_text)
                else redact(item)
            )
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_TEXT_PATTERN.sub(_redact_match, value)
    return value


def _redact_match(match: re.Match[str]) -> str:
    if match.group(1):
        return f"{match.group(1)}[REDACTED]"
    if match.group(3):
        return f"{match.group(3)}[REDACTED]"
    return "[REDACTED]"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    return safe.strip("-") or "unknown"
