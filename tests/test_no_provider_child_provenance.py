from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

from sword_voice_agent.adapters.no_provider_child_provenance import (
    build_no_provider_child_provenance_diagnostics,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
SHARED_VECTOR_ENV = "SWORD_M4_SHARED_VECTOR_PATH"
MAX_SHARED_VECTOR_BYTES = 128 * 1024
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.provenance_diagnostics import (  # noqa: E402
    build_child_provenance_diagnostics,
)
from thought_core import server as thought_core_server  # noqa: E402
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.server import (  # noqa: E402
    _decorate_correlated_event_with_conversation_attempt_ref,
    _is_opaque_conversation_attempt_ref,
    create_server,
)


def load_shared_attempt_vectors() -> dict[str, object] | None:
    configured = os.environ.get(SHARED_VECTOR_ENV, "").strip()
    if not configured:
        return None
    path = Path(configured).resolve(strict=True)
    if len(str(path)) > 4096 or path.suffix != ".json" or not path.is_file():
        raise AssertionError("shared vector path must be a bounded JSON file")
    if not 0 < path.stat().st_size <= MAX_SHARED_VECTOR_BYTES:
        raise AssertionError("shared vector file size is out of bounds")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("shared vector root must be an object")
    if payload.get("schema_version") != "m4_cross_repo_attempt_vectors.v0":
        raise AssertionError("shared vector schema_version is invalid")
    required = {
        "canonical_conversation_attempt_ref",
        "invalid_conversation_attempt_refs",
        "accepted_user_speech_candidate",
        "private_turn",
        "assistant_event",
    }
    if not required.issubset(payload):
        raise AssertionError("shared vector shape is incomplete")
    return payload


class NoProviderChildProvenanceTests(unittest.TestCase):
    def _accepted_candidate_payload(self, candidate_id: str) -> dict[str, object]:
        candidate = json.loads(
            (
                REPO_ROOT.parents[1]
                / "contracts"
                / "accepted_user_speech_candidate_input_gate"
                / "examples"
                / "source_static_accepted_private_user_speech_candidate.example.json"
            ).read_text(encoding="utf-8")
        )
        candidate["candidate_id"] = candidate_id
        payload = {
            "accepted_user_speech_candidate": candidate,
            "private_turn": {
                "text": "private turn",
                "turn_id": "turn_deadline_test",
                "session_id": "session_deadline_test",
                "locale": "ja-JP",
                "context_refs": {},
            },
        }
        self.assertEqual(
            set(payload),
            {"accepted_user_speech_candidate", "private_turn"},
        )
        return payload

    def _post_turn(
        self,
        port: int,
        payload: dict[str, object],
        *,
        deadline_header: str | None = None,
        path: str = "/turn",
    ) -> tuple[int, str]:
        headers = {"Content-Type": "application/json"}
        if deadline_header is not None:
            headers["X-Sword-Route-Deadline-Monotonic"] = deadline_header
        req = request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_shared_vectors_decorate_only_the_canonical_ref_when_configured(self) -> None:
        vectors = load_shared_attempt_vectors()
        if vectors is None:
            return
        canonical_ref = vectors["canonical_conversation_attempt_ref"]
        invalid_refs = vectors["invalid_conversation_attempt_refs"]
        candidate = vectors["accepted_user_speech_candidate"]
        private_turn = vectors["private_turn"]
        assistant_event = vectors["assistant_event"]
        self.assertIsInstance(canonical_ref, str)
        self.assertIsInstance(invalid_refs, dict)
        self.assertEqual(
            set(invalid_refs),
            {"colonless", "uppercase", "wrong_prefix", "short", "long", "unsafe", "whitespace"},
        )
        self.assertIsInstance(candidate, dict)
        self.assertIsInstance(private_turn, dict)
        self.assertIsNone(private_turn.get("text"))
        self.assertEqual(
            private_turn.get("text_representation"),
            "legacy_no_speech_placeholder_not_publicly_representable_in_parent_fixture",
        )
        self.assertIsInstance(assistant_event, dict)
        self.assertEqual(
            assistant_event.get("expected_conversation_attempt_ref"), canonical_ref
        )
        candidate_for_core = dict(candidate)
        candidate_for_core["redaction_guards"] = {
            key: False
            for key in (
                "raw_audio_included",
                "raw_media_included",
                "raw_transcript_included",
                "raw_recognized_text_included",
                "private_path_included",
                "provider_payload_included",
                "browser_storage_included",
                "token_or_secret_included",
                "home_control_action_authority_included",
            )
        }

        class VectorLoop:
            def run_dicts(self, turn, *, event_sink=None):
                injected = dict(assistant_event)
                injected["event_id"] = "evt_shared_vector_message"
                injected["conversation_attempt_ref"] = (
                    "m4.prepared_sample_attempt:ffffffffffffffffffffffffffffffff"
                )
                injected["data"] = dict(assistant_event["data"])
                events = [
                    {
                        "event_id": "evt_shared_vector_delta",
                        "type": "assistant.speech_delta",
                        "conversation_attempt_ref": (
                            "m4.prepared_sample_attempt:ffffffffffffffffffffffffffffffff"
                        ),
                        "data": {
                            "delta": "synthetic delta",
                            "conversation_attempt_ref": assistant_event["data"][
                                "conversation_attempt_ref"
                            ],
                        },
                    },
                    injected,
                    {
                        "event_id": "evt_shared_vector_completed",
                        "type": "turn.completed",
                        "data": {"status": "success"},
                    },
                    {
                        "event_id": "evt_shared_vector_motion",
                        "type": "motion.requested",
                        "conversation_attempt_ref": "injected:not_authoritative",
                        "data": {
                            "schema_version": "motion_stimulus.v0",
                            "motion_event_id": "mot_evt_shared_vector_001",
                            "conversation_attempt_ref": "injected:not_authoritative",
                        },
                    },
                ]
                if event_sink is not None:
                    for event in events:
                        event_sink(event)
                return events

        def request_events(port: int, ref: str, suffix: str) -> list[dict[str, object]]:
            private_payload = dict(private_turn)
            private_payload["text"] = "synthetic private test turn"
            private_payload["context_refs"] = {"conversation_attempt_ref": ref}
            body = json.dumps(
                {
                    "accepted_user_speech_candidate": candidate_for_core,
                    "private_turn": private_payload,
                }
            ).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}{suffix}",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with request.urlopen(req, timeout=5) as response:
                body = response.read().decode("utf-8")
            if suffix == "/turn":
                return json.loads(body)["events"]
            return [
                json.loads(line[6:])
                for line in body.splitlines()
                if line.startswith("data: ")
            ]

        server = create_server("127.0.0.1", 0, thought_loop=VectorLoop())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            for suffix in ("/turn", "/turn/stream"):
                with self.subTest(case="canonical", path=suffix):
                    events = request_events(port, canonical_ref, suffix)
                    for event in events:
                        if event["type"].startswith("assistant."):
                            self.assertEqual(
                                event["data"]["conversation_attempt_ref"], canonical_ref
                            )
                            self.assertNotIn("conversation_attempt_ref", event)
                        elif event["type"] == "motion.requested":
                            self.assertEqual(
                                event["conversation_attempt_ref"], canonical_ref
                            )
                            self.assertNotIn(
                                "conversation_attempt_ref",
                                event["data"],
                            )
                        else:
                            self.assertNotIn("conversation_attempt_ref", event["data"])
            for name, invalid_ref in invalid_refs.items():
                for suffix in ("/turn", "/turn/stream"):
                    with self.subTest(case=name, path=suffix):
                        events = request_events(port, invalid_ref, suffix)
                        for event in events:
                            self.assertNotIn(
                                "conversation_attempt_ref",
                                event["data"],
                            )
                            self.assertNotIn("conversation_attempt_ref", event)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_materializes_accepted_candidate_once_for_normal_and_stream_turns(
        self,
    ) -> None:
        class RecordingLoop:
            def __init__(self) -> None:
                self.turns: list[TurnInput] = []

            def run_dicts(self, turn, *, event_sink=None, execution_deadline=None):
                self.turns.append(turn)
                events = [
                    {
                        "event_id": "evt_candidate_delta",
                        "type": "assistant.speech_delta",
                        "conversation_attempt_ref": (
                            "m4.prepared_sample_attempt:ffffffffffffffffffffffffffffffff"
                        ),
                        "data": {
                            "delta": "synthetic assistant delta",
                            "conversation_attempt_ref": "injected:not_authoritative",
                        },
                    },
                    {
                        "event_id": "evt_candidate_message",
                        "type": "assistant.message",
                        "conversation_attempt_ref": "C:/injected/private/path.wav",
                        "data": {
                            "speech": "synthetic assistant response",
                            "conversation_attempt_ref": "injected:not_authoritative",
                        },
                    },
                    {
                        "event_id": "evt_candidate_completed",
                        "type": "turn.completed",
                        "data": {"status": "success"},
                    },
                    {
                        "event_id": "evt_candidate_motion",
                        "type": "motion.requested",
                        "conversation_attempt_ref": "injected:not_authoritative",
                        "data": {
                            "schema_version": "motion_stimulus.v0",
                            "motion_event_id": "mot_evt_candidate_001",
                            "conversation_attempt_ref": "injected:not_authoritative",
                        },
                    },
                ]
                if event_sink is not None:
                    for event in events:
                        event_sink(event)
                return events

        candidate = json.loads(
            (
                REPO_ROOT.parents[1]
                / "contracts"
                / "accepted_user_speech_candidate_input_gate"
                / "examples"
                / "source_static_accepted_private_user_speech_candidate.example.json"
            ).read_text(encoding="utf-8")
        )
        payload = {
            "accepted_user_speech_candidate": candidate,
            "private_turn": {
                "text": "synthetic private turn text",
                "turn_id": "turn_candidate_server_001",
                "session_id": "session_candidate_server_001",
                "locale": "ja-JP",
                "context_refs": {
                    "conversation_attempt_ref": (
                        "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
                    ),
                },
            },
        }
        loop = RecordingLoop()
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            rendered_outputs = []
            expected_candidate_refs = []
            for index, suffix in enumerate(("/turn", "/turn/stream"), start=1):
                with self.subTest(path=suffix):
                    request_payload = json.loads(json.dumps(payload))
                    request_candidate = request_payload[
                        "accepted_user_speech_candidate"
                    ]
                    request_candidate["candidate_id"] = (
                        f"ausc_live:cid_{index:032x}"
                    )
                    expected_candidate_refs.append(request_candidate["candidate_id"])
                    body = json.dumps(request_payload).encode("utf-8")
                    req = request.Request(
                        f"http://127.0.0.1:{port}{suffix}",
                        data=body,
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "X-Sword-Route-Deadline-Monotonic": str(
                                time.monotonic() + 5
                            ),
                        },
                    )
                    with request.urlopen(req, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        body = response.read().decode("utf-8")
                    if suffix == "/turn":
                        events = json.loads(body)["events"]
                    else:
                        events = [
                            json.loads(line[6:])
                            for line in body.splitlines()
                            if line.startswith("data: ")
                        ]
                    rendered_outputs.append(json.dumps(events, ensure_ascii=False))
                    for event in events:
                        if event["type"].startswith("assistant."):
                            self.assertEqual(
                                event["data"]["conversation_attempt_ref"],
                                "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef",
                            )
                            self.assertNotIn("conversation_attempt_ref", event)
                        elif event["type"] == "motion.requested":
                            self.assertEqual(
                                event["conversation_attempt_ref"],
                                "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef",
                            )
                            self.assertNotIn(
                                "conversation_attempt_ref",
                                event["data"],
                            )
                        else:
                            self.assertNotIn(
                                "conversation_attempt_ref",
                                event["data"],
                            )

            self.assertEqual(len(loop.turns), 2)
            for turn, expected_candidate_ref in zip(
                loop.turns,
                expected_candidate_refs,
                strict=True,
            ):
                self.assertIsInstance(turn, TurnInput)
                self.assertEqual(turn.turn_id, "turn_candidate_server_001")
                self.assertEqual(
                    turn.context_refs["accepted_user_speech_candidate_ref"],
                    expected_candidate_ref,
                )
                self.assertEqual(
                    turn.context_refs["conversation_attempt_ref"],
                    "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef",
                )
            for output in rendered_outputs:
                self.assertNotIn("synthetic private turn text", output)
                self.assertNotIn("accepted_user_speech_candidate", output)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_rejects_expired_deadline_before_materialization(self) -> None:
        loop = unittest.mock.Mock()
        payload = self._accepted_candidate_payload(
            "ausc_live:cid_11111111111111111111111111111111"
        )
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with patch.object(
                thought_core_server,
                "materialize_turn_input",
                wraps=thought_core_server.materialize_turn_input,
            ) as materialize:
                status, body = self._post_turn(
                    port,
                    payload,
                    deadline_header=str(time.monotonic() - 1),
                )
            self.assertEqual(status, 408)
            self.assertEqual(json.loads(body), {"error": "turn_deadline_expired"})
            materialize.assert_not_called()
            loop.run_dicts.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_accepts_and_forwards_deadline_inside_thirty_second_bound(
        self,
    ) -> None:
        class RecordingDeadlineLoop:
            def __init__(self) -> None:
                self.remaining_seconds: float | None = None

            def run_dicts(self, turn, *, event_sink=None, execution_deadline=None):
                self.remaining_seconds = execution_deadline.remaining_seconds()
                return []

        loop = RecordingDeadlineLoop()
        payload = self._accepted_candidate_payload(
            "ausc_live:cid_55555555555555555555555555555555"
        )
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _ = self._post_turn(
                server.server_address[1],
                payload,
                deadline_header=str(time.monotonic() + 25.0),
            )
            self.assertEqual(status, 200)
            self.assertIsNotNone(loop.remaining_seconds)
            self.assertGreater(loop.remaining_seconds, 20.0)
            self.assertLessEqual(loop.remaining_seconds, 25.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_returns_fixed_error_when_deadline_expires_inside_loop(self) -> None:
        class CancellingLoop:
            def __init__(self) -> None:
                self.calls = 0

            def run_dicts(self, turn, *, event_sink=None, execution_deadline=None):
                self.calls += 1
                execution_deadline.cancel()
                execution_deadline.ensure_current()
                return []

        marker = "private-mid-loop-marker"
        loop = CancellingLoop()
        payload = self._accepted_candidate_payload(
            "ausc_live:cid_44444444444444444444444444444444"
        )
        payload["private_turn"]["text"] = marker
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post_turn(
                server.server_address[1],
                payload,
                deadline_header=str(time.monotonic() + 5.0),
            )
            self.assertEqual(status, 408)
            self.assertEqual(json.loads(body), {"error": "turn_deadline_exceeded"})
            self.assertNotIn(marker, body)
            self.assertEqual(loop.calls, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_expiry_after_materialize_does_not_reserve_candidate(self) -> None:
        class CountingLoop:
            def __init__(self) -> None:
                self.count = 0

            def run_dicts(self, turn, *, event_sink=None):
                self.count += 1
                return []

        loop = CountingLoop()
        payload = self._accepted_candidate_payload(
            "ausc_live:cid_22222222222222222222222222222222"
        )
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with (
                patch.object(
                    thought_core_server.time,
                    "monotonic",
                    side_effect=(100.0, 100.5, 102.0),
                ),
                patch.object(
                    thought_core_server,
                    "materialize_turn_input",
                    wraps=thought_core_server.materialize_turn_input,
                ) as materialize,
            ):
                status, body = self._post_turn(
                    port,
                    payload,
                    deadline_header="101.5",
                )
            self.assertEqual(status, 408)
            self.assertEqual(json.loads(body), {"error": "turn_deadline_expired"})
            materialize.assert_called_once()
            retry_status, _ = self._post_turn(port, payload)
            self.assertEqual(retry_status, 200)
            self.assertEqual(loop.count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_rejects_malformed_and_overlong_deadlines_without_echo(self) -> None:
        marker = "private-deadline-marker"
        loop = unittest.mock.Mock()
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            for deadline_header in (marker, str(time.monotonic() + 31)):
                with self.subTest(deadline_header=deadline_header):
                    payload = self._accepted_candidate_payload(
                        "ausc_live:cid_33333333333333333333333333333333"
                    )
                    status, body = self._post_turn(
                        port,
                        payload,
                        deadline_header=deadline_header,
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(
                        json.loads(body),
                        {"error": "turn_deadline_invalid"},
                    )
                    self.assertNotIn(marker, body)
            loop.run_dicts.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_reserves_candidate_once_across_sequential_requests(self) -> None:
        class CountingLoop:
            def __init__(self) -> None:
                self.count = 0

            def run_dicts(self, turn, *, event_sink=None):
                self.count += 1
                return []

        candidate_id = "ausc_live:cid_44444444444444444444444444444444"
        payload = self._accepted_candidate_payload(candidate_id)
        loop = CountingLoop()
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            first_status, _ = self._post_turn(port, payload, path="/turn")
            replay_status, replay_body = self._post_turn(
                port,
                payload,
                path="/turn/stream",
            )
            self.assertEqual(first_status, 200)
            self.assertEqual(replay_status, 409)
            self.assertEqual(
                json.loads(replay_body),
                {"error": "accepted_candidate_duplicate"},
            )
            self.assertNotIn(candidate_id, replay_body)
            self.assertEqual(loop.count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_reserves_candidate_once_across_concurrent_requests(self) -> None:
        class CountingLoop:
            def __init__(self) -> None:
                self.count = 0
                self.lock = threading.Lock()

            def run_dicts(self, turn, *, event_sink=None):
                with self.lock:
                    self.count += 1
                return []

        payload = self._accepted_candidate_payload(
            "ausc_live:cid_55555555555555555555555555555555"
        )
        loop = CountingLoop()
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda _: self._post_turn(
                            port,
                            payload,
                            path="/turn/stream",
                        ),
                        range(2),
                    )
                )
            self.assertEqual(sorted(status for status, _ in results), [200, 409])
            self.assertEqual(loop.count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_rejects_invalid_envelope_without_reserving_candidate(self) -> None:
        class CountingLoop:
            def __init__(self) -> None:
                self.count = 0

            def run_dicts(self, turn, *, event_sink=None):
                self.count += 1
                return []

        payload = self._accepted_candidate_payload(
            "ausc_live:cid_66666666666666666666666666666666"
        )
        invalid_payload = dict(payload)
        invalid_payload["unexpected"] = "private"
        loop = CountingLoop()
        server = create_server("127.0.0.1", 0, thought_loop=loop)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            invalid_status, _ = self._post_turn(port, invalid_payload)
            valid_status, _ = self._post_turn(port, payload)
            self.assertEqual(invalid_status, 400)
            self.assertEqual(valid_status, 200)
            self.assertEqual(loop.count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_server_registry_full_is_fixed_and_does_not_enter_loop(self) -> None:
        loop = unittest.mock.Mock()
        payload = self._accepted_candidate_payload(
            "ausc_live:cid_77777777777777777777777777777777"
        )
        with patch.object(
            thought_core_server,
            "MAX_ACCEPTED_CANDIDATE_RESERVATIONS",
            0,
        ):
            server = create_server("127.0.0.1", 0, thought_loop=loop)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, body = self._post_turn(server.server_address[1], payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(body),
            {"error": "accepted_candidate_registry_full"},
        )
        loop.run_dicts.assert_not_called()

    def test_assistant_ref_uses_only_validated_turn_context_and_strips_envelope(
        self,
    ) -> None:
        canonical_ref = (
            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
        )
        turn_cases = {
            "canonical": (
                TurnInput(
                    text="private text",
                    turn_id="turn_canonical_ref",
                    session_id="session_canonical_ref",
                    context_refs={"conversation_attempt_ref": canonical_ref},
                ),
                canonical_ref,
            ),
            "missing": (
                TurnInput(
                    text="private text",
                    turn_id="turn_missing_ref",
                    session_id="session_missing_ref",
                ),
                None,
            ),
            "malformed": (
                TurnInput(
                    text="private text",
                    turn_id="turn_malformed_ref",
                    session_id="session_malformed_ref",
                    context_refs={"conversation_attempt_ref": "malformed"},
                ),
                None,
            ),
            "path": (
                TurnInput(
                    text="private text",
                    turn_id="turn_path_ref",
                    session_id="session_path_ref",
                    context_refs={"conversation_attempt_ref": "C:/private/path.wav"},
                ),
                None,
            ),
            "private_marker": (
                TurnInput(
                    text="private text",
                    turn_id="turn_private_marker_ref",
                    session_id="session_private_marker_ref",
                    context_refs={"conversation_attempt_ref": "private:test-marker"},
                ),
                None,
            ),
            "plain_mapping": ({"text": "ordinary turn"}, None),
        }
        envelope_injections = {
            "valid_looking": (
                "m4.prepared_sample_attempt:ffffffffffffffffffffffffffffffff"
            ),
            "malformed": "malformed",
            "path": "C:/injected/private/path.wav",
            "private_marker": "private:injected-marker",
        }

        for injection_case, injected_ref in envelope_injections.items():
            for turn_case, (turn, expected_ref) in turn_cases.items():
                injected_event = {
                    "type": "assistant.message",
                    "conversation_attempt_ref": injected_ref,
                    "data": {
                        "speech": "response",
                        "conversation_attempt_ref": "injected:not_authoritative",
                    },
                }
                with self.subTest(
                    injection=injection_case,
                    turn=turn_case,
                ):
                    decorated = _decorate_correlated_event_with_conversation_attempt_ref(
                        injected_event,
                        turn,
                    )
                    self.assertNotIn("conversation_attempt_ref", decorated)
                    if expected_ref is None:
                        self.assertNotIn(
                            "conversation_attempt_ref",
                            decorated["data"],
                        )
                    else:
                        self.assertEqual(
                            decorated["data"]["conversation_attempt_ref"],
                            expected_ref,
                        )

    def test_assistant_non_dict_data_strips_only_the_event_envelope_ref(self) -> None:
        turn = TurnInput(
            text="private text",
            turn_id="turn_non_dict_assistant_data",
            session_id="session_non_dict_assistant_data",
            context_refs={
                "conversation_attempt_ref": (
                    "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
                )
            },
        )
        cases = {
            "string": {
                "type": "assistant.message",
                "conversation_attempt_ref": "private:injected-marker",
                "data": "unchanged non-dict data",
            },
            "missing_data": {
                "type": "assistant.completed",
                "conversation_attempt_ref": "C:/injected/private/path.wav",
            },
        }

        for case, event in cases.items():
            with self.subTest(case=case):
                decorated = _decorate_correlated_event_with_conversation_attempt_ref(
                    event,
                    turn,
                )
                self.assertIsNot(decorated, event)
                self.assertNotIn("conversation_attempt_ref", decorated)
                self.assertEqual(decorated.get("data"), event.get("data"))

    def test_non_assistant_event_does_not_receive_the_turn_ref(self) -> None:
        non_assistant_event = {"type": "turn.completed", "data": {"status": "success"}}
        self.assertNotIn(
            "conversation_attempt_ref",
            _decorate_correlated_event_with_conversation_attempt_ref(
                non_assistant_event,
                TurnInput(
                    text="private text",
                    turn_id="turn_non_assistant",
                    session_id="session_non_assistant",
                    context_refs={
                        "conversation_attempt_ref": (
                            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
                        )
                    },
                ),
            )["data"],
        )

    def test_motion_request_uses_only_the_turn_ref_on_the_event_envelope(self) -> None:
        canonical_ref = (
            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
        )
        strict_payload = {
            "schema_version": "motion_stimulus.v0",
            "motion_event_id": "mot_evt_turn_001",
            "phase": "queued",
        }
        event = {
            "type": "motion.requested",
            "conversation_attempt_ref": "injected:not_authoritative",
            "data": dict(strict_payload),
        }
        turn = TurnInput(
            text="private text",
            turn_id="turn_motion_ref",
            session_id="session_motion_ref",
            context_refs={"conversation_attempt_ref": canonical_ref},
        )

        decorated = _decorate_correlated_event_with_conversation_attempt_ref(
            event,
            turn,
        )

        self.assertEqual(decorated["conversation_attempt_ref"], canonical_ref)
        self.assertEqual(decorated["data"], strict_payload)
        self.assertNotIn("conversation_attempt_ref", decorated["data"])
        self.assertEqual(
            event["conversation_attempt_ref"],
            "injected:not_authoritative",
        )

    def test_motion_request_strips_non_authoritative_refs(self) -> None:
        event = {
            "type": "motion.requested",
            "conversation_attempt_ref": (
                "m4.prepared_sample_attempt:ffffffffffffffffffffffffffffffff"
            ),
            "data": {
                "schema_version": "motion_stimulus.v0",
                "motion_event_id": "mot_evt_turn_002",
                "conversation_attempt_ref": "injected:not_authoritative",
            },
        }
        invalid_turns = {
            "missing": TurnInput(
                text="private text",
                turn_id="turn_missing_ref",
                session_id="session_missing_ref",
            ),
            "malformed": TurnInput(
                text="private text",
                turn_id="turn_malformed_ref",
                session_id="session_malformed_ref",
                context_refs={"conversation_attempt_ref": "malformed"},
            ),
            "private_marker": TurnInput(
                text="private text",
                turn_id="turn_private_marker_ref",
                session_id="session_private_marker_ref",
                context_refs={"conversation_attempt_ref": "private:test-marker"},
            ),
            "path": TurnInput(
                text="private text",
                turn_id="turn_path_ref",
                session_id="session_path_ref",
                context_refs={"conversation_attempt_ref": "C:/private/path.wav"},
            ),
            "plain_mapping": {
                "conversation_attempt_ref": event["conversation_attempt_ref"]
            },
        }

        for case, turn in invalid_turns.items():
            with self.subTest(case=case):
                decorated = _decorate_correlated_event_with_conversation_attempt_ref(
                    event,
                    turn,
                )
                self.assertNotIn("conversation_attempt_ref", decorated)
                self.assertNotIn("conversation_attempt_ref", decorated["data"])

    def test_non_assistant_non_motion_events_are_unchanged(self) -> None:
        event = {
            "type": "turn.completed",
            "conversation_attempt_ref": "existing-non-correlation-field",
            "data": {
                "status": "success",
                "conversation_attempt_ref": "existing-non-correlation-data",
            },
        }
        turn = TurnInput(
            text="private text",
            turn_id="turn_non_correlated",
            session_id="session_non_correlated",
            context_refs={
                "conversation_attempt_ref": (
                    "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
                )
            },
        )

        self.assertIs(
            _decorate_correlated_event_with_conversation_attempt_ref(event, turn),
            event,
        )

    def test_conversation_attempt_ref_grammar_is_canonical_and_bounded(self) -> None:
        valid = "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef"
        invalid = (
            "m4.prepared_sample_attempt0123456789abcdef0123456789abcdef",
            "m4.prepared_sample_attempt:0123456789ABCDEF0123456789abcdef",
            "m4.other_attempt:0123456789abcdef0123456789abcdef",
            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcde",
            "m4.prepared_sample_attempt:0123456789abcdef0123456789abcdef/",
        )

        self.assertTrue(_is_opaque_conversation_attempt_ref(valid))
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(_is_opaque_conversation_attempt_ref(value))
    def test_server_rejects_candidate_envelope_without_private_turn(self) -> None:
        candidate = json.loads(
            (
                REPO_ROOT.parents[1]
                / "contracts"
                / "accepted_user_speech_candidate_input_gate"
                / "examples"
                / "source_static_accepted_private_user_speech_candidate.example.json"
            ).read_text(encoding="utf-8")
        )
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            req = request.Request(
                f"http://127.0.0.1:{port}/turn",
                data=json.dumps({"accepted_user_speech_candidate": candidate}).encode(
                    "utf-8"
                ),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(request.HTTPError) as caught:
                request.urlopen(req, timeout=5)
            self.assertEqual(caught.exception.code, 400)
            self.assertIn(
                "private_turn must be an object",
                caught.exception.read().decode("utf-8"),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_launcher_helper_reports_env_import_override_without_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "core"
            profile_dir = control_plane / "ops" / "manifests" / "profiles"
            profile_dir.mkdir(parents=True)
            (profile_dir / "thought-core-v0.json").write_text(
                json.dumps(
                    {
                        "profile": "thought-core-v0",
                        "services": [
                            "home_assistant_bridge",
                            "thought_core_api",
                            "thought_core_watcher",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            thought_core = control_plane / "services" / "thought-core"
            thought_core.mkdir(parents=True)
            (thought_core / ".env").write_text(
                "\n".join(
                    [
                        "THOUGHT_CORE_LLM_ENABLED=enabled",
                        "THOUGHT_CORE_LLM_API_KEY=mock-private-key",
                    ]
                ),
                encoding="utf-8",
            )
            source_root = thought_core / "src" / "thought_core"
            source_root.mkdir(parents=True)
            (source_root / "input_understanding.py").write_text(
                "INPUT = 'source only'\n",
                encoding="utf-8",
            )
            (source_root / "loop.py").write_text(
                "LOOP = 'source only'\n",
                encoding="utf-8",
            )

            payload = build_no_provider_child_provenance_diagnostics(
                agent_os_root=root,
                selected_profile="thought-core-v0",
                process_env={
                    "THOUGHT_CORE_LLM_ENABLED": "0",
                    "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
                },
                listener_classes={"thought_core_api": "none"},
                top_level_text_present_class="present_redacted",
                payload_marker_class="happy_marker_plus_move_marker",
                context_ref_payload_class="happy_expression_motion_request",
            )

        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "provider_capable_enabled_after_env_import",
        )
        self.assertEqual(
            payload["thought_core_action_llm_enabled_class"],
            "action_llm_disabled_literal",
        )
        self.assertEqual(
            payload["external_provider_route_class"],
            "thought_core_route_selected",
        )
        self.assertEqual(payload["mapping_input_source"], "top_level_text")
        self.assertEqual(
            payload["marker_class_consistency"],
            "consistent_marker_and_context_label",
        )
        self.assertEqual(payload["standard_diagnostics_surface_class"], "partial")
        self.assertEqual(
            payload["diagnostics_status_writer_surface"],
            "missing_status_surface",
        )
        self.assertFalse(payload["runtime_import_provenance_available"])
        self.assertEqual(
            payload["running_child_input_understanding_import_provenance"][
                "collection_point"
            ],
            "thought_core_child_diagnostics_endpoint",
        )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_launcher_helper_force_no_provider_wins_after_env_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "core"
            profile_dir = control_plane / "ops" / "manifests" / "profiles"
            profile_dir.mkdir(parents=True)
            (profile_dir / "thought-core-v0.json").write_text(
                json.dumps(
                    {
                        "profile": "thought-core-v0",
                        "services": [
                            "thought_core_api",
                            "thought_core_watcher",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            thought_core = control_plane / "services" / "thought-core"
            thought_core.mkdir(parents=True)
            (thought_core / ".env").write_text(
                "\n".join(
                    [
                        "THOUGHT_CORE_LLM_ENABLED=enabled",
                        "THOUGHT_CORE_LLM_API_KEY=mock-private-key",
                        "OPENAI_API_KEY=mock-private-key",
                    ]
                ),
                encoding="utf-8",
            )
            source_root = thought_core / "src" / "thought_core"
            source_root.mkdir(parents=True)
            (source_root / "input_understanding.py").write_text(
                "INPUT = 'source only'\n",
                encoding="utf-8",
            )
            (source_root / "loop.py").write_text(
                "LOOP = 'source only'\n",
                encoding="utf-8",
            )

            payload = build_no_provider_child_provenance_diagnostics(
                agent_os_root=root,
                selected_profile="thought-core-v0",
                process_env={"THOUGHT_CORE_FORCE_NO_PROVIDER": "1"},
                payload_marker_class="happy_marker_plus_move_marker",
                context_ref_payload_class="happy_expression_motion_request",
            )

        self.assertEqual(
            payload["thought_core_force_no_provider_class"],
            "enabled_literal",
        )
        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "no_provider_or_fallback_only_bound_by_final_child_env_class",
        )
        self.assertEqual(payload["thought_core_llm_enabled_class"], "disabled_literal")
        self.assertEqual(
            payload["thought_core_action_llm_enabled_class"],
            "action_llm_disabled_literal",
        )
        self.assertEqual(
            payload["provider_config_presence_class"],
            "provider_config_absent_or_empty",
        )
        for key in (
            "THOUGHT_CORE_CODEX_CLI_PATH",
            "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
            "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
        ):
            self.assertEqual(
                payload["provider_config_key_classes"][key],
                "empty",
            )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_launcher_helper_preserves_codex_cli_child_env_classes_without_raw_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_plane = root / "control-plane" / "core"
            profile_dir = control_plane / "ops" / "manifests" / "profiles"
            profile_dir.mkdir(parents=True)
            (profile_dir / "thought-core-v0.json").write_text(
                json.dumps(
                    {
                        "profile": "thought-core-v0",
                        "services": [
                            "thought_core_api",
                            "thought_core_watcher",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            thought_core = control_plane / "services" / "thought-core"
            thought_core.mkdir(parents=True)
            (thought_core / ".env").write_text(
                "\n".join(
                    [
                        "THOUGHT_CORE_LLM_ENABLED=1",
                        "THOUGHT_CORE_LLM_PROVIDER=codex-cli",
                        "THOUGHT_CORE_CODEX_CLI_MODE=operate",
                        "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION=0.142.0",
                        "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY=warn",
                    ]
                ),
                encoding="utf-8",
            )
            source_root = thought_core / "src" / "thought_core"
            source_root.mkdir(parents=True)
            (source_root / "input_understanding.py").write_text(
                "INPUT = 'source only'\n",
                encoding="utf-8",
            )
            (source_root / "loop.py").write_text(
                "LOOP = 'source only'\n",
                encoding="utf-8",
            )

            payload = build_no_provider_child_provenance_diagnostics(
                agent_os_root=root,
                selected_profile="thought-core-v0",
                process_env={
                    "THOUGHT_CORE_CODEX_CLI_PATH": r"C:\private\codex.cmd",
                    "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT": r"C:\private\sword-agent-os",
                    "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT": "xhigh",
                },
            )

        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "provider_capable_enabled_after_env_import",
        )
        self.assertEqual(
            payload["provider_config_presence_class"],
            "provider_config_present_nonempty_redacted",
        )
        for key in (
            "THOUGHT_CORE_LLM_PROVIDER",
            "THOUGHT_CORE_CODEX_CLI_PATH",
            "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT",
            "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT",
            "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
            "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
        ):
            self.assertEqual(
                payload["provider_config_key_classes"][key],
                "present_nonempty_redacted",
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(r"C:\private", serialized)
        self.assertNotIn("0.142.0", serialized)
        self.assertNotIn("xhigh", serialized)
        self.assert_json_string_values_are_publication_safe(payload)

    def test_thought_core_child_diagnostics_reports_running_import_hashes(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "THOUGHT_CORE_LLM_ENABLED": "0",
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
                "OPENAI_API_KEY": "private-test-key",
            },
            clear=True,
        ):
            payload = build_child_provenance_diagnostics(
                selected_profile="thought-core-v0",
                ops_profile="thought-core-v0",
                top_level_text_present_class="present_redacted",
                top_level_text_marker_class="happy_marker_plus_move_marker",
                context_ref_payload_class="happy_expression_motion_request",
            )

        self.assertEqual(
            payload["child_process_no_provider_binding_class"],
            "no_provider_or_fallback_only_bound_by_running_child_env_class",
        )
        self.assertEqual(
            payload["provider_config_presence_class"],
            "provider_config_present_nonempty_redacted",
        )
        self.assertEqual(
            payload["thought_core_force_no_provider_class"],
            "absent",
        )
        self.assertTrue(payload["runtime_import_provenance_available"])
        self.assertTrue(
            payload["running_child_input_understanding_import_provenance"]["available"]
        )
        self.assertEqual(
            payload["running_child_loop_import_provenance"]["collection_point"],
            "running_thought_core_child_process",
        )
        self.assertEqual(payload["mapping_input_source"], "top_level_text")
        self.assertEqual(
            payload["marker_class_consistency"],
            "consistent_marker_and_context_label",
        )
        self.assertEqual(payload["standard_diagnostics_surface_class"], "partial")
        self.assertEqual(
            payload["diagnostics_reader_surface"],
            "direct_child_endpoint_available_normal_status_reader_missing",
        )
        self.assert_json_string_values_are_publication_safe(payload)

    def test_thought_core_server_exposes_no_turn_diagnostics_endpoint(self) -> None:
        env = {
            "THOUGHT_CORE_LLM_ENABLED": "0",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
        }
        with patch.dict("os.environ", env, clear=False):
            server = create_server("127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                url = (
                    f"http://127.0.0.1:{port}"
                    "/diagnostics/no-provider-child-provenance"
                    "?selected_profile=thought-core-v0"
                    "&ops_profile=thought-core-v0"
                    "&top_level_text_present_class=present_redacted"
                    "&top_level_text_marker_class=happy_marker_plus_move_marker"
                    "&context_ref_payload_class=happy_expression_motion_request"
                )
                with request.urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(
                    payload["diagnostics_schema_version"],
                    "thought_core.no_provider_child_provenance.v0",
                )
                self.assertEqual(
                    payload["child_process_no_provider_binding_class"],
                    "no_provider_or_fallback_only_bound_by_running_child_env_class",
                )
                self.assertTrue(payload["runtime_import_provenance_available"])
                self.assertEqual(
                    payload["standard_diagnostics_surface_class"],
                    "partial",
                )
                self.assertFalse(payload["one_off_artifact_only"])
                self.assertNotIn("events", payload)
                self.assert_json_string_values_are_publication_safe(payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def assert_json_string_values_are_publication_safe(self, payload: object) -> None:
        for value in iter_string_values(payload):
            self.assertNotIn("http://", value)
            self.assertNotIn("https://", value)
            self.assertNotIn(":\\", value)
            self.assertNotIn("private-test-key", value)
            self.assertNotIn("mock-private-key", value)


def iter_string_values(value: object):
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_string_values(child)
    elif isinstance(value, str):
        yield value


if __name__ == "__main__":
    unittest.main()
