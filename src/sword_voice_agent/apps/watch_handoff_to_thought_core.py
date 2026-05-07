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
from sword_voice_agent.adapters.thought_core import (
    ThoughtCoreClient,
    ThoughtCoreClientError,
    ThoughtCoreStreamEvent,
)
from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.apps.send_handoff_to_thought_core import (
    build_result,
    format_event_line,
    validate_path_argument,
)
from sword_voice_agent.apps.thought_core_status import build_thought_core_status_writer


NO_SPEECH_PLACEHOLDER = "音声を認識できませんでした。"
THOUGHT_CORE_WATCHER_MODULE = "thought_core_watcher"
THOUGHT_CORE_WATCHER_LABEL = "thought-core watcher"


@dataclass(frozen=True)
class HandoffSignature:
    path: str
    size: int
    mtime_ns: int
    digest: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch ai_talk_core handoff output and send new turns to thought-core."
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
        help="Which handoff field to send as the thought-core turn text.",
    )
    parser.add_argument("--user", default=os.environ.get("THOUGHT_CORE_USER", "local-user"))
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
        default="",
        help="Optional explicit thought-core turn_id. Usually leave blank in watch mode.",
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
        "--output-json",
        default="",
        help=(
            "Path to write the latest thought-core result JSON. Defaults to "
            ".cache/codex/{source}_thought_core_latest.json."
        ),
    )
    parser.add_argument(
        "--output-text",
        default="",
        help=(
            "Path to write the latest thought-core response text. Defaults to "
            ".cache/codex/{source}_thought_core_latest.txt."
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
        help=f"Send the ai_talk_core no-speech placeholder instead of skipping it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the turn payload without calling thought-core.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print each request/result as JSON.",
    )
    parser.add_argument(
        "--print-events",
        action="store_true",
        help="Print compact event lines while streaming.",
    )
    parser.set_defaults(text="")
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


def run_once(
    args: argparse.Namespace,
    *,
    client: ThoughtCoreClient | None = None,
) -> dict[str, Any]:
    result = build_result(args)
    status_writer = build_thought_core_status_writer(
        args.status_dir,
        result,
        source="watch_handoff_to_thought_core",
    )
    text = str(result["turn_payload"].get("text") or "")
    if should_skip_text(text, args):
        result["skipped"] = True
        result["skip_reason"] = "no_speech_placeholder"
        save_result_outputs(args, result)
        if status_writer is not None:
            status_writer.finish(result)
        return result

    result["skipped"] = False
    if args.dry_run:
        save_result_outputs(args, result)
        return result

    events: list[dict[str, Any]] = []

    def on_event(event: ThoughtCoreStreamEvent) -> None:
        events.append(event.to_dict())
        if status_writer is not None:
            status_writer(event)
        if args.print_events:
            print(format_event_line(event), flush=True)

    thought_core = client or ThoughtCoreClient.from_env()
    response = thought_core.send_turn_streaming(result["turn_payload"], on_event=on_event)
    result["events"] = events
    result["response"] = response.to_dict()
    save_result_outputs(args, result)
    if status_writer is not None:
        status_writer.finish(result)
    return result


def should_skip_text(text: str, args: argparse.Namespace) -> bool:
    return not args.send_no_speech and text.strip() == NO_SPEECH_PLACEHOLDER


def save_result_outputs(
    args: argparse.Namespace,
    result: dict[str, Any],
) -> tuple[Path | None, Path | None]:
    json_path = resolve_output_json_path(args)
    text_path = resolve_output_text_path(args)

    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    response = result.get("response")
    response_text = ""
    if isinstance(response, dict):
        response_text = str(response.get("text", "") or "")

    if text_path is not None:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(response_text, encoding="utf-8")

    return json_path, text_path


def resolve_output_json_path(args: argparse.Namespace) -> Path | None:
    if args.output_json:
        return Path(args.output_json)
    cache_dir = resolve_default_cache_dir(args)
    if cache_dir is None:
        return None
    return cache_dir / f"{args.source}_thought_core_latest.json"


def resolve_output_text_path(args: argparse.Namespace) -> Path | None:
    if args.output_text:
        return Path(args.output_text)
    cache_dir = resolve_default_cache_dir(args)
    if cache_dir is None:
        return None
    return cache_dir / f"{args.source}_thought_core_latest.txt"


def resolve_default_cache_dir(args: argparse.Namespace) -> Path | None:
    if args.ai_talk_core_root:
        return Path(args.ai_talk_core_root) / ".cache" / "codex"
    if args.handoff_json:
        return Path(args.handoff_json).resolve().parent
    return None


def run_watch(args: argparse.Namespace) -> None:
    handoff_path = resolve_handoff_json_path(args)
    seen = handoff_signature(handoff_path) if args.skip_existing else None
    print(format_watch_start_message(handoff_path, skip_existing=args.skip_existing))
    while True:
        current = handoff_signature(handoff_path)
        write_watcher_module_status(
            args,
            "running",
            detail=watcher_module_detail(current, skip_existing=args.skip_existing),
        )
        if current is not None and current != seen:
            try:
                result = run_once(args)
            except (AiTalkCoreHandoffError, ThoughtCoreClientError, ValueError) as exc:
                print(f"[thought-core-watch] input error: {exc}")
                write_watcher_module_status(
                    args,
                    "error",
                    detail="last processing error",
                )
            else:
                seen = current
                write_watcher_module_status(
                    args,
                    "running",
                    detail=result_module_detail(result),
                )
                print_result(args, result)
        time.sleep(max(0.05, args.poll_interval_s))


def print_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("skipped"):
        print(f"[thought-core-watch] skipped: {result.get('skip_reason', 'unknown')}")
        return
    response = result.get("response")
    if isinstance(response, dict):
        text = str(response.get("text", "") or "")
        if text:
            print(text)


def format_watch_start_message(path: Path, *, skip_existing: bool) -> str:
    mode = "新規handoffのみ" if skip_existing else "現在のhandoffと新規handoff"
    return f"[thought-core-watch] 監視中: {path} ({mode})"


def watcher_module_detail(
    signature: HandoffSignature | None,
    *,
    skip_existing: bool,
) -> str:
    mode = "new handoffs only" if skip_existing else "current and new handoffs"
    availability = "handoff present" if signature is not None else "waiting for handoff"
    return f"{availability} / {mode}"


def result_module_detail(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return f"skipped {result.get('skip_reason', 'unknown')}"
    response = result.get("response")
    if isinstance(response, dict):
        raw = response.get("raw")
        if isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, dict) and data.get("status"):
                return f"last result {data.get('status')}"
        if response.get("text"):
            return "last result completed"
    return "last result processed"


def write_watcher_module_status(
    args: argparse.Namespace,
    state: str,
    *,
    detail: str,
) -> None:
    status_dir = str(getattr(args, "status_dir", "") or "")
    if not status_dir:
        return
    StatusStore(status_dir).write_module_status(
        THOUGHT_CORE_WATCHER_MODULE,
        state,
        label=THOUGHT_CORE_WATCHER_LABEL,
        detail=detail,
    )


def format_missing_handoff_message(path: Path) -> str:
    return (
        f"handoff JSON が見つかりません: {path}\n"
        "ai-talk-core側で handoff 保存を有効にして音声入力を処理するか、まずは手入力で "
        '次の確認を実行してください: uv run sword-thought-core-handoff --text "電気つけて" '
        "--session-id living_room_main --turn-id turn_manual_001 --print-events"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.once:
            handoff_path = resolve_handoff_json_path(args)
            if handoff_signature(handoff_path) is None:
                print(f"Input error: {format_missing_handoff_message(handoff_path)}")
                return 1
            result = run_once(args)
            print_result(args, result)
        else:
            run_watch(args)
    except (AiTalkCoreHandoffError, ThoughtCoreClientError, ValueError) as exc:
        print(f"Input error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
