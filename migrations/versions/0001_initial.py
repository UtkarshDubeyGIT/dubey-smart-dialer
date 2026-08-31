"""Initial SmartDialer schema.

Revision ID: 0001
Revises: None
"""
from alembic import op

from smart_dialer.db.base import Base
from smart_dialer.db import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
