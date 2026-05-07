from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sword_voice_agent.apps.watch_handoff_to_dify import (
    AituberSpeechForwarder,
    TtsStreamForwarder,
    build_parser,
    clean_speech_message,
    handoff_signature,
    is_suspect_short_ascii_stt,
    resolve_handoff_json_path,
    run_once,
    split_speech_chunks,
)
from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoffError
from sword_voice_agent.adapters.dify import DifyStreamEvent
from sword_voice_agent.adapters.status_store import StatusStore
from sword_voice_agent.protocol.messages import AgentResponse


class FakeDifyClient:
    def __init__(self) -> None:
        self.requests = []

    def send_chat_message(self, agent_request):
        self.requests.append(agent_request)
        return AgentResponse(
            text="Dify応答です",
            conversation_id="conv-2",
            message_id="msg-1",
            raw={"answer": "Dify応答です"},
        )

    def send_chat_message_streaming(self, agent_request, *, on_event=None):
        self.requests.append(agent_request)
        events = [
            DifyStreamEvent(
                event="message",
                answer_delta="Dify",
                elapsed_s=0.2,
                conversation_id="conv-2",
                message_id="msg-1",
                raw={
                    "event": "message",
                    "answer": "Dify",
                    "conversation_id": "conv-2",
                    "message_id": "msg-1",
                },
            ),
            DifyStreamEvent(
                event="message",
                answer_delta="応答です",
                elapsed_s=0.3,
                conversation_id="conv-2",
                message_id="msg-1",
                raw={
                    "event": "message",
                    "answer": "応答です",
                    "conversation_id": "conv-2",
                    "message_id": "msg-1",
                },
            ),
            DifyStreamEvent(
                event="message_end",
                elapsed_s=0.4,
                conversation_id="conv-2",
                message_id="msg-1",
                raw={
                    "event": "message_end",
                    "conversation_id": "conv-2",
                    "message_id": "msg-1",
                },
            ),
        ]
        for event in events:
            if on_event is not None:
                on_event(event)
        return AgentResponse(
            text="Dify応答です",
            conversation_id="conv-2",
            message_id="msg-1",
            raw={
                "event": "message_end",
                "_streaming": {
                    "event_count": 3,
                    "first_token_elapsed_s": 0.2,
                    "completed_elapsed_s": 0.4,
                },
            },
        )


