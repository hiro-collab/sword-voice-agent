import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.agentic_turn_provider import (  # noqa: E402
    MAX_CAPABILITY_VIEW_COUNT,
    MAX_CATALOG_ID_LENGTH,
    MAX_CATALOG_VERSION_LENGTH,
    AgenticTurnProviderUnavailable,
    AgenticTurnProviderRequest,
    StaticAgenticTurnProvider,
    UnavailableAgenticTurnProvider,
)
from thought_core.capability_catalog import (  # noqa: E402
    CapabilityCatalogError,
    HomeCapabilityCatalog,
)
from thought_core.input_understanding import InputFrame  # noqa: E402
from thought_core.loop import ThoughtLoop  # noqa: E402
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


class _CapturingUnavailableProvider:
    def __init__(self) -> None:
        self.requests: list[AgenticTurnProviderRequest] = []

    def decide(self, request: AgenticTurnProviderRequest) -> object:
        self.requests.append(request)
        raise AgenticTurnProviderUnavailable("provider_unavailable")


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
        self.assertEqual(request.capability_view.catalog_id, "sword.home-actions")
        self.assertEqual(request.capability_view.catalog_version, "home-actions.v3")
        self.assertEqual(len(request.capability_view.capabilities), 15)
        self.assertLess(len(request.capability_view.capabilities), MAX_CAPABILITY_VIEW_COUNT)
        capabilities = {
            entry.capability_id: entry for entry in request.capability_view.capabilities
        }
        self.assertTrue(capabilities["light_on"].available)
        self.assertFalse(capabilities["aircon_on"].available)
        self.assertEqual(capabilities["light_on"].description, "ライトをつける")
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
                self.assertNotIn("memory.retrieved", event_types)
                self.assertNotIn("action.proposed", event_types)
                self.assertEqual(
                    [
                        event["data"]["tool"]
                        for event in events
                        if event["type"] == "tool.started"
                    ],
                    [],
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
                self.assertNotIn("memory.retrieved", event_types)
                self.assertNotIn("action.proposed", event_types)
                self.assertEqual(
                    [event for event in events if event["type"] == "tool.started"],
                    [],
                )
                self.assertEqual(tools.direct_preview_calls, [])
                self.assertEqual(tools.execute_calls, [])
                if reason is not None:
                    held = next(event for event in events if event["type"] == "agentic.decision")
                    self.assertEqual(held["data"]["reason"], reason)

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
                    "thought_core.loop.HomeCapabilityCatalog.from_default_path",
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
                        "thought_core.loop.HomeCapabilityCatalog.from_default_path",
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
            "door_close",
            speech=private_sentinel,
            display=private_sentinel,
        )
        confirmation_response = {"speech": "閉める前に確認します。", "display": "確認が必要です。"}
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
        self.assertEqual(execute_events[-1]["data"]["status"], "success")
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertTrue(execute_messages)
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
