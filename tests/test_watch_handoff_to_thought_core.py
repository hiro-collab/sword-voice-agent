from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoffError
from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent
from sword_voice_agent.apps.watch_handoff_to_thought_core import (
    HandoffSignature,
    ThoughtCoreAituberForwarder,
    build_parser,
    format_missing_handoff_message,
    format_watch_start_message,
    handoff_signature,
    pending_action_review,
    result_module_detail,
    resolve_handoff_json_path,
    run_pending_action_reviews,
    run_once,
    watcher_module_detail,
    write_watcher_module_status,
)
from sword_voice_agent.protocol.messages import AgentResponse


class FakeThoughtCoreClient:
    def __init__(self) -> None:
        self.turn_payloads = []

    def send_turn_streaming(self, turn_payload, *, on_event=None):
        self.turn_payloads.append(turn_payload)
        events = [
            ThoughtCoreStreamEvent(
                event_type="assistant.speech_delta",
                turn_id=turn_payload["turn_id"],
                session_id=turn_payload["session_id"],
                seq=1,
                data={"delta": "了解"},
                elapsed_s=0.1,
            ),
            ThoughtCoreStreamEvent(
                event_type="assistant.message",
                turn_id=turn_payload["turn_id"],
                session_id=turn_payload["session_id"],
                seq=2,
                data={"speech": "了解です"},
                elapsed_s=0.2,
            ),
            ThoughtCoreStreamEvent(
                event_type="turn.completed",
                turn_id=turn_payload["turn_id"],
                session_id=turn_payload["session_id"],
                seq=3,
                data={"status": "success"},
                elapsed_s=0.3,
            ),
        ]
        for event in events:
            if on_event is not None:
                on_event(event)
        return AgentResponse(
            text="了解です",
            conversation_id=turn_payload["turn_id"],
            raw={"status": "success"},
        )


class PendingReviewThoughtCoreClient:
    def __init__(self) -> None:
        self.turn_payloads = []

    def send_turn_streaming(self, turn_payload, *, on_event=None):
        self.turn_payloads.append(turn_payload)
        if len(self.turn_payloads) == 1:
            events = [
                ThoughtCoreStreamEvent(
                    event_type="action.review_pending",
                    turn_id=turn_payload["turn_id"],
                    session_id=turn_payload["session_id"],
                    seq=1,
                    data={
                        "action": {"action_id": "aircon_on", "target": "aircon"},
                        "review": {"status": "unknown"},
                        "observations_done": 1,
                        "observation_attempts": 3,
                        "settle_ms": 2000,
                    },
                    elapsed_s=0.1,
                ),
                ThoughtCoreStreamEvent(
                    event_type="assistant.message",
                    turn_id=turn_payload["turn_id"],
                    session_id=turn_payload["session_id"],
                    seq=2,
                    data={"speech": "あとで見直します"},
                    elapsed_s=0.2,
                ),
                ThoughtCoreStreamEvent(
                    event_type="turn.completed",
                    turn_id=turn_payload["turn_id"],
                    session_id=turn_payload["session_id"],
                    seq=3,
                    data={"status": "verification_pending"},
                    elapsed_s=0.3,
                ),
            ]
            response_text = "あとで見直します"
            raw = {"data": {"status": "verification_pending"}}
        else:
            events = [
                ThoughtCoreStreamEvent(
                    event_type="assistant.message",
                    turn_id=turn_payload["turn_id"],
                    session_id=turn_payload["session_id"],
                    seq=1,
                    data={"speech": "見直して確認できました"},
                    elapsed_s=0.1,
                ),
                ThoughtCoreStreamEvent(
                    event_type="turn.completed",
                    turn_id=turn_payload["turn_id"],
                    session_id=turn_payload["session_id"],
                    seq=2,
                    data={"status": "success"},
                    elapsed_s=0.2,
                ),
            ]
            response_text = "見直して確認できました"
            raw = {"data": {"status": "success"}}
        for event in events:
            if on_event is not None:
                on_event(event)
        return AgentResponse(
            text=response_text,
            conversation_id=turn_payload["turn_id"],
            raw=raw,
        )


