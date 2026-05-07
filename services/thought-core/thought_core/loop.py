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
        self.pending_confirmations: dict[str, dict[str, Any]] = {}
        self.pending_action_reviews: dict[str, dict[str, Any]] = {}
        self.pending_state_queries: dict[str, dict[str, Any]] = {}

    def run(self, turn: TurnInput | Mapping[str, Any]) -> list[ThoughtEvent]:
        turn_input = turn if isinstance(turn, TurnInput) else TurnInput.from_mapping(turn)
        factory = EventFactory(turn_input.turn_id, turn_input.session_id, source=self.source)
        events: list[ThoughtEvent] = []
        try:
            self._emit_input_ack(events, factory, turn_input)
            if self._handle_state_query_feedback_if_needed(events, factory, turn_input):
                return events
            if self._handle_pending_confirmation_if_needed(events, factory, turn_input):
                return events
            if self._handle_pending_action_review_if_needed(events, factory, turn_input):
                return events

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

            if action.get("confirm_required"):
                self._remember_confirmation(turn_input, action)
                speech = self._confirmation_prompt(action)
                events.append(
                    factory.emit(
                        "action.confirmation_required",
                        {
                            "action": action,
                            "confirmation_token_present": bool(
                                action.get("confirmation_token")
                            ),
                            "speech": speech,
                        },
                    )
                )
                self._emit_message(
                    events,
                    factory,
                    speech=speech,
                    display=speech,
                    emotion="focused",
                    motion="small_nod",
                    priority="immediate",
                )
                events.append(
                    factory.emit(
                        "turn.completed",
                        {
                            "status": "confirmation_required",
                            "action": action,
                            "preview_status": preview.get("status"),
                            "confirmation_token_present": bool(
                                action.get("confirmation_token")
                            ),
                        },
                    )
                )
                return events

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

                review = self._review_action_result(action, after_observation, execute_result)
                events.append(factory.emit("action.reviewed", review))

                if review["status"] == "succeeded":
                    post_action_feedback = self._post_action_room_light_feedback(
                        turn_input,
                        action,
                        after_observation,
                    )
                    success_speech = (
                        str(execute_result.get("speak") or execute_result.get("message") or "")
                        or messages["success_speech"]
                    )
                    success_display = success_speech or messages["success_display"]
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
                        self._remember_state_query_pending(turn_input, pending)
                        events.append(factory.emit("state_query.feedback_pending", pending))
                    post_action_feedback_saved = False
                    if not pending:
                        post_action_feedback_saved = self._save_post_action_room_light_learning(
                            events,
                            factory,
                            turn_input,
                            action,
                            after_observation,
                        )
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
                                "post_action_feedback_saved": post_action_feedback_saved,
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

                if self._begin_pending_action_review(
                    events,
                    factory,
                    turn_input,
                    action=action,
                    execute_result=execute_result,
                    review=review,
                    observations_done=1,
                    execute_attempts=attempt,
                ):
                    return events

                retryable = bool(execute_result.get("retryable", True))
                if attempt < self.max_execute_attempts and retryable:
                    self._write_short_memory(
                        events,
                        factory,
                        turn_input,
                        action=action,
                        status="execute_retry_scheduled",
                        retry_scope="execute",
                        progress={
                            "execute_attempts": attempt,
                            "max_execute_attempts": self.max_execute_attempts,
                        },
                        review={},
                    )
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
                self._write_short_memory(
                    events,
                    factory,
                    turn_input,
                    action=action,
                    status="execute_retry_exhausted",
                    retry_scope="execute",
                    progress={
                        "execute_attempts": attempt,
                        "max_execute_attempts": self.max_execute_attempts,
                        "last_execute_status": execute_result.get("status"),
                    },
                    review={},
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

    def _handle_state_query_feedback_if_needed(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> bool:
        action_intent = detect_home_action_intent(turn_input.text)
        has_action = action_intent is not None and self._looks_like_home_action_command(
            turn_input.text
        )
        pending = self.pending_state_queries.get(turn_input.session_id)
        if pending and pending.get("state_query_id") == "room_light":
            label = self._room_light_feedback_label(turn_input.text, pending=pending)
            if label:
                self.pending_state_queries.pop(turn_input.session_id, None)
                if self._state_query_pending_is_expired(pending):
                    events.append(
                        factory.emit(
                            "state_query.feedback_expired",
                            {
                                "state_query_id": "room_light",
                                "target": "room_light",
                                "created_at": pending.get("created_at"),
                                "ttl_seconds": 120,
                                "continued_as_action": has_action,
                            },
                        )
                    )
                    if has_action:
                        return False
                    speech = "少し前の状態確認なので、もう一回見てから覚えます。"
                    self._emit_message(
                        events,
                        factory,
                        speech=speech,
                        display=speech,
                        emotion="focused",
                        motion="think",
                        priority="normal",
                    )
                    events.append(
                        factory.emit(
                            "turn.completed",
                            {
                                "status": "state_feedback_expired",
                                "state_query_id": "room_light",
                            },
                        )
                    )
                    return True

                self._persist_state_query_feedback(
                    events,
                    factory,
                    turn_input,
                    pending=pending,
                    user_label=label,
                )
                speech = self._state_feedback_reply(label, continue_action=has_action)
                self._emit_message(
                    events,
                    factory,
                    speech=speech,
                    display=speech,
                    emotion="attentive",
                    motion="small_nod",
                    priority="normal",
                )
                if has_action:
                    return False
                events.append(
                    factory.emit(
                        "turn.completed",
                        {
                            "status": "state_feedback",
                            "state_query_id": "room_light",
                            "user_label": label,
                        },
                    )
                )
                return True
            if has_action:
                self.pending_state_queries.pop(turn_input.session_id, None)
                events.append(
                    factory.emit(
                        "state_query.feedback_cleared",
                        {
                            "state_query_id": "room_light",
                            "reason": "new_action_without_feedback",
                            "action_id": action_intent.action_id,
                        },
                    )
                )

        direct_label = self._direct_room_light_feedback_label(turn_input.text)
        if not direct_label:
            return False
        self._persist_state_query_feedback(
            events,
            factory,
            turn_input,
            pending={},
            user_label=direct_label,
            feedback_reason="user_reported_room_light_state",
            source_context="state_query",
        )
        speech = self._state_feedback_reply(direct_label, continue_action=has_action)
        self._emit_message(
            events,
            factory,
            speech=speech,
            display=speech,
            emotion="attentive",
            motion="small_nod",
            priority="normal",
        )
        if has_action:
            return False
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "state_feedback",
                    "state_query_id": "room_light",
                    "user_label": direct_label,
                    "direct_feedback": True,
                },
            )
        )
        return True

    def _persist_state_query_feedback(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        pending: dict[str, Any],
        user_label: str,
        feedback_reason: str = "",
        source_context: str = "",
        current_observation: dict[str, Any] | None = None,
        emit_observation: bool = True,
    ) -> dict[str, Any]:
        observation = current_observation
        if observation is None:
            observation = self._call_tool(
                events,
                factory,
                "environment.observe",
                lambda: self.tools.environment_observe(
                    turn_input,
                    reason="state_feedback",
                ),
            )
            emit_observation = True
        if emit_observation:
            events.append(
                factory.emit(
                    "observation.received",
                    {
                        "observation_ref": observation.get("observation_ref"),
                        "observation_source": observation.get("observation_source"),
                        "facts": observation.get("facts", {}),
                        "state_query_id": "room_light",
                        "after_tool": "state_query.feedback",
                    },
                )
            )
        if not pending:
            environment = (
                observation.get("environment")
                if isinstance(observation.get("environment"), dict)
                else {}
            )
            pending = self._build_state_query_pending(
                turn_input,
                self._room_light_from_observation(observation),
                environment,
                feedback_reason=feedback_reason or "user_reported_room_light_state",
                source_context=source_context or "state_query",
            )
        payload = self._build_state_query_feedback_payload(
            turn_input,
            pending=pending,
            user_label=user_label,
            current_observation=observation,
            feedback_reason=feedback_reason,
            source_context=source_context,
        )
        events.append(
            factory.emit(
                "state_query.feedback_detected",
                {
                    "state_query_id": "room_light",
                    "target": "room_light",
                    "user_label": user_label,
                    "source_context": payload.get("source_context"),
                    "feedback_reason": payload.get("feedback_reason"),
                    "continued_as_input": self._looks_like_home_action_command(
                        turn_input.text
                    )
                    and detect_home_action_intent(turn_input.text) is not None,
                },
            )
        )
        result = self._call_tool(
            events,
            factory,
            "state_query.feedback",
            lambda: self.tools.state_query_feedback(turn_input, payload),
        )
        events.append(
            factory.emit(
                "state_query.feedback_saved",
                {
                    "state_query_id": "room_light",
                    "target": "room_light",
                    "status": result.get("status"),
                    "ok": bool(result.get("ok", result.get("status") in {"accepted", "duplicate"})),
                    "feedback_id": result.get("feedback_id"),
                    "duplicate": bool(result.get("duplicate")),
                    "user_label": user_label,
                    "source_context": payload.get("source_context"),
                    "feedback_reason": payload.get("feedback_reason"),
                    "idempotency_key": payload.get("idempotency_key"),
                },
            )
        )
        return {"payload": payload, "result": result}

    def _build_state_query_feedback_payload(
        self,
        turn_input: TurnInput,
        *,
        pending: dict[str, Any],
        user_label: str,
        current_observation: dict[str, Any],
        feedback_reason: str = "",
        source_context: str = "",
    ) -> dict[str, Any]:
        environment = (
            current_observation.get("environment")
            if isinstance(current_observation.get("environment"), dict)
            else {}
        )
        current_room_light = self._room_light_from_observation(current_observation)
        return {
            "type": "state_query_feedback",
            "target": "room_light",
            "state_query_id": "room_light",
            "authority": "user_feedback",
            "source": self.source,
            "workflow_version": "thought-core-state-query-feedback-v1",
            "feedback_reason": str(
                feedback_reason
                or pending.get("feedback_reason")
                or "user_correction_after_state_query"
            ),
            "source_context": str(source_context or pending.get("source_context") or "state_query"),
            "action_id": str(pending.get("action_id") or ""),
            "issue_id": str(pending.get("issue_id") or ""),
            "expected_state": str(pending.get("expected_state") or ""),
            "snapshot_id": str(pending.get("snapshot_id") or ""),
            "current_snapshot_id": str(environment.get("snapshot_id") or ""),
            "predicted_state": str(pending.get("predicted_state") or "unknown"),
            "predicted_confidence_label": str(
                pending.get("confidence_label")
                or pending.get("predicted_confidence_label")
                or ""
            ),
            "user_label": user_label,
            "user_text": turn_input.text,
            "idempotency_key": self._state_query_feedback_idempotency_key(
                turn_input,
                pending,
                user_label,
            ),
            "pending": {
                **pending,
                "current_state_query": {
                    "state": current_room_light.get("state") if current_room_light else "",
                    "confidence_label": (
                        current_room_light.get("confidence_label")
                        if current_room_light
                        else ""
                    ),
                    "answer_hint": (
                        current_room_light.get("answer_hint")
                        if current_room_light
                        else ""
                    ),
                    "authority": (
                        current_room_light.get("authority")
                        if current_room_light
                        else ""
                    ),
                },
            },
        }

    def _state_query_feedback_idempotency_key(
        self,
        turn_input: TurnInput,
        pending: dict[str, Any],
        user_label: str,
    ) -> str:
        session = self._idempotency_part(turn_input.session_id or "session")
        snapshot = self._idempotency_part(
            str(pending.get("snapshot_id") or turn_input.turn_id or "snapshot")
        )
        label = self._idempotency_part(user_label)
        return f"state-query-feedback:{session}:{snapshot}:{label}"[:200]

    def _idempotency_part(self, value: str) -> str:
        text = value.replace(" ", "_").replace("　", "_").strip()
        return "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "."}) or "na"

    def _remember_state_query_pending(
        self,
        turn_input: TurnInput,
        pending: dict[str, Any],
    ) -> None:
        if pending.get("state_query_id") != "room_light":
            return
        self.pending_state_queries[turn_input.session_id] = dict(pending)

    def _state_query_pending_is_expired(self, pending: dict[str, Any]) -> bool:
        created_at = str(pending.get("created_at") or "").strip()
        if not created_at:
            return False
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - created.astimezone(UTC)).total_seconds()
        return age > 120

    def _room_light_feedback_label(
        self,
        text: str,
        *,
        pending: dict[str, Any] | None = None,
    ) -> str:
        normalized = self._normalize_text(text)
        explicit = self._explicit_room_light_state_label(normalized)
        if explicit:
            return explicit
        if not pending:
            return ""
        predicted = str(pending.get("predicted_state") or "").lower()
        yes_markers = (
            "はい",
            "うん",
            "そう",
            "その通り",
            "合ってる",
            "あってる",
            "正しい",
            "せや",
            "yes",
            "ok",
        )
        no_markers = (
            "いいえ",
            "いや",
            "違う",
            "ちがう",
            "逆",
            "ちがいます",
            "違います",
            "no",
        )
        if any(marker in normalized for marker in yes_markers):
            return predicted if predicted in {"on", "off", "daylight"} else "on"
        if any(marker in normalized for marker in no_markers):
            if predicted == "on":
                return "off"
            if predicted == "off":
                return "on"
            return "unknown"
        return ""

    def _direct_room_light_feedback_label(self, text: str) -> str:
        normalized = self._normalize_text(text)
        label = self._explicit_room_light_state_label(normalized)
        if not label:
            return ""
        if not self._mentions_room_light(normalized):
            return ""
        question_markers = ("?", "？", "かな", "教えて", "確認して", "どう")
        if any(marker in normalized for marker in question_markers):
            return ""
        command_markers = (
            "消して",
            "消す",
            "消せ",
            "消しといて",
            "消灯して",
            "オフにして",
            "切って",
            "つけて",
            "点けて",
            "付けて",
            "つける",
            "オンにして",
            "入れて",
        )
        state_cues = (
            "今",
            "現在",
            "実際",
            "状態",
            "もう",
            "まだ",
            "です",
            "だよ",
            "だね",
            "なって",
            "いる",
            "います",
            "中",
        )
        if any(marker in normalized for marker in command_markers) and not any(
            cue in normalized for cue in state_cues
        ):
            return ""
        return label

    def _explicit_room_light_state_label(self, normalized: str) -> str:
        if any(marker in normalized for marker in ("日光", "外光", "太陽光", "昼光")):
            return "daylight"
        if any(marker in normalized for marker in ("わからない", "分からない", "不明", "不確か")):
            return "unknown"
        off_markers = (
            "ついてない",
            "点いてない",
            "付いてない",
            "消えてる",
            "消えています",
            "消えてます",
            "消灯状態",
            "消灯中",
            "消灯してる",
            "消灯しています",
            "暗い",
            "オフです",
            "offです",
            "offだ",
            "切れてる",
        )
        if any(marker in normalized for marker in off_markers):
            return "off"
        on_markers = (
            "ついてる",
            "点いてる",
            "付いてる",
            "ついています",
            "点いてます",
            "ついてます",
            "点灯状態",
            "点灯中",
            "点灯してる",
            "点灯しています",
            "明るい",
            "オンです",
            "onです",
            "onだ",
        )
        if any(marker in normalized for marker in on_markers):
            return "on"
        return ""

    def _mentions_room_light(self, normalized: str) -> bool:
        return any(
            marker in normalized
            for marker in ("電気", "照明", "ライト", "明かり", "明り", "部屋", "リビング")
        )

    def _looks_like_home_action_command(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        command_markers = (
            "して",
            "お願い",
            "おねがい",
            "つけて",
            "点けて",
            "付けて",
            "つける",
            "点ける",
            "付ける",
            "オンに",
            "入れて",
            "入れる",
            "消して",
            "消す",
            "消せ",
            "オフに",
            "切って",
            "切る",
            "開けて",
            "開ける",
            "閉めて",
            "閉める",
            "止めて",
            "止める",
            "戻して",
            "戻す",
            "動かして",
            "動かす",
            "一時停止",
            "起動",
        )
        return any(marker in normalized for marker in command_markers)

    def _normalize_text(self, text: str) -> str:
        return text.replace(" ", "").replace("　", "").lower()

    def _state_feedback_reply(self, user_label: str, *, continue_action: bool) -> str:
        label_text = {
            "on": "ついてる",
            "off": "消えてる",
            "daylight": "日光の影響がある",
            "unknown": "判断しづらい",
        }.get(user_label, user_label)
        suffix = "そのうえで操作も続けるね。" if continue_action else "学習用の材料として残したよ。"
        return f"なるほど、実際は{label_text}んだね。{suffix}"

    def _handle_pending_confirmation_if_needed(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> bool:
        pending = self.pending_confirmations.get(turn_input.session_id)
        if not pending:
            return False
        if self._is_confirmation_cancel(turn_input.text):
            self.pending_confirmations.pop(turn_input.session_id, None)
            speech = "了解、さっきの家電操作は実行せずに取り消しました。"
            events.append(
                factory.emit(
                    "action.confirmation_cancelled",
                    {"action": pending.get("action", {}), "speech": speech},
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
                    {"status": "cancelled", "action": pending.get("action", {})},
                )
            )
            return True
        if not self._is_confirmation_reply(turn_input.text):
            return False
        action = dict(pending.get("action") or {})
        if not action:
            self.pending_confirmations.pop(turn_input.session_id, None)
            return False
        token = str(pending.get("confirmation_token") or action.get("confirmation_token") or "")
        action["confirmed"] = True
        if token:
            action["confirmation_token"] = token
        events.append(
            factory.emit(
                "action.confirmed",
                {
                    "action": action,
                    "confirmation_token_present": bool(token),
                },
            )
        )
        self._emit_message(
            events,
            factory,
            speech="OK、続きやるね。",
            display="確認しました",
            emotion="confident",
            motion="nod",
            priority="immediate",
        )
        execute_result = self._call_tool(
            events,
            factory,
            "home.execute",
            lambda: self.tools.home_execute(turn_input, action),
        )
        if execute_result.get("status") == "confirmation_required":
            if execute_result.get("confirmation_token"):
                action["confirmation_token"] = execute_result.get("confirmation_token")
                self._remember_confirmation(turn_input, action)
            speech = (
                "確認の有効期限が切れたみたいです。まだ実行していません。"
                "実行してよければ、もう一度「お願い」か「OK」と言ってください。"
            )
            events.append(
                factory.emit(
                    "action.confirmation_required",
                    {
                        "action": action,
                        "confirmation_token_present": bool(
                            action.get("confirmation_token")
                        ),
                        "speech": speech,
                    },
                )
            )
            self._emit_message(
                events,
                factory,
                speech=speech,
                display=speech,
                emotion="focused",
                motion="small_nod",
                priority="immediate",
            )
            events.append(
                factory.emit(
                    "turn.completed",
                    {
                        "status": "confirmation_required",
                        "action": action,
                        "execute_status": execute_result.get("status"),
                        "confirmation_token_present": bool(
                            action.get("confirmation_token")
                        ),
                    },
                )
            )
            return True

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
                    "attempt": execute_result.get("attempt", 1),
                    "confirmed": True,
                },
            )
        )
        review = self._review_action_result(action, after_observation, execute_result)
        events.append(factory.emit("action.reviewed", review))

        if review["status"] == "succeeded":
            self.pending_confirmations.pop(turn_input.session_id, None)
            messages = self._home_action_messages(action)
            post_action_feedback = self._post_action_room_light_feedback(
                turn_input,
                action,
                after_observation,
            )
            success_speech = (
                str(execute_result.get("speak") or execute_result.get("message") or "")
                or messages["success_speech"]
            )
            success_display = success_speech or messages["success_display"]
            if post_action_feedback["speech_suffix"]:
                success_speech = f"{success_speech} {post_action_feedback['speech_suffix']}"
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
            pending_feedback = post_action_feedback["pending"]
            if pending_feedback:
                self._remember_state_query_pending(turn_input, pending_feedback)
                events.append(factory.emit("state_query.feedback_pending", pending_feedback))
            post_action_feedback_saved = False
            if not pending_feedback:
                post_action_feedback_saved = self._save_post_action_room_light_learning(
                    events,
                    factory,
                    turn_input,
                    action,
                    after_observation,
                )
            events.append(
                factory.emit(
                    "turn.completed",
                    {
                        "status": "success",
                        "attempts": execute_result.get("attempt", 1),
                        "action": action,
                        "execute_status": execute_result.get("status"),
                        "confirmed": True,
                        "room_light_wait_matched": post_action_feedback["wait_matched"],
                        "post_action_feedback_pending": bool(pending_feedback),
                        "post_action_feedback_saved": post_action_feedback_saved,
                        "pending_state_query_id": (
                            pending_feedback.get("state_query_id")
                            if pending_feedback
                            else ""
                        ),
                        "pending_state_query_json": (
                            json.dumps(
                                pending_feedback,
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            if pending_feedback
                            else ""
                        ),
                    },
                )
            )
            return True

        if self._begin_pending_action_review(
            events,
            factory,
            turn_input,
            action=action,
            execute_result=execute_result,
            review=review,
            observations_done=1,
            execute_attempts=int(execute_result.get("attempt") or 1),
            confirmed=True,
        ):
            self.pending_confirmations.pop(turn_input.session_id, None)
            return True

        retryable = bool(execute_result.get("retryable", True))
        if retryable:
            self.pending_confirmations[turn_input.session_id] = {
                "action": action,
                "confirmation_token": action.get("confirmation_token"),
            }
        else:
            self.pending_confirmations.pop(turn_input.session_id, None)
        messages = self._home_action_messages(action)
        events.append(
            factory.emit(
                "feedback.requested",
                {
                    "reason": "confirmed_action_failed",
                    "speech": messages["feedback_speech"],
                    "display": "家電の状態確認が必要です",
                    "last_execute_status": execute_result.get("status"),
                    "confirmed": True,
                },
            )
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "needs_feedback",
                    "attempts": execute_result.get("attempt", 1),
                    "action": action,
                    "confirmed": True,
                },
            )
        )
        return True

    def _handle_pending_action_review_if_needed(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> bool:
        pending = self.pending_action_reviews.get(turn_input.session_id)
        if not pending:
            return False
        if self._is_confirmation_cancel(turn_input.text):
            self.pending_action_reviews.pop(turn_input.session_id, None)
            action = dict(pending.get("action") or {})
            speech = "了解、さっきの実行後確認はここで止めます。"
            events.append(
                factory.emit(
                    "action.review_cancelled",
                    {"action": action, "speech": speech},
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
                    {"status": "review_cancelled", "action": action},
                )
            )
            return True

        action = dict(pending.get("action") or {})
        if not action:
            self.pending_action_reviews.pop(turn_input.session_id, None)
            return False
        policy = self._action_review_policy(action)
        observations_done = int(pending.get("observations_done") or 0) + 1
        execute_attempts = int(pending.get("execute_attempts") or 1)
        execute_result = (
            pending.get("execute_result")
            if isinstance(pending.get("execute_result"), dict)
            else {}
        )
        observation = self._call_tool(
            events,
            factory,
            "environment.observe",
            lambda: self.tools.environment_observe(turn_input, reason="action_review"),
        )
        events.append(
            factory.emit(
                "observation.received",
                {
                    "observation_ref": observation.get("observation_ref"),
                    "observation_source": observation.get("observation_source"),
                    "facts": observation.get("facts", {}),
                    "review_for_action": action.get("action_id"),
                    "observations_done": observations_done,
                    "execute_attempts": execute_attempts,
                },
            )
        )
        review = self._review_action_result(action, observation, execute_result)
        review["observations_done"] = observations_done
        review["execute_attempts"] = execute_attempts
        review["settle_ms"] = policy["settle_ms"]
        events.append(factory.emit("action.reviewed", review))

        if review["status"] == "succeeded":
            self.pending_action_reviews.pop(turn_input.session_id, None)
            messages = self._home_action_messages(action)
            speech = f"確認できました。{messages['success_speech']}"
            self._emit_message(
                events,
                factory,
                speech=speech,
                display=speech,
                emotion="satisfied",
                motion="small_nod",
                priority="normal",
            )
            events.append(
                factory.emit(
                    "turn.completed",
                    {
                        "status": "success",
                        "action": action,
                        "review_status": review["status"],
                        "observations_done": observations_done,
                        "execute_attempts": execute_attempts,
                    },
                )
            )
            return True

        if observations_done < policy["observation_attempts"]:
            pending["observations_done"] = observations_done
            pending["last_review"] = review
            pending["updated_at"] = datetime.now(UTC).isoformat()
            speech = self._pending_review_speech(action, policy, observations_done)
            events.append(
                factory.emit(
                    "action.review_pending",
                    {
                        "action": action,
                        "review": review,
                        "observations_done": observations_done,
                        "observation_attempts": policy["observation_attempts"],
                        "settle_ms": policy["settle_ms"],
                    },
                )
            )
            self._emit_message(
                events,
                factory,
                speech=speech,
                display=speech,
                emotion="focused",
                motion="think",
                priority="normal",
            )
            self._write_short_memory(
                events,
                factory,
                turn_input,
                action=action,
                status="review_observation_pending",
                retry_scope="review",
                policy=policy,
                progress={
                    "observations_done": observations_done,
                    "execute_attempts": execute_attempts,
                },
                review=review,
            )
            events.append(
                factory.emit(
                    "turn.completed",
                    {
                        "status": "verification_pending",
                        "action": action,
                        "review_status": review["status"],
                        "observations_done": observations_done,
                        "observation_attempts": policy["observation_attempts"],
                        "settle_ms": policy["settle_ms"],
                    },
                )
            )
            return True

        if self._can_retry_review_action(action, pending, policy):
            return self._retry_pending_action_review(
                events,
                factory,
                turn_input,
                action=action,
                pending=pending,
                policy=policy,
                last_review=review,
            )

        self.pending_action_reviews.pop(turn_input.session_id, None)
        messages = self._home_action_messages(action)
        speech = (
            f"{messages['feedback_speech']} "
            "何回か環境を見直しましたが、期待した状態を確認できませんでした。"
        )
        events.append(
            factory.emit(
                "feedback.requested",
                {
                    "reason": "review_exhausted",
                    "speech": speech,
                    "display": "実行後確認に失敗しました",
                    "action": action,
                    "last_review": review,
                    "observations_done": observations_done,
                    "execute_attempts": execute_attempts,
                },
            )
        )
        self._write_short_memory(
            events,
            factory,
            turn_input,
            action=action,
            status="review_retry_exhausted",
            retry_scope="review",
            policy=policy,
            progress={
                "observations_done": observations_done,
                "execute_attempts": execute_attempts,
            },
            review=review,
        )
        self._emit_message(
            events,
            factory,
            speech=speech,
            display=speech,
            emotion="concerned",
            motion="look_back",
            priority="normal",
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "needs_feedback",
                    "action": action,
                    "review_status": review["status"],
                    "observations_done": observations_done,
                    "execute_attempts": execute_attempts,
                },
            )
        )
        return True

    def _retry_pending_action_review(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        action: dict[str, Any],
        pending: dict[str, Any],
        policy: dict[str, int],
        last_review: dict[str, Any],
    ) -> bool:
        execute_attempts = int(pending.get("execute_attempts") or 1) + 1
        self._write_short_memory(
            events,
            factory,
            turn_input,
            action=action,
            status="review_retry_scheduled",
            retry_scope="review",
            policy=policy,
            progress={
                "execute_attempts": execute_attempts,
                "previous_review_status": last_review.get("status"),
            },
            review=last_review,
        )
        events.append(
            factory.emit(
                "action.retrying",
                {
                    "action": action,
                    "execute_attempts": execute_attempts,
                    "last_review": last_review,
                },
            )
        )
        self._emit_message(
            events,
            factory,
            speech="反映が確認できなかったので、もう一度だけ試してから見直します。",
            display="再実行して確認します",
            emotion="focused",
            motion="nod",
            priority="immediate",
        )
        execute_result = self._call_tool(
            events,
            factory,
            "home.execute",
            lambda: self.tools.home_execute(turn_input, action),
        )
        observation = self._call_tool(
            events,
            factory,
            "environment.observe",
            lambda: self.tools.environment_observe(turn_input, reason="after_action"),
        )
        events.append(
            factory.emit(
                "observation.received",
                {
                    "observation_ref": observation.get("observation_ref"),
                    "observation_source": observation.get("observation_source"),
                    "facts": observation.get("facts", {}),
                    "after_tool": "home.execute",
                    "attempt": execute_attempts,
                    "review_retry": True,
                },
            )
        )
        review = self._review_action_result(action, observation, execute_result)
        review["observations_done"] = 1
        review["execute_attempts"] = execute_attempts
        review["settle_ms"] = policy["settle_ms"]
        events.append(factory.emit("action.reviewed", review))
        if review["status"] == "succeeded":
            self.pending_action_reviews.pop(turn_input.session_id, None)
            messages = self._home_action_messages(action)
            speech = f"再実行後に確認できました。{messages['success_speech']}"
            self._emit_message(
                events,
                factory,
                speech=speech,
                display=speech,
                emotion="satisfied",
                motion="small_nod",
                priority="normal",
            )
            events.append(
                factory.emit(
                    "turn.completed",
                    {
                        "status": "success",
                        "action": action,
                        "review_status": review["status"],
                        "observations_done": 1,
                        "execute_attempts": execute_attempts,
                    },
                )
            )
            return True

        self._begin_pending_action_review(
            events,
            factory,
            turn_input,
            action=action,
            execute_result=execute_result,
            review=review,
            observations_done=1,
            execute_attempts=execute_attempts,
        )
        return True

    def _begin_pending_action_review(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        action: dict[str, Any],
        execute_result: dict[str, Any],
        review: dict[str, Any],
        observations_done: int,
        execute_attempts: int,
        confirmed: bool = False,
    ) -> bool:
        if review["status"] in {"succeeded", "execute_failed"}:
            return False
        if not self._execution_was_accepted(execute_result):
            return False
        policy = self._action_review_policy(action)
        self._write_short_memory(
            events,
            factory,
            turn_input,
            action=action,
            status="review_budget_opened",
            retry_scope="review",
            policy=policy,
            progress={
                "observations_done": observations_done,
                "execute_attempts": execute_attempts,
                "confirmed": confirmed,
            },
            review=review,
        )
        self.pending_action_reviews[turn_input.session_id] = {
            "action": dict(action),
            "execute_result": dict(execute_result),
            "last_review": dict(review),
            "observations_done": observations_done,
            "execute_attempts": execute_attempts,
            "confirmed": confirmed,
            "policy": dict(policy),
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        speech = self._pending_review_speech(action, policy, observations_done)
        events.append(
            factory.emit(
                "action.review_pending",
                {
                    "action": action,
                    "review": review,
                    "observations_done": observations_done,
                    "observation_attempts": policy["observation_attempts"],
                    "settle_ms": policy["settle_ms"],
                    "execute_attempts": execute_attempts,
                    "confirmed": confirmed,
                },
            )
        )
        self._emit_message(
            events,
            factory,
            speech=speech,
            display=speech,
            emotion="focused",
            motion="think",
            priority="normal",
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "verification_pending",
                    "action": action,
                    "review_status": review["status"],
                    "observations_done": observations_done,
                    "observation_attempts": policy["observation_attempts"],
                    "settle_ms": policy["settle_ms"],
                    "execute_attempts": execute_attempts,
                    "confirmed": confirmed,
                },
            )
        )
        return True

    def _review_action_result(
        self,
        action: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = str(action.get("action_id") or "")
        expected_state = str(
            action.get("expected_state") or execute_result.get("expected_state") or ""
        ).strip()
        status = str(execute_result.get("status") or "")
        if not self._execution_was_accepted(execute_result):
            return {
                "status": "execute_failed",
                "reason": str(execute_result.get("error") or status or "execute_failed"),
                "action_id": action_id,
                "expected_state": expected_state,
            }

        device_review = self._review_device_state(action, observation, expected_state)
        if device_review:
            return device_review

        room_light_review = self._review_room_light_state(action, observation, expected_state)
        if room_light_review:
            return room_light_review

        wait_result = self._wait_result_from_observation(observation)
        if wait_result and self._as_bool(wait_result.get("matched")) is False:
            return {
                "status": "pending",
                "reason": "environment_wait_timeout",
                "action_id": action_id,
                "expected_state": expected_state,
                "wait_result": wait_result,
            }

        return {
            "status": "pending",
            "reason": "accepted_but_unverified",
            "action_id": action_id,
            "expected_state": expected_state,
            "bridge_status": status,
        }

    def _review_device_state(
        self,
        action: dict[str, Any],
        observation: dict[str, Any],
        expected_state: str,
    ) -> dict[str, Any]:
        targets = self._action_targets(action)
        if not targets:
            return {}
        facts = observation.get("facts", {})
        devices = facts.get("devices", []) if isinstance(facts, dict) else []
        if not isinstance(devices, list):
            return {}
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = str(device.get("id") or "")
            if device_id not in targets:
                continue
            state = str(device.get("state") or "").strip()
            evidence = {
                "source": device.get("source") or "environment.observe",
                "device_id": device_id,
                "state": state,
                "stale": device.get("stale"),
                "updated_at": device.get("updated_at"),
            }
            if self._as_bool(device.get("stale")) is True:
                return {
                    "status": "pending",
                    "reason": "device_state_stale",
                    "action_id": action.get("action_id"),
                    "expected_state": expected_state,
                    "evidence": evidence,
                }
            if expected_state and state == expected_state:
                return {
                    "status": "succeeded",
                    "reason": "device_state_matched",
                    "action_id": action.get("action_id"),
                    "expected_state": expected_state,
                    "evidence": evidence,
                }
            if state:
                return {
                    "status": "mismatch",
                    "reason": "device_state_mismatch",
                    "action_id": action.get("action_id"),
                    "expected_state": expected_state,
                    "evidence": evidence,
                }
        return {}

    def _review_room_light_state(
        self,
        action: dict[str, Any],
        observation: dict[str, Any],
        expected_state: str,
    ) -> dict[str, Any]:
        if action.get("target") not in {"light", "living_room_light"}:
            return {}
        if expected_state not in {"on", "off"}:
            return {}
        room_light = self._room_light_from_observation(observation)
        if not room_light:
            return {}
        evidence = {
            "source": room_light.get("authority") or room_light.get("source"),
            "state": room_light.get("state"),
            "confidence_label": room_light.get("confidence_label"),
            "stale": room_light.get("stale"),
            "observed_at": room_light.get("observed_at"),
            "updated_at": room_light.get("updated_at"),
        }
        if room_light.get("available") is False or self._as_bool(room_light.get("stale")):
            return {
                "status": "pending",
                "reason": "room_light_unavailable_or_stale",
                "action_id": action.get("action_id"),
                "expected_state": expected_state,
                "evidence": evidence,
            }
        state = str(room_light.get("state") or "unknown").lower()
        confidence = str(room_light.get("confidence_label") or "").lower()
        confidence_low = confidence in {"", "low", "very_low", "unknown"}
        if state == expected_state and not confidence_low:
            return {
                "status": "succeeded",
                "reason": "room_light_state_matched",
                "action_id": action.get("action_id"),
                "expected_state": expected_state,
                "evidence": evidence,
            }
        if state in {"on", "off"} and state != expected_state and not confidence_low:
            return {
                "status": "mismatch",
                "reason": "room_light_state_mismatch",
                "action_id": action.get("action_id"),
                "expected_state": expected_state,
                "evidence": evidence,
            }
        return {
            "status": "pending",
            "reason": "room_light_low_confidence",
            "action_id": action.get("action_id"),
            "expected_state": expected_state,
            "evidence": evidence,
        }

    def _action_review_policy(self, action: dict[str, Any]) -> dict[str, int]:
        action_id = str(action.get("action_id") or "")
        profiles = {
            "light_on": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
            "light_off": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
            "fan_on": {"settle_ms": 2500, "observation_attempts": 2, "auto_retries": 1},
            "fan_off": {"settle_ms": 2500, "observation_attempts": 2, "auto_retries": 1},
            "aircon_on": {"settle_ms": 8000, "observation_attempts": 3, "auto_retries": 0},
            "aircon_off": {"settle_ms": 8000, "observation_attempts": 3, "auto_retries": 0},
            "door_open": {"settle_ms": 5000, "observation_attempts": 3, "auto_retries": 0},
            "door_close": {"settle_ms": 5000, "observation_attempts": 3, "auto_retries": 0},
            "door_stop": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 0},
            "vacuum_start": {"settle_ms": 10000, "observation_attempts": 4, "auto_retries": 0},
            "vacuum_return": {"settle_ms": 10000, "observation_attempts": 4, "auto_retries": 0},
            "vacuum_pause": {"settle_ms": 3000, "observation_attempts": 2, "auto_retries": 0},
        }
        policy = dict(
            profiles.get(
                action_id,
                {"settle_ms": 3000, "observation_attempts": 2, "auto_retries": 0},
            )
        )
        expected_effect = action.get("expected_effect")
        if isinstance(expected_effect, dict):
            policy["settle_ms"] = self._int_value(
                expected_effect.get("settle_ms")
                or expected_effect.get("settle_time_ms")
                or expected_effect.get("verification_delay_ms"),
                policy["settle_ms"],
            )
            policy["observation_attempts"] = self._int_value(
                expected_effect.get("observation_attempts")
                or expected_effect.get("verification_attempts"),
                policy["observation_attempts"],
            )
        if action.get("confirm_required"):
            policy["auto_retries"] = 0
        policy["settle_ms"] = max(0, policy["settle_ms"])
        policy["observation_attempts"] = max(1, policy["observation_attempts"])
        policy["auto_retries"] = max(0, policy["auto_retries"])
        return policy

    def _pending_review_speech(
        self,
        action: dict[str, Any],
        policy: dict[str, int],
        observations_done: int,
    ) -> str:
        phrase = str(
            action.get("pre_action_phrase")
            or action.get("target_name")
            or action.get("action_id")
            or "この操作"
        )
        seconds = max(1, round(policy["settle_ms"] / 1000))
        remaining = max(0, policy["observation_attempts"] - observations_done)
        tail = (
            f"あと{remaining}回くらい見直します。"
            if remaining
            else "次で判断します。"
        )
        return (
            f"{phrase}の操作は送信しました。反映には{seconds}秒くらいかかる見込みです。"
            f"まだ環境で結果を確認しきれていないので、少し待ってから確認します。{tail}"
        )

    def _can_retry_review_action(
        self,
        action: dict[str, Any],
        pending: dict[str, Any],
        policy: dict[str, int],
    ) -> bool:
        if action.get("confirm_required"):
            return False
        execute_attempts = int(pending.get("execute_attempts") or 1)
        return execute_attempts <= policy["auto_retries"]

    def _execution_was_accepted(self, execute_result: dict[str, Any]) -> bool:
        status = str(execute_result.get("status") or "")
        if bool(execute_result.get("executed")) or bool(execute_result.get("verified_by_bridge")):
            return True
        return status in {"accepted", "submitted", "duplicate"}

    def _action_targets(self, action: dict[str, Any]) -> set[str]:
        target = action.get("target")
        target_aliases = action.get("target_aliases")
        targets = {str(target)} if target is not None else set()
        if isinstance(target_aliases, list):
            targets.update(str(item) for item in target_aliases)
        return {target for target in targets if target}

    def _wait_result_from_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        environment = observation.get("environment")
        if isinstance(environment, dict) and isinstance(environment.get("wait_result"), dict):
            return environment["wait_result"]
        return {}

    def _int_value(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _write_short_memory(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        action: dict[str, Any],
        status: str,
        retry_scope: str,
        progress: dict[str, Any],
        review: dict[str, Any],
        policy: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        retry_budget = {
            "scope": retry_scope,
            "status": status,
            "max_execute_attempts": self.max_execute_attempts,
            "observation_attempts": int((policy or {}).get("observation_attempts") or 0),
            "auto_retries": int((policy or {}).get("auto_retries") or 0),
            "settle_ms": int((policy or {}).get("settle_ms") or 0),
            "progress": dict(progress),
        }
        item = {
            "type": "short_memory",
            "kind": "retry_budget",
            "turn_id": turn_input.turn_id,
            "session_id": turn_input.session_id,
            "action_id": str(action.get("action_id") or ""),
            "target": str(action.get("target") or ""),
            "expected_state": str(action.get("expected_state") or ""),
            "retry_budget": retry_budget,
            "last_review": dict(review),
            "created_at": datetime.now(UTC).isoformat(),
        }
        result = self._call_tool(
            events,
            factory,
            "short_memory.write",
            lambda: self.tools.short_memory_write(turn_input, item),
        )
        events.append(
            factory.emit(
                "short_memory.updated",
                {
                    "kind": item["kind"],
                    "status": status,
                    "action_id": item["action_id"],
                    "retry_budget": retry_budget,
                    "write_status": result.get("status"),
                    "written": bool(result.get("written", result.get("ok", False))),
                },
            )
        )
        return result

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

    def _emit_input_ack(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> None:
        speech = self._input_ack_speech(turn_input)
        events.append(
            factory.emit(
                "input.acknowledged",
                {
                    "text_length": len(turn_input.text),
                    "speech": speech,
                    "streamed": True,
                },
            )
        )
        self._emit_message(
            events,
            factory,
            speech=speech,
            display=speech,
            emotion="attentive",
            motion="small_nod",
            priority="immediate",
        )

    def _input_ack_speech(self, turn_input: TurnInput) -> str:
        text = turn_input.text.replace(" ", "").replace("　", "")
        if self.pending_confirmations.get(turn_input.session_id):
            if self._is_confirmation_reply(text):
                return "うん、確認したよ。"
            if self._is_confirmation_cancel(text):
                return "うん、止めるね。"
            return "うん、確認中の操作があるよ。"
        if self.pending_action_reviews.get(turn_input.session_id):
            return "うん、もう一度見てみるね。"
        pending_state_query = self.pending_state_queries.get(turn_input.session_id)
        if pending_state_query and self._room_light_feedback_label(
            turn_input.text,
            pending=pending_state_query,
        ):
            if self._looks_like_home_action_command(
                turn_input.text
            ) and detect_home_action_intent(turn_input.text) is not None:
                return "うん、状態も受け取って操作も確認するね。"
            return "うん、その状態を覚えるね。"
        if self._direct_room_light_feedback_label(turn_input.text):
            if self._looks_like_home_action_command(
                turn_input.text
            ) and detect_home_action_intent(turn_input.text) is not None:
                return "うん、状態も受け取って操作も確認するね。"
            return "うん、その状態を覚えるね。"
        if detect_room_light_state_query(turn_input.text):
            return "うん、状態を見てみるね。"
        if detect_home_action_intent(turn_input.text) is not None:
            return "うん、操作できるか確認するね。"
        return "うん、聞いたよ。"

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
        environment = (
            observation.get("environment")
            if isinstance(observation.get("environment"), dict)
            else {}
        )
        pending = self._build_state_query_pending(
            turn_input,
            room_light,
            environment,
            feedback_reason="user_correction_after_state_query",
            source_context="state_query",
        )
        self._remember_state_query_pending(turn_input, pending)
        events.append(factory.emit("state_query.feedback_pending", pending))
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
                    "pending_state_query_id": pending.get("state_query_id"),
                    "pending_state_query_json": json.dumps(
                        pending,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
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

    def _remember_confirmation(self, turn_input: TurnInput, action: dict[str, Any]) -> None:
        self.pending_confirmations[turn_input.session_id] = {
            "action": dict(action),
            "confirmation_token": action.get("confirmation_token"),
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _confirmation_prompt(self, action: dict[str, Any]) -> str:
        phrase = str(
            action.get("pre_action_phrase")
            or action.get("target_name")
            or action.get("action_id")
            or "この操作"
        ).strip()
        return (
            f"{phrase}には確認が必要です。まだ実行していません。"
            "実行してよければ「お願い」か「OK」と言ってください。"
        )

    def _is_confirmation_reply(self, text: str) -> bool:
        normalized = text.replace(" ", "").replace("　", "").lower()
        if not normalized:
            return False
        positive_markers = (
            "お願い",
            "おねがい",
            "はい",
            "ok",
            "ｏｋ",
            "オーケー",
            "おっけ",
            "いいよ",
            "実行して",
            "やって",
            "続き",
            "許可",
        )
        return any(marker in normalized for marker in positive_markers)

    def _is_confirmation_cancel(self, text: str) -> bool:
        normalized = text.replace(" ", "").replace("　", "").lower()
        cancel_markers = (
            "キャンセル",
            "やめ",
            "中止",
            "取り消",
            "とりけ",
            "いいえ",
            "だめ",
            "ダメ",
            "しないで",
        )
        return any(marker in normalized for marker in cancel_markers)

    def _save_post_action_room_light_learning(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        action: dict[str, Any],
        observation: dict[str, Any],
    ) -> bool:
        action_id = str(action.get("action_id") or "")
        expected_state = str(action.get("expected_state") or "")
        if action_id not in {"light_on", "light_off"} or expected_state not in {"on", "off"}:
            return False
        environment = (
            observation.get("environment")
            if isinstance(observation.get("environment"), dict)
            else {}
        )
        room_light = self._room_light_from_observation(observation)
        if not room_light:
            return False
        if room_light.get("available") is False or self._as_bool(room_light.get("stale")):
            return False
        state = str(room_light.get("state") or "unknown").lower()
        confidence = str(room_light.get("confidence_label") or "").lower()
        if state != expected_state or confidence in {"", "low", "very_low", "unknown"}:
            return False
        pending = self._build_state_query_pending(
            turn_input,
            room_light,
            environment,
            feedback_reason="action_expected_state_after_light_action",
            source_context="post_light_action",
            action_id=action_id,
            expected_state=expected_state,
            workflow_version="thought-core-post-action-room-light-feedback-v1",
        )
        persisted = self._persist_state_query_feedback(
            events,
            factory,
            turn_input,
            pending=pending,
            user_label=expected_state,
            feedback_reason="action_expected_state_after_light_action",
            source_context="post_light_action",
            current_observation=observation,
            emit_observation=False,
        )
        result = persisted.get("result") if isinstance(persisted, dict) else {}
        return bool(
            isinstance(result, dict)
            and result.get("status") in {"accepted", "accepted_with_warning", "duplicate"}
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
        pending = self._build_state_query_pending(
            turn_input,
            room_light,
            environment,
            feedback_reason="user_correction_after_light_action",
            source_context="post_light_action",
            action_id=action_id,
            expected_state=expected_state,
            workflow_version="thought-core-post-action-room-light-feedback-v1",
        )
        wait_result = environment.get("wait_result")
        pending["wait_result"] = wait_result if isinstance(wait_result, dict) else {}
        return pending

    def _build_state_query_pending(
        self,
        turn_input: TurnInput,
        room_light: dict[str, Any],
        environment: dict[str, Any],
        *,
        feedback_reason: str,
        source_context: str,
        action_id: str = "",
        expected_state: str = "",
        workflow_version: str = "thought-core-state-query-feedback-v1",
    ) -> dict[str, Any]:
        evidence = (
            room_light.get("evidence")
            if isinstance(room_light.get("evidence"), dict)
            else {}
        )
        wait_result = environment.get("wait_result")
        pending = {
            "type": "state_query_pending",
            "state_query_id": "room_light",
            "target": "room_light",
            "feedback_reason": feedback_reason,
            "source_context": source_context,
            "workflow_version": workflow_version,
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
        return pending

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
