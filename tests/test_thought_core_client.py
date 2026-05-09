from unittest import TestCase
from unittest.mock import MagicMock, patch
import json

from sword_voice_agent.adapters.thought_core import (
    ThoughtCoreClient,
    ThoughtCoreClientError,
    build_turn_payload,
    iter_sse_json_payloads,
)
from sword_voice_agent.protocol.messages import AgentRequest


class ThoughtCoreClientTest(TestCase):
    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    def test_send_turn_streaming_aggregates_assistant_messages(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter(
            [
                b"event: assistant.speech_delta\n",
                (
                    'data: {"schema_version":"thought-core.event.v0","event_id":"evt-1",'
                    '"turn_id":"turn-1","session_id":"living","seq":1,'
                    '"type":"assistant.speech_delta","data":{"delta":"了解"}}\n'
                ).encode("utf-8"),
                b"\n",
                b"event: assistant.message\n",
                (
                    'data: {"schema_version":"thought-core.event.v0","event_id":"evt-2",'
                    '"turn_id":"turn-1","session_id":"living","seq":2,'
                    '"type":"assistant.message","data":{"speech":"了解、電気をつけるね。"}}\n'
                ).encode("utf-8"),
                b"\n",
                b"event: turn.completed\n",
                (
                    'data: {"schema_version":"thought-core.event.v0","event_id":"evt-3",'
                    '"turn_id":"turn-1","session_id":"living","seq":3,'
                    '"type":"turn.completed","data":{"status":"success"}}\n'
                ).encode("utf-8"),
                b"\n",
            ]
        )
        response.__enter__.return_value = stream
        urlopen.return_value = response
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")
        events = []

        result = client.send_turn_streaming(
            {
                "text": "電気つけて",
                "turn_id": "turn-1",
                "session_id": "living",
                "locale": "ja-JP",
                "context_refs": {},
            },
            on_event=events.append,
        )

        self.assertEqual(result.text, "了解、電気をつけるね。")
        self.assertEqual(result.conversation_id, "turn-1")
        self.assertEqual(events[0].event_type, "assistant.speech_delta")
        self.assertEqual(events[0].speech_delta, "了解")
        self.assertEqual(events[-1].event_type, "turn.completed")
        self.assertEqual(result.raw["_streaming"]["event_count"], 3)
        request_arg = urlopen.call_args.args[0]
        self.assertEqual(request_arg.full_url, "http://127.0.0.1:18787/turn?stream=true")
        self.assertEqual(request_arg.headers["Accept"], "text/event-stream")
        sent = json.loads(request_arg.data.decode("utf-8"))
        self.assertEqual(sent["turn_id"], "turn-1")

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    def test_turn_error_event_raises_client_error(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter(
            [
                b"event: turn.error\n",
                b'data: {"type":"turn.error","turn_id":"turn-1","session_id":"living",'
                b'"data":{"code":"bad_tool","message":"failed"}}\n',
                b"\n",
            ]
        )
        response.__enter__.return_value = stream
        urlopen.return_value = response
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        with self.assertRaises(ThoughtCoreClientError):
            client.send_turn_streaming(
                {
                    "text": "電気つけて",
                    "turn_id": "turn-1",
                    "session_id": "living",
                    "locale": "ja-JP",
                    "context_refs": {},
                }
            )

    def test_build_turn_payload_maps_agent_request_context(self) -> None:
        payload = build_turn_payload(
            AgentRequest(
                text="電気つけて",
                user="living-user",
                timestamp=123.456,
                context={
                    "turn_id": "turn-context",
                    "session_id": "living-room",
                    "locale": "ja-JP",
                    "context_refs": {"voice_turn": "voice-1"},
                },
                conversation_id="conv-1",
            )
        )

        self.assertEqual(payload["text"], "電気つけて")
        self.assertEqual(payload["turn_id"], "turn-context")
        self.assertEqual(payload["session_id"], "living-room")
        self.assertEqual(payload["locale"], "ja-JP")
        self.assertEqual(payload["context_refs"]["voice_turn"], "voice-1")
        self.assertEqual(payload["context_refs"]["conversation_id"], "conv-1")

    def test_build_turn_payload_rejects_non_object_context_refs(self) -> None:
        with self.assertRaises(ValueError):
            build_turn_payload(
                AgentRequest(
                    text="電気つけて",
                    context={"context_refs": "bad"},
                )
            )

    def test_rejects_plain_http_for_non_loopback_base_url(self) -> None:
        with self.assertRaises(ValueError):
            ThoughtCoreClient(base_url="http://thought-core.test")

    def test_allows_plain_http_for_loopback_base_url(self) -> None:
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        self.assertEqual(client.base_url, "http://127.0.0.1:18787")

    def test_sse_parser_uses_event_field_when_data_lacks_type(self) -> None:
        payloads = list(
            iter_sse_json_payloads(
                [
                    b"event: assistant.message\n",
                    b'data: {"turn_id":"turn-1","session_id":"living","data":{"speech":"ok"}}\n',
                    b"\n",
                ]
            )
        )

        self.assertEqual(payloads[0]["type"], "assistant.message")

