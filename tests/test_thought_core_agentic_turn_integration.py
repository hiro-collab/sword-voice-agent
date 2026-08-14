import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.agentic_turn_provider import (  # noqa: E402
    MAX_CAPABILITY_VIEW_COUNT,
    MAX_CATALOG_ID_LENGTH,
    MAX_CATALOG_VERSION_LENGTH,
    PROVIDER_ATTEMPT_RECEIPT_CLASS,
    PROVIDER_ATTEMPT_TERMINAL_CLASS,
    PROVIDER_AUTHORSHIP_CLASS,
    PROVIDER_AUTHORSHIP_EVIDENCE_CLASS,
    AgenticProviderAttemptReceipt,
    AgenticTurnProviderUnavailable,
    AgenticTurnProviderRequest,
    AgenticTurnProviderResult,
    StaticAgenticTurnProvider,
    UnavailableAgenticTurnProvider,
)
from thought_core.agentic_turn_runtime_provider import (  # noqa: E402
    _normalize_agentic_turn_provider_output,
)
from thought_core.capability_catalog import (  # noqa: E402
    CapabilityCatalogError,
    HomeCapabilityCatalog,
    MAX_CAPABILITY_ALIAS_CHARS,
    MAX_CAPABILITY_ALIAS_COUNT,
)
from thought_core.execution_deadline import TurnDeadlineExceeded  # noqa: E402
from thought_core.event_journal import journal_entry_from_event  # noqa: E402
from thought_core.events import EventFactory  # noqa: E402
from thought_core.input_understanding import InputFrame  # noqa: E402
from thought_core.loop import (  # noqa: E402
    AGENTIC_HOLD_INTERNAL_FAILURE_REASON_CODE,
    AGENTIC_HOLD_REASON_CODES,
    ThoughtLoop,
)
from thought_core.tools import MockThoughtTools  # noqa: E402


class _DirectOnlyTools(MockThoughtTools):
    def __init__(self) -> None:
        super().__init__()
        self.direct_preview_calls: list[dict[str, object]] = []

    def home_preview(self, turn, observation):  # type: ignore[no-untyped-def]
        raise AssertionError("compatibility_preview_must_not_run")

    def home_preview_direct(self, turn, observation, action):  # type: ignore[no-untyped-def]
        self.direct_preview_calls.append(dict(action))
        return super().home_preview_direct(turn, observation, action)


class _NoopDirectTools(_DirectOnlyTools):
    def home_preview_direct(self, turn, observation, action):  # type: ignore[no-untyped-def]
        self.direct_preview_calls.append(dict(action))
        return {
            "status": "noop",
            "action": dict(action),
            "message": "deterministic_noop",
            "should_execute": False,
        }


class _FailingDirectTools(_DirectOnlyTools):
    def home_preview_direct(self, turn, observation, action):  # type: ignore[no-untyped-def]
        self.direct_preview_calls.append(dict(action))
        adjusted_action = dict(action)
        adjusted_action.update(
            {
                "state_tracking": "state_path",
                "verification_mode": "state_observation",
                "state_authority": "ha_observed",
            }
        )
        return {"status": "ok", "action": adjusted_action}

    def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
        self.execute_calls.append(dict(action))
        return {"status": "failed", "retryable": False, "executed": False}


class _CapturingConversationProvider:
    def __init__(self, candidate: object) -> None:
        self.candidate = candidate
        self.requests: list[AgenticTurnProviderRequest] = []

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        self.requests.append(request)
        return self.candidate


class _ReceiptBackedConversationProvider(_CapturingConversationProvider):
    def decide(self, request: AgenticTurnProviderRequest) -> object:
        self.requests.append(request)
        return AgenticTurnProviderResult(
            candidate=self.candidate,
            provider_attempt_receipt=AgenticProviderAttemptReceipt(
                receipt_class=PROVIDER_ATTEMPT_RECEIPT_CLASS,
                decision_event_id=request.decision_event_id,
                upstream_attempt_count=1,
                retry_count=0,
                fallback_count=0,
                attempt_terminal_class=PROVIDER_ATTEMPT_TERMINAL_CLASS,
            ),
        )


class _NeverCalledVisibleResponder:
    adapter_kind = "must_not_run"
    provider = "must_not_run"
    model = "must_not_run"

    def __init__(self) -> None:
        self.calls = 0

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        del turn, response_context
        self.calls += 1
        raise AssertionError("second_visible_ai_call_must_not_run")


class _CapturingUnavailableProvider:
    def __init__(self) -> None:
        self.requests: list[AgenticTurnProviderRequest] = []

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        self.requests.append(request)
        raise AgenticTurnProviderUnavailable("provider_unavailable")


class _OrderedProvider(_CapturingConversationProvider):
    def __init__(self, candidate: object, call_order: list[str]) -> None:
        super().__init__(candidate)
        self.call_order = call_order

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        self.call_order.append("provider.decide")
        return super().decide(request)

    def respond_to_receipt(self, receipt):  # type: ignore[no-untyped-def]
        self.call_order.append(f"provider.receipt:{receipt.phase}")
        return {"speech": "結果を確認しました。", "display": "確認済みです。"}


class _ReceiptExceptionProvider:
    def __init__(self, candidate: object, exception: Exception) -> None:
        self.candidate = candidate
        self.exception = exception
        self.receipt_phases: list[str] = []
        self.receipts: list[object] = []

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        del request
        return self.candidate

    def respond_to_receipt(self, receipt):  # type: ignore[no-untyped-def]
        self.receipts.append(receipt)
        self.receipt_phases.append(receipt.phase)
        if receipt.phase == "confirmation":
            return {
                "speech": "実行前に確認します。",
                "display": "確認が必要です。",
            }
        raise self.exception


class _OrderedPredecisionTools(_DirectOnlyTools):
    def __init__(self, call_order: list[str]) -> None:
        super().__init__()
        self.call_order = call_order
        self.observation_reasons: list[str] = []
        self.direct_preview_observation_refs: list[object] = []

    def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
        self.call_order.append(f"environment.observe:{reason}")
        self.observation_reasons.append(reason)
        return super().environment_observe(turn, reason=reason)

    def memory_retrieve(self, turn):  # type: ignore[no-untyped-def]
        self.call_order.append("memory.retrieve")
        return super().memory_retrieve(turn)

    def home_preview_direct(self, turn, observation, action):  # type: ignore[no-untyped-def]
        self.call_order.append("home.preview.direct")
        self.direct_preview_observation_refs.append(observation.get("observation_ref"))
        preview = super().home_preview_direct(turn, observation, action)
        reviewable_action = dict(preview.get("action", {}))
        reviewable_action.update(
            {
                "state_tracking": "state_path",
                "verification_mode": "state_observation",
                "state_authority": "environment_observed",
            }
        )
        preview["action"] = reviewable_action
        return preview

    def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
        self.call_order.append("home.execute")
        return super().home_execute(turn, action)


class _PrivatePredecisionObservationTools(_DirectOnlyTools):
    def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
        del turn, reason
        return {
            "status": "ok",
            "observation_ref": "PRIVATE_RAW_REF/C:\\PRIVATE_PATH_SENTINEL",
            "observation_source": "Bearer PRIVATE_TOKEN_SENTINEL",
            "facts": {
                "raw_private": "PRIVATE_RAW_FACT_SENTINEL",
                "location": "C:\\PRIVATE_LOCATION_SENTINEL",
                "devices": [
                    {
                        "id": "device_safe_1",
                        "name": "token PRIVATE_DEVICE_SENTINEL",
                        "state": True,
                        "api_key": "PRIVATE_API_KEY_SENTINEL",
                    }
                ],
                "state_queries": {
                    "room_light": {
                        "available": True,
                        "state": "on",
                        "path": "C:\\PRIVATE_STATE_PATH_SENTINEL",
                    }
                },
            },
        }


