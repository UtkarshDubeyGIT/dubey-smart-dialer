from smart_dialer.domain.pacing import PacingProposal, SafetyContext
from smart_dialer.services.safety import SafetyController
from smart_dialer.statistics import binomial_overload_probability, wilson_upper_bound


def healthy_context(**overrides: object) -> SafetyContext:
    values: dict[str, object] = {
        "available_agents": 10,
        "observed_answers": 30,
        "observed_attempts": 100,
        "requested_risk": 0.005,
        "provider_healthy": True,
        "agent_data_stale": False,
        "rapid_agent_drop": False,
    }
    values.update(overrides)
    return SafetyContext(**values)


def test_wilson_upper_bound_is_conservative() -> None:
    upper = wilson_upper_bound(successes=30, trials=100)

    assert 0.30 < upper < 0.50


def test_binomial_tail_is_zero_when_calls_do_not_exceed_capacity() -> None:
    assert binomial_overload_probability(calls=10, answer_probability=0.9, capacity=10) == 0.0


def test_safety_controller_reduces_an_unsafe_proposal() -> None:
    receipt = SafetyController().evaluate(
        PacingProposal(requested_calls=40, explanation="expected-value proposal"),
        healthy_context(),
    )

    assert 10 < receipt.approved_calls < 40
    assert receipt.overload_probability <= 0.005
    assert receipt.decision == "reduced"


def test_zero_risk_behaves_identically_to_progressive() -> None:
    controller = SafetyController()
    zero_risk = controller.evaluate(
        PacingProposal(requested_calls=40, explanation="predictive proposal"),
        healthy_context(requested_risk=0.0),
    )
    progressive = controller.evaluate(
        PacingProposal(requested_calls=40, explanation="progressive proposal"),
        healthy_context(requested_risk=0.0),
    )

    assert zero_risk.approved_calls == progressive.approved_calls == 10
    assert zero_risk.effective_mode == progressive.effective_mode == "progressive"


def test_risk_is_capped_at_absolute_one_percent() -> None:
    receipt = SafetyController().evaluate(
        PacingProposal(requested_calls=100, explanation="overconfigured campaign"),
        healthy_context(requested_risk=0.50),
    )

    assert receipt.effective_risk == 0.01
    assert "absolute risk ceiling" in receipt.reasons


def test_cold_start_forces_progressive() -> None:
    receipt = SafetyController().evaluate(
        PacingProposal(requested_calls=30, explanation="cold start"),
        healthy_context(observed_answers=2, observed_attempts=9),
    )

    assert receipt.approved_calls == 10
    assert receipt.effective_mode == "progressive"
    assert "cold start" in receipt.reasons


def test_provider_degradation_forces_progressive() -> None:
    receipt = SafetyController().evaluate(
        PacingProposal(requested_calls=30, explanation="provider degraded"),
        healthy_context(provider_healthy=False),
    )

    assert receipt.approved_calls == 10
    assert receipt.effective_mode == "progressive"
    assert "provider degradation" in receipt.reasons
