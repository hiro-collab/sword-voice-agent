from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from urllib import error, request

from sword_voice_agent.adapters.ai_talk_core import (
    get_handoff_json_path,
    get_handoff_text_path,
)
from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.protocol.messages import now_timestamp


@dataclass(frozen=True)
class ConsoleStatusConfig:
    ai_talk_core_root: Path
    source: str = "web"
    gesture_status_json: Path | None = None
    status_dir: Path | None = Path(".cache/sword_voice_agent")
    input_gate_url: str | None = None
    input_gate_timeout_s: float = 1.5


def build_console_status(config: ConsoleStatusConfig) -> dict[str, Any]:
    cache_dir = config.ai_talk_core_root / ".cache" / "codex"
    status_store = StatusStore(config.status_dir) if config.status_dir else None
    handoff_json = read_json_file(get_handoff_json_path(config.ai_talk_core_root, config.source))
    handoff_text = read_text_file(get_handoff_text_path(config.ai_talk_core_root, config.source))
    dify_json = read_json_file(cache_dir / f"{config.source}_dify_latest.json")
    store_dify_json = (
        read_json_file(status_store.latest_dify_response_path)
        if status_store is not None
        else empty_file_state()
    )
    store_voice_turn_json = (
        read_json_file(status_store.latest_voice_turn_path)
        if status_store is not None
        else empty_file_state()
    )
    if store_dify_json["exists"] and not store_dify_json.get("error"):
        dify_json = store_dify_json
    dify_text = read_text_file(cache_dir / f"{config.source}_dify_latest.txt")
    conversation_id = read_text_file(
        cache_dir / f"{config.source}_dify_conversation_id.txt"
    )
    input_gate = fetch_input_gate(config)
    store_gesture = (
        read_json_file(status_store.latest_gesture_path)
        if status_store is not None
        else empty_file_state()
    )
    gesture = (
        store_gesture
        if store_gesture["exists"] and not store_gesture.get("error")
        else read_json_file(config.gesture_status_json)
        if config.gesture_status_json is not None
        else empty_file_state()
    )
    events = status_store.read_events(limit=40) if status_store is not None else []

    return {
        "type": "console_status",
        "timestamp": now_timestamp(),
        "paths": {
            "ai_talk_core_root": str(config.ai_talk_core_root),
            "cache_dir": str(cache_dir),
            "gesture_status_json": (
                str(config.gesture_status_json)
                if config.gesture_status_json is not None
                else None
            ),
            "status_dir": str(config.status_dir) if config.status_dir else None,
        },
        "health": {
            "handoff": handoff_json["exists"] and not handoff_json.get("error"),
            "dify": dify_json["exists"] and not dify_json.get("error"),
            "gesture": gesture["exists"] and not gesture.get("error"),
            "input_gate": None if not config.input_gate_url else input_gate["available"],
        },
        "gesture": normalize_gesture_status(gesture),
        "voice": normalize_voice_status(
            handoff_json,
            handoff_text,
            store_voice_turn_json,
        ),
        "dify": normalize_dify_status(dify_json, dify_text, conversation_id),
        "input_gate": input_gate,
        "events": events,
        "files": {
            "handoff_json": strip_payload(handoff_json),
            "handoff_text": strip_payload(handoff_text),
            "dify_json": strip_payload(dify_json),
            "dify_text": strip_payload(dify_text),
            "conversation_id": strip_payload(conversation_id),
            "gesture_json": strip_payload(gesture),
            "status_dify_json": strip_payload(store_dify_json),
            "status_gesture_json": strip_payload(store_gesture),
            "status_voice_turn_json": strip_payload(store_voice_turn_json),
        },
    }


