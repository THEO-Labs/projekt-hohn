import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe rate limiter with 429 backoff. Blocks calls so at least
    min_interval elapses between them, and on rate-limit errors sleeps long
    enough that Anthropic's per-minute token budget resets before retrying."""

    def __init__(self, min_interval: float = 12.0, max_retries: int = 3, backoff_seconds: float = 65.0):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def call(self, fn: Callable[[], T]) -> T:
        """Execute fn with rate limiting. Blocks until interval elapsed. Retries on
        429 rate_limit_error with a full-minute backoff so the token budget resets."""
        attempt = 0
        while True:
            with self._lock:
                now = time.monotonic()
                wait = self.min_interval - (now - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
                try:
                    return fn()
                except Exception as e:
                    if attempt >= self.max_retries:
                        raise
                    is_rate_limit = (
                        getattr(e, "status_code", None) == 429
                        or "rate_limit" in str(e).lower()
                        or "429" in str(e)
                    )
                    if not is_rate_limit:
                        raise
                    logger.warning(
                        "Claude 429 rate limit (attempt %s/%s). Sleeping %ss before retry.",
                        attempt + 1, self.max_retries, self.backoff_seconds,
                    )
                    time.sleep(self.backoff_seconds)
                    self._last_call = time.monotonic()
                    attempt += 1


from app.config import settings

claude_limiter = RateLimiter(min_interval=settings.claude_rate_limit_interval)
