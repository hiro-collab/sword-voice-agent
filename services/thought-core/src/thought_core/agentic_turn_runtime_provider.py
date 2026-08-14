"""Production wiring for the bounded agentic-turn semantic boundary."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from .agentic_turn_provider import (
    AGENTIC_PREDECISION_CONTEXT_SCHEMA_VERSION,
    AGENTIC_PREDECISION_CONTEXT_SECTION_NAMES,
    AGENTIC_PREDECISION_CONTEXT_STATUSES,
    MAX_CAPABILITY_VIEW_COUNT,
    MAX_CATALOG_ID_LENGTH,
    MAX_CATALOG_VERSION_LENGTH,
    AgenticActionReceipt,
    AgenticZeroArgumentCapabilityConstraint,
    AgenticCapabilityView,
    AgenticPredecisionContext,
    AgenticPredecisionContextSection,
    AgenticTurnProvider,
    AgenticTurnProviderDecisionInvalid,
    AgenticTurnProviderRequest,
    AgenticTurnProviderResult,
    AgenticTurnProviderUnavailable,
    UnavailableAgenticTurnProvider,
    validate_agentic_provider_attempt_receipt,
)
from .capability_catalog import (
    MAX_CAPABILITY_DESCRIPTION_CHARS,
    MAX_CAPABILITY_ID_CHARS,
)
from .ordinary_route_contract import review_checkpoint_payload
from .responders import (
    OpenAICompatibleStructuredCompletion,
    StructuredCompletion,
    StructuredCompletionInvalid,
    StructuredCompletionResult,
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
MAX_PREDECISION_CONTEXT_SERIALIZED_BYTES = 32_768
MAX_PREDECISION_SECTION_ITEMS = 8
MAX_PREDECISION_ITEM_PROPERTIES = 12
MAX_PREDECISION_VALUE_LIST_ITEMS = 8
MAX_PREDECISION_VALUE_DEPTH = 3
MAX_PREDECISION_VALUE_NODES = 512
MAX_PREDECISION_KEY_CHARS = 64
MAX_PREDECISION_VALUE_STRING_CHARS = 320
MAX_PREDECISION_SECTION_SUMMARY_CHARS = 360
MAX_PREDECISION_STATUS_DETAIL_CHARS = 180
MAX_PREDECISION_LATEST_CORRECTION_CHARS = 600
MAX_AGENTIC_HUMAN_WISH_CHARS = 600
MAX_AGENTIC_HUMAN_WISH_UTF8_BYTES = 2_400
AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME = "agentic_turn_provider_output_v1"
_CONTROL_ROOT = Path(__file__).resolve().parents[4]
_AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_PATH = (
    _CONTROL_ROOT
    / "contracts"
    / "turn"
    / "agentic-turn-provider-output.v1.schema.json"
)

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
_BLOCKED_PREDECISION_KEYS = frozenset(
    {
        "api_key",
        "args",
        "argv",
        "auth",
        "authorization",
        "command",
        "command_line",
        "credential",
        "credentials",
        "endpoint",
        "jsonl",
        "password",
        "path",
        "payload",
        "port",
        "provider_payload",
        "raw",
        "raw_config",
        "secret",
        "token",
        "url",
        "uri",
    }
)
_RECEIPT_PHASES = frozenset(
    {"confirmation", "failure", "noop", "preview", "submitted", "success"}
)
_RECEIPT_STATUSES = frozenset(
    {
        "confirmation_required",
        "needs_feedback",
        "noop",
        "preview_failed",
        "previewed",
        "submitted_external_observation_required",
        "success",
    }
)
_RECEIPT_EXECUTION_CERTAINTIES = frozenset(
    {"executed", "not_executed", "unknown"}
)
_RECEIPT_REVIEW_STATUSES = frozenset(
    {"execute_failed", "mismatch", "not_reviewed", "pending", "succeeded"}
)
_DECISION_SYSTEM_PROMPT = (
    "Return exactly one JSON object for AgenticTurnDecision V1. Use schemaVersion 1; "
    "kind must be conversation, clarification, hold, or capability; response must "
    "contain non-empty speech and display strings. Always include capability. Use null "
    "unless kind is capability. A capability "
    "decision must contain capability with exactly id and arguments; arguments must "
    "always contain position, strength, and durationMs, using null for omitted values. "
    "Select only an available "
    "capability from the supplied catalog snapshot. Do not execute anything, do not "
    "invent evidence, and do not return markdown or explanatory text. The human_wish "
    "is the newest user instruction. Treat capability availability and prior context "
    "as options, never action authority. Set kind to capability only when human_wish "
    "semantically and unambiguously requests that matching action. Greetings, ordinary "
    "conversation, capability questions, hypothetical statements, and ambiguous wishes "
    "must use conversation or clarification with capability null. When human_wish "
    "directly asks to start, stop, reset, or otherwise invoke one available capability, "
    "use kind capability even if current state or downstream execution outcome is "
    "unknown. Do not downgrade a direct action request to conversation merely because "
    "validation, delivery, or a downstream receipt is still required. Before that receipt, "
    "the response may describe the request but must not claim completion. When "
    "bounded_capability_constraint is present, it is a reader-safe semantic constraint, "
    "not execution authority. Confirm it against human_wish and the catalog. If it "
    "matches, return kind capability with that exact id and arguments; do not answer "
    "with a capability refusal merely because downstream delivery is not yet proven. "
    "latest_user_correction is present in the "
    "predecision context, it overrides older continuity or memory summaries. Preserve "
    "missing, unavailable, stale, and conflict status instead of treating it as current "
    "context."
)
_RECEIPT_SYSTEM_PROMPT = (
    "Return exactly one JSON object with non-empty speech and display strings. Render "
    "only the supplied bounded action-response context and lifecycle facts. Use the "
    "approved semantic purpose, capability, target, expected state, execution certainty, "
    "and review checkpoint to explain what was requested and what was actually verified. "
    "Do not claim any action, state, confirmation, execution, or observation beyond those "
    "facts. Do not return markdown or explanatory text."
)


class OpenAICompatibleAgenticTurnProvider:
    """Translate bounded turn and receipt facts into structured completions."""

    __slots__ = ("_completion",)
    requires_provider_attempt_receipt = False

    def __init__(self, completion: StructuredCompletion) -> None:
        self._completion = completion

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        try:
            payload = _decision_input_payload(request)
            completion_result = self._completion.complete_json(
                system_prompt=_DECISION_SYSTEM_PROMPT,
                input_payload=payload,
                max_tokens=DECISION_MAX_TOKENS,
                response_format=_agentic_turn_provider_response_format(),
                decision_event_id=(
                    request.decision_event_id
                    if self.requires_provider_attempt_receipt
                    else None
                ),
            )
            if self.requires_provider_attempt_receipt:
                if type(completion_result) is not StructuredCompletionResult:
                    raise AgenticTurnProviderDecisionInvalid(
                        "provider_content_invalid"
                    )
                receipt = validate_agentic_provider_attempt_receipt(
                    completion_result.provider_attempt_receipt,
                    decision_event_id=request.decision_event_id,
                )
                if receipt is None:
                    raise AgenticTurnProviderDecisionInvalid(
                        "provider_content_invalid"
                    )
                return AgenticTurnProviderResult(
                    candidate=_normalize_agentic_turn_provider_output(
                        completion_result.value
                    ),
                    provider_attempt_receipt=receipt,
                )
            candidate = (
                completion_result.value
                if type(completion_result) is StructuredCompletionResult
                else completion_result
            )
            return _normalize_agentic_turn_provider_output(candidate)
        except AgenticTurnProviderDecisionInvalid:
            raise
        except StructuredCompletionUnavailable:
            raise AgenticTurnProviderUnavailable(
                "agentic_provider_unavailable"
            ) from None
        except StructuredCompletionInvalid:
            raise AgenticTurnProviderDecisionInvalid(
                "provider_content_invalid"
            ) from None
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


@lru_cache(maxsize=1)
def _agentic_turn_provider_response_format_json() -> str:
    try:
        schema = json.loads(
            _AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("agentic_turn_provider_schema_unavailable") from None
    if type(schema) is not dict:
        raise ValueError("agentic_turn_provider_schema_invalid")
    return json.dumps(
        {
            "type": "json_schema",
            "json_schema": {
                "name": AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME,
                "strict": True,
                "schema": schema,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _agentic_turn_provider_response_format() -> dict[str, object]:
    response_format = json.loads(_agentic_turn_provider_response_format_json())
    if type(response_format) is not dict:
        raise ValueError("agentic_turn_provider_schema_invalid")
    return response_format


def _normalize_agentic_turn_provider_output(candidate: object) -> object:
    if type(candidate) is not dict or set(candidate) != {
        "schemaVersion",
        "kind",
        "response",
        "capability",
    }:
        return candidate

    kind = candidate.get("kind")
    capability = candidate.get("capability")
    if kind != "capability":
        if capability is not None:
            return candidate
        return {
            "schemaVersion": candidate.get("schemaVersion"),
            "kind": kind,
            "response": candidate.get("response"),
        }

    if type(capability) is not dict or set(capability) != {"id", "arguments"}:
        return candidate
    arguments = capability.get("arguments")
    if type(arguments) is not dict or set(arguments) != {
        "position",
        "strength",
        "durationMs",
    }:
        return candidate
    normalized_arguments = {
        key: value for key, value in arguments.items() if value is not None
    }
    return {
        "schemaVersion": candidate.get("schemaVersion"),
        "kind": kind,
        "response": candidate.get("response"),
        "capability": {
            "id": capability.get("id"),
            "arguments": normalized_arguments,
        },
    }


class SwordOpenAIBrokerAgenticTurnProvider(OpenAICompatibleAgenticTurnProvider):
    """The sole primary production route for the credential-free Sword broker."""

    __slots__ = ()
    provider_name = SWORD_OPENAI_BROKER_PROVIDER
    requires_provider_attempt_receipt = True


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
    if (
        type(request.human_wish) is not str
        or not request.human_wish.strip()
        or len(request.human_wish) > MAX_AGENTIC_HUMAN_WISH_CHARS
        or len(request.human_wish.encode("utf-8")) > MAX_AGENTIC_HUMAN_WISH_UTF8_BYTES
    ):
        raise ValueError("agentic_human_wish_invalid")

    catalog, capabilities = _bounded_capability_view(request.capability_view)
    predecision_context = _bounded_predecision_context(
        request.predecision_context,
        catalog=catalog,
        capabilities=capabilities,
    )

    return {
        "human_wish": request.human_wish,
        "catalog": catalog,
        "capabilities": capabilities,
        "bounded_capability_constraint": _bounded_capability_constraint(
            request.bounded_capability_constraint,
            capabilities=capabilities,
        ),
        "context_refs": _bounded_context_refs(request.context_refs),
        "agent_context": _bounded_agent_context(request.agent_context),
        "predecision_context": predecision_context,
    }


def _bounded_capability_constraint(
    candidate: object,
    *,
    capabilities: list[dict[str, object]],
) -> dict[str, object] | None:
    if candidate is None:
        return None
    if type(candidate) is not AgenticZeroArgumentCapabilityConstraint:
        raise ValueError("agentic_capability_constraint_invalid")
    if (
        type(candidate.capability_id) is not str
        or not candidate.capability_id
        or len(candidate.capability_id) > MAX_CAPABILITY_ID_CHARS
        or dict(candidate.arguments)
    ):
        raise ValueError("agentic_capability_constraint_invalid")
    matches = [
        capability
        for capability in capabilities
        if capability.get("id") == candidate.capability_id
        and capability.get("available") is True
    ]
    if len(matches) != 1:
        raise ValueError("agentic_capability_constraint_invalid")
    return {"id": candidate.capability_id, "arguments": {}}


def _bounded_capability_view(
    capability_view: object,
) -> tuple[dict[str, str], list[dict[str, object]]]:
    if (
        type(capability_view) is not AgenticCapabilityView
        or type(capability_view.catalog_id) is not str
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

    return (
        {
            "id": capability_view.catalog_id,
            "version": capability_view.catalog_version,
        },
        capabilities,
    )


def _receipt_input_payload(receipt: AgenticActionReceipt) -> dict[str, object]:
    if (
        type(receipt.decision_ref) is not str
        or not receipt.decision_ref.startswith("evt_")
        or len(receipt.decision_ref) > MAX_CONTEXT_REF_STRING_CHARS
        or type(receipt.receipt_ref) is not str
        or receipt.receipt_ref != f"{receipt.decision_ref}:{receipt.phase}"
        or len(receipt.receipt_ref) > MAX_CONTEXT_REF_STRING_CHARS
        or type(receipt.action_id) is not str
        or not receipt.action_id
        or len(receipt.action_id) > MAX_CAPABILITY_ID_CHARS
        or type(receipt.capability_id) is not str
        or not receipt.capability_id
        or len(receipt.capability_id) > MAX_CAPABILITY_ID_CHARS
        or type(receipt.semantic_purpose) is not str
        or type(receipt.target_ref) is not str
        or type(receipt.expected_state) is not str
        or type(receipt.phase) is not str
        or receipt.phase not in _RECEIPT_PHASES
        or type(receipt.status) is not str
        or receipt.status not in _RECEIPT_STATUSES
        or type(receipt.confirmed) is not bool
        or type(receipt.executed) is not bool
        or receipt.execution_certainty not in _RECEIPT_EXECUTION_CERTAINTIES
        or receipt.review_status not in _RECEIPT_REVIEW_STATUSES
        or review_checkpoint_payload(receipt.review_checkpoint_class)[
            "review_checkpoint_class"
        ]
        != receipt.review_checkpoint_class
        or (receipt.executed and receipt.execution_certainty != "executed")
        or (
            not receipt.executed
            and receipt.execution_certainty not in {"not_executed", "unknown"}
        )
    ):
        raise ValueError("agentic_receipt_invalid")
    try:
        semantic_purpose = _bounded_scalar(
            receipt.semantic_purpose,
            max_string=MAX_CONTEXT_REF_STRING_CHARS,
        )
        target_ref = _bounded_scalar(
            receipt.target_ref,
            max_string=MAX_CONTEXT_REF_STRING_CHARS,
        )
        expected_state = _bounded_scalar(
            receipt.expected_state,
            max_string=MAX_CONTEXT_REF_STRING_CHARS,
        )
    except ValueError:
        raise ValueError("agentic_receipt_invalid") from None
    return {
        "context_version": "agentic_action_response_context.v1",
        "decision_ref": receipt.decision_ref,
        "receipt_ref": receipt.receipt_ref,
        "action_id": receipt.action_id,
        "capability_id": receipt.capability_id,
        "semantic_purpose": semantic_purpose,
        "target_ref": target_ref,
        "expected_state": expected_state,
        "phase": receipt.phase,
        "status": receipt.status,
        "confirmed": receipt.confirmed,
        "executed": receipt.executed,
        "execution_certainty": receipt.execution_certainty,
        "review_status": receipt.review_status,
        "review_checkpoint_class": receipt.review_checkpoint_class,
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


def _bounded_predecision_context(
    value: object,
    *,
    catalog: Mapping[str, str],
    capabilities: list[dict[str, object]],
) -> dict[str, object]:
    if type(value) is not AgenticPredecisionContext:
        raise ValueError("agentic_predecision_context_invalid")

    latest_user_correction: str | None = None
    if value.latest_user_correction is not None:
        latest_user_correction = _bounded_predecision_text(
            value.latest_user_correction,
            max_chars=MAX_PREDECISION_LATEST_CORRECTION_CHARS,
            allow_empty=False,
        )

    node_budget = [0]
    sections: dict[str, object] = {}
    for section_name in AGENTIC_PREDECISION_CONTEXT_SECTION_NAMES:
        sections[section_name] = _bounded_predecision_section(
            getattr(value, section_name, None),
            node_budget=node_budget,
        )

    result: dict[str, object] = {
        "schema_version": AGENTIC_PREDECISION_CONTEXT_SCHEMA_VERSION,
        "latest_user_correction": latest_user_correction,
        **sections,
        "capability_view": {
            "catalog": dict(catalog),
            "capabilities": [dict(entry) for entry in capabilities],
        },
    }
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_PREDECISION_CONTEXT_SERIALIZED_BYTES:
        raise ValueError("agentic_predecision_context_invalid")
    return result


def _bounded_predecision_section(
    value: object,
    *,
    node_budget: list[int],
) -> dict[str, object]:
    if type(value) is not AgenticPredecisionContextSection:
        raise ValueError("agentic_predecision_context_invalid")
    if value.status not in AGENTIC_PREDECISION_CONTEXT_STATUSES:
        raise ValueError("agentic_predecision_context_invalid")
    status_detail = _bounded_predecision_text(
        value.status_detail,
        max_chars=MAX_PREDECISION_STATUS_DETAIL_CHARS,
        allow_empty=value.status == "available",
    )
    if value.status != "available" and not status_detail:
        raise ValueError("agentic_predecision_context_invalid")
    summary = _bounded_predecision_text(
        value.summary,
        max_chars=MAX_PREDECISION_SECTION_SUMMARY_CHARS,
        allow_empty=True,
    )
    if type(value.items) is not tuple or len(value.items) > MAX_PREDECISION_SECTION_ITEMS:
        raise ValueError("agentic_predecision_context_invalid")

    items: list[dict[str, object]] = []
    for item in value.items:
        if not isinstance(item, Mapping) or len(item) > MAX_PREDECISION_ITEM_PROPERTIES:
            raise ValueError("agentic_predecision_context_invalid")
        bounded = _bounded_predecision_value(item, depth=0, node_budget=node_budget)
        if type(bounded) is not dict:
            raise ValueError("agentic_predecision_context_invalid")
        items.append(bounded)
    return {
        "status": value.status,
        "status_detail": status_detail,
        "summary": summary,
        "items": items,
    }


def _bounded_predecision_value(
    value: object,
    *,
    depth: int,
    node_budget: list[int],
) -> object:
    node_budget[0] += 1
    if node_budget[0] > MAX_PREDECISION_VALUE_NODES:
        raise ValueError("agentic_predecision_context_invalid")

    if isinstance(value, Mapping):
        if depth >= MAX_PREDECISION_VALUE_DEPTH or len(value) > MAX_PREDECISION_ITEM_PROPERTIES:
            raise ValueError("agentic_predecision_context_invalid")
        keys = list(value)
        if any(not _safe_predecision_key(key) for key in keys):
            raise ValueError("agentic_predecision_context_invalid")
        return {
            key: _bounded_predecision_value(
                value[key],
                depth=depth + 1,
                node_budget=node_budget,
            )
            for key in sorted(keys)
        }
    if type(value) in {list, tuple}:
        if depth >= MAX_PREDECISION_VALUE_DEPTH or len(value) > MAX_PREDECISION_VALUE_LIST_ITEMS:
            raise ValueError("agentic_predecision_context_invalid")
        return [
            _bounded_predecision_value(
                item,
                depth=depth + 1,
                node_budget=node_budget,
            )
            for item in value
        ]
    if type(value) is str:
        return _bounded_predecision_text(
            value,
            max_chars=MAX_PREDECISION_VALUE_STRING_CHARS,
            allow_empty=False,
        )
    if type(value) is bool or value is None:
        return value
    if type(value) is int:
        if abs(value) > MAX_CONTEXT_REF_NUMBER_ABS:
            raise ValueError("agentic_predecision_context_invalid")
        return value
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > MAX_CONTEXT_REF_NUMBER_ABS:
            raise ValueError("agentic_predecision_context_invalid")
        return value
    raise ValueError("agentic_predecision_context_invalid")


def _safe_predecision_key(value: object) -> bool:
    if type(value) is not str or not 1 <= len(value) <= MAX_PREDECISION_KEY_CHARS:
        return False
    if value[0] not in "abcdefghijklmnopqrstuvwxyz":
        return False
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value):
        return False
    return not (
        value in _BLOCKED_PREDECISION_KEYS
        or value.startswith("raw_")
        or value.endswith(("_path", "_url", "_uri", "_port"))
        or "credential" in value
        or "secret" in value
        or "api_key" in value
        or "provider_payload" in value
        or "command_line" in value
        or "jsonl" in value
    )


def _bounded_predecision_text(
    value: object,
    *,
    max_chars: int,
    allow_empty: bool,
) -> str:
    if type(value) is not str or len(value) > max_chars or (not allow_empty and not value):
        raise ValueError("agentic_predecision_context_invalid")
    lowered = value.lower()
    if (
        "://" in lowered
        or "/" in value
        or "\\" in value
        or lowered.startswith(("bearer ", "token ", "system prompt", "developer prompt"))
        or "access_token=" in lowered
        or "api_key=" in lowered
        or "password=" in lowered
        or "prompt:" in lowered
    ):
        raise ValueError("agentic_predecision_context_invalid")
    return value


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
