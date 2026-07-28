"""Runtime authority for the versioned closed-loop correlation contract."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from uuid import uuid4


CONTRACT_FILENAME = "closed-loop-correlation-feedback.v1.json"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:+-]{1,180}$")
SECRET_LIKE_STRING_PATTERN = re.compile(
    r"(?:"
    r"(?:OPENAI_API_KEY|API[_-]?KEY|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|"
    r"AUTHORIZATION|PASSWORD|SECRET|CREDENTIAL)(?:=|:)"
    r"[A-Za-z0-9._:+/-]{6,}"
    r"|sk-[A-Za-z0-9_-]{8,}"
    r"|Bearer(?:\s+|[:=_-]+)[A-Za-z0-9._-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{12,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|eyJ[A-Za-z0-9_-]{6,}\.eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"
    r")",
    re.IGNORECASE,
)
_TIMESTAMP_FIELDS = {"observed_at", "stale_after"}
_INTEGER_FIELDS = {"operation_revision"}
_BOOLEAN_DETAIL_FIELDS = {"late"}


@lru_cache(maxsize=1)
def load_closed_loop_contract() -> dict[str, Any]:
    path = _contract_path()
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("closed_loop_contract_not_object")
    _validate_contract_descriptor(value)
    return value


def closed_loop_enabled() -> bool:
    contract = load_closed_loop_contract()
    variable = str(contract["activation"]["environment_variable"])
    return os.environ.get(variable, "").strip().lower() in {"1", "true", "yes", "on"}


def materialize_closed_loop_event(
    candidate: Mapping[str, Any],
    *,
    event_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Issue canonical Thought Core event identity and validate safe content."""

    if "event_id" in candidate or "schema_version" in candidate:
        raise ValueError("closed_loop_event_identity_must_be_issued_by_thought_core")
    event = dict(candidate)
    event["schema_version"] = str(load_closed_loop_contract()["contract_version"])
    event["event_id"] = event_id or f"evt_{uuid4().hex}"
    event["observed_at"] = observed_at or str(event.get("observed_at") or _timestamp())
    return validate_closed_loop_event(event)


