"""Add helper app credentials and device profiles.

Revision ID: 0023_helper_app_identity
Revises: 0022_owner_text_links
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_helper_app_identity"
down_revision = "0022_owner_text_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "helper_app_credentials" not in tables:
        op.create_table(
            "helper_app_credentials",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("label", sa.String(length=128), nullable=True),
            sa.Column("api_id", sa.Integer(), nullable=False),
            sa.Column("api_hash_enc", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        inspector,
        "helper_app_credentials",
        "ix_helper_app_credentials_api_id",
        ["api_id"],
    )
    _create_index_if_missing(
        inspector,
        "helper_app_credentials",
        "ix_helper_app_credentials_is_active",
        ["is_active"],
    )

    if "helper_device_profiles" not in tables:
        op.create_table(
            "helper_device_profiles",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("device_model", sa.String(length=128), nullable=False),
            sa.Column("system_version", sa.String(length=64), nullable=False),
            sa.Column("app_version", sa.String(length=64), nullable=False),
            sa.Column("lang_code", sa.String(length=8), server_default="en", nullable=False),
            sa.Column("system_lang_code", sa.String(length=16), server_default="en-US", nullable=False),
            sa.Column("source", sa.String(length=64), server_default="manual", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "device_model",
                "system_version",
                "app_version",
                name="uq_helper_device_profiles_identity",
            ),
        )
    _create_index_if_missing(
        inspector,
        "helper_device_profiles",
        "ix_helper_device_profiles_is_active",
        ["is_active"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "helper_device_profiles" in tables:
        _drop_index_if_exists(inspector, "helper_device_profiles", "ix_helper_device_profiles_is_active")
        op.drop_table("helper_device_profiles")
    if "helper_app_credentials" in tables:
        _drop_index_if_exists(inspector, "helper_app_credentials", "ix_helper_app_credentials_is_active")
        _drop_index_if_exists(inspector, "helper_app_credentials", "ix_helper_app_credentials_api_id")
        op.drop_table("helper_app_credentials")


def _create_index_if_missing(
    inspector: sa.Inspector,
    table_name: str,
    index_name: str,
    columns: list[str],
) -> None:
    existing = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> None:
    existing = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name in existing:
        op.drop_index(index_name, table_name=table_name)
