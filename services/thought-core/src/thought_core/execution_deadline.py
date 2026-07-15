"""Process-local deadline token for one Thought Core turn execution."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import math
import threading
import time
from typing import Iterator


TURN_DEADLINE_EXCEEDED = "turn_deadline_exceeded"
TURN_DEADLINE_INVALID = "turn_deadline_invalid"
_ISSUER = object()
_CURRENT: ContextVar[TurnExecutionDeadline | None] = ContextVar(
    "thought_core_turn_execution_deadline",
    default=None,
)


class TurnDeadlineExceeded(RuntimeError):
    def __init__(self) -> None:
        super().__init__(TURN_DEADLINE_EXCEEDED)


class TurnDeadlineInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__(TURN_DEADLINE_INVALID)


class TurnExecutionDeadline:
    """One-use, non-serializable deadline/cancellation capability."""

    __slots__ = (
        "_deadline_monotonic",
        "_turn_key",
        "_issuer",
        "_lock",
        "_claimed",
        "_cancelled",
    )

    def __init__(
        self,
        deadline_monotonic: float,
        *,
        _turn_key: tuple[str, str] | None = None,
        _issuer: object | None = None,
    ) -> None:
        self._deadline_monotonic = deadline_monotonic
        self._turn_key = _turn_key
        self._issuer = _issuer
        self._lock = threading.Lock()
        self._claimed = False
        self._cancelled = False

    def _is_authentic(self) -> bool:
        return (
            self._issuer is _ISSUER
            and not isinstance(self._deadline_monotonic, bool)
            and isinstance(self._deadline_monotonic, (int, float))
            and math.isfinite(float(self._deadline_monotonic))
        )

    def claim(self, turn_key: tuple[str, str] | None = None) -> None:
        if not self._is_authentic():
            raise TurnDeadlineInvalid()
        if self._turn_key is not None and turn_key != self._turn_key:
            raise TurnDeadlineInvalid()
        with self._lock:
            if self._claimed:
                raise TurnDeadlineInvalid()
            self._claimed = True
        self.ensure_current()

    def ensure_current(self) -> None:
        if not self._is_authentic():
            raise TurnDeadlineInvalid()
        with self._lock:
            cancelled = self._cancelled
        if cancelled or float(self._deadline_monotonic) - time.monotonic() <= 0:
            raise TurnDeadlineExceeded()

    def remaining_seconds(self) -> float:
        self.ensure_current()
        remaining = float(self._deadline_monotonic) - time.monotonic()
        if remaining <= 0:
            raise TurnDeadlineExceeded()
        return remaining

    def cancel(self) -> None:
        if not self._is_authentic():
            raise TurnDeadlineInvalid()
        with self._lock:
            self._cancelled = True


def issue_turn_execution_deadline(
    deadline_monotonic: float,
    *,
    turn_key: tuple[str, str] | None = None,
) -> TurnExecutionDeadline:
    token = TurnExecutionDeadline(
        deadline_monotonic,
        _turn_key=turn_key,
        _issuer=_ISSUER,
    )
    token.ensure_current()
    return token


@contextmanager
def execution_deadline_scope(
    execution_deadline: TurnExecutionDeadline | None,
    *,
    turn_key: tuple[str, str] | None = None,
) -> Iterator[None]:
    if execution_deadline is not None and not isinstance(
        execution_deadline,
        TurnExecutionDeadline,
    ):
        raise TurnDeadlineInvalid()
    if execution_deadline is not None:
        execution_deadline.claim(turn_key)
    reset_token = _CURRENT.set(execution_deadline)
    try:
        ensure_execution_active()
        yield
        ensure_execution_active()
    finally:
        _CURRENT.reset(reset_token)


def current_execution_deadline() -> TurnExecutionDeadline | None:
    return _CURRENT.get()


def ensure_execution_active() -> None:
    execution_deadline = current_execution_deadline()
    if execution_deadline is not None:
        execution_deadline.ensure_current()


def remaining_execution_seconds() -> float | None:
    execution_deadline = current_execution_deadline()
    if execution_deadline is None:
        return None
    return execution_deadline.remaining_seconds()


def clamp_execution_timeout(configured_seconds: float) -> float:
    configured = float(configured_seconds)
    if not math.isfinite(configured) or configured <= 0:
        raise ValueError("configured timeout must be positive and finite")
    remaining = remaining_execution_seconds()
    return configured if remaining is None else min(configured, remaining)
