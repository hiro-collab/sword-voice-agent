from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.system.access_control import AccessDenied, PolicyStore
from sword_voice_agent.system.state_store import StateStore


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPO_ROOT / "policies" / "access"


class StateStoreTest(TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = StateStore(
            Path(self.tempdir.name) / "state",
            policy=PolicyStore(POLICY_ROOT),
        )

    def test_service_can_write_own_state(self) -> None:
        self.store.write_state(
            requester="environment_state_server",
            target_service="environment_state_server",
            payload={"health": "ok", "last_observation_id": "obs_001"},
        )

        state = self.store.read_state("environment_state_server")
        self.assertEqual(state["health"], "ok")

    def test_service_cannot_write_other_state(self) -> None:
        with self.assertRaises(AccessDenied):
            self.store.write_state(
                requester="environment_state_server",
                target_service="home_assistant_bridge",
                payload={"health": "ok"},
            )

    def test_expression_cannot_write_thought_state(self) -> None:
        with self.assertRaises(AccessDenied):
            self.store.write_state(
                requester="touchdesigner_control_gui",
                target_service="thought_core_api",
                payload={"turn": "changed"},
            )
