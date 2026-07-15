import json
import sys
import threading
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.events import EventFactory  # noqa: E402
from thought_core.execution_deadline import (  # noqa: E402
    TURN_DEADLINE_EXCEEDED,
    TurnDeadlineExceeded,
    TurnDeadlineInvalid,
    TurnExecutionDeadline,
    current_execution_deadline,
    ensure_execution_active,
    execution_deadline_scope,
    issue_turn_execution_deadline,
)
from thought_core.loop import ThoughtLoop, _EventBuffer  # noqa: E402
from thought_core.responders import ResponderResult  # noqa: E402
from thought_core.server import create_server  # noqa: E402


GENERAL_TURN = {
    "text": "今日は短く話しましょう。",
    "turn_id": "turn_deadline_general_001",
    "session_id": "session_deadline_general",
    "locale": "ja-JP",
    "context_refs": {},
}


class StaticResponder:
    adapter_kind = "deadline_test"
    provider = "test"
    model = "test"

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        return ResponderResult(
            speech="期限内の応答です。",
            display="期限内の応答です。",
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={},
        )


class ThoughtCoreTurnDeadlineTest(TestCase):
    def test_normal_cardinality_is_unchanged_with_valid_deadline(self) -> None:
        baseline = ThoughtLoop(responder=StaticResponder()).run_dicts(GENERAL_TURN)
        deadline = issue_turn_execution_deadline(time.monotonic() + 5.0)
        bounded = ThoughtLoop(responder=StaticResponder()).run_dicts(
            GENERAL_TURN,
            execution_deadline=deadline,
        )

        self.assertEqual(
            [event["type"] for event in bounded],
            [event["type"] for event in baseline],
        )
        self.assertEqual(len(bounded), len(baseline))

    def test_stale_caller_and_replayed_tokens_fail_closed(self) -> None:
        clock = {"now": 10.0}
        with patch(
            "thought_core.execution_deadline.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            stale = issue_turn_execution_deadline(11.0)
            clock["now"] = 12.0
            with self.assertRaisesRegex(TurnDeadlineExceeded, TURN_DEADLINE_EXCEEDED):
                ThoughtLoop(responder=StaticResponder()).run_dicts(
                    GENERAL_TURN,
                    execution_deadline=stale,
                )

        caller_token = TurnExecutionDeadline(time.monotonic() + 5.0)
        with self.assertRaises(TurnDeadlineInvalid):
            ThoughtLoop(responder=StaticResponder()).run_dicts(
                GENERAL_TURN,
                execution_deadline=caller_token,
            )
        foreign = issue_turn_execution_deadline(
            time.monotonic() + 5.0,
            turn_key=("turn_other", "session_other"),
        )
        with self.assertRaises(TurnDeadlineInvalid):
            ThoughtLoop(responder=StaticResponder()).run_dicts(
                GENERAL_TURN,
                execution_deadline=foreign,
            )
        with self.assertRaises(TypeError):
            json.dumps(issue_turn_execution_deadline(time.monotonic() + 5.0))

        replayed = issue_turn_execution_deadline(time.monotonic() + 5.0)
        with execution_deadline_scope(replayed):
            ensure_execution_active()
        with self.assertRaises(TurnDeadlineInvalid):
            with execution_deadline_scope(replayed):
                pass

    def test_expiry_during_responder_blocks_assistant_publication_and_state(self) -> None:
        clock = {"now": 100.0}
        streamed: list[dict[str, object]] = []

        class ExpiringResponder(StaticResponder):
            def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
                clock["now"] = 102.0
                return super().respond(turn, response_context=response_context)

        loop = ThoughtLoop(responder=ExpiringResponder())
        with patch(
            "thought_core.execution_deadline.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            deadline = issue_turn_execution_deadline(101.0)
            with self.assertRaises(TurnDeadlineExceeded):
                loop.run_dicts(
                    GENERAL_TURN,
                    event_sink=streamed.append,
                    execution_deadline=deadline,
                )

        self.assertNotIn("responder.completed", [event["type"] for event in streamed])
        self.assertNotIn(
            "期限内の応答です。",
            json.dumps(streamed, ensure_ascii=False),
        )
        self.assertEqual(loop.pending_confirmations, {})
        self.assertEqual(loop.pending_action_reviews, {})
        self.assertEqual(loop.pending_state_queries, {})
        self.assertNotIn(
            "期限内の応答です。",
            loop.recent_speech_by_session.get("session_deadline_general", []),
        )

    def test_expiry_during_tool_call_blocks_result_and_nested_state_mutation(self) -> None:
        clock = {"now": 200.0}
        streamed = []
        loop = ThoughtLoop(responder=StaticResponder())
        events = _EventBuffer(streamed.append)
        factory = EventFactory("turn_tool_deadline", "session_tool_deadline")

        def expiring_call():
            clock["now"] = 202.0
            return {"status": "ok"}

        with patch(
            "thought_core.execution_deadline.time.monotonic",
            side_effect=lambda: clock["now"],
        ):
            deadline = issue_turn_execution_deadline(201.0)
            with self.assertRaises(TurnDeadlineExceeded):
                with execution_deadline_scope(deadline):
                    loop.pending_state_queries["session_tool_deadline"] = {
                        "status": "pending"
                    }
                    try:
                        loop._call_tool(events, factory, "test.tool", expiring_call)
                    except TurnDeadlineExceeded:
                        with self.assertRaises(TurnDeadlineExceeded):
                            loop.pending_state_queries["session_tool_deadline"][
                                "status"
                            ] = "late"
                        raise

            self.assertEqual([event.type for event in streamed], ["tool.started"])
            self.assertEqual(
                loop.pending_state_queries["session_tool_deadline"]["status"],
                "pending",
            )

    def test_concurrent_deadlines_are_context_local(self) -> None:
        first = issue_turn_execution_deadline(time.monotonic() + 5.0)
        second = issue_turn_execution_deadline(time.monotonic() + 5.0)
        barrier = threading.Barrier(2)
        results: dict[str, str] = {}

        def worker(name: str, token, cancel: bool) -> None:  # type: ignore[no-untyped-def]
            try:
                with execution_deadline_scope(token):
                    barrier.wait(timeout=2)
                    if cancel:
                        token.cancel()
                    ensure_execution_active()
                results[name] = "ok"
            except TurnDeadlineExceeded:
                results[name] = TURN_DEADLINE_EXCEEDED

        threads = [
            threading.Thread(target=worker, args=("first", first, True)),
            threading.Thread(target=worker, args=("second", second, False)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        self.assertEqual(results, {"first": TURN_DEADLINE_EXCEEDED, "second": "ok"})

    def test_same_loop_concurrent_turn_context_does_not_cross_requests(self) -> None:
        barrier_before_cancel = threading.Barrier(2)
        barrier_after_cancel = threading.Barrier(2)
        captured: dict[str, list[tuple[str, str, str, str]]] = {}
        results: dict[str, dict[str, object]] = {}
        capture_lock = threading.Lock()

        class BarrierResponder(StaticResponder):
            def __init__(self) -> None:
                self.loop: ThoughtLoop | None = None

            def _snapshot(self):  # type: ignore[no-untyped-def]
                assert self.loop is not None
                context = self.loop._request_context()
                assert context is not None
                return (
                    context.session_id,
                    context.issue_key,
                    context.turn_input.turn_id,
                    str(context.working_memory_context.get("context_id") or ""),
                )

            def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
                with capture_lock:
                    captured.setdefault(turn.turn_id, []).append(self._snapshot())
                barrier_before_cancel.wait(timeout=3)
                if turn.turn_id == "turn_shared_cancelled":
                    deadline = current_execution_deadline()
                    assert deadline is not None
                    deadline.cancel()
                barrier_after_cancel.wait(timeout=3)
                with capture_lock:
                    captured.setdefault(turn.turn_id, []).append(self._snapshot())
                return ResponderResult(
                    speech=f"response:{turn.session_id}",
                    display=f"response:{turn.session_id}",
                    status="llm_response",
                    adapter_kind=self.adapter_kind,
                    provider=self.provider,
                    model=self.model,
                    used_llm=True,
                    metadata={},
                )

        responder = BarrierResponder()
        loop = ThoughtLoop(responder=responder)
        responder.loop = loop
        turns = {
            "cancelled": {
                "text": "alpha context",
                "turn_id": "turn_shared_cancelled",
                "session_id": "session_shared_alpha",
                "locale": "ja-JP",
                "context_refs": {"event_id": "evt_shared_alpha"},
            },
            "sibling": {
                "text": "beta context",
                "turn_id": "turn_shared_sibling",
                "session_id": "session_shared_beta",
                "locale": "ja-JP",
                "context_refs": {"event_id": "evt_shared_beta"},
            },
        }

        def worker(name: str) -> None:
            turn = turns[name]
            deadline = issue_turn_execution_deadline(
                time.monotonic() + 5.0,
                turn_key=(turn["turn_id"], turn["session_id"]),
            )
            streamed: list[dict[str, object]] = []
            try:
                events = loop.run_dicts(
                    turn,
                    event_sink=streamed.append,
                    execution_deadline=deadline,
                )
                outcome = "ok"
            except TurnDeadlineExceeded:
                events = []
                outcome = TURN_DEADLINE_EXCEEDED
            results[name] = {
                "outcome": outcome,
                "events": events,
                "streamed": streamed,
                "post_context": loop._request_context(),
            }

        threads = [
            threading.Thread(target=worker, args=("cancelled",)),
            threading.Thread(target=worker, args=("sibling",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(results["cancelled"]["outcome"], TURN_DEADLINE_EXCEEDED)
        self.assertEqual(results["sibling"]["outcome"], "ok")
        self.assertIsNone(results["cancelled"]["post_context"])
        self.assertIsNone(results["sibling"]["post_context"])
        self.assertIsNone(loop._request_context())
        self.assertEqual(
            {item[0] for item in captured["turn_shared_cancelled"]},
            {"session_shared_alpha"},
        )
        self.assertEqual(
            {item[0] for item in captured["turn_shared_sibling"]},
            {"session_shared_beta"},
        )
        self.assertEqual(
            {item[2] for item in captured["turn_shared_cancelled"]},
            {"turn_shared_cancelled"},
        )
        self.assertEqual(
            {item[2] for item in captured["turn_shared_sibling"]},
            {"turn_shared_sibling"},
        )
        self.assertTrue(all(item[1] for values in captured.values() for item in values))
        self.assertEqual(
            len({captured[key][0][3] for key in captured}),
            2,
        )
        sibling_events = results["sibling"]["events"]
        self.assertIn("turn.completed", [event["type"] for event in sibling_events])
        self.assertIn(
            "response:session_shared_beta",
            json.dumps(sibling_events, ensure_ascii=False),
        )
        self.assertNotIn(
            "response:session_shared_alpha",
            json.dumps(sibling_events, ensure_ascii=False),
        )

    def test_sse_broken_pipe_cancels_only_that_request_token(self) -> None:
        class BrokenWriter:
            def write(self, data):  # type: ignore[no-untyped-def]
                raise BrokenPipeError()

            def flush(self) -> None:
                return None

        class EmittingLoop:
            def run_dicts(self, turn, *, event_sink=None, execution_deadline=None):
                with execution_deadline_scope(execution_deadline):
                    event_sink(
                        {
                            "event_id": "evt_deadline_sse",
                            "type": "assistant.message",
                            "data": {},
                        }
                    )
                return []

        server = create_server("127.0.0.1", 0, thought_loop=EmittingLoop())
        handler = object.__new__(server.RequestHandlerClass)
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        handler.wfile = BrokenWriter()
        first = issue_turn_execution_deadline(time.monotonic() + 5.0)
        second = issue_turn_execution_deadline(time.monotonic() + 5.0)
        try:
            handler._send_sse_live(
                GENERAL_TURN,
                execution_deadline=first,
            )
            with self.assertRaises(TurnDeadlineExceeded):
                first.ensure_current()
            second.ensure_current()
        finally:
            server.server_close()
