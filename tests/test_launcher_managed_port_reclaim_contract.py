from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"

DORMANT_RECLAIM_SYMBOLS = (
    "listeningPortProcessOwners",
    "commandLineContainsPath",
    "commandLineContainsAll",
    "MANAGED_PORT_RECLAIM_POLICIES",
    "reclaimPolicyForTarget",
    "sealedValidationCounts",
    "validateSealedListenerEntry",
    "recordedSealedListenerOwner",
    "isReclaimableManagedPortOwner",
    "stopProcessById",
    "reclaimManagedPortResidue",
)


class LauncherManagedPortCutoverContractTest(unittest.TestCase):
    def test_retired_reclaim_endpoint_has_no_process_kill_authority(self) -> None:
        server = LAUNCHER_SERVER.read_text(encoding="utf-8")
        reclaim_start = server.index("const reclaimManagedPortsFromLauncher")
        reclaim_end = server.index("const isProcessAlive", reclaim_start)
        reclaim = server[reclaim_start:reclaim_end]
        route_start = server.index("requestUrl.pathname === '/api/reclaim-managed-ports'")
        route_end = server.index("requestUrl.pathname === '/api/status-script'", route_start)
        route = server[route_start:route_end]

        self.assertIn("result_class: 'independent_reclaim_retired'", reclaim)
        self.assertIn("authority_class: 'node_supervisor'", reclaim)
        self.assertIn("operation: launcherRuntime.publicState()", reclaim)
        self.assertIn("kill_authority: false", reclaim)
        self.assertIn("raw_private_publication_flags: false", reclaim)
        self.assertRegex(
            route,
            re.compile(
                r"runExclusiveStackOperation\(\s*'reclaim',\s*"
                r"async \(\) => reclaimManagedPortsFromLauncher\(body\)\s*\)"
            ),
        )

        for symbol in DORMANT_RECLAIM_SYMBOLS:
            self.assertNotIn(symbol, server)

        for source in (reclaim, route):
            self.assertNotIn("process.kill", source)
            self.assertNotIn("childProcess.spawn", source)


if __name__ == "__main__":
    unittest.main()
