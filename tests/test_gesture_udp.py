from unittest import TestCase
import json

from sword_voice_agent.adapters.gesture_udp import build_udp_gesture_response
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController


class GestureUdpTest(TestCase):
    def test_builds_response_from_sword_sign_datagram(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        payload = {
            "type": "gesture_state",
            "timestamp": 10.0,
            "source": "mediapipe_sword_sign",
            "hand_detected": True,
            "primary": "sword_sign",
            "gestures": {
                "sword_sign": {
                    "active": True,
                    "confidence": 0.95,
                    "label": 0,
                },
                "victory": {
                    "active": False,
                    "confidence": 0.01,
                    "label": 1,
                },
            },
        }

        response = build_udp_gesture_response(
            json.dumps(payload).encode("utf-8"),
            gate,
            turn_controller=VoiceTurnController(),
        )

        self.assertTrue(response["voice_state"]["mic_enabled"])
        self.assertEqual(
            response["voice_control_command"]["action"],
            "start_recording",
        )

