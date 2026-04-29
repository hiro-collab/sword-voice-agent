from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time
from typing import Any, Callable, ClassVar, Iterable, Iterator, Mapping
from urllib import error, request

from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.protocol.messages import AgentRequest, AgentResponse, now_timestamp


class DifyClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class DifyStreamEvent:
    """One parsed Dify SSE event from response_mode=streaming."""

    event: str
    answer_delta: str = ""
    timestamp: float = field(default_factory=now_timestamp)
    elapsed_s: float | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    task_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    type: ClassVar[str] = "dify_stream_event"

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        elapsed_s: float | None = None,
    ) -> "DifyStreamEvent":
        return cls(
            event=str(payload.get("event") or "message"),
            answer_delta=(
                str(payload["answer"])
                if payload.get("answer") is not None
                else ""
            ),
            elapsed_s=elapsed_s,
            conversation_id=_optional_payload_text(payload, "conversation_id"),
            message_id=(
                _optional_payload_text(payload, "message_id")
                or _optional_payload_text(payload, "id")
            ),
            task_id=_optional_payload_text(payload, "task_id"),
            raw=dict(payload),
        )

    @property
    def is_message_end(self) -> bool:
        return self.event == "message_end"

    @property
    def is_error(self) -> bool:
        return self.event == "error"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "event": self.event,
            "timestamp": self.timestamp,
            "answer_delta": self.answer_delta,
        }
        if self.elapsed_s is not None:
            payload["elapsed_s"] = self.elapsed_s
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id
        if self.message_id:
            payload["message_id"] = self.message_id
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.raw:
            payload["raw"] = dict(self.raw)
        return payload


class DifyClient:
    """Minimal blocking client for Dify Chat App API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost/v1",
        timeout_s: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = validate_base_url(base_url).rstrip("/")
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> "DifyClient":
        api_key = os.environ.get("DIFY_API_KEY", "")
        base_url = os.environ.get("DIFY_BASE_URL", "http://localhost/v1")
        timeout_s = float(os.environ.get("DIFY_TIMEOUT_S", "60"))
        return cls(api_key=api_key, base_url=base_url, timeout_s=timeout_s)

    def send_chat_message(self, agent_request: AgentRequest) -> AgentResponse:
        payload = build_chat_message_payload(agent_request, response_mode="blocking")
        response_payload = self._post_json("/chat-messages", payload)
        return AgentResponse(
            text=str(response_payload.get("answer", "")),
            timestamp=float(response_payload.get("created_at", now_timestamp())),
            conversation_id=(
                str(response_payload["conversation_id"])
                if response_payload.get("conversation_id") is not None
                else None
            ),
            message_id=(
                str(response_payload["message_id"])
                if response_payload.get("message_id") is not None
                else None
            ),
            raw=response_payload,
        )

    def stream_chat_message(
        self,
        agent_request: AgentRequest,
    ) -> Iterator[DifyStreamEvent]:
        payload = build_chat_message_payload(agent_request, response_mode="streaming")
        yield from self._post_json_stream("/chat-messages", payload)

    def send_chat_message_streaming(
        self,
        agent_request: AgentRequest,
        *,
        on_event: Callable[[DifyStreamEvent], None] | None = None,
    ) -> AgentResponse:
        answer_parts: list[str] = []
        event_count = 0
        first_token_elapsed_s: float | None = None
        completed_elapsed_s: float | None = None
        conversation_id: str | None = None
        message_id: str | None = None
        last_raw: Mapping[str, Any] = {}
        created_at = now_timestamp()

        for event_payload in self.stream_chat_message(agent_request):
            event_count += 1
            completed_elapsed_s = event_payload.elapsed_s
            last_raw = event_payload.raw
            if event_payload.conversation_id:
                conversation_id = event_payload.conversation_id
            if event_payload.message_id:
                message_id = event_payload.message_id
            raw_created_at = event_payload.raw.get("created_at")
            if isinstance(raw_created_at, (int, float)) and not isinstance(raw_created_at, bool):
                created_at = float(raw_created_at)
            if event_payload.answer_delta:
                if first_token_elapsed_s is None:
                    first_token_elapsed_s = event_payload.elapsed_s
                answer_parts.append(event_payload.answer_delta)
            if on_event is not None:
                on_event(event_payload)

        raw = dict(last_raw)
        raw["_streaming"] = {
            "event_count": event_count,
            "first_token_elapsed_s": first_token_elapsed_s,
            "completed_elapsed_s": completed_elapsed_s,
        }
        return AgentResponse(
            text="".join(answer_parts),
            timestamp=created_at,
            conversation_id=conversation_id,
            message_id=message_id,
            raw=raw,
        )

    def _post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise DifyClientError(
                f"Dify API returned HTTP {exc.code}: {details}"
            ) from exc
        except error.URLError as exc:
            raise DifyClientError(f"failed to connect to Dify API: {exc}") from exc

        try:
            decoded = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise DifyClientError("Dify API returned non-JSON response") from exc

        if not isinstance(decoded, dict):
            raise DifyClientError("Dify API returned unexpected JSON payload")
        return decoded

    def _post_json_stream(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> Iterator[DifyStreamEvent]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        started_monotonic = time.perf_counter()

        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                for payload_chunk in iter_sse_json_payloads(response):
                    event_payload = DifyStreamEvent.from_payload(
                        payload_chunk,
                        elapsed_s=time.perf_counter() - started_monotonic,
                    )
                    if event_payload.is_error:
                        raise DifyClientError(dify_stream_error_message(payload_chunk))
                    yield event_payload
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise DifyClientError(
                f"Dify API returned HTTP {exc.code}: {details}"
            ) from exc
        except error.URLError as exc:
            raise DifyClientError(f"failed to connect to Dify API: {exc}") from exc


def validate_base_url(base_url: str) -> str:
    return validate_http_url(base_url, label="DIFY_BASE_URL")


def build_chat_message_payload(
    agent_request: AgentRequest,
    *,
    response_mode: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "inputs": dict(agent_request.context),
        "query": agent_request.text,
        "response_mode": response_mode,
        "user": agent_request.user,
    }
    if agent_request.conversation_id:
        payload["conversation_id"] = agent_request.conversation_id
    return payload


def iter_sse_json_payloads(
    lines: Iterable[bytes | str],
) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw_line in lines:
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else raw_line
        )
        line = line.rstrip("\r\n")
        if line == "":
            payload = decode_sse_data_lines(data_lines)
            data_lines = []
            if payload is not None:
                yield payload
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    payload = decode_sse_data_lines(data_lines)
    if payload is not None:
        yield payload


def decode_sse_data_lines(data_lines: list[str]) -> dict[str, Any] | None:
    if not data_lines:
        return None
    data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return None
    try:
        decoded = json.loads(data)
    except json.JSONDecodeError as exc:
        raise DifyClientError("Dify API returned invalid SSE JSON") from exc
    if not isinstance(decoded, dict):
        raise DifyClientError("Dify API returned unexpected SSE JSON payload")
    return decoded


def dify_stream_error_message(payload: Mapping[str, Any]) -> str:
    message = str(payload.get("message") or payload.get("error") or "stream error")
    code = str(payload.get("code") or "").strip()
    status = str(payload.get("status") or "").strip()
    details = " ".join(part for part in (code, status, message) if part)
    return f"Dify streaming error: {details}"


def _optional_payload_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
