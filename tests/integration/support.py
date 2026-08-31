from sqlalchemy.orm import Session

from smart_dialer.db.models import SafetyDecision


def add_approved_safety_decision(
    session: Session,
    *,
    campaign_id: str,
    mode: str,
    approved_calls: int = 100,
) -> str:
    """Create explicit authorization for integration-test setup paths."""
    decision = SafetyDecision(
        campaign_id=campaign_id,
        requested_calls=approved_calls,
        approved_calls=approved_calls,
        decision="approved",
        effective_mode=mode,
        effective_risk=0.0 if mode == "progressive" else 0.005,
        overload_probability=0.0,
        inputs={"source": "integration_test_setup"},
        reasons=[],
    )
    session.add(decision)
    session.flush()
    return decision.id
