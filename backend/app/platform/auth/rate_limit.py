"""In-memory login throttle. Good enough for a single-process deployment (ADR 002)."""

import time
from collections import deque
from threading import Lock


class LoginRateLimiter:
    """At most `max_attempts` failures per key (IP + email) within `window_seconds`. Keys whose failures all fell out
    of the window are dropped, so memory holds only the keys with a recent failure."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._last_sweep = time.monotonic()

    def _recent(self, key: str, now: float) -> deque[float] | None:
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def _sweep(self, now: float) -> None:
        """Drop every expired key, at most once per window (a key that is never asked about again goes too)."""
        if now - self._last_sweep < self.window_seconds:
            return
        self._last_sweep = now
        for key in list(self._failures):
            self._recent(key, now)

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            attempts = self._recent(key, time.monotonic())
            return attempts is not None and len(attempts) >= self.max_attempts

    def register_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._sweep(now)
            attempts = self._recent(key, now)
            if attempts is None:
                attempts = self._failures[key] = deque()
            attempts.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def __len__(self) -> int:
        """Keys held in memory."""
        with self._lock:
            return len(self._failures)
