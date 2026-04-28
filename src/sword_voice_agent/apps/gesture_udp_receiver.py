from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreInputGateClient
from sword_voice_agent.adapters.gesture_udp import GestureUdpReceiver
from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.core.turn_controller import VoiceTurnController


def format_debug_line(
    response: Mapping[str, Any],
    address: tuple[str, int],
    *,
    sequence: int,
) -> str:
    decision = _mapping(response.get("gate_decision"))
    voice_state = _mapping(response.get("voice_state"))
    command = _mapping(response.get("voice_control_command"))
    input_gate = _mapping(response.get("input_gate_response"))
    input_gate_state = _mapping(input_gate.get("input_gate"))
    input_gate_status = (
        "not_configured"
        if not input_gate
        else "ok" if input_gate.get("ok") else "error"
    )
    return (
        "[gesture-udp] "
        f"seq={sequence} "
        f"from={address[0]}:{address[1]} "
        f"raw_active={_bool_label(decision.get('raw_active'))} "
        f"confidence={_float_value(decision.get('confidence')):.3f} "
        f"mic_enabled={_bool_label(voice_state.get('mic_enabled'))} "
        f"changed={_bool_label(decision.get('changed'))} "
        f"reason={decision.get('reason', '')} "
        f"phase={voice_state.get('phase', '')} "
        f"action={command.get('action', 'none')} "
        f"input_gate={input_gate_status}"
        f"{_input_gate_suffix(input_gate_state)}"
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bool_label(value: object) -> str:
    return "1" if bool(value) else "0"


def _float_value(value: object) -> float:
    if value is None:
        return 0.0
    return float(value)


def _input_gate_suffix(input_gate_state: Mapping[str, Any]) -> str:
    if not input_gate_state:
        return ""
    return (
        f" input_gate_enabled={_bool_label(input_gate_state.get('input_enabled'))}"
        f" input_gate_reason={input_gate_state.get('reason', '')}"
    )


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
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print one-line receive/gate/forwarding diagnostics.",
    )
    parser.add_argument(
        "--debug-every",
        type=int,
        default=1,
        help="Print every N received datagrams when --debug is enabled.",
    )
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
    sequence = 0
    try:
        with receiver:
            while True:
                response, address = receiver.receive_once()
                sequence += 1
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
                if args.debug and sequence % max(1, args.debug_every) == 0:
                    print(
                        format_debug_line(response, address, sequence=sequence),
                        flush=True,
                    )
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
