import json
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPO_ROOT / "policies" / "access"
MANIFEST_ROOT = REPO_ROOT / "ops" / "manifests"
MEMORY_LAYERS = {"M0", "M1", "M2", "M3", "M4", "M5", "M6"}


class MemoryPolicyTest(TestCase):
    def test_access_policy_files_are_valid_json(self) -> None:
        for path in sorted(POLICY_ROOT.glob("*.json")):
            with self.subTest(path=path):
                self.assertIsInstance(_load_json(path), dict)

    def test_every_service_manifest_has_policy_and_memory_layers(self) -> None:
        policies = _load_json(POLICY_ROOT / "services.json")["services"]
        manifests = _load_service_manifests()
        touched_layers: set[str] = set()

        for service_id, manifest in manifests.items():
            with self.subTest(service_id=service_id):
                self.assertIn(service_id, policies)
                memory = manifest.get("memory")
                self.assertIsInstance(memory, dict)
                for key in ("reads", "writes"):
                    self.assertIsInstance(memory.get(key), list)
                    for layer in memory[key]:
                        self.assertIn(layer, MEMORY_LAYERS)
                        touched_layers.add(layer)
                for layer in memory.get("candidates", []):
                    self.assertIn(layer, MEMORY_LAYERS)
                    touched_layers.add(layer)

        self.assertEqual(touched_layers, MEMORY_LAYERS)

    def test_policy_capabilities_are_declared(self) -> None:
        capabilities = set(
            _load_json(POLICY_ROOT / "capabilities.json")["capabilities"].keys()
        )
        services = _load_json(POLICY_ROOT / "services.json")["services"]
        action_rules = _load_json(POLICY_ROOT / "action-approval.json")["rules"]

        for service_id, policy in services.items():
            with self.subTest(service_id=service_id):
                for capability in policy.get("capabilities", []):
                    self.assertIn(capability, capabilities)
                for capability in policy.get("denied", []):
                    self.assertIn(capability, capabilities)

        for rule in action_rules:
            with self.subTest(rule=rule["capability"]):
                self.assertIn(rule["capability"], capabilities)

    def test_memory_scopes_reference_known_identities(self) -> None:
        services = _load_json(POLICY_ROOT / "services.json")["services"]
        identities = {
            policy["identity"]
            for policy in services.values()
        }
        scopes = _load_json(POLICY_ROOT / "memory-scopes.json")["scopes"]

        for scope_name, scope in scopes.items():
            with self.subTest(scope=scope_name):
                self.assertIn(scope["layer"], MEMORY_LAYERS)
                for identity in scope.get("readable_by", []):
                    self.assertIn(identity, identities)
                for identity in scope.get("writable_by", []):
                    self.assertIn(identity, identities)

    def test_secrets_are_not_readable_by_memory_or_thought(self) -> None:
        scopes = _load_json(POLICY_ROOT / "memory-scopes.json")["scopes"]
        services = _load_json(POLICY_ROOT / "services.json")["services"]

        self.assertEqual(scopes["secrets"]["readable_by"], [])
        self.assertNotIn("secrets.use.adapter", services["thought_core_api"]["capabilities"])
        self.assertNotIn("secrets.use.adapter", services["memory_core"]["capabilities"])
        self.assertIn("secrets.use.adapter", services["ops"]["capabilities"])


def _load_service_manifests() -> dict[str, dict]:
    return {
        path.stem: _load_json(path)
        for path in sorted((MANIFEST_ROOT / "services").glob("*.json"))
    }


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value
