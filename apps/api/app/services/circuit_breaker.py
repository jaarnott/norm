"""Simple circuit breaker for external API calls.

Tracks failures and opens the circuit after a threshold, returning
a cached error response instead of hammering a failing service.

States:
  CLOSED  — normal operation, requests pass through
  OPEN    — requests fail immediately with a cached error
  HALF_OPEN — one probe request allowed; if it succeeds, circuit closes

Usage:
    from app.services.circuit_breaker import anthropic_breaker

    if not anthropic_breaker.allow_request():
        raise APIError("Anthropic API is temporarily unavailable")

    try:
        result = call_anthropic(...)
        anthropic_breaker.record_success()
    except Exception:
        anthropic_breaker.record_failure()
        raise
"""

import logging
import time
import threading

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Thread-safe circuit breaker."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self._lock = threading.Lock()
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._state = "closed"  # closed | open | half_open
        self._half_open_count = 0

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    self._state = "half_open"
                    self._half_open_count = 0
                    logger.info("Circuit %s: open → half_open", self.name)
            return self._state

    def allow_request(self) -> bool:
        """Return True if the request should proceed."""
        state = self.state
        if state == "closed":
            return True
        if state == "half_open":
            with self._lock:
                if self._half_open_count < self.half_open_max:
                    self._half_open_count += 1
                    return True
            return False
        return False  # open

    def record_success(self) -> None:
        with self._lock:
            if self._state == "half_open":
                logger.info(
                    "Circuit %s: half_open → closed (probe succeeded)", self.name
                )
            self._failure_count = 0
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == "half_open":
                self._state = "open"
                logger.warning("Circuit %s: half_open → open (probe failed)", self.name)
            elif self._failure_count >= self.failure_threshold:
                if self._state != "open":
                    logger.warning(
                        "Circuit %s: closed → open (%d failures)",
                        self.name,
                        self._failure_count,
                    )
                self._state = "open"


# Pre-configured breaker for Anthropic API
anthropic_breaker = CircuitBreaker(
    name="anthropic",
    failure_threshold=5,
    recovery_timeout=60.0,
)
