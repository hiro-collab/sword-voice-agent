import os
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.readiness import (  # noqa: E402
    READINESS_ID,
    run_conscious_readiness_probe,
)


class ThoughtCoreReadinessTest(TestCase):
    def test_conscious_readiness_probe_uses_deterministic_turn(self) -> None:
        result = run_conscious_readiness_probe()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["startup_stage"], "conscious_ready")
        self.assertEqual(result["readiness_id"], READINESS_ID)
        self.assertFalse(result["external_api_required"])
        self.assertFalse(result["used_llm"])
        self.assertIn("assistant.message", result["observed_events"])
        self.assertIn("turn.completed", result["observed_events"])
        self.assertEqual(result["next_stage"], "full_conscious_ready")

    def test_conscious_readiness_ignores_configured_external_llm_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_API_KEY": "test-key-that-must-not-be-used",
                "THOUGHT_CORE_LLM_BASE_URL": "https://example.invalid/v1",
            },
            clear=False,
        ):
            result = run_conscious_readiness_probe()

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["used_llm"])
