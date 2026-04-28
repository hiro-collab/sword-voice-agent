from unittest import TestCase
import json

from sword_voice_agent.adapters.gesture_udp import build_udp_gesture_response
from sword_voice_agent.apps.gesture_udp_receiver import format_debug_line
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

    def test_formats_udp_receiver_debug_line(self) -> None:
        response = {
            "voice_state": {
                "phase": "armed",
                "mic_enabled": True,
            },
            "gate_decision": {
                "raw_active": True,
                "confidence": 0.9512,
                "changed": True,
                "reason": "activation_delay_passed",
            },
            "voice_control_command": {
                "action": "start_recording",
            },
            "input_gate_response": {
                "ok": True,
                "input_gate": {
                    "input_enabled": True,
                    "reason": "activation_delay_passed",
                },
            },
        }

        line = format_debug_line(response, ("127.0.0.1", 55218), sequence=3)

        self.assertIn("[gesture-udp]", line)
        self.assertIn("seq=3", line)
        self.assertIn("from=127.0.0.1:55218", line)
        self.assertIn("raw_active=1", line)
        self.assertIn("confidence=0.951", line)
        self.assertIn("mic_enabled=1", line)
        self.assertIn("changed=1", line)
        self.assertIn("reason=activation_delay_passed", line)
        self.assertIn("action=start_recording", line)
        self.assertIn("input_gate=ok", line)
        self.assertIn("input_gate_enabled=1", line)
