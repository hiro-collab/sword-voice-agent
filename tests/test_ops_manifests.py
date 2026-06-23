import json
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_ROOT = REPO_ROOT / "ops" / "manifests"


class OpsManifestTest(TestCase):
    def test_service_manifests_are_valid(self) -> None:
        layers = _allowed_layers()
        contract_areas = _contract_areas()
        services = _load_service_manifests()

        self.assertGreater(len(services), 0)
        for service_id, manifest in services.items():
            with self.subTest(service_id=service_id):
                self.assertEqual(manifest["service_id"], service_id)
                self.assertIn(manifest["layer"], layers)
                self.assertIsInstance(manifest.get("logical_service"), str)
                self.assertIsInstance(manifest.get("current_owner"), str)
                self.assertIsInstance(manifest.get("start"), dict)
                self.assertIsInstance(manifest.get("health"), dict)
                self.assertIsInstance(manifest.get("stop"), dict)
                for contract in manifest.get("contracts", []):
                    self.assertIn(contract, contract_areas)
                for adapter in manifest.get("adapters", []):
                    self.assertIsInstance(adapter, str)
                    self.assertGreater(len(adapter.strip()), 0)
                for dependency in manifest.get("depends_on", []):
                    self.assertIn(dependency, services)

    def test_profile_manifests_reference_known_services(self) -> None:
        layers = _allowed_layers()
        services = _load_service_manifests()
        profiles = _load_profile_manifests()

        self.assertGreater(len(profiles), 0)
        for profile_id, manifest in profiles.items():
            with self.subTest(profile_id=profile_id):
                self.assertEqual(manifest["profile_id"], profile_id)
                self.assertIn(manifest["layer"], layers)
                self.assertIsInstance(manifest.get("description"), str)
                alias_for = manifest.get("alias_for")
                if alias_for:
                    self.assertIn(alias_for, profiles)
                    continue
                services_in_profile = manifest.get("services", [])
                self.assertGreater(len(services_in_profile), 0)
                for service_id in services_in_profile:
                    self.assertIn(service_id, services)

    def test_profile_service_sets_match_current_lifecycle_modes(self) -> None:
        profiles = _load_profile_manifests()

        thought_core = _resolve_profile_services("thought-core-v0", profiles)
        self.assertIn("thought_core_api", thought_core)
        self.assertIn("thought_core_watcher", thought_core)
        self.assertEqual(
            _resolve_profile_services("thought-core-experimental", profiles),
            thought_core,
        )

        demo_fast = set(profiles["demo-fast"]["services"])
        self.assertEqual(demo_fast, {"thought_core_api", "aituber_kit"})

        camera_debug = set(profiles["camera-debug"]["services"])
        self.assertEqual(
            camera_debug,
            {"mediapipe_camera_hub_stack", "vision_snapshot_processor"},
        )

        aituber_only = set(profiles["aituber-only"]["services"])
        self.assertEqual(aituber_only, {"aituber_kit"})

    def test_lifecycle_scripts_are_consolidated_under_ops(self) -> None:
        script_names = {
            "start-home-control-stack.ps1",
            "status-home-control-stack.ps1",
            "stop-home-control-stack.ps1",
            "start-home-control-launcher.ps1",
            "stop-home-control-launcher.ps1",
            "install-root-shortcuts.ps1",
            "resolve-home-control-workspace.ps1",
        }
        ops_script_dir = REPO_ROOT / "ops" / "scripts" / "home-control-stack"
        wrapper_dir = REPO_ROOT / "scripts" / "home-control-stack"

        for script_name in script_names:
            with self.subTest(script_name=script_name):
                self.assertTrue((ops_script_dir / script_name).is_file())
                self.assertTrue((wrapper_dir / script_name).is_file())

        for script_name, command in {
            "start-home-control-stack.ps1": "start",
            "status-home-control-stack.ps1": "status",
            "stop-home-control-stack.ps1": "stop",
        }.items():
            text = (wrapper_dir / script_name).read_text(encoding="utf-8")
            self.assertIn("ops\\scripts\\system.ps1", text)
            self.assertIn(command, text)


def _allowed_layers() -> set[str]:
    schema_path = REPO_ROOT / "contracts" / "events" / "layer.schema.json"
    schema = _load_json(schema_path)
    return set(schema["enum"])


def _contract_areas() -> set[str]:
    return {
        path.name
        for path in (REPO_ROOT / "contracts").iterdir()
        if path.is_dir()
    }


def _load_service_manifests() -> dict[str, dict]:
    return {
        path.stem: _load_json(path)
        for path in sorted((MANIFEST_ROOT / "services").glob("*.json"))
    }


def _load_profile_manifests() -> dict[str, dict]:
    return {
        path.stem: _load_json(path)
        for path in sorted((MANIFEST_ROOT / "profiles").glob("*.json"))
    }


def _resolve_profile_services(profile_id: str, profiles: dict[str, dict]) -> set[str]:
    seen: set[str] = set()
    current = profile_id
    while True:
        if current in seen:
            raise AssertionError(f"profile alias loop: {current}")
        seen.add(current)
        manifest = profiles[current]
        alias_for = manifest.get("alias_for")
        if alias_for:
            if alias_for not in profiles:
                raise AssertionError(f"unknown profile alias target: {alias_for}")
            current = alias_for
            continue
        return set(manifest["services"])


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value
