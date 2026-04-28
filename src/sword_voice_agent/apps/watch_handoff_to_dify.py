from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    get_handoff_json_path,
)
from sword_voice_agent.adapters.dify import DifyClient, DifyClientError
from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.apps.send_handoff_to_dify import (
    load_handoff_from_args,
    parse_context_pairs,
    validate_path_argument,
)


NO_SPEECH_PLACEHOLDER = "音声を認識できませんでした。"


@dataclass(frozen=True)
class HandoffSignature:
    path: str
    size: int
    mtime_ns: int
    digest: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch ai_talk_core handoff output and send new turns to Dify."
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
        "--conversation-id-file",
        default="",
        help=(
            "Path used to persist Dify conversation_id. Defaults to "
            ".cache/codex/{source}_dify_conversation_id.txt."
        ),
    )
    parser.add_argument(
        "--no-conversation-state",
        action="store_true",
        help="Do not read or persist a Dify conversation_id between turns.",
    )
    parser.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra Dify input context. Can be repeated.",
    )
    parser.add_argument(
        "--include-transcript-context",
        action="store_true",
        help="Include the raw transcript in Dify inputs/context.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help=(
            "Path to write the latest Dify handoff result JSON. Defaults to "
            ".cache/codex/{source}_dify_latest.json."
        ),
    )
    parser.add_argument(
        "--output-text",
        default="",
        help=(
            "Path to write the latest Dify answer text. Defaults to "
            ".cache/codex/{source}_dify_latest.txt."
        ),
    )
    parser.add_argument(
        "--status-dir",
        default=".cache/sword_voice_agent",
        help="Directory for latest status snapshots and events.jsonl.",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=0.5,
        help="Polling interval for watch mode.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process the current handoff once and exit.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="In watch mode, ignore the handoff that already exists on startup.",
    )
    parser.add_argument(
        "--send-no-speech",
        action="store_true",
        help=f"Send the ai_talk_core no-speech placeholder to Dify instead of skipping it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the AgentRequest without calling Dify.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print each request/result as JSON.",
    )
    return parser


def handoff_signature(path: str | Path) -> HandoffSignature | None:
    resolved = Path(path)
    try:
        stat = resolved.stat()
        data = resolved.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AiTalkCoreHandoffError(
            f"cannot read handoff JSON path: {resolved} ({exc})"
        ) from exc

    return HandoffSignature(
        path=str(resolved.resolve()),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        digest=hashlib.sha256(data).hexdigest(),
    )


def resolve_handoff_json_path(args: argparse.Namespace) -> Path:
    if args.handoff_json:
        validate_path_argument(args.handoff_json, "--handoff-json")
        return Path(args.handoff_json)
    if args.ai_talk_core_root:
        validate_path_argument(args.ai_talk_core_root, "--ai-talk-core-root")
        return get_handoff_json_path(args.ai_talk_core_root, args.source)
    raise AiTalkCoreHandoffError(
        "set --ai-talk-core-root, --handoff-json, AI_TALK_CORE_ROOT, "
        "or AI_TALK_CORE_HANDOFF_JSON"
    )


def process_handoff(
    args: argparse.Namespace,
    *,
    client: DifyClient | None = None,
) -> dict[str, Any]:
    handoff = load_handoff_from_args(args)
    conversation_id = resolve_conversation_id(args)
    context = parse_context_pairs(args.context)
    agent_request = handoff.to_agent_request(
        field=args.field,
        user=args.user,
        conversation_id=conversation_id,
        context=context,
        include_transcript_context=args.include_transcript_context,
    )

    result: dict[str, Any] = {
        "type": "dify_handoff_result",
        "handoff": {
            "json_path": str(handoff.json_path) if handoff.json_path else None,
            "text_path": str(handoff.text_path) if handoff.text_path else None,
            "source": handoff.source,
            "field": args.field,
        },
        "request": agent_request.to_dict(),
        "response": None,
        "skipped": False,
    }

    if should_skip_request(agent_request.text, args):
        result["skipped"] = True
        result["skip_reason"] = "no_speech_placeholder"
        return result

    if args.dry_run:
        return result

    dify_client = client or DifyClient.from_env()
    response = dify_client.send_chat_message(agent_request)
    result["response"] = response.to_dict()
    return result


