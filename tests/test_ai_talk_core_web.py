import copy
from concurrent.futures import ThreadPoolExecutor
from unittest import TestCase
from argparse import Namespace
import json
from pathlib import Path
import shutil
import threading
import time
from uuid import uuid4
from unittest.mock import MagicMock, Mock, patch

from sword_voice_agent.apps.ai_talk_core_web import (
    AiTalkCoreWebDefaults,
    apply_ai_talk_core_web_defaults,
    build_live_private_turn_sink,
    build_native_startup_query,
    create_ai_talk_core_app,
    detect_native_startup_profile,
    run,
    set_checkbox_checked,
    should_use_native_profile,
)
from sword_voice_agent.apps.watch_handoff_to_thought_core import (
    ThoughtCoreAituberForwarder,
)


class AiTalkCoreWebDefaultsTest(TestCase):
    def test_sets_integration_checkboxes(self) -> None:
        html = """
        <input id="record_gate_auto" type="checkbox" value="true">
        <input id="record_save_handoff" type="checkbox" value="true">
        <input id="upload_save_handoff" type="checkbox" value="true">
        """

        result = apply_ai_talk_core_web_defaults(
            html,
            AiTalkCoreWebDefaults(record_gate_auto=True, save_handoff=True),
        )

        self.assertIn('id="record_gate_auto" type="checkbox" value="true" checked', result)
        self.assertIn('id="record_save_handoff" type="checkbox" value="true" checked', result)
        self.assertIn('id="upload_save_handoff" type="checkbox" value="true" checked', result)

    def test_can_leave_integration_checkboxes_unchecked(self) -> None:
        html = """
        <input id="record_gate_auto" type="checkbox" value="true" checked>
        <input id="record_save_handoff" type="checkbox" value="true" checked>
        <input id="upload_save_handoff" type="checkbox" value="true" checked>
        """

        result = apply_ai_talk_core_web_defaults(
            html,
            AiTalkCoreWebDefaults(record_gate_auto=False, save_handoff=False),
        )

        self.assertNotIn("checked", result)

    def test_does_not_duplicate_checked_attribute(self) -> None:
        html = '<input id="record_gate_auto" type="checkbox" value="true" checked>'

        result = set_checkbox_checked(html, element_id="record_gate_auto", checked=True)

        self.assertEqual(result.count("checked"), 1)

    def test_detects_latest_native_integration_profile(self) -> None:
        with workspace_tempdir() as tmp:
            app_js = tmp / "src" / "web" / "static" / "app.js"
            app_js.parent.mkdir(parents=True)
            app_js.write_text(
                "const OPTION_PROFILES = { integration: { record_gate_auto: '1' } };",
                encoding="utf-8",
            )

            self.assertEqual(detect_native_startup_profile(tmp), "integration")

    def test_ignores_removed_native_profile_names(self) -> None:
        with workspace_tempdir() as tmp:
            app_js = tmp / "src" / "web" / "static" / "app.js"
            app_js.parent.mkdir(parents=True)
            app_js.write_text(
                "const OPTION_PROFILES = { legacy: { record_gate_auto: '1' } };",
                encoding="utf-8",
            )

            self.assertIsNone(detect_native_startup_profile(tmp))

    def test_builds_native_startup_query_with_overrides(self) -> None:
        result = build_native_startup_query(
            "integration",
            AiTalkCoreWebDefaults(record_gate_auto=False, save_handoff=False),
        )

        self.assertIn("profile=integration", result)
        self.assertIn("record_gate_auto=0", result)
        self.assertIn("record_save_handoff=0", result)
        self.assertIn("upload_save_handoff=0", result)

    def test_disables_native_profile_when_all_integration_defaults_are_off(self) -> None:
        self.assertFalse(
            should_use_native_profile(
                AiTalkCoreWebDefaults(record_gate_auto=False, save_handoff=False)
            )
        )

    def test_run_accepts_current_path_validator_signature(self) -> None:
        with workspace_tempdir() as tmp:
            app_js = tmp / "src" / "web" / "static" / "app.js"
            app_js.parent.mkdir(parents=True)
            app_js.write_text(
                "const OPTION_PROFILES = { integration: { record_gate_auto: '1' } };",
                encoding="utf-8",
            )
            app = Mock()

            with patch(
                "sword_voice_agent.apps.ai_talk_core_web.load_ai_talk_core_app",
                return_value=app,
            ):
                result = run(
                    Namespace(
                        ai_talk_core_root=str(tmp),
                        host="127.0.0.1",
                        port=8000,
                        record_gate_auto=True,
                        save_handoff=True,
                    )
                )

            self.assertEqual(result, 0)
            app.run.assert_called_once()

    def test_injects_private_turn_sink_into_current_ai_talk_core_app(self) -> None:
        module = Mock()
        app = Mock()
        sink = Mock()
        module.create_app.return_value = app

        result = create_ai_talk_core_app(
            module,
            host="127.0.0.1",
            port=8000,
            runtime_status_writer=None,
            started_at="2026-07-13T00:00:00Z",
            private_turn_sink=sink,
        )

        self.assertIs(result, app)
        module.create_app.assert_called_once_with(
            host="127.0.0.1",
            port=8000,
            runtime_status_writer=None,
            started_at="2026-07-13T00:00:00Z",
            private_turn_sink=sink,
        )

    def test_private_turn_sink_submits_once_after_one_completion(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def send_turn_streaming(self, payload, *, on_event):
                copied = copy.deepcopy(payload)
                self.calls.append(copied)
                turn_id = copied["private_turn"]["turn_id"]
                on_event(Mock(is_completed=True, turn_id=turn_id))
                return Mock(conversation_id=turn_id)

        client = FakeClient()
        private_marker = "private-live-speech-do-not-echo"
        sink = build_live_private_turn_sink(client)

        result = sink(accepted_candidate_audit(), private_marker)

        self.assertEqual(
            result,
            {
                "result_class": "thought_core_turninput_accepted",
                "submission_count": 1,
                "thought_core_turninput_count": 1,
            },
        )
        self.assertEqual(len(client.calls), 1)
        sent = client.calls[0]
        self.assertEqual(sent["private_turn"]["text"], private_marker)
        self.assertNotIn(
            "text",
            sent["accepted_user_speech_candidate"],
        )
        self.assertNotIn(private_marker, repr(result))

    def test_private_turn_sink_forwards_one_public_assistant_message_after_acceptance(
        self,
    ) -> None:
        private_marker = "private-live-speech-do-not-forward"
        forwarded_events: list[object] = []
        closed: list[bool] = []
        factory_turn_ids: list[str] = []

        class FakeForwarder:
            def __call__(self, event: object) -> None:
                forwarded_events.append(event)

            def close(self) -> None:
                closed.append(True)

        class FakeClient:
            def send_turn_streaming(self, payload, *, on_event):
                turn_id = payload["private_turn"]["turn_id"]
                on_event(
                    Mock(
                        is_message=True,
                        is_completed=False,
                        turn_id=turn_id,
                        event_id="evt-live-1",
                        speech="公開応答です",
                    )
                )
                on_event(
                    Mock(
                        is_message=False,
                        is_completed=True,
                        turn_id=turn_id,
                    )
                )
                return Mock(conversation_id=turn_id)

        def build_forwarder(turn_id: str) -> FakeForwarder:
            factory_turn_ids.append(turn_id)
            return FakeForwarder()

        result = build_live_private_turn_sink(
            FakeClient(),
            aituber_forwarder_factory=build_forwarder,
        )(accepted_candidate_audit(), private_marker)

        self.assertEqual(result["thought_core_turninput_count"], 1)
        self.assertEqual(len(factory_turn_ids), 1)
        self.assertRegex(factory_turn_ids[0], r"^turn_live_speech_[0-9a-f]{32}$")
        self.assertEqual(len(forwarded_events), 1)
        self.assertEqual(forwarded_events[0].speech, "公開応答です")
        self.assertEqual(forwarded_events[0].event_id, "evt-live-1")
        self.assertEqual(closed, [True])
        self.assertNotIn(private_marker, repr(forwarded_events))

    @patch("sword_voice_agent.apps.watch_handoff_to_thought_core.request.urlopen")
    def test_private_turn_sink_invokes_production_aituber_post(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        urlopen.return_value = response

        class FakeClient:
            def send_turn_streaming(self, payload, *, on_event):
                turn_id = payload["private_turn"]["turn_id"]
                on_event(
                    Mock(
                        is_message=True,
                        is_completed=False,
                        turn_id=turn_id,
                        event_id="evt-live-post-1",
                        speech="一文目です。二文目です。",
                    )
                )
                on_event(
                    Mock(
                        is_message=False,
                        is_completed=True,
                        turn_id=turn_id,
                    )
                )
                return Mock(conversation_id=turn_id)

        with patch.dict(
            "os.environ",
            {
                "AITUBER_MESSAGE_URL": (
                    "http://127.0.0.1:3000/api/messages"
                    "?clientId=client-1&type=direct_send"
                ),
                "AITUBER_HTTP_TIMEOUT_S": "0.2",
            },
            clear=True,
        ):
            result = build_live_private_turn_sink(FakeClient())(
                accepted_candidate_audit(),
                "private-do-not-publish",
            )

        self.assertEqual(result["thought_core_turninput_count"], 1)
        self.assertEqual(urlopen.call_count, 1)
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(payload["messages"], ["一文目です。二文目です。"])
        self.assertRegex(payload["turn_id"], r"^turn_live_speech_[0-9a-f]{32}$")
        self.assertEqual(payload["message_id"], "evt-live-post-1")
        self.assertEqual(
            payload["response_source"],
            "thought_core_assistant_message",
        )
        self.assertNotIn("private-do-not-publish", repr(payload))

    @patch("sword_voice_agent.apps.watch_handoff_to_thought_core.request.urlopen")
    def test_private_turn_sink_rejects_over_limit_presentation_without_rewriting_acceptance(
        self,
        urlopen: MagicMock,
    ) -> None:
        private_marker = "private-over-limit-do-not-echo"
        created_forwarders: list[ThoughtCoreAituberForwarder] = []

        class FakeClient:
            def send_turn_streaming(self, payload, *, on_event):
                turn_id = payload["private_turn"]["turn_id"]
                on_event(
                    Mock(
                        is_message=True,
                        is_completed=False,
                        turn_id=turn_id,
                        event_id="evt-live-over-limit",
                        speech="長" * 4001,
                    )
                )
                on_event(
                    Mock(
                        is_message=False,
                        is_completed=True,
                        turn_id=turn_id,
                    )
                )
                return Mock(conversation_id=turn_id)

        def build_forwarder(turn_id: str) -> ThoughtCoreAituberForwarder:
            forwarder = ThoughtCoreAituberForwarder(
                "http://127.0.0.1:3000/api/messages"
                "?clientId=client-1&type=direct_send",
                timeout_s=0.2,
                async_post=False,
                max_chars=4000,
                turn_id=turn_id,
                preserve_message_unit=True,
            )
            created_forwarders.append(forwarder)
            return forwarder

        result = build_live_private_turn_sink(
            FakeClient(),
            aituber_forwarder_factory=build_forwarder,
        )(accepted_candidate_audit(), private_marker)

        self.assertEqual(
            result,
            {
                "result_class": "thought_core_turninput_accepted",
                "submission_count": 1,
                "thought_core_turninput_count": 1,
            },
        )
        self.assertEqual(len(created_forwarders), 1)
        self.assertEqual(created_forwarders[0].dispatch_count, 0)
        self.assertEqual(created_forwarders[0].error_count, 1)
        urlopen.assert_not_called()
        self.assertNotIn(private_marker, repr(result))
        self.assertNotIn("長" * 100, repr(result))

    def test_private_turn_sink_does_not_forward_ambiguous_assistant_messages(
        self,
    ) -> None:
        factory = Mock()

        class FakeClient:
            def send_turn_streaming(self, payload, *, on_event):
                turn_id = payload["private_turn"]["turn_id"]
                for event_id in ("evt-live-1", "evt-live-2"):
                    on_event(
                        Mock(
                            is_message=True,
                            is_completed=False,
                            turn_id=turn_id,
                            event_id=event_id,
                            speech="公開応答です",
                        )
                    )
                on_event(
                    Mock(
                        is_message=False,
                        is_completed=True,
                        turn_id=turn_id,
                    )
                )
                return Mock(conversation_id=turn_id)

        result = build_live_private_turn_sink(
            FakeClient(),
            aituber_forwarder_factory=factory,
        )(accepted_candidate_audit(), "private")

        self.assertEqual(result["thought_core_turninput_count"], 1)
        factory.assert_not_called()

    def test_private_turn_sink_does_not_forward_unowned_or_unsafe_message_ids(
        self,
    ) -> None:
        for event_turn_id, event_id in (
            ("turn_other", "evt-live-1"),
            (None, "evt-live-1"),
            ("current", "private/raw"),
            ("current", None),
        ):
            with self.subTest(event_turn_id=event_turn_id, event_id=event_id):
                factory = Mock()

                class FakeClient:
                    def send_turn_streaming(self, payload, *, on_event):
                        turn_id = payload["private_turn"]["turn_id"]
                        on_event(
                            Mock(
                                is_message=True,
                                is_completed=False,
                                turn_id=(
                                    turn_id
                                    if event_turn_id == "current"
                                    else event_turn_id
                                ),
                                event_id=event_id,
                                speech="公開応答です",
                            )
                        )
                        on_event(
                            Mock(
                                is_message=False,
                                is_completed=True,
                                turn_id=turn_id,
                            )
                        )
                        return Mock(conversation_id=turn_id)

                result = build_live_private_turn_sink(
                    FakeClient(),
                    aituber_forwarder_factory=factory,
                )(accepted_candidate_audit(), "private")

                self.assertEqual(result["thought_core_turninput_count"], 1)
                factory.assert_not_called()

    def test_private_turn_sink_rejects_mixed_valid_and_invalid_message_stream(
        self,
    ) -> None:
        factory = Mock()

        class FakeClient:
            def send_turn_streaming(self, payload, *, on_event):
                turn_id = payload["private_turn"]["turn_id"]
                on_event(
                    Mock(
                        is_message=True,
                        is_completed=False,
                        turn_id=turn_id,
                        event_id="evt-live-1",
                        speech="公開応答です",
                    )
                )
                on_event(
                    Mock(
                        is_message=True,
                        is_completed=False,
                        turn_id="turn_other",
                        event_id="private/raw",
                        speech="混在応答です",
                    )
                )
                on_event(
                    Mock(
                        is_message=False,
                        is_completed=True,
                        turn_id=turn_id,
                    )
                )
                return Mock(conversation_id=turn_id)

        result = build_live_private_turn_sink(
            FakeClient(),
            aituber_forwarder_factory=factory,
        )(accepted_candidate_audit(), "private")

        self.assertEqual(result["thought_core_turninput_count"], 1)
        factory.assert_not_called()

    def test_private_turn_sink_keeps_acceptance_when_presentation_fails(
        self,
    ) -> None:
        private_marker = "private-presentation-error-do-not-echo"

        class FakeClient:
            def send_turn_streaming(self, payload, *, on_event):
                turn_id = payload["private_turn"]["turn_id"]
                on_event(
                    Mock(
                        is_message=True,
                        is_completed=False,
                        turn_id=turn_id,
                        event_id="evt-live-1",
                        speech="公開応答です",
                    )
                )
                on_event(
                    Mock(
                        is_message=False,
                        is_completed=True,
                        turn_id=turn_id,
                    )
                )
                return Mock(conversation_id=turn_id)

        class FailingForwarder:
            def __init__(self, failure: str) -> None:
                self.failure = failure

            def __call__(self, _event: object) -> None:
                if self.failure == "call":
                    raise RuntimeError(private_marker)

            def close(self) -> None:
                if self.failure == "close":
                    raise RuntimeError(private_marker)

        factories = {
            "factory": lambda _turn_id: (_ for _ in ()).throw(
                RuntimeError(private_marker)
            ),
            "call": lambda _turn_id: FailingForwarder("call"),
            "close": lambda _turn_id: FailingForwarder("close"),
        }
        for failure, factory in factories.items():
            with self.subTest(failure=failure):
                result = build_live_private_turn_sink(
                    FakeClient(),
                    aituber_forwarder_factory=factory,
                )(accepted_candidate_audit(), private_marker)

                self.assertEqual(
                    result,
                    {
                        "result_class": "thought_core_turninput_accepted",
                        "submission_count": 1,
                        "thought_core_turninput_count": 1,
                    },
                )
                self.assertNotIn(private_marker, repr(result))

    def test_private_turn_sink_fails_closed_without_exact_completion(self) -> None:
        private_marker = "private-client-error-do-not-echo"
        variants = ("error", "missing", "duplicate")
        for variant in variants:
            with self.subTest(variant=variant):
                client = Mock()

                def send(payload, *, on_event):
                    turn_id = payload["private_turn"]["turn_id"]
                    if variant == "error":
                        raise RuntimeError(private_marker)
                    if variant == "duplicate":
                        on_event(Mock(is_completed=True, turn_id=turn_id))
                        on_event(Mock(is_completed=True, turn_id=turn_id))
                    return Mock(conversation_id=turn_id)

                client.send_turn_streaming.side_effect = send
                result = build_live_private_turn_sink(client)(
                    accepted_candidate_audit(),
                    private_marker,
                )
                self.assertEqual(
                    result,
                    {
                        "result_class": "thought_core_turninput_rejected",
                        "submission_count": 0,
                        "thought_core_turninput_count": 0,
                    },
                )
                self.assertEqual(client.send_turn_streaming.call_count, 1)
                self.assertNotIn(private_marker, repr(result))

    def test_private_turn_sink_rejects_non_gate_authority_before_send(self) -> None:
        client = Mock()
        candidate = accepted_candidate_audit()
        candidate["input_gate"] = {
            **candidate["input_gate"],
            "input_gate_decision_owner": "caller",
        }

        result = build_live_private_turn_sink(client)(candidate, "private")

        self.assertEqual(result["submission_count"], 0)
        self.assertEqual(result["thought_core_turninput_count"], 0)
        client.send_turn_streaming.assert_not_called()

    def test_private_turn_sink_rejects_sequential_candidate_replay(self) -> None:
        client = completed_turn_client()
        sink = build_live_private_turn_sink(client)
        candidate = accepted_candidate_audit()

        first = sink(candidate, "private first")
        replay = sink(candidate, "private replay")

        self.assertEqual(first["thought_core_turninput_count"], 1)
        self.assertEqual(replay["thought_core_turninput_count"], 0)
        self.assertEqual(client.send_turn_streaming.call_count, 1)

    def test_private_turn_sink_rejects_concurrent_candidate_replay(self) -> None:
        client = completed_turn_client(delay_seconds=0.02)
        sink = build_live_private_turn_sink(client)
        candidate = accepted_candidate_audit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda text: sink(candidate, text),
                    ("private one", "private two"),
                )
            )

        self.assertEqual(
            sorted(result["thought_core_turninput_count"] for result in results),
            [0, 1],
        )
        self.assertEqual(client.send_turn_streaming.call_count, 1)

    def test_private_turn_sink_consumes_claim_when_send_fails(self) -> None:
        client = Mock()
        client.send_turn_streaming.side_effect = RuntimeError("private")
        sink = build_live_private_turn_sink(client)
        candidate = accepted_candidate_audit()

        failed = sink(candidate, "private first")
        retry = sink(candidate, "private retry")

        self.assertEqual(failed["thought_core_turninput_count"], 0)
        self.assertEqual(retry["thought_core_turninput_count"], 0)
        self.assertEqual(client.send_turn_streaming.call_count, 1)

    def test_private_turn_sink_requires_canonical_candidate_id(self) -> None:
        client = completed_turn_client()
        for candidate_id in (None, "", "candidate:raw", "ausc_bad/segment"):
            with self.subTest(candidate_id=candidate_id):
                candidate = accepted_candidate_audit()
                candidate["candidate_id"] = candidate_id
                result = build_live_private_turn_sink(client)(candidate, "private")
                self.assertEqual(result["thought_core_turninput_count"], 0)
        client.send_turn_streaming.assert_not_called()


