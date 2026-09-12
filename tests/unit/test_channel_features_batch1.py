"""Regression coverage for Batch 1 of the channel-feature backlog.

Covers ROLE-01 (owner aliases), ROLE-04 (VIP grant expiry), ROLE-06 (creator
auto-owner), ROLE-07 (group datacenter), MISC-02 (inactive id mode), MISC-06
(auto-clear stopped notice), MISC-07 (equalizer), CALLSEC-02 (reset cadence),
CALLSEC-03 (call-message aliases), CALLMGMT-04 (public-only call link) and
TRANSPORT-08 (repeat text command).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.utils.group_text_commands import GroupTextCommandType, parse_group_text_command
from app.utils.manager_text_commands import parse_duration_token, parse_manager_text_command


# ── ROLE-01: evidenced owner-role command strings ────────────────────────────

@pytest.mark.parametrize(
    ("text", "family"),
    [
        ("ارتقا مالک پلیر", "owner_add"),
        ("SetOwner Player", "owner_add"),
        ("عزل مالک پلیر", "owner_remove"),
        ("RemOwner Player", "owner_remove"),
        ("لیست مالکان پلیر", "owner_list"),
        ("ListOwner Player", "owner_list"),
        ("پاکسازی لیست مالکان پلیر", "owner_clear"),
    ],
)
def test_evidenced_owner_aliases_route_to_owner_family(text: str, family: str) -> None:
    parsed = parse_manager_text_command(text)
    assert parsed is not None, f"{text!r} is not recognised"
    assert parsed.family == family


def test_pre_existing_owner_aliases_still_route() -> None:
    """The historical wording must keep working (backward compatibility)."""
    for text, family in (
        ("افزودن مالک پلیر", "owner_add"),
        ("حذف مالک پلیر", "owner_remove"),
        ("لیست مالک پلیر", "owner_list"),
    ):
        parsed = parse_manager_text_command(text)
        assert parsed is not None and parsed.family == family


# ── ROLE-04: VIP grant duration ──────────────────────────────────────────────

@pytest.mark.parametrize(
    ("token", "expected", "explicit"),
    [
        ("30", "30d", False),
        ("30d", "30d", True),
        ("7 روز", "7d", True),
        ("12h", "12h", True),
        ("6 ساعت", "6h", True),
        ("abc", None, False),
    ],
)
def test_parse_duration_token(token: str, expected: str | None, explicit: bool) -> None:
    assert parse_duration_token(token) == (expected, explicit)


def test_vip_add_splits_target_and_duration() -> None:
    parsed = parse_manager_text_command("ارتقا ویژه پلیر @someuser 30")
    assert parsed is not None
    assert parsed.family == "vip_add"
    assert parsed.target_user == "@someuser"
    assert parsed.duration == "30d"


def test_vip_add_bare_number_stays_a_user_id() -> None:
    """A bare number is ambiguous, so it must keep meaning 'target user id'."""
    parsed = parse_manager_text_command("ارتقا ویژه پلیر 12345")
    assert parsed is not None
    assert parsed.target_user == "12345"
    assert parsed.duration is None


def test_vip_add_unit_suffixed_lone_token_is_a_duration() -> None:
    parsed = parse_manager_text_command("ارتقا ویژه پلیر 30d")
    assert parsed is not None
    assert parsed.target_user is None
    assert parsed.duration == "30d"


def test_other_role_families_ignore_duration_splitting() -> None:
    parsed = parse_manager_text_command("ارتقا معاون پلیر 12345")
    assert parsed is not None
    assert parsed.family == "deputy_add"
    assert parsed.target_user == "12345"
    assert parsed.duration is None


def test_duration_to_expires_at_maps_days_and_hours() -> None:
    from app.services.manager_command_service import duration_to_expires_at

    assert duration_to_expires_at(None) is None
    assert duration_to_expires_at("0d") is None

    before = datetime.now(timezone.utc)
    got = duration_to_expires_at("2d")
    assert got is not None
    assert timedelta(days=2) - (got - before) < timedelta(seconds=5)

    got_hours = duration_to_expires_at("6h")
    assert got_hours is not None
    assert timedelta(hours=6) - (got_hours - before) < timedelta(seconds=5)


# ── ROLE-06: Telegram creator detection ──────────────────────────────────────

@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SimpleNamespace(value="creator"), True),
        (SimpleNamespace(value="owner"), True),
        (SimpleNamespace(value="administrator"), False),
        ("creator", True),
        ("administrator", False),
        (None, False),
    ],
)
def test_is_chat_creator(status, expected: bool) -> None:
    from app.services.manager_command_service import _is_chat_creator

    assert _is_chat_creator(SimpleNamespace(status=status)) is expected


def test_is_chat_creator_accepts_kurigram_enum() -> None:
    from pyrogram.enums import ChatMemberStatus

    from app.services.manager_command_service import _is_chat_creator

    assert _is_chat_creator(SimpleNamespace(status=ChatMemberStatus.OWNER)) is True
    assert _is_chat_creator(SimpleNamespace(status=ChatMemberStatus.ADMINISTRATOR)) is False


# ── ROLE-07: group datacenter lookup ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_chat_dc_id_returns_value_and_falls_back() -> None:
    from app.services.user_info_formatter_service import resolve_chat_dc_id

    client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(dc_id=4)))
    assert await resolve_chat_dc_id(client, -100123, "fa") == "4"

    failing = SimpleNamespace(get_chat=AsyncMock(side_effect=RuntimeError("boom")))
    label = await resolve_chat_dc_id(failing, -100123, "fa")
    assert label and label != "4"

    no_dc = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(dc_id=None)))
    assert await resolve_chat_dc_id(no_dc, -100123, "fa") == label


# ── MISC-02: inactive id-display mode ────────────────────────────────────────

def test_inactive_is_a_valid_id_output_mode() -> None:
    from app.repositories import id_command_settings_repo as repo

    assert "inactive" in repo._VALID_OUTPUT_MODES
    assert {"simple", "photo"} <= repo._VALID_OUTPUT_MODES


@pytest.mark.asyncio
async def test_inactive_mode_suppresses_group_id_reply() -> None:
    from app.services import user_info_formatter_service as svc

    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type=SimpleNamespace(value="supergroup")),
        reply=AsyncMock(),
        reply_photo=AsyncMock(),
        from_user=SimpleNamespace(id=7, username="u", first_name="U"),
        reply_to_message=None,
        entities=[],
        text="آیدی",
    )
    await svc.reply_user_info(
        SimpleNamespace(),
        message,
        output_mode="inactive",
        include_call_stats=False,
        lang="fa",
    )
    message.reply.assert_not_awaited()
    message.reply_photo.assert_not_awaited()


# ── CALLSEC-03 / TRANSPORT-08 / MISC-06 / MISC-07 / CALLSEC-02 parsing ───────

@pytest.mark.parametrize(
    "text",
    ["پیام کال فعال", "مسیج کال فعال", "کامنت کال فعال", "Call message active", "Call comment active"],
)
def test_all_evidenced_call_message_aliases_toggle_call_comment(text: str) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command is GroupTextCommandType.CALL_COMMENT
    assert parsed.mode is True


@pytest.mark.parametrize(
    ("text", "command", "mode"),
    [
        ("تکرار فعال", GroupTextCommandType.REPEAT, True),
        ("تکرار غیرفعال", GroupTextCommandType.REPEAT, False),
        ("Repeat active", GroupTextCommandType.REPEAT, True),
        ("Repaet inactive", GroupTextCommandType.REPEAT, False),
        ("پاکسازی خودکار فعال", GroupTextCommandType.AUTO_CLEAR, True),
        ("Clearauto inactive", GroupTextCommandType.AUTO_CLEAR, False),
    ],
)
def test_new_toggle_commands_parse(text: str, command, mode: bool) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command is command
    assert parsed.mode is mode


@pytest.mark.parametrize(
    ("text", "cadence"),
    [
        ("ریست آمار کال روزانه", "daily"),
        ("ریست آمار کال ماهیانه", "monthly"),
        ("ریست امار کال ماهانه", "monthly"),
        ("Reset stats call daily", "daily"),
        ("Reset stats call monthly", "monthly"),
    ],
)
def test_reset_cadence_parses(text: str, cadence: str) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command is GroupTextCommandType.RESET_CALL_STATS
    assert parsed.cadence == cadence
    assert parsed.error is None


def test_reset_cadence_rejects_missing_and_invalid_values() -> None:
    assert parse_group_text_command("ریست آمار کال").error == "missing_cadence"
    assert parse_group_text_command("ریست آمار کال هفتگی").error == "invalid_cadence"


def test_reset_command_does_not_shadow_the_stats_panel_command() -> None:
    """'آمار کال' must still open the stats panel, not the reset command."""
    for text in ("آمار کال", "امار کال"):
        parsed = parse_group_text_command(text)
        assert parsed is not None
        assert parsed.command is GroupTextCommandType.CALL_STATS_PANEL
    auto = parse_group_text_command("آمار خودکار کال فعال")
    assert auto is not None and auto.command is GroupTextCommandType.AUTO_CALL_STATS


@pytest.mark.parametrize("text", ["اکولایزر", "Equalizer"])
def test_equalizer_command_parses(text: str) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command is GroupTextCommandType.EQUALIZER


# ── MISC-07: equalizer filter chain ──────────────────────────────────────────

def test_equalizer_presets_are_a_closed_literal_set() -> None:
    from app.services.group_text_call_command_service import EQUALIZER_PRESETS
    from app.services.transcode_pool import EQUALIZER_FILTERS, normalize_equalizer

    assert set(EQUALIZER_PRESETS) == set(EQUALIZER_FILTERS)
    assert EQUALIZER_FILTERS["normal"] == ""
    # Unknown / hostile input can never reach the FFmpeg command line.
    assert normalize_equalizer("bogus") == "normal"
    assert normalize_equalizer(None) == "normal"
    assert normalize_equalizer("; rm -rf /") == "normal"
    assert normalize_equalizer("BASSBOOST") == "bassboost"


def test_equalizer_changes_the_speed_cache_key() -> None:
    """Two presets must not collide on one cached derivative file."""
    from pathlib import Path
    from unittest.mock import patch

    from app.services import transcode_pool

    src = Path("media/song.mp3")
    fake_stat = SimpleNamespace(st_mtime_ns=1, st_size=2)
    with patch.object(Path, "stat", lambda _self: fake_stat), patch.object(
        transcode_pool, "_speed_cache_dir", lambda: Path("cache")
    ):
        normal = transcode_pool._speed_output_path(
            src, 120, media_type="audio", start_at_seconds=0, equalizer="normal"
        )
        bass = transcode_pool._speed_output_path(
            src, 120, media_type="audio", start_at_seconds=0, equalizer="bassboost"
        )
    assert normal != bass


# ── MISC-06: auto-clear of the playback-stopped notice ───────────────────────

@pytest.mark.asyncio
async def test_stop_notice_deletes_only_when_auto_clear_is_enabled() -> None:
    from unittest.mock import patch

    from app.utils import stop_notice

    sent = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(reply=AsyncMock(return_value=sent))

    with patch.object(stop_notice, "is_auto_clear_enabled", AsyncMock(return_value=False)):
        await stop_notice.reply_stop_notice(message, "stopped", -100123)
    await asyncio.sleep(0)
    sent.delete.assert_not_awaited()

    with patch.object(stop_notice, "is_auto_clear_enabled", AsyncMock(return_value=True)):
        await stop_notice.reply_stop_notice(message, "stopped", -100123)
    # Zero-delay drain instead of really waiting the production 10 seconds.
    stop_notice.schedule_auto_clear(sent, delay=0)
    await asyncio.sleep(0.05)
    sent.delete.assert_awaited()


# ── TRANSPORT-08: repeat is rejected for live sources ────────────────────────

@pytest.mark.asyncio
async def test_repeat_requires_an_active_finite_track() -> None:
    from unittest.mock import patch

    from app.services import group_text_call_command_service as svc

    with patch.object(svc.CallService, "is_chat_playing", staticmethod(lambda _c: False)):
        assert (await svc.set_repeat(-100123, True)).reason == "no_active_call"

    with patch.object(svc.CallService, "is_chat_playing", staticmethod(lambda _c: True)), \
         patch.object(
             svc.CallService,
             "get_active_calls",
             staticmethod(lambda: {-100123: {"playback_feature": "radio"}}),
         ):
        result = await svc.set_repeat(-100123, True)
    assert result.ok is False
    assert result.reason == "repeat_live_unsupported"


@pytest.mark.asyncio
async def test_repeat_toggles_session_state_for_finite_media() -> None:
    from unittest.mock import patch

    from app.services import group_text_call_command_service as svc

    seen: dict[int, bool] = {}
    with patch.object(svc.CallService, "is_chat_playing", staticmethod(lambda _c: True)), \
         patch.object(
             svc.CallService,
             "get_active_calls",
             staticmethod(lambda: {-100123: {"playback_feature": "youtube"}}),
         ), \
         patch.object(
             svc.CallService,
             "set_repeat_state",
             staticmethod(lambda chat_id, enabled: seen.__setitem__(chat_id, enabled)),
         ):
        result = await svc.set_repeat(-100123, True)
    assert result.ok is True
    assert seen == {-100123: True}


# ── MISC-01: bare-emoji playback shortcuts ───────────────────────────────────

def test_all_fourteen_evidenced_emoji_are_mapped() -> None:
    from app.handlers.playback import _EMOJI_PLAYBACK_ACTIONS, normalize_playback_emoji

    evidenced = "▶️ ⏸ ⏯ ⏹ ⏮ ⏭ 🔇 🔉 🔊 🔈 📻 📺 📡 🎞".split()
    assert len(evidenced) == 14
    assert len(_EMOJI_PLAYBACK_ACTIONS) == 14
    for emoji in evidenced:
        assert normalize_playback_emoji(emoji) is not None, emoji


def test_emoji_normalization_tolerates_variation_selector_and_spacing() -> None:
    from app.handlers.playback import normalize_playback_emoji

    assert normalize_playback_emoji("⏸") == normalize_playback_emoji("⏸️")
    assert normalize_playback_emoji("  ⏹  ") == "⏹"


def test_non_emoji_text_is_not_a_playback_shortcut() -> None:
    from app.handlers.playback import normalize_playback_emoji

    for text in ("hello", "", None, "▶ play", "🙂", "پخش"):
        assert normalize_playback_emoji(text) is None


# ── CONTENT-03/05: catalog taxonomy step ─────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "slug"),
    [
        ("news", "news"),
        ("classic;music", "classic_music"),
        ("IR", "ir"),
        ("Kids & Family", "kids_family"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_slugify_catalog_group_is_callback_safe(raw, slug: str) -> None:
    import re

    from app.handlers.tv_radio import slugify_catalog_group

    got = slugify_catalog_group(raw)
    assert got == slug
    assert re.fullmatch(r"[a-z0-9_]+", got)


def test_group_catalog_entries_buckets_and_keeps_every_entry() -> None:
    from app.handlers.tv_radio import group_catalog_entries

    entries = [
        {"id": "a", "group": "news"},
        {"id": "b", "group": "movies"},
        {"id": "c", "group": "news"},
        {"id": "d"},  # missing field must still be reachable
    ]
    buckets = group_catalog_entries(entries, "group")
    assert sum(len(v) for v in buckets.values()) == len(entries)
    assert {e["id"] for e in buckets["news"]} == {"a", "c"}
    assert buckets["other"] == [{"id": "d"}]
    # Largest bucket first so the busiest category leads the menu.
    assert list(buckets)[0] == "news"


def test_real_satellite_and_radio_catalogs_group_cleanly() -> None:
    import json
    import re

    from app.handlers.tv_radio import group_catalog_entries

    with open("app/assets/satellite_channels.json", encoding="utf-8") as fh:
        sat = json.load(fh)
    with open("app/assets/radio_stations.json", encoding="utf-8") as fh:
        radio = json.load(fh)

    sat_buckets = group_catalog_entries(sat, "group")
    radio_buckets = group_catalog_entries(radio, "country")
    assert sum(len(v) for v in sat_buckets.values()) == len(sat)
    assert sum(len(v) for v in radio_buckets.values()) == len(radio)
    for slug in (*sat_buckets, *radio_buckets):
        # Every slug has to survive the dispatch regexes unchanged.
        assert re.fullmatch(r"[a-z0-9_]{1,40}", slug), slug
    assert "movies" in sat_buckets
    assert "ir" in radio_buckets


def test_catalog_group_label_falls_back_without_rendering_a_debug_marker() -> None:
    from app.handlers.tv_radio import catalog_group_label

    assert catalog_group_label("fa", "sat_group", "movies")  # real key exists
    fallback = catalog_group_label("fa", "sat_group", "totally_unknown_group")
    assert "missing:" not in fallback
    assert "invalid:" not in fallback
    assert fallback == "Totally Unknown Group"


# ── CONTENT-06 / PANEL-02 ────────────────────────────────────────────────────

def test_thematic_command_reuses_the_world_channel_topic_surface() -> None:
    from app.handlers.tv_radio import _SATELLITE_TEXT_CMDS, _THEMATIC_TEXT_CMDS

    assert "پخش موضوعی" in _THEMATIC_TEXT_CMDS
    assert "Thematic Play" in _THEMATIC_TEXT_CMDS
    # Distinct trigger, shared surface - not a second hardcoded catalog flow.
    assert not set(_THEMATIC_TEXT_CMDS) & set(_SATELLITE_TEXT_CMDS)


def test_support_menu_exposes_the_evidenced_five_link_types() -> None:
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_support_menu("fa")
    callbacks = {b.callback_data for row in kb.inline_keyboard for b in row}
    for key in (
        "GRP_SUDO",
        "GRP_GUIDE_CHANNEL",
        "GRP_SUPPORT_GROUP",
        "GRP_BOT_CHANNEL",
        "GRP_MESSENGER",
    ):
        assert CB[key] in callbacks, key


# ── SEARCH-01 / SEARCH-04 ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("سرچ یوتیوب آهنگ", "آهنگ"),
        ("جستجو یوتیوب آهنگ", "آهنگ"),
        ("Search Youtube song", "song"),
        ("سرچ آهنگ", "آهنگ"),
        ("جستجو آهنگ", "آهنگ"),
        ("Search song", "song"),
    ],
)
def test_all_documented_search_triggers_extract_the_query(text: str, query: str) -> None:
    from app.handlers.search import _extract_search_alias_query

    assert _extract_search_alias_query(text) == query


def test_longer_search_alias_wins_over_the_bare_one() -> None:
    """'سرچ یوتیوب X' must not be read as 'سرچ' with query 'یوتیوب X'."""
    from app.handlers.search import _extract_search_alias_query

    assert _extract_search_alias_query("سرچ یوتیوب X") == "X"
    assert _extract_search_alias_query("Search Youtube X") == "X"


def test_unrelated_text_is_not_a_search_trigger() -> None:
    from app.handlers.search import _extract_search_alias_query

    for text in ("hello", "", None, "پخش آهنگ"):
        assert _extract_search_alias_query(text) is None


@pytest.mark.parametrize(
    ("raw", "rendered"),
    [
        (0, "-"),
        (None, "-"),
        ("not-a-number", "-"),
        (999, "999"),
        (1000, "1K"),
        (1500, "1.5K"),
        (2_000_000, "2M"),
        (3_200_000_000, "3.2B"),
    ],
)
def test_view_count_formatting(raw, rendered: str) -> None:
    from app.handlers.search import _format_view_count

    assert _format_view_count(raw) == rendered


def test_search_download_callback_parses_only_valid_video_ids() -> None:
    from app.handlers.search import _parse_search_download_video_id

    assert _parse_search_download_video_id("search:dl:dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _parse_search_download_video_id("search:dl:short") is None
    assert _parse_search_download_video_id("search:play:dQw4w9WgXcQ") is None
    assert _parse_search_download_video_id("search:dl:../../etc/passwd") is None


# ── SEARCH-06 / SEARCH-07: name-based download ───────────────────────────────

def test_name_download_commands_register_before_the_generic_url_command() -> None:
    """`دانلود موزیک X` must not be swallowed by the generic `دانلود` filter."""
    from app.handlers import download as dl

    class _Recorder:
        def __init__(self) -> None:
            self.names: list[str] = []

        def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
            def _decorator(fn):
                self.names.append(fn.__name__)
                return fn

            return _decorator

        def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
            def _decorator(fn):
                self.names.append(fn.__name__)
                return fn

            return _decorator

    bot = _Recorder()
    dl.register(bot, None)
    order = bot.names
    assert "download_music_by_name" in order
    assert "download_video_by_name" in order
    assert "download_media" in order
    assert order.index("download_music_by_name") < order.index("download_media")
    assert order.index("download_video_by_name") < order.index("download_media")


@pytest.mark.parametrize(
    ("text", "cmds_attr", "expected"),
    [
        ("دانلود موزیک شادمهر", "_DOWNLOAD_MUSIC_CMDS", "شادمهر"),
        ("دانلود آهنگ شادمهر", "_DOWNLOAD_MUSIC_CMDS", "شادمهر"),
        ("Download Music some song", "_DOWNLOAD_MUSIC_CMDS", "some song"),
        ("دانلود ویدیو کلیپ", "_DOWNLOAD_VIDEO_CMDS", "کلیپ"),
        ("دانلود ویدئو کلیپ", "_DOWNLOAD_VIDEO_CMDS", "کلیپ"),
        ("دانلود موزیک", "_DOWNLOAD_MUSIC_CMDS", ""),
    ],
)
def test_name_download_strips_the_command_prefix(text, cmds_attr, expected) -> None:
    from app.handlers import download as dl

    assert dl._strip_command_prefix(text, getattr(dl, cmds_attr)) == expected


@pytest.mark.asyncio
async def test_name_download_requires_a_name() -> None:
    from unittest.mock import patch

    from app.handlers import download as dl

    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=7),
        reply=AsyncMock(),
    )
    with patch.object(dl, "_download_allowed", AsyncMock(return_value=None)), \
         patch("app.handlers.search.resolve_youtube_first_result", AsyncMock()) as resolver:
        await dl._download_by_name(
            SimpleNamespace(), message, query_text="   ", media_type="audio"
        )
    resolver.assert_not_awaited()
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_name_download_reports_when_nothing_is_found() -> None:
    from unittest.mock import patch

    from app.handlers import download as dl

    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=7),
        reply=AsyncMock(return_value=status),
    )
    with patch.object(dl, "_download_allowed", AsyncMock(return_value=None)), \
         patch("app.handlers.search.resolve_youtube_first_result", AsyncMock(return_value=None)):
        await dl._download_by_name(
            SimpleNamespace(), message, query_text="nothing here", media_type="audio"
        )
    status.edit_text.assert_awaited()


# ── PANEL-04: service-message removal ────────────────────────────────────────

@pytest.mark.parametrize(
    "attr",
    [
        "new_chat_members",
        "left_chat_member",
        "new_chat_title",
        "pinned_message",
        "video_chat_started",
        "video_chat_ended",
    ],
)
def test_service_events_are_detected(attr: str) -> None:
    from app.handlers.service_messages import is_service_message

    assert is_service_message(SimpleNamespace(**{attr: object()})) is True


def test_plain_user_messages_are_not_service_messages() -> None:
    from app.handlers.service_messages import is_service_message

    assert is_service_message(SimpleNamespace(text="hello")) is False
    assert is_service_message(SimpleNamespace()) is False
    # Falsy service attributes (empty join list) must not count either.
    assert is_service_message(SimpleNamespace(new_chat_members=[])) is False


@pytest.mark.asyncio
async def test_service_messages_deleted_only_when_toggle_is_on() -> None:
    from unittest.mock import patch

    from app.handlers import service_messages as svc

    class _Recorder:
        def __init__(self) -> None:
            self.handlers: list = []

        def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
            def _decorator(fn):
                self.handlers.append(fn)
                return fn

            return _decorator

    bot = _Recorder()
    svc.register(bot, None)
    handler = bot.handlers[0]

    def _message():
        return SimpleNamespace(
            chat=SimpleNamespace(id=-100123),
            new_chat_members=[object()],
            delete=AsyncMock(),
            continue_propagation=lambda: None,
        )

    off = _message()
    with patch.object(
        svc.settings_repo,
        "get_chat_settings",
        AsyncMock(return_value=SimpleNamespace(service_clean_enabled=False)),
    ):
        await handler(SimpleNamespace(), off)
    off.delete.assert_not_awaited()

    on = _message()
    with patch.object(
        svc.settings_repo,
        "get_chat_settings",
        AsyncMock(return_value=SimpleNamespace(service_clean_enabled=True)),
    ):
        await handler(SimpleNamespace(), on)
    on.delete.assert_awaited_once()


def test_service_clean_toggle_is_wired_to_its_persisted_column() -> None:
    from app.database.models import ChatSettings
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP
    from app.utils.ui import CB

    assert _SETTING_TOGGLE_MAP[CB["GRP_SERVICE_CLEAN"]] == "service_clean_enabled"
    assert hasattr(ChatSettings, "service_clean_enabled")


# ── CALLMGMT-01 / CALLMGMT-03: channel link and routing ──────────────────────

@pytest.mark.parametrize(
    ("text", "command", "channel_id", "error"),
    [
        ("تنظیم کانال -1001234567890", "set_channel", -1001234567890, None),
        ("تنظیم کانال پلیر -1001234567890", "set_channel", -1001234567890, None),
        ("Set Channel -1001234567890", "set_channel", -1001234567890, None),
        ("تنظیم کانال", "set_channel", None, "missing_channel"),
        ("تنظیم کانال abc", "set_channel", None, "invalid_channel"),
    ],
)
def test_set_channel_parsing(text, command, channel_id, error) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command.value == command
    assert parsed.target_chat_id == channel_id
    assert parsed.error == error


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("پخش کانال فعال", True),
        ("پخش کانال غیرفعال", False),
        ("Play channel active", True),
        ("Play channel inactive", False),
    ],
)
def test_channel_playback_toggle_parsing(text: str, mode: bool) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command is GroupTextCommandType.CHANNEL_PLAYBACK
    assert parsed.mode is mode


def test_channel_commands_do_not_leak_into_the_generic_play_parser() -> None:
    """'پخش کانال فعال' must not be played as a track named 'کانال فعال'."""
    from app.utils.playback_commands import _NON_GENERIC_PLAY_COMMAND_PREFIXES

    assert "پخش کانال" in _NON_GENERIC_PLAY_COMMAND_PREFIXES
    assert "پخش موضوعی" in _NON_GENERIC_PLAY_COMMAND_PREFIXES
    # A real play query is still generic.
    assert parse_group_text_command("پخش آهنگ من") is None


@pytest.mark.asyncio
async def test_channel_link_requires_full_admin_in_both_chats() -> None:
    from unittest.mock import patch

    from app.services import group_text_call_command_service as svc

    client = SimpleNamespace()
    with patch.object(svc, "_bot_is_full_admin", AsyncMock(return_value=False)):
        result = await svc.link_playback_channel(client, -100111, -100222)
    assert result.ok is False
    assert result.reason == "not_admin_here"

    # Admin in the group but not in the channel must also be rejected.
    calls = {"n": 0}

    async def _admin_here_only(_client, chat_id):
        calls["n"] += 1
        return calls["n"] == 1

    client = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(id=-100222, title="Ch"))
    )
    with patch.object(svc, "_bot_is_full_admin", _admin_here_only):
        result = await svc.link_playback_channel(client, -100111, -100222)
    assert result.ok is False
    assert result.reason == "not_admin_channel"


@pytest.mark.asyncio
async def test_channel_playback_toggle_requires_an_existing_link() -> None:
    from unittest.mock import patch

    from app.repositories import settings_repo
    from app.services import group_text_call_command_service as svc

    with patch.object(
        settings_repo,
        "get_chat_settings",
        AsyncMock(return_value=SimpleNamespace(linked_channel_id=None)),
    ):
        result = await svc.set_channel_playback(-100111, True)
    assert result.ok is False
    assert result.reason == "no_channel_link"


@pytest.mark.asyncio
async def test_playback_target_falls_back_to_the_group() -> None:
    from unittest.mock import patch

    from app.repositories import settings_repo
    from app.services import group_text_call_command_service as svc

    async def _target(row):
        with patch.object(settings_repo, "get_chat_settings", AsyncMock(return_value=row)):
            return await svc.resolve_playback_target_chat(-100111)

    # Routing off -> group.
    assert await _target(
        SimpleNamespace(channel_playback_enabled=False, linked_channel_id=-100222)
    ) == -100111
    # Routing on but link removed -> group, never a stale channel id.
    assert await _target(
        SimpleNamespace(channel_playback_enabled=True, linked_channel_id=None)
    ) == -100111
    # Routing on with a live link -> channel.
    assert await _target(
        SimpleNamespace(channel_playback_enabled=True, linked_channel_id=-100222)
    ) == -100222


# ── TRANSPORT-09/10/11: named playlists ──────────────────────────────────────

def test_playlist_name_normalisation_and_validation() -> None:
    from app.repositories import named_playlist_repo as repo
    from app.services.named_playlist_service import validate_name

    assert repo.normalize_name("  my   list ") == "my list"
    assert validate_name("  my   list ") == ("my list", None)
    assert validate_name("   ")[1] == "missing_name"
    assert validate_name("x" * (repo.MAX_NAME_LENGTH + 1))[1] == "name_too_long"


def test_named_playlist_command_argument_and_rename_split() -> None:
    from app.handlers.playlist import (
        _NP_CREATE_CMDS,
        _NP_RENAME_CMDS,
        _command_argument,
        _split_rename_argument,
    )

    assert _command_argument("ساخت لیست favourites", _NP_CREATE_CMDS) == "favourites"
    assert _command_argument("Create Playlist my list", _NP_CREATE_CMDS) == "my list"
    assert _command_argument("ساخت لیست", _NP_CREATE_CMDS) == ""
    rest = _command_argument("تغییر نام لیست old | new", _NP_RENAME_CMDS)
    assert _split_rename_argument(rest) == ("old", "new")
    assert _split_rename_argument("old => new") == ("old", "new")
    assert _split_rename_argument("only-old") == ("only-old", "")


def test_named_playlist_commands_registered_before_queue_commands() -> None:
    """'ساخت لیست'/'لیست ها' must not fall through to the queue handlers."""
    from app.handlers import playlist as pl

    class _Recorder:
        def __init__(self) -> None:
            self.names: list[str] = []

        def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
            def _decorator(fn):
                self.names.append(fn.__name__)
                return fn

            return _decorator

        def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
            def _decorator(fn):
                self.names.append(fn.__name__)
                return fn

            return _decorator

    bot = _Recorder()
    pl.register(bot, None)
    for named in ("np_create", "np_rename", "np_delete", "np_list_all"):
        assert named in bot.names
    assert bot.names.index("np_create") < bot.names.index("add_to_playlist")
    assert bot.names.index("np_list_all") < bot.names.index("add_to_playlist")


@pytest.mark.asyncio
async def test_named_playlist_create_rejects_duplicates_and_quota() -> None:
    from unittest.mock import patch

    from app.services import named_playlist_service as svc

    with patch.object(svc.repo, "get_playlist", AsyncMock(return_value=object())):
        result = await svc.create(-100123, "dupe")
    assert (result.ok, result.reason) == (False, "already_exists")

    with patch.object(svc.repo, "get_playlist", AsyncMock(return_value=None)), \
         patch.object(
             svc.repo, "count_playlists",
             AsyncMock(return_value=svc.repo.MAX_PLAYLISTS_PER_CHAT),
         ):
        result = await svc.create(-100123, "another")
    assert (result.ok, result.reason) == (False, "too_many_playlists")


@pytest.mark.asyncio
async def test_named_playlist_add_and_play_report_missing_and_empty() -> None:
    from unittest.mock import patch

    from app.services import named_playlist_service as svc

    with patch.object(svc.repo, "get_playlist", AsyncMock(return_value=None)):
        assert (await svc.add_media(-1, "gone", source="s", title=None)).reason == "not_found"
        assert (await svc.items_for_play(-1, "gone")).reason == "not_found"

    playlist = SimpleNamespace(id=7)
    with patch.object(svc.repo, "get_playlist", AsyncMock(return_value=playlist)), \
         patch.object(svc.repo, "get_items", AsyncMock(return_value=[])):
        assert (await svc.items_for_play(-1, "empty")).reason == "playlist_empty"

    with patch.object(svc.repo, "get_playlist", AsyncMock(return_value=playlist)), \
         patch.object(
             svc.repo, "count_items",
             AsyncMock(return_value=svc.repo.MAX_ITEMS_PER_PLAYLIST),
         ):
        assert (await svc.add_media(-1, "full", source="s", title=None)).reason == "playlist_full"


# ── CALLSEC-01: owner-DM call report ─────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("گزارش کال فعال", True),
        ("گزارش کال غیرفعال", False),
        ("Call report active", True),
        ("Call report inactive", False),
    ],
)
def test_call_report_toggle_parsing(text: str, mode: bool) -> None:
    parsed = parse_group_text_command(text)
    assert parsed is not None
    assert parsed.command is GroupTextCommandType.CALL_REPORT
    assert parsed.mode is mode


def test_call_report_does_not_shadow_the_stats_commands() -> None:
    assert parse_group_text_command("آمار کال").command is GroupTextCommandType.CALL_STATS_PANEL
    assert (
        parse_group_text_command("ریست آمار کال روزانه").command
        is GroupTextCommandType.RESET_CALL_STATS
    )


def test_call_report_dm_column_is_distinct_from_the_buttons_toggle() -> None:
    """The evidenced feature must not reuse the unrelated same-named toggle."""
    from app.database.models import ChatSettings
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP
    from app.utils.ui import CB

    assert hasattr(ChatSettings, "call_report_dm_enabled")
    # grp:set:call_report still drives buttons_enabled (now-playing extras).
    assert _SETTING_TOGGLE_MAP[CB["GRP_CALL_REPORT"]] == "buttons_enabled"


@pytest.mark.asyncio
async def test_owner_dm_only_when_enabled_and_never_raises() -> None:
    from unittest.mock import patch

    from app.repositories import admin_repo, settings_repo
    from app.services import group_text_call_command_service as svc

    owners = [SimpleNamespace(user_id=1), SimpleNamespace(user_id=2)]

    # Disabled -> no DM at all.
    client = SimpleNamespace(send_message=AsyncMock())
    with patch.object(
        settings_repo,
        "get_chat_settings",
        AsyncMock(return_value=SimpleNamespace(call_report_dm_enabled=False)),
    ):
        sent = await svc.notify_owners_of_moderation(
            client, -100123, action="mute", target_user_id=9, actor_user_id=5
        )
    assert sent == 0
    client.send_message.assert_not_awaited()

    # Enabled -> one DM per owner.
    client = SimpleNamespace(send_message=AsyncMock())
    with patch.object(
        settings_repo,
        "get_chat_settings",
        AsyncMock(return_value=SimpleNamespace(call_report_dm_enabled=True)),
    ), patch.object(admin_repo, "get_player_owners", AsyncMock(return_value=owners)):
        sent = await svc.notify_owners_of_moderation(
            client, -100123, action="mute", target_user_id=9, actor_user_id=5
        )
    assert sent == 2

    # A blocked owner must not break the moderation action.
    client = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("blocked")))
    with patch.object(
        settings_repo,
        "get_chat_settings",
        AsyncMock(return_value=SimpleNamespace(call_report_dm_enabled=True)),
    ), patch.object(admin_repo, "get_player_owners", AsyncMock(return_value=owners)):
        sent = await svc.notify_owners_of_moderation(
            client, -100123, action="unmute", target_user_id=9, actor_user_id=None
        )
    assert sent == 0


# ── HOTSEAT-01/02/03 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rest", "count", "mode"),
    [
        ("", 5, "all"),
        ("10", 10, "all"),
        ("10 خصوصی", 10, "private"),
        ("private 7", 7, "private"),
        ("عمومی", 5, "all"),
    ],
)
def test_hot_seat_create_argument_parsing(rest: str, count: int, mode: str) -> None:
    from app.handlers.hot_seat import parse_create_arguments

    assert parse_create_arguments(rest) == (count, mode)


@pytest.mark.asyncio
async def test_hot_seat_creation_is_blocked_by_channel_playback_mode() -> None:
    """HOTSEAT-01's evidenced blocking condition maps to CALLMGMT-03's flag."""
    from unittest.mock import patch

    from app.services import hot_seat_service as svc

    with patch.object(svc, "_channel_playback_active", AsyncMock(return_value=True)):
        result = await svc.create_game(-100123, question_count=5)
    assert (result.ok, result.reason) == (False, "channel_mode_blocked")


@pytest.mark.asyncio
async def test_hot_seat_creation_validates_mode_and_count() -> None:
    from app.services import hot_seat_service as svc

    assert (await svc.create_game(-1, question_count=5, mode="bogus")).reason == "invalid_mode"
    assert (await svc.create_game(-1, question_count=0)).reason == "invalid_question_count"
    assert (
        await svc.create_game(-1, question_count=svc.MAX_QUESTIONS + 1)
    ).reason == "invalid_question_count"


@pytest.mark.asyncio
async def test_hot_seat_guest_rules_follow_the_evidence() -> None:
    from unittest.mock import patch

    from app.services import hot_seat_service as svc

    # No game at all.
    with patch.object(svc, "get_active_game", AsyncMock(return_value=None)):
        assert (await svc.add_guest(-1, 9, actor_id=1)).reason == "no_active_game"
        assert (await svc.remove_guest(-1, 9)).reason == "no_active_game"

    public = SimpleNamespace(id=1, mode="all", state="joining", created_by=1)
    with patch.object(svc, "get_active_game", AsyncMock(return_value=public)):
        assert (await svc.add_guest(-1, 9, actor_id=1)).reason == "not_private_mode"

    running = SimpleNamespace(id=1, mode="private", state="game", created_by=1)
    with patch.object(svc, "get_active_game", AsyncMock(return_value=running)):
        assert (await svc.add_guest(-1, 9, actor_id=1)).reason == "not_joining_state"
        # Removal is also state-guarded.
        assert (await svc.remove_guest(-1, 9)).reason == "not_joining_state"

    joining = SimpleNamespace(id=1, mode="private", state="joining", created_by=1)
    with patch.object(svc, "get_active_game", AsyncMock(return_value=joining)):
        assert (await svc.add_guest(-1, 9, actor_id=2)).reason == "creator_only"


def test_hot_seat_state_machine_constants() -> None:
    from app.services import hot_seat_service as svc

    assert svc.CANCELLABLE_STATES == (svc.STATE_JOINING, svc.STATE_GAME)
    assert svc.STATE_ENDED not in svc.CANCELLABLE_STATES


def test_hot_seat_module_is_registered() -> None:
    from app.handlers import _MODULES, hot_seat

    assert hot_seat in _MODULES


# ── PANEL-05: unified per-user panel ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_panel_target_resolution() -> None:
    from app.handlers.user_panel import resolve_target_user_id

    reply = SimpleNamespace(reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=42)))
    assert await resolve_target_user_id(SimpleNamespace(), reply, "") == 42

    plain = SimpleNamespace(reply_to_message=None)
    assert await resolve_target_user_id(SimpleNamespace(), plain, "777") == 777
    assert await resolve_target_user_id(SimpleNamespace(), plain, "") is None
    assert await resolve_target_user_id(SimpleNamespace(), plain, "not-an-id") is None

    client = SimpleNamespace(get_users=AsyncMock(return_value=SimpleNamespace(id=99)))
    assert await resolve_target_user_id(client, plain, "@someuser") == 99


@pytest.mark.asyncio
async def test_user_panel_aggregates_and_gates_actions() -> None:
    from unittest.mock import patch

    from app.handlers import user_panel as up

    async def _panel(*, is_group, deputy, sudo, banned=False, admin=False):
        with patch.object(up.admin_repo, "is_music_admin", AsyncMock(return_value=admin)),              patch.object(up.admin_repo, "is_player_deputy_or_above", AsyncMock(return_value=deputy)),              patch.object(up.global_ban_repo, "is_globally_banned", AsyncMock(return_value=banned)),              patch(
                 "app.services.user_info_formatter_service.resolve_role_label",
                 AsyncMock(return_value="member"),
             ),              patch("app.utils.sudo_permissions.can_use_sudo_admin_bypass", AsyncMock(return_value=sudo)):
            return await up.build_user_panel(
                SimpleNamespace(), target_id=5, chat_id=-100123, actor_id=1, is_group=is_group
            )

    # Group + deputy + sudo: both sections offered.
    text, markup = await _panel(is_group=True, deputy=True, sudo=True)
    cbs = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert any(c.startswith("up:promote:") for c in cbs)
    assert any(c.startswith("up:ban:") for c in cbs)
    assert "5" in text

    # Group, no privileges at all: aggregate view only, no action buttons.
    _text, markup = await _panel(is_group=True, deputy=False, sudo=False)
    assert markup is None

    # Private context: promote/demote is group-scoped, so it is not offered.
    _text, markup = await _panel(is_group=False, deputy=True, sudo=True)
    cbs = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert not any(c.startswith("up:promote:") for c in cbs)
    assert any(c.startswith("up:ban:") for c in cbs)

    # An already-banned user is offered unban instead of ban.
    _text, markup = await _panel(is_group=False, deputy=False, sudo=True, banned=True)
    cbs = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert any(c.startswith("up:unban:") for c in cbs)


def test_user_panel_callback_shape_rejects_malformed_payloads() -> None:
    from app.handlers.user_panel import USER_PANEL_CB_RE

    assert USER_PANEL_CB_RE.match("up:promote:12345")
    assert USER_PANEL_CB_RE.match("up:unban:1")
    assert not USER_PANEL_CB_RE.match("up:delete:12345")
    assert not USER_PANEL_CB_RE.match("up:promote:abc")
    assert not USER_PANEL_CB_RE.match("up:promote:")


# ── CALLSEC-02: cadence rollover job ─────────────────────────────────────────

def test_call_stats_reset_bucket_labels() -> None:
    from app.scheduler import call_stats_reset_bucket

    moment = datetime(2026, 3, 15, 12, 30, tzinfo=timezone.utc)
    daily_label, daily_start = call_stats_reset_bucket("daily", moment)
    monthly_label, monthly_start = call_stats_reset_bucket("monthly", moment)

    assert len(daily_label) == len("YYYY-MM-DD")
    assert len(monthly_label) == len("YYYY-MM")
    assert daily_start <= moment
    assert monthly_start <= daily_start


@pytest.mark.asyncio
async def test_reset_cadence_first_run_anchors_without_hiding_history() -> None:
    """The first observation records the bucket but must not bump the watermark."""
    from unittest.mock import patch

    from app import scheduler as sched

    watermarks: dict[int, datetime] = {}
    buckets: dict[int, str] = {}

    fake = SimpleNamespace(
        chat_ids_with_reset_cadence=AsyncMock(return_value={-100123: "daily"}),
        get_last_reset_bucket=AsyncMock(side_effect=lambda cid: buckets.get(cid)),
        set_last_reset_bucket=AsyncMock(
            side_effect=lambda cid, b: buckets.__setitem__(cid, b)
        ),
        set_reset_watermark=AsyncMock(
            side_effect=lambda cid, m: watermarks.__setitem__(cid, m)
        ),
    )
    with patch.dict(
        "sys.modules", {"app.repositories.call_stats_settings_repo": fake}
    ), patch("app.repositories.call_stats_settings_repo", fake, create=True):
        import app.repositories as repos

        original = getattr(repos, "call_stats_settings_repo", None)
        repos.call_stats_settings_repo = fake  # type: ignore[attr-defined]
        try:
            rolled = await sched.apply_call_stats_reset_cadence(
                datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
            )
            assert rolled == 0
            assert watermarks == {}
            assert buckets

            # A later bucket is a real transition and must bump the watermark.
            rolled = await sched.apply_call_stats_reset_cadence(
                datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
            )
            assert rolled == 1
            assert -100123 in watermarks

            # Re-running inside the same bucket is idempotent.
            rolled = await sched.apply_call_stats_reset_cadence(
                datetime(2026, 3, 16, 18, 0, tzinfo=timezone.utc)
            )
            assert rolled == 0
        finally:
            if original is not None:
                repos.call_stats_settings_repo = original  # type: ignore[attr-defined]
