"""
ratelimit — Token-bucket rate limiter for outbound HTTP requests.

Usage:
    limiter = RateLimiter(rate=0.5)  # 0.5 req/s = 1 request every 2 seconds
    limiter.wait()  # blocks until a token is available
    requests.get(url)
"""
from __future__ import annotations
import threading
import time


class RateLimiter:
    """Simple token-bucket rate limiter (thread-safe)."""

    def __init__(self, rate: float = 0.5, burst: int = 1):
        """
        rate:  tokens per second (0.5 = one request every 2s)
        burst: max tokens that can accumulate
        """
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def wait(self):
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            # Sleep briefly and retry
            time.sleep(0.1)

    def try_acquire(self) -> bool:
        """Non-blocking: return True if a token was available."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# Global limiter for mujrozhlas.cz
mrz_limiter = RateLimiter(rate=0.5, burst=2)
