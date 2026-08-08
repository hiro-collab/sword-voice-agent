"""Bounded, credential-isolating OpenAI Chat Completions broker adapter.

This is deliberately not a general upstream proxy. It accepts only the
current Thought Core structured two-message shape and retains no prompt,
header, response, or secret after a request completes.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request

LOOPBACK_HOST = "127.0.0.1"
STANDARD_PORT = 18786
ISOLATED_PORT = 18886
ALLOWED_PORTS = frozenset({STANDARD_PORT, ISOLATED_PORT})
COMPLETIONS_PATH = "/v1/chat/completions"
UPSTREAM_URL = "https://api.openai.com/v1/chat/completions"
FIXED_MODEL = "gpt-4o-mini"
SECRET_SOURCE_CLASS = "thought-core-existing-env-v1"
DECISION_MAX_TOKENS = 720
RECEIPT_MAX_TOKENS = 240
DECISION_EVENT_ID_HEADER = "X-Sword-Agentic-Decision-Event-Id"
PROVIDER_ATTEMPT_RECEIPT_KEY = "sword_provider_attempt_receipt"
PROVIDER_ATTEMPT_RECEIPT_CLASS = "sword.openai_broker.provider_attempt_receipt.v1"
PROVIDER_ATTEMPT_TERMINAL_CLASS = "upstream_response_accepted"
CANONICAL_EVENT_ID_PATTERN = re.compile(r"^evt_[0-9a-f]{32}$")
ALLOWED_MAX_TOKENS = frozenset({DECISION_MAX_TOKENS, RECEIPT_MAX_TOKENS})
MAX_BODY_BYTES = 32 * 1024
MAX_SYSTEM_BYTES = 12 * 1024
MAX_USER_BYTES = 16 * 1024
MAX_UPSTREAM_BYTES = 64 * 1024
MAX_UPSTREAM_CONTENT_BYTES = 64 * 1024
MAX_TIMEOUT_S = 12.0
DEFAULT_TIMEOUT_S = 12.0
DEFAULT_REQUEST_BUDGET = 64
MAX_REQUEST_BUDGET = 64

_CONTROL_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SECRET_FILE = _CONTROL_ROOT / "services" / "thought-core" / ".env"
AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME = "agentic_turn_provider_output_v1"
_AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_PATH = (
    _CONTROL_ROOT
    / "contracts"
    / "turn"
    / "agentic-turn-provider-output.v1.schema.json"
)


class BrokerError(Exception):
    """One fixed, non-sensitive broker error classification."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Opener(Protocol):
    def open(self, fullurl: request.Request, data: object = ..., timeout: float = ...) -> Any: ...


SecretLoader = Callable[[Path], str]
OpenerFactory = Callable[[], Opener]


