import json
import os
import re
import sys
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import Mock, patch, sentinel
from urllib import request


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.agentic_turn_provider import (  # noqa: E402
    PROVIDER_ATTEMPT_RECEIPT_CLASS,
    PROVIDER_ATTEMPT_TERMINAL_CLASS,
    AgenticActionReceipt,
    AgenticZeroArgumentCapabilityConstraint,
    AgenticCapabilityView,
    AgenticCapabilityViewEntry,
    AgenticPredecisionContext,
    AgenticPredecisionContextSection,
    AgenticTurnProviderRequest,
    AgenticTurnProviderDecisionInvalid,
    AgenticTurnProviderResult,
    UnavailableAgenticTurnProvider,
)
from thought_core.agentic_turn_runtime_provider import (  # noqa: E402
    AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME,
    OpenAICompatibleAgenticTurnProvider,
    MAX_PREDECISION_CONTEXT_SERIALIZED_BYTES,
    MAX_SWORD_OPENAI_BROKER_TIMEOUT_S,
    SWORD_OPENAI_BROKER_BASE_URLS,
    SWORD_OPENAI_BROKER_MODEL,
    SWORD_OPENAI_BROKER_PROVIDER,
    SwordOpenAIBrokerAgenticTurnProvider,
    build_agentic_turn_provider_from_env,
)
from thought_core.input_understanding import (  # noqa: E402
    InputFrame,
    LocalInputUnderstanding,
)
from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.persona import PlainPersona  # noqa: E402
from thought_core.reasoning import LocalActionReasoner  # noqa: E402
from thought_core.responders import (  # noqa: E402
    LocalFallbackResponder,
    OpenAICompatibleStructuredCompletion,
    SWORD_DECISION_EVENT_ID_HEADER,
    SWORD_PROVIDER_ATTEMPT_RECEIPT_KEY,
    StructuredCompletionInvalid,
    StructuredCompletionResult,
    StructuredCompletionUnavailable,
    _RejectAllRedirects,
)
from thought_core.server import _build_default_thought_loop, create_server  # noqa: E402
from thought_core.tools import HomeControlHttpTools  # noqa: E402


DECISION_EVENT_ID = "evt_" + ("c" * 32)


def _provider_attempt_receipt(
    decision_event_id: str = DECISION_EVENT_ID,
) -> dict[str, object]:
    return {
        "receipt_class": PROVIDER_ATTEMPT_RECEIPT_CLASS,
        "decision_event_id": decision_event_id,
        "upstream_attempt_count": 1,
        "retry_count": 0,
        "fallback_count": 0,
        "attempt_terminal_class": PROVIDER_ATTEMPT_TERMINAL_CLASS,
    }


class _CapturingCompletion:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def complete_json(
        self,
        *,
        system_prompt: str,
        input_payload: Mapping[str, object],
        max_tokens: int,
        response_format: Mapping[str, object] | None = None,
        decision_event_id: str | None = None,
    ) -> object:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "input_payload": input_payload,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "decision_event_id": decision_event_id,
            }
        )
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _MutatingResponseFormatCompletion(_CapturingCompletion):
    def complete_json(
        self,
        *,
        system_prompt: str,
        input_payload: Mapping[str, object],
        max_tokens: int,
        response_format: Mapping[str, object] | None = None,
        decision_event_id: str | None = None,
    ) -> object:
        result = super().complete_json(
            system_prompt=system_prompt,
            input_payload=input_payload,
            max_tokens=max_tokens,
            response_format=response_format,
            decision_event_id=decision_event_id,
        )
        if len(self.calls) == 1:
            if type(response_format) is not dict:
                raise AssertionError("mutable response format required")
            json_schema = response_format.get("json_schema")
            if type(json_schema) is not dict:
                raise AssertionError("mutable json schema required")
            json_schema["name"] = "malicious_mutation"
            json_schema["schema"] = {"type": "string"}
        return result


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_args):  # type: ignore[no-untyped-def]
        return False

    def read(self, limit: int = -1) -> bytes:
        return self.raw if limit < 0 else self.raw[:limit]


