from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"


def read_public(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


def read_launcher_server() -> str:
    return LAUNCHER_SERVER.read_text(encoding="utf-8")


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

    def test_quick_links_use_display_safe_environment_endpoint(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")

        self.assertIn("Environment display state", server)
        self.assertIn("/indicators/current", server)
        self.assertNotIn("name: 'Environment current state'", server)
        self.assertNotIn("/environment/current`,", server)
        self.assertIn("'Environment display state': 'Env state'", app)
