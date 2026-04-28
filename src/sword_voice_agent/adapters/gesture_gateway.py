from __future__ import annotations

from typing import Any, Mapping

from sword_voice_agent.application.gesture_pipeline import (
    VoiceStateSink,
    handle_gesture_payload,
)
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController


def build_gesture_response(
    payload: Mapping[str, Any],
    gate: GestureInputGate,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
) -> dict[str, Any]:
    return handle_gesture_payload(
        payload,
        gate,
        voice_state_sink=voice_state_sink,
        turn_controller=turn_controller,
    )
