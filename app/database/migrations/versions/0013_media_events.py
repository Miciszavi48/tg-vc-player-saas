"""Add media_events table for developer URL ranking."""

from alembic import op
import sqlalchemy as sa

revision = "0013_media_events"
down_revision = "0012_helper_session_fingerprint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("url_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("redacted_url", sa.String(length=512), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False, server_default="audio"),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("source_kind", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_events_event_type", "media_events", ["event_type"])
    op.create_index("ix_media_events_url_fingerprint", "media_events", ["url_fingerprint"])
    op.create_index("ix_media_events_host", "media_events", ["host"])
    op.create_index("ix_media_events_created_at", "media_events", ["created_at"])
    op.create_index("ix_media_events_chat_id", "media_events", ["chat_id"])
    op.create_index(
        "ix_media_events_type_created",
        "media_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_media_events_fingerprint_type",
        "media_events",
        ["url_fingerprint", "event_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_events_fingerprint_type", table_name="media_events")
    op.drop_index("ix_media_events_type_created", table_name="media_events")
    op.drop_index("ix_media_events_chat_id", table_name="media_events")
    op.drop_index("ix_media_events_created_at", table_name="media_events")
    op.drop_index("ix_media_events_host", table_name="media_events")
    op.drop_index("ix_media_events_url_fingerprint", table_name="media_events")
    op.drop_index("ix_media_events_event_type", table_name="media_events")
    op.drop_table("media_events")
