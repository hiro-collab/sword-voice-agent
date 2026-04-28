from __future__ import annotations

import argparse
import json
import socket
import time

from sword_voice_agent.adapters.auth import resolve_auth_token
from sword_voice_agent.protocol.messages import GestureSignal, GestureState


def build_state(timestamp: float, active: bool, confidence: float) -> GestureState:
    return GestureState(
        source="demo",
        timestamp=timestamp,
        gestures={
            "sword_sign": GestureSignal(active=active, confidence=confidence),
            "victory": GestureSignal(active=False, confidence=0.0),
        },
    )


def demo_sequence() -> list[GestureState]:
    samples = [
        (0.0, False, 0.05),
        (0.1, True, 0.93),
        (0.2, True, 0.94),
        (0.4, True, 0.95),
        (0.7, True, 0.96),
        (0.8, False, 0.10),
        (1.1, False, 0.10),
        (1.4, False, 0.10),
    ]
    return [build_state(timestamp, active, confidence) for timestamp, active, confidence in samples]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--print-json", action="store_true")
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Optional UDP auth token. Defaults to SWORD_VOICE_AGENT_AUTH_TOKEN.",
    )
    args = parser.parse_args(argv)
    auth_token = resolve_auth_token(args.auth_token)

    address = (args.host, args.port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for state in demo_sequence():
            message = state.to_dict()
            if auth_token:
                message["auth_token"] = auth_token
            payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
            sock.sendto(payload, address)
            if args.print_json:
                print(state.to_json(), flush=True)
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
