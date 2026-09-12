"""Add safe PostgreSQL indexes for hot read paths.

Revision ID: 0017_safe_hot_path_indexes
Revises: 0016_global_bans
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op

revision = "0017_safe_hot_path_indexes"
down_revision = "0016_global_bans"
branch_labels = None
depends_on = None


_UPGRADE_SQL = (
    # Scheduler restore: broadcast_repo.get_pending_scheduled().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_broadcasts_pending_run_at
    ON broadcasts (run_at)
    WHERE status = 'pending' AND run_at IS NOT NULL
    """,
    # Early message guard: blacklist_repo.is_blacklisted().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_blacklist_active_entity
    ON blacklist (entity_type, entity_id)
    WHERE is_active IS TRUE
    """,
    # Trial expiry scheduler: scheduler.check_trial_expiry().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_group_credits_active_trial_expire
    ON group_credits (trial_expire_at)
    WHERE is_trial IS TRUE
      AND status = 'active'
      AND trial_expire_at IS NOT NULL
    """,
    # Force-join target cache/admin pages: force_join_repo active target queries.
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_fjc_active_position
    ON force_join_channels (position, id)
    WHERE is_active IS TRUE
    """,
    # Developer global-ban list pages: global_ban_repo.list_global_bans().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_global_bans_active_created
    ON global_bans (created_at DESC, id)
    WHERE is_active IS TRUE
    """,
    # Auto-leave notification lookup: CreditService.auto_leave_check().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_install_logs_chat_action_time
    ON install_logs (chat_id, action, occurred_at DESC)
    """,
    # Owner sales panel: owner_panel.own_sales_report().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_owner_sales_owner_created
    ON owner_sales (owner_user_id, created_at DESC)
    """,
    # Broadcast users scope with 7d/30d filters: BroadcastServiceV2._get_recipients().
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_not_banned_last_seen
    ON users (last_seen DESC, user_id)
    WHERE is_banned IS FALSE
    """,
)

_INDEX_NAMES = (
    "idx_broadcasts_pending_run_at",
    "idx_blacklist_active_entity",
    "idx_group_credits_active_trial_expire",
    "idx_fjc_active_position",
    "idx_global_bans_active_created",
    "idx_install_logs_chat_action_time",
    "idx_owner_sales_owner_created",
    "idx_users_not_banned_last_seen",
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        # SQLite test schemas are created from models; PostgreSQL-only
        # CONCURRENTLY/partial-index DDL is intentionally skipped there.
        return

    with op.get_context().autocommit_block():
        for sql in _UPGRADE_SQL:
            op.execute(sql)


def downgrade() -> None:
    if not _is_postgresql():
        return

    with op.get_context().autocommit_block():
        for name in reversed(_INDEX_NAMES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
