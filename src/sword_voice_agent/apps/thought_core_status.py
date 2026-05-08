from __future__ import annotations

from typing import Any

from sword_voice_agent.adapters.status_store import StatusStore, redacted_text
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent


class ThoughtCoreStreamStatusWriter:
    def __init__(
        self,
        store: StatusStore,
        *,
        turn_id: str | None = None,
        session_id: str | None = None,
        turn_text: str = "",
        issue_id: str | None = None,
        source: str = "thought_core",
    ) -> None:
        self.store = store
        self.turn_id = turn_id
        self.session_id = session_id
        self.turn_text = turn_text
        self.issue_id = issue_id
        self.source = source
        self.first_message_seen = False
        self.completed_seen = False
        self.user_entry_written = False
        self.assistant_entry_written = False
        self.speech_parts: list[str] = []
        self.message_speech = ""
        self.event_count = 0
        self.speech_delta_count = 0
        self.last_event_elapsed_s: float | None = None
        self.first_event_elapsed_s: float | None = None
        self.first_speech_elapsed_s: float | None = None
        self.first_message_elapsed_s: float | None = None
        self.completed_elapsed_s: float | None = None
        self.max_gap_s: float | None = None
        self.slowest_gap: dict[str, object] | None = None
        self.stream_timeline: list[dict[str, object]] = []

    def __call__(self, event: ThoughtCoreStreamEvent) -> None:
        self._append_user_entry(event.turn_id)
        timing = self._record_stream_event(event)
        self.store.append_event(
            "thought_core.stream_event",
            source=self.source,
            turn_id=self.turn_id or event.turn_id,
            payload=stream_event_payload(event, timing=timing),
        )
        if event.is_speech_delta and event.speech_delta:
            self.speech_parts.append(event.speech_delta)
        if event.is_message and event.speech:
            self.message_speech = event.speech
        if event.is_message and not self.first_message_seen:
            self.first_message_seen = True
            self.store.append_event(
                "thought_core.first_message",
                source=self.source,
                turn_id=self.turn_id or event.turn_id,
                payload=stream_event_payload(event, timing=timing),
            )
        if event.is_completed and not self.completed_seen:
            self.completed_seen = True
            self._append_assistant_entry(event)
            self.store.append_event(
                "thought_core.completed",
                source=self.source,
                turn_id=self.turn_id or event.turn_id,
                payload=stream_event_payload(event, timing=timing),
            )

    def finish(self, result: dict[str, object]) -> None:
        if not bool(result.get("skipped", False)):
            self._append_user_entry(self.turn_id)
            self._append_assistant_entry_from_result(result)
        self.store.write_latest_thought_core_response(
            result,
            turn_id=self.turn_id,
            source=self.source,
        )

    def _append_user_entry(self, event_turn_id: str | None) -> None:
        if self.user_entry_written or not self.turn_text.strip():
            return
        self.user_entry_written = True
        self.store.append_conversation_entry(
            "user",
            self.turn_text,
            source=self.source,
            turn_id=self.turn_id or event_turn_id,
            session_id=self.session_id,
            issue_id=self.issue_id,
            event_type="thought_core.user_turn",
        )

    def _append_assistant_entry(self, event: ThoughtCoreStreamEvent) -> None:
        if self.assistant_entry_written:
            return
        text = "".join(self.speech_parts).strip() or self.message_speech.strip()
        if not text:
            return
        self.assistant_entry_written = True
        self.store.append_conversation_entry(
            "assistant",
            text,
            source=self.source,
            turn_id=self.turn_id or event.turn_id,
            session_id=self.session_id or event.session_id,
            issue_id=self.issue_id,
            event_type=event.event_type,
            metadata={
                "seq": event.seq,
                "elapsed_s": event.elapsed_s,
                "status": event.data.get("status"),
                "timing": self._timing_summary(),
            },
        )

    def _append_assistant_entry_from_result(self, result: dict[str, object]) -> None:
        if self.assistant_entry_written:
            return
        response_payload = _mapping(result.get("response"))
        text = response_payload.get("text")
        if not str(text or "").strip():
            text = "".join(self.speech_parts).strip() or self.message_speech.strip()
        if not str(text or "").strip():
            return
        self.assistant_entry_written = True
        self.store.append_conversation_entry(
            "assistant",
            text,
            source=self.source,
            turn_id=self.turn_id,
            session_id=self.session_id,
            issue_id=self.issue_id,
            event_type="thought_core.response",
            metadata={"timing": self._timing_summary_from_result(result)},
        )

    def _record_stream_event(
        self,
        event: ThoughtCoreStreamEvent,
    ) -> dict[str, object]:
        self.event_count += 1
        elapsed_s = _float_or_none(event.elapsed_s)
        delta_s = None
        if elapsed_s is not None:
            if self.first_event_elapsed_s is None:
                self.first_event_elapsed_s = elapsed_s
            if self.last_event_elapsed_s is not None:
                delta_s = max(0.0, elapsed_s - self.last_event_elapsed_s)
                if self.max_gap_s is None or delta_s > self.max_gap_s:
                    self.max_gap_s = delta_s
                    self.slowest_gap = {
                        "after_event_seq": event.seq,
                        "event_type": event.event_type,
                        "delta_elapsed_s": delta_s,
                    }
            self.last_event_elapsed_s = elapsed_s

        if event.is_speech_delta:
            self.speech_delta_count += 1
            if self.first_speech_elapsed_s is None and elapsed_s is not None:
                self.first_speech_elapsed_s = elapsed_s
        if event.is_message and self.first_message_elapsed_s is None and elapsed_s is not None:
            self.first_message_elapsed_s = elapsed_s
        if event.is_completed and elapsed_s is not None:
            self.completed_elapsed_s = elapsed_s

        timeline_item = {
            "event_type": event.event_type,
            "seq": event.seq,
            "phase": stream_event_phase(event),
            "elapsed_s": elapsed_s,
            "delta_elapsed_s": delta_s,
            "stage": event.data.get("stage"),
            "status": event.data.get("status"),
            "tool": event.data.get("tool"),
            "speech_present": bool(event.speech),
            "speech_chars": len(event.speech or event.speech_delta or ""),
        }
        self.stream_timeline.append(timeline_item)
        if len(self.stream_timeline) > 80:
            del self.stream_timeline[0 : len(self.stream_timeline) - 80]
        return timeline_item

    def _timing_summary(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "speech_delta_count": self.speech_delta_count,
            "first_event_elapsed_s": self.first_event_elapsed_s,
            "first_speech_elapsed_s": self.first_speech_elapsed_s,
            "first_message_elapsed_s": self.first_message_elapsed_s,
            "completed_elapsed_s": self.completed_elapsed_s,
            "max_gap_s": self.max_gap_s,
            "slowest_gap": self.slowest_gap,
            "timeline": list(self.stream_timeline),
        }

    def _timing_summary_from_result(self, result: dict[str, object]) -> dict[str, object]:
        summary = self._timing_summary()
        streaming = _mapping(_mapping(result.get("response")).get("raw")).get("_streaming")
        if isinstance(streaming, dict):
            for key in (
                "event_count",
                "first_event_elapsed_s",
                "first_token_elapsed_s",
                "completed_elapsed_s",
            ):
                if (
                    summary.get(key) in (None, 0)
                    and streaming.get(key) is not None
                ):
                    summary[key] = streaming.get(key)
        return summary


