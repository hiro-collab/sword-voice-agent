"""Deterministic, text-free PerformancePlan V1 validation."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


SCHEMA_VERSION = 1
MIN_DURATION_MS = 500
MAX_DURATION_MS = 12_000
MAX_KEYFRAMES = 4
MAX_ID_LENGTH = 128
MAX_REVISION = 2_147_483_647
MAX_SEED = 2_147_483_647

PerformancePlanEffectId = Literal["fire", "thunderBall"]

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PLAN_FIELDS = frozenset(
    {
        "schemaVersion",
        "planId",
        "sessionId",
        "revision",
        "action",
        "effectId",
        "position",
        "strength",
        "durationMs",
        "seed",
        "keyframes",
    }
)
_POSITION_FIELDS = frozenset({"x", "y"})
_KEYFRAME_FIELDS = frozenset({"atMs", "position", "strength"})
_PUBLIC_ERROR_CLASS = "projection_performance_plan_invalid"
_PUBLIC_ERROR_REASON = "invalid_performance_plan"


class ProjectionPerformancePlanValidationError(ValueError):
    """Fixed, non-echoing public validation error."""

    code = _PUBLIC_ERROR_CLASS
    reason = _PUBLIC_ERROR_REASON

    def __init__(self) -> None:
        super().__init__(_PUBLIC_ERROR_REASON)

    def public_payload(self) -> dict[str, str]:
        return {"class": self.code, "reason": self.reason}


@dataclass(frozen=True)
class PerformancePlanPosition:
    x: float
    y: float


@dataclass(frozen=True)
class PerformancePlanKeyframe:
    at_ms: int
    position: PerformancePlanPosition
    strength: float


@dataclass(frozen=True)
class ProjectionPerformancePlan:
    schema_version: int
    plan_id: str
    session_id: str
    revision: int
    action: Literal["start"]
    effect_id: PerformancePlanEffectId
    position: PerformancePlanPosition
    strength: float
    duration_ms: int
    seed: int
    keyframes: tuple[PerformancePlanKeyframe, ...]

    def to_payload(self) -> dict[str, object]:
        return _validated_plan_payload(self)

    def canonical_json(self) -> str:
        return _dump_validated_payload(_validated_plan_payload(self))


def validate_projection_performance_plan(
    candidate: object,
) -> ProjectionPerformancePlan:
    """Validate one complete V1 plan or fail atomically with a fixed error."""

    try:
        return _validate_plan(candidate)
    except ProjectionPerformancePlanValidationError:
        raise
    except Exception:
        raise ProjectionPerformancePlanValidationError() from None


def canonical_projection_performance_plan_json(candidate: object) -> str:
    """Return deterministic JSON only after complete validation."""

    return _dump_validated_payload(_validated_plan_payload(candidate))


def _validate_plan(candidate: object) -> ProjectionPerformancePlan:
    if isinstance(candidate, ProjectionPerformancePlan):
        _guard_plan_instance_shape(candidate)
        candidate = _plan_payload_unchecked(candidate)
    mapping = _exact_mapping(candidate, _PLAN_FIELDS)

    if (
        type(mapping["schemaVersion"]) is not int
        or mapping["schemaVersion"] != SCHEMA_VERSION
    ):
        _reject()
    plan_id = _bounded_id(mapping["planId"])
    session_id = _bounded_id(mapping["sessionId"])
    revision = _bounded_integer(mapping["revision"], minimum=1, maximum=MAX_REVISION)
    if type(mapping["action"]) is not str or mapping["action"] != "start":
        _reject()

    effect_id = mapping["effectId"]
    if type(effect_id) is not str or effect_id not in {"fire", "thunderBall"}:
        _reject()

    position = _position(mapping["position"])
    strength = _bounded_number(mapping["strength"], minimum=0.0, maximum=1.0)
    duration_ms = _bounded_integer(
        mapping["durationMs"],
        minimum=MIN_DURATION_MS,
        maximum=MAX_DURATION_MS,
    )
    seed = _bounded_integer(mapping["seed"], minimum=0, maximum=MAX_SEED)
    keyframes = _keyframes(mapping["keyframes"], duration_ms=duration_ms)

    return ProjectionPerformancePlan(
        schema_version=SCHEMA_VERSION,
        plan_id=plan_id,
        session_id=session_id,
        revision=revision,
        action="start",
        effect_id=effect_id,
        position=position,
        strength=strength,
        duration_ms=duration_ms,
        seed=seed,
        keyframes=keyframes,
    )


def _guard_plan_instance_shape(plan: ProjectionPerformancePlan) -> None:
    if type(plan) is not ProjectionPerformancePlan:
        _reject()
    if type(plan.position) is not PerformancePlanPosition:
        _reject()

    keyframes = plan.keyframes
    if type(keyframes) is not tuple:
        _reject()
    if not 1 <= len(keyframes) <= MAX_KEYFRAMES:
        _reject()
    for keyframe in keyframes:
        if type(keyframe) is not PerformancePlanKeyframe:
            _reject()
        if type(keyframe.position) is not PerformancePlanPosition:
            _reject()


def _validated_plan_payload(candidate: object) -> dict[str, object]:
    try:
        validated = validate_projection_performance_plan(candidate)
        return _plan_payload_unchecked(validated)
    except ProjectionPerformancePlanValidationError:
        raise
    except Exception:
        raise ProjectionPerformancePlanValidationError() from None


def _plan_payload_unchecked(plan: ProjectionPerformancePlan) -> dict[str, object]:
    return {
        "schemaVersion": plan.schema_version,
        "planId": plan.plan_id,
        "sessionId": plan.session_id,
        "revision": plan.revision,
        "action": plan.action,
        "effectId": plan.effect_id,
        "position": _position_payload_unchecked(plan.position),
        "strength": plan.strength,
        "durationMs": plan.duration_ms,
        "seed": plan.seed,
        "keyframes": [
            _keyframe_payload_unchecked(keyframe) for keyframe in plan.keyframes
        ],
    }


def _position_payload_unchecked(
    position: PerformancePlanPosition,
) -> dict[str, float]:
    return {"x": position.x, "y": position.y}


def _keyframe_payload_unchecked(
    keyframe: PerformancePlanKeyframe,
) -> dict[str, object]:
    return {
        "atMs": keyframe.at_ms,
        "position": _position_payload_unchecked(keyframe.position),
        "strength": keyframe.strength,
    }


def _dump_validated_payload(payload: dict[str, object]) -> str:
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception:
        raise ProjectionPerformancePlanValidationError() from None


def _exact_mapping(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject()
    if len(value) != len(fields) or set(value.keys()) != fields:
        _reject()
    return value


def _bounded_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_ID_LENGTH
        or _ID_PATTERN.fullmatch(value) is None
    ):
        _reject()
    return value


def _bounded_integer(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()
    return value


def _bounded_number(value: object, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject()
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        _reject()
    return number


def _position(value: object) -> PerformancePlanPosition:
    mapping = _exact_mapping(value, _POSITION_FIELDS)
    return PerformancePlanPosition(
        x=_bounded_number(mapping["x"], minimum=-1.0, maximum=1.0),
        y=_bounded_number(mapping["y"], minimum=-1.0, maximum=1.0),
    )


def _keyframes(
    value: object, *, duration_ms: int
) -> tuple[PerformancePlanKeyframe, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_KEYFRAMES:
        _reject()

    keyframes: list[PerformancePlanKeyframe] = []
    previous_at_ms = -1
    for item in value:
        mapping = _exact_mapping(item, _KEYFRAME_FIELDS)
        at_ms = _bounded_integer(mapping["atMs"], minimum=0, maximum=duration_ms)
        if at_ms <= previous_at_ms:
            _reject()
        keyframes.append(
            PerformancePlanKeyframe(
                at_ms=at_ms,
                position=_position(mapping["position"]),
                strength=_bounded_number(
                    mapping["strength"], minimum=0.0, maximum=1.0
                ),
            )
        )
        previous_at_ms = at_ms
    return tuple(keyframes)


def _reject() -> None:
    raise ProjectionPerformancePlanValidationError()
