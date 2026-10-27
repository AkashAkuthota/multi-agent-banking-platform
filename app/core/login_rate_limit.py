"""Small, concurrency-safe login attempt limiter for a single API process."""

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginAttemptLimiter:
    """Reserve bounded login attempts keyed by a normalized customer email."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1 or window_seconds < 1:
            raise ValueError("Login rate-limit configuration must be positive")
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._clock = clock
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def reserve(self, email: str) -> int | None:
        """Reserve an attempt or return the number of seconds until retry."""
        now = self._clock()
        async with self._lock:
            attempts = self._attempts[email]
            cutoff = now - self._window_seconds
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self._max_attempts:
                return max(1, int(attempts[0] + self._window_seconds - now) + 1)
            attempts.append(now)
            return None

    async def clear(self, email: str) -> None:
        """Clear failed-attempt state after a successful authentication."""
        async with self._lock:
            self._attempts.pop(email, None)

    async def clear_all(self) -> None:
        """Clear state for deterministic tests and controlled local resets."""
        async with self._lock:
            self._attempts.clear()
