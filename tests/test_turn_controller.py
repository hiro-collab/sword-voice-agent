from unittest import TestCase

from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.protocol.messages import (
    VoiceControlAction,
    VoicePhase,
    VoiceState,
)


class VoiceTurnControllerTest(TestCase):
    def test_emits_start_and_stop_on_mic_edges(self) -> None:
        controller = VoiceTurnController(source="test")

        idle = controller.update(
            VoiceState(
                phase=VoicePhase.IDLE,
                mic_enabled=False,
                recording=False,
                timestamp=0.0,
                reason="gesture_lost",
            )
        )
        start = controller.update(
            VoiceState(
                phase=VoicePhase.ARMED,
                mic_enabled=True,
                recording=True,
                timestamp=0.4,
                reason="activation_delay_passed",
            )
        )
        stable = controller.update(
            VoiceState(
                phase=VoicePhase.ARMED,
                mic_enabled=True,
                recording=True,
                timestamp=0.6,
                reason="stable",
            )
        )
        stop = controller.update(
            VoiceState(
                phase=VoicePhase.IDLE,
                mic_enabled=False,
                recording=False,
                timestamp=1.3,
                reason="release_delay_passed",
            )
        )

        self.assertEqual(idle.action, VoiceControlAction.NONE)
        self.assertEqual(start.action, VoiceControlAction.START_RECORDING)
        self.assertEqual(stable.action, VoiceControlAction.NONE)
        self.assertEqual(stop.action, VoiceControlAction.STOP_RECORDING)
        self.assertEqual(stop.source, "test")
        self.assertIsNotNone(start.turn_id)
        self.assertEqual(stable.turn_id, start.turn_id)
        self.assertEqual(stop.turn_id, start.turn_id)
        self.assertIsNone(idle.turn_id)
