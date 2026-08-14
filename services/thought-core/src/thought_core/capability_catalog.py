"""Read-only adapters from product catalogs to agentic capabilities."""

from __future__ import annotations

import json
import math
import re
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
from .correlation_feedback_contract import SECRET_LIKE_STRING_PATTERN


MAX_CAPABILITY_ID_CHARS = 96
MAX_CAPABILITY_DESCRIPTION_CHARS = 180
MAX_CAPABILITY_ALIAS_COUNT = 16
MAX_CAPABILITY_ALIAS_CHARS = 64
AGENTIC_CATALOG_ID = "sword.agentic-capabilities"
AGENTIC_CATALOG_VERSION = "agentic-capabilities.v1"
_PRIVATE_CATALOG_ALIAS_PREFIX = re.compile(
    r"^(?:bearer|token|secret|credential|password|api[\s_-]*key|"
    r"access[\s_-]*token|refresh[\s_-]*token)(?:\s+|[:=_-]+)",
    re.IGNORECASE,
)
_CATALOG_PROMPT_PREFIX = re.compile(
    r"^(?:system|developer)[\s_-]+prompt(?:\s+|[:=_-]+|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProjectionCapabilitySpec:
    action: str
    effect_id: str | None
    description: str


_PROJECTION_CAPABILITIES = MappingProxyType(
    {
        "projection.fire.start": ProjectionCapabilitySpec(
            action="start",
            effect_id="fire",
            description=(
                "Projection VisualのFireを開始。argumentsは省略可。指定時は"
                "position{x,y:-1..1}, strength:0..1, durationMs:500..12000のみ。"
            ),
        ),
        "projection.thunder.start": ProjectionCapabilitySpec(
            action="start",
            effect_id="thunderBall",
            description=(
                "Projection VisualのThunderを開始。argumentsは省略可。指定時は"
                "position{x,y:-1..1}, strength:0..1, durationMs:500..12000のみ。"
            ),
        ),
        "projection.effect.stop": ProjectionCapabilitySpec(
            action="stop",
            effect_id=None,
            description=(
                "現在のProjection Visualエフェクトへの直接の停止要求に使用。"
                "argumentsは空のみ。停止完了は下流receiptが所有。"
            ),
        ),
        "projection.effect.reset": ProjectionCapabilitySpec(
            action="reset",
            effect_id=None,
            description=(
                "Projection Visualエフェクトへの直接のReset要求に使用。"
                "argumentsは空のみ。Reset完了は下流receiptが所有。"
            ),
        ),
    }
)


class CapabilityCatalogError(Exception):
    """The read-only product capability catalog cannot authorize a decision."""


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
                    description=_agentic_home_description(row),
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


@dataclass(frozen=True)
class AgenticCapabilityCatalog:
    """Compose Home actions and the existing Projection event surface."""

    home: HomeCapabilityCatalog
    capability_view: AgenticCapabilityView

    @classmethod
    def from_default_path(cls) -> "AgenticCapabilityCatalog":
        return cls.from_home_catalog(HomeCapabilityCatalog.from_default_path())

    @classmethod
    def from_home_catalog(
        cls,
        home: HomeCapabilityCatalog,
    ) -> "AgenticCapabilityCatalog":
        projection_entries = tuple(
            AgenticCapabilityViewEntry(
                capability_id=capability_id,
                description=spec.description,
                available=True,
            )
            for capability_id, spec in sorted(_PROJECTION_CAPABILITIES.items())
        )
        combined = home.capability_view.capabilities + projection_entries
        if len(combined) > MAX_CAPABILITY_VIEW_COUNT:
            raise CapabilityCatalogError("agentic_capability_catalog_over_limit")
        if any(capability_id in home.actions for capability_id in _PROJECTION_CAPABILITIES):
            raise CapabilityCatalogError("agentic_capability_catalog_collision")
        return cls(
            home=home,
            capability_view=AgenticCapabilityView(
                catalog_id=AGENTIC_CATALOG_ID,
                catalog_version=AGENTIC_CATALOG_VERSION,
                capabilities=combined,
            ),
        )

    def authorizes(self, capability_id: str, arguments: Mapping[str, object]) -> bool:
        if capability_id in self.home.actions:
            return self.home.authorizes(capability_id, arguments)
        spec = _PROJECTION_CAPABILITIES.get(capability_id)
        return spec is not None and _valid_projection_arguments(spec, arguments)

    def action_for(
        self,
        capability_id: str,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        if capability_id in self.home.actions:
            return self.home.action_for(capability_id, arguments)
        spec = _PROJECTION_CAPABILITIES.get(capability_id)
        if spec is None or not _valid_projection_arguments(spec, arguments):
            raise CapabilityCatalogError("agentic_capability_not_authorized")
        canonical_arguments = _canonical_projection_arguments(spec, arguments)
        return {
            "route_kind": "projection_effect",
            "action": spec.action,
            "effect_id": spec.effect_id or "",
            "action_id": capability_id,
            "agentic_capability_id": capability_id,
            "capability_arguments": canonical_arguments,
            "semantic_authority": "agentic_provider",
        }


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
        and _valid_catalog_aliases(row.get("aliases"))
        and type(row.get("confirmation_required")) is bool
        and type(row.get("expected_effect")) is dict
        and type(row.get("observation")) is dict
    )


def _valid_catalog_aliases(value: object) -> bool:
    return (
        type(value) is list
        and len(value) <= MAX_CAPABILITY_ALIAS_COUNT
        and all(
            type(alias) is str
            and bool(alias.strip())
            and alias == alias.strip()
            and len(alias) <= MAX_CAPABILITY_ALIAS_CHARS
            and not _unsafe_catalog_alias(alias)
            for alias in value
        )
    )


def _unsafe_catalog_alias(value: str) -> bool:
    lowered = value.lower()
    return (
        any(ord(character) < 32 or ord(character) == 127 for character in value)
        or SECRET_LIKE_STRING_PATTERN.search(value) is not None
        or _PRIVATE_CATALOG_ALIAS_PREFIX.search(value) is not None
        or _CATALOG_PROMPT_PREFIX.search(value) is not None
        or "://" in lowered
        or ":\\" in value
        or value.startswith("\\\\")
        or "/" in value
        or "\\" in value
        or "access_token=" in lowered
        or "api_key=" in lowered
        or "password=" in lowered
        or "prompt:" in lowered
    )


def _agentic_home_description(row: Mapping[str, object]) -> str:
    aliases = row.get("aliases")
    if not isinstance(aliases, tuple) or not all(type(alias) is str for alias in aliases):
        raise CapabilityCatalogError("home_capability_catalog_invalid")
    description = (
        f"{row['label']}。対象名:{row['target_label']}。"
        f"呼称:{'、'.join(aliases)}"
    )
    if len(description) > MAX_CAPABILITY_DESCRIPTION_CHARS:
        raise CapabilityCatalogError("home_capability_catalog_invalid")
    return description


def _valid_projection_arguments(
    spec: ProjectionCapabilitySpec,
    arguments: Mapping[str, object],
) -> bool:
    if spec.action in {"stop", "reset"}:
        return len(arguments) == 0
    if set(arguments) - {"position", "strength", "durationMs"}:
        return False
    if "position" in arguments:
        position = arguments["position"]
        if not isinstance(position, Mapping) or set(position) != {"x", "y"}:
            return False
        if not all(_bounded_number(position.get(axis), -1.0, 1.0) for axis in ("x", "y")):
            return False
    if "strength" in arguments:
        strength = arguments["strength"]
        if not _bounded_number(strength, 0.0, 1.0):
            return False
    if "durationMs" in arguments:
        duration_ms = arguments["durationMs"]
        if type(duration_ms) is not int or not 500 <= duration_ms <= 12_000:
            return False
    return True


def _bounded_number(value: object, minimum: float, maximum: float) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _canonical_projection_arguments(
    spec: ProjectionCapabilitySpec,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    if not _valid_projection_arguments(spec, arguments):
        raise CapabilityCatalogError("agentic_capability_not_authorized")
    canonical: dict[str, object] = {}
    position = arguments.get("position")
    if isinstance(position, Mapping):
        canonical["position"] = {
            "x": float(position["x"]),
            "y": float(position["y"]),
        }
    if "strength" in arguments:
        canonical["strength"] = float(arguments["strength"])
    if "durationMs" in arguments:
        canonical["durationMs"] = int(arguments["durationMs"])
    return canonical


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
