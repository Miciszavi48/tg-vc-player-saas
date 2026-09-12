"""Add spec §7 database indexes for hot query paths.

Revision ID: 0002_spec_indexes
Revises: fa2e02e5c03e
Create Date: 2026-02-25
"""
from alembic import op

revision = "0002_spec_indexes"
down_revision = "fa2e02e5c03e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── groups ───────────────────────────────────────────────────────────
    op.create_index("idx_groups_status", "groups", ["status"], if_not_exists=True)
    op.create_index("idx_groups_installed_by", "groups", ["installed_by"], if_not_exists=True)
    op.create_index("idx_groups_installed_at", "groups", ["installed_at"], if_not_exists=True)

    # ── channels ─────────────────────────────────────────────────────────
    op.create_index("idx_channels_status", "channels", ["status"], if_not_exists=True)
    op.create_index("idx_channels_installed_by", "channels", ["installed_by"], if_not_exists=True)

    # ── group_credits ────────────────────────────────────────────────────
    op.create_index("idx_group_credits_charged_by", "group_credits", ["charged_by"], if_not_exists=True)

    # ── credit_history ───────────────────────────────────────────────────
    op.create_index("idx_credit_history_chat_date", "credit_history", ["chat_id", "operated_at"], if_not_exists=True)
    op.create_index("idx_credit_history_operator", "credit_history", ["operated_by", "operated_at"], if_not_exists=True)

    # ── users ────────────────────────────────────────────────────────────
    op.create_index("idx_users_joined", "users", ["joined_at"], if_not_exists=True)

    # ── music_admins / video_admins ──────────────────────────────────────
    op.create_index("idx_music_admins_chat", "music_admins", ["chat_id"], if_not_exists=True)
    op.create_index("idx_music_admins_user_chat", "music_admins", ["user_id", "chat_id"], if_not_exists=True)
    op.create_index("idx_video_admins_chat", "video_admins", ["chat_id"], if_not_exists=True)
    op.create_index("idx_video_admins_user_chat", "video_admins", ["user_id", "chat_id"], if_not_exists=True)

    # ── player_owners / player_vips ──────────────────────────────────────
    op.create_index("idx_player_owners_chat", "player_owners", ["chat_id"], if_not_exists=True)
    op.create_index("idx_player_owners_user_chat", "player_owners", ["user_id", "chat_id"], if_not_exists=True)
    op.create_index("idx_player_vips_chat", "player_vips", ["chat_id"], if_not_exists=True)
    op.create_index("idx_player_vips_user_chat", "player_vips", ["user_id", "chat_id"], if_not_exists=True)

    # ── playlists ────────────────────────────────────────────────────────
    op.create_index("idx_playlists_chat_position", "playlists", ["chat_id", "position"], if_not_exists=True)
    op.create_index("idx_playlists_added_by", "playlists", ["added_by"], if_not_exists=True)

    # ── favorites ────────────────────────────────────────────────────────
    op.create_index("idx_favorites_user_chat", "favorites", ["user_id", "chat_id"], if_not_exists=True)

    # ── install_logs ─────────────────────────────────────────────────────
    op.create_index("idx_install_logs_sudo_date", "install_logs", ["sudo_id", "occurred_at"], if_not_exists=True)
    op.create_index("idx_install_logs_chat", "install_logs", ["chat_id"], if_not_exists=True)
    op.create_index("idx_install_logs_date", "install_logs", ["occurred_at"], if_not_exists=True)

    # ── call_reports ─────────────────────────────────────────────────────
    op.create_index("idx_call_reports_chat_date", "call_reports", ["chat_id", "started_at"], if_not_exists=True)
    op.create_index("idx_call_reports_date", "call_reports", ["started_at"], if_not_exists=True)

    # ── invoices ─────────────────────────────────────────────────────────
    op.create_index("idx_invoices_chat", "invoices", ["chat_id", "issued_at"], if_not_exists=True)
    op.create_index("idx_invoices_issued_by", "invoices", ["issued_by"], if_not_exists=True)

    # ── helper_accounts ──────────────────────────────────────────────────
    op.create_index("idx_helper_accounts_status_calls", "helper_accounts", ["status", "current_active_calls"], if_not_exists=True)
    op.create_index("idx_helper_accounts_last_used", "helper_accounts", ["last_used_at"], if_not_exists=True)

    # ── helper_chat_bindings ─────────────────────────────────────────────
    op.create_index("idx_helper_chat_bindings_helper", "helper_chat_bindings", ["helper_account_id"], if_not_exists=True)

    # ── playback_states ──────────────────────────────────────────────────
    op.create_index("idx_playback_states_helper", "playback_states", ["helper_account_id"], if_not_exists=True)
    op.create_index("idx_playback_states_last_update", "playback_states", ["last_update_at"], if_not_exists=True)

    # ── sudo_wallets ─────────────────────────────────────────────────────
    op.create_index("idx_sudo_wallets_balance", "sudo_wallets", ["balance_toman"], if_not_exists=True)

    # ── sudo_wallet_transactions ─────────────────────────────────────────
    op.create_index("idx_sudo_wallet_tx_sudo_time", "sudo_wallet_transactions", ["sudo_user_id", "created_at"], if_not_exists=True)

    # ── free_install_whitelist ────────────────────────────────────────────
    op.create_index("idx_free_install_whitelist_expires", "free_install_whitelist", ["expires_at"], if_not_exists=True)


def downgrade() -> None:
    indexes = [
        "idx_groups_status", "idx_groups_installed_by", "idx_groups_installed_at",
        "idx_channels_status", "idx_channels_installed_by",
        "idx_group_credits_charged_by",
        "idx_credit_history_chat_date", "idx_credit_history_operator",
        "idx_users_joined",
        "idx_music_admins_chat", "idx_music_admins_user_chat",
        "idx_video_admins_chat", "idx_video_admins_user_chat",
        "idx_player_owners_chat", "idx_player_owners_user_chat",
        "idx_player_vips_chat", "idx_player_vips_user_chat",
        "idx_playlists_chat_position", "idx_playlists_added_by",
        "idx_favorites_user_chat",
        "idx_install_logs_sudo_date", "idx_install_logs_chat", "idx_install_logs_date",
        "idx_call_reports_chat_date", "idx_call_reports_date",
        "idx_invoices_chat", "idx_invoices_issued_by",
        "idx_helper_accounts_status_calls", "idx_helper_accounts_last_used",
        "idx_helper_chat_bindings_helper",
        "idx_playback_states_helper", "idx_playback_states_last_update",
        "idx_sudo_wallets_balance",
        "idx_sudo_wallet_tx_sudo_time",
        "idx_free_install_whitelist_expires",
    ]
    for idx in indexes:
        try:
            op.drop_index(idx)
        except Exception:
            pass
