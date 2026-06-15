"""Explicit thought loop for the first thought-core contract draft."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from .events import EventFactory, ThoughtEvent
from .input_understanding import (
    InputFrame,
    InputUnderstanding,
    build_input_understanding_from_env,
    describe_input_understanding,
)
from .persona import AssistantPersona, build_persona_from_env, strip_persona_tags
from .responders import (
    TURN_RESPONDER_BOUNDARY,
    EnvironmentTurnResponder,
    TurnResponder,
    describe_responder,
)
from .reasoning import (
    ACTION_REASONER_BOUNDARY,
    ActionReasoner,
    build_action_reasoner_from_env,
    describe_action_reasoner,
)
from .schema import TurnInput
from .tools import (
    ThoughtTools,
    build_tools_from_env,
    detect_home_action_ambiguity,
    detect_home_action_intent,
    detect_home_action_negative_request,
    detect_room_light_state_query,
)


CURRENT_SAFE_CONTEXT_REF_KEYS = {
    "event_id",
    "turn_id",
    "trace_id",
    "observation_id",
    "observation_ref",
    "motion_event_id",
    "stimulus_id",
    "stimulus_instance_id",
    "runtime_result_id",
    "event_journal_entry_id",
    "memory_candidate_id",
}
AUTHORITY_GATED_CONTEXT_REF_KEYS = {
    "driver_result_id",
    "body_schema_snapshot_id",
    "verifier_result_id",
}
SAFE_CONTEXT_REF_KEYS = CURRENT_SAFE_CONTEXT_REF_KEYS | AUTHORITY_GATED_CONTEXT_REF_KEYS
STRUCTURED_CONTEXT_KEYS = {
    "working_memory_context",
    "body_expression_context",
    "prior_result_context",
}
UNSAFE_CONTEXT_KEY_PARTS = {
    "raw",
    "prompt",
    "transcript",
    "provider_payload",
    "screenshot",
    "frame",
    "media",
    "local_path",
    "path",
    "authorization",
    "access_token",
    "secret",
    "password",
    "credential",
}
WORKING_MEMORY_CONTEXT_FIELDS = SAFE_CONTEXT_REF_KEYS | {
    "context_id",
    "observed_at",
    "stale_after",
    "freshness",
    "staleness",
    "uncertainty",
    "repetition",
    "state_authority",
    "authority",
    "confidence",
    "safe_for_thought_core_use",
    "safe_to_act",
    "not_proven",
    "must_not_imply",
}
DRIVER_RESULT_AUTHORITIES = {
    "motion_driver_result",
    "motion_driver_result_contract",
    "motion_driver_result.v0",
    "driver_result_contract",
}
CONTEXT_VALUE_MAX_CHARS = 180


class _EventBuffer(list[ThoughtEvent]):
    def __init__(self, event_sink: Callable[[ThoughtEvent], None] | None = None) -> None:
        super().__init__()
        self._event_sink = event_sink

    def append(self, event: ThoughtEvent) -> None:
        super().append(event)
        if self._event_sink is not None:
            self._event_sink(event)


class ThoughtLoop:
    def __init__(
        self,
        tools: ThoughtTools | None = None,
        *,
        max_execute_attempts: int = 1,
        source: str = "thought-core",
        responder: TurnResponder | None = None,
        action_reasoner: ActionReasoner | None = None,
        input_understanding: InputUnderstanding | None = None,
        persona: AssistantPersona | None = None,
        llm_visible_speech: bool | None = None,
        require_llm_visible_speech: bool | None = None,
    ) -> None:
        self.tools = tools or build_tools_from_env()
        self.max_execute_attempts = 1
        self.source = source
        self.responder = responder or EnvironmentTurnResponder.from_env()
        self.action_reasoner = action_reasoner or build_action_reasoner_from_env()
        self.input_understanding = (
            input_understanding or build_input_understanding_from_env()
        )
        self.persona = persona or build_persona_from_env()
        self.pending_confirmations: dict[str, dict[str, Any]] = {}
        self.pending_action_reviews: dict[str, dict[str, Any]] = {}
        self.pending_state_queries: dict[str, dict[str, Any]] = {}
        self.recent_speech_by_session: dict[str, list[str]] = {}
        self.recent_speech_by_issue: dict[str, list[str]] = {}
        self._active_session_id = ""
        self._active_issue_key = ""
        self._active_turn_input: TurnInput | None = None
        self._active_working_memory_context: dict[str, Any] = {}
        self.llm_visible_speech = (
            _env_bool("THOUGHT_CORE_LLM_VISIBLE_SPEECH_ENABLED", False)
            if llm_visible_speech is None
            else llm_visible_speech
        )
        self.require_llm_visible_speech = (
            _env_bool("THOUGHT_CORE_REQUIRE_LLM_VISIBLE_SPEECH", False)
            if require_llm_visible_speech is None
            else require_llm_visible_speech
        )

    def run(
        self,
        turn: TurnInput | Mapping[str, Any],
        *,
        event_sink: Callable[[ThoughtEvent], None] | None = None,
    ) -> list[ThoughtEvent]:
        turn_input = turn if isinstance(turn, TurnInput) else TurnInput.from_mapping(turn)
        factory = EventFactory(turn_input.turn_id, turn_input.session_id, source=self.source)
        events: list[ThoughtEvent] = _EventBuffer(event_sink)
        previous_active_session_id = self._active_session_id
        previous_active_issue_key = self._active_issue_key
        previous_active_turn_input = self._active_turn_input
        previous_active_working_memory_context = self._active_working_memory_context
        self._active_session_id = turn_input.session_id
        self._active_turn_input = turn_input
        self._active_working_memory_context = {}
        try:
            input_frame = self._understand_input(turn_input)
            self._active_issue_key = self._speech_issue_key(turn_input, input_frame)
            self._emit_input_ack(events, factory, turn_input, input_frame)
            self._emit_input_understood(events, factory, input_frame)
            working_memory_context = self._build_working_memory_context(turn_input)
            self._active_working_memory_context = working_memory_context
            self._emit_context_trace_events(events, factory, working_memory_context)
            memory_context = self._retrieve_memory_context(events, factory, turn_input)
            self._hydrate_pending_action_review_from_memory(
                turn_input,
                memory_context,
                input_frame,
            )
            self._active_issue_key = self._speech_issue_key(turn_input, input_frame)
            if self._handle_pending_confirmation_if_needed(events, factory, turn_input):
                return events
            if input_frame.kind == "state_query":
                self._handle_room_light_state_query(events, factory, turn_input)
                return events
            if input_frame.kind == "environment_status_query":
                self._handle_environment_status_query_turn(
                    events,
                    factory,
                    turn_input,
                    input_frame,
                    memory_context,
                )
                return events
            if input_frame.kind == "audio_check":
                self._handle_audio_check_turn(events, factory, turn_input, input_frame)
                return events
            if input_frame.kind == "motion_request":
                self._handle_motion_request_turn(
                    events,
                    factory,
                    turn_input,
                    input_frame,
                )
                return events
            if self._handle_pending_action_review_if_needed(
                events,
                factory,
                turn_input,
                input_frame,
            ):
                return events
            if self._handle_state_query_feedback_if_needed(
                events,
                factory,
                turn_input,
                input_frame,
            ):
                return events

            if input_frame.kind == "state_query" or detect_room_light_state_query(
                turn_input.text
            ):
                self._handle_room_light_state_query(events, factory, turn_input)
                return events

            negative_action = detect_home_action_negative_request(turn_input.text)
            if negative_action:
                self._handle_home_action_negative_request(
                    events,
                    factory,
                    turn_input,
                    negative_action,
                )
                return events

            action_ambiguity = detect_home_action_ambiguity(turn_input.text)
            if action_ambiguity:
                self._handle_home_action_ambiguity(
                    events,
                    factory,
                    turn_input,
                    action_ambiguity,
                )
                return events

            action_intent = detect_home_action_intent(turn_input.text)
            if action_intent is None:
                self._handle_general_turn(events, factory, turn_input)
                return events

            self._emit_stage_update(
                events,
                factory,
                stage="environment.observe.before_action",
                speech="いまの環境を短く見ています。",
                detail={"reason": "before_action"},
            )
            observation = self._call_tool(
                events,
                factory,
                "environment.observe",
                lambda: self.tools.environment_observe(turn_input, reason="before_action"),
            )
            observation = self._with_memory_context(observation, memory_context)
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
            self._emit_stage_update(
                events,
                factory,
                stage="target_state.generate",
                speech="望む状態を組み立てます。",
                detail={"memory_items": memory_context.get("item_count", 0)},
            )
            target_state = self._imagine_target_state(
                events,
                factory,
                turn_input,
                observation,
            )

            self._emit_stage_update(
                events,
                factory,
                stage="home.preview",
                speech="使える操作を確認しています。",
                detail={"target_state_id": target_state.get("target_state_id")},
            )
            preview = self._call_tool(
                events,
                factory,
                "home.preview",
                lambda: self.tools.home_preview(turn_input, observation),
            )
            action = preview.get("action", {})
            command_plan: dict[str, Any] = {}
            if isinstance(action, dict) and action:
                self._emit_stage_update(
                    events,
                    factory,
                    stage="command.plan",
                    speech="現在との差分から、実行内容を決めます。",
                    detail={"action_id": action.get("action_id")},
                )
                command_plan = self._plan_command(
                    events,
                    factory,
                    turn_input,
                    observation,
                    target_state,
                    preview,
                )
                action = self._attach_reasoning_to_action(
                    action,
                    target_state,
                    command_plan,
                )
                action["memory_context"] = memory_context
            if command_plan.get("status") == "already_satisfied" and action:
                speech = self._already_satisfied_message(action)
                events.append(
                    factory.emit(
                        "action.skipped",
                        {
                            "reason": "target_state_already_satisfied",
                            "action": action,
                            "message": speech,
                            "command_plan": command_plan,
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
                            "reason": "target_state_already_satisfied",
                            "preview_status": preview.get("status"),
                        },
                    )
                )
                return events
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
                if self._should_defer_action_review(action, execute_result):
                    pending_review = self._begin_deferred_action_review(
                        events,
                        factory,
                        turn_input,
                        action=action,
                        execute_result=execute_result,
                        execute_attempts=attempt,
                    )
                    speech = self._action_sent_review_speech(action)
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
                                "status": "verification_pending",
                                "attempts": attempt,
                                "action": action,
                                "execute_status": execute_result.get("status"),
                                "review_deferred": True,
                                "review_delay_ms": pending_review.get("delay_ms", 0),
                                "review_checkpoint_ms": pending_review.get(
                                    "checkpoint_ms",
                                    0,
                                ),
                            },
                        )
                    )
                    return events

                if self._automatic_review_blocked_by_tracking(action, execute_result):
                    self._complete_action_submitted_without_automatic_review(
                        events,
                        factory,
                        action=action,
                        execute_result=execute_result,
                        attempts=attempt,
                        confirmed=False,
                    )
                    return events

                self._emit_stage_update(
                    events,
                    factory,
                    stage="environment.observe.after_action",
                    speech="反映を待って、環境を見直します。",
                    detail={"attempt": attempt, "action_id": action.get("action_id")},
                )
                after_observation = self._call_tool(
                    events,
                    factory,
                    "environment.observe",
                    lambda: self.tools.environment_observe(turn_input, reason="after_action"),
                )
                after_observation = self._with_memory_context(
                    after_observation,
                    memory_context,
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

                self._emit_stage_update(
                    events,
                    factory,
                    stage="action.review",
                    speech="望んだ状態になったか判定します。",
                    detail={"attempt": attempt, "action_id": action.get("action_id")},
                )
                review = self._review_action_result(
                    turn_input,
                    action,
                    after_observation,
                    execute_result,
                )
                events.append(factory.emit("action.reviewed", review))

                if review["status"] == "succeeded":
                    post_action_feedback = self._post_action_room_light_feedback(
                        turn_input,
                        action,
                        after_observation,
                    )
                    response = self._canonical_action_result_response(
                        execute_result,
                        messages,
                        post_action_feedback,
                    )
                    self._emit_message(
                        events,
                        factory,
                        speech=response["speech"],
                        display=response["display"],
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
        finally:
            self._active_session_id = previous_active_session_id
            self._active_issue_key = previous_active_issue_key
            self._active_turn_input = previous_active_turn_input
            self._active_working_memory_context = previous_active_working_memory_context

    def run_dicts(
        self,
        turn: TurnInput | Mapping[str, Any],
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        def _sink(event: ThoughtEvent) -> None:
            if event_sink is not None:
                event_sink(event.to_dict())

        return [event.to_dict() for event in self.run(turn, event_sink=_sink)]

    def _handle_state_query_feedback_if_needed(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        input_frame: InputFrame | None = None,
    ) -> bool:
        action_intent = detect_home_action_intent(turn_input.text)
        has_action = (
            bool(input_frame and input_frame.is_command)
            or self._input_requests_home_action(turn_input, input_frame)
        )
        pending = self.pending_state_queries.get(turn_input.session_id)
        if pending and pending.get("state_query_id") == "room_light":
            label = self._feedback_label_from_input_frame(
                input_frame,
                pending=pending,
            ) or self._room_light_feedback_label(turn_input.text, pending=pending)
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

                persisted = self._persist_state_query_feedback(
                    events,
                    factory,
                    turn_input,
                    pending=pending,
                    user_label=label,
                )
                action = self._action_from_feedback_pending(pending)
                if action and self._resolve_action_feedback(
                    events,
                    factory,
                    turn_input,
                    pending=pending,
                    action=action,
                    user_label=label,
                    persisted=persisted,
                    source_context=str(pending.get("source_context") or "state_query"),
                ):
                    return True
                speech = self._state_feedback_reply(
                    label,
                    continue_action=has_action,
                    saved=self._feedback_persisted_ok(persisted),
                    fallback_saved=self._feedback_fallback_ok(persisted),
                )
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
                                "action_id": (
                                    action_intent.action_id
                                    if action_intent is not None
                                    else input_frame.action_id
                                    if input_frame
                                    else ""
                                ),
                            },
                        )
                    )

        direct_label = self._feedback_label_from_input_frame(
            input_frame,
            pending=None,
        ) or self._direct_room_light_feedback_label(turn_input.text)
        if not direct_label:
            return False
        persisted = self._persist_state_query_feedback(
            events,
            factory,
            turn_input,
            pending={},
            user_label=direct_label,
            feedback_reason="user_reported_room_light_state",
            source_context="state_query",
        )
        speech = self._state_feedback_reply(
            direct_label,
            continue_action=has_action,
            saved=self._feedback_persisted_ok(persisted),
            fallback_saved=self._feedback_fallback_ok(persisted),
        )
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
                    "error": result.get("error"),
                    "detail": result.get("detail"),
                },
            )
        )
        persisted = {"payload": payload, "result": result}
        if not self._feedback_result_ok(result):
            persisted["fallback_result"] = self._write_state_feedback_short_memory(
                events,
                factory,
                turn_input,
                payload=payload,
                result=result,
            )
        return persisted

    def _feedback_persisted_ok(self, persisted: dict[str, Any] | None) -> bool:
        if not isinstance(persisted, dict):
            return False
        result = persisted.get("result")
        return self._feedback_result_ok(result)

    def _feedback_result_ok(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        status = str(result.get("status") or "").lower()
        if result.get("ok") is False:
            return False
        return status in {"accepted", "accepted_with_warning", "duplicate"}

    def _feedback_fallback_ok(self, persisted: dict[str, Any] | None) -> bool:
        if not isinstance(persisted, dict):
            return False
        result = persisted.get("fallback_result")
        if not isinstance(result, dict):
            return False
        status = str(result.get("status") or "").lower()
        return bool(result.get("written", result.get("ok", False))) and status in {
            "ok",
            "accepted",
            "accepted_with_warning",
            "duplicate",
        }

    def _write_state_feedback_short_memory(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        item = {
            "type": "short_memory",
            "kind": "state_query_feedback_fallback",
            "turn_id": turn_input.turn_id,
            "session_id": turn_input.session_id,
            "state_query_id": str(payload.get("state_query_id") or "room_light"),
            "target": str(payload.get("target") or "room_light"),
            "user_label": str(payload.get("user_label") or ""),
            "feedback_reason": str(payload.get("feedback_reason") or ""),
            "source_context": str(payload.get("source_context") or ""),
            "action_id": str(payload.get("action_id") or ""),
            "expected_state": str(payload.get("expected_state") or ""),
            "predicted_state": str(payload.get("predicted_state") or ""),
            "idempotency_key": str(payload.get("idempotency_key") or ""),
            "external_feedback_status": str(result.get("status") or ""),
            "external_feedback_error": str(result.get("error") or ""),
            "summary": "User supplied room_light state feedback; external feedback log write did not succeed, so Thought Core kept a local session fallback.",
            "created_at": datetime.now(UTC).isoformat(),
        }
        fallback_result = self._call_tool(
            events,
            factory,
            "short_memory.write",
            lambda: self.tools.short_memory_write(turn_input, item),
        )
        events.append(
            factory.emit(
                "state_query.feedback_fallback_saved",
                {
                    "state_query_id": item["state_query_id"],
                    "target": item["target"],
                    "user_label": item["user_label"],
                    "source_context": item["source_context"],
                    "feedback_reason": item["feedback_reason"],
                    "idempotency_key": item["idempotency_key"],
                    "external_status": item["external_feedback_status"],
                    "external_error": item["external_feedback_error"],
                    "write_status": fallback_result.get("status"),
                    "written": bool(
                        fallback_result.get("written", fallback_result.get("ok", False))
                    ),
                },
            )
        )
        return fallback_result

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

    def _feedback_label_from_input_frame(
        self,
        input_frame: InputFrame | None,
        *,
        pending: dict[str, Any] | None,
    ) -> str:
        if input_frame is None or input_frame.kind != "state_feedback":
            return ""
        if input_frame.target != "room_light":
            return ""
        label = str(input_frame.asserted_state or "").lower()
        if label not in {"on", "off", "daylight", "unknown"}:
            return ""
        if pending is None and str(input_frame.reason or "") != "direct_room_light_state_report":
            return ""
        return label

    def _room_light_feedback_label(
        self,
        text: str,
        *,
        pending: dict[str, Any] | None = None,
    ) -> str:
        if self._is_room_light_state_question(text):
            return ""
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
        if self._is_room_light_state_question(text):
            return ""
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

    def _is_room_light_state_question(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        if not normalized or not self._mentions_room_light(normalized):
            return False
        if self._looks_like_home_action_command(text):
            return False
        question_markers = (
            "?",
            "？",
            "でしょう",
            "ですか",
            "ますか",
            "教えて",
            "確認して",
            "確認したい",
            "見て",
            "どう",
        )
        if any(marker in normalized for marker in question_markers):
            return True
        state_question_markers = (
            "ついてるか",
            "ついているか",
            "点いてるか",
            "点いているか",
            "付いてるか",
            "付いているか",
            "ついてないか",
            "点いてないか",
            "付いてないか",
            "消えてるか",
            "消えているか",
            "消えてないか",
            "点灯か",
            "消灯か",
            "オンか",
            "オフか",
            "明るいか",
            "暗いか",
        )
        if any(marker in normalized for marker in state_question_markers):
            return True
        return normalized.endswith(("か", "かな", "かね", "かい")) and bool(
            self._explicit_room_light_state_label(normalized) or "状態" in normalized
        )

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

    def _state_feedback_reply(
        self,
        user_label: str,
        *,
        continue_action: bool,
        saved: bool = True,
        fallback_saved: bool = False,
    ) -> str:
        label_text = {
            "on": "ついてる",
            "off": "消えてる",
            "daylight": "日光の影響がある",
            "unknown": "判断しづらい",
        }.get(user_label, user_label)
        if not saved:
            if fallback_saved:
                return (
                    f"なるほど、実際は{label_text}んだね。"
                    "この会話の記憶には反映したよ。"
                    "学習ログ本体はあとで同期が必要です。"
                )
            return f"なるほど、実際は{label_text}んだね。ただ、学習ログへの保存に失敗したので、まだ反映できていません。"
        suffix = "そのうえで操作も続けるね。" if continue_action else "学習用の材料として残したよ。"
        return f"なるほど、実際は{label_text}んだね。{suffix}"

    def _handle_action_feedback_if_needed(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        pending: dict[str, Any],
        action: dict[str, Any],
        source_context: str,
    ) -> bool:
        if str(action.get("target") or "") not in {"light", "living_room_light"}:
            return False
        feedback_pending = self._action_feedback_pending(
            turn_input,
            pending=pending,
            action=action,
            source_context=source_context,
        )
        user_label = self._room_light_feedback_label(
            turn_input.text,
            pending=feedback_pending,
        ) or self._direct_room_light_feedback_label(turn_input.text)
        if not user_label:
            return False
        persisted = self._persist_state_query_feedback(
            events,
            factory,
            turn_input,
            pending=feedback_pending,
            user_label=user_label,
            feedback_reason=str(
                feedback_pending.get("feedback_reason")
                or "user_feedback_after_action_review"
            ),
            source_context=source_context,
        )
        return self._resolve_action_feedback(
            events,
            factory,
            turn_input,
            pending=feedback_pending,
            action=action,
            user_label=user_label,
            persisted=persisted,
            source_context=source_context,
        )

    def _action_feedback_pending(
        self,
        turn_input: TurnInput,
        *,
        pending: dict[str, Any],
        action: dict[str, Any],
        source_context: str,
    ) -> dict[str, Any]:
        expected_state = str(action.get("expected_state") or pending.get("expected_state") or "")
        last_review = (
            pending.get("last_review")
            if isinstance(pending.get("last_review"), dict)
            else {}
        )
        evidence = (
            last_review.get("evidence")
            if isinstance(last_review.get("evidence"), dict)
            else {}
        )
        predicted_state = str(evidence.get("state") or expected_state or "unknown")
        return {
            "type": "state_query_pending",
            "state_query_id": "room_light",
            "target": "room_light",
            "feedback_reason": "user_feedback_after_action_review",
            "source_context": source_context,
            "workflow_version": "thought-core-action-feedback-v1",
            "turn_id": turn_input.turn_id,
            "session_id": turn_input.session_id,
            "action_id": str(action.get("action_id") or pending.get("action_id") or ""),
            "expected_state": expected_state,
            "predicted_state": predicted_state,
            "confidence_label": str(evidence.get("confidence_label") or ""),
            "created_at": pending.get("created_at") or datetime.now(UTC).isoformat(),
            "action": dict(action),
            "retry_budget": self._action_review_policy(action),
        }

    def _resolve_action_feedback(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        pending: dict[str, Any],
        action: dict[str, Any],
        user_label: str,
        persisted: dict[str, Any] | None,
        source_context: str,
    ) -> bool:
        expected_state = str(action.get("expected_state") or pending.get("expected_state") or "")
        if expected_state not in {"on", "off"} or user_label not in {"on", "off"}:
            return False
        self.pending_state_queries.pop(turn_input.session_id, None)
        if user_label == expected_state:
            self.pending_action_reviews.pop(turn_input.session_id, None)
            review = {
                "status": "succeeded",
                "reason": "user_feedback_matched_target_state",
                "action_id": action.get("action_id"),
                "expected_state": expected_state,
                "actual_state": user_label,
                "source_context": source_context,
            }
            self._write_short_memory(
                events,
                factory,
                turn_input,
                action=action,
                status="review_feedback_confirmed",
                retry_scope="review",
                policy=self._action_review_policy(action),
                progress={"feedback_label": user_label},
                review=review,
            )
            messages = self._home_action_messages(action)
            if self._feedback_persisted_ok(persisted):
                speech = f"確認ありがとう。{messages['success_speech']} その状態として覚えます。"
            elif self._feedback_fallback_ok(persisted):
                speech = (
                    f"確認ありがとう。{messages['success_speech']} "
                    "この会話の記憶には反映しました。"
                    "学習ログ本体はあとで同期が必要です。"
                )
            else:
                speech = (
                    f"確認ありがとう。{messages['success_speech']} "
                    "ただ、学習ログへの保存はできていません。"
                )
            events.append(
                factory.emit(
                    "action.feedback_resolved",
                    {
                        "status": "succeeded",
                        "action": action,
                        "user_label": user_label,
                        "expected_state": expected_state,
                        "feedback_status": (
                            (persisted or {}).get("result", {}).get("status")
                            if isinstance((persisted or {}).get("result"), dict)
                            else ""
                        ),
                    },
                )
            )
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
                        "review_status": "user_feedback_succeeded",
                        "user_label": user_label,
                    },
                )
            )
            return True

        review = {
            "status": "mismatch",
            "reason": "user_feedback_mismatch",
            "action_id": action.get("action_id"),
            "expected_state": expected_state,
            "actual_state": user_label,
            "source_context": source_context,
        }
        policy = self._action_review_policy(action)
        review_pending = self.pending_action_reviews.get(turn_input.session_id, {})
        execute_result = (
            review_pending.get("execute_result")
            if isinstance(review_pending.get("execute_result"), dict)
            else {}
        )
        retry_pending = {
            "action": dict(action),
            "execute_result": {
                "status": "accepted",
                "executed": True,
                "retryable": False,
                **execute_result,
            },
            "last_review": dict(review),
            "observations_done": policy["observation_attempts"],
            "execute_attempts": self._int_value(
                review_pending.get("execute_attempts")
                if isinstance(review_pending, dict)
                else None,
                1,
            ),
            "policy": dict(policy),
            "created_at": pending.get("created_at") or datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        events.append(
            factory.emit(
                "action.feedback_resolved",
                {
                    "status": "mismatch",
                    "action": action,
                    "user_label": user_label,
                    "expected_state": expected_state,
                    "retryable": self._can_retry_review_action(
                        action,
                        retry_pending,
                        policy,
                    ),
                },
            )
        )
        if self._can_retry_review_action(action, retry_pending, policy):
            self.pending_action_reviews[turn_input.session_id] = retry_pending
            return self._retry_pending_action_review(
                events,
                factory,
                turn_input,
                action=action,
                pending=retry_pending,
                policy=policy,
                last_review=review,
            )

        self.pending_action_reviews.pop(turn_input.session_id, None)
        self._write_short_memory(
            events,
            factory,
            turn_input,
            action=action,
            status="review_feedback_mismatch_exhausted",
            retry_scope="review",
            policy=policy,
            progress={"feedback_label": user_label},
            review=review,
        )
        speech = "実際の状態が望みと違うままでした。これ以上の自動再試行は止めて、状態確認をお願いしたいです。"
        events.append(
            factory.emit(
                "feedback.requested",
                {
                    "reason": "user_feedback_mismatch_retry_exhausted",
                    "speech": speech,
                    "display": "操作結果の確認が必要です",
                    "action": action,
                    "last_review": review,
                },
            )
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
                    "review_status": "user_feedback_mismatch",
                    "user_label": user_label,
                },
            )
        )
        return True

    def _action_from_feedback_pending(self, pending: dict[str, Any]) -> dict[str, Any]:
        action = pending.get("action") if isinstance(pending, dict) else None
        if isinstance(action, dict) and action.get("action_id"):
            return dict(action)
        action_id = str(pending.get("action_id") or "").strip()
        expected_state = str(pending.get("expected_state") or "").strip()
        if not action_id or not expected_state:
            return {}
        target = str(pending.get("target") or "").strip()
        target_name = str(pending.get("target_name") or "").strip()
        action_name = f"home.{action_id}"
        pre_action_phrase = str(pending.get("pre_action_phrase") or "").strip()
        if action_id in {"light_on", "light_off"}:
            target = "light"
            target_name = "リビングの電気"
            action_name = "home.light.turn_on" if expected_state == "on" else "home.light.turn_off"
            pre_action_phrase = (
                "リビングの電気をつける" if expected_state == "on" else "リビングの電気を消す"
            )
        aliases = [target, action_id]
        if target == "light":
            aliases.append("living_room_light")
        return {
            "action": action_name,
            "action_id": action_id,
            "target": target,
            "target_aliases": [alias for alias in aliases if alias],
            "target_name": target_name or target or action_id,
            "confidence": 0.72,
            "expected_state": expected_state,
            "pre_action_phrase": pre_action_phrase or target_name or action_id,
            "available": True,
            "noop": False,
        }

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

        if self._execution_was_accepted(execute_result):
            if self._automatic_review_blocked_by_tracking(action, execute_result):
                self.pending_confirmations.pop(turn_input.session_id, None)
                self._complete_action_submitted_without_automatic_review(
                    events,
                    factory,
                    action=action,
                    execute_result=execute_result,
                    attempts=int(execute_result.get("attempt") or 1),
                    confirmed=True,
                )
                return True
            speech = self._action_recheck_cue_speech(action)
            self._emit_message(
                events,
                factory,
                speech=speech,
                display=speech,
                emotion="focused",
                motion="small_nod",
                priority="immediate",
            )
        self._emit_stage_update(
            events,
            factory,
            stage="environment.observe.after_action",
            speech="反映を待って、環境を見直します。",
            detail={
                "attempt": execute_result.get("attempt", 1),
                "action_id": action.get("action_id"),
                "confirmed": True,
            },
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
                    "attempt": execute_result.get("attempt", 1),
                    "confirmed": True,
                },
            )
        )
        self._emit_stage_update(
            events,
            factory,
            stage="action.review",
            speech="望んだ状態になったか判定します。",
            detail={
                "attempt": execute_result.get("attempt", 1),
                "action_id": action.get("action_id"),
                "confirmed": True,
            },
        )
        review = self._review_action_result(
            turn_input,
            action,
            after_observation,
            execute_result,
        )
        events.append(factory.emit("action.reviewed", review))

        if review["status"] == "succeeded":
            self.pending_confirmations.pop(turn_input.session_id, None)
            messages = self._home_action_messages(action)
            post_action_feedback = self._post_action_room_light_feedback(
                turn_input,
                action,
                after_observation,
            )
            response = self._canonical_action_result_response(
                execute_result,
                messages,
                post_action_feedback,
            )
            self._emit_message(
                events,
                factory,
                speech=response["speech"],
                display=response["display"],
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
        input_frame: InputFrame | None = None,
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
        if (input_frame and input_frame.kind == "state_query") or self._is_room_light_state_question(
            turn_input.text
        ):
            return False
        if self._input_requests_home_action(
            turn_input,
            input_frame,
        ) and pending.get("origin_turn_id") != turn_input.turn_id:
            self._supersede_pending_action_review_for_new_command(
                events,
                factory,
                turn_input,
                pending=pending,
                action=action,
                input_frame=input_frame,
            )
            return False
        if self._handle_action_feedback_if_needed(
            events,
            factory,
            turn_input,
            pending=pending,
            action=action,
            source_context="action_review",
        ):
            return True
        if not self._input_requests_pending_action_review(
            turn_input,
            input_frame,
            pending=pending,
        ):
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
            lambda: self._observe_for_pending_action_review(turn_input, pending),
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
                    "environment_recheck": self._environment_recheck_marker(
                        action,
                        "running",
                        observations_done=observations_done,
                        observation_attempts=policy["observation_attempts"],
                        settle_ms=policy["settle_ms"],
                    ),
                },
            )
        )
        review = self._review_action_result(
            turn_input,
            action,
            observation,
            execute_result,
        )
        review["observations_done"] = observations_done
        review["execute_attempts"] = execute_attempts
        review["settle_ms"] = policy["settle_ms"]
        review_recheck_status = (
            "done"
            if review["status"] == "succeeded"
            else (
                "pending"
                if observations_done < policy["observation_attempts"]
                else "failed"
            )
        )
        review["environment_recheck"] = self._environment_recheck_marker(
            action,
            review_recheck_status,
            observations_done=observations_done,
            observation_attempts=policy["observation_attempts"],
            settle_ms=policy["settle_ms"],
            review=review,
        )
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
                        "environment_recheck": self._environment_recheck_marker(
                            action,
                            "done",
                            observations_done=observations_done,
                            observation_attempts=policy["observation_attempts"],
                            settle_ms=policy["settle_ms"],
                            review=review,
                        ),
                    },
                )
            )
            return True

        if observations_done < policy["observation_attempts"]:
            pending["observations_done"] = observations_done
            pending["last_review"] = review
            pending["updated_at"] = datetime.now(UTC).isoformat()
            auto_continue = self._should_auto_continue_action_review(action, policy)
            speech = self._pending_review_speech(
                action,
                policy,
                observations_done,
                continuation=True,
            )
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
            if not (auto_continue and bool(pending.get("confirmed"))):
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
            if auto_continue:
                return self._handle_pending_action_review_if_needed(
                    events,
                    factory,
                    turn_input,
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
                        "environment_recheck": self._environment_recheck_marker(
                            action,
                            "pending",
                            observations_done=observations_done,
                            observation_attempts=policy["observation_attempts"],
                            settle_ms=policy["settle_ms"],
                            review=review,
                        ),
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
        speech = self._review_exhausted_speech(events, action, messages, review)
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
                    "environment_recheck": self._environment_recheck_marker(
                        action,
                        "failed",
                        observations_done=observations_done,
                        observation_attempts=policy["observation_attempts"],
                        settle_ms=policy["settle_ms"],
                        review=review,
                    ),
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
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "needs_feedback",
                    "action": action,
                    "review_status": review["status"],
                    "observations_done": observations_done,
                    "execute_attempts": execute_attempts,
                    "environment_recheck": self._environment_recheck_marker(
                        action,
                        "failed",
                        observations_done=observations_done,
                        observation_attempts=policy["observation_attempts"],
                        settle_ms=policy["settle_ms"],
                        review=review,
                    ),
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
        review = self._review_action_result(
            turn_input,
            action,
            observation,
            execute_result,
        )
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

    def _begin_deferred_action_review(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        action: dict[str, Any],
        execute_result: dict[str, Any],
        execute_attempts: int,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        policy = self._action_review_policy(action)
        review = {
            "status": "pending",
            "reason": "verification_deferred",
            "action_id": str(action.get("action_id") or ""),
            "expected_state": str(
                action.get("expected_state")
                or execute_result.get("expected_state")
                or ""
            ),
            "execute_status": execute_result.get("status"),
        }
        self._write_short_memory(
            events,
            factory,
            turn_input,
            action=action,
            status="review_budget_opened",
            retry_scope="review",
            policy=policy,
            progress={
                "observations_done": 0,
                "execute_attempts": execute_attempts,
                "confirmed": confirmed,
            },
            review=review,
        )
        self.pending_action_reviews[turn_input.session_id] = {
            "action": dict(action),
            "execute_result": dict(execute_result),
            "last_review": dict(review),
            "observations_done": 0,
            "execute_attempts": execute_attempts,
            "confirmed": confirmed,
            "policy": dict(policy),
            "origin_turn_id": turn_input.turn_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        pending = self._action_review_pending_payload(
            action,
            review,
            policy,
            observations_done=0,
            execute_attempts=execute_attempts,
            confirmed=confirmed,
        )
        pending["deferred"] = True
        events.append(factory.emit("action.review_pending", pending))
        return pending

    def _action_review_pending_payload(
        self,
        action: dict[str, Any],
        review: dict[str, Any],
        policy: dict[str, Any],
        *,
        observations_done: int,
        execute_attempts: int,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        checkpoint_ms = self._checkpoint_ms_for_observation(policy, observations_done)
        return {
            "action": action,
            "review": review,
            "observations_done": observations_done,
            "observation_attempts": policy["observation_attempts"],
            "settle_ms": policy["settle_ms"],
            "checkpoint_ms": checkpoint_ms,
            "delay_ms": checkpoint_ms or policy["settle_ms"],
            "execute_attempts": execute_attempts,
            "confirmed": confirmed,
            "environment_recheck": self._environment_recheck_marker(
                action,
                "pending",
                observations_done=observations_done,
                observation_attempts=policy["observation_attempts"],
                settle_ms=policy["settle_ms"],
                checkpoint_ms=checkpoint_ms,
                review=review,
            ),
        }

    def _environment_recheck_marker(
        self,
        action: dict[str, Any],
        status: str,
        *,
        observations_done: int,
        observation_attempts: int,
        settle_ms: int = 0,
        checkpoint_ms: int | None = None,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = max(1, int(observation_attempts or 1))
        done = max(0, int(observations_done or 0))
        marker: dict[str, Any] = {
            "status": status,
            "source": "thought_core.action_review",
            "action_id": action.get("action_id"),
            "target": action.get("target"),
            "target_name": action.get("target_name"),
            "observations_done": done,
            "observation_attempts": attempts,
            "remaining_observations": max(0, attempts - done),
        }
        if settle_ms:
            marker["settle_ms"] = int(settle_ms)
        if checkpoint_ms:
            marker["checkpoint_ms"] = int(checkpoint_ms)
        if isinstance(review, dict) and review.get("status"):
            marker["review_status"] = review.get("status")
        return marker

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
        if self._automatic_review_blocked_by_tracking(action, execute_result):
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
            "origin_turn_id": turn_input.turn_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        speech = self._pending_review_speech(
            action,
            policy,
            observations_done,
            continuation=confirmed,
        )
        events.append(
            factory.emit(
                "action.review_pending",
                self._action_review_pending_payload(
                    action,
                    review,
                    policy,
                    observations_done=observations_done,
                    execute_attempts=execute_attempts,
                    confirmed=confirmed,
                ),
            )
        )
        auto_continue = self._should_auto_continue_action_review(action, policy)
        if not (auto_continue and confirmed):
            self._emit_message(
                events,
                factory,
                speech=speech,
                display=speech,
                emotion="focused",
                motion="think",
                priority="normal",
            )
        if auto_continue:
            return self._handle_pending_action_review_if_needed(
                events,
                factory,
                turn_input,
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

    def _imagine_target_state(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            target_state = self.action_reasoner.imagine_target_state(
                turn_input,
                observation,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            target_state = {
                "schema": ACTION_REASONER_BOUNDARY,
                "status": "error",
                "reason": "target_state_reasoner_error",
                "error": str(exc),
                "bindings": [],
                "wildcard_policy": "unspecified_values_are_any",
            }
        events.append(
            factory.emit(
                "target_state.imagined",
                {
                    "boundary": ACTION_REASONER_BOUNDARY,
                    "reasoner": describe_action_reasoner(self.action_reasoner),
                    "target_state": target_state,
                },
            )
        )
        return target_state

    def _plan_command(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        observation: dict[str, Any],
        target_state: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            command_plan = self.action_reasoner.plan_command(
                turn_input,
                observation,
                target_state,
                preview,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            command_plan = {
                "schema": ACTION_REASONER_BOUNDARY,
                "status": "error",
                "reason": "command_reasoner_error",
                "error": str(exc),
                "wildcard_policy": target_state.get(
                    "wildcard_policy",
                    "unspecified_values_are_any",
                ),
            }
        events.append(
            factory.emit(
                "command.planned",
                {
                    "boundary": ACTION_REASONER_BOUNDARY,
                    "reasoner": describe_action_reasoner(self.action_reasoner),
                    "target_state_id": target_state.get("target_state_id"),
                    "command_plan": command_plan,
                    "target_state_diff": command_plan.get("diff_before", {}),
                },
            )
        )
        return command_plan

    def _attach_reasoning_to_action(
        self,
        action: dict[str, Any],
        target_state: dict[str, Any],
        command_plan: dict[str, Any],
    ) -> dict[str, Any]:
        attached = dict(action)
        if target_state:
            attached["target_state"] = dict(target_state)
        if command_plan:
            attached["command_plan"] = dict(command_plan)
        return attached

    def _target_state_from_action(self, action: dict[str, Any]) -> dict[str, Any]:
        target_state = action.get("target_state")
        if isinstance(target_state, dict) and isinstance(target_state.get("bindings"), list):
            return target_state
        target = str(action.get("target") or "").strip()
        expected_state = str(action.get("expected_state") or "").strip()
        if not target or not expected_state:
            return {
                "schema": ACTION_REASONER_BOUNDARY,
                "status": "unsupported",
                "reason": "action_has_no_target_state",
                "bindings": [],
                "wildcard_policy": "unspecified_values_are_any",
            }
        target_aliases = action.get("target_aliases")
        aliases: list[str] = []
        if isinstance(target_aliases, list):
            aliases.extend(str(item) for item in target_aliases if str(item or "").strip())
        aliases.append(target)
        if action.get("action_id"):
            aliases.append(str(action.get("action_id")))
        if target == "light":
            aliases.append("living_room_light")
        return {
            "schema": ACTION_REASONER_BOUNDARY,
            "status": "ok",
            "source": "action_fallback",
            "target_state_id": f"target_action_{action.get('action_id') or target}",
            "action_id_hint": action.get("action_id"),
            "bindings": [
                {
                    "kind": "appliance_state",
                    "target": target,
                    "target_name": action.get("target_name"),
                    "target_aliases": sorted(set(aliases)),
                    "field": "state",
                    "operator": "eq",
                    "value": expected_state,
                    "scope": "required",
                    "path_hint": f"environment.appliances.{target}.state",
                }
            ],
            "wildcard_policy": "unspecified_values_are_any",
            "ignored_values": "all_environment_values_without_required_bindings",
        }

    def _target_state_is_reviewable(self, target_state: dict[str, Any]) -> bool:
        bindings = target_state.get("bindings")
        return isinstance(bindings, list) and bool(bindings)

    def _review_action_result(
        self,
        turn_input: TurnInput,
        action: dict[str, Any],
        observation: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> dict[str, Any]:
        action_id = str(action.get("action_id") or "")
        expected_state = str(
            action.get("expected_state") or execute_result.get("expected_state") or ""
        ).strip()
        target_state = self._target_state_from_action(action)
        if self._target_state_is_reviewable(target_state):
            try:
                review = self.action_reasoner.review_target_state(
                    turn_input,
                    action,
                    target_state,
                    observation,
                    execute_result,
                )
            except Exception as exc:  # pragma: no cover - defensive boundary
                review = {
                    "status": "pending",
                    "reason": "target_state_review_error",
                    "error": str(exc),
                }
            if not isinstance(review, dict):
                review = {
                    "status": "pending",
                    "reason": "target_state_review_invalid",
                }
            if review.get("status") in {
                "succeeded",
                "mismatch",
                "pending",
                "execute_failed",
            }:
                review.setdefault("action_id", action_id)
                review.setdefault("expected_state", expected_state)
                review.setdefault("target_state_id", target_state.get("target_state_id"))
                review.setdefault("review_basis", "target_state")
                review.setdefault(
                    "wildcard_policy",
                    target_state.get("wildcard_policy", "unspecified_values_are_any"),
                )
                return review
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

    def _action_review_policy(self, action: dict[str, Any]) -> dict[str, Any]:
        action_id = str(action.get("action_id") or "")
        profiles = {
            "light_on": {
                "settle_ms": 2000,
                "observation_attempts": 2,
                "auto_retries": 0,
                "checkpoint_ms": [2000, 5000],
            },
            "light_off": {
                "settle_ms": 2000,
                "observation_attempts": 2,
                "auto_retries": 0,
                "checkpoint_ms": [2000, 5000],
            },
            "fan_on": {"settle_ms": 2500, "observation_attempts": 2, "auto_retries": 0},
            "fan_off": {"settle_ms": 2500, "observation_attempts": 2, "auto_retries": 0},
            "aircon_on": {
                "settle_ms": 8000,
                "observation_attempts": 3,
                "auto_retries": 0,
                "checkpoint_ms": [8000, 15000, 25000],
            },
            "aircon_off": {
                "settle_ms": 8000,
                "observation_attempts": 3,
                "auto_retries": 0,
                "checkpoint_ms": [8000, 15000, 25000],
            },
            "door_open": {"settle_ms": 5000, "observation_attempts": 2, "auto_retries": 0},
            "door_close": {"settle_ms": 5000, "observation_attempts": 2, "auto_retries": 0},
            "door_stop": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 0},
            "vacuum_start": {"settle_ms": 10000, "observation_attempts": 2, "auto_retries": 0},
            "vacuum_return": {"settle_ms": 10000, "observation_attempts": 2, "auto_retries": 0},
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
            checkpoints = (
                expected_effect.get("checkpoint_ms")
                or expected_effect.get("checkpoints_ms")
                or expected_effect.get("verification_checkpoints_ms")
            )
            if isinstance(checkpoints, list):
                policy["checkpoint_ms"] = checkpoints
        policy["settle_ms"] = max(0, policy["settle_ms"])
        policy["observation_attempts"] = min(4, max(1, policy["observation_attempts"]))
        policy["auto_retries"] = 0
        checkpoints = self._review_checkpoints_ms(policy)
        if checkpoints:
            policy["checkpoint_ms"] = checkpoints[: policy["observation_attempts"]]
        return policy

    def _review_checkpoints_ms(self, policy: dict[str, Any]) -> list[int]:
        raw = policy.get("checkpoint_ms") or policy.get("checkpoints_ms")
        if not isinstance(raw, list):
            return []
        checkpoints: list[int] = []
        for item in raw:
            value = self._int_value(item, 0)
            if value > 0:
                checkpoints.append(value)
        return checkpoints

    def _checkpoint_ms_for_observation(
        self,
        policy: dict[str, Any],
        observations_done: int,
    ) -> int:
        checkpoints = self._review_checkpoints_ms(policy)
        if observations_done < len(checkpoints):
            return checkpoints[observations_done]
        return self._int_value(policy.get("settle_ms"), 0)

    def _should_auto_continue_action_review(
        self,
        action: dict[str, Any],
        policy: dict[str, int],
    ) -> bool:
        action_id = str(action.get("action_id") or "")
        if not action_id:
            return False
        return policy["observation_attempts"] <= 4

    def _observe_for_pending_action_review(
        self,
        turn_input: TurnInput,
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        execute_result = (
            pending.get("execute_result")
            if isinstance(pending.get("execute_result"), dict)
            else {}
        )
        issued_at = str(execute_result.get("issued_at") or pending.get("issued_at") or "")
        policy = (
            pending.get("policy")
            if isinstance(pending.get("policy"), dict)
            else self._action_review_policy(
                pending.get("action") if isinstance(pending.get("action"), dict) else {}
            )
        )
        if issued_at:
            execute_result = {**execute_result, "issued_at": issued_at}
        self._prepare_action_review_checkpoint(
            turn_input,
            execute_result,
            policy=policy,
            observations_done=int(pending.get("observations_done") or 0),
        )
        return self.tools.environment_observe(turn_input, reason="after_action")

    def _prepare_action_review_checkpoint(
        self,
        turn_input: TurnInput,
        execute_result: dict[str, Any],
        *,
        policy: dict[str, Any],
        observations_done: int,
    ) -> dict[str, Any]:
        issued_at_text = str(execute_result.get("issued_at") or "").strip()
        checkpoint_ms = self._checkpoint_ms_for_observation(policy, observations_done)
        checkpoint_after = self._checkpoint_after_iso(issued_at_text, checkpoint_ms)
        wait_after = checkpoint_after or issued_at_text
        if not wait_after:
            return {}

        timeout_ms = self._checkpoint_wait_timeout_ms(wait_after)
        wait_after_by_turn = getattr(self.tools, "room_light_wait_after_by_turn", None)
        if isinstance(wait_after_by_turn, dict):
            wait_after_by_turn[turn_input.turn_id] = wait_after
        wait_timeout_by_turn = getattr(
            self.tools,
            "room_light_wait_timeout_ms_by_turn",
            None,
        )
        if isinstance(wait_timeout_by_turn, dict):
            wait_timeout_by_turn[turn_input.turn_id] = timeout_ms
        return {
            "checkpoint_ms": checkpoint_ms,
            "wait_after": wait_after,
            "wait_timeout_ms": timeout_ms,
        }

    def _checkpoint_after_iso(self, issued_at_text: str, checkpoint_ms: int) -> str:
        issued_at = self._parse_iso_datetime(issued_at_text)
        if issued_at is None or checkpoint_ms <= 0:
            return issued_at_text
        return (issued_at + timedelta(milliseconds=checkpoint_ms)).isoformat()

    def _checkpoint_wait_timeout_ms(self, wait_after_text: str) -> int:
        base_timeout_ms = 1500
        config = getattr(self.tools, "config", None)
        configured = self._int_value(
            getattr(config, "room_light_wait_timeout_ms", None),
            base_timeout_ms,
        )
        base_timeout_ms = max(500, configured)
        wait_after = self._parse_iso_datetime(wait_after_text)
        if wait_after is None:
            return base_timeout_ms
        remaining_ms = max(
            0,
            int((wait_after - datetime.now(UTC)).total_seconds() * 1000),
        )
        return max(base_timeout_ms, remaining_ms + base_timeout_ms)

    def _parse_iso_datetime(self, text: str) -> datetime | None:
        value = str(text or "").strip()
        if not value:
            return None
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _pending_review_speech(
        self,
        action: dict[str, Any],
        policy: dict[str, int],
        observations_done: int,
        *,
        continuation: bool = False,
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
        if continuation:
            return (
                f"反映には{seconds}秒くらいかかる見込みです。"
                f"まだ環境で結果を確認しきれていないので、少し待ってから確認します。{tail}"
            )
        return (
            f"{phrase}の操作は送信しました。反映には{seconds}秒くらいかかる見込みです。"
            f"まだ環境で結果を確認しきれていないので、少し待ってから確認します。{tail}"
        )

    def _review_exhausted_speech(
        self,
        events: list[ThoughtEvent],
        action: dict[str, Any],
        messages: dict[str, str],
        review: dict[str, Any],
    ) -> str:
        if self._review_has_stale_expected_match(review):
            expected_text = self._state_label_text(
                str(review.get("expected_state") or action.get("expected_state") or "")
            )
            speech = (
                f"送信は済んでいて、表示上は{expected_text}ように見えます。"
                "ただ情報が古いので、物理状態はまだ断定できません。"
                "必要ならもう一度確認します。"
            )
            return self._coherent_feedback_speech(events, speech)

        if str(review.get("status") or "") == "pending":
            speech = (
                "送信は済んでいますが、環境側で確証が取れませんでした。"
                "必要ならもう一度確認します。"
            )
            return self._coherent_feedback_speech(events, speech)

        speech = (
            f"{messages['feedback_speech']} "
            "何回か環境を見直しましたが、期待した状態を確認できませんでした。"
        )
        return self._coherent_feedback_speech(events, speech)

    def _coherent_feedback_speech(
        self,
        events: list[ThoughtEvent],
        speech: str,
    ) -> str:
        coherent = self._coherent_stream_speech(events, speech)
        if not coherent:
            coherent = "まだ確証が取れていません。必要ならもう一度確認します。"
        self._remember_stream_speech(coherent)
        return coherent

    def _review_has_stale_expected_match(self, review: dict[str, Any]) -> bool:
        expected_state = str(review.get("expected_state") or "").strip().lower()
        diff = review.get("target_state_diff")
        if not isinstance(diff, dict):
            return False
        unknowns = diff.get("unknowns")
        if not isinstance(unknowns, list):
            return False
        for item in unknowns:
            if not isinstance(item, dict):
                continue
            if str(item.get("reason") or "") != "state_stale":
                continue
            observed = item.get("observed")
            binding = item.get("binding")
            if not isinstance(observed, dict) or not isinstance(binding, dict):
                continue
            actual = str(observed.get("state") or "").strip().lower()
            expected = str(binding.get("value") or expected_state).strip().lower()
            if actual and expected and actual == expected:
                return True
        return False

    def _state_label_text(self, state: str) -> str:
        return {
            "on": "ついている",
            "off": "消えている",
            "open": "開いている",
            "closed": "閉まっている",
            "stopped": "止まっている",
            "cleaning": "掃除中である",
            "returning": "戻っている",
            "paused": "一時停止している",
        }.get(str(state or "").strip().lower(), "期待した状態にある")

    def _action_sent_review_speech(self, action: dict[str, Any]) -> str:
        phrase = str(
            action.get("pre_action_phrase")
            or action.get("target_name")
            or action.get("action_id")
            or "この操作"
        )
        policy = self._action_review_policy(action)
        checkpoints = self._review_checkpoints_ms(policy)
        delay_ms = (
            checkpoints[0]
            if checkpoints
            else self._int_value(policy.get("settle_ms"), 0)
        )
        seconds = max(1, round(delay_ms / 1000)) if delay_ms else 1
        return (
            f"{phrase}の操作は送信しました。"
            f"反映には{seconds}秒くらいかかる見込みです。"
            "少し待ってから環境を見直します。"
        )

    def _action_recheck_cue_speech(self, action: dict[str, Any]) -> str:
        phrase = str(
            action.get("pre_action_phrase")
            or action.get("target_name")
            or action.get("action_id")
            or "この操作"
        )
        return f"{phrase}操作を送信しました。反映後の状態を確認します。"

    def _complete_action_submitted_without_automatic_review(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        *,
        action: dict[str, Any],
        execute_result: dict[str, Any],
        attempts: int,
        confirmed: bool,
    ) -> None:
        speech = self._action_external_observation_required_speech(
            action,
            execute_result,
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
                    "status": "submitted_external_observation_required",
                    "attempts": attempts,
                    "action": action,
                    "execute_status": execute_result.get("status"),
                    "confirmed": confirmed,
                    "proof_layer": "command_accepted_only",
                    "state_tracking": _first_nonempty_text(
                        action.get("state_tracking"),
                        execute_result.get("state_tracking"),
                    ),
                    "verification_mode": _first_nonempty_text(
                        action.get("verification_mode"),
                        execute_result.get("verification_mode"),
                    ),
                    "state_authority": _first_nonempty_text(
                        action.get("state_authority"),
                        execute_result.get("state_authority"),
                    ),
                    "automatic_review_scheduled": False,
                    "physical_state_confirmed": False,
                },
            )
        )

    def _action_external_observation_required_speech(
        self,
        action: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> str:
        base = str(execute_result.get("speak") or execute_result.get("message") or "").strip()
        if not base:
            phrase = str(
                action.get("pre_action_phrase")
                or action.get("target_name")
                or action.get("action_id")
                or "この操作"
            ).strip()
            base = f"{phrase}操作を送信しました"
        if not base.endswith(("。", "！", "？", "!", "?")):
            base = f"{base}。"
        return (
            f"{base}"
            "ただし、この操作はHome Assistantだけでは物理状態を確認できないため、"
            "実際に変わったかは外部確認が必要です。"
        )

    def _can_retry_review_action(
        self,
        action: dict[str, Any],
        pending: dict[str, Any],
        policy: dict[str, int],
    ) -> bool:
        if action.get("confirm_required"):
            return False
        last_review = (
            pending.get("last_review")
            if isinstance(pending.get("last_review"), dict)
            else {}
        )
        reason = str(last_review.get("reason") or "")
        status = str(last_review.get("status") or "")
        uncertain_reasons = {
            "accepted_but_unverified",
            "environment_wait_timeout",
            "target_state_unverified",
            "room_light_low_confidence",
            "room_light_unavailable_or_stale",
            "device_state_stale",
        }
        if status == "pending" and reason in uncertain_reasons:
            return False
        execute_attempts = int(pending.get("execute_attempts") or 1)
        return execute_attempts <= policy["auto_retries"]

    def _execution_was_accepted(self, execute_result: dict[str, Any]) -> bool:
        status = str(execute_result.get("status") or "")
        if bool(execute_result.get("executed")) or bool(execute_result.get("verified_by_bridge")):
            return True
        return status in {"accepted", "submitted", "duplicate"}

    def _should_defer_action_review(
        self,
        action: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> bool:
        if not self._execution_was_accepted(execute_result):
            return False
        if self._automatic_review_blocked_by_tracking(action, execute_result):
            return False
        issued_at = str(execute_result.get("issued_at") or "")
        if self._parse_iso_datetime(issued_at) is None:
            return False
        if action.get("confirm_required"):
            return False
        return bool(self._review_checkpoints_ms(self._action_review_policy(action)))

    def _automatic_review_blocked_by_tracking(
        self,
        action: dict[str, Any],
        execute_result: dict[str, Any],
    ) -> bool:
        state_tracking = _first_nonempty_text(
            action.get("state_tracking"),
            execute_result.get("state_tracking"),
        )
        verification_mode = _first_nonempty_text(
            action.get("verification_mode"),
            execute_result.get("verification_mode"),
        )
        state_authority = _first_nonempty_text(
            action.get("state_authority"),
            execute_result.get("state_authority"),
        )
        if state_tracking in {
            "external_required",
            "ack_only",
            "manual_required",
            "unsupported",
        }:
            return True
        if verification_mode in {
            "external_observation",
            "command_ack_only",
            "manual_confirmation",
            "unsupported",
        }:
            return True
        if state_authority in {"open_loop", "submitted_only", "manual", "unknown"}:
            return True
        return False

    def _input_requests_home_action(
        self,
        turn_input: TurnInput,
        input_frame: InputFrame | None = None,
    ) -> bool:
        if input_frame and input_frame.is_command:
            return True
        return detect_home_action_intent(turn_input.text) is not None and (
            self._looks_like_home_action_command(turn_input.text)
        )

    def _input_requests_pending_action_review(
        self,
        turn_input: TurnInput,
        input_frame: InputFrame | None = None,
        *,
        pending: dict[str, Any] | None = None,
    ) -> bool:
        pending = pending or self.pending_action_reviews.get(turn_input.session_id)
        if not pending:
            return False
        if pending.get("origin_turn_id") == turn_input.turn_id:
            return True
        if input_frame and input_frame.kind == "state_feedback":
            return True
        context_refs = turn_input.context_refs if isinstance(turn_input.context_refs, dict) else {}
        if bool(context_refs.get("pending_action_review")):
            return True
        review_context_values = {
            "pending_action_review",
            "action_review",
            "auto_action_review",
            "review_pending_action",
        }
        for key in ("purpose", "intent", "review_trigger", "source_context"):
            value = context_refs.get(key)
            if isinstance(value, str) and value.strip().lower() in review_context_values:
                return True
        normalized = turn_input.text.replace(" ", "").replace("　", "").lower()
        explicit_markers = (
            "確認して",
            "確認する",
            "確認します",
            "確認お願い",
            "再確認",
            "見直して",
            "見直す",
            "見直します",
            "結果を確認",
            "結果確認",
            "反映を確認",
            "反映確認",
            "状態を確認",
            "状態確認",
            "レビューして",
            "review",
            "チェックして",
        )
        if any(marker in normalized for marker in explicit_markers):
            return True
        repeat_markers = ("もう一度", "もう一回", "再度", "もっかい")
        return any(marker in normalized for marker in repeat_markers) and any(
            verb in normalized for verb in ("確認", "見直", "反映", "結果", "状態")
        )

    def _supersede_pending_action_review_for_new_command(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        *,
        pending: dict[str, Any],
        action: dict[str, Any],
        input_frame: InputFrame | None = None,
    ) -> None:
        self.pending_action_reviews.pop(turn_input.session_id, None)
        session_key = self._speech_fragment_key(turn_input.session_id or "default")
        old_issue_key = self._action_speech_issue_key(session_key, action)
        self.recent_speech_by_issue.pop(old_issue_key, None)
        policy = (
            pending.get("policy")
            if isinstance(pending.get("policy"), dict)
            else self._action_review_policy(action)
        )
        review = {
            "status": "superseded",
            "reason": "new_home_command_received",
            "previous_action_id": action.get("action_id"),
            "new_text": turn_input.text,
        }
        self._write_short_memory(
            events,
            factory,
            turn_input,
            action=action,
            status="review_superseded_by_new_command",
            retry_scope="review",
            policy=policy,
            progress={
                "observations_done": self._int_value(
                    pending.get("observations_done"),
                    0,
                ),
                "execute_attempts": self._int_value(
                    pending.get("execute_attempts"),
                    1,
                ),
                "new_command": turn_input.text,
            },
            review=review,
        )
        events.append(
            factory.emit(
                "action.review_superseded",
                {
                    "reason": "new_home_command_received",
                    "previous_action": action,
                    "new_text": turn_input.text,
                },
            )
        )
        self._active_issue_key = self._speech_issue_key(turn_input, input_frame)

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
            "action": dict(action),
            "target_state": (
                action.get("target_state")
                if isinstance(action.get("target_state"), dict)
                else {}
            ),
            "retry_budget": retry_budget,
            "last_review": dict(review),
            "target_state_diff": (
                review.get("target_state_diff")
                if isinstance(review.get("target_state_diff"), dict)
                else {}
            ),
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
        if status.endswith("_exhausted") or status.endswith("_failed"):
            self._write_failure_pattern_candidate(
                events,
                factory,
                turn_input,
                action=action,
                status=status,
                retry_scope=retry_scope,
                progress=progress,
                review=review,
            )
        return result

    def _write_failure_pattern_candidate(
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
    ) -> None:
        turn_key = self._speech_fragment_key(turn_input.turn_id or "turn")
        candidate_id = f"mcand_{turn_key}_failure_pattern_001"
        source_event_id = events[-1].event_id if events else ""
        item = {
            "schema_version": "memory.item.v0",
            "memory_id": candidate_id,
            "memory_type": "failure_pattern",
            "scope": "failure_patterns",
            "status": "candidate",
            "derived_from_event_id": source_event_id,
            "why_record": "retry_or_review_budget_exhausted",
            "evidence_summary_ref": f"event:{source_event_id}" if source_event_id else "",
            "retention_class": "candidate_ephemeral_review",
            "redaction_state": "summary_only_no_raw_evidence",
            "deletion_or_forgetting_state": "deletable_candidate",
            "protected_or_deletable": "deletable_unprotected",
            "safe_to_act": False,
            "safe_for_future_reasoning": {
                "allowed": True,
                "mode": "advisory_only",
                "must_revalidate_current_state": True,
            },
            "content": {
                "action_id": str(action.get("action_id") or ""),
                "target": str(action.get("target") or ""),
                "expected_state": str(action.get("expected_state") or ""),
                "retry_scope": retry_scope,
                "retry_status": status,
                "review_status": str(review.get("status") or ""),
                "review_reason": str(review.get("reason") or ""),
                "progress": dict(progress),
                "summary": (
                    "Home action did not reach its target state within the "
                    "available retry/review budget."
                ),
            },
            "source": {
                "service": "thought-core",
                "trace_id": str(
                    turn_input.context_refs.get("trace_id") or f"trace_{turn_input.turn_id}"
                ),
                "turn_id": turn_input.turn_id,
                "event_id": source_event_id,
            },
            "confidence": 0.72,
            "created_at": datetime.now(UTC).isoformat(),
        }
        events.append(
            factory.emit(
                "memory.candidate_requested",
                {
                    "candidate_id": candidate_id,
                    "memory_type": item["memory_type"],
                    "scope": item["scope"],
                    "derived_from_event_id": item["derived_from_event_id"],
                    "why_record": item["why_record"],
                    "evidence_summary_ref": item["evidence_summary_ref"],
                    "retention_class": item["retention_class"],
                    "redaction_state": item["redaction_state"],
                    "deletion_or_forgetting_state": item["deletion_or_forgetting_state"],
                    "protected_or_deletable": item["protected_or_deletable"],
                    "safe_to_act": item["safe_to_act"],
                    "safe_for_future_reasoning": item["safe_for_future_reasoning"],
                    "durable_memory_claimed": False,
                },
            )
        )
        result = self._call_tool(
            events,
            factory,
            "memory.write",
            lambda: self.tools.memory_write(turn_input, item),
        )
        events.append(
            factory.emit(
                "memory.candidate_recorded",
                {
                    "requested_candidate_id": candidate_id,
                    "scope": item["scope"],
                    "memory_type": item["memory_type"],
                    "action_id": item["content"]["action_id"],
                    "write_status": result.get("status"),
                    "candidate_id": result.get("candidate_id") or candidate_id,
                    "written": bool(result.get("written", result.get("ok", False))),
                },
            )
        )

    def _retrieve_memory_context(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> dict[str, Any]:
        self._emit_stage_update(
            events,
            factory,
            stage="memory.retrieve",
            speech="関連しそうな記憶を短く確認します。",
            detail={"session_id": turn_input.session_id},
        )
        result = self._call_tool(
            events,
            factory,
            "memory.retrieve",
            lambda: self.tools.memory_retrieve(turn_input),
        )
        raw_items = result.get("items", [])
        items = raw_items if isinstance(raw_items, list) else []
        compact_items = [self._compact_memory_item(item) for item in items[:8]]
        context = {
            "status": str(result.get("status") or "ok"),
            "item_count": len(compact_items),
            "items": compact_items,
            "summary": result.get("summary") or self._memory_summary(compact_items),
            "source": result.get("source") or result.get("adapter_kind") or "memory.retrieve",
        }
        events.append(
            factory.emit(
                "memory.retrieved",
                {
                    "status": context["status"],
                    "item_count": context["item_count"],
                    "summary": context["summary"],
                    "source": context["source"],
                },
            )
        )
        return context

    def _hydrate_pending_action_review_from_memory(
        self,
        turn_input: TurnInput,
        memory_context: dict[str, Any],
        input_frame: InputFrame | None = None,
    ) -> None:
        if self.pending_action_reviews.get(turn_input.session_id):
            return
        if input_frame is not None and input_frame.kind in {
            "state_feedback",
            "state_query",
        }:
            return
        if self._input_requests_home_action(turn_input, input_frame):
            return
        items = memory_context.get("items", [])
        if not isinstance(items, list):
            return
        open_statuses = {
            "review_budget_opened",
            "review_observation_pending",
            "review_retry_scheduled",
        }
        closed_statuses = {
            "review_feedback_confirmed",
            "review_feedback_mismatch_exhausted",
            "review_retry_exhausted",
            "review_superseded_by_new_command",
        }
        closed_action_keys: set[str] = set()
        open_candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]] = []
        for item in items:
            content = item.get("content") if isinstance(item, dict) else {}
            if not isinstance(content, dict):
                continue
            retry_budget = content.get("retry_budget")
            if not isinstance(retry_budget, dict):
                continue
            action = content.get("action")
            if not isinstance(action, dict) or not action.get("action_id"):
                action = self._action_from_feedback_pending(content)
            if not action:
                continue
            action_key = self._action_review_key(action)
            status = str(retry_budget.get("status") or "")
            if status in closed_statuses:
                closed_action_keys.add(action_key)
                continue
            if status not in open_statuses:
                continue
            open_candidates.append((content, retry_budget, dict(action), action_key))
        for content, retry_budget, action, action_key in open_candidates:
            if action_key in closed_action_keys:
                continue
            progress = (
                retry_budget.get("progress")
                if isinstance(retry_budget.get("progress"), dict)
                else {}
            )
            last_review = (
                content.get("last_review")
                if isinstance(content.get("last_review"), dict)
                else {}
            )
            self.pending_action_reviews[turn_input.session_id] = {
                "action": dict(action),
                "execute_result": {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "issued_at": content.get("issued_at") or "",
                },
                "last_review": dict(last_review),
                "observations_done": self._int_value(
                    progress.get("observations_done"),
                    0,
                ),
                "execute_attempts": self._int_value(
                    progress.get("execute_attempts"),
                    1,
                ),
                "confirmed": bool(progress.get("confirmed")),
                "policy": {
                    "settle_ms": self._int_value(retry_budget.get("settle_ms"), 0),
                    "observation_attempts": self._int_value(
                        retry_budget.get("observation_attempts"),
                        1,
                    ),
                    "auto_retries": self._int_value(retry_budget.get("auto_retries"), 0),
                },
                "created_at": content.get("created_at") or datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "origin_turn_id": content.get("turn_id") or "",
                "hydrated_from_memory": True,
            }
            return

    def _with_memory_context(
        self,
        observation: dict[str, Any],
        memory_context: dict[str, Any],
    ) -> dict[str, Any]:
        if not memory_context:
            return observation
        enriched = dict(observation)
        enriched["memory_context"] = {
            "status": memory_context.get("status"),
            "item_count": memory_context.get("item_count", 0),
            "items": list(memory_context.get("items", [])),
            "summary": memory_context.get("summary") or "",
        }
        return enriched

    def _compact_memory_item(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {"content": str(item)[:240]}
        content = item.get("content")
        if isinstance(content, dict):
            compact_content = {
                key: content[key]
                for key in sorted(content.keys())
                if key
                in {
                    "action_id",
                    "target",
                    "expected_state",
                    "action",
                    "retry_budget",
                    "last_review",
                    "issued_at",
                    "actual_state",
                    "preference",
                    "alias",
                    "canonical",
                    "summary",
                    "note",
                }
            }
            if not compact_content:
                compact_content = dict(list(content.items())[:5])
        else:
            compact_content = str(content or item.get("summary") or "")[:320]
        return {
            "scope": item.get("scope"),
            "memory_type": item.get("memory_type") or item.get("type"),
            "status": item.get("status"),
            "content": compact_content,
            "confidence": item.get("confidence"),
            "created_at": item.get("created_at"),
            "source": item.get("source"),
        }

    def _memory_summary(self, items: list[dict[str, Any]]) -> str:
        if not items:
            return "関連メモリは見つかりませんでした。"
        scopes = sorted({str(item.get("scope") or "unknown") for item in items})
        types = sorted({str(item.get("memory_type") or "unknown") for item in items})
        return (
            f"{len(items)}件の関連メモリを確認しました。"
            f"scope={', '.join(scopes)} / type={', '.join(types)}"
        )

    def _build_working_memory_context(self, turn_input: TurnInput) -> dict[str, Any]:
        refs = turn_input.context_refs
        if not refs:
            return {}

        structured_context = self._structured_context_refs(refs)
        direct_refs: list[dict[str, Any]] = []
        pending_refs: list[dict[str, Any]] = []
        ignored_refs: list[dict[str, Any]] = []
        for key, value in refs.items():
            ref_key = str(key)
            if ref_key in STRUCTURED_CONTEXT_KEYS:
                continue
            if _is_unsafe_context_key(ref_key):
                ignored_refs.append(
                    {
                        "key": ref_key,
                        "reason": "unsafe_or_raw_context_ref",
                    }
                )
                continue
            if ref_key not in SAFE_CONTEXT_REF_KEYS:
                continue
            unsafe_value_reason = _unsafe_context_value_reason(value)
            if unsafe_value_reason:
                ignored_refs.append({"key": ref_key, "reason": unsafe_value_reason})
                continue
            safe_value = _safe_context_scalar(value)
            if safe_value == "":
                ignored_refs.append(
                    {
                        "key": ref_key,
                        "reason": "non_scalar_or_empty_context_ref",
                    }
                )
                continue
            classification, reason = self._classify_context_ref(
                ref_key,
                safe_value,
                structured_context,
            )
            if classification == "safe":
                direct_refs.append({"key": ref_key, "value": safe_value})
                continue
            if classification == "pending":
                pending_refs.append({"key": ref_key, "value": safe_value, "reason": reason})
                continue
            ignored_refs.append({"key": ref_key, "reason": reason})

        self._add_structured_current_refs(direct_refs, structured_context)

        if not structured_context and not direct_refs and not pending_refs and not ignored_refs:
            return {}

        structured_can_make_safe = (
            structured_context.get("safe_for_thought_core_use") is True
            and self._structured_context_can_make_safe(structured_context)
        )
        safe_for_use = bool(direct_refs or structured_can_make_safe)
        context_id = str(
            structured_context.get("context_id")
            or self._context_id_from_refs(turn_input, direct_refs)
        )
        context: dict[str, Any] = {
            "schema_version": "thought-core.working_memory_context.v0",
            "context_id": context_id,
            "status": "available" if safe_for_use else "ignored",
            "received_ref_count": len(direct_refs) + len(pending_refs) + len(structured_context),
            "safe_refs": direct_refs,
            "pending_authority_refs": pending_refs,
            "observed_at": structured_context.get("observed_at") or "",
            "stale_after": structured_context.get("stale_after") or "",
            "freshness": structured_context.get("freshness") or "unknown",
            "staleness": structured_context.get("staleness") or "unknown",
            "uncertainty": structured_context.get("uncertainty") or "unknown",
            "repetition": structured_context.get("repetition") or {},
            "state_authority": (
                structured_context.get("state_authority")
                or structured_context.get("authority")
                or "supplied_context_refs"
            ),
            "safe_for_thought_core_use": safe_for_use,
            "safe_to_act": False,
            "not_proven": bool(structured_context.get("not_proven", True)),
            "must_not_imply": _safe_context_list(
                structured_context.get("must_not_imply"),
                defaults=[
                    "verified_current_state",
                    "durable_memory",
                    "safe_to_act",
                ],
            ),
            "ignored_refs": ignored_refs,
        }
        for ref in direct_refs:
            context.setdefault(ref["key"], ref["value"])
        return context

    def _classify_context_ref(
        self,
        ref_key: str,
        safe_value: str,
        structured_context: dict[str, Any],
    ) -> tuple[str, str]:
        if ref_key in CURRENT_SAFE_CONTEXT_REF_KEYS:
            return "safe", ""
        if ref_key == "driver_result_id":
            if safe_value.startswith("driver-result-"):
                return "ignored", "runtime_local_driver_result_id_not_authority"
            if safe_value.startswith("mot_drv_"):
                if self._has_explicit_driver_authority(structured_context):
                    return "safe", ""
                return "pending", "driver_result_id_pending_explicit_authority"
            return "ignored", "driver_result_id_not_contract_shaped"
        if ref_key in {"body_schema_snapshot_id", "verifier_result_id"}:
            return "pending", f"{ref_key}_authority_route_not_open"
        return "ignored", "unsupported_context_ref"

    def _add_structured_current_refs(
        self,
        direct_refs: list[dict[str, Any]],
        structured_context: dict[str, Any],
    ) -> None:
        existing = {str(ref.get("key") or "") for ref in direct_refs}
        for key in CURRENT_SAFE_CONTEXT_REF_KEYS:
            if key in existing:
                continue
            safe_value = _safe_context_scalar(structured_context.get(key))
            if safe_value:
                direct_refs.append(
                    {
                        "key": key,
                        "value": safe_value,
                        "source": "structured_context",
                    }
                )

    def _structured_context_can_make_safe(self, structured_context: dict[str, Any]) -> bool:
        for key in CURRENT_SAFE_CONTEXT_REF_KEYS:
            if _safe_context_scalar(structured_context.get(key)):
                return True
        driver_result_id = _safe_context_scalar(structured_context.get("driver_result_id"))
        if driver_result_id.startswith("mot_drv_"):
            return self._has_explicit_driver_authority(structured_context)
        has_future_authority_ref = any(
            _safe_context_scalar(structured_context.get(key))
            for key in AUTHORITY_GATED_CONTEXT_REF_KEYS
        )
        return not has_future_authority_ref

    def _has_explicit_driver_authority(self, structured_context: dict[str, Any]) -> bool:
        authority = str(
            structured_context.get("state_authority")
            or structured_context.get("authority")
            or ""
        ).strip()
        return authority in DRIVER_RESULT_AUTHORITIES

    def _structured_context_refs(self, refs: Mapping[str, Any]) -> dict[str, Any]:
        context: dict[str, Any] = {}
        for key in STRUCTURED_CONTEXT_KEYS:
            value = refs.get(key)
            if not isinstance(value, Mapping):
                continue
            for item_key, item_value in value.items():
                text_key = str(item_key)
                if text_key not in WORKING_MEMORY_CONTEXT_FIELDS:
                    continue
                if _is_unsafe_context_key(text_key):
                    continue
                if text_key == "repetition" and isinstance(item_value, Mapping):
                    context[text_key] = {
                        str(sub_key): _safe_context_scalar(sub_value)
                        for sub_key, sub_value in item_value.items()
                        if not _is_unsafe_context_key(str(sub_key))
                    }
                    continue
                if text_key == "must_not_imply":
                    context[text_key] = _safe_context_list(item_value)
                    continue
                if isinstance(item_value, bool | int | float):
                    context[text_key] = item_value
                    continue
                safe_value = _safe_context_scalar(item_value)
                if safe_value != "":
                    context[text_key] = safe_value
        return context

    def _context_id_from_refs(
        self,
        turn_input: TurnInput,
        direct_refs: list[dict[str, Any]],
    ) -> str:
        for ref in direct_refs:
            if ref["key"] in {"event_id", "motion_event_id", "body_schema_snapshot_id"}:
                return f"ctx_{self._speech_fragment_key(str(ref['value']))}"
        return f"ctx_{self._speech_fragment_key(turn_input.turn_id or 'turn')}"

    def _emit_context_trace_events(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        working_memory_context: dict[str, Any],
    ) -> None:
        if not working_memory_context:
            return
        safe_refs = list(working_memory_context.get("safe_refs", []))
        pending_refs = list(working_memory_context.get("pending_authority_refs", []))
        ignored_refs = list(working_memory_context.get("ignored_refs", []))
        events.append(
            factory.emit(
                "context.received",
                {
                    "contract_name": "context_received",
                    "context_id": working_memory_context.get("context_id"),
                    "safe_ref_keys": [ref.get("key") for ref in safe_refs],
                    "pending_authority_ref_keys": [ref.get("key") for ref in pending_refs],
                    "ignored_ref_keys": [ref.get("key") for ref in ignored_refs],
                    "working_memory_context": {
                        "observed_at": working_memory_context.get("observed_at"),
                        "stale_after": working_memory_context.get("stale_after"),
                        "freshness": working_memory_context.get("freshness"),
                        "staleness": working_memory_context.get("staleness"),
                        "uncertainty": working_memory_context.get("uncertainty"),
                        "state_authority": working_memory_context.get("state_authority"),
                        "safe_for_thought_core_use": working_memory_context.get(
                            "safe_for_thought_core_use"
                        ),
                        "safe_to_act": False,
                        "not_proven": working_memory_context.get("not_proven"),
                        "must_not_imply": working_memory_context.get("must_not_imply"),
                    },
                },
            )
        )
        if ignored_refs:
            events.append(
                factory.emit(
                    "context.ignored_with_reason",
                    {
                        "contract_name": "context_ignored_with_reason",
                        "context_id": working_memory_context.get("context_id"),
                        "ignored_refs": ignored_refs,
                    },
                )
            )
        if working_memory_context.get("safe_for_thought_core_use") is True:
            events.append(
                factory.emit(
                    "context.used",
                    {
                        "contract_name": "context_used",
                        "context_id": working_memory_context.get("context_id"),
                        "used_by": ["thought_core.response_context"],
                        "safe_to_act": False,
                        "not_proven": working_memory_context.get("not_proven"),
                    },
                )
            )

    def _understand_input(self, turn_input: TurnInput) -> InputFrame:
        return self.input_understanding.understand(
            turn_input,
            pending_state_query=self.pending_state_queries.get(turn_input.session_id),
            pending_action_review=self.pending_action_reviews.get(turn_input.session_id),
        )

    def _emit_input_understood(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        input_frame: InputFrame,
    ) -> None:
        payload = input_frame.to_dict()
        payload["adapter"] = describe_input_understanding(self.input_understanding)
        events.append(factory.emit("input.understood", payload))

    def _speech_issue_key(
        self,
        turn_input: TurnInput,
        input_frame: InputFrame | None = None,
    ) -> str:
        session_key = self._speech_fragment_key(turn_input.session_id or "default")
        if input_frame is not None:
            if input_frame.is_command:
                parts = [
                    input_frame.action_id,
                    input_frame.action_target,
                    input_frame.action_expected_state,
                ]
                key = ":".join(
                    self._speech_fragment_key(part) for part in parts if part
                )
                if key:
                    return f"{session_key}:home:{key}"
            if input_frame.kind == "state_query":
                target = input_frame.target or "state"
                return f"{session_key}:state:{self._speech_fragment_key(target)}"

        pending_action_review = self.pending_action_reviews.get(turn_input.session_id)
        if input_frame is not None and input_frame.kind == "state_feedback":
            if not (isinstance(pending_action_review, dict) and pending_action_review):
                target = input_frame.target or "state"
                return f"{session_key}:state:{self._speech_fragment_key(target)}"

        if (
            isinstance(pending_action_review, dict)
            and pending_action_review
            and self._input_requests_pending_action_review(
                turn_input,
                input_frame,
                pending=pending_action_review,
            )
        ):
            action = pending_action_review.get("action")
            if isinstance(action, dict):
                return self._action_speech_issue_key(session_key, action)

        pending_state_query = self.pending_state_queries.get(turn_input.session_id)
        if isinstance(pending_state_query, dict) and pending_state_query:
            query_id = str(
                pending_state_query.get("state_query_id")
                or pending_state_query.get("target")
                or "state"
            )
            return f"{session_key}:state:{self._speech_fragment_key(query_id)}"

        if input_frame is not None and input_frame.kind == "state_feedback":
            target = input_frame.target or "state"
            return f"{session_key}:state:{self._speech_fragment_key(target)}"

        action_intent = detect_home_action_intent(turn_input.text)
        if action_intent is not None:
            parts = [
                str(getattr(action_intent, "action_id", "") or ""),
                str(getattr(action_intent, "target", "") or ""),
                str(getattr(action_intent, "expected_state", "") or ""),
            ]
            key = ":".join(self._speech_fragment_key(part) for part in parts if part)
            if key:
                return f"{session_key}:home:{key}"

        return f"{session_key}:turn:{self._speech_fragment_key(turn_input.turn_id)}"

    def _action_speech_issue_key(self, session_key: str, action: dict[str, Any]) -> str:
        key = self._action_review_key(action)
        return f"{session_key}:home:{key or 'action'}"

    def _action_review_key(self, action: dict[str, Any]) -> str:
        parts = [
            str(action.get("action_id") or ""),
            str(action.get("target") or ""),
            str(action.get("expected_state") or ""),
        ]
        return ":".join(self._speech_fragment_key(part) for part in parts if part)

    def _emit_stage_update(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        *,
        stage: str,
        speech: str,
        detail: dict[str, Any] | None = None,
        stream_to_speech: bool = False,
    ) -> None:
        previous_fragment = self._last_spoken_fragment(events) or self._last_issue_fragment()
        stage_speech = str(speech or "")
        audible_speech = (
            self._coherent_stream_speech(events, stage_speech, stage=stage)
            if stream_to_speech
            else ""
        )
        payload = {
            "stage": stage,
            "speech": stage_speech,
            "streamed": bool(audible_speech),
            "audible": bool(audible_speech),
        }
        if detail:
            payload["detail"] = detail
        events.append(factory.emit("thought.stage", payload))
        if audible_speech:
            events.append(
                factory.emit(
                    "assistant.speech_delta",
                    {
                        "delta": audible_speech,
                        "channel": "speech",
                        "stage": stage,
                        "partial": True,
                        "speech_context": {
                            "previous_fragment": previous_fragment,
                            "issue_key": self._active_issue_key,
                        },
                    },
                )
            )
            self._remember_stream_speech(audible_speech)

    def _handle_home_action_ambiguity(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        ambiguity: dict[str, Any],
    ) -> None:
        speech = (
            "対象が複数に見えるので、実行せずに止めておくね。"
            "一つずつ、どれを操作するか指定してね。"
        )
        events.append(
            factory.emit(
                "action.clarification_requested",
                {
                    "reason": ambiguity.get("reason") or "ambiguous_home_action",
                    "targets": ambiguity.get("targets") or [],
                    "text_length": len(turn_input.text),
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
            priority="normal",
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "needs_clarification",
                    "reason": "ambiguous_home_action",
                    "targets": ambiguity.get("targets") or [],
                },
            )
        )

    def _handle_home_action_negative_request(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        negative_action: dict[str, Any],
    ) -> None:
        targets = [
            self._home_action_target_label(str(target))
            for target in negative_action.get("targets") or []
        ]
        target_text = "、".join(targets) if targets else "その操作"
        speech = f"了解、{target_text}は操作しないでおくね。"
        events.append(
            factory.emit(
                "action.noop",
                {
                    "reason": negative_action.get("reason")
                    or "negative_home_action_request",
                    "targets": negative_action.get("targets") or [],
                    "text_length": len(turn_input.text),
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
            priority="normal",
        )
        events.append(
            factory.emit(
                "turn.completed",
                {
                    "status": "noop",
                    "reason": "negative_home_action_request",
                    "targets": negative_action.get("targets") or [],
                },
            )
        )

    def _home_action_target_label(self, target: str) -> str:
        return {
            "light": "リビングの電気",
            "fan": "扇風機",
            "aircon": "エアコン",
            "door": "中扉",
            "vacuum": "掃除機",
        }.get(target, target or "その操作")

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
        input_frame: InputFrame | None = None,
    ) -> None:
        speech = self._input_ack_speech(turn_input, input_frame)
        events.append(
            factory.emit(
                "input.acknowledged",
                {
                    "text_length": len(turn_input.text),
                    "speech": speech,
                    "streamed": True,
                    "input_kind": input_frame.kind if input_frame else "",
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
            reflex=True,
        )

    def _input_ack_speech(
        self,
        turn_input: TurnInput,
        input_frame: InputFrame | None = None,
    ) -> str:
        text = turn_input.text.replace(" ", "").replace("　", "")
        if self.pending_confirmations.get(turn_input.session_id):
            if self._is_confirmation_reply(text):
                return "うん、確認したよ。"
            if self._is_confirmation_cancel(text):
                return "うん、止めるね。"
            return "うん、確認中の操作があるよ。"
        if input_frame and input_frame.kind == "state_query":
            return "うん、状態を見てみるね。"
        if input_frame and input_frame.kind == "environment_status_query":
            return "うん、いま分かる状態を確認するね。"
        if input_frame and input_frame.kind == "state_feedback":
            if input_frame.continued_as_command:
                return "うん、状態も受け取って操作も確認するね。"
            return "うん、その状態を覚えるね。"
        pending_action_review = self.pending_action_reviews.get(turn_input.session_id)
        if pending_action_review and self._input_requests_pending_action_review(
            turn_input,
            input_frame,
            pending=pending_action_review,
        ):
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
        if (input_frame and input_frame.kind == "home_command") or detect_home_action_intent(
            turn_input.text
        ) is not None:
            return "うん、操作できるか確認するね。"
        if input_frame and input_frame.kind == "audio_check":
            return "うん、音声入力の受け取り状態を確認するね。"
        return "うん、聞いたよ。"

    def _handle_audio_check_turn(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        input_frame: InputFrame,
    ) -> None:
        speech = (
            "音声入力はテキストとして受け取れています。"
            "ただし、マイク音質やスピーカーから聞こえたかは、この応答だけでは確認できません。"
        )
        events.append(
            factory.emit(
                "audio.status_checked",
                {
                    "input_kind": input_frame.kind,
                    "input_received_as_text": True,
                    "audio_quality_confirmed": False,
                    "speaker_output_confirmed": False,
                    "proof_layer": "text_handoff_only",
                    "non_claims": [
                        "microphone_quality_proven",
                        "speaker_output_heard",
                        "raw_audio_reviewed",
                    ],
                },
            )
        )
        self._emit_response_route_classified(
            events,
            factory,
            turn_input=turn_input,
            response_route="audio_status_check",
            intent_kind=input_frame.kind,
            responder_status="audio_status_check",
            fallback_used=False,
            provider_route="thought-core-audio-status",
            used_llm=False,
            non_claims=[
                "microphone_quality_proven",
                "speaker_output_heard",
                "ordinary_conversation_quality",
                "direct_dify_route_used",
            ],
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
                    "status": "audio_status_check",
                    "input_received_as_text": True,
                    "audio_quality_confirmed": False,
                    "speaker_output_confirmed": False,
                    "pending_action_review_continued": False,
                },
            )
        )

    def _handle_environment_status_query_turn(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        input_frame: InputFrame,
        memory_context: dict[str, Any],
    ) -> None:
        observation = self._call_tool(
            events,
            factory,
            "environment.observe",
            lambda: self.tools.environment_observe(turn_input, reason="status_query"),
        )
        grounding = self._environment_grounding_summary(
            observation,
            memory_context,
            input_frame,
        )
        events.append(factory.emit("environment.grounding_summary", grounding))
        self._emit_response_route_classified(
            events,
            factory,
            turn_input=turn_input,
            response_route="environment_state_grounded",
            intent_kind=input_frame.kind,
            responder_status=str(grounding.get("status") or "environment_status_answer"),
            fallback_used=False,
            provider_route="deterministic_environment_grounding",
            used_llm=False,
            non_claims=list(grounding.get("does_not_prove", [])),
        )
        speech = self._environment_grounded_speech(grounding)
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
                    "status": "environment_status_answer",
                    "query_class": grounding.get("query_class"),
                    "grounding_status": grounding.get("environment_status"),
                    "home_control_bridge_class": grounding.get(
                        "home_control_bridge_class"
                    ),
                    "ha_readiness_class": grounding.get("ha_readiness_class"),
                    "memory_context_used_class": grounding.get(
                        "memory_context_used_class"
                    ),
                    "proof_ceiling": grounding.get("proof_ceiling"),
                    "must_revalidate_current_state": True,
                },
            )
        )

    def _environment_grounding_summary(
        self,
        observation: dict[str, Any],
        memory_context: dict[str, Any],
        input_frame: InputFrame,
    ) -> dict[str, Any]:
        environment = (
            observation.get("environment")
            if isinstance(observation.get("environment"), dict)
            else {}
        )
        facts = observation.get("facts") if isinstance(observation.get("facts"), dict) else {}
        devices = self._safe_environment_devices(facts, environment)
        action_families = self._safe_environment_action_families(environment)
        room_light = self._room_light_from_observation(observation)
        memory_summary = self._safe_memory_grounding_summary(memory_context)
        freshness_class = self._environment_freshness_class(
            observation,
            devices,
            room_light,
        )
        env_status = str(observation.get("status") or "unknown")
        bridge_class, ha_class = self._environment_availability_classes(
            observation,
            action_families,
        )
        query_class = str(
            input_frame.metadata.get("query_class")
            or input_frame.target
            or "current_environment_status"
        )
        return {
            "schema_version": "thought_core_environment_grounding.v0",
            "status": "environment_status_answer",
            "query_class": query_class,
            "input_kind": input_frame.kind,
            "environment_status": env_status,
            "observation_ref_present": bool(observation.get("observation_ref")),
            "observation_source_class": self._observation_source_class(
                observation.get("observation_source")
            ),
            "freshness_class": freshness_class,
            "home_control_bridge_class": bridge_class,
            "ha_readiness_class": ha_class,
            "checktracking_class": "unknown",
            "checkstate_class": self._checkstate_class(room_light),
            "readable_devices": devices[:8],
            "readable_device_count": len(devices),
            "available_action_families": action_families[:8],
            "memory_context_used_class": memory_summary["used_class"],
            "memory_context": memory_summary,
            "authority_ordering": [
                "latest_user_instruction",
                "current_environment_state",
                "safety_boundaries",
                "safe_relevant_memory_reference_only",
                "stale_history_non_authoritative",
            ],
            "current_authority_ordering_result": "current_environment_before_memory",
            "proof_ceiling": self._environment_proof_ceiling(devices, room_light),
            "must_revalidate_current_state": True,
            "raw_private_publication_flags": {
                "raw_prompt_shared": False,
                "raw_transcript_shared": False,
                "provider_payload_shared": False,
                "raw_home_assistant_entity_shared": False,
                "raw_device_identifier_shared": False,
                "raw_memory_shared": False,
                "private_path_shared": False,
            },
            "does_not_prove": [
                "current_physical_appliance_state",
                "home_control_action_success",
                "physical_light_on_off",
                "fan_airflow_or_running_state",
                "door_obstruction_safety",
                "vacuum_path_or_floor_safety",
                "physical_hvac_comfort",
                "durable_memory_truth",
                "provider_backed_conversation_quality",
            ],
        }

    def _environment_grounded_speech(self, grounding: dict[str, Any]) -> str:
        status = str(grounding.get("environment_status") or "unknown")
        freshness = str(grounding.get("freshness_class") or "unknown")
        query_class = str(grounding.get("query_class") or "")
        bridge = str(grounding.get("home_control_bridge_class") or "unknown")
        ha_class = str(grounding.get("ha_readiness_class") or "unknown")
        devices = grounding.get("readable_devices")
        action_families = grounding.get("available_action_families")
        memory = grounding.get("memory_context")
        device_text = self._readable_device_sentence(devices if isinstance(devices, list) else [])
        action_text = self._action_family_sentence(
            action_families if isinstance(action_families, list) else []
        )
        memory_text = self._memory_grounding_sentence(
            memory if isinstance(memory, dict) else {},
            query_class=query_class,
        )

        if status not in {"ok", "available"}:
            return (
                "現在のEnvironment Stateは取得できません。"
                f"状態クラスは{self._safe_status_label(status)}です。"
                "推測では答えず、使える情報が戻るまで現在状態としては断定しません。"
                f"{memory_text}"
            )

        freshness_text = (
            "取得情報は古い可能性があるため、最後に分かっている範囲として答えます。"
            "現在状態としては断定しません。"
            if freshness in {"stale", "unknown", "unavailable"}
            else "現在取得できるEnvironment Stateを優先して見ています。"
        )
        parts = [
            freshness_text,
            f"Home Control/HAは{self._safe_status_label(bridge)}、"
            f"状態読み取りは{self._safe_status_label(ha_class)}として扱います。",
        ]
        if device_text:
            parts.append(device_text)
        if query_class == "home_control_availability" or action_text:
            parts.append(action_text or "操作カタログはこの応答では確認できません。")
        if memory_text:
            parts.append(memory_text)
        parts.append(
            "これはHAやEnvironment State上の要約で、物理状態や安全確認の証明ではありません。"
        )
        return "".join(parts)

    def _safe_environment_devices(
        self,
        facts: dict[str, Any],
        environment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        fact_devices = facts.get("devices")
        if isinstance(fact_devices, list):
            candidates.extend(item for item in fact_devices if isinstance(item, dict))
        appliances = environment.get("appliances")
        if isinstance(appliances, dict):
            for key, appliance in appliances.items():
                if isinstance(appliance, dict):
                    item = dict(appliance)
                    item.setdefault("kind", key)
                    candidates.append(item)
        seen: set[tuple[str, str]] = set()
        safe_devices: list[dict[str, Any]] = []
        for item in candidates:
            kind = self._safe_device_kind(item.get("kind") or item.get("id"))
            if not kind:
                continue
            state_class = self._safe_state_class(item.get("state"))
            key = (kind, state_class)
            if key in seen:
                continue
            seen.add(key)
            safe_devices.append(
                {
                    "kind": kind,
                    "label": self._safe_device_label(kind),
                    "state_class": state_class,
                    "freshness_class": (
                        "stale" if self._as_bool(item.get("stale")) is True else "fresh"
                    ),
                    "proof_ceiling": self._device_proof_ceiling(kind, item),
                }
            )
        return safe_devices

    def _safe_environment_action_families(
        self,
        environment: dict[str, Any],
    ) -> list[str]:
        actions = environment.get("actions")
        if not isinstance(actions, list):
            return []
        families: set[str] = set()
        for action in actions:
            if not isinstance(action, dict):
                continue
            family = self._safe_device_kind(
                action.get("appliance_id")
                or action.get("target")
                or str(action.get("action_id") or "").split("_")[0]
            )
            if family:
                families.add(family)
        return sorted(families)

    def _safe_memory_grounding_summary(
        self,
        memory_context: dict[str, Any],
    ) -> dict[str, Any]:
        item_count = self._int_value(memory_context.get("item_count"), 0)
        status = str(memory_context.get("status") or "unknown")
        return {
            "status": status,
            "item_count": item_count,
            "used_class": "reference_only" if item_count > 0 else "not_available",
            "source_class": self._memory_source_class(memory_context.get("source")),
            "current_authority": False,
            "must_revalidate_current_state": True,
            "raw_memory_shared": False,
        }

    def _environment_freshness_class(
        self,
        observation: dict[str, Any],
        devices: list[dict[str, Any]],
        room_light: dict[str, Any],
    ) -> str:
        status = str(observation.get("status") or "unknown")
        if status in {"skipped", "failed", "unavailable"}:
            return "unavailable"
        if room_light and self._as_bool(room_light.get("stale")) is True:
            return "stale"
        if any(device.get("freshness_class") == "stale" for device in devices):
            return "stale"
        return "fresh" if status == "ok" else "unknown"

    def _environment_availability_classes(
        self,
        observation: dict[str, Any],
        action_families: list[str],
    ) -> tuple[str, str]:
        status = str(observation.get("status") or "unknown")
        source = str(observation.get("observation_source") or "").lower()
        if status == "ok":
            bridge = "bridge_available" if action_families else "bridge_skipped"
            return bridge, "ha_state_surface_readable"
        if "unconfigured" in source or status == "skipped":
            return "config_missing", "ha_not_configured"
        if status == "failed":
            return "bridge_unavailable", "ha_unavailable"
        return "unknown", "unknown"

    def _observation_source_class(self, source: Any) -> str:
        text = str(source or "").lower()
        if not text:
            return "unknown"
        if "mock" in text:
            return "environment_state_mock"
        if "http" in text or "environment-state-server" in text:
            return "environment_state_surface"
        if "unconfigured" in text:
            return "environment_state_unconfigured"
        return "environment_state_surface"

    def _checkstate_class(self, room_light: dict[str, Any]) -> str:
        if not room_light:
            return "unknown"
        if room_light.get("available") is False:
            return "unavailable"
        if self._as_bool(room_light.get("stale")) is True:
            return "stale"
        state = str(room_light.get("state") or "").lower()
        if state in {"on", "off", "daylight"}:
            return "matched"
        return "unknown"

    def _environment_proof_ceiling(
        self,
        devices: list[dict[str, Any]],
        room_light: dict[str, Any],
    ) -> str:
        if room_light and self._as_bool(room_light.get("stale")) is True:
            return "external_observation_required"
        if any(device.get("proof_ceiling") == "HA_visible_state_only" for device in devices):
            return "HA_visible_state_only"
        if devices:
            return "external_observation_required"
        return "physical_proof_not_available"

    def _device_proof_ceiling(self, kind: str, item: dict[str, Any]) -> str:
        source = str(item.get("source") or item.get("authority") or "").lower()
        if self._as_bool(item.get("stale")) is True:
            return "external_observation_required"
        if "home_assistant" in source or "ha" in source:
            return "HA_visible_state_only"
        if kind in {"light", "fan"}:
            return "external_observation_required"
        if kind in {"door", "vacuum", "aircon"}:
            return "HA_visible_state_only"
        return "physical_proof_not_available"

    def _safe_device_kind(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "living_room_light": "light",
            "light": "light",
            "fan": "fan",
            "aircon": "aircon",
            "climate": "aircon",
            "door": "door",
            "cover": "door",
            "vacuum": "vacuum",
        }
        return aliases.get(text, "")

    def _safe_device_label(self, kind: str) -> str:
        return {
            "light": "リビングの電気",
            "fan": "扇風機",
            "aircon": "エアコン",
            "door": "ドア/カバー",
            "vacuum": "掃除機",
        }.get(kind, "家電")

    def _safe_state_class(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "on": "on",
            "off": "off",
            "open": "open",
            "closed": "closed",
            "closing": "moving",
            "opening": "moving",
            "running": "running",
            "cleaning": "running",
            "paused": "paused",
            "idle": "idle",
            "docked": "docked",
            "returning": "returning",
            "heat": "active",
            "cool": "active",
            "auto": "active",
            "unknown": "unknown",
            "unavailable": "unavailable",
        }
        return aliases.get(text, "unknown")

    def _safe_status_label(self, value: str) -> str:
        labels = {
            "ok": "取得済み",
            "available": "利用可能",
            "bridge_available": "利用可能",
            "bridge_skipped": "状態面は確認済み、操作カタログは未確認",
            "bridge_unavailable": "利用不可",
            "config_missing": "未設定",
            "ha_state_surface_readable": "読み取り可能",
            "ha_unavailable": "利用不可",
            "ha_not_configured": "未設定",
            "fresh": "新しい",
            "stale": "古い可能性あり",
            "unknown": "不明",
            "failed": "取得失敗",
            "skipped": "未設定",
        }
        return labels.get(value, "不明")

    def _readable_device_sentence(self, devices: list[Any]) -> str:
        fragments: list[str] = []
        state_labels = {
            "on": "オン扱い",
            "off": "オフ扱い",
            "open": "開いている扱い",
            "closed": "閉じている扱い",
            "moving": "移動中扱い",
            "running": "動作中扱い",
            "paused": "一時停止扱い",
            "idle": "待機扱い",
            "docked": "帰還済み扱い",
            "returning": "帰還中扱い",
            "active": "有効扱い",
            "unavailable": "利用不可扱い",
            "unknown": "不明",
        }
        for device in devices[:4]:
            if not isinstance(device, dict):
                continue
            label = str(device.get("label") or "家電")
            state = str(device.get("state_class") or "unknown")
            freshness = str(device.get("freshness_class") or "unknown")
            prefix = "最後に分かっている範囲では" if freshness == "stale" else ""
            fragments.append(f"{prefix}{label}は{state_labels.get(state, '不明')}です")
        if not fragments:
            return "現在この応答で要約できる家電状態はありません。"
        return "読める状態は、" + "、".join(fragments) + "。"

    def _action_family_sentence(self, action_families: list[Any]) -> str:
        labels = [self._safe_device_label(str(family)) for family in action_families[:5]]
        labels = [label for label in labels if label]
        if not labels:
            return ""
        return "操作カタログ上は、" + "、".join(labels) + "の系統を確認できます。"

    def _memory_grounding_sentence(
        self,
        memory: dict[str, Any],
        *,
        query_class: str,
    ) -> str:
        count = self._int_value(memory.get("item_count"), 0)
        if count <= 0:
            if query_class == "memory_grounded_status":
                return "関連メモリは見つからないため、現在の情報だけで答えます。"
            return ""
        return (
            f"関連メモリは{count}件、参考情報として確認しました。"
            "ただし現在状態の根拠にはせず、最新のEnvironment Stateを優先します。"
        )

    def _memory_source_class(self, source: Any) -> str:
        text = str(source or "").lower()
        if "mock" in text:
            return "mock_memory"
        if "local" in text:
            return "local_memory"
        if "memory" in text:
            return "memory_retrieve"
        return "unknown"

    def _handle_general_turn(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
    ) -> None:
        events.append(factory.emit("responder.started", describe_responder(self.responder)))
        response_context = self._response_context(
            events,
            current_stage="general_responder",
        )
        result = self.responder.respond(
            turn_input,
            response_context=response_context,
        )
        fallback_used = str(result.status or "").startswith("local_fallback")
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
                    "response_context": response_context,
                },
            )
        )
        self._emit_response_route_classified(
            events,
            factory,
            turn_input=turn_input,
            response_route="ordinary_conversation",
            intent_kind="general",
            responder_status=result.status,
            fallback_used=fallback_used,
            provider_route=result.provider,
            used_llm=result.used_llm,
            non_claims=[
                "microphone_quality_proven",
                "speaker_output_heard",
                "device_action_proven",
                "direct_dify_route_used",
            ],
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

    def _emit_response_route_classified(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        *,
        turn_input: TurnInput,
        response_route: str,
        intent_kind: str,
        responder_status: str,
        fallback_used: bool,
        provider_route: str,
        used_llm: bool,
        non_claims: list[str],
    ) -> None:
        events.append(
            factory.emit(
                "thought_core.response_route_classified",
                {
                    "schema_version": "thought_core_response_route.v0",
                    "turn_id": turn_input.turn_id,
                    "trace_id": _safe_context_scalar(
                        turn_input.context_refs.get("trace_id", "")
                    ),
                    "response_route": response_route,
                    "intent_kind": intent_kind,
                    "responder_status": responder_status,
                    "fallback_used": fallback_used,
                    "provider_route": provider_route,
                    "used_llm": used_llm,
                    "direct_dify_used": False,
                    "non_claims": list(non_claims),
                },
            )
        )

    def _handle_motion_request_turn(
        self,
        events: list[ThoughtEvent],
        factory: EventFactory,
        turn_input: TurnInput,
        input_frame: InputFrame,
    ) -> None:
        events.append(
            factory.emit(
                "motion_or_action_intent.selected",
                {
                    "contract_name": "motion_or_action_intent_selected",
                    "intent_kind": "motion",
                    "input_kind": input_frame.kind,
                    "contextual": bool(self._active_working_memory_context),
                    "working_memory_context_id": self._active_working_memory_context.get(
                        "context_id",
                        "",
                    ),
                },
            )
        )
        event = factory.emit("motion.requested", {})
        event.data.update(self._motion_request_payload(turn_input, input_frame, event))
        events.append(event)
        self._handle_general_turn(events, factory, turn_input)

    def _motion_request_payload(
        self,
        turn_input: TurnInput,
        input_frame: InputFrame,
        event: ThoughtEvent,
    ) -> dict[str, Any]:
        metadata = input_frame.metadata.get("motion_request")
        request = dict(metadata) if isinstance(metadata, dict) else {}
        turn_key = self._speech_fragment_key(turn_input.turn_id or "turn")
        legacy_kind = str(request.get("kind") or "expression_motion")
        motion_kind = self._motion_stimulus_kind(legacy_kind)
        tracks = self._motion_track_mask(legacy_kind)
        required_tracks = self._motion_required_tracks(legacy_kind)
        priority_tracks = self._motion_priority_tracks(legacy_kind)
        optional_tracks = [
            track for track in priority_tracks if track not in set(required_tracks)
        ]
        motion_event_id = f"mot_evt_{turn_key}_001"
        stimulus_id = f"mot_stim_{turn_key}_{motion_kind}"
        stimulus_instance_id = f"mot_inst_{turn_key}_001"
        requirements: dict[str, Any] = {
            "required_tracks": required_tracks,
            "optional_tracks": optional_tracks,
            "compatible_model_types": ["vrm"],
            "provenance_required": True,
            "allow_degraded": True,
            "allow_fallback": True,
        }
        requirements.update(self._motion_expression_visible_requirements(legacy_kind))
        return {
            "schema_version": "motion_stimulus.v0",
            "motion_event_id": motion_event_id,
            "stimulus_id": stimulus_id,
            "stimulus_instance_id": stimulus_instance_id,
            "source_class": "user_command",
            "source_family": "user_or_operator_command",
            "source_origin": "thought_core",
            "requested_at": event.timestamp,
            "kind": motion_kind,
            "request_mode": self._motion_request_mode(legacy_kind),
            "phase": "queued",
            "lifecycle_state": "queued",
            "safe_visible_state": "requested",
            "safe_display_name": self._motion_safe_display_name(legacy_kind, request),
            "target_model_type": "vrm",
            "track_mask": tracks,
            "priority_by_track": self._motion_priority_by_track(
                priority_tracks,
                required_tracks,
            ),
            "requirements": requirements,
            "payload_ref": self._motion_payload_ref(legacy_kind),
            "intensity": self._motion_intensity(str(request.get("intensity") or "medium")),
            "duration_ms": self._motion_duration_ms(request.get("duration_ms")),
            "loop": motion_kind == "dance_sequence",
            "loop_count": 0,
            "interrupt_policy": self._motion_interrupt_policy(legacy_kind),
            "fallback_state": self._motion_fallback_state(legacy_kind),
            "fallback_used": False,
            "stop_reason": self._motion_stop_reason(legacy_kind),
            "trace": {
                "event_id": event.event_id,
                "turn_id": turn_input.turn_id,
                "selection_id": f"mot_sel_{turn_key}_001",
                "runtime_result_id": f"mot_res_{turn_key}_pending_001",
                "motion_event_id": motion_event_id,
                "stimulus_id": stimulus_id,
                "stimulus_instance_id": stimulus_instance_id,
            },
            "redaction": {
                "redaction_status": "summary_only",
                "redaction_profile": "motion_contract_public_v0",
                "shareability_class": "source_static_fixture",
                "proof_layer": "source_static",
            },
            "safety": {
                "raw_user_text_shared": False,
                "raw_prompt_shared": False,
                "raw_media_shared": False,
                "raw_path_shared": False,
                "raw_asset_filename_shared": False,
                "provider_payload_shared": False,
                "home_assistant_route": False,
            },
        }

    def _motion_stimulus_kind(self, legacy_kind: str) -> str:
        if legacy_kind == "dance":
            return "dance_sequence"
        if legacy_kind == "expression_motion":
            return "expression"
        if legacy_kind == "cancel":
            return "stop"
        return "action_indicator" if legacy_kind == "action_indicator" else "gesture"

    def _motion_safe_display_name(
        self,
        legacy_kind: str,
        request: dict[str, Any],
    ) -> str:
        if legacy_kind == "dance":
            rhythm = str(request.get("rhythm_hint") or "none")
            return "Music dance" if rhythm == "music_sync_requested" else "Dance sequence"
        if legacy_kind == "expression_motion":
            style = str(request.get("style") or "neutral")
            return "Happy expression" if style == "happy" else "Expression motion"
        if legacy_kind == "cancel":
            return "Stop motion"
        return "Action indicator"

    def _motion_request_mode(self, legacy_kind: str) -> str:
        if legacy_kind == "expression_motion":
            return "apply"
        if legacy_kind == "cancel":
            return "stop"
        return "play"

    def _motion_track_mask(self, legacy_kind: str) -> list[str] | dict[str, Any]:
        if legacy_kind == "dance":
            return [
                "body_root",
                "spine",
                "chest",
                "neck",
                "head",
                "left_arm",
                "right_arm",
                "left_hand",
                "right_hand",
                "balance",
            ]
        if legacy_kind == "expression_motion":
            return {"scope": "face_head", "channels": ["expression_weight"]}
        if legacy_kind == "cancel":
            return ["body_root", "spine", "head", "face"]
        return ["head", "face"]

    def _motion_priority_tracks(self, legacy_kind: str) -> list[str]:
        if legacy_kind == "expression_motion":
            return ["face", "head", "neck"]
        tracks = self._motion_track_mask(legacy_kind)
        return tracks if isinstance(tracks, list) else []

    def _motion_required_tracks(self, legacy_kind: str) -> list[str]:
        if legacy_kind == "dance":
            return ["body_root", "spine"]
        if legacy_kind == "expression_motion":
            return ["face"]
        if legacy_kind == "cancel":
            return ["body_root"]
        return ["head"]

    def _motion_interrupt_policy(self, legacy_kind: str) -> str:
        return "stop" if legacy_kind == "cancel" else "replace_same_track"

    def _motion_fallback_state(self, legacy_kind: str) -> str:
        return "stop_to_idle" if legacy_kind == "cancel" else "neutral_idle"

    def _motion_stop_reason(self, legacy_kind: str) -> str:
        return "user_requested" if legacy_kind == "cancel" else "none"

    def _motion_priority_by_track(
        self,
        tracks: list[str],
        required_tracks: list[str],
    ) -> list[dict[str, Any]]:
        required = set(required_tracks)
        return [
            {"track": track, "priority": 70 if track in required else 35}
            for track in tracks
        ]

    def _motion_intensity(self, intensity: str) -> str:
        return {
            "low": "subtle",
            "medium": "normal",
            "high": "expressive",
        }.get(intensity, "normal")

    def _motion_duration_ms(self, value: Any) -> int:
        if value is None:
            return 10000
        try:
            duration_ms = int(value)
        except (TypeError, ValueError):
            return 10000
        return min(max(duration_ms, 0), 600000)

    def _motion_payload_ref(self, legacy_kind: str) -> str:
        if legacy_kind == "dance":
            return "motion.thought_core.dance_sequence.v0"
        if legacy_kind == "expression_motion":
            return "motion.thought_core.expression_visible.v0"
        if legacy_kind == "cancel":
            return "motion.thought_core.stop.v0"
        return "motion.thought_core.action_indicator.v0"

    def _motion_expression_visible_requirements(
        self,
        legacy_kind: str,
    ) -> dict[str, Any]:
        if legacy_kind != "expression_motion":
            return {}
        return {
            "expression_profile_ref": "motion.runtime.vrm_expression_weights.v0",
            "expected_visible_change": "face_expression",
            "expected_roi": "avatar_face_head",
        }

    def _response_context(
        self,
        events: list[ThoughtEvent],
        *,
        current_stage: str,
        action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_fragment = self._last_spoken_fragment(events) or self._last_issue_fragment()
        recent_fragments = self._compact_recent_fragments()
        context: dict[str, Any] = {
            "previous_fragment": self._compact_speech_fragment(previous_fragment),
            "issue_key": self._active_issue_key,
            "recent_fragments": recent_fragments,
            "current_stage": current_stage,
        }
        if self._active_working_memory_context.get("safe_for_thought_core_use") is True:
            context["working_memory_context"] = dict(self._active_working_memory_context)
        if action:
            context["action_id"] = str(action.get("action_id") or "")
            context["target"] = str(action.get("target") or "")
            context["expected_state"] = str(action.get("expected_state") or "")
        return {key: value for key, value in context.items() if value not in ("", [], {})}

    def _compact_recent_fragments(self) -> list[str]:
        fragments: list[str] = []
        if self._active_issue_key:
            fragments.extend(self.recent_speech_by_issue.get(self._active_issue_key, []))
        if self._active_session_id:
            fragments.extend(self.recent_speech_by_session.get(self._active_session_id, []))
        compacted: list[str] = []
        seen: set[str] = set()
        for fragment in fragments[-12:]:
            compact = self._compact_speech_fragment(fragment)
            key = self._speech_fragment_key(compact)
            if not compact or key in seen:
                continue
            seen.add(key)
            compacted.append(compact)
        return compacted[-6:]

    def _compact_speech_fragment(self, fragment: str) -> str:
        text = strip_persona_tags(str(fragment or "")).strip()
        if len(text) <= 80:
            return text
        return f"{text[:80]}..."

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
        reflex: bool = False,
    ) -> None:
        phrase_generation = self._generate_visible_phrase(
            events,
            speech=speech,
            display=display,
            emotion=emotion,
            motion=motion,
            priority=priority,
            reflex=reflex,
        )
        if phrase_generation.get("required_failed"):
            events.append(
                factory.emit(
                    "phrase.generation_failed",
                    {
                        "boundary": TURN_RESPONDER_BOUNDARY,
                        "reason": phrase_generation.get("status")
                        or "llm_visible_speech_required",
                        "detail": phrase_generation.get("detail") or "",
                        "semantic_draft": speech,
                    },
                )
            )
            return
        if phrase_generation.get("used_llm"):
            speech = str(phrase_generation.get("speech") or speech)
            display = str(phrase_generation.get("display") or speech)
        original_speech = speech
        previous_fragment = self._last_spoken_fragment(events) or self._last_issue_fragment()
        speech = self._coherent_stream_speech(events, speech)
        if not speech:
            return
        display_matches_speech = display == original_speech
        if display == original_speech:
            display = speech
        else:
            display = self._dedupe_repeated_speech(display)
        persona_message = self.persona.apply(speech, emotion=emotion, motion=motion)
        speech = persona_message.speech
        emotion = persona_message.emotion
        motion = persona_message.motion
        if display_matches_speech:
            display = speech
        message_index = sum(1 for event in events if event.type == "assistant.message") + 1
        message_id = f"msg_{self._speech_fragment_key(factory.turn_id)}_{message_index:03d}"
        events.append(
            factory.emit(
                "assistant.speech_delta",
                {
                    "message_id": message_id,
                    "delta": speech,
                    "channel": "speech",
                    "phrase_generation": self._speech_generation_metadata(
                        phrase_generation
                    ),
                    "speech_context": {
                        "previous_fragment": previous_fragment,
                        "issue_key": self._active_issue_key,
                    },
                },
            )
        )
        events.append(
            factory.emit(
                "assistant.message",
                {
                    "message_id": message_id,
                    "speech": speech,
                    "display": display,
                    "emotion": emotion,
                    "motion": motion,
                    "priority": priority,
                    "phrase_generation": self._speech_generation_metadata(
                        phrase_generation
                    ),
                    "speech_context": {
                        "previous_fragment": previous_fragment,
                        "issue_key": self._active_issue_key,
                    },
                },
            )
        )
        self._remember_stream_speech(speech)

    def _generate_visible_phrase(
        self,
        events: list[ThoughtEvent],
        *,
        speech: str,
        display: str,
        emotion: str,
        motion: str,
        priority: str,
        reflex: bool = False,
    ) -> dict[str, Any]:
        if reflex:
            return {
                "enabled": False,
                "used_llm": False,
                "status": "scripted_reflex_allowed",
                "reflex": True,
            }
        if not self.llm_visible_speech:
            return {"enabled": False, "used_llm": False, "status": "disabled"}
        turn_input = self._active_turn_input
        if turn_input is None:
            return {"enabled": True, "used_llm": False, "status": "no_active_turn"}
        response_context = self._response_context(
            events,
            current_stage="visible_phrase_generation",
        )
        response_context.update(
            {
                "response_goal": (
                    "Generate this assistant-visible phrase in natural Japanese "
                    "from the semantic facts. Do not copy semantic_draft verbatim. "
                    "Prefer a concise continuation over repeating the device name."
                ),
                "semantic_draft": speech,
                "display_draft": display,
                "required_facts": self._visible_phrase_required_facts(speech),
                "forbidden_claims": [
                    "do not claim a device action happened unless the draft says it did",
                    "do not ask the user to confirm when the draft is a completed action",
                    "do not mention internal event names or local fallback",
                    "do not start with stock acknowledgements like 了解",
                    "do not copy labels such as カメラ推定では when a softer phrase works",
                ],
                "style_rules": [
                    "one short sentence is usually enough",
                    "if the previous phrase named the device, refer to it as その状態 or omit the repeated name",
                    "sound like a spoken assistant, not a test fixture",
                ],
                "visible_phrase_contract": "llm-authored-visible-speech-v1",
                "emotion": emotion,
                "motion": motion,
                "priority": priority,
            }
        )
        try:
            result = self.responder.respond(
                turn_input,
                response_context=response_context,
            )
        except Exception as exc:  # pragma: no cover - defensive responder boundary
            return {
                "enabled": True,
                "used_llm": False,
                "status": "responder_error",
                "detail": str(exc),
                "required_failed": self.require_llm_visible_speech,
            }
        if result.used_llm:
            return {
                "enabled": True,
                "used_llm": True,
                "status": result.status,
                "adapter_kind": result.adapter_kind,
                "provider": result.provider,
                "model": result.model,
                "speech": result.speech,
                "display": result.display,
                "metadata": result.metadata,
            }
        return {
            "enabled": True,
            "used_llm": False,
            "status": result.status,
            "adapter_kind": result.adapter_kind,
            "provider": result.provider,
            "model": result.model,
            "detail": result.detail,
            "required_failed": self.require_llm_visible_speech,
        }

    def _visible_phrase_required_facts(self, speech: str) -> list[str]:
        facts: list[str] = []
        for token in (
            "電気",
            "リビング",
            "エアコン",
            "扇風機",
            "中扉",
            "掃除機",
            "つけ",
            "消",
            "開け",
            "閉め",
            "一時停止",
            "すでに",
            "確認",
            "実行",
        ):
            if token in speech:
                facts.append(token)
        return facts[:8]

    def _speech_generation_metadata(
        self,
        phrase_generation: dict[str, Any],
    ) -> dict[str, Any]:
        keys = (
            "enabled",
            "used_llm",
            "status",
            "adapter_kind",
            "provider",
            "model",
            "required_failed",
            "reflex",
        )
        return {
            key: phrase_generation.get(key)
            for key in keys
            if key in phrase_generation
        }

    def _coherent_stream_speech(
        self,
        events: list[ThoughtEvent],
        speech: str,
        *,
        stage: str = "",
    ) -> str:
        if stage:
            speech = self._stage_continuation_speech(events, speech, stage=stage)
        speech = self._dedupe_repeated_speech(speech)
        fragments = self._speech_fragments(speech)
        if not fragments:
            return speech
        previous = self._spoken_fragment_keys(events) | self._issue_fragment_keys()
        kept = [
            fragment
            for fragment in fragments
            if self._speech_fragment_key(fragment) not in previous
        ]
        if len(kept) == len(fragments):
            return speech
        if kept:
            return self._continuation_speech(kept)
        fallback = self._repeat_fallback_speech(speech, stage=stage)
        fallback_fragments = self._speech_fragments(fallback)
        fallback_kept = [
            fragment
            for fragment in fallback_fragments
            if self._speech_fragment_key(fragment) not in previous
        ]
        if not fallback_kept:
            return ""
        if len(fallback_kept) == len(fallback_fragments):
            return fallback
        return self._continuation_speech(fallback_kept)

    def _stage_continuation_speech(
        self,
        events: list[ThoughtEvent],
        speech: str,
        *,
        stage: str,
    ) -> str:
        if not self._last_spoken_fragment(events):
            return speech
        stage_speech = {
            "memory.retrieve": "まず、関連しそうな記憶を短く確認します。",
            "environment.observe.before_action": "次に、いまの環境を見ます。",
            "target_state.generate": "それを踏まえて、望む状態を整理します。",
            "home.preview": "続けて、使える操作を確認します。",
            "command.plan": "現在との差分から、実行内容を決めます。",
            "environment.observe.after_action": "反映を待って、環境を見直します。",
            "action.review": "その結果が望んだ状態か判定します。",
        }
        return stage_speech.get(stage, speech)

    def _dedupe_repeated_speech(self, speech: str) -> str:
        fragments = self._speech_fragments(speech)
        if len(fragments) <= 1:
            return speech
        seen: set[str] = set()
        kept: list[str] = []
        for fragment in fragments:
            key = self._speech_fragment_key(fragment)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            kept.append(fragment)
        return "".join(kept)

    def _spoken_fragment_keys(self, events: list[ThoughtEvent]) -> set[str]:
        keys: set[str] = set()
        for event in events:
            if event.type != "assistant.speech_delta":
                continue
            delta = str(event.data.get("delta") or "")
            for fragment in self._speech_fragments(delta):
                key = self._speech_fragment_key(fragment)
                if key:
                    keys.add(key)
        return keys

    def _issue_fragment_keys(self) -> set[str]:
        if not self._active_issue_key:
            return set()
        remembered = self.recent_speech_by_issue.get(self._active_issue_key, [])
        return {
            key
            for fragment in remembered
            if (key := self._speech_fragment_key(fragment))
        }

    def _last_spoken_fragment(self, events: list[ThoughtEvent]) -> str:
        for event in reversed(events):
            if event.type != "assistant.speech_delta":
                continue
            fragments = self._speech_fragments(str(event.data.get("delta") or ""))
            if fragments:
                return fragments[-1]
        return ""

    def _last_issue_fragment(self) -> str:
        if not self._active_issue_key:
            return ""
        remembered = self.recent_speech_by_issue.get(self._active_issue_key, [])
        for fragment in reversed(remembered):
            if str(fragment or "").strip():
                return str(fragment)
        return ""

    def _remember_stream_speech(self, speech: str) -> None:
        if not self._active_session_id:
            return
        fragments = self._speech_fragments(speech)
        if not fragments:
            return
        remembered = self.recent_speech_by_session.setdefault(self._active_session_id, [])
        remembered.extend(fragments)
        del remembered[:-12]
        if self._active_issue_key:
            issue_remembered = self.recent_speech_by_issue.setdefault(
                self._active_issue_key,
                [],
            )
            issue_remembered.extend(fragments)
            del issue_remembered[:-40]

    def _speech_fragments(self, speech: str) -> list[str]:
        fragments: list[str] = []
        buffer: list[str] = []
        for char in str(speech or "").strip():
            buffer.append(char)
            if char in {"。", "！", "？", "!", "?"}:
                fragment = "".join(buffer).strip()
                if fragment:
                    fragments.append(fragment)
                buffer = []
        tail = "".join(buffer).strip()
        if tail:
            fragments.append(tail)
        return fragments

    def _speech_fragment_key(self, fragment: str) -> str:
        drop_chars = set(" \t\r\n　。！？!?、，,.・/／「」『』（）()[]【】")
        fragment = strip_persona_tags(fragment)
        return "".join(
            char.lower()
            for char in str(fragment or "")
            if char not in drop_chars
        )

    def _continuation_speech(self, fragments: list[str]) -> str:
        text = "".join(fragments).strip()
        if not text:
            return "続けて確認します。"
        if text.startswith(("あと", "次で", "まだ", "ここまで", "何回か")):
            return text
        return f"その続きで、{text}"

    def _repeat_fallback_speech(self, speech: str, *, stage: str) -> str:
        if "確認できませんでした" in speech or "確認できない" in speech:
            return "まだ確証が取れていません。次の判断に移ります。"
        stage_fallbacks = {
            "memory.retrieve": "記憶の確認は済んでいます。続けます。",
            "environment.observe.before_action": "続けて環境を見ています。",
            "target_state.generate": "その情報から望む状態を整理します。",
            "home.preview": "使える操作の確認を続けます。",
            "command.plan": "差分から実行内容を絞ります。",
            "environment.observe.after_action": "反映後の状態をもう一度見ます。",
            "action.review": "確認結果を判定します。",
        }
        return stage_fallbacks.get(stage, "続けて確認します。")

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
            action,
            action_id,
            expected_state,
            room_light,
            environment,
        )
        return {"speech_suffix": prompt, "pending": pending, "wait_matched": True}

    def _build_post_action_pending(
        self,
        turn_input: TurnInput,
        action: dict[str, Any],
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
        pending["action"] = dict(action)
        pending["retry_budget"] = self._action_review_policy(action)
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
                "electric_on_probability": evidence.get("electric_on_probability"),
                "daylight_present_probability": evidence.get("daylight_present_probability"),
                "dark_probability": evidence.get("dark_probability"),
                "confidence": evidence.get("confidence"),
                "model": evidence.get("model"),
                "sequence": evidence.get("sequence"),
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
                return self._effective_room_light(queries["room_light"])
        facts = observation.get("facts")
        if isinstance(facts, dict):
            queries = facts.get("state_queries")
            if isinstance(queries, dict) and isinstance(queries.get("room_light"), dict):
                return self._effective_room_light(queries["room_light"])
        return {}

    def _effective_room_light(self, room_light: dict[str, Any]) -> dict[str, Any]:
        projected = dict(room_light)
        effective_state = str(projected.get("effective_state") or "").strip().lower()
        effective_confidence = str(
            projected.get("effective_confidence_label") or ""
        ).strip().lower()
        if effective_state not in {"on", "off"}:
            return projected
        if effective_confidence not in {"medium", "high"}:
            return projected
        projected.setdefault("raw_state", projected.get("state"))
        projected.setdefault("raw_confidence_label", projected.get("confidence_label"))
        projected["state"] = effective_state
        projected["confidence_label"] = effective_confidence
        projected["authority"] = (
            projected.get("effective_authority")
            or projected.get("authority")
            or "environment_state_server.calibration"
        )
        if projected.get("effective_answer_hint"):
            projected["answer_hint"] = projected.get("effective_answer_hint")
        projected["effective_projection_applied"] = True
        return projected

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
            if self._room_light_authority_is_home_assistant(room_light):
                speech = "Home Assistant上では、リビングの電気はついている扱いです。カメラ判定は補助情報として見ています。"
                return {"speech": speech, "display": speech}
            prefix = "学習補正込みでは" if room_light.get("effective_projection_applied") else "カメラ推定では"
            speech = f"{prefix}、リビングの電気はついているように見えます。"
            return {"speech": speech, "display": speech}
        if state == "off":
            if self._room_light_authority_is_home_assistant(room_light):
                speech = "Home Assistant上では、リビングの電気は消えている扱いです。カメラ判定は補助情報として見ています。"
                return {"speech": speech, "display": speech}
            prefix = "学習補正込みでは" if room_light.get("effective_projection_applied") else "カメラ推定では"
            speech = f"{prefix}、リビングの電気は消えているように見えます。"
            return {"speech": speech, "display": speech}
        hint = str(room_light.get("answer_hint") or "").strip()
        speech = (
            f"カメラ推定ではまだ判断できません。{hint}"
            if hint
            else "カメラ推定ではまだ判断できません。"
        )
        return {"speech": speech, "display": speech}

    def _room_light_authority_is_home_assistant(self, room_light: dict[str, Any]) -> bool:
        authority = str(
            room_light.get("authority") or room_light.get("effective_authority") or ""
        ).lower()
        return "home_assistant" in authority

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
            "fan_on": ("扇風機をつける", "扇風機をつける操作を送信したよ。"),
            "fan_off": ("扇風機を消す", "扇風機を消す操作を送信したよ。"),
            "aircon_on": ("エアコンをつける", "エアコンをつける操作を送信したよ。"),
            "aircon_off": ("エアコンを消す", "エアコンを消す操作を送信したよ。"),
            "door_open": ("中扉を開ける", "中扉を開ける操作を送信したよ。"),
            "door_close": ("中扉を閉める", "中扉を閉める操作を送信したよ。"),
            "door_stop": ("中扉を止める", "中扉を止める操作を送信したよ。"),
            "vacuum_start": ("掃除機を動かす", "掃除機を動かす操作を送信したよ。"),
            "vacuum_return": ("掃除機を戻す", "掃除機を戻す操作を送信したよ。"),
            "vacuum_pause": ("掃除機を一時停止する", "掃除機を一時停止する操作を送信したよ。"),
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
                "success_speech": f"{target_name}を消す操作を送信したよ。",
                "success_display": f"{target_name}を消す操作を送信しました",
                "feedback_speech": "電気が消えたか確認できませんでした。状態を確認してもらえますか？",
            }
        return {
            "before_speech": f"了解、{target_name}をつけるね。",
            "before_display": f"{target_name}をつけます",
            "success_speech": f"{target_name}をつける操作を送信したよ。",
            "success_display": f"{target_name}をつける操作を送信しました",
            "feedback_speech": "電気がついたか確認できませんでした。状態を確認してもらえますか？",
        }

    def _canonical_action_result_response(
        self,
        execute_result: dict[str, Any],
        messages: dict[str, str],
        post_action_feedback: dict[str, Any],
    ) -> dict[str, str]:
        speech = (
            str(execute_result.get("speak") or execute_result.get("message") or "").strip()
            or messages["success_speech"]
        )
        suffix = str(post_action_feedback.get("speech_suffix") or "").strip()
        if suffix:
            speech = f"{speech} {suffix}"
        return {"speech": speech, "display": speech}

    def _already_satisfied_message(self, action: dict[str, Any]) -> str:
        target_name = str(action.get("target_name") or "対象").strip()
        expected_state = str(action.get("expected_state") or "").strip()
        if expected_state == "on":
            return f"{target_name}はすでについています。"
        if expected_state == "off":
            return f"{target_name}はすでに消えています。"
        return f"{target_name}はすでに望んだ状態です。"

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


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "")
    if not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "on", "yes"}


def _first_nonempty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _is_unsafe_context_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in UNSAFE_CONTEXT_KEY_PARTS)


def _safe_context_scalar(value: Any) -> str:
    if isinstance(value, bool | int | float):
        return str(value)
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.strip().split())
    if not compact:
        return ""
    if _unsafe_context_value_reason(compact):
        return ""
    return compact[:CONTEXT_VALUE_MAX_CHARS]


def _safe_context_list(value: Any, *, defaults: list[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return list(defaults or [])
    safe_items: list[str] = []
    for item in value[:12]:
        safe_value = _safe_context_scalar(item)
        if safe_value:
            safe_items.append(safe_value)
    return safe_items or list(defaults or [])


def _unsafe_context_value_reason(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    compact = " ".join(value.strip().split())
    lowered = compact.lower()
    if "://" in lowered or lowered.startswith(("http://", "https://", "file://")):
        return "url_like_context_ref_value"
    if ":\\" in compact or compact.startswith("\\\\") or "/" in compact or "\\" in compact:
        return "path_like_context_ref_value"
    if lowered.startswith(("bearer ", "token ")) or "access_token=" in lowered or "api_key=" in lowered:
        return "token_like_context_ref_value"
    if "prompt:" in lowered or lowered.startswith(("system prompt", "developer prompt")):
        return "prompt_like_context_ref_value"
    return ""
