"""Read-only adapter from the Home action catalog to agentic capabilities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .agentic_turn_provider import (
    MAX_CAPABILITY_VIEW_COUNT,
    MAX_CATALOG_ID_LENGTH,
    MAX_CATALOG_VERSION_LENGTH,
    AgenticCapabilityView,
    AgenticCapabilityViewEntry,
)


MAX_CAPABILITY_ID_CHARS = 96
MAX_CAPABILITY_DESCRIPTION_CHARS = 180


class CapabilityCatalogError(Exception):
    """The read-only Home capability catalog cannot authorize a decision."""


@dataclass(frozen=True)
class HomeCapabilityCatalog:
    """Expose only existing Home action rows as zero-argument capabilities."""

    catalog_id: str
    catalog_version: str
    actions: Mapping[str, Mapping[str, object]]
    capability_view: AgenticCapabilityView

    @classmethod
    def from_default_path(cls) -> "HomeCapabilityCatalog":
        repository_root = Path(__file__).resolve().parents[4]
        return cls.from_path(repository_root / "catalogs" / "actions" / "home-actions.json")

    @classmethod
    def from_path(cls, path: Path) -> "HomeCapabilityCatalog":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CapabilityCatalogError("home_capability_catalog_unavailable") from exc
        if (
            type(payload) is not dict
            or type(payload.get("catalog_id")) is not str
            or not payload["catalog_id"]
            or len(payload["catalog_id"]) > MAX_CATALOG_ID_LENGTH
            or type(payload.get("catalog_version")) is not str
            or not payload["catalog_version"]
            or len(payload["catalog_version"]) > MAX_CATALOG_VERSION_LENGTH
            or type(payload.get("actions")) is not dict
            or len(payload["actions"]) > MAX_CAPABILITY_VIEW_COUNT
        ):
            raise CapabilityCatalogError("home_capability_catalog_invalid")

        actions: dict[str, Mapping[str, object]] = {}
        for capability_id, row in payload["actions"].items():
            if (
                type(capability_id) is not str
                or not capability_id
                or len(capability_id) > MAX_CAPABILITY_ID_CHARS
                or type(row) is not dict
            ):
                raise CapabilityCatalogError("home_capability_catalog_invalid")
            if not _valid_action_row(row):
                raise CapabilityCatalogError("home_capability_catalog_invalid")
            actions[capability_id] = _freeze_catalog_mapping(row)
        if not actions:
            raise CapabilityCatalogError("home_capability_catalog_invalid")
        catalog_id = payload["catalog_id"]
        catalog_version = payload["catalog_version"]
        snapshot = MappingProxyType(actions)
        capability_view = AgenticCapabilityView(
            catalog_id=catalog_id,
            catalog_version=catalog_version,
            capabilities=tuple(
                AgenticCapabilityViewEntry(
                    capability_id=capability_id,
                    description=str(row["label"]),
                    available=_is_currently_available(row),
                )
                for capability_id, row in sorted(snapshot.items())
            ),
        )
        return cls(
            catalog_id=catalog_id,
            catalog_version=catalog_version,
            actions=snapshot,
            capability_view=capability_view,
        )

    def authorizes(self, capability_id: str, arguments: Mapping[str, object]) -> bool:
        """Exact3 callback: catalog rows have no argument surface in this slice."""

        row = self.actions.get(capability_id)
        return row is not None and _is_currently_available(row) and len(arguments) == 0

    def action_for(
        self,
        capability_id: str,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        if not self.authorizes(capability_id, arguments):
            raise CapabilityCatalogError("home_capability_not_authorized")
        row = self.actions[capability_id]
        expected_effect = row.get("expected_effect")
        observation = row.get("observation")
        if not isinstance(expected_effect, Mapping) or not isinstance(observation, Mapping):
            raise CapabilityCatalogError("home_capability_catalog_invalid")

        target = str(row["target"])
        aliases = [target, capability_id]
        if target == "light":
            aliases.append("living_room_light")
        action: dict[str, Any] = {
            "action": f"home.{capability_id}",
            "action_id": capability_id,
            "agentic_capability_id": capability_id,
            "capability_arguments": dict(arguments),
            "target": target,
            "target_aliases": aliases,
            "target_name": str(row["target_label"]),
            "confidence": 1.0,
            "expected_state": str(row["expected_state"]),
            "expected_effect": dict(expected_effect),
            "pre_action_phrase": str(row["pre_action_phrase"]),
            "confirm_required": bool(row["confirmation_required"]),
            "confirmation_reason": row.get("confirmation_reason"),
            "available": True,
            "noop": False,
            "semantic_authority": "agentic_provider",
            "control_type": expected_effect.get("control_type", ""),
            "state_authority": expected_effect.get("state_authority", ""),
            "verification_mode": expected_effect.get("verification_mode", ""),
            "state_tracking": observation.get("state_path", ""),
        }
        return action


def _valid_action_row(row: Mapping[str, object]) -> bool:
    required_text = (
        "label",
        "target",
        "target_label",
        "pre_action_phrase",
        "expected_state",
    )
    return (
        all(
            type(row.get(key)) is str
            and row[key]
            and len(str(row[key])) <= MAX_CAPABILITY_DESCRIPTION_CHARS
            for key in required_text
        )
        and type(row.get("confirmation_required")) is bool
        and type(row.get("expected_effect")) is dict
        and type(row.get("observation")) is dict
    )


def _is_currently_available(row: Mapping[str, object]) -> bool:
    """Retired or explicitly unavailable rows never enter normal AI authority."""

    natural_language_status = row.get("natural_language_status")
    if natural_language_status not in {None, "", "current"}:
        return False
    available = row.get("available")
    if available is not None and available is not True:
        return False
    availability = row.get("availability")
    return availability in {None, "", "current", "available"}


def _freeze_catalog_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(
        {str(key): _freeze_catalog_value(value) for key, value in values.items()}
    )


def _freeze_catalog_value(value: object) -> object:
    if isinstance(value, dict):
        return _freeze_catalog_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_catalog_value(item) for item in value)
    return value
