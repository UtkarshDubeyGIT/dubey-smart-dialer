"""Add durable provider circuit state.

Revision ID: 0002
Revises: 0001
"""
from sqlalchemy import inspect

from alembic import op

from smart_dialer.db.models import ProviderHealth


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(ProviderHealth.__tablename__):
        ProviderHealth.__table__.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table(ProviderHealth.__tablename__):
        ProviderHealth.__table__.drop(bind=bind)
