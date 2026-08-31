"""Enforce safety receipts and persist live presence departures.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
from sqlalchemy import Column, String, inspect, text

from smart_dialer.db.models import AgentPresenceEvent


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table(AgentPresenceEvent.__tablename__):
        AgentPresenceEvent.__table__.create(bind=bind)

    call_intent_columns = {
        column["name"] for column in inspect(bind).get_columns("call_intents")
    }
    if "safety_decision_id" not in call_intent_columns:
        op.add_column(
            "call_intents",
            Column("safety_decision_id", String(32), nullable=True),
        )
        # Existing prototype rows predate schema-enforced authorization. Give each
        # one an explicit, auditable migration receipt before making the FK strict.
        bind.execute(text("""
            INSERT INTO safety_decisions (
                id, campaign_id, requested_calls, approved_calls, decision,
                effective_mode, effective_risk, overload_probability,
                inputs, reasons, created_at
            )
            SELECT
                md5('legacy-safety:' || ci.id), ci.campaign_id, 1, 1, 'approved',
                ci.mode::text, 0.0, 0.0,
                json_build_object('source', 'migration_backfill'),
                json_build_array('legacy call-intent authorization backfill'),
                ci.created_at
            FROM call_intents AS ci
            WHERE ci.safety_decision_id IS NULL
            ON CONFLICT (id) DO NOTHING
        """))
        bind.execute(text("""
            UPDATE call_intents
            SET safety_decision_id = md5('legacy-safety:' || id)
            WHERE safety_decision_id IS NULL
        """))
        op.alter_column("call_intents", "safety_decision_id", nullable=False)
        op.create_foreign_key(
            "fk_call_intents_safety_decision_id",
            "call_intents",
            "safety_decisions",
            ["safety_decision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            "ix_call_intents_safety_decision_id",
            "call_intents",
            ["safety_decision_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("call_intents")}
    if "safety_decision_id" in columns:
        indexes = {index["name"] for index in inspector.get_indexes("call_intents")}
        if "ix_call_intents_safety_decision_id" in indexes:
            op.drop_index("ix_call_intents_safety_decision_id", table_name="call_intents")
        for foreign_key in inspector.get_foreign_keys("call_intents"):
            if foreign_key["constrained_columns"] == ["safety_decision_id"]:
                op.drop_constraint(foreign_key["name"], "call_intents", type_="foreignkey")
        op.drop_column("call_intents", "safety_decision_id")
    if inspect(bind).has_table(AgentPresenceEvent.__tablename__):
        AgentPresenceEvent.__table__.drop(bind=bind)