class workspace_tempdir:
    def __enter__(self) -> Path:
        self.path = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
        self.path.mkdir(parents=True)
        return self.path

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def accepted_candidate_audit() -> dict[str, object]:
    return {
        "schema_version": "accepted_user_speech_candidate_input_gate.v0",
        "candidate_id": "ausc_live:cid_0123456789abcdef0123456789abcdef",
        "source_kind": "user_speech_candidate",
        "speaker_role": "user_candidate",
        "input_gate": {
            "input_gate_decision_owner": "ai_talk_core_input_gate",
            "input_gate_decision_class": "accepted_user_speech_candidate",
            "normal_turn_block_reason": None,
        },
        "acceptance_decision": {
            "acceptance_status": "accepted_user_speech_candidate",
            "may_materialize_thought_core_turninput": True,
            "private_text_handoff_required": True,
        },
        "raw_private_publication_flags": False,
    }


def completed_turn_client(*, delay_seconds: float = 0.0) -> Mock:
    client = Mock()
    call_lock = threading.Lock()

    def send(payload, *, on_event):
        with call_lock:
            turn_id = payload["private_turn"]["turn_id"]
        if delay_seconds:
            time.sleep(delay_seconds)
        on_event(Mock(is_completed=True, turn_id=turn_id))
        return Mock(conversation_id=turn_id)

    client.send_turn_streaming.side_effect = send
    return client
