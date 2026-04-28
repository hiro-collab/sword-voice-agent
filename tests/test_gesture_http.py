from unittest import TestCase
from urllib import error, request
import json
import threading

from sword_voice_agent.adapters.auth import AuthError, headers_authorized
from sword_voice_agent.adapters.gesture_http import build_gesture_response
from sword_voice_agent.adapters.gesture_http import create_server
from sword_voice_agent.adapters.gesture_http import parse_content_length
from sword_voice_agent.adapters.gesture_http import RequestBodyTooLarge
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.protocol.messages import ProtocolError
from sword_voice_agent.protocol.messages import VoiceState


class FakeVoiceStateSink:
    def __init__(self) -> None:
        self.voice_states: list[VoiceState] = []

    def send_voice_state(self, voice_state: VoiceState) -> dict[str, bool]:
        self.voice_states.append(voice_state)
        return {"ok": True}


class FailingVoiceStateSink:
    def send_voice_state(self, voice_state: VoiceState) -> dict[str, bool]:
        raise RuntimeError("internal path C:\\secret\\input-gate failed")


class GestureHttpTest(TestCase):
    def test_requires_auth_for_non_loopback_bind(self) -> None:
        with self.assertRaises(AuthError):
            create_server("0.0.0.0", 0, GestureInputGate())

    def test_accepts_bearer_or_header_token(self) -> None:
        self.assertTrue(
            headers_authorized({"Authorization": "Bearer secret"}, "secret")
        )
        self.assertTrue(
            headers_authorized({"X-Sword-Agent-Token": "secret"}, "secret")
        )
        self.assertFalse(
            headers_authorized({"Authorization": "Bearer wrong"}, "secret")
        )

    def test_rejects_oversized_content_length(self) -> None:
        with self.assertRaises(RequestBodyTooLarge):
            parse_content_length("9", max_body_bytes=8)

    def test_rejects_invalid_content_length(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_content_length("not-a-number", max_body_bytes=8)

    def test_server_returns_413_for_oversized_body(self) -> None:
        server = create_server(
            "127.0.0.1",
            0,
            GestureInputGate(),
            max_body_bytes=8,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(
                {
                    "type": "gesture_state",
                    "source": "test",
                    "timestamp": 10.0,
                    "gestures": {"sword_sign": {"active": True, "confidence": 0.95}},
                }
            ).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/gesture-state",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(req, timeout=2)
            self.assertEqual(caught.exception.code, 413)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_server_hides_upstream_error_details(self) -> None:
        server = create_server(
            "127.0.0.1",
            0,
            GestureInputGate(activation_delay_s=0.0),
            voice_state_sink=FailingVoiceStateSink(),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(
                {
                    "type": "gesture_state",
                    "source": "test",
                    "timestamp": 10.0,
                    "gestures": {"sword_sign": {"active": True, "confidence": 0.95}},
                }
            ).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{server.server_port}/gesture-state",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(req, timeout=2)
            body = caught.exception.read().decode("utf-8")
            self.assertEqual(caught.exception.code, 502)
            self.assertIn("upstream_error", body)
            self.assertNotIn("secret", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_builds_voice_state_response(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0, release_delay_s=0.5)

        response = build_gesture_response(
            {
                "type": "gesture_state",
                "source": "test",
                "timestamp": 10.0,
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.95,
                    }
                },
            },
            gate,
        )

        self.assertTrue(response["ok"])
        self.assertTrue(response["voice_state"]["mic_enabled"])
        self.assertEqual(response["voice_state"]["phase"], "armed")
        self.assertEqual(response["gate_decision"]["reason"], "activation_delay_passed")

    def test_low_confidence_does_not_enable_mic(self) -> None:
        gate = GestureInputGate(min_confidence=0.8, activation_delay_s=0.0)

        response = build_gesture_response(
            {
                "type": "gesture_state",
                "source": "test",
                "timestamp": 10.0,
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.2,
                    }
                },
            },
            gate,
        )

        self.assertFalse(response["voice_state"]["mic_enabled"])
        self.assertFalse(response["gate_decision"]["raw_active"])

    def test_forwards_voice_state_to_sink(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        sink = FakeVoiceStateSink()

        response = build_gesture_response(
            {
                "type": "gesture_state",
                "source": "test",
                "timestamp": 10.0,
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.95,
                    }
                },
            },
            gate,
            sink,
        )

        self.assertEqual(response["input_gate_response"], {"ok": True})
        self.assertEqual(len(sink.voice_states), 1)
        self.assertTrue(sink.voice_states[0].mic_enabled)

    def test_includes_voice_control_command_when_controller_is_provided(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        controller = VoiceTurnController(source="test")

        response = build_gesture_response(
            {
                "type": "gesture_state",
                "source": "test",
                "timestamp": 10.0,
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.95,
                    }
                },
            },
            gate,
            turn_controller=controller,
        )

        self.assertEqual(
            response["voice_control_command"]["action"],
            "start_recording",
        )
        self.assertEqual(response["voice_control_command"]["source"], "test")
