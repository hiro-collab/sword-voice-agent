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

