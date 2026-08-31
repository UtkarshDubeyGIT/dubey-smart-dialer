from smart_dialer.simulation import Scenario, run_scenario, run_suite


def test_simulator_is_seed_reproducible() -> None:
    scenario = Scenario("A", answer_rate=0.2, average_talk_seconds=120)

    assert run_scenario(scenario, seed=42) == run_scenario(scenario, seed=42)


def test_simulator_reports_pacing_safety_and_failure_metrics() -> None:
    report = run_suite(seed=2026)

    assert {row["scenario"] for row in report["pacing_scenarios"]} >= {"A", "B", "C", "D-changing"}
    assert report["failure_scenarios"]["duplicate_events"]["state_transitions"] == 1
    assert report["failure_scenarios"]["worker_crash"]["recovered"] is True
    assert report["failure_scenarios"]["provider_outage"]["circuit_opened"] is True
    assert report["failure_scenarios"]["agent_drop"]["fallback"] == "progressive"


def test_simulator_calls_production_safety_controller(monkeypatch) -> None:
    calls = 0
    from smart_dialer.services.safety import SafetyController
    original = SafetyController.evaluate

    def counted(self, proposal, context):
        nonlocal calls
        calls += 1
        return original(self, proposal, context)

    monkeypatch.setattr(SafetyController, "evaluate", counted)
    run_scenario(Scenario("proof", 0.5, 90), seed=1)

    assert calls > 0
