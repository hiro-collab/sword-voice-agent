from pathlib import Path
from unittest import TestCase

from sword_voice_agent.adapters.auth import AuthError
from sword_voice_agent.apps.console_server import build_parser, run_server


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ConsoleServerTest(TestCase):
    def test_requires_auth_for_non_loopback_bind(self) -> None:
        args = build_parser().parse_args(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "0",
                "--ai-talk-core-root",
                str(FIXTURES / "ai_talk_core_root"),
            ]
        )

        with self.assertRaises(AuthError):
            run_server(args)
