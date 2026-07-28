from unittest import TestCase
from unittest.mock import MagicMock, patch
import json
from urllib import error

from sword_voice_agent.adapters.thought_core import (
    ROUTE_DEADLINE_HEADER,
    ThoughtCoreClient,
    ThoughtCoreClientError,
    build_turn_payload,
    iter_sse_json_payloads,
)
from sword_voice_agent.protocol.messages import AgentRequest


class ThoughtCoreClientTest(TestCase):
    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    def test_append_closed_loop_event_uses_fixed_endpoint_and_bounded_receipt(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "ok": True,
                "event_id": "evt_closed_loop_001",
                "journal_entry_id": "jrn_closed_loop_001",
                "ingest_offset": 7,
            }
        ).encode("utf-8")
        urlopen.return_value = response
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        result = client.append_closed_loop_event(
            {
                "event_kind": "output.dispatch_intent",
                "session_id": "session_001",
                "turn_id": "turn_001",
                "assistant_message_id": "msg_001",
                "details": {},
            }
        )

        self.assertEqual(result["ingest_offset"], 7)
        req = urlopen.call_args.args[0]
        self.assertEqual(
            req.full_url,
            "http://127.0.0.1:18787/feedback/closed-loop",
        )
        self.assertNotIn("event_id", json.loads(req.data.decode("utf-8")))

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    def test_append_closed_loop_event_does_not_echo_http_error_body(
        self,
        urlopen: MagicMock,
    ) -> None:
        marker = "PRIVATE_CLOSED_LOOP_RESPONSE"
        urlopen.side_effect = error.HTTPError(
            "http://127.0.0.1:18787/feedback/closed-loop",
            503,
            marker,
            {},
            MagicMock(read=lambda: marker.encode("utf-8")),
        )
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        with self.assertRaises(ThoughtCoreClientError) as raised:
            client.append_closed_loop_event({})

        self.assertNotIn(marker, str(raised.exception))

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

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    @patch("sword_voice_agent.adapters.thought_core.time.monotonic", return_value=100.0)
    def test_expired_deadline_refuses_open(
        self,
        _monotonic: MagicMock,
        urlopen: MagicMock,
    ) -> None:
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        with self.assertRaisesRegex(ThoughtCoreClientError, "deadline expired"):
            list(client.stream_turn({}, deadline_monotonic=100.0))

        urlopen.assert_not_called()

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    @patch("sword_voice_agent.adapters.thought_core.time.monotonic", return_value=100.0)
    def test_urlopen_uses_minimum_configured_and_remaining_timeout(
        self,
        _monotonic: MagicMock,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter([])
        response.__enter__.return_value = stream
        urlopen.return_value = response
        client = ThoughtCoreClient(
            base_url="http://127.0.0.1:18787",
            timeout_s=5.0,
        )

        self.assertEqual(
            list(client.stream_turn({}, deadline_monotonic=102.0)),
            [],
        )

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2.0)
        req = urlopen.call_args.args[0]
        request_headers = {
            name.lower(): value for name, value in req.header_items()
        }
        self.assertEqual(
            request_headers[ROUTE_DEADLINE_HEADER.lower()],
            "102.000000000",
        )
        self.assertEqual(json.loads(req.data.decode("utf-8")), {})

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    @patch("sword_voice_agent.adapters.thought_core.time.monotonic", return_value=100.0)
    def test_remote_deadline_is_rejected_before_request_creation(
        self,
        _monotonic: MagicMock,
        urlopen: MagicMock,
    ) -> None:
        client = ThoughtCoreClient(base_url="https://thought.example.test")

        with self.assertRaisesRegex(ThoughtCoreClientError, "same-host"):
            list(client.stream_turn({}, deadline_monotonic=102.0))

        urlopen.assert_not_called()

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    def test_remote_request_without_deadline_preserves_legacy_compatibility(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter([])
        response.__enter__.return_value = stream
        urlopen.return_value = response
        client = ThoughtCoreClient(base_url="https://thought.example.test")

        self.assertEqual(list(client.stream_turn({})), [])
        request_headers = {
            name.lower(): value
            for name, value in urlopen.call_args.args[0].header_items()
        }
        self.assertNotIn(ROUTE_DEADLINE_HEADER.lower(), request_headers)

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    @patch("sword_voice_agent.adapters.thought_core.time.monotonic")
    def test_event_at_deadline_is_rejected_before_callback(
        self,
        monotonic: MagicMock,
        urlopen: MagicMock,
    ) -> None:
        monotonic.side_effect = (100.0, 100.0, 100.0, 100.0, 100.0, 105.0)
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter(
            [
                b"event: assistant.message\n",
                b'data: {"type":"assistant.message","turn_id":"turn-1",'
                b'"session_id":"living","data":{"speech":"private"}}\n',
                b"\n",
            ]
        )
        response.__enter__.return_value = stream
        urlopen.return_value = response
        callback = MagicMock()
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        with self.assertRaisesRegex(ThoughtCoreClientError, "deadline expired"):
            client.send_turn_streaming(
                {},
                on_event=callback,
                deadline_monotonic=105.0,
            )

        callback.assert_not_called()

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    @patch("sword_voice_agent.adapters.thought_core.time.monotonic", return_value=100.0)
    def test_first_event_before_deadline_reaches_callback(
        self,
        _monotonic: MagicMock,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        stream = MagicMock()
        stream.__iter__.return_value = iter(
            [
                b"event: assistant.message\n",
                b'data: {"type":"assistant.message","turn_id":"turn-1",'
                b'"session_id":"living","data":{"speech":"public"}}\n',
                b"\n",
            ]
        )
        response.__enter__.return_value = stream
        urlopen.return_value = response
        callback = MagicMock()
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        result = client.send_turn_streaming(
            {},
            on_event=callback,
            deadline_monotonic=105.0,
        )

        self.assertEqual(result.text, "public")
        callback.assert_called_once()

    @patch("sword_voice_agent.adapters.thought_core.request.urlopen")
    def test_http_error_does_not_echo_response_body(self, urlopen: MagicMock) -> None:
        marker = "private-deadline-candidate-marker"
        urlopen.side_effect = error.HTTPError(
            "http://127.0.0.1:18787/turn",
            409,
            marker,
            {},
            MagicMock(read=lambda: marker.encode("utf-8")),
        )
        client = ThoughtCoreClient(base_url="http://127.0.0.1:18787")

        with self.assertRaises(ThoughtCoreClientError) as raised:
            list(client.stream_turn({}))

        self.assertNotIn(marker, str(raised.exception))

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

