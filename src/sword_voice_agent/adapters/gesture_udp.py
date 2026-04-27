from __future__ import annotations

import json
import socket
from typing import Any, Mapping

from sword_voice_agent.adapters.gesture_gateway import (
    VoiceStateSink,
    build_gesture_response,
)
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController
from sword_voice_agent.protocol.messages import ProtocolError


def build_udp_gesture_response(
    datagram: bytes,
    gate: GestureInputGate,
    voice_state_sink: VoiceStateSink | None = None,
    turn_controller: VoiceTurnController | None = None,
) -> dict[str, Any]:
    payload = json.loads(datagram.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ProtocolError("UDP datagram must contain a JSON object")
    return build_gesture_response(
        payload,
        gate,
        voice_state_sink=voice_state_sink,
        turn_controller=turn_controller,
    )


class GestureUdpReceiver:
    def __init__(
        self,
        host: str,
        port: int,
        gate: GestureInputGate,
        voice_state_sink: VoiceStateSink | None = None,
        turn_controller: VoiceTurnController | None = None,
        buffer_size: int = 65535,
        sock: socket.socket | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.gate = gate
        self.voice_state_sink = voice_state_sink
        self.turn_controller = turn_controller
        self.buffer_size = buffer_size
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._owns_socket = sock is None
        self._bound = False

    def __enter__(self) -> "GestureUdpReceiver":
        self.bind()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def bind(self) -> None:
        if not self._bound:
            self.sock.bind((self.host, self.port))
            self._bound = True

    def close(self) -> None:
        if self._owns_socket:
            self.sock.close()

    def receive_once(self) -> tuple[dict[str, Any], tuple[str, int]]:
        data, address = self.sock.recvfrom(self.buffer_size)
        return (
            build_udp_gesture_response(
                data,
                self.gate,
                voice_state_sink=self.voice_state_sink,
                turn_controller=self.turn_controller,
            ),
            address,
        )

