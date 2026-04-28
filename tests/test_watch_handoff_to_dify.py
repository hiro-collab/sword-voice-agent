from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.apps.watch_handoff_to_dify import (
    build_parser,
    handoff_signature,
    run_once,
)
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
