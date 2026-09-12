"""Add Hot Seat voice game tables (HOTSEAT-01/02/03).

Revision ID: 0039_hot_seat
Revises: 0038_chat_call_report_dm
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_hot_seat"
down_revision = "0038_chat_call_report_dm"
branch_labels = None
depends_on = None

GAMES = "hot_seat_games"
GUESTS = "hot_seat_guests"


def _has_table(inspector: sa.Inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, GAMES):
        op.create_table(
            GAMES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("chat_id", sa.BigInteger(), nullable=False, index=True),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="joining"),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="all"),
            sa.Column("question_count", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("question_type", sa.String(length=32), nullable=False, server_default="general"),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_hot_seat_games_chat_state", GAMES, ["chat_id", "state"])

    if not _has_table(inspector, GUESTS):
        op.create_table(
            GUESTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "game_id", sa.Integer(),
                sa.ForeignKey("hot_seat_games.id", ondelete="CASCADE"),
                nullable=False, index=True,
            ),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("added_by", sa.BigInteger(), nullable=True),
            sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("game_id", "user_id", name="uq_hot_seat_guests_game_user"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, GUESTS):
        op.drop_table(GUESTS)
    if _has_table(inspector, GAMES):
        op.drop_table(GAMES)
