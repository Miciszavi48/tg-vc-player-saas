from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest

from app.services.playback_callback_dispatcher import (
    CALLBACK_TO_ACTION,
    PlaybackCallbackAction,
    dispatch_playback_callback,
    expected_callbacks_match,
    parse_playback_callback,
    playback_controls_regex,
)
from app.utils.ui import CB, KeyboardFactory


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pb:vol:-", PlaybackCallbackAction.VOL_DOWN),
        ("pb:vol:+", PlaybackCallbackAction.VOL_UP),
        ("pb:speed:-", PlaybackCallbackAction.SPEED_DOWN),
        ("pb:speed:+", PlaybackCallbackAction.SPEED_UP),
        ("pb:prev", PlaybackCallbackAction.PREV),
        ("pb:next", PlaybackCallbackAction.NEXT),
        ("pb:repeat", PlaybackCallbackAction.REPEAT),
        ("pb:fav:add", PlaybackCallbackAction.FAV_ADD),
        ("pb:fav:play", PlaybackCallbackAction.FAV_PLAY),
        ("pb:pause", PlaybackCallbackAction.PAUSE),
        ("pb:resume", PlaybackCallbackAction.RESUME),
        ("pb:stop", PlaybackCallbackAction.STOP),
    ],
)
def test_parse_playback_callback_all_expected_actions(raw: str, expected: PlaybackCallbackAction):
    parsed = parse_playback_callback(raw)
    assert parsed is not None
    assert parsed.raw == raw
    assert parsed.action == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "pb",
        "pb:",
        "pb:unknown",
        "pb:vol",
        "pb:vol:++",
        "pb:seek:+10",
        "pb:seek:-10",
        "pb:replay",
        "pb:restart",
        "pb:fav",
        "pb:fav:list",
        "grp:settings",
        None,
    ],
)
def test_parse_playback_callback_rejects_invalid(raw: str | None):
    assert parse_playback_callback(raw) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", list(CALLBACK_TO_ACTION.keys()))
async def test_dispatch_routes_each_callback_to_its_exact_handler(raw: str):
    parsed = parse_playback_callback(raw)
    assert parsed is not None

    handlers = {action: AsyncMock() for action in PlaybackCallbackAction}
    ok = await dispatch_playback_callback(parsed, handlers, "query")

    assert ok is True
    for action, handler in handlers.items():
        if action == parsed.action:
            handler.assert_awaited_once_with("query")
        else:
            handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_unknown_action_handler_returns_false():
    parsed = parse_playback_callback("pb:stop")
    assert parsed is not None
    ok = await dispatch_playback_callback(parsed, {}, "query")
    assert ok is False


def test_ui_callback_constants_match_dispatcher_surface():
    assert expected_callbacks_match(CB) is True


def test_now_playing_keyboard_controls_stay_in_dispatcher_surface():
    kb = KeyboardFactory.now_playing_controls("en", repeat_on=False)
    callbacks = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    }
    expected = set(CALLBACK_TO_ACTION.keys())
    assert callbacks == expected


def test_playback_controls_regex_matches_all_dispatcher_callbacks():
    pattern = playback_controls_regex()
    for raw in CALLBACK_TO_ACTION:
        assert re.fullmatch(pattern, raw) is not None
    assert re.fullmatch(pattern, "pb:unknown") is None


def test_generated_playback_callback_prefixes_are_routable_and_short():
    generated: set[str] = set()

    for kb in (
        KeyboardFactory.playback_type_menu("en"),
        KeyboardFactory.now_playing_controls("en", repeat_on=False),
        KeyboardFactory.tv_channels_menu("en", [{"id": "tv_1", "name": "TV One"}]),
        KeyboardFactory.satellite_channels_menu(
            "en",
            [{"id": f"sat_{idx}", "name": f"Sat {idx}"} for idx in range(9)],
            page=0,
            per_page=8,
        ),
    ):
        generated.update(
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if getattr(btn, "callback_data", None)
        )

    generated.update(
        {
            "pb:radio:radio_1",
            "search:play:dQw4w9WgXcQ",
            CB["PB_FAV_LIST"],
            f"{CB['PAGE_FAV']}1",
            f"{CB['FAV_RM_PREFIX']}12",
            f"{CB['FAV_INFO_PREFIX']}12",
        }
    )

    routable_patterns = [
        playback_controls_regex(),
        r"^pb:type:audio$",
        r"^pb:type:video$",
        r"^pb:type:tv$",
        r"^pb:type:satellite$",
        r"^pb:type:radio$",
        r"^pb:type:download$",
        r"^pb:tv:[A-Za-z0-9_-]{1,64}$",
        r"^pb:sat:page:\d+$",
        r"^pb:sat:(?!page:)[A-Za-z0-9_-]{1,64}$",
        r"^pb:radio:[A-Za-z0-9_-]{1,64}$",
        r"^search:play:[A-Za-z0-9_-]{11}$",
        r"^pb:fav:list$",
        r"^pg:fav:\d+$",
        r"^fav:rm:\d+$",
        r"^fav:info:\d+$",
        r"^nav:back$",
        r"^wz:home$",
    ]

    for callback_data in generated:
        assert len(callback_data.encode("utf-8")) <= 64
        assert any(re.fullmatch(pattern, callback_data) for pattern in routable_patterns), callback_data

    assert "pb:seek:+10" not in generated
    assert "pb:replay" not in generated
