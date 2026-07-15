"""Single-step tool adapters for the thought-core experiment."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from urllib import error, request
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .execution_deadline import (
    TurnDeadlineExceeded,
    clamp_execution_timeout,
    ensure_execution_active,
    remaining_execution_seconds,
)
from .schema import TurnInput


class ThoughtTools(Protocol):
    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        ...

    def home_preview(self, turn: TurnInput, observation: dict[str, Any]) -> dict[str, Any]:
        ...

    def home_execute(self, turn: TurnInput, action: dict[str, Any]) -> dict[str, Any]:
        ...

    def state_query_feedback(
        self,
        turn: TurnInput,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def memory_retrieve(self, turn: TurnInput) -> dict[str, Any]:
        ...

    def short_memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
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
    pre_action_phrase: str = ""
    available: bool = True
    noop: bool = False
    reason: str = ""
    reason_text: str = ""
    control_type: str = ""
    state_authority: str = ""
    verification_mode: str = ""
    state_tracking: str = ""
    proof_ceiling: str = ""
    live_test_readiness: str = ""
    live_test_blockers: tuple[str, ...] = ()
    restore_action_id: str = ""
    stop_action_id: str = ""
    terminal_action: bool = False
    safety_requirements: tuple[str, ...] = ()


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
    appliance_states: dict[str, str] = field(default_factory=dict)
    state_query_feedback_calls: list[dict[str, Any]] = field(default_factory=list)
    short_memory_write_calls: list[dict[str, Any]] = field(default_factory=list)
    memory_write_calls: list[dict[str, Any]] = field(default_factory=list)

    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        light_on = self._light_on_for_turn(turn)
        forced_state = _optional_context_text(turn, f"mock_{reason}_light_state")
        if forced_state in {"on", "off"}:
            light_on = forced_state == "on"
            self.light_state_by_turn[turn.turn_id] = light_on
        state = "on" if light_on else "off"
        devices = []
        for appliance_id, appliance_state in sorted(self.appliance_states.items()):
            if appliance_id in {"light", "living_room_light"}:
                continue
            devices.append(
                {
                    "id": appliance_id,
                    "kind": appliance_id,
                    "name": _device_name(appliance_id),
                    "state": appliance_state,
                }
            )
        environment = self._mock_environment(turn, reason, state)
        for appliance_id, appliance_state in sorted(self.appliance_states.items()):
            if appliance_id in {"light", "living_room_light"}:
                continue
            environment.setdefault("appliances", {})[appliance_id] = {
                "state": appliance_state,
                "updated_at": "2026-05-08T00:00:00+00:00",
                "source": "home_assistant.mock",
            }
        return {
            "status": "ok",
            "observation_ref": (
                f"obs_{self.execute_attempts_by_turn.get(turn.turn_id, 0):04d}_{reason}"
            ),
            "observation_source": "environment-state-server.mock",
            "facts": {
                "location": "living_room",
                "devices": devices,
                "state_queries": environment.get("state_queries", {}),
            },
            "environment": environment,
        }

    def home_preview(self, turn: TurnInput, observation: dict[str, Any]) -> dict[str, Any]:
        intent = detect_home_action_intent(turn.text, observation) or HomeLightIntent(
            action_id="light_on",
            expected_state="on",
            action_name="home.light.turn_on",
        )
        action = _action_from_intent(intent)
        if action.get("noop") or action.get("available") is False:
            return {
                "status": "noop",
                "action": action,
                "message": _noop_message(action),
                "should_execute": False,
            }
        return {
            "status": "ok",
            "action": action,
        }

    def home_execute(self, turn: TurnInput, action: dict[str, Any]) -> dict[str, Any]:
        self.execute_calls.append(action)
        attempt = self.execute_attempts_by_turn.get(turn.turn_id, 0) + 1
        self.execute_attempts_by_turn[turn.turn_id] = attempt
        mock_metadata = {
            "adapter": "mock",
            "execution_mode": "mock",
            "real_execution": False,
            "executed": False,
            "verified_by_bridge": False,
        }
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
                **mock_metadata,
            }
        else:
            self.light_on = action.get("expected_state") == "on"
            self.light_state_by_turn[turn.turn_id] = self.light_on
            target = str(action.get("target") or "").strip()
            expected_state = str(action.get("expected_state") or "").strip()
            if target and target not in {"light", "living_room_light"} and expected_state:
                self.appliance_states[target] = expected_state
            result = {
                "status": "accepted",
                "retryable": False,
                "command_id": f"cmd_{attempt:04d}",
                "attempt": attempt,
                "message": _mock_action_message(action),
                "speak": _mock_action_message(action),
                **mock_metadata,
            }
        if self.include_secret_in_execute_result:
            result["access_token"] = "mock-token-that-must-not-leak"
            result["authorization"] = "Bearer mock-token-that-must-not-leak"
        return result

    def memory_retrieve(self, turn: TurnInput) -> dict[str, Any]:
        items = turn.context_refs.get("mock_memory_items", [])
        if not isinstance(items, list):
            items = []
        summary = turn.context_refs.get("mock_memory_summary")
        return {
            "status": "ok",
            "items": list(items),
            "summary": summary if isinstance(summary, str) else "",
            "source": "mock_memory",
        }

    def state_query_feedback(
        self,
        turn: TurnInput,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.state_query_feedback_calls.append(dict(payload))
        return {
            "status": "accepted",
            "ok": True,
            "feedback_id": f"sqf_mock_{len(self.state_query_feedback_calls):04d}",
            "duplicate": False,
            "target": payload.get("target") or payload.get("state_query_id"),
            "user_label": payload.get("user_label"),
        }

    def short_memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        ensure_execution_active()
        self.short_memory_write_calls.append(dict(item))
        return {"status": "ok", "written": True}

    def memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        ensure_execution_active()
        self.memory_write_calls.append(dict(item))
        return {"status": "ok", "written": True, "candidate": True}

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

    def _mock_environment(
        self,
        turn: TurnInput,
        reason: str,
        light_state: str,
    ) -> dict[str, Any]:
        room_light_state = _first_context_text(
            turn,
            f"mock_{reason}_room_light_state",
            "mock_room_light_state",
            default=light_state,
        ).lower()
        if room_light_state not in {"on", "off", "unknown"}:
            room_light_state = light_state
        confidence_label = _first_context_text(
            turn,
            f"mock_{reason}_room_light_confidence_label",
            "mock_room_light_confidence_label",
            default="high",
        ).lower()
        available = _first_context_bool(
            turn,
            f"mock_{reason}_room_light_available",
            "mock_room_light_available",
            default=True,
        )
        stale = _first_context_bool(
            turn,
            f"mock_{reason}_room_light_stale",
            "mock_room_light_stale",
            default=False,
        )
        environment: dict[str, Any] = {
            "snapshot_id": f"env_mock_{turn.turn_id}_{reason}",
            "appliances": {},
            "last_home_assistant_events": [],
            "state_queries": {
                "room_light": {
                    "available": available,
                    "stale": stale,
                    "state": room_light_state,
                    "confidence_label": confidence_label,
                    "authority": "vision_snapshot_processor.mock",
                    "projected_by": "environment_state_server.mock",
                    "answer_hint": _room_light_answer_hint(
                        room_light_state,
                        confidence_label,
                    ),
                    "observed_at": "2026-05-08T00:00:00+00:00",
                    "updated_at": "2026-05-08T00:00:00+00:00",
                    "evidence": {
                        "source": "mock",
                        "topic": "room_light",
                        "lighting_type": room_light_state,
                    },
                }
            },
        }
        wait_matched = _first_context_bool(
            turn,
            f"mock_{reason}_wait_matched",
            "mock_wait_matched",
            default=None,
        )
        if wait_matched is not None:
            environment["wait_result"] = {
                "wait_for": "room_light",
                "matched": wait_matched,
                "after": _first_context_text(
                    turn,
                    f"mock_{reason}_wait_after",
                    "mock_wait_after",
                    default="2026-05-08T00:00:00+00:00",
                ),
                "timeout_ms": 1500,
            }
        return environment


@dataclass(frozen=True)
class HomeControlToolConfig:
    bridge_base_url: str
    api_token: str
    environment_state_url: str = ""
    environment_api_token: str = ""
    environment_feedback_url: str = ""
    memory_root: str = ""
    memory_policy_root: str = ""
    memory_retrieve_limit: int = 8
    timeout_s: float = 3.0
    room_light_wait_timeout_ms: int = 1500

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
        environment_feedback_url = _env_first("ENVIRONMENT_FEEDBACK_URL")
        if not environment_feedback_url and environment_state_url:
            environment_feedback_url = environment_state_url.replace(
                "/environment/current",
                "/feedback/state-query",
            )
        if not environment_feedback_url:
            environment_feedback_url = "http://127.0.0.1:8790/feedback/state-query"
        environment_api_token = _env_first("ENVIRONMENT_API_TOKEN") or api_token
        timeout_s = _optional_float(_env_first("THOUGHT_CORE_HOME_HTTP_TIMEOUT_S"), 3.0)
        room_light_wait_timeout_ms = _optional_int(
            _env_first("THOUGHT_CORE_ROOM_LIGHT_WAIT_TIMEOUT_MS"),
            1500,
        )
        repo_root = _repo_root_from_here()
        memory_root = _env_first(
            "THOUGHT_CORE_MEMORY_ROOT",
            "SWORD_MEMORY_ROOT",
            default=str(repo_root / "local" / "memory"),
        )
        memory_policy_root = _env_first(
            "THOUGHT_CORE_MEMORY_POLICY_ROOT",
            default=str(repo_root / "policies" / "access"),
        )
        memory_retrieve_limit = _optional_int(
            _env_first("THOUGHT_CORE_MEMORY_RETRIEVE_LIMIT"),
            8,
        )
        return cls(
            bridge_base_url=bridge_base_url,
            api_token=api_token,
            environment_state_url=environment_state_url,
            environment_api_token=environment_api_token,
            environment_feedback_url=environment_feedback_url,
            memory_root=memory_root,
            memory_policy_root=memory_policy_root,
            memory_retrieve_limit=memory_retrieve_limit,
            timeout_s=timeout_s,
            room_light_wait_timeout_ms=room_light_wait_timeout_ms,
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
    last_execute_issued_at_by_turn: dict[str, str] = field(default_factory=dict)
    room_light_wait_after_by_turn: dict[str, str] = field(default_factory=dict)
    room_light_wait_timeout_ms_by_turn: dict[str, int] = field(default_factory=dict)

    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, Any]:
        if not self.config.environment_state_url or not self.config.environment_api_token:
            return {
                "status": "skipped",
                "observation_ref": f"env_unconfigured_{reason}",
                "observation_source": "environment-state-server.unconfigured",
                "facts": {"devices": []},
            }
        url = self.config.environment_state_url
        if reason == "after_action":
            wait_after = (
                self.room_light_wait_after_by_turn.pop(turn.turn_id, "")
                or self.last_execute_issued_at_by_turn.get(turn.turn_id, "")
            )
            wait_timeout_ms = self.room_light_wait_timeout_ms_by_turn.pop(
                turn.turn_id,
                self.config.room_light_wait_timeout_ms,
            )
            if wait_after:
                url = _url_with_query(
                    url,
                    {
                        "wait_for": "room_light",
                        "after": wait_after,
                        "timeout_ms": str(wait_timeout_ms),
                    },
                )
        try:
            payload = self._json_request(
                "GET",
                url,
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
        intent = detect_home_action_intent(turn.text, observation)
        if intent is None:
            return {
                "status": "unsupported",
                "error": "unsupported_home_intent",
                "action": {},
            }
        action = _action_from_intent(intent)
        if action.get("noop") or action.get("available") is False:
            return {
                "status": "noop",
                "action": action,
                "message": _noop_message(action),
                "should_execute": False,
            }
        if not self.config.api_token:
            return {
                "status": "failed",
                "error": "home_control_api_token_missing",
                "action": action,
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
                "action": action,
            }

        action.update(
            {
                "confirm_required": bool(payload.get("confirmation_required")),
                "confirmation_token": payload.get("confirmation_token"),
                "bridge_status": payload.get("status"),
                "control_type": payload.get("control_type"),
                "state_authority": payload.get("state_authority"),
                "verification_mode": payload.get("verification_mode"),
                "state_tracking": payload.get("state_tracking"),
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

        deadline_remaining = remaining_execution_seconds()
        if deadline_remaining is None:
            return {
                "status": "failed",
                "retryable": False,
                "error": "turn_execution_deadline_missing",
            }

        attempt = self.execute_attempts_by_turn.get(turn.turn_id, 0) + 1
        self.execute_attempts_by_turn[turn.turn_id] = attempt
        body = _bridge_body(
            turn,
            request_id=f"{turn.turn_id}-attempt-{attempt}",
            deadline_monotonic_s=monotonic() + deadline_remaining,
        )
        if bool(action.get("confirmed")):
            body["confirmed"] = True
        confirmation_token = str(action.get("confirmation_token") or "").strip()
        if confirmation_token:
            body["confirmation_token"] = confirmation_token
        try:
            payload = self._json_request(
                "POST",
                self._bridge_url(f"/actions/{action_id}/execute"),
                token=self.config.api_token,
                body=body,
            )
        except HomeControlToolError as exc:
            return {
                "status": "failed",
                "retryable": False,
                "error": exc.code,
                "detail": exc.safe_detail,
                "attempt": attempt,
                "execution_lifecycle_class": "bridge_request_outcome_unknown",
            }

        lifecycle = str(payload.get("execution_lifecycle_class") or "")
        submission_count = payload.get("submission_count")
        issued_at_value = payload.get("issued_at") or payload.get("started_at")
        issued_at = str(issued_at_value or datetime.now(UTC).isoformat())
        if lifecycle == "submission_completed" and submission_count == 1:
            self.last_execute_issued_at_by_turn[turn.turn_id] = issued_at
        bridge_status = str(payload.get("status") or "unknown")
        executed = bool(payload.get("executed"))
        ok = bool(payload.get("ok", executed))
        non_retryable_lifecycles = {
            "submission_in_flight",
            "submission_completed",
            "failed_before_submit",
            "submission_outcome_unknown",
            "expired_before_submit",
        }
        return {
            "status": "accepted" if ok and executed else bridge_status,
            "retryable": not ok
            and bridge_status not in {"confirmation_required", "dry_run", "duplicate"}
            and lifecycle not in non_retryable_lifecycles,
            "executed": executed,
            "verified_by_bridge": ok and executed,
            "command_id": payload.get("execution_id"),
            "attempt": attempt,
            "issued_at": issued_at,
            "bridge_status": bridge_status,
            "message": payload.get("message"),
            "speak": payload.get("speak"),
            "confirmation_token": payload.get("confirmation_token"),
            "expected_state": payload.get("expected_state"),
            "control_type": payload.get("control_type"),
            "state_authority": payload.get("state_authority"),
            "verification_mode": payload.get("verification_mode"),
            "state_tracking": payload.get("state_tracking"),
            "expected_effect": payload.get("expected_effect"),
            "execution_lifecycle_class": lifecycle or None,
            "submission_count": submission_count,
            "terminal": payload.get("terminal"),
        }

    def state_query_feedback(
        self,
        turn: TurnInput,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.config.environment_feedback_url or not self.config.environment_api_token:
            return {
                "status": "skipped",
                "ok": False,
                "error": "environment_feedback_unconfigured",
            }
        try:
            response_payload = self._json_request(
                "POST",
                self.config.environment_feedback_url,
                token=self.config.environment_api_token,
                body=payload,
            )
        except HomeControlToolError as exc:
            return {
                "status": "failed",
                "ok": False,
                "error": exc.code,
                "detail": exc.safe_detail,
            }
        status = str(response_payload.get("status") or "accepted")
        return {
            "status": status,
            "ok": status in {"accepted", "accepted_with_warning", "duplicate"},
            "feedback_id": response_payload.get("feedback_id"),
            "duplicate": status == "duplicate",
            "warnings": response_payload.get("warnings", []),
            "target": response_payload.get("target") or payload.get("target"),
            "user_label": response_payload.get("user_label") or payload.get("user_label"),
            "response": response_payload,
        }

    def memory_retrieve(self, turn: TurnInput) -> dict[str, Any]:
        limit = max(1, int(self.config.memory_retrieve_limit or 8))
        items: list[dict[str, Any]] = []
        items.extend(self._recent_short_memory(turn, limit=limit))
        remaining = max(0, limit - len(items))
        if remaining:
            items.extend(self._committed_memory(turn, limit=remaining))
        return {
            "status": "ok",
            "items": items[:limit],
            "source": "local_memory",
            "summary": _memory_result_summary(items[:limit]),
        }

    def short_memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        ensure_execution_active()
        if not self.config.memory_root:
            return {
                "status": "skipped",
                "written": False,
                "scope": "short_memory",
                "reason": "memory_root_unconfigured",
            }
        payload = dict(item)
        payload.setdefault("type", "short_memory")
        payload.setdefault("scope", "session")
        payload.setdefault("session_id", turn.session_id)
        payload.setdefault("turn_id", turn.turn_id)
        payload.setdefault("created_at", datetime.now(UTC).isoformat())
        path = Path(self.config.memory_root) / "short_memory.jsonl"
        try:
            ensure_execution_active()
            _append_jsonl(path, payload)
            ensure_execution_active()
        except OSError as exc:
            return {
                "status": "failed",
                "written": False,
                "scope": "short_memory",
                "error": "short_memory_write_failed",
                "detail": str(exc)[:240],
            }
        return {
            "status": "ok",
            "written": True,
            "scope": "short_memory",
            "path": str(path),
        }

    def memory_write(self, turn: TurnInput, item: dict[str, Any]) -> dict[str, Any]:
        ensure_execution_active()
        if item.get("type") == "short_memory" or item.get("kind") == "retry_budget":
            return self.short_memory_write(turn, item)
        candidate = dict(item)
        candidate.setdefault("schema_version", "memory.item.v0")
        candidate.setdefault("scope", "failure_patterns")
        candidate.setdefault("status", "candidate")
        candidate.setdefault("created_at", datetime.now(UTC).isoformat())
        source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
        source = dict(source)
        source.setdefault("service", "thought-core")
        source.setdefault(
            "trace_id",
            str(turn.context_refs.get("trace_id") or f"trace_{turn.turn_id}"),
        )
        source.setdefault("turn_id", turn.turn_id)
        candidate["source"] = source
        try:
            store = _memory_store(self.config.memory_root, self.config.memory_policy_root)
        except ImportError as exc:  # pragma: no cover - standalone thought-core fallback
            path = Path(self.config.memory_root) / "candidates.jsonl"
            try:
                ensure_execution_active()
                _append_jsonl(path, candidate)
                ensure_execution_active()
            except OSError:
                return {
                    "status": "failed",
                    "written": False,
                    "error": "memory_candidate_write_failed",
                    "detail": str(exc)[:240],
                }
            return {
                "status": "accepted",
                "written": True,
                "candidate": True,
                "fallback": "jsonl",
                "path": str(path),
                "detail": str(exc)[:240],
            }
        try:
            ensure_execution_active()
            result = store.write_candidate(requester="thought_core_api", item=candidate)
            ensure_execution_active()
        except TurnDeadlineExceeded:
            raise
        except Exception as exc:  # pragma: no cover - defensive adapter boundary
            return {
                "status": "failed",
                "written": False,
                "candidate": True,
                "error": "memory_candidate_write_failed",
                "detail": str(exc)[:240],
            }
        return {
            **result,
            "written": bool(result.get("ok")),
            "candidate": True,
        }

    def _recent_short_memory(self, turn: TurnInput, *, limit: int) -> list[dict[str, Any]]:
        if not self.config.memory_root:
            return []
        path = Path(self.config.memory_root) / "short_memory.jsonl"
        items = []
        scan_lines = max(limit * 64, 256)
        for item in reversed(_read_recent_jsonl(path, max_lines=scan_lines)):
            if item.get("session_id") not in {turn.session_id, None, ""}:
                continue
            items.append(
                {
                    "schema_version": "memory.item.v0",
                    "memory_type": "short_memory",
                    "scope": "session",
                    "status": "active",
                    "content": item,
                    "source": {
                        "service": "thought-core",
                        "turn_id": item.get("turn_id"),
                    },
                    "created_at": item.get("created_at"),
                }
            )
            if len(items) >= limit:
                break
        return items

    def _committed_memory(self, turn: TurnInput, *, limit: int) -> list[dict[str, Any]]:
        if not self.config.memory_root:
            return []
        scopes = turn.context_refs.get("memory_scopes")
        if not isinstance(scopes, list):
            scopes = ["failure_patterns", "user_preferences", "device_aliases"]
        safe_scopes = [str(scope) for scope in scopes if str(scope or "").strip()]
        try:
            store = _memory_store(self.config.memory_root, self.config.memory_policy_root)
            return store.retrieve(
                requester="thought_core_api",
                scopes=safe_scopes,
                limit=limit,
            )
        except Exception:
            return []


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
        data = (
            None
            if body is None
            else json.dumps(
                _json_transport_safe(body),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            ensure_execution_active()
            with request.urlopen(
                req,
                timeout=clamp_execution_timeout(self.config.timeout_s),
            ) as response:
                raw = response.read().decode("utf-8")
            ensure_execution_active()
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
    bridge_url_configured = bool(
        _env_first("HOME_CONTROL_BRIDGE_URL", "HOME_ASSISTANT_BRIDGE_URL")
    )
    if adapter in {"home_control", "home-control", "bridge"} or bridge_url_configured:
        return HomeControlHttpTools(HomeControlToolConfig.from_env())
    return MockThoughtTools()


def detect_home_light_intent(text: str) -> HomeLightIntent | None:
    intent = detect_home_action_intent(text)
    if intent is None or intent.action_id not in {"light_on", "light_off"}:
        return None
    return intent


def detect_home_action_intent(
    text: str,
    observation: dict[str, Any] | None = None,
) -> HomeLightIntent | None:
    normalized = text.replace(" ", "").replace("　", "")
    lowered = normalized.lower()
    if _is_negative_home_action_request(normalized, lowered):
        return None

    registry_intent = _intent_from_environment_actions(text, observation or {})
    if registry_intent is not None:
        return registry_intent

    builtin_candidates = _builtin_home_action_candidates(normalized, lowered)
    unique_candidates = {
        candidate.action_id: candidate for candidate in builtin_candidates
    }
    if len(unique_candidates) == 1:
        return next(iter(unique_candidates.values()))
    if len(unique_candidates) > 1:
        return None
    return None


def detect_home_action_ambiguity(text: str) -> dict[str, Any] | None:
    normalized = text.replace(" ", "").replace("　", "")
    lowered = normalized.lower()
    if not normalized or _is_negative_home_action_request(normalized, lowered):
        return None
    target_groups = _mentioned_home_action_target_groups(normalized, lowered)
    if len(target_groups) < 2:
        return None
    connectors = (
        "と",
        "もしくは",
        "または",
        "あるいは",
        "又は",
        "或いは",
        "や",
        "or",
        "/",
        "、",
    )
    has_connector = any(marker in normalized or marker in lowered for marker in connectors)
    has_kana_choice = "か" in normalized and len(target_groups) >= 2
    if not has_connector and not has_kana_choice:
        return None
    return {
        "reason": "multiple_home_action_targets",
        "targets": sorted(target_groups),
        "text": text,
    }


def detect_home_action_negative_request(text: str) -> dict[str, Any] | None:
    normalized = text.replace(" ", "").replace("　", "")
    lowered = normalized.lower()
    if not normalized or not _is_negative_home_action_request(normalized, lowered):
        return None
    target_groups = _mentioned_home_action_target_groups(normalized, lowered)
    if not target_groups:
        return None
    return {
        "reason": "negative_home_action_request",
        "targets": sorted(target_groups),
        "text": text,
    }


def _mentioned_home_action_target_groups(normalized: str, lowered: str) -> set[str]:
    groups: set[str] = set()
    target_words = {
        "light": ("電気", "ライト", "照明"),
        "fan": ("扇風機", "ファン"),
        "aircon": ("エアコン", "冷房", "暖房", "空調"),
        "door": ("中扉", "扉", "ドア", "カーテン"),
        "vacuum": ("掃除機", "ロボット掃除機", "ルンバ"),
    }
    for group, words in target_words.items():
        if any(word in normalized or word.lower() in lowered for word in words):
            groups.add(group)
    return groups


def _is_negative_home_action_request(normalized: str, lowered: str) -> bool:
    negative_markers = (
        "ないで",
        "なくていい",
        "しないで",
        "しなくていい",
        "キャンセル",
        "取り消",
        "中止",
    )
    if any(marker in normalized for marker in negative_markers):
        return True
    return "do not" in lowered or "don't" in lowered or "cancel" in lowered


def _builtin_home_action_candidates(
    normalized: str,
    lowered: str,
) -> list[HomeLightIntent]:
    candidates: list[HomeLightIntent] = []
    mentions_light = any(word in normalized for word in ("電気", "ライト", "照明"))
    if mentions_light:
        if (
            any(
                word in normalized
                for word in ("消し", "消す", "消して", "消灯", "オフ", "切っ", "切る")
            )
            or "off" in lowered
        ):
            candidates.append(
                HomeLightIntent(
                    action_id="light_off",
                    expected_state="off",
                    action_name="home.light.turn_off",
                )
            )
        if (
            any(
                word in normalized
                for word in ("つけ", "点け", "付け", "点灯", "オン", "入れ")
            )
            or "on" in lowered
        ):
            candidates.append(
                HomeLightIntent(
                    action_id="light_on",
                    expected_state="on",
                    action_name="home.light.turn_on",
                )
            )

    fixed_actions = (
        (
            ("扇風機", "ファン"),
            ("つけ", "点け", "付け", "オン", "入れ"),
            HomeLightIntent(
                action_id="fan_on",
                expected_state="on",
                action_name="home.fan.turn_on",
                target="fan",
                target_name="扇風機",
                pre_action_phrase="扇風機をつける",
            ),
        ),
        (
            ("扇風機", "ファン"),
            ("消し", "消す", "止め", "オフ", "切っ", "切る"),
            HomeLightIntent(
                action_id="fan_off",
                expected_state="off",
                action_name="home.fan.turn_off",
                target="fan",
                target_name="扇風機",
                pre_action_phrase="扇風機を消す",
            ),
        ),
        (
            ("エアコン", "冷房", "暖房", "空調"),
            ("冷房に", "冷房"),
            HomeLightIntent(
                action_id="aircon_cool",
                expected_state="cool",
                action_name="home.aircon.cool",
                target="aircon",
                target_name="エアコン",
                pre_action_phrase="エアコンを冷房にする",
            ),
        ),
        (
            ("エアコン", "空調"),
            ("停止",),
            HomeLightIntent(
                action_id="aircon_hvac_off",
                expected_state="off",
                action_name="home.aircon.hvac_off",
                target="aircon",
                target_name="エアコン",
                pre_action_phrase="エアコンを停止する",
            ),
        ),
        (
            ("エアコン", "冷房", "空調"),
            ("つけ", "点け", "付け", "オン", "入れ"),
            HomeLightIntent(
                action_id="aircon_cool",
                expected_state="cool",
                action_name="home.aircon.cool",
                target="aircon",
                target_name="エアコン",
                pre_action_phrase="エアコンを冷房にする",
            ),
        ),
        (
            ("エアコン", "冷房", "暖房", "空調"),
            ("消し", "消す", "止め", "オフ", "切っ", "切る"),
            HomeLightIntent(
                action_id="aircon_hvac_off",
                expected_state="off",
                action_name="home.aircon.hvac_off",
                target="aircon",
                target_name="エアコン",
                pre_action_phrase="エアコンを停止する",
            ),
        ),
        (
            ("プロジェクション", "投影"),
            ("モード", "切り替", "して"),
            HomeLightIntent(
                action_id="projection_mode",
                expected_state="projection_mode",
                action_name="home.projection.mode",
                target="projection",
                target_name="プロジェクション",
                pre_action_phrase="プロジェクションモードにする",
            ),
        ),
        (
            ("中扉", "扉", "ドア", "カーテン"),
            ("開け", "開い"),
            HomeLightIntent(
                action_id="door_open",
                expected_state="open",
                action_name="home.door.open",
                target="door",
                target_name="中扉",
                pre_action_phrase="中扉を開ける",
            ),
        ),
        (
            ("中扉", "扉", "ドア", "カーテン"),
            ("閉め", "閉じ"),
            HomeLightIntent(
                action_id="door_close",
                expected_state="closed",
                action_name="home.door.close",
                target="door",
                target_name="中扉",
                pre_action_phrase="中扉を閉める",
            ),
        ),
        (
            ("中扉", "扉", "ドア", "カーテン"),
            ("止め", "停止"),
            HomeLightIntent(
                action_id="door_stop",
                expected_state="stopped",
                action_name="home.door.stop",
                target="door",
                target_name="中扉",
                pre_action_phrase="中扉を止める",
            ),
        ),
        (
            ("掃除機", "ロボット掃除機", "ルンバ"),
            ("かけ", "動か", "動かし", "始め", "スタート"),
            HomeLightIntent(
                action_id="vacuum_start",
                expected_state="cleaning",
                action_name="home.vacuum.start",
                target="vacuum",
                target_name="掃除機",
                pre_action_phrase="掃除機を動かす",
            ),
        ),
        (
            ("掃除機", "ロボット掃除機", "ルンバ"),
            ("戻し", "戻して", "帰", "充電"),
            HomeLightIntent(
                action_id="vacuum_return",
                expected_state="returning",
                action_name="home.vacuum.return",
                target="vacuum",
                target_name="掃除機",
                pre_action_phrase="掃除機を戻す",
            ),
        ),
        (
            ("掃除機", "ロボット掃除機", "ルンバ"),
            ("止め", "停止", "一時停止"),
            HomeLightIntent(
                action_id="vacuum_pause",
                expected_state="paused",
                action_name="home.vacuum.pause",
                target="vacuum",
                target_name="掃除機",
                pre_action_phrase="掃除機を一時停止する",
            ),
        ),
    )
    for target_words, verb_words, intent in fixed_actions:
        if any(word in normalized for word in target_words) and any(
            word in normalized for word in verb_words
        ):
            candidates.append(intent)
    return candidates


def detect_room_light_state_query(text: str) -> bool:
    normalized = text.replace(" ", "").replace("　", "")
    if not normalized:
        return False
    command_markers = (
        "つけて",
        "付けて",
        "点けて",
        "消して",
        "消しといて",
        "オンにして",
        "オフにして",
        "入れて",
        "切って",
    )
    if any(marker in normalized for marker in command_markers):
        return False
    target_markers = ("電気", "照明", "ライト", "明かり", "あかり", "部屋")
    state_markers = (
        "ついてる",
        "ついている",
        "ついてます",
        "点いてる",
        "点いている",
        "点いてます",
        "付いてる",
        "付いている",
        "付いてます",
        "ついてない",
        "点いてない",
        "付いてない",
        "点灯",
        "消えてる",
        "消えている",
        "消えてます",
        "消えています",
        "消えてない",
        "消灯",
        "オン",
        "オフ",
        "明るい",
        "明るさ",
        "暗い",
        "暗さ",
        "状態",
    )
    return any(marker in normalized for marker in target_markers) and any(
        marker in normalized for marker in state_markers
    )


def _intent_from_environment_actions(
    text: str,
    observation: dict[str, Any],
) -> HomeLightIntent | None:
    normalized = text.replace(" ", "").replace("　", "")
    if not normalized:
        return None
    environment = observation.get("environment")
    if not isinstance(environment, dict):
        return None
    actions = environment.get("actions")
    if not isinstance(actions, list):
        return None
    matches: dict[str, HomeLightIntent] = {}
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("action_id") or "").strip()
        if not action_id or not _environment_action_matches(normalized, action):
            continue
        expected_effect = (
            action.get("expected_effect")
            if isinstance(action.get("expected_effect"), dict)
            else {}
        )
        expected_state = str(
            action.get("expected_state") or expected_effect.get("expected_state") or ""
        ).strip()
        target = str(
            action.get("appliance_id") or action.get("target") or action_id.split("_")[0]
        ).strip()
        target_name = str(
            action.get("target_label") or action.get("label") or target or action_id
        ).strip()
        matches[action_id] = HomeLightIntent(
            action_id=action_id,
            expected_state=expected_state,
            action_name=f"home.{action_id}",
            target=target,
            target_name=target_name,
            pre_action_phrase=str(
                action.get("pre_action_phrase") or action.get("label") or ""
            ),
            available=action.get("available") is not False,
            noop=bool(action.get("noop")),
            reason=str(action.get("reason") or ""),
            reason_text=str(action.get("reason_text") or ""),
            control_type=str(action.get("control_type") or ""),
            state_authority=str(action.get("state_authority") or ""),
            verification_mode=str(action.get("verification_mode") or ""),
            state_tracking=str(action.get("state_tracking") or ""),
            proof_ceiling=str(action.get("proof_ceiling") or ""),
            live_test_readiness=str(action.get("live_test_readiness") or ""),
            live_test_blockers=_tuple_of_text(action.get("live_test_blockers")),
            restore_action_id=str(action.get("restore_action_id") or ""),
            stop_action_id=str(action.get("stop_action_id") or ""),
            terminal_action=bool(action.get("terminal_action")),
            safety_requirements=_tuple_of_text(action.get("safety_requirements")),
        )
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def _environment_action_matches(normalized_text: str, action: dict[str, Any]) -> bool:
    aliases = action.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            normalized_alias = str(alias or "").replace(" ", "").replace("　", "")
            if normalized_alias and normalized_alias in normalized_text:
                return True
    target_label = str(action.get("target_label") or action.get("label") or "").strip()
    verb = str(action.get("verb") or "").strip()
    if target_label and verb:
        target = target_label.replace(" ", "").replace("　", "")
        normalized_verb = verb.replace(" ", "").replace("　", "")
        if target in normalized_text and normalized_verb[:2] in normalized_text:
            return True
    return False


def _action_from_intent(intent: HomeLightIntent) -> dict[str, Any]:
    target_aliases = [intent.target, intent.action_id]
    if intent.target == "light":
        target_aliases.append("living_room_light")
    action = {
        "action": intent.action_name,
        "action_id": intent.action_id,
        "target": intent.target,
        "target_aliases": target_aliases,
        "target_name": intent.target_name,
        "confidence": 0.93,
        "expected_state": intent.expected_state,
        "pre_action_phrase": intent.pre_action_phrase,
        "available": intent.available,
        "noop": intent.noop,
        "reason": intent.reason,
        "reason_text": intent.reason_text,
    }
    optional_fields: dict[str, Any] = {
        "control_type": intent.control_type,
        "state_authority": intent.state_authority,
        "verification_mode": intent.verification_mode,
        "state_tracking": intent.state_tracking,
        "proof_ceiling": intent.proof_ceiling,
        "live_test_readiness": intent.live_test_readiness,
        "restore_action_id": intent.restore_action_id,
        "stop_action_id": intent.stop_action_id,
    }
    for key, value in optional_fields.items():
        if value:
            action[key] = value
    if intent.live_test_blockers:
        action["live_test_blockers"] = list(intent.live_test_blockers)
    if intent.safety_requirements:
        action["safety_requirements"] = list(intent.safety_requirements)
    if intent.terminal_action:
        action["terminal_action"] = True
    return action


def _tuple_of_text(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _noop_message(action: dict[str, Any]) -> str:
    reason_text = str(action.get("reason_text") or "").strip()
    if reason_text:
        return reason_text if reason_text.endswith(("。", "！", "？")) else f"{reason_text}。"
    target = str(action.get("target_name") or "その操作").strip()
    return f"{target}は今の状態では操作しなくて大丈夫です。"


def _facts_from_environment_current(payload: dict[str, Any]) -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    appliances = (
        payload.get("appliances")
        if isinstance(payload.get("appliances"), dict)
        else {}
    )
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
        "actions": payload.get("actions", []),
        "state_queries": payload.get("state_queries", {}),
    }


def _device_name(device_id: str) -> str:
    return {
        "light": "リビングの電気",
        "living_room_light": "リビングの電気",
        "fan": "扇風機",
        "aircon": "エアコン",
        "door": "中扉",
        "vacuum": "掃除機",
    }.get(device_id, device_id)


def _mock_action_message(action: dict[str, Any]) -> str:
    target_name = str(
        action.get("target_name")
        or _device_name(str(action.get("target") or ""))
        or "対象"
    ).strip()
    expected_state = str(action.get("expected_state") or "").strip()
    if expected_state == "on":
        action_text = f"{target_name}をつけた想定です"
    elif expected_state == "off":
        action_text = f"{target_name}を消した想定です"
    elif expected_state == "open":
        action_text = f"{target_name}を開けた想定です"
    elif expected_state == "closed":
        action_text = f"{target_name}を閉めた想定です"
    elif expected_state == "stopped":
        action_text = f"{target_name}を止めた想定です"
    elif expected_state == "cleaning":
        action_text = f"{target_name}の掃除を始めた想定です"
    elif expected_state == "returning":
        action_text = f"{target_name}を戻した想定です"
    elif expected_state == "paused":
        action_text = f"{target_name}を一時停止した想定です"
    elif expected_state:
        action_text = f"{target_name}を{expected_state}にした想定です"
    else:
        action_text = f"{target_name}の操作をした想定です"
    return f"テストモード上では、{action_text}。実家電には送っていません。"


def _bridge_body(
    turn: TurnInput,
    *,
    request_id: str,
    deadline_monotonic_s: float | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source": "thought-core",
        "request_id": request_id,
        "user_text": turn.text,
    }
    if deadline_monotonic_s is not None:
        body["deadline_monotonic_s"] = deadline_monotonic_s
    return body


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


def _optional_int(value: str, default: int) -> int:
    try:
        return max(1, int(str(value).strip()))
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


def _first_context_text(
    turn: TurnInput,
    *keys: str,
    default: str = "",
) -> str:
    for key in keys:
        value = turn.context_refs.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _first_context_bool(
    turn: TurnInput,
    *keys: str,
    default: bool | None,
) -> bool | None:
    for key in keys:
        value = _optional_context_text(turn, key)
        if value in {"1", "true", "yes", "on", "matched"}:
            return True
        if value in {"0", "false", "no", "off", "unmatched"}:
            return False
    return default


def _room_light_answer_hint(state: str, confidence_label: str) -> str:
    if confidence_label in {"low", "very_low", "unknown"}:
        return "判定の自信が低い"
    if state == "on":
        return "部屋は明るい"
    if state == "off":
        return "部屋は暗い"
    return "判定できない"


def _url_with_query(url: str, values: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def _repo_root_from_here() -> Path:
    # tools.py -> thought_core -> src -> thought-core -> services -> repo root
    return Path(__file__).resolve().parents[4]


def _memory_store(memory_root: str, policy_root: str):
    from sword_voice_agent.system.access_control import PolicyStore
    from sword_voice_agent.system.memory_store import MemoryStore

    return MemoryStore(
        Path(memory_root),
        policy=PolicyStore(Path(policy_root)),
    )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        stream.write("\n")


def _json_transport_safe(value: Any, *, max_depth: int = 12) -> Any:
    if max_depth <= 0:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_transport_safe(item, max_depth=max_depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_transport_safe(item, max_depth=max_depth - 1) for item in value]
    return str(value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return _parse_jsonl_lines(lines)


def _read_recent_jsonl(
    path: Path,
    *,
    max_lines: int,
    max_bytes: int = 1_048_576,
) -> list[dict[str, Any]]:
    if max_lines <= 0 or max_bytes <= 0:
        return []
    try:
        file_size = path.stat().st_size
    except FileNotFoundError:
        return []
    if file_size <= max_bytes:
        return _read_jsonl(path)[-max_lines:]

    chunks: list[bytes] = []
    remaining = min(file_size, max_bytes)
    position = file_size
    newline_count = 0
    try:
        with path.open("rb") as stream:
            while remaining > 0 and newline_count <= max_lines:
                chunk_size = min(8192, remaining)
                position -= chunk_size
                stream.seek(position)
                chunk = stream.read(chunk_size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
                remaining -= chunk_size
    except OSError:
        return []

    data = b"".join(reversed(chunks))
    if position > 0:
        first_newline = data.find(b"\n")
        if first_newline < 0:
            return []
        data = data[first_newline + 1 :]
    lines = data.decode("utf-8", errors="replace").splitlines()
    return _parse_jsonl_lines(lines[-max_lines:])


def _parse_jsonl_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _memory_result_summary(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No relevant memory items were retrieved."
    scopes = sorted({str(item.get("scope") or "unknown") for item in items})
    types = sorted(
        {str(item.get("memory_type") or item.get("type") or "unknown") for item in items}
    )
    return (
        f"Retrieved {len(items)} memory item(s). "
        f"scopes={', '.join(scopes)}; types={', '.join(types)}."
    )
