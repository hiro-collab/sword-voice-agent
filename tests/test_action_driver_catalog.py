import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = REPO_ROOT.parent
CATALOG_PATH = REPO_ROOT / "catalogs" / "actions" / "home-actions.json"
HA_CONFIG_PATH = (
    SYSTEM_ROOT
    / "organs"
    / "action"
    / "home-assistant-server"
    / "config"
    / "home-control.yaml"
)
ENV_ACTIONS_PATH = (
    SYSTEM_ROOT
    / "organs"
    / "environment"
    / "environment-state-server"
    / "src"
    / "environment_state_server"
    / "actions.py"
)


def load_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def load_home_control_actions() -> dict[str, dict[str, object]]:
    actions: dict[str, dict[str, object]] = {}
    current: str | None = None
    in_actions = False
    for line in HA_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip() == "actions:":
            in_actions = True
            continue
        if not in_actions:
            continue
        action_match = re.match(r"^  ([A-Za-z0-9_]+):\s*$", line)
        if action_match:
            current = action_match.group(1)
            actions[current] = {}
            continue
        field_match = re.match(r"^    ([A-Za-z0-9_]+):\s*(.+?)\s*$", line)
        if current and field_match:
            key, raw_value = field_match.groups()
            value: object = raw_value.strip().strip('"')
            if raw_value.strip() == "true":
                value = True
            elif raw_value.strip() == "false":
                value = False
            actions[current][key] = value
    return actions


def load_environment_actions() -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("environment_actions_for_catalog", ENV_ACTIONS_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load environment actions from {ENV_ACTIONS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {action.action_id: action for action in module.ACTION_DEFINITIONS}


class ActionDriverCatalogTest(unittest.TestCase):
    def test_catalog_shape_and_update_flow_are_declared(self) -> None:
        catalog = load_catalog()

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["authority"], "sword-control-plane")
        self.assertTrue(catalog["catalog_version"])
        self.assertGreaterEqual(len(catalog["update_flow"]), 5)
        self.assertIn("actions", catalog)
        self.assertIn("aircon_off", catalog["actions"])

    def test_catalog_matches_home_control_bridge_execution_allowlist(self) -> None:
        catalog_actions = load_catalog()["actions"]
        bridge_actions = load_home_control_actions()

        self.assertEqual(set(catalog_actions), set(bridge_actions))
        for action_id, catalog_action in catalog_actions.items():
            bridge_action = bridge_actions[action_id]
            execution = catalog_action["execution"]
            self.assertEqual(catalog_action["label"], bridge_action["label"], action_id)
            self.assertEqual(execution["ha_script"], bridge_action["ha_script"], action_id)
            self.assertEqual(
                catalog_action["confirmation_required"],
                bridge_action["confirm_required"],
                action_id,
            )
            self.assertEqual(execution["response_text"], bridge_action["response_text"], action_id)

    def test_catalog_matches_environment_action_projection(self) -> None:
        catalog_actions = load_catalog()["actions"]
        environment_actions = load_environment_actions()

        self.assertEqual(set(catalog_actions), set(environment_actions))
        for action_id, catalog_action in catalog_actions.items():
            environment_action = environment_actions[action_id]
            self.assertEqual(catalog_action["label"], environment_action.label, action_id)
            self.assertEqual(catalog_action["target"], environment_action.appliance_id, action_id)
            self.assertEqual(catalog_action["target_label"], environment_action.target_label, action_id)
            self.assertEqual(catalog_action["verb"], environment_action.verb, action_id)
            self.assertEqual(
                catalog_action["pre_action_phrase"],
                environment_action.pre_action_phrase,
                action_id,
            )
            self.assertEqual(catalog_action["expected_state"], environment_action.expected_state, action_id)
            self.assertEqual(
                catalog_action["confirmation_required"],
                environment_action.requires_confirmation,
                action_id,
            )
            self.assertEqual(catalog_action["confirmation_reason"], environment_action.confirmation_reason, action_id)
            self.assertEqual(catalog_action["risk_level"], environment_action.risk_level, action_id)
            self.assertEqual(catalog_action["expected_effect"], environment_action.expected_effect, action_id)
            self.assertEqual(catalog_action["aliases"], list(environment_action.aliases), action_id)

    def test_catalog_intent_examples_are_recognized_by_thought_core_fallback(self) -> None:
        thought_src = REPO_ROOT / "services" / "thought-core" / "src"
        sys.path.insert(0, str(thought_src))
        try:
            from thought_core.tools import detect_home_action_intent
        finally:
            try:
                sys.path.remove(str(thought_src))
            except ValueError:
                pass

        for action_id, action in load_catalog()["actions"].items():
            for text in action.get("intent_examples", []):
                intent = detect_home_action_intent(text)
                self.assertIsNotNone(intent, text)
                self.assertEqual(intent.action_id, action_id, text)


if __name__ == "__main__":
    unittest.main()
