from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
import os
import socket
import time
from typing import Any, Callable, ClassVar, Iterable, Iterator, Mapping
from urllib import error, parse, request

from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.protocol.messages import AgentRequest, AgentResponse, now_timestamp


DEFAULT_THOUGHT_CORE_BASE_URL = "http://127.0.0.1:18787"
ROUTE_DEADLINE_HEADER = "X-Sword-Route-Deadline-Monotonic"
MAX_ROUTE_DEADLINE_SECONDS = 10.0


class ThoughtCoreClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThoughtCoreStreamEvent:
    """One parsed thought-core event from the turn event stream."""

    event_type: str
    turn_id: str
    session_id: str
    seq: int | None = None
    schema_version: str | None = None
    event_id: str | None = None
    timestamp_text: str | None = None
    source: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=now_timestamp)
    elapsed_s: float | None = None

    type: ClassVar[str] = "thought_core_stream_event"

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        elapsed_s: float | None = None,
    ) -> "ThoughtCoreStreamEvent":
        data = payload.get("data")
        if data is not None and not isinstance(data, Mapping):
            raise ThoughtCoreClientError("thought-core event data must be an object")
        return cls(
            event_type=_required_text(payload, "type"),
            turn_id=_required_text(payload, "turn_id"),
            session_id=_required_text(payload, "session_id"),
            seq=_optional_int(payload.get("seq")),
            schema_version=_optional_text(payload, "schema_version"),
            event_id=_optional_text(payload, "event_id"),
            timestamp_text=_optional_text(payload, "timestamp"),
            source=_optional_text(payload, "source"),
            data=data or {},
            raw=dict(payload),
            elapsed_s=elapsed_s,
        )

    @property
    def is_speech_delta(self) -> bool:
        return self.event_type == "assistant.speech_delta"

    @property
    def is_message(self) -> bool:
        return self.event_type == "assistant.message"

    @property
    def is_completed(self) -> bool:
        return self.event_type == "turn.completed"

    @property
    def is_error(self) -> bool:
        return self.event_type == "turn.error"

    @property
    def speech_delta(self) -> str:
        if not self.is_speech_delta:
            return ""
        return str(self.data.get("delta") or "")

    @property
    def speech(self) -> str:
        if self.is_message:
            return str(self.data.get("speech") or "")
        return self.speech_delta

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "event_type": self.event_type,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "data": dict(self.data),
        }
        if self.seq is not None:
            payload["seq"] = self.seq
        if self.schema_version:
            payload["schema_version"] = self.schema_version
        if self.event_id:
            payload["event_id"] = self.event_id
        if self.timestamp_text:
            payload["timestamp_text"] = self.timestamp_text
        if self.source:
            payload["source"] = self.source
        if self.elapsed_s is not None:
            payload["elapsed_s"] = self.elapsed_s
        if self.raw:
            payload["raw"] = dict(self.raw)
        return payload


