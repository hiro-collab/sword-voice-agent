from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "no_provider_child_provenance_diagnostics.v0"

FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}

STACK_THOUGHT_CORE_ENV_KEYS = {
    "THOUGHT_CORE_LLM_ENABLED",
    "THOUGHT_CORE_LLM_BASE_URL",
    "THOUGHT_CORE_LLM_API_KEY",
    "THOUGHT_CORE_LLM_MODEL",
    "THOUGHT_CORE_LLM_TIMEOUT_S",
    "THOUGHT_CORE_LLM_MAX_CHARS",
    "THOUGHT_CORE_PERSONA",
    "SWORD_THOUGHT_CORE_PERSONA",
    "THOUGHT_CORE_HOME_HTTP_TIMEOUT_S",
    "THOUGHT_CORE_ROOM_LIGHT_WAIT_TIMEOUT_MS",
    "THOUGHT_CORE_TOOLS_ADAPTER",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
}

PROVIDER_CONFIG_KEYS = (
    "THOUGHT_CORE_LLM_BASE_URL",
    "THOUGHT_CORE_LLM_API_KEY",
    "THOUGHT_CORE_LLM_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)

SELECTED_PORT_CLASSES = {
    "launcher": 8799,
    "home_assistant_bridge": 8787,
    "environment_state_server": 8790,
    "camera_hub_or_local_media": 8765,
    "camera_browser_monitor": 8770,
    "vision_snapshot_processor": 8776,
    "aituber_projection_visual": 3000,
    "touchdesigner_gui": 8788,
    "thought_core_api": 18787,
}

LAUNCHER_PROFILE_TO_OPS_PROFILE = {
    "full-stack": "thought-core-v0",
    "no-touchdesigner": "thought-core-v0",
    "thought-core-v0": "thought-core-v0",
    "thought-core-experimental": "thought-core-experimental",
    "aituber-only": "aituber-only",
    "camera-debug": "camera-debug",
}

SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
UNSAFE_VALUE_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]", re.IGNORECASE),
    re.compile(r"\\\\[A-Za-z0-9_.-]+\\"),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,}|secret|token)\b", re.IGNORECASE),
)


class NoProviderChildProvenanceError(ValueError):
    """Raised when diagnostics cannot be built safely."""


@dataclass(frozen=True)
class EnvValue:
    value: str | None
    source: str

    @property
    def is_present(self) -> bool:
        return self.value is not None

    @property
    def is_nonblank(self) -> bool:
        return bool((self.value or "").strip())


