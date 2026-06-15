import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
LAUNCHER_PROFILES = ROOT / "tools" / "home-control-launcher" / "config" / "default-profiles.json"
STACK_START_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
SYSTEM_SCRIPT = ROOT / "ops" / "scripts" / "system.ps1"
THOUGHT_CORE_START_SCRIPT = ROOT / "scripts" / "start-thought-core.ps1"


def read_public(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


def read_launcher_server() -> str:
    return LAUNCHER_SERVER.read_text(encoding="utf-8")


def read_launcher_profiles() -> list[dict]:
    payload = json.loads(LAUNCHER_PROFILES.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8")


def read_system_script() -> str:
    return SYSTEM_SCRIPT.read_text(encoding="utf-8")


def read_thought_core_start_script() -> str:
    return THOUGHT_CORE_START_SCRIPT.read_text(encoding="utf-8")


class LauncherUiContractTest(TestCase):
    def test_launch_configuration_uses_progressive_disclosure(self) -> None:
        html = read_public("index.html")

        self.assertIn("Launch configuration", html)
        self.assertNotIn("<h2>System profile</h2>", html)
        self.assertIn("Launch summary", html)
        self.assertIn('id="services-summary"', html)
        self.assertIn('id="diagnostics-summary"', html)
        self.assertIn('id="runtime-summary"', html)
        self.assertIn('id="ports-summary"', html)
        self.assertIn("<summary>", html)
        self.assertIn("Core ports", html)
        self.assertIn("Core services", html)
        self.assertIn("Compatibility runtime", html)
        self.assertIn("Advanced overrides", html)

    def test_launcher_first_view_keeps_quick_links_and_density_hooks(self) -> None:
        html = read_public("index.html")
        css = read_public("styles.css")

        self.assertIn("Quick Links", html)
        self.assertIn('class="panel read-surface reference-panel"', html)
        self.assertIn("grid-template-columns: clamp(286px, 20%, 310px) minmax(0, 1fr)", css)
        self.assertIn(".profile-summary-grid", css)
        self.assertIn(".launch-panel .option-drawer", css)

    def test_launch_summary_warns_about_duplicate_ports(self) -> None:
        app = read_public("app.js")

        self.assertIn("const renderLaunchSummary", app)
        self.assertIn("Duplicate port values", app)
        self.assertIn("Check conflict", app)
        self.assertIn("services-summary", app)
        self.assertIn("ports-drawer-summary", app)

    def test_operation_banner_exposes_startup_progress_bar(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn('id="operation-progress"', html)
        self.assertIn('role="progressbar"', html)
        self.assertIn('id="operation-progress-bar"', html)
        self.assertIn('id="operation-progress-label"', html)
        self.assertIn("setOperationProgressFromSummary", app)
        self.assertIn("remaining", app)
        self.assertIn(".operation-progress", css)

    def test_stop_stack_reports_verified_shutdown_or_residue(self) -> None:
        server = read_launcher_server()
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn("collectStackStopVerification", server)
        self.assertIn("waitForStackStopVerification", server)
        self.assertIn("stopVerification", server)
        self.assertIn("managed ports still listening", server)
        self.assertIn("recorded processes still alive", server)
        self.assertIn("formatStopVerificationDetail", app)
        self.assertIn("Stop verified", app)
        self.assertIn("Stop incomplete", app)
        self.assertIn("Stop Launcher Only", html)
        self.assertIn("Stop Launcher Only", app)

    def test_service_rows_mark_startup_booting_progress(self) -> None:
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn("const serviceIsBooting", app)
        self.assertIn("state.operation === 'starting'", app)
        self.assertIn("serviceStateGroup(service?.state) !== 'ok'", app)
        self.assertIn('data-booting="${isBooting ? \'true\' : \'false\'}"', app)
        self.assertIn('.service-row[data-booting="true"]', css)
        self.assertIn("@keyframes service-row-scan", css)
        self.assertIn("@keyframes service-row-boot-line", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("position: absolute", css)

    def test_quick_links_use_display_safe_environment_endpoint(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")

        self.assertIn("Environment display state", server)
        self.assertIn("/indicators/current", server)
        self.assertNotIn("name: 'Environment current state'", server)
        self.assertNotIn("/environment/current`,", server)
        self.assertIn("'Environment display state': 'Env state'", app)

    def test_projection_quick_links_use_canonical_trailing_slash_routes(self) -> None:
        server = read_launcher_server()
        stack_start = read_stack_start_script()
        app = read_public("app.js")

        self.assertIn("name: 'Passive Projection'", server)
        self.assertIn("/projection-visual/`", server)
        self.assertIn("/projection-visual/?mode=passive&hud=0`", server)
        self.assertIn("/projection-visual/?mode=passive", stack_start)
        self.assertIn("'Passive Projection': 'Stage'", app)
        self.assertNotIn("/projection-visual?mode=passive", server)
        self.assertNotIn("/projection-visual?mode=passive", stack_start)

    def test_stack_start_reclaims_only_managed_stale_port_owners(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("function Test-CommandLineReferencesPath", stack_start)
        self.assertIn("function Get-ReclaimableRootForPortConflict", stack_start)
        self.assertIn("function Stop-ReclaimablePortConflicts", stack_start)
        self.assertIn("[ports] reclaiming stale managed port owner", stack_start)
        self.assertIn("Test-ExternalProcessDenied", stack_start)
        self.assertIn("AITuber Kit", stack_start)

    def test_stack_start_uses_voicevox_readiness_helper(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("scripts\\check-voicevox-readiness.ps1", stack_start)
        self.assertIn("-StartIfNeeded", stack_start)
        self.assertIn("started existing local VOICEVOX", stack_start)
        self.assertIn("If you intentionally do not use VOICEVOX", stack_start)

    def test_home_control_stack_prefers_local_live_config(self) -> None:
        server = read_launcher_server()
        stack_start = read_stack_start_script()

        self.assertIn("DEFAULT_HOME_CONTROL_LIVE_CONFIG", server)
        self.assertIn("'home-control.live.yaml'", server)
        self.assertIn("defaultHomeControlConfigPath", server)
        self.assertIn("!normalized.SkipHomeAssistantBridge", server)
        self.assertIn("normalized.HomeControlConfigPath = defaultHomeControlConfigPath()", server)

        self.assertIn("function Resolve-HomeControlConfigPath", stack_start)
        self.assertIn("local\\env\\home-control.live.yaml", stack_start)
        self.assertIn("function Assert-HomeControlConfigNotDemoLiveMapping", stack_start)
        self.assertIn("script\\.demo_light_(on|off)", stack_start)
        self.assertIn("selected bridge config still maps light actions to demo scripts", stack_start)

    def test_launcher_status_exposes_home_control_config_state(self) -> None:
        server = read_launcher_server()

        self.assertIn("compactHomeControlConfigState", server)
        self.assertIn("homeControlConfigState", server)
        self.assertIn("expected_profile", server)
        self.assertIn("active_profile", server)
        self.assertIn("light_demo_mappings_present", server)
        self.assertIn("live_home_invalid", server)
        self.assertIn("payload_policy: 'compact_redacted'", server)

    def test_stack_start_defers_camera_hub_ready_wait_until_after_spawn(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("$mediapipeCameraHubChild = $null", stack_start)
        self.assertIn("$delayedVisionSnapshotSpecs = @()", stack_start)
        self.assertIn('$spec.Name -eq "vision_snapshot_processor"', stack_start)
        self.assertIn("$mediapipeCameraHubChild = $children[-1]", stack_start)
        self.assertIn("if ($null -ne $mediapipeCameraHubChild)", stack_start)
        self.assertIn("foreach ($spec in $delayedVisionSnapshotSpecs)", stack_start)

    def test_thought_core_no_provider_option_flows_to_child_after_env_import(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")
        system = read_system_script()
        stack_start = read_stack_start_script()
        thought_start = read_thought_core_start_script()

        self.assertIn("ThoughtCoreNoProvider: false", server)
        self.assertIn("'ThoughtCoreNoProvider'", app)
        self.assertIn("Thought Core fallback-only", app)
        self.assertIn("[switch]$ThoughtCoreNoProvider", system)
        self.assertIn("-ThoughtCoreNoProvider", system)
        self.assertIn("[switch]$ThoughtCoreNoProvider", stack_start)
        self.assertIn('"THOUGHT_CORE_FORCE_NO_PROVIDER"', stack_start)
        self.assertIn('$thoughtCoreEnvironment["THOUGHT_CORE_LLM_ENABLED"] = "0"', stack_start)
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"',
            stack_start,
        )
        import_index = thought_start.index("Import-SwordEnv")
        force_index = thought_start.index("THOUGHT_CORE_FORCE_NO_PROVIDER")
        self.assertLess(import_index, force_index)
        self.assertIn('$env:THOUGHT_CORE_LLM_ENABLED = "0"', thought_start)
        self.assertIn('$env:THOUGHT_CORE_ACTION_LLM_ENABLED = "0"', thought_start)

    def test_launcher_blocks_unknown_profile_parser_paths_before_stack_start(self) -> None:
        server = read_launcher_server()

        self.assertIn("const requireKnownProfile", server)
        self.assertIn("unknown_profile", server)
        self.assertIn("blocked_unknown_profile", server)
        self.assertIn("requestedProfileClass: compactProfileId(profileId)", server)
        self.assertIn("const profileError = requireKnownProfile(profileId)", server)
        self.assertIn("if (!preview.ok)", server)
        self.assertIn("sendJson(response, 400, profileError)", server)
        self.assertIn("profileConfigState", server)

    def test_launcher_profiles_keep_skip_enabled_combinations_explicit(self) -> None:
        profiles = {profile["id"]: profile for profile in read_launcher_profiles()}

        thought_core = profiles["thought-core-v0"]["options"]
        self.assertTrue(thought_core["SkipDify"])
        self.assertTrue(thought_core["SkipDifyWatch"])
        self.assertTrue(thought_core["EnableThoughtCore"])
        self.assertTrue(thought_core["EnableThoughtCoreWatch"])

        camera_debug = profiles["camera-debug"]["options"]
        self.assertTrue(camera_debug["SkipHomeAssistantBridge"])
        self.assertTrue(camera_debug["SkipAituber"])
        self.assertTrue(camera_debug["SkipTouchDesignerGui"])
        self.assertFalse(camera_debug["MediapipeNoBrowser"])
        self.assertTrue(camera_debug["MediapipeOpenBrowser"])

        aituber_only = profiles["aituber-only"]["options"]
        self.assertTrue(aituber_only["SkipHomeAssistantBridge"])
        self.assertTrue(aituber_only["SkipEnvironmentState"])
        self.assertTrue(aituber_only["SkipMediapipe"])
        self.assertTrue(aituber_only["SkipVisionSnapshotProcessor"])
        self.assertTrue(aituber_only["SkipTouchDesignerGui"])

    def test_launcher_config_status_uses_compact_redacted_classes(self) -> None:
        server = read_launcher_server()

        self.assertIn("compactProfileId", server)
        self.assertIn("knownProfileIds: profileIds()", server)
        self.assertIn("homeControlConfigProfileFromPath", server)
        self.assertIn("payload_policy: 'compact_redacted'", server)
        self.assertIn("live_home_invalid", server)
        self.assertNotIn("requestedProfileId: profileId", server)
