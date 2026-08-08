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
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib import request

from sword_voice_agent.adapters.openai_broker import (
    ALLOWED_PORTS,
    DECISION_MAX_TOKENS,
    DECISION_EVENT_ID_HEADER,
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
    PROVIDER_ATTEMPT_RECEIPT_CLASS,
    PROVIDER_ATTEMPT_RECEIPT_KEY,
    PROVIDER_ATTEMPT_TERMINAL_CLASS,
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
    _validated_decision_event_id,
)

PRIVATE_SENTINEL = "SYNTHETIC_PRIVATE_BROKER_SENTINEL"
DECISION_EVENT_ID = "evt_" + ("a" * 32)


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
        result = broker.complete(
            json.dumps(_payload()).encode("utf-8"),
            decision_event_id=DECISION_EVENT_ID,
        )
        self.assertEqual(
            result,
            {
                "choices": [{"message": {"content": '{"kind":"hold"}'}}],
                PROVIDER_ATTEMPT_RECEIPT_KEY: {
                    "receipt_class": PROVIDER_ATTEMPT_RECEIPT_CLASS,
                    "decision_event_id": DECISION_EVENT_ID,
                    "upstream_attempt_count": 1,
                    "retry_count": 0,
                    "fallback_count": 0,
                    "attempt_terminal_class": PROVIDER_ATTEMPT_TERMINAL_CLASS,
                },
            },
        )
        self.assertEqual(len(opener.calls), 1)
        outbound, timeout = opener.calls[0]
        self.assertEqual(outbound.full_url, UPSTREAM_URL)
        self.assertEqual(outbound.get_method(), "POST")
        self.assertLessEqual(timeout, MAX_TIMEOUT_S)
        headers = dict(outbound.header_items())
        self.assertEqual(sum(name.lower() == "authorization" for name in headers), 1)
        self.assertEqual(headers["Authorization"], f"Bearer {PRIVATE_SENTINEL}")
        self.assertNotIn(DECISION_EVENT_ID_HEADER.lower(), {name.lower() for name in headers})
        self.assertNotIn(DECISION_EVENT_ID, outbound.data.decode("utf-8"))
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(result))
        self.assertNotIn(PRIVATE_SENTINEL, outbound.data.decode("utf-8"))
        outbound_body = json.loads(outbound.data.decode("utf-8"))
        self.assertEqual(outbound_body["model"], FIXED_MODEL)
        self.assertFalse(outbound_body["store"])

    def test_both_output_budgets_and_one_attempt_only(self) -> None:
        for max_tokens in (DECISION_MAX_TOKENS, RECEIPT_MAX_TOKENS):
            broker, opener = _broker()
            result = broker.complete(
                json.dumps(_payload(max_tokens)).encode("utf-8"),
                decision_event_id=(
                    DECISION_EVENT_ID
                    if max_tokens == DECISION_MAX_TOKENS
                    else None
                ),
            )
            self.assertEqual(len(opener.calls), 1)
            self.assertEqual(
                PROVIDER_ATTEMPT_RECEIPT_KEY in result,
                max_tokens == DECISION_MAX_TOKENS,
            )
        broker, opener = _broker(failure=OSError("synthetic transport failure"))
        with self.assertRaisesRegex(BrokerError, "upstream_unavailable"):
            broker.complete(
                json.dumps(_payload()).encode("utf-8"),
                decision_event_id=DECISION_EVENT_ID,
            )
        self.assertEqual(len(opener.calls), 1)

    def test_decision_correlation_is_per_call_and_not_process_budget_state(self) -> None:
        secret_reads: list[Path] = []
        opener = _Opener()
        broker = OpenAIBroker(
            BrokerConfig(secret_file=Path("synthetic.env"), request_budget=2),
            secret_loader=lambda path: secret_reads.append(path) or PRIVATE_SENTINEL,
            opener_factory=lambda: opener,
        )
        event_ids = (DECISION_EVENT_ID, "evt_" + ("b" * 32))
        receipts = []
        raw = json.dumps(_payload(), separators=(",", ":")).encode("utf-8")
        with patch(
            "sword_voice_agent.apps.openai_broker.SingleAdmissionHTTPServer"
        ) as server_type:
            create_server(broker, BrokerConfig(request_budget=2))
        handler_type = server_type.call_args.args[1]

        def invoke(event_id: str) -> tuple[int, dict[str, object]]:
            headers = Message()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(raw))
            headers[DECISION_EVENT_ID_HEADER] = event_id
            handler = object.__new__(handler_type)
            handler.path = COMPLETIONS_PATH
            handler.headers = headers
            handler.rfile = _BodyReader(raw)
            handler.connection = Mock()
            responses: list[tuple[int, dict[str, object]]] = []
            handler._send = lambda status, payload: responses.append(  # type: ignore[method-assign]
                (status, dict(payload))
            )
            handler.do_POST()
            self.assertEqual(len(responses), 1)
            return responses[0]

        for event_id in event_ids:
            status, result = invoke(event_id)
            self.assertEqual(status, 200)
            receipts.append(result[PROVIDER_ATTEMPT_RECEIPT_KEY])

        exhausted_status, exhausted = invoke("evt_" + ("c" * 32))
        self.assertEqual(exhausted_status, 503)
        self.assertEqual(
            exhausted, {"error": {"code": "request_budget_exhausted"}}
        )
        self.assertNotIn(PROVIDER_ATTEMPT_RECEIPT_KEY, exhausted)

        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(len(secret_reads), 2)
        self.assertEqual(broker._request_count, 2)
        for event_id, receipt in zip(event_ids, receipts, strict=True):
            self.assertEqual(
                receipt,
                {
                    "receipt_class": PROVIDER_ATTEMPT_RECEIPT_CLASS,
                    "decision_event_id": event_id,
                    "upstream_attempt_count": 1,
                    "retry_count": 0,
                    "fallback_count": 0,
                    "attempt_terminal_class": PROVIDER_ATTEMPT_TERMINAL_CLASS,
                },
            )

    def test_missing_malformed_and_wrong_call_correlation_fail_before_boundary(self) -> None:
        for max_tokens, decision_event_id in (
            (DECISION_MAX_TOKENS, None),
            (DECISION_MAX_TOKENS, "evt_" + ("A" * 32)),
            (DECISION_MAX_TOKENS, DECISION_EVENT_ID + "," + DECISION_EVENT_ID),
            (RECEIPT_MAX_TOKENS, DECISION_EVENT_ID),
        ):
            with self.subTest(max_tokens=max_tokens, decision_event_id=decision_event_id):
                secret_reads: list[Path] = []
                opener = _Opener()
                broker = OpenAIBroker(
                    BrokerConfig(secret_file=Path("synthetic.env")),
                    secret_loader=lambda path: secret_reads.append(path) or PRIVATE_SENTINEL,
                    opener_factory=lambda: opener,
                )
                with self.assertRaisesRegex(BrokerError, "invalid_request"):
                    broker.complete(
                        json.dumps(_payload(max_tokens)).encode("utf-8"),
                        decision_event_id=decision_event_id,
                    )
                self.assertEqual(secret_reads, [])
                self.assertEqual(opener.calls, [])
                self.assertEqual(broker._request_count, 0)

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
                broker.complete(
                    json.dumps(payload).encode("utf-8"),
                    decision_event_id=DECISION_EVENT_ID,
                )
            self.assertEqual(secret_reads, [])
            self.assertEqual(opener.calls, [])
        broker, opener = _broker()
        with self.assertRaisesRegex(BrokerError, "invalid_request"):
            broker.complete(
                b"x" * (MAX_BODY_BYTES + 1),
                decision_event_id=DECISION_EVENT_ID,
            )
        self.assertEqual(opener.calls, [])

    def test_request_budget_concurrency_and_upstream_shape_are_fail_closed(self) -> None:
        raw = json.dumps(_payload()).encode("utf-8")
        broker, opener = _broker(request_budget=1)
        broker.complete(raw, decision_event_id=DECISION_EVENT_ID)
        with self.assertRaisesRegex(BrokerError, "request_budget_exhausted"):
            broker.complete(raw, decision_event_id=DECISION_EVENT_ID)
        self.assertEqual(len(opener.calls), 1)
        broker, opener = _broker()
        self.assertTrue(broker._lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(BrokerError, "capacity_exhausted"):
                broker.complete(raw, decision_event_id=DECISION_EVENT_ID)
        finally:
            broker._lock.release()
        self.assertEqual(opener.calls, [])
        for body in (b"{}", b'{"choices":[]}', b'x' * (MAX_UPSTREAM_BYTES + 1)):
            broker, _ = _broker(body=body)
            with self.assertRaisesRegex(BrokerError, "upstream_invalid"):
                broker.complete(raw, decision_event_id=DECISION_EVENT_ID)


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

    def test_internal_decision_header_is_exact_and_ingress_rejects_before_body_or_broker(self) -> None:
        self.assertIsNone(_validated_decision_event_id(None))
        self.assertEqual(
            _validated_decision_event_id([DECISION_EVENT_ID]),
            DECISION_EVENT_ID,
        )
        invalid_values: tuple[list[object], ...] = (
            [],
            [""],
            [" " + DECISION_EVENT_ID],
            [DECISION_EVENT_ID + " "],
            ["evt_" + ("A" * 32)],
            [DECISION_EVENT_ID + "," + DECISION_EVENT_ID],
            ["C:/private/path"],
            [DECISION_EVENT_ID + "\r\nInjected: value"],
            [DECISION_EVENT_ID, DECISION_EVENT_ID],
            [True],
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaisesRegex(
                BrokerError,
                "invalid_request",
            ):
                _validated_decision_event_id(values)  # type: ignore[arg-type]

        broker = Mock(spec=OpenAIBroker)
        with patch(
            "sword_voice_agent.apps.openai_broker.SingleAdmissionHTTPServer"
        ) as server_type:
            create_server(broker, BrokerConfig())
        handler_type = server_type.call_args.args[1]
        for values in (
            [DECISION_EVENT_ID, DECISION_EVENT_ID],
            [DECISION_EVENT_ID + "," + DECISION_EVENT_ID],
            ["evt_" + ("A" * 32)],
            ["C:/private/path"],
        ):
            with self.subTest(ingress_values=values):
                headers = Message()
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = "1"
                for value in values:
                    headers[DECISION_EVENT_ID_HEADER] = value
                handler = object.__new__(handler_type)
                handler.path = COMPLETIONS_PATH
                handler.headers = headers
                handler.rfile = Mock()
                handler.rfile.read1.side_effect = AssertionError(
                    "body_must_not_be_read"
                )
                handler.connection = Mock()
                responses: list[tuple[int, object]] = []
                handler._send = lambda status, payload: responses.append(  # type: ignore[method-assign]
                    (status, payload)
                )
                broker.reset_mock()

                handler.do_POST()

                self.assertEqual(
                    responses,
                    [(400, {"error": {"code": "invalid_request"}})],
                )
                handler.rfile.read1.assert_not_called()
                broker.complete.assert_not_called()

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
            broker.complete(
                (PRIVATE_SENTINEL + "{").encode("utf-8"),
                decision_event_id=DECISION_EVENT_ID,
            )
        self.assertNotIn(PRIVATE_SENTINEL, repr(captured.exception))


if __name__ == "__main__":
    unittest.main()
