from __future__ import annotations

from typing import Any, Mapping, Protocol

from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.protocol.messages import GestureState, VoiceState


class VoiceStateSink(Protocol):
    def send_voice_state(self, voice_state: VoiceState) -> Mapping[str, Any]:
        ...


def handle_gesture_payload(
    payload: Mapping[str, Any],
    gate: GestureInputGate,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
) -> dict[str, Any]:
    state = GestureState.from_dict(payload)
    decision = gate.update(state)
    voice_state = decision.to_voice_state()
    response_payload: dict[str, Any] = {
        "ok": True,
        "voice_state": voice_state.to_dict(),
        "gate_decision": decision.to_dict(),
    }
    if turn_controller is not None:
        response_payload["voice_control_command"] = (
            turn_controller.update(voice_state).to_dict()
        )
    if voice_state_sink is not None:
        response_payload["input_gate_response"] = dict(
            voice_state_sink.send_voice_state(voice_state)
        )
    return response_payload