def should_skip_request(text: str, args: argparse.Namespace) -> bool:
    return not args.send_no_speech and text.strip() == NO_SPEECH_PLACEHOLDER


def load_latest_turn_id(status_dir: str | Path) -> str | None:
    path = StatusStore(status_dir).latest_voice_turn_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("turn_id")
    if value is None:
        command = payload.get("voice_control_command")
        if isinstance(command, dict):
            value = command.get("turn_id")
    text = str(value).strip() if value is not None else ""
    return text or None


def resolve_conversation_id(args: argparse.Namespace) -> str | None:
    if args.conversation_id:
        return args.conversation_id
    if args.no_conversation_state:
        return None

    path = resolve_conversation_id_path(args)
    if path is None or not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def save_result_outputs(
    args: argparse.Namespace,
    result: dict[str, Any],
) -> tuple[Path | None, Path | None, Path | None]:
    json_path = resolve_output_json_path(args)
    text_path = resolve_output_text_path(args)
    conversation_id_path = resolve_conversation_id_path(args)

    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    response = result.get("response")
    response_text = ""
    conversation_id = ""
    if isinstance(response, dict):
        response_text = str(response.get("text", ""))
        conversation_id = str(response.get("conversation_id", "") or "")

    if text_path is not None:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(response_text, encoding="utf-8")

    if (
        conversation_id
        and conversation_id_path is not None
        and not args.no_conversation_state
    ):
        conversation_id_path.parent.mkdir(parents=True, exist_ok=True)
        conversation_id_path.write_text(conversation_id, encoding="utf-8")

    return json_path, text_path, conversation_id_path


def resolve_output_json_path(args: argparse.Namespace) -> Path | None:
    if args.output_json:
        return Path(args.output_json)
    cache_dir = resolve_default_cache_dir(args)
    if cache_dir is None:
        return None
    return cache_dir / f"{args.source}_dify_latest.json"


def resolve_output_text_path(args: argparse.Namespace) -> Path | None:
    if args.output_text:
        return Path(args.output_text)
    cache_dir = resolve_default_cache_dir(args)
    if cache_dir is None:
        return None
    return cache_dir / f"{args.source}_dify_latest.txt"


def resolve_conversation_id_path(args: argparse.Namespace) -> Path | None:
    if args.conversation_id_file:
        return Path(args.conversation_id_file)
    cache_dir = resolve_default_cache_dir(args)
    if cache_dir is None:
        return None
    return cache_dir / f"{args.source}_dify_conversation_id.txt"


def resolve_default_cache_dir(args: argparse.Namespace) -> Path | None:
    if args.ai_talk_core_root:
        return Path(args.ai_talk_core_root) / ".cache" / "codex"
    if args.handoff_json:
        return Path(args.handoff_json).resolve().parent
    return None


def run_once(
    args: argparse.Namespace,
    *,
    client: DifyClient | None = None,
) -> dict[str, Any]:
    result = process_handoff(args, client=client)
    save_result_outputs(args, result)
    if args.status_dir:
        StatusStore(args.status_dir).write_latest_dify_response(
            result,
            turn_id=load_latest_turn_id(args.status_dir),
        )
    return result


def run_watch(args: argparse.Namespace) -> None:
    handoff_path = resolve_handoff_json_path(args)
    seen = handoff_signature(handoff_path) if args.skip_existing else None

    while True:
        current = handoff_signature(handoff_path)
        if current is not None and current != seen:
            try:
                result = run_once(args)
            except (AiTalkCoreHandoffError, DifyClientError, ValueError) as exc:
                print(f"[dify-watch] input error: {exc}")
            else:
                seen = current
                print_result(args, result)
        time.sleep(args.poll_interval_s)


def print_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.print_json or args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if result.get("skipped"):
        print(f"[dify-watch] skipped: {result.get('skip_reason', 'unknown')}")
        return

    response = result.get("response")
    if isinstance(response, dict):
        print(str(response.get("text", "")))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.once:
            result = run_once(args)
            print_result(args, result)
            return 0
        run_watch(args)
    except KeyboardInterrupt:
        return 130
    except (AiTalkCoreHandoffError, DifyClientError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