def read_json_file(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return empty_file_state()
    resolved = Path(path)
    base = file_base(resolved)
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return base
    except OSError as exc:
        base["error"] = str(exc)
        return base

    base["exists"] = True
    refresh_stat(base, resolved)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        base["error"] = f"invalid JSON: {exc}"
        return base
    if not isinstance(payload, Mapping):
        base["error"] = "JSON payload must be an object"
        return base
    base["payload"] = dict(payload)
    return base


def read_text_file(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return empty_file_state()
    resolved = Path(path)
    base = file_base(resolved)
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return base
    except OSError as exc:
        base["error"] = str(exc)
        return base

    base["exists"] = True
    base["text"] = text
    refresh_stat(base, resolved)
    return base


def empty_file_state() -> dict[str, Any]:
    return {
        "path": None,
        "exists": False,
        "mtime": None,
        "size": None,
        "error": None,
    }


def file_base(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": False,
        "mtime": None,
        "size": None,
        "error": None,
    }


def refresh_stat(state: dict[str, Any], path: Path) -> None:
    stat = path.stat()
    state["mtime"] = stat.st_mtime
    state["size"] = stat.st_size


def strip_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": state.get("path"),
        "exists": state.get("exists", False),
        "mtime": state.get("mtime"),
        "size": state.get("size"),
        "error": state.get("error"),
    }


def normalize_gesture_status(state: Mapping[str, Any]) -> dict[str, Any]:
    payload = mapping(state.get("payload"))
    response = mapping(payload.get("response"))
    decision = mapping(response.get("gate_decision"))
    voice_state = mapping(response.get("voice_state"))
    command = mapping(response.get("voice_control_command"))
    input_gate = mapping(response.get("input_gate_response"))
    return {
        "available": bool(state.get("exists")) and not state.get("error"),
        "updated_at": state.get("mtime"),
        "sequence": payload.get("sequence"),
        "from": payload.get("from"),
        "raw_active": bool(decision.get("raw_active", False)),
        "confidence": float_or_none(decision.get("confidence")),
        "mic_enabled": bool(voice_state.get("mic_enabled", False)),
        "phase": voice_state.get("phase"),
        "reason": decision.get("reason"),
        "action": command.get("action", "none"),
        "turn_id": str(command.get("turn_id") or ""),
        "input_gate_ok": input_gate.get("ok"),
    }


def normalize_voice_status(
    handoff_json: Mapping[str, Any],
    handoff_text: Mapping[str, Any],
    latest_voice_turn: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = mapping(handoff_json.get("payload"))
    turn_payload = mapping(
        latest_voice_turn.get("payload") if latest_voice_turn is not None else None
    )
    turn_command = mapping(turn_payload.get("voice_control_command"))
    return {
        "available": bool(handoff_json.get("exists")) and not handoff_json.get("error"),
        "updated_at": handoff_json.get("mtime"),
        "transcript": str(payload.get("transcript", "")),
        "command": str(payload.get("command", "")),
        "turn_id": str(
            payload.get("turn_id")
            or turn_payload.get("turn_id")
            or turn_command.get("turn_id")
            or ""
        ),
        "prompt_text": str(handoff_text.get("text", "")),
    }


def normalize_dify_status(
    dify_json: Mapping[str, Any],
    dify_text: Mapping[str, Any],
    conversation_id: Mapping[str, Any],
) -> dict[str, Any]:
    payload = mapping(dify_json.get("payload"))
    request_payload = mapping(payload.get("request"))
    request_context = mapping(request_payload.get("context"))
    response = mapping(payload.get("response"))
    raw = mapping(response.get("raw"))
    metadata = mapping(raw.get("metadata"))
    usage = mapping(metadata.get("usage"))
    return {
        "available": bool(dify_json.get("exists")) and not dify_json.get("error"),
        "updated_at": dify_json.get("mtime"),
        "skipped": bool(payload.get("skipped", False)),
        "skip_reason": payload.get("skip_reason"),
        "request_text": str(request_payload.get("text", "")),
        "turn_id": str(payload.get("turn_id") or request_context.get("turn_id") or ""),
        "answer": str(response.get("text") or dify_text.get("text") or ""),
        "conversation_id": str(
            response.get("conversation_id") or conversation_id.get("text") or ""
        ).strip(),
        "message_id": str(response.get("message_id", "")),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "total_price": usage.get("total_price"),
            "currency": usage.get("currency"),
            "latency": usage.get("latency"),
        },
    }


def fetch_input_gate(config: ConsoleStatusConfig) -> dict[str, Any]:
    if not config.input_gate_url:
        return {"available": False, "url": None, "error": None, "payload": None}

    req = request.Request(
        url=config.input_gate_url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=config.input_gate_timeout_s) as response:
            body = response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        return {
            "available": False,
            "url": config.input_gate_url,
            "error": str(exc),
            "payload": None,
        }

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "url": config.input_gate_url,
            "error": f"invalid JSON: {exc}",
            "payload": None,
        }

    return {
        "available": isinstance(payload, Mapping),
        "url": config.input_gate_url,
        "error": None,
        "payload": payload if isinstance(payload, Mapping) else None,
    }


def mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
