"""Configurable token-bucket rate limiter for broker requests.

The bucket is clock-injectable so tests are deterministic. Capacity and refill
rates are supplied by YAML config; no limits are hard-coded here.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable


class TokenBucket:
    """A token bucket that refills continuously at a configured rate."""

    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a full bucket using configured limits only."""
        if capacity < 1:
            raise ValueError("Token bucket capacity must be at least 1")
        if refill_per_second <= 0:
            raise ValueError("Token bucket refill rate must be positive")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock()

    def allow(self, cost: int = 1) -> bool:
        """Consume tokens if enough are available; otherwise refuse the call."""
        if cost < 1:
            raise ValueError("Token cost must be at least 1")
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.refill_per_second)
        self._last = now
        if self._tokens < cost:
            return False
        self._tokens -= cost
        return True

    def retry_after_seconds(self, cost: int = 1) -> float:
        """Return how long until ``cost`` tokens will be available."""
        if self._tokens >= cost:
            return 0.0
        missing = cost - self._tokens
        return math.ceil(missing / self.refill_per_second * 1_000_000) / 1_000_000
