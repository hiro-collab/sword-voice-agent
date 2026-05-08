from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping
from urllib import error, request

from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.adapters.ai_talk_core import (
    ai_talk_core_api_headers,
    get_handoff_json_path,
    get_handoff_text_path,
)
from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.protocol.messages import now_timestamp

REDACTED = "[redacted]"

EXPECTED_MODULES = (
    ("ai_talk_core", "ai_talk_core Web UI"),
    ("gesture_udp_receiver", "Gesture UDP receiver"),
    ("mediapipe_udp_publisher", "MediaPipe UDP publisher"),
    ("dify_api", "Dify API"),
    ("thought_core_api", "thought-core API"),
    ("dify_watcher", "Dify watcher"),
    ("thought_core_watcher", "thought-core watcher"),
    ("tts_service", "TTS service"),
    ("avatar_service", "Avatar service"),
    ("console", "Integration console"),
)


@dataclass(frozen=True)
class ConsoleStatusConfig:
    ai_talk_core_root: Path
    source: str = "web"
    gesture_status_json: Path | None = None
    status_dir: Path | None = Path(".cache/sword_voice_agent")
    tts_status_dir: Path | None = Path(".cache/tts_service")
    tts_app_volume_file: Path | None = None
    tts_volume_url: str | None = None
    tts_volume_preview_url: str | None = None
    tts_volume_timeout_s: float = 1.5
    input_gate_url: str | None = None
    input_gate_token: str | None = None
    input_gate_timeout_s: float = 1.5
    dify_base_url: str | None = None
    dify_timeout_s: float = 1.5
    thought_core_base_url: str | None = None
    thought_core_timeout_s: float = 1.5
    avatar_url: str | None = None
    avatar_model_url: str | None = None
    avatar_timeout_s: float = 1.5
    module_stale_after_s: float = 6.0
    redact_sensitive: bool = False


