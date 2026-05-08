from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.system.event_journal import EventJournal
from sword_voice_agent.system.access_control import PolicyStore


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_ROOT = REPO_ROOT / "policies" / "access"


class EventJournalTest(TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.journal = EventJournal(
            self.root / "events",
            policy=PolicyStore(POLICY_ROOT),
        )

    def test_append_event_writes_service_and_all_journals(self) -> None:
        event = self.journal.append_event(
            service_id="thought_core_api",
            service="thought-core",
            event="memory.candidate_requested",
            trace_id="trace_event_001",
            turn_id="turn_event_001",
            payload={"scope": "failure_patterns"},
            layer="turn",
        )

        all_events = self.journal.read_events()
        service_events = self.journal.read_events(
            self.journal.service_events_path("thought-core")
        )
        self.assertEqual(event["schema_version"], "system.event.v0")
        self.assertEqual(event["trace_id"], "trace_event_001")
        self.assertEqual(len(all_events), 1)
        self.assertEqual(len(service_events), 1)

    def test_journal_is_append_only_and_skips_broken_lines(self) -> None:
        all_events_path = self.journal.all_events_path
        all_events_path.parent.mkdir(parents=True, exist_ok=True)
        all_events_path.write_text("{broken\n", encoding="utf-8")

        self.journal.append_event(
            service_id="thought_core_api",
            service="thought-core",
            event="turn.started",
            trace_id="trace_event_002",
        )
        events = self.journal.read_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "turn.started")
        self.assertEqual(len(all_events_path.read_text(encoding="utf-8").splitlines()), 2)

    def test_secrets_are_redacted_from_payloads(self) -> None:
        event = self.journal.append_event(
            service_id="thought_core_api",
            service="thought-core",
            event="debug.payload",
            trace_id="trace_event_003",
            payload={
                "api_key": "sk-testsecret12345",
                "message": "OPENAI_API_KEY=sk-testsecret12345 Bearer abc.def",
            },
        )

        self.assertEqual(event["payload"]["api_key"], "[REDACTED]")
        self.assertIn("OPENAI_API_KEY=[REDACTED]", event["payload"]["message"])
        self.assertIn("Bearer [REDACTED]", event["payload"]["message"])
