from datetime import UTC, datetime, timedelta

from smart_dialer.services.circuit_breaker import CircuitBreaker, CircuitState


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_three_consecutive_timeouts_open_the_circuit() -> None:
    breaker = CircuitBreaker()
    for offset in range(3):
        breaker.record_attempt(succeeded=False, timed_out=True, now=NOW + timedelta(seconds=offset))

    assert breaker.state is CircuitState.OPEN
    assert not breaker.allow_initiation(NOW + timedelta(seconds=10))


def test_failure_rate_is_not_evaluated_before_ten_samples() -> None:
    breaker = CircuitBreaker()
    for offset in range(9):
        breaker.record_attempt(succeeded=offset >= 3, timed_out=False, now=NOW)

    assert breaker.state is CircuitState.CLOSED


def test_thirty_percent_failure_rate_opens_after_minimum_sample() -> None:
    breaker = CircuitBreaker()
    outcomes = [False, True, True, False, True, True, False, True, True, True]
    for succeeded in outcomes:
        breaker.record_attempt(succeeded=succeeded, timed_out=False, now=NOW)

    assert breaker.state is CircuitState.OPEN


def test_half_open_uses_health_check_and_does_not_place_borrower_call() -> None:
    breaker = CircuitBreaker()
    for offset in range(3):
        breaker.record_attempt(succeeded=False, timed_out=True, now=NOW + timedelta(seconds=offset))

    probe_time = NOW + timedelta(seconds=35)
    assert breaker.needs_health_probe(probe_time)
    breaker.record_health_probe(healthy=True, now=probe_time)

    assert breaker.state is CircuitState.CLOSED


def test_backoff_has_bounded_deterministic_jitter() -> None:
    breaker = CircuitBreaker(seed=42)

    delays = [breaker.retry_delay(attempt) for attempt in (1, 2, 3)]

    assert 0.8 <= delays[0] <= 1.2
    assert 1.6 <= delays[1] <= 2.4
    assert 3.2 <= delays[2] <= 4.8
