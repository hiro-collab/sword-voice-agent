from __future__ import annotations

import argparse
import json

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreInputGateClient
from sword_voice_agent.adapters.gesture_udp import GestureUdpReceiver
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--gesture-name", default="sword_sign")
    parser.add_argument("--min-confidence", type=float, default=0.8)
    parser.add_argument("--activation-delay", type=float, default=0.3)
    parser.add_argument("--release-delay", type=float, default=0.5)
    parser.add_argument(
        "--input-gate-url",
        default=None,
        help="Optional ai_talk_core-compatible input gate endpoint.",
    )
    parser.add_argument("--input-gate-timeout", type=float, default=5.0)
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args(argv)

    gate = GestureInputGate(
        gesture_name=args.gesture_name,
        min_confidence=args.min_confidence,
        activation_delay_s=args.activation_delay,
        release_delay_s=args.release_delay,
    )
    sink = (
        AiTalkCoreInputGateClient(
            endpoint_url=args.input_gate_url,
            timeout_s=args.input_gate_timeout,
        )
        if args.input_gate_url
        else None
    )
    receiver = GestureUdpReceiver(
        args.host,
        args.port,
        gate,
        voice_state_sink=sink,
        turn_controller=VoiceTurnController(),
    )

    print(f"listening for GestureState UDP on {args.host}:{args.port}", flush=True)
    try:
        with receiver:
            while True:
                response, address = receiver.receive_once()
                if args.print_json:
                    print(
                        json.dumps(
                            {
                                "from": f"{address[0]}:{address[1]}",
                                "response": response,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

