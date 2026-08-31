from smart_dialer.domain.pacing import PacingSnapshot
from smart_dialer.services.pacing import PredictivePacingEngine, ProgressivePacingEngine


def test_progressive_proposes_one_call_per_available_agent() -> None:
    proposal = ProgressivePacingEngine().propose(
        PacingSnapshot(
            available_agents=12,
            ringing_calls=7,
            observed_answers=30,
            observed_attempts=100,
        )
    )

    assert proposal.requested_calls == 12
    assert "one call per available agent" in proposal.explanation


def test_predictive_proposal_accounts_for_expected_ringing_answers() -> None:
    proposal = PredictivePacingEngine(max_batch=100).propose(
        PacingSnapshot(
            available_agents=10,
            ringing_calls=5,
            observed_answers=30,
            observed_attempts=100,
        )
    )

    assert proposal.requested_calls == 29
    assert "answer_rate=0.3000" in proposal.explanation


def test_predictive_proposal_uses_releases_expected_during_setup() -> None:
    without_releases = PredictivePacingEngine(max_batch=100).propose(
        PacingSnapshot(10, 0, 30, 100, expected_releases_within_setup=0)
    )
    with_releases = PredictivePacingEngine(max_batch=100).propose(
        PacingSnapshot(10, 0, 30, 100, expected_releases_within_setup=2)
    )

    assert with_releases.requested_calls > without_releases.requested_calls


def test_predictive_proposal_respects_campaign_batch_cap() -> None:
    proposal = PredictivePacingEngine(max_batch=15).propose(
        PacingSnapshot(50, 0, 10, 100)
    )

    assert proposal.requested_calls == 15
    assert "batch_cap=15" in proposal.explanation


def test_predictive_cold_start_proposes_progressive_count_for_safety_to_review() -> None:
    proposal = PredictivePacingEngine(max_batch=100).propose(
        PacingSnapshot(8, 0, 0, 0)
    )

    assert proposal.requested_calls == 8
    assert "cold start" in proposal.explanation
