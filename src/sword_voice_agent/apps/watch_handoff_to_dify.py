from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib import error, request

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    get_handoff_json_path,
)
from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.adapters.dify import DifyClient, DifyClientError, DifyStreamEvent
from sword_voice_agent.adapters.status_store import StatusStore, redacted_text
from sword_voice_agent.apps.send_handoff_to_dify import (
    load_handoff_from_args,
    parse_context_pairs,
    validate_path_argument,
)


NO_SPEECH_PLACEHOLDER = "音声を認識できませんでした。"
SHORT_ASCII_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z'-]*")
STRIPPABLE_ASCII_PUNCTUATION = " \t\r\n.,!?;:\"'`“”‘’()[]{}<>"
RESPONSE_MODES = {"blocking", "streaming"}
SPEECH_MARKER_PATTERN = re.compile(r"\[\[SPEECH:[A-Z0-9_-]+\]\]")
KNOWN_MOTION_TAGS = frozenset(
    (
        "listening",
        "think",
        "cheer",
        "cross",
        "mouth_cover",
        "crossed_arms",
        "bow",
        "shrug",
        "shy",
        "wave",
        "clap",
    )
)
MOTION_TAG_PATTERN = re.compile(r"\[motion:([A-Za-z_][A-Za-z0-9_-]*)\]", re.I)
BARE_TAG_PATTERN = re.compile(r"\[([A-Za-z_][A-Za-z0-9_-]*)\]")
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
    parser.add_argument(
        "--response-mode",
        choices=sorted(RESPONSE_MODES),
        default=default_response_mode(),
        help="Dify response mode. streaming emits first-token timing events.",
    )
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
        "--tts-chunk-url",
        default=os.environ.get("TTS_HTTP_CHUNK_URL", ""),
        help=(
            "Optional tts-service /api/tts/chunk URL. In streaming mode, "
            "Dify answer deltas are posted here for lower TTS latency."
        ),
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
        help=(
            "Optional AITuberKit /api/messages URL. In streaming mode, Dify "
            "answer text is split into speech-sized direct_send messages."
        ),
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
        help="Flush an unfinished AITuber speech chunk after roughly this many characters.",
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
        "--skip-short-ascii",
        action="store_true",
        help=(
            "Skip suspicious short ASCII-only one-word STT results, such as "
            "'inverse', which are common silence/noise hallucinations in Japanese use."
        ),
    )
    parser.add_argument(
        "--short-ascii-max-chars",
        type=int,
        default=16,
        help="Maximum token length for --skip-short-ascii.",
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
    on_stream_event: Callable[[DifyStreamEvent], None] | None = None,
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
        "response_mode": args.response_mode,
        "skipped": False,
    }

    if should_skip_request(agent_request.text, args):
        result["skipped"] = True
        result["skip_reason"] = skip_reason_for_request(agent_request.text, args)
        return result

    if args.dry_run:
        return result

    dify_client = client or DifyClient.from_env()
    if args.response_mode == "streaming":
        response = dify_client.send_chat_message_streaming(
            agent_request,
            on_event=on_stream_event,
        )
    else:
        response = dify_client.send_chat_message(agent_request)
    result["response"] = response.to_dict()
    return result


def should_skip_request(text: str, args: argparse.Namespace) -> bool:
    return skip_reason_for_request(text, args) is not None


def skip_reason_for_request(text: str, args: argparse.Namespace) -> str | None:
    if not args.send_no_speech and text.strip() == NO_SPEECH_PLACEHOLDER:
        return "no_speech_placeholder"
    if args.skip_short_ascii and is_suspect_short_ascii_stt(
        text,
        max_chars=args.short_ascii_max_chars,
    ):
        return "short_ascii_stt_suspect"
    return None


def is_suspect_short_ascii_stt(text: str, *, max_chars: int = 16) -> bool:
    token = text.strip(STRIPPABLE_ASCII_PUNCTUATION)
    if not token or len(token) > max(1, max_chars):
        return False
    if any(character.isspace() for character in token):
        return False
    return bool(SHORT_ASCII_TOKEN_PATTERN.fullmatch(token))


