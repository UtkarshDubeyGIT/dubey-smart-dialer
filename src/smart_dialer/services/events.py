from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from smart_dialer.db.models import Agent, BorrowerState, CallIntent, Incident, IntentMode, ProviderEvent
from smart_dialer.domain.states import AgentState, CallState, InvalidTransition, TERMINAL_CALL_STATES, transition_call
from smart_dialer.providers.base import NormalizedProviderEvent
from smart_dialer.services.allocation import attach_agent_on_answer
from smart_dialer.services.recovery import release_owned_reservations


def ingest_provider_event(session: Session, event: NormalizedProviderEvent) -> str:
    """Insert inbox event and apply its transition in the caller's single transaction.

    Database uniqueness, not an application check-then-insert, wins duplicate races.
    Only a row returned from INSERT is eligible to drive the state machine.
    """
    inserted_id = session.scalar(
        insert(ProviderEvent)
        .values(
            call_intent_id=event.call_intent_id,
            provider_name=event.provider_name,
            provider_event_id=event.provider_event_id,
            semantic_fingerprint=event.semantic_fingerprint,
            target_state=event.target_state,
            occurred_at=event.occurred_at,
            payload=event.payload,
            processing_result="pending",
        )
        .on_conflict_do_nothing()
        .returning(ProviderEvent.id)
    )
    if inserted_id is None:
        return "duplicate"

    stored = session.get(ProviderEvent, inserted_id)
    intent = session.scalar(
        select(CallIntent)
        .where(CallIntent.id == event.call_intent_id)
        .with_for_update()
    )
    if intent is None:
        stored.processing_result = "orphaned"
        return "orphaned"

    if intent.state in TERMINAL_CALL_STATES:
        stored.processing_result = "stale"
        return "stale"

    try:
        transition = transition_call(intent.state, event.target_state)
    except InvalidTransition:
        stored.processing_result = "stale"
        return "stale"

    intent.state = transition.current
    if transition.answer_observation is not None:
        if intent.answer_observation != "observed":
            intent.answer_observation = transition.answer_observation
    stored.processing_result = "applied"
    if intent.mode is IntentMode.PROGRESSIVE and intent.agent_id and intent.state is CallState.ANSWERED:
        agent = session.get(Agent, intent.agent_id)
        if agent is not None and agent.reservation_owner_id == intent.id:
            agent.state = AgentState.CONNECTED
    if intent.mode is IntentMode.PREDICTIVE and intent.state.value == "answered":
        attached = attach_agent_on_answer(
            session,
            intent_id=intent.id,
            now=event.occurred_at,
        )
        if attached is None:
            intent.state = CallState.FAILED
            intent.manual_review_reason = "answered call had no human agent capacity"
            session.add(Incident(
                call_intent_id=intent.id,
                kind="overload",
                detail={"provider_event_id": event.provider_event_id},
            ))
            release_owned_reservations(
                session, intent, borrower_state=BorrowerState.MANUAL_REVIEW
            )
    if intent.state in TERMINAL_CALL_STATES:
        borrower_state = BorrowerState.QUEUED
        if intent.state is CallState.COMPLETED:
            borrower_state = BorrowerState.COMPLETED
        elif intent.state is CallState.AMBIGUOUS or intent.manual_review_reason:
            borrower_state = BorrowerState.MANUAL_REVIEW
        release_owned_reservations(session, intent, borrower_state=borrower_state)
    session.flush()
    return "applied"