def materialize_closed_loop_output_ingress(
    candidate: Mapping[str, Any],
    *,
    event_id: str | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Validate the fixed HTTP output-ingress matrix and derive its authority."""

    if not isinstance(candidate, Mapping):
        raise ValueError("closed_loop_output_ingress_not_object")
    event_kind = candidate.get("event_kind")
    common_fields = {
        "event_kind",
        "session_id",
        "turn_id",
        "assistant_message_id",
        "details",
    }
    if event_kind == "output.dispatch_intent":
        allowed_fields = common_fields
    elif event_kind == "output.feedback":
        allowed_fields = common_fields | {"causal_parent_event_id"}
    else:
        raise ValueError("closed_loop_output_ingress_event_kind_invalid")
    if set(candidate) != allowed_fields:
        raise ValueError("closed_loop_output_ingress_envelope_invalid")

    details = candidate.get("details")
    if not isinstance(details, Mapping):
        raise ValueError("closed_loop_output_ingress_details_invalid")
    normalized_details = dict(details)
    profile_fields = {
        "phase",
        "outcome_class",
        "submission_class",
        "receipt_class",
        "cleanup_class",
        "proof_layer",
        "verification_class",
    }
    if set(normalized_details) != profile_fields | {"output_channel", "component"}:
        raise ValueError("closed_loop_output_ingress_details_invalid")

    contract = load_closed_loop_contract()
    ingress = contract["http_output_ingress"]
    channel = ingress["channels"].get(normalized_details["output_channel"])
    if not isinstance(channel, Mapping):
        raise ValueError("closed_loop_output_ingress_channel_invalid")
    permitted_components = {
        str(channel["component"]),
        *(str(component) for component in channel["additional_components"]),
    }
    if normalized_details["component"] not in permitted_components:
        raise ValueError("closed_loop_output_ingress_component_invalid")

    supplied_profile = {key: normalized_details[key] for key in profile_fields}
    permitted_profile_names = ingress["profiles"][event_kind]
    matched_profile_name = next(
        (
            profile_name
            for profile_name in permitted_profile_names
            if supplied_profile == contract["transition_profiles"][profile_name]
        ),
        None,
    )
    if matched_profile_name is None:
        raise ValueError("closed_loop_output_ingress_transition_invalid")

    source_authority = (
        ingress["dispatch_source_authority"]
        if event_kind == "output.dispatch_intent"
        or matched_profile_name == ingress["send_attempt_profile"]
        else channel["feedback_source_authority"]
    )
    canonical_candidate = {
        **dict(candidate),
        "source_authority": source_authority,
        "details": normalized_details,
    }
    return materialize_closed_loop_event(
        canonical_candidate,
        event_id=event_id,
        observed_at=observed_at,
    )


def validate_closed_loop_event(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one normalized event, rejecting raw/private or unbounded shapes."""

    contract = load_closed_loop_contract()
    if not isinstance(value, Mapping):
        raise ValueError("closed_loop_event_not_object")
    event = dict(value)
    envelope = contract["event_envelope"]
    required = set(envelope["required"])
    optional = set(envelope["optional"])
    allowed = required | optional
    if set(event) - allowed:
        raise ValueError("closed_loop_event_unknown_field")
    if required - set(event):
        raise ValueError("closed_loop_event_missing_field")
    if event.get("schema_version") != contract["contract_version"]:
        raise ValueError("closed_loop_event_schema_version_invalid")

    event_kind = event.get("event_kind")
    event_kinds = contract["event_kinds"]
    if event_kind not in event_kinds:
        raise ValueError("closed_loop_event_kind_invalid")
    event_rule = event_kinds[event_kind]
    if any(not event.get(field) for field in event_rule["requires"]):
        raise ValueError("closed_loop_event_identity_missing")

    for key, item in event.items():
        if key == "details":
            continue
        if key in _INTEGER_FIELDS:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError("closed_loop_event_integer_invalid")
            continue
        if not isinstance(item, str) or _SAFE_TOKEN.fullmatch(item) is None:
            raise ValueError("closed_loop_event_scalar_invalid")
        if SECRET_LIKE_STRING_PATTERN.search(item):
            raise ValueError("closed_loop_event_secret_like_string_rejected")
        if key in _TIMESTAMP_FIELDS:
            _validate_timestamp(item)

    source_authorities = contract["enums"]["source_authority"]
    if event["source_authority"] not in source_authorities:
        raise ValueError("closed_loop_event_source_authority_invalid")

    details = event.get("details")
    if not isinstance(details, Mapping):
        raise ValueError("closed_loop_event_details_not_object")
    normalized_details = dict(details)
    allowed_detail_fields = set(contract["details"]["allowed_fields"])
    if len(normalized_details) > int(contract["details"]["max_fields"]):
        raise ValueError("closed_loop_event_details_too_large")
    if set(normalized_details) - allowed_detail_fields:
        raise ValueError("closed_loop_event_detail_field_invalid")
    if set(event_rule["required_details"]) - set(normalized_details):
        raise ValueError("closed_loop_event_detail_missing")

    enum_values = contract["enums"]
    for key, item in normalized_details.items():
        if _unsafe_key(key, contract["forbidden_key_parts"]):
            raise ValueError("closed_loop_event_private_key_rejected")
        if key in _BOOLEAN_DETAIL_FIELDS:
            if not isinstance(item, bool):
                raise ValueError("closed_loop_event_detail_boolean_invalid")
            continue
        if not isinstance(item, str) or _SAFE_TOKEN.fullmatch(item) is None:
            raise ValueError("closed_loop_event_detail_scalar_invalid")
        if SECRET_LIKE_STRING_PATTERN.search(item):
            raise ValueError("closed_loop_event_secret_like_string_rejected")
        if key in enum_values and item not in enum_values[key]:
            raise ValueError("closed_loop_event_detail_enum_invalid")

    normalized = {key: event[key] for key in envelope["required"]}
    for key in envelope["optional"]:
        if key in event:
            normalized[key] = event[key]
    normalized["details"] = normalized_details
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > int(envelope["max_serialized_bytes"]):
        raise ValueError("closed_loop_event_serialized_size_invalid")
    return normalized


def _validate_contract_descriptor(value: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "activation",
        "identities",
        "event_envelope",
        "event_kinds",
        "details",
        "enums",
        "http_output_ingress",
        "transition_profiles",
        "limits",
        "provider_order",
        "forbidden_key_parts",
    }
    if set(value) != required:
        raise ValueError("closed_loop_contract_shape_invalid")
    if value.get("contract_version") != "closed-loop-correlation-feedback.v1":
        raise ValueError("closed_loop_contract_version_invalid")
    identities = value.get("identities")
    if not isinstance(identities, Mapping) or not identities:
        raise ValueError("closed_loop_contract_identities_invalid")
    for identity in identities.values():
        if not isinstance(identity, Mapping) or not isinstance(identity.get("issuer"), str):
            raise ValueError("closed_loop_contract_issuer_invalid")
    if not isinstance(value.get("event_kinds"), Mapping) or not value["event_kinds"]:
        raise ValueError("closed_loop_contract_event_kinds_invalid")
    ingress = value.get("http_output_ingress")
    expected_ingress = {
        "path": "/feedback/closed-loop",
        "dispatch_source_authority": "control_output_adapter",
        "send_attempt_profile": "send_attempt_started_outcome_unknown",
        "channels": {
            "display": {
                "component": "aituber_direct_send",
                "additional_components": ["aituber_message_store"],
                "feedback_source_authority": "display_transport",
            },
            "tts": {
                "component": "tts_chunk_post",
                "additional_components": ["aituber_tts_synthesis"],
                "feedback_source_authority": "tts_transport",
            },
        },
        "profiles": {
            "output.dispatch_intent": ["dispatch_intent_recorded"],
            "output.feedback": [
                "send_attempt_started_outcome_unknown",
                "submission_ack_needs_feedback",
                "possible_send_timeout",
                "dispatch_rejected_before_send",
            ],
        },
    }
    if ingress != expected_ingress:
        raise ValueError("closed_loop_contract_http_output_ingress_invalid")


def _unsafe_key(key: str, forbidden_parts: list[str]) -> bool:
    lowered = key.lower().replace("-", "_")
    exact = {str(part).lower().replace("-", "_") for part in forbidden_parts}
    if lowered in exact or lowered.startswith("raw_"):
        return True
    if lowered.endswith(("_path", "_url", "_uri")):
        return True
    sensitive = {
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "confirmation_token",
        "secret",
        "password",
        "credential",
    }
    return any(part in lowered for part in sensitive)


def _validate_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("closed_loop_event_timestamp_invalid") from exc


def _contract_path() -> Path:
    return _repo_root_from_here() / "contracts" / "turn" / CONTRACT_FILENAME


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[4]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
