from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smart_dialer.db.base import Base
from smart_dialer.domain.states import AgentState, CallState


def new_id() -> str:
    return uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class BorrowerState(StrEnum):
    QUEUED = "queued"
    RESERVED = "reserved"
    DIALING = "dialing"
    COMPLETED = "completed"
    MANUAL_REVIEW = "manual_review"


class IntentMode(StrEnum):
    PROGRESSIVE = "progressive"
    PREDICTIVE = "predictive"


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="progressive")
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en-IN")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_tolerance: Mapped[float] = mapped_column(Float, nullable=False, default=0.005)
    provider_name: Mapped[str] = mapped_column(String(40), nullable=False, default="plivo_mock")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en-IN")
    state: Mapped[AgentState] = mapped_column(
        Enum(AgentState, name="agent_state", values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
        default=AgentState.OFFLINE,
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reservation_owner_id: Mapped[str | None] = mapped_column(String(32), index=True)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_agents_allocation", "campaign_id", "state", "language", "available_since"),
    )


class Borrower(Base):
    __tablename__ = "borrowers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="en-IN")
    state: Mapped[BorrowerState] = mapped_column(
        Enum(BorrowerState, name="borrower_state", values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
        default=BorrowerState.QUEUED,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reservation_owner_id: Mapped[str | None] = mapped_column(String(32), index=True)

    __table_args__ = (
        UniqueConstraint("campaign_id", "external_id", name="uq_borrower_campaign_external"),
        Index("ix_borrowers_allocation", "campaign_id", "state", "next_attempt_at", "created_at"),
    )


class CallIntent(Base):
    __tablename__ = "call_intents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    borrower_id: Mapped[str] = mapped_column(ForeignKey("borrowers.id", ondelete="RESTRICT"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    mode: Mapped[IntentMode] = mapped_column(
        Enum(IntentMode, name="intent_mode", values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
    )
    state: Mapped[CallState] = mapped_column(
        Enum(CallState, name="call_state", values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
        default=CallState.RESERVED,
    )
    provider_name: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(100))
    lease_owner: Mapped[str | None] = mapped_column(String(100), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answer_observation: Mapped[str | None] = mapped_column(String(20))
    manual_review_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    agent: Mapped[Agent | None] = relationship()
    borrower: Mapped[Borrower] = relationship()

    __table_args__ = (
        Index("ix_call_intents_claim", "state", "lease_expires_at", "created_at"),
    )


class ProviderEvent(Base):
    __tablename__ = "provider_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    call_intent_id: Mapped[str] = mapped_column(ForeignKey("call_intents.id", ondelete="CASCADE"), index=True)
    provider_name: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    target_state: Mapped[CallState] = mapped_column(
        Enum(CallState, name="provider_target_call_state", values_callable=lambda enum: [item.value for item in enum]),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    processing_result: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")

    __table_args__ = (
        UniqueConstraint("provider_name", "provider_event_id", name="uq_provider_event_id"),
        UniqueConstraint("provider_name", "semantic_fingerprint", name="uq_provider_event_fingerprint"),
    )


class SafetyDecision(Base):
    __tablename__ = "safety_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    requested_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_risk: Mapped[float] = mapped_column(Float, nullable=False)
    overload_probability: Mapped[float] = mapped_column(Float, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    call_intent_id: Mapped[str | None] = mapped_column(ForeignKey("call_intents.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