def build_console_status(config: ConsoleStatusConfig) -> dict[str, Any]:
    timestamp = now_timestamp()
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
    store_thought_core_json = (
        read_json_file(status_store.latest_thought_core_response_path)
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
    tts_json = (
        read_json_file(config.tts_status_dir / "latest_tts_state.json")
        if config.tts_status_dir is not None
        else empty_file_state()
    )
    tts_volume_json = read_tts_volume_state(config)
    conversation_id = read_text_file(
        cache_dir / f"{config.source}_dify_conversation_id.txt"
    )
    input_gate = fetch_input_gate(config)
    dify_api = fetch_dify_api(config)
    thought_core_api = fetch_thought_core_api(config)
    avatar = fetch_avatar_service(config)
    store_gesture = (
        read_json_file(status_store.latest_gesture_path)
        if status_store is not None
        else empty_file_state()
    )
    store_gesture_diagnostic = (
        read_json_file(status_store.latest_gesture_diagnostic_path)
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
    events = read_console_events(config, limit=40)
    conversation_log = (
        status_store.read_conversation_log(limit=80)
        if status_store is not None
        else []
    )
    module_statuses = (
        status_store.read_module_statuses() if status_store is not None else {}
    )

    status = {
        "type": "console_status",
        "timestamp": timestamp,
        "redacted": False,
        "paths": {
            "ai_talk_core_root": str(config.ai_talk_core_root),
            "cache_dir": str(cache_dir),
            "gesture_status_json": (
                str(config.gesture_status_json)
                if config.gesture_status_json is not None
                else None
            ),
            "status_dir": str(config.status_dir) if config.status_dir else None,
            "tts_status_dir": str(config.tts_status_dir) if config.tts_status_dir else None,
        },
        "health": {
            "handoff": handoff_json["exists"] and not handoff_json.get("error"),
            "dify": dify_json["exists"] and not dify_json.get("error"),
            "thought_core": (
                store_thought_core_json["exists"]
                and not store_thought_core_json.get("error")
            ),
            "dify_api": dify_api["available"],
            "thought_core_api": thought_core_api["available"],
            "gesture": gesture["exists"] and not gesture.get("error"),
            "input_gate": None if not config.input_gate_url else input_gate["available"],
            "tts": tts_json["exists"] and not tts_json.get("error"),
            "avatar": avatar["available"],
        },
        "gesture": normalize_gesture_status(gesture),
        "gesture_diagnostic": normalize_gesture_diagnostic(store_gesture_diagnostic),
        "voice": normalize_voice_status(
            handoff_json,
            handoff_text,
            store_voice_turn_json,
        ),
        "dify": normalize_dify_status(dify_json, dify_text, conversation_id),
        "thought_core": normalize_thought_core_status(store_thought_core_json),
        "tts": normalize_tts_status(
            tts_json,
            tts_volume_json,
            app_volume_file=resolve_tts_app_volume_file(config),
            volume_url=config.tts_volume_url,
            volume_preview_url=config.tts_volume_preview_url,
        ),
        "avatar": avatar,
        "dify_api": dify_api,
        "thought_core_api": thought_core_api,
        "input_gate": input_gate,
        "modules": normalize_module_statuses(
            module_statuses,
            input_gate=input_gate,
            dify_api=dify_api,
            thought_core_api=thought_core_api,
            avatar=avatar,
            timestamp=timestamp,
            stale_after_s=config.module_stale_after_s,
        ),
        "events": events,
        "conversation_log": conversation_log,
        "files": {
            "handoff_json": strip_payload(handoff_json),
            "handoff_text": strip_payload(handoff_text),
            "dify_json": strip_payload(dify_json),
            "dify_text": strip_payload(dify_text),
            "conversation_id": strip_payload(conversation_id),
            "tts_json": strip_payload(tts_json),
            "tts_volume_json": strip_payload(tts_volume_json),
            "gesture_json": strip_payload(gesture),
            "status_dify_json": strip_payload(store_dify_json),
            "status_thought_core_json": strip_payload(store_thought_core_json),
            "status_gesture_json": strip_payload(store_gesture),
            "status_gesture_diagnostic_json": strip_payload(store_gesture_diagnostic),
            "status_voice_turn_json": strip_payload(store_voice_turn_json),
        },
    }
    return redact_console_status(status) if config.redact_sensitive else status


def redact_console_status(status: Mapping[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(dict(status))
    redacted["redacted"] = True

    for value in _mapping_mutable(redacted.get("paths")).keys():
        redacted["paths"][value] = _redact_scalar(redacted["paths"][value])

    gesture = _mapping_mutable(redacted.get("gesture"))
    for key in ("from", "turn_id"):
        gesture[key] = _redact_scalar(gesture.get(key))

    voice = _mapping_mutable(redacted.get("voice"))
    for key in ("transcript", "command", "turn_id", "prompt_text"):
        voice[key] = _redact_scalar(voice.get(key))

    dify = _mapping_mutable(redacted.get("dify"))
    for key in ("request_text", "turn_id", "answer", "conversation_id", "message_id"):
        dify[key] = _redact_scalar(dify.get(key))

    thought_core = _mapping_mutable(redacted.get("thought_core"))
    for key in ("request_text", "turn_id", "answer"):
        thought_core[key] = _redact_scalar(thought_core.get(key))

    tts = _mapping_mutable(redacted.get("tts"))
    for key in (
        "request_id",
        "message_id",
        "conversation_id",
        "turn_id",
        "text_hash",
        "watching",
        "app_volume_file",
        "volume_url",
        "volume_preview_url",
    ):
        tts[key] = _redact_scalar(tts.get(key))

    input_gate = _mapping_mutable(redacted.get("input_gate"))
    input_gate["url"] = _redact_scalar(input_gate.get("url"))

    dify_api = _mapping_mutable(redacted.get("dify_api"))
    dify_api["url"] = _redact_scalar(dify_api.get("url"))

    thought_core_api = _mapping_mutable(redacted.get("thought_core_api"))
    thought_core_api["url"] = _redact_scalar(thought_core_api.get("url"))

    avatar = _mapping_mutable(redacted.get("avatar"))
    avatar["url"] = _redact_scalar(avatar.get("url"))
    avatar["model_url"] = _redact_scalar(avatar.get("model_url"))

    for module in _list_of_mappings(redacted.get("modules")):
        module["detail"] = _redact_scalar(module.get("detail"))

    for file_state in _mapping_mutable(redacted.get("files")).values():
        if isinstance(file_state, dict):
            file_state["path"] = _redact_scalar(file_state.get("path"))

    redacted["events"] = [
        redact_event(event)
        for event in redacted.get("events", [])
        if isinstance(event, Mapping)
    ]
    redacted["conversation_log"] = [
        redact_conversation_entry(entry)
        for entry in redacted.get("conversation_log", [])
        if isinstance(entry, Mapping)
    ]
    return redacted


def redact_conversation_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    item["text"] = _redact_scalar(item.get("text"))
    item["turn_id"] = _redact_scalar(item.get("turn_id"))
    item["session_id"] = _redact_scalar(item.get("session_id"))
    item["issue_id"] = _redact_scalar(item.get("issue_id"))
    metadata = mapping(item.get("metadata"))
    if metadata:
        item["metadata"] = redact_conversation_metadata(metadata)
    return item


def redact_conversation_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in ("seq", "elapsed_s", "status"):
        if key in metadata:
            safe[key] = metadata.get(key)
    timing = mapping(metadata.get("timing"))
    if timing:
        safe["timing"] = redact_timing_summary(timing)
    return safe or {"redacted": True}


def redact_timing_summary(timing: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in (
        "event_count",
        "speech_delta_count",
        "first_event_elapsed_s",
        "first_speech_elapsed_s",
        "first_message_elapsed_s",
        "completed_elapsed_s",
        "max_gap_s",
    ):
        if key in timing:
            safe[key] = timing.get(key)
    slowest_gap = mapping(timing.get("slowest_gap"))
    if slowest_gap:
        safe["slowest_gap"] = {
            "after_event_seq": slowest_gap.get("after_event_seq"),
            "event_type": slowest_gap.get("event_type"),
            "delta_elapsed_s": slowest_gap.get("delta_elapsed_s"),
        }
    timeline = timing.get("timeline")
    if isinstance(timeline, list):
        safe["timeline"] = [
            redact_timing_timeline_item(item)
            for item in timeline[-80:]
            if isinstance(item, Mapping)
        ]
    return safe


def redact_timing_timeline_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_type": item.get("event_type"),
        "seq": item.get("seq"),
        "phase": item.get("phase"),
        "elapsed_s": item.get("elapsed_s"),
        "delta_elapsed_s": item.get("delta_elapsed_s"),
        "stage": item.get("stage"),
        "status": item.get("status"),
        "tool": item.get("tool"),
        "speech_present": item.get("speech_present"),
        "speech_chars": item.get("speech_chars"),
    }


def redact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(event)
    item["turn_id"] = _redact_scalar(item.get("turn_id"))
    data = mapping(item.get("payload") or item.get("data"))
    redacted_payload: dict[str, Any] = {"redacted": True}
    if item.get("type") == "dify.response":
        redacted_payload.update(
            {
                "request_text": _redact_scalar(data.get("request_text")),
                "response_text": _redact_scalar(data.get("response_text")),
                "conversation_id": _redact_scalar(data.get("conversation_id")),
                "conversation_id_present": data.get("conversation_id_present"),
                "message_id": _redact_scalar(data.get("message_id")),
                "skipped": data.get("skipped", False),
                "skip_reason": data.get("skip_reason"),
            }
        )
    elif item.get("type") == "thought_core.response":
        redacted_payload.update(
            {
                "request_text": _redact_scalar(data.get("request_text")),
                "turn_text": _redact_scalar(data.get("turn_text")),
                "response_text": _redact_scalar(data.get("response_text")),
                "event_count": data.get("event_count"),
                "skipped": data.get("skipped", False),
                "skip_reason": data.get("skip_reason"),
            }
        )
    elif str(item.get("type") or "").startswith("thought_core."):
        redacted_payload.update(
            {
                "event_type": data.get("event_type"),
                "seq": data.get("seq"),
                "elapsed_s": data.get("elapsed_s"),
                "delta_elapsed_s": data.get("delta_elapsed_s"),
                "phase": data.get("phase"),
                "stage": data.get("stage"),
                "partial": data.get("partial"),
                "speech_present": data.get("speech_present"),
                "speech": _redact_scalar(data.get("speech")),
                "speech_chars": data.get("speech_chars"),
                "status": data.get("status"),
                "tool": data.get("tool"),
                "tool_call_id": _redact_scalar(data.get("tool_call_id")),
                "tool_call_id_present": data.get("tool_call_id_present"),
            }
        )
    elif item.get("type") == "gesture.received":
        response = mapping(data.get("response"))
        command = mapping(response.get("voice_control_command"))
        decision = mapping(response.get("gate_decision"))
        redacted_payload.update(
            {
                "response": {
                    "voice_control_command": {
                        "action": command.get("action", "none"),
                        "turn_id": _redact_scalar(command.get("turn_id")),
                    },
                    "gate_decision": {
                        "raw_active": decision.get("raw_active"),
                        "reason": decision.get("reason"),
                        "confidence": decision.get("confidence"),
                    },
                }
            }
        )
    elif item.get("type") == "tts.state":
        redacted_payload.update(
            {
                "phase": data.get("phase"),
                "service": data.get("service"),
                "engine": data.get("engine"),
                "player": data.get("player"),
                "volume": data.get("volume"),
                "rate": data.get("rate"),
                "app_volume": data.get("app_volume"),
                "app_volume_file": _redact_scalar(data.get("app_volume_file")),
                "error": data.get("error"),
            }
        )
    item["payload"] = redacted_payload
    item.pop("data", None)
    return item


def normalize_module_statuses(
    statuses: Mapping[str, Mapping[str, Any]],
    *,
    input_gate: Mapping[str, Any],
    dify_api: Mapping[str, Any] | None = None,
    thought_core_api: Mapping[str, Any] | None = None,
    avatar: Mapping[str, Any] | None = None,
    timestamp: float,
    stale_after_s: float,
) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for name, default_label in EXPECTED_MODULES:
        status = mapping(statuses.get(name))
        state = str(status.get("state") or "missing")
        updated_at = float_or_none(status.get("timestamp"))
        age = timestamp - updated_at if updated_at is not None else None
        label = str(status.get("label") or default_label)
        detail = str(status.get("detail") or "")

        if name == "ai_talk_core" and input_gate.get("available"):
            state = "running"
            updated_at = timestamp
            age = 0.0
            detail = detail or "input gate API reachable"
        elif name == "dify_api" and dify_api is not None:
            if dify_api.get("available"):
                state = "running"
                updated_at = timestamp
                age = 0.0
                detail = str(dify_api.get("url") or detail or "reachable")
            elif dify_api.get("url"):
                state = "error"
                updated_at = timestamp
                age = 0.0
                error_text = str(dify_api.get("error") or "not reachable")
                detail = f"{dify_api.get('url')} / {error_text}"
        elif name == "thought_core_api" and thought_core_api is not None:
            if thought_core_api.get("available"):
                state = "running"
                updated_at = timestamp
                age = 0.0
                detail = str(thought_core_api.get("url") or detail or "reachable")
            elif thought_core_api.get("url"):
                state = "error"
                updated_at = timestamp
                age = 0.0
                error_text = str(thought_core_api.get("error") or "not reachable")
                detail = f"{thought_core_api.get('url')} / {error_text}"
        elif name == "avatar_service" and avatar is not None:
            if avatar.get("available"):
                state = "running"
                updated_at = timestamp
                age = 0.0
                detail = str(avatar.get("url") or detail or "reachable")
            elif avatar.get("url"):
                state = "error"
                updated_at = timestamp
                age = 0.0
                error_text = str(avatar.get("error") or "not reachable")
                detail = f"{avatar.get('url')} / {error_text}"
        elif name == "console":
            state = "running"
            updated_at = timestamp
            age = 0.0
            detail = detail or "console API reachable"
        elif state in {"running", "starting"} and age is not None and age > stale_after_s:
            state = "stale"

        modules.append(
            {
                "name": name,
                "label": label,
                "state": state,
                "detail": detail,
                "updated_at": updated_at,
                "age_seconds": age,
            }
        )
    return modules


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
        "hand_detected": None,
        "primary_gesture": None,
        "best_confidence": None,
        "fps": None,
        "frame_id": None,
        "camera_opened": None,
    }


def normalize_gesture_diagnostic(state: Mapping[str, Any]) -> dict[str, Any]:
    payload = mapping(state.get("payload"))
    response = mapping(payload.get("response"))
    diagnostic = mapping(response.get("diagnostic"))
    sword = mapping(diagnostic.get("sword_sign"))
    best = mapping(diagnostic.get("best_gesture"))
    camera = mapping(diagnostic.get("camera"))
    return {
        "available": bool(state.get("exists")) and not state.get("error"),
        "updated_at": state.get("mtime"),
        "sequence": payload.get("sequence"),
        "from": payload.get("from"),
        "diagnostic_type": diagnostic.get("type"),
        "diagnostic_status": diagnostic.get("status"),
        "raw_active": bool(sword.get("active", False)),
        "confidence": float_or_none(sword.get("confidence")),
        "hand_detected": diagnostic.get("hand_detected"),
        "primary_gesture": diagnostic.get("primary_gesture") or best.get("name"),
        "best_confidence": float_or_none(best.get("confidence")),
        "fps": float_or_none(diagnostic.get("fps")),
        "frame_id": diagnostic.get("frame_id"),
        "camera_opened": camera.get("opened"),
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
    streaming = mapping(raw.get("_streaming"))
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
            "latency": usage.get("latency") or streaming.get("completed_elapsed_s"),
            "first_token_latency": streaming.get("first_token_elapsed_s"),
        },
    }


def normalize_thought_core_status(
    thought_core_json: Mapping[str, Any],
) -> dict[str, Any]:
    payload = mapping(thought_core_json.get("payload"))
    request_payload = mapping(payload.get("request"))
    request_context = mapping(request_payload.get("context"))
    turn_payload = mapping(payload.get("turn_payload"))
    response = mapping(payload.get("response"))
    raw = mapping(response.get("raw"))
    raw_data = mapping(raw.get("data"))
    streaming = mapping(raw.get("_streaming"))
    return {
        "available": bool(thought_core_json.get("exists"))
        and not thought_core_json.get("error"),
        "updated_at": thought_core_json.get("mtime"),
        "skipped": bool(payload.get("skipped", False)),
        "skip_reason": payload.get("skip_reason"),
        "status": raw_data.get("status"),
        "request_text": str(
            request_payload.get("text") or turn_payload.get("text") or ""
        ),
        "turn_id": str(
            payload.get("turn_id")
            or turn_payload.get("turn_id")
            or response.get("conversation_id")
            or request_context.get("turn_id")
            or ""
        ),
        "session_id": str(turn_payload.get("session_id", "")),
        "answer": str(response.get("text", "")),
        "event_count": streaming.get("event_count"),
        "latency": streaming.get("completed_elapsed_s"),
        "first_event_latency": streaming.get("first_event_elapsed_s"),
    }


def normalize_tts_status(
    tts_json: Mapping[str, Any],
    tts_volume_json: Mapping[str, Any] | None = None,
    *,
    app_volume_file: Path | None = None,
    volume_url: str | None = None,
    volume_preview_url: str | None = None,
) -> dict[str, Any]:
    payload = mapping(tts_json.get("payload"))
    volume_payload = mapping(tts_volume_json.get("payload")) if tts_volume_json is not None else {}
    app_volume = app_volume_from_payload(volume_payload)
    if app_volume is None:
        app_volume = float_or_none(payload.get("app_volume"))
    app_volume_file_value = str(payload.get("app_volume_file") or "")
    if not app_volume_file_value and app_volume_file is not None:
        app_volume_file_value = str(app_volume_file)
    tts_volume_from_api = bool(tts_volume_json and tts_volume_json.get("source") == "api")
    return {
        "available": bool(tts_json.get("exists")) and not tts_json.get("error"),
        "updated_at": tts_json.get("mtime"),
        "phase": str(payload.get("phase", "")),
        "service": str(payload.get("service") or ""),
        "request_id": str(payload.get("request_id") or ""),
        "message_id": str(payload.get("message_id") or ""),
        "conversation_id": str(payload.get("conversation_id") or ""),
        "turn_id": str(payload.get("turn_id") or ""),
        "source": str(payload.get("source") or ""),
        "watching": str(payload.get("watching") or ""),
        "engine": str(payload.get("engine") or ""),
        "player": str(payload.get("player") or ""),
        "voice_name": str(payload.get("voice_name") or ""),
        "poll_interval": float_or_none(payload.get("poll_interval")),
        "volume": int_or_none(payload.get("volume")),
        "rate": int_or_none(payload.get("rate")),
        "app_volume": app_volume,
        "app_volume_file": app_volume_file_value,
        "volume_url": volume_url or "",
        "volume_preview_url": volume_preview_url or "",
        "app_volume_file_exists": bool(
            tts_volume_json and tts_volume_json.get("exists") and not tts_volume_from_api
        ),
        "app_volume_available": app_volume is not None
        or bool(volume_url)
        or bool(app_volume_file_value),
        "text_hash": str(payload.get("text_hash") or ""),
        "error": str(payload.get("error") or ""),
    }


def resolve_tts_app_volume_file(config: ConsoleStatusConfig) -> Path | None:
    if config.tts_app_volume_file is not None:
        return config.tts_app_volume_file
    if config.tts_status_dir is None:
        return None
    return config.tts_status_dir / "app_volume.json"


def read_tts_volume_state(config: ConsoleStatusConfig) -> dict[str, Any]:
    if config.tts_volume_url:
        api_state = read_tts_volume_api(config)
        if api_state["exists"] and not api_state.get("error"):
            return api_state
        file_state = read_json_file(resolve_tts_app_volume_file(config))
        if file_state["exists"] and not file_state.get("error"):
            file_state["volume_url"] = config.tts_volume_url
            file_state["volume_url_error"] = api_state.get("error")
            return file_state
        return api_state
    return read_json_file(resolve_tts_app_volume_file(config))


def read_tts_volume_api(config: ConsoleStatusConfig) -> dict[str, Any]:
    state = {
        "path": config.tts_volume_url,
        "exists": False,
        "mtime": None,
        "size": None,
        "error": None,
        "source": "api",
    }
    if not config.tts_volume_url:
        return state
    try:
        volume_url = validate_http_url(config.tts_volume_url, label="TTS_VOLUME_URL")
    except ValueError as exc:
        state["error"] = str(exc)
        return state

    req = request.Request(
        url=volume_url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=config.tts_volume_timeout_s) as response:
            body = response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        state["error"] = str(exc)
        return state

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        state["error"] = f"invalid JSON: {exc}"
        return state
    if not isinstance(payload, Mapping):
        state["error"] = "TTS volume API payload must be an object"
        return state

    state["exists"] = True
    state["payload"] = dict(payload)
    state["payload"]["volume_url"] = volume_url
    return state


def fetch_input_gate(config: ConsoleStatusConfig) -> dict[str, Any]:
    if not config.input_gate_url:
        return {"available": False, "url": None, "error": None, "payload": None}

    try:
        input_gate_url = validate_http_url(
            config.input_gate_url,
            label="input gate status URL",
        )
    except ValueError as exc:
        return {
            "available": False,
            "url": config.input_gate_url,
            "error": str(exc),
            "payload": None,
        }

    req = request.Request(
        url=input_gate_url,
        method="GET",
        headers={
            "Accept": "application/json",
            **ai_talk_core_api_headers(config.input_gate_token),
        },
    )
    try:
        with request.urlopen(req, timeout=config.input_gate_timeout_s) as response:
            body = response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        return {
            "available": False,
            "url": input_gate_url,
            "error": str(exc),
            "payload": None,
        }

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "url": input_gate_url,
            "error": f"invalid JSON: {exc}",
            "payload": None,
        }

    return {
        "available": isinstance(payload, Mapping),
        "url": input_gate_url,
        "error": None,
        "payload": payload if isinstance(payload, Mapping) else None,
    }


def fetch_dify_api(config: ConsoleStatusConfig) -> dict[str, Any]:
    return fetch_http_reachability(
        config.dify_base_url,
        timeout_s=config.dify_timeout_s,
        label="DIFY_BASE_URL",
    )


def fetch_thought_core_api(config: ConsoleStatusConfig) -> dict[str, Any]:
    return fetch_http_reachability(
        config.thought_core_base_url,
        timeout_s=config.thought_core_timeout_s,
        label="THOUGHT_CORE_BASE_URL",
    )


def fetch_avatar_service(config: ConsoleStatusConfig) -> dict[str, Any]:
    result = fetch_http_reachability(
        config.avatar_url,
        timeout_s=config.avatar_timeout_s,
        label="AVATAR_SERVICE_URL",
    )
    result["model_url"] = config.avatar_model_url
    return result


def fetch_http_reachability(
    url: str | None,
    *,
    timeout_s: float,
    label: str,
) -> dict[str, Any]:
    if not url:
        return {
            "available": False,
            "url": None,
            "status": None,
            "error": None,
        }
    try:
        request_url = validate_http_url(url, label=label)
    except ValueError as exc:
        return {
            "available": False,
            "url": url,
            "status": None,
            "error": str(exc),
        }

    req = request.Request(
        url=request_url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            response.read(0)
            return {
                "available": True,
                "url": request_url,
                "status": response.status,
                "error": None,
            }
    except error.HTTPError as exc:
        try:
            exc.close()
        except AttributeError:
            pass
        return {
            "available": True,
            "url": request_url,
            "status": exc.code,
            "error": None,
        }
    except (error.URLError, TimeoutError, OSError) as exc:
        return {
            "available": False,
            "url": request_url,
            "status": None,
            "error": str(exc),
        }


def read_console_events(config: ConsoleStatusConfig, *, limit: int = 40) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    if config.status_dir is not None:
        merged.extend(StatusStore(config.status_dir).read_events(limit=limit))
    merged.extend(read_ai_talk_core_events(config.ai_talk_core_root, limit=limit))
    if config.tts_status_dir is not None:
        merged.extend(read_tts_service_events(config.tts_status_dir, limit=limit))
    merged.sort(key=lambda event: float_or_none(event.get("timestamp")) or 0.0)
    return merged[-max(1, limit) :]


def read_console_events_after(
    config: ConsoleStatusConfig,
    event_id: str | None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    events = read_console_events(config, limit=limit)
    if not event_id:
        return events
    for index, event in enumerate(events):
        if str(event.get("event_id") or "") == event_id:
            return events[index + 1 :]
    return events


def read_ai_talk_core_events(
    ai_talk_core_root: Path,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    path = ai_talk_core_root / ".cache" / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        return []

    events: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            events.append(normalize_ai_talk_core_event(payload))
    return events


def normalize_ai_talk_core_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    event_name = str(payload.get("event") or "event")
    timestamp = timestamp_from_ai_core_event(payload)
    turn_id = str(payload.get("turn_id") or "")
    monotonic = payload.get("timestamp_monotonic")
    event_id = f"ai_core:{turn_id}:{event_name}:{monotonic or timestamp}"
    return {
        "event_id": event_id,
        "type": f"ai_core.{event_name}",
        "timestamp": timestamp,
        "source": f"ai_talk_core:{payload.get('source') or 'core'}",
        "turn_id": turn_id or None,
        "payload": dict(mapping(payload.get("payload"))),
    }


def read_tts_service_events(
    tts_status_dir: Path,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    path = tts_status_dir / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        return []

    events: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            events.append(normalize_tts_service_event(payload))
    return events


def normalize_tts_service_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("type") == "tts_event":
        event_name = str(payload.get("event") or "event")
        timestamp = timestamp_from_tts_event(payload)
        request_id = str(payload.get("request_id") or "")
        turn_id = str(payload.get("turn_id") or "")
        event_id = f"tts:event:{event_name}:{request_id}:{timestamp}"
        return {
            "event_id": event_id,
            "type": f"tts.{event_name}",
            "timestamp": timestamp,
            "source": "tts_service",
            "turn_id": turn_id or None,
            "payload": dict(payload),
        }

    phase = str(payload.get("phase") or "")
    timestamp = timestamp_from_tts_event(payload)
    request_id = str(payload.get("request_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    event_id = f"tts:state:{phase}:{request_id}:{timestamp}"
    return {
        "event_id": event_id,
        "type": "tts.state",
        "timestamp": timestamp,
        "source": "tts_service",
        "turn_id": turn_id or None,
        "payload": dict(payload),
    }


def timestamp_from_ai_core_event(payload: Mapping[str, Any]) -> float:
    wall = payload.get("timestamp_wall")
    if isinstance(wall, str) and wall.strip():
        try:
            return datetime.fromisoformat(wall.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    monotonic = float_or_none(payload.get("timestamp_monotonic"))
    return monotonic if monotonic is not None else 0.0


def timestamp_from_tts_event(payload: Mapping[str, Any]) -> float:
    for key in ("wall_time", "updated_at"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    monotonic = float_or_none(payload.get("monotonic_time") or payload.get("perf_counter"))
    return monotonic if monotonic is not None else 0.0


def mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_mutable(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_mappings(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _redact_scalar(value: object) -> object:
    if value is None or value == "":
        return value
    return REDACTED


def float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def app_volume_from_payload(payload: Mapping[str, Any]) -> float | None:
    if payload.get("muted") is True:
        return 0.0
    for key in ("app_volume", "volume", "value"):
        value = float_or_none(payload.get(key))
        if value is not None:
            return clamp_app_volume(value)
    percent = float_or_none(payload.get("app_volume_percent"))
    if percent is not None:
        return clamp_app_volume(percent / 100.0)
    return None


def clamp_app_volume(value: float) -> float:
    return max(0.0, min(1.0, value))
