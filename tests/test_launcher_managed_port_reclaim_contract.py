import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"


def read_launcher_server() -> str:
    return LAUNCHER_SERVER.read_text(encoding="utf-8")


def read_public(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


class LauncherManagedPortReclaimContractTest(unittest.TestCase):
    def test_stop_reclaims_only_route_owned_managed_port_residue(self) -> None:
        server = read_launcher_server()

        self.assertIn("reclaimManagedPortResidue", server)
        self.assertIn("MANAGED_PORT_RECLAIM_POLICIES", server)
        self.assertIn("listeningPortProcessOwners", server)
        self.assertIn("isReclaimableManagedPortOwner", server)
        self.assertIn("route_owned_managed_port_residue", server)
        self.assertIn("HOME_CONTROL_LAUNCHER_MANAGED_PORT_RECLAIM", server)
        self.assertIn("EXTERNAL_PROCESS_DENY_LIST", server)
        self.assertIn("home_assistant_bridge:", server)
        self.assertIn("mediapipe_rtsp:", server)
        self.assertIn("touchdesigner_control_gui:", server)
        self.assertIn("thought_core_api:", server)
        self.assertIn("commandLineContainsPath(commandLine, policy.rootPath)", server)
        self.assertIn("Stop-Process -Id ${numericPid} -Force", server)
        self.assertIn("const managedPortReclaim = await reclaimManagedPortResidue(options)", server)
        self.assertIn("managedPortReclaim,", server)

    def test_launcher_ui_exposes_manual_managed_port_recovery(self) -> None:
        server = read_launcher_server()
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn("/api/reclaim-managed-ports", server)
        self.assertNotIn("/api/reclaim-display-runtime", server)
        self.assertIn("reclaimManagedPortsFromLauncher", server)
        self.assertIn('id="reclaim-ports-button"', html)
        self.assertIn("button.reclaimPorts", app)
        self.assertIn("operation.reclaim", app)
        self.assertIn("const reclaimManagedPorts = async () =>", app)
        self.assertIn("api('/api/reclaim-managed-ports'", app)
        self.assertIn("reclaim-ports-button", app)
        self.assertIn("payload.managedPortReclaim", app)

    def test_launcher_reclaim_surface_does_not_keep_display_specific_legacy_route(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")
        html = read_public("index.html")

        combined = "\n".join([server, app, html])
        self.assertNotIn("orphanReclaim", combined)
        self.assertNotIn("lastOrphanReclaim", combined)
        self.assertNotIn("HOME_CONTROL_LAUNCHER_STOP_ORPHAN_RECLAIM", combined)
        self.assertNotIn("reclaim-display-runtime", combined)
        self.assertNotIn("reclaim-display-button", combined)
        self.assertNotIn("button.reclaimDisplay", combined)
        self.assertNotIn("action.reclaimDisplay", combined)


if __name__ == "__main__":
    unittest.main()
