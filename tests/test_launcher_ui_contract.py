from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
STACK_START_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"


def read_public(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


def read_launcher_server() -> str:
    return LAUNCHER_SERVER.read_text(encoding="utf-8")


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8")


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
