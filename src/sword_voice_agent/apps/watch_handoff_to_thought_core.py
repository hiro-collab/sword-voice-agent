from __future__ import annotations

import argparse
import queue
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable
from urllib import error, request

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    get_handoff_json_path,
)
from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.adapters.thought_core import (
    ThoughtCoreClient,
    ThoughtCoreClientError,
    ThoughtCoreStreamEvent,
)
from sword_voice_agent.adapters.status_store import StatusStore, redacted_text
from sword_voice_agent.apps.send_handoff_to_thought_core import (
    build_result,
    format_event_line,
    validate_path_argument,
)
from sword_voice_agent.apps.thought_core_status import build_thought_core_status_writer


NO_SPEECH_PLACEHOLDER = "音声を認識できませんでした。"
THOUGHT_CORE_WATCHER_MODULE = "thought_core_watcher"
THOUGHT_CORE_WATCHER_LABEL = "thought-core watcher"
LOCAL_ACK_MODES = {"auto", "off"}
SPEECH_END_CHARS = "。．.!?！？\n"
SPEECH_SOFT_BREAK_CHARS = "、,， "


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
        "--tts-chunk-url",
        default=os.environ.get("TTS_HTTP_CHUNK_URL", ""),
        help="Optional tts-service /api/tts/chunk URL for thought-core speech deltas.",
    )
    parser.add_argument(
        "--tts-http-timeout-s",
        type=float,
        default=default_tts_http_timeout_s(),
        help="Timeout for each local TTS chunk POST.",
    )
    parser.add_argument(
        "--aituber-message-url",
        default=os.environ.get("AITUBER_MESSAGE_URL", ""),
        help="Optional AITuberKit /api/messages URL for assistant.message output.",
    )
    parser.add_argument(
        "--aituber-http-timeout-s",
        type=float,
        default=default_aituber_http_timeout_s(),
        help="Timeout for each AITuberKit direct_send POST.",
    )
    parser.add_argument(
        "--aituber-speech-max-chars",
        type=int,
        default=default_aituber_speech_max_chars(),
        help="Split AITuber direct_send messages after roughly this many characters.",
    )
    parser.add_argument(
        "--local-ack-mode",
        choices=sorted(LOCAL_ACK_MODES),
        default=default_local_ack_mode(),
        help="Post a tiny local AITuber acknowledgement before the thought-core request.",
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
    status_store = StatusStore(args.status_dir) if args.status_dir else None
    turn_id = turn_id_from_result(result)
    stream_handlers: list[Callable[[ThoughtCoreStreamEvent], None]] = []
    status_writer = build_thought_core_status_writer(
        args.status_dir,
        result,
        source="watch_handoff_to_thought_core",
    )
    if status_writer is not None:
        stream_handlers.append(status_writer)
    tts_forwarder = ThoughtCoreTtsForwarder.from_args(
        args,
        store=status_store,
        turn_id=turn_id,
    )
    if tts_forwarder is not None:
        stream_handlers.append(tts_forwarder)
    aituber_forwarder = ThoughtCoreAituberForwarder.from_args(
        args,
        store=status_store,
        turn_id=turn_id,
    )
    if aituber_forwarder is not None:
        stream_handlers.append(aituber_forwarder)

    text = str(result["turn_payload"].get("text") or "")
    if should_skip_text(text, args):
        result["skipped"] = True
        result["skip_reason"] = "no_speech_placeholder"
        save_result_outputs(args, result)
        if status_writer is not None:
            status_writer.finish(result)
        close_stream_handlers(stream_handlers)
        return result

    result["skipped"] = False
    if args.dry_run:
        save_result_outputs(args, result)
        close_stream_handlers(stream_handlers)
        return result

    events: list[dict[str, Any]] = []

    def on_event(event: ThoughtCoreStreamEvent) -> None:
        events.append(event.to_dict())
        for stream_handler in stream_handlers:
            stream_handler(event)
        if args.print_events:
            print(format_event_line(event), flush=True)

    thought_core = client or ThoughtCoreClient.from_env()
    try:
        if should_post_local_ack(args, aituber_forwarder):
            aituber_forwarder.post_local_ack(build_local_ack(text))
        response = thought_core.send_turn_streaming(
            result["turn_payload"],
            on_event=on_event,
        )
    except Exception:
        close_stream_handlers(stream_handlers)
        raise
    result["events"] = events
    result["response"] = response.to_dict()
    save_result_outputs(args, result)
    for stream_handler in stream_handlers:
        finish = getattr(stream_handler, "finish", None)
        if callable(finish):
            finish(result)
    return result


def turn_id_from_result(result: dict[str, Any]) -> str | None:
    turn_payload = result.get("turn_payload")
    if not isinstance(turn_payload, dict):
        return None
    turn_id = str(turn_payload.get("turn_id") or "").strip()
    return turn_id or None


def close_stream_handlers(
    handlers: list[Callable[[ThoughtCoreStreamEvent], None]],
) -> None:
    for handler in handlers:
        close = getattr(handler, "close", None)
        if callable(close):
            close()


def should_post_local_ack(
    args: argparse.Namespace,
    aituber_forwarder: "ThoughtCoreAituberForwarder | None",
) -> bool:
    if aituber_forwarder is None:
        return False
    return str(getattr(args, "local_ack_mode", "auto") or "auto") != "off"


def should_skip_text(text: str, args: argparse.Namespace) -> bool:
    return not args.send_no_speech and text.strip() == NO_SPEECH_PLACEHOLDER


def default_tts_http_timeout_s() -> float:
    try:
        return max(0.05, float(os.environ.get("TTS_HTTP_TIMEOUT_S", "0.75")))
    except ValueError:
        return 0.75


def default_aituber_http_timeout_s() -> float:
    try:
        return max(0.05, float(os.environ.get("AITUBER_HTTP_TIMEOUT_S", "0.75")))
    except ValueError:
        return 0.75


def default_aituber_speech_max_chars() -> int:
    try:
        return max(8, int(os.environ.get("AITUBER_SPEECH_MAX_CHARS", "80")))
    except ValueError:
        return 80


def default_local_ack_mode() -> str:
    value = os.environ.get("THOUGHT_CORE_LOCAL_ACK_MODE", "auto").strip().lower()
    return value if value in LOCAL_ACK_MODES else "auto"


def build_local_ack(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if any(
        marker in compact
        for marker in (
            "つけて",
            "付けて",
            "点けて",
            "消して",
            "開けて",
            "閉めて",
            "オンにして",
            "オフにして",
        )
    ):
        return "[neutral]はいよ。"
    if any(
        marker in compact
        for marker in ("ついてる", "点いてる", "消えてる", "明るさ", "照明", "電気")
    ):
        return "[relaxed]ふんふん。"
    return "[neutral]うん。"


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


class AsyncJsonPostWorker:
    def __init__(
        self,
        url: str,
        *,
        timeout_s: float,
        on_error: Callable[[str], None],
        enabled: bool = False,
        queue_size: int = 100,
    ) -> None:
        self.url = url
        self.timeout_s = max(0.05, timeout_s)
        self.on_error = on_error
        self._closed = False
        self._queue: queue.Queue[bytes | None] | None = None
        self._thread: threading.Thread | None = None
        if enabled:
            self._queue = queue.Queue(maxsize=max(1, queue_size))
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def post(self, body: bytes) -> None:
        if self._closed:
            self.on_error("forward worker is already closed")
            return
        if self._queue is None:
            self._post_body(body)
            return
        try:
            self._queue.put_nowait(body)
        except queue.Full:
            self.on_error("forward queue full")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._queue is None or self._thread is None:
            return
        pending = self._queue.qsize()
        try:
            self._queue.put(None, timeout=self.timeout_s)
        except queue.Full:
            self.on_error("forward queue full during close")
            return
        wait_s = max(1.0, (pending + 1) * self.timeout_s + 0.5)
        self._thread.join(timeout=wait_s)
        if self._thread.is_alive():
            self.on_error("forward worker did not stop before timeout")

    def _run(self) -> None:
        if self._queue is None:
            return
        while True:
            body = self._queue.get()
            try:
                if body is None:
                    return
                self._post_body(body)
            finally:
                self._queue.task_done()

    def _post_body(self, body: bytes) -> None:
        req = request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                response.read()
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            self.on_error(str(exc))


class ThoughtCoreTtsForwarder:
    def __init__(
        self,
        chunk_url: str,
        *,
        timeout_s: float,
        async_post: bool = False,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.chunk_url = validate_http_url(chunk_url, label="--tts-chunk-url")
        self.timeout_s = max(0.05, timeout_s)
        self.store = store
        self.turn_id = turn_id
        self.final_sent = False
        self.error_count = 0
        self.poster = AsyncJsonPostWorker(
            self.chunk_url,
            timeout_s=self.timeout_s,
            on_error=self.record_error,
            enabled=async_post,
        )

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> "ThoughtCoreTtsForwarder | None":
        chunk_url = str(getattr(args, "tts_chunk_url", "") or "").strip()
        if not chunk_url:
            return None
        return cls(
            chunk_url,
            timeout_s=float(getattr(args, "tts_http_timeout_s", 0.75)),
            async_post=True,
            store=store,
            turn_id=turn_id,
        )

    def __call__(self, event: ThoughtCoreStreamEvent) -> None:
        if event.is_speech_delta and event.speech_delta:
            self.post(thought_core_tts_chunk_payload(event, turn_id=self.turn_id))
        if event.is_completed and not self.final_sent:
            self.final_sent = True
            self.post(
                thought_core_tts_chunk_payload(
                    event,
                    turn_id=self.turn_id,
                    final=True,
                )
            )

    def finish(self, result: dict[str, Any]) -> None:
        try:
            response = result.get("response")
            if self.final_sent or result.get("skipped") or not isinstance(response, dict):
                return
            self.final_sent = True
            self.post(
                {
                    "event": "turn.completed",
                    "final": True,
                    "turn_id": self.turn_id,
                    "message_id": response.get("message_id"),
                    "conversation_id": response.get("conversation_id"),
                }
            )
        finally:
            self.close()

    def close(self) -> None:
        self.poster.close()

    def post(self, payload: dict[str, Any]) -> None:
        clean_payload = {
            key: value
            for key, value in payload.items()
            if value is not None and value != ""
        }
        body = json.dumps(clean_payload, ensure_ascii=False).encode("utf-8")
        self.poster.post(body)

    def record_error(self, message: str) -> None:
        self.error_count += 1
        if self.error_count != 1 or self.store is None:
            return
        self.store.append_event(
            "tts.forward_error",
            source="watch_handoff_to_thought_core",
            turn_id=self.turn_id,
            payload={
                "chunk_url": redacted_text(self.chunk_url),
                "chunk_url_present": bool(self.chunk_url),
                "error": message[:240],
            },
        )


class ThoughtCoreAituberForwarder:
    def __init__(
        self,
        message_url: str,
        *,
        timeout_s: float,
        async_post: bool = False,
        max_chars: int = 80,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.message_url = validate_http_url(message_url, label="--aituber-message-url")
        self.timeout_s = max(0.05, timeout_s)
        self.max_chars = max(8, max_chars)
        self.store = store
        self.turn_id = turn_id
        self.error_count = 0
        self.poster = AsyncJsonPostWorker(
            self.message_url,
            timeout_s=self.timeout_s,
            on_error=self.record_error,
            enabled=async_post,
        )

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> "ThoughtCoreAituberForwarder | None":
        message_url = str(getattr(args, "aituber_message_url", "") or "").strip()
        if not message_url:
            return None
        return cls(
            message_url,
            timeout_s=float(getattr(args, "aituber_http_timeout_s", 0.75)),
            async_post=True,
            max_chars=int(getattr(args, "aituber_speech_max_chars", 80)),
            store=store,
            turn_id=turn_id,
        )

    def __call__(self, event: ThoughtCoreStreamEvent) -> None:
        if event.is_message and event.speech:
            self.post(event.speech)

    def finish(self, result: dict[str, Any]) -> None:
        self.close()

    def close(self) -> None:
        self.poster.close()

    def post_local_ack(self, message: str) -> None:
        self.post(message)

    def post(self, message: str) -> None:
        for chunk in split_aituber_speech_message(message, max_chars=self.max_chars):
            body = json.dumps({"messages": [chunk]}, ensure_ascii=False).encode("utf-8")
            self.poster.post(body)

    def record_error(self, message: str) -> None:
        self.error_count += 1
        if self.error_count != 1 or self.store is None:
            return
        self.store.append_event(
            "aituber.forward_error",
            source="watch_handoff_to_thought_core",
            turn_id=self.turn_id,
            payload={
                "message_url": redacted_text(self.message_url),
                "message_url_present": bool(self.message_url),
                "error": message[:240],
            },
        )


def split_aituber_speech_message(text: str, *, max_chars: int = 80) -> list[str]:
    remaining = text.strip()
    chunks: list[str] = []
    while remaining:
        cut_at = first_speech_boundary(remaining)
        if cut_at is None and visible_speech_length(remaining) > max(8, max_chars):
            cut_at = soft_speech_boundary(remaining, max_chars=max_chars)
        if cut_at is None:
            chunks.append(remaining)
            break
        chunk = remaining[:cut_at].strip()
        remaining = remaining[cut_at:].lstrip()
        if chunk:
            chunks.append(chunk)
    return chunks


def first_speech_boundary(text: str) -> int | None:
    for index, character in enumerate(text):
        if character in SPEECH_END_CHARS:
            return index + 1
    return None


def soft_speech_boundary(text: str, *, max_chars: int) -> int:
    visible_count = 0
    best_cut = 0
    for index, character in enumerate(text):
        visible_count += 0 if character.isspace() else 1
        if character in SPEECH_SOFT_BREAK_CHARS:
            best_cut = index + 1
        if visible_count >= max(8, max_chars):
            return best_cut or index + 1
    return len(text)


def visible_speech_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def thought_core_tts_chunk_payload(
    event: ThoughtCoreStreamEvent,
    *,
    turn_id: str | None,
    final: bool = False,
) -> dict[str, Any]:
    return {
        "event": event.event_type,
        "delta": event.speech_delta,
        "final": final,
        "turn_id": turn_id or event.turn_id,
        "message_id": event.event_id,
        "conversation_id": event.turn_id,
        "elapsed_s": event.elapsed_s,
    }


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
