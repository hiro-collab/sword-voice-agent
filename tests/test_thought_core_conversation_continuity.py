import json
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.conversation_continuity import ConversationContinuity  # noqa: E402
from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.responders import (  # noqa: E402
    TRUSTED_LOCAL_HISTORY_CAPABILITY,
    OpenAICompatibleChatResponder,
    ResponderResult,
    _codex_cli_prompt,
    _conversation_history_messages,
    _response_context_prompt,
)
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class _RecordingResponder:
    adapter_kind = "continuity_recording"
    provider = "test"
    model = "test"

    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        self.contexts.append(dict(response_context or {}))
        speech = "前の内容を踏まえて返します。"
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={},
        )


class ConversationContinuityTest(TestCase):
    def test_bounded_recent_turns_and_latest_correction_win(self) -> None:
        clock = _Clock()
        continuity = ConversationContinuity(
            recent_turn_limit=2,
            ttl_seconds=60,
            clock=clock,
        )
        self._complete(
            continuity,
            turn_id="turn-1",
            user="候補Aを採用します",
            assistant="候補Aで進めます。",
        )
        self._complete(
            continuity,
            turn_id="turn-2",
            user="候補Bは却下します",
            assistant="候補Bは使いません。",
        )
        self._complete(
            continuity,
            turn_id="turn-3",
            user="いや、訂正。候補Cだけを採用します",
            assistant="候補Cだけに絞ります。",
        )
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-4",
            user_text="その方針で続けて",
        )

        context = continuity.context_for_response(session_id="session")

        self.assertEqual(
            [turn["turn_id"] for turn in context["recent_turns"]],
            ["turn-2", "turn-3"],
        )
        decisions = context["rolling_state"]["decisions"]
        self.assertEqual(decisions[0]["status"], "correction")
        self.assertIn("候補C", decisions[0]["proposition"])
        self.assertEqual(decisions[-1]["status"], "accepted")
        self.assertFalse(any("候補A" in item["proposition"] for item in decisions))
        self.assertFalse(any("候補B" in item["proposition"] for item in decisions))
        self.assertEqual(
            context["referent_resolution"],
            "latest_completed_turn_available",
        )

    def test_topic_switch_cancel_expiry_and_secret_withholding(self) -> None:
        clock = _Clock()
        continuity = ConversationContinuity(ttl_seconds=10, clock=clock)
        self._complete(
            continuity,
            turn_id="turn-1",
            user="前の候補を採用します",
            assistant="前の候補で進めます。",
        )
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-2",
            user_text="ところで、別の話をしよう",
        )
        switched = continuity.context_for_response(session_id="session")
        self.assertEqual(switched["recent_turns"], [])
        self.assertEqual(
            switched["rolling_state"]["latest_transition"],
            "topic_switched",
        )

        continuity.begin_turn(
            session_id="session",
            turn_id="turn-3",
            user_text="api_key=private-value-123456",
        )
        withheld = continuity.public_summary(session_id="session")
        self.assertEqual(withheld["withheld_turn_count"], 1)
        self.assertNotIn(
            "private-value-123456",
            str(continuity.context_for_response(session_id="session")),
        )
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-3b",
            user_text="パスワードはlocal-private-value",
        )
        self.assertNotIn(
            "local-private-value",
            str(continuity.context_for_response(session_id="session")),
        )

        continuity.begin_turn(
            session_id="session",
            turn_id="turn-4",
            user_text="前の話はなし",
        )
        cancelled = continuity.context_for_response(session_id="session")
        self.assertEqual(cancelled["recent_turns"], [])
        self.assertEqual(
            cancelled["rolling_state"]["latest_transition"],
            "cancelled",
        )

        clock.value += 11
        self.assertEqual(continuity.context_for_response(session_id="session"), {})

    def test_ambiguous_referent_without_anchor_requires_clarification(self) -> None:
        continuity = ConversationContinuity()
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-1",
            user_text="それを続けて",
        )

        context = continuity.context_for_response(session_id="session")

        self.assertEqual(context["referent_resolution"], "clarification_required")
        self.assertIn(
            "ask_when_referent_or_topic_is_ambiguous",
            context["rules"],
        )
        self.assertEqual(
            context["retention"]["older_history_retrieval"],
            "disabled",
        )

    def test_assistant_question_is_one_turn_open_item_and_sessions_do_not_mix(self) -> None:
        continuity = ConversationContinuity()
        self._complete(
            continuity,
            turn_id="turn-1",
            user="候補について相談したい",
            assistant="候補Aと候補Bのどちらを先に見ますか？",
        )
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-2",
            user_text="候補Bです",
        )
        current = continuity.context_for_response(session_id="session")
        self.assertEqual(
            current["rolling_state"]["open_items"][0]["source"],
            "assistant",
        )
        self.assertIn(
            "どちらを先に見ますか",
            current["rolling_state"]["open_items"][0]["question"],
        )

        continuity.begin_turn(
            session_id="session",
            turn_id="turn-3",
            user_text="続けて",
        )
        next_context = continuity.context_for_response(session_id="session")
        self.assertEqual(next_context["rolling_state"]["open_items"], [])
        self.assertEqual(
            continuity.context_for_response(session_id="other-session"),
            {},
        )

    def test_loop_passes_private_context_but_events_keep_text_free_summary(self) -> None:
        responder = _RecordingResponder()
        loop = ThoughtLoop(
            tools=MockThoughtTools(),
            responder=responder,
        )
        first_user = "候補Aは使わず、候補Bを検討しよう"
        loop.run_dicts(self._turn("turn-1", first_user))
        events = loop.run_dicts(self._turn("turn-2", "さっきの話を続けよう"))

        private = responder.contexts[-1]["conversation_continuity"]
        self.assertEqual(private["recent_turns"][0]["user_text"], first_user)
        prompt = _response_context_prompt(responder.contexts[-1])
        self.assertIn("latest user correction wins", prompt)
        self.assertIn("Ambiguous references are resolved by Thought Core", prompt)

        completed = next(
            event for event in events if event["type"] == "responder.completed"
        )
        public = completed["data"]["response_context"]
        self.assertNotIn("conversation_continuity", public)
        self.assertNotIn(first_user, str(public))
        self.assertEqual(
            public["conversation_continuity_summary"]["recent_turn_count"],
            1,
        )
        self.assertFalse(
            public["conversation_continuity_summary"]["raw_text_persisted"]
        )

    def test_nfkc_secret_withholds_the_whole_turn(self) -> None:
        continuity = ConversationContinuity()
        self._complete(
            continuity,
            turn_id="turn-1",
            user="候補Aを採用します",
            assistant="候補Aで進めます。",
        )
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-2",
            user_text=(
                "訂正。それを使って。"
                "ａｐｉ＿ｋｅｙ＝ｓｋ－ｐｒｉｖａｔｅ－１２３４５６７８"
            ),
        )

        context = continuity.context_for_response(session_id="session")

        self.assertEqual(context["withheld_turn_count"], 1)
        self.assertEqual(context["current_referents"], [])
        self.assertEqual(
            [item["status"] for item in context["rolling_state"]["decisions"]],
            ["accepted"],
        )
        self.assertNotIn("private", str(context).lower())

    def test_private_assistant_fragment_withholds_the_whole_pending_turn(self) -> None:
        continuity = ConversationContinuity()
        user_text = "候補Aを採用して、それを進めます"
        safe_before = "候補Aを進めます。"
        private_fragment = "api_key=private-value-123456"
        safe_after = "この後続断片も保持しません。"
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-private-assistant",
            user_text=user_text,
        )
        continuity.record_assistant(
            session_id="session",
            turn_id="turn-private-assistant",
            assistant_text=safe_before,
        )
        continuity.record_assistant(
            session_id="session",
            turn_id="turn-private-assistant",
            assistant_text=private_fragment,
        )
        continuity.record_assistant(
            session_id="session",
            turn_id="turn-private-assistant",
            assistant_text=safe_after,
        )

        state = continuity._sessions["session"]
        pending = state.pending
        context = continuity.context_for_response(session_id="session")
        summary = continuity.public_summary(session_id="session")

        self.assertIsNotNone(pending)
        self.assertTrue(pending.withheld)
        self.assertIsNone(pending.user_text)
        self.assertEqual(pending.assistant_parts, [])
        self.assertEqual(pending.referents, [])
        self.assertEqual(pending.signal, "")
        self.assertEqual(context, {})
        self.assertEqual(summary["recent_turn_count"], 0)
        self.assertEqual(summary["referent_count"], 0)
        self.assertEqual(summary["decision_count"], 0)
        self.assertEqual(summary["withheld_turn_count"], 1)
        for raw_text in (user_text, safe_before, private_fragment, safe_after):
            self.assertNotIn(raw_text, str(context))

        continuity.begin_turn(
            session_id="session",
            turn_id="turn-safe",
            user_text="新しい安全な会話です",
        )
        self.assertEqual(continuity.context_for_response(session_id="session"), {})
        continuity.record_assistant(
            session_id="session",
            turn_id="turn-safe",
            assistant_text="安全な返答です。",
        )
        continuity.begin_turn(
            session_id="session",
            turn_id="turn-after-safe",
            user_text="次の話です",
        )
        retained = continuity.context_for_response(session_id="session")
        self.assertEqual(len(retained["recent_turns"]), 1)
        self.assertEqual(
            retained["recent_turns"][0]["user_text"],
            "新しい安全な会話です",
        )
        self.assertEqual(
            retained["recent_turns"][0]["assistant_text"],
            "安全な返答です。",
        )

    def test_ttl_and_lru_eviction_erase_retained_values(self) -> None:
        clock = _Clock()
        continuity = ConversationContinuity(
            session_limit=1,
            ttl_seconds=10,
            clock=clock,
        )
        continuity.begin_turn(
            session_id="session-1",
            turn_id="turn-1",
            user_text="消去対象の会話です",
        )
        continuity.record_assistant(
            session_id="session-1",
            turn_id="turn-1",
            assistant_text="消去対象の返答です。",
        )
        evicted_state = continuity._sessions["session-1"]
        evicted_pending = evicted_state.pending

        continuity.begin_turn(
            session_id="session-2",
            turn_id="turn-2",
            user_text="別セッションです",
        )

        self.assertNotIn("session-1", continuity._sessions)
        self.assertEqual(evicted_state.recent_turns, [])
        self.assertEqual(evicted_state.decisions, [])
        self.assertIsNone(evicted_state.pending)
        self.assertIsNotNone(evicted_pending)
        self.assertIsNone(evicted_pending.user_text)
        self.assertEqual(evicted_pending.assistant_parts, [])
        self.assertEqual(evicted_pending.turn_id, "")

        ttl_state = continuity._sessions["session-2"]
        ttl_pending = ttl_state.pending
        clock.value += 11
        self.assertEqual(
            continuity.context_for_response(session_id="session-2"),
            {},
        )
        self.assertNotIn("session-2", continuity._sessions)
        self.assertIsNone(ttl_state.pending)
        self.assertIsNotNone(ttl_pending)
        self.assertIsNone(ttl_pending.user_text)
        self.assertEqual(ttl_pending.turn_id, "")

    def test_ambiguous_reference_is_code_blocked_without_responder_or_memory(self) -> None:
        responder = _RecordingResponder()
        loop = ThoughtLoop(
            tools=MockThoughtTools(),
            responder=responder,
        )

        events = loop.run_dicts(self._turn("turn-1", "それを続けて"))

        self.assertEqual(responder.contexts, [])
        self.assertFalse(
            any(
                event["type"] == "tool.started"
                and event["data"]["tool"] == "memory.retrieve"
                for event in events
            )
        )
        completed = next(
            event for event in events if event["type"] == "responder.completed"
        )
        self.assertEqual(
            completed["data"]["status"],
            "continuity_clarification_required",
        )
        self.assertFalse(completed["data"]["used_llm"])
        assistant = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]
        self.assertEqual(
            assistant,
            "どの話や項目を指しているか、もう少し具体的に教えてください。",
        )

    def test_history_transport_is_default_deny_and_trusted_local_response_only(
        self,
    ) -> None:
        marker = "直前履歴の秘密ではない検証文"
        context = {
            "previous_fragment": marker,
            "recent_fragments": [marker],
            "current_stage": "general_responder",
            "conversation_continuity": {
                "recent_turns": [
                    {
                        "user_text": marker,
                        "assistant_text": "直前の返答",
                    }
                ]
            },
        }
        system_context = _response_context_prompt(context)
        history_messages = _conversation_history_messages(context)
        turn = TurnInput.from_mapping(self._turn("turn-current", "続きを相談したい"))
        operate_prompt = _codex_cli_prompt(
            turn,
            context,
            mode="operate",
            sandbox="workspace-write",
            approval="never",
        )
        response_prompt = _codex_cli_prompt(
            turn,
            context,
            mode="respond",
            sandbox="read-only",
            approval="never",
        )
        trusted_history = _conversation_history_messages(
            context,
            capability=TRUSTED_LOCAL_HISTORY_CAPABILITY,
            destination_is_loopback=True,
            response_only=True,
        )
        capability_configured_response_prompt = _codex_cli_prompt(
            turn,
            context,
            mode="respond",
            sandbox="read-only",
            approval="never",
            history_capability=TRUSTED_LOCAL_HISTORY_CAPABILITY,
        )
        capability_configured_operate_prompt = _codex_cli_prompt(
            turn,
            context,
            mode="operate",
            sandbox="workspace-write",
            approval="never",
            history_capability=TRUSTED_LOCAL_HISTORY_CAPABILITY,
        )

        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "確認しました。"}}]}
        ).encode("utf-8")
        non_loopback_adapter = OpenAICompatibleChatResponder(
            base_url="https://example.invalid/v1",
            api_key="test-key",
            model="test-model",
            conversation_history_capability=TRUSTED_LOCAL_HISTORY_CAPABILITY,
        )
        with patch(
            "thought_core.responders.request.urlopen",
            return_value=response,
        ) as non_loopback_urlopen:
            non_loopback_adapter.respond(turn, response_context=context)
        non_loopback_payload = json.loads(
            non_loopback_urlopen.call_args.args[0].data.decode("utf-8")
        )

        loopback_adapter = OpenAICompatibleChatResponder(
            base_url="http://127.0.0.1:8000/v1",
            api_key="",
            model="test-model",
            conversation_history_capability=TRUSTED_LOCAL_HISTORY_CAPABILITY,
        )
        with patch(
            "thought_core.responders.request.urlopen",
            return_value=response,
        ) as loopback_urlopen:
            loopback_adapter.respond(turn, response_context=context)
        loopback_payload = json.loads(
            loopback_urlopen.call_args.args[0].data.decode("utf-8")
        )

        self.assertNotIn(marker, system_context)
        self.assertEqual(history_messages, [])
        self.assertNotIn(marker, response_prompt)
        self.assertNotIn(marker, operate_prompt)
        self.assertNotIn(
            marker,
            json.dumps(non_loopback_payload, ensure_ascii=False),
        )
        self.assertIn(marker, json.dumps(loopback_payload, ensure_ascii=False))
        self.assertEqual(trusted_history[0]["role"], "user")
        self.assertEqual(trusted_history[0]["content"], marker)
        self.assertNotIn(marker, capability_configured_response_prompt)
        self.assertNotIn(marker, capability_configured_operate_prompt)

    def _complete(
        self,
        continuity: ConversationContinuity,
        *,
        turn_id: str,
        user: str,
        assistant: str,
    ) -> None:
        continuity.begin_turn(
            session_id="session",
            turn_id=turn_id,
            user_text=user,
        )
        continuity.record_assistant(
            session_id="session",
            turn_id=turn_id,
            assistant_text=assistant,
        )

    def _turn(self, turn_id: str, text: str) -> dict[str, object]:
        return {
            "text": text,
            "turn_id": turn_id,
            "session_id": "continuity-session",
            "locale": "ja-JP",
            "context_refs": {},
        }
