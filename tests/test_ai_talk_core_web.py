from unittest import TestCase
from argparse import Namespace
from pathlib import Path
import shutil
from uuid import uuid4
from unittest.mock import Mock, patch

from sword_voice_agent.apps.ai_talk_core_web import (
    AiTalkCoreWebDefaults,
    apply_ai_talk_core_web_defaults,
    build_native_startup_query,
    detect_native_startup_profile,
    run,
    set_checkbox_checked,
    should_use_native_profile,
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

    def test_detects_legacy_native_dify_profile(self) -> None:
        with workspace_tempdir() as tmp:
            app_js = tmp / "src" / "web" / "static" / "app.js"
            app_js.parent.mkdir(parents=True)
            app_js.write_text(
                "const OPTION_PROFILES = { dify: { record_gate_auto: '1' } };",
                encoding="utf-8",
            )

            self.assertEqual(detect_native_startup_profile(tmp), "dify")

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


class workspace_tempdir:
    def __enter__(self) -> Path:
        self.path = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
        self.path.mkdir(parents=True)
        return self.path

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
