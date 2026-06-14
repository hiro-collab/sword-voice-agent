from __future__ import annotations

from dataclasses import dataclass
import math
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
        activation_gap_grace_s: float = 0.0,
        min_activation_active_frames: int = 1,
    ) -> None:
        if not math.isfinite(min_confidence) or not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be finite and between 0.0 and 1.0")
        if not math.isfinite(activation_delay_s) or activation_delay_s < 0:
            raise ValueError("activation_delay_s must be finite and >= 0")
        if not math.isfinite(release_delay_s) or release_delay_s < 0:
            raise ValueError("release_delay_s must be finite and >= 0")
        if not math.isfinite(activation_gap_grace_s) or activation_gap_grace_s < 0:
            raise ValueError("activation_gap_grace_s must be finite and >= 0")
        if min_activation_active_frames < 1:
            raise ValueError("min_activation_active_frames must be >= 1")

        self.gesture_name = gesture_name
        self.min_confidence = min_confidence
        self.activation_delay_s = activation_delay_s
        self.release_delay_s = release_delay_s
        self.activation_gap_grace_s = activation_gap_grace_s
        self.min_activation_active_frames = min_activation_active_frames
        self._mic_enabled = False
        self._active_since: float | None = None
        self._last_active_at: float | None = None
        self._activation_active_count = 0
        self._inactive_since: float | None = None

    @property
    def mic_enabled(self) -> bool:
        return self._mic_enabled

    def reset(self) -> None:
        self._mic_enabled = False
        self._active_since = None
        self._last_active_at = None
        self._activation_active_count = 0
        self._inactive_since = None

    def update(self, state: GestureState) -> InputGateDecision:
        signal = state.gesture(self.gesture_name)
        raw_active = signal.active and signal.confidence >= self.min_confidence
        timestamp = state.timestamp
        previous = self._mic_enabled
        reason = "stable"

        if raw_active:
            self._inactive_since = None
            self._last_active_at = timestamp
            if self._active_since is None:
                self._active_since = timestamp
                self._activation_active_count = 0
                reason = "gesture_detected"
            self._activation_active_count += 1

            if (
                not self._mic_enabled
                and timestamp - self._active_since >= self.activation_delay_s
                and self._activation_active_count >= self.min_activation_active_frames
            ):
                self._mic_enabled = True
                reason = "activation_delay_passed"
            elif not self._mic_enabled:
                reason = "waiting_for_activation_delay"
        else:
            within_activation_gap_grace = (
                not self._mic_enabled
                and self._active_since is not None
                and self._last_active_at is not None
                and self.activation_gap_grace_s > 0
                and timestamp - self._last_active_at <= self.activation_gap_grace_s
                and self._activation_active_count >= self.min_activation_active_frames
            )
            opened_after_gap_grace = False
            if within_activation_gap_grace:
                reason = "waiting_for_activation_gap_grace"
                if timestamp - self._active_since >= self.activation_delay_s:
                    self._mic_enabled = True
                    reason = "activation_delay_passed_after_gap_grace"
                    opened_after_gap_grace = True
            else:
                self._active_since = None
                self._last_active_at = None
                self._activation_active_count = 0
            if self._inactive_since is None:
                self._inactive_since = timestamp
                if not within_activation_gap_grace:
                    reason = "gesture_lost"

            if (
                not opened_after_gap_grace
                and
                self._mic_enabled
                and timestamp - self._inactive_since >= self.release_delay_s
            ):
                self._mic_enabled = False
                reason = "release_delay_passed"
            elif self._mic_enabled and not opened_after_gap_grace:
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