def default_response_mode() -> str:
    value = os.environ.get("DIFY_RESPONSE_MODE", "blocking").strip().lower()
    return value if value in RESPONSE_MODES else "blocking"


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
    status_store = StatusStore(args.status_dir) if args.status_dir else None
    turn_id = load_latest_turn_id(args.status_dir) if args.status_dir else None
    stream_handlers: list[Callable[[DifyStreamEvent], None]] = []
    stream_status_writer = (
        DifyStreamStatusWriter(status_store, turn_id=turn_id)
        if status_store is not None
        else None
    )
    if stream_status_writer is not None:
        stream_handlers.append(stream_status_writer)
    tts_forwarder = TtsStreamForwarder.from_args(
        args,
        store=status_store,
        turn_id=turn_id,
    )
    if tts_forwarder is not None:
        stream_handlers.append(tts_forwarder)
    aituber_forwarder = AituberSpeechForwarder.from_args(
        args,
        store=status_store,
        turn_id=turn_id,
    )
    if aituber_forwarder is not None:
        stream_handlers.append(aituber_forwarder)
    result = process_handoff(
        args,
        client=client,
        on_stream_event=dispatch_stream_event(stream_handlers),
    )
    save_result_outputs(args, result)
    for stream_handler in stream_handlers:
        finish = getattr(stream_handler, "finish", None)
        if callable(finish):
            finish(result)
    if status_store is not None:
        status_store.write_latest_dify_response(
            result,
            turn_id=turn_id,
        )
    return result


def dispatch_stream_event(
    handlers: list[Callable[[DifyStreamEvent], None]],
) -> Callable[[DifyStreamEvent], None] | None:
    if not handlers:
        return None

    def dispatch(event: DifyStreamEvent) -> None:
        for handler in handlers:
            handler(event)

    return dispatch


class DifyStreamStatusWriter:
    def __init__(self, store: StatusStore, *, turn_id: str | None = None) -> None:
        self.store = store
        self.turn_id = turn_id
        self.first_token_seen = False
        self.done_seen = False

    def __call__(self, event: DifyStreamEvent) -> None:
        if event.answer_delta and not self.first_token_seen:
            self.first_token_seen = True
            self.store.append_event(
                "dify.first_token",
                source="watch_handoff_to_dify",
                turn_id=self.turn_id,
                payload=stream_event_payload(event),
            )
        if event.is_message_end and not self.done_seen:
            self.done_seen = True
            self.store.append_event(
                "dify.done",
                source="watch_handoff_to_dify",
                turn_id=self.turn_id,
                payload=stream_event_payload(event),
            )

    def finish(self, result: dict[str, Any]) -> None:
        response = result.get("response")
        if (
            self.done_seen
            or result.get("response_mode") != "streaming"
            or result.get("skipped")
            or not isinstance(response, dict)
        ):
            return
        raw = response.get("raw")
        streaming = raw.get("_streaming") if isinstance(raw, dict) else {}
        self.store.append_event(
            "dify.done",
            source="watch_handoff_to_dify",
            turn_id=self.turn_id,
            payload={
                "event": "stream_completed",
                "elapsed_s": (
                    streaming.get("completed_elapsed_s")
                    if isinstance(streaming, dict)
                    else None
                ),
                "answer_delta_present": False,
                "conversation_id": redacted_text(
                    response.get("conversation_id", "")
                    if isinstance(response, dict)
                    else ""
                ),
                "conversation_id_present": bool(
                    response.get("conversation_id") if isinstance(response, dict) else ""
                ),
                "message_id": redacted_text(
                    response.get("message_id", "")
                    if isinstance(response, dict)
                    else ""
                ),
                "message_id_present": bool(
                    response.get("message_id") if isinstance(response, dict) else ""
                ),
            },
        )


