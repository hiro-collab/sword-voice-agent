from contextlib import contextmanager
from unittest import TestCase
import json
from pathlib import Path
import shutil
from uuid import uuid4

from sword_voice_agent.adapters.gesture_udp import build_udp_gesture_response
from sword_voice_agent.adapters.gesture_udp import GestureUdpReceiver
from sword_voice_agent.apps.gesture_udp_receiver import (
    format_debug_line,
    write_status_json,
)
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.protocol.messages import ProtocolError


class GestureUdpTest(TestCase):
    def test_builds_response_from_sword_sign_datagram(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        payload = {
            "type": "gesture_state",
            "timestamp": 10.0,
            "source": "mediapipe_sword_sign",
            "hand_detected": True,
            "primary": "sword_sign",
            "gestures": {
                "sword_sign": {
                    "active": True,
                    "confidence": 0.95,
                    "label": 0,
                },
                "victory": {
                    "active": False,
                    "confidence": 0.01,
                    "label": 1,
                },
            },
        }

        response = build_udp_gesture_response(
            json.dumps(payload).encode("utf-8"),
            gate,
            turn_controller=VoiceTurnController(),
        )

        self.assertTrue(response["voice_state"]["mic_enabled"])
        self.assertEqual(
            response["voice_control_command"]["action"],
            "start_recording",
        )

    def test_rejects_udp_datagram_without_required_token(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        payload = {
            "type": "gesture_state",
            "timestamp": 10.0,
            "source": "mediapipe_sword_sign",
            "gestures": {
                "sword_sign": {
                    "active": True,
                    "confidence": 0.95,
                }
            },
        }

        with self.assertRaises(ProtocolError):
            build_udp_gesture_response(
                json.dumps(payload).encode("utf-8"),
                gate,
                auth_token="secret",
            )

    def test_accepts_udp_datagram_with_required_token(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        payload = {
            "type": "gesture_state",
            "timestamp": 10.0,
            "source": "mediapipe_sword_sign",
            "auth_token": "secret",
            "gestures": {
                "sword_sign": {
                    "active": True,
                    "confidence": 0.95,
                }
            },
        }

        response = build_udp_gesture_response(
            json.dumps(payload).encode("utf-8"),
            gate,
            auth_token="secret",
        )

        self.assertTrue(response["voice_state"]["mic_enabled"])
        self.assertNotIn("secret", json.dumps(response, ensure_ascii=False))

    def test_accepts_gesture_status_as_diagnostic_datagram(self) -> None:
        gate = GestureInputGate(activation_delay_s=0.0)
        payload = {
            "type": "gesture_status",
            "source": "mediapipe_sword_sign",
            "status": "running",
            "frame_id": 42,
            "fps": 30.0,
            "hand_detected": True,
            "primary_gesture": "sword_sign",
            "sword_sign": {
                "active": True,
                "confidence": 0.95,
            },
        }

        response = build_udp_gesture_response(
            json.dumps(payload).encode("utf-8"),
            gate,
            turn_controller=VoiceTurnController(),
        )

        self.assertEqual(response["type"], "gesture_diagnostic_response")
        self.assertEqual(response["diagnostic"]["type"], "gesture_status")
        self.assertNotIn("voice_control_command", response)

    def test_formats_udp_receiver_debug_line(self) -> None:
        response = {
            "voice_state": {
                "phase": "armed",
                "mic_enabled": True,
            },
            "gate_decision": {
                "raw_active": True,
                "confidence": 0.9512,
                "changed": True,
                "reason": "activation_delay_passed",
            },
            "voice_control_command": {
                "action": "start_recording",
            },
            "input_gate_response": {
                "ok": True,
                "input_gate": {
                    "input_enabled": True,
                    "reason": "activation_delay_passed",
                },
            },
        }

        line = format_debug_line(response, ("127.0.0.1", 55218), sequence=3)

        self.assertIn("[gesture-udp]", line)
        self.assertIn("seq=3", line)
        self.assertIn("from=127.0.0.1:55218", line)
        self.assertIn("raw_active=1", line)
        self.assertIn("confidence=0.951", line)
        self.assertIn("mic_enabled=1", line)
        self.assertIn("changed=1", line)
        self.assertIn("reason=activation_delay_passed", line)
        self.assertIn("action=start_recording", line)
        self.assertIn("input_gate=ok", line)
        self.assertIn("input_gate_enabled=1", line)

    def test_formats_udp_receiver_diagnostic_debug_line(self) -> None:
        response = {
            "type": "gesture_diagnostic_response",
            "diagnostic": {
                "type": "gesture_heartbeat",
                "status": "sending",
                "frame_id": 9,
                "fps": 29.5,
            },
        }

        line = format_debug_line(response, ("127.0.0.1", 55218), sequence=3)

        self.assertIn("diagnostic=gesture_heartbeat", line)
        self.assertIn("status=sending", line)
        self.assertIn("frame=9", line)

    def test_writes_receiver_status_json(self) -> None:
        response = {
            "voice_state": {
                "phase": "armed",
                "mic_enabled": True,
            }
        }
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "status" / "gesture.json"

            write_status_json(path, response, ("127.0.0.1", 55218), sequence=4)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["type"], "gesture_receiver_status")
            self.assertEqual(payload["sequence"], 4)
            self.assertEqual(payload["from"], "127.0.0.1:55218")
            self.assertTrue(payload["response"]["voice_state"]["mic_enabled"])

    def test_receiver_configures_socket_timeout_for_responsive_shutdown(self) -> None:
        sock = FakeSocket()

        GestureUdpReceiver(
            "127.0.0.1",
            8765,
            GestureInputGate(),
            sock=sock,
            receive_timeout_s=0.25,
        )

        self.assertEqual(sock.timeout, 0.25)

    def test_receiver_can_disable_socket_timeout(self) -> None:
        sock = FakeSocket()

        GestureUdpReceiver(
            "127.0.0.1",
            8765,
            GestureInputGate(),
            sock=sock,
            receive_timeout_s=0,
        )

        self.assertIsNone(sock.timeout)

    def test_receiver_rate_limits_datagrams_by_source(self) -> None:
        payload = json.dumps(
            {
                "type": "gesture_state",
                "timestamp": 10.0,
                "source": "mediapipe_sword_sign",
                "gestures": {
                    "sword_sign": {
                        "active": True,
                        "confidence": 0.95,
                    }
                },
            }
        ).encode("utf-8")
        sock = FakeSocket(
            [
                (payload, ("127.0.0.1", 50100)),
                (payload, ("127.0.0.1", 50100)),
            ]
        )
        receiver = GestureUdpReceiver(
            "127.0.0.1",
            8765,
            GestureInputGate(activation_delay_s=0.0),
            sock=sock,
            rate_limit_per_minute=1,
        )

        response, _ = receiver.receive_once()
        self.assertTrue(response["voice_state"]["mic_enabled"])
        with self.assertRaises(ProtocolError):
            receiver.receive_once()


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


class FakeSocket:
    def __init__(
        self,
        datagrams: list[tuple[bytes, tuple[str, int]]] | None = None,
    ) -> None:
        self.timeout = "unset"
        self.datagrams = list(datagrams or [])

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def recvfrom(self, buffer_size: int) -> tuple[bytes, tuple[str, int]]:
        return self.datagrams.pop(0)
