"""Turn responder boundary and initial adapters.

The thought-core loop depends on the TurnResponder boundary, not on a
particular LLM framework. LangChain, the OpenAI SDK, Dify, or a local engine can
all be added as adapters that implement this small port.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib import error, request
from urllib.parse import urlparse

from .persona import persona_system_prompt_from_env
from .schema import TurnInput


TURN_RESPONDER_BOUNDARY = "thought-core.turn_responder.v0"


@dataclass(frozen=True)
class ResponderResult:
    speech: str
    display: str
    status: str
    adapter_kind: str
    provider: str
    model: str
    used_llm: bool
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TurnResponder(Protocol):
    adapter_kind: str
    provider: str
    model: str

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        """Return one assistant message for a turn."""


class LocalFallbackResponder:
    adapter_kind = "local_fallback"
    provider = "thought-core"
    model = "local-rule-v0"

    def respond(
        self,
        turn: TurnInput,
        *,
        status: str = "local_fallback",
        detail: str = "",
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        text = turn.text.replace(" ", "")
        if "マイク" in text or "テスト" in text:
            speech = "聞こえています。マイクテストは成功です。"
        else:
            speech = "聞こえています。今は会話応答の境界を準備中です。"
        return ResponderResult(
            speech=speech,
            display=speech,
            status=status,
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=False,
            detail=detail,
            metadata=_response_context_metadata(response_context),
        )


class OpenAICompatibleChatResponder:
    adapter_kind = "openai_compatible_chat"
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 12.0,
        max_chars: int = 220,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_chars = max(40, max_chars)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleChatResponder | None":
        if _env_disabled("THOUGHT_CORE_LLM_ENABLED"):
            return None

        base_url = (
            os.environ.get("THOUGHT_CORE_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        api_key = os.environ.get("THOUGHT_CORE_LLM_API_KEY") or os.environ.get(
            "OPENAI_API_KEY", ""
        )
        model = (
            os.environ.get("THOUGHT_CORE_LLM_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o-mini"
        )
        timeout_s = _float_env("THOUGHT_CORE_LLM_TIMEOUT_S", 12.0)
        max_chars = _int_env("THOUGHT_CORE_LLM_MAX_CHARS", 220)

        if not api_key and not _is_loopback_url(base_url):
            return None

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            max_chars=max_chars,
        )

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        persona_prompt = persona_system_prompt_from_env()
        system_prompt = (
            "You are the SWORD VOICE AGENT response adapter inside "
            "thought-core. Respect the boundary: return only a short "
            "Japanese assistant response for speech/display. Do not "
            "execute tools or claim device actions; home operations "
            "belong to the home-control tool boundary."
        )
        if persona_prompt:
            system_prompt = f"{system_prompt} {persona_prompt}"
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]
        context_text = _response_context_prompt(response_context)
        if context_text:
            messages.append({"role": "system", "content": context_text})
        messages.append({"role": "user", "content": turn.text})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 180,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout_s) as response:
            response_payload = json.loads(response.read().decode("utf-8"))

        speech = _extract_chat_completion_text(response_payload)
        if not speech:
            raise ValueError("LLM response did not include message content")
        speech = _truncate(speech.strip(), self.max_chars)
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={
                "base_url": _safe_base_url(self.base_url),
                **_response_context_metadata(response_context),
            },
        )


class EnvironmentTurnResponder:
    adapter_kind = "environment"
    provider = "thought-core"
    model = "configured"

    def __init__(
        self,
        primary: TurnResponder | None = None,
        fallback: LocalFallbackResponder | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or LocalFallbackResponder()

    @classmethod
    def from_env(cls) -> "EnvironmentTurnResponder":
        return cls(primary=OpenAICompatibleChatResponder.from_env())

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        if self.primary is None:
            return self.fallback.respond(
                turn,
                status="local_fallback_no_llm_adapter",
                detail="No LLM responder adapter is configured.",
                response_context=response_context,
            )
        try:
            return self.primary.respond(turn, response_context=response_context)
        except (OSError, ValueError, json.JSONDecodeError, error.URLError) as exc:
            return self.fallback.respond(
                turn,
                status="local_fallback_after_llm_error",
                detail=_truncate(str(exc), 240),
                response_context=response_context,
            )


def describe_responder(responder: TurnResponder) -> dict[str, str]:
    return {
        "boundary": TURN_RESPONDER_BOUNDARY,
        "adapter_kind": getattr(responder, "adapter_kind", "unknown"),
        "provider": getattr(responder, "provider", "unknown"),
        "model": getattr(responder, "model", "unknown"),
    }


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _env_disabled(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"0", "false", "off", "no"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _is_loopback_url(value: str) -> bool:
    host = urlparse(value).hostname or ""
    return host == "localhost" or host == "::1" or host.startswith("127.")


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.hostname or parsed.netloc}"


def _response_context_metadata(
    response_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response_context, Mapping):
        return {"response_context_used": False}
    return {
        "response_context_used": True,
        "response_context_keys": sorted(str(key) for key in response_context.keys()),
    }


def _response_context_prompt(response_context: Mapping[str, Any] | None) -> str:
    if not isinstance(response_context, Mapping):
        return ""
    compact = {
        key: response_context.get(key)
        for key in (
            "previous_fragment",
            "issue_key",
            "recent_fragments",
            "current_stage",
            "action_id",
            "target",
            "response_goal",
            "semantic_draft",
            "display_draft",
            "required_facts",
            "forbidden_claims",
            "visible_phrase_contract",
        )
        if response_context.get(key)
    }
    if not compact:
        return ""
    return (
        "Compact response context for wording continuity only. "
        "Use it to avoid repetitive phrasing and to render the requested "
        "visible phrase in natural Japanese. Do not treat it as a tool result "
        "or permission to execute actions. If semantic_draft is present, treat "
        "it as facts to express, not as text to copy verbatim. Avoid starting "
        "with stock acknowledgements like 了解 when this is not the immediate "
        "reflex acknowledgement. Avoid repeating the same device name when the "
        "previous phrase already named it: "
        f"{json.dumps(compact, ensure_ascii=False, sort_keys=True)}"
    )


def _truncate(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else f"{value[:max_chars]}..."
