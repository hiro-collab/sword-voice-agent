from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib import error, request

from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.protocol.messages import AgentRequest, VoiceState

AI_TALK_CORE_WEB_TOKEN_ENV = "AI_TALK_CORE_WEB_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-AI-Core-Token"
ACCEPTED_USER_SPEECH_CANDIDATE_INPUT_GATE_SCHEMA = (
    "accepted_user_speech_candidate_input_gate.v0"
)


class AiTalkCoreInputGateError(RuntimeError):
    pass


class AiTalkCoreHandoffError(RuntimeError):
    pass


@dataclass(frozen=True)
class AiTalkCoreHandoff:
    transcript: str
    command: str
    prompt_text: str = ""
    source: str = "web"
    json_path: Path | None = None
    text_path: Path | None = None
    turn_id: str | None = None

    def text_for_agent(self, field: str = "command") -> str:
        if field == "command":
            return self.command
        if field == "transcript":
            return self.transcript
        if field == "prompt":
            return self.prompt_text
        raise AiTalkCoreHandoffError(
            "handoff field must be command, transcript, or prompt"
        )

    def to_agent_request(
        self,
        *,
        field: str = "command",
        user: str = "local-user",
        conversation_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        include_transcript_context: bool = False,
    ) -> AgentRequest:
        text = self.text_for_agent(field).strip()
        if not text:
            raise AiTalkCoreHandoffError(f"handoff {field} is empty")

        request_context: dict[str, Any] = {
            "source": "ai_talk_core",
            "handoff_source": self.source,
            "handoff_field": field,
            # A handoff can originate from a gesture, a browser button,
            # TouchDesigner, or another trusted local adapter.  The legacy
            # handoff bundle does not retain that exact trigger identity, so do
            # not invent one here.  Callers with correlated trigger evidence
            # may still override this value through ``context`` below.
            "trigger": "ai_talk_core_handoff",
        }
        if self.turn_id:
            request_context["turn_id"] = self.turn_id
        if include_transcript_context and self.transcript:
            request_context["transcript"] = self.transcript
        if context:
            request_context.update(dict(context))

        return AgentRequest(
            text=text,
            user=user,
            context=request_context,
            conversation_id=conversation_id,
        )


def build_input_gate_state_payload(
    *,
    input_enabled: bool,
    reason: str,
    source: str,
    timestamp: float | None = None,
) -> dict[str, bool | float | str | None]:
    """Build the one backend-neutral input-gate state payload.

    Gesture smoothing is only one producer of this payload.  TouchDesigner,
    local web tools, buttons, and other trusted adapters use the same shape and
    the same ai-talk-core decision owner.
    """
    if not isinstance(input_enabled, bool):
        raise ValueError("input_enabled must be a boolean")
    return {
        "type": "input_gate_state",
        "input_enabled": input_enabled,
        "mic_enabled": input_enabled,
        "reason": reason,
        "source": source,
        "timestamp": timestamp,
    }


def voice_state_to_input_gate_payload(
    voice_state: VoiceState,
    source: str = "sword_voice_agent",
) -> dict[str, bool | float | str | None]:
    reason = voice_state.reason or voice_state.phase.value
    return build_input_gate_state_payload(
        input_enabled=voice_state.mic_enabled,
        reason=reason,
        source=source,
        timestamp=voice_state.timestamp,
    )


