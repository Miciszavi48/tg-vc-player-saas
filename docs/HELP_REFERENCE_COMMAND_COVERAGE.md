# Help Reference Command Coverage

This file tracks the Help-to-runtime command sync. Customer-provided Help copy (2026-06-20) and `app/resources/i18n/*/system/help.json` are the canonical user-facing Help text sources. `help.md` remains a legacy VarDump reference for command-name classification tests. Runtime aliases preserve old names while accepting the Help names where the underlying feature exists. Missing features are not faked.

- [Synced command mappings](./HELP_REFERENCE_COMMAND_MAPPINGS.md)
- [Missing / later-phase commands](./HELP_REFERENCE_MISSING_COMMANDS.md)

## Summary

| Category | Count | Notes |
|---|---:|---|
| Required Help pages represented | 20 | Home, playback, public, promote, utility, and detail pages remain represented in Help UI/i18n. |
| Routed/synced concepts | 41 | Existing playback/search/TV/radio/satellite/role/VIP/public commands accept canonical Help names. |
| Implemented with limitations | 2 | Speed and seek use finite trusted local/cached media only; live/uncached URL sources return unsupported feedback. |
| Placeholder concepts | 1 | Serial returns coming-soon and does not start playback/search/queue. |
| Help-listed, not implemented | 0 | No non-placeholder Help command remains without a runtime route. |
| Fully missing no-route commands | 0 | No remaining Help command is silently swallowed by generic playback. |

## Routed Command Inventory

| Help area | Commands | Runtime status | Source evidence |
|---|---|---|---|
| Generic play | `پخش`, `Play` | implemented | `app/utils/playback_commands.py`, `app/handlers/playback.py` |
| Auto Music | `پخش خودکار موزیک`, `Play Auto Music` | implemented | `app/handlers/playback.py`, `app/handlers/search.py` |
| Auto Video | `پخش خودکار ویدئو`, `Play Auto Video` | implemented | `app/handlers/playback.py`, `app/handlers/search.py`, `app/services/media_service.py` |
| Playback controls | `توقف پخش`, `Stop Play`, `مکث پلیر`, `Pause Play`, `ازسرگیری`, `Resume Play`, `پخش بیصدا`, `Mute Play`, `پخش باصدا`, `UnMute Play` | implemented | `app/handlers/playback.py` |
| Speed controls | `کاهش سرعت`, `Speed Down`, `افزایش سرعت`, `Speed Up` | implemented with media limitations | `app/handlers/playback.py`, `app/handlers/callbacks.py`, `app/services/call_service.py`, `app/services/transcode_pool.py` |
| Volume controls | `کاهش صدا`, `Volume-`, `افزایش صدا`, `Volume+`, `تنظیم صدا`, `Set Volume` | implemented | `app/handlers/playback.py`, `app/handlers/callbacks.py` |
| Seek controls | `جلو`, `Front`, `عقب`, `Back` | implemented with media limitations | `app/handlers/playback.py`, `app/services/call_service.py`, `app/services/transcode_pool.py` |
| Call controls | `شروع کال`, `Start Call`, `پایان کال`, `End Call`, `پایان کال 25`, `End Call 25`, `پایان کال N`, `End Call N`, `لینک کال`, `Link Call` | implemented | `app/utils/group_text_commands.py`, `app/handlers/group_text_call_commands.py` |
| Radio/Satellite | `پخش رادیو`, `Radio Play`, `پخش ماهواره`, `Satellite Play` | implemented as menu-entry text commands | `app/handlers/tv_radio.py` |
| TV | `پخش تیوی`, `پخش تلویزیون`, `Tv Play` | implemented | `app/handlers/playback.py` |
| Public group/user | `شناسه گروه`, `Group id`, `شناسه کانال`, `Channel id`, `وضعیت پلیر`, `Status Player`, `وضعیت نمایش شناسه عکس`, `Show Id Status Photo`, `وضعیت نمایش شناسه ساده`, `Show Id Status Simple`, `آیدی`, `Id` | implemented | `app/handlers/group_panel.py`, `app/repositories/settings_repo.py` |
| Public channel ID | `شناسه کانال`, `Channel id` | implemented | `app/handlers/group_panel.py` | Replies with the current channel ID in channels; in groups/supergroups it returns the current chat ID with an honest current-chat label; PMs receive unsupported feedback. |
| Public expire | `اعتبار پلیر`, `Expire Player` | implemented | `app/utils/manager_text_commands.py`, `app/handlers/manager_text_commands.py` |
| VIP roles | `ارتقا ویژه پلیر`, `SetVip Player`, `عزل ویژه پلیر`, `RemVip Player`, `لیست ویژه پلیر`, `ListVip Player`, `پاکسازی لیست ویژه پلیر`, `ClearListVip Player` | implemented | `app/handlers/promotion.py`, `app/handlers/group_panel.py`, `app/repositories/admin_repo.py` |
| Manager/Admin roles | `ارتقا مقام پلیر`, `Promote Player`, `عزل مقام پلیر`, `Demote Player`, `لیست مدیران پلیر`, `ListAdmin Player`, `پاکسازی لیست مدیران پلیر`, `ClearListAdmin Player`, `پیکربندی پلیر`, `Config Player` | implemented | `app/utils/manager_text_commands.py`, `app/handlers/manager_text_commands.py` |
| Deputy roles | `ارتقا معاون پلیر`, `SetDeputy Player`, `عزل معاون پلیر`, `RemDeputy Player`, `لیست معاونان پلیر`, `ListDeputy Player`, `پاکسازی لیست معاونان پلیر`, `ClearListDeputy Player` | implemented | `app/utils/manager_text_commands.py`, `app/handlers/manager_text_commands.py` |

