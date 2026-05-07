"""Event contract for thought-core output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "thought-core.event.v0"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "credential",
)


@dataclass(frozen=True)
class ThoughtEvent:
    type: str
    turn_id: str
    session_id: str
    seq: int
    data: dict[str, Any]
    source: str = "thought-core"
    schema_version: str = SCHEMA_VERSION
    event_id: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return redact_secrets(
            {
                "schema_version": self.schema_version,
                "event_id": self.event_id or f"evt_{uuid4().hex}",
                "turn_id": self.turn_id,
                "session_id": self.session_id,
                "seq": self.seq,
                "timestamp": self.timestamp or utc_timestamp(),
                "source": self.source,
                "type": self.type,
                "data": self.data,
            }
        )


class EventFactory:
    def __init__(self, turn_id: str, session_id: str, source: str = "thought-core") -> None:
        self.turn_id = turn_id
        self.session_id = session_id
        self.source = source
        self._seq = 0
        self._tool_call_seq = 0

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> ThoughtEvent:
        self._seq += 1
        return ThoughtEvent(
            type=event_type,
            event_id=f"evt_{uuid4().hex}",
            turn_id=self.turn_id,
            session_id=self.session_id,
            seq=self._seq,
            timestamp=utc_timestamp(),
            source=self.source,
            data=data or {},
        )

    def next_tool_call_id(self) -> str:
        self._tool_call_seq += 1
        return f"tc_{self._tool_call_seq:04d}"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)

