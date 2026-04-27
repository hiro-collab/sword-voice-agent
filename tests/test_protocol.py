from unittest import TestCase

from sword_voice_agent.protocol.messages import (
    AgentRequest,
    GestureSignal,
    GestureState,
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
            }
        )

        self.assertIsInstance(message, VoiceControlCommand)
        self.assertEqual(message.action.value, "start_recording")

    def test_agent_request_context_defaults(self) -> None:
        request = AgentRequest.from_dict({"text": "今日の記録をまとめて"})

        self.assertEqual(request.user, "local-user")
        self.assertEqual(request.context, {})
