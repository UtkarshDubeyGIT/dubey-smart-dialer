from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from smart_dialer.db.models import CallIntent
from smart_dialer.domain.states import CallState


RECENT_ATTEMPT_WINDOW = 200


@dataclass(frozen=True)
class AnswerHistory:
    observed_answers: int
    observed_attempts: int
    inferred_answers: int


def load_answer_history(
    session: Session,
    *,
    campaign_id: str,
    limit: int = RECENT_ATTEMPT_WINDOW,
) -> AnswerHistory:
    observations = session.scalars(
        select(CallIntent.answer_observation)
        .where(
            CallIntent.campaign_id == campaign_id,
            CallIntent.state == CallState.COMPLETED,
            CallIntent.provider_call_id.is_not(None),
        )
        .order_by(CallIntent.updated_at.desc(), CallIntent.id.desc())
        .limit(max(0, limit))
    ).all()
    return AnswerHistory(
        observed_answers=sum(value == "observed" for value in observations),
        observed_attempts=sum(value != "inferred" for value in observations),
        inferred_answers=sum(value == "inferred" for value in observations),
    )
