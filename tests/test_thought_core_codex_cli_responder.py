import json
import subprocess
import sys
import threading
from pathlib import Path
from subprocess import TimeoutExpired
from unittest import TestCase
from unittest.mock import patch
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
STACK_START = REPO_ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.responders import (  # noqa: E402
    CodexCliChatResponder,
    EnvironmentTurnResponder,
    OpenAICompatibleChatResponder,
    _codex_config_overrides_from_env,
    _default_codex_cwd,
    _run_codex_command,
)
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.server import create_server  # noqa: E402


class ThoughtCoreCodexCliResponderTests(TestCase):
    def test_default_codex_cwd_is_system_root_not_nested_control_plane(self) -> None:
        self.assertEqual(_default_codex_cwd(), REPO_ROOT.parents[1])
        self.assertTrue((_default_codex_cwd() / "control-plane").is_dir())
        self.assertTrue((_default_codex_cwd() / "AGENTS.md").is_file())

    def test_stack_start_passes_codex_cli_mode_keys_to_thought_core(self) -> None:
        stack_start = STACK_START.read_text(encoding="utf-8")

        for key in (
            "THOUGHT_CORE_LLM_PROVIDER",
            "THOUGHT_CORE_CODEX_CLI_MODE",
            "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT",
            "THOUGHT_CORE_CODEX_CLI_MODEL",
            "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT",
            "THOUGHT_CORE_CODEX_CLI_CONFIG_OVERRIDES",
            "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
            "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
            "THOUGHT_CORE_CODEX_CLI_VERSION_TIMEOUT_S",
            "THOUGHT_CORE_CODEX_CLI_SANDBOX",
            "THOUGHT_CORE_CODEX_CLI_APPROVAL",
            "THOUGHT_CORE_CODEX_CLI_EPHEMERAL",
            "CODEX_CLI_PATH",
        ):
            self.assertIn(f'"{key}"', stack_start)

    def test_env_selects_codex_cli_as_thought_core_internal_responder(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "codex-cli",
                "THOUGHT_CORE_CODEX_CLI_PATH": "codex",
                "THOUGHT_CORE_CODEX_CLI_CWD": str(REPO_ROOT),
                "THOUGHT_CORE_CODEX_CLI_MODEL": "gpt-5.5",
                "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT": "xhigh",
                "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION": "0.142.0",
            },
            clear=True,
        ):
            responder = EnvironmentTurnResponder.from_env()

        self.assertIsInstance(responder.primary, CodexCliChatResponder)
        self.assertEqual(responder.primary.adapter_kind, "codex_cli_operator")
        self.assertEqual(responder.primary.provider, "codex-cli")
        self.assertEqual(responder.primary.mode, "operate")
        self.assertEqual(responder.primary.sandbox, "workspace-write")
        self.assertEqual(responder.primary.model, "gpt-5.5")
        self.assertIn('model_reasoning_effort="xhigh"', responder.primary.config_overrides)
        self.assertEqual(responder.primary.expected_version, "0.142.0")
        self.assertEqual(responder.primary.version_policy, "warn")

    def test_env_without_codex_provider_preserves_openai_compatible_mode(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:9999/v1",
                "THOUGHT_CORE_LLM_API_KEY": "",
            },
            clear=True,
        ):
            responder = EnvironmentTurnResponder.from_env()

        self.assertIsInstance(responder.primary, OpenAICompatibleChatResponder)
        self.assertEqual(responder.primary.adapter_kind, "openai_compatible_chat")

    def test_codex_cli_config_overrides_keep_reasoning_allowlist_only(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT": "high",
                "THOUGHT_CORE_CODEX_CLI_CONFIG_OVERRIDES": (
                    "model_verbosity=low;"
                    "sandbox_mode=danger-full-access;"
                    "approval_policy=never"
                ),
            },
            clear=True,
        ):
            overrides = _codex_config_overrides_from_env()

        self.assertEqual(
            overrides,
            [
                'model_reasoning_effort="high"',
                'model_verbosity="low"',
            ],
        )

    def test_codex_cli_operate_mode_uses_workspace_root_and_can_write_workspace(
        self,
    ) -> None:
        seen_args = []

        def fake_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            seen_args.append(list(args))
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="codex-cli 0.142.0\n",
                    stderr="",
                )
            self.assertIn("User turn:", prompt)
            self.assertIn("今の部屋の様子", prompt)
            self.assertIn("self-operating development agent", prompt)
            self.assertIn("AGENTS.md", prompt)
            output_path = Path(args[args.index("--output-last-message") + 1])
            output_path.write_text(
                "環境を見ながら、落ち着いて進めます。",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        responder = CodexCliChatResponder(
            command="codex",
            model="gpt-5.5",
            cwd=REPO_ROOT.parents[1],
            timeout_s=15,
            config_overrides=('model_reasoning_effort="xhigh"',),
            expected_version="codex-cli 0.142.0",
            runner=fake_runner,
        )

        result = responder.respond(
            TurnInput(
                text="今の部屋の様子を見て話して。",
                turn_id="turn_codex_cli_test",
                session_id="codex_cli_test",
            ),
            response_context={
                "response_goal": "environment_grounded_reply",
                "required_facts": ["environment_state_observation_required"],
            },
        )

        self.assertEqual(seen_args[0], ["codex", "--version"])
        args = seen_args[1]
        self.assertEqual(args[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", args)
        self.assertIn("--skip-git-repo-check", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(args[args.index("--cd") + 1], str(REPO_ROOT.parents[1]))
        config_values = [
            args[index + 1] for index, value in enumerate(args) if value == "-c"
        ]
        self.assertIn('model_reasoning_effort="xhigh"', config_values)
        self.assertIn('approval_policy="never"', config_values)
        self.assertEqual(args[args.index("--model") + 1], "gpt-5.5")
        self.assertEqual(args[-1], "-")
        self.assertNotIn("今の部屋の様子を見て話して。", args)
        self.assertNotIn("--json", args)
        self.assertEqual(result.speech, "環境を見ながら、落ち着いて進めます。")
        self.assertEqual(result.adapter_kind, "codex_cli_operator")
        self.assertEqual(result.provider, "codex-cli")
        self.assertTrue(result.used_llm)
        self.assertEqual(result.metadata["codex_cli_mode"], "operate")
        self.assertEqual(result.metadata["sandbox"], "workspace-write")
        self.assertEqual(result.metadata["approval"], "never")
        self.assertEqual(result.metadata["workspace_class"], "sword_agent_os_system_root")
        self.assertEqual(result.metadata["codex_cli_version"], "0.142.0")
        self.assertEqual(result.metadata["codex_cli_expected_version"], "0.142.0")
        self.assertEqual(
            result.metadata["codex_cli_version_class"],
            "observed_matching_expected",
        )

    def test_codex_cli_respond_mode_can_restore_read_only_response_adapter(self) -> None:
        seen_prompts = []

        def fake_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="codex-cli 0.142.0\n",
                    stderr="",
                )
            seen_prompts.append(prompt)
            output_path = Path(args[args.index("--output-last-message") + 1])
            output_path.write_text("短く返します。", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        responder = CodexCliChatResponder(
            command="codex",
            model="codex-cli",
            cwd=REPO_ROOT,
            mode="respond",
            runner=fake_runner,
        )

        result = responder.respond(
            TurnInput(
                text="こんにちは",
                turn_id="turn_codex_cli_respond",
                session_id="codex_cli_test",
            )
        )

        self.assertEqual(result.adapter_kind, "codex_cli_responder")
        self.assertEqual(result.metadata["codex_cli_mode"], "respond")
        self.assertEqual(result.metadata["sandbox"], "read-only")
        self.assertEqual(result.metadata["codex_cli_version_class"], "observed_no_expected")
        self.assertIn("response-only adapter", seen_prompts[0])

    def test_codex_cli_strict_version_mismatch_falls_back_before_exec(self) -> None:
        seen_args = []

        def fake_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            seen_args.append(list(args))
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="codex-cli 0.141.0\n",
                stderr="",
            )

        environment = EnvironmentTurnResponder(
            primary=CodexCliChatResponder(
                command="codex",
                model="codex-cli",
                cwd=REPO_ROOT,
                expected_version="0.142.0",
                version_policy="strict",
                runner=fake_runner,
            )
        )

        result = environment.respond(
            TurnInput(
                text="こんにちは",
                turn_id="turn_codex_cli_strict_version",
                session_id="codex_cli_test",
            )
        )

        self.assertEqual(seen_args, [["codex", "--version"]])
        self.assertEqual(result.adapter_kind, "local_fallback")
        self.assertEqual(result.status, "local_fallback_after_llm_error")
        self.assertEqual(result.detail, "codex_cli_failure_class:unavailable")

    def test_codex_cli_warn_version_mismatch_still_executes_with_metadata(self) -> None:
        seen_args = []

        def fake_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            seen_args.append(list(args))
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="codex-cli 0.141.0\n",
                    stderr="",
                )
            output_path = Path(args[args.index("--output-last-message") + 1])
            output_path.write_text("不一致は記録して続行しました。", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        responder = CodexCliChatResponder(
            command="codex",
            model="codex-cli",
            cwd=REPO_ROOT.parents[1],
            expected_version="0.142.0",
            version_policy="warn",
            runner=fake_runner,
        )

        result = responder.respond(
            TurnInput(
                text="バージョン警告の確認",
                turn_id="turn_codex_cli_warn_version",
                session_id="codex_cli_test",
            )
        )

        self.assertEqual(seen_args[0], ["codex", "--version"])
        self.assertEqual(seen_args[1][:2], ["codex", "exec"])
        self.assertTrue(result.used_llm)
        self.assertEqual(
            result.metadata["codex_cli_version_class"],
            "observed_mismatch_expected",
        )
        self.assertEqual(result.metadata["codex_cli_version_policy"], "warn")

    def test_codex_command_runner_sends_prompt_as_utf8(self) -> None:
        with patch("thought_core.responders.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["codex", "--version"],
                0,
                stdout="codex-cli 0.142.0\n",
                stderr="",
            )

            _run_codex_command(["codex", "--version"], 3.0, "日本語の入力")

        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], "日本語の入力")
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        self.assertTrue(kwargs["text"])

    def test_codex_cli_failure_falls_back_without_leaking_command_details(self) -> None:
        def failing_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="simulated provider failure",
            )

        environment = EnvironmentTurnResponder(
            primary=CodexCliChatResponder(
                command="codex",
                model="codex-cli",
                cwd=REPO_ROOT,
                runner=failing_runner,
            )
        )

        result = environment.respond(
            TurnInput(
                text="こんにちは",
                turn_id="turn_codex_cli_failure",
                session_id="codex_cli_test",
            )
        )

        self.assertEqual(result.adapter_kind, "local_fallback")
        self.assertEqual(result.status, "local_fallback_after_llm_error")
        self.assertFalse(result.used_llm)
        self.assertEqual(result.detail, "codex_cli_failure_class:nonzero_returncode")
        self.assertNotIn("simulated provider failure", result.detail)
        self.assertNotIn("こんにちは", result.detail)
        self.assertNotIn(str(REPO_ROOT), result.detail)

    def test_codex_cli_private_path_failures_publish_class_only_detail(self) -> None:
        private_command = r"C:\Users\kawai\private\missing-codex.cmd"
        private_workspace = r"C:\Users\kawai\works\secret\sword-agent-os"

        def failing_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            raise FileNotFoundError(
                f"{private_command} cwd={private_workspace} prompt={prompt}"
            )

        environment = EnvironmentTurnResponder(
            primary=CodexCliChatResponder(
                command=private_command,
                model="codex-cli",
                cwd=Path(private_workspace),
                runner=failing_runner,
            )
        )

        result = environment.respond(
            TurnInput(
                text="秘密の入力",
                turn_id="turn_codex_cli_private_failure",
                session_id="codex_cli_test",
            )
        )

        self.assertEqual(result.detail, "codex_cli_failure_class:unavailable")
        for forbidden in (
            private_command,
            private_workspace,
            "秘密の入力",
            "missing-codex",
        ):
            self.assertNotIn(forbidden, result.detail)

    def test_codex_cli_timeout_and_provider_text_publish_class_only_detail(self) -> None:
        def failing_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            raise TimeoutExpired(
                cmd=[
                    "codex",
                    "exec",
                    "--cd",
                    r"C:\Users\kawai\private\sword-agent-os",
                ],
                timeout=timeout_s,
                output="provider payload with token secret",
                stderr="stderr with HOME_CONTROL_API_TOKEN",
            )

        environment = EnvironmentTurnResponder(
            primary=CodexCliChatResponder(
                command="codex",
                model="codex-cli",
                cwd=REPO_ROOT,
                runner=failing_runner,
            )
        )

        result = environment.respond(
            TurnInput(
                text="こんにちは",
                turn_id="turn_codex_cli_timeout_failure",
                session_id="codex_cli_test",
            )
        )

        self.assertEqual(result.detail, "codex_cli_failure_class:timeout")
        for forbidden in (
            "HOME_CONTROL_API_TOKEN",
            "provider payload",
            "private",
            "こんにちは",
            "codex exec",
        ):
            self.assertNotIn(forbidden, result.detail)

    def test_codex_cli_env_cannot_broaden_sandbox_or_child_approval(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "codex-cli",
                "THOUGHT_CORE_CODEX_CLI_PATH": "codex",
                "THOUGHT_CORE_CODEX_CLI_CWD": str(REPO_ROOT),
                "THOUGHT_CORE_CODEX_CLI_SANDBOX": "danger-full-access",
                "THOUGHT_CORE_CODEX_CLI_APPROVAL": "on-request",
            },
            clear=True,
        ):
            responder = EnvironmentTurnResponder.from_env()

        self.assertIsInstance(responder.primary, CodexCliChatResponder)
        self.assertEqual(responder.primary.sandbox, "workspace-write")
        self.assertEqual(responder.primary.approval, "never")

    def test_http_turn_smoke_uses_stubbed_codex_cli_adapter_without_model(self) -> None:
        seen_args = []
        seen_prompts = []

        def fake_runner(args, timeout_s, prompt):  # type: ignore[no-untyped-def]
            seen_args.append(list(args))
            if "--version" in args:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="codex-cli 0.142.0\n",
                    stderr="",
                )
            seen_prompts.append(prompt)
            output_path = Path(args[args.index("--output-last-message") + 1])
            output_path.write_text(
                "stub Codex CLI 経路で応答しました。",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        responder = CodexCliChatResponder(
            command="codex",
            model="codex-cli",
            cwd=REPO_ROOT.parents[1],
            expected_version="0.142.0",
            runner=fake_runner,
        )
        server = create_server(
            "127.0.0.1",
            0,
            thought_loop=ThoughtLoop(responder=responder),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps(
                {
                    "text": "Codex CLI stub HTTP smoke です。ソースは編集しないでください。",
                    "turn_id": "turn_codex_cli_http_stub",
                    "session_id": "codex_cli_test",
                    "locale": "ja-JP",
                    "context_refs": {},
                },
                ensure_ascii=False,
            ).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}/turn",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with request.urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        completed = [
            event for event in payload["events"] if event["type"] == "responder.completed"
        ][-1]
        message = [
            event for event in payload["events"] if event["type"] == "assistant.message"
        ][-1]
        exec_args = seen_args[1]
        config_values = [
            exec_args[index + 1]
            for index, value in enumerate(exec_args)
            if value == "-c"
        ]
        serialized_args = " ".join(exec_args)

        self.assertEqual(seen_args[0], ["codex", "--version"])
        self.assertEqual(exec_args[:2], ["codex", "exec"])
        self.assertEqual(exec_args[-1], "-")
        self.assertEqual(exec_args[exec_args.index("--cd") + 1], str(REPO_ROOT.parents[1]))
        self.assertEqual(exec_args[exec_args.index("--sandbox") + 1], "workspace-write")
        self.assertIn('approval_policy="never"', config_values)
        self.assertNotIn("--ask-for-approval", exec_args)
        self.assertNotIn("Codex CLI stub HTTP smoke", serialized_args)
        self.assertIn("Codex CLI stub HTTP smoke", seen_prompts[0])
        for forbidden in (
            "/actions",
            "preview",
            "dry-run",
            "live execute",
            "CheckTracking",
            "CheckState",
            "HOME_CONTROL_API_TOKEN",
        ):
            self.assertNotIn(forbidden, serialized_args)

        self.assertEqual(completed["data"]["adapter_kind"], "codex_cli_operator")
        self.assertEqual(completed["data"]["provider"], "codex-cli")
        self.assertTrue(completed["data"]["used_llm"])
        self.assertEqual(
            completed["data"]["metadata"]["codex_cli_version_class"],
            "observed_matching_expected",
        )
        self.assertEqual(
            completed["data"]["metadata"]["workspace_class"],
            "sword_agent_os_system_root",
        )
        self.assertIn("stub Codex CLI", message["data"]["speech"])


if __name__ == "__main__":
    import unittest

    unittest.main()
