from __future__ import annotations

import json
import socket
from typing import Any, Mapping

from sword_voice_agent.adapters.auth import (
    payload_authorized,
    strip_payload_auth,
)
from sword_voice_agent.adapters.gesture_gateway import (
    VoiceStateSink,
    build_gesture_response,
)
from sword_voice_agent.adapters.rate_limit import (
    FixedWindowRateLimiter,
    RateLimitExceeded,
)
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.protocol.messages import ProtocolError, VoicePhase, VoiceState, now_timestamp

DEFAULT_RATE_LIMIT_PER_MINUTE = 6000
GESTURE_EDGE_ACTIVE = "gesture_active"
GESTURE_EDGE_RELEASED = "gesture_released"


def build_udp_gesture_response(
    datagram: bytes,
    gate: GestureInputGate,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
    auth_token: str = "",
) -> dict[str, Any]:
    payload = json.loads(datagram.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ProtocolError("UDP datagram must contain a JSON object")
    if not payload_authorized(payload, auth_token):
        raise ProtocolError("unauthorized gesture datagram")
    sanitized_payload = strip_payload_auth(payload)
    message_type = sanitized_payload.get("type")
    if message_type in {"gesture_status", "gesture_heartbeat"}:
        return {
            "type": "gesture_diagnostic_response",
            "timestamp": now_timestamp(),
            "diagnostic": dict(sanitized_payload),
        }
    if message_type == "gesture_edge":
        return build_gesture_edge_response(
            sanitized_payload,
            voice_state_sink=voice_state_sink,
            turn_controller=turn_controller,
        )
    return build_gesture_response(
        sanitized_payload,
        gate,
        voice_state_sink=voice_state_sink,
        turn_controller=turn_controller,
    )


def build_gesture_edge_response(
    payload: Mapping[str, Any],
    *,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
) -> dict[str, Any]:
    event_name = str(payload.get("event") or "")
    if event_name not in {GESTURE_EDGE_ACTIVE, GESTURE_EDGE_RELEASED}:
        raise ProtocolError("gesture_edge requires gesture_active or gesture_released event")

    active = event_name == GESTURE_EDGE_ACTIVE
    timestamp = _float_payload_value(payload.get("timestamp"), default=now_timestamp())
    confidence = _float_payload_value(payload.get("confidence"), default=0.0)
    turn_id = _optional_text(payload.get("turn_id"))
    voice_state = VoiceState(
        phase=VoicePhase.ARMED if active else VoicePhase.IDLE,
        mic_enabled=active,
        recording=active,
        timestamp=timestamp,
        reason=event_name,
    )
    response_payload: dict[str, Any] = {
        "ok": True,
        "voice_state": voice_state.to_dict(),
        "gate_decision": {
            "timestamp": timestamp,
            "gesture_name": str(payload.get("target_gesture") or "sword_sign"),
            "raw_active": bool(payload.get("current_active", active)),
            "confidence": confidence,
            "mic_enabled": active,
            "changed": True,
            "reason": event_name,
            "source_event": "gesture_edge",
            "frame_id": payload.get("frame_id"),
            "detected_at": payload.get("detected_at"),
            "sent_at": payload.get("sent_at"),
        },
    }
    if turn_controller is not None:
        command = dict(turn_controller.update(voice_state).to_dict())
        if turn_id:
            command["turn_id"] = turn_id
        response_payload["voice_control_command"] = command
    if voice_state_sink is not None:
        response_payload["input_gate_response"] = dict(
            voice_state_sink.send_voice_state(voice_state)
        )
    return response_payload


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_payload_value(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class GestureUdpReceiver:
    def __init__(
        self,
        host: str,
        port: int,
        gate: GestureInputGate,
        voice_state_sink: VoiceStateSink | None = None,
        turn_controller: VoiceTurnController | None = None,
        buffer_size: int = 65535,
        sock: socket.socket | None = None,
        auth_token: str = "",
        receive_timeout_s: float | None = 0.5,
        rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
    ) -> None:
        self.host = host
        self.port = port
        self.gate = gate
        self.voice_state_sink = voice_state_sink
        self.turn_controller = turn_controller
        self.buffer_size = buffer_size
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.auth_token = auth_token
        self.rate_limiter = FixedWindowRateLimiter(rate_limit_per_minute)
        self.receive_timeout_s = (
            None if receive_timeout_s is None or receive_timeout_s <= 0 else receive_timeout_s
        )
        self._owns_socket = sock is None
        self._bound = False
        self._configure_timeout()

    def __enter__(self) -> "GestureUdpReceiver":
        self.bind()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def bind(self) -> None:
        if not self._bound:
            self.sock.bind((self.host, self.port))
            self._bound = True

    def close(self) -> None:
        if self._owns_socket:
            self.sock.close()

    def _configure_timeout(self) -> None:
        settimeout = getattr(self.sock, "settimeout", None)
        if callable(settimeout):
            settimeout(self.receive_timeout_s)

    def receive_once(self) -> tuple[dict[str, Any], tuple[str, int]]:
        data, address = self.sock.recvfrom(self.buffer_size)
        try:
            self.rate_limiter.check(address[0])
        except RateLimitExceeded as exc:
            raise ProtocolError(
                f"rate limit exceeded; retry after {exc.retry_after_s:.3f}s"
            ) from exc
        return (
            build_udp_gesture_response(
                data,
                self.gate,
                voice_state_sink=self.voice_state_sink,
                turn_controller=self.turn_controller,
                auth_token=self.auth_token,
            ),
            address,
        )
