"""Add non-secret expiry metadata for YouTube cookie-session monitoring.

Revision ID: 0031_youtube_cookie_hardening
Revises: 0030_youtube_cookie_session_pool
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0031_youtube_cookie_hardening"
down_revision = "0030_youtube_cookie_session_pool"
branch_labels = None
depends_on = None

_TABLE = "youtube_cookie_sessions"
_INDEX = "idx_youtube_cookie_sessions_expiry"


def _has_column(inspector: sa.Inspector, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(_TABLE))


def _has_index(inspector: sa.Inspector, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_column(inspector, "cookie_expires_at"):
        op.add_column(_TABLE, sa.Column("cookie_expires_at", sa.DateTime(timezone=True), nullable=True))
        inspector = sa.inspect(bind)
    if not _has_index(inspector, _INDEX):
        op.create_index(_INDEX, _TABLE, ["status", "cookie_expires_at", "id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_index(inspector, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    inspector = sa.inspect(bind)
    if _has_column(inspector, "cookie_expires_at"):
        op.drop_column(_TABLE, "cookie_expires_at")
