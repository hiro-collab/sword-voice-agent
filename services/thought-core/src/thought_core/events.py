"""Event contract for thought-core output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "thought-core.event.v0"
CANONICAL_EVENT_ID_PATTERN = re.compile(r"^evt_[0-9a-f]{32}$")
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
        self._reserved_event_ids: set[str] = set()
        self._allocated_event_ids: set[str] = set()

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> ThoughtEvent:
        return self._emit_with_id(
            self._allocate_event_id(),
            event_type,
            data,
        )

    def reserve_event_id(self) -> str:
        event_id = self._allocate_event_id()
        self._reserved_event_ids.add(event_id)
        return event_id

    def _allocate_event_id(self) -> str:
        while True:
            event_id = f"evt_{uuid4().hex}"
            if event_id not in self._allocated_event_ids:
                self._allocated_event_ids.add(event_id)
                return event_id

    def emit_reserved(
        self,
        event_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> ThoughtEvent:
        if (
            not is_canonical_event_id(event_id)
            or event_id not in self._reserved_event_ids
        ):
            raise ValueError("reserved_event_id_invalid")
        self._reserved_event_ids.remove(event_id)
        return self._emit_with_id(event_id, event_type, data)

    def _emit_with_id(
        self,
        event_id: str,
        event_type: str,
        data: dict[str, Any] | None,
    ) -> ThoughtEvent:
        self._seq += 1
        return ThoughtEvent(
            type=event_type,
            event_id=event_id,
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


def is_canonical_event_id(value: object) -> bool:
    return type(value) is str and CANONICAL_EVENT_ID_PATTERN.fullmatch(value) is not None


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
