"""Bounded, privacy-safe conversational intents for Projection Effects."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal


ProjectionEffectIntentStatus = Literal[
    "accepted",
    "clarification_required",
    "no_match",
]
ProjectionEffectAction = Literal["start", "stop", "reset"]

_START_PHRASES = {
    "炎を出して": "fire",
    "炎を見せて": "fire",
    "雷を出して": "thunderBall",
    "雷を見せて": "thunderBall",
}
_CONTROL_PHRASES: dict[str, ProjectionEffectAction] = {
    "止めて": "stop",
    "リセットして": "reset",
}
_BARE_MULTI_CONTROL_COMMANDS = frozenset(
    {
        "止めて、リセットして",
        "リセットして、止めて",
        "止めて、止めて",
        "リセットして、リセットして",
    }
)
_EFFECT_MARKERS = ("炎", "雷")
_QUESTION_MARKERS = ("?", "？", "ですか", "ますか", "かな", "でしょう")
_NEGATION_MARKERS = ("ないで", "なくて", "しない", "やめておいて")


@dataclass(frozen=True)
class ProjectionEffectIntentDecision:
    status: ProjectionEffectIntentStatus
    action: ProjectionEffectAction | None = None
    effect_id: str | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    def event_payload(self) -> dict[str, object]:
        if not self.accepted or self.action is None:
            raise ValueError("projection_effect_intent_not_accepted")
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "action": self.action,
        }
        if self.action == "start":
            if self.effect_id not in {"fire", "thunderBall"}:
                raise ValueError("projection_effect_start_effect_invalid")
            payload["effectId"] = self.effect_id
        return payload


def detect_projection_effect_intent(text: str) -> ProjectionEffectIntentDecision:
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    compact = "".join(normalized.split())
    if not compact:
        return ProjectionEffectIntentDecision(status="no_match")

    command = compact.rstrip("。.!！")
    effect_id = _START_PHRASES.get(command)
    if effect_id is not None:
        return ProjectionEffectIntentDecision(
            status="accepted",
            action="start",
            effect_id=effect_id,
            reason="bounded_projection_effect_start",
        )
    control_action = _CONTROL_PHRASES.get(command)
    if control_action is not None:
        return ProjectionEffectIntentDecision(
            status="accepted",
            action=control_action,
            reason=f"bounded_projection_effect_{control_action}",
        )

    relevant = (
        _mentions_effect(compact)
        or command in _BARE_MULTI_CONTROL_COMMANDS
        or _is_bare_control_question(compact)
    )
    if relevant and any(marker in compact for marker in _QUESTION_MARKERS):
        return ProjectionEffectIntentDecision(
            status="clarification_required",
            reason="projection_effect_question_not_action",
        )
    if relevant and any(marker in compact for marker in _NEGATION_MARKERS):
        return ProjectionEffectIntentDecision(
            status="clarification_required",
            reason="projection_effect_negative_request",
        )

    if relevant:
        return ProjectionEffectIntentDecision(
            status="clarification_required",
            reason="projection_effect_request_not_bounded",
        )
    return ProjectionEffectIntentDecision(status="no_match")


def _mentions_effect(text: str) -> bool:
    return any(marker in text for marker in _EFFECT_MARKERS)


def _is_bare_control_question(text: str) -> bool:
    return text.endswith("?") and text[:-1] in _CONTROL_PHRASES
