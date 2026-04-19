import threading
import time

from app.llm.rate_limiter import RateLimiter


def test_first_call_no_wait():
    limiter = RateLimiter(min_interval=2.0)
    start = time.monotonic()
    result = limiter.call(lambda: "ok")
    elapsed = time.monotonic() - start
    assert result == "ok"
    assert elapsed < 1.0


def test_consecutive_calls_respect_interval():
    interval = 0.2
    limiter = RateLimiter(min_interval=interval)
    limiter.call(lambda: None)
    start = time.monotonic()
    limiter.call(lambda: None)
    elapsed = time.monotonic() - start
    assert elapsed >= interval * 0.9


def test_thread_safety():
    interval = 0.1
    limiter = RateLimiter(min_interval=interval)
    call_times: list[float] = []
    lock = threading.Lock()

    def worker():
        limiter.call(lambda: None)
        with lock:
            call_times.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    call_times.sort()
    for i in range(1, len(call_times)):
        gap = call_times[i] - call_times[i - 1]
        assert gap >= interval * 0.9, f"gap between call {i-1} and {i} was {gap:.3f}s, expected >= {interval * 0.9:.3f}s"


def test_call_returns_fn_result():
    limiter = RateLimiter(min_interval=0.0)
    assert limiter.call(lambda: 42) == 42
    assert limiter.call(lambda: "hello") == "hello"
