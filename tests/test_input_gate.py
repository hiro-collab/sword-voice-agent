from unittest import TestCase

from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.protocol.messages import GestureSignal, GestureState


def state(timestamp: float, active: bool, confidence: float = 0.95) -> GestureState:
    return GestureState(
        source="test",
        timestamp=timestamp,
        gestures={
            "sword_sign": GestureSignal(active=active, confidence=confidence)
        },
    )


class GestureInputGateTest(TestCase):
    def test_enables_after_activation_delay(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.3, release_delay_s=0.5)

        self.assertFalse(gate.update(state(0.0, True)).mic_enabled)
        self.assertFalse(gate.update(state(0.2, True)).mic_enabled)

        decision = gate.update(state(0.3, True))

        self.assertTrue(decision.mic_enabled)
        self.assertTrue(decision.changed)
        self.assertEqual(decision.reason, "activation_delay_passed")

    def test_does_not_enable_when_confidence_is_too_low(self) -> None:
        gate = GestureInputGate(min_confidence=0.8, activation_delay_s=0.0)

        decision = gate.update(state(0.0, True, confidence=0.4))

        self.assertFalse(decision.raw_active)
        self.assertFalse(decision.mic_enabled)

    def test_releases_after_release_delay(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0, release_delay_s=0.5)

        self.assertTrue(gate.update(state(0.0, True)).mic_enabled)
        self.assertTrue(gate.update(state(0.1, False)).mic_enabled)
        self.assertTrue(gate.update(state(0.5, False)).mic_enabled)

        decision = gate.update(state(0.6, False))

        self.assertFalse(decision.mic_enabled)
        self.assertTrue(decision.changed)
        self.assertEqual(decision.reason, "release_delay_passed")

    def test_short_drop_does_not_release(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0, release_delay_s=0.5)

        self.assertTrue(gate.update(state(0.0, True)).mic_enabled)
        self.assertTrue(gate.update(state(0.1, False)).mic_enabled)
        self.assertTrue(gate.update(state(0.2, True)).mic_enabled)
        self.assertTrue(gate.update(state(0.7, True)).mic_enabled)

    def test_activation_gap_grace_can_bridge_brief_pre_activation_drop(self) -> None:
        gate = GestureInputGate(
            activation_delay_s=0.3,
            activation_gap_grace_s=0.15,
            min_activation_active_frames=3,
            release_delay_s=0.5,
        )

        self.assertFalse(gate.update(state(0.0, True)).mic_enabled)
        self.assertFalse(gate.update(state(0.1, True)).mic_enabled)
        self.assertFalse(gate.update(state(0.2, True)).mic_enabled)

        decision = gate.update(state(0.31, False))

        self.assertTrue(decision.mic_enabled)
        self.assertTrue(decision.changed)
        self.assertEqual(decision.reason, "activation_delay_passed_after_gap_grace")

    def test_activation_gap_grace_does_not_bridge_long_pre_activation_drop(self) -> None:
        gate = GestureInputGate(
            activation_delay_s=0.3,
            activation_gap_grace_s=0.15,
            min_activation_active_frames=3,
            release_delay_s=0.5,
        )

        self.assertFalse(gate.update(state(0.0, True)).mic_enabled)
        self.assertFalse(gate.update(state(0.1, True)).mic_enabled)
        self.assertFalse(gate.update(state(0.5, False)).mic_enabled)

        decision = gate.update(state(0.6, False))

        self.assertFalse(decision.mic_enabled)
        self.assertEqual(decision.reason, "stable")

    def test_min_activation_active_frames_blocks_sparse_false_open(self) -> None:
        gate = GestureInputGate(
            activation_delay_s=0.3,
            activation_gap_grace_s=0.15,
            min_activation_active_frames=3,
            release_delay_s=0.5,
        )

        self.assertFalse(gate.update(state(0.0, True)).mic_enabled)
        self.assertFalse(gate.update(state(0.32, True)).mic_enabled)

        decision = gate.update(state(0.4, False))

        self.assertFalse(decision.mic_enabled)
        self.assertEqual(decision.reason, "gesture_lost")

    def test_rejects_out_of_range_min_confidence(self) -> None:
        with self.assertRaises(ValueError):
            GestureInputGate(min_confidence=-0.1)

    def test_rejects_negative_activation_gap_grace(self) -> None:
        with self.assertRaises(ValueError):
            GestureInputGate(activation_gap_grace_s=-0.1)

    def test_rejects_zero_min_activation_active_frames(self) -> None:
        with self.assertRaises(ValueError):
            GestureInputGate(min_activation_active_frames=0)
