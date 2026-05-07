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

    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        state = "on" if self.light_on else "off"
        return {
            "status": "ok",
            "observation_ref": f"obs_{len(self.execute_calls):04d}_{reason}",
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
        attempt = len(self.execute_calls)
        if attempt <= self.execute_failures_before_success:
            result: dict[str, Any] = {
                "status": "failed",
                "retryable": True,
                "error": "mock_home_execute_failed",
                "attempt": attempt,
            }
        else:
            self.light_on = action.get("expected_state") == "on"
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

