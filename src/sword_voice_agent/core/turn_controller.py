from __future__ import annotations

from uuid import uuid4

from sword_voice_agent.protocol.messages import (
    VoiceControlAction,
    VoiceControlCommand,
    VoiceState,
)


class VoiceTurnController:
    """Converts stable microphone state into recording lifecycle commands."""

    def __init__(self, source: str = "sword_voice_agent") -> None:
        self.source = source
        self._recording = False
        self._turn_id: str | None = None

    @property
    def recording(self) -> bool:
        return self._recording

    def reset(self) -> None:
        self._recording = False
        self._turn_id = None

    def update(self, voice_state: VoiceState) -> VoiceControlCommand:
        action = VoiceControlAction.NONE
        command_turn_id = self._turn_id
        if voice_state.mic_enabled and not self._recording:
            action = VoiceControlAction.START_RECORDING
            self._recording = True
            self._turn_id = uuid4().hex
            command_turn_id = self._turn_id
        elif not voice_state.mic_enabled and self._recording:
            action = VoiceControlAction.STOP_RECORDING
            self._recording = False
            command_turn_id = self._turn_id
            self._turn_id = None

        return VoiceControlCommand(
            action=action,
            timestamp=voice_state.timestamp,
            mic_enabled=voice_state.mic_enabled,
            reason=voice_state.reason or voice_state.phase.value,
            source=self.source,
            turn_id=command_turn_id,
        )
