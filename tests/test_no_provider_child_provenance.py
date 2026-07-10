from __future__ import annotations

import json
import os
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
SHARED_VECTOR_ENV = "SWORD_M4_SHARED_VECTOR_PATH"
MAX_SHARED_VECTOR_BYTES = 128 * 1024
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.provenance_diagnostics import (  # noqa: E402
    build_child_provenance_diagnostics,
)
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.server import (  # noqa: E402
    _decorate_assistant_event_with_conversation_attempt_ref,
    _is_opaque_conversation_attempt_ref,
    create_server,
)


def load_shared_attempt_vectors() -> dict[str, object] | None:
    configured = os.environ.get(SHARED_VECTOR_ENV, "").strip()
    if not configured:
        return None
    path = Path(configured).resolve(strict=True)
    if len(str(path)) > 4096 or path.suffix != ".json" or not path.is_file():
        raise AssertionError("shared vector path must be a bounded JSON file")
    if not 0 < path.stat().st_size <= MAX_SHARED_VECTOR_BYTES:
        raise AssertionError("shared vector file size is out of bounds")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("shared vector root must be an object")
    if payload.get("schema_version") != "m4_cross_repo_attempt_vectors.v0":
        raise AssertionError("shared vector schema_version is invalid")
    required = {
        "canonical_conversation_attempt_ref",
        "invalid_conversation_attempt_refs",
        "accepted_user_speech_candidate",
        "private_turn",
        "assistant_event",
    }
    if not required.issubset(payload):
        raise AssertionError("shared vector shape is incomplete")
    return payload