## Limited Implementations

| Feature | Status | Limitation |
|---|---|---|
| Speed Up/Down | implemented with media limitations | Supports trusted finite local media and already cached finite URL/YouTube originals. Uncached direct URLs, live streams, radio, satellite, TV, and unknown sources return unsupported feedback. Stream replacement resumes near the estimated position; it is not sample-exact seamless playback. |
| Front/Back seek | implemented with media limitations | Supports the same finite source model as speed. No-argument commands use 10 seconds; numeric arguments use the requested seconds. Unsupported sources return safe feedback and do not use queue next/previous. |
| Set Volume | implemented with strict range validation | `تنظیم صدا` / `Set Volume` require a numeric value from 1 to 200 after the command. Missing, nonnumeric, and out-of-range values return usage feedback without mutation. |
| End Call | implemented as immediate or scheduled end | Bare `پایان کال` / `End Call` immediately ends/leaves the active voice call. `پایان کال 25` / `End Call 25` are documented examples of the numeric scheduled form (`پایان کال N` / `End Call N`). Invalid suffixes are rejected without mutation. |
| Link Call | implemented as public group utility | `لینک کال` / `Link Call` are intentionally public within managed groups, but still require group context, an active call, and an exportable public group/channel link. |
| Radio/Satellite | implemented as menu-entry text commands | Text commands open existing selection menus only. Direct station/channel-name text playback remains out of scope. |
| VIP clear | implemented with confirmation | Text command opens a user-bound confirmation; confirmation re-checks permission and clears only VIP rows. |

## Placeholder

| Help area | Commands | Status | Notes |
|---|---|---|---|
| Serial | `پخش سریال`, `Serial Play` | coming-soon placeholder | No real serial playback feature exists yet. The exact text commands return a safe coming-soon message and do not start playback/search/queue. Help text preserves customer copy plus an explicit coming-soon note. |

## Utility Help page

| Help section | Help page key | Runtime status | Notes |
|---|---|---|---|
| Utility / کاربردی | `help.pages.utility` | informational only | Copy now describes the real group-panel settings features: call security, playback lock, downloads, cleanup, reports/notices, queue, helper presence, default media, language, call stats, and ID/cover/text display. No standalone text commands; toggles remain in `/settings` / group panel. |
