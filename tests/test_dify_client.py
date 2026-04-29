from unittest import TestCase
from unittest.mock import MagicMock, patch
import json

from sword_voice_agent.adapters.dify import DifyClient, DifyClientError
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
        client = DifyClient(api_key="test-key", base_url="https://dify.test/v1")

        result = client.send_chat_message(
            AgentRequest(text="こんにちは", context={"trigger": "sword_sign"})
        )

        self.assertEqual(result.text, "応答です")
        self.assertEqual(result.conversation_id, "conv-1")
        request_arg = urlopen.call_args.args[0]
        self.assertEqual(request_arg.full_url, "https://dify.test/v1/chat-messages")
        self.assertEqual(request_arg.headers["Authorization"], "Bearer test-key")
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertEqual(sent["query"], "こんにちは")
        self.assertEqual(sent["inputs"], {"trigger": "sword_sign"})
        self.assertEqual(sent["response_mode"], "blocking")

    @patch("sword_voice_agent.adapters.dify.request.urlopen")
    def test_send_chat_message_streaming_aggregates_sse(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter(
            [
                (
                    'data: {"event":"message","answer":"こん",'
                    '"conversation_id":"conv-1","message_id":"msg-1",'
                    '"created_at":123}\n'
                ).encode("utf-8"),
                b"\n",
                (
                    'data: {"event":"message","answer":"にちは",'
                    '"conversation_id":"conv-1","message_id":"msg-1"}\n'
                ).encode("utf-8"),
                b"\n",
                b'data: {"event":"message_end","conversation_id":"conv-1","message_id":"msg-1"}\n',
                b"\n",
            ]
        )
        response.__enter__.return_value = stream
        urlopen.return_value = response
        client = DifyClient(api_key="test-key", base_url="https://dify.test/v1")
        events = []

        result = client.send_chat_message_streaming(
            AgentRequest(text="こんにちは", context={"trigger": "sword_sign"}),
            on_event=events.append,
        )

        self.assertEqual(result.text, "こんにちは")
        self.assertEqual(result.conversation_id, "conv-1")
        self.assertEqual(result.message_id, "msg-1")
        self.assertEqual(events[0].answer_delta, "こん")
        self.assertEqual(events[-1].event, "message_end")
        self.assertEqual(result.raw["_streaming"]["event_count"], 3)
        self.assertIsNotNone(result.raw["_streaming"]["first_token_elapsed_s"])
        request_arg = urlopen.call_args.args[0]
        self.assertEqual(request_arg.headers["Accept"], "text/event-stream")
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertEqual(sent["response_mode"], "streaming")

    @patch("sword_voice_agent.adapters.dify.request.urlopen")
    def test_streaming_error_event_raises_client_error(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter(
            [
                b'data: {"event":"error","code":"bad_request","message":"invalid"}\n',
                b"\n",
            ]
        )
        response.__enter__.return_value = stream
        urlopen.return_value = response
        client = DifyClient(api_key="test-key", base_url="https://dify.test/v1")

        with self.assertRaises(DifyClientError):
            client.send_chat_message_streaming(AgentRequest(text="こんにちは"))

    def test_rejects_plain_http_for_non_loopback_base_url(self) -> None:
        with self.assertRaises(ValueError):
            DifyClient(api_key="test-key", base_url="http://dify.test/v1")

    def test_allows_plain_http_for_loopback_base_url(self) -> None:
        client = DifyClient(api_key="test-key", base_url="http://127.0.0.1:8080/v1")

        self.assertEqual(client.base_url, "http://127.0.0.1:8080/v1")