def build_no_provider_child_provenance_diagnostics(
    *,
    agent_os_root: Path,
    selected_profile: str = "thought-core-v0",
    generated_at: str | None = None,
    process_env: Mapping[str, str] | None = None,
    listener_classes: Mapping[str, str] | None = None,
    payload_marker_class: str = "",
    payload_text: str | None = None,
    top_level_text_present_class: str = "",
    context_ref_payload_class: str = "",
) -> dict[str, Any]:
    """Build a summary-only Launch Manager / Thought Core provenance diagnostic.

    The returned payload intentionally contains only classes, booleans, and
    hashes. It never copies env values, raw payload text, provider payloads,
    local absolute paths, or command lines.
    """

    agent_os_root = Path(agent_os_root)
    control_plane_root = agent_os_root / "control-plane" / "sword-voice-agent"
    thought_core_root = control_plane_root / "services" / "thought-core"
    thought_core_env = read_dotenv(thought_core_root / ".env")
    process_env = dict(process_env or {})
    ops_profile = LAUNCHER_PROFILE_TO_OPS_PROFILE.get(selected_profile, selected_profile)
    profile = read_profile(control_plane_root, ops_profile)
    services = tuple(str(item) for item in profile.get("services", ()) if item)

    final_env = {
        key: resolve_final_child_env_value(
            key,
            process_env=process_env,
            thought_core_env=thought_core_env,
        )
        for key in set(STACK_THOUGHT_CORE_ENV_KEYS)
        | {"THOUGHT_CORE_ACTION_LLM_ENABLED", "THOUGHT_CORE_FORCE_NO_PROVIDER"}
    }
    if classify_env_value(final_env["THOUGHT_CORE_FORCE_NO_PROVIDER"].value) == "enabled_literal":
        final_env["THOUGHT_CORE_LLM_ENABLED"] = EnvValue("0", "force_no_provider")
        final_env["THOUGHT_CORE_ACTION_LLM_ENABLED"] = EnvValue(
            "0",
            "force_no_provider",
        )
        for key in PROVIDER_CONFIG_KEYS:
            final_env[key] = EnvValue("", "force_no_provider")
    llm_class = classify_env_value(final_env["THOUGHT_CORE_LLM_ENABLED"].value)
    action_llm_class = classify_env_value(
        final_env["THOUGHT_CORE_ACTION_LLM_ENABLED"].value
    )
    provider_classes = {
        key: classify_env_value(final_env[key].value) for key in PROVIDER_CONFIG_KEYS
    }
    provider_presence = provider_presence_class(provider_classes)
    external_provider_route = external_provider_route_class(services)
    listener_summary = classify_listener_state(listener_classes)
    input_hash = source_hash_class(
        thought_core_root / "src" / "thought_core" / "input_understanding.py"
    )
    loop_hash = source_hash_class(thought_core_root / "src" / "thought_core" / "loop.py")
    payload_preflight = payload_marker_preflight(
        payload_marker_class=payload_marker_class,
        payload_text=payload_text,
        top_level_text_present_class=top_level_text_present_class,
        context_ref_payload_class=context_ref_payload_class,
    )
    diagnostic = {
        "diagnostics_schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_text(),
        "selected_profile": safe_label(selected_profile),
        "ops_profile": safe_label(ops_profile),
        "launcher_child_identity_class": "future_launch_profile_managed_child",
        "child_process_no_provider_binding_class": child_no_provider_binding_class(
            llm_class=llm_class,
            provider_presence=provider_presence,
        ),
        "thought_core_llm_enabled_class": llm_class,
        "thought_core_action_llm_enabled_class": action_llm_effective_class(
            action_llm_class=action_llm_class,
            llm_class=llm_class,
        ),
        "thought_core_force_no_provider_class": classify_env_value(
            final_env["THOUGHT_CORE_FORCE_NO_PROVIDER"].value
        ),
        "provider_config_presence_class": provider_presence,
        "provider_config_key_classes": provider_classes,
        "external_provider_route_class": external_provider_route,
        "stale_or_reused_process_class": listener_summary["stale_or_reused_process_class"],
        "selected_port_listener_classes": listener_summary["selected_port_listener_classes"],
        "imported_input_understanding_hash_class": input_hash,
        "imported_loop_hash_class": loop_hash,
        "runtime_import_provenance_available": False,
        "running_child_input_understanding_import_provenance": {
            "available": False,
            "collection_point": "thought_core_child_diagnostics_endpoint",
            "reason_class": "not_running_child_context",
        },
        "running_child_loop_import_provenance": {
            "available": False,
            "collection_point": "thought_core_child_diagnostics_endpoint",
            "reason_class": "not_running_child_context",
        },
        "top_level_text_present_class": payload_preflight["top_level_text_present_class"],
        "top_level_text_marker_class": payload_preflight["top_level_text_marker_class"],
        "context_ref_payload_class": payload_preflight["context_ref_payload_class"],
        "marker_class_consistency": payload_preflight["marker_class_consistency"],
        "mapping_input_source": payload_preflight["mapping_input_source"],
        "marker_class_object_hash": payload_preflight["marker_class_object_hash"],
        "payload_marker_class_preflight": payload_preflight["payload_marker_class"],
        "payload_marker_hash_class": payload_preflight["payload_marker_hash_class"],
        "payload_marker_preflight_source_class": payload_preflight["source_class"],
        "standard_diagnostics_surface_class": "partial",
        "diagnostics_status_writer_surface": "missing_status_surface",
        "diagnostics_reader_surface": (
            "route_local_cli_artifact_plus_child_endpoint_direct_read_available"
        ),
        "one_off_artifact_only": False,
        "remaining_standardization_gaps": [
            "normal_diagnostics_status_writer_not_integrated",
            "launch_manager_status_reader_not_integrated",
            "runtime_child_snapshot_not_observed_until_child_endpoint_is_queried",
        ],
        "raw_private_publication_flags": raw_private_publication_flags(),
        "does_not_prove": [
            "provider_quality",
            "runtime_turn_completed",
            "motion_pv_aituber_dispatch",
            "self_mirror_capture",
            "home_control_action",
            "live_input_or_stt",
            "rr003_review_ready_or_representative_pass",
        ],
    }
    assert_no_provider_child_provenance_safe(diagnostic)
    return diagnostic


def read_dotenv(path: Path) -> dict[str, EnvValue]:
    result: dict[str, EnvValue] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return result
    except OSError as exc:
        raise NoProviderChildProvenanceError("dotenv could not be read") from exc
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if not key:
            continue
        result[key] = EnvValue(_unquote_dotenv_value(value.strip()), "thought_core_env")
    return result


