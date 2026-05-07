"""Single-step tool adapters for the thought-core experiment."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib import error, request
from urllib.parse import urljoin

from .schema import TurnInput


class ThoughtTools(Protocol):
    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        ...

    def home_preview(self, turn: TurnInput, observation: dict[str, Any]) -> dict[str, Any]:
        ...

    def home_execute(self, turn: TurnInput, action: dict[str, Any]) -> dict[str, Any]:
        ...

    def memory_retrieve(self, turn: TurnInput) -> dict[str, Any]:
        ...

    def memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        ...

    def web_search(self, turn: TurnInput, query: str) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class HomeLightIntent:
    action_id: str
    expected_state: str
    action_name: str
    target: str = "light"
    target_name: str = "リビングの電気"


@dataclass
class MockThoughtTools:
    """Dependency-free mock tools.

    home_execute is intentionally one-shot. Retry behavior is owned by
    ThoughtLoop, not by this adapter.
    """

    execute_failures_before_success: int = 0
    include_secret_in_execute_result: bool = False
    light_on: bool = False
    execute_calls: list[dict[str, Any]] = field(default_factory=list)
    execute_attempts_by_turn: dict[str, int] = field(default_factory=dict)
    light_state_by_turn: dict[str, bool] = field(default_factory=dict)

    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        light_on = self._light_on_for_turn(turn)
        forced_state = _optional_context_text(turn, f"mock_{reason}_light_state")
        if forced_state in {"on", "off"}:
            light_on = forced_state == "on"
            self.light_state_by_turn[turn.turn_id] = light_on
        state = "on" if light_on else "off"
        return {
            "status": "ok",
            "observation_ref": (
                f"obs_{self.execute_attempts_by_turn.get(turn.turn_id, 0):04d}_{reason}"
            ),
            "observation_source": "environment-state-server.mock",
            "facts": {
                "location": "living_room",
                "devices": [
                    {
                        "id": "living_room_light",
                        "kind": "light",
                        "name": "リビングの電気",
                        "state": state,
                    }
                ],
            },
        }

    def home_preview(self, turn: TurnInput, observation: dict[str, Any]) -> dict[str, Any]:
        intent = detect_home_light_intent(turn.text) or HomeLightIntent(
            action_id="light_on",
            expected_state="on",
            action_name="home.light.turn_on",
        )
        return {
            "status": "ok",
            "action": {
                "action": intent.action_name,
                "action_id": intent.action_id,
                "target": intent.target,
                "target_aliases": ["light", "living_room_light"],
                "target_name": intent.target_name,
                "confidence": 0.93,
                "expected_state": intent.expected_state,
            },
        }

    def home_execute(self, turn: TurnInput, action: dict[str, Any]) -> dict[str, Any]:
        self.execute_calls.append(action)
        attempt = self.execute_attempts_by_turn.get(turn.turn_id, 0) + 1
        self.execute_attempts_by_turn[turn.turn_id] = attempt
        failures_before_success = _optional_context_int(
            turn,
            "mock_execute_failures_before_success",
            self.execute_failures_before_success,
        )
        if attempt <= failures_before_success:
            result: dict[str, Any] = {
                "status": "failed",
                "retryable": True,
                "error": "mock_home_execute_failed",
                "attempt": attempt,
            }
        else:
            self.light_on = action.get("expected_state") == "on"
            self.light_state_by_turn[turn.turn_id] = self.light_on
            result = {
                "status": "accepted",
                "retryable": False,
                "command_id": f"cmd_{attempt:04d}",
                "attempt": attempt,
            }
        if self.include_secret_in_execute_result:
            result["access_token"] = "mock-token-that-must-not-leak"
            result["authorization"] = "Bearer mock-token-that-must-not-leak"
        return result

    def memory_retrieve(self, turn: TurnInput) -> dict[str, Any]:
        return {"status": "ok", "items": []}

    def memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "written": True}

    def web_search(self, turn: TurnInput, query: str) -> dict[str, Any]:
        return {"status": "ok", "query": query, "results": []}

    def _light_on_for_turn(self, turn: TurnInput) -> bool:
        if turn.turn_id in self.light_state_by_turn:
            return self.light_state_by_turn[turn.turn_id]
        initial_state = _optional_context_text(turn, "mock_initial_light_state")
        if initial_state in {"on", "off"}:
            self.light_state_by_turn[turn.turn_id] = initial_state == "on"
        else:
            self.light_state_by_turn[turn.turn_id] = self.light_on
        return self.light_state_by_turn[turn.turn_id]


@dataclass(frozen=True)
class HomeControlToolConfig:
    bridge_base_url: str
    api_token: str
    environment_state_url: str = ""
    environment_api_token: str = ""
    timeout_s: float = 3.0

    @classmethod
    def from_env(cls) -> "HomeControlToolConfig":
        bridge_base_url = _env_first(
            "HOME_CONTROL_BRIDGE_URL",
            "HOME_ASSISTANT_BRIDGE_URL",
            default="http://127.0.0.1:8787",
        )
        api_token = _env_first("HOME_CONTROL_API_TOKEN")
        environment_state_url = _env_first(
            "ENVIRONMENT_STATE_URL",
            default="http://127.0.0.1:8790/environment/current",
        )
        environment_api_token = _env_first("ENVIRONMENT_API_TOKEN") or api_token
        timeout_s = _optional_float(_env_first("THOUGHT_CORE_HOME_HTTP_TIMEOUT_S"), 3.0)
        return cls(
            bridge_base_url=bridge_base_url,
            api_token=api_token,
            environment_state_url=environment_state_url,
            environment_api_token=environment_api_token,
            timeout_s=timeout_s,
        )


@dataclass
class HomeControlHttpTools:
    """HTTP adapter for the local Home Control Safety Bridge.

    This adapter keeps the Thought Core boundary narrow: preview/execute only
    use allowlisted bridge action_id values. Home Assistant service/entity
    details stay inside the bridge configuration.
    """

    config: HomeControlToolConfig
    execute_attempts_by_turn: dict[str, int] = field(default_factory=dict)

    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        if not self.config.environment_state_url or not self.config.environment_api_token:
            return {
                "status": "skipped",
                "observation_ref": f"env_unconfigured_{reason}",
                "observation_source": "environment-state-server.unconfigured",
                "facts": {"devices": []},
            }
        try:
            payload = self._json_request(
                "GET",
                self.config.environment_state_url,
                token=self.config.environment_api_token,
            )
        except HomeControlToolError as exc:
            return {
                "status": "failed",
                "retryable": True,
                "error": exc.code,
                "detail": exc.safe_detail,
                "observation_ref": f"env_error_{reason}",
                "observation_source": "environment-state-server.http",
                "facts": {"devices": []},
            }
        return {
            "status": "ok",
            "observation_ref": str(payload.get("snapshot_id") or f"env_{reason}"),
            "observation_source": "environment-state-server.http",
            "facts": _facts_from_environment_current(payload),
            "environment": payload,
        }

    def home_preview(self, turn: TurnInput, observation: dict[str, Any]) -> dict[str, Any]:
        intent = detect_home_light_intent(turn.text)
        if intent is None:
            return {
                "status": "unsupported",
                "error": "unsupported_home_intent",
                "action": {},
            }
        if not self.config.api_token:
            return {
                "status": "failed",
                "error": "home_control_api_token_missing",
                "action": _action_from_intent(intent),
            }
        try:
            payload = self._json_request(
                "POST",
                self._bridge_url(f"/actions/{intent.action_id}/preview"),
                token=self.config.api_token,
                body=_bridge_body(turn, request_id=f"{turn.turn_id}-preview"),
            )
        except HomeControlToolError as exc:
            return {
                "status": "failed",
                "error": exc.code,
                "detail": exc.safe_detail,
                "action": _action_from_intent(intent),
            }

        action = _action_from_intent(intent)
        action.update(
            {
                "confirm_required": bool(payload.get("confirmation_required")),
                "bridge_status": payload.get("status"),
                "expected_effect": payload.get("expected_effect"),
                "preview": payload.get("preview"),
            }
        )
        if payload.get("expected_state"):
            action["expected_state"] = payload.get("expected_state")
        return {
            "status": "ok" if payload.get("ok", True) else "failed",
            "action": action,
            "bridge_response": payload,
        }

    def home_execute(self, turn: TurnInput, action: dict[str, Any]) -> dict[str, Any]:
        action_id = str(action.get("action_id") or "").strip()
        if not action_id:
            return {
                "status": "failed",
                "retryable": False,
                "error": "missing_action_id",
            }
        if not self.config.api_token:
            return {
                "status": "failed",
                "retryable": False,
                "error": "home_control_api_token_missing",
            }

        attempt = self.execute_attempts_by_turn.get(turn.turn_id, 0) + 1
        self.execute_attempts_by_turn[turn.turn_id] = attempt
        try:
            payload = self._json_request(
                "POST",
                self._bridge_url(f"/actions/{action_id}/execute"),
                token=self.config.api_token,
                body=_bridge_body(turn, request_id=f"{turn.turn_id}-attempt-{attempt}"),
            )
        except HomeControlToolError as exc:
            return {
                "status": "failed",
                "retryable": True,
                "error": exc.code,
                "detail": exc.safe_detail,
                "attempt": attempt,
            }

        bridge_status = str(payload.get("status") or "unknown")
        executed = bool(payload.get("executed"))
        ok = bool(payload.get("ok", executed))
        return {
            "status": "accepted" if ok and executed else bridge_status,
            "retryable": not ok and bridge_status not in {"confirmation_required", "dry_run"},
            "executed": executed,
            "verified_by_bridge": ok and executed,
            "command_id": payload.get("execution_id"),
            "attempt": attempt,
            "bridge_status": bridge_status,
            "message": payload.get("message"),
            "speak": payload.get("speak"),
            "expected_state": payload.get("expected_state"),
            "expected_effect": payload.get("expected_effect"),
        }

    def memory_retrieve(self, turn: TurnInput) -> dict[str, Any]:
        return {"status": "ok", "items": []}

    def memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "written": True}

    def web_search(self, turn: TurnInput, query: str) -> dict[str, Any]:
        return {"status": "ok", "query": query, "results": []}

    def _bridge_url(self, path: str) -> str:
        base = self.config.bridge_base_url.rstrip("/") + "/"
        return urljoin(base, path.lstrip("/"))

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        token: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.config.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = _safe_http_error_detail(exc)
            raise HomeControlToolError("home_control_http_error", detail) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise HomeControlToolError("home_control_request_failed", str(exc)) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HomeControlToolError("home_control_invalid_json", "invalid JSON response") from exc
        if not isinstance(payload, dict):
            raise HomeControlToolError("home_control_invalid_json", "JSON response was not an object")
        return payload


class HomeControlToolError(Exception):
    def __init__(self, code: str, safe_detail: str) -> None:
        super().__init__(f"{code}: {safe_detail}")
        self.code = code
        self.safe_detail = safe_detail


def build_tools_from_env() -> ThoughtTools:
    adapter = _env_first("THOUGHT_CORE_TOOLS_ADAPTER").strip().lower()
    if adapter in {"mock", "local_mock", "local-mock"}:
        return MockThoughtTools()
    bridge_url_configured = bool(_env_first("HOME_CONTROL_BRIDGE_URL", "HOME_ASSISTANT_BRIDGE_URL"))
    if adapter in {"home_control", "home-control", "bridge"} or bridge_url_configured:
        return HomeControlHttpTools(HomeControlToolConfig.from_env())
    return MockThoughtTools()


def detect_home_light_intent(text: str) -> HomeLightIntent | None:
    normalized = text.replace(" ", "").replace("　", "")
    lowered = normalized.lower()
    mentions_light = any(word in normalized for word in ("電気", "ライト", "照明"))
    if not mentions_light:
        return None
    if any(word in normalized for word in ("消し", "消す", "消して", "消灯", "オフ")) or "off" in lowered:
        return HomeLightIntent(
            action_id="light_off",
            expected_state="off",
            action_name="home.light.turn_off",
        )
    if (
        any(word in normalized for word in ("つけ", "点け", "付け", "点灯", "オン"))
        or "on" in lowered
    ):
        return HomeLightIntent(
            action_id="light_on",
            expected_state="on",
            action_name="home.light.turn_on",
        )
    return None


def _action_from_intent(intent: HomeLightIntent) -> dict[str, Any]:
    return {
        "action": intent.action_name,
        "action_id": intent.action_id,
        "target": intent.target,
        "target_aliases": ["light", "living_room_light"],
        "target_name": intent.target_name,
        "confidence": 0.93,
        "expected_state": intent.expected_state,
    }


def _facts_from_environment_current(payload: dict[str, Any]) -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    appliances = payload.get("appliances") if isinstance(payload.get("appliances"), dict) else {}
    for key, appliance in appliances.items():
        if not isinstance(appliance, dict):
            continue
        devices.append(
            {
                "id": str(key),
                "kind": str(key),
                "name": _device_name(str(key)),
                "state": appliance.get("state"),
                "stale": appliance.get("stale"),
                "updated_at": appliance.get("updated_at"),
                "source": appliance.get("source"),
                "action_id": appliance.get("action_id"),
            }
        )
    return {
        "devices": devices,
        "last_home_assistant_events": payload.get("last_home_assistant_events", []),
        "state_queries": payload.get("state_queries", {}),
    }


def _device_name(device_id: str) -> str:
    return {
        "light": "リビングの電気",
        "living_room_light": "リビングの電気",
        "fan": "扇風機",
    }.get(device_id, device_id)


def _bridge_body(turn: TurnInput, *, request_id: str) -> dict[str, Any]:
    return {
        "source": "thought-core",
        "request_id": request_id,
        "user_text": turn.text,
    }


def _safe_http_error_detail(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    if not body:
        return f"HTTP {exc.code}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return f"HTTP {exc.code}"
    if not isinstance(payload, dict):
        return f"HTTP {exc.code}"
    detail = payload.get("error") or payload.get("message") or payload.get("detail")
    return f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}"


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _optional_float(value: str, default: float) -> float:
    try:
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default


def _optional_context_text(turn: TurnInput, key: str) -> str:
    value = turn.context_refs.get(key)
    if value is None:
        return ""
    return str(value).strip().lower()


def _optional_context_int(turn: TurnInput, key: str, default: int) -> int:
    value = turn.context_refs.get(key)
    if value is None:
        return default
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return default
