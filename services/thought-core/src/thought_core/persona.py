"""Assistant persona shaping for thought-core expression output."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol


CHEERFUL_OSSAN_PROFILE = "cheerful_ossan_v0"

CHEERFUL_OSSAN_SYSTEM_PROMPT = (
    "Speak as a cheerful, warm, slightly rough Japanese home assistant. "
    "Use casual Japanese, not polite desu/masu style. Use exactly one emotion "
    "tag at the start, chosen from [neutral], [happy], [angry], [sad], "
    "[relaxed], [surprised]. Add a [motion:name] tag only when it naturally "
    "fits; allowed motions are think, cheer, cross, mouth_cover, crossed_arms, "
    "bow, shrug, shy, wave, clap. Keep the reply short and do not claim home "
    "actions were executed unless tool results say so."
)

_EMOTION_TAGS = {"neutral", "happy", "angry", "sad", "relaxed", "surprised"}
_MOTION_TAGS = {
    "think",
    "cheer",
    "cross",
    "mouth_cover",
    "crossed_arms",
    "bow",
    "shrug",
    "shy",
    "wave",
    "clap",
}
_TAG_PATTERN = re.compile(r"^\[(neutral|happy|angry|sad|relaxed|surprised)\]", re.I)
_MOTION_PATTERN = re.compile(r"\[motion:([A-Za-z_][A-Za-z0-9_-]*)\]", re.I)
_BARE_MOTION_PATTERN = re.compile(r"\[([A-Za-z_][A-Za-z0-9_-]*)\]")


@dataclass(frozen=True)
class PersonaMessage:
    speech: str
    emotion: str
    motion: str
    profile: str


class AssistantPersona(Protocol):
    profile: str

    def apply(self, speech: str, *, emotion: str, motion: str) -> PersonaMessage:
        ...


class PlainPersona:
    profile = "plain"

    def apply(self, speech: str, *, emotion: str, motion: str) -> PersonaMessage:
        return PersonaMessage(
            speech=str(speech or ""),
            emotion=emotion,
            motion=motion,
            profile=self.profile,
        )


class CheerfulOssanPersona:
    profile = CHEERFUL_OSSAN_PROFILE

    def apply(self, speech: str, *, emotion: str, motion: str) -> PersonaMessage:
        text = normalize_persona_tags(str(speech or "").strip())
        tag = _emotion_tag(emotion)
        motion_tag = _motion_tag(motion)
        if _TAG_PATTERN.match(text):
            shaped = text
        else:
            shaped = _casualize_text(text)
            prefix = f"[{tag}]"
            if motion_tag:
                prefix += f"[motion:{motion_tag}]"
            shaped = f"{prefix}{shaped}"
        return PersonaMessage(
            speech=shaped,
            emotion=tag,
            motion=motion_tag or motion,
            profile=self.profile,
        )


def build_persona_from_env() -> AssistantPersona:
    profile = (
        os.environ.get("THOUGHT_CORE_PERSONA")
        or os.environ.get("SWORD_THOUGHT_CORE_PERSONA")
        or "plain"
    ).strip().lower()
    if profile in {"", "0", "false", "off", "none", "plain"}:
        return PlainPersona()
    if profile in {"cheerful_ossan", "cheerful-ossan", CHEERFUL_OSSAN_PROFILE}:
        return CheerfulOssanPersona()
    return PlainPersona()


def persona_system_prompt_from_env() -> str:
    profile = (
        os.environ.get("THOUGHT_CORE_PERSONA")
        or os.environ.get("SWORD_THOUGHT_CORE_PERSONA")
        or ""
    ).strip().lower()
    if profile in {"cheerful_ossan", "cheerful-ossan", CHEERFUL_OSSAN_PROFILE}:
        return CHEERFUL_OSSAN_SYSTEM_PROMPT
    return ""


def normalize_persona_tags(text: str) -> str:
    def normalize_motion(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name in _MOTION_TAGS:
            return f"[motion:{name}]"
        return match.group(0)

    def normalize_bare(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name in _MOTION_TAGS:
            return f"[motion:{name}]"
        if name in _EMOTION_TAGS:
            return f"[{name}]"
        return match.group(0)

    return _BARE_MOTION_PATTERN.sub(normalize_bare, _MOTION_PATTERN.sub(normalize_motion, text))


def strip_persona_tags(text: str) -> str:
    return re.sub(
        r"\[(?:motion:[^\]\s]+|neutral|happy|angry|sad|relaxed|surprised)\]",
        "",
        str(text or ""),
        flags=re.I,
    )


def _emotion_tag(emotion: str) -> str:
    value = str(emotion or "").strip().lower()
    mapping = {
        "satisfied": "happy",
        "confident": "happy",
        "attentive": "neutral",
        "focused": "neutral",
        "concerned": "sad",
        "troubled": "sad",
        "serious": "neutral",
    }
    value = mapping.get(value, value)
    return value if value in _EMOTION_TAGS else "neutral"


def _motion_tag(motion: str) -> str:
    value = str(motion or "").strip().lower()
    mapping = {
        "idle": "",
        "small_nod": "",
        "nod": "",
        "look_back": "shrug",
        "thinking": "think",
        "confident": "crossed_arms",
        "satisfied": "cheer",
    }
    value = mapping.get(value, value)
    return value if value in _MOTION_TAGS else ""


def _casualize_text(text: str) -> str:
    exact = {
        "うん、聞いたよ。": "おう、聞いたぜ。",
        "うん、状態を見てみるね。": "おう、状態を見てみるぜ。",
        "うん、操作できるか確認するね。": "おう、操作できるか見てみるぜ。",
        "うん、その状態を覚えるね。": "おう、その状態、覚えとくぜ。",
        "うん、確認したよ。": "おう、確認したぜ。",
        "うん、止めるね。": "おう、止めとくぜ。",
        "OK、続きやるね。": "OK、続きやるぞ。",
        "聞こえています。マイクテストは成功です。": "おう、聞こえてるぜ。マイクテスト成功だな。",
        "聞こえています。今は会話応答の境界を準備中です。": "おう、聞こえてるぜ。会話応答の境界は準備中だな。",
    }
    if text in exact:
        return exact[text]
    replacements = (
        ("了解、", "おう、"),
        ("確認します。", "確認するぞ。"),
        ("確認しています。", "確認してるぞ。"),
        ("見直します。", "見直すぞ。"),
        ("成功です。", "成功だぜ。"),
        ("準備中です。", "準備中だな。"),
        ("必要です。", "必要だ。"),
        ("まだ実行していません。", "まだ実行してないぞ。"),
        ("してください。", "してくれ。"),
        ("見えます。", "見えるぜ。"),
        ("しました。", "したぜ。"),
    )
    result = text
    for source, target in replacements:
        result = result.replace(source, target)
    return result
