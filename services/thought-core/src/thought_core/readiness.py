"""Readiness probes for Thought Core startup stages."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any, Iterable

from .input_understanding import LocalInputUnderstanding
from .loop import ThoughtLoop
from .persona import PlainPersona
from .reasoning import LocalActionReasoner
from .responders import ResponderResult
from .schema import TurnInput
from .tools import MockThoughtTools


READINESS_ID = "conscious_ready_no_external_turn"
DEFAULT_TEXT = "Agent OS conscious readiness probe"
DEFAULT_TURN_ID = "turn_conscious_ready_probe"
DEFAULT_SESSION_ID = "agent_os_readiness"
REQUIRED_EVENT_TYPES = ("assistant.message", "turn.completed")


@dataclass(frozen=True)
class DeterministicReadinessResponder:
    adapter_kind: str = "deterministic_readiness"
    provider: str = "thought-core"
    model: str = "conscious-ready-probe-v0"

    def respond(self, turn: TurnInput, *, response_context=None) -> ResponderResult:
        speech = "conscious_ready probe ok"
        return ResponderResult(
            speech=speech,
            display=speech,
            status=READINESS_ID,
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=False,
            detail="Deterministic Thought Core readiness response; no external API.",
            metadata={
                "readiness_id": READINESS_ID,
                "external_api_required": False,
                "turn_text_len": len(turn.text),
            },
        )


def build_conscious_readiness_turn(
    *,
    text: str = DEFAULT_TEXT,
    turn_id: str = DEFAULT_TURN_ID,
    session_id: str = DEFAULT_SESSION_ID,
    locale: str = "ja-JP",
) -> dict[str, Any]:
    return {
        "text": text,
        "turn_id": turn_id,
        "session_id": session_id,
        "locale": locale,
        "context_refs": {
            "readiness_id": READINESS_ID,
            "external_api_allowed": False,
        },
    }


def build_conscious_readiness_loop() -> ThoughtLoop:
    return ThoughtLoop(
        tools=MockThoughtTools(),
        max_execute_attempts=1,
        responder=DeterministicReadinessResponder(),
        action_reasoner=LocalActionReasoner(),
        input_understanding=LocalInputUnderstanding(),
        persona=PlainPersona(),
    )


def run_conscious_readiness_probe(
    *,
    text: str = DEFAULT_TEXT,
    turn_id: str = DEFAULT_TURN_ID,
    session_id: str = DEFAULT_SESSION_ID,
    include_events: bool = False,
) -> dict[str, Any]:
    turn = build_conscious_readiness_turn(
        text=text,
        turn_id=turn_id,
        session_id=session_id,
    )
    events = build_conscious_readiness_loop().run_dicts(turn)
    validation = validate_conscious_readiness_events(events)
    status = "ok" if validation["ok"] else "failed"
    payload: dict[str, Any] = {
        "status": status,
        "startup_stage": "conscious_ready" if validation["ok"] else "nonresponsive",
        "readiness_id": READINESS_ID,
        "turn_id": turn_id,
        "session_id": session_id,
        "external_api_required": False,
        "required_events": list(REQUIRED_EVENT_TYPES),
        "observed_events": validation["observed_events"],
        "used_llm": validation["used_llm"],
        "detail": validation["detail"],
        "next_stage": "full_conscious_ready",
    }
    if include_events:
        payload["events"] = events
    return payload


def validate_conscious_readiness_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    event_list = list(events)
    event_types = [str(event.get("type") or "") for event in event_list]
    missing = [event_type for event_type in REQUIRED_EVENT_TYPES if event_type not in event_types]
    used_llm = any(
        bool(event.get("data", {}).get("used_llm"))
        for event in event_list
        if isinstance(event.get("data"), dict)
    )
    completed = next(
        (event for event in event_list if str(event.get("type") or "") == "turn.completed"),
        {},
    )
    completed_data = completed.get("data") if isinstance(completed, dict) else {}
    completed_status = (
        str(completed_data.get("status") or "")
        if isinstance(completed_data, dict)
        else ""
    )
    errors: list[str] = []
    if missing:
        errors.append(f"missing required events: {', '.join(missing)}")
    if used_llm:
        errors.append("readiness turn used LLM/API path")
    if completed_status != READINESS_ID:
        errors.append(f"unexpected completion status: {completed_status or '<missing>'}")
    ok = not errors
    return {
        "ok": ok,
        "observed_events": event_types,
        "used_llm": used_llm,
        "detail": "conscious readiness turn completed" if ok else "; ".join(errors),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic no-external-API Thought Core readiness turn."
    )
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--turn-id", default=DEFAULT_TURN_ID)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--include-events", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_conscious_readiness_probe(
        text=args.text,
        turn_id=args.turn_id,
        session_id=args.session_id,
        include_events=args.include_events,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