class AiTalkCoreInputGateClient:
    """HTTP client for an ai_talk_core-compatible input-gate endpoint."""

    def __init__(
        self,
        endpoint_url: str = "http://127.0.0.1:8000/api/input-gate",
        timeout_s: float = 5.0,
        source: str = "sword_voice_agent",
        api_token: str | None = None,
    ) -> None:
        self.endpoint_url = validate_http_url(
            endpoint_url,
            label="ai_talk_core input gate URL",
        )
        self.timeout_s = timeout_s
        self.source = source
        self.api_token = resolve_ai_talk_core_web_token(api_token)

    def send_voice_state(self, voice_state: VoiceState) -> dict[str, Any]:
        return self.set_input_enabled(
            voice_state.mic_enabled,
            reason=voice_state.reason or voice_state.phase.value,
            timestamp=voice_state.timestamp,
        )

    def set_input_enabled(
        self,
        input_enabled: bool,
        *,
        reason: str = "external",
        source: str | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Send one generic local trigger state to the canonical input gate."""
        payload = build_input_gate_state_payload(
            input_enabled=input_enabled,
            reason=reason,
            source=source or self.source,
            timestamp=timestamp,
        )
        return self._post_json(payload)

    def _post_json(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_token:
            headers[LOCAL_API_TOKEN_HEADER] = self.api_token
        req = request.Request(
            url=self.endpoint_url,
            data=body,
            method="POST",
            headers=headers,
        )

        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise AiTalkCoreInputGateError(
                f"ai_talk_core input gate returned HTTP {exc.code}: {details}"
            ) from exc
        except error.URLError as exc:
            raise AiTalkCoreInputGateError(
                f"failed to connect to ai_talk_core input gate: {exc}"
            ) from exc

        if not response_body:
            return {}
        try:
            decoded = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise AiTalkCoreInputGateError(
                "ai_talk_core input gate returned non-JSON response"
            ) from exc

        if not isinstance(decoded, dict):
            raise AiTalkCoreInputGateError(
                "ai_talk_core input gate returned unexpected JSON payload"
            )
        return decoded


def resolve_ai_talk_core_web_token(value: str | None = None) -> str:
    return (value or os.environ.get(AI_TALK_CORE_WEB_TOKEN_ENV, "")).strip()


def ai_talk_core_api_headers(api_token: str | None = None) -> dict[str, str]:
    token = resolve_ai_talk_core_web_token(api_token)
    if not token:
        return {}
    return {LOCAL_API_TOKEN_HEADER: token}


def get_handoff_json_path(ai_talk_core_root: str | Path, source: str = "web") -> Path:
    safe_source = _normalize_handoff_source(source)
    return Path(ai_talk_core_root) / ".cache" / "codex" / f"{safe_source}_latest.json"


def get_handoff_text_path(ai_talk_core_root: str | Path, source: str = "web") -> Path:
    safe_source = _normalize_handoff_source(source)
    return Path(ai_talk_core_root) / ".cache" / "codex" / f"{safe_source}_latest.txt"


def load_handoff_from_root(
    ai_talk_core_root: str | Path,
    *,
    source: str = "web",
) -> AiTalkCoreHandoff:
    return load_handoff_json(
        get_handoff_json_path(ai_talk_core_root, source),
        source=source,
        text_path=get_handoff_text_path(ai_talk_core_root, source),
    )


def load_handoff_json(
    json_path: str | Path,
    *,
    source: str = "web",
    text_path: str | Path | None = None,
) -> AiTalkCoreHandoff:
    path = Path(json_path)
    if not path.exists():
        raise AiTalkCoreHandoffError(f"handoff JSON not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AiTalkCoreHandoffError(f"handoff JSON is invalid: {path}") from exc

    if not isinstance(payload, Mapping):
        raise AiTalkCoreHandoffError("handoff JSON must be an object")

    transcript = _expect_text(payload, "transcript")
    command = _expect_text(payload, "command")
    prompt_text = ""
    resolved_text_path = Path(text_path) if text_path is not None else None
    if resolved_text_path is not None and resolved_text_path.exists():
        prompt_text = resolved_text_path.read_text(encoding="utf-8")

    return AiTalkCoreHandoff(
        transcript=transcript,
        command=command,
        prompt_text=prompt_text,
        source=_normalize_handoff_source(source),
        json_path=path,
        text_path=resolved_text_path,
        turn_id=(
            str(payload["turn_id"])
            if payload.get("turn_id") is not None
            else None
        ),
    )


def load_accepted_user_speech_candidate_json(
    json_path: str | Path,
) -> dict[str, Any]:
    payload = _load_json_mapping(json_path, label="accepted speech candidate")
    if payload.get("schema_version") != ACCEPTED_USER_SPEECH_CANDIDATE_INPUT_GATE_SCHEMA:
        raise AiTalkCoreHandoffError(
            "accepted speech candidate requires canonical input-gate schema"
        )
    return payload


def load_private_turn_json(json_path: str | Path) -> dict[str, Any]:
    return _load_json_mapping(json_path, label="private turn")


def build_accepted_user_speech_turn_envelope(
    candidate: Mapping[str, Any],
    private_turn: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    if candidate.get("schema_version") != ACCEPTED_USER_SPEECH_CANDIDATE_INPUT_GATE_SCHEMA:
        raise AiTalkCoreHandoffError(
            "accepted speech candidate requires canonical input-gate schema"
        )
    if not isinstance(private_turn, Mapping):
        raise AiTalkCoreHandoffError("private turn must be an object")
    return {
        "accepted_user_speech_candidate": dict(candidate),
        "private_turn": dict(private_turn),
    }


def _load_json_mapping(json_path: str | Path, *, label: str) -> dict[str, Any]:
    path = Path(json_path)
    if not path.exists():
        raise AiTalkCoreHandoffError(f"{label} JSON not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AiTalkCoreHandoffError(f"{label} JSON is invalid: {path}") from exc
    if not isinstance(payload, Mapping):
        raise AiTalkCoreHandoffError(f"{label} JSON must be an object")
    return dict(payload)


def _expect_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise AiTalkCoreHandoffError(f"handoff JSON requires string {key!r}")
    return value




def _normalize_handoff_source(source: str) -> str:
    normalized = (source or "web").strip() or "web"
    if not normalized.replace("_", "").replace("-", "").isalnum():
        raise AiTalkCoreHandoffError(
            "handoff source must contain only letters, numbers, hyphen, or underscore"
        )
    return normalized
