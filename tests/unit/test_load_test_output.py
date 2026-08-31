from pathlib import Path

import smart_dialer.load_test as load_test


class FakePostgresContainer:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get_connection_url(self) -> str:
        return "postgresql+psycopg://unused"


def result_row(scale: int) -> dict:
    return {
        "scale": scale,
        "measured_allocations": min(scale, 1000),
        "throughput_per_second": 125.5,
        "p50_ms": 2.1,
        "p95_ms": 4.2,
        "p99_ms": 5.4,
        "skip_or_retry_count": 3,
        "deadlocks": 0,
        "duplicate_agents": 0,
        "duplicate_borrowers": 0,
        "pool_max_checked_out": 16,
        "pool_capacity": 32,
        "pool_saturation_percent": 50.0,
        "agent_drop_percent": 40,
        "agents_released": int(scale * 0.4),
        "heartbeat_release_virtual_seconds": 15.0,
        "heartbeat_release_db_ms": 8.0,
    }


def test_load_test_prints_a_compact_comparison_table(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(load_test, "PostgresContainer", lambda *_args, **_kwargs: FakePostgresContainer())
    monkeypatch.setattr(load_test, "run_scale", lambda _url, scale: result_row(scale))
    output = tmp_path / "load-test.json"

    load_test.run(output)

    terminal = capsys.readouterr().out
    assert "[OK] Load test complete" in terminal
    assert "Scale" in terminal
    assert "Throughput/s" in terminal
    assert "Pool saturation" in terminal
    assert "10,000" in terminal
    assert str(output) in terminal
    assert str(output.with_suffix(".csv")) in terminal
    assert not terminal.lstrip().startswith("{")
    assert b"\r\n" not in output.with_suffix(".csv").read_bytes()
