from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable

from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.protocol.messages import GestureSignal, GestureState


def demo_states() -> Iterable[GestureState]:
    samples = [
        (0.0, False, 0.05),
        (0.1, True, 0.93),
        (0.2, True, 0.94),
        (0.4, True, 0.95),
        (0.6, True, 0.96),
        (0.7, False, 0.10),
        (0.9, False, 0.10),
        (1.3, False, 0.10),
    ]
    for timestamp, active, confidence in samples:
        yield GestureState(
            source="demo",
            timestamp=timestamp,
            gestures={
                "sword_sign": GestureSignal(active=active, confidence=confidence)
            },
        )


def states_from_stdin() -> Iterable[GestureState]:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        yield GestureState.from_dict(json.loads(line))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="run built-in gesture sequence")
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

    states = demo_states() if args.demo else states_from_stdin()
    for state in states:
        decision = gate.update(state)
        print(decision.to_voice_state().to_json(), flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

