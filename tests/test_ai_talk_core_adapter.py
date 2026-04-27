from unittest import TestCase
from unittest.mock import MagicMock, patch
import json

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreInputGateClient,
    voice_state_to_input_gate_payload,
)
from sword_voice_agent.protocol.messages import VoicePhase, VoiceState


class AiTalkCoreAdapterTest(TestCase):
    def test_voice_state_to_input_gate_payload(self) -> None:
        voice_state = VoiceState(
            phase=VoicePhase.ARMED,
            mic_enabled=True,
            recording=True,
            timestamp=12.5,
            reason="activation_delay_passed",
        )

        payload = voice_state_to_input_gate_payload(voice_state)

        self.assertEqual(payload["type"], "input_gate_state")
        self.assertTrue(payload["input_enabled"])
        self.assertTrue(payload["mic_enabled"])
        self.assertEqual(payload["reason"], "activation_delay_passed")
        self.assertEqual(payload["source"], "sword_voice_agent")
        self.assertEqual(payload["timestamp"], 12.5)

    @patch("sword_voice_agent.adapters.ai_talk_core.request.urlopen")
    def test_client_posts_input_gate_payload(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"ok": True}
        ).encode("utf-8")
        urlopen.return_value = response
        client = AiTalkCoreInputGateClient(
            endpoint_url="http://voice.test/api/input-gate",
            source="test-source",
        )

        result = client.send_voice_state(
            VoiceState(
                phase=VoicePhase.IDLE,
                mic_enabled=False,
                recording=False,
                timestamp=20.0,
                reason="release_delay_passed",
            )
        )

        self.assertEqual(result, {"ok": True})
        request_arg = urlopen.call_args.args[0]
        self.assertEqual(request_arg.full_url, "http://voice.test/api/input-gate")
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertFalse(sent["input_enabled"])
        self.assertEqual(sent["source"], "test-source")
        self.assertEqual(sent["reason"], "release_delay_passed")
