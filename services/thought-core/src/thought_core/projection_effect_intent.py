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
_EFFECT_CONCEPTS = {
    "fire": ("炎", "火炎", "ファイア"),
    "thunderBall": ("雷", "稲妻", "サンダー"),
}
_EFFECT_MARKERS = tuple(
    marker for markers in _EFFECT_CONCEPTS.values() for marker in markers
)
_QUESTION_MARKERS = ("?", "？", "ですか", "ますか", "かな", "でしょう")
_NEGATION_MARKERS = ("ないで", "なくて", "しない", "やめておいて")
_CAPABILITY_MARKERS = ("出せますか", "見せられますか", "表示できますか", "召喚できますか")
_DISALLOWED_MANIPULATION_MARKERS = (
    "大きく",
    "小さく",
    "強く",
    "弱く",
    "位置",
    "色",
    "速",
    "長く",
    "短く",
    "から",
    "あと",
    "そして",
    "止めて",
    "リセット",
    "変えて",
    "動かして",
)
_CONTEXT_FILLERS = (
    "ねえ",
    "えっと",
    "あの",
    "すみません",
    "どうか",
    "ちょっと",
    "お願い",
    "じゃあ",
    "では",
    "それなら",
    "今度は",
)
_CONTEXT_SEPARATORS = "、,。.!！"
MAX_CONTEXT_FILLERS = 4
_REQUEST_SUFFIXES = (
    "を出して",
    "を出してください",
    "を出して下さい",
    "を出してほしい",
    "を出して欲しい",
    "を出してもらえますか",
    "を出していただけますか",
    "を見せて",
    "を見せてください",
    "を見せて下さい",
    "を見せてほしい",
    "を見せて欲しい",
    "を見せてもらえますか",
    "を見せていただけますか",
    "を表示して",
    "を表示してください",
    "を表示して下さい",
    "を表示してほしい",
    "を表示して欲しい",
    "を表示してもらえますか",
    "を表示していただけますか",
    "を召喚して",
    "を召喚してください",
    "を召喚して下さい",
    "を召喚してほしい",
    "を召喚して欲しい",
    "を召喚してもらえますか",
    "を召喚していただけますか",
    "を出してくれますか",
    "を出してくれる",
    "を見せてくれますか",
    "を見せてくれる",
    "を表示してくれますか",
    "を召喚してくれますか",
    "が見たい",
    "を見たい",
    "を見てみたい",
    "お願い",
    "をお願いします",
    "をお願いできますか",
)


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

    question_terminated = compact.endswith(("?", "？"))
    command = compact.rstrip("。.!！?？")
    effect_id = _detect_start_effect(command)
    if effect_id is not None:
        return ProjectionEffectIntentDecision(
            status="accepted",
            action="start",
            effect_id=effect_id,
            reason="bounded_projection_effect_start",
        )
    control_action = None if question_terminated else _CONTROL_PHRASES.get(command)
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


def _detect_start_effect(command: str) -> str | None:
    """Accept one unparameterized effect wish; everything else fails closed."""
    if any(marker in command for marker in _NEGATION_MARKERS):
        return None
    if any(marker in command for marker in _CAPABILITY_MARKERS):
        return None
    if any(marker in command for marker in _DISALLOWED_MANIPULATION_MARKERS):
        return None

    request = _strip_context_fillers(command)
    if request is None:
        return None
    matched_effects = [
        effect_id
        for effect_id, markers in _EFFECT_CONCEPTS.items()
        if any(marker in request for marker in markers)
    ]
    if len(matched_effects) != 1:
        return None

    effect_id = matched_effects[0]
    for concept in _EFFECT_CONCEPTS[effect_id]:
        for suffix in _REQUEST_SUFFIXES:
            if request == f"{concept}{suffix}":
                return effect_id
    return None


def _strip_context_fillers(command: str) -> str | None:
    """Remove at most the fixed number of leading discourse fillers."""
    candidate = command.lstrip(_CONTEXT_SEPARATORS)
    filler_count = 0
    while candidate:
        matched = False
        for filler in _CONTEXT_FILLERS:
            if candidate.startswith(filler):
                if filler_count >= MAX_CONTEXT_FILLERS:
                    return None
                filler_count += 1
                candidate = candidate[len(filler) :].lstrip(_CONTEXT_SEPARATORS)
                matched = True
                break
        if not matched:
            break
    return candidate