class _OrderedCompatibilityTools(MockThoughtTools):
    def __init__(self, call_order: list[str]) -> None:
        super().__init__()
        self.call_order = call_order
        self.observation_reasons: list[str] = []

    def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
        self.call_order.append(f"environment.observe:{reason}")
        self.observation_reasons.append(reason)
        return super().environment_observe(turn, reason=reason)

    def memory_retrieve(self, turn):  # type: ignore[no-untyped-def]
        self.call_order.append("memory.retrieve")
        return super().memory_retrieve(turn)

    def home_preview(self, turn, observation):  # type: ignore[no-untyped-def]
        self.call_order.append("home.preview")
        return super().home_preview(turn, observation)


class _CountingInputUnderstanding:
    adapter_kind = "test_counting"
    provider = "test"
    model = "test-counting-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def understand(self, turn, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(turn.text)
        return InputFrame(kind="home_command", is_command=True, reason="test_counting")


class AgenticTurnIntegrationTest(TestCase):
    def test_event_factory_reserved_ids_are_factory_local_single_use_and_gap_free(self) -> None:
        factory = EventFactory("turn_reservation", "session_reservation")
        reserved = factory.reserve_event_id()
        abandoned = factory.reserve_event_id()
        first = factory.emit("synthetic.first")
        second = factory.emit_reserved(reserved, "synthetic.reserved")
        third = factory.emit("synthetic.third")

        self.assertRegex(reserved, r"^evt_[0-9a-f]{32}$")
        self.assertEqual([first.seq, second.seq, third.seq], [1, 2, 3])
        self.assertEqual(second.event_id, reserved)
        self.assertNotEqual(abandoned, reserved)
        with self.assertRaisesRegex(ValueError, "reserved_event_id_invalid"):
            factory.emit_reserved(reserved, "synthetic.replay")
        with self.assertRaisesRegex(ValueError, "reserved_event_id_invalid"):
            factory.emit_reserved("evt_" + ("A" * 32), "synthetic.malformed")
        foreign = EventFactory("turn_foreign", "session_foreign")
        with self.assertRaisesRegex(ValueError, "reserved_event_id_invalid"):
            foreign.emit_reserved(abandoned, "synthetic.foreign")
        self.assertEqual(factory.emit("synthetic.after_abandon").seq, 4)

        with patch(
            "thought_core.events.uuid4",
            side_effect=(
                Mock(hex="1" * 32),
                Mock(hex="1" * 32),
                Mock(hex="2" * 32),
            ),
        ):
            collision_factory = EventFactory("turn_collision", "session_collision")
            collision_reserved = collision_factory.reserve_event_id()
            collision_emitted = collision_factory.emit("synthetic.after_collision")
        self.assertEqual(collision_reserved, "evt_" + ("1" * 32))
        self.assertEqual(collision_emitted.event_id, "evt_" + ("2" * 32))

    def test_capability_decision_is_paraphrase_independent_and_uses_direct_preview(self) -> None:
        candidate = self._capability("light_on")
        observed_actions = []
        for index, wish in enumerate(
            (
                "部屋をもう少し明るい雰囲気にしたい。",
                "作業しやすい明るさへ整えてください。",
            )
        ):
            with self.subTest(wish=wish):
                tools = _DirectOnlyTools()
                events = ThoughtLoop(
                    tools=tools,
                    agentic_turn_provider=StaticAgenticTurnProvider(candidate),
                ).run_dicts(self._turn(wish, turn_id=f"agentic_light_{index}"))
                proposed = next(event for event in events if event["type"] == "action.proposed")
                tool_names = [
                    event["data"]["tool"]
                    for event in events
                    if event["type"] == "tool.started"
                ]

                self.assertEqual(proposed["data"]["action"]["action_id"], "light_on")
                self.assertEqual(
                    proposed["data"]["action"]["semantic_authority"],
                    "agentic_provider",
                )
                self.assertEqual(len(tools.direct_preview_calls), 1)
                self.assertIn("home.preview.direct", tool_names)
                self.assertNotIn("home.preview", tool_names)
                self.assertNotIn("target_state.imagined", [event["type"] for event in events])
                self.assertIn(
                    events[-1]["data"]["status"],
                    {"success", "submitted_external_observation_required"},
                )
                self.assertGreater(len(tools.execute_calls), 0)
                observed_actions.append(proposed["data"]["action"]["action_id"])
        self.assertEqual(observed_actions, ["light_on", "light_on"])

    def test_agentic_projection_capabilities_use_one_existing_event_route(self) -> None:
        cases = (
            (
                "projection.fire.start",
                {"position": {"x": 0.4, "y": -0.2}, "strength": 0.8, "durationMs": 4200},
                {"schemaVersion": 2, "action": "start", "effectId": "fire"},
                "画面に炎の映像効果を出して。",
                "炎の開始を要求します。",
            ),
            (
                "projection.thunder.start",
                {},
                {"schemaVersion": 1, "action": "start", "effectId": "thunderBall"},
                "雷の映像効果を始めて。",
                "雷の開始を要求します。",
            ),
            (
                "projection.effect.stop",
                {},
                {"schemaVersion": 1, "action": "stop"},
                "映像効果を止めて。",
                "映像効果の停止を要求します。",
            ),
            (
                "projection.effect.reset",
                {},
                {"schemaVersion": 1, "action": "reset"},
                "映像効果を初期状態に戻して。",
                "映像効果のResetを要求します。",
            ),
        )
        for index, (
            capability_id,
            arguments,
            expected,
            human_wish,
            speech,
        ) in enumerate(cases):
            with self.subTest(capability_id=capability_id):
                tools = _DirectOnlyTools()
                candidate = self._capability(
                    capability_id,
                    arguments=arguments,
                    speech=speech,
                    display=f"Projection {index}",
                )
                with (
                    patch(
                        "thought_core.loop.detect_projection_effect_intent",
                        side_effect=AssertionError("fixed_projection_parser_must_not_run"),
                    ),
                    patch(
                        "thought_core.loop.compile_projection_effect_plan_intent",
                        side_effect=AssertionError("fixed_projection_compiler_must_not_run"),
                    ),
                ):
                    events = ThoughtLoop(
                        tools=tools,
                        agentic_turn_provider=StaticAgenticTurnProvider(candidate),
                    ).run_dicts(
                        self._turn(
                            human_wish,
                            turn_id=f"agentic_projection_{index}",
                        )
                    )

                requested = [
                    event for event in events if event["type"] == "projection.effect.requested"
                ]
                self.assertEqual(len(requested), 1)
                payload = requested[0]["data"]
                self.assertEqual(payload["schemaVersion"], expected["schemaVersion"])
                self.assertEqual(payload["action"], expected["action"])
                if payload["schemaVersion"] == 2:
                    plan = payload["plan"]
                    self.assertEqual(plan["effectId"], expected["effectId"])
                    self.assertEqual(plan["position"], arguments["position"])
                    self.assertEqual(plan["strength"], arguments["strength"])
                    self.assertEqual(plan["durationMs"], arguments["durationMs"])
                    self.assertEqual(plan["keyframes"][0]["position"], arguments["position"])
                    self.assertEqual(plan["keyframes"][0]["strength"], arguments["strength"])
                elif "effectId" in expected:
                    self.assertEqual(payload["effectId"], expected["effectId"])
                else:
                    self.assertEqual(set(payload), {"schemaVersion", "action"})

                event_types = [event["type"] for event in events]
                self.assertLess(
                    event_types.index("projection.effect.requested"),
                    event_types.index("assistant.speech_delta"),
                )
                self.assertEqual(
                    [
                        event["data"]["speech"]
                        for event in events
                        if event["type"] == "assistant.message"
                    ],
                    [speech],
                )
                self.assertEqual(tools.direct_preview_calls, [])
                self.assertEqual(tools.execute_calls, [])
                self.assertNotIn("action.proposed", event_types)
                self.assertEqual(events[-1]["data"]["status"], "projection_effect_requested")
                self.assertEqual(
                    events[-1]["data"]["execution_receipt"],
                    "downstream_required",
                )
                self.assertIn("要求", speech)

        provider = _CapturingConversationProvider(
            {
                "schemaVersion": 1,
                "kind": "conversation",
                "response": {"speech": "確認します。", "display": "確認中です。"},
            }
        )
        ThoughtLoop(agentic_turn_provider=provider).run_dicts(
            self._turn("現在の映像効果を確認して。")
        )
        capabilities = {
            entry.capability_id: entry
            for entry in provider.requests[0].capability_view.capabilities
        }
        self.assertIn(
            "直接の停止要求",
            capabilities["projection.effect.stop"].description,
        )
        self.assertIn("下流receipt", capabilities["projection.effect.stop"].description)
        self.assertIn(
            "直接のReset要求",
            capabilities["projection.effect.reset"].description,
        )

    def test_provider_wire_fire_normalization_reaches_existing_projection_route(self) -> None:
        candidate = _normalize_agentic_turn_provider_output(
            {
                "schemaVersion": 1,
                "kind": "capability",
                "response": {"speech": "炎を開始します。", "display": "Fire"},
                "capability": {
                    "id": "projection.fire.start",
                    "arguments": {
                        "position": {"x": 0.3, "y": -0.1},
                        "strength": 0.7,
                        "durationMs": 3000,
                    },
                },
            }
        )
        events = ThoughtLoop(
            tools=_DirectOnlyTools(),
            agentic_turn_provider=StaticAgenticTurnProvider(candidate),
        ).run_dicts(self._turn("会話の流れに合う炎を出して。"))

        requested = next(
            event for event in events if event["type"] == "projection.effect.requested"
        )
        self.assertEqual(requested["data"]["plan"]["effectId"], "fire")
        self.assertEqual(requested["data"]["plan"]["durationMs"], 3000)

    def test_agentic_projection_arguments_fail_closed_without_dispatch(self) -> None:
        cases = (
            ("projection.fire.start", {"position": None}),
            ("projection.fire.start", {"strength": None}),
            ("projection.fire.start", {"durationMs": None}),
            ("projection.fire.start", {"position": {"x": 0.0}}),
            ("projection.fire.start", {"position": {"x": 1.1, "y": 0.0}}),
            ("projection.fire.start", {"strength": True}),
            ("projection.fire.start", {"durationMs": 499}),
            ("projection.fire.start", {"durationMs": 3000.0}),
            ("projection.fire.start", {"unknown": 1}),
            ("projection.effect.stop", {"strength": 0.5}),
            ("projection.effect.reset", {"durationMs": 3000}),
        )
        for capability_id, arguments in cases:
            with self.subTest(capability_id=capability_id, arguments=arguments):
                events = ThoughtLoop(
                    tools=_DirectOnlyTools(),
                    agentic_turn_provider=StaticAgenticTurnProvider(
                        self._capability(capability_id, arguments=arguments)
                    ),
                ).run_dicts(self._turn("演出を調整して。"))
                self.assertNotIn(
                    "projection.effect.requested",
                    [event["type"] for event in events],
                )
                self.assertNotIn("turn.error", [event["type"] for event in events])
                self.assertEqual(events[-1]["data"]["status"], "held")
                self.assertEqual(
                    events[-1]["data"]["reason"],
                    "agentic_decision_invalid",
                )

    def test_conversation_decisions_keep_multiple_natural_responses_without_execution(self) -> None:
        responses = (
            ("少し静かな光に整えます。", "静かな光を考えています。"),
            ("今の雰囲気に合う明るさを一緒に探しましょう。", "明るさを検討中です。"),
        )
        observed = set()
        for index, (speech, display) in enumerate(responses):
            with self.subTest(speech=speech):
                tools = _DirectOnlyTools()
                candidate = {
                    "schemaVersion": 1,
                    "kind": "conversation",
                    "response": {"speech": speech, "display": display},
                }
                events = ThoughtLoop(
                    tools=tools,
                    agentic_turn_provider=StaticAgenticTurnProvider(candidate),
                ).run_dicts(self._turn("雑談として明るさを相談したい。", turn_id=f"chat_{index}"))
                messages = [
                    (event["data"]["speech"], event["data"]["display"])
                    for event in events
                    if event["type"] == "assistant.message"
                ]
                self.assertIn((speech, display), messages)
                self.assertEqual(tools.execute_calls, [])
                self.assertNotIn(
                    "action.proposed",
                    [event["type"] for event in events],
                )
                observed.add((speech, display))
        self.assertEqual(observed, set(responses))

    def test_provider_request_carries_private_wish_and_compact_context_only(self) -> None:
        candidate = {
            "schemaVersion": 1,
            "kind": "hold",
            "response": {"speech": "今は安全に保留します。", "display": "保留中です。"},
        }
        provider = _CapturingConversationProvider(candidate)
        ThoughtLoop(agentic_turn_provider=provider).run_dicts(
            self._turn(
                "I would like help choosing a room setting.",
                context_refs={
                    "observation_ref": "obs_safe_1",
                    "raw_text": "PRIVATE_USER_TEXT",
                    "provider_payload": {"private": True},
                },
            )
        )

        request = provider.requests[0]
        self.assertEqual(request.human_wish, "I would like help choosing a room setting.")
        self.assertEqual(dict(request.context_refs), {"observation_ref": "obs_safe_1"})
        self.assertEqual(request.capability_view.catalog_id, "sword.agentic-capabilities")
        self.assertEqual(
            request.capability_view.catalog_version,
            "agentic-capabilities.v1",
        )
        self.assertEqual(len(request.capability_view.capabilities), 19)
        self.assertLess(len(request.capability_view.capabilities), MAX_CAPABILITY_VIEW_COUNT)
        capabilities = {
            entry.capability_id: entry for entry in request.capability_view.capabilities
        }
        self.assertTrue(capabilities["light_on"].available)
        self.assertFalse(capabilities["aircon_on"].available)
        self.assertIn("ライトをつける", capabilities["light_on"].description)
        self.assertIn("電気をつけて", capabilities["light_on"].description)
        self.assertIn("Curtain3", capabilities["door_open"].description)
        self.assertIn("カーテン3", capabilities["door_open"].description)
        self.assertTrue(capabilities["projection.fire.start"].available)
        self.assertIn(
            "durationMs:500..12000",
            capabilities["projection.fire.start"].description,
        )
        with self.assertRaises(AttributeError):
            capabilities["light_on"].available = False  # type: ignore[misc]
        self.assertEqual(
            set(request.agent_context),
            {
                "bounded_wish_refs",
                "observation_refs",
                "memory_refs",
                "working_memory_item_count",
            },
        )
        self.assertEqual(request.predecision_context.environment_state.status, "available")
        self.assertEqual(request.predecision_context.relevant_memory.status, "available")
        self.assertEqual(
            request.predecision_context.same_session_continuity.status,
            "missing",
        )
        self.assertEqual(request.predecision_context.system_topology.status, "missing")
        self.assertTrue(
            any(
                item.get("item_type") == "working_memory"
                for item in request.predecision_context.relevant_memory.items
            )
        )

    def test_explicit_curtain3_open_executes_without_confirmation(self) -> None:
        terminal_response = {
            "speech": "Curtain3の実行結果を確認しました。",
            "display": "Curtain3の結果を確認しました。",
        }
        tools = _DirectOnlyTools()
        events = ThoughtLoop(
            tools=tools,
            agentic_turn_provider=StaticAgenticTurnProvider(
                self._capability("door_open"),
                receipt_responses={"success": terminal_response},
            ),
        ).run_dicts(
            self._turn(
                "Curtain3を開けてください。",
                turn_id="curtain3_explicit_open",
            )
        )

        proposed = next(event for event in events if event["type"] == "action.proposed")
        event_types = [event["type"] for event in events]
        assistant_messages = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]
        action = proposed["data"]["action"]

        self.assertEqual(action["agentic_capability_id"], "door_open")
        self.assertEqual(action["target"], "door")
        self.assertEqual(action["target_name"], "Curtain3（中扉）")
        self.assertEqual(action["expected_state"], "open")
        self.assertFalse(action["confirm_required"])
        self.assertNotIn("action.confirmation_required", event_types)
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(events[-1]["data"]["status"], "success")
        self.assertEqual(assistant_messages, [terminal_response["speech"]])

    def test_explicit_curtain3_failure_has_no_canned_terminal_message(self) -> None:
        private_sentinel = "PRIVATE_CURTAIN_RECEIPT_ERROR_SENTINEL"
        tools = _FailingDirectTools()
        provider = _ReceiptExceptionProvider(
            self._capability("door_open"),
            RuntimeError(private_sentinel),
        )
        events = ThoughtLoop(
            tools=tools,
            agentic_turn_provider=provider,
        ).run_dicts(
            self._turn(
                "カーテン3を開けてください。",
                turn_id="curtain3_explicit_failure",
            )
        )

        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(provider.receipt_phases, ["failure"])
        self.assertEqual(
            [event for event in events if event["type"] == "assistant.message"],
            [],
        )
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertNotIn(private_sentinel, json.dumps(events, ensure_ascii=False))

    def test_plain_greeting_remains_conversation_without_home_or_projection(self) -> None:
        candidate = {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {"speech": "こんにちは。", "display": "こんにちは。"},
        }
        provider = _CapturingConversationProvider(candidate)
        tools = _DirectOnlyTools()
        events = ThoughtLoop(
            tools=tools,
            agentic_turn_provider=provider,
        ).run_dicts(self._turn("こんにちは。", turn_id="plain_greeting"))

        event_types = [event["type"] for event in events]
        decision = next(event for event in events if event["type"] == "agentic.decision")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(decision["data"]["status"], "accepted")
        self.assertEqual(decision["data"]["kind"], "conversation")
        self.assertNotIn("action.proposed", event_types)
        self.assertFalse(
            any(event_type.startswith("projection.effect.") for event_type in event_types)
        )
        self.assertEqual(tools.direct_preview_calls, [])
        self.assertEqual(tools.execute_calls, [])

    def test_receipt_backed_noncapability_terminal_join_is_exact_and_skips_second_ai(self) -> None:
        for index, kind in enumerate(("conversation", "clarification", "hold")):
            expected_speech = f"provider speech {index}"
            expected_display = f"provider display {index}"
            turn_id = f"receipt_join_{index}"
            candidate = {
                "schemaVersion": 1,
                "kind": kind,
                "response": {
                    "speech": expected_speech,
                    "display": expected_display,
                },
            }
            provider = _ReceiptBackedConversationProvider(candidate)
            responder = _NeverCalledVisibleResponder()
            events = ThoughtLoop(
                tools=_DirectOnlyTools(),
                responder=responder,
                agentic_turn_provider=provider,
                llm_visible_speech=True,
                require_llm_visible_speech=True,
            ).run_dicts(
                self._turn(
                    f"synthetic wish {index}",
                    turn_id=turn_id,
                )
            )

            with self.subTest(kind=kind):
                self.assertEqual(responder.calls, 0)
                self.assertEqual(
                    [
                        event["type"]
                        for event in events
                        if event["type"].startswith("assistant.")
                    ],
                    ["assistant.speech_delta", "assistant.message"],
                )
                self.assertEqual(
                    [event["type"] for event in events[-4:]],
                    [
                        "agentic.decision",
                        "assistant.speech_delta",
                        "assistant.message",
                        "turn.completed",
                    ],
                )
                decision, speech_delta, message, completed = events[-4:]
                self.assertEqual(decision["data"]["status"], "accepted")
                self.assertEqual(decision["data"]["kind"], kind)
                self.assertEqual(
                    decision["data"]["semantic_authority"],
                    "agentic_provider",
                )
                self.assertIs(decision["data"]["capability_present"], False)
                self.assertEqual(completed["data"]["status"], kind)
                self.assertEqual(
                    completed["data"]["semantic_authority"],
                    "agentic_provider",
                )
                self.assertIs(completed["data"]["capability_executed"], False)
                self.assertEqual(
                    [
                        (event["source"], event["session_id"], event["turn_id"])
                        for event in events[-4:]
                    ],
                    [
                        ("thought-core", "agentic_integration_session", turn_id),
                    ]
                    * 4,
                )
                self.assertEqual(
                    provider.requests[0].decision_event_id,
                    decision["event_id"],
                )
                assistant_message_id = message["data"]["assistant_message_id"]
                self.assertEqual(
                    speech_delta["data"]["assistant_message_id"],
                    assistant_message_id,
                )
                self.assertEqual(speech_delta["data"]["delta"], expected_speech)
                self.assertEqual(message["data"]["speech"], expected_speech)
                self.assertEqual(message["data"]["display"], expected_display)
                self.assertEqual(
                    speech_delta["data"]["phrase_generation"],
                    {
                        "enabled": True,
                        "used_llm": True,
                        "status": "agentic_provider_decision_response",
                    },
                )
                self.assertEqual(
                    message["data"]["phrase_generation"],
                    {
                        "enabled": True,
                        "used_llm": True,
                        "status": "agentic_provider_decision_response",
                    },
                )
                self.assertEqual(
                    completed["data"]["provider_attempt_evidence"],
                    {
                        "evidence_class": PROVIDER_AUTHORSHIP_EVIDENCE_CLASS,
                        "decision_event_id": decision["event_id"],
                        "assistant_message_id": assistant_message_id,
                        "upstream_attempt_count": 1,
                        "retry_count": 0,
                        "fallback_count": 0,
                        "attempt_terminal_class": PROVIDER_ATTEMPT_TERMINAL_CLASS,
                        "authorship_class": PROVIDER_AUTHORSHIP_CLASS,
                    },
                )
                self.assertEqual(
                    [event["seq"] for event in events],
                    list(range(1, len(events) + 1)),
                )
                self.assertEqual(
                    len({event["event_id"] for event in events}),
                    len(events),
                )
                serialized = json.dumps(events, ensure_ascii=False).lower()
                for forbidden in (
                    "x-sword-agentic-decision-event-id",
                    "https://api.openai.com",
                    "gpt-4o-mini",
                    "response_sha256",
                    "provider_request_id",
                ):
                    self.assertNotIn(forbidden, serialized)

    def test_receipt_backed_capability_does_not_carry_conversation_authorship_evidence(self) -> None:
        provider = _ReceiptBackedConversationProvider(self._capability("light_on"))
        events = ThoughtLoop(
            tools=_NoopDirectTools(),
            agentic_turn_provider=provider,
        ).run_dicts(self._turn("少し明るくして。", turn_id="receipt_capability"))

        self.assertIn("agentic.decision", [event["type"] for event in events])
        self.assertNotIn("provider_attempt_evidence", json.dumps(events))

    def test_unavailable_or_invalid_capability_holds_without_compatibility_fallback(self) -> None:
        cases = (
            (UnavailableAgenticTurnProvider(), "agentic_provider_unavailable"),
            (StaticAgenticTurnProvider(self._capability("not_catalogued")), "agentic_decision_invalid"),
            (StaticAgenticTurnProvider(self._capability("aircon_on")), "agentic_decision_invalid"),
            (
                StaticAgenticTurnProvider(
                    self._capability("light_on", arguments={"room": "living"})
                ),
                "agentic_decision_invalid",
            ),
        )
        for provider, reason in cases:
            with self.subTest(reason=reason):
                tools = _DirectOnlyTools()
                events = ThoughtLoop(
                    tools=tools,
                    agentic_turn_provider=provider,
                ).run_dicts(self._turn("リビングの電気をつけて"))
                decision = next(event for event in events if event["type"] == "agentic.decision")

                self.assertEqual(decision["data"]["status"], "held")
                self.assertEqual(decision["data"]["reason"], reason)
                self.assertTrue(decision["data"]["degraded"])
                self.assertEqual(tools.direct_preview_calls, [])
                self.assertEqual(tools.execute_calls, [])
                self.assertNotIn("action.proposed", [event["type"] for event in events])

    def test_provider_precedes_ordinary_home_projection_and_state_semantics(self) -> None:
        candidate = {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {"speech": "AIが意味を受け取りました。", "display": "判断中です。"},
        }
        for index, text in enumerate(
            (
                "部屋を明るくして。",
                "右側に炎を出して。",
                "部屋の明るさはどう？",
            )
        ):
            with self.subTest(text=text):
                provider = _CapturingConversationProvider(candidate)
                understanding = _CountingInputUnderstanding()
                tools = _DirectOnlyTools()
                events = ThoughtLoop(
                    tools=tools,
                    input_understanding=understanding,
                    agentic_turn_provider=provider,
                ).run_dicts(self._turn(text, turn_id=f"provider_first_{index}"))

                event_types = [event["type"] for event in events]
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(understanding.calls, [])
                self.assertNotIn("input.understood", event_types)
                self.assertIn("memory.retrieved", event_types)
                self.assertNotIn("action.proposed", event_types)
                self.assertEqual(
                    [
                        event["data"]["tool"]
                        for event in events
                        if event["type"] == "tool.started"
                    ],
                    ["environment.observe", "memory.retrieve"],
                )
                self.assertEqual(tools.direct_preview_calls, [])
                self.assertEqual(tools.execute_calls, [])

    def test_unavailable_hold_and_invalid_decision_precede_compatibility_work(self) -> None:
        invalid_candidate = {"schemaVersion": 1, "kind": "capability"}
        cases = (
            (_CapturingUnavailableProvider(), "agentic_provider_unavailable"),
            (
                _CapturingConversationProvider(
                    {
                        "schemaVersion": 1,
                        "kind": "hold",
                        "response": {"speech": "保留します。", "display": "保留中です。"},
                    }
                ),
                None,
            ),
            (_CapturingConversationProvider(invalid_candidate), "agentic_decision_invalid"),
        )
        for provider, reason in cases:
            with self.subTest(reason=reason):
                understanding = _CountingInputUnderstanding()
                tools = _DirectOnlyTools()
                events = ThoughtLoop(
                    tools=tools,
                    input_understanding=understanding,
                    agentic_turn_provider=provider,
                ).run_dicts(self._turn("部屋を明るくして。"))
                event_types = [event["type"] for event in events]

                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(understanding.calls, [])
                self.assertNotIn("input.understood", event_types)
                self.assertIn("memory.retrieved", event_types)
                self.assertNotIn("action.proposed", event_types)
                self.assertEqual(
                    [
                        event["data"]["tool"]
                        for event in events
                        if event["type"] == "tool.started"
                    ],
                    ["environment.observe", "memory.retrieve"],
                )
                self.assertEqual(tools.direct_preview_calls, [])
                self.assertEqual(tools.execute_calls, [])
                if reason is not None:
                    held = next(event for event in events if event["type"] == "agentic.decision")
                    self.assertEqual(held["data"]["reason"], reason)

    def test_predecision_context_is_gathered_before_provider_and_observation_is_reused(self) -> None:
        call_order: list[str] = []
        tools = _OrderedPredecisionTools(call_order)
        provider = _OrderedProvider(self._capability("light_on"), call_order)
        events = ThoughtLoop(
            tools=tools,
            agentic_turn_provider=provider,
        ).run_dicts(
            self._turn(
                "部屋を明るくして。",
                turn_id="predecision_order",
                context_refs={
                    "observation_ref": "working_obs_1",
                    "mock_memory_items": [
                        {
                            "scope": "session",
                            "memory_type": "preference",
                            "content": {"preference": "soft_light"},
                        }
                    ],
                },
            )
        )

        self.assertLess(
            call_order.index("environment.observe:before_decision"),
            call_order.index("provider.decide"),
        )
        self.assertLess(call_order.index("memory.retrieve"), call_order.index("provider.decide"))
        self.assertLess(call_order.index("provider.decide"), call_order.index("home.preview.direct"))
        self.assertEqual(
            tools.observation_reasons,
            ["before_decision", "after_action"],
        )
        request = provider.requests[0]
        environment_items = request.predecision_context.environment_state.items
        state_item = next(
            item for item in environment_items if item.get("item_type") == "state_query"
        )
        self.assertEqual(state_item["target"], "room_light")
        memory_items = request.predecision_context.relevant_memory.items
        self.assertTrue(
            any(item.get("item_type") == "working_memory" for item in memory_items)
        )
        self.assertTrue(
            any(item.get("item_type") == "retrieved_memory" for item in memory_items)
        )
        predecision_observation = next(
            event
            for event in events
            if event["type"] == "observation.received"
            and event["data"].get("purpose") == "agentic_predecision"
        )
        self.assertEqual(
            len(tools.direct_preview_observation_refs),
            1,
        )
        self.assertEqual(predecision_observation["data"]["status"], "available")
        self.assertTrue(predecision_observation["data"]["available"])
        self.assertTrue(
            predecision_observation["data"]["safe_observation_ref_present"]
        )
        self.assertGreater(
            predecision_observation["data"]["filtered_item_count"],
            0,
        )
        self.assertNotIn("facts", predecision_observation["data"])
        self.assertNotIn("observation_source", predecision_observation["data"])
        self.assertIn("action.reviewed", [event["type"] for event in events])
        self.assertIn("agentic.receipt_response", [event["type"] for event in events])

    def test_observation_events_publish_only_reader_safe_text_free_summary(self) -> None:
        provider = _CapturingConversationProvider(
            {
                "schemaVersion": 1,
                "kind": "conversation",
                "response": {"speech": "確認しました。", "display": "確認済みです。"},
            }
        )
        events = ThoughtLoop(
            tools=_PrivatePredecisionObservationTools(),
            agentic_turn_provider=provider,
        ).run_dicts(self._turn("いまの状況を見て。", turn_id="private_observation"))

        observations = [
            event for event in events if event["type"] == "observation.received"
        ]
        self.assertEqual(len(observations), 1)
        data = observations[0]["data"]
        self.assertEqual(
            set(data),
            {
                "purpose",
                "status",
                "available",
                "filtered_item_count",
                "safe_observation_ref_present",
            },
        )
        self.assertEqual(data["purpose"], "agentic_predecision")
        self.assertEqual(data["status"], "available")
        self.assertTrue(data["available"])
        self.assertFalse(data["safe_observation_ref_present"])
        self.assertGreater(data["filtered_item_count"], 0)
        serialized = json.dumps(observations, ensure_ascii=False)
        for sentinel in (
            "PRIVATE_RAW_REF",
            "PRIVATE_PATH_SENTINEL",
            "PRIVATE_TOKEN_SENTINEL",
            "PRIVATE_RAW_FACT_SENTINEL",
            "PRIVATE_LOCATION_SENTINEL",
            "PRIVATE_API_KEY_SENTINEL",
            "PRIVATE_STATE_PATH_SENTINEL",
        ):
            self.assertNotIn(sentinel, serialized)
        self.assertNotIn('"facts"', serialized)
        self.assertNotIn("observation_source", serialized)
        self.assertTrue(
            any(
                item.get("item_type") == "state_query"
                and item.get("target") == "room_light"
                for item in provider.requests[0].predecision_context.environment_state.items
            )
        )

    def test_same_session_correction_and_prior_turn_reach_next_provider_decision(self) -> None:
        candidate = {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {"speech": "承知しました。", "display": "更新しました。"},
        }
        provider = _CapturingConversationProvider(candidate)
        loop = ThoughtLoop(agentic_turn_provider=provider)
        loop.run_dicts(self._turn("照明を変えたい。", turn_id="continuity_first"))
        correction = "いや、照明ではなく映像を変えて。"
        loop.run_dicts(self._turn(correction, turn_id="continuity_second"))

        context = provider.requests[1].predecision_context
        self.assertEqual(context.latest_user_correction, correction)
        self.assertEqual(context.same_session_continuity.status, "available")
        self.assertTrue(
            any(
                item.get("item_type") == "recent_turn"
                and item.get("user_text") == "照明を変えたい。"
                for item in context.same_session_continuity.items
            )
        )
        self.assertTrue(
            any(
                item.get("item_type") == "decision"
                and item.get("status") == "correction"
                for item in context.same_session_continuity.items
            )
        )

    def test_none_provider_keeps_compatibility_parser_and_before_action_order(self) -> None:
        call_order: list[str] = []
        tools = _OrderedCompatibilityTools(call_order)
        understanding = _CountingInputUnderstanding()
        events = ThoughtLoop(
            tools=tools,
            input_understanding=understanding,
            agentic_turn_provider=None,
        ).run_dicts(self._turn("ライトをつけて", turn_id="compatibility_order"))

        self.assertEqual(understanding.calls, ["ライトをつけて"])
        self.assertIn("input.understood", [event["type"] for event in events])
        self.assertNotIn("environment.observe:before_decision", call_order)
        self.assertIn("environment.observe:before_action", call_order)
        self.assertLess(
            call_order.index("memory.retrieve"),
            call_order.index("environment.observe:before_action"),
        )
        self.assertLess(
            call_order.index("environment.observe:before_action"),
            call_order.index("home.preview"),
        )

    def test_pending_confirmation_and_review_precede_provider(self) -> None:
        action = {
            "action_id": "light_on",
            "target": "light",
            "target_name": "リビングの電気",
            "expected_state": "on",
            "confirm_required": True,
        }
        confirmation_provider = _CapturingConversationProvider(self._capability("light_on"))
        confirmation_loop = ThoughtLoop(agentic_turn_provider=confirmation_provider)
        confirmation_loop.pending_confirmations["agentic_integration_session"] = {
            "action": action,
        }
        confirmation_events = confirmation_loop.run_dicts(self._turn("やめて"))
        self.assertEqual(confirmation_provider.requests, [])
        self.assertIn(
            "action.confirmation_cancelled",
            [event["type"] for event in confirmation_events],
        )

        review_provider = _CapturingConversationProvider(self._capability("light_on"))
        review_loop = ThoughtLoop(agentic_turn_provider=review_provider)
        review_loop.pending_action_reviews["agentic_integration_session"] = {
            "action": action,
        }
        review_events = review_loop.run_dicts(self._turn("やめて"))
        self.assertEqual(review_provider.requests, [])
        self.assertIn(
            "action.review_cancelled",
            [event["type"] for event in review_events],
        )

    def test_capability_view_envelope_bounds_reject_before_provider_call(self) -> None:
        catalog_path = REPO_ROOT / "catalogs" / "actions" / "home-actions.json"
        baseline = json.loads(catalog_path.read_text(encoding="utf-8"))
        oversized_actions = json.loads(json.dumps(baseline))
        while len(oversized_actions["actions"]) <= MAX_CAPABILITY_VIEW_COUNT:
            oversized_actions["actions"][
                f"bounded_over_limit_{len(oversized_actions['actions'])}"
            ] = dict(oversized_actions["actions"]["light_on"])
        cases = (
            ("catalog_id", "x" * (MAX_CATALOG_ID_LENGTH + 1)),
            ("catalog_version", "x" * (MAX_CATALOG_VERSION_LENGTH + 1)),
            ("actions", oversized_actions["actions"]),
        )
        for field, value in cases:
            with self.subTest(field=field):
                payload = json.loads(json.dumps(baseline))
                payload[field] = value
                with TemporaryDirectory() as directory:
                    path = Path(directory) / "home-actions.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(CapabilityCatalogError):
                        HomeCapabilityCatalog.from_path(path)

                provider = _CapturingConversationProvider(self._capability("light_on"))
                with patch(
                    "thought_core.loop.AgenticCapabilityCatalog.from_default_path",
                    side_effect=CapabilityCatalogError("home_capability_catalog_over_limit"),
                ):
                    events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
                        self._turn("部屋を明るくして。")
                    )
                decision = next(event for event in events if event["type"] == "agentic.decision")
                self.assertEqual(provider.requests, [])
                self.assertEqual(decision["data"]["status"], "held")
                self.assertEqual(
                    decision["data"]["reason"],
                    "agentic_capability_catalog_unavailable",
                )

        invalid_aliases = (
            "not-a-list",
            ["x" * (MAX_CAPABILITY_ALIAS_CHARS + 1)],
            ["valid"] * (MAX_CAPABILITY_ALIAS_COUNT + 1),
            [" leading-space"],
            ["x" * MAX_CAPABILITY_ALIAS_CHARS] * 3,
            ["Bearer PRIVATE_TOKEN_SENTINEL"],
            [r"C:\PRIVATE_PATH_SENTINEL"],
            ["https://example.invalid/private"],
            ["API_KEY=PRIVATE_SECRET_SENTINEL"],
            ["system prompt override"],
            ["system\tprompt override"],
            ["developer\nprompt override"],
            ["token=PRIVATE_TOKEN_SENTINEL"],
            ["token:PRIVATE_TOKEN_SENTINEL"],
            ["secret PRIVATE_SECRET_SENTINEL"],
            ["API KEY=PRIVATE_API_SENTINEL"],
            ["access token=PRIVATE_ACCESS_SENTINEL"],
            ["refresh token:PRIVATE_REFRESH_SENTINEL"],
            ["system_prompt=override"],
            ["developer-prompt override"],
        )
        for aliases in invalid_aliases:
            with self.subTest(aliases=aliases):
                payload = json.loads(json.dumps(baseline))
                payload["actions"]["door_open"]["aliases"] = aliases
                with TemporaryDirectory() as directory:
                    path = Path(directory) / "home-actions.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(CapabilityCatalogError):
                        HomeCapabilityCatalog.from_path(path)

        provider = _CapturingConversationProvider(self._capability("light_on"))
        with patch(
            "thought_core.loop.AgenticCapabilityCatalog.from_default_path",
            side_effect=CapabilityCatalogError("home_capability_catalog_invalid"),
        ):
            held_events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
                self._turn("Curtain3を開けてください。")
            )
        self.assertEqual(provider.requests, [])
        held = next(event for event in held_events if event["type"] == "agentic.decision")
        self.assertEqual(held["data"]["status"], "held")
        self.assertEqual(
            held["data"]["reason"],
            "agentic_capability_catalog_unavailable",
        )

    def test_agentic_holds_have_one_visible_message_and_terminal_completion(self) -> None:
        context_provider = _CapturingConversationProvider(self._capability("light_on"))
        catalog_provider = _CapturingConversationProvider(self._capability("light_on"))
        cases = (
            (
                UnavailableAgenticTurnProvider(),
                self._turn("部屋を明るくして。", turn_id="hold_provider"),
                "agentic_provider_unavailable",
                None,
            ),
            (
                StaticAgenticTurnProvider(self._capability("not_catalogued")),
                self._turn("部屋を明るくして。", turn_id="hold_decision"),
                "agentic_decision_invalid",
                None,
            ),
            (
                context_provider,
                self._turn(
                    "部屋を明るくして。",
                    turn_id="hold_context",
                    context_refs={"observation_ref": "x" * 181},
                ),
                "agentic_context_refs_invalid",
                None,
            ),
            (
                catalog_provider,
                self._turn("部屋を明るくして。", turn_id="hold_catalog"),
                "agentic_capability_catalog_unavailable",
                CapabilityCatalogError("home_capability_catalog_over_limit"),
            ),
        )
        for provider, turn, reason, catalog_error in cases:
            with self.subTest(reason=reason):
                tools = _DirectOnlyTools()
                if catalog_error is None:
                    events = ThoughtLoop(
                        tools=tools,
                        agentic_turn_provider=provider,
                    ).run_dicts(turn)
                else:
                    with patch(
                        "thought_core.loop.AgenticCapabilityCatalog.from_default_path",
                        side_effect=catalog_error,
                    ):
                        events = ThoughtLoop(
                            tools=tools,
                            agentic_turn_provider=provider,
                        ).run_dicts(turn)
                hold_messages = [
                    event
                    for event in events
                    if event["type"] == "assistant.message"
                    and event["data"]["emotion"] == "focused"
                    and event["data"]["priority"] == "normal"
                ]
                completed = [event for event in events if event["type"] == "turn.completed"]

                self.assertEqual(len(hold_messages), 1)
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0]["data"]["status"], "held")
                self.assertEqual(completed[0]["data"]["reason"], reason)
                self.assertEqual(
                    completed[0]["data"]["semantic_authority"],
                    "agentic_provider",
                )
                self.assertTrue(completed[0]["data"]["degraded"])
                self.assertEqual(tools.direct_preview_calls, [])
                self.assertEqual(tools.execute_calls, [])
                self.assertNotIn("action.proposed", [event["type"] for event in events])
        self.assertEqual(context_provider.requests, [])
        self.assertEqual(catalog_provider.requests, [])

    def test_all_agentic_hold_branches_emit_bounded_journal_reason_codes(self) -> None:
        loop = ThoughtLoop(agentic_turn_provider=UnavailableAgenticTurnProvider())
        for reason in sorted(AGENTIC_HOLD_REASON_CODES):
            with self.subTest(reason=reason):
                events = []
                loop._emit_agentic_hold(  # noqa: SLF001 - exact hold boundary contract
                    events,
                    EventFactory(f"turn_{reason}", "session_hold_reason_codes"),
                    reason=reason,
                )
                decision = next(event for event in events if event.type == "agentic.decision")
                entry = journal_entry_from_event(decision.to_dict())

                self.assertEqual(decision.data["reason_code"], reason)
                self.assertEqual(entry["summary"]["reason_code"], reason)
                self.assertTrue(entry["summary"]["reason_present"])
                self.assertNotIn("reason", entry["summary"])

    def test_unknown_agentic_hold_reason_fails_closed_without_persisting_raw_text(self) -> None:
        private_sentinel = "Bearer PRIVATE_PROVIDER_REASON_SENTINEL"
        events = []
        ThoughtLoop()._emit_agentic_hold(  # noqa: SLF001 - exact hold boundary contract
            events,
            EventFactory("turn_private_hold_reason", "session_private_hold_reason"),
            reason=private_sentinel,
        )
        journal_entries = [journal_entry_from_event(event.to_dict()) for event in events]
        serialized = json.dumps(journal_entries, ensure_ascii=False)

        self.assertTrue(
            all(
                event.data["reason_code"] == AGENTIC_HOLD_INTERNAL_FAILURE_REASON_CODE
                for event in events
                if event.type in {"agentic.decision", "turn.completed"}
            )
        )
        self.assertNotIn(private_sentinel, serialized)
        self.assertNotIn("PRIVATE_PROVIDER_REASON_SENTINEL", serialized)

    def test_over_limit_provider_context_refs_hold_before_provider_call(self) -> None:
        candidate = {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {"speech": "確認します。", "display": "確認中です。"},
        }
        cases = (
            {"observation_ref": "x" * 181},
            {"observation_ref": {"nested": "not_reader_safe"}},
            {
                "event_id": "1",
                "turn_id": "2",
                "trace_id": "3",
                "observation_id": "4",
                "observation_ref": "5",
                "motion_event_id": "6",
                "stimulus_id": "7",
                "stimulus_instance_id": "8",
                "runtime_result_id": "9",
            },
        )
        for context_refs in cases:
            with self.subTest(context_refs=context_refs):
                provider = _CapturingConversationProvider(candidate)
                events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
                    self._turn("今の状態を考えて。", context_refs=context_refs)
                )
                decision = next(event for event in events if event["type"] == "agentic.decision")

                self.assertEqual(provider.requests, [])
                self.assertEqual(decision["data"]["status"], "held")
                self.assertEqual(decision["data"]["reason"], "agentic_context_refs_invalid")
                self.assertTrue(decision["data"]["degraded"])

    def test_state_query_reaches_provider_before_fixed_state_detector(self) -> None:
        candidate = {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {
                "speech": "AIの判断で、明るさの様子を一緒に考えます。",
                "display": "明るさを検討します。",
            },
        }
        provider = _CapturingConversationProvider(candidate)
        events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
            self._turn("部屋の明るさはどう？", turn_id="agentic_state_query")
        )

        messages = [
            (event["data"]["speech"], event["data"]["display"])
            for event in events
            if event["type"] == "assistant.message"
        ]
        self.assertEqual(len(provider.requests), 1)
        self.assertIn((candidate["response"]["speech"], candidate["response"]["display"]), messages)
        self.assertFalse(
            any(event["type"].startswith("state_query.") for event in events)
        )

        held_events = ThoughtLoop(
            agentic_turn_provider=UnavailableAgenticTurnProvider()
        ).run_dicts(self._turn("部屋の明るさはどう？", turn_id="agentic_state_query_hold"))
        held = next(event for event in held_events if event["type"] == "agentic.decision")
        self.assertEqual(held["data"]["status"], "held")
        self.assertEqual(held["data"]["reason"], "agentic_provider_unavailable")
        self.assertFalse(
            any(event["type"].startswith("state_query.") for event in held_events)
        )

    def test_direct_capability_confirmation_and_final_receipt_do_not_echo_premature_completion(self) -> None:
        private_sentinel = "PRIVATE_PROVIDER_RESPONSE_SENTINEL"
        candidate = self._capability(
            "vacuum_start",
            speech=private_sentinel,
            display=private_sentinel,
        )
        confirmation_response = {"speech": "開始前に確認します。", "display": "確認が必要です。"}
        success_response = {"speech": "確認結果を受け取りました。", "display": "確認済みです。"}
        tools = _DirectOnlyTools()
        loop = ThoughtLoop(
            tools=tools,
            agentic_turn_provider=StaticAgenticTurnProvider(
                candidate,
                receipt_responses={
                    "confirmation": confirmation_response,
                    "success": success_response,
                },
            ),
        )

        preview_events = loop.run_dicts(
            self._turn("通路を安全にしたい。", turn_id="door_preview")
        )
        preview_messages = [
            event["data"]["speech"]
            for event in preview_events
            if event["type"] == "assistant.message"
        ]
        proposed = next(event for event in preview_events if event["type"] == "action.proposed")
        self.assertEqual(preview_events[-1]["data"]["status"], "confirmation_required")
        self.assertEqual(tools.execute_calls, [])
        self.assertEqual(proposed["data"]["action"]["semantic_authority"], "agentic_provider")
        self.assertNotIn("agentic_response", proposed["data"]["action"])
        self.assertNotIn(private_sentinel, json.dumps(preview_events, ensure_ascii=False))
        self.assertNotIn(
            private_sentinel,
            json.dumps(loop.pending_confirmations, ensure_ascii=False),
        )
        self.assertIn(confirmation_response["speech"], preview_messages)
        self.assertIn(
            "confirmation",
            [
                event["data"]["phase"]
                for event in preview_events
                if event["type"] == "agentic.receipt_response"
                and event["data"]["status"] == "accepted"
            ],
        )

        execute_events = loop.run_dicts(self._turn("お願い", turn_id="door_confirm"))
        execute_messages = [
            event["data"]["speech"]
            for event in execute_events
            if event["type"] == "assistant.message"
        ]
        execute_event_types = [event["type"] for event in execute_events]
        execute_started_index = next(
            index
            for index, event in enumerate(execute_events)
            if event["type"] == "tool.started"
            and event["data"]["tool"] == "home.execute"
        )
        success_receipt_index = next(
            index
            for index, event in enumerate(execute_events)
            if event["type"] == "agentic.receipt_response"
            and event["data"]["phase"] == "success"
            and event["data"]["status"] == "accepted"
        )
        assistant_message_index = execute_event_types.index("assistant.message")
        self.assertEqual(execute_events[-1]["data"]["status"], "success")
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(execute_messages, [success_response["speech"]])
        self.assertLess(execute_event_types.index("action.confirmed"), execute_started_index)
        self.assertLess(execute_started_index, success_receipt_index)
        self.assertLess(success_receipt_index, assistant_message_index)
        self.assertNotIn(private_sentinel, json.dumps(execute_events, ensure_ascii=False))
        self.assertNotIn(
            private_sentinel,
            json.dumps(loop.pending_confirmations, ensure_ascii=False),
        )
        self.assertIn(success_response["speech"], execute_messages)
        self.assertIn(
            "success",
            [
                event["data"]["phase"]
                for event in execute_events
                if event["type"] == "agentic.receipt_response"
                and event["data"]["status"] == "accepted"
            ],
        )
        self.assertIn(
            "succeeded",
            [
                event["data"]["status"]
                for event in execute_events
                if event["type"] == "action.reviewed"
            ],
        )

    def test_confirmed_failure_emits_only_terminal_receipt_message(self) -> None:
        confirmation_response = {"speech": "閉める前に確認します。", "display": "確認が必要です。"}
        failure_response = {"speech": "結果を確認できなかったため保留します。", "display": "確認待ちです。"}
        tools = _FailingDirectTools()
        loop = ThoughtLoop(
            tools=tools,
            agentic_turn_provider=StaticAgenticTurnProvider(
                self._capability("vacuum_start"),
                receipt_responses={
                    "confirmation": confirmation_response,
                    "failure": failure_response,
                },
            ),
        )

        preview_events = loop.run_dicts(
            self._turn("通路を安全にしたい。", turn_id="door_failure_preview")
        )
        self.assertEqual(preview_events[-1]["data"]["status"], "confirmation_required")
        self.assertEqual(tools.execute_calls, [])

        execute_events = loop.run_dicts(
            self._turn("お願い", turn_id="door_failure_confirm")
        )
        execute_event_types = [event["type"] for event in execute_events]
        execute_messages = [
            event["data"]["speech"]
            for event in execute_events
            if event["type"] == "assistant.message"
        ]
        execute_started_index = next(
            index
            for index, event in enumerate(execute_events)
            if event["type"] == "tool.started"
            and event["data"]["tool"] == "home.execute"
        )
        failure_receipt_index = next(
            index
            for index, event in enumerate(execute_events)
            if event["type"] == "agentic.receipt_response"
            and event["data"]["phase"] == "failure"
            and event["data"]["status"] == "accepted"
        )
        assistant_message_index = execute_event_types.index("assistant.message")

        self.assertEqual(execute_events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(execute_messages, [failure_response["speech"]])
        self.assertLess(execute_event_types.index("action.confirmed"), execute_started_index)
        self.assertLess(execute_started_index, failure_receipt_index)
        self.assertLess(failure_receipt_index, assistant_message_index)

    def test_receipt_response_reaches_user_for_noop_and_failure_only_after_phase(self) -> None:
        noop_response = {"speech": "今の状態に合わせて見送ります。", "display": "見送ります。"}
        noop_events = ThoughtLoop(
            tools=_NoopDirectTools(),
            agentic_turn_provider=StaticAgenticTurnProvider(
                self._capability("light_on"),
                receipt_responses={"noop": noop_response},
            ),
        ).run_dicts(self._turn("少し明るくして。", turn_id="agentic_noop"))
        noop_messages = [
            event["data"]["speech"]
            for event in noop_events
            if event["type"] == "assistant.message"
        ]
        self.assertEqual(noop_events[-1]["data"]["status"], "noop")
        self.assertIn(noop_response["speech"], noop_messages)

        failure_response = {"speech": "結果を確認できなかったため保留します。", "display": "確認待ちです。"}
        failing_tools = _FailingDirectTools()
        failure_events = ThoughtLoop(
            tools=failing_tools,
            agentic_turn_provider=StaticAgenticTurnProvider(
                self._capability("light_on"),
                receipt_responses={"failure": failure_response},
            ),
        ).run_dicts(self._turn("部屋を明るくして。", turn_id="agentic_failure"))
        failure_messages = [
            event["data"]["speech"]
            for event in failure_events
            if event["type"] == "assistant.message"
        ]
        self.assertEqual(failure_events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(failing_tools.execute_calls), 1)
        self.assertIn(failure_response["speech"], failure_messages)
        self.assertIn(
            "failure",
            [
                event["data"]["phase"]
                for event in failure_events
                if event["type"] == "agentic.receipt_response"
                and event["data"]["status"] == "accepted"
            ],
        )

    def test_receipt_deadline_propagates_after_confirmed_execution(self) -> None:
        provider = _ReceiptExceptionProvider(
            self._capability("vacuum_start"),
            TurnDeadlineExceeded(),
        )
        tools = _DirectOnlyTools()
        loop = ThoughtLoop(tools=tools, agentic_turn_provider=provider)

        preview_events = loop.run_dicts(
            self._turn("通路を安全にしたい。", turn_id="deadline_preview")
        )
        self.assertEqual(preview_events[-1]["data"]["status"], "confirmation_required")

        with self.assertRaisesRegex(TurnDeadlineExceeded, "turn_deadline_exceeded"):
            loop.run_dicts(self._turn("お願い", turn_id="deadline_confirm"))

        self.assertEqual(provider.receipt_phases, ["confirmation", "success"])
        self.assertEqual(len(tools.execute_calls), 1)

    def test_confirmed_success_freezes_context_and_never_uses_canned_success_when_provider_fails(
        self,
    ) -> None:
        private_sentinel = "PRIVATE_PREEXECUTION_RESPONSE_SENTINEL"
        provider = _ReceiptExceptionProvider(
            self._capability(
                "vacuum_start",
                speech=private_sentinel,
                display=private_sentinel,
            ),
            RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL"),
        )
        tools = _DirectOnlyTools()
        loop = ThoughtLoop(tools=tools, agentic_turn_provider=provider)

        preview_events = loop.run_dicts(
            self._turn("通路を安全にしたい。", turn_id="context_preview")
        )
        self.assertEqual(preview_events[-1]["data"]["status"], "confirmation_required")
        execute_events = loop.run_dicts(
            self._turn("お願い", turn_id="context_confirm")
        )

        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(provider.receipt_phases, ["confirmation", "success"])
        self.assertEqual(
            [event for event in execute_events if event["type"] == "assistant.message"],
            [],
        )
        self.assertEqual(execute_events[-1]["data"]["status"], "success")
        terminal_receipt = provider.receipts[-1]
        self.assertEqual(terminal_receipt.capability_id, "vacuum_start")
        self.assertEqual(terminal_receipt.target_ref, "vacuum")
        self.assertEqual(terminal_receipt.expected_state, "cleaning")
        self.assertEqual(terminal_receipt.execution_certainty, "executed")
        self.assertEqual(terminal_receipt.review_status, "succeeded")
        self.assertEqual(terminal_receipt.review_checkpoint_class, "matched")
        self.assertTrue(terminal_receipt.decision_ref.startswith("evt_"))
        self.assertEqual(
            terminal_receipt.receipt_ref,
            f"{terminal_receipt.decision_ref}:success",
        )
        serialized = json.dumps(terminal_receipt.__dict__, ensure_ascii=False)
        self.assertNotIn(private_sentinel, serialized)
        self.assertNotIn("PRIVATE_PROVIDER_ERROR_SENTINEL", json.dumps(execute_events))
        unavailable = [
            event
            for event in execute_events
            if event["type"] == "agentic.receipt_response"
            and event["data"]["status"] == "unavailable"
        ]
        self.assertEqual(len(unavailable), 1)
        self.assertEqual(unavailable[0]["data"]["receipt_ref"], terminal_receipt.receipt_ref)

    def test_non_deadline_receipt_error_remains_bounded_and_unavailable(self) -> None:
        private_sentinel = "PRIVATE_RECEIPT_ERROR_SENTINEL"
        provider = _ReceiptExceptionProvider(
            self._capability("light_on"),
            RuntimeError(private_sentinel),
        )
        events = ThoughtLoop(
            tools=_NoopDirectTools(),
            agentic_turn_provider=provider,
        ).run_dicts(self._turn("少し明るくして。", turn_id="receipt_error"))

        unavailable = [
            event
            for event in events
            if event["type"] == "agentic.receipt_response"
            and event["data"]["status"] == "unavailable"
        ]
        self.assertEqual(provider.receipt_phases, ["noop"])
        self.assertEqual(len(unavailable), 1)
        self.assertEqual(unavailable[0]["data"]["phase"], "noop")
        self.assertNotIn(private_sentinel, json.dumps(events, ensure_ascii=False))

    def _capability(
        self,
        capability_id: str,
        *,
        arguments: dict[str, object] | None = None,
        speech: str = "この操作を進めます。",
        display: str = "操作を準備しています。",
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "kind": "capability",
            "response": {"speech": speech, "display": display},
            "capability": {
                "id": capability_id,
                "arguments": {} if arguments is None else arguments,
            },
        }

    def _turn(
        self,
        text: str,
        *,
        turn_id: str = "agentic_turn",
        context_refs: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "text": text,
            "turn_id": turn_id,
            "session_id": "agentic_integration_session",
            "locale": "ja-JP",
            "context_refs": {} if context_refs is None else context_refs,
        }
