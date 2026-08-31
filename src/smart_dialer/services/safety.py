from smart_dialer.domain.pacing import PacingProposal, SafetyContext, SafetyReceipt
from smart_dialer.statistics import largest_safe_batch, wilson_upper_bound


class SafetyController:
    """Mandatory boundary between pacing proposals and durable call intents."""

    ABSOLUTE_RISK_CEILING = 0.01
    MINIMUM_PREDICTIVE_SAMPLE = 30

    def evaluate(self, proposal: PacingProposal, context: SafetyContext) -> SafetyReceipt:
        requested = max(0, proposal.requested_calls)
        effective_risk = min(
            self.ABSOLUTE_RISK_CEILING,
            max(0.0, context.requested_risk),
        )
        reasons: list[str] = []
        if context.requested_risk > self.ABSOLUTE_RISK_CEILING:
            reasons.append("absolute risk ceiling")

        fallback_reasons = self._fallback_reasons(context, effective_risk)
        reasons.extend(fallback_reasons)
        if fallback_reasons:
            approved = min(requested, max(0, context.available_agents))
            return SafetyReceipt(
                requested_calls=requested,
                approved_calls=approved,
                decision=self._decision(requested, approved),
                effective_mode="progressive",
                effective_risk=effective_risk,
                answer_rate_upper_bound=1.0,
                overload_probability=0.0,
                reasons=tuple(reasons),
                explanation=proposal.explanation,
            )

        answer_upper = wilson_upper_bound(
            context.observed_answers,
            context.observed_attempts,
        )
        approved, overload_probability = largest_safe_batch(
            requested,
            answer_upper,
            max(0, context.available_agents),
            effective_risk,
        )
        if approved < requested:
            reasons.append("confidence-bounded overload limit")
        return SafetyReceipt(
            requested_calls=requested,
            approved_calls=approved,
            decision=self._decision(requested, approved),
            effective_mode="predictive",
            effective_risk=effective_risk,
            answer_rate_upper_bound=answer_upper,
            overload_probability=overload_probability,
            reasons=tuple(reasons),
            explanation=proposal.explanation,
        )

    def _fallback_reasons(self, context: SafetyContext, effective_risk: float) -> list[str]:
        reasons: list[str] = []
        if effective_risk == 0.0:
            reasons.append("zero risk policy")
        if context.observed_attempts < self.MINIMUM_PREDICTIVE_SAMPLE:
            reasons.append("cold start")
        if not context.provider_healthy:
            reasons.append("provider degradation")
        if context.agent_data_stale:
            reasons.append("stale agent data")
        if context.rapid_agent_drop:
            reasons.append("rapid agent availability drop")
        return reasons

    @staticmethod
    def _decision(requested: int, approved: int) -> str:
        if approved == requested:
            return "approved"
        if approved == 0:
            return "rejected"
        return "reduced"
