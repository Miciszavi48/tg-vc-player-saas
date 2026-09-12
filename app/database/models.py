from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── 0. Database instance ownership ───────────────────────────────────────────

class BotInstanceMetadata(Base):
    """Singleton marker proving that this database belongs to one bot instance."""

    __tablename__ = "bot_instance_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    instance_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_bot_instance_metadata_singleton"),
    )


# ── 1. Group ─────────────────────────────────────────────────────────────────

class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    installed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    credits: Mapped[GroupCredit | None] = relationship(
        "GroupCredit",
        primaryjoin="and_(Group.chat_id == foreign(GroupCredit.chat_id), GroupCredit.chat_type == 'group')",
        uselist=False,
        viewonly=True,
    )
    settings: Mapped[ChatSettings | None] = relationship(
        "ChatSettings",
        primaryjoin="and_(Group.chat_id == foreign(ChatSettings.chat_id), ChatSettings.chat_type == 'group')",
        uselist=False,
        viewonly=True,
    )


# ── 2. Channel ───────────────────────────────────────────────────────────────

class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    installed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_activity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    credits: Mapped[GroupCredit | None] = relationship(
        "GroupCredit",
        primaryjoin="and_(Channel.chat_id == foreign(GroupCredit.chat_id), GroupCredit.chat_type == 'channel')",
        uselist=False,
        viewonly=True,
    )
    settings: Mapped[ChatSettings | None] = relationship(
        "ChatSettings",
        primaryjoin="and_(Channel.chat_id == foreign(ChatSettings.chat_id), ChatSettings.chat_type == 'channel')",
        uselist=False,
        viewonly=True,
    )


# ── 3. GroupCredit ────────────────────────────────────────────────────────────

class GroupCredit(Base):
    __tablename__ = "group_credits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group")
    credit_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    charged_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_charged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_daily_deducted_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint("chat_id", "chat_type", name="uq_group_credits_chat"),
    )


# ── 4. CreditHistory ─────────────────────────────────────────────────────────

class CreditHistory(Base):
    __tablename__ = "credit_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group")
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_days: Mapped[int] = mapped_column(Integer, nullable=False)
    operated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


# ── 5. Invoice ────────────────────────────────────────────────────────────────

class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group")
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    items: Mapped[list[InvoiceItem]] = relationship("InvoiceItem", back_populates="invoice")


# ── 6. InvoiceItem ────────────────────────────────────────────────────────────

class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    item_ref_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount_irr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="items")


# ── 6b. MonthlyInvoice ───────────────────────────────────────────────────────

class MonthlyInvoice(Base):
    __tablename__ = "monthly_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    bot_identifier: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    install_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    private_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    developer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "period_start",
            "period_end",
            name="uq_monthly_invoices_owner_period",
        ),
        Index("idx_monthly_invoices_status_due", "status", "due_at"),
        Index("idx_monthly_invoices_owner_created", "owner_user_id", "created_at"),
    )


# ── 7. User ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    banned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    banned_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── 8. Owner ──────────────────────────────────────────────────────────────────

class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── 9. Sudo ───────────────────────────────────────────────────────────────────

class Sudo(Base):
    __tablename__ = "sudos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    sudo_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_installs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    can_manage_groups: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_manage_channels: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_manage_credit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_remove_bot: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_manage_chat_settings: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_admin_bypass: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    wallet: Mapped[SudoWallet | None] = relationship("SudoWallet", back_populates="sudo", uselist=False)


# ── 10. MusicAdmin ────────────────────────────────────────────────────────────

class MusicAdmin(Base):
    __tablename__ = "music_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_music_admin_chat_user"),
    )


# ── 11. VideoAdmin ────────────────────────────────────────────────────────────

class VideoAdmin(Base):
    __tablename__ = "video_admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_video_admin_chat_user"),
    )


# ── 12. PlayerOwner ──────────────────────────────────────────────────────────

class PlayerOwner(Base):
    __tablename__ = "player_owners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_player_owner_chat_user"),
    )


# ── 13. PlayerDeputy ─────────────────────────────────────────────────────────

