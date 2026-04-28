from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from numbers import Real
import time
from typing import Any, ClassVar, Mapping


def now_timestamp() -> float:
    return time.time()


class ProtocolError(ValueError):
    """Raised when an inbound protocol payload is malformed."""


class VoicePhase(str, Enum):
    IDLE = "idle"
    ARMED = "armed"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class VoiceControlAction(str, Enum):
    NONE = "none"
    START_RECORDING = "start_recording"
    STOP_RECORDING = "stop_recording"


@dataclass(frozen=True)
class GestureSignal:
    active: bool
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GestureSignal":
        if "active" not in payload:
            raise ProtocolError("gesture signal requires 'active'")
        active = payload["active"]
        if not isinstance(active, bool):
            raise ProtocolError("gesture signal 'active' must be a bool")

        confidence = payload.get("confidence", 0.0)
        if not isinstance(confidence, Real) or isinstance(confidence, bool):
            raise ProtocolError("gesture signal 'confidence' must be numeric")
        confidence_float = float(confidence)
        if not math.isfinite(confidence_float):
            raise ProtocolError("gesture signal 'confidence' must be finite")
        if confidence_float < 0.0 or confidence_float > 1.0:
            raise ProtocolError("gesture signal 'confidence' must be between 0.0 and 1.0")
        return cls(
            active=active,
            confidence=confidence_float,
        )


