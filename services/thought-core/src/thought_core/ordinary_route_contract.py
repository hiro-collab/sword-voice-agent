"""Single machine-readable authority for the ordinary standard route."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


CONTRACT_RELATIVE_PATH = Path("contracts/turn/ordinary-standard-route.v1.json")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def contract_path() -> Path:
    return repository_root() / CONTRACT_RELATIVE_PATH


def load_ordinary_route_contract(path: Path | None = None) -> dict[str, Any]:
    selected = path or contract_path()
    raw = selected.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("ordinary_route_contract_invalid")
    _validate_contract(value)
    return value


def ordinary_route_contract_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or contract_path()).read_bytes()).hexdigest()


def max_route_deadline_seconds() -> float:
    return float(load_ordinary_route_contract()["deadlines"]["server_seconds"])


def route_deadline_header() -> str:
    return str(load_ordinary_route_contract()["deadlines"]["header"])


def review_checkpoint_class(
    tracking: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None,
    *,
    unavailable: bool = False,
) -> str:
    contract = load_ordinary_route_contract()
    classes = contract["review_checkpoint"]["classes"]
    authorities = set(contract["review_checkpoint"]["authority_values"])
    if not isinstance(tracking, Mapping):
        return str(classes["not_checked"])
    if unavailable:
        return str(classes["unavailable"])
    if not isinstance(payload, Mapping):
        return str(classes["pending"])
    if str(payload.get("action_id") or "") != str(tracking.get("action_id") or ""):
        return str(classes["action_id_mismatch"])
    if str(payload.get("status") or "") != "matched":
        return str(classes["status_not_matched"])
    if str(payload.get("state_tracking") or "") != "tracked":
        return str(classes["tracking_not_tracked"])
    if str(payload.get("verification_mode") or "") != "ha_state":
        return str(classes["verification_mode_mismatch"])
    if str(payload.get("state_authority") or "") not in authorities:
        return str(classes["authority_mismatch"])
    return str(classes["matched"])


def review_checkpoint_payload(checkpoint_class: str) -> dict[str, str]:
    contract = load_ordinary_route_contract()
    checkpoint = contract["review_checkpoint"]
    allowed = set(checkpoint["classes"].values())
    safe_class = checkpoint_class if checkpoint_class in allowed else checkpoint["classes"]["not_checked"]
    return {
        "schema_version": str(checkpoint["schema_version"]),
        "review_checkpoint_class": str(safe_class),
    }


def canonical_review_match_required(*sources: Mapping[str, Any]) -> bool:
    contract = load_ordinary_route_contract()
    checkpoint = contract["review_checkpoint"]
    tracking_value = str(checkpoint["state_tracking_value"])
    verification_value = str(checkpoint["verification_mode_value"])
    authorities = set(checkpoint["authority_values"])
    for source in sources:
        if (
            str(source.get("state_tracking") or "") == tracking_value
            or str(source.get("verification_mode") or "") == verification_value
            or str(source.get("state_authority") or "") in authorities
        ):
            return True
    return False


def review_checkpoint_from_observation(observation: Mapping[str, Any]) -> str:
    contract = load_ordinary_route_contract()
    fallback = str(contract["review_checkpoint"]["classes"]["not_checked"])
    checkpoint = observation.get("review_checkpoint")
    if not isinstance(checkpoint, Mapping):
        return fallback
    value = str(checkpoint.get("review_checkpoint_class") or "")
    allowed = set(contract["review_checkpoint"]["classes"].values())
    if value in allowed:
        return value
    return str(contract["review_checkpoint"]["classes"]["unavailable"])


def _validate_contract(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != "ordinary_standard_route.v1":
        raise RuntimeError("ordinary_route_contract_version_invalid")
    deadlines = value.get("deadlines")
    if not isinstance(deadlines, Mapping):
        raise RuntimeError("ordinary_route_contract_deadlines_invalid")
    server = deadlines.get("server_seconds")
    client = deadlines.get("client_seconds")
    if (
        isinstance(server, bool)
        or not isinstance(server, (int, float))
        or isinstance(client, bool)
        or not isinstance(client, (int, float))
        or not (0 < float(client) < float(server) <= 75)
    ):
        raise RuntimeError("ordinary_route_contract_deadline_relation_invalid")
    review = value.get("review_checkpoint")
    if not isinstance(review, Mapping) or not isinstance(review.get("classes"), Mapping):
        raise RuntimeError("ordinary_route_contract_review_invalid")
    if (
        review.get("state_tracking_value") != "tracked"
        or review.get("verification_mode_value") != "ha_state"
    ):
        raise RuntimeError("ordinary_route_contract_review_binding_invalid")
    classes = list(review["classes"].values())
    if len(classes) != len(set(classes)) or len(classes) != 10:
        raise RuntimeError("ordinary_route_contract_review_classes_invalid")
