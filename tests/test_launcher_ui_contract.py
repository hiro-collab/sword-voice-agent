import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT.parents[1]
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
LAUNCHER_PROFILES = ROOT / "tools" / "home-control-launcher" / "config" / "default-profiles.json"
DEMO_SAFE_DEFAULTS = PRODUCT_ROOT / "manifests" / "demo-safe-settings" / "defaults.json"
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


def read_demo_safe_defaults() -> dict:
    payload = json.loads(DEMO_SAFE_DEFAULTS.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8")


def read_system_script() -> str:
    return SYSTEM_SCRIPT.read_text(encoding="utf-8")


def read_thought_core_start_script() -> str:
    return THOUGHT_CORE_START_SCRIPT.read_text(encoding="utf-8")


def extract_between(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


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
        self.assertIn("Provider", html)
        self.assertIn("Advanced overrides", html)
        self.assertIn("Start Stack starts enabled/expected services only.", html)
        self.assertIn('id="launch-scope-enabled"', html)
        self.assertIn('id="launch-scope-skipped"', html)

    def test_launcher_exposes_demo_safe_settings_without_claiming_proof(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()

        self.assertIn('id="demo-safe-summary"', html)
        self.assertIn('id="demo-safe-drawer-summary"', html)
        self.assertIn('id="demo-safe-settings-list"', html)
        self.assertIn("Demo settings", html)

        self.assertIn("demoSafeSettings", app)
        self.assertIn("demoReadinessStatus", app)
        self.assertIn("const renderDemoSafeSettings", app)
        self.assertIn("const currentDemoSafeSettings", app)
        self.assertIn("max_duration_sec", app)
        self.assertIn("does_not_prove", app)

        self.assertIn("DEMO_SAFE_SETTINGS_FILE", server)
        self.assertIn("effectiveDemoSafeSettings", server)
        self.assertIn("demoReadinessStatus", server)
        self.assertIn("launcher_state_dir_gitignored_demo_settings_json", server)

    def test_demo_safe_defaults_start_disabled_and_separate_readiness(self) -> None:
        defaults = read_demo_safe_defaults()
        rows = defaults["rows"]
        row_ids = {row["id"] for row in rows}

        self.assertEqual(defaults["schema_version"], "demo_safe_settings.v0")
        self.assertFalse(defaults["fresh_clone_default_enabled"])
        self.assertTrue(rows)
        self.assertTrue(all(row["enabled"] is False for row in rows))

        self.assertIn("appliance.aircon_cool_restore", row_ids)
        self.assertIn("audio.voicevox_local_speech", row_ids)
        self.assertIn("audio.browser_or_pc_output_awareness", row_ids)
        self.assertIn("avatar.aituber_projection_surface", row_ids)
        self.assertIn("avatar.expression_or_motion_request", row_ids)
        self.assertIn("display.projection_visual_mode", row_ids)
        self.assertIn("display.self_mirror_visible_motion", row_ids)

        for row in rows:
            self.assertIn("restore_required", row)
            self.assertIn("max_action_count", row)
            self.assertIn("max_duration_sec", row)
            self.assertIn("proof_ceiling", row)
            self.assertIn("does_not_prove", row)

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
        self.assertIn("const summarizeLaunchScope", app)
        self.assertIn("Duplicate port values", app)
        self.assertIn("Check conflict", app)
        self.assertIn("services-summary", app)
        self.assertIn("launch-scope-enabled", app)
        self.assertIn("launch-scope-skipped", app)
        self.assertIn("ports-drawer-summary", app)

    def test_launcher_switches_read_as_positive_start_scope(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn("Start Stack starts enabled/expected services only.", html)
        self.assertIn("const displaySwitchValue", app)
        self.assertIn("const setSwitchValue", app)
        self.assertIn("field.startsWith('Skip') ? !state.options[field]", app)
        self.assertIn("field.startsWith('Skip') ? !checked : checked", app)
        self.assertIn("Start expression UI", app)
        self.assertIn("Start action bridge", app)
        self.assertIn("Start environment state", app)
        self.assertIn("Start reflex sensor", app)
        self.assertIn("Start vision snapshot", app)
        self.assertIn("Start display runtime GUI", app)
        self.assertIn("Require VOICEVOX readiness check", app)
        self.assertNotIn("Disable expression UI", app)
        self.assertNotIn("Disable action bridge", app)

    def test_launcher_review_ui_has_no_legacy_compatibility_controls(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertNotIn("compatibility-switch-grid", html)
        self.assertNotIn("runtime-drawer-summary", html)
        self.assertNotIn("renderSwitchGroup('compatibility-switch-grid'", app)
        self.assertNotIn("Legacy paths active", app)
        self.assertNotIn("Legacy paths off", app)

    def test_launcher_public_ui_supports_english_and_japanese_language_mode(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn('id="language-switch"', html)
        self.assertIn('data-language="en"', html)
        self.assertIn('data-language="ja"', html)
        self.assertIn('data-i18n="launch.title"', html)
        self.assertIn('data-i18n="launchScope.statement"', html)
        self.assertIn('data-i18n="quickLinks.title"', html)
        self.assertIn('data-i18n="command.title"', html)
        self.assertIn('data-i18n="log.title"', html)

        self.assertIn("const LANGUAGE_STORAGE_KEY = 'sword.launcher.language'", app)
        self.assertIn("const translations", app)
        self.assertIn("document.documentElement.lang = state.language", app)
        self.assertIn("window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)", app)
        self.assertIn("'launch.title': 'Launch configuration'", app)
        self.assertIn("'launch.title': '起動設定'", app)
        self.assertIn("'button.start': 'Start Stack'", app)
        self.assertIn("'button.start': '起動する'", app)
        self.assertIn("'launchScope.statement': 'Start Stack starts enabled/expected services only.'", app)
        self.assertIn("'launchScope.statement': 'Start Stack は有効な起動対象だけを開始します。'", app)
        self.assertIn("'service.header.target': 'Start target'", app)
        self.assertIn("'service.header.target': '起動対象'", app)
        self.assertIn("'quickLinks.title': '確認リンク'", app)
        self.assertIn("'command.title': '起動コマンド確認'", app)
        self.assertIn("'log.title': 'ランチャー記録'", app)

    def test_launcher_language_mode_preserves_technical_values(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn('data-i18n="port.expression">Expression</span><input id="AituberPort"', html)
        self.assertIn('data-i18n="port.thoughtCore">Thought Core</span><input id="ThoughtCorePort"', html)
        self.assertIn("<span>VOICEVOX URL</span>", html)
        self.assertIn('id="HomeControlConfigPath"', html)
        self.assertIn("$('command-preview').textContent = formatReviewCommandPreview(commandLine)", app)

    def test_launcher_japanese_copy_uses_meaning_first_labels(self) -> None:
        app = read_public("app.js")

        ja_table = extract_between(app, "  ja: {", "  }\n}\n\nconst t =")
        service_labels_ja = extract_between(app, "const serviceLabelsJa = {", "}\nconst hiddenServiceKeys")
        launch_scope_labels_ja = extract_between(app, "const launchScopeLabelsJa = {", "}\n\nconst fieldLabels")
        field_labels_ja = extract_between(app, "const fieldLabelsJa = {", "}\n\nconst switchDescriptions")
        switch_descriptions_ja = extract_between(app, "const switchDescriptionsJa = {", "}\n\nconst positiveDisplayFields")
        endpoint_labels_ja = extract_between(app, "  const labelsJa = {", "  }\n  if (state.language === 'ja')")

        self.assertIn("'port.thoughtCore': '思考中枢'", ja_table)
        self.assertIn("'summary.fallbackOnly': '簡易応答のみ'", ja_table)
        self.assertIn("'summary.providerAllowed': '会話LLMを使用'", ja_table)
        self.assertIn("thought_core_api: '思考中枢API'", service_labels_ja)
        self.assertIn("thought_core_watcher: '思考中枢の監視'", service_labels_ja)
        self.assertIn("vision_snapshot_processor: '視覚状態の取得'", service_labels_ja)
        self.assertIn("EnableThoughtCore: '思考中枢API'", launch_scope_labels_ja)
        self.assertIn("SkipVisionSnapshotProcessor: '視覚状態の取得'", launch_scope_labels_ja)
        self.assertIn("EnableThoughtCore: '思考中枢APIを起動'", field_labels_ja)
        self.assertIn("SkipVisionSnapshotProcessor: '視覚状態の取得を起動'", field_labels_ja)
        self.assertIn("外部LLMを使わない簡易応答のみ", switch_descriptions_ja)
        self.assertIn("'Thought Core health': '思考中枢の状態'", endpoint_labels_ja)
        self.assertIn("'Vision Snapshot Processor WebSocket': '視覚状態取得WebSocket'", endpoint_labels_ja)

        for japanese_block in [
            ja_table,
            service_labels_ja,
            launch_scope_labels_ja,
            field_labels_ja,
            switch_descriptions_ja,
            endpoint_labels_ja,
        ]:
            self.assertNotIn("ソート", japanese_block)
            self.assertNotIn("ビジョンスナップショット", japanese_block)
            self.assertNotIn("フォールバック", japanese_block)
            self.assertNotIn("プロバイダー", japanese_block)

    def test_launcher_command_preview_uses_review_formatter(self) -> None:
        app = read_public("app.js")

        self.assertIn("const formatReviewCommandPreview", app)
        self.assertIn("const setCommandPreview", app)
        self.assertIn("setCommandPreview(preview.commandLine)", app)
        self.assertIn("setCommandPreview(payload.preview?.commandLine || '')", app)
        self.assertIn(
            "$('command-preview').textContent = formatReviewCommandPreview(commandLine)",
            app,
        )
        self.assertNotIn("$('command-preview').textContent = commandLine", app)

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

    def test_service_rows_expose_startup_target_without_claiming_runtime_state(self) -> None:
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn("const serviceStartupTargetFields", app)
        self.assertIn("const startupTargetFieldsByService", app)
        self.assertIn("const serviceStartupTargetBlockers", app)
        self.assertIn("vision_snapshot_processor: ['SkipVisionSnapshotProcessor']", app)
        self.assertIn("voicevox: ['SkipVoicevoxCheck']", app)
        self.assertIn("t('service.requires', { targets: targetBlockers.join(', ') })", app)
        self.assertIn("const setServiceStartupTarget", app)
        self.assertIn('data-service-startup-target="${escapeHtml(name)}"', app)
        self.assertIn('data-startup-target="${included ? \'included\' : \'skipped\'}"', app)
        self.assertIn("Startup target only; current runtime state is unchanged.", app)
        self.assertIn("renderControls()", app)
        self.assertIn("renderLaunchSummary()", app)
        self.assertIn("renderServices(state.latestServices || {})", app)
        self.assertIn("Start target", app)
        self.assertIn(".service-startup-target", css)
        self.assertIn(".service-target-toggle", css)
        self.assertIn('.service-row[data-startup-target="skipped"]', css)

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
        self.assertIn("Use configured conversation LLM", app)
        self.assertIn("configured Thought Core LLM provider", app)
        self.assertIn("local fallback-only mode", app)
        self.assertNotIn("Use OpenAI-compatible LLM responses", app)
        self.assertIn("const positiveDisplayFields = new Set(['ThoughtCoreNoProvider'])", app)
        self.assertIn("positiveDisplayFields.has(field)", app)
        self.assertIn("setOption(field, !checked)", app)
        self.assertNotIn("Force Thought Core fallback-only", app)
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

    def test_launcher_environment_status_whitelists_action_readiness_without_raw_ha_fields(self) -> None:
        server = read_launcher_server()
        action_compactor = extract_between(
            server,
            "const compactActionReadiness = (action) =>",
            "const compactActionReadinessSummary = (summary) =>",
        )
        summary_compactor = extract_between(
            server,
            "const compactActionReadinessSummary = (summary) =>",
            "const compactEnvironmentForLauncherStatus = (indicatorPayload) =>",
        )

        self.assertIn("const compactActionReadiness = (action) =>", server)
        self.assertIn("const compactActionReadinessSummary = (summary) =>", server)
        self.assertIn("action_readiness", server)
        self.assertIn("'proof_ceiling'", action_compactor)
        self.assertIn("'live_test_readiness'", action_compactor)
        self.assertIn("'live_test_blockers'", action_compactor)
        self.assertIn("'restore_action_id'", action_compactor)
        self.assertIn("'stop_action_id'", action_compactor)
        self.assertIn("'test_now_count'", summary_compactor)
        self.assertIn("'blocked_candidate_count'", summary_compactor)
        self.assertNotIn("'expected_effect'", action_compactor)
        self.assertNotIn("'entity_id'", action_compactor)
        self.assertNotIn("'domain'", action_compactor)
        self.assertNotIn("'service'", action_compactor)
        self.assertNotIn("'HOME_ASSISTANT_TOKEN'", action_compactor)
        self.assertNotIn("'expected_effect'", summary_compactor)
        self.assertNotIn("'entity_id'", summary_compactor)
        self.assertNotIn("'HOME_ASSISTANT_TOKEN'", summary_compactor)
