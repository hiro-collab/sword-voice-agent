from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from sword_voice_agent.core.input_gate import GestureInputGate
from sword_voice_agent.protocol.messages import GestureState, ProtocolError


SCHEMA_VERSION = "local-media.voice-gate-proof.v0"
DEFAULT_PROOF_LAYER = "source/static-command-preview"
RAW_INPUT_SUFFIXES = {
    ".m4a",
    ".mp3",
    ".mp4",
    ".wav",
    ".jpeg",
    ".jpg",
    ".png",
}
SECRET_NAME_MARKERS = (
    ".env",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "secret",
    "token",
)
FORBIDDEN_OUTPUT_KEYS = {
    "answer",
    "api_key",
    "authorization",
    "content",
    "entity_id",
    "home_assistant",
    "message",
    "messages",
    "prompt",
    "provider_payload",
    "query",
    "response",
    "speech",
    "text",
    "token",
    "transcript",
    "url",
}
SAFE_LABEL_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_./:-")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build redacted voice-gate/STT/Thought Core proof summaries."
    )
    parser.add_argument("--asset-id", required=True)
    parser.add_argument(
        "--media-index",
        default="local/media/media-index.json",
        help="Path to local/media/media-index.json.",
    )
    parser.add_argument("--gate-events", default="")
    parser.add_argument("--gesture-events", default="")
    parser.add_argument("--stt-diagnostic", default="")
    parser.add_argument("--thought-core-events", default="")
    parser.add_argument(
        "--gate-source",
        choices=("auto", "gate-events", "gesture-events"),
        default="auto",
        help="Which redacted event stream should provide the gate result.",
    )
    parser.add_argument(
        "--expected-gate",
        choices=("any", "open", "closed"),
        default="any",
        help="Expected gate behavior for local-media positive/negative cases.",
    )
    parser.add_argument("--target-gesture", default="sword_sign")
    parser.add_argument("--min-confidence", type=float, default=0.8)
    parser.add_argument("--activation-delay", type=float, default=0.3)
    parser.add_argument("--activation-gap-grace", type=float, default=0.0)
    parser.add_argument("--min-activation-active-frames", type=int, default=1)
    parser.add_argument("--release-delay", type=float, default=0.5)
    parser.add_argument("--mode", choices=("preview", "collect-local"), default="preview")
    parser.add_argument("--proof-layer", default=DEFAULT_PROOF_LAYER)
    parser.add_argument(
        "--known-limitation",
        default="",
        help="Safe label for an expected limitation, for example victory_false_open.",
    )
    parser.add_argument("--print-json", action="store_true")
    return parser


def reject_unsafe_input_path(path: str | Path, *, allow_media_index: bool = False) -> None:
    path_obj = Path(path)
    normalized_name = path_obj.name.lower()
    normalized_path = str(path_obj).replace("\\", "/").lower()
    if allow_media_index and normalized_path.endswith("local/media/media-index.json"):
        return
    if path_obj.suffix.lower() in RAW_INPUT_SUFFIXES:
        raise ValueError(f"raw media input is not allowed: {path_obj.name}")
    if any(marker in normalized_name for marker in SECRET_NAME_MARKERS):
        raise ValueError(f"secret/config input is not allowed: {path_obj.name}")


def load_json(path: str | Path) -> Any:
    reject_unsafe_input_path(path, allow_media_index=True)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path: str | Path) -> list[Any]:
    reject_unsafe_input_path(path)
    rows: list[Any] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def load_media_asset(media_index_path: str | Path, asset_id: str) -> dict[str, Any]:
    index = load_json(media_index_path)
    for asset in index.get("assets", []):
        if str(asset.get("id", "")) == asset_id:
            return {
                "asset_id": asset_id,
                "asset_kind": str(asset.get("kind", "")) or "unknown",
                "duration_sec": asset.get("duration_sec"),
            }
    raise ValueError(f"asset id not found in media index: {asset_id}")


def records_from_path(path: str, *, jsonl: bool) -> list[Any]:
    if not path:
        return []
    if jsonl:
        return load_jsonl(path)
    loaded = load_json(path)
    if isinstance(loaded, list):
        return loaded
    return [loaded]


def walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(walk_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(walk_values(item))
    return values


def first_string_by_keys(value: Any, keys: set[str]) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in keys and isinstance(item, str):
                return item
        for item in value.values():
            found = first_string_by_keys(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_string_by_keys(item, keys)
            if found:
                return found
    return ""


def first_value_by_keys(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in keys:
                return item
        for item in value.values():
            found = first_value_by_keys(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_value_by_keys(item, keys)
            if found is not None:
                return found
    return None


def safe_label(value: Any) -> str | None:
    label = str(value or "").strip()
    if not label:
        return None
    lowered = label.lower()
    if len(lowered) > 80 or any(char not in SAFE_LABEL_CHARS for char in lowered):
        return None
    return lowered


def bucket_transcript_length(value: Any) -> str:
    text = str(value or "")
    length = len(text)
    if length == 0:
        return "empty"
    if length <= 40:
        return "nonempty:short"
    if length <= 120:
        return "nonempty:medium"
    return "nonempty:long"


def summarize_gate_events(events: list[Any]) -> dict[str, Any]:
    labels: list[str] = []
    for event in events:
        label = safe_label(
            first_value_by_keys(event, {"transition", "label", "phase", "reason", "type"})
        )
        if label:
            labels.append(label)
    label_counts = Counter(labels)
    return {
        "opened": any("open" in label for label in labels),
        "released": any("release" in label or "close" in label for label in labels),
        "transition_count": len(labels),
        "transition_labels": sorted(label_counts),
        "transition_label_counts": dict(sorted(label_counts.items())),
    }


def unwrap_topic_payload(record: Any) -> Mapping[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    payload = record.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return record


def gesture_state_from_record(
    record: Any,
    *,
    fallback_timestamp: float,
) -> GestureState | None:
    payload = unwrap_topic_payload(record)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("gestures"), Mapping):
        return None
    normalized = dict(payload)
    normalized.setdefault("source", "redacted_gesture_events")
    normalized.setdefault("timestamp", fallback_timestamp)
    try:
        return GestureState.from_dict(normalized)
    except ProtocolError:
        return None


def safe_primary_label(record: Any) -> str:
    payload = unwrap_topic_payload(record)
    if not isinstance(payload, Mapping):
        return "unknown"
    return (
        safe_label(payload.get("primary"))
        or safe_label(payload.get("primary_gesture"))
        or "none"
    )


def stable_target_signal(record: Any, target_gesture: str) -> Mapping[str, Any]:
    payload = unwrap_topic_payload(record)
    if not isinstance(payload, Mapping):
        return {}
    stable = payload.get("stable")
    if not isinstance(stable, Mapping):
        return {}
    gestures = stable.get("gestures")
    if not isinstance(gestures, Mapping):
        return {}
    target = gestures.get(target_gesture)
    if isinstance(target, Mapping):
        return target
    return {}


def summarize_gesture_events(
    events: list[Any],
    *,
    target_gesture: str,
    min_confidence: float,
    activation_delay_s: float,
    release_delay_s: float,
    activation_gap_grace_s: float,
    min_activation_active_frames: int,
) -> dict[str, Any]:
    states: list[GestureState] = []
    primary_labels: list[str] = []
    stable_active_count = 0
    stable_activated_count = 0
    stable_released_count = 0

    for index, event in enumerate(events):
        primary_labels.append(safe_primary_label(event))
        stable_target = stable_target_signal(event, target_gesture)
        stable_active_count += bool(stable_target.get("active"))
        stable_activated_count += bool(stable_target.get("activated"))
        stable_released_count += bool(stable_target.get("released"))
        state = gesture_state_from_record(event, fallback_timestamp=float(index) / 10.0)
        if state is not None:
            states.append(state)

    gate = GestureInputGate(
        gesture_name=target_gesture,
        min_confidence=min_confidence,
        activation_delay_s=activation_delay_s,
        release_delay_s=release_delay_s,
        activation_gap_grace_s=activation_gap_grace_s,
        min_activation_active_frames=min_activation_active_frames,
    )
    reason_labels: list[str] = []
    opened_count = 0
    released_count = 0
    raw_active_count = 0
    for state in states:
        decision = gate.update(state)
        reason_labels.append(decision.reason)
        raw_active_count += decision.raw_active
        if decision.changed and decision.mic_enabled:
            opened_count += 1
        if decision.changed and not decision.mic_enabled:
            released_count += 1

    reason_counts = Counter(reason_labels)
    primary_counts = Counter(primary_labels)
    return {
        "event_count": len(events),
        "parsed_state_count": len(states),
        "target_gesture": target_gesture,
        "primary_counts": dict(sorted(primary_counts.items())),
        "raw_active_count": raw_active_count,
        "stable_active_count": stable_active_count,
        "stable_activated_count": stable_activated_count,
        "stable_released_count": stable_released_count,
        "gate": {
            "opened": opened_count > 0,
            "released": released_count > 0,
            "activation_delay_s": activation_delay_s,
            "activation_gap_grace_s": activation_gap_grace_s,
            "min_activation_active_frames": min_activation_active_frames,
            "release_delay_s": release_delay_s,
            "open_transition_count": opened_count,
            "release_transition_count": released_count,
            "transition_count": len(reason_labels),
            "transition_labels": sorted(reason_counts),
            "transition_label_counts": dict(sorted(reason_counts.items())),
        },
    }


def select_gate_summary(
    *,
    gate_events: list[Any],
    gesture_summary: dict[str, Any],
    gate_source: str,
) -> dict[str, Any]:
    if gate_source == "gesture-events":
        return dict(gesture_summary["gate"])
    if gate_source == "gate-events":
        return summarize_gate_events(gate_events)
    if gesture_summary["event_count"]:
        return dict(gesture_summary["gate"])
    return summarize_gate_events(gate_events)


def evaluate_gate_expectation(gate_summary: Mapping[str, Any], expected_gate: str) -> dict[str, Any]:
    opened = bool(gate_summary.get("opened"))
    if expected_gate == "open":
        matched = opened
    elif expected_gate == "closed":
        matched = not opened
    else:
        matched = True
    return {
        "expected": expected_gate,
        "observed": "open" if opened else "closed",
        "matched": matched,
    }


def summarize_stt_diagnostic(records: list[Any]) -> dict[str, Any]:
    phase_labels: list[str] = []
    transcript = ""
    final_result = False
    for record in records:
        for key_set in ({"phase"}, {"label"}, {"event"}, {"type"}):
            label = safe_label(first_value_by_keys(record, key_set))
            if label:
                phase_labels.append(label)
        phase_path = first_value_by_keys(record, {"phase_path", "phases"})
        if isinstance(phase_path, list):
            for phase in phase_path:
                label = safe_label(phase)
                if label:
                    phase_labels.append(label)
        if not transcript:
            transcript = first_string_by_keys(record, {"transcript", "text"})
        final_value = first_value_by_keys(record, {"final_result", "result_ready"})
        final_result = final_result or bool(final_value)
    return {
        "phase_labels": sorted(set(phase_labels)),
        "final_result": final_result,
        "transcript_bucket": bucket_transcript_length(transcript),
        "raw_transcript_shared": False,
    }


def summarize_thought_core_events(events: list[Any]) -> dict[str, Any]:
    event_types: list[str] = []
    observed_intent: str | None = None
    observed_action_id: str | None = None
    observed_status: str | None = None
    for event in events:
        event_type = safe_label(first_value_by_keys(event, {"event_type", "type"}))
        if event_type:
            event_types.append(event_type)
        if observed_intent is None:
            observed_intent = safe_label(
                first_value_by_keys(event, {"observed_intent", "intent_label", "intent"})
            )
        if observed_action_id is None:
            observed_action_id = safe_label(
                first_value_by_keys(event, {"observed_action_id", "action_id"})
            )
        if observed_status is None:
            observed_status = safe_label(first_value_by_keys(event, {"status"}))
    event_counts = Counter(event_types)
    return {
        "turn_seen": bool(events),
        "turn_completed": any(
            event_type in {"turn.completed", "thought_core.completed"}
            for event_type in event_types
        ),
        "event_counts": dict(sorted(event_counts.items())),
        "observed_intent": observed_intent,
        "observed_action_id": observed_action_id,
        "status": observed_status,
    }


def summarize_chain(
    *,
    gate_summary: Mapping[str, Any],
    stt_summary: Mapping[str, Any],
    thought_core_summary: Mapping[str, Any],
) -> dict[str, Any]:
    gate_opened = bool(gate_summary.get("opened"))
    stt_final_result = bool(stt_summary.get("final_result"))
    thought_core_turn_completed = bool(thought_core_summary.get("turn_completed"))
    layer_results = {
        "gate_opened": gate_opened,
        "stt_final_result": stt_final_result,
        "thought_core_turn_completed": thought_core_turn_completed,
    }
    return {
        "path": "gesture_gate_to_stt_to_thought_core",
        "layer_results": layer_results,
        "ready_for_middle_review": all(layer_results.values()),
        "result": "pass" if all(layer_results.values()) else "not_enough_evidence",
        "raw_transcript_shared": False,
        "live_capture_used": False,
    }


def has_absolute_private_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if len(value) >= 3 and value[1:3] == ":\\":
        return True
    return normalized.startswith("/Users/") or normalized.startswith("/home/")


def scrub_forbidden_output(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            if lowered in FORBIDDEN_OUTPUT_KEYS:
                raise ValueError(f"forbidden output key: {key}")
            scrub_forbidden_output(item)
    elif isinstance(value, list):
        for item in value:
            scrub_forbidden_output(item)
    elif isinstance(value, str) and has_absolute_private_path(value):
        raise ValueError("forbidden absolute private path in output")


def build_summary(
    *,
    asset: dict[str, Any],
    proof_layer: str,
    gate_events: list[Any],
    gesture_events: list[Any],
    stt_records: list[Any],
    thought_core_events: list[Any],
    gate_source: str,
    expected_gate: str,
    target_gesture: str,
    min_confidence: float,
    activation_delay_s: float,
    release_delay_s: float,
    activation_gap_grace_s: float,
    min_activation_active_frames: int,
    known_limitation: str,
) -> dict[str, Any]:
    gesture_summary = summarize_gesture_events(
        gesture_events,
        target_gesture=target_gesture,
        min_confidence=min_confidence,
        activation_delay_s=activation_delay_s,
        release_delay_s=release_delay_s,
        activation_gap_grace_s=activation_gap_grace_s,
        min_activation_active_frames=min_activation_active_frames,
    )
    gate_summary = select_gate_summary(
        gate_events=gate_events,
        gesture_summary=gesture_summary,
        gate_source=gate_source,
    )
    gate_expectation = evaluate_gate_expectation(gate_summary, expected_gate)
    stt_summary = summarize_stt_diagnostic(stt_records)
    thought_core_summary = summarize_thought_core_events(thought_core_events)
    limitation_label = safe_label(known_limitation)
    if known_limitation and limitation_label is None:
        raise ValueError("known limitation must be a short safe label")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "asset_id": asset["asset_id"],
        "asset_kind": asset["asset_kind"],
        "proof_layer": proof_layer,
        "duration_sec": asset.get("duration_sec"),
        "gate_source": (
            "gesture-events"
            if gate_source == "auto" and gesture_summary["event_count"]
            else gate_source
        ),
        "gate": gate_summary,
        "gate_expectation": gate_expectation,
        "gesture": gesture_summary,
        "stt": stt_summary,
        "thought_core": thought_core_summary,
        "chain": summarize_chain(
            gate_summary=gate_summary,
            stt_summary=stt_summary,
            thought_core_summary=thought_core_summary,
        ),
        "safety": {
            "raw_media_shared": False,
            "raw_transcript_shared": False,
            "raw_prompt_shared": False,
            "raw_response_shared": False,
            "live_action_executed": False,
            "global_audio_changed": False,
        },
    }
    if limitation_label:
        summary["known_limitation"] = limitation_label
    if not gate_expectation["matched"]:
        summary["result"] = "known_limitation_fail" if limitation_label else "fail"
    elif expected_gate != "any":
        summary["result"] = "pass"
    elif (
        summary["gate"]["opened"]
        and summary["stt"]["final_result"]
        and summary["thought_core"]["turn_completed"]
    ):
        summary["result"] = "pass"
    else:
        summary["result"] = "not_enough_evidence"
    scrub_forbidden_output(summary)
    return summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    asset = load_media_asset(args.media_index, args.asset_id)
    gate_events = records_from_path(args.gate_events, jsonl=True)
    gesture_events = records_from_path(args.gesture_events, jsonl=True)
    stt_records = records_from_path(args.stt_diagnostic, jsonl=False)
    thought_core_events = records_from_path(args.thought_core_events, jsonl=True)
    return build_summary(
        asset=asset,
        proof_layer=args.proof_layer,
        gate_events=gate_events,
        gesture_events=gesture_events,
        stt_records=stt_records,
        thought_core_events=thought_core_events,
        gate_source=args.gate_source,
        expected_gate=args.expected_gate,
        target_gesture=args.target_gesture,
        min_confidence=args.min_confidence,
        activation_delay_s=args.activation_delay,
        release_delay_s=args.release_delay,
        activation_gap_grace_s=args.activation_gap_grace,
        min_activation_active_frames=args.min_activation_active_frames,
        known_limitation=args.known_limitation,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run(args)
    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status={result['result']}")
        print(f"proof_layer={result['proof_layer']}")
        print(f"asset_id={result['asset_id']}")
        print(f"gate_source={result['gate_source']}")
        print(f"expected_gate={result['gate_expectation']['expected']}")
        print(f"observed_gate={result['gate_expectation']['observed']}")
        print(f"gate_expectation_matched={str(result['gate_expectation']['matched']).lower()}")
        print(f"transcript_bucket={result['stt']['transcript_bucket']}")
        print("raw_media_shared=false")
        print("raw_transcript_shared=false")
        print("raw_prompt_shared=false")
        print("raw_response_shared=false")
        print("live_action_executed=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
