from __future__ import annotations

import argparse

from sword_voice_agent.adapters.gesture_http import create_server
from sword_voice_agent.core.input_gate import GestureInputGate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--gesture-name", default="sword_sign")
    parser.add_argument("--min-confidence", type=float, default=0.8)
    parser.add_argument("--activation-delay", type=float, default=0.3)
    parser.add_argument("--release-delay", type=float, default=0.5)
    args = parser.parse_args(argv)

    gate = GestureInputGate(
        gesture_name=args.gesture_name,
        min_confidence=args.min_confidence,
        activation_delay_s=args.activation_delay,
        release_delay_s=args.release_delay,
    )
    server = create_server(args.host, args.port, gate)
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

