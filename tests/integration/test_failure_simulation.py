import pytest

from smart_dialer.db.base import Base
from smart_dialer.simulation import write_report


pytestmark = pytest.mark.integration


def test_failure_report_is_produced_by_executed_postgresql_paths(
    session_factory, tmp_path
) -> None:
    try:
        report = write_report(
            tmp_path / "failure-report.json",
            seed=2026,
            session_factory=session_factory,
        )
    except TypeError:
        pytest.fail("simulation does not accept a PostgreSQL execution context")

    failures = report["failure_scenarios"]
    assert all(
        scenario["evidence_source"] == "executed_postgresql_production_path"
        for scenario in failures.values()
    )

    assert failures["worker_crash"]["recovery_outcome"] == "reconciled"
    assert failures["worker_crash"]["provider_calls_created"] == 1
    assert failures["worker_crash"]["duplicate_call"] is False

    assert failures["duplicate_events"]["duplicate_events_ignored"] == 1
    assert failures["duplicate_events"]["applied_transitions"] == 1
    assert failures["out_of_order_events"]["final_state"] == "completed"
    assert failures["out_of_order_events"]["stale_events"] == 2

    assert failures["provider_outage"]["circuit_opened"] is True
    assert failures["provider_outage"]["deferred_attempts_consumed"] == 0
    assert failures["provider_outage"]["health_probe_recovered"] is True

    assert failures["agent_drop"]["agents_released"] == 40
    assert failures["agent_drop"]["heartbeat_release_seconds"] == 15.0
    assert failures["agent_drop"]["fallback"] == "progressive"


def test_executed_failure_evidence_is_seed_reproducible(
    session_factory, tmp_path
) -> None:
    first = write_report(
        tmp_path / "first.json", seed=2026, session_factory=session_factory
    )
    engine = session_factory.kw["bind"]
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    second = write_report(
        tmp_path / "second.json", seed=2026, session_factory=session_factory
    )

    assert first["failure_scenarios"] == second["failure_scenarios"]
