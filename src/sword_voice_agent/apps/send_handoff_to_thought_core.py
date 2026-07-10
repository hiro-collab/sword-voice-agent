from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    build_accepted_user_speech_turn_envelope,
    load_accepted_user_speech_candidate_json,
    load_handoff_from_root,
    load_handoff_json,
    load_private_turn_json,
)
from sword_voice_agent.adapters.thought_core import (
    ThoughtCoreClient,
    ThoughtCoreClientError,
    ThoughtCoreStreamEvent,
    build_turn_payload,
)
from sword_voice_agent.apps.thought_core_status import build_thought_core_status_writer
from sword_voice_agent.protocol.messages import AgentRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send the latest ai_talk_core handoff to thought-core."
    )
    parser.add_argument(
        "--ai-talk-core-root",
        default=os.environ.get("AI_TALK_CORE_ROOT", ""),
        help="Path to the ai_talk_core repository root.",
    )
    parser.add_argument(
        "--handoff-json",
        default=os.environ.get("AI_TALK_CORE_HANDOFF_JSON", ""),
        help="Path to a saved ai_talk_core handoff JSON file.",
    )
    parser.add_argument(
        "--handoff-text",
        default=os.environ.get("AI_TALK_CORE_HANDOFF_TEXT", ""),
        help="Optional path to a saved ai_talk_core handoff prompt text file.",
    )
    parser.add_argument(
        "--accepted-speech-candidate-json",
        default=os.environ.get("ACCEPTED_USER_SPEECH_CANDIDATE_JSON", ""),
        help="Path to a canonical accepted-user-speech candidate JSON file.",
    )
    parser.add_argument(
        "--private-turn-json",
        default=os.environ.get("THOUGHT_CORE_PRIVATE_TURN_JSON", ""),
        help="Path to the separate private Thought Core turn JSON file.",
    )
    parser.add_argument("--source", default="web")
    parser.add_argument(
        "--text",
        default=os.environ.get("THOUGHT_CORE_TEXT", ""),
        help="Send this text directly instead of loading an ai_talk_core handoff.",
    )
    parser.add_argument(
        "--field",
        choices=("command", "transcript", "prompt"),
        default="command",
        help="Which handoff field to send as the thought-core turn text.",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("THOUGHT_CORE_USER", "local-user"),
        help="User value used when building the intermediate AgentRequest.",
    )
    parser.add_argument(
        "--session-id",
        default=os.environ.get("THOUGHT_CORE_SESSION_ID", ""),
        help="thought-core session_id. Defaults to AgentRequest user.",
    )
    parser.add_argument(
        "--locale",
        default=os.environ.get("THOUGHT_CORE_LOCALE", "ja-JP"),
        help="thought-core locale.",
    )
    parser.add_argument(
        "--turn-id",
        default=os.environ.get("THOUGHT_CORE_TURN_ID", ""),
        help="Optional explicit thought-core turn_id.",
    )
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra AgentRequest context. Can be repeated.",
    )
    parser.add_argument(
        "--context-ref",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra thought-core context_refs entry. Can be repeated.",
    )
    parser.add_argument(
        "--include-transcript-context",
        action="store_true",
        help="Include the raw transcript in AgentRequest context.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the AgentRequest and turn payload without calling thought-core.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print request, turn payload, response, and events as JSON.",
    )
    parser.add_argument(
        "--print-events",
        action="store_true",
        help="Print compact event lines while streaming.",
    )
    parser.add_argument(
        "--status-dir",
        default=".cache/sword_voice_agent",
        help="Directory for latest status snapshots and events.jsonl.",
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
    raise AiTalkCoreHandoffError(
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
        raise AiTalkCoreHandoffError(
            f"{label} still contains a placeholder: {value!r}. "
            "Replace placeholders with an actual local path."
        )


def parse_key_value_pairs(pairs: list[str], *, label: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise AiTalkCoreHandoffError(f"{label} must be KEY=VALUE, got: {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise AiTalkCoreHandoffError(f"{label} key must not be empty")
        values[key] = value
    return values


def build_result(args: argparse.Namespace) -> dict[str, Any]:
    if args.accepted_speech_candidate_json or args.private_turn_json:
        return build_accepted_speech_candidate_result(args)

    context = parse_key_value_pairs(args.context, label="context")
    context_refs = parse_key_value_pairs(args.context_ref, label="context-ref")
    if context_refs:
        existing_refs = context.get("context_refs")
        if existing_refs is not None:
            raise AiTalkCoreHandoffError(
                "context_refs cannot be set through --context; use --context-ref"
            )
        context["context_refs"] = context_refs
    if args.turn_id:
        context["turn_id"] = args.turn_id
    if args.session_id:
        context["session_id"] = args.session_id
    if args.locale:
        context["locale"] = args.locale

    if args.text:
        context.setdefault("source", "manual")
        context.setdefault("trigger", "manual")
        agent_request = AgentRequest(
            text=args.text,
            user=args.user,
            context=context,
        )
    else:
        handoff = load_handoff_from_args(args)
        agent_request = handoff.to_agent_request(
            field=args.field,
            user=args.user,
            context=context,
            include_transcript_context=args.include_transcript_context,
        )
    turn_payload = build_turn_payload(
        agent_request,
        session_id=args.session_id or None,
        locale=args.locale or None,
        turn_id=args.turn_id or None,
    )
    return {
        "request": agent_request.to_dict(),
        "turn_payload": turn_payload,
        "events": [],
        "response": None,
    }


def build_accepted_speech_candidate_result(args: argparse.Namespace) -> dict[str, Any]:
    if not args.accepted_speech_candidate_json or not args.private_turn_json:
        raise AiTalkCoreHandoffError(
            "accepted speech candidate and private turn JSON are both required"
        )
    validate_path_argument(
        args.accepted_speech_candidate_json,
        "--accepted-speech-candidate-json",
    )
    validate_path_argument(args.private_turn_json, "--private-turn-json")
    candidate = load_accepted_user_speech_candidate_json(
        args.accepted_speech_candidate_json
    )
    private_turn = load_private_turn_json(args.private_turn_json)
    return {
        "request": accepted_speech_request_summary(candidate, private_turn),
        "turn_payload": build_accepted_user_speech_turn_envelope(
            candidate,
            private_turn,
        ),
        **accepted_speech_source_static_rows(),
        "events": [],
        "response": None,
    }


def accepted_speech_request_summary(
    candidate: Mapping[str, Any],
    private_turn: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source": "ai_talk_core",
        "handoff_field": "accepted_user_speech_candidate_input_gate",
        "candidate_id": candidate.get("candidate_id"),
        "turn_id": private_turn.get("turn_id"),
        "session_id": private_turn.get("session_id"),
    }


def accepted_speech_source_static_rows() -> dict[str, Any]:
    return {
        "input_gate_class": (
            "contract_declared_accepted_user_speech_candidate_not_runtime_observed"
        ),
        "thought_core_turninput_count": None,
        "turn_materialization_class": "not_observed_source_static",
        "assistant_response_class": "not_observed_source_static",
    }


def output_safe_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project accepted-speech envelopes without publishing their private payload."""
    turn_payload = result.get("turn_payload")
    if not isinstance(turn_payload, Mapping) or not {
        "accepted_user_speech_candidate",
        "private_turn",
    }.issubset(turn_payload):
        return dict(result)

    projection: dict[str, Any] = {
        "accepted_speech_candidate_present": True,
        "private_turn_present": True,
        **accepted_speech_source_static_rows(),
    }
    for key in ("skipped", "skip_reason"):
        if key in result:
            projection[key] = result[key]
    return projection


def run(args: argparse.Namespace) -> dict[str, Any]:
    result = build_result(args)
    if args.dry_run:
        return result

    events: list[dict[str, Any]] = []
    status_writer = build_thought_core_status_writer(
        args.status_dir,
        result,
        source="send_handoff_to_thought_core",
    )

    def on_event(event: ThoughtCoreStreamEvent) -> None:
        events.append(event.to_dict())
        if status_writer is not None:
            status_writer(event)
        if args.print_events:
            print(format_event_line(event), flush=True)

    client = ThoughtCoreClient.from_env()
    response = client.send_turn_streaming(result["turn_payload"], on_event=on_event)
    result["events"] = events
    result["response"] = response.to_dict()
    if status_writer is not None:
        status_writer.finish(output_safe_result(result))
    return result


def format_event_line(event: ThoughtCoreStreamEvent) -> str:
    prefix = f"{event.seq or '-'} {event.event_type}"
    if event.is_speech_delta and event.speech_delta:
        return f"{prefix}: {event.speech_delta}"
    if event.is_message and event.speech:
        return f"{prefix}: {event.speech}"
    tool = event.data.get("tool")
    if isinstance(tool, str) and tool:
        return f"{prefix}: {tool}"
    status = event.data.get("status")
    if isinstance(status, str) and status:
        return f"{prefix}: {status}"
    return prefix


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (AiTalkCoreHandoffError, ThoughtCoreClientError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 1

    if args.print_json or args.dry_run:
        print(json.dumps(output_safe_result(result), ensure_ascii=False, indent=2))
        return 0

    response = result.get("response")
    if isinstance(response, dict):
        print(str(response.get("text", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
