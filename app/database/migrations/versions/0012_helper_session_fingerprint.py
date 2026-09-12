"""Add session_fingerprint to helper_accounts for duplicate detection."""

from alembic import op
import sqlalchemy as sa

revision = "0012_helper_session_fingerprint"
down_revision = "0011_playback_state_source_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "helper_accounts",
        sa.Column("session_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_helper_accounts_session_fingerprint",
        "helper_accounts",
        ["session_fingerprint"],
        unique=True,
        postgresql_where=sa.text("session_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_helper_accounts_session_fingerprint",
        table_name="helper_accounts",
    )
    op.drop_column("helper_accounts", "session_fingerprint")
