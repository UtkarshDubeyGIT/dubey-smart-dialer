from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from smart_dialer.db.models import ProviderHealth
from smart_dialer.providers.base import TelecomProvider


FAILURE_RATE_THRESHOLD = 0.30
MINIMUM_FAILURE_RATE_SAMPLE = 10
MAXIMUM_OUTCOME_WINDOW = 20
CONSECUTIVE_TIMEOUT_THRESHOLD = 3
CIRCUIT_COOLDOWN = timedelta(seconds=30)


def record_provider_attempt(
    session: Session,
    *,
    provider_name: str,
    succeeded: bool,
    timed_out: bool,
    now: datetime,
) -> ProviderHealth:
    health = _locked_health(session, provider_name=provider_name)
    outcomes = [*health.recent_outcomes, succeeded][-MAXIMUM_OUTCOME_WINDOW:]
    health.recent_outcomes = outcomes
    health.consecutive_timeouts = health.consecutive_timeouts + 1 if timed_out else 0
    failure_rate = 1.0 - (sum(outcomes) / len(outcomes))
    if (
        health.consecutive_timeouts >= CONSECUTIVE_TIMEOUT_THRESHOLD
        or (
            len(outcomes) >= MINIMUM_FAILURE_RATE_SAMPLE
            and failure_rate >= FAILURE_RATE_THRESHOLD
        )
    ):
        health.state = "open"
        health.opened_at = now
    health.updated_at = now
    session.flush()
    return health


def provider_is_healthy(session: Session, *, provider_name: str) -> bool:
    health = session.get(ProviderHealth, provider_name)
    return health is None or health.state == "closed"


def provider_allows_initiation(
    session: Session,
    *,
    provider: TelecomProvider,
    now: datetime,
) -> bool:
    health = session.get(ProviderHealth, provider.name)
    if health is None or health.state == "closed":
        return True
    if (
        health.state != "open"
        or health.opened_at is None
        or now < health.opened_at + CIRCUIT_COOLDOWN
    ):
        return False

    health = session.scalar(
        select(ProviderHealth)
        .where(ProviderHealth.provider_name == provider.name)
        .with_for_update()
    )
    if health.state == "closed":
        return True
    if (
        health.state != "open"
        or health.opened_at is None
        or now < health.opened_at + CIRCUIT_COOLDOWN
    ):
        return False

    health.state = "half_open"
    health.last_probe_at = now
    try:
        healthy = provider.health_check()
    except Exception:
        healthy = False
    if healthy:
        health.state = "closed"
        health.recent_outcomes = []
        health.consecutive_timeouts = 0
        health.opened_at = None
    else:
        health.state = "open"
        health.opened_at = now
    health.updated_at = now
    session.flush()
    return healthy


def _locked_health(session: Session, *, provider_name: str) -> ProviderHealth:
    session.execute(
        insert(ProviderHealth)
        .values(provider_name=provider_name, state="closed", recent_outcomes=[])
        .on_conflict_do_nothing(index_elements=[ProviderHealth.provider_name])
    )
    return session.scalar(
        select(ProviderHealth)
        .where(ProviderHealth.provider_name == provider_name)
        .with_for_update()
    )