class ThoughtCoreClient:
    """Blocking client for the experimental thought-core turn API."""

    def __init__(
        self,
        base_url: str = DEFAULT_THOUGHT_CORE_BASE_URL,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = validate_base_url(base_url).rstrip("/")
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> "ThoughtCoreClient":
        base_url = os.environ.get("THOUGHT_CORE_BASE_URL", DEFAULT_THOUGHT_CORE_BASE_URL)
        timeout_s = float(os.environ.get("THOUGHT_CORE_TIMEOUT_S", "60"))
        return cls(base_url=base_url, timeout_s=timeout_s)

    def stream_turn(
        self,
        turn_payload: Mapping[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> Iterator[ThoughtCoreStreamEvent]:
        _remaining_deadline_seconds(deadline_monotonic)
        yield from self._post_json_stream(
            "/turn?stream=true",
            dict(turn_payload),
            deadline_monotonic=deadline_monotonic,
        )

    def send_turn_streaming(
        self,
        turn_payload: Mapping[str, Any],
        *,
        on_event: Callable[[ThoughtCoreStreamEvent], None] | None = None,
        deadline_monotonic: float | None = None,
    ) -> AgentResponse:
        _remaining_deadline_seconds(deadline_monotonic)
        messages: list[str] = []
        event_count = 0
        first_event_elapsed_s: float | None = None
        completed_elapsed_s: float | None = None
        turn_id: str | None = None
        last_raw: Mapping[str, Any] = {}

        for event_payload in self.stream_turn(
            turn_payload,
            deadline_monotonic=deadline_monotonic,
        ):
            _remaining_deadline_seconds(deadline_monotonic)
            event_count += 1
            if first_event_elapsed_s is None:
                first_event_elapsed_s = event_payload.elapsed_s
            completed_elapsed_s = event_payload.elapsed_s
            last_raw = event_payload.raw
            if event_payload.turn_id:
                turn_id = event_payload.turn_id
            if event_payload.is_message and event_payload.speech:
                messages.append(event_payload.speech)
            if on_event is not None:
                _remaining_deadline_seconds(deadline_monotonic)
                on_event(event_payload)
            if event_payload.is_error:
                raise ThoughtCoreClientError(thought_core_error_message(event_payload.raw))

        _remaining_deadline_seconds(deadline_monotonic)
        raw = dict(last_raw)
        raw["_streaming"] = {
            "event_count": event_count,
            "first_event_elapsed_s": first_event_elapsed_s,
            "completed_elapsed_s": completed_elapsed_s,
        }
        return AgentResponse(
            text="\n".join(messages),
            timestamp=now_timestamp(),
            conversation_id=turn_id,
            message_id=raw.get("event_id") if isinstance(raw.get("event_id"), str) else None,
            raw=raw,
        )

    def send_agent_request_streaming(
        self,
        agent_request: AgentRequest,
        *,
        session_id: str | None = None,
        locale: str | None = None,
        turn_id: str | None = None,
        on_event: Callable[[ThoughtCoreStreamEvent], None] | None = None,
    ) -> AgentResponse:
        return self.send_turn_streaming(
            build_turn_payload(
                agent_request,
                session_id=session_id,
                locale=locale,
                turn_id=turn_id,
            ),
            on_event=on_event,
        )

    def _post_json_stream(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> Iterator[ThoughtCoreStreamEvent]:
        remaining_s = _remaining_deadline_seconds(deadline_monotonic)
        if deadline_monotonic is not None and not _is_same_host_deadline_target(
            self.base_url
        ):
            raise ThoughtCoreClientError(
                "thought-core route deadline requires a same-host target"
            )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if deadline_monotonic is not None:
            headers[ROUTE_DEADLINE_HEADER] = format(deadline_monotonic, ".9f")
        req = request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        started_monotonic = time.monotonic()

        try:
            remaining_s = _remaining_deadline_seconds(deadline_monotonic)
            timeout_s = (
                self.timeout_s
                if remaining_s is None
                else min(self.timeout_s, remaining_s)
            )
            with request.urlopen(req, timeout=timeout_s) as response:
                for payload_chunk in iter_sse_json_payloads(response):
                    _remaining_deadline_seconds(deadline_monotonic)
                    yield ThoughtCoreStreamEvent.from_payload(
                        payload_chunk,
                        elapsed_s=time.monotonic() - started_monotonic,
                    )
        except error.HTTPError as exc:
            raise ThoughtCoreClientError(
                f"thought-core returned HTTP {exc.code}"
            ) from exc
        except error.URLError as exc:
            raise ThoughtCoreClientError("failed to connect to thought-core") from exc


def _remaining_deadline_seconds(deadline_monotonic: float | None) -> float | None:
    if deadline_monotonic is None:
        # Compatibility: the current two-argument ai-talk-core private-turn callback
        # cannot yet pass a deadline. Remove this branch after that named consumer
        # supplies deadline_monotonic for every accepted candidate.
        return None
    if (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(float(deadline_monotonic))
    ):
        raise ThoughtCoreClientError("thought-core route deadline invalid")
    remaining_s = float(deadline_monotonic) - time.monotonic()
    if remaining_s <= 0:
        raise ThoughtCoreClientError("thought-core route deadline expired")
    if remaining_s > MAX_ROUTE_DEADLINE_SECONDS:
        raise ThoughtCoreClientError("thought-core route deadline invalid")
    return remaining_s


def _is_same_host_deadline_target(base_url: str) -> bool:
    hostname = (parse.urlsplit(base_url).hostname or "").lower().rstrip(".")
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        local_names = {
            socket.gethostname().lower().rstrip("."),
            socket.getfqdn().lower().rstrip("."),
        }
        return hostname in local_names


def validate_base_url(base_url: str) -> str:
    return validate_http_url(base_url, label="THOUGHT_CORE_BASE_URL")


def build_turn_payload(
    agent_request: AgentRequest,
    *,
    session_id: str | None = None,
    locale: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    context = dict(agent_request.context)
    context_refs = context.get("context_refs")
    if context_refs is not None and not isinstance(context_refs, Mapping):
        raise ValueError("context_refs must be an object")

    resolved_turn_id = (
        turn_id
        or _optional_mapping_text(context, "turn_id")
        or f"turn_{int(agent_request.timestamp * 1000)}"
    )
    resolved_session_id = (
        session_id
        or _optional_mapping_text(context, "session_id")
        or agent_request.user
        or "local-user"
    )
    resolved_locale = locale or _optional_mapping_text(context, "locale") or "ja-JP"

    payload: dict[str, Any] = {
        "text": agent_request.text,
        "turn_id": resolved_turn_id,
        "session_id": resolved_session_id,
        "locale": resolved_locale,
        "context_refs": dict(context_refs or {}),
    }
    if agent_request.conversation_id:
        payload["context_refs"]["conversation_id"] = agent_request.conversation_id
    return payload


def iter_sse_json_payloads(
    lines: Iterable[bytes | str],
) -> Iterator[dict[str, Any]]:
    event_type = ""
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
            if payload is not None:
                if event_type and not payload.get("type"):
                    payload["type"] = event_type
                yield payload
            event_type = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    payload = decode_sse_data_lines(data_lines)
    if payload is not None:
        if event_type and not payload.get("type"):
            payload["type"] = event_type
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
        raise ThoughtCoreClientError("thought-core returned invalid SSE JSON") from exc
    if not isinstance(decoded, dict):
        raise ThoughtCoreClientError("thought-core returned unexpected SSE JSON payload")
    return decoded


def thought_core_error_message(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        data = {}
    code = str(data.get("code") or payload.get("code") or "").strip()
    message = str(data.get("message") or payload.get("message") or "turn error").strip()
    details = " ".join(part for part in (code, message) if part)
    return f"thought-core turn error: {details}"


def _optional_mapping_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ThoughtCoreClientError(f"thought-core event {key} must be a string")
    text = str(value).strip()
    return text or None


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ThoughtCoreClientError(f"thought-core event {key} must be a string")
    text = value.strip()
    if not text:
        raise ThoughtCoreClientError(f"thought-core event {key} must not be empty")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ThoughtCoreClientError(f"thought-core event {key} must not contain control characters")
    return text


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ThoughtCoreClientError("thought-core event seq must be an integer")
    return value
