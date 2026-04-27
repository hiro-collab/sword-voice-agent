from unittest import TestCase
from unittest.mock import MagicMock, patch
import io
import json

from sword_voice_agent.adapters.dify import DifyClient
from sword_voice_agent.protocol.messages import AgentRequest


class DifyClientTest(TestCase):
    @patch("sword_voice_agent.adapters.dify.request.urlopen")
    def test_send_chat_message_maps_response(self, urlopen: MagicMock) -> None:
        payload = {
            "answer": "応答です",
            "conversation_id": "conv-1",
            "message_id": "msg-1",
            "created_at": 123.0,
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        urlopen.return_value = response
        client = DifyClient(api_key="test-key", base_url="http://dify.test/v1")

        result = client.send_chat_message(
            AgentRequest(text="こんにちは", context={"trigger": "sword_sign"})
        )

        self.assertEqual(result.text, "応答です")
        self.assertEqual(result.conversation_id, "conv-1")
        request_arg = urlopen.call_args.args[0]
        self.assertEqual(request_arg.full_url, "http://dify.test/v1/chat-messages")
        self.assertEqual(request_arg.headers["Authorization"], "Bearer test-key")
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertEqual(sent["query"], "こんにちは")
        self.assertEqual(sent["inputs"], {"trigger": "sword_sign"})

