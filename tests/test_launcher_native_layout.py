import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT.parents[1]
SYSTEM = ROOT / "ops" / "scripts" / "system.ps1"
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

    control_plane = root / "control-plane/core"
    make_file(control_plane / ".env", "THOUGHT_CORE_LLM_MODE=off\n")
    make_file(control_plane / "scripts/start-thought-core.ps1", "")
    make_file(control_plane / "scripts/start-thought-core-watch.ps1", "")
    make_dir(control_plane / "services/thought-core")


def make_legacy_residue(root: Path) -> None:
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


def run_system(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SYSTEM),
            *arguments,
        ],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=30,
    )


@skipUnless(POWERSHELL, "PowerShell is required for launcher dry-run contract tests")
class LauncherNativeLayoutTest(TestCase):
    def test_system_default_workspace_matches_explicit_product_root_independent_of_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sword-system-default-status-") as temp_dir:
            temp_root = Path(temp_dir)
            default_cwd = temp_root / "default-cwd"
            explicit_cwd = temp_root / "explicit-cwd"
            default_cwd.mkdir()
            explicit_cwd.mkdir()
            relative_state_dir = Path(".cache") / f"workspace-root-parity-{temp_root.name}"
            canonical_state_dir = PRODUCT_ROOT / relative_state_dir
            incorrect_state_dir = PRODUCT_ROOT / "control-plane" / relative_state_dir
            common_arguments = (
                "status",
                "-Profile",
                "thought-core-v0",
                "-StackStateDir",
                str(relative_state_dir),
                "-ManifestOnly",
            )

            default_result = run_system(*common_arguments, cwd=default_cwd)
            explicit_result = run_system(
                *common_arguments,
                "-WorkspaceRoot",
                str(PRODUCT_ROOT),
                cwd=explicit_cwd,
            )
            default_output = f"{default_result.stdout}\n{default_result.stderr}"
            explicit_output = f"{explicit_result.stdout}\n{explicit_result.stderr}"

            self.assertEqual(default_result.returncode, 0, default_output)
            self.assertEqual(explicit_result.returncode, 0, explicit_output)
            self.assertEqual(default_result.stdout, explicit_result.stdout)
            self.assertEqual(default_result.stderr, explicit_result.stderr)
            for output in (default_output, explicit_output):
                self.assertIn(f"state_dir={canonical_state_dir}", output)
                self.assertNotIn(str(incorrect_state_dir), output)
            self.assertFalse(canonical_state_dir.exists())
            self.assertFalse((canonical_state_dir / "pids.json").exists())
            self.assertFalse(incorrect_state_dir.exists())

    def test_system_default_workspace_dry_run_reaches_canonical_product_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sword-system-default-start-") as temp_dir:
            temp_root = Path(temp_dir)
            unrelated_cwd = temp_root / "unrelated-cwd"
            unrelated_cwd.mkdir()
            state_dir = temp_root / "stack-state"

            result = run_system(
                "start",
                "-Profile",
                "thought-core-v0",
                "-StackStateDir",
                str(state_dir),
                "-SkipHomeAssistantBridge",
                "-SkipEnvironmentState",
                "-SkipMediapipe",
                "-SkipVisionSnapshotProcessor",
                "-SkipAituber",
                "-SkipTouchDesignerGui",
                "-SkipVoicevoxCheck",
                "-ThoughtCoreNoProvider",
                "-StopExisting",
                "-DryRun",
                cwd=unrelated_cwd,
            )
            output = f"{result.stdout}\n{result.stderr}"

            self.assertEqual(result.returncode, 0, output)
            self.assertIn(str(PRODUCT_ROOT / "control-plane/core/scripts/start-thought-core.ps1"), output)
            self.assertIn(str(PRODUCT_ROOT / "control-plane/core/scripts/start-thought-core-watch.ps1"), output)
            self.assertIn(str(PRODUCT_ROOT / "organs/speech-input/ai-talk-core"), output)
            self.assertNotIn(r"sword-control-plane\scripts", output)
            self.assertNotIn(r"organs\voice\ai-talk-core", output)
            self.assertTrue((state_dir / "logs").is_dir())
            self.assertEqual({path.name for path in state_dir.iterdir()}, {"logs"})
            self.assertEqual(list((state_dir / "logs").iterdir()), [])
            self.assertFalse((state_dir / "pids.json").exists())

    def test_system_camera_selection_is_required_only_for_camera_profiles(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sword-system-camera-selection-") as temp_dir:
            temp_root = Path(temp_dir)
            non_camera_state = temp_root / "aituber-only-state"
            non_camera_result = run_system(
                "start",
                "-Profile",
                "aituber-only",
                "-WorkspaceRoot",
                str(PRODUCT_ROOT),
                "-StackStateDir",
                str(non_camera_state),
                "-MediapipeCameraName",
                "",
                "-SkipVoicevoxCheck",
                "-DryRun",
                cwd=temp_root,
            )
            non_camera_output = (
                f"{non_camera_result.stdout}\n{non_camera_result.stderr}"
            )

            self.assertEqual(non_camera_result.returncode, 0, non_camera_output)
            self.assertIn("delegate=start", non_camera_output)
            self.assertNotIn("-MediapipeCameraName", non_camera_output)
            self.assertFalse((non_camera_state / "pids.json").exists())

            for case_name, selection_arguments in (
                ("omitted", ()),
                ("explicit-blank", ("-MediapipeCameraName", "")),
            ):
                with self.subTest(case=case_name):
                    camera_state = temp_root / f"camera-debug-{case_name}-state"
                    camera_result = run_system(
                        "start",
                        "-Profile",
                        "camera-debug",
                        "-WorkspaceRoot",
                        str(PRODUCT_ROOT),
                        "-StackStateDir",
                        str(camera_state),
                        *selection_arguments,
                        "-DryRun",
                        cwd=temp_root,
                    )
                    camera_output = (
                        f"{camera_result.stdout}\n{camera_result.stderr}"
                    )

                    self.assertNotEqual(
                        camera_result.returncode, 0, camera_output
                    )
                    self.assertIn(
                        "MediapipeCameraName is required for a dshow camera profile",
                        camera_output,
                    )
                    self.assertFalse((camera_state / "pids.json").exists())

            testsrc_state = temp_root / "camera-debug-testsrc-state"
            testsrc_result = run_system(
                "start",
                "-Profile",
                "camera-debug",
                "-WorkspaceRoot",
                str(PRODUCT_ROOT),
                "-StackStateDir",
                str(testsrc_state),
                "-MediapipeVideoSource",
                "testsrc",
                "-DryRun",
                cwd=temp_root,
            )
            testsrc_output = f"{testsrc_result.stdout}\n{testsrc_result.stderr}"

            self.assertEqual(testsrc_result.returncode, 0, testsrc_output)
            self.assertIn("delegate=start", testsrc_output)
            self.assertNotIn("-MediapipeCameraName", testsrc_output)
            self.assertFalse((testsrc_state / "pids.json").exists())

            selected_state = temp_root / "camera-debug-selected-state"
            selected_camera = "private-camera-selection"
            selected_result = run_system(
                "start",
                "-Profile",
                "camera-debug",
                "-WorkspaceRoot",
                str(PRODUCT_ROOT),
                "-StackStateDir",
                str(selected_state),
                "-MediapipeCameraName",
                selected_camera,
                "-DryRun",
                cwd=temp_root,
            )
            selected_output = f"{selected_result.stdout}\n{selected_result.stderr}"

            self.assertEqual(selected_result.returncode, 0, selected_output)
            self.assertIn(
                "-MediapipeCameraName <local-camera-selection>",
                selected_output,
            )
            self.assertNotIn(selected_camera, selected_output)
            self.assertFalse((selected_state / "pids.json").exists())

    def test_stack_dry_run_accepts_native_agent_os_layout_without_legacy_aliases(self) -> None:
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
            self.assertIn(r"control-plane\core\scripts\start-thought-core.ps1", output)
            self.assertIn(r"control-plane\core\scripts\start-thought-core-watch.ps1", output)
            self.assertIn(r"organs\speech-input\ai-talk-core", output)
            self.assertNotIn(r"organs\voice\ai-talk-core", output)

    def test_stack_dry_run_prefers_native_paths_when_legacy_aliases_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sword-launch-native-preferred-") as temp_dir:
            workspace = Path(temp_dir) / "sword-agent-os"
            make_native_workspace(workspace)
            make_legacy_residue(workspace)

            result = run_stack_dry_run(workspace)
            output = f"{result.stdout}\n{result.stderr}"
            self.assertEqual(result.returncode, 0, output)
            self.assertIn(r"control-plane\core\scripts\start-thought-core.ps1", output)
            self.assertIn(r"control-plane\core\scripts\start-thought-core-watch.ps1", output)
            self.assertIn(r"organs\speech-input\ai-talk-core", output)
            self.assertNotIn(r"sword-control-plane\scripts", output)
            self.assertNotIn(r"organs\voice\ai-talk-core", output)

    def test_stack_dry_run_rejects_missing_canonical_speech_input_even_if_legacy_residue_exists(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sword-launch-partial-ai-talk-") as temp_dir:
            workspace = Path(temp_dir) / "sword-agent-os"
            make_native_workspace(workspace)
            make_legacy_residue(workspace)
            speech_input = workspace / "organs/speech-input"
            self.assertTrue(str(speech_input.resolve()).startswith(str(workspace.resolve())))
            shutil.rmtree(speech_input)

            result = run_stack_dry_run(workspace)
            output = f"{result.stdout}\n{result.stderr}"
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("ai-talk-core directory not found", output)
            self.assertIn(r"organs\speech-input\ai-talk-core", output)
            self.assertNotIn(r"organs\voice\ai-talk-core", output)
