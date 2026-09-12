"""Add start customization tables.

Revision ID: 0029_start_customization_schema
Revises: 0028_monthly_invoices
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_start_customization_schema"
down_revision = "0028_monthly_invoices"
branch_labels = None
depends_on = None

TABLE_MESSAGES = "start_customization_messages"
TABLE_BUTTONS = "start_button_configs"
TABLE_STYLES = "start_style_configs"

IDX_MESSAGES_SCOPE_CATEGORY_ACTIVE = "idx_start_custom_messages_scope_category_active"
IDX_MESSAGES_SOURCE = "idx_start_custom_messages_source"
IDX_BUTTONS_SCOPE = "idx_start_button_configs_scope"


def _has_table(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    if not _has_table(inspector, table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, TABLE_MESSAGES):
        op.create_table(
            TABLE_MESSAGES,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("scope_type", sa.String(length=16), nullable=False),
            sa.Column(
                "scope_owner_user_id",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("category", sa.String(length=16), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("weight", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
            sa.Column("source_message_id", sa.Integer(), nullable=True),
            sa.Column("source_chat_type", sa.String(length=32), nullable=True),
            sa.Column("message_type", sa.String(length=32), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("caption", sa.Text(), nullable=True),
            sa.Column("media_file_id", sa.String(length=512), nullable=True),
            sa.Column("media_type", sa.String(length=32), nullable=True),
            sa.Column("entities_json", sa.Text(), nullable=True),
            sa.Column("extra_json", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            sa.CheckConstraint(
                "scope_type IN ('global', 'owner')",
                name="ck_start_custom_msg_scope_type",
            ),
            sa.CheckConstraint(
                "(scope_type = 'global' AND scope_owner_user_id = 0) "
                "OR (scope_type = 'owner' AND scope_owner_user_id > 0)",
                name="ck_start_custom_msg_scope_owner",
            ),
            sa.CheckConstraint(
                "category IN ('start', 'ability', 'test', 'use', 'history', 'note')",
                name="ck_start_custom_msg_category",
            ),
            sa.CheckConstraint("weight > 0", name="ck_start_custom_msg_weight_positive"),
            sa.PrimaryKeyConstraint("id"),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, TABLE_MESSAGES, IDX_MESSAGES_SCOPE_CATEGORY_ACTIVE):
        op.create_index(
            IDX_MESSAGES_SCOPE_CATEGORY_ACTIVE,
            TABLE_MESSAGES,
            ["scope_type", "scope_owner_user_id", "category", "is_active"],
            unique=False,
        )
    if not _has_index(inspector, TABLE_MESSAGES, IDX_MESSAGES_SOURCE):
        op.create_index(
            IDX_MESSAGES_SOURCE,
            TABLE_MESSAGES,
            ["source_chat_id", "source_message_id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE_BUTTONS):
        op.create_table(
            TABLE_BUTTONS,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("scope_type", sa.String(length=16), nullable=False),
            sa.Column(
                "scope_owner_user_id",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("slot_key", sa.String(length=32), nullable=False),
            sa.Column("slot_index", sa.SmallInteger(), nullable=False),
            sa.Column("custom_text", sa.Text(), nullable=True),
            sa.Column("color_token", sa.String(length=1), nullable=True),
            sa.Column("emoji_text", sa.Text(), nullable=True),
            sa.Column("emoji_entities_json", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            sa.CheckConstraint(
                "scope_type IN ('global', 'owner')",
                name="ck_start_button_configs_scope_type",
            ),
            sa.CheckConstraint(
                "(scope_type = 'global' AND scope_owner_user_id = 0) "
                "OR (scope_type = 'owner' AND scope_owner_user_id > 0)",
                name="ck_start_button_configs_scope_owner",
            ),
            sa.CheckConstraint(
                "slot_key IN ('purchase', 'test', 'use', 'history', 'ability', "
                "'commands', 'support', 'note')",
                name="ck_start_button_configs_slot_key",
            ),
            sa.CheckConstraint(
                "slot_index BETWEEN 1 AND 8",
                name="ck_start_button_configs_slot_index",
            ),
            sa.CheckConstraint(
                "color_token IS NULL OR color_token IN ('R', 'G', 'B', 'N')",
                name="ck_start_button_configs_color",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scope_type",
                "scope_owner_user_id",
                "slot_key",
                name="uq_start_button_configs_scope_slot",
            ),
        )
        inspector = sa.inspect(bind)

    if not _has_index(inspector, TABLE_BUTTONS, IDX_BUTTONS_SCOPE):
        op.create_index(
            IDX_BUTTONS_SCOPE,
            TABLE_BUTTONS,
            ["scope_type", "scope_owner_user_id"],
            unique=False,
        )

    inspector = sa.inspect(bind)
    if not _has_table(inspector, TABLE_STYLES):
        op.create_table(
            TABLE_STYLES,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("scope_type", sa.String(length=16), nullable=False),
            sa.Column(
                "scope_owner_user_id",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column(
                "style_mode",
                sa.String(length=16),
                server_default=sa.text("'simple'"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            sa.CheckConstraint(
                "scope_type IN ('global', 'owner')",
                name="ck_start_style_configs_scope_type",
            ),
            sa.CheckConstraint(
                "(scope_type = 'global' AND scope_owner_user_id = 0) "
                "OR (scope_type = 'owner' AND scope_owner_user_id > 0)",
                name="ck_start_style_configs_scope_owner",
            ),
            sa.CheckConstraint(
                "style_mode IN ('simple', 'advanced')",
                name="ck_start_style_configs_style_mode",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scope_type",
                "scope_owner_user_id",
                name="uq_start_style_configs_scope",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, TABLE_STYLES):
        op.drop_table(TABLE_STYLES)

    inspector = sa.inspect(bind)
    if _has_table(inspector, TABLE_BUTTONS):
        if _has_index(inspector, TABLE_BUTTONS, IDX_BUTTONS_SCOPE):
            op.drop_index(IDX_BUTTONS_SCOPE, table_name=TABLE_BUTTONS)
        op.drop_table(TABLE_BUTTONS)

    inspector = sa.inspect(bind)
    if _has_table(inspector, TABLE_MESSAGES):
        for index_name in (
            IDX_MESSAGES_SOURCE,
            IDX_MESSAGES_SCOPE_CATEGORY_ACTIVE,
        ):
            inspector = sa.inspect(bind)
            if _has_index(inspector, TABLE_MESSAGES, index_name):
                op.drop_index(index_name, table_name=TABLE_MESSAGES)
        op.drop_table(TABLE_MESSAGES)
