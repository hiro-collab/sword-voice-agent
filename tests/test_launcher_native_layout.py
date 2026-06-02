import shutil
import subprocess
from pathlib import Path
from unittest import TestCase, skipUnless


ROOT = Path(__file__).resolve().parents[1]
STACK_START = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def make_file(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def make_native_workspace(root: Path) -> None:
    make_file(root / "organs/action/home-assistant-server/config/home-control.yaml", "actions: []\n")
    make_file(root / "organs/action/home-assistant-server/.env", "HOME_CONTROL_API_TOKEN=\n")
    make_dir(root / "organs/environment/environment-state-server")
    make_file(root / "organs/environment/vision-snapshot-processor/src/vision_snapshot_processor/main.py", "")
    make_dir(root / "organs/expression/aituber-kit")
    make_dir(root / "organs/reflex/mediapipe-sword-sign")
    make_file(root / "organs/reflex/mediapipe-sword-sign/scripts/camera_hub_stack.py", "")
    make_file(root / "organs/display/touchdesigner-ai-controller/tools/server.js", "")
    make_dir(root / "organs/speech-input/ai-talk-core")

    control_plane = root / "control-plane/sword-voice-agent"
    make_file(control_plane / ".env", "THOUGHT_CORE_LLM_MODE=off\n")
    make_file(control_plane / "scripts/start-thought-core.ps1", "")
    make_file(control_plane / "scripts/start-thought-core-watch.ps1", "")
    make_dir(control_plane / "services/thought-core")


def make_legacy_aliases(root: Path) -> None:
    legacy_control = root / "sword-control-plane"
    make_file(legacy_control / ".env", "THOUGHT_CORE_LLM_MODE=off\n")
    make_file(legacy_control / "scripts/start-thought-core.ps1", "")
    make_file(legacy_control / "scripts/start-thought-core-watch.ps1", "")
    make_dir(legacy_control / "services/thought-core")
    make_dir(root / "organs/voice/ai-talk-core")


def run_stack_dry_run(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(STACK_START),
            "-WorkspaceRoot",
            str(workspace),
            "-StackStateDir",
            str(workspace / ".cache/home-control-stack"),
            "-SkipDify",
            "-SkipDifyWatch",
            "-SkipVoicevoxCheck",
            "-EnableThoughtCore",
            "-EnableThoughtCoreWatch",
            "-MediapipeNoBrowser",
            "-DryRun",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


@skipUnless(POWERSHELL, "PowerShell is required for launcher dry-run contract tests")
class LauncherNativeLayoutTest(TestCase):
    def test_stack_dry_run_accepts_native_agent_os_layout_without_legacy_aliases(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="sword-launch-native-layout-") as temp_dir:
            workspace = Path(temp_dir) / "sword-agent-os"
            make_native_workspace(workspace)

            self.assertFalse((workspace / "sword-control-plane").exists())
            self.assertFalse((workspace / "organs/voice/ai-talk-core").exists())

            result = run_stack_dry_run(workspace)
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(result.returncode, 0, output)
            self.assertNotIn("sword-control-plane directory not found", output)
            self.assertNotIn("ai-talk-core directory not found", output)
            self.assertIn(r"control-plane\sword-voice-agent\scripts\start-thought-core.ps1", output)
            self.assertIn(r"control-plane\sword-voice-agent\scripts\start-thought-core-watch.ps1", output)
            self.assertIn(r"organs\speech-input\ai-talk-core", output)
            self.assertNotIn(r"organs\voice\ai-talk-core", output)

    def test_stack_dry_run_prefers_native_paths_when_legacy_aliases_exist(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="sword-launch-native-preferred-") as temp_dir:
            workspace = Path(temp_dir) / "sword-agent-os"
            make_native_workspace(workspace)
            make_legacy_aliases(workspace)

            result = run_stack_dry_run(workspace)
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(result.returncode, 0, output)
            self.assertIn(r"control-plane\sword-voice-agent\scripts\start-thought-core.ps1", output)
            self.assertIn(r"control-plane\sword-voice-agent\scripts\start-thought-core-watch.ps1", output)
            self.assertIn(r"organs\speech-input\ai-talk-core", output)
            self.assertNotIn(r"sword-control-plane\scripts", output)
            self.assertNotIn(r"organs\voice\ai-talk-core", output)

    def test_stack_dry_run_uses_legacy_ai_talk_alias_when_native_speech_input_is_missing(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="sword-launch-partial-ai-talk-") as temp_dir:
            workspace = Path(temp_dir) / "sword-agent-os"
            make_native_workspace(workspace)
            make_legacy_aliases(workspace)
            speech_input = workspace / "organs/speech-input"
            self.assertTrue(str(speech_input.resolve()).startswith(str(workspace.resolve())))
            shutil.rmtree(speech_input)

            result = run_stack_dry_run(workspace)
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(result.returncode, 0, output)
            self.assertIn(r"organs\voice\ai-talk-core", output)
            self.assertNotIn("ai-talk-core directory not found", output)
