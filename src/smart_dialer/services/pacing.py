import math

from smart_dialer.domain.pacing import PacingProposal, PacingSnapshot


class ProgressivePacingEngine:
    def propose(self, snapshot: PacingSnapshot) -> PacingProposal:
        requested = max(0, snapshot.available_agents)
        return PacingProposal(
            requested_calls=requested,
            explanation=f"progressive: one call per available agent; available={requested}",
        )


class PredictivePacingEngine:
    """Produces proposals only; this class has no provider or allocator dependency."""

    def __init__(self, max_batch: int = 100) -> None:
        self.max_batch = max(0, max_batch)

    def propose(self, snapshot: PacingSnapshot) -> PacingProposal:
        available = max(0, snapshot.available_agents)
        if snapshot.observed_attempts <= 0:
            return PacingProposal(
                requested_calls=min(available, self.max_batch),
                explanation=(
                    "predictive cold start: progressive-sized proposal for mandatory "
                    f"Safety Controller review; available={available}; batch_cap={self.max_batch}"
                ),
            )

        answer_rate = snapshot.observed_answers / snapshot.observed_attempts
        answer_rate = min(1.0, max(0.01, answer_rate))
        projected_capacity = available + max(0, snapshot.expected_releases_within_setup)
        expected_ringing_answers = max(0, snapshot.ringing_calls) * answer_rate
        unfilled_capacity = max(0.0, projected_capacity - expected_ringing_answers)
        requested = min(self.max_batch, math.ceil(unfilled_capacity / answer_rate))
        return PacingProposal(
            requested_calls=requested,
            explanation=(
                "predictive expected-value proposal: "
                f"answer_rate={answer_rate:.4f}; available={available}; "
                f"expected_releases={max(0, snapshot.expected_releases_within_setup)}; "
                f"ringing={max(0, snapshot.ringing_calls)}; batch_cap={self.max_batch}"
            ),
        )
