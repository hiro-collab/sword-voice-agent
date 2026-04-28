from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib import error, request
from urllib.parse import urlparse

from sword_voice_agent.adapters.auth import is_loopback_host
from sword_voice_agent.protocol.messages import AgentRequest, AgentResponse, now_timestamp


class DifyClientError(RuntimeError):
    pass


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
        payload: dict[str, Any] = {
            "inputs": dict(agent_request.context),
            "query": agent_request.text,
            "response_mode": "blocking",
            "user": agent_request.user,
        }
        if agent_request.conversation_id:
            payload["conversation_id"] = agent_request.conversation_id

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


def validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("DIFY_BASE_URL must be an http(s) URL")
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname or ""):
        raise ValueError(
            "DIFY_BASE_URL may use http only for loopback hosts; use https for remote Dify"
        )
    return base_url
