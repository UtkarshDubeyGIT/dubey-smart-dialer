from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from smart_dialer.config import get_settings


pytestmark = pytest.mark.integration


def test_0003_backfills_existing_intents_before_enforcing_safety_fk(
    session_factory, monkeypatch
) -> None:
    engine = session_factory.kw["bind"]
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE call_intents "
            "DROP CONSTRAINT fk_call_intents_safety_decision_id"
        ))
        connection.execute(text("DROP INDEX ix_call_intents_safety_decision_id"))
        connection.execute(text(
            "ALTER TABLE call_intents DROP COLUMN safety_decision_id"
        ))
        connection.execute(text("DROP TABLE agent_presence_events"))
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        ))
        connection.execute(text(
            "INSERT INTO alembic_version (version_num) VALUES ('0002')"
        ))
        connection.execute(text("""
            INSERT INTO campaigns (
                id, name, mode, language, priority, risk_tolerance,
                provider_name, created_at
            ) VALUES (
                'migration-campaign', 'Migration fixture', 'progressive',
                'en-IN', 0, 0.005, 'plivo_mock', :now
            )
        """), {"now": now})
        connection.execute(text("""
            INSERT INTO borrowers (
                id, campaign_id, external_id, phone, language, state, created_at
            ) VALUES (
                'migration-borrower', 'migration-campaign', 'legacy-borrower',
                '+919000000008', 'en-IN', 'queued', :now
            )
        """), {"now": now})
        connection.execute(text("""
            INSERT INTO call_intents (
                id, campaign_id, borrower_id, mode, state, provider_name,
                provider_idempotency_key, processing_attempts, created_at, updated_at
            ) VALUES (
                'migration-intent', 'migration-campaign', 'migration-borrower',
                'progressive', 'reserved', 'plivo_mock', 'migration-key', 0,
                :now, :now
            )
        """), {"now": now})

    monkeypatch.setenv(
        "DATABASE_URL", engine.url.render_as_string(hide_password=False)
    )
    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")

        inspector = inspect(engine)
        safety_column = next(
            column
            for column in inspector.get_columns("call_intents")
            if column["name"] == "safety_decision_id"
        )
        safety_fks = [
            foreign_key
            for foreign_key in inspector.get_foreign_keys("call_intents")
            if foreign_key["constrained_columns"] == ["safety_decision_id"]
        ]
        with engine.connect() as connection:
            migrated = connection.execute(text("""
                SELECT ci.safety_decision_id, sd.inputs
                FROM call_intents AS ci
                JOIN safety_decisions AS sd ON sd.id = ci.safety_decision_id
                WHERE ci.id = 'migration-intent'
            """)).mappings().one()

        assert safety_column["nullable"] is False
        assert len(safety_fks) == 1
        assert safety_fks[0]["referred_table"] == "safety_decisions"
        assert migrated["inputs"]["source"] == "migration_backfill"
        assert inspector.has_table("agent_presence_events")
    finally:
        get_settings.cache_clear()
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
