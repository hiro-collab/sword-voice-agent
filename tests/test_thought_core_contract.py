import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.responders import ResponderResult  # noqa: E402
from thought_core.server import create_server  # noqa: E402
from thought_core.tools import HomeControlHttpTools, HomeControlToolConfig, MockThoughtTools  # noqa: E402


TURN = {
    "text": "電気つけて",
    "turn_id": "turn_test_001",
    "session_id": "living_room_main",
    "locale": "ja-JP",
    "context_refs": {
        "environment_snapshot": "env_abc123",
        "voice_turn": "voice_789",
    },
}

GENERAL_TURN = {
    **TURN,
    "text": "マイクテストです。聞こえていますか？",
    "turn_id": "turn_general_001",
}


class StaticResponder:
    adapter_kind = "test_responder"
    provider = "test"
    model = "test-model"

    def __init__(self, *, used_llm: bool = True) -> None:
        self.used_llm = used_llm

    def respond(self, turn):  # type: ignore[no-untyped-def]
        return ResponderResult(
            speech="聞こえています。応答境界も動いています。",
            display="聞こえています。応答境界も動いています。",
            status="llm_response" if self.used_llm else "local_fallback",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=self.used_llm,
            metadata={"turn_text_len": len(turn.text)},
        )


class ThoughtCoreContractTest(TestCase):
    def test_common_metadata_is_carried_by_all_events(self) -> None:
        events = ThoughtLoop().run_dicts(TURN)

        self.assertGreater(len(events), 0)
        for expected_seq, event in enumerate(events, start=1):
            self.assertEqual(event["schema_version"], "thought-core.event.v0")
            self.assertTrue(event["event_id"].startswith("evt_"))
            self.assertEqual(event["turn_id"], "turn_test_001")
            self.assertEqual(event["session_id"], "living_room_main")
            self.assertEqual(event["seq"], expected_seq)
            self.assertEqual(event["source"], "thought-core")
            self.assertIn("timestamp", event)

    def test_event_order_for_minimal_light_demo(self) -> None:
        event_types = [event["type"] for event in ThoughtLoop().run_dicts(TURN)]

        self.assertEqual(
            event_types,
            [
                "tool.started",
                "tool.result",
                "observation.received",
                "tool.started",
                "tool.result",
                "action.proposed",
                "assistant.speech_delta",
                "assistant.message",
                "tool.started",
                "tool.result",
                "tool.started",
                "tool.result",
                "observation.received",
                "assistant.speech_delta",
                "assistant.message",
                "turn.completed",
            ],
        )

    def test_light_off_uses_home_tool_path(self) -> None:
        tools = MockThoughtTools(light_on=True)
        turn = {
            **TURN,
            "text": "リビングの電気を消して",
            "turn_id": "turn_light_off",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        action_event = next(event for event in events if event["type"] == "action.proposed")
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertNotIn("responder.started", event_types)
        self.assertEqual(action_event["data"]["action"]["action_id"], "light_off")
        self.assertEqual(action_event["data"]["action"]["expected_state"], "off")
        self.assertIn("了解、リビングの電気を消すね。", speeches)
        self.assertIn("リビングの電気を消したよ。", speeches)
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_room_light_state_query_uses_environment_without_home_execute(self) -> None:
        tools = MockThoughtTools(light_on=True)
        turn = {
            **TURN,
            "text": "電気ついてる？",
            "turn_id": "turn_room_light_state_query",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertIn("environment.state_query_answer", event_types)
        self.assertNotIn("responder.started", event_types)
        self.assertEqual(tool_names, ["environment.observe"])
        self.assertEqual(tools.execute_calls, [])
        self.assertIn("カメラ推定", speeches[-1])
        self.assertEqual(events[-1]["data"]["status"], "state_answer")
        self.assertEqual(events[-1]["data"]["state"], "on")

    def test_light_action_adds_room_light_feedback_when_vision_mismatches(self) -> None:
        tools = MockThoughtTools(light_on=False)
        turn = {
            **TURN,
            "text": "リビングの電気をつけて",
            "turn_id": "turn_room_light_feedback",
            "context_refs": {
                **TURN["context_refs"],
                "mock_after_action_room_light_state": "off",
                "mock_after_action_room_light_confidence_label": "high",
                "mock_after_action_wait_matched": "true",
            },
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        message = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]
        pending = next(
            event for event in events if event["type"] == "state_query.feedback_pending"
        )

        self.assertIn("state_query.feedback_pending", event_types)
        self.assertIn("映像", message)
        self.assertEqual(pending["data"]["state_query_id"], "room_light")
        self.assertEqual(pending["data"]["expected_state"], "on")
        self.assertEqual(pending["data"]["predicted_state"], "off")
        self.assertTrue(events[-1]["data"]["post_action_feedback_pending"])
        self.assertEqual(events[-1]["data"]["room_light_wait_matched"], True)

    def test_environment_noop_action_skips_home_execute(self) -> None:
        class NoopLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                observation = super().environment_observe(turn, reason=reason)
                observation["environment"]["actions"] = [
                    {
                        "action_id": "light_off",
                        "aliases": ["電気を消して"],
                        "label": "ライトを消す",
                        "appliance_id": "light",
                        "target_label": "電気",
                        "verb": "消す",
                        "pre_action_phrase": "電気を消す",
                        "expected_state": "off",
                        "available": False,
                        "noop": True,
                        "reason": "already_off",
                        "reason_text": "電気はすでに消えています",
                    }
                ]
                return observation

        tools = NoopLightTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気を消して",
                "turn_id": "turn_light_noop",
            }
        )
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        message = next(
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        )

        self.assertIn("action.skipped", [event["type"] for event in events])
        self.assertEqual(tool_names, ["environment.observe", "home.preview"])
        self.assertEqual(tools.execute_calls, [])
        self.assertIn("すでに消えています", message)
        self.assertEqual(events[-1]["data"]["status"], "noop")

    def test_vacuum_return_uses_dify_action_id_dictionary(self) -> None:
        tools = MockThoughtTools()
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "掃除機を戻して",
                "turn_id": "turn_vacuum_return",
            }
        )
        action_event = next(event for event in events if event["type"] == "action.proposed")
        message = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]

        self.assertEqual(action_event["data"]["action"]["action_id"], "vacuum_return")
        self.assertIn("掃除機を戻した", message)
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_home_control_http_tools_call_bridge_execute(self) -> None:
        calls: list[dict[str, object]] = []
        state = {"light": "on"}

        class BridgeHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                calls.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                self._send_json(
                    {
                        "snapshot_id": "env_test",
                        "appliances": {
                            "light": {
                                "state": state["light"],
                                "updated_at": "2026-05-08T00:00:00+00:00",
                                "source": "home_assistant",
                            }
                        },
                        "last_home_assistant_events": [],
                        "state_queries": {},
                    }
                )

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                if self.path == "/actions/light_off/preview":
                    self._send_json(
                        {
                            "ok": True,
                            "action_id": "light_off",
                            "executed": False,
                            "status": "preview",
                            "confirmation_required": False,
                            "message": "preview",
                            "speak": "preview",
                            "expected_state": "off",
                            "expected_effect": {"expected_state": "off"},
                        }
                    )
                    return
                if self.path == "/actions/light_off/execute":
                    state["light"] = "off"
                    self._send_json(
                        {
                            "ok": True,
                            "action_id": "light_off",
                            "executed": True,
                            "status": "submitted",
                            "confirmation_required": False,
                            "message": "done",
                            "speak": "done",
                            "execution_id": "exec_test",
                            "expected_state": "off",
                            "expected_effect": {"expected_state": "off"},
                        }
                    )
                    return
                self._send_json({"ok": False, "error": "not_found"}, status=404)

            def log_message(self, format, *args):  # type: ignore[no-untyped-def]  # noqa: A002
                return

            def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            tools = HomeControlHttpTools(
                HomeControlToolConfig(
                    bridge_base_url=base_url,
                    api_token="bridge-token",
                    environment_state_url=f"{base_url}/environment/current",
                    environment_api_token="environment-token",
                    timeout_s=2,
                )
            )
            events = ThoughtLoop(tools=tools).run_dicts(
                {
                    **TURN,
                    "text": "リビングの電気を消して",
                    "turn_id": "turn_bridge_light_off",
                }
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(events[-1]["data"]["status"], "success")
        post_paths = [call["path"] for call in calls if call["method"] == "POST"]
        self.assertEqual(post_paths, ["/actions/light_off/preview", "/actions/light_off/execute"])
        get_paths = [str(call["path"]) for call in calls if call["method"] == "GET"]
        self.assertEqual(get_paths[0], "/environment/current")
        self.assertTrue(
            any(
                path.startswith("/environment/current?")
                and "wait_for=room_light" in path
                and "timeout_ms=1500" in path
                for path in get_paths
            )
        )
        execute_call = calls[2]
        self.assertEqual(execute_call["authorization"], "Bearer bridge-token")
        self.assertEqual(execute_call["body"]["source"], "thought-core")
        self.assertEqual(execute_call["body"]["request_id"], "turn_bridge_light_off-attempt-1")

    def test_general_turn_uses_responder_boundary(self) -> None:
        events = ThoughtLoop(responder=StaticResponder()).run_dicts(GENERAL_TURN)
        event_types = [event["type"] for event in events]

        self.assertEqual(
            event_types,
            [
                "responder.started",
                "responder.completed",
                "assistant.speech_delta",
                "assistant.message",
                "turn.completed",
            ],
        )
        self.assertEqual(events[0]["data"]["boundary"], "thought-core.turn_responder.v0")
        self.assertEqual(events[1]["data"]["adapter_kind"], "test_responder")
        self.assertTrue(events[1]["data"]["used_llm"])
        self.assertEqual(events[3]["data"]["speech"], "聞こえています。応答境界も動いています。")
        self.assertEqual(events[-1]["data"]["status"], "llm_response")

    def test_general_turn_can_fall_back_without_llm(self) -> None:
        events = ThoughtLoop(responder=StaticResponder(used_llm=False)).run_dicts(
            GENERAL_TURN
        )

        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "local_fallback")
        self.assertFalse(events[-1]["data"]["used_llm"])

    def test_tool_started_and_result_share_tool_call_id(self) -> None:
        events = ThoughtLoop().run_dicts(TURN)
        pending: dict[str, str] = {}
        paired = 0

        for event in events:
            if event["type"] not in {"tool.started", "tool.result"}:
                continue
            data = event["data"]
            tool_call_id = data["tool_call_id"]
            if event["type"] == "tool.started":
                pending[tool_call_id] = data["tool"]
            else:
                self.assertEqual(pending.pop(tool_call_id), data["tool"])
                paired += 1

        self.assertEqual(pending, {})
        self.assertGreaterEqual(paired, 1)

    def test_tool_failure_can_be_retried_by_thought_loop(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=1)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)

        execute_results = [
            event
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        ]
        self.assertEqual(len(execute_results), 2)
        self.assertEqual(len(tools.execute_calls), 2)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_tool_failure_requests_feedback_when_retry_exhausted(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=3)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)
        event_types = [event["type"] for event in events]

        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(tools.execute_calls), 2)

    def test_mock_context_ref_can_demo_retry_success(self) -> None:
        tools = MockThoughtTools()
        turn = {
            **TURN,
            "turn_id": "turn_retry_demo",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
                "mock_execute_failures_before_success": "1",
            },
        }

        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(turn)
        execute_results = [
            event
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        ]

        self.assertEqual(len(execute_results), 2)
        self.assertEqual(execute_results[0]["data"]["status"], "failed")
        self.assertEqual(execute_results[1]["data"]["status"], "accepted")
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_mock_context_ref_can_demo_feedback_request(self) -> None:
        tools = MockThoughtTools()
        turn = {
            **TURN,
            "turn_id": "turn_feedback_demo",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
                "mock_execute_failures_before_success": "3",
            },
        }

        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(turn)
        event_types = [event["type"] for event in events]

        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(tools.execute_attempts_by_turn["turn_feedback_demo"], 2)

    def test_mock_failure_context_is_scoped_to_turn(self) -> None:
        tools = MockThoughtTools()
        failing_turn = {
            **TURN,
            "turn_id": "turn_failure_scoped",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
                "mock_execute_failures_before_success": "3",
            },
        }
        success_turn = {
            **TURN,
            "turn_id": "turn_success_scoped",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
            },
        }

        failing_events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(
            failing_turn
        )
        success_events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(
            success_turn
        )

        self.assertEqual(failing_events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(success_events[-1]["data"]["status"], "success")
        self.assertEqual(tools.execute_attempts_by_turn["turn_failure_scoped"], 2)
        self.assertEqual(tools.execute_attempts_by_turn["turn_success_scoped"], 1)

    def test_secret_values_are_redacted_from_events(self) -> None:
        tools = MockThoughtTools(include_secret_in_execute_result=True)
        events = ThoughtLoop(tools=tools).run_dicts(TURN)
        serialized = json.dumps(events, ensure_ascii=False)

        self.assertNotIn("mock-token-that-must-not-leak", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_get_root_returns_api_index(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]

            with request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                content_type = response.headers["Content-Type"]

            self.assertEqual(content_type, "application/json; charset=utf-8")
            self.assertEqual(payload["service"], "thought-core")
            self.assertEqual(payload["kind"], "api")
            self.assertIn("POST /turn", payload["endpoints"]["turn_json"])
            self.assertIn("sword-console", payload["console_command"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_post_turn_stream_returns_sse_events(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps(TURN).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}/turn?stream=true",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )

            with request.urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8")
                content_type = response.headers["Content-Type"]

            self.assertEqual(content_type, "text/event-stream; charset=utf-8")
            self.assertIn("event: assistant.message", payload)
            self.assertIn("event: turn.completed", payload)
            self.assertIn('"turn_id":"turn_test_001"', payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_get_turn_stream_returns_sse_events(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]

            with request.urlopen(
                "http://127.0.0.1:"
                f"{port}/turn/stream?text=%E9%9B%BB%E6%B0%97%E3%81%A4%E3%81%91%E3%81%A6"
                "&turn_id=turn_get_test&session_id=living_room_main",
                timeout=5,
            ) as response:
                payload = response.read().decode("utf-8")
                content_type = response.headers["Content-Type"]

            self.assertEqual(content_type, "text/event-stream; charset=utf-8")
            self.assertIn("event: assistant.message", payload)
            self.assertIn("event: turn.completed", payload)
            self.assertIn('"turn_id":"turn_get_test"', payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
