from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_s = max(0.0, retry_after_s)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_s: float = 0.0


class FixedWindowRateLimiter:
    def __init__(
        self,
        max_events: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_events = int(max_events)
        self.window_seconds = max(0.001, float(window_seconds))
        self.clock = clock
        self._windows: dict[str, tuple[float, int]] = {}

    @classmethod
    def disabled(cls) -> "FixedWindowRateLimiter":
        return cls(0)

    @property
    def enabled(self) -> bool:
        return self.max_events > 0

    def allow(self, key: str) -> RateLimitDecision:
        if not self.enabled:
            return RateLimitDecision(True)

        now = self.clock()
        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start = now
            count = 0
            self._purge_expired(now)

        if count >= self.max_events:
            retry_after = self.window_seconds - (now - window_start)
            return RateLimitDecision(False, retry_after)

        self._windows[key] = (window_start, count + 1)
        return RateLimitDecision(True)

    def check(self, key: str) -> None:
        decision = self.allow(key)
        if not decision.allowed:
            raise RateLimitExceeded(decision.retry_after_s)

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, (window_start, _) in self._windows.items()
            if now - window_start >= self.window_seconds
        ]
        for key in expired:
            self._windows.pop(key, None)