class _FakeOpener:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[request.Request, float]] = []

    def open(
        self,
        outbound_request: request.Request,
        *,
        timeout: float,
    ):  # type: ignore[no-untyped-def]
        self.calls.append((outbound_request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _CountingInputUnderstanding:
    adapter_kind = "test_counting"
    provider = "test"
    model = "test-counting-v1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def understand(self, turn, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(turn.text)
        return InputFrame(kind="home_command", is_command=True, reason="test_counting")


class AgenticTurnRuntimeProviderTest(TestCase):
    def test_factory_selects_only_explicit_local_openai_compatible_config(self) -> None:
        configs = (
            {
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
                "THOUGHT_CORE_LLM_MODEL": "local-model",
            },
            {
                "OPENAI_BASE_URL": "http://localhost:1234/v1",
                "OPENAI_MODEL": "configured-local-model",
            },
        )
        for env in configs:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                provider = build_agentic_turn_provider_from_env()
                self.assertIsInstance(
                    provider,
                    OpenAICompatibleAgenticTurnProvider,
                )

    def test_factory_returns_unavailable_for_all_non_supported_config_classes(self) -> None:
        local = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
            "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_LLM_MODEL": "local-model",
        }
        cases = {
            "missing": {},
            "disabled": {**local, "THOUGHT_CORE_LLM_ENABLED": "0"},
            "unsupported": {**local, "THOUGHT_CORE_LLM_PROVIDER": "other"},
            "codex": {**local, "THOUGHT_CORE_LLM_PROVIDER": "codex-cli"},
            "non_loopback": {
                **local,
                "THOUGHT_CORE_LLM_BASE_URL": "https://example.invalid/v1",
            },
            "credential_in_endpoint": {
                **local,
                "THOUGHT_CORE_LLM_BASE_URL": "http://secret@127.0.0.1:11434/v1",
            },
            "thought_core_credential": {
                **local,
                "THOUGHT_CORE_LLM_API_KEY": "PRIVATE_CREDENTIAL_SENTINEL",
            },
            "openai_credential": {
                **local,
                "OPENAI_API_KEY": "PRIVATE_CREDENTIAL_SENTINEL",
            },
            "missing_model": {
                key: value
                for key, value in local.items()
                if key != "THOUGHT_CORE_LLM_MODEL"
            },
            "invalid_enabled": {**local, "THOUGHT_CORE_LLM_ENABLED": "maybe"},
            "invalid_timeout": {**local, "THOUGHT_CORE_LLM_TIMEOUT_S": "invalid"},
        }
        for config_class, env in cases.items():
            with self.subTest(config_class=config_class), patch.dict(
                os.environ,
                env,
                clear=True,
            ):
                provider = build_agentic_turn_provider_from_env()
                self.assertIsInstance(provider, UnavailableAgenticTurnProvider)

    def test_force_no_provider_is_the_only_explicit_none_selector(self) -> None:
        env = {
            "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
            "THOUGHT_CORE_LLM_ENABLED": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(build_agentic_turn_provider_from_env())

        with patch.dict(os.environ, {"THOUGHT_CORE_FORCE_NO_PROVIDER": "0"}, clear=True):
            self.assertIsInstance(
                build_agentic_turn_provider_from_env(),
                UnavailableAgenticTurnProvider,
            )

    def test_server_default_injects_factory_provider(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "thought_core.server.build_agentic_turn_provider_from_env",
                return_value=sentinel.provider,
            ) as build_provider,
            patch("thought_core.server.ThoughtLoop", return_value=sentinel.loop) as loop_type,
            patch(
                "thought_core.server.ThreadingHTTPServer",
                return_value=sentinel.server,
            ) as server_type,
        ):
            result = create_server("127.0.0.1", 18787)

        self.assertIs(result, sentinel.server)
        build_provider.assert_called_once_with()
        loop_type.assert_called_once()
        self.assertEqual(
            set(loop_type.call_args.kwargs),
            {"agentic_turn_provider", "action_reasoner", "responder"},
        )
        self.assertIs(
            loop_type.call_args.kwargs["agentic_turn_provider"],
            sentinel.provider,
        )
        self.assertIsInstance(
            loop_type.call_args.kwargs["action_reasoner"],
            LocalActionReasoner,
        )
        self.assertIsInstance(
            loop_type.call_args.kwargs["responder"],
            LocalFallbackResponder,
        )
        self.assertEqual(server_type.call_args.args[0], ("127.0.0.1", 18787))

    def test_clean_force_uses_explicit_local_compatibility_without_legacy_remote(self) -> None:
        env = {
            "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
            "THOUGHT_CORE_LLM_ENABLED": "0",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_ACTION_LLM_MODEL": "local-action-model",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "thought_core.loop.EnvironmentTurnResponder.from_env"
            ) as legacy_responder,
            patch(
                "thought_core.responders.OpenAICompatibleStructuredCompletion.complete_json"
            ) as structured_completion,
            patch(
                "thought_core.loop.build_action_reasoner_from_env"
            ) as action_reasoner_factory,
            patch(
                "thought_core.reasoning.OpenAICompatibleActionReviewer.from_env"
            ) as action_reviewer_factory,
            patch("thought_core.responders._run_codex_command") as codex_runner,
            patch("thought_core.responders.request.OpenerDirector.open") as remote_open,
            patch("thought_core.reasoning.request.urlopen") as urlopen,
            patch("thought_core.responders.request.Request") as request_type,
        ):
            loop = _build_default_thought_loop()
            events = loop.run_dicts(self._turn("こんにちは。"))

        self.assertIsNone(loop.agentic_turn_provider)
        self.assertIsInstance(loop.responder, LocalFallbackResponder)
        self.assertIsInstance(loop.action_reasoner, LocalActionReasoner)
        legacy_responder.assert_not_called()
        structured_completion.assert_not_called()
        action_reasoner_factory.assert_not_called()
        action_reviewer_factory.assert_not_called()
        codex_runner.assert_not_called()
        remote_open.assert_not_called()
        urlopen.assert_not_called()
        request_type.assert_not_called()
        self.assertNotIn("agentic.decision", [event["type"] for event in events])
        completed = next(
            event for event in events if event["type"] == "responder.completed"
        )
        self.assertEqual(completed["data"]["adapter_kind"], "local_fallback")

    def test_clean_force_preserves_local_home_capability_semantics(self) -> None:
        env = {
            "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
            "THOUGHT_CORE_LLM_ENABLED": "0",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_ACTION_LLM_MODEL": "local-action-model",
            "THOUGHT_CORE_TOOLS_ADAPTER": "mock",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "thought_core.reasoning.OpenAICompatibleActionReviewer.from_env"
            ) as action_reviewer_factory,
            patch("thought_core.reasoning.request.urlopen") as urlopen,
            patch("thought_core.responders.request.Request") as request_type,
        ):
            loop = _build_default_thought_loop()
            events = loop.run_dicts(self._turn("リビングの電気をつけて。"))

        self.assertIsNone(loop.agentic_turn_provider)
        self.assertIsInstance(loop.action_reasoner, LocalActionReasoner)
        action_reviewer_factory.assert_not_called()
        urlopen.assert_not_called()
        request_type.assert_not_called()
        event_types = [event["type"] for event in events]
        self.assertNotIn("agentic.decision", event_types)
        proposed = next(event for event in events if event["type"] == "action.proposed")
        imagined = next(
            event for event in events if event["type"] == "target_state.imagined"
        )
        self.assertEqual(proposed["data"]["action"]["action_id"], "light_on")
        self.assertEqual(
            imagined["data"]["reasoner"]["adapter_kind"],
            "local_target_projection",
        )

    def test_force_unsafe_configuration_matrix_holds_without_legacy_remote(self) -> None:
        private_sentinel = "PRIVATE_FORCE_CONFIGURATION_SENTINEL"
        cases = {
            "credential": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
                "THOUGHT_CORE_LLM_MODEL": "local-model",
                "THOUGHT_CORE_LLM_API_KEY": private_sentinel,
            },
            "non_loopback_openai": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
                "THOUGHT_CORE_LLM_BASE_URL": (
                    f"https://example.invalid/{private_sentinel}/v1"
                ),
                "THOUGHT_CORE_LLM_MODEL": "remote-model",
            },
            "codex": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "codex-cli",
                "THOUGHT_CORE_CODEX_CLI_PATH": private_sentinel,
            },
            "action_credential": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
                "THOUGHT_CORE_ACTION_LLM_BASE_URL": (
                    "http://127.0.0.1:11434/v1"
                ),
                "THOUGHT_CORE_ACTION_LLM_MODEL": "local-action-model",
                "THOUGHT_CORE_ACTION_LLM_API_KEY": private_sentinel,
            },
            "action_non_loopback": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
                "THOUGHT_CORE_ACTION_LLM_BASE_URL": (
                    f"https://example.invalid/{private_sentinel}/v1"
                ),
                "THOUGHT_CORE_ACTION_LLM_MODEL": "remote-action-model",
            },
            "action_codex_like": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
                "THOUGHT_CORE_ACTION_LLM_PROVIDER": "codex-cli",
                "THOUGHT_CORE_ACTION_CODEX_CLI_PATH": private_sentinel,
            },
            "all_dangerous_collaborators": {
                "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                "THOUGHT_CORE_LLM_ENABLED": "1",
                "THOUGHT_CORE_LLM_PROVIDER": "codex-cli",
                "THOUGHT_CORE_LLM_BASE_URL": (
                    f"https://example.invalid/{private_sentinel}/agentic"
                ),
                "THOUGHT_CORE_LLM_API_KEY": private_sentinel,
                "THOUGHT_CORE_CODEX_CLI_PATH": private_sentinel,
                "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
                "THOUGHT_CORE_ACTION_LLM_PROVIDER": "codex-cli",
                "THOUGHT_CORE_ACTION_LLM_BASE_URL": (
                    f"https://example.invalid/{private_sentinel}/action"
                ),
                "THOUGHT_CORE_ACTION_LLM_API_KEY": private_sentinel,
                "THOUGHT_CORE_TOOLS_ADAPTER": "home_control",
                "HOME_CONTROL_BRIDGE_URL": (
                    f"https://example.invalid/{private_sentinel}/tools"
                ),
                "HOME_CONTROL_API_TOKEN": private_sentinel,
            },
        }
        for config_class, env in cases.items():
            with (
                self.subTest(config_class=config_class),
                patch.dict(os.environ, env, clear=True),
                patch(
                    "thought_core.loop.EnvironmentTurnResponder.from_env"
                ) as legacy_responder,
                patch(
                    "thought_core.responders.OpenAICompatibleStructuredCompletion.complete_json"
                ) as structured_completion,
                patch(
                    "thought_core.loop.build_action_reasoner_from_env"
                ) as action_reasoner_factory,
                patch(
                    "thought_core.reasoning.OpenAICompatibleActionReviewer.from_env"
                ) as action_reviewer_factory,
                patch("thought_core.responders._run_codex_command") as codex_runner,
                patch(
                    "thought_core.responders.request.OpenerDirector.open"
                ) as remote_open,
                patch("thought_core.reasoning.request.urlopen") as urlopen,
                patch("thought_core.responders.request.Request") as request_type,
            ):
                loop = _build_default_thought_loop()
                events = loop.run_dicts(self._turn("部屋を明るくして。"))

            self.assertIsInstance(
                loop.agentic_turn_provider,
                UnavailableAgenticTurnProvider,
            )
            self.assertIsInstance(loop.responder, LocalFallbackResponder)
            self.assertIsInstance(loop.action_reasoner, LocalActionReasoner)
            legacy_responder.assert_not_called()
            structured_completion.assert_not_called()
            action_reasoner_factory.assert_not_called()
            action_reviewer_factory.assert_not_called()
            codex_runner.assert_not_called()
            remote_open.assert_not_called()
            urlopen.assert_not_called()
            request_type.assert_not_called()
            held = next(
                event for event in events if event["type"] == "agentic.decision"
            )
            self.assertEqual(held["data"]["status"], "held")
            self.assertEqual(
                held["data"]["reason"],
                "agentic_provider_unavailable",
            )
            serialized = json.dumps(events, ensure_ascii=False)
            self.assertNotIn(private_sentinel, serialized)
            self.assertNotIn("Authorization", serialized)
            self.assertNotIn("action.proposed", [event["type"] for event in events])

    def test_force_constructor_census_has_no_environment_driven_ai_client(self) -> None:
        private_sentinel = "PRIVATE_CONSTRUCTOR_CENSUS_SENTINEL"
        env = {
            "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
            "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_LLM_MODEL": "local-model",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_ACTION_LLM_MODEL": "local-action-model",
            "THOUGHT_CORE_TOOLS_ADAPTER": "home_control",
            "HOME_CONTROL_BRIDGE_URL": "http://127.0.0.1:8787",
            "HOME_CONTROL_API_TOKEN": private_sentinel,
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "thought_core.loop.EnvironmentTurnResponder.from_env"
            ) as responder_factory,
            patch(
                "thought_core.loop.build_action_reasoner_from_env"
            ) as action_reasoner_factory,
            patch(
                "thought_core.reasoning.OpenAICompatibleActionReviewer.from_env"
            ) as action_reviewer_factory,
            patch(
                "thought_core.responders.OpenAICompatibleChatResponder.from_env"
            ) as chat_responder_factory,
            patch(
                "thought_core.responders.CodexCliChatResponder.from_env"
            ) as codex_responder_factory,
            patch(
                "thought_core.agentic_turn_runtime_provider."
                "OpenAICompatibleStructuredCompletion"
            ) as structured_completion_type,
            patch("thought_core.responders._run_codex_command") as codex_runner,
            patch("thought_core.reasoning.request.urlopen") as urlopen,
            patch("thought_core.responders.request.Request") as request_type,
        ):
            loop = _build_default_thought_loop()
            events = loop.run_dicts(self._turn("こんにちは。"))

        self.assertIsNone(loop.agentic_turn_provider)
        self.assertIsInstance(loop.responder, LocalFallbackResponder)
        self.assertIsInstance(loop.action_reasoner, LocalActionReasoner)
        self.assertIsInstance(loop.input_understanding, LocalInputUnderstanding)
        self.assertIsInstance(loop.persona, PlainPersona)
        self.assertIsInstance(loop.tools, HomeControlHttpTools)
        responder_factory.assert_not_called()
        action_reasoner_factory.assert_not_called()
        action_reviewer_factory.assert_not_called()
        chat_responder_factory.assert_not_called()
        codex_responder_factory.assert_not_called()
        structured_completion_type.assert_not_called()
        codex_runner.assert_not_called()
        urlopen.assert_not_called()
        request_type.assert_not_called()
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn(private_sentinel, serialized)
        self.assertNotIn("Authorization", serialized)

    def test_server_explicit_loop_skips_factory_and_constructor(self) -> None:
        with (
            patch("thought_core.server.build_agentic_turn_provider_from_env") as build_provider,
            patch("thought_core.server.ThoughtLoop") as loop_type,
            patch(
                "thought_core.server.ThreadingHTTPServer",
                return_value=sentinel.server,
            ),
        ):
            result = create_server(
                "127.0.0.1",
                18787,
                thought_loop=sentinel.injected_loop,
            )

        self.assertIs(result, sentinel.server)
        build_provider.assert_not_called()
        loop_type.assert_not_called()

    def test_default_constructor_matrix_pins_local_secondary_collaborators(
        self,
    ) -> None:
        private_sentinel = "PRIVATE_DEFAULT_CONSTRUCTOR_SENTINEL"
        local = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
            "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_LLM_MODEL": "local-model",
        }
        cases = {
            "force_clean": (
                {
                    "THOUGHT_CORE_FORCE_NO_PROVIDER": "1",
                    "THOUGHT_CORE_LLM_ENABLED": "0",
                    "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
                },
                type(None),
                0,
            ),
            "valid_loopback": (
                {
                    **local,
                    "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
                    "THOUGHT_CORE_ACTION_LLM_BASE_URL": (
                        "http://127.0.0.1:11434/v1"
                    ),
                },
                OpenAICompatibleAgenticTurnProvider,
                1,
            ),
            "missing": ({}, UnavailableAgenticTurnProvider, 0),
            "disabled": (
                {**local, "THOUGHT_CORE_LLM_ENABLED": "0"},
                UnavailableAgenticTurnProvider,
                0,
            ),
            "non_force_credentials": (
                {
                    **local,
                    "THOUGHT_CORE_LLM_API_KEY": private_sentinel,
                    "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
                    "THOUGHT_CORE_ACTION_LLM_BASE_URL": (
                        f"https://example.invalid/{private_sentinel}/action"
                    ),
                    "THOUGHT_CORE_ACTION_LLM_API_KEY": private_sentinel,
                },
                UnavailableAgenticTurnProvider,
                0,
            ),
            "non_loopback": (
                {
                    **local,
                    "THOUGHT_CORE_LLM_BASE_URL": (
                        f"https://example.invalid/{private_sentinel}/agentic"
                    ),
                },
                UnavailableAgenticTurnProvider,
                0,
            ),
            "unsupported_codex": (
                {
                    **local,
                    "THOUGHT_CORE_LLM_PROVIDER": "codex-cli",
                    "THOUGHT_CORE_CODEX_CLI_PATH": private_sentinel,
                },
                UnavailableAgenticTurnProvider,
                0,
            ),
        }
        for config_class, (env, provider_type, structured_calls) in cases.items():
            with (
                self.subTest(config_class=config_class),
                patch.dict(os.environ, env, clear=True),
                patch(
                    "thought_core.loop.EnvironmentTurnResponder.from_env"
                ) as responder_factory,
                patch(
                    "thought_core.loop.build_action_reasoner_from_env"
                ) as action_reasoner_factory,
                patch(
                    "thought_core.reasoning.OpenAICompatibleActionReviewer.from_env"
                ) as action_reviewer_factory,
                patch(
                    "thought_core.responders.OpenAICompatibleChatResponder.from_env"
                ) as chat_responder_factory,
                patch(
                    "thought_core.responders.CodexCliChatResponder.from_env"
                ) as codex_responder_factory,
                patch(
                    "thought_core.agentic_turn_runtime_provider."
                    "OpenAICompatibleStructuredCompletion",
                    wraps=OpenAICompatibleStructuredCompletion,
                ) as structured_completion_type,
                patch("thought_core.responders._run_codex_command") as codex_runner,
                patch("thought_core.responders.request.OpenerDirector.open") as opener,
                patch("thought_core.reasoning.request.urlopen") as urlopen,
                patch("thought_core.responders.request.Request") as request_type,
            ):
                loop = _build_default_thought_loop()

            if provider_type is type(None):
                self.assertIsNone(loop.agentic_turn_provider)
            else:
                self.assertIsInstance(loop.agentic_turn_provider, provider_type)
            self.assertIsInstance(loop.responder, LocalFallbackResponder)
            self.assertIsInstance(loop.action_reasoner, LocalActionReasoner)
            responder_factory.assert_not_called()
            action_reasoner_factory.assert_not_called()
            action_reviewer_factory.assert_not_called()
            chat_responder_factory.assert_not_called()
            codex_responder_factory.assert_not_called()
            self.assertEqual(structured_completion_type.call_count, structured_calls)
            codex_runner.assert_not_called()
            opener.assert_not_called()
            urlopen.assert_not_called()
            request_type.assert_not_called()

    def test_credential_free_sword_broker_selector_is_exact_and_preserves_limits(self) -> None:
        env_base = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": SWORD_OPENAI_BROKER_PROVIDER,
            "THOUGHT_CORE_LLM_MODEL": SWORD_OPENAI_BROKER_MODEL,
            "THOUGHT_CORE_LLM_TIMEOUT_S": "12",
        }
        for base_url in sorted(SWORD_OPENAI_BROKER_BASE_URLS):
            with (
                self.subTest(base_url=base_url),
                patch.dict(os.environ, {**env_base, "THOUGHT_CORE_LLM_BASE_URL": base_url}, clear=True),
                patch(
                    "thought_core.agentic_turn_runtime_provider."
                    "OpenAICompatibleStructuredCompletion",
                    wraps=OpenAICompatibleStructuredCompletion,
                ) as completion_type,
            ):
                provider = build_agentic_turn_provider_from_env()
            self.assertIsInstance(provider, SwordOpenAIBrokerAgenticTurnProvider)
            self.assertEqual(completion_type.call_count, 1)
            self.assertEqual(completion_type.call_args.kwargs["base_url"], base_url)
            self.assertEqual(
                completion_type.call_args.kwargs["model"],
                SWORD_OPENAI_BROKER_MODEL,
            )
            self.assertEqual(
                completion_type.call_args.kwargs["timeout_s"],
                MAX_SWORD_OPENAI_BROKER_TIMEOUT_S,
            )

        rejected = (
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18888/v1"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1"},
            {"THOUGHT_CORE_LLM_BASE_URL": "https://api.openai.com/v1"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://user@127.0.0.1:18786/v1"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_LLM_MODEL": "different-model"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_LLM_TIMEOUT_S": "12.001"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_LLM_TIMEOUT_S": "inf"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_LLM_TIMEOUT_S": "0"},
            {
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1",
                "THOUGHT_CORE_LLM_ADAPTER": "sword-openai-broker",
            },
            {
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1",
                "THOUGHT_CORE_LLM_ADAPTER": "openai-compatible",
                "OPENAI_BASE_URL": "http://127.0.0.1:18786/v1",
                "OPENAI_MODEL": SWORD_OPENAI_BROKER_MODEL,
            },
            {
                "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1",
                "THOUGHT_CORE_LLM_ADAPTER": "different-adapter",
                "OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
                "OPENAI_MODEL": "different-model",
            },
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "OPENAI_API_KEY": "synthetic-private-credential"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_LLM_API_KEY": "synthetic-private-credential"},
            {"THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:18786/v1", "THOUGHT_CORE_ACTION_LLM_API_KEY": "synthetic-private-credential"},
        )
        for overrides in rejected:
            with (
                self.subTest(overrides=tuple(sorted(overrides))),
                patch.dict(os.environ, {**env_base, **overrides}, clear=True),
                patch(
                    "thought_core.agentic_turn_runtime_provider."
                    "OpenAICompatibleStructuredCompletion",
                ) as completion_type,
            ):
                provider = build_agentic_turn_provider_from_env()
            self.assertIsInstance(provider, UnavailableAgenticTurnProvider)
            completion_type.assert_not_called()

        alias_only = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_ADAPTER": SWORD_OPENAI_BROKER_PROVIDER,
            "OPENAI_BASE_URL": "http://127.0.0.1:18786/v1",
            "OPENAI_MODEL": SWORD_OPENAI_BROKER_MODEL,
        }
        with (
            patch.dict(os.environ, alias_only, clear=True),
            patch(
                "thought_core.agentic_turn_runtime_provider."
                "OpenAICompatibleStructuredCompletion",
            ) as completion_type,
        ):
            provider = build_agentic_turn_provider_from_env()
        self.assertIsInstance(provider, UnavailableAgenticTurnProvider)
        completion_type.assert_not_called()

    def test_broker_timeout_ceiling_does_not_change_compatibility_provider(self) -> None:
        env = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
            "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_LLM_MODEL": "local-model",
            "THOUGHT_CORE_LLM_TIMEOUT_S": "12.001",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "thought_core.agentic_turn_runtime_provider."
                "OpenAICompatibleStructuredCompletion",
                wraps=OpenAICompatibleStructuredCompletion,
            ) as completion_type,
        ):
            provider = build_agentic_turn_provider_from_env()
        self.assertIsInstance(provider, OpenAICompatibleAgenticTurnProvider)
        self.assertEqual(completion_type.call_count, 1)
        self.assertEqual(completion_type.call_args.kwargs["timeout_s"], 12.001)

    def test_sword_broker_provider_preserves_decision_and_receipt_token_caps(self) -> None:
        completion = _CapturingCompletion(
            StructuredCompletionResult(
                value=self._conversation_candidate(),
                provider_attempt_receipt=_provider_attempt_receipt(),
            ),
            {"speech": "receipt", "display": "receipt"},
        )
        provider = SwordOpenAIBrokerAgenticTurnProvider(completion)
        result = provider.decide(
            self._provider_request(
                human_wish="synthetic wish",
                context_refs=MappingProxyType({}),
            )
        )
        self.assertIsInstance(result, AgenticTurnProviderResult)
        provider.respond_to_receipt(
            AgenticActionReceipt(
                decision_ref="evt_receipt_token_cap",
                receipt_ref="evt_receipt_token_cap:success",
                action_id="light_on",
                capability_id="light_on",
                semantic_purpose="部屋の明かりをつける",
                target_ref="light",
                expected_state="on",
                phase="success",
                status="success",
                confirmed=True,
                executed=True,
                execution_certainty="executed",
                review_status="succeeded",
                review_checkpoint_class="matched",
            )
        )
        self.assertEqual(
            [call["max_tokens"] for call in completion.calls],
            [720, 240],
        )
        self.assertEqual(
            [call["decision_event_id"] for call in completion.calls],
            [DECISION_EVENT_ID, None],
        )

    def test_sword_broker_provider_rejects_missing_wrong_or_replayed_receipt(self) -> None:
        valid = _provider_attempt_receipt()
        cases: list[tuple[str, object]] = [
            ("missing", self._conversation_candidate()),
            (
                "wrong_id",
                {**valid, "decision_event_id": "evt_" + ("d" * 32)},
            ),
            ("extra", {**valid, "extra": "PRIVATE_RECEIPT_SENTINEL"}),
            ("wrong_class", {**valid, "receipt_class": "wrong"}),
            ("wrong_terminal", {**valid, "attempt_terminal_class": "wrong"}),
            ("attempt_zero", {**valid, "upstream_attempt_count": 0}),
            ("attempt_two", {**valid, "upstream_attempt_count": 2}),
            ("attempt_bool", {**valid, "upstream_attempt_count": True}),
            ("retry_string", {**valid, "retry_count": "0"}),
            ("fallback_bool", {**valid, "fallback_count": False}),
        ]
        for name, receipt in cases:
            result = (
                receipt
                if name == "missing"
                else StructuredCompletionResult(
                    value=self._conversation_candidate(),
                    provider_attempt_receipt=receipt,
                )
            )
            completion = _CapturingCompletion(result)
            provider = SwordOpenAIBrokerAgenticTurnProvider(completion)
            with self.subTest(name=name), self.assertRaisesRegex(
                AgenticTurnProviderDecisionInvalid,
                "agentic_decision_invalid",
            ) as captured:
                provider.decide(
                    self._provider_request(
                        human_wish="synthetic wish",
                        context_refs=MappingProxyType({}),
                    )
                )
            self.assertEqual(
                captured.exception.validation_subcode,
                "provider_content_invalid",
            )
            self.assertNotIn("PRIVATE_RECEIPT_SENTINEL", repr(captured.exception))

    def test_broker_example_is_credential_free_and_exact(self) -> None:
        example = (REPO_ROOT / "services" / "thought-core" / ".env.example").read_text(
            encoding="utf-8"
        )
        self.assertIn("THOUGHT_CORE_LLM_PROVIDER=sword-openai-broker", example)
        self.assertIn("THOUGHT_CORE_LLM_BASE_URL=http://127.0.0.1:18786/v1", example)
        self.assertNotIn("API_KEY=", example)
        self.assertNotIn("https://api.openai.com", example)

    def test_valid_loopback_agentic_capability_lifecycle_uses_only_structured_client(
        self,
    ) -> None:
        private_wish = "PRIVATE_AGENTIC_WISH_SENTINEL"
        decision = {
            "schemaVersion": 1,
            "kind": "capability",
            "response": {
                "speech": "掃除機を開始する前に確認するね。",
                "display": "掃除機を開始する確認を準備します。",
            },
            "capability": {"id": "vacuum_start", "arguments": {}},
        }
        confirmation_response = {
            "speech": "実行前の確認が必要です。",
            "display": "確認待ちです。",
        }
        success_response = {
            "speech": "実行結果を受け取りました。",
            "display": "実行結果を受領しました。",
        }
        env = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
            "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_LLM_MODEL": "local-model",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "1",
            "THOUGHT_CORE_ACTION_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_ACTION_LLM_MODEL": "local-action-model",
            "THOUGHT_CORE_TOOLS_ADAPTER": "mock",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch(
                "thought_core.responders.OpenAICompatibleStructuredCompletion.complete_json",
                side_effect=[decision, confirmation_response, success_response],
            ) as structured_completion,
            patch(
                "thought_core.loop.EnvironmentTurnResponder.from_env"
            ) as responder_factory,
            patch(
                "thought_core.loop.build_action_reasoner_from_env"
            ) as action_reasoner_factory,
            patch(
                "thought_core.reasoning.OpenAICompatibleActionReviewer.from_env"
            ) as action_reviewer_factory,
            patch(
                "thought_core.responders.OpenAICompatibleChatResponder.from_env"
            ) as chat_responder_factory,
            patch(
                "thought_core.responders.CodexCliChatResponder.from_env"
            ) as codex_responder_factory,
            patch("thought_core.responders._run_codex_command") as codex_runner,
            patch("thought_core.responders.request.OpenerDirector.open") as opener,
            patch("thought_core.reasoning.request.urlopen") as urlopen,
            patch("thought_core.responders.request.Request") as request_type,
        ):
            loop = _build_default_thought_loop()
            preview_events = loop.run_dicts(
                self._turn(private_wish),
            )
            execute_turn = self._turn("お願い")
            execute_turn["turn_id"] = "runtime_provider_confirmation"
            execute_events = loop.run_dicts(execute_turn)

        self.assertIsInstance(
            loop.agentic_turn_provider,
            OpenAICompatibleAgenticTurnProvider,
        )
        self.assertIsInstance(loop.responder, LocalFallbackResponder)
        self.assertIsInstance(loop.action_reasoner, LocalActionReasoner)
        responder_factory.assert_not_called()
        action_reasoner_factory.assert_not_called()
        action_reviewer_factory.assert_not_called()
        chat_responder_factory.assert_not_called()
        codex_responder_factory.assert_not_called()
        codex_runner.assert_not_called()
        opener.assert_not_called()
        urlopen.assert_not_called()
        request_type.assert_not_called()
        self.assertEqual(structured_completion.call_count, 3)

        decision_call = structured_completion.call_args_list[0].kwargs
        self.assertEqual(
            decision_call["input_payload"]["human_wish"],
            private_wish,
        )
        receipt_payloads = [
            call.kwargs["input_payload"]
            for call in structured_completion.call_args_list[1:]
        ]
        expected_receipt_fields = {
            "context_version",
            "decision_ref",
            "receipt_ref",
            "action_id",
            "capability_id",
            "semantic_purpose",
            "target_ref",
            "expected_state",
            "phase",
            "status",
            "confirmed",
            "executed",
            "execution_certainty",
            "review_status",
            "review_checkpoint_class",
        }
        self.assertEqual(len(receipt_payloads), 2)
        for receipt_payload in receipt_payloads:
            self.assertEqual(set(receipt_payload), expected_receipt_fields)
            self.assertNotIn(
                private_wish,
                json.dumps(receipt_payload, ensure_ascii=False),
            )

        preview_types = [event["type"] for event in preview_events]
        execute_types = [event["type"] for event in execute_events]
        self.assertIn("action.proposed", preview_types)
        self.assertEqual(preview_events[-1]["data"]["status"], "confirmation_required")
        self.assertIn("action.reviewed", execute_types)
        self.assertEqual(execute_events[-1]["data"]["status"], "success")
        self.assertIn(
            success_response["speech"],
            [
                event["data"]["speech"]
                for event in execute_events
                if event["type"] == "assistant.message"
            ],
        )
        event_wire = json.dumps(
            preview_events + execute_events,
            ensure_ascii=False,
        )
        self.assertNotIn(private_wish, event_wire)
        self.assertNotIn("Authorization", event_wire)

    def test_unavailable_default_holds_before_all_compatibility_work(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            provider = build_agentic_turn_provider_from_env()
        understanding = _CountingInputUnderstanding()
        events = ThoughtLoop(
            agentic_turn_provider=provider,
            input_understanding=understanding,
        ).run_dicts(self._turn("リビングの電気をつけて"))

        held = next(event for event in events if event["type"] == "agentic.decision")
        self.assertEqual(held["data"]["status"], "held")
        self.assertEqual(held["data"]["reason"], "agentic_provider_unavailable")
        self.assertEqual(understanding.calls, [])
        self.assertEqual(
            [
                event["data"]["tool"]
                for event in events
                if event["type"] == "tool.started"
            ],
            [],
        )
        self.assertNotIn("action.proposed", [event["type"] for event in events])

    def test_decision_prompt_contains_only_bounded_private_boundary_fields(self) -> None:
        candidate = self._conversation_candidate()
        completion = _CapturingCompletion(candidate)
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        request = self._provider_request(
            human_wish="静かな明るさにしたい。",
            context_refs=MappingProxyType({"observation_ref": "obs_safe_1"}),
        )

        self.assertEqual(provider.decide(request), candidate)
        call = completion.calls[0]
        self.assertIsNone(call["decision_event_id"])
        payload = call["input_payload"]
        self.assertEqual(
            set(payload),  # type: ignore[arg-type]
            {
                "human_wish",
                "catalog",
                "capabilities",
                "bounded_capability_constraint",
                "context_refs",
                "agent_context",
                "predecision_context",
            },
        )
        self.assertEqual(
            payload["catalog"],  # type: ignore[index]
            {"id": "catalog-test", "version": "catalog-test.v1"},
        )
        self.assertEqual(
            payload["capabilities"],  # type: ignore[index]
            [
                {
                    "id": "light_on",
                    "description": "ライトをつける",
                    "available": True,
                },
                {
                    "id": "aircon_on",
                    "description": "エアコンをつける",
                    "available": False,
                },
            ],
        )
        self.assertIsNone(payload["bounded_capability_constraint"])  # type: ignore[index]
        predecision = payload["predecision_context"]  # type: ignore[index]
        self.assertEqual(
            predecision["schema_version"],  # type: ignore[index]
            "agentic-predecision-context.v1",
        )
        self.assertIsNone(predecision["latest_user_correction"])  # type: ignore[index]
        for section_name in (
            "environment_state",
            "active_operations",
            "feedback_context",
            "relevant_memory",
            "same_session_continuity",
            "system_topology",
        ):
            self.assertEqual(
                predecision[section_name],  # type: ignore[index]
                {
                    "status": "missing",
                    "status_detail": "not_supplied",
                    "summary": "",
                    "items": [],
                },
            )
        self.assertEqual(
            predecision["capability_view"],  # type: ignore[index]
            {
                "catalog": payload["catalog"],  # type: ignore[index]
                "capabilities": payload["capabilities"],  # type: ignore[index]
            },
        )
        serialized = json.dumps(call, ensure_ascii=False)
        self.assertNotIn("provider_payload", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("endpoint", serialized)

    def test_decision_prompt_serializes_one_zero_argument_capability_constraint(
        self,
    ) -> None:
        candidate = self._conversation_candidate()
        completion = _CapturingCompletion(candidate)
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        request = self._provider_request(
            human_wish="ライトをつけて。",
            context_refs=MappingProxyType({}),
            bounded_capability_constraint=AgenticZeroArgumentCapabilityConstraint(
                "light_on"
            ),
        )

        self.assertEqual(provider.decide(request), candidate)
        payload = completion.calls[0]["input_payload"]
        self.assertEqual(
            payload["bounded_capability_constraint"],  # type: ignore[index]
            {"id": "light_on", "arguments": {}},
        )

    def test_capability_constraint_rejects_nonempty_unknown_or_unavailable_rows(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "agentic_capability_constraint_invalid",
        ):
            AgenticZeroArgumentCapabilityConstraint(
                "light_on",
                MappingProxyType({"unexpected": True}),
            )

        for capability_id in ("not_catalogued", "aircon_on"):
            with self.subTest(capability_id=capability_id):
                completion = _CapturingCompletion(self._conversation_candidate())
                provider = OpenAICompatibleAgenticTurnProvider(completion)
                request = self._provider_request(
                    human_wish="操作して。",
                    context_refs=MappingProxyType({}),
                    bounded_capability_constraint=(
                        AgenticZeroArgumentCapabilityConstraint(capability_id)
                    ),
                )
                self.assertIsNone(provider.decide(request))
                self.assertEqual(completion.calls, [])

    def test_decision_prompt_requires_current_wish_authority_for_capability(self) -> None:
        greeting = {**self._conversation_candidate(), "capability": None}
        action = {
            "schemaVersion": 1,
            "kind": "capability",
            "response": {"speech": "照明をつけます。", "display": "照明をつけます。"},
            "capability": {
                "id": "light_on",
                "arguments": {},
            },
        }
        completion = _CapturingCompletion(greeting, action)
        provider = OpenAICompatibleAgenticTurnProvider(completion)

        greeting_result = provider.decide(
            self._provider_request(
                human_wish="今の起動確認として、短く自然に挨拶してください。",
                context_refs={},
            )
        )
        action_result = provider.decide(
            self._provider_request(
                human_wish="ライトをつけてください。",
                context_refs={},
            )
        )

        self.assertEqual(greeting_result["kind"], "conversation")
        self.assertIsNone(greeting_result.get("capability"))
        self.assertEqual(action_result["kind"], "capability")
        self.assertEqual(
            action_result["capability"]["id"],  # type: ignore[index]
            "light_on",
        )
        self.assertEqual(len(completion.calls), 2)
        for call in completion.calls:
            prompt = call["system_prompt"]
            self.assertIn(
                "Treat capability availability and prior context as options, never action authority",
                prompt,
            )
            self.assertIn(
                "only when human_wish semantically and unambiguously requests that matching action",
                prompt,
            )
            self.assertIn(
                "Greetings, ordinary conversation, capability questions, hypothetical statements, and ambiguous wishes",
                prompt,
            )
            self.assertIn(
                "must use conversation or clarification with capability null",
                prompt,
            )
            self.assertIn(
                "directly asks to start, stop, reset, or otherwise invoke one available capability",
                prompt,
            )
            self.assertIn(
                "Do not downgrade a direct action request to conversation merely because validation, delivery, or a downstream receipt is still required",
                prompt,
            )
            self.assertIn(
                "the response may describe the request but must not claim completion",
                prompt,
            )

    def test_predecision_context_is_bounded_explicit_and_stably_ordered(self) -> None:
        candidate = self._conversation_candidate()
        completion = _CapturingCompletion(candidate, candidate)
        provider = OpenAICompatibleAgenticTurnProvider(completion)

        def _context(item: Mapping[str, object]) -> AgenticPredecisionContext:
            return AgenticPredecisionContext(
                latest_user_correction="照明ではなく映像を変えて。",
                environment_state=AgenticPredecisionContextSection(
                    status="available",
                    status_detail="",
                    summary="現在の部屋状態を観測済み。",
                    items=(item,),
                ),
                relevant_memory=AgenticPredecisionContextSection(
                    status="stale",
                    status_detail="memory_snapshot_stale",
                    summary="関連する以前の希望は古い。",
                    items=(MappingProxyType({"memory_ref": "memory_safe_1"}),),
                ),
                same_session_continuity=AgenticPredecisionContextSection(
                    status="available",
                    status_detail="",
                    summary="同じ会話内の修正を保持。",
                    items=(
                        MappingProxyType({"topic": "映像表現", "correction_rank": 1}),
                    ),
                ),
                system_topology=AgenticPredecisionContextSection(
                    status="conflict",
                    status_detail="component_reports_disagree",
                    summary="表示系の報告が一致していない。",
                    items=(
                        MappingProxyType(
                            {
                                "component": "projection_host",
                                "reported_states": ("ready", "unknown"),
                            }
                        ),
                    ),
                ),
            )

        first = _context(MappingProxyType({"z_value": "最後", "a_value": "先頭"}))
        second = _context(MappingProxyType({"a_value": "先頭", "z_value": "最後"}))
        for context in (first, second):
            self.assertEqual(
                provider.decide(
                    self._provider_request(
                        human_wish="意図が見える映像にして。",
                        context_refs=MappingProxyType({}),
                        predecision_context=context,
                    )
                ),
                candidate,
            )

        observed = [
            call["input_payload"]["predecision_context"]  # type: ignore[index]
            for call in completion.calls
        ]
        encoded = [
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            for value in observed
        ]
        self.assertEqual(encoded[0], encoded[1])
        self.assertLessEqual(
            len(encoded[0].encode("utf-8")),
            MAX_PREDECISION_CONTEXT_SERIALIZED_BYTES,
        )
        self.assertEqual(
            observed[0]["latest_user_correction"],  # type: ignore[index]
            "照明ではなく映像を変えて。",
        )
        self.assertEqual(
            observed[0]["relevant_memory"]["status"],  # type: ignore[index]
            "stale",
        )
        self.assertEqual(
            observed[0]["system_topology"]["status"],  # type: ignore[index]
            "conflict",
        )
        self.assertIn("latest_user_correction", completion.calls[0]["system_prompt"])

    def test_runtime_payload_matches_draft_2020_12_schema_and_safety_parity(self) -> None:
        candidate = self._conversation_candidate()
        completion = _CapturingCompletion(candidate)
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        available = lambda items: AgenticPredecisionContextSection(  # noqa: E731
            status="available",
            status_detail="",
            summary="bounded context",
            items=items,
        )
        context = AgenticPredecisionContext(
            latest_user_correction="映像の方を先に変えて。",
            environment_state=available(
                (MappingProxyType({"item_type": "state", "available": True}),)
            ),
            relevant_memory=available(
                (
                    MappingProxyType(
                        {"item_type": "working_memory", "safe_ref_count": 2}
                    ),
                )
            ),
            same_session_continuity=available(
                (
                    MappingProxyType(
                        {"item_type": "decision", "correction_rank": 1}
                    ),
                )
            ),
        )

        self.assertEqual(
            provider.decide(
                self._provider_request(
                    human_wish="いまの状態を踏まえて映像を変えて。",
                    context_refs=MappingProxyType({}),
                    predecision_context=context,
                )
            ),
            candidate,
        )
        payload = completion.calls[0]["input_payload"]["predecision_context"]
        schema = json.loads(
            (
                REPO_ROOT
                / "contracts"
                / "turn"
                / "agentic-predecision-context.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(_draft_2020_12_errors(payload, schema), [])
        self.assertIsInstance(
            payload["relevant_memory"]["items"][0]["safe_ref_count"],  # type: ignore[index]
            int,
        )
        self.assertIsInstance(
            payload["same_session_continuity"]["items"][0]["correction_rank"],  # type: ignore[index]
            int,
        )

        forbidden_keys = (
            "api_key",
            "raw_note",
            "device_path",
            "user_secret_hint",
            "provider_payload_copy",
            "command_line_copy",
            "event_jsonl_record",
        )
        for key in forbidden_keys:
            invalid = json.loads(json.dumps(payload, ensure_ascii=False))
            invalid["environment_state"]["items"] = [{key: "safe"}]
            with self.subTest(forbidden_key=key):
                self.assertTrue(_draft_2020_12_errors(invalid, schema))

        forbidden_strings = (
            "https://example.invalid/value",
            "C:\\private\\value",
            "Bearer PRIVATE_VALUE",
            "token PRIVATE_VALUE",
            "access_token=PRIVATE_VALUE",
            "api_key=PRIVATE_VALUE",
            "password=PRIVATE_VALUE",
            "prompt: PRIVATE_VALUE",
            "System Prompt PRIVATE_VALUE",
        )
        for value in forbidden_strings:
            invalid = json.loads(json.dumps(payload, ensure_ascii=False))
            invalid["environment_state"]["items"] = [{"note": value}]
            with self.subTest(forbidden_string=value):
                self.assertTrue(_draft_2020_12_errors(invalid, schema))

        for field, value in (
            ("id", "x" * 97),
            ("description", "x" * 181),
        ):
            invalid = json.loads(json.dumps(payload, ensure_ascii=False))
            invalid["capability_view"]["capabilities"][0][field] = value
            with self.subTest(capability_field=field):
                self.assertTrue(_draft_2020_12_errors(invalid, schema))

    def test_human_wish_limits_fail_closed_before_completion(self) -> None:
        candidate = self._conversation_candidate()
        exact_wish = "\U0001f4a1" * 600
        exact_completion = _CapturingCompletion(candidate)
        exact_provider = OpenAICompatibleAgenticTurnProvider(exact_completion)
        self.assertEqual(
            exact_provider.decide(
                self._provider_request(
                    human_wish=exact_wish,
                    context_refs=MappingProxyType({}),
                )
            ),
            candidate,
        )
        self.assertEqual(len(exact_wish), 600)
        self.assertEqual(len(exact_wish.encode("utf-8")), 2_400)
        self.assertEqual(len(exact_completion.calls), 1)

        for wish in ("x" * 601, "\U0001f4a1" * 601):
            completion = _CapturingCompletion(candidate)
            provider = OpenAICompatibleAgenticTurnProvider(completion)
            with self.subTest(chars=len(wish), bytes=len(wish.encode("utf-8"))):
                self.assertIsNone(
                    provider.decide(
                        self._provider_request(
                            human_wish=wish,
                            context_refs=MappingProxyType({}),
                        )
                    )
                )
                self.assertEqual(completion.calls, [])

    def test_predecision_context_rejects_size_count_depth_and_private_shapes(self) -> None:
        available = lambda items: AgenticPredecisionContextSection(  # noqa: E731
            status="available",
            status_detail="",
            summary="context",
            items=items,
        )
        dense_items = tuple(
            MappingProxyType({f"fact_{index:02d}": "x" * 160 for index in range(12)})
            for _ in range(8)
        )
        cases = {
            "item_count": AgenticPredecisionContext(
                environment_state=available(
                    tuple(MappingProxyType({"state": "known"}) for _ in range(9))
                )
            ),
            "depth": AgenticPredecisionContext(
                environment_state=available(
                    (MappingProxyType({"a": {"b": {"c": {"d": "too_deep"}}}}),)
                )
            ),
            "private_key": AgenticPredecisionContext(
                environment_state=available(
                    (MappingProxyType({"api_key": "PRIVATE_SENTINEL"}),)
                )
            ),
            "private_key_pattern": AgenticPredecisionContext(
                environment_state=available(
                    (MappingProxyType({"user_secret_hint": "PRIVATE_SENTINEL"}),)
                )
            ),
            "private_value": AgenticPredecisionContext(
                environment_state=available(
                    (MappingProxyType({"note": "https://example.invalid/private"}),)
                )
            ),
            "token_value": AgenticPredecisionContext(
                environment_state=available(
                    (MappingProxyType({"note": "Bearer PRIVATE_SENTINEL"}),)
                )
            ),
            "path_value": AgenticPredecisionContext(
                environment_state=available(
                    (MappingProxyType({"note": "C:\\private\\sentinel"}),)
                )
            ),
            "serialized_size": AgenticPredecisionContext(
                environment_state=available(dense_items),
                relevant_memory=available(dense_items),
                same_session_continuity=available(dense_items),
                system_topology=available(dense_items),
            ),
        }
        for name, context in cases.items():
            completion = _CapturingCompletion(self._conversation_candidate())
            provider = OpenAICompatibleAgenticTurnProvider(completion)
            with self.subTest(name=name):
                observed = provider.decide(
                    self._provider_request(
                        human_wish="現在の情報から判断して。",
                        context_refs=MappingProxyType({}),
                        predecision_context=context,
                    )
                )
                self.assertIsNone(observed)
                self.assertEqual(completion.calls, [])

    def test_receipt_prompt_contains_only_bounded_action_response_context(self) -> None:
        private_wish = "PRIVATE_WISH_SENTINEL"
        private_ref = "PRIVATE_REF_SENTINEL"
        preexecution_response = "PREEXECUTION_RESPONSE_SENTINEL"
        decision = {
            "schemaVersion": 1,
            "kind": "capability",
            "response": {
                "speech": preexecution_response,
                "display": preexecution_response,
            },
            "capability": {"id": "light_on", "arguments": {}},
        }
        receipt_response = {"speech": "完了しました。", "display": "完了"}
        completion = _CapturingCompletion(decision, receipt_response)
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        provider.decide(
            self._provider_request(
                human_wish=private_wish,
                context_refs=MappingProxyType({"observation_ref": private_ref}),
            )
        )

        observed = provider.respond_to_receipt(
            AgenticActionReceipt(
                decision_ref="evt_receipt_context",
                receipt_ref="evt_receipt_context:success",
                action_id="light_on",
                capability_id="light_on",
                semantic_purpose="部屋の明かりをつける",
                target_ref="light",
                expected_state="on",
                phase="success",
                status="success",
                confirmed=True,
                executed=True,
                execution_certainty="executed",
                review_status="succeeded",
                review_checkpoint_class="matched",
            )
        )

        self.assertEqual(observed, receipt_response)
        receipt_call = completion.calls[1]
        self.assertEqual(
            receipt_call["input_payload"],
            {
                "context_version": "agentic_action_response_context.v1",
                "decision_ref": "evt_receipt_context",
                "receipt_ref": "evt_receipt_context:success",
                "action_id": "light_on",
                "capability_id": "light_on",
                "semantic_purpose": "部屋の明かりをつける",
                "target_ref": "light",
                "expected_state": "on",
                "phase": "success",
                "status": "success",
                "confirmed": True,
                "executed": True,
                "execution_certainty": "executed",
                "review_status": "succeeded",
                "review_checkpoint_class": "matched",
            },
        )
        serialized = json.dumps(receipt_call, ensure_ascii=False)
        self.assertNotIn(private_wish, serialized)
        self.assertNotIn(private_ref, serialized)
        self.assertNotIn(preexecution_response, serialized)
        decision_format = completion.calls[0]["response_format"]
        self.assertEqual(decision_format["type"], "json_schema")  # type: ignore[index]
        self.assertEqual(
            decision_format["json_schema"]["name"],  # type: ignore[index]
            AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME,
        )
        self.assertTrue(
            decision_format["json_schema"]["strict"]  # type: ignore[index]
        )
        self.assertIsNone(receipt_call["response_format"])

    def test_receipt_context_rejects_private_or_mismatched_fields_before_provider(self) -> None:
        completion = _CapturingCompletion({"speech": "unused", "display": "unused"})
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        cases = (
            AgenticActionReceipt(
                decision_ref="evt_private_context",
                receipt_ref="evt_private_context:success",
                action_id="door_close",
                capability_id="door_close",
                semantic_purpose="C:\\PRIVATE_PATH_SENTINEL",
                target_ref="door",
                expected_state="closed",
                phase="success",
                status="success",
                confirmed=True,
                executed=True,
                execution_certainty="executed",
                review_status="succeeded",
                review_checkpoint_class="matched",
            ),
            AgenticActionReceipt(
                decision_ref="evt_mismatch_context",
                receipt_ref="evt_other:success",
                action_id="door_close",
                capability_id="door_close",
                semantic_purpose="中扉を閉める",
                target_ref="door",
                expected_state="closed",
                phase="success",
                status="success",
                confirmed=True,
                executed=False,
                execution_certainty="executed",
                review_status="succeeded",
                review_checkpoint_class="matched",
            ),
        )

        for receipt in cases:
            with self.subTest(receipt=receipt.receipt_ref):
                self.assertIsNone(provider.respond_to_receipt(receipt))
        self.assertEqual(completion.calls, [])

    def test_provider_wire_fire_decision_normalizes_nullable_arguments_once(self) -> None:
        completion = _CapturingCompletion(
            {
                "schemaVersion": 1,
                "kind": "capability",
                "response": {"speech": "炎を出します。", "display": "Fire"},
                "capability": {
                    "id": "projection.fire.start",
                    "arguments": {
                        "position": {"x": 0.4, "y": -0.2},
                        "strength": 0.8,
                        "durationMs": None,
                    },
                },
            }
        )
        provider = OpenAICompatibleAgenticTurnProvider(completion)

        observed = provider.decide(
            self._provider_request(
                human_wish="右上に炎を出して。",
                context_refs={},
            )
        )

        self.assertEqual(
            observed,
            {
                "schemaVersion": 1,
                "kind": "capability",
                "response": {"speech": "炎を出します。", "display": "Fire"},
                "capability": {
                    "id": "projection.fire.start",
                    "arguments": {
                        "position": {"x": 0.4, "y": -0.2},
                        "strength": 0.8,
                    },
                },
            },
        )
        self.assertEqual(len(completion.calls), 1)

    def test_provider_wire_malformed_extra_and_wrong_branch_remain_fail_closed(self) -> None:
        capability = {
            "id": "projection.fire.start",
            "arguments": {
                "position": None,
                "strength": None,
                "durationMs": None,
            },
        }
        cases = (
            {
                "schemaVersion": 1,
                "kind": "conversation",
                "response": {"speech": "会話です。", "display": "会話"},
                "capability": None,
                "extra": "PRIVATE_PROVIDER_TEXT",
            },
            {
                "schemaVersion": 1,
                "kind": "conversation",
                "response": {"speech": "会話です。", "display": "会話"},
                "capability": capability,
            },
            {
                "schemaVersion": 1,
                "kind": "capability",
                "response": {"speech": "炎です。", "display": "Fire"},
                "capability": None,
            },
        )
        for candidate in cases:
            completion = _CapturingCompletion(candidate)
            provider = OpenAICompatibleAgenticTurnProvider(completion)
            with self.subTest(candidate=candidate):
                events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
                    self._turn("演出を判断して。")
                )
                held = next(
                    event for event in events if event["type"] == "agentic.decision"
                )
                self.assertEqual(held["data"]["status"], "held")
                self.assertIn(
                    held["data"].get("validation_subcode"),
                    {"decision_shape_invalid", "capability_shape_invalid"},
                )
                self.assertEqual(len(completion.calls), 1)
                self.assertNotIn(
                    "PRIVATE_PROVIDER_TEXT",
                    json.dumps(events, ensure_ascii=False),
                )

    def test_provider_wire_schema_uses_only_required_closed_objects(self) -> None:
        completion = _CapturingCompletion(self._conversation_candidate())
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        provider.decide(
            self._provider_request(
                human_wish="会話して。",
                context_refs={},
            )
        )
        response_format = completion.calls[0]["response_format"]
        schema = response_format["json_schema"]["schema"]  # type: ignore[index]

        def assert_strict_objects(node: object) -> None:
            if type(node) is dict:
                if node.get("type") == "object":
                    self.assertFalse(node.get("additionalProperties", True))
                    self.assertEqual(
                        set(node.get("required", [])),
                        set(node.get("properties", {})),
                    )
                for value in node.values():
                    assert_strict_objects(value)
            elif type(node) is list:
                for value in node:
                    assert_strict_objects(value)

        assert_strict_objects(schema)

    def test_provider_wire_schema_is_fresh_after_completion_mutation(self) -> None:
        completion = _MutatingResponseFormatCompletion(
            self._conversation_candidate(),
            self._conversation_candidate(),
        )
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        request_payload = self._provider_request(
            human_wish="会話して。",
            context_refs={},
        )

        provider.decide(request_payload)
        provider.decide(request_payload)

        first_format = completion.calls[0]["response_format"]
        second_format = completion.calls[1]["response_format"]
        self.assertEqual(
            first_format["json_schema"]["name"],  # type: ignore[index]
            "malicious_mutation",
        )
        self.assertEqual(
            second_format["json_schema"]["name"],  # type: ignore[index]
            AGENTIC_TURN_PROVIDER_OUTPUT_SCHEMA_NAME,
        )
        self.assertTrue(
            second_format["json_schema"]["strict"]  # type: ignore[index]
        )
        self.assertEqual(
            second_format["json_schema"]["schema"]["type"],  # type: ignore[index]
            "object",
        )

    def test_structured_completion_parses_one_json_value_without_fallback(self) -> None:
        candidate = self._conversation_candidate()
        response_payload = {
            "choices": [
                {"message": {"content": json.dumps(candidate, ensure_ascii=False)}}
            ]
        }
        opener = _FakeOpener(_FakeHttpResponse(response_payload))
        completion = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
            opener=opener,
        )
        observed = completion.complete_json(
            system_prompt="Return JSON.",
            input_payload={"human_wish": "こんにちは"},
            max_tokens=100,
        )

        self.assertEqual(observed, candidate)
        outbound_request = opener.calls[0][0]
        outbound_payload = json.loads(outbound_request.data.decode("utf-8"))
        self.assertEqual(outbound_payload["response_format"], {"type": "json_object"})
        self.assertEqual(
            json.loads(outbound_payload["messages"][1]["content"]),
            {"human_wish": "こんにちは"},
        )
        self.assertFalse(outbound_request.has_header("Authorization"))

    def test_structured_completion_carries_exact_loopback_receipt_without_secret_header(self) -> None:
        candidate = self._conversation_candidate()
        response_payload = {
            "choices": [
                {"message": {"content": json.dumps(candidate, ensure_ascii=False)}}
            ],
            SWORD_PROVIDER_ATTEMPT_RECEIPT_KEY: _provider_attempt_receipt(),
        }
        opener = _FakeOpener(_FakeHttpResponse(response_payload))
        completion = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:18786/v1",
            model="gpt-4o-mini",
            opener=opener,
        )

        observed = completion.complete_json(
            system_prompt="Return JSON.",
            input_payload={"human_wish": "synthetic"},
            max_tokens=720,
            decision_event_id=DECISION_EVENT_ID,
        )

        self.assertEqual(
            observed,
            StructuredCompletionResult(
                value=candidate,
                provider_attempt_receipt=_provider_attempt_receipt(),
            ),
        )
        outbound_request = opener.calls[0][0]
        headers = {name.lower(): value for name, value in outbound_request.header_items()}
        self.assertEqual(
            headers[SWORD_DECISION_EVENT_ID_HEADER.lower()],
            DECISION_EVENT_ID,
        )
        self.assertNotIn("authorization", headers)

        missing_receipt = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:18786/v1",
            model="gpt-4o-mini",
            opener=_FakeOpener(
                _FakeHttpResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(candidate)
                                }
                            }
                        ]
                    }
                )
            ),
        )
        with self.assertRaisesRegex(
            StructuredCompletionInvalid,
            "structured_completion_response_invalid",
        ):
            missing_receipt.complete_json(
                system_prompt="Return JSON.",
                input_payload={"human_wish": "synthetic"},
                max_tokens=720,
                decision_event_id=DECISION_EVENT_ID,
            )

        for invalid_event_id in (
            "evt_" + ("A" * 32),
            DECISION_EVENT_ID + "," + DECISION_EVENT_ID,
            True,
        ):
            with self.subTest(invalid_event_id=invalid_event_id), self.assertRaisesRegex(
                StructuredCompletionInvalid,
                "structured_completion_input_invalid",
            ):
                completion.complete_json(
                    system_prompt="Return JSON.",
                    input_payload={"human_wish": "synthetic"},
                    max_tokens=720,
                    decision_event_id=invalid_event_id,  # type: ignore[arg-type]
                )

    def test_credential_configuration_holds_without_header_or_sentinel_publication(
        self,
    ) -> None:
        private_credential = "PRIVATE_CREDENTIAL_SENTINEL"
        local = {
            "THOUGHT_CORE_LLM_ENABLED": "1",
            "THOUGHT_CORE_LLM_PROVIDER": "openai-compatible",
            "THOUGHT_CORE_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
            "THOUGHT_CORE_LLM_MODEL": "local-model",
        }
        for credential_name in ("THOUGHT_CORE_LLM_API_KEY", "OPENAI_API_KEY"):
            with self.subTest(credential_name=credential_name), patch.dict(
                os.environ,
                {**local, credential_name: private_credential},
                clear=True,
            ):
                provider = build_agentic_turn_provider_from_env()
            self.assertIsInstance(provider, UnavailableAgenticTurnProvider)
            events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
                self._turn("部屋を明るくして。")
            )
            serialized = json.dumps(events, ensure_ascii=False)
            held = next(
                event for event in events if event["type"] == "agentic.decision"
            )
            self.assertEqual(held["data"]["reason"], "agentic_provider_unavailable")
            self.assertNotIn(private_credential, serialized)

        response_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            self._conversation_candidate(),
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        opener = _FakeOpener(_FakeHttpResponse(response_payload))
        completion = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
            opener=opener,
        )
        completion.complete_json(
            system_prompt="Return JSON.",
            input_payload={"human_wish": "こんにちは"},
            max_tokens=100,
        )
        outbound_request = opener.calls[0][0]
        outbound_wire = "|".join(
            (
                outbound_request.full_url,
                outbound_request.data.decode("utf-8"),
                json.dumps(dict(outbound_request.header_items())),
            )
        )
        self.assertFalse(outbound_request.has_header("Authorization"))
        self.assertNotIn(private_credential, outbound_wire)

    def test_loopback_redirect_is_rejected_before_external_transmission(self) -> None:
        completion = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
        )
        redirect_handlers = [
            handler
            for handler in completion._opener.handlers  # type: ignore[attr-defined]
            if isinstance(handler, _RejectAllRedirects)
        ]
        self.assertEqual(len(redirect_handlers), 1)
        redirect_handler = redirect_handlers[0]

        redirect_sentinel = "PRIVATE_REDIRECT_SENTINEL"
        redirect_headers = Message()
        redirect_headers["Location"] = (
            f"https://example.invalid/{redirect_sentinel}"
        )
        redirect_handler.parent = Mock()
        source_request = request.Request(
            "http://127.0.0.1:11434/v1/chat/completions"
        )
        source_response = Mock()
        with self.assertRaisesRegex(
            StructuredCompletionUnavailable,
            "structured_completion_unavailable",
        ) as raised:
            redirect_handler.http_error_302(
                source_request,
                source_response,
                302,
                "Found",
                redirect_headers,
            )
        redirect_handler.parent.open.assert_not_called()
        source_response.read.assert_not_called()
        self.assertNotIn(redirect_sentinel, str(raised.exception))

    def test_malformed_and_timeout_fail_closed(self) -> None:
        cases = (
            (
                StructuredCompletionInvalid("PRIVATE_MALFORMED_SENTINEL"),
                "agentic_decision_invalid",
            ),
            (TimeoutError("PRIVATE_TIMEOUT_SENTINEL"), "agentic_provider_unavailable"),
            (
                StructuredCompletionUnavailable("PRIVATE_TRANSPORT_SENTINEL"),
                "agentic_provider_unavailable",
            ),
        )
        for failure, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                provider = OpenAICompatibleAgenticTurnProvider(
                    _CapturingCompletion(failure)
                )
                events = ThoughtLoop(agentic_turn_provider=provider).run_dicts(
                    self._turn("PRIVATE_WISH_SENTINEL")
                )
                held = next(
                    event for event in events if event["type"] == "agentic.decision"
                )
                serialized = json.dumps(events, ensure_ascii=False)

                self.assertEqual(held["data"]["status"], "held")
                self.assertEqual(held["data"]["reason"], expected_reason)
                if isinstance(failure, StructuredCompletionInvalid):
                    self.assertEqual(
                        held["data"]["validation_subcode"],
                        "provider_content_invalid",
                    )
                else:
                    self.assertNotIn("validation_subcode", held["data"])
                self.assertNotIn("PRIVATE_WISH_SENTINEL", serialized)
                self.assertNotIn("PRIVATE_MALFORMED_SENTINEL", serialized)
                self.assertNotIn("PRIVATE_TIMEOUT_SENTINEL", serialized)
                self.assertNotIn("PRIVATE_TRANSPORT_SENTINEL", serialized)
                self.assertNotIn("action.proposed", [event["type"] for event in events])

    def test_provider_content_invalid_is_fixed_and_completion_is_called_once(self) -> None:
        completion = _CapturingCompletion(
            StructuredCompletionInvalid("PRIVATE_PROVIDER_CONTENT")
        )
        provider = OpenAICompatibleAgenticTurnProvider(completion)
        request_value = self._provider_request(
            human_wish="PRIVATE_WISH_SENTINEL",
            context_refs={},
        )

        with self.assertRaises(AgenticTurnProviderDecisionInvalid) as raised:
            provider.decide(request_value)

        self.assertEqual(raised.exception.validation_subcode, "provider_content_invalid")
        self.assertEqual(len(completion.calls), 1)
        self.assertNotIn("PRIVATE_PROVIDER_CONTENT", repr(raised.exception))

    def test_structured_transport_malformed_and_timeout_have_fixed_failures(self) -> None:
        malformed_opener = _FakeOpener(
            _FakeHttpResponse(
                {
                    "choices": [
                        {"message": {"content": "PRIVATE_RAW_OUTPUT_SENTINEL"}}
                    ]
                }
            )
        )
        completion = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
            opener=malformed_opener,
        )
        with self.assertRaisesRegex(
            StructuredCompletionInvalid,
            "structured_completion_response_invalid",
        ):
            completion.complete_json(
                system_prompt="Return JSON.",
                input_payload={"human_wish": "private"},
                max_tokens=100,
            )

        timeout_completion = OpenAICompatibleStructuredCompletion(
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
            opener=_FakeOpener(TimeoutError("PRIVATE_TIMEOUT_DETAIL")),
        )
        with self.assertRaisesRegex(
            StructuredCompletionUnavailable,
            "structured_completion_unavailable",
        ):
            timeout_completion.complete_json(
                system_prompt="Return JSON.",
                input_payload={"human_wish": "private"},
                max_tokens=100,
            )

    @staticmethod
    def _conversation_candidate() -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "kind": "conversation",
            "response": {"speech": "一緒に考えます。", "display": "検討中です。"},
        }

    @staticmethod
    def _provider_request(
        *,
        human_wish: str,
        context_refs: Mapping[str, object],
        predecision_context: AgenticPredecisionContext | None = None,
        bounded_capability_constraint: (
            AgenticZeroArgumentCapabilityConstraint | None
        ) = None,
        decision_event_id: str = DECISION_EVENT_ID,
    ) -> AgenticTurnProviderRequest:
        values: dict[str, object] = {
            "human_wish": human_wish,
            "context_refs": context_refs,
            "decision_event_id": decision_event_id,
            "capability_view": AgenticCapabilityView(
                catalog_id="catalog-test",
                catalog_version="catalog-test.v1",
                capabilities=(
                    AgenticCapabilityViewEntry(
                        capability_id="light_on",
                        description="ライトをつける",
                        available=True,
                    ),
                    AgenticCapabilityViewEntry(
                        capability_id="aircon_on",
                        description="エアコンをつける",
                        available=False,
                    ),
                ),
            ),
            "agent_context": MappingProxyType(
                {
                    "bounded_wish_refs": (),
                    "observation_refs": (),
                    "memory_refs": (),
                    "working_memory_item_count": 0,
                }
            ),
        }
        if predecision_context is not None:
            values["predecision_context"] = predecision_context
        if bounded_capability_constraint is not None:
            values["bounded_capability_constraint"] = bounded_capability_constraint
        return AgenticTurnProviderRequest(**values)  # type: ignore[arg-type]

    @staticmethod
    def _turn(text: str) -> dict[str, object]:
        return {
            "text": text,
            "turn_id": "runtime_provider_turn",
            "session_id": "runtime_provider_session",
            "locale": "ja-JP",
            "context_refs": {},
        }


