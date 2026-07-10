from pathlib import Path
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoffError
from sword_voice_agent.apps.send_handoff_to_thought_core import (
    build_parser,
    build_result,
    format_event_line,
    main,
    output_safe_result,
    run,
)
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent
from sword_voice_agent.protocol.messages import AgentResponse


FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANONICAL_CANDIDATE = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "accepted_user_speech_candidate_input_gate"
    / "examples"
    / "source_static_accepted_private_user_speech_candidate.example.json"
)


class SendHandoffToThoughtCoreTest(TestCase):
    def test_builds_canonical_candidate_envelope_from_separate_private_turn(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            candidate_path = root / "candidate.json"
            private_turn_path = root / "private-turn.json"
            candidate_path.write_text(
                CANONICAL_CANDIDATE.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            private_turn_path.write_text(
                json.dumps(
                    {
                        "text": "synthetic private user speech",
                        "turn_id": "turn_candidate_send_001",
                        "session_id": "session_candidate_send_001",
                        "locale": "ja-JP",
                        "context_refs": {
                            "conversation_attempt_ref": "attempt:opaque_send_001",
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--accepted-speech-candidate-json",
                    str(candidate_path),
                    "--private-turn-json",
                    str(private_turn_path),
                ]
            )

            result = build_result(args)

        self.assertNotIn("text", result["request"])
        self.assertEqual(
            result["turn_payload"]["private_turn"]["turn_id"],
            "turn_candidate_send_001",
        )
        self.assertEqual(
            result["turn_payload"]["accepted_user_speech_candidate"][
                "schema_version"
            ],
            "accepted_user_speech_candidate_input_gate.v0",
        )

    def test_candidate_output_projection_omits_private_and_text_like_fields(self) -> None:
        result = {
            "turn_payload": {
                "accepted_user_speech_candidate": {
                    "text_publication": {
                        "recognized_text": "candidate-marker-keep-private",
                    },
                    "source_path": "C:/private/candidate-marker.wav",
                },
                "private_turn": {
                    "text": "private-marker-keep-private",
                    "context_refs": {
                        "source_path": "C:/private/private-marker.wav",
                    },
                },
            },
            "response": {"text": "assistant-marker-not-projected"},
        }

        projection = output_safe_result(result)
        rendered = json.dumps(projection, ensure_ascii=False)

        self.assertEqual(
            projection["input_gate_class"],
            "contract_declared_accepted_user_speech_candidate_not_runtime_observed",
        )
        self.assertIsNone(projection["thought_core_turninput_count"])
        self.assertEqual(
            projection["turn_materialization_class"],
            "not_observed_source_static",
        )
        self.assertEqual(
            projection["assistant_response_class"],
            "not_observed_source_static",
        )
        for marker in (
            "candidate-marker-keep-private",
            "private-marker-keep-private",
            "C:/private/candidate-marker.wav",
            "C:/private/private-marker.wav",
            "assistant-marker-not-projected",
        ):
            self.assertNotIn(marker, rendered)

    @patch("sword_voice_agent.apps.send_handoff_to_thought_core.ThoughtCoreClient")
    def test_candidate_send_print_and_status_use_output_projection(
        self,
        client_class: MagicMock,
    ) -> None:
        private_marker = "private-marker-send-keep-private"
        candidate_marker = "candidate-marker-send-keep-private"
        source_path_marker = "C:/private/send-source-path-marker.wav"
        client = MagicMock()
        client.send_turn_streaming.side_effect = lambda payload, **_: AgentResponse(
            text="assistant-marker-send-not-projected",
            conversation_id=payload["private_turn"]["turn_id"],
            raw={"status": "success"},
        )
        client_class.from_env.return_value = client

        with workspace_tempdir() as tmp:
            root = Path(tmp)
            candidate_path = root / "candidate.json"
            private_turn_path = root / "private-turn.json"
            candidate = json.loads(
                (
                    Path(__file__).resolve().parents[3]
                    / "contracts"
                    / "accepted_user_speech_candidate_input_gate"
                    / "examples"
                    / "source_static_accepted_prepared_sample_candidate.example.json"
                ).read_text(encoding="utf-8")
            )
            candidate["text_publication"]["expected_sample_text"] = candidate_marker
            candidate["text_publication"]["recognized_text"] = candidate_marker
            candidate["text_publication"]["content_match_text"] = candidate_marker
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            private_turn_path.write_text(
                json.dumps(
                    {
                        "text": private_marker,
                        "turn_id": "turn_candidate_send_projection_001",
                        "session_id": "session_candidate_send_projection_001",
                        "locale": "ja-JP",
                        "context_refs": {"source_path": source_path_marker},
                    }
                ),
                encoding="utf-8",
            )
            status_dir = root / "status"
            argv = [
                "--accepted-speech-candidate-json",
                str(candidate_path),
                "--private-turn-json",
                str(private_turn_path),
                "--status-dir",
                str(status_dir),
            ]

            run(build_parser().parse_args(argv))
            status_output = (status_dir / "latest_thought_core_response.json").read_text(
                encoding="utf-8"
            )
            with patch("builtins.print") as print_mock:
                self.assertEqual(main([*argv, "--dry-run", "--print-json"]), 0)
            print_output = str(print_mock.call_args.args[0])

        for output in (status_output, print_output):
            for marker in (
                private_marker,
                candidate_marker,
                source_path_marker,
                "assistant-marker-send-not-projected",
            ):
                self.assertNotIn(marker, output)
            projected = json.loads(output)
            self.assertEqual(
                projected["input_gate_class"],
                "contract_declared_accepted_user_speech_candidate_not_runtime_observed",
            )
            self.assertIsNone(projected["thought_core_turninput_count"])
            self.assertEqual(
                projected["turn_materialization_class"],
                "not_observed_source_static",
            )
            self.assertEqual(
                projected["assistant_response_class"], "not_observed_source_static"
            )

    def test_dry_run_builds_turn_payload_without_calling_thought_core(self) -> None:
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--dry-run",
                "--context",
                "mode=test",
                "--context-ref",
                "voice_turn=voice-1",
                "--session-id",
                "living_room_main",
                "--turn-id",
                "turn-test",
            ]
        )

        result = run(args)

        self.assertIsNone(result["response"])
        self.assertEqual(result["request"]["text"], "冷蔵庫の材料から買い物リストを提案する")
        self.assertEqual(result["request"]["context"]["mode"], "test")
        self.assertEqual(result["turn_payload"]["turn_id"], "turn-test")
        self.assertEqual(result["turn_payload"]["session_id"], "living_room_main")
        self.assertEqual(result["turn_payload"]["context_refs"]["voice_turn"], "voice-1")

    def test_can_include_transcript_context(self) -> None:
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--dry-run",
                "--include-transcript-context",
            ]
        )

        result = run(args)

        self.assertEqual(
            result["request"]["context"]["transcript"],
            "買い物リストを作って",
        )

    def test_dry_run_can_send_direct_text_without_handoff(self) -> None:
        args = build_parser().parse_args(
            [
                "--text",
                "電気つけて",
                "--dry-run",
                "--session-id",
                "living_room_main",
                "--turn-id",
                "turn-manual",
            ]
        )

        result = run(args)

        self.assertEqual(result["request"]["text"], "電気つけて")
        self.assertEqual(result["request"]["context"]["source"], "manual")
        self.assertEqual(result["turn_payload"]["turn_id"], "turn-manual")
        self.assertEqual(result["turn_payload"]["session_id"], "living_room_main")

    @patch("sword_voice_agent.apps.send_handoff_to_thought_core.ThoughtCoreClient")
    def test_run_calls_thought_core_client(self, client_class: MagicMock) -> None:
        client = MagicMock()
        event = ThoughtCoreStreamEvent(
            event_type="assistant.message",
            turn_id="turn-test",
            session_id="living_room_main",
            seq=1,
            data={"speech": "了解です"},
            elapsed_s=0.1,
        )

        def fake_streaming(turn_payload, *, on_event=None):
            if on_event is not None:
                on_event(event)
            return AgentResponse(
                text="了解です",
                conversation_id=turn_payload["turn_id"],
                raw={"ok": True},
            )

        client.send_turn_streaming.side_effect = fake_streaming
        client_class.from_env.return_value = client
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--turn-id",
                "turn-test",
                "--session-id",
                "living_room_main",
                "--status-dir",
                "",
            ]
        )

        result = run(args)

        self.assertEqual(result["response"]["text"], "了解です")
        self.assertEqual(result["events"][0]["event_type"], "assistant.message")
        client.send_turn_streaming.assert_called_once()

    @patch("sword_voice_agent.apps.send_handoff_to_thought_core.ThoughtCoreClient")
    def test_run_writes_status_for_manual_text(self, client_class: MagicMock) -> None:
        client = MagicMock()

        def fake_streaming(turn_payload, *, on_event=None):
            if on_event is not None:
                on_event(
                    ThoughtCoreStreamEvent(
                        event_type="assistant.message",
                        turn_id=turn_payload["turn_id"],
                        session_id=turn_payload["session_id"],
                        seq=1,
                        data={"speech": "了解です"},
                        elapsed_s=0.1,
                    )
                )
                on_event(
                    ThoughtCoreStreamEvent(
                        event_type="turn.completed",
                        turn_id=turn_payload["turn_id"],
                        session_id=turn_payload["session_id"],
                        seq=2,
                        data={"status": "success"},
                        elapsed_s=0.2,
                    )
                )
            return AgentResponse(
                text="了解です",
                conversation_id=turn_payload["turn_id"],
                raw={"_streaming": {"event_count": 2}},
            )

        client.send_turn_streaming.side_effect = fake_streaming
        client_class.from_env.return_value = client

        with workspace_tempdir() as tmp:
            args = build_parser().parse_args(
                [
                    "--text",
                    "電気つけて",
                    "--turn-id",
                    "turn-status",
                    "--session-id",
                    "living_room_main",
                    "--status-dir",
                    tmp,
                ]
            )

            run(args)

            latest = json.loads(
                (Path(tmp) / "latest_thought_core_response.json").read_text(
                    encoding="utf-8"
                )
            )
            events = [
                json.loads(line)
                for line in (Path(tmp) / "events.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(latest["turn_id"], "turn-status")
            self.assertEqual(
                [event["type"] for event in events],
                [
                    "thought_core.stream_event",
                    "thought_core.first_message",
                    "thought_core.stream_event",
                    "thought_core.completed",
                    "thought_core.response",
                ],
            )
            self.assertEqual(events[-1]["source"], "send_handoff_to_thought_core")
            self.assertEqual(events[0]["payload"]["phase"], "message")
            self.assertEqual(events[0]["payload"]["elapsed_s"], 0.1)
            self.assertEqual(events[2]["payload"]["delta_elapsed_s"], 0.1)

    def test_rejects_placeholder_root(self) -> None:
        args = build_parser().parse_args(
            [
                "--ai-talk-core-root",
                "<ai_talk_core_root>",
                "--dry-run",
            ]
        )

        with self.assertRaises(AiTalkCoreHandoffError):
            run(args)

    def test_format_event_line_includes_speech_or_tool(self) -> None:
        speech = ThoughtCoreStreamEvent(
            event_type="assistant.speech_delta",
            turn_id="turn-test",
            session_id="living_room_main",
            seq=3,
            data={"delta": "了解"},
        )
        tool = ThoughtCoreStreamEvent(
            event_type="tool.started",
            turn_id="turn-test",
            session_id="living_room_main",
            seq=4,
            data={"tool": "environment.observe"},
        )

        self.assertEqual(format_event_line(speech), "3 assistant.speech_delta: 了解")
        self.assertEqual(format_event_line(tool), "4 tool.started: environment.observe")


def workspace_tempdir():
    from contextlib import contextmanager
    import shutil
    from uuid import uuid4

    @contextmanager
    def _workspace_tempdir():
        root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
        root.mkdir(parents=True)
        try:
            yield str(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    return _workspace_tempdir()
