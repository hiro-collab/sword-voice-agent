from unittest import TestCase

from sword_voice_agent.protocol.messages import (
    AgentRequest,
    GestureSignal,
    GestureState,
    ProtocolError,
    VoiceControlCommand,
    VoicePhase,
    VoiceState,
    message_from_dict,
)


class ProtocolTest(TestCase):
    def test_gesture_state_roundtrip(self) -> None:
        state = GestureState(
            source="mediapipe_sword_sign",
            timestamp=1.25,
            gestures={
                "sword_sign": GestureSignal(active=True, confidence=0.92)
            },
        )

        decoded = GestureState.from_json(state.to_json())

        self.assertEqual(decoded.source, "mediapipe_sword_sign")
        self.assertTrue(decoded.is_active("sword_sign", min_confidence=0.9))
        self.assertFalse(decoded.is_active("victory"))

    def test_message_dispatch(self) -> None:
        message = message_from_dict(
            {
                "type": "voice_state",
                "timestamp": 1.0,
                "phase": "recording",
                "mic_enabled": True,
                "recording": True,
            }
        )

        self.assertIsInstance(message, VoiceState)
        self.assertEqual(message.phase, VoicePhase.RECORDING)

    def test_voice_control_command_dispatch(self) -> None:
        message = message_from_dict(
            {
                "type": "voice_control_command",
                "timestamp": 1.0,
                "action": "start_recording",
                "mic_enabled": True,
                "reason": "activation_delay_passed",
                "source": "test",
                "turn_id": "turn-1",
            }
        )

        self.assertIsInstance(message, VoiceControlCommand)
        self.assertEqual(message.action.value, "start_recording")
        self.assertEqual(message.turn_id, "turn-1")

    def test_agent_request_context_defaults(self) -> None:
        request = AgentRequest.from_dict({"text": "今日の記録をまとめて"})

        self.assertEqual(request.user, "local-user")
        self.assertEqual(request.context, {})

    def test_rejects_string_active_gesture_signal(self) -> None:
        with self.assertRaises(ProtocolError):
            GestureSignal.from_dict({"active": "false", "confidence": 0.1})

    def test_rejects_out_of_range_confidence(self) -> None:
        with self.assertRaises(ProtocolError):
            GestureSignal.from_dict({"active": True, "confidence": 1.1})

    def test_rejects_non_finite_confidence(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ProtocolError):
                    GestureSignal.from_dict({"active": True, "confidence": value})

    def test_rejects_malformed_gesture_entry(self) -> None:
        with self.assertRaises(ProtocolError):
            GestureState.from_dict(
                {
                    "type": "gesture_state",
                    "timestamp": 1.0,
                    "gestures": {"sword_sign": "not-an-object"},
                }
            )

    def test_rejects_non_finite_gesture_timestamp(self) -> None:
        with self.assertRaises(ProtocolError):
            GestureState.from_dict(
                {
                    "type": "gesture_state",
                    "timestamp": float("inf"),
                    "gestures": {
                        "sword_sign": {"active": True, "confidence": 0.9},
                    },
                }
            )

    def test_rejects_string_voice_state_bool(self) -> None:
        with self.assertRaises(ProtocolError):
            VoiceState.from_dict(
                {
                    "type": "voice_state",
                    "timestamp": 1.0,
                    "phase": "recording",
                    "mic_enabled": "false",
                    "recording": True,
                }
            )

    def test_rejects_string_voice_control_bool(self) -> None:
        with self.assertRaises(ProtocolError):
            VoiceControlCommand.from_dict(
                {
                    "type": "voice_control_command",
                    "timestamp": 1.0,
                    "action": "start_recording",
                    "mic_enabled": "false",
                }
            )
