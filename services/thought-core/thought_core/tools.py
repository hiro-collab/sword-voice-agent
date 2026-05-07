"""Single-step tool adapters for the thought-core experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

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
        return {
            "status": "ok",
            "action": {
                "action": "home.light.turn_on",
                "target": "living_room_light",
                "target_name": "リビングの電気",
                "confidence": 0.93,
                "expected_state": "on",
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
