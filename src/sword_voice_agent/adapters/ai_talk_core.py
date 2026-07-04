from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib import error, request

from sword_voice_agent.adapters.auth import validate_http_url
from sword_voice_agent.protocol.messages import AgentRequest, VoiceState

AI_TALK_CORE_WEB_TOKEN_ENV = "AI_TALK_CORE_WEB_TOKEN"
LOCAL_API_TOKEN_HEADER = "X-AI-Core-Token"
ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA_VERSION = "accepted_user_speech_candidate.v0"


class AiTalkCoreInputGateError(RuntimeError):
    pass


class AiTalkCoreHandoffError(RuntimeError):
    pass


class AiTalkCoreAcceptedSpeechCandidateError(RuntimeError):
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
            "trigger": "sword_sign",
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


@dataclass(frozen=True)
class AcceptedUserSpeechCandidate:
    accepted_text: str
    turn_id: str
    session_id: str
    candidate_id: str
    locale: str = "ja-JP"
    source: str = "ai_talk_core"
    context_refs: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AcceptedUserSpeechCandidate":
        if payload.get("schema_version") != ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA_VERSION:
            raise AiTalkCoreAcceptedSpeechCandidateError(
                "accepted speech candidate requires accepted_user_speech_candidate.v0"
            )
        _raise_if_non_materializing_audio(payload)
        if payload.get("acceptance_status") != "accepted":
            raise AiTalkCoreAcceptedSpeechCandidateError(
                "accepted speech candidate requires acceptance_status=accepted"
            )
        if payload.get("may_start_user_turn") is not True:
            raise AiTalkCoreAcceptedSpeechCandidateError(
                "accepted speech candidate requires may_start_user_turn=true"
            )
        if payload.get("turn_adoption_authority") is not True:
            raise AiTalkCoreAcceptedSpeechCandidateError(
                "accepted speech candidate requires turn_adoption_authority=true"
            )
        if payload.get("raw_private_publication_flags") is not False:
            raise AiTalkCoreAcceptedSpeechCandidateError(
                "accepted speech candidate requires raw_private_publication_flags=false"
            )

        context_refs = payload.get("context_refs") or {}
        if not isinstance(context_refs, Mapping):
            raise AiTalkCoreAcceptedSpeechCandidateError("context_refs must be an object")

        return cls(
            accepted_text=_expect_non_empty_text(payload, "accepted_text"),
            turn_id=_expect_non_empty_text(payload, "turn_id"),
            session_id=_expect_non_empty_text(payload, "session_id"),
            candidate_id=_expect_non_empty_text(payload, "candidate_id"),
            locale=str(payload.get("locale") or "ja-JP"),
            source=str(payload.get("source") or "ai_talk_core"),
            context_refs=dict(context_refs),
        )

    def to_agent_request(
        self,
        *,
        user: str = "local-user",
        conversation_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> AgentRequest:
        context_refs = dict(self.context_refs)
        context_refs.setdefault("accepted_user_speech_candidate_ref", self.candidate_id)
        request_context: dict[str, Any] = {
            "source": "ai_talk_core",
            "handoff_source": self.source,
            "handoff_field": "accepted_user_speech_candidate",
            "trigger": "accepted_user_speech_candidate",
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "locale": self.locale,
            "context_refs": context_refs,
        }
        if context:
            request_context.update(dict(context))
            if "context_refs" in context:
                extra_refs = context["context_refs"]
                if not isinstance(extra_refs, Mapping):
                    raise AiTalkCoreAcceptedSpeechCandidateError(
                        "context context_refs must be an object"
                    )
                merged_refs = dict(context_refs)
                merged_refs.update(dict(extra_refs))
                request_context["context_refs"] = merged_refs

        return AgentRequest(
            text=self.accepted_text,
            user=user,
            context=request_context,
            conversation_id=conversation_id,
        )


def voice_state_to_input_gate_payload(
    voice_state: VoiceState,
    source: str = "sword_voice_agent",
) -> dict[str, bool | float | str | None]:
    reason = voice_state.reason or voice_state.phase.value
    return {
        "type": "input_gate_state",
        "input_enabled": voice_state.mic_enabled,
        "mic_enabled": voice_state.mic_enabled,
        "reason": reason,
        "source": source,
        "timestamp": voice_state.timestamp,
    }


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
        payload = voice_state_to_input_gate_payload(voice_state, source=self.source)
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


def _expect_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise AiTalkCoreHandoffError(f"handoff JSON requires string {key!r}")
    return value


def _expect_non_empty_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AiTalkCoreAcceptedSpeechCandidateError(
            f"accepted speech candidate requires non-empty {key}"
        )
    return value.strip()


_BLOCKED_ACCEPTED_SPEECH_CLASSIFICATIONS = {
    "blocked_self_output",
    "blocked_cooldown",
    "blocked_ambiguous",
    "blocked_missing_session_join",
    "blocked_low_confidence",
    "blocked_capture_not_ready",
    "system_self_output_candidate",
    "mixed_or_ambiguous_audio",
}

_NON_MATERIALIZING_AUDIO_GATE_DECISIONS = _BLOCKED_ACCEPTED_SPEECH_CLASSIFICATIONS | {
    "candidate_user_turn_needs_ai_talk_core_acceptance",
    "recognition_low_confidence",
    "recognition_failed",
    "recognition_not_authorized",
}


def _raise_if_non_materializing_audio(payload: Mapping[str, Any]) -> None:
    if payload.get("speaker_role") == "system_self_output":
        raise AiTalkCoreAcceptedSpeechCandidateError("system self-output is not a user turn")
    if payload.get("route") == "self_output_observation":
        raise AiTalkCoreAcceptedSpeechCandidateError(
            "self-output observation cannot become a user turn"
        )

    pre_turn_result = payload.get("pre_turn_result")
    if isinstance(pre_turn_result, Mapping) and (
        pre_turn_result.get("turn_input_materialized") is False
        or pre_turn_result.get("normal_turn_adoption_blocked") is True
    ):
        raise AiTalkCoreAcceptedSpeechCandidateError(
            "blocked pre-turn result cannot become a user turn"
        )

    status = payload.get("turn_adoption_status")
    if isinstance(status, str) and status in _BLOCKED_ACCEPTED_SPEECH_CLASSIFICATIONS:
        raise AiTalkCoreAcceptedSpeechCandidateError(
            "blocked turn adoption status cannot become a user turn"
        )

    classification = payload.get("classification")
    if (
        isinstance(classification, str)
        and classification in _BLOCKED_ACCEPTED_SPEECH_CLASSIFICATIONS
    ):
        raise AiTalkCoreAcceptedSpeechCandidateError(
            "blocked audio classification cannot become a user turn"
        )

    gate_decision = payload.get("self_output_gate_decision")
    if (
        isinstance(gate_decision, str)
        and gate_decision in _NON_MATERIALIZING_AUDIO_GATE_DECISIONS
    ):
        raise AiTalkCoreAcceptedSpeechCandidateError(
            "non-materializing audio gate decision cannot become a user turn"
        )


def _normalize_handoff_source(source: str) -> str:
    normalized = (source or "web").strip() or "web"
    if not normalized.replace("_", "").replace("-", "").isalnum():
        raise AiTalkCoreHandoffError(
            "handoff source must contain only letters, numbers, hyphen, or underscore"
        )
    return normalized