class NoProviderChildProvenanceTests(unittest.TestCase):
    def test_shared_vectors_decorate_only_the_canonical_ref_when_configured(self) -> None:
        vectors = load_shared_attempt_vectors()
        if vectors is None:
            return
        canonical_ref = vectors["canonical_conversation_attempt_ref"]
        invalid_refs = vectors["invalid_conversation_attempt_refs"]
        candidate = vectors["accepted_user_speech_candidate"]
        private_turn = vectors["private_turn"]
        assistant_event = vectors["assistant_event"]
        self.assertIsInstance(canonical_ref, str)
        self.assertIsInstance(invalid_refs, dict)
        self.assertEqual(
            set(invalid_refs),
            {"colonless", "uppercase", "wrong_prefix", "short", "long", "unsafe", "whitespace"},
        )
        self.assertIsInstance(candidate, dict)
        self.assertIsInstance(private_turn, dict)
        self.assertIsNone(private_turn.get("text"))
        self.assertEqual(
            private_turn.get("text_representation"),
            "legacy_no_speech_placeholder_not_publicly_representable_in_parent_fixture",
        )
        self.assertIsInstance(assistant_event, dict)
        self.assertEqual(
            assistant_event.get("expected_conversation_attempt_ref"), canonical_ref
        )
        candidate_for_core = dict(candidate)
        candidate_for_core["redaction_guards"] = {
            key: False
            for key in (
                "raw_audio_included",
                "raw_media_included",
                "raw_transcript_included",
                "raw_recognized_text_included",
                "private_path_included",
                "provider_payload_included",
                "browser_storage_included",
                "token_or_secret_included",
                "home_control_action_authority_included",
            )
        }

        class VectorLoop:
            def run_dicts(self, turn, *, event_sink=None):
                injected = dict(assistant_event)
                injected["event_id"] = "evt_shared_vector_message"
                injected["data"] = dict(assistant_event["data"])
                events = [
                    {
                        "event_id": "evt_shared_vector_delta",
                        "type": "assistant.speech_delta",
                        "data": {
                            "delta": "synthetic delta",
                            "conversation_attempt_ref": assistant_event["data"][
                                "conversation_attempt_ref"
                            ],
                        },
                    },
                    injected,
                    {
                        "event_id": "evt_shared_vector_completed",
                        "type": "turn.completed",
                        "data": {"status": "success"},
                    },
                ]
                if event_sink is not None:
                    for event in events:
                        event_sink(event)
                return events

        def request_events(port: int, ref: str, suffix: str) -> list[dict[str, object]]:
            private_payload = dict(private_turn)
            private_payload["text"] = "synthetic private test turn"
            private_payload["context_refs"] = {"conversation_attempt_ref": ref}
            body = json.dumps(
                {
                    "accepted_user_speech_candidate": candidate_for_core,
                    "private_turn": private_payload,
                }
            ).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}{suffix}",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8")
            if suffix == "/turn":
                return json.loads(body)["events"]
            return [
                json.loads(line[6:])
                for line in body.splitlines()
                if line.startswith("data: ")
            ]

        server = create_server("127.0.0.1", 0, thought_loop=VectorLoop())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            for suffix in ("/turn", "/turn/stream"):
                with self.subTest(case="canonical", path=suffix):
                    events = request_events(port, canonical_ref, suffix)
                    for event in events:
                        if event["type"].startswith("assistant."):
                            self.assertEqual(
                                event["data"]["conversation_attempt_ref"], canonical_ref
                            )
                        else:
                            self.assertNotIn("conversation_attempt_ref", event["data"])
            for name, invalid_ref in invalid_refs.items():
                for suffix in ("/turn", "/turn/stream"):
                    with self.subTest(case=name, path=suffix):
                        events = request_events(port, invalid_ref, suffix)
                        for event in events:
                            self.assertNotIn(
                                "conversation_attempt_ref",
                                event["data"],
                            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_materializes_accepted_candidate_once_for_normal_and_stream_turns(
        self,
    ) -> None:
        class RecordingLoop:
            def __init__(self) -> None:
                self.turns: list[TurnInput] = []

            def run_dicts(self, turn, *, event_sink=None):
                self.turns.append(turn)
                events = [
                    {
                        "event_id": "evt_candidate_delta",
                        "type": "assistant.speech_delta",
                        "data": {
                            "delta": "synthetic assistant delta",
                            "conversation_attempt_ref": "injected:not_authoritative",
                        },
                    },
                    {
                        "event_id": "evt_candidate_message",
                        "type": "assistant.message",
                        "data": {
                            "speech": "synthetic assistant response",
                            "conversation_attempt_ref": "injected:not_authoritative",
                        },
                    },
                    {
                        "event_id": "evt_candidate_completed",
                        "type": "turn.completed",
                        "data": {"status": "success"},
                    },
                ]
                if event_sink is not None:
                    for event in events:
                        event_sink(event)
                return events

        candidate = json.loads(
            (
                REPO_ROOT.parents[1]
                / "contracts"
                / "accepted_user_speech_candidate_input_gate"
                / "examples"
                / "source_static_accepted_private_user_speech_candidate.example.json"
            ).read_text(encoding="utf-8")
        )
        payload = {
            "accepted_user_speech_candidate": candidate,
            "private_turn": {
                "text": "synthetic private turn text",
                "turn_id": "turn_candidate_server_001",
                "session_id": "session_candidate_server_001",
                "locale": "ja-JP",
                "context_refs": {
                    "conversation_attempt_ref": (
                        "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
                    ),
                },
            },
        }
        loop = RecordingLoop()
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            rendered_outputs = []
            for suffix in ("/turn", "/turn/stream"):
                with self.subTest(path=suffix):
                    body = json.dumps(payload).encode("utf-8")
                    req = request.Request(
                        f"http://127.0.0.1:{port}{suffix}",
                        data=body,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    with request.urlopen(req, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        body = response.read().decode("utf-8")
                    if suffix == "/turn":
                        events = json.loads(body)["events"]
                    else:
                        events = [
                            json.loads(line[6:])
                            for line in body.splitlines()
                            if line.startswith("data: ")
                        ]
                    rendered_outputs.append(json.dumps(events, ensure_ascii=False))
                    for event in events:
                        if event["type"].startswith("assistant."):
                            self.assertEqual(
                                event["data"]["conversation_attempt_ref"],
                                "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef",
                            )
                        else:
                            self.assertNotIn(
                                "conversation_attempt_ref",
                                event["data"],
                            )

            self.assertEqual(len(loop.turns), 2)
            for turn in loop.turns:
                self.assertIsInstance(turn, TurnInput)
                self.assertEqual(turn.turn_id, "turn_candidate_server_001")
                self.assertEqual(
                    turn.context_refs["accepted_user_speech_candidate_ref"],
                    candidate["candidate_id"],
                )
                self.assertEqual(
                    turn.context_refs["conversation_attempt_ref"],
                    "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef",
                )
            for output in rendered_outputs:
                self.assertNotIn("synthetic private turn text", output)
                self.assertNotIn("accepted_user_speech_candidate", output)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_conversation_attempt_ref_decorator_ignores_missing_invalid_and_plain_turns(
        self,
    ) -> None:
        event = {"type": "assistant.message", "data": {"speech": "response"}}
        non_assistant_event = {"type": "turn.completed", "data": {"status": "success"}}

        missing = TurnInput(
            text="private text",
            turn_id="turn_missing_ref",
            session_id="session_missing_ref",
        )
        invalid = TurnInput(
            text="private text",
            turn_id="turn_invalid_ref",
            session_id="session_invalid_ref",
            context_refs={"conversation_attempt_ref": "C:/private/path.wav"},
        )

        for turn in (missing, invalid, {"text": "ordinary turn"}):
            with self.subTest(turn=type(turn).__name__):
                injected_event = {
                    "type": "assistant.message",
                    "data": {
                        "speech": "response",
                        "conversation_attempt_ref": "injected:not_authoritative",
                    },
                }
                self.assertNotIn(
                    "conversation_attempt_ref",
                    _decorate_assistant_event_with_conversation_attempt_ref(
                        injected_event,
                        turn,
                    )[
                        "data"
                    ],
                )
        self.assertNotIn(
            "conversation_attempt_ref",
            _decorate_assistant_event_with_conversation_attempt_ref(
                non_assistant_event,
                TurnInput(
                    text="private text",
                    turn_id="turn_non_assistant",
                    session_id="session_non_assistant",
                    context_refs={
                        "conversation_attempt_ref": (
                            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
                        )
                    },
                ),
            )["data"],
        )

    def test_conversation_attempt_ref_grammar_is_canonical_and_bounded(self) -> None:
        valid = "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
        invalid = (
            "m4.prepared_sample_attempt0123456789abcdef0123456789abcdef",
            "m4.prepared_sample_attempt:0123456789ABCDEF0123456789abcdef",
            "m4.other_attempt:0123456789abcdef0123456789abcdef",
            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcde",
            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef/",
        )

        self.assertTrue(_is_opaque_conversation_attempt_ref(valid))
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(_is_opaque_conversation_attempt_ref(value))
    def test_server_rejects_candidate_envelope_without_private_turn(self) -> None:
        candidate = json.loads(
            (
                REPO_ROOT.parents[1]
                / "contracts"
                / "accepted_user_speech_candidate_input_gate"
                / "examples"
                / "source_static_accepted_private_user_speech_candidate.example.json"
            ).read_text(encoding="utf-8")
        )
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            req = request.Request(
                f"http://127.0.0.1:{port}/turn",
                data=json.dumps({"accepted_user_speech_candidate": candidate}).encode(
                    "utf-8"
                ),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(request.HTTPError) as caught:
                request.urlopen(req, timeout=5)
            self.assertEqual(caught.exception.code, 400)
            self.assertIn(
                "private_turn must be an object",
                caught.exception.read().decode("utf-8"),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_launcher_helper_reports_env_import_override_without_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "core"
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
            payload["external_provider_route_class"],
            "thought_core_route_selected",
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
            control_plane = root / "control-plane" / "core"
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
        for key in (
            "THOUGHT_CORE_CODEX_CLI_PATH",
            "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
            "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
        ):
            self.assertEqual(
                payload["provider_config_key_classes"][key],
                "empty",
            )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_launcher_helper_preserves_codex_cli_child_env_classes_without_raw_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "core"
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
                        "THOUGHT_CORE_LLM_ENABLED=1",
                        "THOUGHT_CORE_LLM_PROVIDER=codex-cli",
                        "THOUGHT_CORE_CODEX_CLI_MODE=operate",
                        "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION=0.142.0",
                        "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY=warn",
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
                    "THOUGHT_CORE_CODEX_CLI_PATH": r"C:\private\codex.cmd",
                    "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT": r"C:\private\sword-agent-os",
                    "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT": "xhigh",
                },
            )

        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "provider_capable_enabled_after_env_import",
        )
        self.assertEqual(
            payload["provider_config_presence_class"],
            "provider_config_present_nonempty_redacted",
        )
        for key in (
            "THOUGHT_CORE_LLM_PROVIDER",
            "THOUGHT_CORE_CODEX_CLI_PATH",
            "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT",
            "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT",
            "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
            "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
        ):
            self.assertEqual(
                payload["provider_config_key_classes"][key],
                "present_nonempty_redacted",
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(r"C:\private", serialized)
        self.assertNotIn("0.142.0", serialized)
        self.assertNotIn("xhigh", serialized)
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