class WatchHandoffToThoughtCoreTest(TestCase):
    def test_run_once_sends_and_persists_thought_core_result(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="電気つけて", turn_id="turn-1")
            status_dir = root / ".cache" / "sword_voice_agent"
            client = FakeThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--session-id",
                    "living_room_main",
                    "--status-dir",
                    str(status_dir),
                ]
            )

            result = run_once(args, client=client)

            self.assertFalse(result["skipped"])
            self.assertEqual(client.turn_payloads[0]["text"], "電気つけて")
            self.assertEqual(client.turn_payloads[0]["turn_id"], "turn-1")
            self.assertEqual(result["response"]["text"], "了解です")
            cache_dir = root / ".cache" / "codex"
            saved = json.loads(
                (cache_dir / "web_thought_core_latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["response"]["text"], "了解です")
            self.assertEqual(
                (cache_dir / "web_thought_core_latest.txt").read_text(encoding="utf-8"),
                "了解です",
            )
            latest_status = json.loads(
                (status_dir / "latest_thought_core_response.json").read_text(
                    encoding="utf-8"
                )
            )
            status_events = [
                json.loads(line)
                for line in (status_dir / "events.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(latest_status["turn_id"], "turn-1")
            self.assertEqual(
                [event["type"] for event in status_events],
                [
                    "thought_core.stream_event",
                    "thought_core.stream_event",
                    "thought_core.first_message",
                    "thought_core.stream_event",
                    "thought_core.completed",
                    "thought_core.response",
                ],
            )
            self.assertEqual(status_events[-1]["source"], "watch_handoff_to_thought_core")
            self.assertEqual(status_events[0]["payload"]["speech"], "[redacted]")
            self.assertEqual(status_events[0]["payload"]["phase"], "speech_delta")
            self.assertEqual(status_events[0]["payload"]["elapsed_s"], 0.1)
            self.assertEqual(status_events[1]["payload"]["delta_elapsed_s"], 0.1)
            self.assertNotIn("了解です", json.dumps(status_events, ensure_ascii=False))

    def test_run_once_skips_no_speech_placeholder_by_default(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="音声を認識できませんでした。")
            client = FakeThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--status-dir",
                    "",
                ]
            )

            result = run_once(args, client=client)

            self.assertTrue(result["skipped"])
            self.assertEqual(result["skip_reason"], "no_speech_placeholder")
            self.assertEqual(client.turn_payloads, [])

    def test_parser_accepts_aituber_speech_max_chars(self) -> None:
        args = build_parser().parse_args(["--aituber-speech-max-chars", "40"])

        self.assertEqual(args.aituber_speech_max_chars, 40)

    def test_run_pending_action_reviews_sends_synthetic_review_turn(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="エアコンをつけて", turn_id="turn-aircon")
            client = PendingReviewThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--session-id",
                    "living_room_main",
                    "--status-dir",
                    "",
                    "--auto-review-max-delay-s",
                    "0.05",
                ]
            )

            initial = run_once(args, client=client)
            sleeps: list[float] = []
            review_results = run_pending_action_reviews(
                args,
                initial,
                client=client,
                sleep=sleeps.append,
                printer=lambda *_: None,
            )

        self.assertIsNotNone(pending_action_review(initial))
        self.assertEqual(sleeps, [0.05])
        self.assertEqual(len(review_results), 1)
        self.assertEqual(client.turn_payloads[1]["text"], "確認して")
        self.assertEqual(client.turn_payloads[1]["session_id"], "living_room_main")
        self.assertEqual(client.turn_payloads[1]["turn_id"], "turn-aircon_review_2")
        self.assertEqual(review_results[0]["response"]["text"], "見直して確認できました")

    @patch("sword_voice_agent.apps.watch_handoff_to_thought_core.request.urlopen")
    def test_run_once_forwards_tts_chunks(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        urlopen.return_value = response

        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="電気つけて", turn_id="turn-tts")
            client = FakeThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--session-id",
                    "living_room_main",
                    "--status-dir",
                    "",
                    "--tts-chunk-url",
                    "http://127.0.0.1:8765/api/tts/chunk",
                    "--tts-http-timeout-s",
                    "0.1",
                ]
            )

            run_once(args, client=client)

        payloads = [
            json.loads(call.args[0].data.decode("utf-8"))
            for call in urlopen.call_args_list
        ]
        self.assertEqual(payloads[0]["delta"], "了解")
        self.assertEqual(payloads[0]["turn_id"], "turn-tts")
        self.assertEqual(payloads[-1]["event"], "turn.completed")
        self.assertTrue(payloads[-1]["final"])

    @patch("sword_voice_agent.apps.watch_handoff_to_thought_core.request.urlopen")
    def test_run_once_posts_local_ack_and_assistant_message_to_aituber(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        urlopen.return_value = response

        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="電気つけて", turn_id="turn-aituber")
            client = FakeThoughtCoreClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--session-id",
                    "living_room_main",
                    "--status-dir",
                    "",
                    "--aituber-message-url",
                    "http://127.0.0.1:3000/api/messages?clientId=client-1&type=direct_send",
                    "--aituber-http-timeout-s",
                    "0.1",
                ]
            )

            run_once(args, client=client)

        payloads = [
            json.loads(call.args[0].data.decode("utf-8"))
            for call in urlopen.call_args_list
        ]
        self.assertEqual(
            [payload["messages"][0] for payload in payloads],
            ["[neutral]はいよ。", "了解です"],
        )

    def test_aituber_forward_error_redacts_message_url(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(Path(tmp) / ".cache" / "sword_voice_agent")
            forwarder = ThoughtCoreAituberForwarder(
                "https://aituber.example.test/api/messages?token=secret",
                timeout_s=0.1,
                store=store,
                turn_id="turn-1",
            )

            forwarder.record_error("connection failed")

            events = store.read_events()
            self.assertEqual(events[0]["type"], "aituber.forward_error")
            self.assertEqual(events[0]["source"], "watch_handoff_to_thought_core")
            self.assertEqual(events[0]["payload"]["message_url"], "[redacted]")
            self.assertTrue(events[0]["payload"]["message_url_present"])
            self.assertNotIn("secret", json.dumps(events, ensure_ascii=False))

    def test_handoff_signature_changes_when_content_changes(self) -> None:
        with workspace_tempdir() as tmp:
            path = Path(tmp) / "handoff.json"
            path.write_text('{"command":"a"}', encoding="utf-8")
            first = handoff_signature(path)

            path.write_text('{"command":"b"}', encoding="utf-8")
            second = handoff_signature(path)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)

    def test_resolve_handoff_path_rejects_placeholder_root(self) -> None:
        args = build_parser().parse_args(
            [
                "--ai-talk-core-root",
                "<ai_talk_core_root>",
                "--source",
                "web",
            ]
        )

        with self.assertRaises(AiTalkCoreHandoffError):
            resolve_handoff_json_path(args)

    def test_status_messages_explain_watch_and_missing_handoff(self) -> None:
        path = Path("..") / "organs" / "voice" / "ai-talk-core" / ".cache" / "codex" / "web_latest.json"

        self.assertIn("監視中", format_watch_start_message(path, skip_existing=True))
        self.assertIn("新規handoffのみ", format_watch_start_message(path, skip_existing=True))
        missing = format_missing_handoff_message(path)
        self.assertIn("handoff JSON が見つかりません", missing)
        self.assertIn("sword-thought-core-handoff", missing)

    def test_watcher_module_status_is_written(self) -> None:
        with workspace_tempdir() as tmp:
            status_dir = Path(tmp) / ".cache" / "sword_voice_agent"
            args = build_parser().parse_args(
                [
                    "--handoff-json",
                    str(Path(tmp) / "missing.json"),
                    "--status-dir",
                    str(status_dir),
                ]
            )

            write_watcher_module_status(args, "running", detail="watching handoffs")

            payload = json.loads(
                (
                    status_dir / "modules" / "thought_core_watcher.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["name"], "thought_core_watcher")
            self.assertEqual(payload["label"], "thought-core watcher")
            self.assertEqual(payload["state"], "running")
            self.assertEqual(payload["detail"], "watching handoffs")

    def test_watcher_module_status_can_be_disabled(self) -> None:
        with workspace_tempdir() as tmp:
            args = build_parser().parse_args(
                [
                    "--handoff-json",
                    str(Path(tmp) / "missing.json"),
                    "--status-dir",
                    "",
                ]
            )

            write_watcher_module_status(args, "running", detail="watching handoffs")

            self.assertFalse((Path(tmp) / "modules").exists())

    def test_watcher_module_detail_is_non_sensitive(self) -> None:
        signature = HandoffSignature(
            path="C:/Users/example/private/web_latest.json",
            size=1,
            mtime_ns=2,
            digest="abc",
        )

        self.assertEqual(
            watcher_module_detail(signature, skip_existing=True),
            "handoff present / new handoffs only",
        )
        self.assertEqual(
            watcher_module_detail(None, skip_existing=False),
            "waiting for handoff / current and new handoffs",
        )
        self.assertEqual(
            result_module_detail(
                {
                    "response": {
                        "text": "了解です",
                        "raw": {"data": {"status": "success"}},
                    }
                }
            ),
            "last result success",
        )
        self.assertEqual(
            result_module_detail(
                {"skipped": True, "skip_reason": "no_speech_placeholder"}
            ),
            "skipped no_speech_placeholder",
        )


def write_handoff(root: Path, *, command: str, turn_id: str | None = None) -> Path:
    cache_dir = root / ".cache" / "codex"
    cache_dir.mkdir(parents=True)
    payload = {
        "transcript": command,
        "command": command,
    }
    if turn_id:
        payload["turn_id"] = turn_id
    (cache_dir / "web_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return cache_dir


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
