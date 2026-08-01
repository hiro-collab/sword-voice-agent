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
        self.assertEqual(
            set(result["termination_class"]["enum"]),
            {"forced_only", "graceful", "already_clear", "not_applicable", "unknown"},
        )
        self.assertEqual(
            set(result["job_query_class"]["enum"]),
            {"trusted", "failed", "not_applicable", "unknown"},
        )
        self.assertEqual(
            set(result["post_stop_listener_class"]["enum"]),
            {"clear", "foreign_present", "unknown", "not_applicable"},
        )

    def test_operation_v2_has_optional_bounded_s2_cleanup_attempts(self) -> None:
        schema = json.loads((ROOT / "contracts" / "launcher" / "launcher-operation.v2.schema.json").read_text(encoding="utf-8"))
        self.assertNotIn("cleanup_attempts", schema["required"])
        self.assertEqual(schema["properties"]["cleanup_attempts"]["maxItems"], 65)
        attempt = schema["$defs"]["cleanup_attempt"]
        self.assertIs(attempt["additionalProperties"], False)
        self.assertEqual(set(attempt["properties"]), set(attempt["required"]))
        self.assertEqual(set(attempt["properties"]["target_class"]["enum"]), {"service", "private_plan"})
        self.assertEqual(set(attempt["properties"]["outcome_class"]["enum"]), {"clear", "failed", "unattempted"})
        self.assertIn("legacy_missing", attempt["properties"]["reason_class"]["enum"])
        self.assertIn("unattempted_transport_unavailable", attempt["properties"]["reason_class"]["enum"])
        clear_rule = attempt["allOf"][1]
        self.assertEqual(clear_rule["if"]["properties"]["target_class"]["const"], "service")
        self.assertEqual(clear_rule["if"]["properties"]["outcome_class"]["const"], "clear")
        self.assertEqual(clear_rule["then"]["properties"]["reason_class"]["const"], "none")
        self.assertEqual(set(clear_rule["then"]["properties"]["termination_class"]["enum"]), {"forced_only", "already_clear"})
        self.assertEqual(clear_rule["then"]["properties"]["job_query_class"]["const"], "trusted")
        self.assertEqual(clear_rule["then"]["properties"]["active_count_after"]["const"], 0)
        self.assertEqual(set(clear_rule["then"]["properties"]["post_stop_listener_class"]["enum"]), {"clear", "not_applicable"})

    def test_reducer_vectors_cover_artifact_only_cleanup_residue(self) -> None:
        vectors = json.loads((ROOT / "contracts" / "launcher" / "launcher-reducer-vectors.v2.json").read_text(encoding="utf-8"))["vectors"]
        artifact = next(vector for vector in vectors if vector["vector_id"] == "private_plan_cleanup_failure_is_artifact_residue")
        self.assertEqual(artifact["expected"]["residue_service_ids"], [])
        self.assertEqual(artifact["expected"]["cleanup_attempts"][0]["target_class"], "private_plan")
        self.assertEqual(artifact["expected"]["cleanup_attempts"][0]["reason_class"], "private_plan_cleanup_failed")

    def test_terminal_clear_uses_the_final_private_plan_row_and_exact_preflight_exemption(self) -> None:
        source = (ROOT / "tools" / "home-control-launcher" / "launcher-supervisor-reducer.js").read_text(encoding="utf-8")
        self.assertIn("const finalPrivatePlanCleanupAttempt", source)
        self.assertIn("attempt.sequence > finalAttempt.sequence", source)
        self.assertIn("const hasExactPreflightNoSideEffectClear", source)
        self.assertIn("operation.cleanup_attempts.length === 0", source)
        self.assertIn("operation.services.every((service) => service.attempt_sequence === 0)", source)
        self.assertIn("const hasTerminalPrivatePlanProof", source)
        self.assertIn("hasTerminalPrivatePlanProof(operation)", source)

        vectors = json.loads((ROOT / "contracts" / "launcher" / "launcher-reducer-vectors.v2.json").read_text(encoding="utf-8"))["vectors"]
        by_id = {vector["vector_id"]: vector for vector in vectors}
        success = by_id["recovery_records_private_plan_clear_before_terminal_clear"]
        self.assertLess(
            next(index for index, event in enumerate(success["events"]) if event["event_type"] == "private_plan_cleanup_completed"),
            next(index for index, event in enumerate(success["events"]) if event["event_type"] == "recovery_started"),
        )
        self.assertEqual(by_id["recovery_private_plan_failure_stays_residue"]["expected"]["cleanup"], "residue")
        self.assertEqual(by_id["recovery_private_plan_unavailable_stays_unknown"]["expected"]["cleanup"], "unknown")

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

    def test_readme_keeps_n0_historical_and_marks_n2_as_the_atomic_authority(self) -> None:
        readme = (ROOT / "tools" / "home-control-launcher" / "README.md").read_text(encoding="utf-8")
        self.assertIn("Launcher Supervisor Node N0", readme)
        self.assertIn("N0 was adopted as source/static preparation only", readme)
        self.assertIn("Launcher Supervisor Node N2 atomic cutover", readme)
        self.assertIn("launcher-supervisor-runtime.js` is the single side-effect coordinator", readme)
        self.assertIn("job_worker_service", readme)
        self.assertIn("Ubuntu", readme)


if __name__ == "__main__":
    unittest.main()
