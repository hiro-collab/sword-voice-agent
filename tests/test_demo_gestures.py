from unittest import TestCase

from sword_voice_agent.apps.send_demo_gestures import demo_sequence
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController


class DemoGesturesTest(TestCase):
    def test_demo_sequence_drives_recording_turn(self) -> None:
        gate = GestureInputGate()
        turn_controller = VoiceTurnController()
        sequence = demo_sequence()

        self.assertGreaterEqual(len(sequence), 2)
        commands = [
            turn_controller.update(gate.update(state).to_voice_state()).action.value
            for state in sequence
        ]

        self.assertIn("start_recording", commands)
        self.assertIn("stop_recording", commands)
        self.assertEqual("none", commands[0])
        self.assertEqual("stop_recording", commands[-1])
