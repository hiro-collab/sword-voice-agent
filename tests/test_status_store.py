from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.adapters.status_store import StatusStore


class StatusStoreTest(TestCase):
    def test_writes_latest_gesture_and_event(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            payload = gesture_payload("start_recording", turn_id="turn-1")

            store.write_latest_gesture(payload)

            latest = json.loads(store.latest_gesture_path.read_text(encoding="utf-8"))
            events = store.read_events()
            self.assertEqual(latest["response"]["voice_control_command"]["turn_id"], "turn-1")
            self.assertTrue(events[0]["event_id"])
            self.assertEqual(events[0]["type"], "gesture.received")
            self.assertEqual(events[0]["turn_id"], "turn-1")
            self.assertEqual(events[0]["payload"]["sequence"], 1)

    def test_does_not_append_unchanged_stable_gesture_events(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            payload = gesture_payload("none")

            store.write_latest_gesture(payload)
            store.write_latest_gesture(payload)

            self.assertEqual(len(store.read_events()), 1)

    def test_writes_dify_response_event(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            store.write_latest_dify_response(
                {
                    "request": {
                        "text": "今日はいい天気ですね",
                        "context": {"turn_id": "turn-1"},
                    },
                    "response": {
                        "text": "はい、いい天気ですね。",
                        "conversation_id": "conv-1",
                        "message_id": "msg-1",
                    },
                    "skipped": False,
                }
            )

            events = store.read_events()
            self.assertEqual(events[0]["type"], "dify.response")
            self.assertEqual(events[0]["turn_id"], "turn-1")
            self.assertTrue(events[0]["event_id"])
            self.assertEqual(events[0]["payload"]["request_text"], "[redacted]")
            self.assertEqual(events[0]["payload"]["response_text"], "[redacted]")
            self.assertEqual(events[0]["payload"]["conversation_id"], "[redacted]")
            self.assertTrue(events[0]["payload"]["conversation_id_present"])

    def test_writes_gesture_diagnostic_event(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            store.write_latest_gesture(
                {
                    "type": "gesture_receiver_status",
                    "timestamp": 1.0,
                    "sequence": 3,
                    "from": "127.0.0.1:50000",
                    "response": {
                        "type": "gesture_diagnostic_response",
                        "diagnostic": {
                            "type": "gesture_status",
                            "status": "running",
                            "frame_id": 10,
                            "fps": 30.0,
                            "hand_detected": True,
                            "primary_gesture": "sword_sign",
                        },
                    },
                }
            )

            self.assertTrue(store.latest_gesture_diagnostic_path.exists())
            events = store.read_events()
            self.assertEqual(events[0]["type"], "gesture.diagnostic")
            self.assertEqual(events[0]["payload"]["diagnostic_type"], "gesture_status")
            self.assertEqual(events[0]["payload"]["fps"], 30.0)

    def test_writes_dify_response_event_with_explicit_turn_id(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            store.write_latest_dify_response(
                {
                    "request": {"text": "今日はいい天気ですね", "context": {}},
                    "response": {"text": "はい。"},
                    "skipped": False,
                },
                turn_id="turn-from-latest-file",
            )

            latest = json.loads(
                store.latest_dify_response_path.read_text(encoding="utf-8")
            )
            events = store.read_events()
            self.assertEqual(latest["turn_id"], "turn-from-latest-file")
            self.assertEqual(events[0]["turn_id"], "turn-from-latest-file")

    def test_limits_event_history(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp, max_events=2)

            store.append_event("one", source="test", payload={})
            store.append_event("two", source="test", payload={})
            store.append_event("three", source="test", payload={})

            events = store.read_events(limit=10)
            self.assertEqual([event["type"] for event in events], ["two", "three"])

    def test_clear_removes_status_files(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)
            store.write_latest_gesture(gesture_payload("start_recording"))
            store.write_latest_dify_response(
                {
                    "request": {"text": "request"},
                    "response": {"text": "response"},
                    "skipped": False,
                }
            )
            store.write_module_status(
                "gesture_udp_receiver",
                "running",
                label="Gesture UDP receiver",
            )

            store.clear()

            self.assertFalse(store.latest_gesture_path.exists())
            self.assertFalse(store.latest_gesture_diagnostic_path.exists())
            self.assertFalse(store.latest_voice_turn_path.exists())
            self.assertFalse(store.latest_dify_response_path.exists())
            self.assertFalse(store.events_path.exists())
            self.assertEqual(store.read_module_statuses(), {})

    def test_idle_gesture_does_not_clear_latest_turn_id(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)

            store.write_latest_gesture(gesture_payload("stop_recording", turn_id="turn-1"))
            store.write_latest_gesture(gesture_payload("none", recording=False))

            latest = json.loads(
                store.latest_voice_turn_path.read_text(encoding="utf-8")
            )
            self.assertEqual(latest["turn_id"], "turn-1")

    def test_writes_and_reads_module_status(self) -> None:
        with workspace_tempdir() as tmp:
            store = StatusStore(tmp)

            store.write_module_status(
                "dify_watcher",
                "running",
                label="Dify watcher",
                detail="source=web",
                timestamp=10.0,
            )

            statuses = store.read_module_statuses()
            self.assertEqual(statuses["dify_watcher"]["state"], "running")
            self.assertEqual(statuses["dify_watcher"]["label"], "Dify watcher")
            self.assertEqual(statuses["dify_watcher"]["detail"], "source=web")
            self.assertEqual(statuses["dify_watcher"]["timestamp"], 10.0)


def gesture_payload(
    action: str,
    turn_id: str | None = None,
    *,
    recording: bool = True,
) -> dict:
    command = {
        "type": "voice_control_command",
        "timestamp": 1.0,
        "action": action,
        "mic_enabled": recording,
        "reason": "stable",
        "source": "test",
    }
    if turn_id:
        command["turn_id"] = turn_id
    return {
        "type": "gesture_receiver_status",
        "timestamp": 1.0,
        "sequence": 1,
        "from": "127.0.0.1:50000",
        "response": {
            "voice_state": {
                "type": "voice_state",
                "timestamp": 1.0,
                "phase": "armed",
                "mic_enabled": recording,
                "recording": recording,
            },
            "gate_decision": {
                "timestamp": 1.0,
                "gesture_name": "sword_sign",
                "raw_active": True,
                "confidence": 0.95,
                "mic_enabled": True,
                "changed": False,
                "reason": "stable",
            },
            "voice_control_command": command,
        },
    }


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
