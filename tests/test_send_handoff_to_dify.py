from pathlib import Path
from unittest import TestCase

from sword_voice_agent.apps.send_handoff_to_dify import build_parser, run


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class SendHandoffToDifyTest(TestCase):
    def test_dry_run_builds_request_without_dify_call(self) -> None:
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--dry-run",
                "--context",
                "mode=test",
            ]
        )

        result = run(args)

        self.assertIsNone(result["response"])
        request = result["request"]
        self.assertEqual(request["text"], "冷蔵庫の材料から買い物リストを提案する")
        self.assertEqual(request["context"]["mode"], "test")
        self.assertEqual(request["context"]["trigger"], "sword_sign")
