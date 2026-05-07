"""Explicit thought loop for the first thought-core contract draft."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .events import EventFactory, ThoughtEvent
from .responders import (
    TURN_RESPONDER_BOUNDARY,
    EnvironmentTurnResponder,
    TurnResponder,
    describe_responder,
)
from .schema import TurnInput
from .tools import ThoughtTools, build_tools_from_env, detect_home_light_intent


class ThoughtLoop:
    def __init__(
        self,
        tools: ThoughtTools | None = None,
        *,
        max_execute_attempts: int = 2,
        source: str = "thought-core",
        responder: TurnResponder | None = None,
    ) -> None:
        self.tools = tools or build_tools_from_env()
        self.max_execute_attempts = max(1, max_execute_attempts)
        self.source = source
        self.responder = responder or EnvironmentTurnResponder.from_env()

    def run(self, turn: TurnInput | Mapping[str, Any]) -> list[ThoughtEvent]:
        turn_input = turn if isinstance(turn, TurnInput) else TurnInput.from_mapping(turn)
        factory = EventFactory(turn_input.turn_id, turn_input.session_id, source=self.source)
        events: list[ThoughtEvent] = []
        try:
            if detect_home_light_intent(turn_input.text) is None:
                self._handle_general_turn(events, factory, turn_input)
                return events

            observation = self._call_tool(
                events,
                factory,
                "environment.observe",
                lambda: self.tools.environment_observe(turn_input, reason="before_action"),
            )
            events.append(
                factory.emit(
                    "observation.received",
                    {
                        "observation_ref": observation.get("observation_ref"),
                        "observation_source": observation.get("observation_source"),
                        "facts": observation.get("facts", {}),
                    },
                )
            )

            preview = self._call_tool(
                events,
                factory,
                "home.preview",
                lambda: self.tools.home_preview(turn_input, observation),
            )
            action = preview.get("action", {})
            if preview.get("status") not in {"ok", "preview"} or not action:
                events.append(
                    factory.emit(
                        "feedback.requested",
                        {
                            "reason": "action_preview_failed",
                            "speech": "家電操作の準備で止まりました。ブリッジ設定を確認します。",
                            "display": "家電操作の準備に失敗しました",
                            "preview_status": preview.get("status"),
                            "preview_error": preview.get("error"),
                        },
                    )
                )
                events.append(
                    factory.emit(
                        "turn.completed",
                        {
                            "status": "needs_feedback",
                            "action": action,
                            "preview_status": preview.get("status"),
                        },
                    )
                )
                return events
            events.append(factory.emit("action.proposed", {"action": action}))

            messages = self._home_action_messages(action)
            self._emit_message(
                events,
                factory,
                speech=messages["before_speech"],
                display=messages["before_display"],
                emotion="confident",
                motion="nod",
                priority="immediate",
            )

            for attempt in range(1, self.max_execute_attempts + 1):
                execute_result = self._call_tool(
                    events,
                    factory,
                    "home.execute",
                    lambda: self.tools.home_execute(turn_input, action),
                )
                after_observation = self._call_tool(
                    events,
                    factory,
                    "environment.observe",
                    lambda: self.tools.environment_observe(turn_input, reason="after_action"),
                )
                events.append(
                    factory.emit(
                        "observation.received",
                        {
                            "observation_ref": after_observation.get("observation_ref"),
                            "observation_source": after_observation.get("observation_source"),
                            "facts": after_observation.get("facts", {}),
                            "after_tool": "home.execute",
                            "attempt": attempt,
                        },
                    )
                )

                if self._action_succeeded(action, after_observation, execute_result):
                    self._emit_message(
                        events,
                        factory,
                        speech=messages["success_speech"],
                        display=messages["success_display"],
                        emotion="satisfied",
                        motion="small_nod",
                        priority="normal",
                    )
                    events.append(
                        factory.emit(
                            "turn.completed",
                            {
                                "status": "success",
                                "attempts": attempt,
                                "action": action,
                                "execute_status": execute_result.get("status"),
                            },
                        )
                    )
                    return events

                retryable = bool(execute_result.get("retryable", True))
                if attempt < self.max_execute_attempts and retryable:
                    self._emit_message(
                        events,
                        factory,
                        speech="反応が確認できないので、もう一度だけ試します。",
                        display="再試行します",
                        emotion="focused",
                        motion="look_back",
                        priority="normal",
                    )
                    continue

                events.append(
                    factory.emit(
                        "feedback.requested",
                        {
                            "reason": "verification_failed",
                            "speech": messages["feedback_speech"],
                            "display": "電気の状態確認が必要です",
                            "attempts": attempt,
                            "last_execute_status": execute_result.get("status"),
                        },
                    )
                )
                events.append(
                    factory.emit(
                        "turn.completed",
                        {
                            "status": "needs_feedback",
                            "attempts": attempt,
                            "action": action,
                        },
                    )
                )
                return events
        except Exception as exc:  # pragma: no cover - defensive boundary
            events.append(
                factory.emit(
                    "turn.error",
                    {
                        "code": "thought_core_error",
                        "message": str(exc),
                    },
                )
            )
            return events

    def run_dicts(self, turn: TurnInput | Mapping[str, Any]) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.run(turn)]

    def _call_tool(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        tool_name: str,
        call: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        tool_call_id = factory.next_tool_call_id()
        events.append(
            factory.emit(
                "tool.started",
                {
                    "tool": tool_name,
                    "tool_call_id": tool_call_id,
                },
            )
        )
        result = call()
        status = result.get("status", "ok")
        events.append(
            factory.emit(
                "tool.result",
                {
                    "tool": tool_name,
                    "tool_call_id": tool_call_id,
                    "status": status,
                    "result": result,
                },
            )
        )
        return result

    def _handle_general_turn(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> None:
        events.append(factory.emit("responder.started", describe_responder(self.responder)))
        result = self.responder.respond(turn_input)
        events.append(
            factory.emit(
                "responder.completed",
                {
                    "boundary": TURN_RESPONDER_BOUNDARY,
                    "adapter_kind": result.adapter_kind,
                    "provider": result.provider,
                    "model": result.model,
                    "status": result.status,
                    "used_llm": result.used_llm,
                    "detail": result.detail,
                    "metadata": result.metadata,
                },
            )
        )
        self._emit_message(
            events,
            factory,
            speech=result.speech,
            display=result.display,
            emotion="neutral",
            motion="idle",
            priority="normal",
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": result.status,
                    "boundary": TURN_RESPONDER_BOUNDARY,
                    "adapter_kind": result.adapter_kind,
                    "used_llm": result.used_llm,
                },
            )
        )

    def _emit_message(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        *,
        speech: str,
        display: str,
        emotion: str,
        motion: str,
        priority: str,
    ) -> None:
        events.append(
            factory.emit(
                "assistant.speech_delta",
                {
                    "delta": speech,
                    "channel": "speech",
                },
            )
        )
        events.append(
            factory.emit(
                "assistant.message",
                {
                    "speech": speech,
                    "display": display,
                    "emotion": emotion,
                    "motion": motion,
                    "priority": priority,
                },
            )
        )

    def _home_action_messages(self, action: dict[str, Any]) -> dict[str, str]:
        target_name = str(action.get("target_name") or "電気")
        expected_state = action.get("expected_state")
        if expected_state == "off":
            return {
                "before_speech": f"了解、{target_name}を消すね。",
                "before_display": f"{target_name}を消します",
                "success_speech": f"{target_name}を消したよ。",
                "success_display": f"{target_name}をOFFにしました",
                "feedback_speech": "電気が消えたか確認できませんでした。状態を確認してもらえますか？",
            }
        return {
            "before_speech": f"了解、{target_name}をつけるね。",
            "before_display": f"{target_name}をつけます",
            "success_speech": f"{target_name}をつけたよ。",
            "success_display": f"{target_name}をONにしました",
            "feedback_speech": "電気がついたか確認できませんでした。状態を確認してもらえますか？",
        }

    def _action_succeeded(
        self,
        action: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> bool:
        expected_state = action.get("expected_state")
        target = action.get("target")
        target_aliases = action.get("target_aliases")
        targets = {str(target)} if target is not None else set()
        if isinstance(target_aliases, list):
            targets.update(str(item) for item in target_aliases)
        facts = observation.get("facts", {})
        devices = facts.get("devices", [])
        if not isinstance(devices, list):
            return bool(execute_result.get("verified_by_bridge"))
        for device in devices:
            if not isinstance(device, dict):
                continue
            if str(device.get("id")) in targets and device.get("state") == expected_state:
                return True
        return bool(execute_result.get("verified_by_bridge"))
