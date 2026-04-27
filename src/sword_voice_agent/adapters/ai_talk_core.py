from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import error, request

from sword_voice_agent.protocol.messages import VoiceState


class AiTalkCoreInputGateError(RuntimeError):
    pass


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
    ) -> None:
        self.endpoint_url = endpoint_url
        self.timeout_s = timeout_s
        self.source = source

    def send_voice_state(self, voice_state: VoiceState) -> dict[str, Any]:
        payload = voice_state_to_input_gate_payload(voice_state, source=self.source)
        return self._post_json(payload)

    def _post_json(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=self.endpoint_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
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

