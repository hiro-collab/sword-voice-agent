"""Deterministic fake-transport coverage for the phase-one OpenAI broker."""

from __future__ import annotations

import ast
import json
import os
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import request

from sword_voice_agent.adapters.openai_broker import (
    ALLOWED_PORTS,
    DECISION_MAX_TOKENS,
    DEFAULT_SECRET_FILE,
    FIXED_MODEL,
    ISOLATED_PORT,
    LOOPBACK_HOST,
    MAX_BODY_BYTES,
    MAX_SYSTEM_BYTES,
    MAX_TIMEOUT_S,
    MAX_UPSTREAM_BYTES,
    MAX_USER_BYTES,
    RECEIPT_MAX_TOKENS,
    SECRET_SOURCE_CLASS,
    STANDARD_PORT,
    UPSTREAM_URL,
    BrokerConfig,
    BrokerError,
    OpenAIBroker,
    _RejectRedirects,
    agentic_turn_provider_response_format,
    build_safe_opener,
    read_openai_api_key,
)
from sword_voice_agent.apps.openai_broker import COMPLETIONS_PATH, HEALTH_PATH, create_server
from sword_voice_agent.apps.openai_broker import (
    BODY_READ_TIMEOUT_S,
    BODY_READ_CHUNK_BYTES,
    SingleAdmissionHTTPServer,
    _body_error_response,
    _read_exact_body,
    _validated_content_length,
)

PRIVATE_SENTINEL = "SYNTHETIC_PRIVATE_BROKER_SENTINEL"


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


class _Opener:
    def __init__(self, body: bytes | None = None, failure: BaseException | None = None) -> None:
        self._body = body or b'{"choices":[{"message":{"content":"{\\"kind\\":\\"hold\\"}"}}]}'
        self._failure = failure
        self.calls: list[tuple[request.Request, float]] = []

    def open(self, target: request.Request, data: object = None, timeout: float = 0.0) -> _Response:
        self.calls.append((target, timeout))
        if self._failure is not None:
            raise self._failure
        return _Response(self._body)


class _BodyReader:
    def __init__(self, result: bytes | BaseException) -> None:
        self._result = result
        self._returned = False
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        raise AssertionError("buffered read must not be used")

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if isinstance(self._result, BaseException):
            raise self._result
        if self._returned:
            return b""
        self._returned = True
        return self._result


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class _TrickleReader:
    def __init__(self, clock: _FakeClock, chunks: list[bytes], delays: list[float]) -> None:
        self._clock = clock
        self._chunks = iter(chunks)
        self._delays = iter(delays)
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        raise AssertionError("buffered read must not be used")

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        self._clock.value += next(self._delays)
        return next(self._chunks)


def _payload(max_tokens: int = DECISION_MAX_TOKENS) -> dict[str, object]:
    return {
        "model": FIXED_MODEL,
        "messages": [
            {"role": "system", "content": "return exactly one JSON object"},
            {"role": "user", "content": "synthetic bounded user wish"},
        ],
        "temperature": 0,
        "response_format": (
            agentic_turn_provider_response_format()
            if max_tokens == DECISION_MAX_TOKENS
            else {"type": "json_object"}
        ),
        "max_tokens": max_tokens,
    }


def _broker(
    *,
    body: bytes | None = None,
    failure: BaseException | None = None,
    request_budget: int = 64,
) -> tuple[OpenAIBroker, _Opener]:
    opener = _Opener(body, failure)
    broker = OpenAIBroker(
        BrokerConfig(secret_file=Path("synthetic.env"), request_budget=request_budget),
        secret_loader=lambda _: PRIVATE_SENTINEL,
        opener_factory=lambda: opener,
    )
    return broker, opener


class BrokerConfigTests(unittest.TestCase):
    def test_closed_ports_source_class_timeout_and_budget(self) -> None:
        self.assertEqual(ALLOWED_PORTS, frozenset({STANDARD_PORT, ISOLATED_PORT}))
        self.assertEqual(BrokerConfig().port, STANDARD_PORT)
        self.assertEqual(BrokerConfig(port=ISOLATED_PORT).port, ISOLATED_PORT)
        self.assertEqual(BrokerConfig().secret_source_class, SECRET_SOURCE_CLASS)
        self.assertIn("services/thought-core/.env", DEFAULT_SECRET_FILE.as_posix())
        for port in (18888, 0, 18785):
            with self.assertRaises(ValueError):
                BrokerConfig(port=port)
        for timeout in (0, MAX_TIMEOUT_S + 0.1, True):
            with self.assertRaises(ValueError):
                BrokerConfig(timeout_s=timeout)
        for budget in (0, 65, True):
            with self.assertRaises(ValueError):
                BrokerConfig(request_budget=budget)
        with self.assertRaises(ValueError):
            BrokerConfig(secret_source_class="not-allowed")