def build_thought_core_status_writer(
    status_dir: str,
    result: dict[str, object],
    *,
    source: str,
) -> ThoughtCoreStreamStatusWriter | None:
    if not status_dir:
        return None
    turn_payload = result.get("turn_payload")
    turn_id = ""
    session_id = ""
    turn_text = ""
    issue_id = None
    if isinstance(turn_payload, dict):
        turn_id = str(turn_payload.get("turn_id") or "")
        session_id = str(turn_payload.get("session_id") or "")
        turn_text = str(turn_payload.get("text") or "")
        context_refs = turn_payload.get("context_refs")
        if isinstance(context_refs, dict):
            issue_id = (
                str(context_refs.get("issue_id") or "")
                or str(context_refs.get("issueId") or "")
                or None
            )
    return ThoughtCoreStreamStatusWriter(
        StatusStore(status_dir),
        turn_id=turn_id or None,
        session_id=session_id or None,
        turn_text=turn_text,
        issue_id=issue_id,
        source=source,
    )


def stream_event_payload(
    event: ThoughtCoreStreamEvent,
    *,
    timing: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = {
        "event_type": event.event_type,
        "seq": event.seq,
        "elapsed_s": event.elapsed_s,
        "speech_present": bool(event.speech),
        "speech": redacted_text(event.speech),
        "speech_chars": len(event.speech or event.speech_delta or ""),
        "phase": stream_event_phase(event),
        "stage": event.data.get("stage"),
        "partial": event.data.get("partial"),
        "status": event.data.get("status"),
        "tool": event.data.get("tool"),
        "tool_call_id": redacted_text(event.data.get("tool_call_id", "")),
        "tool_call_id_present": bool(event.data.get("tool_call_id")),
    }
    if timing:
        payload["delta_elapsed_s"] = timing.get("delta_elapsed_s")
    return payload


def stream_event_phase(event: ThoughtCoreStreamEvent) -> str:
    if event.is_speech_delta:
        return "speech_delta"
    if event.is_message:
        return "message"
    if event.is_completed:
        return "completed"
    if event.event_type.startswith("thought."):
        return "stage"
    if event.event_type.startswith("tool."):
        return "tool"
    if event.event_type.startswith("action."):
        return "action"
    if event.event_type.startswith("observation."):
        return "observe"
    return event.event_type


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
