"""Summary-only provenance diagnostics for the running thought-core child."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


SCHEMA_VERSION = "thought_core.no_provider_child_provenance.v0"

FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
PROVIDER_CONFIG_KEYS = (
    "THOUGHT_CORE_LLM_BASE_URL",
    "THOUGHT_CORE_LLM_API_KEY",
    "THOUGHT_CORE_LLM_MODEL",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)
SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def build_child_provenance_diagnostics(
    *,
    selected_profile: str = "thought-core-v0",
    ops_profile: str = "thought-core-v0",
    top_level_text_present_class: str = "",
    top_level_text_marker_class: str = "",
    context_ref_payload_class: str = "",
) -> dict[str, Any]:
    """Return no-turn, no-provider summary classes from inside the child process."""

    llm_class = classify_env_value(os.environ.get("THOUGHT_CORE_LLM_ENABLED"))
    action_llm_class = classify_env_value(
        os.environ.get("THOUGHT_CORE_ACTION_LLM_ENABLED")
    )
    provider_classes = {
        key: classify_env_value(os.environ.get(key)) for key in PROVIDER_CONFIG_KEYS
    }
    provider_presence = provider_presence_class(provider_classes)
    marker_summary = payload_marker_summary(
        top_level_text_present_class=top_level_text_present_class,
        top_level_text_marker_class=top_level_text_marker_class,
        context_ref_payload_class=context_ref_payload_class,
    )
    payload = {
        "diagnostics_schema_version": SCHEMA_VERSION,
        "selected_profile": safe_label(selected_profile),
        "ops_profile": safe_label(ops_profile),
        "launcher_child_identity_class": "running_thought_core_child_process",
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
            os.environ.get("THOUGHT_CORE_FORCE_NO_PROVIDER")
        ),
        "provider_config_presence_class": provider_presence,
        "provider_config_key_classes": provider_classes,
        "direct_dify_exclusion_class": (
            "thought_core_child_current_route_does_not_use_direct_dify"
        ),
        "stale_or_reused_process_class": "running_child_identity_observed_by_endpoint",
        "imported_input_understanding_hash_class": module_hash_class(
            "thought_core.input_understanding"
        ),
        "imported_loop_hash_class": module_hash_class("thought_core.loop"),
        "runtime_import_provenance_available": True,
        "running_child_input_understanding_import_provenance": module_provenance(
            "thought_core.input_understanding"
        ),
        "running_child_loop_import_provenance": module_provenance("thought_core.loop"),
        **marker_summary,
        "raw_private_publication_flags": raw_private_publication_flags(),
        "does_not_prove": [
            "runtime_turn_completed",
            "provider_quality",
            "motion_pv_aituber_dispatch",
            "self_mirror_capture",
            "home_control_action",
            "live_input_or_stt",
            "rr003_review_ready_or_representative_pass",
        ],
    }
    return payload


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
        return "no_provider_or_fallback_only_bound_by_running_child_env_class"
    if llm_class == "enabled_literal":
        return "provider_capable_enabled_in_running_child_env"
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


def module_hash_class(module_name: str) -> dict[str, Any]:
    module = importlib.import_module(module_name)
    source_path = module_source_path(module)
    if source_path is None:
        return {"available": False, "module": module_name, "sha256_16": "missing"}
    try:
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]
    except OSError:
        return {"available": False, "module": module_name, "sha256_16": "unreadable"}
    return {"available": True, "module": module_name, "sha256_16": digest}


def module_provenance(module_name: str) -> dict[str, Any]:
    hash_class = module_hash_class(module_name)
    return {
        "available": hash_class["available"],
        "module": module_name,
        "sha256_16": hash_class["sha256_16"],
        "path_publication_class": "private_path_not_shared",
        "collection_point": "running_thought_core_child_process",
    }


def module_source_path(module: ModuleType) -> Path | None:
    raw_path = getattr(module, "__file__", None)
    if not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_file() else None


def payload_marker_summary(
    *,
    top_level_text_present_class: str = "",
    top_level_text_marker_class: str = "",
    context_ref_payload_class: str = "",
) -> dict[str, str]:
    top_level_present = safe_label(top_level_text_present_class or "not_provided")
    marker_class = safe_label(top_level_text_marker_class or "not_provided")
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
        "payload_marker_class_preflight": marker_class,
        "payload_marker_hash_class": f"sha256_16:{marker_object_hash}",
        "payload_marker_preflight_source_class": "summary_class_supplied_no_raw_text",
        "standard_diagnostics_surface_class": "partial",
        "diagnostics_status_writer_surface": "missing_status_surface",
        "diagnostics_reader_surface": (
            "direct_child_endpoint_available_normal_status_reader_missing"
        ),
        "one_off_artifact_only": False,
        "remaining_standardization_gaps": [
            "normal_diagnostics_status_writer_not_integrated",
            "launch_manager_status_reader_not_integrated",
        ],
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


def safe_label(value: str) -> str:
    normalized = SAFE_LABEL_RE.sub("_", str(value or "").strip())
    normalized = normalized.strip("._:-")
    return normalized[:120] or "unknown"
