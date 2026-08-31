import logging

import pytest

import smart_dialer.worker_loop as worker_loop


def test_worker_logs_iteration_failure_and_keeps_running(
    monkeypatch, caplog
) -> None:
    attempts = iter([RuntimeError("database temporarily unavailable"), KeyboardInterrupt()])

    def run_once(_factory):
        outcome = next(attempts)
        raise outcome

    monkeypatch.setattr(worker_loop, "run_once", run_once)
    monkeypatch.setattr(worker_loop, "build_session_factory", lambda: object())
    monkeypatch.setattr(worker_loop.time, "sleep", lambda _seconds: None)

    with caplog.at_level(logging.INFO, logger="smart_dialer.worker"):
        with pytest.raises(KeyboardInterrupt):
            worker_loop.run_forever(poll_seconds=0.25)

    assert "Worker started; polling every 0.25s." in caplog.text
    assert "Worker iteration failed; retrying in 0.25s." in caplog.text
    assert "database temporarily unavailable" in caplog.text
