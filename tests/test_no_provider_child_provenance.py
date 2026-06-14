from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import request

from sword_voice_agent.adapters.no_provider_child_provenance import (
    build_no_provider_child_provenance_diagnostics,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.provenance_diagnostics import (  # noqa: E402
    build_child_provenance_diagnostics,
)
from thought_core.server import create_server  # noqa: E402


class NoProviderChildProvenanceTests(unittest.TestCase):
    def test_launcher_helper_reports_env_import_override_without_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "sword-voice-agent"
            profile_dir = control_plane / "ops" / "manifests" / "profiles"
            profile_dir.mkdir(parents=True)
            (profile_dir / "thought-core-v0.json").write_text(
                json.dumps(
                    {
                        "profile": "thought-core-v0",
                        "services": [
                            "home_assistant_bridge",
                            "thought_core_api",
                            "thought_core_watcher",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            thought_core = control_plane / "services" / "thought-core"
            thought_core.mkdir(parents=True)
            (thought_core / ".env").write_text(
                "\n".join(
                    [
                        "THOUGHT_CORE_LLM_ENABLED=enabled",
                        "THOUGHT_CORE_LLM_API_KEY=mock-private-key",
                    ]
                ),
                encoding="utf-8",
            )
            source_root = thought_core / "src" / "thought_core"
            source_root.mkdir(parents=True)
            (source_root / "input_understanding.py").write_text(
                "INPUT = 'source only'\n",
                encoding="utf-8",
            )
            (source_root / "loop.py").write_text(
                "LOOP = 'source only'\n",
                encoding="utf-8",
            )

            payload = build_no_provider_child_provenance_diagnostics(
                agent_os_root=root,
                selected_profile="thought-core-v0",
                process_env={
                    "THOUGHT_CORE_LLM_ENABLED": "0",
                    "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
                },
                listener_classes={"thought_core_api": "none"},
                top_level_text_present_class="present_redacted",
                payload_marker_class="happy_marker_plus_move_marker",
                context_ref_payload_class="happy_expression_motion_request",
            )

        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "provider_capable_enabled_after_env_import",
        )
        self.assertEqual(
            payload["thought_core_action_llm_enabled_class"],
            "action_llm_disabled_literal",
        )
        self.assertEqual(
            payload["direct_dify_exclusion_class"],
            "profile_delegate_excludes_dify_stack_and_watcher",
        )
        self.assertEqual(payload["mapping_input_source"], "top_level_text")
        self.assertEqual(
            payload["marker_class_consistency"],
            "consistent_marker_and_context_label",
        )
        self.assertEqual(payload["standard_diagnostics_surface_class"], "partial")
        self.assertEqual(
            payload["diagnostics_status_writer_surface"],
            "missing_status_surface",
        )
        self.assertFalse(payload["runtime_import_provenance_available"])
        self.assertEqual(
            payload["running_child_input_understanding_import_provenance"][
                "collection_point"
            ],
            "thought_core_child_diagnostics_endpoint",
        )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_launcher_helper_force_no_provider_wins_after_env_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "sword-voice-agent"
            profile_dir = control_plane / "ops" / "manifests" / "profiles"
            profile_dir.mkdir(parents=True)
            (profile_dir / "thought-core-v0.json").write_text(
                json.dumps(
                    {
                        "profile": "thought-core-v0",
                        "services": [
                            "thought_core_api",
                            "thought_core_watcher",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            thought_core = control_plane / "services" / "thought-core"
            thought_core.mkdir(parents=True)
            (thought_core / ".env").write_text(
                "\n".join(
                    [
                        "THOUGHT_CORE_LLM_ENABLED=enabled",
                        "THOUGHT_CORE_LLM_API_KEY=mock-private-key",
                        "OPENAI_API_KEY=mock-private-key",
                    ]
                ),
                encoding="utf-8",
            )
            source_root = thought_core / "src" / "thought_core"
            source_root.mkdir(parents=True)
            (source_root / "input_understanding.py").write_text(
                "INPUT = 'source only'\n",
                encoding="utf-8",
            )
            (source_root / "loop.py").write_text(
                "LOOP = 'source only'\n",
                encoding="utf-8",
            )

            payload = build_no_provider_child_provenance_diagnostics(
                agent_os_root=root,
                selected_profile="thought-core-v0",
                process_env={"THOUGHT_CORE_FORCE_NO_PROVIDER": "1"},
                payload_marker_class="happy_marker_plus_move_marker",
                context_ref_payload_class="happy_expression_motion_request",
            )

        self.assertEqual(
            payload["thought_core_force_no_provider_class"],
            "enabled_literal",
        )
        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "no_provider_or_fallback_only_bound_by_final_child_env_class",
        )
        self.assertEqual(payload["thought_core_llm_enabled_class"], "disabled_literal")
        self.assertEqual(
            payload["thought_core_action_llm_enabled_class"],
            "action_llm_disabled_literal",
        )
        self.assertEqual(
            payload["provider_config_presence_class"],
            "provider_config_absent_or_empty",
        )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_thought_core_child_diagnostics_reports_running_import_hashes(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THOUGHT_CORE_LLM_ENABLED": "0",
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
                "OPENAI_API_KEY": "private-test-key",
            },
            clear=True,
        ):
            payload = build_child_provenance_diagnostics(
                selected_profile="thought-core-v0",
                ops_profile="thought-core-v0",
                top_level_text_present_class="present_redacted",
                top_level_text_marker_class="happy_marker_plus_move_marker",
                context_ref_payload_class="happy_expression_motion_request",
            )

        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "no_provider_or_fallback_only_bound_by_running_child_env_class",
        )
        self.assertEqual(
            payload["provider_config_presence_class"],
            "provider_config_present_nonempty_redacted",
        )
        self.assertEqual(
            payload["thought_core_force_no_provider_class"],
            "absent",
        )
        self.assertTrue(payload["runtime_import_provenance_available"])
        self.assertTrue(
            payload["running_child_input_understanding_import_provenance"]["available"]
        )
        self.assertEqual(
            payload["running_child_loop_import_provenance"]["collection_point"],
            "running_thought_core_child_process",
        )
        self.assertEqual(payload["mapping_input_source"], "top_level_text")
        self.assertEqual(
            payload["marker_class_consistency"],
            "consistent_marker_and_context_label",
        )
        self.assertEqual(payload["standard_diagnostics_surface_class"], "partial")
        self.assertEqual(
            payload["diagnostics_reader_surface"],
            "direct_child_endpoint_available_normal_status_reader_missing",
        )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_thought_core_server_exposes_no_turn_diagnostics_endpoint(self) -> None:
        env = {
            "THOUGHT_CORE_LLM_ENABLED": "0",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
        }
        with patch.dict("os.environ", env, clear=False):
            server = create_server("127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                url = (
                    f"http://127.0.0.1:{port}"
                    "/diagnostics/no-provider-child-provenance"
                    "?selected_profile=thought-core-v0"
                    "&ops_profile=thought-core-v0"
                    "&top_level_text_present_class=present_redacted"
                    "&top_level_text_marker_class=happy_marker_plus_move_marker"
                    "&context_ref_payload_class=happy_expression_motion_request"
                )
                with request.urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(
                    payload["diagnostics_schema_version"],
                    "thought_core.no_provider_child_provenance.v0",
                )
                self.assertEqual(
                    payload["child_process_no_provider_binding_class"],
                    "no_provider_or_fallback_only_bound_by_running_child_env_class",
                )
                self.assertTrue(payload["runtime_import_provenance_available"])
                self.assertEqual(
                    payload["standard_diagnostics_surface_class"],
                    "partial",
                )
                self.assertFalse(payload["one_off_artifact_only"])
                self.assertNotIn("events", payload)
                self.assert_json_string_values_are_publication_safe(payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def assert_json_string_values_are_publication_safe(self, payload: object) -> None:
        for value in iter_string_values(payload):
            self.assertNotIn("http://", value)
            self.assertNotIn("https://", value)
            self.assertNotIn(":\\", value)
            self.assertNotIn("private-test-key", value)
            self.assertNotIn("mock-private-key", value)


def iter_string_values(value: object):
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_string_values(child)
    elif isinstance(value, str):
        yield value


if __name__ == "__main__":
    unittest.main()
