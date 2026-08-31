from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from smart_dialer.db.models import CallIntent, ProviderEvent
from smart_dialer.domain.states import CallState


RECENT_ATTEMPT_WINDOW = 200


@dataclass(frozen=True)
class AnswerHistory:
    observed_answers: int
    observed_attempts: int
    inferred_answers: int


@dataclass(frozen=True)
class TimingHistory:
    average_setup_seconds: float | None
    average_talk_seconds: float | None
    expected_releases_within_setup: int
    setup_samples: int
    talk_samples: int


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


def load_timing_history(
    session: Session,
    *,
    campaign_id: str,
    now: datetime,
    limit: int = RECENT_ATTEMPT_WINDOW,
) -> TimingHistory:
    completed_ids = session.scalars(
        select(CallIntent.id)
        .where(
            CallIntent.campaign_id == campaign_id,
            CallIntent.state == CallState.COMPLETED,
            CallIntent.answer_observation == "observed",
        )
        .order_by(CallIntent.updated_at.desc(), CallIntent.id.desc())
        .limit(max(0, limit))
    ).all()
    completed_times = _event_times(session, intent_ids=completed_ids)
    setup_samples: list[float] = []
    talk_samples: list[float] = []
    for times in completed_times.values():
        ringing = times.get(CallState.RINGING)
        answered = times.get(CallState.ANSWERED)
        completed = times.get(CallState.COMPLETED)
        connected = times.get(CallState.CONNECTED)
        if ringing is not None and answered is not None and answered >= ringing:
            setup_samples.append((answered - ringing).total_seconds())
        talk_start = connected or answered
        if talk_start is not None and completed is not None and completed >= talk_start:
            talk_samples.append((completed - talk_start).total_seconds())

    average_setup = _average(setup_samples)
    average_talk = _average(talk_samples)
    expected_releases = 0
    if average_setup is not None and average_talk is not None:
        active_ids = session.scalars(
            select(CallIntent.id).where(
                CallIntent.campaign_id == campaign_id,
                CallIntent.state.in_({CallState.ANSWERED, CallState.CONNECTED}),
                CallIntent.agent_id.is_not(None),
                CallIntent.answer_observation == "observed",
            )
        ).all()
        for times in _event_times(session, intent_ids=active_ids).values():
            talk_start = times.get(CallState.CONNECTED) or times.get(CallState.ANSWERED)
            if talk_start is None:
                continue
            expected_completion = talk_start.timestamp() + average_talk
            if expected_completion <= now.timestamp() + average_setup:
                expected_releases += 1

    return TimingHistory(
        average_setup_seconds=average_setup,
        average_talk_seconds=average_talk,
        expected_releases_within_setup=expected_releases,
        setup_samples=len(setup_samples),
        talk_samples=len(talk_samples),
    )


def _event_times(
    session: Session,
    *,
    intent_ids: list[str],
) -> dict[str, dict[CallState, datetime]]:
    if not intent_ids:
        return {}
    events = session.scalars(
        select(ProviderEvent)
        .where(
            ProviderEvent.call_intent_id.in_(intent_ids),
            ProviderEvent.target_state.in_({
                CallState.RINGING,
                CallState.ANSWERED,
                CallState.CONNECTED,
                CallState.COMPLETED,
            }),
        )
        .order_by(ProviderEvent.occurred_at, ProviderEvent.id)
    ).all()
    by_intent: dict[str, dict[CallState, datetime]] = {}
    for event in events:
        by_intent.setdefault(event.call_intent_id, {}).setdefault(
            event.target_state, event.occurred_at
        )
    return by_intent


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)