class PlayerDeputy(Base):
    __tablename__ = "player_deputies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_player_deputy_chat_user"),
        Index("idx_player_deputies_chat", "chat_id"),
        Index("idx_player_deputies_user_chat", "user_id", "chat_id"),
    )


# ── 14. PlayerVip ─────────────────────────────────────────────────────────────

class PlayerVip(Base):
    __tablename__ = "player_vips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_player_vip_chat_user"),
    )


# ── 15. ChatSettings ─────────────────────────────────────────────────────────

class ChatSettings(Base):
    __tablename__ = "chat_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group")

    # Boolean feature flags
    music_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    video_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    audio_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    file_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    soundcloud_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    spotify_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    inline_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    force_join_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_leave_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    anti_spam_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    security_call_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    download_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lyrics_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    announce_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    buttons_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Additional settings from spec
    vote_skip_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smart_radio_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="fa")
    default_media_type: Mapped[str] = mapped_column(
        String(8), nullable=False, default="audio", server_default="audio"
    )
    show_track_id: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    show_cover: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    show_now_playing_text: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    # Auto-delete the bot's own "playback stopped" notice after a short delay.
    auto_clear_stopped_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Audio-filter preset applied at the transcode stage ("normal" = unfiltered).
    equalizer_preset: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal", server_default="normal"
    )
    # Delete Telegram service messages (joins/leaves/pins) from the group.
    service_clean_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # CALLSEC-01: DM the group owners when voice-chat moderation happens.
    # Distinct from `buttons_enabled`, which the grp:set:call_report button
    # controls (now-playing keyboard extras) despite the similar name.
    call_report_dm_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # CALLMGMT-01: channel whose voice chat this group's playback may target.
    linked_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    linked_channel_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # CALLMGMT-03: route playback to `linked_channel_id` instead of this chat.
    channel_playback_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "chat_type", name="uq_chat_settings_chat"),
    )


# ── 15. Playlist ──────────────────────────────────────────────────────────────

class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stream_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="audio")
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 15b. Named playlists (TRANSPORT-09/10/11) ────────────────────────────────
# Distinct from `Playlist` above, which is the transient per-chat play queue.

class NamedPlaylist(Base):
    __tablename__ = "named_playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("chat_id", "name", name="uq_named_playlists_chat_name"),
    )


class NamedPlaylistItem(Base):
    __tablename__ = "named_playlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("named_playlists.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(2048), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="audio")
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 15c. Hot Seat voice game (HOTSEAT-01/02/03) ──────────────────────────────

class HotSeatGame(Base):
    __tablename__ = "hot_seat_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # "joining" -> members may join; "game" -> running; "ended" -> terminal.
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="joining")
    # "all" (anyone may join) or "private" (creator-curated guest list).
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_hot_seat_games_chat_state", "chat_id", "state"),
    )


class HotSeatGuest(Base):
    __tablename__ = "hot_seat_guests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hot_seat_games.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("game_id", "user_id", name="uq_hot_seat_guests_game_user"),
    )


# ── 16. Favorite ──────────────────────────────────────────────────────────────

class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stream_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="audio")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 16b. CallSecuritySettings ────────────────────────────────────────────────

