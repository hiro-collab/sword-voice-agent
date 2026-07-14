import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT.parents[1]
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
LAUNCHER_PROFILES = ROOT / "tools" / "home-control-launcher" / "config" / "default-profiles.json"
TIMING_COLLECTOR = ROOT / "tools" / "home-control-launcher" / "scripts" / "collect-demo-timing.mjs"
DEMO_SAFE_DEFAULTS = PRODUCT_ROOT / "manifests" / "demo-safe-settings" / "defaults.json"
STACK_START_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
LAUNCHER_START_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-launcher.ps1"
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


def read_timing_collector() -> str:
    return TIMING_COLLECTOR.read_text(encoding="utf-8")


def read_demo_safe_defaults() -> dict:
    payload = json.loads(DEMO_SAFE_DEFAULTS.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8")


def read_launcher_start_script() -> str:
    return LAUNCHER_START_SCRIPT.read_text(encoding="utf-8")


def read_system_script() -> str:
    return SYSTEM_SCRIPT.read_text(encoding="utf-8")


def read_thought_core_start_script() -> str:
    return THOUGHT_CORE_START_SCRIPT.read_text(encoding="utf-8")


def extract_between(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


class LauncherUiContractTest(TestCase):
    def test_launcher_reuse_requires_same_workspace_launcher_owner(self) -> None:
        script = read_launcher_start_script()

        self.assertIn("Get-LauncherListeners", script)
        self.assertIn("IsLauncher", script)
        self.assertIn("WorkspaceMatches", script)
        self.assertIn("Refusing to reuse it", script)

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

    def test_launcher_selects_conversation_provider_without_rewriting_env(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()
        system = read_system_script()
        stack = read_stack_start_script()

        self.assertIn('id="ThoughtCoreLlmProvider"', html)
        for provider in ("configured", "openai-compatible", "codex-cli", "codex-cli-luna"):
            self.assertIn(f'value="{provider}"', html)
            self.assertIn(provider, server)
        self.assertIn("ThoughtCoreLlmProvider", app)
        bind_controls = extract_between(app, "const bindControls = () => {", "const showError =")
        self.assertIn("$('ThoughtCoreLlmProvider').addEventListener('change'", bind_controls)
        self.assertIn("ThoughtCoreLlmProvider", system)
        self.assertIn("ThoughtCoreLlmProvider", stack)
        self.assertIn('"gpt-5.6-terra"', stack)
        self.assertIn('"gpt-5.6-luna"', stack)
        self.assertIn('"medium"', stack)
        self.assertIn('"low"', stack)
        self.assertIn(
            '$ThoughtCoreLlmProvider -in @("codex-cli", "codex-cli-luna")',
            stack,
        )
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_LLM_PROVIDER"] = $thoughtCoreRuntimeProvider',
            stack,
        )
        self.assertIn(
            'if ($ThoughtCoreLlmProvider -eq "codex-cli-luna")',
            stack,
        )
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_LLM_VISIBLE_SPEECH_ENABLED"] = "1"',
            stack,
        )
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_REQUIRE_LLM_VISIBLE_SPEECH"] = "0"',
            stack,
        )
        self.assertIn('"respond"', stack)
        self.assertIn('"read-only"', stack)
        self.assertIn('"never"', stack)
        self.assertIn('"true"', stack)

    def test_launcher_routes_streamcam_capture_request_without_claiming_achieved_fps(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()
        system = read_system_script()
        stack = read_stack_start_script()

        for field in (
            "MediapipeCameraName",
            "MediapipeCameraWidth",
            "MediapipeCameraHeight",
            "MediapipeCameraFps",
            "MediapipeCameraInputCodec",
        ):
            self.assertIn(f'id="{field}"', html)
            self.assertIn(field, app)
            self.assertIn(field, server)
            self.assertIn(field, system)
            self.assertIn(field, stack)

        self.assertIn("Logitech StreamCam", server)
        self.assertIn("MediapipeCameraWidth: 1920", server)
        self.assertIn("MediapipeCameraHeight: 1080", server)
        self.assertIn("MediapipeCameraFps: 30", server)
        self.assertIn("MediapipeCameraInputCodec: 'mjpeg'", server)
        self.assertIn("MediapipeCameraWidth: { min: 160, max: 3840 }", server)
        self.assertIn("MediapipeCameraHeight: { min: 120, max: 2160 }", server)
        self.assertIn("MediapipeCameraFps: { min: 1, max: 120 }", server)
        self.assertIn("numberValue >= limits.min && numberValue <= limits.max", server)
        self.assertIn("[ValidateRange(160, 3840)]", system)
        self.assertIn("[ValidateRange(120, 2160)]", system)
        self.assertIn("[ValidateRange(1, 120)]", system)
        self.assertIn("[ValidateRange(160, 3840)]", stack)
        self.assertIn("[ValidateRange(120, 2160)]", stack)
        self.assertIn("[ValidateRange(1, 120)]", stack)
        self.assertIn("runtime diagnostics remain the authority for achieved FPS", app)
        self.assertIn('"--ffmpeg-input-codec"', stack)
        self.assertIn('$MediapipeCameraInputCodec', stack)

    def test_launcher_docs_name_streamcam_request_without_60_fps_claim(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Logitech StreamCam", readme)
        self.assertIn("実際の解像度/FPS", readme)
        self.assertNotIn("現在の既定例は `HD Pro Webcam C920`", readme)

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
        self.assertIn("action_ids", app)
        self.assertIn("timing_estimate_sec", app)
        self.assertIn("max_duration_sec", app)
        self.assertIn("does_not_prove", app)

        self.assertIn("DEMO_SAFE_SETTINGS_FILE", server)
        self.assertIn("effectiveDemoSafeSettings", server)
        self.assertIn("demoReadinessStatus", server)
        self.assertIn("feedback_stimulus_class", server)
        self.assertIn("timing_estimate_source_class", server)
        self.assertIn("launcher_state_dir_gitignored_demo_settings_json", server)

    def test_launcher_exposes_no_live_diagnostic_surfaces_and_startup_timing(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()

        self.assertIn('id="startup-timing-list"', html)
        self.assertIn('id="diagnostic-surface-list"', html)
        self.assertIn("Startup timing", html)
        self.assertIn("Diagnostic surfaces", html)

        self.assertIn("startupTiming", app)
        self.assertIn("diagnosticSurfaces", app)
        self.assertIn("renderStartupTiming", app)
        self.assertIn("renderDiagnosticSurfaces", app)
        self.assertIn("formatReadyTimeout", app)
        self.assertIn("startup.maxWait", app)
        self.assertIn("startup.maxWaitUnset", app)
        self.assertIn("startup-timing-table", app)
        self.assertIn("startup-timing-header", app)
        self.assertIn("startup-timeout-input", app)
        self.assertIn("data-ready-timeout-field", app)
        self.assertIn("readyTimeoutOptionFields", app)
        self.assertIn("setReadyTimeoutOption", app)
        self.assertIn("VoicevoxReadyTimeoutSeconds", app)
        self.assertIn("MediapipeReadyTimeoutSeconds", app)
        self.assertNotIn("advanced.voicevoxReadyTimeout", app)
        self.assertNotIn('id="VoicevoxReadyTimeoutSeconds"', html)
        self.assertNotIn('id="MediapipeReadyTimeoutSeconds"', html)
        self.assertIn("numericOptionFields", app)

        self.assertIn("launcher_startup_timing.v0", server)
        self.assertIn("timelineEvents", server)
        self.assertIn("startupTimingEvents", server)
        self.assertIn("launcher_start_accepted", server)
        self.assertIn("service_first_ready", server)
        self.assertIn("service_waiting", server)
        self.assertIn("criticalPathServiceId", server)
        self.assertIn("startupReadyTimeoutMsForService", server)
        self.assertIn("readyTimeoutMs", server)
        self.assertIn("explicit_service_ready_timeout", server)
        self.assertIn("no_explicit_service_ready_timeout", server)
        self.assertIn("diagnosticSurfacesSummary", server)
        self.assertIn("getStartupTimingPayload", server)
        self.assertIn("getDiagnosticSurfacesPayload", server)
        self.assertIn("demoTimedActionReadiness", server)
        self.assertIn("/api/startup-timing", server)
        self.assertIn("/api/diagnostic-surfaces", server)
        self.assertIn("/api/demo-timed-action-readiness", server)
        self.assertIn("launcher_demo_timed_action_readiness.v0", server)
        self.assertIn("ready_for_reviewed_first_action_handoff", server)
        self.assertIn("remaining_ms_to_first_action_target", server)
        self.assertIn("next_operator_steps", server)
        self.assertIn("foreground_projection_visual", server)
        self.assertIn("submit_non_appliance_preface", server)
        self.assertIn("select_reviewed_ac_action_or_hold", server)
        self.assertIn("aircon_cool", server)
        self.assertIn("aircon_hvac_off", server)
        self.assertIn("latency_bottleneck_hints", server)
        self.assertIn("command_submission_authorized_by_this_summary: false", server)
        self.assertIn("Action bridge operator", server)
        self.assertIn("/operator", server)
        self.assertIn("Action bridge operator", app)
        self.assertIn("家電操作面", app)
        self.assertIn("source_static_diagnostic_surface_inventory.v0", server)
        self.assertIn("audio_input_awareness", server)
        self.assertIn("self_mirror_temporal_motion", server)
        self.assertIn("projection_visual_response_binding", server)
        self.assertIn("message_receiver_client_binding_status_summary", server)
        self.assertIn("os_display_window_prompt", server)
        self.assertIn("live_capture_default_class: 'disabled'", server)
        self.assertIn("raw_private_publication_flags: false", server)

    def test_launcher_readme_documents_fast_timing_and_no_live_diagnostics(self) -> None:
        readme = (ROOT / "tools" / "home-control-launcher" / "README.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("VoicevoxReadyTimeoutSeconds", readme)
        self.assertIn("MediapipeReadyTimeoutSeconds", readme)
        self.assertIn("8-second wait", readme)
        self.assertIn("GET /api/startup-timing", readme)
        self.assertIn("launcher_startup_timing.v0", readme)
        self.assertIn("timeline events", readme)
        self.assertIn("GET /api/diagnostic-surfaces", readme)
        self.assertIn("GET /api/demo-timed-action-readiness", readme)
        self.assertIn("first-feedback/first-action readiness", readme)
        self.assertIn("remaining milliseconds", readme)
        self.assertIn("next operator steps", readme)
        self.assertIn("aircon_cool", readme)
        self.assertIn("aircon_hvac_off", readme)
        self.assertIn("collect-demo-timing.mjs", readme)
        self.assertIn("polls only the Launcher summary endpoints", readme)
        self.assertIn("Self Mirror temporal motion", readme)
        self.assertIn("Action bridge operator", readme)
        self.assertIn("not command authority", readme)
        self.assertIn("class/count/timing summaries only", readme)
        self.assertIn("do not perform", readme)
        self.assertIn("Home Control", readme)

    def test_timing_collector_is_read_only_summary_collector(self) -> None:
        collector = read_timing_collector()

        self.assertIn("launcher_demo_timing_snapshot.v0", collector)
        self.assertIn("/api/demo-timed-action-readiness", collector)
        self.assertIn("/api/startup-timing", collector)
        self.assertIn("/api/diagnostic-surfaces", collector)
        self.assertIn("ready_for_reviewed_first_action_handoff", collector)
        self.assertIn("command_submission_count: 0", collector)
        self.assertIn("raw_private_publication_flags: false", collector)
        self.assertIn("not_home_assistant_or_home_control_operation", collector)
        self.assertNotIn("`${baseUrl}/api/start`", collector)
        self.assertNotIn("`${baseUrl}/api/stop`", collector)
        self.assertNotIn("/operator/execute", collector)

    def test_demo_safe_defaults_start_disabled_and_separate_readiness(self) -> None:
        defaults = read_demo_safe_defaults()
        rows = defaults["rows"]
        row_ids = {row["id"] for row in rows}

        self.assertEqual(defaults["schema_version"], "demo_safe_settings.v0")
        self.assertFalse(defaults["fresh_clone_default_enabled"])
        self.assertTrue(rows)
        self.assertTrue(all(row["enabled"] is False for row in rows))

        self.assertIn("appliance.aircon_cool_restore", row_ids)
        self.assertIn("appliance.light_command_stimulus", row_ids)
        self.assertIn("appliance.fan_command_stimulus", row_ids)
        self.assertIn("appliance.door_open_close", row_ids)
        self.assertIn("appliance.vacuum_start_return", row_ids)
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

    def test_demo_safe_defaults_include_all_appliance_command_stimuli(self) -> None:
        defaults = read_demo_safe_defaults()
        rows = {row["id"]: row for row in defaults["rows"]}

        expected_sequences = {
            "appliance.aircon_cool_restore": ["aircon_cool", "aircon_hvac_off"],
            "appliance.light_command_stimulus": ["light_on"],
            "appliance.fan_command_stimulus": ["fan_on"],
            "appliance.door_open_close": ["door_open", "door_close"],
            "appliance.vacuum_start_return": ["vacuum_start", "vacuum_return"],
        }
        for row_id, action_ids in expected_sequences.items():
            with self.subTest(row_id=row_id):
                row = rows[row_id]
                self.assertEqual(row["area"], "appliance")
                self.assertEqual(row["action_ids"], action_ids)
                self.assertTrue(row["feedback_stimulus_class"].startswith("appliance_command_stimulus"))
                self.assertIn("state_requirement_class", row)
                self.assertGreater(row["timing_estimate_sec"], 0)
                self.assertTrue(row["measurement_required"])

        self.assertFalse(rows["appliance.light_command_stimulus"]["restore_required"])
        self.assertFalse(rows["appliance.fan_command_stimulus"]["restore_required"])
        self.assertTrue(rows["appliance.door_open_close"]["restore_required"])
        self.assertTrue(rows["appliance.vacuum_start_return"]["restore_required"])

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

    def test_launcher_log_view_groups_entries_without_breaking_plain_text_copy(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn('class="log-meta-strip"', html)
        self.assertIn('id="log-output" class="log-output"', html)
        self.assertIn("'log.viewMode': 'Grouped by module'", app)
        self.assertIn("'log.copyMode': 'Copy keeps plain text'", app)
        self.assertIn("'log.viewMode': '機能別に整理'", app)
        self.assertIn("'log.copyMode': 'コピーは通常テキスト'", app)
        self.assertIn("latestLogTailRaw", app)
        self.assertIn("const parseLauncherLogLine", app)
        self.assertIn("const renderLauncherLog", app)
        self.assertIn("const prependLauncherLog", app)
        self.assertIn("renderLauncherLog(payload.logTail)", app)
        self.assertIn("renderLauncherLog(logs.logTail)", app)
        self.assertIn("navigator.clipboard.writeText(state.latestLogTailRaw", app)
        self.assertIn(".log-entry-source", css)
        self.assertIn(".log-entry-message", css)
        self.assertIn("user-select: text", css)

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

    def test_launcher_status_uses_lightweight_action_bridge_probe(self) -> None:
        server = read_launcher_server()

        self.assertIn(
            "`http://127.0.0.1:${options.HomeAssistantBridgePort}/operator`",
            server,
        )
        self.assertNotIn(
            "`http://127.0.0.1:${options.HomeAssistantBridgePort}/health`,\n      2500",
            server,
        )

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

        self.assertIn("Get-ReclaimableRootForPortConflict", stack_start)
        self.assertIn("Stop-ReclaimablePortConflicts", stack_start)
        self.assertIn("Test-ExternalProcessDenied", stack_start)

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
        self.assertIn("$mediapipeCameraHubChild = $rootChild", stack_start)
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

        demo_fast = profiles["demo-fast"]["options"]
        self.assertFalse(demo_fast["StopExisting"])
        self.assertTrue(demo_fast["EnableThoughtCore"])
        self.assertFalse(demo_fast["EnableThoughtCoreWatch"])
        self.assertTrue(demo_fast["ThoughtCoreNoProvider"])
        self.assertEqual(demo_fast["VoicevoxReadyTimeoutSeconds"], 8)
        self.assertTrue(demo_fast["SkipHomeAssistantBridge"])
        self.assertTrue(demo_fast["SkipEnvironmentState"])
        self.assertTrue(demo_fast["SkipMediapipe"])
        self.assertTrue(demo_fast["SkipVisionSnapshotProcessor"])
        self.assertTrue(demo_fast["SkipTouchDesignerGui"])

        demo_fast_action = profiles["demo-fast-action"]["options"]
        self.assertFalse(demo_fast_action["StopExisting"])
        self.assertTrue(demo_fast_action["EnableThoughtCore"])
        self.assertFalse(demo_fast_action["EnableThoughtCoreWatch"])
        self.assertTrue(demo_fast_action["ThoughtCoreNoProvider"])
        self.assertEqual(demo_fast_action["VoicevoxReadyTimeoutSeconds"], 8)
        self.assertNotIn("SkipHomeAssistantBridge", demo_fast_action)
        self.assertTrue(demo_fast_action["SkipEnvironmentState"])
        self.assertTrue(demo_fast_action["SkipMediapipe"])
        self.assertTrue(demo_fast_action["SkipVisionSnapshotProcessor"])
        self.assertTrue(demo_fast_action["SkipTouchDesignerGui"])

        camera_debug = profiles["camera-debug"]["options"]
        self.assertTrue(camera_debug["SkipHomeAssistantBridge"])
        self.assertTrue(camera_debug["SkipEnvironmentState"])
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

    def test_launcher_passes_readiness_timeouts_to_stack_scripts(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")
        system = read_system_script()
        stack_start = read_stack_start_script()

        self.assertIn("VoicevoxReadyTimeoutSeconds: 45", server)
        self.assertIn("MediapipeReadyTimeoutSeconds: 90", server)
        self.assertIn("'VoicevoxReadyTimeoutSeconds'", server)
        self.assertIn("'MediapipeReadyTimeoutSeconds'", server)
        self.assertIn("options.VoicevoxReadyTimeoutSeconds", server)
        self.assertIn("options.MediapipeReadyTimeoutSeconds", server)
        self.assertIn("addSupportedParam", server)
        self.assertIn("numericOptionFields", app)
        self.assertIn("readyTimeoutOptionFields", app)
        self.assertIn("setReadyTimeoutOption", app)
        self.assertIn("[int]$VoicevoxReadyTimeoutSeconds = 45", system)
        self.assertIn("[int]$MediapipeReadyTimeoutSeconds = 90", system)
        self.assertIn("-VoicevoxReadyTimeoutSeconds", system)
        self.assertIn("-MediapipeReadyTimeoutSeconds", system)
        self.assertIn("[int]$VoicevoxReadyTimeoutSeconds = 45", stack_start)
        self.assertIn("[int]$MediapipeReadyTimeoutSeconds = 90", stack_start)
        self.assertIn("Assert-VoicevoxReady", stack_start)
        self.assertIn("-TimeoutSeconds $VoicevoxReadyTimeoutSeconds", stack_start)
        self.assertIn("Wait-CameraHubStackReady", stack_start)
        self.assertIn("-TimeoutSeconds $MediapipeReadyTimeoutSeconds", stack_start)

    def test_launcher_stack_log_strips_ansi_control_sequences(self) -> None:
        server = read_launcher_server()

        self.assertIn("stripAnsiControlSequences", server)
        self.assertIn("NO_COLOR: '1'", server)
        self.assertIn("FORCE_COLOR: '0'", server)
        self.assertIn("TERM: 'dumb'", server)
        self.assertIn("fs.appendFileSync(STACK_LOG_FILE, sanitizedContent, 'utf8')", server)
        self.assertIn("return stripAnsiControlSequences(buffer.toString('utf8'))", server)

    def test_launcher_config_status_uses_compact_redacted_classes(self) -> None:
        server = read_launcher_server()

        self.assertIn("compactProfileId", server)
        self.assertIn("knownProfileIds: profileIds()", server)
        self.assertIn("homeControlConfigProfileFromPath", server)
        self.assertIn("payload_policy: 'compact_redacted'", server)
        self.assertIn("live_home_invalid", server)
        self.assertNotIn("requestedProfileId: profileId", server)

    def test_launcher_environment_status_does_not_keep_stale_action_readiness_payload(self) -> None:
        server = read_launcher_server()

        self.assertNotIn("const compactActionReadiness = (action) =>", server)
        self.assertNotIn("const compactActionReadinessSummary = (summary) =>", server)
        self.assertNotIn("'live_test_readiness'", server)
        self.assertNotIn("'live_test_blockers'", server)
        self.assertNotIn("'restore_action_id'", server)
        self.assertNotIn("'stop_action_id'", server)
        self.assertNotIn("'test_now_count'", server)
        self.assertNotIn("'blocked_candidate_count'", server)
        self.assertNotIn("'HOME_ASSISTANT_TOKEN'", server)
