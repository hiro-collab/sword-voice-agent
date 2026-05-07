"""Explicit thought loop for the first thought-core contract draft."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from .events import EventFactory, ThoughtEvent
from .responders import (
    TURN_RESPONDER_BOUNDARY,
    EnvironmentTurnResponder,
    TurnResponder,
    describe_responder,
)
from .schema import TurnInput
from .tools import (
    ThoughtTools,
    build_tools_from_env,
    detect_home_action_intent,
    detect_room_light_state_query,
)


class ThoughtLoop:
    def __init__(
        self,
        tools: ThoughtTools | None = None,
        *,
        max_execute_attempts: int = 3,
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
            if detect_room_light_state_query(turn_input.text):
                self._handle_room_light_state_query(events, factory, turn_input)
                return events

            if detect_home_action_intent(turn_input.text) is None:
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
            if preview.get("status") == "noop" and action:
                speech = str(
                    preview.get("message")
                    or action.get("reason_text")
                    or "今の状態では操作しなくて大丈夫です。"
                )
                events.append(
                    factory.emit(
                        "action.skipped",
                        {
                            "reason": action.get("reason") or "noop",
                            "action": action,
                            "message": speech,
                        },
                    )
                )
                self._emit_message(
                    events,
                    factory,
                    speech=speech,
                    display=speech,
                    emotion="neutral",
                    motion="small_nod",
                    priority="normal",
                )
                events.append(
                    factory.emit(
                        "turn.completed",
                        {
                            "status": "noop",
                            "action": action,
                            "reason": action.get("reason") or "noop",
                            "preview_status": preview.get("status"),
                        },
                    )
                )
                return events
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
                    post_action_feedback = self._post_action_room_light_feedback(
                        turn_input,
                        action,
                        after_observation,
                    )
                    success_speech = messages["success_speech"]
                    success_display = messages["success_display"]
                    if post_action_feedback["speech_suffix"]:
                        success_speech = (
                            f"{success_speech} {post_action_feedback['speech_suffix']}"
                        )
                        success_display = f"{success_display} / 映像確認"
                    self._emit_message(
                        events,
                        factory,
                        speech=success_speech,
                        display=success_display,
                        emotion="satisfied",
                        motion="small_nod",
                        priority="normal",
                    )
                    pending = post_action_feedback["pending"]
                    if pending:
                        events.append(factory.emit("state_query.feedback_pending", pending))
                    events.append(
                        factory.emit(
                            "turn.completed",
                            {
                                "status": "success",
                                "attempts": attempt,
                                "action": action,
                                "execute_status": execute_result.get("status"),
                                "room_light_wait_matched": post_action_feedback[
                                    "wait_matched"
                                ],
                                "post_action_feedback_pending": bool(pending),
                                "pending_state_query_id": (
                                    pending.get("state_query_id") if pending else ""
                                ),
                                "pending_state_query_json": (
                                    json.dumps(pending, ensure_ascii=False, sort_keys=True)
                                    if pending
                                    else ""
                                ),
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

    def _handle_room_light_state_query(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> None:
        observation = self._call_tool(
            events,
            factory,
            "environment.observe",
            lambda: self.tools.environment_observe(turn_input, reason="state_query"),
        )
        room_light = self._room_light_from_observation(observation)
        events.append(
            factory.emit(
                "observation.received",
                {
                    "observation_ref": observation.get("observation_ref"),
                    "observation_source": observation.get("observation_source"),
                    "facts": observation.get("facts", {}),
                    "state_query_id": "room_light",
                },
            )
        )
        reply = self._room_light_state_reply(room_light)
        events.append(
            factory.emit(
                "environment.state_query_answer",
                {
                    "state_query_id": "room_light",
                    "target": "room_light",
                    "available": room_light.get("available") if room_light else False,
                    "stale": room_light.get("stale") if room_light else None,
                    "state": room_light.get("state") if room_light else "unknown",
                    "confidence_label": (
                        room_light.get("confidence_label") if room_light else ""
                    ),
                    "authority": room_light.get("authority") if room_light else "",
                    "answer_hint": room_light.get("answer_hint") if room_light else "",
                    "source": "environment.observe",
                },
            )
        )
        self._emit_message(
            events,
            factory,
            speech=reply["speech"],
            display=reply["display"],
            emotion="focused",
            motion="think",
            priority="normal",
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "state_answer",
                    "state_query_id": "room_light",
                    "state": room_light.get("state") if room_light else "unknown",
                    "confidence_label": (
                        room_light.get("confidence_label") if room_light else ""
                    ),
                    "authority": room_light.get("authority") if room_light else "",
                    "available": room_light.get("available") if room_light else False,
                    "stale": room_light.get("stale") if room_light else None,
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

    def _post_action_room_light_feedback(
        self,
        turn_input: TurnInput,
        action: dict[str, Any],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        empty = {"speech_suffix": "", "pending": {}, "wait_matched": None}
        action_id = str(action.get("action_id") or "")
        expected_state = str(action.get("expected_state") or "")
        if (
            action_id not in {"light_on", "light_off"}
            or expected_state not in {"on", "off"}
        ):
            return empty

        environment = observation.get("environment")
        if not isinstance(environment, dict):
            return empty
        wait_result = environment.get("wait_result")
        if not isinstance(wait_result, dict):
            return empty
        wait_matched = self._as_bool(wait_result.get("matched"))
        if wait_matched is False:
            return {
                "speech_suffix": "映像側の更新はまだ取れていないので、状態確認は少し待ってからにします。",
                "pending": {},
                "wait_matched": False,
            }
        if wait_matched is not True:
            return empty

        room_light = self._room_light_from_observation(observation)
        if not room_light or room_light.get("available") is False or self._as_bool(
            room_light.get("stale")
        ):
            return {"speech_suffix": "", "pending": {}, "wait_matched": True}

        state = str(room_light.get("state") or "unknown").lower()
        confidence = str(room_light.get("confidence_label") or "").lower()
        confidence_low = confidence in {"", "low", "very_low", "unknown"}
        mismatch = state in {"on", "off"} and state != expected_state
        unknown = state not in {"on", "off"}
        if not (unknown or confidence_low or mismatch):
            return {"speech_suffix": "", "pending": {}, "wait_matched": True}

        expected_text = "ついてる" if expected_state == "on" else "消えてる"
        if mismatch:
            prompt = f"映像だと少しズレて見えるんだけど、実際は{expected_text}？"
        elif confidence_low:
            prompt = f"映像だとまだ自信が低いんだけど、実際は{expected_text}？"
        else:
            prompt = f"実際は{expected_text}？"
        pending = self._build_post_action_pending(
            turn_input,
            action_id,
            expected_state,
            room_light,
            environment,
        )
        return {"speech_suffix": prompt, "pending": pending, "wait_matched": True}

    def _build_post_action_pending(
        self,
        turn_input: TurnInput,
        action_id: str,
        expected_state: str,
        room_light: dict[str, Any],
        environment: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = (
            room_light.get("evidence")
            if isinstance(room_light.get("evidence"), dict)
            else {}
        )
        wait_result = environment.get("wait_result")
        return {
            "type": "state_query_pending",
            "state_query_id": "room_light",
            "target": "room_light",
            "feedback_reason": "user_correction_after_light_action",
            "source_context": "post_light_action",
            "workflow_version": "thought-core-post-action-room-light-feedback-v1",
            "turn_id": turn_input.turn_id,
            "session_id": turn_input.session_id,
            "action_id": action_id,
            "expected_state": expected_state,
            "snapshot_id": str(environment.get("snapshot_id") or ""),
            "authority": str(room_light.get("authority") or room_light.get("source") or ""),
            "projected_by": str(room_light.get("projected_by") or ""),
            "predicted_state": str(room_light.get("state") or "unknown"),
            "confidence_label": str(room_light.get("confidence_label") or ""),
            "answer_hint": str(room_light.get("answer_hint") or ""),
            "observed_at": str(
                room_light.get("observed_at") or evidence.get("observed_at") or ""
            ),
            "updated_at": str(
                room_light.get("updated_at") or evidence.get("updated_at") or ""
            ),
            "wait_result": wait_result if isinstance(wait_result, dict) else {},
            "evidence": {
                "source": evidence.get("source"),
                "topic": evidence.get("topic"),
                "lighting_type": evidence.get("lighting_type"),
                "daylight_state": evidence.get("daylight_state"),
                "probabilities": evidence.get("probabilities"),
            },
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _room_light_from_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        environment = observation.get("environment")
        if isinstance(environment, dict):
            queries = environment.get("state_queries")
            if isinstance(queries, dict) and isinstance(queries.get("room_light"), dict):
                return queries["room_light"]
        facts = observation.get("facts")
        if isinstance(facts, dict):
            queries = facts.get("state_queries")
            if isinstance(queries, dict) and isinstance(queries.get("room_light"), dict):
                return queries["room_light"]
        return {}

    def _room_light_state_reply(self, room_light: dict[str, Any]) -> dict[str, str]:
        if not room_light:
            speech = "映像側の room_light がまだ届いていません。少し待ってからもう一度確認します。"
            return {"speech": speech, "display": speech}
        if room_light.get("available") is False or self._as_bool(room_light.get("stale")):
            speech = "映像側の room_light がまだ安定していません。少し待ってからもう一度確認します。"
            return {"speech": speech, "display": speech}
        state = str(room_light.get("state") or "unknown").lower()
        confidence = str(room_light.get("confidence_label") or "").lower()
        if confidence in {"low", "very_low", "unknown"}:
            speech = "カメラ推定では明るさの判定がまだ弱いです。実際の状態を教えてもらえると助かります。"
            return {"speech": speech, "display": speech}
        if state == "on":
            speech = "カメラ推定では、リビングの電気はついているように見えます。"
            return {"speech": speech, "display": speech}
        if state == "off":
            speech = "カメラ推定では、リビングの電気は消えているように見えます。"
            return {"speech": speech, "display": speech}
        hint = str(room_light.get("answer_hint") or "").strip()
        speech = (
            f"カメラ推定ではまだ判断できません。{hint}"
            if hint
            else "カメラ推定ではまだ判断できません。"
        )
        return {"speech": speech, "display": speech}

    def _as_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "matched"}:
            return True
        if text in {"0", "false", "no", "off", "unmatched"}:
            return False
        return None

    def _home_action_messages(self, action: dict[str, Any]) -> dict[str, str]:
        action_id = str(action.get("action_id") or "")
        action_messages = {
            "fan_on": ("扇風機をつける", "扇風機をつけたよ。"),
            "fan_off": ("扇風機を消す", "扇風機を消したよ。"),
            "aircon_on": ("エアコンをつける", "エアコンをつけたよ。"),
            "aircon_off": ("エアコンを消す", "エアコンを消したよ。"),
            "door_open": ("中扉を開ける", "中扉を開けたよ。"),
            "door_close": ("中扉を閉める", "中扉を閉めたよ。"),
            "door_stop": ("中扉を止める", "中扉を止めたよ。"),
            "vacuum_start": ("掃除機を動かす", "掃除機を動かしたよ。"),
            "vacuum_return": ("掃除機を戻す", "掃除機を戻したよ。"),
            "vacuum_pause": ("掃除機を一時停止する", "掃除機を一時停止したよ。"),
        }
        if action_id in action_messages:
            phrase, success_speech = action_messages[action_id]
            phrase = str(action.get("pre_action_phrase") or phrase)
            return {
                "before_speech": f"了解、{phrase}ね。",
                "before_display": f"{phrase}",
                "success_speech": success_speech,
                "success_display": success_speech,
                "feedback_speech": f"{phrase}操作を確認できませんでした。状態を確認してもらえますか？",
            }

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
