from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils import player_permissions


def _client_with_status(status: str = "member"):
    client = MagicMock()
    client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value=status))
    )
    return client


def _base_global_patches():
    return (
        patch("app.utils.player_permissions.is_developer", return_value=False),
        patch("app.utils.player_permissions.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.sudo_has_permission", AsyncMock(return_value=False)),
    )


def _patched_permissions(*extra_patches):
    stack = ExitStack()
    for patcher in (*_base_global_patches(), *extra_patches):
        stack.enter_context(patcher)
    return stack


def _patch_user_repo_sudo(is_sudo: bool = False, is_owner: bool = False):
    return (
        patch("app.utils.player_permissions.user_repo.is_sudo", AsyncMock(return_value=is_sudo)),
        patch("app.utils.player_permissions.user_repo.is_owner", AsyncMock(return_value=is_owner)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper_name", "music_admin", "deputy_or_above", "expected"),
    [
        ("can_open_group_panel", True, False, True),
        ("can_open_group_panel", False, False, False),
        ("can_manage_call_security", False, True, True),
        ("can_manage_call_security", True, False, False),
    ],
)
async def test_music_admin_panel_vs_call_security_matrix(
    helper_name: str,
    music_admin: bool,
    deputy_or_above: bool,
    expected: bool,
):
    client = _client_with_status("member")
    helper = getattr(player_permissions, helper_name)
    with _patched_permissions(
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=music_admin),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=deputy_or_above),
        ),
    ):
        assert await helper(client, -1010, 7010) is expected


@pytest.mark.asyncio
async def test_sudo_without_auto_admin_bypass_cannot_bypass_group_panel_or_call_security():
    client = _client_with_status("member")
    with _patched_permissions(
        *_patch_user_repo_sudo(is_sudo=True),
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
    ):
        assert await player_permissions.can_open_group_panel(client, -1004, 7004) is False
        assert await player_permissions.can_manage_call_security(client, -1004, 7004) is False


@pytest.mark.asyncio
async def test_sudo_with_auto_admin_bypass_can_bypass_group_panel_and_call_security():
    client = _client_with_status("member")
    with _patched_permissions(
        *_patch_user_repo_sudo(is_sudo=True),
        patch(
            "app.utils.player_permissions.can_use_sudo_admin_bypass",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
    ):
        assert await player_permissions.can_open_group_panel(client, -1004, 7004) is True
        assert await player_permissions.can_manage_call_security(client, -1004, 7004) is True


@pytest.mark.asyncio
async def test_bot_owner_bypasses_group_panel_and_call_security():
    client = _client_with_status("member")
    with _patched_permissions(
        *_patch_user_repo_sudo(is_owner=True),
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
    ):
        assert await player_permissions.can_open_group_panel(client, -1005, 7005) is True
        assert await player_permissions.can_manage_call_security(client, -1005, 7005) is True


@pytest.mark.asyncio
async def test_developer_bypasses_group_panel_and_call_security():
    client = _client_with_status("member")
    with _patched_permissions(
        patch("app.utils.player_permissions.is_developer", return_value=True),
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
    ):
        assert await player_permissions.can_open_group_panel(client, -1006, 7006) is True
        assert await player_permissions.can_manage_call_security(client, -1006, 7006) is True


@pytest.mark.asyncio
async def test_player_deputy_can_open_panel_and_call_security():
    client = _client_with_status("member")
    with _patched_permissions(
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=True),
        ),
    ):
        assert await player_permissions.can_open_group_panel(client, -1007, 7007) is True
        assert await player_permissions.can_manage_call_security(client, -1007, 7007) is True


@pytest.mark.asyncio
async def test_normal_user_denied_panel_and_call_security():
    client = _client_with_status("member")
    with _patched_permissions(
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
        patch("app.utils.player_permissions.admin_repo.is_vip", AsyncMock(return_value=False)),
    ):
        assert await player_permissions.can_open_group_panel(client, -1008, 7008) is False
        assert await player_permissions.can_manage_call_security(client, -1008, 7008) is False


@pytest.mark.asyncio
async def test_telegram_creator_can_open_panel_and_call_security():
    client = _client_with_status("creator")
    with _patched_permissions(
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
    ):
        assert await player_permissions.can_open_group_panel(client, -1001, 7001) is True
        assert await player_permissions.can_manage_call_security(client, -1001, 7001) is True


@pytest.mark.asyncio
async def test_telegram_creator_can_manage_local_ladder_but_not_player_owner():
    client = _client_with_status("creator")
    with _patched_permissions():
        assert await player_permissions.can_open_group_settings(client, -1001, 7001) is True
        assert await player_permissions.can_manage_deputy(client, -1001, 7001) is True
        assert await player_permissions.can_manage_admin(client, -1001, 7001) is True
        assert await player_permissions.can_manage_vip(client, -1001, 7001) is True
        assert await player_permissions.can_manage_player_owner(client, -1001, 7001) is False


@pytest.mark.asyncio
async def test_player_vip_can_play_but_cannot_manage_or_open_settings():
    client = _client_with_status("member")
    with _patched_permissions(
        patch("app.utils.player_permissions.admin_repo.is_player_owner", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.admin_repo.is_player_deputy_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.admin_repo.is_music_admin_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.admin_repo.is_vip", AsyncMock(return_value=True)),
    ):
        assert await player_permissions.can_play_media(client, -1002, 7002, security_enabled=True) is True
        assert await player_permissions.can_open_playback_panel(client, -1002, 7002) is True
        assert await player_permissions.can_open_group_settings(client, -1002, 7002) is False
        assert await player_permissions.can_open_group_panel(client, -1002, 7002) is False
        assert await player_permissions.can_manage_call_security(client, -1002, 7002) is False
        assert await player_permissions.can_manage_vip(client, -1002, 7002) is False
        assert await player_permissions.can_manage_admin(client, -1002, 7002) is False
        assert await player_permissions.can_manage_deputy(client, -1002, 7002) is False


@pytest.mark.asyncio
async def test_music_admin_can_manage_vip_only_below_deputy_boundary():
    client = _client_with_status("member")
    with _patched_permissions(
        patch("app.utils.player_permissions.admin_repo.is_player_owner", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.admin_repo.is_player_deputy_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.admin_repo.is_music_admin_or_above", AsyncMock(return_value=True)),
    ):
        assert await player_permissions.can_manage_vip(client, -1003, 7003) is True
        assert await player_permissions.can_manage_admin(client, -1003, 7003) is False
        assert await player_permissions.can_manage_deputy(client, -1003, 7003) is False
