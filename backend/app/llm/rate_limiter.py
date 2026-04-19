import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RateLimiter:
    """Thread-safe rate limiter. Blocks calls so at least min_interval elapses between them."""

    def __init__(self, min_interval: float = 4.0):
        self.min_interval = min_interval
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn with rate limiting. Blocks until interval elapsed, then calls."""
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            return fn()


from app.config import settings

claude_limiter = RateLimiter(min_interval=settings.claude_rate_limit_interval)
