from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import select

from smart_dialer.db.models import Agent, Borrower, CallIntent, Campaign
from smart_dialer.domain.states import AgentState
from smart_dialer.services.allocation import reserve_progressive_pair
from tests.integration.support import add_approved_safety_decision

pytestmark = pytest.mark.integration


def seed_campaign(session_factory, *, pairs: int) -> tuple[str, str]:
    with session_factory.begin() as session:
        campaign = Campaign(name="collections", mode="progressive", language="en-IN")
        session.add(campaign)
        session.flush()
        for index in range(pairs):
            session.add(
                Agent(
                    campaign_id=campaign.id,
                    name=f"Agent {index}",
                    language="en-IN",
                    state=AgentState.AVAILABLE,
                    last_heartbeat_at=datetime.now(UTC),
                    available_since=datetime.now(UTC),
                )
            )
            session.add(
                Borrower(
                    campaign_id=campaign.id,
                    external_id=f"borrower-{index}",
                    phone=f"+91900000{index:04d}",
                    language="en-IN",
                )
            )
        safety_decision_id = add_approved_safety_decision(
            session,
            campaign_id=campaign.id,
            mode="progressive",
            approved_calls=pairs,
        )
        return campaign.id, safety_decision_id


def test_concurrent_workers_allocate_each_pair_at_most_once(session_factory) -> None:
    worker_count = 8
    campaign_id, safety_decision_id = seed_campaign(session_factory, pairs=worker_count)
    start = Barrier(worker_count)

    def allocate(worker_number: int) -> tuple[str, str] | None:
        with session_factory.begin() as session:
            start.wait(timeout=5)
            intent = reserve_progressive_pair(
                session,
                campaign_id=campaign_id,
                safety_decision_id=safety_decision_id,
                worker_id=f"worker-{worker_number}",
                now=datetime.now(UTC),
            )
            return (intent.agent_id, intent.borrower_id) if intent else None

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        allocations = list(executor.map(allocate, range(worker_count)))

    successful = [allocation for allocation in allocations if allocation is not None]
    assert len(successful) == worker_count
    assert len({agent_id for agent_id, _ in successful}) == worker_count
    assert len({borrower_id for _, borrower_id in successful}) == worker_count


def test_only_one_worker_can_claim_a_single_pair(session_factory) -> None:
    worker_count = 6
    campaign_id, safety_decision_id = seed_campaign(session_factory, pairs=1)
    start = Barrier(worker_count)

    def allocate(worker_number: int) -> str | None:
        with session_factory.begin() as session:
            start.wait(timeout=5)
            intent = reserve_progressive_pair(
                session,
                campaign_id=campaign_id,
                safety_decision_id=safety_decision_id,
                worker_id=f"worker-{worker_number}",
                now=datetime.now(UTC),
            )
            return intent.id if intent else None

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        intent_ids = list(executor.map(allocate, range(worker_count)))

    assert len([intent_id for intent_id in intent_ids if intent_id]) == 1


def test_locked_agent_is_skipped_instead_of_blocking(session_factory) -> None:
    campaign_id, safety_decision_id = seed_campaign(session_factory, pairs=2)

    with session_factory() as locking_session:
        locking_session.begin()
        first_agent = locking_session.scalar(
            select(Agent)
            .where(Agent.campaign_id == campaign_id)
            .order_by(Agent.available_since, Agent.id)
            .with_for_update()
            .limit(1)
        )
        assert first_agent is not None

        with session_factory.begin() as allocating_session:
            intent = reserve_progressive_pair(
                allocating_session,
                campaign_id=campaign_id,
                safety_decision_id=safety_decision_id,
                worker_id="worker-skip",
                now=datetime.now(UTC),
            )
            assert intent is not None
            assert intent.agent_id != first_agent.id
        locking_session.rollback()


def test_worker_crash_before_commit_leaves_no_reservation(session_factory) -> None:
    campaign_id, safety_decision_id = seed_campaign(session_factory, pairs=1)

    with pytest.raises(RuntimeError, match="simulated crash"):
        with session_factory.begin() as session:
            assert reserve_progressive_pair(
                session,
                campaign_id=campaign_id,
                safety_decision_id=safety_decision_id,
                worker_id="crashing-worker",
                now=datetime.now(UTC),
            )
            raise RuntimeError("simulated crash")

    with session_factory() as session:
        agent = session.scalar(select(Agent))
        borrower = session.scalar(select(Borrower))
        intents = session.scalars(select(CallIntent)).all()

        assert agent is not None and agent.state == AgentState.AVAILABLE
        assert agent.reservation_owner_id is None
        assert borrower is not None and borrower.reservation_owner_id is None
        assert intents == []
