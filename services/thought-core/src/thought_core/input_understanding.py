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


@dataclass(frozen=True)
class _StatusTarget:
    target_class: str
    appliance_class: str = ""
    confidence: float = 0.0
    reason: str = ""


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

        if _is_audio_status_check(text):
            return InputFrame(
                kind="audio_check",
                target="audio_input",
                is_question=True,
                confidence=0.83,
                reason="audio_status_check",
                metadata={"normalized": normalized},
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

        environment_status = _environment_status_query_metadata(text)
        if environment_status is not None:
            confidence_hint = float(environment_status.get("confidence_hint") or 0.8)
            return InputFrame(
                kind="environment_status_query",
                target=str(environment_status["query_class"]),
                is_question=True,
                confidence=confidence_hint,
                reason=str(environment_status["reason"]),
                metadata={"normalized": normalized, **environment_status},
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

        motion_request = _motion_request_metadata(text)
        if motion_request is not None:
            return InputFrame(
                kind="motion_request",
                target=str(motion_request["kind"]),
                desired_state=str(motion_request["motion_intent"]),
                is_command=True,
                confidence=0.82,
                reason=str(motion_request["reason"]),
                metadata={"normalized": normalized, "motion_request": motion_request},
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


def _motion_request_metadata(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    if not normalized:
        return None

    base: dict[str, Any] = {
        "schema_version": "motion_stimulus.v0",
        "kind": "dance",
        "utterance_class": "explicit_motion_request",
        "motion_intent": "dance",
        "style": "neutral",
        "intensity": "medium",
        "duration_ms": 10000,
        "rhythm_hint": "none",
        "body_priority": ["upper_body", "arms", "head"],
        "cancelable": True,
        "home_action_allowed": False,
        "raw_prompt_included": False,
        "private_path_included": False,
        "device_route_included": False,
        "memory_candidate_policy": "separate_policy_required",
        "default_should_remember": False,
    }

    if _looks_like_motion_stop_request(normalized, lowered):
        base.update(
            {
                "kind": "cancel",
                "utterance_class": "explicit_motion_stop_request",
                "motion_intent": "stop",
                "duration_ms": 0,
                "body_priority": ["body_root", "spine", "head", "face"],
                "reason": "dance_motion_stop_request",
            }
        )
        return base

    if "踊" in normalized or "dance" in lowered:
        if any(marker in normalized for marker in ("音楽", "曲", "リズム", "ビート")):
            base["rhythm_hint"] = "music_sync_requested"
        base["reason"] = "dance_motion_request"
        return base

    happy_markers = ("うれしそう", "嬉しそう", "楽しそう", "喜んで", "はしゃいで")
    move_markers = ("動いて", "動きを", "動作", "身振り", "ジェスチャ")
    if any(marker in normalized for marker in happy_markers) and any(
        marker in normalized for marker in move_markers
    ):
        base.update(
            {
                "kind": "expression_motion",
                "motion_intent": "happy_motion",
                "style": "happy",
                "reason": "happy_expression_motion_request",
            }
        )
        return base

    return None


def _looks_like_motion_stop_request(normalized: str, lowered: str) -> bool:
    target_markers = (
        "踊",
        "ダンス",
        "dance",
        "dancing",
        "motion",
        "モーション",
        "動き",
        "動作",
    )
    stop_markers = (
        "止め",
        "停止",
        "やめ",
        "中止",
        "キャンセル",
        "ストップ",
        "stop",
        "cancel",
        "quit",
    )
    return any(marker in normalized for marker in target_markers) and any(
        marker in lowered for marker in stop_markers
    )


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


def _environment_status_query_metadata(text: str) -> dict[str, str] | None:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    if not normalized:
        return None
    structured_status = _structured_status_query_metadata(normalized, lowered)
    if structured_status is not None:
        return structured_status
    if _looks_like_home_action_command(text):
        return None

    question_markers = (
        "?",
        "？",
        "教えて",
        "確認",
        "見て",
        "どう",
        "どんな",
        "何が",
        "なにが",
        "使える",
        "できる",
        "ですか",
        "ますか",
    )
    if not any(marker in normalized for marker in question_markers):
        return None

    memory_markers = (
        "覚えて",
        "記憶",
        "前に",
        "前回",
        "以前",
        "さっき",
        "これまで",
        "踏まえて",
    )
    if any(marker in normalized for marker in memory_markers) and any(
        marker in normalized for marker in ("状況", "状態", "文脈", "作業", "続き")
    ):
        return {
            "query_class": "memory_grounded_status",
            "reason": "memory_dependent_status_question",
        }

    home_control_markers = (
        "home assistant",
        "ホームアシスタント",
        "home control",
        "ホームコントロール",
        "家電",
    )
    if any(marker in lowered for marker in home_control_markers) or any(
        marker in normalized for marker in home_control_markers
    ):
        if any(marker in normalized for marker in ("状態", "どうな", "ついて", "消えて")):
            return {
                "query_class": "appliance_state",
                "reason": "appliance_state_status_question",
            }
        return {
            "query_class": "home_control_availability",
            "reason": "home_control_availability_question",
        }

    environment_markers = (
        "今の状況",
        "現在の状況",
        "今の状態",
        "現在の状態",
        "周り",
        "まわり",
        "環境",
        "見えて",
        "見える",
        "状況",
        "ステータス",
        "状態",
    )
    if any(marker in normalized for marker in environment_markers):
        return {
            "query_class": "current_environment_status",
            "reason": "current_environment_status_question",
        }
    return None


def _structured_status_query_metadata(
    normalized: str,
    lowered: str,
) -> dict[str, str] | None:
    target = _extract_status_target(normalized, lowered)
    if target is None:
        return None

    status_score = _status_question_score(normalized)
    if target.target_class == "memory":
        if status_score <= 0:
            return None
        return _status_metadata(
            query_class="memory_grounded_status",
            reason="memory_dependent_status_question",
            target=target,
            status_score=status_score,
        )

    if target.target_class == "home_control":
        if status_score <= 0:
            return None
        query_class = "appliance_state" if _mentions_state_surface(normalized) else "home_control_availability"
        reason = (
            "appliance_state_status_question"
            if query_class == "appliance_state"
            else "home_control_availability_question"
        )
        return _status_metadata(
            query_class=query_class,
            reason=reason,
            target=target,
            status_score=status_score,
        )

    if target.target_class == "environment":
        if status_score <= 0:
            return None
        return _status_metadata(
            query_class="current_environment_status",
            reason="current_environment_status_question",
            target=target,
            status_score=status_score,
        )

    if target.target_class != "appliance":
        return None
    if _looks_like_action_only_request(normalized) and not _has_question_or_check_cue(
        normalized
    ):
        return None
    if status_score <= 0:
        return None
    return _status_metadata(
        query_class="appliance_state",
        reason=f"{target.appliance_class}_state_status_question",
        target=target,
        status_score=status_score,
    )


def _extract_status_target(normalized: str, lowered: str) -> _StatusTarget | None:
    appliance_markers = {
        "aircon": (
            "エアコン",
            "クーラー",
            "冷房",
            "暖房",
            "空調",
            "aircon",
            "a/c",
        ),
        "fan": ("扇風機", "ファン", "fan"),
        "door": ("中扉", "ドア", "カーテン", "cover", "door"),
        "vacuum": ("掃除機", "ロボット掃除機", "vacuum"),
    }
    for appliance, markers in appliance_markers.items():
        if any(marker in normalized for marker in markers) or any(
            marker in lowered for marker in markers
        ):
            return _StatusTarget(
                target_class="appliance",
                appliance_class=appliance,
                confidence=0.9,
                reason=f"{appliance}_target_detected",
            )
    if any(
        marker in lowered
        for marker in ("home assistant", "home control")
    ) or any(
        marker in normalized
        for marker in ("ホームアシスタント", "ホームコントロール", "家電")
    ):
        return _StatusTarget(
            target_class="home_control",
            confidence=0.82,
            reason="home_control_target_detected",
        )
    if any(
        marker in normalized
        for marker in (
            "今の状況",
            "現在の状況",
            "今の状態",
            "現在の状態",
            "周り",
            "まわり",
            "環境",
            "見えて",
            "見える",
            "状況",
            "ステータス",
            "状態",
        )
    ):
        if any(
            marker in normalized
            for marker in (
                "覚えて",
                "記憶",
                "前に",
                "前回",
                "以前",
                "さっき",
                "これまで",
                "踏まえて",
            )
        ):
            return _StatusTarget(
                target_class="memory",
                confidence=0.78,
                reason="memory_context_target_detected",
            )
        return _StatusTarget(
            target_class="environment",
            confidence=0.78,
            reason="environment_target_detected",
        )
    return None


def _status_question_score(normalized: str) -> int:
    status_markers = (
        "?",
        "？",
        "か",
        "確認",
        "見て",
        "見てもら",
        "調べ",
        "状態",
        "どう",
        "どの",
        "分かる",
        "わかる",
        "感じ",
        "ついてる",
        "ついている",
        "点いてる",
        "付いてる",
        "入ってる",
        "入っている",
        "オン",
        "消えてる",
        "消えている",
        "オフ",
        "切れてる",
        "動いてる",
        "動いている",
        "動作",
        "稼働",
        "運転",
        "止まって",
        "止まった",
    )
    return sum(1 for marker in status_markers if marker in normalized)


def _mentions_state_surface(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in (
            "状態",
            "どうな",
            "ついて",
            "消えて",
            "動いて",
            "分かる",
            "わかる",
        )
    )


def _looks_like_action_only_request(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in (
            "つけて",
            "点けて",
            "付けて",
            "オンにして",
            "消して",
            "オフにして",
            "切って",
            "開けて",
            "閉めて",
            "止めて",
            "戻して",
        )
    )


def _has_question_or_check_cue(normalized: str) -> bool:
    return any(
        marker in normalized
        for marker in (
            "?",
            "？",
            "か",
            "確認",
            "状態",
            "どう",
            "ついてる",
            "ついている",
            "消えてる",
            "消えている",
            "入ってる",
            "入っている",
        )
    )


def _status_metadata(
    *,
    query_class: str,
    reason: str,
    target: _StatusTarget,
    status_score: int,
) -> dict[str, str]:
    confidence = min(0.92, max(0.55, target.confidence + (status_score * 0.03)))
    ambiguity_class = (
        "clear_status_query" if status_score >= 2 else "low_confidence_status_query"
    )
    return {
        "query_class": query_class,
        "reason": reason,
        "intent_class": "status_query",
        "target_class": target.target_class,
        "appliance_class": target.appliance_class,
        "confidence_hint": f"{confidence:.2f}",
        "ambiguity_class": ambiguity_class,
        "classification_schema": "thought_core_intent_classification.v0",
        "classifier_mode": "local_deterministic_guarded",
        "llm_assist_used": "false",
        "llm_assist_status": "not_enabled_in_local_input_understanding",
        "safety_guardrail": "status_query_never_executes_home_action",
    }


def _is_audio_status_check(text: str) -> bool:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    if not normalized:
        return False
    audio_markers = (
        "音声",
        "マイク",
        "声",
        "聞こえ",
        "聞き取",
        "stt",
        "speech",
        "audio",
    )
    status_markers = (
        "聞こえた",
        "聞こえる",
        "聞き取",
        "認識",
        "受け取",
        "届いて",
        "入力",
        "テスト",
        "確認",
    )
    question_markers = (
        "確認して",
        "確認してください",
        "確認",
        "ですか",
        "ますか",
        "かな",
        "か",
        "?",
        "？",
    )
    return (
        any(marker in normalized or marker in lowered for marker in audio_markers)
        and any(marker in normalized or marker in lowered for marker in status_markers)
        and any(marker in normalized or marker in lowered for marker in question_markers)
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
