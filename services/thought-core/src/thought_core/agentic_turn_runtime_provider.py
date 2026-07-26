"""Production wiring for the bounded agentic-turn semantic boundary."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

from .agentic_turn_provider import (
    MAX_CAPABILITY_VIEW_COUNT,
    MAX_CATALOG_ID_LENGTH,
    MAX_CATALOG_VERSION_LENGTH,
    AgenticActionReceipt,
    AgenticTurnProvider,
    AgenticTurnProviderRequest,
    AgenticTurnProviderUnavailable,
    UnavailableAgenticTurnProvider,
)
from .capability_catalog import (
    MAX_CAPABILITY_DESCRIPTION_CHARS,
    MAX_CAPABILITY_ID_CHARS,
)
from .responders import (
    OpenAICompatibleStructuredCompletion,
    StructuredCompletion,
    StructuredCompletionInvalid,
    StructuredCompletionUnavailable,
    is_loopback_http_url,
)


DECISION_MAX_TOKENS = 720
RECEIPT_MAX_TOKENS = 240
SWORD_OPENAI_BROKER_PROVIDER = "sword-openai-broker"
SWORD_OPENAI_BROKER_MODEL = "gpt-4o-mini"
MAX_SWORD_OPENAI_BROKER_TIMEOUT_S = 12.0
SWORD_OPENAI_BROKER_BASE_URLS = frozenset(
    {
        "http://127.0.0.1:18786/v1",
        "http://127.0.0.1:18886/v1",
    }
)
MAX_CONTEXT_REF_COUNT = 8
MAX_CONTEXT_REF_KEY_CHARS = 64
MAX_CONTEXT_REF_STRING_CHARS = 180
MAX_CONTEXT_REF_NUMBER_ABS = 1_000_000
MAX_AGENT_CONTEXT_REF_COUNT = 8
MAX_AGENT_CONTEXT_LIST_ITEMS = 8
MAX_AGENT_CONTEXT_STRING_CHARS = 180

_SUPPORTED_PROVIDER_NAMES = frozenset(
    {
        "openai",
        "openai-compatible",
        "openai_compatible",
        "openai_compatible_chat",
    }
)
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
_AGENT_CONTEXT_KEYS = frozenset(
    {
        "bounded_wish_refs",
        "observation_refs",
        "memory_refs",
        "working_memory_item_count",
    }
)
_SAFE_CONTEXT_REF_KEYS = frozenset(
    {
        "event_id",
        "turn_id",
        "trace_id",
        "observation_id",
        "observation_ref",
        "motion_event_id",
        "stimulus_id",
        "stimulus_instance_id",
        "runtime_result_id",
        "event_journal_entry_id",
        "memory_candidate_id",
        "driver_result_id",
        "body_schema_snapshot_id",
        "verifier_result_id",
    }
)
_DECISION_SYSTEM_PROMPT = (
    "Return exactly one JSON object for AgenticTurnDecision V1. Use schemaVersion 1; "
    "kind must be conversation, clarification, hold, or capability; response must "
    "contain non-empty speech and display strings. A capability decision must also "
    "contain capability with exactly id and arguments. Select only an available "
    "capability from the supplied catalog snapshot. Do not execute anything, do not "
    "invent evidence, and do not return markdown or explanatory text."
)
_RECEIPT_SYSTEM_PROMPT = (
    "Return exactly one JSON object with non-empty speech and display strings. Render "
    "only the supplied completed action-receipt facts. Do not claim any action, state, "
    "confirmation, execution, or observation beyond those facts. Do not return markdown "
    "or explanatory text."
)


class OpenAICompatibleAgenticTurnProvider:
    """Translate bounded turn and receipt facts into structured completions."""

    __slots__ = ("_completion",)

    def __init__(self, completion: StructuredCompletion) -> None:
        self._completion = completion

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        try:
            payload = _decision_input_payload(request)
            return self._completion.complete_json(
                system_prompt=_DECISION_SYSTEM_PROMPT,
                input_payload=payload,
                max_tokens=DECISION_MAX_TOKENS,
            )
        except StructuredCompletionUnavailable:
            raise AgenticTurnProviderUnavailable(
                "agentic_provider_unavailable"
            ) from None
        except StructuredCompletionInvalid:
            return None
        except (OSError, TimeoutError):
            raise AgenticTurnProviderUnavailable(
                "agentic_provider_unavailable"
            ) from None
        except (TypeError, ValueError):
            return None

    def respond_to_receipt(self, receipt: AgenticActionReceipt) -> object:
        try:
            payload = _receipt_input_payload(receipt)
            return self._completion.complete_json(
                system_prompt=_RECEIPT_SYSTEM_PROMPT,
                input_payload=payload,
                max_tokens=RECEIPT_MAX_TOKENS,
            )
        except StructuredCompletionUnavailable:
            raise AgenticTurnProviderUnavailable(
                "agentic_provider_unavailable"
            ) from None
        except StructuredCompletionInvalid:
            return None
        except (OSError, TimeoutError):
            raise AgenticTurnProviderUnavailable(
                "agentic_provider_unavailable"
            ) from None
        except (TypeError, ValueError):
            return None


class SwordOpenAIBrokerAgenticTurnProvider(OpenAICompatibleAgenticTurnProvider):
    """The sole primary production route for the credential-free Sword broker."""

    __slots__ = ()
    provider_name = SWORD_OPENAI_BROKER_PROVIDER


def build_agentic_turn_provider_from_env() -> AgenticTurnProvider | None:
    """Build the sole production semantic route or an explicit degraded provider."""

    llm_enabled = os.environ.get("THOUGHT_CORE_LLM_ENABLED", "").strip().lower()
    canonical_provider = os.environ.get("THOUGHT_CORE_LLM_PROVIDER", "")
    compatibility_adapter = os.environ.get("THOUGHT_CORE_LLM_ADAPTER", "")
    canonical_base_url = os.environ.get("THOUGHT_CORE_LLM_BASE_URL", "")
    compatibility_base_url = os.environ.get("OPENAI_BASE_URL", "")
    canonical_model = os.environ.get("THOUGHT_CORE_LLM_MODEL", "")
    compatibility_model = os.environ.get("OPENAI_MODEL", "")
    provider_name = (canonical_provider or compatibility_adapter).strip().lower()
    base_url = (canonical_base_url or compatibility_base_url).strip()
    model = (canonical_model or compatibility_model).strip()
    broker_aliases_present = any(
        value for value in (
            compatibility_adapter,
            compatibility_base_url,
            compatibility_model,
        )
    )
    broker_canonical_config = (
        canonical_provider == SWORD_OPENAI_BROKER_PROVIDER
        and canonical_base_url in SWORD_OPENAI_BROKER_BASE_URLS
        and canonical_model == SWORD_OPENAI_BROKER_MODEL
    )
    action_llm_enabled = os.environ.get(
        "THOUGHT_CORE_ACTION_LLM_ENABLED",
        "",
    ).strip().lower()
    action_provider_name = (
        os.environ.get("THOUGHT_CORE_ACTION_LLM_PROVIDER")
        or os.environ.get("THOUGHT_CORE_ACTION_LLM_ADAPTER")
        or ""
    ).strip().lower()
    action_base_url = os.environ.get(
        "THOUGHT_CORE_ACTION_LLM_BASE_URL",
        "",
    ).strip()
    if any(
        os.environ.get(name, "").strip()
        for name in (
            "THOUGHT_CORE_LLM_API_KEY",
            "THOUGHT_CORE_ACTION_LLM_API_KEY",
            "OPENAI_API_KEY",
        )
    ):
        return UnavailableAgenticTurnProvider()
    if _env_enabled("THOUGHT_CORE_FORCE_NO_PROVIDER"):
        if provider_name == SWORD_OPENAI_BROKER_PROVIDER:
            if not broker_canonical_config or broker_aliases_present:
                return UnavailableAgenticTurnProvider()
            return None
        if provider_name and provider_name not in _SUPPORTED_PROVIDER_NAMES:
            return UnavailableAgenticTurnProvider()
        if base_url and not is_loopback_http_url(base_url):
            return UnavailableAgenticTurnProvider()
        if action_llm_enabled and action_llm_enabled not in (
            _ENABLED_VALUES | _DISABLED_VALUES
        ):
            return UnavailableAgenticTurnProvider()
        if (
            action_provider_name
            and action_provider_name not in _SUPPORTED_PROVIDER_NAMES
        ):
            return UnavailableAgenticTurnProvider()
        if action_base_url and not is_loopback_http_url(action_base_url):
            return UnavailableAgenticTurnProvider()
        return None
    if llm_enabled in _DISABLED_VALUES or (
        llm_enabled and llm_enabled not in _ENABLED_VALUES
    ):
        return UnavailableAgenticTurnProvider()
    if provider_name == SWORD_OPENAI_BROKER_PROVIDER:
        if not broker_canonical_config or broker_aliases_present:
            return UnavailableAgenticTurnProvider()
        return _build_sword_openai_broker_provider(
            base_url=canonical_base_url,
            model=canonical_model,
        )
    if base_url in SWORD_OPENAI_BROKER_BASE_URLS:
        return UnavailableAgenticTurnProvider()
    if provider_name and provider_name not in _SUPPORTED_PROVIDER_NAMES:
        return UnavailableAgenticTurnProvider()
    if not base_url or not model or not is_loopback_http_url(base_url):
        return UnavailableAgenticTurnProvider()
    timeout_s = _positive_float_env("THOUGHT_CORE_LLM_TIMEOUT_S", 12.0)
    if timeout_s is None:
        return UnavailableAgenticTurnProvider()
    try:
        completion = OpenAICompatibleStructuredCompletion(
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
        )
    except (OSError, TypeError, ValueError):
        return UnavailableAgenticTurnProvider()
    return OpenAICompatibleAgenticTurnProvider(completion)


def _build_sword_openai_broker_provider(
    *,
    base_url: str,
    model: str,
) -> AgenticTurnProvider:
    if (
        base_url not in SWORD_OPENAI_BROKER_BASE_URLS
        or model != SWORD_OPENAI_BROKER_MODEL
    ):
        return UnavailableAgenticTurnProvider()
    timeout_s = _positive_float_env(
        "THOUGHT_CORE_LLM_TIMEOUT_S",
        MAX_SWORD_OPENAI_BROKER_TIMEOUT_S,
    )
    if (
        timeout_s is None
        or timeout_s > MAX_SWORD_OPENAI_BROKER_TIMEOUT_S
    ):
        return UnavailableAgenticTurnProvider()
    try:
        completion = OpenAICompatibleStructuredCompletion(
            base_url=base_url,
            model=SWORD_OPENAI_BROKER_MODEL,
            timeout_s=timeout_s,
        )
    except (OSError, TypeError, ValueError):
        return UnavailableAgenticTurnProvider()
    return SwordOpenAIBrokerAgenticTurnProvider(completion)


def _decision_input_payload(request: AgenticTurnProviderRequest) -> dict[str, object]:
    if type(request.human_wish) is not str or not request.human_wish.strip():
        raise ValueError("agentic_human_wish_invalid")

    capability_view = request.capability_view
    if (
        type(capability_view.catalog_id) is not str
        or not capability_view.catalog_id
        or len(capability_view.catalog_id) > MAX_CATALOG_ID_LENGTH
        or type(capability_view.catalog_version) is not str
        or not capability_view.catalog_version
        or len(capability_view.catalog_version) > MAX_CATALOG_VERSION_LENGTH
        or not 1 <= len(capability_view.capabilities) <= MAX_CAPABILITY_VIEW_COUNT
    ):
        raise ValueError("agentic_capability_view_invalid")

    capabilities: list[dict[str, object]] = []
    for entry in capability_view.capabilities:
        if (
            type(entry.capability_id) is not str
            or not entry.capability_id
            or len(entry.capability_id) > MAX_CAPABILITY_ID_CHARS
            or type(entry.description) is not str
            or not entry.description
            or len(entry.description) > MAX_CAPABILITY_DESCRIPTION_CHARS
            or type(entry.available) is not bool
        ):
            raise ValueError("agentic_capability_view_invalid")
        capabilities.append(
            {
                "id": entry.capability_id,
                "description": entry.description,
                "available": entry.available,
            }
        )

    return {
        "human_wish": request.human_wish,
        "catalog": {
            "id": capability_view.catalog_id,
            "version": capability_view.catalog_version,
        },
        "capabilities": capabilities,
        "context_refs": _bounded_context_refs(request.context_refs),
        "agent_context": _bounded_agent_context(request.agent_context),
    }


def _receipt_input_payload(receipt: AgenticActionReceipt) -> dict[str, object]:
    if (
        type(receipt.action_id) is not str
        or not receipt.action_id
        or len(receipt.action_id) > MAX_CAPABILITY_ID_CHARS
        or type(receipt.phase) is not str
        or not receipt.phase
        or len(receipt.phase) > MAX_CONTEXT_REF_KEY_CHARS
        or type(receipt.status) is not str
        or not receipt.status
        or len(receipt.status) > MAX_CONTEXT_REF_KEY_CHARS
        or type(receipt.confirmed) is not bool
        or type(receipt.executed) is not bool
    ):
        raise ValueError("agentic_receipt_invalid")
    return {
        "action_id": receipt.action_id,
        "phase": receipt.phase,
        "status": receipt.status,
        "confirmed": receipt.confirmed,
        "executed": receipt.executed,
    }


def _bounded_context_refs(values: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(values, Mapping) or len(values) > MAX_CONTEXT_REF_COUNT:
        raise ValueError("agentic_context_refs_invalid")
    result: dict[str, object] = {}
    for key, value in values.items():
        if (
            type(key) is not str
            or key not in _SAFE_CONTEXT_REF_KEYS
            or len(key) > MAX_CONTEXT_REF_KEY_CHARS
        ):
            raise ValueError("agentic_context_refs_invalid")
        result[key] = _bounded_scalar(value, max_string=MAX_CONTEXT_REF_STRING_CHARS)
    return result


def _bounded_agent_context(values: Mapping[str, object]) -> dict[str, object]:
    if (
        not isinstance(values, Mapping)
        or len(values) > MAX_AGENT_CONTEXT_REF_COUNT
        or any(key not in _AGENT_CONTEXT_KEYS for key in values)
    ):
        raise ValueError("agentic_agent_context_invalid")
    result: dict[str, object] = {}
    for key, value in values.items():
        if key == "working_memory_item_count":
            if type(value) is not int or not 0 <= value <= MAX_CONTEXT_REF_NUMBER_ABS:
                raise ValueError("agentic_agent_context_invalid")
            result[key] = value
            continue
        if type(value) not in {list, tuple} or len(value) > MAX_AGENT_CONTEXT_LIST_ITEMS:
            raise ValueError("agentic_agent_context_invalid")
        refs: list[str] = []
        for item in value:
            if (
                type(item) is not str
                or not item
                or len(item) > MAX_AGENT_CONTEXT_STRING_CHARS
            ):
                raise ValueError("agentic_agent_context_invalid")
            refs.append(item)
        result[key] = refs
    return result


def _bounded_scalar(value: object, *, max_string: int) -> object:
    if type(value) is str:
        lowered = value.lower()
        if (
            not value
            or len(value) > max_string
            or "://" in lowered
            or ":\\" in value
            or "/" in value
            or "\\" in value
            or lowered.startswith(
                ("bearer ", "token ", "system prompt", "developer prompt")
            )
            or "access_token=" in lowered
            or "api_key=" in lowered
            or "prompt:" in lowered
        ):
            raise ValueError("agentic_context_refs_invalid")
        return value
    if type(value) is bool or value is None:
        return value
    if type(value) is int:
        if abs(value) > MAX_CONTEXT_REF_NUMBER_ABS:
            raise ValueError("agentic_context_refs_invalid")
        return value
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > MAX_CONTEXT_REF_NUMBER_ABS:
            raise ValueError("agentic_context_refs_invalid")
        return value
    raise ValueError("agentic_context_refs_invalid")


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_float_env(name: str, default: float) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None
