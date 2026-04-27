from __future__ import annotations

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

    @property
    def recording(self) -> bool:
        return self._recording

    def reset(self) -> None:
        self._recording = False

    def update(self, voice_state: VoiceState) -> VoiceControlCommand:
        action = VoiceControlAction.NONE
        if voice_state.mic_enabled and not self._recording:
            action = VoiceControlAction.START_RECORDING
            self._recording = True
        elif not voice_state.mic_enabled and self._recording:
            action = VoiceControlAction.STOP_RECORDING
            self._recording = False

        return VoiceControlCommand(
            action=action,
            timestamp=voice_state.timestamp,
            mic_enabled=voice_state.mic_enabled,
            reason=voice_state.reason or voice_state.phase.value,
            source=self.source,
        )

