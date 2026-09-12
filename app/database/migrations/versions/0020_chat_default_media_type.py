"""Add default_media_type to chat_settings for group playback default.

Revision ID: 0020_chat_default_media_type
Revises: 0019_daily_deduct_idem
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_chat_default_media_type"
down_revision = "0019_daily_deduct_idem"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "default_media_type",
            sa.String(8),
            nullable=False,
            server_default="audio",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "default_media_type")