class CallSecuritySettings(Base):
    """Per-chat Call Security (امنیت کال) configuration."""

    __tablename__ = "call_security_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    owner_access_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    mute_incoming_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    summary_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    report_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    membership_age_days: Mapped[int] = mapped_column(
        Integer, default=7, server_default=text("7"), nullable=False
    )
    # Legacy 0024 column; runtime semantics use membership_age_days only.
    account_age_days: Mapped[int] = mapped_column(
        Integer, default=7, server_default=text("7"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ── 16c. GroupMemberMembership ───────────────────────────────────────────────

class GroupMemberMembership(Base):
    """Best-known group membership timestamps observed by the bot."""

    __tablename__ = "group_member_memberships"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="manual_unknown",
        server_default=text("'manual_unknown'"),
    )

    __table_args__ = (
        Index("ix_group_member_memberships_chat_id", "chat_id"),
        Index("ix_group_member_memberships_user_id", "user_id"),
        Index("ix_group_member_memberships_joined_at", "joined_at"),
    )


# ── 16d. YouTube cookie-session pool ─────────────────────────────────────────

class YoutubeCookieSession(Base):
    """Encrypted, global yt-dlp cookie jar managed by active Owners/Developers."""

    __tablename__ = "youtube_cookie_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cookie_blob_enc: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="disabled")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cookie_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled', 'invalid')",
            name="ck_youtube_cookie_sessions_status",
        ),
        CheckConstraint("use_count >= 0", name="ck_youtube_cookie_sessions_use_count"),
        CheckConstraint("failure_count >= 0", name="ck_youtube_cookie_sessions_failure_count"),
        Index(
            "idx_youtube_cookie_sessions_rotation",
            "status",
            "cooldown_until",
            "last_selected_at",
            "id",
        ),
        Index(
            "idx_youtube_cookie_sessions_expiry",
            "status",
            "cookie_expires_at",
            "id",
        ),
    )


class YoutubeCookieSessionEvent(Base):
    """Secret-free audit trail for YouTube cookie-session operations."""

    __tablename__ = "youtube_cookie_session_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        Index("idx_youtube_cookie_session_events_session_at", "session_id", "occurred_at"),
    )


# ── 16e. Fast-Creat API-token pool ──────────────────────────────────────────

class FastCreatApiToken(Base):
    """Encrypted global Fast-Creat token selected fairly per provider."""

    __tablename__ = "fast_creat_api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default=text("'active'"),
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('instagram', 'tiktok', 'spotify')",
            name="ck_fast_creat_api_tokens_provider",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'invalid')",
            name="ck_fast_creat_api_tokens_status",
        ),
        CheckConstraint("use_count >= 0", name="ck_fast_creat_api_tokens_use_count"),
        CheckConstraint("failure_count >= 0", name="ck_fast_creat_api_tokens_failure_count"),
        UniqueConstraint("provider", "fingerprint", name="uq_fast_creat_api_tokens_provider_fingerprint"),
        Index(
            "idx_fast_creat_api_tokens_rotation",
            "provider",
            "status",
            "cooldown_until",
            "last_selected_at",
            "id",
        ),
    )


class FastCreatApiTokenEvent(Base):
    """Secret-free audit trail for Fast-Creat token lifecycle and use."""

    __tablename__ = "fast_creat_api_token_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('instagram', 'tiktok', 'spotify')",
            name="ck_fast_creat_api_token_events_provider",
        ),
        Index("idx_fast_creat_api_token_events_token_at", "token_id", "occurred_at"),
    )


# ── 17. BotSetting ────────────────────────────────────────────────────────────

class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ── 17b. OwnerTextLink ───────────────────────────────────────────────────────

class OwnerTextLink(Base):
    __tablename__ = "owner_text_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint("owner_user_id", "key", name="uq_owner_text_links_owner_key"),
    )


# ── 17c. StartCustomizationMessage ───────────────────────────────────────────

class StartCustomizationMessage(Base):
    __tablename__ = "start_customization_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_chat_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('global', 'owner')",
            name="ck_start_custom_msg_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'global' AND scope_owner_user_id = 0) "
            "OR (scope_type = 'owner' AND scope_owner_user_id > 0)",
            name="ck_start_custom_msg_scope_owner",
        ),
        CheckConstraint(
            "category IN ('start', 'ability', 'test', 'use', 'history', 'note')",
            name="ck_start_custom_msg_category",
        ),
        CheckConstraint("weight > 0", name="ck_start_custom_msg_weight_positive"),
        Index(
            "idx_start_custom_messages_scope_category_active",
            "scope_type",
            "scope_owner_user_id",
            "category",
            "is_active",
        ),
        Index(
            "idx_start_custom_messages_source",
            "source_chat_id",
            "source_message_id",
        ),
    )


# ── 17d. StartButtonConfig ───────────────────────────────────────────────────

