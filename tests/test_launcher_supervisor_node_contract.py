from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "launcher-supervisor-node.test.js"
MODULES = (
    ROOT / "tools" / "home-control-launcher" / "launcher-supervisor-contract.js",
    ROOT / "tools" / "home-control-launcher" / "launcher-supervisor-reducer.js",
    ROOT / "tools" / "home-control-launcher" / "launcher-operation-store.js",
)


class LauncherSupervisorNodeContractTests(unittest.TestCase):
    def test_dependency_free_node_suite_passes(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "node_not_found")
        completed = subprocess.run(
            [node, "--test", str(NODE_TEST)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, "launcher_supervisor_node_tests_failed")
        self.assertRegex(completed.stdout, r"fail 0\b")

    def test_modules_use_only_node_builtins_and_no_process_execution(self) -> None:
        allowed = {"node:assert/strict", "node:crypto", "node:fs", "node:os", "node:path", "node:test"}
        for module in MODULES:
            source = module.read_text(encoding="utf-8")
            for required in re.findall(r"require\('([^']+)'\)", source):
                if required.startswith("."):
                    continue
                self.assertIn(required, allowed, f"non_builtin_dependency:{module.name}:{required}")
            self.assertNotIn("node:child_process", source)
            self.assertIsNone(re.search(r"\b(?:spawn|exec|fork|kill)Sync?\s*\(", source))
        test_source = NODE_TEST.read_text(encoding="utf-8")
        for required in re.findall(r"^const .*?require\('([^']+)'\)", test_source, re.MULTILINE):
            if required.startswith("."):
                continue
            self.assertIn(required, allowed, f"non_builtin_test_dependency:{required}")

    def test_worker_protocol_is_strict_and_contains_no_raw_process_identity(self) -> None:
        schema = json.loads((ROOT / "contracts" / "launcher" / "launcher-worker.v2.schema.json").read_text(encoding="utf-8"))

        def visit(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                for key, value in node.items():
                    self.assertNotIn(key.lower(), {"pid", "process", "command", "args", "env", "path", "url", "secret", "token"})
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(schema)
        result = schema["$defs"]["result"]["properties"]
        self.assertIn("mismatch", result["ownership_class"]["enum"])
        self.assertIn("mismatch", result["listener_class"]["enum"])
        self.assertIn("foreign", result["descendant_class"]["enum"])

    def test_operation_store_has_fixed_child_files_and_fixed_error_surface(self) -> None:
        source = (ROOT / "tools" / "home-control-launcher" / "launcher-operation-store.js").read_text(encoding="utf-8")
        self.assertIn("const STORE_DIRECTORY = 'launcher-operation.v2'", source)
        self.assertIn("const RECORD_FILE = 'launcher-operation.v2.json'", source)
        self.assertIn("const LOCK_FILE = 'launcher-operation.v2.lock'", source)
        self.assertIn("const TEMP_FILE = 'launcher-operation.v2.json.tmp'", source)
        self.assertIn("const LOCK_RECOVERY_FILE = 'launcher-operation.v2.lock.recovering'", source)
        self.assertIn("const LOCK_DISCARD_FILE = 'launcher-operation.v2.lock.discarding'", source)
        self.assertIn("const SUPERVISOR_LEASE_FILE = 'launcher-supervisor.v2.lease'", source)
        self.assertIn("const SUPERVISOR_LEASE_DISCARD_FILE = 'launcher-supervisor.v2.lease.discarding'", source)
        self.assertIn("const MAX_OPERATION_RECORD_BYTES = 64 * 1024", source)
        self.assertNotIn("error.message", source)
        self.assertNotIn("error.stack", source)
        self.assertNotIn("authorizedPrivateRuntimeRoot)", "".join(re.findall(r"fail\(([^\n]+)", source)))

    def test_public_operation_has_no_asserted_or_private_lease_identity(self) -> None:
        schema = json.loads((ROOT / "contracts" / "launcher" / "launcher-operation.v2.schema.json").read_text(encoding="utf-8"))
        serialized = json.dumps(schema, sort_keys=True)
        for private_name in ("authority_lease", "owner_nonce", "owner_pid", "created_at_ms", "lease_path"):
            self.assertNotIn(private_name, serialized)
        source = (ROOT / "tools" / "home-control-launcher" / "launcher-supervisor-reducer.js").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bauthority_lease\s*:", source))

    def test_readme_marks_n0_as_no_cutover_and_future_portable_authority(self) -> None:
        readme = (ROOT / "tools" / "home-control-launcher" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Launcher Supervisor Node N0", readme)
        self.assertIn("does not start, stop, probe, or spawn", readme)
        self.assertIn("job_worker_service", readme)
        self.assertIn("Ubuntu", readme)


if __name__ == "__main__":
    unittest.main()
