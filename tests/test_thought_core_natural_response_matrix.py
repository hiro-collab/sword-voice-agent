import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.responders import ResponderResult  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


TURN = {
    "text": "",
    "turn_id": "turn_natural_response_matrix",
    "session_id": "natural_response_matrix_session",
    "locale": "ja-JP",
    "context_refs": {},
}

CASES_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "thought_core_natural_response_cases.json"
)
MECHANICAL_FALLBACK_PHRASES = (
    "会話応答の境界",
    "今は会話応答",
    "local_fallback",
)
SCRIPTED_PREFIXES = ("了解、",)


class NaturalMatrixResponder:
    adapter_kind = "natural_matrix_test_responder"
    provider = "test"
    model = "test-model"

    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        context = dict(response_context or {})
        self.contexts.append(context)
        draft = str(context.get("semantic_draft") or "")
        speech = self._render(draft)
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={"semantic_draft_seen": bool(draft)},
        )

    def _render(self, draft: str) -> str:
        if "対象が複数" in draft:
            return "どちらを動かすか判断が分かれるので、ここでは実行せず一つずつ指定してほしい。"
        if "操作しない" in draft and "エアコン" in draft:
            return "わかった。エアコンには触らず、そのままにしておくね。"
        if "すでに" in draft and "扇風機" in draft:
            return "扇風機はもう消えているので、追加の操作はしないね。"
        if "リビングの電気" in draft and "つけた" in draft:
            return "点灯まで確認できたよ。"
        if "リビングの電気" in draft and "つけ" in draft:
            return "リビングの電気は点灯に向けて進めるね。"
        if "エアコン" in draft and "消した" in draft:
            return "オフになったところまで確認できたよ。"
        if "エアコン" in draft and "消す" in draft:
            return "エアコンをオフにするね。"
        if "ついている" in draft and "電気" in draft:
            return "電気はついているように見えるよ。"
        return "状況に合わせて、短く自然に返すね。"


class ThoughtCoreNaturalResponseMatrixTest(TestCase):
    def test_natural_response_matrix(self) -> None:
        pack = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        for case in pack["cases"]:
            with self.subTest(case=case["name"]):
                result = self._run_case(case)
                events = result["events"]
                tools = result["tools"]
                event_types = [event["type"] for event in events]
                visible_messages = self._visible_messages(events)
                visible_speech = "\n".join(
                    str(event["data"].get("speech") or "")
                    for event in visible_messages
                )
                generated_messages = [
                    event
                    for event in visible_messages
                    if event["data"].get("phrase_generation", {}).get("enabled")
                ]
                expect = case["expect"]

                self.assertGreater(generated_messages, [])
                for event in generated_messages:
                    generation = event["data"]["phrase_generation"]
                    self.assertTrue(generation.get("used_llm"))
                    self.assertEqual(
                        generation.get("adapter_kind"),
                        "natural_matrix_test_responder",
                    )
                self.assert_no_exact_duplicate_visible_speech(visible_messages)
                self.assert_no_scripted_shape(generated_messages)

                for event_type in expect.get("event_types", []):
                    self.assertIn(event_type, event_types)
                for phrase in expect.get("speech_contains", []):
                    self.assertIn(str(phrase), visible_speech)
                for phrase in expect.get("speech_not_contains", []):
                    self.assertNotIn(str(phrase), visible_speech)
                for phrase in MECHANICAL_FALLBACK_PHRASES:
                    self.assertNotIn(phrase, visible_speech)
                for phrase, max_count in expect.get(
                    "speech_occurrences_max",
                    {},
                ).items():
                    self.assertLessEqual(
                        visible_speech.count(str(phrase)),
                        int(max_count),
                        f"{phrase!r} appears too often in visible speech",
                    )

                self.assertEqual(len(tools.execute_calls), int(expect["execute_calls"]))
                completed = self._last_completed(events)
                self.assertIsNotNone(completed)
                assert completed is not None
                self.assertEqual(completed.get("status"), expect["completed_status"])

    def _run_case(self, case: dict[str, object]) -> dict[str, object]:
        responder = NaturalMatrixResponder()
        tools = MockThoughtTools(light_on=bool(case.get("initial_light_on", False)))
        appliance_states = case.get("initial_appliance_states")
        if isinstance(appliance_states, dict):
            tools.appliance_states.update(
                {str(key): str(value) for key, value in appliance_states.items()}
            )
        loop = ThoughtLoop(
            tools=tools,
            responder=responder,
            llm_visible_speech=True,
            require_llm_visible_speech=True,
        )
        context_refs = case.get("context_refs")
        events = loop.run_dicts(
            {
                **TURN,
                "text": str(case.get("text") or ""),
                "turn_id": f"turn_{case['name']}",
                "context_refs": context_refs if isinstance(context_refs, dict) else {},
            }
        )
        return {"events": events, "tools": tools, "responder": responder}

    def _visible_messages(self, events: list[dict[str, object]]) -> list[dict[str, object]]:
        return [event for event in events if event["type"] == "assistant.message"]

    def assert_no_exact_duplicate_visible_speech(
        self,
        messages: list[dict[str, object]],
    ) -> None:
        seen: set[str] = set()
        for event in messages:
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            speech = " ".join(str(data.get("speech") or "").split())
            if not speech:
                continue
            self.assertNotIn(speech, seen, f"duplicate visible speech: {speech!r}")
            seen.add(speech)

    def assert_no_scripted_shape(
        self,
        messages: list[dict[str, object]],
    ) -> None:
        for event in messages:
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            speech = str(data.get("speech") or "")
            self.assertFalse(
                speech.startswith(SCRIPTED_PREFIXES),
                f"script-like response leaked: {speech!r}",
            )

    def _last_completed(self, events: list[dict[str, object]]) -> dict[str, object] | None:
        for event in reversed(events):
            if event["type"] != "turn.completed":
                continue
            data = event.get("data")
            return data if isinstance(data, dict) else None
        return None
