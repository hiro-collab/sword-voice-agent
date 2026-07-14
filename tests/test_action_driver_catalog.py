import json
import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def find_system_root() -> Path:
    for candidate in (REPO_ROOT, *REPO_ROOT.parents):
        if (candidate / "organs").is_dir():
            return candidate
    return REPO_ROOT.parent


SYSTEM_ROOT = find_system_root()
CATALOG_PATH = REPO_ROOT / "catalogs" / "actions" / "home-actions.json"
HA_CONFIG_PATH = (
    SYSTEM_ROOT
    / "organs"
    / "action"
    / "home-assistant-server"
    / "config"
    / "home-control.yaml"
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


class ActionDriverCatalogTest(unittest.TestCase):
    def test_catalog_shape_and_update_flow_are_declared(self) -> None:
        catalog = load_catalog()

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["authority"], "control-plane-core")
        self.assertTrue(catalog["catalog_version"])
        self.assertGreaterEqual(len(catalog["update_flow"]), 5)
        self.assertIn("actions", catalog)
        self.assertIn("aircon_off", catalog["actions"])

    def test_catalog_matches_home_control_bridge_action_ids_and_proof_metadata(self) -> None:
        catalog_actions = load_catalog()["actions"]
        bridge_actions = load_home_control_actions()

        self.assertEqual(
            set(bridge_actions),
            set(catalog_actions) | {"aircon_restore_original"},
            {
                "missing_from_bridge": sorted(set(catalog_actions) - set(bridge_actions)),
                "unclassified_bridge_only": sorted(
                    set(bridge_actions) - set(catalog_actions) - {"aircon_restore_original"}
                ),
            },
        )
        for action_id, catalog_action in catalog_actions.items():
            bridge_action = bridge_actions[action_id]
            execution = catalog_action["execution"]
            expected_effect = catalog_action.get("expected_effect", {})
            self.assertEqual(execution["adapter"], "home-assistant", action_id)
            self.assertRegex(str(execution["ha_script"]), r"^script\.", action_id)
            self.assertRegex(str(bridge_action["ha_script"]), r"^script\.", action_id)
            self.assertEqual(
                catalog_action["confirmation_required"],
                bridge_action["confirm_required"],
                action_id,
            )
            self.assertTrue(str(execution.get("response_text") or "").strip(), action_id)
            self.assertTrue(str(bridge_action.get("response_text") or "").strip(), action_id)
            for metadata_key in ("control_type", "state_authority"):
                if metadata_key in bridge_action:
                    if action_id in {"light_on", "light_off"}:
                        continue
                    self.assertEqual(
                        expected_effect.get(metadata_key),
                        bridge_action[metadata_key],
                        f"{action_id}:{metadata_key}",
                    )

    def test_light_rows_use_explicit_submission_only_semantics(self) -> None:
        catalog_actions = load_catalog()["actions"]

        for action_id in ("light_on", "light_off"):
            action = catalog_actions[action_id]
            expected_effect = action["expected_effect"]
            observation = action["observation"]

            self.assertEqual(expected_effect["control_type"], "explicit_light_action")
            self.assertEqual(expected_effect["state_authority"], "submitted_only")
            self.assertEqual(expected_effect["verification_mode"], "command_ack_only")
            self.assertEqual(expected_effect["evidence_class"], "command_submission_only")
            self.assertEqual(
                expected_effect["unverified_state_label"],
                "explicit_light_action_submitted",
            )
            self.assertNotIn("state_path", observation)
            self.assertEqual(observation["state_query_path"], "environment.state_queries.room_light")
            self.assertEqual(
                observation["observation_class"],
                "room_light_estimate_only_not_appliance_state",
            )

    def test_response_text_reports_submission_not_completed_state(self) -> None:
        catalog_actions = load_catalog()["actions"]
        stale_completion_phrases = (
            "つけました",
            "消しました",
            "冷房にしました",
            "停止しました",
            "開けました",
            "閉めました",
            "止めました",
            "開始しました",
            "戻しました",
            "一時停止しました",
            "モードにしました",
        )

        for action_id, action in catalog_actions.items():
            response_text = action["execution"]["response_text"]
            self.assertIn("送信しました", response_text, action_id)
            for phrase in stale_completion_phrases:
                self.assertNotIn(phrase, response_text, action_id)

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

    def test_thought_core_fallback_keeps_aircon_stop_separate_from_door_stop(self) -> None:
        thought_src = REPO_ROOT / "services" / "thought-core" / "src"
        sys.path.insert(0, str(thought_src))
        try:
            from thought_core.tools import detect_home_action_intent
        finally:
            try:
                sys.path.remove(str(thought_src))
            except ValueError:
                pass

        for text in (
            "エアコンを止めて",
            "エアコンを消して",
            "エアコンを停止して",
            "エアコン停止して",
        ):
            intent = detect_home_action_intent(text)
            self.assertIsNotNone(intent, text)
            self.assertEqual(intent.action_id, "aircon_hvac_off", text)
            self.assertEqual(intent.target, "aircon", text)

        for text in ("エアコンをつけて", "エアコンを入れて", "空調をつけて"):
            intent = detect_home_action_intent(text)
            self.assertIsNotNone(intent, text)
            self.assertEqual(intent.action_id, "aircon_cool", text)
            self.assertEqual(intent.target, "aircon", text)

        self.assertIsNone(detect_home_action_intent("暖房をつけて"))

        intent = detect_home_action_intent("中扉を止めて")
        self.assertIsNotNone(intent)
        self.assertEqual(intent.action_id, "door_stop")
        self.assertEqual(intent.target, "door")

    def test_legacy_aircon_actions_mark_physical_state_confirmation_as_unsupported(self) -> None:
        catalog_actions = load_catalog()["actions"]

        for action_id in ("aircon_on", "aircon_off"):
            action = catalog_actions[action_id]
            self.assertEqual(action["aliases"], [])
            self.assertEqual(action["intent_examples"], [])
            self.assertEqual(action["natural_language_status"], "retired_compatibility_only")
            expected_effect = catalog_actions[action_id]["expected_effect"]
            self.assertEqual(expected_effect["control_type"], "stateless_command")
            self.assertEqual(expected_effect["state_authority"], "submitted_only")
            self.assertEqual(expected_effect["verification_mode"], "command_ack_only")
            self.assertEqual(expected_effect["evidence_class"], "command_ack_only")
            self.assertEqual(expected_effect["physical_state_source"], "not_supported")
            self.assertEqual(expected_effect["unverified_state_label"], "submitted_unverified")

    def test_tracked_climate_mode_actions_keep_ha_state_authority(self) -> None:
        catalog_actions = load_catalog()["actions"]

        for action_id, expected_state in (("aircon_cool", "cool"), ("aircon_hvac_off", "off")):
            expected_effect = catalog_actions[action_id]["expected_effect"]
            self.assertEqual(expected_effect["expected_state"], expected_state)
            self.assertEqual(expected_effect["control_type"], "mode_command")
            self.assertEqual(expected_effect["state_authority"], "ha_entity")
            self.assertEqual(expected_effect["verification_mode"], "ha_state")
            self.assertEqual(expected_effect["evidence_class"], "ha_state")
            self.assertEqual(expected_effect["physical_state_source"], "home_assistant")


if __name__ == "__main__":
    unittest.main()
