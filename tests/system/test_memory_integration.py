from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.system.access_control import AccessDenied, PolicyStore
from sword_voice_agent.system.event_journal import EventJournal
from sword_voice_agent.system.memory_store import MemoryStore


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPO_ROOT / "policies" / "access"


class MemoryIntegrationTest(TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.policy = PolicyStore(POLICY_ROOT)
        self.journal = EventJournal(root / "logs" / "events", policy=self.policy)
        self.memory = MemoryStore(root / "local" / "memory", policy=self.policy, journal=self.journal)

    def test_thought_memory_candidate_flow_records_memory_event(self) -> None:
        result = self.memory.write_candidate(
            requester="thought_core_api",
            item=_candidate(),
        )

        events = self.journal.read_events(
            self.journal.service_events_path("memory-core")
        )
        self.assertEqual(result["status"], "accepted")
        self.assertFalse(self.memory.facts_path.exists())
        self.assertEqual(events[0]["event"], "memory.candidate_created")
        self.assertEqual(events[0]["payload"]["scope"], "failure_patterns")

    def test_thought_commit_and_deep_home_execute_are_denied(self) -> None:
        candidate = self.memory.write_candidate(
            requester="thought_core_api",
            item=_candidate(),
        )

        with self.assertRaises(AccessDenied):
            self.memory.commit(
                requester="thought_core_api",
                candidate_id=candidate["candidate_id"],
            )
        decision = self.policy.authorize("deep_core", "home.execute.low_risk")

        self.assertFalse(decision.allowed)
        self.assertIn("denied", decision.reason)

    def test_memory_core_does_not_get_secret_capability(self) -> None:
        decision = self.policy.authorize("memory_core", "secrets.use.adapter")

        self.assertFalse(decision.allowed)


def _candidate() -> dict:
    return {
        "schema_version": "memory.item.v0",
        "memory_id": "mcand_integration_001",
        "memory_type": "failure_pattern",
        "scope": "failure_patterns",
        "status": "candidate",
        "content": {"pattern": "light command needs re-observe"},
        "source": {
            "service": "thought-core",
            "trace_id": "trace_integration_001",
            "turn_id": "turn_integration_001",
        },
        "confidence": 0.9,
        "created_at": "2026-05-08T12:00:00+09:00",
    }
