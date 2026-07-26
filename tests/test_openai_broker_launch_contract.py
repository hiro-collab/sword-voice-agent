import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
STACK_START = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
STACK_STOP = ROOT / "ops" / "scripts" / "home-control-stack" / "stop-home-control-stack.ps1"
SYSTEM = ROOT / "ops" / "scripts" / "system.ps1"
THOUGHT_CORE_START = ROOT / "scripts" / "start-thought-core.ps1"
LAUNCHER = ROOT / "tools" / "home-control-launcher" / "server.js"


class OpenAIBrokerLaunchContractTest(TestCase):
    @staticmethod
    def _quoted_ps_array(source: str, variable: str) -> set[str]:
        prefix = f"${variable} = @("
        start = source.find(prefix)
        if start < 0:
            raise AssertionError(f"missing PowerShell array: {variable}")
        end = source.find("\n)", start)
        if end < 0:
            raise AssertionError(f"unterminated PowerShell array: {variable}")
        return set(re.findall(r'"([A-Za-z0-9_]+)"', source[start + len(prefix):end]))

    @staticmethod
    def _powershell_regex_matches(pattern: str, command_line: str) -> bool:
        def quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        script = (
            f"$pattern = {quote(pattern)}; $commandLine = {quote(command_line)}; "
            "if ($commandLine -match $pattern) { exit 0 }; exit 1"
        )
        result = subprocess.run(
            [
                r"C:\Program Files\PowerShell\7\pwsh.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        if result.returncode not in (0, 1):
            raise AssertionError("PowerShell regex helper failed")
        return result.returncode == 0

    def test_primary_profile_and_manifests_are_broker_first(self) -> None:
        profile = json.loads(
            (ROOT / "tools" / "home-control-launcher" / "config" / "default-profiles.json").read_text(encoding="utf-8")
        )
        primary = next(item for item in profile if item["id"] == "thought-core-v0")
        self.assertEqual(primary["options"]["ThoughtCoreLlmProvider"], "sword-openai-broker")

        service = json.loads(
            (ROOT / "ops" / "manifests" / "services" / "openai_provider_broker.json").read_text(encoding="utf-8")
        )
        lifecycle = json.loads(
            (ROOT / "ops" / "manifests" / "profiles" / "thought-core-v0.json").read_text(encoding="utf-8")
        )
        self.assertEqual(service["health"]["url"], "http://127.0.0.1:18786/health")
        self.assertEqual(service["secret_source_class"], "thought-core-existing-env-v1")
        self.assertLess(lifecycle["services"].index("openai_provider_broker"), lifecycle["services"].index("thought_core_api"))

    def test_launcher_and_system_preserve_the_primary_provider(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        system = SYSTEM.read_text(encoding="utf-8")
        self.assertIn("'sword-openai-broker'", launcher)
        self.assertIn('"sword-openai-broker"', system)
        self.assertIn('"-ThoughtCoreLlmProvider"', system)
        self.assertIn('Invoke-StackScript -ScriptName "start-home-control-stack.ps1"', system)

    def test_broker_port_is_mode_derived_and_propagated_end_to_end(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        system = SYSTEM.read_text(encoding="utf-8")
        stack = STACK_START.read_text(encoding="utf-8")

        self.assertIn("const OPENAI_BROKER_PORT_BY_MODE = {", launcher)
        self.assertIn("manifest_default: 18786", launcher)
        self.assertIn("isolated_override: 18886", launcher)
        self.assertIn("OpenAIBrokerPort: OPENAI_BROKER_PORT", launcher)
        self.assertIn(
            "Number(base.OpenAIBrokerPort) !== OPENAI_BROKER_PORT",
            launcher,
        )
        self.assertIn("throw new Error('invalid_openai_broker_port')", launcher)
        self.assertIn(
            "const { OpenAIBrokerPort, ...persistedOptions } = options || {}",
            launcher,
        )
        self.assertIn("options: persistedOptions", launcher)
        self.assertLess(
            launcher.index("invalid_openai_broker_port"),
            launcher.index("const buildSystemStartArgs"),
        )
        self.assertIn(
            "addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'OpenAIBrokerPort', options.OpenAIBrokerPort)",
            launcher,
        )

        self.assertIn("[ValidateSet(18786, 18886)]", system)
        self.assertIn("[int]$OpenAIBrokerPort = 18786", system)
        self.assertIn('"-OpenAIBrokerPort"', system)
        self.assertIn("[ValidateSet(18786, 18886)]", stack)
        self.assertIn("[int]$OpenAIBrokerPort = 18786", stack)
        self.assertIn(
            '$OpenAIBrokerBaseUrl = "http://{0}:{1}/v1" -f $OpenAIBrokerHost, $OpenAIBrokerPort',
            stack,
        )
        self.assertIn(
            '$OpenAIBrokerHealthUrl = "http://{0}:{1}/health" -f $OpenAIBrokerHost, $OpenAIBrokerPort',
            stack,
        )
        self.assertIn("Port = $OpenAIBrokerPort", stack)
        self.assertIn('"--port", [string]$OpenAIBrokerPort', stack)
        self.assertIn("Get-ListeningPortOwner -Port $OpenAIBrokerPort", stack)

        broker_ports = {
            mode: int(port)
            for mode, port in re.findall(
                r"^\s*(manifest_default|isolated_override):\s*(\d+),?$",
                launcher,
                flags=re.MULTILINE,
            )
        }
        self.assertEqual(
            broker_ports,
            {"manifest_default": 18786, "isolated_override": 18886},
        )
        self.assertNotIn(18888, broker_ports.values())
        self.assertIn("ThoughtCorePort: 18787", launcher)
        self.assertIn("ThoughtCorePort: 18888", launcher)

    def test_stack_sanitizes_children_and_waits_for_broker_before_thought_core(self) -> None:
        stack = STACK_START.read_text(encoding="utf-8")
        thought_core = THOUGHT_CORE_START.read_text(encoding="utf-8")
        stop = STACK_STOP.read_text(encoding="utf-8")

        self.assertIn("[ValidateSet(18786, 18886)]", stack)
        self.assertIn("[int]$OpenAIBrokerPort = 18786", stack)
        self.assertNotIn('18888', stack)
        self.assertIn('function Wait-OpenAIBrokerReady', stack)
        self.assertIn('Wait-OpenAIBrokerReady -Child $rootChild -TimeoutSeconds 12', stack)
        self.assertLess(stack.index('-Name "openai_provider_broker"'), stack.index('-Name "thought_core_api"'))
        self.assertIn('RemoveEnvironment = @($RemoveEnvironment)', stack)
        self.assertIn('ClearInheritedEnvironment = [bool]$ClearInheritedEnvironment', stack)
        self.assertIn('$startInfo.Environment.Clear()', stack)
        self.assertIn('$MinimalBrokerRuntimeEnvironmentNames', stack)
        self.assertIn('-ClearInheritedEnvironment', stack)
        self.assertIn('$startInfo.Environment.Remove([string]$name)', stack)
        self.assertIn('"OPENAI_API_KEY"', stack)
        self.assertIn('"THOUGHT_CORE_ACTION_LLM_API_KEY"', stack)
        self.assertIn('"-SkipEnvImport"', stack)
        self.assertNotIn('Get-DotEnvValue -Path $ThoughtCoreEnvPath -Name "OPENAI_API_KEY"', stack)
        self.assertNotIn('Get-DotEnvValue -Path $ThoughtCoreEnvPath -Name "THOUGHT_CORE_LLM_API_KEY"', stack)
        self.assertIn('[switch]$SkipEnvImport', thought_core)
        self.assertIn('(-not $SkipEnvImport)', thought_core)
        self.assertIn('Remove-Item -Path "Env:$name"', thought_core)
        self.assertIn('Stop-ManagedProcessEntry', stop)
        self.assertIn('Remove-Item -LiteralPath $PidFile', stop)

    def test_private_provider_sentinels_are_excluded_by_full_scrub_lists(self) -> None:
        stack = STACK_START.read_text(encoding="utf-8")
        thought_core = THOUGHT_CORE_START.read_text(encoding="utf-8")
        provider_inputs = (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "THOUGHT_CORE_LLM_API_KEY",
            "THOUGHT_CORE_LLM_PROVIDER",
            "THOUGHT_CORE_LLM_ADAPTER",
            "THOUGHT_CORE_LLM_BASE_URL",
            "THOUGHT_CORE_LLM_MODEL",
            "THOUGHT_CORE_LLM_TIMEOUT_S",
            "THOUGHT_CORE_LLM_ENABLED",
            "THOUGHT_CORE_ACTION_LLM_API_KEY",
            "THOUGHT_CORE_ACTION_LLM_PROVIDER",
            "THOUGHT_CORE_ACTION_LLM_ADAPTER",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL",
            "THOUGHT_CORE_ACTION_LLM_MODEL",
            "THOUGHT_CORE_ACTION_LLM_TIMEOUT_S",
            "THOUGHT_CORE_ACTION_LLM_ENABLED",
        )
        for name in provider_inputs:
            with self.subTest(name=name):
                self.assertIn(f'"{name}"', stack)
                self.assertIn(f'"{name}"', thought_core)
        for unsafe_name in ("SSLKEYLOGFILE", "HTTP_PROXY", "PYTHONPATH"):
            self.assertNotIn(f'"{unsafe_name}"', stack[stack.index('$MinimalBrokerRuntimeEnvironmentNames'):stack.index('function New-ServiceSpec')])
        self.assertIn('$thoughtCoreEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"', stack)
        self.assertIn('$canonicalBrokerEnvironment[$name] = $value', thought_core)
        self.assertNotIn("PRIVATE_PARENT_ENV_SENTINEL", stack + thought_core)

    def test_broker_environment_composition_excludes_parent_sentinels(self) -> None:
        stack = STACK_START.read_text(encoding="utf-8")
        allowlist = self._quoted_ps_array(stack, "MinimalBrokerRuntimeEnvironmentNames")
        scrubbed = self._quoted_ps_array(stack, "ProviderEnvironmentInputNames")
        parent = {
            "ComSpec": "synthetic-comspec",
            "SystemRoot": "synthetic-system-root",
            "PATH": "synthetic-path",
            "SSLKEYLOGFILE": "synthetic-tls-key-log",
            "HTTP_PROXY": "http://synthetic-proxy.invalid",
            "PYTHONPATH": "synthetic-python-injection",
            "PRIVATE_PARENT_ENV_SENTINEL": "synthetic-private-parent",
            "OPENAI_API_KEY": "synthetic-private-credential",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_ACTION_LLM_MODEL": "synthetic-action-model",
        }
        broker_child = {name: parent[name] for name in allowlist if name in parent}
        broker_child.update({"PYTHONUTF8": "1", "NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb"})
        self.assertIn("ComSpec", broker_child)
        self.assertTrue(set(parent).isdisjoint(set(broker_child) - {"ComSpec", "SystemRoot", "PATH"}))
        self.assertTrue(set(scrubbed).isdisjoint(set(broker_child)))
        for sentinel in (
            "SSLKEYLOGFILE",
            "HTTP_PROXY",
            "PYTHONPATH",
            "PRIVATE_PARENT_ENV_SENTINEL",
            "OPENAI_API_KEY",
            "THOUGHT_CORE_ACTION_LLM_ENABLED",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL",
            "THOUGHT_CORE_ACTION_LLM_MODEL",
        ):
            self.assertNotIn(sentinel, broker_child)
        self.assertIn("$startInfo.Environment.Clear()", stack)
        self.assertIn("foreach ($name in $MinimalBrokerRuntimeEnvironmentNames)", stack)
        self.assertIn("if (-not $Spec.ClearInheritedEnvironment)", stack)

    def test_skip_env_import_preserves_sanitized_codex_compatibility_provider(self) -> None:
        stack = STACK_START.read_text(encoding="utf-8")
        thought_core = THOUGHT_CORE_START.read_text(encoding="utf-8")
        self.assertIn(
            '$isBrokerPrimary = $SkipEnvImport -and $env:THOUGHT_CORE_LLM_PROVIDER -ceq "sword-openai-broker"',
            thought_core,
        )
        broker_scrub = thought_core[
            thought_core.index("if ($isBrokerPrimary) {"):thought_core.index("if ($env:THOUGHT_CORE_FORCE_NO_PROVIDER")
        ]
        self.assertIn('Remove-Item -Path "Env:$name"', broker_scrub)
        self.assertIn('$canonicalBrokerEnvironment[$name] = $value', broker_scrub)
        self.assertIn('$canonicalBrokerEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"', broker_scrub)
        self.assertNotIn('Remove-Item -Path "Env:$name"', thought_core.replace(broker_scrub, ""))
        self.assertIn('"THOUGHT_CORE_LLM_TIMEOUT_S"', stack)
        self.assertIn('"THOUGHT_CORE_LLM_TIMEOUT_S"', thought_core)
        self.assertIn('if ($thoughtCoreRuntimeProvider -eq "codex-cli")', stack)
        self.assertIn('if ($ThoughtCoreLlmProvider -eq "codex-cli-luna")', stack)
        self.assertIn('$thoughtCoreEnvironment["THOUGHT_CORE_LLM_PROVIDER"] = $thoughtCoreRuntimeProvider', stack)
        self.assertIn('-Environment $thoughtCoreEnvironment', stack)
        self.assertIn('"-SkipEnvImport"', stack)

    def test_broker_readiness_requires_owned_listener_before_health(self) -> None:
        stack = STACK_START.read_text(encoding="utf-8")
        self.assertIn("function Test-SealedOpenAIBrokerListenerOwnership", stack)
        self.assertIn("Get-ListeningPortOwner -Port $OpenAIBrokerPort", stack)
        self.assertIn("Get-DescendantProcessIds -RootProcessId $rootPid", stack)
        self.assertIn('Label = "openai-provider-broker"', stack)
        wait_start = stack.index("function Wait-OpenAIBrokerReady")
        wait = stack[wait_start:stack.index("New-Item -ItemType Directory", wait_start)]
        self.assertLess(
            wait.index("Test-SealedOpenAIBrokerListenerOwnership -Child $Child"),
            wait.index("Invoke-WebRequest"),
        )

        port_match = re.search(r'\$expectedPort = "([^"]+)"', stack)
        module_match = re.search(r'-match "([^"\n]*sword_voice_agent[^"\n]*)"', stack)
        self.assertIsNotNone(port_match)
        self.assertIsNotNone(module_match)
        module_pattern = module_match.group(1)
        for port, wrong_port in ((18786, 18886), (18886, 18786)):
            port_pattern = port_match.group(1).format(port)
            canonical_command = (
                "uv run python -m sword_voice_agent.apps.openai_broker "
                f"--port {port}"
            )
            self.assertTrue(self._powershell_regex_matches(module_pattern, canonical_command))
            self.assertTrue(self._powershell_regex_matches(port_pattern, canonical_command))
            for rejected_command in (
                f"uv run python -m other_broker --port {port}",
                "uv run python -m sword_voice_agent.apps.openai_broker "
                f"--port {wrong_port}",
                f"python -m unrelated_health --port {port}",
            ):
                self.assertFalse(
                    self._powershell_regex_matches(module_pattern, rejected_command)
                    and self._powershell_regex_matches(port_pattern, rejected_command)
                )

        def owned_listener(
            expected_port: int,
            observed_port: int,
            owner_pid: int,
            owned_pids: set[int],
            listener_pids: list[int],
        ) -> bool:
            return (
                observed_port == expected_port
                and len(listener_pids) == 1
                and listener_pids[0] in owned_pids | {owner_pid}
            )

        for port, wrong_port in ((18786, 18886), (18886, 18786)):
            self.assertFalse(owned_listener(port, port, 7100, {7101}, [7200]))
            self.assertFalse(owned_listener(port, port, 7100, {7101}, [7101, 7200]))
            self.assertFalse(owned_listener(port, wrong_port, 7100, {7101}, [7101]))
            self.assertTrue(owned_listener(port, port, 7100, {7101}, [7101]))

    def test_broker_primary_child_environment_cannot_construct_or_request_action_reviewer(self) -> None:
        thought_core_src = ROOT / "services" / "thought-core" / "src"
        sys.path.insert(0, str(thought_core_src))
        try:
            from thought_core.reasoning import OpenAICompatibleActionReviewer

            child_environment = {
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "sword-openai-broker",
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1",
                "THOUGHT_CORE_LLM_MODEL": "gpt-4o-mini",
                "THOUGHT_CORE_LLM_TIMEOUT_S": "12",
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
            }
            self.assertEqual(
                set(child_environment),
                {
                    "THOUGHT_CORE_LLM_ENABLED",
                    "THOUGHT_CORE_LLM_PROVIDER",
                    "THOUGHT_CORE_LLM_BASE_URL",
                    "THOUGHT_CORE_LLM_MODEL",
                    "THOUGHT_CORE_LLM_TIMEOUT_S",
                    "THOUGHT_CORE_ACTION_LLM_ENABLED",
                },
            )
            with (
                patch.dict(os.environ, child_environment, clear=True),
                patch.object(OpenAICompatibleActionReviewer, "__init__", autospec=True) as constructor,
                patch.object(OpenAICompatibleActionReviewer, "_chat_json", autospec=True) as request,
            ):
                self.assertIsNone(OpenAICompatibleActionReviewer.from_env())
            constructor.assert_not_called()
            request.assert_not_called()
        finally:
            sys.path.remove(str(thought_core_src))
