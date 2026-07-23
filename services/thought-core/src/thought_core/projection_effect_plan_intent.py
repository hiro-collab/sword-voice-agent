"""Bounded Japanese compiler for Projection PerformancePlan V1."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from .projection_effect_plan import (
    MAX_DURATION_MS,
    MIN_DURATION_MS,
    ProjectionPerformancePlan,
    ProjectionPerformancePlanValidationError,
    validate_projection_performance_plan,
)


ProjectionEffectPlanIntentStatus = Literal[
    "accepted",
    "clarification_required",
    "no_match",
]
ProjectionEffectPlanIntentReason = Literal[
    "projection_plan_needs_clarification",
]

MAX_UTTERANCE_CHARS = 1_024
MAX_CONTEXT_FILLERS = 4
MAX_CLAUSES = 16
DEFAULT_POSITION = (0.0, 0.0)
DEFAULT_STRENGTH = 0.6
DEFAULT_DURATION_MS = 3_000

_EFFECT_MARKERS: dict[str, tuple[str, ...]] = {
    "fire": ("ファイア", "火炎", "炎"),
    "thunderBall": ("サンダー", "稲妻", "雷"),
}
_POSITION_ALIASES = (
    ("中央より少し上", (0.0, 0.3)),
    ("中央の少し上", (0.0, 0.3)),
    ("真ん中より少し上", (0.0, 0.3)),
    ("真ん中の少し上", (0.0, 0.3)),
    ("右上あたり", (0.65, 0.55)),
    ("左上あたり", (-0.65, 0.55)),
    ("右下あたり", (0.65, -0.55)),
    ("左下あたり", (-0.65, -0.55)),
    ("中央あたり", (0.0, 0.0)),
    ("真ん中あたり", (0.0, 0.0)),
    ("少し上", (0.0, 0.3)),
    ("右上", (0.65, 0.55)),
    ("左上", (-0.65, 0.55)),
    ("右下", (0.65, -0.55)),
    ("左下", (-0.65, -0.55)),
    ("右側", (0.65, 0.0)),
    ("左側", (-0.65, 0.0)),
    ("上側", (0.0, 0.55)),
    ("下側", (0.0, -0.55)),
    ("中央", (0.0, 0.0)),
    ("真ん中", (0.0, 0.0)),
    ("右", (0.65, 0.0)),
    ("左", (-0.65, 0.0)),
    ("上", (0.0, 0.55)),
    ("下", (0.0, -0.55)),
)
_STRENGTH_ALIASES = (
    ("かなり弱め", 0.25),
    ("かなり小さめ", 0.25),
    ("かなり強め", 0.9),
    ("かなり大きめ", 0.9),
    ("中くらい", 0.6),
    ("小さめ", 0.4),
    ("弱め", 0.4),
    ("強め", 0.8),
    ("大きめ", 0.8),
    ("普通", 0.6),
)
_CONTEXT_FILLERS = (
    "すみません",
    "えっと",
    "あの",
    "ねえ",
    "じゃあ",
    "では",
    "ちょっと",
)
_AFFIRMATIVE_CUES = (
    "表示してもらえますか",
    "召喚してもらえますか",
    "出してもらえますか",
    "見せてもらえますか",
    "表示してくれますか",
    "召喚してくれますか",
    "出してくれますか",
    "見せてくれますか",
    "移動させながら",
    "動かしながら",
    "表示してください",
    "召喚してください",
    "出してください",
    "見せてください",
    "表示してほしい",
    "召喚してほしい",
    "出してほしい",
    "見せてほしい",
    "表示して",
    "召喚して",
    "出して",
    "見せて",
    "移動して",
    "動かして",
    "お願いします",
    "お願い",
)
_POLITE_REQUEST_CUES = frozenset(
    cue for cue in _AFFIRMATIVE_CUES if cue.endswith("ますか")
)
_MOVEMENT_CUES = (
    "移動させながら",
    "動かしながら",
    "移動して",
    "動かして",
)
_CAPABILITY_MARKERS = (
    "どうやって",
    "できますか",
    "出せますか",
    "出せる",
    "見せられますか",
    "見せられる",
    "表示できますか",
    "表示できる",
    "召喚できますか",
    "召喚できる",
    "可能ですか",
    "できる",
    "出すの",
)
_TOPIC_OR_MENTION_MARKERS = (
    "について",
    "という",
    "位置は",
    "と言って",
    "って言って",
    "話して",
    "説明して",
)
_NEGATION_MARKERS = (
    "ないで",
    "しない",
    "なくて",
    "出さず",
    "出さない",
    "見せない",
    "表示しない",
    "召喚しない",
    "やめておいて",
)
_CONDITIONAL_MARKERS = (
    "もし",
    "できたら",
    "出せたら",
    "見せられたら",
    "なら",
    "れば",
    "かもしれ",
)
_UNSUPPORTED_MARKERS = (
    "色",
    "赤く",
    "青く",
    "明るく",
    "暗く",
    "速く",
    "遅く",
    "ゆっくり",
    "速度",
    "方向",
    "角度",
    "回転",
    "奥",
    "手前",
    "深さ",
    "右手",
    "左手",
    "手の",
    "頭",
    "足元",
    "身体",
    "追従",
    "アンカー",
    "同時",
    "混ぜ",
    "組み合わせ",
    "その後",
    "次に",
    "順番",
    "連続",
    "止めて",
    "リセット",
    "緊急停止",
    "更新",
    "変えて",
    "弱めて",
    "強めて",
    "大きくして",
    "小さくして",
    "さっき",
    "もう一度",
    "同じ感じ",
    "繰り返",
    "前の",
    "http",
    "www.",
    "url",
    "shader",
    "glsl",
    "json",
    "コード",
    "プロンプト",
    "ナレッジ",
    "知識ベース",
)
_UNKNOWN_DURATION_MARKERS = (
    "分",
    "時間",
    "ミリ秒",
    "ms",
    "msec",
    "nan",
    "inf",
    "∞",
    "無限",
)
_DURATION_PATTERN = re.compile(r"(?<![0-9.])([0-9]{1,2}(?:\.[0-9])?)秒(?:間)?")
_ACTION_PATTERN = re.compile(
    "|".join(re.escape(cue) for cue in sorted(_AFFIRMATIVE_CUES, key=len, reverse=True))
)
_GRAMMAR_GAP_PATTERN = re.compile(
    r"^(?:(?:[、。.!！?？])|(?:を経由して)|(?:経由して)|"
    r"(?:を)|(?:に)|(?:で)|(?:だけ)|(?:の)|(?:より)|(?:から)|(?:へ))*$"
)


@dataclass(frozen=True)
class ProjectionEffectPlanIntentDecision:
    status: ProjectionEffectPlanIntentStatus
    plan: ProjectionPerformancePlan | None = None
    reason: ProjectionEffectPlanIntentReason | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted" and self.plan is not None


@dataclass(frozen=True)
class _PositionMention:
    start: int
    end: int
    x: float
    y: float


@dataclass(frozen=True)
class _EffectMention:
    start: int
    end: int
    effect_id: Literal["fire", "thunderBall"]


@dataclass(frozen=True)
class _StrengthMention:
    start: int
    end: int
    value: float


@dataclass(frozen=True)
class _DurationMention:
    start: int
    end: int
    milliseconds: int


@dataclass(frozen=True)
class _ActionMention:
    start: int
    end: int
    cue: str

    @property
    def polite_request(self) -> bool:
        return self.cue in _POLITE_REQUEST_CUES


def compile_projection_effect_plan_intent(
    utterance: object,
    *,
    plan_id: object,
    session_id: object,
    revision: object,
    seed: object,
) -> ProjectionEffectPlanIntentDecision:
    """Compile one bounded planned-cast wish without side effects."""

    normalized = _bounded_normalize(utterance)
    if normalized is None:
        return _no_match()
    if normalized == "__REJECT__":
        return _clarification()

    normalized = _strip_context_fillers(normalized)
    if normalized is None:
        return _clarification()
    if not normalized:
        return _no_match()

    effect_mentions = _effect_mentions(normalized)
    if not effect_mentions:
        return _no_match()
    if len(effect_mentions) != 1:
        return _clarification()
    effect_mention = effect_mentions[0]
    effect_id = effect_mention.effect_id

    if any(marker in normalized for marker in _TOPIC_OR_MENTION_MARKERS):
        return _no_match()
    if any(marker in normalized for marker in _NEGATION_MARKERS):
        return _clarification()
    if any(marker in normalized for marker in _CONDITIONAL_MARKERS):
        return _clarification()
    if any(marker in normalized.casefold() for marker in _UNSUPPORTED_MARKERS):
        return _clarification()
    if _clause_count(normalized) > MAX_CLAUSES:
        return _clarification()

    action_mentions = tuple(
        _ActionMention(match.start(), match.end(), match.group(0))
        for match in _ACTION_PATTERN.finditer(normalized)
    )
    if len(action_mentions) > 1:
        return _clarification()
    action_mention = action_mentions[0] if action_mentions else None
    if any(marker in normalized for marker in _CAPABILITY_MARKERS):
        return _no_match()
    if ("?" in normalized or "？" in normalized) and not (
        action_mention is not None and action_mention.polite_request
    ):
        return _no_match()
    movement = (
        action_mention is not None and action_mention.cue in _MOVEMENT_CUES
    )

    positions = _position_mentions(normalized)
    if positions is None:
        return _clarification()
    strength_mention = _strength_mention(normalized)
    if strength_mention is False:
        return _clarification()
    duration_mention = _duration_mention(normalized)
    if duration_mention is False:
        return _clarification()

    explicit_position = len(positions) > 0
    explicit_strength = strength_mention is not None
    explicit_duration = duration_mention is not None
    has_planned_dimension = (
        explicit_position or explicit_strength or explicit_duration or movement
    )
    if not has_planned_dimension:
        return _no_match()

    if not _whole_input_consumed(
        normalized,
        effect_mention=effect_mention,
        positions=positions,
        strength_mention=strength_mention,
        duration_mention=duration_mention,
        action_mention=action_mention,
    ):
        return _clarification()

    if action_mention is None:
        if not _is_compact_planned_cast(
            normalized,
            effect_mention=effect_mention,
            positions=positions,
            strength_mention=strength_mention,
            duration_mention=duration_mention,
        ):
            return _no_match()

    if movement:
        if duration_mention is None or not 2 <= len(positions) <= 4:
            return _clarification()
        if not _valid_movement_path(normalized, positions):
            return _clarification()
    elif len(positions) > 1:
        return _clarification()

    resolved_strength = (
        strength_mention.value
        if strength_mention is not None
        else DEFAULT_STRENGTH
    )
    resolved_duration_ms = (
        duration_mention.milliseconds
        if duration_mention is not None
        else DEFAULT_DURATION_MS
    )
    if movement:
        resolved_positions = tuple((item.x, item.y) for item in positions)
    else:
        resolved_positions = (
            ((positions[0].x, positions[0].y),)
            if positions
            else (DEFAULT_POSITION,)
        )
    keyframes = _keyframes(
        resolved_positions,
        strength=resolved_strength,
        duration_ms=resolved_duration_ms,
    )
    first_x, first_y = resolved_positions[0]

    candidate = {
        "schemaVersion": 1,
        "planId": plan_id,
        "sessionId": session_id,
        "revision": revision,
        "action": "start",
        "effectId": effect_id,
        "position": {"x": first_x, "y": first_y},
        "strength": resolved_strength,
        "durationMs": resolved_duration_ms,
        "seed": seed,
        "keyframes": keyframes,
    }
    try:
        plan = validate_projection_performance_plan(candidate)
    except ProjectionPerformancePlanValidationError:
        return _clarification()
    return ProjectionEffectPlanIntentDecision(status="accepted", plan=plan)


def _bounded_normalize(utterance: object) -> str | None:
    if type(utterance) is not str:
        return None
    if len(utterance) > MAX_UTTERANCE_CHARS:
        return "__REJECT__"
    if not utterance:
        return None
    try:
        normalized = unicodedata.normalize("NFKC", utterance).casefold()
    except Exception:
        return "__REJECT__"
    if len(normalized) > MAX_UTTERANCE_CHARS:
        return "__REJECT__"
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace(",", "、").replace("，", "、")
    normalized = normalized.replace("．", "。")
    normalized = normalized.strip("。.!！")
    return normalized or None


def _strip_context_fillers(text: str) -> str | None:
    current = text
    stripped = 0
    while stripped <= MAX_CONTEXT_FILLERS:
        match = next(
            (filler for filler in _CONTEXT_FILLERS if current.startswith(filler)),
            None,
        )
        if match is None:
            return current.lstrip("、")
        current = current[len(match) :].lstrip("、")
        stripped += 1
    return None


def _effect_mentions(text: str) -> tuple[_EffectMention, ...]:
    occupied = [False] * len(text)
    mentions: list[_EffectMention] = []
    aliases = sorted(
        (
            (marker, effect_id)
            for effect_id, markers in _EFFECT_MARKERS.items()
            for marker in markers
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for marker, effect_id in aliases:
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            end = index + len(marker)
            if not any(occupied[index:end]):
                mentions.append(
                    _EffectMention(
                        index,
                        end,
                        effect_id,
                    )
                )
                for occupied_index in range(index, end):
                    occupied[occupied_index] = True
            start = end
    mentions.sort(key=lambda item: item.start)
    return tuple(mentions)


def _position_mentions(text: str) -> tuple[_PositionMention, ...] | None:
    occupied = [False] * len(text)
    mentions: list[_PositionMention] = []
    for alias, (x, y) in sorted(
        _POSITION_ALIASES,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        start = 0
        while True:
            index = text.find(alias, start)
            if index < 0:
                break
            end = index + len(alias)
            if not any(occupied[index:end]):
                mentions.append(_PositionMention(index, end, x, y))
                for occupied_index in range(index, end):
                    occupied[occupied_index] = True
            start = index + len(alias)
    mentions.sort(key=lambda item: item.start)
    if len(mentions) > 4:
        return None
    return tuple(mentions)


def _strength_mention(
    text: str,
) -> _StrengthMention | None | Literal[False]:
    occupied = [False] * len(text)
    matches: list[_StrengthMention] = []
    for alias, value in sorted(
        _STRENGTH_ALIASES,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        start = 0
        while True:
            index = text.find(alias, start)
            if index < 0:
                break
            end = index + len(alias)
            if not any(occupied[index:end]):
                matches.append(_StrengthMention(index, end, value))
                for occupied_index in range(index, end):
                    occupied[occupied_index] = True
            start = end
    if len(matches) > 1:
        return False
    return matches[0] if matches else None


def _duration_mention(
    text: str,
) -> _DurationMention | None | Literal[False]:
    folded = text.casefold()
    if any(marker in folded for marker in _UNKNOWN_DURATION_MARKERS):
        return False
    matches = tuple(_DURATION_PATTERN.finditer(folded))
    if len(matches) > 1:
        return False
    if not matches:
        return False if "秒" in folded else None
    try:
        milliseconds = Decimal(matches[0].group(1)) * 1_000
    except (InvalidOperation, ValueError):
        return False
    if milliseconds != milliseconds.to_integral_value():
        return False
    value = int(milliseconds)
    if not MIN_DURATION_MS <= value <= MAX_DURATION_MS:
        return False
    match = matches[0]
    return _DurationMention(match.start(), match.end(), value)


def _is_compact_planned_cast(
    text: str,
    *,
    effect_mention: _EffectMention,
    positions: tuple[_PositionMention, ...],
    strength_mention: _StrengthMention | None,
    duration_mention: _DurationMention | None,
) -> bool:
    if len(positions) != 1 or strength_mention is None or duration_mention is None:
        return False
    spans = (
        (positions[0].start, positions[0].end, "position"),
        (strength_mention.start, strength_mention.end, "strength"),
        (effect_mention.start, effect_mention.end, "effect"),
        (duration_mention.start, duration_mention.end, "duration"),
    )
    ordered = sorted(spans, key=lambda item: item[0])
    if [item[2] for item in ordered] != [
        "position",
        "strength",
        "effect",
        "duration",
    ]:
        return False
    gaps = _gaps(text, tuple((start, end) for start, end, _ in ordered))
    if gaps is None:
        return False
    normalized_gaps = tuple(_without_punctuation(gap) for gap in gaps)
    return normalized_gaps == ("", "に", "の", "を", "")


def _whole_input_consumed(
    text: str,
    *,
    effect_mention: _EffectMention,
    positions: tuple[_PositionMention, ...],
    strength_mention: _StrengthMention | None,
    duration_mention: _DurationMention | None,
    action_mention: _ActionMention | None,
) -> bool:
    spans = [
        (effect_mention.start, effect_mention.end),
        *((position.start, position.end) for position in positions),
    ]
    if strength_mention is not None:
        spans.append((strength_mention.start, strength_mention.end))
    if duration_mention is not None:
        spans.append((duration_mention.start, duration_mention.end))
    if action_mention is not None:
        spans.append((action_mention.start, action_mention.end))
    spans.sort()
    gaps = _gaps(text, tuple(spans))
    return gaps is not None and all(
        _GRAMMAR_GAP_PATTERN.fullmatch(gap) is not None for gap in gaps
    )


def _gaps(
    text: str,
    spans: tuple[tuple[int, int], ...],
) -> tuple[str, ...] | None:
    cursor = 0
    gaps: list[str] = []
    for start, end in spans:
        if start < cursor or end < start:
            return None
        gaps.append(text[cursor:start])
        cursor = end
    gaps.append(text[cursor:])
    return tuple(gaps)


def _without_punctuation(text: str) -> str:
    return re.sub(r"[、。.!！?？]", "", text)


def _valid_movement_path(
    text: str,
    positions: tuple[_PositionMention, ...],
) -> bool:
    first_gap = text[positions[0].end : positions[1].start]
    if "から" not in first_gap:
        return False
    for index in range(1, len(positions) - 1):
        gap = text[positions[index].end : positions[index + 1].start]
        if "経由" not in gap:
            return False
    tail = text[positions[-1].end :]
    return "へ" in tail and any(cue in tail for cue in _MOVEMENT_CUES)


def _keyframes(
    positions: tuple[tuple[float, float], ...],
    *,
    strength: float,
    duration_ms: int,
) -> list[dict[str, object]]:
    if len(positions) == 1:
        times = (0,)
    else:
        denominator = len(positions) - 1
        times = tuple(
            duration_ms if index == denominator else duration_ms * index // denominator
            for index in range(len(positions))
        )
    return [
        {
            "atMs": at_ms,
            "position": {"x": position[0], "y": position[1]},
            "strength": strength,
        }
        for at_ms, position in zip(times, positions, strict=True)
    ]


def _clause_count(text: str) -> int:
    return 1 + sum(text.count(separator) for separator in ("、", "。", ";", "；"))


def _clarification() -> ProjectionEffectPlanIntentDecision:
    return ProjectionEffectPlanIntentDecision(
        status="clarification_required",
        reason="projection_plan_needs_clarification",
    )


def _no_match() -> ProjectionEffectPlanIntentDecision:
    return ProjectionEffectPlanIntentDecision(status="no_match")
