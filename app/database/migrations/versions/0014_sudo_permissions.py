"""Add per-sudo permission flags to sudos table."""

from alembic import op
import sqlalchemy as sa

revision = "0014_sudo_permissions"
down_revision = "0013_media_events"
branch_labels = None
depends_on = None

_PERMISSION_COLUMNS = (
    "can_manage_groups",
    "can_manage_channels",
    "can_manage_credit",
    "can_remove_bot",
    "can_manage_chat_settings",
    "auto_admin_bypass",
)


def upgrade() -> None:
    for column_name in _PERMISSION_COLUMNS:
        op.add_column(
            "sudos",
            sa.Column(
                column_name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    for column_name in reversed(_PERMISSION_COLUMNS):
        op.drop_column("sudos", column_name)
