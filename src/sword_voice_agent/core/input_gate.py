from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sword_voice_agent.protocol.messages import GestureState, VoicePhase, VoiceState


@dataclass(frozen=True)
class InputGateDecision:
    timestamp: float
    gesture_name: str
    raw_active: bool
    confidence: float
    mic_enabled: bool
    changed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "gesture_name": self.gesture_name,
            "raw_active": self.raw_active,
            "confidence": self.confidence,
            "mic_enabled": self.mic_enabled,
            "changed": self.changed,
            "reason": self.reason,
        }

    def to_voice_state(self) -> VoiceState:
        phase = VoicePhase.ARMED if self.mic_enabled else VoicePhase.IDLE
        return VoiceState(
            phase=phase,
            mic_enabled=self.mic_enabled,
            recording=self.mic_enabled,
            timestamp=self.timestamp,
            reason=self.reason,
        )


class GestureInputGate:
    """Turns noisy gesture detections into stable microphone enable decisions."""

    def __init__(
        self,
        gesture_name: str = "sword_sign",
        min_confidence: float = 0.8,
        activation_delay_s: float = 0.3,
        release_delay_s: float = 0.5,
    ) -> None:
        if activation_delay_s < 0:
            raise ValueError("activation_delay_s must be >= 0")
        if release_delay_s < 0:
            raise ValueError("release_delay_s must be >= 0")

        self.gesture_name = gesture_name
        self.min_confidence = min_confidence
        self.activation_delay_s = activation_delay_s
        self.release_delay_s = release_delay_s
        self._mic_enabled = False
        self._active_since: float | None = None
        self._inactive_since: float | None = None

    @property
    def mic_enabled(self) -> bool:
        return self._mic_enabled

    def reset(self) -> None:
        self._mic_enabled = False
        self._active_since = None
        self._inactive_since = None

    def update(self, state: GestureState) -> InputGateDecision:
        signal = state.gesture(self.gesture_name)
        raw_active = signal.active and signal.confidence >= self.min_confidence
        timestamp = state.timestamp
        previous = self._mic_enabled
        reason = "stable"

        if raw_active:
            self._inactive_since = None
            if self._active_since is None:
                self._active_since = timestamp
                reason = "gesture_detected"

            if (
                not self._mic_enabled
                and timestamp - self._active_since >= self.activation_delay_s
            ):
                self._mic_enabled = True
                reason = "activation_delay_passed"
            elif not self._mic_enabled:
                reason = "waiting_for_activation_delay"
        else:
            self._active_since = None
            if self._inactive_since is None:
                self._inactive_since = timestamp
                reason = "gesture_lost"

            if (
                self._mic_enabled
                and timestamp - self._inactive_since >= self.release_delay_s
            ):
                self._mic_enabled = False
                reason = "release_delay_passed"
            elif self._mic_enabled:
                reason = "waiting_for_release_delay"

        return InputGateDecision(
            timestamp=timestamp,
            gesture_name=self.gesture_name,
            raw_active=raw_active,
            confidence=signal.confidence,
            mic_enabled=self._mic_enabled,
            changed=previous != self._mic_enabled,
            reason=reason,
        )
