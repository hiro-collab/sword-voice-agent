from unittest import TestCase

from sword_voice_agent.adapters.gesture_http import build_gesture_response
from sword_voice_agent.core.input_gate import GestureInputGate


class GestureHttpTest(TestCase):
    def test_builds_voice_state_response(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0, release_delay_s=0.5)

        response = build_gesture_response(
            {
                "type": "gesture_state",
                "source": "test",
                "timestamp": 10.0,
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.95,
                    }
                },
            },
            gate,
        )

        self.assertTrue(response["ok"])
        self.assertTrue(response["voice_state"]["mic_enabled"])
        self.assertEqual(response["voice_state"]["phase"], "armed")
        self.assertEqual(response["gate_decision"]["reason"], "activation_delay_passed")

    def test_low_confidence_does_not_enable_mic(self) -> None:
        gate = GestureInputGate(min_confidence=0.8, activation_delay_s=0.0)

        response = build_gesture_response(
            {
                "type": "gesture_state",
                "source": "test",
                "timestamp": 10.0,
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.2,
                    }
                },
            },
            gate,
        )

        self.assertFalse(response["voice_state"]["mic_enabled"])
        self.assertFalse(response["gate_decision"]["raw_active"])

