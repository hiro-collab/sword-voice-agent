"""Action reasoning boundary for target-state based home control.

The action loop should reason over a projection of Environment State, not over
the whole snapshot. A target state only constrains required values; all
unspecified values are intentionally "any".
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol
from urllib import error, request
from urllib.parse import urlparse

from .execution_deadline import clamp_execution_timeout, ensure_execution_active
from .schema import TurnInput
from .tools import detect_home_action_intent


ACTION_REASONER_BOUNDARY = "thought-core.action_reasoner.v0"


class ActionReasoner(Protocol):
    adapter_kind: str
    provider: str
    model: str

    def imagine_target_state(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def plan_command(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
        target_state: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def review_target_state(
        self,
        turn: TurnInput,
        action: dict[str, Any],
        target_state: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class LocalActionReasoner:
    adapter_kind = "local_target_projection"
    provider = "thought-core"
    model = "local-action-reasoner-v0"

    def imagine_target_state(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        intent = detect_home_action_intent(turn.text, observation)
        if intent is None:
            return {
                "schema": ACTION_REASONER_BOUNDARY,
                "status": "unsupported",
                "reason": "no_home_action_intent",
                "bindings": [],
                "wildcard_policy": "unspecified_values_are_any",
            }
        binding = {
            "kind": "appliance_state",
            "target": intent.target,
            "target_name": intent.target_name,
            "target_aliases": _target_aliases(intent.target, intent.action_id),
            "field": "state",
            "operator": "eq",
            "value": intent.expected_state,
            "scope": "required",
            "path_hint": _target_state_path_hint(intent.target),
        }
        return {
            "schema": ACTION_REASONER_BOUNDARY,
            "status": "ok",
            "source": "prompt+environment",
            "target_state_id": f"target_{turn.turn_id}_{intent.action_id}",
            "action_id_hint": intent.action_id,
            "bindings": [binding],
            "wildcard_policy": "unspecified_values_are_any",
            "ignored_values": "all_environment_values_without_required_bindings",
            "rationale": (
                "The user requested a home action; only the affected device state "
                "is constrained. Other environment values are arbitrary."
            ),
        }

    def plan_command(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
        target_state: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        action = preview.get("action") if isinstance(preview.get("action"), dict) else {}
        diff = evaluate_target_state(target_state, observation)
        status = "ready"
        if preview.get("status") == "noop" or diff.get("status") == "matched":
            status = "already_satisfied"
        elif preview.get("status") not in {"ok", "preview"}:
            status = "unavailable"
        return {
            "schema": ACTION_REASONER_BOUNDARY,
            "status": status,
            "action_id": action.get("action_id"),
            "target": action.get("target"),
            "target_name": action.get("target_name"),
            "command": action.get("action"),
            "pre_action_phrase": action.get("pre_action_phrase"),
            "diff_before": diff,
            "wildcard_policy": target_state.get(
                "wildcard_policy",
                "unspecified_values_are_any",
            ),
        }

    def review_target_state(
        self,
        turn: TurnInput,
        action: dict[str, Any],
        target_state: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> dict[str, Any]:
        diff = evaluate_target_state(target_state, observation)
        status = str(execute_result.get("status") or "")
        action_id = str(action.get("action_id") or target_state.get("action_id_hint") or "")
        expected_state = str(
            action.get("expected_state") or _first_expected_value(target_state) or ""
        ).strip()
        if not _execution_was_accepted(execute_result):
            return {
                "status": "execute_failed",
                "reason": str(execute_result.get("error") or status or "execute_failed"),
                "action_id": action_id,
                "expected_state": expected_state,
                "target_state_diff": diff,
                "judge": {
                    "adapter_kind": self.adapter_kind,
                    "provider": self.provider,
                    "model": self.model,
                    "used_llm": False,
                },
            }
        diff_status = str(diff.get("status") or "")
        if diff_status == "matched":
            review_status = "succeeded"
            reason = "target_state_matched"
        elif diff_status == "mismatch":
            review_status = "mismatch"
            reason = "target_state_mismatch"
        else:
            review_status = "pending"
            reason = "target_state_unverified"
        return {
            "status": review_status,
            "reason": reason,
            "action_id": action_id,
            "expected_state": expected_state,
            "target_state_diff": diff,
            "judge": {
                "adapter_kind": self.adapter_kind,
                "provider": self.provider,
                "model": self.model,
                "used_llm": False,
            },
        }


class OpenAICompatibleActionReviewer:
    adapter_kind = "openai_compatible_action_reasoner"
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 12.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = max(0.5, timeout_s)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleActionReviewer | None":
        enabled = os.environ.get("THOUGHT_CORE_ACTION_LLM_ENABLED", "").strip()
        if enabled:
            if enabled.lower() in {"0", "false", "off", "no"}:
                return None
        elif os.environ.get("THOUGHT_CORE_LLM_ENABLED", "").strip().lower() not in {
            "1",
            "true",
            "on",
            "yes",
        }:
            return None
        base_url = (
            os.environ.get("THOUGHT_CORE_ACTION_LLM_BASE_URL")
            or os.environ.get("THOUGHT_CORE_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        api_key = (
            os.environ.get("THOUGHT_CORE_ACTION_LLM_API_KEY")
            or os.environ.get("THOUGHT_CORE_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        model = (
            os.environ.get("THOUGHT_CORE_ACTION_LLM_MODEL")
            or os.environ.get("THOUGHT_CORE_LLM_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o-mini"
        )
        if not api_key and not _is_loopback_url(base_url):
            return None
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=_float_env("THOUGHT_CORE_ACTION_LLM_TIMEOUT_S", 12.0),
        )

    def imagine_target_state(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
        local_target_state: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the Target State generator inside thought-core. "
                        "Return JSON only. Build a compact Target State projection "
                        "from user prompt, Environment State, and relevant memory. "
                        "Only required values belong in bindings; unrelated values "
                        "are ANY. Do not invent tools or device IDs."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt": turn.text,
                            "environment": _compact_observation(observation),
                            "local_target_state": local_target_state,
                            "instruction": (
                                "Return JSON with status, rationale, optional bindings, "
                                "and brief. If unsure, keep local_target_state semantics."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 260,
        }
        decision = self._chat_json(payload)
        return {
            "status": str(decision.get("status") or local_target_state.get("status") or "ok"),
            "rationale": str(decision.get("rationale") or decision.get("brief") or ""),
            "bindings": decision.get("bindings"),
            "judge": self._judge_metadata(),
        }

    def plan_command(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
        target_state: dict[str, Any],
        preview: dict[str, Any],
        local_plan: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the command planner inside thought-core. Return JSON only. "
                        "Use the allowlisted preview action; do not invent service calls. "
                        "Decide whether the preview action is needed to close the Target "
                        "State diff."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt": turn.text,
                            "environment": _compact_observation(observation),
                            "target_state": target_state,
                            "preview": _safe_preview(preview),
                            "local_plan": local_plan,
                            "instruction": (
                                "Return JSON with status, action_id, reason, and brief. "
                                "action_id must match preview.action.action_id when present."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 220,
        }
        decision = self._chat_json(payload)
        return {
            "status": str(decision.get("status") or local_plan.get("status") or "ready"),
            "action_id": decision.get("action_id"),
            "reason": str(decision.get("reason") or decision.get("brief") or ""),
            "brief": str(decision.get("brief") or ""),
            "judge": self._judge_metadata(),
        }

    def review_target_state(
        self,
        turn: TurnInput,
        action: dict[str, Any],
        target_state: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
        local_review: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an action reviewer inside thought-core. Return JSON only. "
                        "Judge whether Environment State satisfies Target State after a "
                        "home-control command. Target State is a projection: only required "
                        "bindings matter, and every unspecified environment value is ANY. "
                        "Allowed status values are succeeded, mismatch, pending, execute_failed."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt": turn.text,
                            "action": _safe_action(action),
                            "target_state": target_state,
                            "environment_after": _compact_observation(observation),
                            "execute_result": _safe_execute_result(execute_result),
                            "local_diff": local_review.get("target_state_diff"),
                            "local_status": local_review.get("status"),
                            "instruction": (
                                "Return JSON with status, reason, confidence, and brief. "
                                "Do not require any value outside target_state.bindings."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 220,
        }
        decision = self._chat_json(payload)
        status = str(decision.get("status") or "").strip()
        if status not in {"succeeded", "mismatch", "pending", "execute_failed"}:
            raise ValueError("action reviewer returned invalid status")
        return {
            "status": status,
            "reason": str(decision.get("reason") or "llm_target_state_review"),
            "confidence": decision.get("confidence"),
            "brief": str(decision.get("brief") or ""),
            "judge": self._judge_metadata(),
        }

    def _chat_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        ensure_execution_active()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        with request.urlopen(
            req,
            timeout=clamp_execution_timeout(self.timeout_s),
        ) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        ensure_execution_active()
        content = _extract_chat_completion_text(response_payload).strip()
        return _parse_json_object(content)

    def _judge_metadata(self) -> dict[str, Any]:
        return {
            "adapter_kind": self.adapter_kind,
            "provider": self.provider,
            "model": self.model,
            "used_llm": True,
            "base_url": _safe_base_url(self.base_url),
        }


class EnvironmentActionReasoner:
    adapter_kind = "environment_action_reasoner"
    provider = "thought-core"
    model = "configured"

    def __init__(
        self,
        *,
        fallback: LocalActionReasoner | None = None,
        reviewer: OpenAICompatibleActionReviewer | None = None,
    ) -> None:
        self.fallback = fallback or LocalActionReasoner()
        self.reviewer = reviewer

    @classmethod
    def from_env(cls) -> "EnvironmentActionReasoner":
        return cls(reviewer=OpenAICompatibleActionReviewer.from_env())

    def imagine_target_state(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        local_target_state = self.fallback.imagine_target_state(turn, observation)
        if self.reviewer is None:
            return local_target_state
        try:
            llm_target_state = self.reviewer.imagine_target_state(
                turn,
                observation,
                local_target_state,
            )
        except (OSError, ValueError, json.JSONDecodeError, error.URLError) as exc:
            fallback = dict(local_target_state)
            fallback["judge"] = {
                "adapter_kind": self.fallback.adapter_kind,
                "provider": self.fallback.provider,
                "model": self.fallback.model,
                "used_llm": False,
                "fallback_after_llm_error": str(exc)[:240],
            }
            return fallback
        return _merge_llm_target_state(local_target_state, llm_target_state)

    def plan_command(
        self,
        turn: TurnInput,
        observation: dict[str, Any],
        target_state: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        local_plan = self.fallback.plan_command(turn, observation, target_state, preview)
        if self.reviewer is None:
            return local_plan
        try:
            llm_plan = self.reviewer.plan_command(
                turn,
                observation,
                target_state,
                preview,
                local_plan,
            )
        except (OSError, ValueError, json.JSONDecodeError, error.URLError) as exc:
            fallback = dict(local_plan)
            fallback["judge"] = {
                "adapter_kind": self.fallback.adapter_kind,
                "provider": self.fallback.provider,
                "model": self.fallback.model,
                "used_llm": False,
                "fallback_after_llm_error": str(exc)[:240],
            }
            return fallback
        return _merge_llm_command_plan(local_plan, llm_plan, preview)

    def review_target_state(
        self,
        turn: TurnInput,
        action: dict[str, Any],
        target_state: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> dict[str, Any]:
        local_review = self.fallback.review_target_state(
            turn,
            action,
            target_state,
            observation,
            execute_result,
        )
        if self.reviewer is None:
            return local_review
        try:
            llm_review = self.reviewer.review_target_state(
                turn,
                action,
                target_state,
                observation,
                execute_result,
                local_review,
            )
        except (OSError, ValueError, json.JSONDecodeError, error.URLError) as exc:
            fallback = dict(local_review)
            fallback["judge"] = {
                "adapter_kind": self.fallback.adapter_kind,
                "provider": self.fallback.provider,
                "model": self.fallback.model,
                "used_llm": False,
                "fallback_after_llm_error": str(exc)[:240],
            }
            return fallback
        return _merge_llm_review(local_review, llm_review)


def build_action_reasoner_from_env() -> EnvironmentActionReasoner:
    return EnvironmentActionReasoner.from_env()


def describe_action_reasoner(reasoner: ActionReasoner) -> dict[str, str]:
    return {
        "boundary": ACTION_REASONER_BOUNDARY,
        "adapter_kind": getattr(reasoner, "adapter_kind", "unknown"),
        "provider": getattr(reasoner, "provider", "unknown"),
        "model": getattr(reasoner, "model", "unknown"),
    }


def evaluate_target_state(
    target_state: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    bindings = target_state.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        return {
            "status": "unsupported",
            "matches": [],
            "mismatches": [],
            "unknowns": [],
            "ignored": {
                "policy": target_state.get(
                    "wildcard_policy",
                    "unspecified_values_are_any",
                ),
            },
        }
    matches: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("scope") != "required":
            continue
        observed = _observed_state_for_binding(observation, binding)
        item = {
            "binding": binding,
            "observed": observed,
        }
        if not observed.get("available"):
            unknowns.append({**item, "reason": "state_unavailable"})
            continue
        if observed.get("stale"):
            unknowns.append({**item, "reason": "state_stale"})
            continue
        confidence = str(observed.get("confidence_label") or "").lower()
        if "confidence_label" in observed and confidence in {"", "low", "very_low", "unknown"}:
            unknowns.append({**item, "reason": "state_low_confidence"})
            continue
        expected = binding.get("value")
        actual = observed.get("state")
        if _state_matches(expected, actual):
            matches.append(item)
        else:
            mismatches.append(
                {
                    **item,
                    "reason": "required_value_mismatch",
                    "expected": expected,
                    "actual": actual,
                }
            )
    status = "matched"
    if mismatches:
        status = "mismatch"
    elif unknowns:
        status = "pending"
    return {
        "status": status,
        "matches": matches,
        "mismatches": mismatches,
        "unknowns": unknowns,
        "ignored": {
            "policy": target_state.get(
                "wildcard_policy",
                "unspecified_values_are_any",
            ),
            "note": "Environment values without required bindings are treated as ANY.",
        },
    }


def _observed_state_for_binding(
    observation: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    target = str(binding.get("target") or "").strip()
    aliases = {
        str(item)
        for item in binding.get("target_aliases", [])
        if str(item or "").strip()
    }
    if target:
        aliases.add(target)
    facts = observation.get("facts")
    devices = facts.get("devices", []) if isinstance(facts, dict) else []
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = str(device.get("id") or "")
            kind = str(device.get("kind") or "")
            if device_id not in aliases and kind not in aliases:
                continue
            return {
                "available": True,
                "state": device.get("state"),
                "source": device.get("source") or "facts.devices",
                "target": target,
                "device_id": device_id,
                "stale": _as_bool(device.get("stale")) is True,
                "updated_at": device.get("updated_at"),
            }
    environment = observation.get("environment")
    appliances = (
        environment.get("appliances", {}) if isinstance(environment, dict) else {}
    )
    if isinstance(appliances, dict):
        for alias in aliases:
            appliance = appliances.get(alias)
            if isinstance(appliance, dict):
                return {
                    "available": True,
                    "state": appliance.get("state"),
                    "source": appliance.get("source") or "environment.appliances",
                    "target": target,
                    "device_id": alias,
                    "stale": _as_bool(appliance.get("stale")) is True,
                    "updated_at": appliance.get("updated_at"),
                }
    if target in {"light", "living_room_light"} or "living_room_light" in aliases:
        room_light = _room_light_from_observation(observation)
        if room_light:
            return {
                "available": room_light.get("available") is not False,
                "state": room_light.get("state"),
                "source": room_light.get("authority") or room_light.get("source"),
                "target": target,
                "device_id": "room_light",
                "stale": _as_bool(room_light.get("stale")) is True,
                "confidence_label": room_light.get("confidence_label"),
                "updated_at": room_light.get("updated_at")
                or room_light.get("observed_at"),
            }
    return {"available": False, "state": None, "target": target}


def _room_light_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    environment = observation.get("environment")
    if not isinstance(environment, dict):
        return {}
    state_queries = environment.get("state_queries")
    if not isinstance(state_queries, dict):
        return {}
    room_light = state_queries.get("room_light")
    return dict(room_light) if isinstance(room_light, dict) else {}


def _target_aliases(target: str, action_id: str) -> list[str]:
    aliases = [target, action_id]
    if target == "light":
        aliases.append("living_room_light")
    return aliases


def _target_state_path_hint(target: str) -> str:
    if target == "light":
        return "environment.state_queries.room_light.state"
    return f"environment.appliances.{target}.state"


def _first_expected_value(target_state: dict[str, Any]) -> str:
    bindings = target_state.get("bindings")
    if isinstance(bindings, list):
        for binding in bindings:
            if isinstance(binding, dict) and binding.get("scope") == "required":
                return str(binding.get("value") or "")
    return ""


def _state_matches(expected: Any, actual: Any) -> bool:
    expected_norm = _normalize_state(expected)
    actual_norm = _normalize_state(actual)
    if expected_norm in {"on", "true"}:
        return actual_norm in {"on", "true"}
    if expected_norm in {"off", "false"}:
        return actual_norm in {"off", "false"}
    return expected_norm == actual_norm


def _normalize_state(value: Any) -> str:
    text = str(value if value is not None else "").strip().lower()
    return {
        "1": "true",
        "0": "false",
        "yes": "true",
        "no": "false",
        "オン": "on",
        "オフ": "off",
        "点灯": "on",
        "消灯": "off",
    }.get(text, text)


def _execution_was_accepted(execute_result: dict[str, Any]) -> bool:
    status = str(execute_result.get("status") or "")
    if bool(execute_result.get("executed")) or bool(execute_result.get("verified_by_bridge")):
        return True
    return status in {"accepted", "submitted", "duplicate"}


def _merge_llm_review(
    local_review: dict[str, Any],
    llm_review: dict[str, Any],
) -> dict[str, Any]:
    local_status = str(local_review.get("status") or "")
    llm_status = str(llm_review.get("status") or "")
    allowed = {
        "succeeded": {"succeeded"},
        "mismatch": {"mismatch", "pending"},
        "pending": {"pending", "mismatch"},
        "execute_failed": {"execute_failed"},
    }.get(local_status, {local_status})
    status = llm_status if llm_status in allowed else local_status
    merged = dict(local_review)
    merged["status"] = status
    merged["reason"] = str(llm_review.get("reason") or merged.get("reason") or "")
    merged["llm_status"] = llm_status
    merged["local_status"] = local_status
    merged["judge"] = llm_review.get("judge", {})
    if llm_review.get("confidence") is not None:
        merged["confidence"] = llm_review.get("confidence")
    if llm_review.get("brief"):
        merged["brief"] = llm_review.get("brief")
    if status != llm_status:
        merged["llm_status_clamped"] = True
    return merged


def _merge_llm_target_state(
    local_target_state: dict[str, Any],
    llm_target_state: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(local_target_state)
    merged["llm_status"] = llm_target_state.get("status")
    merged["judge"] = llm_target_state.get("judge", {})
    if llm_target_state.get("rationale"):
        merged["llm_rationale"] = llm_target_state.get("rationale")
        merged["rationale"] = str(llm_target_state.get("rationale"))
    bindings = llm_target_state.get("bindings")
    if isinstance(bindings, list) and _bindings_are_compatible(
        local_target_state.get("bindings"),
        bindings,
    ):
        merged["bindings"] = bindings
    elif isinstance(bindings, list):
        merged["llm_bindings_rejected"] = True
    return merged


def _merge_llm_command_plan(
    local_plan: dict[str, Any],
    llm_plan: dict[str, Any],
    preview: dict[str, Any],
) -> dict[str, Any]:
    action = preview.get("action") if isinstance(preview.get("action"), dict) else {}
    allowed_action_id = str(action.get("action_id") or local_plan.get("action_id") or "")
    llm_action_id = str(llm_plan.get("action_id") or "")
    merged = dict(local_plan)
    merged["llm_status"] = llm_plan.get("status")
    merged["judge"] = llm_plan.get("judge", {})
    if llm_plan.get("reason"):
        merged["reason"] = llm_plan.get("reason")
    if llm_plan.get("brief"):
        merged["brief"] = llm_plan.get("brief")
    if llm_action_id and llm_action_id != allowed_action_id:
        merged["llm_action_id_rejected"] = llm_action_id
        return merged
    local_status = str(local_plan.get("status") or "")
    llm_status = str(llm_plan.get("status") or "")
    if llm_status == local_status and llm_status in {
        "ready",
        "already_satisfied",
        "unavailable",
    }:
        merged["status"] = llm_status
    elif llm_status and llm_status != local_status:
        merged["llm_status_rejected"] = llm_status
    return merged


def _bindings_are_compatible(local_bindings: Any, llm_bindings: list[Any]) -> bool:
    if not isinstance(local_bindings, list) or not local_bindings:
        return False
    if len(local_bindings) != len(llm_bindings):
        return False
    for local, proposed in zip(local_bindings, llm_bindings, strict=False):
        if not isinstance(local, dict) or not isinstance(proposed, dict):
            return False
        if str(local.get("kind") or "") != str(proposed.get("kind") or ""):
            return False
        if str(local.get("target") or "") != str(proposed.get("target") or ""):
            return False
        if _normalize_state(local.get("value")) != _normalize_state(proposed.get("value")):
            return False
        if str(proposed.get("scope") or "required") != "required":
            return False
    return True


def _safe_action(action: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "action",
        "action_id",
        "target",
        "target_aliases",
        "target_name",
        "expected_state",
        "pre_action_phrase",
        "expected_effect",
    }
    return {key: value for key, value in action.items() if key in allowed}


def _safe_execute_result(execute_result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "status",
        "executed",
        "verified_by_bridge",
        "retryable",
        "attempt",
        "expected_state",
        "error",
    }
    return {key: value for key, value in execute_result.items() if key in allowed}


def _safe_preview(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": preview.get("status"),
        "should_execute": preview.get("should_execute"),
        "action": _safe_action(preview.get("action") if isinstance(preview.get("action"), dict) else {}),
        "error": preview.get("error"),
    }


def _compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "facts": observation.get("facts", {}),
        "environment": {},
    }
    environment = observation.get("environment")
    if isinstance(environment, dict):
        compact["environment"] = {
            "appliances": environment.get("appliances", {}),
            "state_queries": environment.get("state_queries", {}),
            "wait_result": environment.get("wait_result", {}),
        }
    memory_context = observation.get("memory_context")
    if isinstance(memory_context, dict):
        compact["memory_context"] = {
            "summary": memory_context.get("summary"),
            "item_count": memory_context.get("item_count", 0),
            "items": memory_context.get("items", [])[:6]
            if isinstance(memory_context.get("items"), list)
            else [],
        }
    return compact


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        decoded = json.loads(text[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError("expected JSON object")
    return decoded


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _safe_base_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None
