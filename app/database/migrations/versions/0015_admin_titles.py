"""Add stored admin title fields for owner and sudo roles."""

from alembic import op
import sqlalchemy as sa

revision = "0015_admin_titles"
down_revision = "0014_sudo_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("owners", sa.Column("admin_title", sa.String(length=64), nullable=True))
    op.add_column("sudos", sa.Column("admin_title", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("sudos", "admin_title")
    op.drop_column("owners", "admin_title")
