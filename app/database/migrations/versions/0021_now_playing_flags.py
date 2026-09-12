"""Add now-playing metadata display flags to chat_settings.

Revision ID: 0021_now_playing_flags
Revises: 0020_chat_default_media_type
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_now_playing_flags"
down_revision = "0020_chat_default_media_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "show_track_id",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "show_cover",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "chat_settings",
        sa.Column(
            "show_now_playing_text",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_settings", "show_now_playing_text")
    op.drop_column("chat_settings", "show_cover")
    op.drop_column("chat_settings", "show_track_id")