class _RejectRedirects(request.HTTPRedirectHandler):
    """Reject any redirect instead of changing the fixed upstream destination."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class BrokerConfig:
    """Value-free configuration with explicit bounds and a fixed secret source class."""

    __slots__ = ("port", "secret_source_class", "secret_file", "timeout_s", "request_budget")

    def __init__(
        self,
        *,
        port: int = STANDARD_PORT,
        secret_source_class: str = SECRET_SOURCE_CLASS,
        secret_file: Path | str = DEFAULT_SECRET_FILE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        request_budget: int = DEFAULT_REQUEST_BUDGET,
    ) -> None:
        if type(port) is not int or port not in ALLOWED_PORTS:
            raise ValueError("broker_port_invalid")
        if secret_source_class != SECRET_SOURCE_CLASS:
            raise ValueError("broker_secret_source_invalid")
        if not isinstance(secret_file, (Path, str)) or not str(secret_file):
            raise ValueError("broker_secret_file_invalid")
        if (
            type(timeout_s) not in {int, float}
            or isinstance(timeout_s, bool)
            or not 0 < float(timeout_s) <= MAX_TIMEOUT_S
        ):
            raise ValueError("broker_timeout_invalid")
        if type(request_budget) is not int or not 1 <= request_budget <= MAX_REQUEST_BUDGET:
            raise ValueError("broker_request_budget_invalid")
        self.port = port
        self.secret_source_class = secret_source_class
        self.secret_file = Path(secret_file)
        self.timeout_s = float(timeout_s)
        self.request_budget = request_budget


def read_openai_api_key(secret_file: Path) -> str:
    """Read exactly one non-empty OPENAI_API_KEY from broker-owned source input."""

    try:
        contents = secret_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise BrokerError("secret_unavailable") from None
    matches: list[str] = []
    for line in contents.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        key, separator, value = candidate.partition("=")
        if separator and key.strip() == "OPENAI_API_KEY":
            matches.append(value.strip())
    if len(matches) != 1 or not matches[0]:
        raise BrokerError("secret_unavailable")
    return matches[0]


def build_safe_opener() -> Opener:
    """Construct verified-TLS, proxy-free, redirect-rejecting transport."""

    return request.build_opener(
        request.ProxyHandler({}),
        request.HTTPSHandler(context=ssl.create_default_context()),
        _RejectRedirects(),
    )


@lru_cache(maxsize=1)
def _agentic_turn_provider_output_schema() -> dict[str, object]:
    try:
        schema = json.loads(
            _AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BrokerError("configuration_invalid") from None
    if type(schema) is not dict:
        raise BrokerError("configuration_invalid")
    return schema


def agentic_turn_provider_response_format() -> dict[str, object]:
    schema = json.loads(
        json.dumps(
            _agentic_turn_provider_output_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME,
            "strict": True,
            "schema": schema,
        },
    }


def validate_chat_payload(payload: object) -> dict[str, object]:
    """Reject all inbound shapes except the exact current bounded compatibility shape."""

    required = {"model", "messages", "temperature", "response_format", "max_tokens"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise BrokerError("invalid_request")
    if payload["model"] != FIXED_MODEL or payload["temperature"] != 0:
        raise BrokerError("invalid_request")
    if type(payload["max_tokens"]) is not int or payload["max_tokens"] not in ALLOWED_MAX_TOKENS:
        raise BrokerError("invalid_request")
    expected_response_format = (
        agentic_turn_provider_response_format()
        if payload["max_tokens"] == DECISION_MAX_TOKENS
        else {"type": "json_object"}
    )
    if payload["response_format"] != expected_response_format:
        raise BrokerError("invalid_request")
    messages = payload["messages"]
    if type(messages) is not list or len(messages) != 2:
        raise BrokerError("invalid_request")
    safe_messages: list[dict[str, str]] = []
    for message, role, max_bytes in zip(
        messages,
        ("system", "user"),
        (MAX_SYSTEM_BYTES, MAX_USER_BYTES),
        strict=True,
    ):
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise BrokerError("invalid_request")
        content = message["content"]
        if message["role"] != role or type(content) is not str or not content:
            raise BrokerError("invalid_request")
        if len(content.encode("utf-8")) > max_bytes:
            raise BrokerError("invalid_request")
        safe_messages.append({"role": role, "content": content})
    return {
        "model": FIXED_MODEL,
        "messages": safe_messages,
        "temperature": 0,
        "response_format": expected_response_format,
        "max_tokens": payload["max_tokens"],
    }


class OpenAIBroker:
    """Concurrency-one, queue-zero forwarding boundary with no retry loop."""

    __slots__ = ("_config", "_secret_loader", "_opener_factory", "_request_count", "_lock")

    def __init__(
        self,
        config: BrokerConfig,
        *,
        secret_loader: SecretLoader = read_openai_api_key,
        opener_factory: OpenerFactory = build_safe_opener,
    ) -> None:
        self._config = config
        self._secret_loader = secret_loader
        self._opener_factory = opener_factory
        self._request_count = 0
        self._lock = threading.Lock()

    def complete(
        self,
        raw_body: bytes,
        *,
        decision_event_id: str | None = None,
    ) -> dict[str, object]:
        if type(raw_body) is not bytes or len(raw_body) > MAX_BODY_BYTES:
            raise BrokerError("invalid_request")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise BrokerError("invalid_request") from None
        safe_payload = validate_chat_payload(payload)
        decision_request = safe_payload["max_tokens"] == DECISION_MAX_TOKENS
        if decision_request:
            if (
                type(decision_event_id) is not str
                or CANONICAL_EVENT_ID_PATTERN.fullmatch(decision_event_id) is None
            ):
                raise BrokerError("invalid_request")
        elif decision_event_id is not None:
            raise BrokerError("invalid_request")
        if not self._lock.acquire(blocking=False):
            raise BrokerError("capacity_exhausted")
        try:
            if self._request_count >= self._config.request_budget:
                raise BrokerError("request_budget_exhausted")
            self._request_count += 1
            result = self._forward(safe_payload)
            if decision_event_id is not None:
                result[PROVIDER_ATTEMPT_RECEIPT_KEY] = {
                    "receipt_class": PROVIDER_ATTEMPT_RECEIPT_CLASS,
                    "decision_event_id": decision_event_id,
                    "upstream_attempt_count": 1,
                    "retry_count": 0,
                    "fallback_count": 0,
                    "attempt_terminal_class": PROVIDER_ATTEMPT_TERMINAL_CLASS,
                }
            return result
        finally:
            self._lock.release()

    def _forward(self, payload: dict[str, object]) -> dict[str, object]:
        api_key = self._secret_loader(self._config.secret_file)
        if type(api_key) is not str or not api_key:
            raise BrokerError("secret_unavailable")
        outbound = dict(payload)
        outbound["store"] = False
        encoded = json.dumps(outbound, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        outbound_request = request.Request(
            UPSTREAM_URL,
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener_factory().open(outbound_request, timeout=self._config.timeout_s) as response:
                upstream_bytes = response.read(MAX_UPSTREAM_BYTES + 1)
        except (OSError, TimeoutError, error.HTTPError):
            raise BrokerError("upstream_unavailable") from None
        if type(upstream_bytes) is not bytes or len(upstream_bytes) > MAX_UPSTREAM_BYTES:
            raise BrokerError("upstream_invalid")
        try:
            upstream = json.loads(upstream_bytes.decode("utf-8"))
            content = upstream["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, UnicodeError, json.JSONDecodeError):
            raise BrokerError("upstream_invalid") from None
        if type(content) is not str or not content or len(content.encode("utf-8")) > MAX_UPSTREAM_CONTENT_BYTES:
            raise BrokerError("upstream_invalid")
        return {"choices": [{"message": {"content": content}}]}
