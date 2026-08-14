from unittest import TestCase
from unittest.mock import MagicMock, patch
import json

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreInputGateClient,
    LOCAL_API_TOKEN_HEADER,
    build_input_gate_state_payload,
    voice_state_to_input_gate_payload,
)
from sword_voice_agent.protocol.messages import VoicePhase, VoiceState


class AiTalkCoreAdapterTest(TestCase):
    def test_builds_backend_neutral_external_trigger_payload(self) -> None:
        payload = build_input_gate_state_payload(
            input_enabled=True,
            reason="operator_button_down",
            source="touchdesigner",
            timestamp=25.0,
        )

        self.assertEqual(
            payload,
            {
                "type": "input_gate_state",
                "input_enabled": True,
                "mic_enabled": True,
                "reason": "operator_button_down",
                "source": "touchdesigner",
                "timestamp": 25.0,
            },
        )

    def test_external_trigger_payload_rejects_ambiguous_boolean(self) -> None:
        with self.assertRaisesRegex(ValueError, "input_enabled must be a boolean"):
            build_input_gate_state_payload(
                input_enabled="true",  # type: ignore[arg-type]
                reason="operator_button_down",
                source="web_tool",
            )

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
            endpoint_url="https://voice.test/api/input-gate",
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
        self.assertEqual(request_arg.full_url, "https://voice.test/api/input-gate")
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertFalse(sent["input_enabled"])
        self.assertEqual(sent["source"], "test-source")
        self.assertEqual(sent["reason"], "release_delay_passed")

    @patch("sword_voice_agent.adapters.ai_talk_core.request.urlopen")
    def test_client_sends_local_api_token_header(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"ok": True}
        ).encode("utf-8")
        urlopen.return_value = response
        client = AiTalkCoreInputGateClient(
            endpoint_url="http://127.0.0.1:8000/api/input-gate",
            api_token="local-token",
        )

        client.send_voice_state(
            VoiceState(
                phase=VoicePhase.IDLE,
                mic_enabled=False,
                recording=False,
                timestamp=20.0,
            )
        )

        request_arg = urlopen.call_args.args[0]
        headers = {key.lower(): value for key, value in request_arg.header_items()}
        self.assertEqual(headers[LOCAL_API_TOKEN_HEADER.lower()], "local-token")

    @patch("sword_voice_agent.adapters.ai_talk_core.request.urlopen")
    def test_client_posts_generic_external_trigger_state(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"ok": True, "input_gate": {"input_enabled": True}}
        ).encode("utf-8")
        urlopen.return_value = response
        client = AiTalkCoreInputGateClient(
            endpoint_url="http://127.0.0.1:8000/api/input-gate",
            source="integration_adapter",
            api_token="local-token",
        )

        result = client.set_input_enabled(
            True,
            reason="external",
            source="external",
            timestamp=30.0,
        )

        self.assertTrue(result["input_gate"]["input_enabled"])
        request_arg = urlopen.call_args.args[0]
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertEqual(sent["source"], "external")
        self.assertEqual(sent["reason"], "external")
        self.assertTrue(sent["input_enabled"])
        self.assertTrue(sent["mic_enabled"])
        self.assertEqual(sent["timestamp"], 30.0)

    def test_client_rejects_plain_http_for_non_loopback_input_gate(self) -> None:
        with self.assertRaises(ValueError):
            AiTalkCoreInputGateClient(endpoint_url="http://voice.test/api/input-gate")
