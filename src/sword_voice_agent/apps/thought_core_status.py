from __future__ import annotations

from sword_voice_agent.adapters.status_store import StatusStore, redacted_text
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent


class ThoughtCoreStreamStatusWriter:
    def __init__(
        self,
        store: StatusStore,
        *,
        turn_id: str | None = None,
        source: str = "thought_core",
    ) -> None:
        self.store = store
        self.turn_id = turn_id
        self.source = source
        self.first_message_seen = False
        self.completed_seen = False

    def __call__(self, event: ThoughtCoreStreamEvent) -> None:
        if event.is_message and not self.first_message_seen:
            self.first_message_seen = True
            self.store.append_event(
                "thought_core.first_message",
                source=self.source,
                turn_id=self.turn_id or event.turn_id,
                payload=stream_event_payload(event),
            )
        if event.is_completed and not self.completed_seen:
            self.completed_seen = True
            self.store.append_event(
                "thought_core.completed",
                source=self.source,
                turn_id=self.turn_id or event.turn_id,
                payload=stream_event_payload(event),
            )

    def finish(self, result: dict[str, object]) -> None:
        self.store.write_latest_thought_core_response(
            result,
            turn_id=self.turn_id,
            source=self.source,
        )


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
    if isinstance(turn_payload, dict):
        turn_id = str(turn_payload.get("turn_id") or "")
    return ThoughtCoreStreamStatusWriter(
        StatusStore(status_dir),
        turn_id=turn_id or None,
        source=source,
    )


def stream_event_payload(event: ThoughtCoreStreamEvent) -> dict[str, object]:
    return {
        "event_type": event.event_type,
        "seq": event.seq,
        "elapsed_s": event.elapsed_s,
        "speech_present": bool(event.speech),
        "speech": redacted_text(event.speech),
        "status": event.data.get("status"),
        "tool": event.data.get("tool"),
        "tool_call_id": redacted_text(event.data.get("tool_call_id", "")),
        "tool_call_id_present": bool(event.data.get("tool_call_id")),
    }
