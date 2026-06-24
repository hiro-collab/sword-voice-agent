from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.system.access_control import AccessDenied, PolicyStore
from sword_voice_agent.system.event_journal import EventJournal
from sword_voice_agent.system.memory_store import MemoryStore


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPO_ROOT / "policies" / "access"


class MemoryScenarioTest(TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.policy = PolicyStore(POLICY_ROOT)
        self.journal = EventJournal(root / "runtime" / "logs" / "events", policy=self.policy)
        self.memory = MemoryStore(root / "local" / "memory", policy=self.policy, journal=self.journal)

    def test_light_on_retry_records_retry_without_success_as_state(self) -> None:
        fake_home = FakeHomeControl()
        fake_environment = FakeEnvironment(states=["off", "off", "on"])
        trace_id = "trace_light_retry_001"

        result = run_light_on_turn(
            fake_environment=fake_environment,
            fake_home=fake_home,
            journal=self.journal,
            trace_id=trace_id,
        )

        self.assertEqual(result["status"], "command_accepted_observation_matched")
        self.assertFalse(result["physical_light_proof_claimed"])
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(fake_home.execute_count, 2)
        events = self.journal.read_events()
        self.assertIn("thought.retry_planned", [event["event"] for event in events])
        completed = next(event for event in events if event["event"] == "turn.completed")
        self.assertEqual(
            completed["payload"]["status"],
            "command_accepted_observation_matched",
        )
        self.assertFalse(completed["payload"]["physical_light_proof_claimed"])
        self.assertIn("physical_light_state", completed["payload"]["does_not_prove"])
        self.assertEqual(fake_home.internal_retry_count, 0)

    def test_user_preference_becomes_candidate_not_committed_memory(self) -> None:
        result = self.memory.write_candidate(
            requester="thought_core_api",
            item={
                "schema_version": "memory.item.v0",
                "memory_id": "mcand_preference_001",
                "memory_type": "user_preference",
                "scope": "user_preferences",
                "status": "candidate",
                "content": {"preference": "Keep appliance replies short."},
                "source": {
                    "service": "thought-core",
                    "trace_id": "trace_preference_001",
                    "turn_id": "turn_preference_001",
                },
                "confidence": 0.86,
                "created_at": "2026-05-08T12:00:00+09:00",
            },
        )

        self.assertEqual(result["status"], "accepted")
        self.assertFalse(self.memory.facts_path.exists())
        with self.assertRaises(AccessDenied):
            self.memory.commit(
                requester="memory_core",
                candidate_id=result["candidate_id"],
            )

    def test_high_risk_action_requires_approval(self) -> None:
        decision = self.policy.authorize(
            "home_assistant_bridge",
            "home.execute.requires_approval",
            context={"action": "door_unlock"},
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.approval_required)


class FakeEnvironment:
    def __init__(self, states: list[str]) -> None:
        self.states = list(states)

    def observe_light(self) -> str:
        if not self.states:
            return "off"
        return self.states.pop(0)


class FakeHomeControl:
    def __init__(self) -> None:
        self.execute_count = 0
        self.internal_retry_count = 0

    def execute_light_on(self) -> dict:
        self.execute_count += 1
        return {"accepted": True, "internal_retry_count": self.internal_retry_count}


def run_light_on_turn(
    *,
    fake_environment: FakeEnvironment,
    fake_home: FakeHomeControl,
    journal: EventJournal,
    trace_id: str,
) -> dict:
    retry_count = 0
    turn_id = "turn_light_retry_001"
    journal.append_event(
        service_id="thought_core_api",
        service="thought-core",
        event="turn.started",
        trace_id=trace_id,
        turn_id=turn_id,
        layer="turn",
    )
    before = fake_environment.observe_light()
    journal.append_event(
        service_id="environment_state_server",
        service="environment-server",
        event="observation.received",
        trace_id=trace_id,
        turn_id=turn_id,
        payload={
            "room_light_estimate_state": before,
            "physical_light_proof_claimed": False,
        },
        layer="environment",
    )

    for attempt in range(2):
        fake_home.execute_light_on()
        journal.append_event(
            service_id="home_assistant_bridge",
            service="home-control-server",
            event="action.sent",
            trace_id=trace_id,
            turn_id=turn_id,
            payload={
                "action": "light_on",
                "attempt": attempt + 1,
                "command_status": "accepted",
                "physical_light_proof_claimed": False,
            },
            layer="action",
        )
        observed = fake_environment.observe_light()
        journal.append_event(
            service_id="environment_state_server",
            service="environment-server",
            event="observation.received",
            trace_id=trace_id,
            turn_id=turn_id,
            payload={
                "room_light_estimate_state": observed,
                "physical_light_proof_claimed": False,
            },
            layer="environment",
        )
        if observed == "on":
            status = "command_accepted_observation_matched"
            journal.append_event(
                service_id="thought_core_api",
                service="thought-core",
                event="turn.completed",
                trace_id=trace_id,
                turn_id=turn_id,
                payload={
                    "status": status,
                    "retry_count": retry_count,
                    "observation_class": "room_light_estimate_only_not_appliance_state",
                    "physical_light_proof_claimed": False,
                    "does_not_prove": ["physical_light_state", "ha_device_state"],
                },
                layer="turn",
            )
            return {
                "status": status,
                "retry_count": retry_count,
                "physical_light_proof_claimed": False,
            }
        retry_count += 1
        journal.append_event(
            service_id="thought_core_api",
            service="thought-core",
            event="thought.retry_planned",
            trace_id=trace_id,
            turn_id=turn_id,
            payload={"retry_count": retry_count},
            layer="turn",
        )
    return {"status": "failed", "retry_count": retry_count}