def _draft_2020_12_errors(
    value: object,
    schema: Mapping[str, object],
    *,
    root: Mapping[str, object] | None = None,
    path: str = "$",
) -> list[str]:
    """Execute the Draft 2020-12 keywords used by the predecision schema."""

    root = schema if root is None else root
    reference = schema.get("$ref")
    if isinstance(reference, str):
        target: object = root
        if not reference.startswith("#/"):
            return [f"{path}: unsupported external reference"]
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or token not in target:
                return [f"{path}: unresolved reference"]
            target = target[token]
        if not isinstance(target, Mapping):
            return [f"{path}: invalid reference target"]
        return _draft_2020_12_errors(value, target, root=root, path=path)

    errors: list[str] = []
    for option in schema.get("allOf", []):
        if isinstance(option, Mapping):
            errors.extend(_draft_2020_12_errors(value, option, root=root, path=path))

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        matches = [
            not _draft_2020_12_errors(value, option, root=root, path=path)
            for option in any_of
            if isinstance(option, Mapping)
        ]
        if not any(matches):
            errors.append(f"{path}: anyOf did not match")

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = sum(
            not _draft_2020_12_errors(value, option, root=root, path=path)
            for option in one_of
            if isinstance(option, Mapping)
        )
        if matches != 1:
            errors.append(f"{path}: oneOf matched {matches} branches")

    negated = schema.get("not")
    if isinstance(negated, Mapping) and not _draft_2020_12_errors(
        value,
        negated,
        root=root,
        path=path,
    ):
        errors.append(f"{path}: forbidden by not")

    condition = schema.get("if")
    consequent = schema.get("then")
    if (
        isinstance(condition, Mapping)
        and isinstance(consequent, Mapping)
        and not _draft_2020_12_errors(value, condition, root=root, path=path)
    ):
        errors.extend(
            _draft_2020_12_errors(value, consequent, root=root, path=path)
        )

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const mismatch")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: enum mismatch")

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _draft_2020_12_type_matches(
        value,
        expected_type,
    ):
        errors.append(f"{path}: expected {expected_type}")
        return errors

    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}: missing {key}")
        max_properties = schema.get("maxProperties")
        if isinstance(max_properties, int) and len(value) > max_properties:
            errors.append(f"{path}: too many properties")
        property_names = schema.get("propertyNames")
        if isinstance(property_names, Mapping):
            for key in value:
                errors.extend(
                    _draft_2020_12_errors(
                        key,
                        property_names,
                        root=root,
                        path=f"{path}.<key>",
                    )
                )
        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, Mapping) else {}
        for key, item in value.items():
            property_schema = properties.get(key)
            if isinstance(property_schema, Mapping):
                errors.extend(
                    _draft_2020_12_errors(
                        item,
                        property_schema,
                        root=root,
                        path=f"{path}.{key}",
                    )
                )
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(f"{path}: unexpected {key}")
            elif isinstance(additional, Mapping):
                errors.extend(
                    _draft_2020_12_errors(
                        item,
                        additional,
                        root=root,
                        path=f"{path}.{key}",
                    )
                )

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: too few items")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(
                    _draft_2020_12_errors(
                        item,
                        item_schema,
                        root=root,
                        path=f"{path}[{index}]",
                    )
                )

    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: too short")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: too long")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: pattern mismatch")

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: below minimum")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"{path}: above maximum")
    return errors


def _draft_2020_12_type_matches(value: object, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, Mapping)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
        ) or (
            isinstance(value, float)
            and value.is_integer()
        )
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return False
