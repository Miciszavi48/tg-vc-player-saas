"""DB factories for creating test entities."""
from __future__ import annotations


from app.database.models import (
    BotSetting,
    ChatSettings,
    Group,
    GroupCredit,
    MusicAdmin,
    Owner,
    PlayerOwner,
    Sudo,
    SudoWallet,
    User,
    VideoAdmin,
)


def make_user(user_id: int = 11111, **kw) -> User:
    return User(user_id=user_id, username=kw.get("username", "u"), first_name=kw.get("first_name", "U"), **{k: v for k, v in kw.items() if k not in ("username", "first_name")})


def make_group(chat_id: int = -100001, **kw) -> Group:
    return Group(chat_id=chat_id, chat_title=kw.get("chat_title", "G"), status=kw.get("status", "active"))


def make_credit(chat_id: int = -100001, days: int = 30, **kw) -> GroupCredit:
    return GroupCredit(chat_id=chat_id, chat_type=kw.get("chat_type", "group"), credit_days=days, status=kw.get("status", "active"))


def make_owner(user_id: int = 22222, **kw) -> Owner:
    return Owner(
        user_id=user_id,
        added_by=kw.get("added_by", 0),
        is_active=True,
        admin_title=kw.get("admin_title"),
    )


def make_sudo(user_id: int = 33333, **kw) -> Sudo:
    return Sudo(
        user_id=user_id,
        added_by=kw.get("added_by", 0),
        is_active=True,
        display_name=kw.get("display_name", "S"),
        admin_title=kw.get("admin_title"),
        can_manage_groups=kw.get("can_manage_groups", True),
        can_manage_channels=kw.get("can_manage_channels", True),
        can_manage_credit=kw.get("can_manage_credit", True),
        can_remove_bot=kw.get("can_remove_bot", True),
        can_manage_chat_settings=kw.get("can_manage_chat_settings", True),
        auto_admin_bypass=kw.get("auto_admin_bypass", True),
    )


def make_sudo_wallet(sudo_user_id: int = 33333, balance: float = 10000, **kw) -> SudoWallet:
    return SudoWallet(sudo_user_id=sudo_user_id, balance_toman=balance)


def make_music_admin(chat_id: int = -100001, user_id: int = 44444) -> MusicAdmin:
    return MusicAdmin(chat_id=chat_id, user_id=user_id)


def make_video_admin(chat_id: int = -100001, user_id: int = 55555) -> VideoAdmin:
    return VideoAdmin(chat_id=chat_id, user_id=user_id)


def make_player_owner(chat_id: int = -100001, user_id: int = 66666) -> PlayerOwner:
    return PlayerOwner(chat_id=chat_id, user_id=user_id)


def make_chat_settings(chat_id: int = -100001, **kw) -> ChatSettings:
    return ChatSettings(chat_id=chat_id, chat_type=kw.get("chat_type", "group"))


def make_bot_setting(key: str, value: str) -> BotSetting:
    return BotSetting(key=key, value=value)