def stream_event_payload(event: DifyStreamEvent) -> dict[str, Any]:
    return {
        "event": event.event,
        "elapsed_s": event.elapsed_s,
        "answer_delta_present": bool(event.answer_delta),
        "answer_delta": redacted_text(event.answer_delta),
        "conversation_id": redacted_text(event.conversation_id or ""),
        "conversation_id_present": bool(event.conversation_id),
        "message_id": redacted_text(event.message_id or ""),
        "message_id_present": bool(event.message_id),
        "task_id": redacted_text(event.task_id or ""),
        "task_id_present": bool(event.task_id),
    }


class TtsStreamForwarder:
    def __init__(
        self,
        chunk_url: str,
        *,
        timeout_s: float,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.chunk_url = validate_http_url(chunk_url, label="--tts-chunk-url")
        self.timeout_s = max(0.05, timeout_s)
        self.store = store
        self.turn_id = turn_id
        self.final_sent = False
        self.error_count = 0

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> "TtsStreamForwarder | None":
        chunk_url = str(getattr(args, "tts_chunk_url", "") or "").strip()
        if not chunk_url:
            return None
        return cls(
            chunk_url,
            timeout_s=float(getattr(args, "tts_http_timeout_s", 0.75)),
            store=store,
            turn_id=turn_id,
        )

    def __call__(self, event: DifyStreamEvent) -> None:
        if event.answer_delta:
            self.post(tts_chunk_payload(event, turn_id=self.turn_id))
        if event.is_message_end and not self.final_sent:
            self.final_sent = True
            self.post(tts_chunk_payload(event, turn_id=self.turn_id, final=True))

    def finish(self, result: dict[str, Any]) -> None:
        response = result.get("response")
        if (
            self.final_sent
            or result.get("response_mode") != "streaming"
            or result.get("skipped")
            or not isinstance(response, dict)
        ):
            return
        self.final_sent = True
        self.post(
            {
                "event": "message_end",
                "final": True,
                "turn_id": self.turn_id,
                "message_id": response.get("message_id"),
                "conversation_id": response.get("conversation_id"),
            }
        )

    def post(self, payload: dict[str, Any]) -> None:
        clean_payload = {
            key: value
            for key, value in payload.items()
            if value is not None and value != ""
        }
        body = json.dumps(clean_payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.chunk_url,
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
            self.record_error(str(exc))

    def record_error(self, message: str) -> None:
        self.error_count += 1
        if self.error_count != 1 or self.store is None:
            return
        self.store.append_event(
            "tts.forward_error",
            source="watch_handoff_to_dify",
            turn_id=self.turn_id,
            payload={
                "chunk_url": redacted_text(self.chunk_url),
                "chunk_url_present": bool(self.chunk_url),
                "error": message[:240],
            },
        )


class AituberSpeechForwarder:
    def __init__(
        self,
        message_url: str,
        *,
        timeout_s: float,
        max_chars: int = 80,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.message_url = validate_http_url(message_url, label="--aituber-message-url")
        self.timeout_s = max(0.05, timeout_s)
        self.max_chars = max(8, max_chars)
        self.store = store
        self.turn_id = turn_id
        self.buffer = ""
        self.done_seen = False
        self.error_count = 0

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        store: StatusStore | None = None,
        turn_id: str | None = None,
    ) -> "AituberSpeechForwarder | None":
        message_url = str(getattr(args, "aituber_message_url", "") or "").strip()
        if not message_url:
            return None
        return cls(
            message_url,
            timeout_s=float(getattr(args, "aituber_http_timeout_s", 0.75)),
            max_chars=int(getattr(args, "aituber_speech_max_chars", 80)),
            store=store,
            turn_id=turn_id,
        )

    def __call__(self, event: DifyStreamEvent) -> None:
        if event.answer_delta:
            self.buffer += event.answer_delta
            self.flush_ready(final=False)
        if event.is_message_end and not self.done_seen:
            self.done_seen = True
            self.flush_ready(final=True)

    def finish(self, result: dict[str, Any]) -> None:
        if (
            self.done_seen
            or result.get("response_mode") != "streaming"
            or result.get("skipped")
        ):
            return
        self.done_seen = True
        self.flush_ready(final=True)

    def flush_ready(self, *, final: bool) -> None:
        chunks, self.buffer = split_speech_chunks(
            self.buffer,
            final=final,
            max_chars=self.max_chars,
        )
        for chunk in chunks:
            self.post(chunk)

    def post(self, message: str) -> None:
        clean_message = clean_speech_message(message)
        if not clean_message:
            return
        body = json.dumps({"messages": [clean_message]}, ensure_ascii=False).encode(
            "utf-8"
        )
        req = request.Request(
            self.message_url,
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
            self.record_error(str(exc))

    def record_error(self, message: str) -> None:
        self.error_count += 1
        if self.error_count != 1 or self.store is None:
            return
        self.store.append_event(
            "aituber.forward_error",
            source="watch_handoff_to_dify",
            turn_id=self.turn_id,
            payload={
                "message_url": redacted_text(self.message_url),
                "message_url_present": bool(self.message_url),
                "error": message[:240],
            },
        )


def split_speech_chunks(
    text: str,
    *,
    final: bool,
    max_chars: int = 80,
) -> tuple[list[str], str]:
    remaining = text
    chunks: list[str] = []

    while remaining:
        cut_at = first_speech_boundary(remaining)
        if cut_at is None:
            break
        chunk = remaining[:cut_at].strip()
        remaining = remaining[cut_at:].lstrip()
        if clean_speech_message(chunk):
            chunks.append(chunk)

    if final:
        chunk = remaining.strip()
        if clean_speech_message(chunk):
            chunks.append(chunk)
        return chunks, ""

    if len(visible_speech_text(remaining)) >= max(8, max_chars):
        cut_at = soft_speech_boundary(remaining, max_chars=max_chars)
        chunk = remaining[:cut_at].strip()
        remaining = remaining[cut_at:].lstrip()
        if clean_speech_message(chunk):
            chunks.append(chunk)

    return chunks, remaining


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


def clean_speech_message(text: str) -> str:
    cleaned = normalize_motion_tags(SPEECH_MARKER_PATTERN.sub("", text)).strip()
    visible = visible_speech_text(cleaned)
    if not visible:
        return ""
    return cleaned


def normalize_motion_tags(text: str) -> str:
    def normalize_motion(match: re.Match[str]) -> str:
        motion_name = match.group(1).lower()
        if motion_name in KNOWN_MOTION_TAGS:
            return f"[motion:{motion_name}]"
        return match.group(0)

    def normalize_bare_tag(match: re.Match[str]) -> str:
        tag_name = match.group(1).lower()
        if tag_name in KNOWN_MOTION_TAGS:
            return f"[motion:{tag_name}]"
        return match.group(0)

    normalized = MOTION_TAG_PATTERN.sub(normalize_motion, text)
    return BARE_TAG_PATTERN.sub(normalize_bare_tag, normalized)


def visible_speech_text(text: str) -> str:
    without_markers = SPEECH_MARKER_PATTERN.sub("", text)
    without_tags = re.sub(
        r"\[(?:motion:[^\]\s]+|[A-Za-z_][A-Za-z0-9_-]*)\]",
        "",
        without_markers,
    )
    return re.sub(r"\s+", "", without_tags)


def tts_chunk_payload(
    event: DifyStreamEvent,
    *,
    turn_id: str | None,
    final: bool = False,
) -> dict[str, Any]:
    return {
        "event": event.event,
        "delta": event.answer_delta,
        "final": final,
        "turn_id": turn_id,
        "message_id": event.message_id,
        "conversation_id": event.conversation_id,
        "elapsed_s": event.elapsed_s,
    }


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
