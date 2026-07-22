"""Bounded, process-local conversation continuity for Thought Core.

This module intentionally does not persist transcripts.  It keeps a small
same-session handoff window for response wording and clarification only.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable


CONVERSATION_CONTINUITY_SCHEMA_VERSION = "thought-core.conversation_continuity.v0"
DEFAULT_RECENT_TURN_LIMIT = 4
DEFAULT_SESSION_LIMIT = 32
DEFAULT_TTL_SECONDS = 15 * 60
MAX_EXACT_TEXT_CHARS = 600
MAX_DECISION_ITEMS = 6
MAX_OPEN_ITEMS = 4

_CORRECTION_CUES = (
    "訂正",
    "いや、",
    "いや，",
    "ではなく",
    "じゃなく",
    "違う",
    "正確には",
    "修正",
    "correction",
    "rather than",
)
_REJECTION_CUES = (
    "却下",
    "採用しない",
    "実施しない",
    "仕様には入れない",
    "仕様から外",
    "後回し",
    "不要",
    "やめる",
    "しないほうが",
    "reject",
    "do not",
)
_ACCEPTANCE_CUES = (
    "採用",
    "それで進め",
    "その方針で",
    "その順番で",
    "実施して",
    "構わない",
    "了解",
    "accept",
    "proceed",
)
_TOPIC_SWITCH_CUES = (
    "話題を変え",
    "別の話",
    "別件",
    "ところで",
    "topic switch",
    "different topic",
)
_CANCEL_CUES = (
    "この話は忘れて",
    "会話の文脈を消して",
    "前の話はなし",
    "前の指示は取り消",
    "さっきの指示は取り消",
    "forget this conversation",
    "cancel the previous instruction",
)
_REFERENT_TOKENS = (
    "それを",
    "それで",
    "それについて",
    "これを",
    "これで",
    "これについて",
    "あれを",
    "あれで",
    "その方針",
    "その話",
    "この方針",
    "この話",
    "直前",
    "さっき",
    "前の",
    "先ほど",
    "the previous",
    "that",
)
_QUESTION_CUES = (
    "?",
    "？",
    "どうだろう",
    "どう思う",
    "教えて",
    "確認してほしい",
    "できますか",
    "できるだろうか",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)(?:\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"password|secret|credential)\b|APIキー|アクセストークン|"
        r"パスワード|秘密鍵)(?:\s*[:=]\s*|\s+is\s+|は)\S+"
    ),
)


@dataclass
class _PendingTurn:
    turn_id: str
    user_text: str | None
    created_at: float
    assistant_parts: list[str] = field(default_factory=list)
    referents: list[str] = field(default_factory=list)
    signal: str = ""
    withheld: bool = False


@dataclass
class _SessionState:
    topic_epoch: int = 0
    sequence: int = 0
    last_activity: float = 0.0
    recent_turns: list[dict[str, object]] = field(default_factory=list)
    decisions: list[dict[str, object]] = field(default_factory=list)
    open_items: list[dict[str, object]] = field(default_factory=list)
    pending: _PendingTurn | None = None
    withheld_turn_count: int = 0
    last_transition: str = ""


class ConversationContinuity:
    """Keep a bounded same-process handoff without creating memory authority."""

    def __init__(
        self,
        *,
        recent_turn_limit: int = DEFAULT_RECENT_TURN_LIMIT,
        session_limit: int = DEFAULT_SESSION_LIMIT,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.recent_turn_limit = max(1, int(recent_turn_limit))
        self.session_limit = max(1, int(session_limit))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock
        self._sessions: OrderedDict[str, _SessionState] = OrderedDict()
        self._lock = RLock()

    def begin_turn(self, *, session_id: str, turn_id: str, user_text: str) -> None:
        """Open one turn and update only deterministic continuity signals."""

        now = self._clock()
        with self._lock:
            state = self._session(session_id, now)
            self._finalize_pending(state)
            state.sequence += 1
            self._expire_open_items(state)
            text = _normalize_text(str(user_text or "").strip())
            safe_text = _exact_safe_text(text)
            if safe_text is None:
                state.withheld_turn_count += 1
                state.pending = _PendingTurn(
                    turn_id=str(turn_id or ""),
                    user_text=None,
                    created_at=now,
                )
                state.last_activity = now
                return

            if _contains_any(safe_text, _CANCEL_CUES):
                self._reset_state(state, transition="cancelled")
                state.sequence += 1
                state.pending = _PendingTurn(
                    turn_id=turn_id,
                    user_text=None,
                    created_at=now,
                    signal="cancelled",
                )
                state.last_activity = now
                return

            if _contains_any(safe_text, _TOPIC_SWITCH_CUES):
                self._reset_state(state, transition="topic_switched")
                state.topic_epoch += 1
                state.sequence += 1

            signal = _decision_signal(safe_text)
            if signal == "correction":
                state.decisions.clear()
            if signal:
                state.decisions.append(
                    {
                        "ordinal": state.sequence,
                        "status": signal,
                        "proposition": _decision_excerpt(safe_text),
                        "provenance": "same_session_user_turn",
                    }
                )
                del state.decisions[:-MAX_DECISION_ITEMS]

            referents = [
                token
                for token in _REFERENT_TOKENS
                if token.lower() in safe_text.lower()
            ]
            state.pending = _PendingTurn(
                turn_id=str(turn_id or ""),
                user_text=safe_text,
                created_at=now,
                referents=referents[:4],
                signal=signal,
            )
            state.last_activity = now

    def record_assistant(
        self,
        *,
        session_id: str,
        turn_id: str,
        assistant_text: str,
    ) -> None:
        """Attach the visible assistant text to the current in-memory turn."""

        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            state = self._sessions.get(session_id)
            if state is None:
                return
            pending = state.pending
            if pending is None or pending.turn_id != str(turn_id or ""):
                return
            if pending.withheld:
                return
            safe_text = _exact_safe_text(
                _normalize_text(str(assistant_text or "").strip())
            )
            if safe_text is None:
                state.withheld_turn_count += 1
                self._withhold_pending(state, pending)
                state.last_activity = now
                return
            if safe_text and safe_text not in pending.assistant_parts:
                pending.assistant_parts.append(safe_text)
                del pending.assistant_parts[:-4]
            state.last_activity = now

    def context_for_response(self, *, session_id: str) -> dict[str, object]:
        """Return private responder-only context, never a tool/action authority."""

        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            state = self._sessions.get(session_id)
            if state is None:
                return {}
            self._sessions.move_to_end(session_id)
            pending = state.pending
            referents = list(pending.referents) if pending is not None else []
            if referents and state.recent_turns:
                referent_resolution = "latest_completed_turn_available"
            elif referents:
                referent_resolution = "clarification_required"
            else:
                referent_resolution = "not_requested"

            has_context = bool(
                state.recent_turns
                or state.decisions
                or state.open_items
                or referents
                or state.last_transition
            )
            if not has_context:
                return {}

            return {
                "schema_version": CONVERSATION_CONTINUITY_SCHEMA_VERSION,
                "scope": "process_local_same_session",
                "topic_epoch": state.topic_epoch,
                "recent_turns": [dict(item) for item in state.recent_turns],
                "rolling_state": {
                    "decisions": [dict(item) for item in state.decisions],
                    "open_items": [dict(item) for item in state.open_items],
                    "latest_transition": state.last_transition or "none",
                },
                "current_referents": referents,
                "referent_resolution": referent_resolution,
                "retention": {
                    "recent_turn_limit": self.recent_turn_limit,
                    "ttl_seconds": self.ttl_seconds,
                    "persistence": "none",
                    "raw_audio_stored": False,
                    "older_history_retrieval": "disabled",
                },
                "rules": [
                    "latest_user_correction_wins",
                    "do_not_revive_rejected_or_superseded_items",
                    "ask_when_referent_or_topic_is_ambiguous",
                    "continuity_is_not_action_authority",
                ],
                "withheld_turn_count": state.withheld_turn_count,
            }

    def public_summary(self, *, session_id: str) -> dict[str, object]:
        """Return a text-free summary safe for diagnostic event metadata."""

        now = self._clock()
        with self._lock:
            self._purge_expired(now)
            state = self._sessions.get(session_id)
            if state is None:
                return {}
            pending = state.pending
            return {
                "schema_version": CONVERSATION_CONTINUITY_SCHEMA_VERSION,
                "status": "available",
                "scope": "process_local_same_session",
                "recent_turn_count": len(state.recent_turns),
                "decision_count": len(state.decisions),
                "open_item_count": len(state.open_items),
                "referent_count": len(pending.referents) if pending else 0,
                "withheld_turn_count": state.withheld_turn_count,
                "raw_audio_stored": False,
                "raw_text_persisted": False,
                "safe_to_act": False,
            }

    def _session(self, session_id: str, now: float) -> _SessionState:
        self._purge_expired(now)
        key = str(session_id or "")
        state = self._sessions.get(key)
        if state is None:
            state = _SessionState(last_activity=now)
            self._sessions[key] = state
        self._sessions.move_to_end(key)
        while len(self._sessions) > self.session_limit:
            _, evicted = self._sessions.popitem(last=False)
            self._erase_state(evicted)
        return state

    def _expired(self, state: _SessionState, now: float) -> bool:
        return bool(state.last_activity and now - state.last_activity >= self.ttl_seconds)

    def _finalize_pending(self, state: _SessionState) -> None:
        pending = state.pending
        if pending is None:
            return
        if pending.user_text and pending.assistant_parts:
            assistant_text = " ".join(pending.assistant_parts)
            if len(assistant_text) <= MAX_EXACT_TEXT_CHARS:
                state.recent_turns.append(
                    {
                        "turn_id": pending.turn_id,
                        "user_text": pending.user_text,
                        "assistant_text": assistant_text,
                        "provenance": "thought_loop_visible_turn",
                        "exact_within_bound": True,
                    }
                )
                del state.recent_turns[:-self.recent_turn_limit]
                if _looks_like_question(assistant_text):
                    state.open_items.append(
                        {
                            "ordinal": state.sequence,
                            "source": "assistant",
                            "question": assistant_text,
                            "provenance": "thought_loop_visible_turn",
                        }
                    )
                    del state.open_items[:-MAX_OPEN_ITEMS]
        self._erase_pending(pending)
        state.pending = None

    def _expire_open_items(self, state: _SessionState) -> None:
        state.open_items = [
            item
            for item in state.open_items
            if state.sequence - int(item.get("ordinal") or 0) <= 1
        ]

    def _reset_state(self, state: _SessionState, *, transition: str) -> None:
        self._erase_turn_data(state)
        state.withheld_turn_count = 0
        state.last_transition = transition

    def _purge_expired(self, now: float) -> None:
        expired_keys = [
            key for key, state in self._sessions.items() if self._expired(state, now)
        ]
        for key in expired_keys:
            state = self._sessions.pop(key)
            self._erase_state(state)

    def _erase_state(self, state: _SessionState) -> None:
        self._erase_turn_data(state)
        state.topic_epoch = 0
        state.sequence = 0
        state.last_activity = 0.0
        state.withheld_turn_count = 0
        state.last_transition = ""

    def _erase_turn_data(self, state: _SessionState) -> None:
        for collection in (
            state.recent_turns,
            state.decisions,
            state.open_items,
        ):
            for item in collection:
                item.clear()
            collection.clear()
        if state.pending is not None:
            self._erase_pending(state.pending)
        state.pending = None

    def _erase_pending(self, pending: _PendingTurn) -> None:
        pending.turn_id = ""
        pending.user_text = None
        pending.created_at = 0.0
        pending.assistant_parts.clear()
        pending.referents.clear()
        pending.signal = ""
        pending.withheld = False

    def _withhold_pending(
        self,
        state: _SessionState,
        pending: _PendingTurn,
    ) -> None:
        retained_decisions: list[dict[str, object]] = []
        for decision in state.decisions:
            if int(decision.get("ordinal") or 0) == state.sequence:
                decision.clear()
            else:
                retained_decisions.append(decision)
        state.decisions[:] = retained_decisions
        pending.user_text = None
        pending.assistant_parts.clear()
        pending.referents.clear()
        pending.signal = ""
        pending.withheld = True


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(cue.lower() in lowered for cue in cues)


def _decision_signal(text: str) -> str:
    if _contains_any(text, _CORRECTION_CUES):
        return "correction"
    if _contains_any(text, _REJECTION_CUES):
        return "rejected"
    if _contains_any(text, _ACCEPTANCE_CUES):
        return "accepted"
    return ""


def _looks_like_question(text: str) -> bool:
    return _contains_any(text, _QUESTION_CUES)


def _normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _exact_safe_text(text: str) -> str | None:
    if not text or len(text) > MAX_EXACT_TEXT_CHARS:
        return None
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return None
    return text


def _decision_excerpt(text: str) -> str:
    return text if len(text) <= 240 else f"{text[:240]}..."
