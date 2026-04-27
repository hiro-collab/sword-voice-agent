from __future__ import annotations

import argparse
import json
import os
from typing import Any

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    load_handoff_from_root,
    load_handoff_json,
)
from sword_voice_agent.adapters.dify import DifyClient, DifyClientError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send the latest ai_talk_core handoff to Dify."
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
    parser.add_argument("--source", default="web")
    parser.add_argument(
        "--field",
        choices=("command", "transcript", "prompt"),
        default="command",
        help="Which handoff field to send as the Dify query.",
    )
    parser.add_argument("--user", default=os.environ.get("DIFY_USER", "local-user"))
    parser.add_argument("--conversation-id", default="")
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra Dify input context. Can be repeated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the AgentRequest without calling Dify.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print request and response as JSON.",
    )
    return parser


def load_handoff_from_args(args: argparse.Namespace):
    if args.handoff_json:
        return load_handoff_json(
            args.handoff_json,
            source=args.source,
            text_path=args.handoff_text or None,
        )
    if args.ai_talk_core_root:
        return load_handoff_from_root(args.ai_talk_core_root, source=args.source)
    raise AiTalkCoreHandoffError(
        "set --ai-talk-core-root, --handoff-json, AI_TALK_CORE_ROOT, "
        "or AI_TALK_CORE_HANDOFF_JSON"
    )


def parse_context_pairs(pairs: list[str]) -> dict[str, str]:
    context: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise AiTalkCoreHandoffError(
                f"context must be KEY=VALUE, got: {pair!r}"
            )
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise AiTalkCoreHandoffError("context key must not be empty")
        context[key] = value
    return context


def run(args: argparse.Namespace) -> dict[str, Any]:
    handoff = load_handoff_from_args(args)
    agent_request = handoff.to_agent_request(
        field=args.field,
        user=args.user,
        conversation_id=args.conversation_id or None,
        context=parse_context_pairs(args.context),
    )
    if args.dry_run:
        return {"request": agent_request.to_dict(), "response": None}

    response = DifyClient.from_env().send_chat_message(agent_request)
    return {
        "request": agent_request.to_dict(),
        "response": response.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (AiTalkCoreHandoffError, DifyClientError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 1

    if args.print_json or args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    response = result.get("response")
    if isinstance(response, dict):
        print(str(response.get("text", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
