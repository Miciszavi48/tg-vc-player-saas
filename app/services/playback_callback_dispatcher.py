from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable


class PlaybackCallbackAction(str, Enum):
    """Supported playback inline-control actions."""

    VOL_DOWN = "vol_down"
    VOL_UP = "vol_up"
    SPEED_DOWN = "speed_down"
    SPEED_UP = "speed_up"
    PREV = "prev"
    NEXT = "next"
    REPEAT = "repeat"
    FAV_ADD = "fav_add"
    FAV_PLAY = "fav_play"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


CALLBACK_TO_ACTION: dict[str, PlaybackCallbackAction] = {
    "pb:vol:-": PlaybackCallbackAction.VOL_DOWN,
    "pb:vol:+": PlaybackCallbackAction.VOL_UP,
    "pb:speed:-": PlaybackCallbackAction.SPEED_DOWN,
    "pb:speed:+": PlaybackCallbackAction.SPEED_UP,
    "pb:prev": PlaybackCallbackAction.PREV,
    "pb:next": PlaybackCallbackAction.NEXT,
    "pb:repeat": PlaybackCallbackAction.REPEAT,
    "pb:fav:add": PlaybackCallbackAction.FAV_ADD,
    "pb:fav:play": PlaybackCallbackAction.FAV_PLAY,
    "pb:pause": PlaybackCallbackAction.PAUSE,
    "pb:resume": PlaybackCallbackAction.RESUME,
    "pb:stop": PlaybackCallbackAction.STOP,
}

EXPECTED_CALLBACKS: tuple[str, ...] = tuple(CALLBACK_TO_ACTION.keys())


def playback_controls_regex() -> str:
    """Build a Pyrogram ``filters.regex`` pattern for all now-playing controls."""
    return "^(" + "|".join(re.escape(cb) for cb in EXPECTED_CALLBACKS) + ")$"


@dataclass(frozen=True, slots=True)
class ParsedPlaybackCallback:
    """Parsed callback payload."""

    raw: str
    action: PlaybackCallbackAction


def parse_playback_callback(data: str | None) -> ParsedPlaybackCallback | None:
    """Parse ``pb:*`` callback_data into a typed action.

    Returns ``None`` for unknown/invalid payloads.
    """
    if not data:
        return None
    action = CALLBACK_TO_ACTION.get(data)
    if action is None:
        return None
    return ParsedPlaybackCallback(raw=data, action=action)


def expected_callbacks_match(cb: dict[str, str]) -> bool:
    """Return True when UI callback constants exactly match dispatcher expectations."""
    expected = {
        cb["PB_VOL_DOWN"],
        cb["PB_VOL_UP"],
        cb["PB_SPEED_DOWN"],
        cb["PB_SPEED_UP"],
        cb["PB_PREV"],
        cb["PB_NEXT"],
        cb["PB_REPEAT_TOGGLE"],
        cb["PB_FAV_ADD"],
        cb["PB_FAV_PLAY"],
        cb["PB_PAUSE"],
        cb["PB_RESUME"],
        cb["PB_STOP"],
    }
    return expected == set(EXPECTED_CALLBACKS)


async def dispatch_playback_callback(
    parsed: ParsedPlaybackCallback,
    handlers: dict[
        PlaybackCallbackAction,
        Callable[..., Awaitable[None]],
    ],
    *args,
    **kwargs,
) -> bool:
    """Dispatch a parsed callback to its action handler.

    Returns False when no handler exists for the action.
    """
    handler = handlers.get(parsed.action)
    if handler is None:
        return False
    await handler(*args, **kwargs)
    return True
