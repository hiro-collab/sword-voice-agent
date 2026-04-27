from __future__ import annotations

import argparse

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreInputGateClient
from sword_voice_agent.adapters.gesture_http import create_server
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
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
    server = create_server(args.host, args.port, gate, sink, VoiceTurnController())
    print(f"listening on http://{args.host}:{args.port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
