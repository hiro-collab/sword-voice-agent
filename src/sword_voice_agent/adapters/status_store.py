from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4

from sword_voice_agent.protocol.messages import now_timestamp

MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StatusStore:
    """File-backed status projection for the local integration console."""

    def __init__(
        self,
        root: str | Path = ".cache/sword_voice_agent",
        *,
        max_events: int = 200,
    ) -> None:
        self.root = Path(root)
        self.max_events = max(1, max_events)

    @property
    def latest_gesture_path(self) -> Path:
        return self.root / "latest_gesture.json"

    @property
    def latest_gesture_diagnostic_path(self) -> Path:
        return self.root / "latest_gesture_diagnostic.json"

    @property
    def latest_voice_turn_path(self) -> Path:
        return self.root / "latest_voice_turn.json"

    @property
    def latest_dify_response_path(self) -> Path:
        return self.root / "latest_dify_response.json"

    @property
    def latest_thought_core_response_path(self) -> Path:
        return self.root / "latest_thought_core_response.json"

    @property
    def modules_dir(self) -> Path:
        return self.root / "modules"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    def write_latest_gesture(self, payload: Mapping[str, Any]) -> None:
        response = _mapping(payload.get("response"))
        diagnostic = _mapping(response.get("diagnostic"))
        if diagnostic:
            previous = self.read_json(self.latest_gesture_diagnostic_path)
            should_append = _diagnostic_event_key(previous) != _diagnostic_event_key(payload)
            self.write_json(self.latest_gesture_diagnostic_path, payload)
            if should_append:
                self.append_event(
                    "gesture.diagnostic",
                    source="gesture_udp_receiver",
                    payload={
                        "diagnostic_type": diagnostic.get("type"),
                        "status": diagnostic.get("status"),
                        "frame_id": diagnostic.get("frame_id"),
                        "fps": diagnostic.get("fps"),
                        "hand_detected": diagnostic.get("hand_detected"),
                        "primary_gesture": diagnostic.get("primary_gesture"),
                    },
                )
            return

        previous = self.read_json(self.latest_gesture_path)
        should_append = (
            _gesture_event_key(previous) != _gesture_event_key(payload)
            or _voice_command_action(payload) != "none"
        )
        self.write_json(self.latest_gesture_path, payload)
        command = _mapping(response.get("voice_control_command"))
        voice_state = _mapping(response.get("voice_state"))
        command_turn_id = _optional_text(command.get("turn_id"))
        if voice_state and (
            command_turn_id is not None or bool(voice_state.get("recording", False))
        ):
            self.write_json(
                self.latest_voice_turn_path,
                {
                    "type": "latest_voice_turn",
                    "timestamp": payload.get("timestamp", now_timestamp()),
                    "turn_id": command_turn_id,
                    "voice_state": dict(voice_state),
                    "voice_control_command": dict(command),
                },
            )
        if should_append:
            self.append_event(
                "gesture.received",
                source="gesture_udp_receiver",
                turn_id=_optional_text(command.get("turn_id")),
                payload=payload,
            )

    def write_latest_dify_response(
        self,
        payload: Mapping[str, Any],
        *,
        turn_id: str | None = None,
    ) -> None:
        stored_payload = dict(payload)
        event_turn_id = turn_id
        if event_turn_id:
            stored_payload["turn_id"] = event_turn_id
        self.write_json(self.latest_dify_response_path, stored_payload)
        request_payload = _mapping(payload.get("request"))
        response_payload = _mapping(payload.get("response"))
        self.append_event(
            "dify.response",
            source="watch_handoff_to_dify",
            turn_id=event_turn_id or _turn_id_from_request(request_payload),
            payload={
                "request_text": redacted_text(request_payload.get("text", "")),
                "response_text": redacted_text(response_payload.get("text", "")),
                "conversation_id": redacted_text(
                    response_payload.get("conversation_id", "")
                ),
                "conversation_id_present": bool(response_payload.get("conversation_id")),
                "message_id": redacted_text(response_payload.get("message_id", "")),
                "message_id_present": bool(response_payload.get("message_id")),
                "skipped": payload.get("skipped", False),
                "skip_reason": payload.get("skip_reason"),
            },
        )

    def write_latest_thought_core_response(
        self,
        payload: Mapping[str, Any],
        *,
        turn_id: str | None = None,
        source: str = "watch_handoff_to_thought_core",
    ) -> None:
        stored_payload = dict(payload)
        event_turn_id = turn_id or _turn_id_from_thought_core_payload(payload)
        if event_turn_id:
            stored_payload["turn_id"] = event_turn_id
        self.write_json(self.latest_thought_core_response_path, stored_payload)
        request_payload = _mapping(payload.get("request"))
        turn_payload = _mapping(payload.get("turn_payload"))
        response_payload = _mapping(payload.get("response"))
        raw_payload = _mapping(response_payload.get("raw"))
        streaming_payload = _mapping(raw_payload.get("_streaming"))
        self.append_event(
            "thought_core.response",
            source=source,
            turn_id=event_turn_id,
            payload={
                "request_text": redacted_text(request_payload.get("text", "")),
                "turn_text": redacted_text(turn_payload.get("text", "")),
                "response_text": redacted_text(response_payload.get("text", "")),
                "event_count": streaming_payload.get("event_count"),
                "skipped": payload.get("skipped", False),
                "skip_reason": payload.get("skip_reason"),
            },
        )

    def write_module_status(
        self,
        name: str,
        state: str,
        *,
        label: str = "",
        detail: str = "",
        timestamp: float | None = None,
    ) -> None:
        safe_name = normalize_module_name(name)
        self.write_json(
            self.modules_dir / f"{safe_name}.json",
            {
                "type": "module_status",
                "name": safe_name,
                "label": label or safe_name,
                "state": state,
                "detail": detail,
                "timestamp": timestamp if timestamp is not None else now_timestamp(),
            },
        )

    def read_module_statuses(self) -> dict[str, dict[str, Any]]:
        if not self.modules_dir.exists():
            return {}
        statuses: dict[str, dict[str, Any]] = {}
        for path in sorted(self.modules_dir.glob("*.json")):
            payload = self.read_json(path)
            if not isinstance(payload, dict):
                continue
            name = str(payload.get("name") or path.stem)
            if not MODULE_NAME_PATTERN.fullmatch(name):
                continue
            statuses[name] = payload
        return statuses

    def write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_event(
        self,
        event_type: str,
        *,
        source: str,
        payload: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        event_payload = payload if payload is not None else data or {}
        event = {
            "event_id": uuid4().hex,
            "type": event_type,
            "timestamp": timestamp if timestamp is not None else now_timestamp(),
            "source": source,
            "turn_id": turn_id,
            "payload": dict(event_payload),
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False))
            stream.write("\n")
        self.trim_events()

    def trim_events(self) -> None:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        if len(lines) <= self.max_events:
            return
        self.events_path.write_text(
            "\n".join(lines[-self.max_events :]) + "\n",
            encoding="utf-8",
        )

    def clear(self) -> None:
        for path in (
            self.latest_gesture_path,
            self.latest_gesture_diagnostic_path,
            self.latest_voice_turn_path,
            self.latest_dify_response_path,
            self.latest_thought_core_response_path,
            self.events_path,
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if self.modules_dir.exists():
            for path in self.modules_dir.glob("*.json"):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def read_events(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def read_events_after(
        self,
        event_id: str | None,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        events = self.read_events(limit=limit)
        if not event_id:
            return events
        for index, event in enumerate(events):
            if str(event.get("event_id") or "") == event_id:
                return events[index + 1 :]
        return events

    def read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_module_name(name: str) -> str:
    normalized = name.strip()
    if not MODULE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "module name must contain only letters, numbers, hyphen, or underscore"
        )
    return normalized


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def redacted_text(value: object) -> str:
    return "[redacted]" if str(value or "") else ""


def _turn_id_from_request(request_payload: Mapping[str, Any]) -> str | None:
    context = _mapping(request_payload.get("context"))
    return _optional_text(context.get("turn_id"))


def _turn_id_from_thought_core_payload(payload: Mapping[str, Any]) -> str | None:
    turn_payload = _mapping(payload.get("turn_payload"))
    response_payload = _mapping(payload.get("response"))
    request_payload = _mapping(payload.get("request"))
    return (
        _optional_text(turn_payload.get("turn_id"))
        or _optional_text(response_payload.get("conversation_id"))
        or _turn_id_from_request(request_payload)
    )


def _gesture_event_key(payload: Mapping[str, Any] | None) -> tuple[object, ...] | None:
    if payload is None:
        return None
    response = _mapping(payload.get("response"))
    decision = _mapping(response.get("gate_decision"))
    voice_state = _mapping(response.get("voice_state"))
    command = _mapping(response.get("voice_control_command"))
    return (
        decision.get("raw_active"),
        decision.get("reason"),
        voice_state.get("mic_enabled"),
        voice_state.get("phase"),
        command.get("action"),
    )


def _voice_command_action(payload: Mapping[str, Any]) -> str:
    response = _mapping(payload.get("response"))
    command = _mapping(response.get("voice_control_command"))
    return str(command.get("action", "none"))


def _diagnostic_event_key(payload: Mapping[str, Any] | None) -> tuple[object, ...] | None:
    if payload is None:
        return None
    response = _mapping(payload.get("response"))
    diagnostic = _mapping(response.get("diagnostic"))
    if not diagnostic:
        return None
    sword = _mapping(diagnostic.get("sword_sign"))
    camera = _mapping(diagnostic.get("camera"))
    return (
        diagnostic.get("type"),
        diagnostic.get("status"),
        diagnostic.get("frame_id"),
        diagnostic.get("hand_detected"),
        diagnostic.get("primary_gesture"),
        sword.get("active"),
        sword.get("confidence"),
        camera.get("opened"),
    )