def read_profile(control_plane_root: Path, profile: str) -> dict[str, Any]:
    profile_path = control_plane_root / "ops" / "manifests" / "profiles" / f"{profile}.json"
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NoProviderChildProvenanceError("profile manifest missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise NoProviderChildProvenanceError("profile manifest unreadable") from exc
    if not isinstance(payload, dict):
        raise NoProviderChildProvenanceError("profile manifest must be an object")
    return payload


def resolve_final_child_env_value(
    key: str,
    *,
    process_env: Mapping[str, str],
    thought_core_env: Mapping[str, EnvValue],
) -> EnvValue:
    inherited = EnvValue(process_env.get(key), "process_env")
    candidate = inherited
    dotenv_value = thought_core_env.get(key)
    if key in STACK_THOUGHT_CORE_ENV_KEYS:
        if inherited.is_nonblank:
            candidate = inherited
        elif dotenv_value is not None and dotenv_value.is_nonblank:
            candidate = dotenv_value
    if dotenv_value is None:
        return candidate
    if dotenv_value.is_nonblank:
        return dotenv_value
    if candidate.is_nonblank:
        return candidate
    return EnvValue(dotenv_value.value, dotenv_value.source)


def classify_env_value(value: str | None) -> str:
    if value is None:
        return "absent"
    text = value.strip()
    if not text:
        return "empty"
    lowered = text.lower()
    if lowered in FALSE_VALUES:
        return "disabled_literal"
    if lowered in TRUE_VALUES:
        return "enabled_literal"
    return "present_nonempty_redacted"


def provider_presence_class(provider_classes: Mapping[str, str]) -> str:
    present = [
        key
        for key, value in provider_classes.items()
        if value not in {"absent", "empty", "disabled_literal"}
    ]
    if not present:
        return "provider_config_absent_or_empty"
    return "provider_config_present_nonempty_redacted"


def child_no_provider_binding_class(*, llm_class: str, provider_presence: str) -> str:
    if llm_class == "disabled_literal":
        return "no_provider_or_fallback_only_bound_by_final_child_env_class"
    if llm_class == "enabled_literal":
        return "provider_capable_enabled_after_env_import"
    if provider_presence == "provider_config_present_nonempty_redacted":
        return "provider_capable_not_disabled_with_provider_config_present"
    return "fallback_possible_but_no_provider_binding_not_explicit"


def action_llm_effective_class(*, action_llm_class: str, llm_class: str) -> str:
    if action_llm_class == "disabled_literal":
        return "action_llm_disabled_literal"
    if action_llm_class in {"enabled_literal", "present_nonempty_redacted"}:
        return "action_llm_explicitly_enabled_or_configured"
    if llm_class == "enabled_literal":
        return "action_llm_inherits_llm_enabled_class"
    return "action_llm_not_enabled_by_class"


def external_provider_route_class(services: tuple[str, ...]) -> str:
    if "thought_core_api" in services or "thought_core_watcher" in services:
        return "thought_core_route_selected"
    return "no_external_provider_route_selected"


def classify_listener_state(
    listener_classes: Mapping[str, str] | None,
) -> dict[str, Any]:
    if listener_classes is None:
        return {
            "stale_or_reused_process_class": "listener_state_not_checked_source_no_live",
            "selected_port_listener_classes": {},
        }
    normalized = {
        safe_label(key): safe_label(value or "unknown")
        for key, value in listener_classes.items()
        if key in SELECTED_PORT_CLASSES
    }
    if any(value not in {"none", "clear"} for value in normalized.values()):
        return {
            "stale_or_reused_process_class": "selected_listener_present_needs_owner_classification",
            "selected_port_listener_classes": normalized,
        }
    return {
        "stale_or_reused_process_class": "selected_ports_clear_no_reuse_detected",
        "selected_port_listener_classes": normalized,
    }


def source_hash_class(path: Path) -> dict[str, Any]:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except FileNotFoundError:
        return {"available": False, "sha256_16": "missing"}
    except OSError as exc:
        raise NoProviderChildProvenanceError("source hash could not be read") from exc
    return {"available": True, "sha256_16": digest}


def payload_marker_preflight(
    *,
    payload_marker_class: str = "",
    payload_text: str | None = None,
    top_level_text_present_class: str = "",
    context_ref_payload_class: str = "",
) -> dict[str, str]:
    if payload_text is not None:
        marker_class = classify_payload_text(payload_text)
        digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()[:16]
        source_class = "computed_from_private_text_without_publication"
        top_level_present = "present_redacted" if payload_text.strip() else "empty"
        payload_hash = f"sha256_16:{digest}"
    elif payload_marker_class:
        marker_class = safe_label(payload_marker_class)
        source_class = "declared_summary_class_not_byte_proof"
        top_level_present = safe_label(
            top_level_text_present_class or "present_redacted_by_marker_class"
        )
        payload_hash = "not_computed"
    else:
        marker_class = "not_provided"
        source_class = "not_provided"
        top_level_present = safe_label(top_level_text_present_class or "not_provided")
        payload_hash = "not_computed"

    context_class = safe_label(context_ref_payload_class or "not_provided")
    consistency = marker_class_consistency_class(marker_class, context_class)
    marker_object = {
        "context_ref_payload_class": context_class,
        "mapping_input_source": "top_level_text",
        "marker_class_consistency": consistency,
        "top_level_text_marker_class": marker_class,
        "top_level_text_present_class": top_level_present,
    }
    marker_object_hash = hashlib.sha256(
        json.dumps(marker_object, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "top_level_text_present_class": top_level_present,
        "top_level_text_marker_class": marker_class,
        "context_ref_payload_class": context_class,
        "marker_class_consistency": consistency,
        "mapping_input_source": "top_level_text",
        "marker_class_object_hash": f"sha256_16:{marker_object_hash}",
        "payload_marker_class": marker_class,
        "payload_marker_hash_class": payload_hash,
        "source_class": source_class,
    }


def marker_class_consistency_class(marker_class: str, context_class: str) -> str:
    if context_class == "not_provided":
        return "context_ref_payload_class_not_provided"
    if marker_class == "not_provided":
        return "top_level_text_marker_class_not_provided"
    if (
        marker_class == "happy_marker_plus_move_marker"
        and context_class == "happy_expression_motion_request"
    ):
        return "consistent_marker_and_context_label"
    if marker_class == context_class:
        return "matching_summary_labels"
    return "mismatch_or_unmapped_summary_labels"


def classify_payload_text(text: str) -> str:
    normalized = text.strip()
    lowered = normalized.lower()
    if not normalized:
        return "empty_text"
    if "dance" in lowered or "\u8e0a" in normalized:
        return "dance_marker"
    happy_markers = ("\u3046\u308c\u3057\u305d\u3046", "\u5b09\u3057\u305d\u3046", "\u697d\u3057\u305d\u3046", "\u559c\u3093\u3067", "\u306f\u3057\u3083\u3044\u3067")
    move_markers = ("\u52d5\u3044\u3066", "\u52d5\u304d\u3092", "\u52d5\u4f5c", "\u8eab\u632f\u308a", "\u30b8\u30a7\u30b9\u30c1\u30e3")
    if any(marker in normalized for marker in happy_markers) and any(
        marker in normalized for marker in move_markers
    ):
        return "happy_marker_plus_move_marker"
    return "no_motion_marker"


def raw_private_publication_flags() -> dict[str, bool]:
    return {
        "raw_input_text_shared": False,
        "raw_response_text_shared": False,
        "raw_prompt_shared": False,
        "raw_transcript_shared": False,
        "raw_audio_shared": False,
        "raw_media_shared": False,
        "raw_screenshot_shared": False,
        "raw_logs_shared": False,
        "command_lines_shared": False,
        "browser_storage_shared": False,
        "private_paths_shared": False,
        "env_values_shared": False,
        "secrets_tokens_shared": False,
        "provider_payload_shared": False,
        "private_endpoint_shared": False,
        "home_assistant_entity_or_device_detail_shared": False,
    }


def assert_no_provider_child_provenance_safe(payload: Mapping[str, Any]) -> None:
    def check(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key.endswith("_shared") and child is not False:
                    raise NoProviderChildProvenanceError(
                        f"{'.'.join(path + (str(key),))} must be false"
                    )
                check(child, path + (str(key),))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                check(child, path + (str(index),))
            return
        if isinstance(value, str):
            for pattern in UNSAFE_VALUE_PATTERNS:
                if pattern.search(value):
                    raise NoProviderChildProvenanceError(
                        f"unsafe value at {'.'.join(path)}"
                    )

    check(payload)


def safe_label(value: str) -> str:
    normalized = SAFE_LABEL_RE.sub("_", str(value or "").strip())
    normalized = normalized.strip("._:-")
    return normalized[:120] or "unknown"


def utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _unquote_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