class BrokerForwardingTests(unittest.TestCase):
    def test_only_fixed_destination_and_single_authorization_header_leave_boundary(self) -> None:
        broker, opener = _broker()
        result = broker.complete(json.dumps(_payload()).encode("utf-8"))
        self.assertEqual(result, {"choices": [{"message": {"content": '{"kind":"hold"}'}}]})
        self.assertEqual(len(opener.calls), 1)
        outbound, timeout = opener.calls[0]
        self.assertEqual(outbound.full_url, UPSTREAM_URL)
        self.assertEqual(outbound.get_method(), "POST")
        self.assertLessEqual(timeout, MAX_TIMEOUT_S)
        headers = dict(outbound.header_items())
        self.assertEqual(sum(name.lower() == "authorization" for name in headers), 1)
        self.assertEqual(headers["Authorization"], f"Bearer {PRIVATE_SENTINEL}")
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(result))
        self.assertNotIn(PRIVATE_SENTINEL, outbound.data.decode("utf-8"))
        outbound_body = json.loads(outbound.data.decode("utf-8"))
        self.assertEqual(outbound_body["model"], FIXED_MODEL)
        self.assertFalse(outbound_body["store"])

    def test_both_output_budgets_and_one_attempt_only(self) -> None:
        for max_tokens in (DECISION_MAX_TOKENS, RECEIPT_MAX_TOKENS):
            broker, opener = _broker()
            broker.complete(json.dumps(_payload(max_tokens)).encode("utf-8"))
            self.assertEqual(len(opener.calls), 1)
        broker, opener = _broker(failure=OSError("synthetic transport failure"))
        with self.assertRaisesRegex(BrokerError, "upstream_unavailable"):
            broker.complete(json.dumps(_payload()).encode("utf-8"))
        self.assertEqual(len(opener.calls), 1)

    def test_invalid_payloads_and_bounds_fail_before_secret_or_transport(self) -> None:
        mutated_strict = json.loads(
            json.dumps(agentic_turn_provider_response_format())
        )
        mutated_strict["json_schema"]["strict"] = False
        mutated_name = json.loads(
            json.dumps(agentic_turn_provider_response_format())
        )
        mutated_name["json_schema"]["name"] = "arbitrary_schema"
        mutated_schema = json.loads(
            json.dumps(agentic_turn_provider_response_format())
        )
        mutated_schema["json_schema"]["schema"]["additionalProperties"] = True
        cases = (
            {"model": "gpt-5.6-terra"},
            {"temperature": 1},
            {"response_format": {"type": "json_schema"}},
            {"response_format": mutated_strict},
            {"response_format": mutated_name},
            {"response_format": mutated_schema},
            {
                "response_format": agentic_turn_provider_response_format(),
                "max_tokens": RECEIPT_MAX_TOKENS,
            },
            {"max_tokens": 721},
            {"messages": [{"role": "system", "content": "x"}]},
            {"extra": True},
            {"messages": [{"role": "system", "content": "x" * (MAX_SYSTEM_BYTES + 1)}, {"role": "user", "content": "y"}]},
            {"messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "y" * (MAX_USER_BYTES + 1)}]},
        )
        for mutation in cases:
            payload = _payload()
            payload.update(mutation)
            secret_reads: list[Path] = []
            opener = _Opener()
            broker = OpenAIBroker(
                BrokerConfig(secret_file=Path("synthetic.env")),
                secret_loader=lambda path: secret_reads.append(path) or PRIVATE_SENTINEL,
                opener_factory=lambda: opener,
            )
            with self.assertRaisesRegex(BrokerError, "invalid_request"):
                broker.complete(json.dumps(payload).encode("utf-8"))
            self.assertEqual(secret_reads, [])
            self.assertEqual(opener.calls, [])
        broker, opener = _broker()
        with self.assertRaisesRegex(BrokerError, "invalid_request"):
            broker.complete(b"x" * (MAX_BODY_BYTES + 1))
        self.assertEqual(opener.calls, [])

    def test_request_budget_concurrency_and_upstream_shape_are_fail_closed(self) -> None:
        raw = json.dumps(_payload()).encode("utf-8")
        broker, opener = _broker(request_budget=1)
        broker.complete(raw)
        with self.assertRaisesRegex(BrokerError, "request_budget_exhausted"):
            broker.complete(raw)
        self.assertEqual(len(opener.calls), 1)
        broker, opener = _broker()
        self.assertTrue(broker._lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(BrokerError, "capacity_exhausted"):
                broker.complete(raw)
        finally:
            broker._lock.release()
        self.assertEqual(opener.calls, [])
        for body in (b"{}", b'{"choices":[]}', b'x' * (MAX_UPSTREAM_BYTES + 1)):
            broker, _ = _broker(body=body)
            with self.assertRaisesRegex(BrokerError, "upstream_invalid"):
                broker.complete(raw)


class BrokerSecretAndSurfaceTests(unittest.TestCase):
    def test_temp_synthetic_secret_fixture_rejects_missing_empty_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret_file = Path(directory) / "synthetic.env"
            secret_file.write_text(f"OPENAI_API_KEY={PRIVATE_SENTINEL}\n", encoding="utf-8")
            self.assertEqual(read_openai_api_key(secret_file), PRIVATE_SENTINEL)
            for contents in ("", "OPENAI_API_KEY=\n", "OPENAI_API_KEY=a\nOPENAI_API_KEY=b\n"):
                secret_file.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(BrokerError, "secret_unavailable"):
                    read_openai_api_key(secret_file)

    def test_tls_proxy_redirect_and_thin_loopback_surface(self) -> None:
        with (
            patch("sword_voice_agent.adapters.openai_broker.ssl.create_default_context", wraps=ssl.create_default_context) as tls,
            patch("sword_voice_agent.adapters.openai_broker.request.build_opener", wraps=request.build_opener) as opener_factory,
        ):
            opener = build_safe_opener()
        tls.assert_called_once_with()
        proxies = [handler for handler in opener_factory.call_args.args if isinstance(handler, request.ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        self.assertTrue(any(isinstance(handler, _RejectRedirects) for handler in opener_factory.call_args.args))
        self.assertIsNotNone(opener)
        self.assertIsNone(_RejectRedirects().redirect_request(None, None, None, None, None, None, None))
        broker, _ = _broker()
        with patch("sword_voice_agent.apps.openai_broker.SingleAdmissionHTTPServer") as server_type:
            create_server(broker, BrokerConfig())
        self.assertEqual(server_type.call_args.args[0], (LOOPBACK_HOST, STANDARD_PORT))
        self.assertEqual(COMPLETIONS_PATH, "/v1/chat/completions")
        self.assertEqual(HEALTH_PATH, "/health")

    def test_python_module_entrypoint_executes_main(self) -> None:
        repository = Path(__file__).parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sword_voice_agent.apps.openai_broker",
                "--port",
                "1",
            ],
            cwd=repository,
            env=environment,
            capture_output=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_http_body_admission_is_single_deadlined_and_strictly_framed(self) -> None:
        self.assertLessEqual(BODY_READ_TIMEOUT_S, MAX_TIMEOUT_S)
        self.assertEqual(SingleAdmissionHTTPServer.request_queue_size, 1)
        for transfer_encoding, lengths in (
            ("chunked", ["1"]),
            (None, None),
            (None, ["1", "1"]),
            (None, ["1, 1"]),
            (None, [str(MAX_BODY_BYTES + 1)]),
        ):
            with self.assertRaisesRegex(BrokerError, "invalid_request"):
                _validated_content_length(lengths, transfer_encoding)
        self.assertEqual(_validated_content_length(["3"], None), 3)
        for result in (b"xy", TimeoutError("slow synthetic body"), OSError("synthetic disconnect")):
            reader = _BodyReader(result)
            with self.assertRaisesRegex(BrokerError, "body_unavailable"):
                _read_exact_body(reader, 3)
            self.assertGreaterEqual(len(reader.read_sizes), 1)
        with self.assertRaisesRegex(BrokerError, "body_unavailable"):
            _read_exact_body(object(), 1)
        failure = BrokerError("body_unavailable")
        self.assertEqual(
            _body_error_response(failure),
            (408, {"error": {"code": "body_unavailable"}}),
        )
        app_source = (Path(__file__).parents[1] / "src" / "sword_voice_agent" / "apps" / "openai_broker.py").read_text(encoding="utf-8")
        self.assertNotIn("ThreadingHTTPServer", app_source)
        self.assertIn("self.connection.settimeout(BODY_READ_TIMEOUT_S)", app_source)
        self.assertIn("self.close_connection = True", app_source)
        self.assertIn("reader.read1", app_source)

    def test_incremental_trickle_cannot_extend_total_deadline(self) -> None:
        clock = _FakeClock()
        reader = _TrickleReader(
            clock,
            [b"ab", b"cd", b"ef", b"gh"],
            [4.0, 4.0, 4.0, 4.0],
        )
        applied_timeouts: list[float] = []
        with self.assertRaisesRegex(BrokerError, "body_unavailable"):
            _read_exact_body(
                reader,
                8,
                clock=clock,
                set_timeout=applied_timeouts.append,
                chunk_bytes=2,
            )
        self.assertEqual(reader.read_sizes, [2, 2, 2])
        self.assertEqual(applied_timeouts, [12.0, 8.0, 4.0])
        self.assertLessEqual(BODY_READ_CHUNK_BYTES, MAX_BODY_BYTES)

    def test_private_sentinel_never_reaches_response_error_repr_or_source_logging(self) -> None:
        source_path = Path(__file__).parents[1] / "src" / "sword_voice_agent" / "adapters" / "openai_broker.py"
        source = source_path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertNotIn("logging", source)
        self.assertNotIn("requests", source)
        broker, _ = _broker()
        with self.assertRaisesRegex(BrokerError, "invalid_request") as captured:
            broker.complete((PRIVATE_SENTINEL + "{").encode("utf-8"))
        self.assertNotIn(PRIVATE_SENTINEL, repr(captured.exception))


if __name__ == "__main__":
    unittest.main()
