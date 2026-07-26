"""Private input boundary for one agent-authored turn decision."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol


# V1 keeps a compact, fixed-cost provider view: 24 entries leave nine slots
# above the current 15-row catalog without adding pagination or dynamic growth.
MAX_CAPABILITY_VIEW_COUNT = 24
MAX_CATALOG_ID_LENGTH = 96
MAX_CATALOG_VERSION_LENGTH = 96
MAX_RECEIPT_RESPONSE_LENGTH = 600


class AgenticTurnProviderUnavailable(Exception):
    """The semantic provider cannot produce a bounded decision for this turn."""


@dataclass(frozen=True)
class AgenticCapabilityViewEntry:
    """Reader-safe capability metadata from one immutable catalog snapshot."""

    capability_id: str
    description: str
    available: bool


@dataclass(frozen=True)
class AgenticCapabilityView:
    """Bounded catalog view available to one semantic-decision provider."""

    catalog_id: str
    catalog_version: str
    capabilities: tuple[AgenticCapabilityViewEntry, ...]


@dataclass(frozen=True)
class AgenticActionReceipt:
    """Bounded deterministic lifecycle facts available after a real phase."""

    action_id: str
    phase: str
    status: str
    confirmed: bool
    executed: bool


@dataclass(frozen=True)
class AgenticReceiptResponse:
    """One AI-authored message constrained to an already-known receipt phase."""

    speech: str
    display: str


@dataclass(frozen=True)
class AgenticTurnProviderRequest:
    """Private, compact context supplied to the semantic-decision boundary.

    ``human_wish`` is deliberately private input. The other fields are bounded
    references rather than raw provider, memory, or observation payloads. The
    empty agent-context field reserves a place for future bounded AI-side
    wishes, feelings, motivations, observation, and memory references without
    creating or evaluating any of them in this slice.
    """

    human_wish: str
    context_refs: Mapping[str, object]
    capability_view: AgenticCapabilityView
    agent_context: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )


class AgenticTurnProvider(Protocol):
    def decide(self, request: AgenticTurnProviderRequest) -> object:
        """Return one untrusted AgenticTurnDecision candidate."""


class AgenticReceiptResponseProvider(Protocol):
    def respond_to_receipt(self, receipt: AgenticActionReceipt) -> object:
        """Return one untrusted natural response for an actual lifecycle phase."""


@dataclass(frozen=True)
class StaticAgenticTurnProvider:
    """Deterministic test provider; production wiring is intentionally absent."""

    candidate: object
    receipt_responses: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        del request
        return self.candidate

    def respond_to_receipt(self, receipt: AgenticActionReceipt) -> object:
        return self.receipt_responses.get(receipt.phase)


@dataclass(frozen=True)
class UnavailableAgenticTurnProvider:
    """Explicit degraded provider used when semantic judgment is unavailable."""

    reason: str = "agentic_provider_unavailable"

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        del request
        raise AgenticTurnProviderUnavailable(self.reason)


def validate_agentic_receipt_response(candidate: object) -> AgenticReceiptResponse | None:
    """Bound a phase-specific response without interpreting ordinary language."""

    if type(candidate) is not dict or set(candidate) != {"speech", "display"}:
        return None
    speech = candidate.get("speech")
    display = candidate.get("display")
    if (
        type(speech) is not str
        or type(display) is not str
        or not speech
        or not display
        or len(speech) > MAX_RECEIPT_RESPONSE_LENGTH
        or len(display) > MAX_RECEIPT_RESPONSE_LENGTH
    ):
        return None
    return AgenticReceiptResponse(speech=speech, display=display)
