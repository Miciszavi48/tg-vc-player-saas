"""Add named playlists and their items (TRANSPORT-09/10/11).

Revision ID: 0037_named_playlists
Revises: 0036_chat_settings_channel_link
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_named_playlists"
down_revision = "0036_chat_settings_channel_link"
branch_labels = None
depends_on = None

PLAYLISTS = "named_playlists"
ITEMS = "named_playlist_items"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, PLAYLISTS):
        op.create_table(
            PLAYLISTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False, index=True),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("chat_id", "name", name="uq_named_playlists_chat_name"),
        )

    if not _has_table(inspector, ITEMS):
        op.create_table(
            ITEMS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "playlist_id",
                sa.Integer(),
                sa.ForeignKey("named_playlists.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("title", sa.String(length=512), nullable=True),
            sa.Column("source", sa.String(length=2048), nullable=False),
            sa.Column("media_type", sa.String(length=16), nullable=False, server_default="audio"),
            sa.Column("added_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "added_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, ITEMS):
        op.drop_table(ITEMS)
    if _has_table(inspector, PLAYLISTS):
        op.drop_table(PLAYLISTS)