@dataclass(frozen=True)
class GestureState:
    source: str
    timestamp: float
    gestures: Mapping[str, GestureSignal] = field(default_factory=dict)

    type: ClassVar[str] = "gesture_state"

    def gesture(self, name: str) -> GestureSignal:
        return self.gestures.get(name, GestureSignal(active=False, confidence=0.0))

    def is_active(self, name: str, min_confidence: float = 0.0) -> bool:
        signal = self.gesture(name)
        return signal.active and signal.confidence >= min_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "source": self.source,
            "timestamp": self.timestamp,
            "gestures": {
                name: signal.to_dict()
                for name, signal in self.gestures.items()
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GestureState":
        message_type = payload.get("type")
        if message_type is not None and message_type != cls.type:
            raise ProtocolError(f"gesture_state has unsupported type: {message_type!r}")

        gestures_payload = payload.get("gestures")
        if not isinstance(gestures_payload, Mapping):
            raise ProtocolError("gesture_state requires mapping 'gestures'")

        timestamp = _finite_float(
            payload.get("timestamp", now_timestamp()),
            "gesture_state 'timestamp'",
        )
        gestures: dict[str, GestureSignal] = {}
        for name, signal in gestures_payload.items():
            if not isinstance(signal, Mapping):
                raise ProtocolError(f"gesture signal {name!r} must be an object")
            gestures[str(name)] = GestureSignal.from_dict(signal)

        return cls(
            source=str(payload.get("source", "unknown")),
            timestamp=timestamp,
            gestures=gestures,
        )

    @classmethod
    def from_json(cls, text: str) -> "GestureState":
        return cls.from_dict(json.loads(text))


@dataclass(frozen=True)
class VoiceState:
    phase: VoicePhase
    mic_enabled: bool
    recording: bool
    timestamp: float = field(default_factory=now_timestamp)
    reason: str | None = None

    type: ClassVar[str] = "voice_state"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
            "phase": self.phase.value,
            "mic_enabled": self.mic_enabled,
            "recording": self.recording,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VoiceState":
        message_type = payload.get("type")
        if message_type is not None and message_type != cls.type:
            raise ProtocolError(f"voice_state has unsupported type: {message_type!r}")

        return cls(
            phase=VoicePhase(str(payload["phase"])),
            mic_enabled=_required_bool(
                payload.get("mic_enabled"),
                "voice_state 'mic_enabled'",
            ),
            recording=_optional_bool(
                payload,
                "recording",
                default=False,
                label="voice_state 'recording'",
            ),
            timestamp=_finite_float(
                payload.get("timestamp", now_timestamp()),
                "voice_state 'timestamp'",
            ),
            reason=(
                str(payload["reason"])
                if payload.get("reason") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class VoiceControlCommand:
    action: VoiceControlAction
    timestamp: float
    mic_enabled: bool
    reason: str
    source: str = "sword_voice_agent"
    turn_id: str | None = None

    type: ClassVar[str] = "voice_control_command"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
            "action": self.action.value,
            "mic_enabled": self.mic_enabled,
            "reason": self.reason,
            "source": self.source,
        }
        if self.turn_id:
            payload["turn_id"] = self.turn_id
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VoiceControlCommand":
        message_type = payload.get("type")
        if message_type is not None and message_type != cls.type:
            raise ProtocolError(
                f"voice_control_command has unsupported type: {message_type!r}"
            )

        return cls(
            action=VoiceControlAction(str(payload["action"])),
            timestamp=_finite_float(
                payload.get("timestamp", now_timestamp()),
                "voice_control_command 'timestamp'",
            ),
            mic_enabled=_optional_bool(
                payload,
                "mic_enabled",
                default=False,
                label="voice_control_command 'mic_enabled'",
            ),
            reason=str(payload.get("reason", "external")),
            source=str(payload.get("source", "external")),
            turn_id=(
                str(payload["turn_id"])
                if payload.get("turn_id") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class AgentRequest:
    text: str
    user: str = "local-user"
    timestamp: float = field(default_factory=now_timestamp)
    context: Mapping[str, Any] = field(default_factory=dict)
    conversation_id: str | None = None

    type: ClassVar[str] = "agent_request"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
            "text": self.text,
            "user": self.user,
            "context": dict(self.context),
        }
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentRequest":
        return cls(
            text=str(payload["text"]),
            user=str(payload.get("user", "local-user")),
            timestamp=float(payload.get("timestamp", now_timestamp())),
            context=(
                payload["context"]
                if isinstance(payload.get("context"), Mapping)
                else {}
            ),
            conversation_id=(
                str(payload["conversation_id"])
                if payload.get("conversation_id") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class AgentResponse:
    text: str
    timestamp: float = field(default_factory=now_timestamp)
    conversation_id: str | None = None
    message_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    type: ClassVar[str] = "agent_response"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
            "text": self.text,
        }
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id
        if self.message_id:
            payload["message_id"] = self.message_id
        if self.raw:
            payload["raw"] = dict(self.raw)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentResponse":
        return cls(
            text=str(payload["text"]),
            timestamp=float(payload.get("timestamp", now_timestamp())),
            conversation_id=(
                str(payload["conversation_id"])
                if payload.get("conversation_id") is not None
                else None
            ),
            message_id=(
                str(payload["message_id"])
                if payload.get("message_id") is not None
                else None
            ),
            raw=(
                payload["raw"]
                if isinstance(payload.get("raw"), Mapping)
                else {}
            ),
        )


def message_from_dict(
    payload: Mapping[str, Any],
) -> GestureState | VoiceState | VoiceControlCommand | AgentRequest | AgentResponse:
    message_type = payload.get("type")
    if message_type == GestureState.type:
        return GestureState.from_dict(payload)
    if message_type == VoiceState.type:
        return VoiceState.from_dict(payload)
    if message_type == VoiceControlCommand.type:
        return VoiceControlCommand.from_dict(payload)
    if message_type == AgentRequest.type:
        return AgentRequest.from_dict(payload)
    if message_type == AgentResponse.type:
        return AgentResponse.from_dict(payload)
    raise ProtocolError(f"unsupported message type: {message_type!r}")


def _finite_float(value: object, label: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ProtocolError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError(f"{label} must be finite")
    return result


def _required_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError(f"{label} must be a bool")
    return value


def _optional_bool(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: bool,
    label: str,
) -> bool:
    if key not in payload:
        return default
    return _required_bool(payload[key], label)
