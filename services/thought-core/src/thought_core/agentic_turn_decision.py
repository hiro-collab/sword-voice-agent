"""Bounded, side-effect-free validation for AgenticTurnDecision V1.

V1 permits one capability call as an atomic decision boundary.  It is not a
claim that future cybernetic composition is limited to one operation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from .agentic_turn_provider import AgenticDecisionValidationSubcode


SCHEMA_VERSION = 1
MAX_RESPONSE_LENGTH = 600
MAX_CAPABILITY_ID_LENGTH = 96
MAX_ARGUMENT_DEPTH = 4
MAX_ARGUMENT_NODES = 64
MAX_CONTAINER_ITEMS = 16
MAX_ARGUMENT_KEY_LENGTH = 64
MAX_ARGUMENT_STRING_LENGTH = 512
MAX_ARGUMENT_NUMBER_ABS = 1_000_000

AgenticTurnDecisionKind = Literal[
    "conversation",
    "clarification",
    "hold",
    "capability",
]
AgenticTurnDecisionStatus = Literal["accepted", "rejected"]
AgenticTurnDecisionReason = Literal["invalid_decision"]

_KINDS = frozenset({"conversation", "clarification", "hold", "capability"})
_BASE_FIELDS = frozenset({"schemaVersion", "kind", "response"})
_CAPABILITY_FIELDS = _BASE_FIELDS | {"capability"}
_RESPONSE_FIELDS = frozenset({"speech", "display"})
_CALL_FIELDS = frozenset({"id", "arguments"})
_CAPABILITY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")

CapabilityCatalogValidator = Callable[[str, Mapping[str, object]], object]


@dataclass(frozen=True)
class AgenticTurnResponse:
    speech: str
    display: str


@dataclass(frozen=True)
class AgenticCapabilityCall:
    capability_id: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class AgenticTurnDecision:
    schema_version: Literal[1]
    kind: AgenticTurnDecisionKind
    response: AgenticTurnResponse
    capability: AgenticCapabilityCall | None


@dataclass(frozen=True)
class AgenticTurnDecisionValidationResult:
    status: AgenticTurnDecisionStatus
    reason: AgenticTurnDecisionReason | None
    decision: AgenticTurnDecision | None
    validation_subcode: AgenticDecisionValidationSubcode | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted" and self.decision is not None


class _InvalidDecision(Exception):
    """Internal text-free rejection signal."""

    __slots__ = ("validation_subcode",)

    def __init__(self, validation_subcode: AgenticDecisionValidationSubcode) -> None:
        self.validation_subcode = validation_subcode
        super().__init__("invalid_decision")


@dataclass
class _NodeBudget:
    count: int = 0

    def add(self) -> None:
        self.count += 1
        if self.count > MAX_ARGUMENT_NODES:
            _reject("capability_shape_invalid")


@dataclass
class _MutationGuard:
    attempted: bool = False

    def reject(self) -> None:
        self.attempted = True
        raise TypeError("immutable")


@dataclass(frozen=True)
class _GuardedObject(Mapping[str, object]):
    _items: tuple[tuple[str, object], ...]
    _guard: _MutationGuard = field(compare=False, repr=False)

    def __getitem__(self, key: str) -> object:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __setitem__(self, key: str, value: object) -> None:
        self._guard.reject()

    def __delitem__(self, key: str) -> None:
        self._guard.reject()

    def clear(self) -> None:
        self._guard.reject()

    def pop(self, key: str, default: object = None) -> object:
        self._guard.reject()

    def popitem(self) -> tuple[str, object]:
        self._guard.reject()

    def setdefault(self, key: str, default: object = None) -> object:
        self._guard.reject()

    def update(self, *args: object, **kwargs: object) -> None:
        self._guard.reject()


@dataclass(frozen=True)
class _GuardedArray(Sequence[object]):
    _items: tuple[object, ...]
    _guard: _MutationGuard = field(compare=False, repr=False)

    def __getitem__(self, index: int | slice) -> object:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __setitem__(self, index: int, value: object) -> None:
        self._guard.reject()

    def __delitem__(self, index: int) -> None:
        self._guard.reject()

    def append(self, value: object) -> None:
        self._guard.reject()

    def clear(self) -> None:
        self._guard.reject()

    def extend(self, values: object) -> None:
        self._guard.reject()

    def insert(self, index: int, value: object) -> None:
        self._guard.reject()

    def pop(self, index: int = -1) -> object:
        self._guard.reject()

    def remove(self, value: object) -> None:
        self._guard.reject()

    def reverse(self) -> None:
        self._guard.reject()

    def sort(self, *args: object, **kwargs: object) -> None:
        self._guard.reject()


def validate_agentic_turn_decision(
    candidate: object,
    *,
    capability_catalog_validator: CapabilityCatalogValidator | None = None,
) -> AgenticTurnDecisionValidationResult:
    """Validate one complete decision without executing it or exposing errors.

    Capability decisions require a caller-owned catalog validator.  It is
    invoked at most once with the canonical id and a deeply immutable argument
    snapshot, and acceptance requires the exact singleton ``True``.
    """

    try:
        return _validate(candidate, capability_catalog_validator)
    except _InvalidDecision as exc:
        return _rejected(exc.validation_subcode)
    except Exception:
        return _rejected("validation_internal")


def _validate(
    candidate: object,
    capability_catalog_validator: CapabilityCatalogValidator | None,
) -> AgenticTurnDecisionValidationResult:
    if type(candidate) is not dict:
        _reject("candidate_not_object")
    mapping = _exact_object_shell(
        candidate,
        allowed=_CAPABILITY_FIELDS,
        subcode="decision_shape_invalid",
    )

    schema_version = mapping.get("schemaVersion")
    kind = mapping.get("kind")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        _reject("decision_shape_invalid")
    if type(kind) is not str or kind not in _KINDS:
        _reject("decision_shape_invalid")

    expected_fields = _CAPABILITY_FIELDS if kind == "capability" else _BASE_FIELDS
    if frozenset(mapping) != expected_fields:
        _reject("decision_shape_invalid")

    response = _validate_response(mapping["response"])
    capability: AgenticCapabilityCall | None = None
    if kind == "capability":
        call = _exact_object(
            mapping["capability"],
            _CALL_FIELDS,
            subcode="capability_shape_invalid",
        )
        capability_id = _validate_capability_id(call["id"])
        raw_arguments = call["arguments"]
        if type(raw_arguments) is not dict:
            _reject("capability_shape_invalid")

        canonical_arguments = _canonical_json(
            raw_arguments,
            depth=1,
            budget=_NodeBudget(),
        )
        if type(canonical_arguments) is not dict:
            _reject("capability_shape_invalid")
        immutable_arguments = _immutable_json(canonical_arguments)
        if not isinstance(immutable_arguments, Mapping):
            _reject("capability_shape_invalid")
        guard = _MutationGuard()
        guarded_arguments = _guarded_json(canonical_arguments, guard)
        if not isinstance(guarded_arguments, _GuardedObject):
            _reject("capability_shape_invalid")
        if capability_catalog_validator is None:
            _reject("catalog_rejected")

        catalog_result = capability_catalog_validator(
            capability_id,
            guarded_arguments,
        )
        if guard.attempted or catalog_result is not True:
            _reject("catalog_rejected")
        capability = AgenticCapabilityCall(
            capability_id=capability_id,
            arguments=immutable_arguments,
        )

    decision = AgenticTurnDecision(
        schema_version=1,
        kind=kind,
        response=response,
        capability=capability,
    )
    return AgenticTurnDecisionValidationResult(
        status="accepted",
        reason=None,
        decision=decision,
        validation_subcode=None,
    )


def _exact_object_shell(
    value: object,
    *,
    allowed: frozenset[str],
    subcode: AgenticDecisionValidationSubcode,
) -> dict[str, object]:
    if type(value) is not dict:
        _reject(subcode)
    if not 1 <= len(value) <= len(allowed):
        _reject(subcode)
    for key in value:
        if type(key) is not str or key not in allowed:
            _reject(subcode)
    return value


def _exact_object(
    value: object,
    fields: frozenset[str],
    *,
    subcode: AgenticDecisionValidationSubcode,
) -> dict[str, object]:
    mapping = _exact_object_shell(value, allowed=fields, subcode=subcode)
    if frozenset(mapping) != fields:
        _reject(subcode)
    return mapping


def _validate_response(value: object) -> AgenticTurnResponse:
    mapping = _exact_object(value, _RESPONSE_FIELDS, subcode="response_invalid")
    return AgenticTurnResponse(
        speech=_bounded_response_text(mapping["speech"]),
        display=_bounded_response_text(mapping["display"]),
    )


def _bounded_response_text(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_RESPONSE_LENGTH
        or not value.strip()
    ):
        _reject("response_invalid")
    return value


def _validate_capability_id(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_CAPABILITY_ID_LENGTH
        or _CAPABILITY_ID_PATTERN.fullmatch(value) is None
    ):
        _reject("capability_shape_invalid")
    return value


def _canonical_json(value: object, *, depth: int, budget: _NodeBudget) -> object:
    if depth > MAX_ARGUMENT_DEPTH:
        _reject("capability_shape_invalid")
    budget.add()
    value_type = type(value)

    if value is None or value_type is bool:
        return value
    if value_type is int:
        if abs(value) > MAX_ARGUMENT_NUMBER_ABS:
            _reject("capability_shape_invalid")
        return value
    if value_type is float:
        if not math.isfinite(value) or abs(value) > MAX_ARGUMENT_NUMBER_ABS:
            _reject("capability_shape_invalid")
        return value
    if value_type is str:
        if len(value) > MAX_ARGUMENT_STRING_LENGTH:
            _reject("capability_shape_invalid")
        return value
    if value_type is dict:
        if len(value) > MAX_CONTAINER_ITEMS:
            _reject("capability_shape_invalid")
        snapshot: dict[str, object] = {}
        for key, item in value.items():
            if len(snapshot) >= MAX_CONTAINER_ITEMS:
                _reject("capability_shape_invalid")
            if (
                type(key) is not str
                or not 1 <= len(key) <= MAX_ARGUMENT_KEY_LENGTH
            ):
                _reject("capability_shape_invalid")
            snapshot[key] = _canonical_json(
                item,
                depth=depth + 1,
                budget=budget,
            )
        return snapshot
    if value_type is list:
        if len(value) > MAX_CONTAINER_ITEMS:
            _reject("capability_shape_invalid")
        snapshot_list: list[object] = []
        for item in value:
            if len(snapshot_list) >= MAX_CONTAINER_ITEMS:
                _reject("capability_shape_invalid")
            snapshot_list.append(
                _canonical_json(
                    item,
                    depth=depth + 1,
                    budget=budget,
                )
            )
        return snapshot_list
    _reject("capability_shape_invalid")


def _guarded_json(value: object, guard: _MutationGuard) -> object:
    value_type = type(value)
    if value_type is dict:
        return _GuardedObject(
            tuple((key, _guarded_json(item, guard)) for key, item in value.items()),
            guard,
        )
    if value_type is list:
        return _GuardedArray(
            tuple(_guarded_json(item, guard) for item in value),
            guard,
        )
    if value is None or value_type in {bool, int, float, str}:
        return value
    _reject("capability_shape_invalid")


def _immutable_json(value: object) -> object:
    value_type = type(value)
    if value_type is dict:
        return MappingProxyType(
            {key: _immutable_json(item) for key, item in value.items()}
        )
    if value_type is list:
        return tuple(_immutable_json(item) for item in value)
    if value is None or value_type in {bool, int, float, str}:
        return value
    _reject("capability_shape_invalid")


def _rejected(
    validation_subcode: AgenticDecisionValidationSubcode,
) -> AgenticTurnDecisionValidationResult:
    return AgenticTurnDecisionValidationResult(
        status="rejected",
        reason="invalid_decision",
        decision=None,
        validation_subcode=validation_subcode,
    )


def _reject(validation_subcode: AgenticDecisionValidationSubcode) -> None:
    raise _InvalidDecision(validation_subcode)