class WatchHandoffToDifyTest(TestCase):
    def test_run_once_sends_and_persists_dify_result(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="今日の作業を整理して")
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                ]
            )

            result = run_once(args, client=client)

            self.assertFalse(result["skipped"])
            self.assertEqual(client.requests[0].text, "今日の作業を整理して")
            self.assertNotIn("transcript", client.requests[0].context)
            cache_dir = root / ".cache" / "codex"
            saved = json.loads(
                (cache_dir / "web_dify_latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["response"]["text"], "Dify応答です")
            self.assertEqual(
                (cache_dir / "web_dify_latest.txt").read_text(encoding="utf-8"),
                "Dify応答です",
            )
            self.assertEqual(
                (cache_dir / "web_dify_conversation_id.txt").read_text(
                    encoding="utf-8"
                ),
                "conv-2",
            )

    def test_run_once_uses_persisted_conversation_id(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            cache_dir = write_handoff(root, command="続きを考えて")
            (cache_dir / "web_dify_conversation_id.txt").write_text(
                "conv-1",
                encoding="utf-8",
            )
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                ]
            )

            run_once(args, client=client)

            self.assertEqual(client.requests[0].conversation_id, "conv-1")

    def test_run_once_can_include_transcript_context(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="続きを考えて")
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--include-transcript-context",
                ]
            )

            run_once(args, client=client)

            self.assertEqual(client.requests[0].context["transcript"], "続きを考えて")

    def test_run_once_uses_latest_turn_id_for_status_only(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="続きを考えて")
            status_dir = root / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            (status_dir / "latest_voice_turn.json").write_text(
                json.dumps(
                    {
                        "type": "latest_voice_turn",
                        "timestamp": 1.0,
                        "turn_id": "turn-1",
                        "voice_control_command": {"turn_id": "turn-1"},
                    }
                ),
                encoding="utf-8",
            )
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--status-dir",
                    str(status_dir),
                ]
            )

            run_once(args, client=client)

            self.assertNotIn("turn_id", client.requests[0].context)
            latest = json.loads(
                (status_dir / "latest_dify_response.json").read_text(encoding="utf-8")
            )
            event = json.loads((status_dir / "events.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(latest["turn_id"], "turn-1")
            self.assertEqual(event["turn_id"], "turn-1")

    def test_run_once_streaming_writes_first_token_and_done_events(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="続きを考えて")
            status_dir = root / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--status-dir",
                    str(status_dir),
                    "--response-mode",
                    "streaming",
                ]
            )

            result = run_once(args, client=client)

            self.assertEqual(result["response_mode"], "streaming")
            events = [
                json.loads(line)
                for line in (status_dir / "events.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(
                [event["type"] for event in events],
                ["dify.first_token", "dify.done", "dify.response"],
            )
            self.assertEqual(events[0]["payload"]["answer_delta"], "[redacted]")
            self.assertEqual(events[0]["payload"]["elapsed_s"], 0.2)
            self.assertEqual(events[0]["payload"]["message_id"], "[redacted]")
            self.assertTrue(events[0]["payload"]["message_id_present"])
            self.assertNotIn("msg-1", json.dumps(events, ensure_ascii=False))
            self.assertNotIn("conv-2", json.dumps(events, ensure_ascii=False))

    def test_tts_forward_error_redacts_chunk_url_in_status_event(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(Path(tmp) / ".cache" / "sword_voice_agent")
            forwarder = TtsStreamForwarder(
                "https://tts.example.test/api/tts/chunk?token=secret",
                timeout_s=0.1,
                store=store,
                turn_id="turn-1",
            )

            forwarder.record_error("connection failed")

            events = store.read_events()
            self.assertEqual(events[0]["type"], "tts.forward_error")
            self.assertEqual(events[0]["payload"]["chunk_url"], "[redacted]")
            self.assertTrue(events[0]["payload"]["chunk_url_present"])
            self.assertNotIn("secret", json.dumps(events, ensure_ascii=False))

    @patch("sword_voice_agent.apps.watch_handoff_to_dify.request.urlopen")
    def test_run_once_streaming_forwards_tts_chunks(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        urlopen.return_value = response

        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="続きを考えて")
            status_dir = root / ".cache" / "sword_voice_agent"
            status_dir.mkdir(parents=True)
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--status-dir",
                    str(status_dir),
                    "--response-mode",
                    "streaming",
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
        self.assertEqual([payload.get("delta") for payload in payloads[:2]], ["Dify", "応答です"])
        self.assertEqual(payloads[-1]["event"], "message_end")
        self.assertTrue(payloads[-1]["final"])

    def test_split_speech_chunks_keeps_partial_until_sentence_end(self) -> None:
        chunks, remainder = split_speech_chunks(
            "[relaxed]ふん",
            final=False,
            max_chars=80,
        )

        self.assertEqual(chunks, [])
        self.assertEqual(remainder, "[relaxed]ふん")

        chunks, remainder = split_speech_chunks(
            "[relaxed]ふんふん。[happy]よし、完了だぜ。",
            final=False,
            max_chars=80,
        )

        self.assertEqual(chunks, ["[relaxed]ふんふん。", "[happy]よし、完了だぜ。"])
        self.assertEqual(remainder, "")

    def test_clean_speech_message_removes_internal_speech_markers(self) -> None:
        self.assertEqual(
            clean_speech_message("[[SPEECH:ACK]][relaxed]ふんふん。"),
            "[relaxed]ふんふん。",
        )
        self.assertEqual(clean_speech_message("[neutral]"), "")

    def test_clean_speech_message_normalizes_bare_motion_tags(self) -> None:
        self.assertEqual(
            clean_speech_message(
                "[neutral]おっ、またお辞儀か！[bow]はい、どうぞ！"
            ),
            "[neutral]おっ、またお辞儀か！[motion:bow]はい、どうぞ！",
        )
        self.assertEqual(
            clean_speech_message("[motion:Bow]どうぞ。"),
            "[motion:bow]どうぞ。",
        )
        self.assertEqual(clean_speech_message("[bow]"), "")

    @patch("sword_voice_agent.apps.watch_handoff_to_dify.request.urlopen")
    def test_aituber_forwarder_posts_sentence_sized_direct_send_messages(
        self,
        urlopen: MagicMock,
    ) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        urlopen.return_value = response
        forwarder = AituberSpeechForwarder(
            "http://127.0.0.1:3000/api/messages?clientId=client-1&type=direct_send",
            timeout_s=0.1,
        )

        forwarder(
            DifyStreamEvent(
                event="message",
                answer_delta="[relaxed]ふん",
                message_id="msg-1",
            )
        )
        self.assertEqual(urlopen.call_count, 0)

        forwarder(
            DifyStreamEvent(
                event="message",
                answer_delta="ふん。[happy]よし、完了だぜ。",
                message_id="msg-1",
            )
        )

        payloads = [
            json.loads(call.args[0].data.decode("utf-8"))
            for call in urlopen.call_args_list
        ]
        self.assertEqual(
            [payload["messages"][0] for payload in payloads],
            ["[relaxed]ふんふん。", "[happy]よし、完了だぜ。"],
        )

    def test_aituber_forward_error_redacts_message_url(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(Path(tmp) / ".cache" / "sword_voice_agent")
            forwarder = AituberSpeechForwarder(
                "https://aituber.example.test/api/messages?token=secret",
                timeout_s=0.1,
                store=store,
                turn_id="turn-1",
            )

            forwarder.record_error("connection failed")

            events = store.read_events()
            self.assertEqual(events[0]["type"], "aituber.forward_error")
            self.assertEqual(events[0]["payload"]["message_url"], "[redacted]")
            self.assertTrue(events[0]["payload"]["message_url_present"])
            self.assertNotIn("secret", json.dumps(events, ensure_ascii=False))

    def test_run_once_skips_no_speech_placeholder_by_default(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="音声を認識できませんでした。")
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                ]
            )

            result = run_once(args, client=client)

            self.assertTrue(result["skipped"])
            self.assertEqual(result["skip_reason"], "no_speech_placeholder")
            self.assertEqual(client.requests, [])

    def test_run_once_skips_suspicious_short_ascii_when_enabled(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            write_handoff(root, command="inverse")
            client = FakeDifyClient()
            args = build_parser().parse_args(
                [
                    "--ai-talk-core-root",
                    str(root),
                    "--once",
                    "--skip-short-ascii",
                ]
            )

            result = run_once(args, client=client)

            self.assertTrue(result["skipped"])
            self.assertEqual(result["skip_reason"], "short_ascii_stt_suspect")
            self.assertEqual(client.requests, [])

    def test_short_ascii_detection_ignores_japanese_and_multi_word_text(self) -> None:
        self.assertTrue(is_suspect_short_ascii_stt("inverse"))
        self.assertTrue(is_suspect_short_ascii_stt("inverse."))
        self.assertFalse(is_suspect_short_ascii_stt("今日はいい天気ですね"))
        self.assertFalse(is_suspect_short_ascii_stt("open settings"))

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

    def test_handoff_signature_wraps_invalid_windows_path_error(self) -> None:
        with self.assertRaises(AiTalkCoreHandoffError):
            handoff_signature("<ai_talk_core_root>\\.cache\\codex\\web_latest.json")


def write_handoff(root: Path, *, command: str) -> Path:
    cache_dir = root / ".cache" / "codex"
    cache_dir.mkdir(parents=True)
    (cache_dir / "web_latest.json").write_text(
        json.dumps(
            {
                "transcript": command,
                "command": command,
            },
            ensure_ascii=False,
        ),
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
