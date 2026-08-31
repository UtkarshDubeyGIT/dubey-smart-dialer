import random
from collections import deque
from datetime import datetime, timedelta
from enum import StrEnum


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, *, seed: int = 1) -> None:
        self.state = CircuitState.CLOSED
        self._outcomes: deque[bool] = deque(maxlen=20)
        self._consecutive_timeouts = 0
        self._opened_at: datetime | None = None
        self._random = random.Random(seed)

    def record_attempt(self, *, succeeded: bool, timed_out: bool, now: datetime) -> None:
        self._outcomes.append(succeeded)
        self._consecutive_timeouts = self._consecutive_timeouts + 1 if timed_out else 0
        enough_samples = len(self._outcomes) >= 10
        failure_rate = 1.0 - (sum(self._outcomes) / len(self._outcomes))
        if self._consecutive_timeouts >= 3 or (enough_samples and failure_rate >= 0.30):
            self.state = CircuitState.OPEN
            self._opened_at = now

    def allow_initiation(self, now: datetime) -> bool:
        if self.state is CircuitState.CLOSED:
            return True
        if self.needs_health_probe(now):
            self.state = CircuitState.HALF_OPEN
        return False

    def needs_health_probe(self, now: datetime) -> bool:
        return (
            self.state is CircuitState.OPEN
            and self._opened_at is not None
            and now >= self._opened_at + timedelta(seconds=30)
        )

    def record_health_probe(self, *, healthy: bool, now: datetime) -> None:
        if healthy:
            self.state = CircuitState.CLOSED
            self._outcomes.clear()
            self._consecutive_timeouts = 0
            self._opened_at = None
        else:
            self.state = CircuitState.OPEN
            self._opened_at = now

    def retry_delay(self, attempt: int) -> float:
        base = 2 ** max(0, attempt - 1)
        return base * self._random.uniform(0.8, 1.2)
