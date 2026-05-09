"""Input understanding boundary for Thought Core turns.

This boundary classifies a user turn before the action loop decides whether to
answer, learn, execute, or continue an existing review. The local adapter is a
deterministic fallback; an LLM adapter can replace it without changing the
ThoughtLoop branch logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .schema import TurnInput
from .tools import detect_home_action_intent


INPUT_UNDERSTANDING_BOUNDARY = "thought-core.input_understanding.v0"


@dataclass(frozen=True)
class InputFrame:
    kind: str
    target: str = ""
    asserted_state: str = ""
    desired_state: str = ""
    is_question: bool = False
    is_feedback: bool = False
    is_command: bool = False
    action_id: str = ""
    action_target: str = ""
    action_expected_state: str = ""
    confidence: float = 0.0
    reason: str = ""
    continued_as_command: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": INPUT_UNDERSTANDING_BOUNDARY,
            "kind": self.kind,
            "target": self.target,
            "asserted_state": self.asserted_state,
            "desired_state": self.desired_state,
            "is_question": self.is_question,
            "is_feedback": self.is_feedback,
            "is_command": self.is_command,
            "action_id": self.action_id,
            "action_target": self.action_target,
            "action_expected_state": self.action_expected_state,
            "confidence": self.confidence,
            "reason": self.reason,
            "continued_as_command": self.continued_as_command,
            "metadata": dict(self.metadata),
        }


class InputUnderstanding(Protocol):
    adapter_kind: str
    provider: str
    model: str

    def understand(
        self,
        turn: TurnInput,
        *,
        pending_state_query: dict[str, Any] | None = None,
        pending_action_review: dict[str, Any] | None = None,
    ) -> InputFrame:
        ...


class LocalInputUnderstanding:
    adapter_kind = "local_intent_frame"
    provider = "thought-core"
    model = "local-input-understanding-v0"

    def understand(
        self,
        turn: TurnInput,
        *,
        pending_state_query: dict[str, Any] | None = None,
        pending_action_review: dict[str, Any] | None = None,
    ) -> InputFrame:
        text = turn.text
        normalized = _normalize_text(text)
        action_intent = detect_home_action_intent(text)
        has_action = action_intent is not None and _looks_like_home_action_command(text)
        action_fields = _action_fields(action_intent) if action_intent is not None else {}

        if _is_room_light_state_question(text):
            return InputFrame(
                kind="state_query",
                target="room_light",
                is_question=True,
                confidence=0.86,
                reason="room_light_state_question",
                metadata={"normalized": normalized},
                **action_fields,
            )

        pending_feedback_label = _room_light_feedback_label(
            text,
            pending=pending_state_query or _pending_state_from_action_review(
                pending_action_review
            ),
        )
        if pending_feedback_label:
            return InputFrame(
                kind="state_feedback",
                target="room_light",
                asserted_state=pending_feedback_label,
                is_feedback=True,
                is_command=has_action,
                confidence=0.82,
                reason="feedback_for_pending_room_light_state",
                continued_as_command=has_action,
                metadata={"normalized": normalized, "pending": True},
                **action_fields,
            )

        direct_feedback_label = _direct_room_light_feedback_label(text)
        if direct_feedback_label:
            return InputFrame(
                kind="state_feedback",
                target="room_light",
                asserted_state=direct_feedback_label,
                is_feedback=True,
                is_command=has_action,
                confidence=0.78,
                reason="direct_room_light_state_report",
                continued_as_command=has_action,
                metadata={"normalized": normalized, "pending": False},
                **action_fields,
            )

        if action_intent is not None:
            return InputFrame(
                kind="home_command",
                target=str(action_intent.target or ""),
                desired_state=str(action_intent.expected_state or ""),
                is_command=True,
                confidence=0.84,
                reason="home_action_intent",
                metadata={"normalized": normalized},
                **action_fields,
            )

        return InputFrame(
            kind="general",
            confidence=0.55,
            reason="no_structured_home_or_state_intent",
            metadata={"normalized": normalized},
        )


def build_input_understanding_from_env() -> InputUnderstanding:
    adapter = os.environ.get("THOUGHT_CORE_INPUT_UNDERSTANDING_ADAPTER", "").strip()
    if adapter and adapter.lower() not in {"local", "local_intent_frame"}:
        # Keep the boundary explicit even before an LLM adapter is wired in.
        return LocalInputUnderstanding()
    return LocalInputUnderstanding()


def describe_input_understanding(adapter: InputUnderstanding) -> dict[str, str]:
    return {
        "boundary": INPUT_UNDERSTANDING_BOUNDARY,
        "adapter_kind": getattr(adapter, "adapter_kind", "unknown"),
        "provider": getattr(adapter, "provider", "unknown"),
        "model": getattr(adapter, "model", "unknown"),
    }


def _action_fields(action_intent: Any) -> dict[str, Any]:
    return {
        "action_id": str(getattr(action_intent, "action_id", "") or ""),
        "action_target": str(getattr(action_intent, "target", "") or ""),
        "action_expected_state": str(
            getattr(action_intent, "expected_state", "") or ""
        ),
    }


def _pending_state_from_action_review(
    pending_action_review: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(pending_action_review, dict):
        return None
    action = pending_action_review.get("action")
    if not isinstance(action, dict):
        action = {}
    expected_state = str(action.get("expected_state") or "").lower()
    if expected_state not in {"on", "off"}:
        return None
    return {
        "state_query_id": "room_light",
        "target": "room_light",
        "predicted_state": expected_state,
        "expected_state": expected_state,
        "action": action,
    }


def _room_light_feedback_label(
    text: str,
    *,
    pending: dict[str, Any] | None = None,
) -> str:
    if _is_room_light_state_question(text):
        return ""
    normalized = _normalize_text(text)
    explicit = _explicit_room_light_state_label(normalized)
    if explicit:
        return explicit
    if not pending:
        return ""
    predicted = str(pending.get("predicted_state") or "").lower()
    yes_markers = (
        "はい",
        "うん",
        "そう",
        "その通り",
        "合ってる",
        "あってる",
        "正しい",
        "せや",
        "yes",
        "ok",
    )
    no_markers = (
        "いいえ",
        "いや",
        "違う",
        "ちがう",
        "逆",
        "ちがいます",
        "違います",
        "no",
    )
    if any(marker in normalized for marker in yes_markers):
        return predicted if predicted in {"on", "off", "daylight"} else "on"
    if any(marker in normalized for marker in no_markers):
        if predicted == "on":
            return "off"
        if predicted == "off":
            return "on"
        return "unknown"
    return ""


def _direct_room_light_feedback_label(text: str) -> str:
    if _is_room_light_state_question(text):
        return ""
    normalized = _normalize_text(text)
    label = _explicit_room_light_state_label(normalized)
    if not label:
        return ""
    if not _mentions_room_light(normalized):
        return ""
    question_markers = ("?", "？", "かな", "教えて", "確認して", "どう")
    if any(marker in normalized for marker in question_markers):
        return ""
    command_markers = (
        "消して",
        "消す",
        "消せ",
        "消しといて",
        "消灯して",
        "オフにして",
        "切って",
        "つけて",
        "点けて",
        "付けて",
        "つける",
        "オンにして",
        "入れて",
    )
    state_cues = (
        "今",
        "現在",
        "実際",
        "状態",
        "もう",
        "まだ",
        "です",
        "だよ",
        "だね",
        "なって",
        "いる",
        "います",
        "中",
    )
    if any(marker in normalized for marker in command_markers) and not any(
        cue in normalized for cue in state_cues
    ):
        return ""
    return label


def _is_room_light_state_question(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or not _mentions_room_light(normalized):
        return False
    if _looks_like_home_action_command(text):
        return False
    question_markers = (
        "?",
        "？",
        "でしょう",
        "ですか",
        "ますか",
        "教えて",
        "確認して",
        "確認したい",
        "見て",
        "どう",
    )
    if any(marker in normalized for marker in question_markers):
        return True
    state_question_markers = (
        "ついてるか",
        "ついているか",
        "点いてるか",
        "点いているか",
        "付いてるか",
        "付いているか",
        "ついてないか",
        "点いてないか",
        "付いてないか",
        "消えてるか",
        "消えているか",
        "消えてないか",
        "点灯か",
        "消灯か",
        "オンか",
        "オフか",
        "明るいか",
        "暗いか",
    )
    if any(marker in normalized for marker in state_question_markers):
        return True
    return normalized.endswith(("か", "かな", "かね", "かい")) and bool(
        _explicit_room_light_state_label(normalized) or "状態" in normalized
    )


def _explicit_room_light_state_label(normalized: str) -> str:
    if any(marker in normalized for marker in ("日光", "外光", "太陽光", "昼光")):
        return "daylight"
    if any(marker in normalized for marker in ("わからない", "分からない", "不明", "不確か")):
        return "unknown"
    off_markers = (
        "ついてない",
        "点いてない",
        "付いてない",
        "消えてる",
        "消えています",
        "消えてます",
        "消灯状態",
        "消灯中",
        "消灯してる",
        "消灯しています",
        "暗い",
        "オフです",
        "offです",
        "offだ",
        "切れてる",
    )
    if any(marker in normalized for marker in off_markers):
        return "off"
    on_markers = (
        "ついてる",
        "点いてる",
        "付いてる",
        "ついています",
        "点いてます",
        "ついてます",
        "点灯状態",
        "点灯中",
        "点灯してる",
        "点灯しています",
        "明るい",
        "オンです",
        "onです",
        "onだ",
    )
    if any(marker in normalized for marker in on_markers):
        return "on"
    return ""


def _mentions_room_light(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in ("電気", "照明", "ライト", "明かり", "明り", "部屋", "リビング")
    )


def _looks_like_home_action_command(text: str) -> bool:
    normalized = _normalize_text(text)
    command_markers = (
        "して",
        "お願い",
        "おねがい",
        "つけて",
        "点けて",
        "付けて",
        "つける",
        "点ける",
        "付ける",
        "オンに",
        "入れて",
        "入れる",
        "消して",
        "消す",
        "消せ",
        "オフに",
        "切って",
        "切る",
        "開けて",
        "開ける",
        "閉めて",
        "閉める",
        "止めて",
        "止める",
        "戻して",
        "戻す",
        "動かして",
        "動かす",
        "一時停止",
        "起動",
    )
    return any(marker in normalized for marker in command_markers)


def _normalize_text(text: str) -> str:
    return text.replace(" ", "").replace("　", "").lower()
