"""Circuit breaker protecting the dialer against telecom provider outages."""

from __future__ import annotations
from collections import deque
from enum import Enum
import time
from typing import Deque


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Normal operations
    OPEN = "OPEN"            # Fast-failing calls to protect system
    HALF_OPEN = "HALF_OPEN"  # Testing canary calls to check recovery


class CircuitBreakerOpenError(Exception):
    """Raised when request rejected due to open circuit breaker."""
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_rate_threshold: float = 0.35,  # Trip when > 35% fail
        consecutive_timeout_threshold: int = 4, # Trip on 4 consecutive timeouts
        recovery_time_sec: float = 4.0,        # Time to wait in OPEN before trying HALF_OPEN
        window_size: int = 20,                 # Rolling window for failure rate
        half_open_success_threshold: int = 3,  # Consecutive canary successes to CLOSE
    ):
        self.name = name
        self.failure_rate_threshold = failure_rate_threshold
        self.consecutive_timeout_threshold = consecutive_timeout_threshold
        self.recovery_time_sec = recovery_time_sec
        self.window_size = window_size
        self.half_open_success_threshold = half_open_success_threshold

        self.state: CircuitState = CircuitState.CLOSED
        self.history: Deque[bool] = deque(maxlen=window_size)  # True = success, False = failure
        self.consecutive_timeouts: int = 0
        self.state_changed_at: float = time.time()
        self.half_open_successes: int = 0

    @property
    def is_available(self) -> bool:
        """Checks if calls are allowed through."""
        now = time.time()
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            if now - self.state_changed_at >= self.recovery_time_sec:
                # Transition to HALF_OPEN to attempt recovery
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        self.history.append(True)
        self.consecutive_timeouts = 0
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_successes += 1
            if self.half_open_successes >= self.half_open_success_threshold:
                self._transition_to(CircuitState.CLOSED)

    def record_failure(self) -> None:
        self.history.append(False)
        self._check_trip_condition()

    def record_timeout(self) -> None:
        self.history.append(False)
        self.consecutive_timeouts += 1
        self._check_trip_condition()

    def _check_trip_condition(self) -> None:
        now = time.time()
        if self.state == CircuitState.HALF_OPEN:
            # Canary failed; immediate trip back to OPEN
            self._transition_to(CircuitState.OPEN)
            return

        if self.consecutive_timeouts >= self.consecutive_timeout_threshold:
            self._transition_to(CircuitState.OPEN)
            return

        if len(self.history) >= min(5, self.window_size):
            failures = self.history.count(False)
            rate = failures / len(self.history)
            if rate >= self.failure_rate_threshold:
                self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        self.state = new_state
        self.state_changed_at = time.time()
        if new_state == CircuitState.HALF_OPEN:
            self.half_open_successes = 0
        elif new_state == CircuitState.CLOSED:
            self.history.clear()
            self.consecutive_timeouts = 0

    def get_error_rate(self) -> float:
        if not self.history:
            return 0.0
        return self.history.count(False) / len(self.history)
