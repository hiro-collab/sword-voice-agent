import json
import os
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
    AgenticActionReceipt,
    AgenticCapabilityView,
    AgenticCapabilityViewEntry,
    AgenticTurnProviderRequest,
    UnavailableAgenticTurnProvider,
)
from thought_core.agentic_turn_runtime_provider import (  # noqa: E402
    OpenAICompatibleAgenticTurnProvider,
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
    StructuredCompletionInvalid,
    StructuredCompletionUnavailable,
    _RejectAllRedirects,
)
from thought_core.server import _build_default_thought_loop, create_server  # noqa: E402
from thought_core.tools import HomeControlHttpTools  # noqa: E402


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
    ) -> object:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "input_payload": input_payload,
                "max_tokens": max_tokens,
            }
        )
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
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

    def test_valid_loopback_agentic_capability_lifecycle_uses_only_structured_client(
        self,
    ) -> None:
        private_wish = "PRIVATE_AGENTIC_WISH_SENTINEL"
        decision = {
            "schemaVersion": 1,
            "kind": "capability",
            "response": {
                "speech": "中扉を閉める前に確認するね。",
                "display": "中扉を閉める確認を準備します。",
            },
            "capability": {"id": "door_close", "arguments": {}},
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
            "action_id",
            "phase",
            "status",
            "confirmed",
            "executed",
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
            [event for event in events if event["type"] == "tool.started"],
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
        payload = call["input_payload"]
        self.assertEqual(
            set(payload),  # type: ignore[arg-type]
            {
                "human_wish",
                "catalog",
                "capabilities",
                "context_refs",
                "agent_context",
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
        serialized = json.dumps(call, ensure_ascii=False)
        self.assertNotIn("provider_payload", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("endpoint", serialized)

    def test_receipt_prompt_contains_only_receipt_facts(self) -> None:
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
                action_id="light_on",
                phase="success",
                status="succeeded",
                confirmed=True,
                executed=True,
            )
        )

        self.assertEqual(observed, receipt_response)
        receipt_call = completion.calls[1]
        self.assertEqual(
            receipt_call["input_payload"],
            {
                "action_id": "light_on",
                "phase": "success",
                "status": "succeeded",
                "confirmed": True,
                "executed": True,
            },
        )
        serialized = json.dumps(receipt_call, ensure_ascii=False)
        self.assertNotIn(private_wish, serialized)
        self.assertNotIn(private_ref, serialized)
        self.assertNotIn(preexecution_response, serialized)

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
                self.assertNotIn("PRIVATE_WISH_SENTINEL", serialized)
                self.assertNotIn("PRIVATE_MALFORMED_SENTINEL", serialized)
                self.assertNotIn("PRIVATE_TIMEOUT_SENTINEL", serialized)
                self.assertNotIn("PRIVATE_TRANSPORT_SENTINEL", serialized)
                self.assertNotIn("action.proposed", [event["type"] for event in events])

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
    ) -> AgenticTurnProviderRequest:
        return AgenticTurnProviderRequest(
            human_wish=human_wish,
            context_refs=context_refs,
            capability_view=AgenticCapabilityView(
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
            agent_context=MappingProxyType(
                {
                    "bounded_wish_refs": (),
                    "observation_refs": (),
                    "memory_refs": (),
                    "working_memory_item_count": 0,
                }
            ),
        )

    @staticmethod
    def _turn(text: str) -> dict[str, object]:
        return {
            "text": text,
            "turn_id": "runtime_provider_turn",
            "session_id": "runtime_provider_session",
            "locale": "ja-JP",
            "context_refs": {},
        }
