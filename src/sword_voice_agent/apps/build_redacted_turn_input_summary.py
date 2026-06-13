from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    load_handoff_from_root,
    load_handoff_json,
)
from sword_voice_agent.adapters.redacted_turn_input import (
    RedactedTurnInputError,
    build_redacted_turn_input_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a raw-free redacted_turn_input.v0 summary."
    )
    parser.add_argument(
        "--ai-talk-core-root",
        default=os.environ.get("AI_TALK_CORE_ROOT", ""),
        help="Path to the ai-talk-core repository root.",
    )
    parser.add_argument(
        "--handoff-json",
        default=os.environ.get("AI_TALK_CORE_HANDOFF_JSON", ""),
        help="Path to a saved ai-talk-core handoff JSON file.",
    )
    parser.add_argument(
        "--handoff-text",
        default=os.environ.get("AI_TALK_CORE_HANDOFF_TEXT", ""),
        help="Optional path to a saved ai-talk-core handoff prompt text file.",
    )
    parser.add_argument("--source", default="web")
    parser.add_argument(
        "--thought-core-result-json",
        default="",
        help="Optional JSON result produced by a handoff-to-Thought-Core helper.",
    )
    parser.add_argument(
        "--turn-id",
        default="",
        help="Redacted-safe Thought Core turn id/ref when no result JSON is supplied.",
    )
    parser.add_argument(
        "--completion-seen",
        action="store_true",
        help="Assert that a Thought Core completion ref was observed.",
    )
    parser.add_argument(
        "--completion-event-ref",
        default="",
        help="Redacted-safe completion event ref, e.g. thought:turn_x_completed.",
    )
    parser.add_argument("--proof-layer", choices=("source-static", "source-no-live"), default="source-no-live")
    parser.add_argument(
        "--source-label",
        choices=("ai_talk_core_web", "manual_text", "gesture_voice_bridge", "test_fixture"),
        default="ai_talk_core_web",
    )
    parser.add_argument(
        "--source-modality",
        choices=("speech", "text", "gesture_release", "mixed", "unknown"),
        default="speech",
    )
    parser.add_argument("--input-disabled", action="store_true")
    parser.add_argument("--mic-disabled", action="store_true")
    parser.add_argument("--input-gate-reason", default="source_no_live_summary")
    parser.add_argument("--input-gate-source", default="redacted_turn_input_helper")
    parser.add_argument("--gate-event-ref", default="")
    parser.add_argument("--stt-event-ref", default="")
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write the redacted summary JSON.",
    )
    return parser


def load_handoff_from_args(args: argparse.Namespace):
    if args.handoff_json:
        validate_path_argument(args.handoff_json, "--handoff-json")
        return load_handoff_json(
            args.handoff_json,
            source=args.source,
            text_path=validated_optional_path(args.handoff_text, "--handoff-text"),
        )
    if args.ai_talk_core_root:
        validate_path_argument(args.ai_talk_core_root, "--ai-talk-core-root")
        return load_handoff_from_root(args.ai_talk_core_root, source=args.source)
    raise RedactedTurnInputError(
        "set --ai-talk-core-root, --handoff-json, AI_TALK_CORE_ROOT, "
        "or AI_TALK_CORE_HANDOFF_JSON"
    )


def validated_optional_path(value: str, label: str) -> str | None:
    if not value:
        return None
    validate_path_argument(value, label)
    return value


def validate_path_argument(value: str, label: str) -> None:
    if "<" in value or ">" in value:
        raise RedactedTurnInputError(f"{label} still contains a placeholder")


def load_json_file(value: str, label: str) -> dict[str, Any] | None:
    if not value:
        return None
    validate_path_argument(value, label)
    path = Path(value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RedactedTurnInputError(f"{label} was not found") from exc
    except OSError as exc:
        raise RedactedTurnInputError(f"{label} could not be read") from exc
    except json.JSONDecodeError as exc:
        raise RedactedTurnInputError(f"{label} must be JSON") from exc
    if not isinstance(payload, dict):
        raise RedactedTurnInputError(f"{label} must contain a JSON object")
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    handoff = load_handoff_from_args(args)
    result = load_json_file(args.thought_core_result_json, "--thought-core-result-json")
    summary = build_redacted_turn_input_summary(
        handoff,
        thought_core_result=result,
        proof_layer=args.proof_layer,
        source_label=args.source_label,
        source_modality=args.source_modality,
        input_enabled=not args.input_disabled,
        mic_enabled=not args.mic_disabled,
        input_gate_reason=args.input_gate_reason,
        input_gate_source=args.input_gate_source,
        gate_event_ref=args.gate_event_ref or None,
        stt_event_ref=args.stt_event_ref or None,
        turn_id=args.turn_id or None,
        completion_seen=True if args.completion_seen else None,
        completion_event_ref=args.completion_event_ref or None,
    )
    if args.output_json:
        validate_path_argument(args.output_json, "--output-json")
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (AiTalkCoreHandoffError, RedactedTurnInputError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