class StartButtonConfig(Base):
    __tablename__ = "start_button_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    slot_key: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    custom_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_token: Mapped[str | None] = mapped_column(String(1), nullable=True)
    emoji_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji_entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_owner_user_id",
            "slot_key",
            name="uq_start_button_configs_scope_slot",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'owner')",
            name="ck_start_button_configs_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'global' AND scope_owner_user_id = 0) "
            "OR (scope_type = 'owner' AND scope_owner_user_id > 0)",
            name="ck_start_button_configs_scope_owner",
        ),
        CheckConstraint(
            "slot_key IN ('purchase', 'test', 'use', 'history', 'ability', "
            "'commands', 'support', 'note')",
            name="ck_start_button_configs_slot_key",
        ),
        CheckConstraint(
            "slot_index BETWEEN 1 AND 8",
            name="ck_start_button_configs_slot_index",
        ),
        CheckConstraint(
            "color_token IS NULL OR color_token IN ('R', 'G', 'B', 'N')",
            name="ck_start_button_configs_color",
        ),
        Index(
            "idx_start_button_configs_scope",
            "scope_type",
            "scope_owner_user_id",
        ),
    )


# ── 17e. StartStyleConfig ────────────────────────────────────────────────────

class StartStyleConfig(Base):
    __tablename__ = "start_style_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    style_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="simple")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_owner_user_id",
            name="uq_start_style_configs_scope",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'owner')",
            name="ck_start_style_configs_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'global' AND scope_owner_user_id = 0) "
            "OR (scope_type = 'owner' AND scope_owner_user_id > 0)",
            name="ck_start_style_configs_scope_owner",
        ),
        CheckConstraint(
            "style_mode IN ('simple', 'advanced')",
            name="ck_start_style_configs_style_mode",
        ),
    )


# ── 18. ForceJoinChannel ─────────────────────────────────────────────────────

class ForceJoinChannel(Base):
    __tablename__ = "force_join_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    channel_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="channel")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", index=True)
    verify_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ── 32. Broadcast ────────────────────────────────────────────────────────────

class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_types_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    filter_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_admin_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_admin_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


# ── 19. FilterWord ────────────────────────────────────────────────────────────

class FilterWord(Base):
    __tablename__ = "filter_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 20. Blacklist ─────────────────────────────────────────────────────────────

class Blacklist(Base):
    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    unblocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ── 20b. GlobalBan ────────────────────────────────────────────────────────────

class GlobalBan(Base):
    """Developer-managed global user ban (ban-all / بن آل)."""

    __tablename__ = "global_bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ── 21. InstallLog ────────────────────────────────────────────────────────────

class InstallLog(Base):
    __tablename__ = "install_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group")
    triggered_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sudo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 22. CallReport ────────────────────────────────────────────────────────────

class CallReport(Base):
    __tablename__ = "call_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="audio")
    played_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    helper_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("helper_accounts.id", ondelete="SET NULL"), nullable=True,
    )

    __table_args__ = (
        Index("idx_call_reports_helper_account_id", "helper_account_id"),
    )


# ── 23. HelperAccount ─────────────────────────────────────────────────────────

class HelperAccount(Base):
    __tablename__ = "helper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_string_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    current_active_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_joins_per_hour: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    joins_last_hour: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    system_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lang_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    proxy_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    proxy_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proxy_password: Mapped[str | None] = mapped_column(String(128), nullable=True)

    bindings: Mapped[list[HelperChatBinding]] = relationship("HelperChatBinding", back_populates="helper")

    __table_args__ = (
        Index(
            "uq_helper_accounts_session_fingerprint",
            "session_fingerprint",
            unique=True,
            postgresql_where=text("session_fingerprint IS NOT NULL"),
        ),
    )


# ── 24. HelperAppCredential ───────────────────────────────────────────────────

class HelperAppCredential(Base):
    __tablename__ = "helper_app_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash_enc: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_helper_app_credentials_is_active", "is_active"),
        Index("ix_helper_app_credentials_api_id", "api_id"),
    )


# ── 25. HelperDeviceProfile ───────────────────────────────────────────────────

