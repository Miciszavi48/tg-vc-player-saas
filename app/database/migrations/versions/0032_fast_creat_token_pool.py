"""Add encrypted global Fast-Creat API token pools.

Revision ID: 0032_fast_creat_token_pool
Revises: 0031_youtube_cookie_hardening
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0032_fast_creat_token_pool"
down_revision = "0031_youtube_cookie_hardening"
branch_labels = None
depends_on = None

_TOKENS = "fast_creat_api_tokens"
_EVENTS = "fast_creat_api_token_events"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return _has_table(inspector, table_name) and any(
        index["name"] == index_name for index in inspector.get_indexes(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, _TOKENS):
        op.create_table(
            _TOKENS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("token_enc", sa.Text(), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
            sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_code", sa.String(length=64), nullable=True),
            sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.CheckConstraint("provider IN ('instagram', 'tiktok', 'spotify')", name="ck_fast_creat_api_tokens_provider"),
            sa.CheckConstraint("status IN ('active', 'disabled', 'invalid')", name="ck_fast_creat_api_tokens_status"),
            sa.CheckConstraint("use_count >= 0", name="ck_fast_creat_api_tokens_use_count"),
            sa.CheckConstraint("failure_count >= 0", name="ck_fast_creat_api_tokens_failure_count"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "fingerprint", name="uq_fast_creat_api_tokens_provider_fingerprint"),
        )
        inspector = sa.inspect(bind)
    if not _has_index(inspector, _TOKENS, "idx_fast_creat_api_tokens_rotation"):
        op.create_index(
            "idx_fast_creat_api_tokens_rotation",
            _TOKENS,
            ["provider", "status", "cooldown_until", "last_selected_at", "id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, _EVENTS):
        op.create_table(
            _EVENTS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("token_id", sa.Integer(), nullable=True),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("actor_id", sa.BigInteger(), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("result_code", sa.String(length=64), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.CheckConstraint("provider IN ('instagram', 'tiktok', 'spotify')", name="ck_fast_creat_api_token_events_provider"),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)
    if not _has_index(inspector, _EVENTS, "idx_fast_creat_api_token_events_token_at"):
        op.create_index(
            "idx_fast_creat_api_token_events_token_at",
            _EVENTS,
            ["token_id", "occurred_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, _EVENTS):
        op.drop_table(_EVENTS)
    inspector = sa.inspect(bind)
    if _has_table(inspector, _TOKENS):
        op.drop_table(_TOKENS)
