from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.system.access_control import AccessDenied, PolicyStore
from sword_voice_agent.system.event_journal import EventJournal
from sword_voice_agent.system.memory_store import MemoryStore


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPO_ROOT / "policies" / "access"


class MemoryStoreTest(TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.policy = PolicyStore(POLICY_ROOT)
        self.journal = EventJournal(self.root / "events", policy=self.policy)
        self.store = MemoryStore(
            self.root / "memory",
            policy=self.policy,
            journal=self.journal,
        )

    def test_thought_core_can_write_candidate_but_not_commit(self) -> None:
        result = self.store.write_candidate(
            requester="thought_core_api",
            item=_candidate(scope="failure_patterns"),
        )

        self.assertEqual(result["status"], "accepted")
        self.assertTrue(self.store.candidates_path.exists())
        self.assertFalse(self.store.facts_path.exists())
        with self.assertRaises(AccessDenied):
            self.store.commit(
                requester="thought_core_api",
                candidate_id=result["candidate_id"],
            )

    def test_memory_core_commits_failure_pattern_without_confirmation(self) -> None:
        candidate = self.store.write_candidate(
            requester="thought_core_api",
            item=_candidate(scope="failure_patterns"),
        )
        committed = self.store.commit(
            requester="memory_core",
            candidate_id=candidate["candidate_id"],
        )
        retrieved = self.store.retrieve(
            requester="thought_core_api",
            scopes=["failure_patterns"],
        )

        self.assertEqual(committed["status"], "committed")
        self.assertEqual(len(retrieved), 1)
        self.assertEqual(retrieved[0]["source"]["candidate_id"], candidate["candidate_id"])

    def test_user_preference_commit_requires_confirmation(self) -> None:
        candidate = self.store.write_candidate(
            requester="thought_core_api",
            item=_candidate(
                scope="user_preferences",
                memory_type="user_preference",
                content={"preference": "Keep appliance replies short."},
            ),
        )

        with self.assertRaises(AccessDenied) as raised:
            self.store.commit(
                requester="memory_core",
                candidate_id=candidate["candidate_id"],
            )
        committed = self.store.commit(
            requester="memory_core",
            candidate_id=candidate["candidate_id"],
            user_confirmed=True,
        )

        self.assertTrue(raised.exception.decision.approval_required)
        self.assertEqual(committed["status"], "committed")

    def test_duplicate_candidate_is_idempotent(self) -> None:
        item = _candidate(scope="failure_patterns")
        first = self.store.write_candidate(requester="thought_core_api", item=item)
        second = self.store.write_candidate(requester="thought_core_api", item=item)

        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["candidate_id"], second["candidate_id"])

    def test_candidate_requires_trace_and_turn_source(self) -> None:
        item = _candidate(scope="failure_patterns")
        item["source"] = {"service": "thought-core"}

        with self.assertRaises(ValueError):
            self.store.write_candidate(requester="thought_core_api", item=item)

    def test_secrets_scope_cannot_be_stored_as_memory(self) -> None:
        with self.assertRaises(AccessDenied):
            self.store.write_candidate(
                requester="thought_core_api",
                item=_candidate(scope="secrets"),
            )


def _candidate(
    *,
    scope: str,
    memory_type: str = "failure_pattern",
    content: dict | None = None,
) -> dict:
    return {
        "schema_version": "memory.item.v0",
        "memory_id": "mcand_test_001",
        "memory_type": memory_type,
        "scope": scope,
        "status": "candidate",
        "content": content
        or {
            "pattern": "light state observation may lag",
            "recommended_wait_ms": 2000,
        },
        "source": {
            "service": "thought-core",
            "trace_id": "trace_test_001",
            "turn_id": "turn_test_001",
        },
        "confidence": 0.82,
        "created_at": "2026-05-08T12:00:00+09:00",
    }
