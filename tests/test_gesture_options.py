import os
from unittest import TestCase

from sword_voice_agent.apps.gesture_options import env_float


class GestureOptionsTest(TestCase):
    def tearDown(self) -> None:
        os.environ.pop("SWORD_TEST_FLOAT", None)

    def test_env_float_uses_default_for_missing_or_blank_value(self) -> None:
        self.assertEqual(env_float("SWORD_TEST_FLOAT", 0.8), 0.8)

        os.environ["SWORD_TEST_FLOAT"] = " "

        self.assertEqual(env_float("SWORD_TEST_FLOAT", 0.8), 0.8)

    def test_env_float_parses_number(self) -> None:
        os.environ["SWORD_TEST_FLOAT"] = "0.15"

        self.assertEqual(env_float("SWORD_TEST_FLOAT", 0.8), 0.15)

    def test_env_float_rejects_invalid_number(self) -> None:
        os.environ["SWORD_TEST_FLOAT"] = "fast"

        with self.assertRaises(ValueError):
            env_float("SWORD_TEST_FLOAT", 0.8)

    def test_env_float_rejects_non_finite_number(self) -> None:
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                os.environ["SWORD_TEST_FLOAT"] = value
                with self.assertRaises(ValueError):
                    env_float("SWORD_TEST_FLOAT", 0.8)