class HelperDeviceProfile(Base):
    __tablename__ = "helper_device_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_model: Mapped[str] = mapped_column(String(128), nullable=False)
    system_version: Mapped[str] = mapped_column(String(64), nullable=False)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    lang_code: Mapped[str] = mapped_column(String(8), nullable=False, default="en", server_default="en")
    system_lang_code: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en-US", server_default="en-US",
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "device_model",
            "system_version",
            "app_version",
            name="uq_helper_device_profiles_identity",
        ),
        Index("ix_helper_device_profiles_is_active", "is_active"),
    )


# ── 26. HelperChatBinding ─────────────────────────────────────────────────────

class HelperChatBinding(Base):
    __tablename__ = "helper_chat_bindings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    helper_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("helper_accounts.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    bound_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    binding_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="bound")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    helper: Mapped[HelperAccount] = relationship("HelperAccount", back_populates="bindings")


# ── 27. PlaybackState ─────────────────────────────────────────────────────────

class PlaybackState(Base):
    __tablename__ = "playback_states"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    helper_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("helper_accounts.id", ondelete="SET NULL"), nullable=True,
    )
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="audio")
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queue_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    seek_sec: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_update_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        Index("idx_playback_states_helper", "helper_account_id"),
        Index("idx_playback_states_last_update", "last_update_at"),
    )


# ── 26. SudoWallet ────────────────────────────────────────────────────────────

class SudoWallet(Base):
    __tablename__ = "sudo_wallets"

    sudo_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sudos.user_id", ondelete="CASCADE"), primary_key=True,
    )
    balance_toman: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    locked_toman: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    sudo: Mapped[Sudo] = relationship("Sudo", back_populates="wallet")
    transactions: Mapped[list[SudoWalletTransaction]] = relationship(
        "SudoWalletTransaction", back_populates="wallet",
    )


# ── 27. SudoWalletTransaction ─────────────────────────────────────────────────

class SudoWalletTransaction(Base):
    __tablename__ = "sudo_wallet_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sudo_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sudo_wallets.sudo_user_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    amount_toman: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    wallet: Mapped[SudoWallet] = relationship("SudoWallet", back_populates="transactions")


# ── 28. SudoBulkInvoice ───────────────────────────────────────────────────────

class SudoBulkInvoice(Base):
    __tablename__ = "sudo_bulk_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sudo_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    amount_toman: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    issued_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


# ── 29. OwnerSale ─────────────────────────────────────────────────────────────

class OwnerSale(Base):
    __tablename__ = "owner_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sudo_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_irr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payment_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 30. InstallPolicySetting ─────────────────────────────────────────────────

class InstallPolicySetting(Base):
    __tablename__ = "install_policy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    policy_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    charge_on_install: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    group_install_fee_irr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chan_install_fee_irr: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


# ── 31. FreeInstallWhitelist ──────────────────────────────────────────────────

class FreeInstallWhitelist(Base):
    __tablename__ = "free_install_whitelist"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── 33. AnalyticsHourly ──────────────────────────────────────────────────────

class AnalyticsHourly(Base):
    __tablename__ = "analytics_hourly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    hour: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("day", "hour", "scope", "scope_key", "metric",
                         name="uq_analytics_hourly_composite"),
        Index("ix_analytics_day_scope", "day", "scope", "scope_key"),
        Index("ix_analytics_day_metric", "day", "metric"),
        Index("ix_analytics_day_hour_metric", "day", "hour", "metric"),
    )


# ── 34. HelperEvent ──────────────────────────────────────────────────────────

class HelperEvent(Base):
    __tablename__ = "helper_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, server_default="cli")
    helper_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )


# ── 35. MediaEvent ────────────────────────────────────────────────────────────

class MediaEvent(Base):
    """Append-only media play/download events for developer URL ranking."""

    __tablename__ = "media_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    url_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    redacted_url: Mapped[str] = mapped_column(String(512), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, default="audio")
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    __table_args__ = (
        Index("ix_media_events_type_created", "event_type", "created_at"),
        Index("ix_media_events_fingerprint_type", "url_fingerprint", "event_type"),
    )
