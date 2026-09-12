# Bot Commands (implementation truth)

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md) | Related: [playback.md](playback.md) | [../architecture/handler_routing.md](../architecture/handler_routing.md)

Canonical list of **text and slash commands** registered in `app/handlers/`. Persian text is the primary UX; English aliases exist for compatibility. Most group commands match **start of line** via regex (`^(?:command)(?:\s|$)`, case-insensitive).

Admin panels (developer, owner, sudo, helper, analytics, broadcast wizard, force join, call security) use **inline keyboards only** — no text commands.

> Source constants: `playback.py`, `playlist.py`, `promotion.py`, `download.py`, `group_panel.py`, `group_text_call_commands.py`, `manager_text_commands.py`, `credit_commands.py`, `help_center.py`, `add_helper.py`, `broadcast.py`, `start.py`, `callbacks.py`, `search.py`.

## Slash commands (`/command`)

| Command | Handler | Scope | Access | Notes |
| --- | --- | --- | --- | --- |
| `/start` | `start.py` | private | all | Root menu + role panel |
| `/cancel` | `callbacks.py` | all chats | all | Hidden compatibility fallback for active ask/wizard flows; visible prompts use inline Back/Cancel buttons, whose callbacks now clear or return state directly |
| `/play [query]` | `playback.py` | group | playback auth | Audio/video per group default media type |
| `/search <query>` | `search.py` | group | all | YouTube search + inline pick |
| `/settings` | `group_panel.py` | group | music admin+ | Opens the group panel with settings, management, help, and support sections |
| `/panel` | `group_panel.py` | group | music admin+ | Same group panel entry as `/settings` |
| `/addhelper` | `add_helper.py` | group | dev/owner/sudo/music admin+ | Invite helper to group |
| `/broadcast_groups` | `broadcast.py` | private | developer | Copy broadcast to groups |
| `/forward_groups` | `broadcast.py` | private | developer | Forward to groups |
| `/broadcast_channels` | `broadcast.py` | private | developer | Copy to channels |
| `/broadcast_users` | `broadcast.py` | private | developer | Copy to users |
| `/forward_users` | `broadcast.py` | private | developer | Forward to users |

The bot does **not** call Telegram `set_bot_commands`; the table reflects only what handlers register.

User-facing summaries also live in Help Center (`app/resources/i18n/*/system/help.json`) and group panel help (`help_content.*` keys).

## Private (PM) commands

Playback, playlist, group-call, and install commands are **group-only**. Private chat supports:

| Command | Handler | Access | Notes |
| --- | --- | --- | --- |
| `/start` | `start.py` | all | Root menu + role panel |
| `راهنما` / `کمک` / `help` / `/help` | `help_center.py` | all | Help Center; shows `private_commands` section in PM |
| `شارژ موزیک -100… ±N` | `manager_text_commands.py` | dev/owner/sudo credit | Charge a managed group by id from PM |
| `ChargeMusic -100… +N` | same | same | English alias; `+`/`-` and `Unlimit` supported |
| `/broadcast_groups` | `broadcast.py` | developer | Copy broadcast to groups |
| `/forward_groups` | `broadcast.py` | developer | Forward to groups |
| `/broadcast_channels` | `broadcast.py` | developer | Copy to channels |
| `/broadcast_users` | `broadcast.py` | developer | Copy to users |
| `/forward_users` | `broadcast.py` | developer | Forward to users |

Private charge examples:

- `شارژ موزیک -1001234567890 20` — add 20 days to group `-1001234567890`
- `ChargeMusic -1001234567890 -20` — subtract 20 days
- `شارژ پلیر نامحدود` / `ChargePlayer Unlimit` — unlimited credit (group context or PM with group id)

## Playback & controls (`playback.py`)

All commands: **group** only. Requires bot full admin, group credit, and `authorize_playback_action` (call security when enabled).

### Play

| FA | EN | Usage |
| --- | --- | --- |
| `پخش` | `play` | `پخش song`, `پخش https://...` (including one direct SoundCloud track), or reply to media + `پخش`; SoundCloud profiles, sets, and playlists are rejected |
| — | — | `/play` (separate slash handler) |
| `پخش ویدیو` | `playvideo` | `پخش ویدیو link` or reply to video |
| `پخش ریپلای` | `replayreply` | Reply to media + command |
| `پخش تیوی` | `playtv` | Opens TV channel inline menu |

**Dedication** (optional on `پخش` / `play` / `/play`):

- `پخش @user query`
- `پخش 123456789 query`
- `پخش query برای @user`

### Stop / pause / volume

| FA | EN | Action |
| --- | --- | --- |
| `توقف پخش` | `stopmusic` | Leave VC / stop audio |
| `توقف ویدیو` | `stopvideo` | Stop video |
| `توقف تیوی` | `stoptv` | Stop TV |
| `مکث` | `pause` | Pause |
| `ازسرگیری` | `resume` | Resume |
| `بیصدا` | `silent` | Volume = 1 |
| `باصدا` | `unsilent` | Volume = 100 |
| `صدای موزیک` | `musicsound` | `صدای موزیک 50` (number in text) |
| `صدای ویدیو` | `videosound` | `صدای ویدیو 80` |

### Utility

| FA | EN | Action |
| --- | --- | --- |
| `پینگ` | `ping` | Latency check |
| `ربات` | `bot`, `robot` | Bot active message |
| `لیست مالکان` | `creatorslist` | List player owners |

**Not text commands:** next track, repeat, **radio**, **satellite** — inline buttons under now-playing only (`callbacks.py`, `tv_radio.py`). There is no plain-text `ماهواره` or `radio` command.

## Playlist (`playlist.py`)

Group only; music admin+ (or dev/sudo bypass).

| FA | EN | Args |
| --- | --- | --- |
| `افزودن به لیست` | `addtoplaylist` | link or reply (audio/voice/text) |
| `پخش لیست` | `playlist` | start queue playback |
| `توقف لیست` | `stoplist` | stop playlist |
| `لیست پخش` | `listplaylist` | show queue (up to 20) |
| `حذف از لیست` | `delfromplaylist` | `حذف از لیست 3` (position) |
| `پاکسازی لیست پخش` | `cleanplaylist` | clear queue |

## Download & search

| Command | Handler | Scope | Args |
| --- | --- | --- | --- |
| `دانلود` / `download` | `download.py` | group | reply audio/video; a YouTube URL opens the existing audio/video choice; a direct SoundCloud track URL downloads as audio; SoundCloud profiles, sets, and playlists are rejected |
| `/search <query>` | `search.py` | group | slash only (no plain `search` text handler) |

## Group panel (`group_panel.py`)

| FA | EN / slash | Scope | Access |
| --- | --- | --- | --- |
| `تنظیمات` | `settings`, `/settings` | group | music admin+ |
| `پنل` | `/panel` | group | music admin+ |
| `پنل پلیر` | `player panel` | installed chat | music admin+; group chats open the group panel, channel/install contexts route to the player/call-security panel |

### ID / user info commands (`group_panel.py`)

Group only. Output format controlled by `id_command:{chat_id}:output_mode` (`simple` text vs `photo` card) in `id_command_settings_repo` — separate from now-playing `show_track_id` (`grp:set:show_id`).

| FA | EN | Access | Notes |
| --- | --- | --- | --- |
| `شناسه گروه` | `Group id` | group user | Returns group chat id |
| `شناسه کانال` | `Channel id` | group user | Returns linked channel id when applicable |
| `آیدی` | `Id` | group user | User info card; respects output mode |
| `وضعیت نمایش شناسه عکس` | `Show Id Status Photo` | call admin | Sets output mode to `photo` |
| `وضعیت نمایش شناسه ساده` | `Show Id Status Simple` | call admin | Sets output mode to `simple` |

Formatter: `app/services/user_info_formatter_service.py`. Panel toggle `grp:set:id_call_stats` controls whether call-stats appear on ID cards (`id_command:{chat_id}:show_call_stats`).

## Group call text commands (`group_text_call_commands.py`)

Plain text group commands; no `/` prefix. See [group_text_commands.md](group_text_commands.md) for persistence keys and smoke checks.

| FA | EN | Access | Notes |
| --- | --- | --- | --- |
| `دریافت پنل` | `GetPanel` | group user | Sends current playback controls, or a visible empty state |
| `پایان کال`, `بستن کال` | `EndCall`, `DiscardCall` | call admin | **Immediate** end — no minutes suffix; calls `end_call_now()` via raw `phone.DiscardGroupCall` |
| `پایان کال N`, `بستن کال N` | `EndCall N`, `DiscardCall N` | call admin | **Scheduled** end — `N` is minutes, Persian digits accepted, max `1440`; persists schedule and job calls raw `phone.DiscardGroupCall` |
| `شروع ویس چت`, `شروع کال` | `StartCall` | call admin | Starts via raw `phone.CreateGroupCall` helper path; visible unsupported/helper error otherwise |
| `بیصدا کال USER` | `MuteCall USER` | call admin | Target from id, `@username`, reply, or mention; raw participant mute |
| `حذف بیصدا کال USER` | `UnmuteCall USER` | call admin | Target from id, `@username`, reply, or mention; raw participant unmute |
| `دعوت کال USER` | `InviteCall USER` | call admin | Raw `phone.InviteToGroupCall` for one target |
| `دعوت کال مدیران` | `InviteCall Admins` | call admin | Raw invite for Telegram admins |
| `دعوت کال اخیر` | `InviteCall Recent` | call admin | Raw invite for recent group users |
| `دعوت کال ویژه` | `InviteCall Special` | call admin | Raw invite for top call-stat users when auto stats are enabled |
| `آمار خودکار کال فعال/غیرفعال` | `AutoCallStatis Active/Inactive` | call admin | Persists `auto_stats_enabled` |
| `آمار کال`, `امار کال`, `Call Stats`, `Voice Call Stats`, `CallStatis` | same | call admin | Exact-only; opens 9-button scope/period panel (group/VIP/admin × all/week/today); requires `call_stats:{chat_id}:enabled` |
| `سکوت کال فعال/غیرفعال` | `CallMute Active/Inactive` | call admin | Persists `call_mute_enabled`; active calls attempt raw `ToggleGroupCallSettings(join_muted=...)` |
| `کامنت کال فعال/غیرفعال` | `CallComment Active/Inactive` | call admin | Persists `call_comment_enabled`; active calls attempt raw `ToggleGroupCallSettings(messages_enabled=...)` |
| `عنوان کال TITLE`, `تنظیم تایتل TITLE`, `تنظیم عنوان کال TITLE` | `SetTitleCall TITLE`, `SetTitle TITLE` | call admin | Requires active call; raw `EditGroupCallTitle`; title is persisted only after live success |
| `لینک کال`, `دریافت لینک کال` | `GetCallLink` | group user | Requires active call; raw `ExportGroupCallInvite`; no synthetic URL fallback |

`call admin` means developer, owner, sudo with chat-settings permission, stored player owner/music admin/video admin, or Telegram group admin/creator with call-management-like privileges.

## Manager/group text commands (`manager_text_commands.py`)

Plain text commands; no `/` prefix. Registered **before** `promotion.py` in [`app/handlers/__init__.py`](../../app/handlers/__init__.py), so overlapping aliases such as `ترفیع موزیک` / `PromoteM` route to the manager mod layer, not legacy promotion handlers.

See [manager_text_commands.md](manager_text_commands.md) for persistence details.

### Install / lifecycle / credit

| Family | FA aliases | EN aliases | Scope |
| --- | --- | --- | --- |
| install | `افزودن موزیک`, `نصب موزیک`, `نصب پلیر` | `Addm`, `AddMusic` | group |
| uninstall | `حذف نصب موزیک`, `حذف نصب پلیر`, `حذف موزیک` | `RemM`, `RemMusic`, `RemPlayer` | group |
| leave | `خروج موزیک`, `ترک گروه موزیک` | `LeaveM`, `LeaveMusic`, `LeavePlayer` | group |
| charge | `شارژ موزیک`, `شارژ پلیر` | `ChargeMusic`, `ChargePlayer`, `ChargeM` | group/private |
| add helper | `افزودن کمکی موزیک`, `افزودن کمکی پلیر` | `AddhelperMusic`, `AddhelperM` | group |
| config | `پیکربندی پلیر`, `پیکربندی موزیک` | `ConfigMusic`, `ConfigPlayer` | group |
| expire | `اعتبار موزیک`, `اعتبار پلیر` | `MusicExpire`, `ExpirePlayer` | group |

Charge semantics: `100` / `100+` increase; `100-` / `-100` decrease; `نامحدود` / `Unlimit` sets unlimited. Private charge requires explicit group id (see PM section above).

### Owner / deputy / mod roles (`group_role_commands.md`)

| Family | FA aliases (sample) | EN aliases (sample) | Target |
| --- | --- | --- | --- |
| owner add | `ارتقا مالک موزیک`, `افزودن مالک پلیر` | `AddOwnerM`, `SetOwnerMusic` | reply / id / @user |
| owner remove | `عزل مالک موزیک`, `حذف مالک پلیر` | `RemOwnerMusic`, `DemOwnerPlayer` | reply / id / @user |
| owner list | `لیست مالک پلیر`, `لیست مالکان موزیک` | `OwnerListM`, `OwnerListMusic` | — |
| owner clear | `پاکسازی لیست مالک موزیک`, … | `ClearOwnerListM`, … | — |
| deputy add | `ارتقا معاون موزیک`, `افزودن معاون پلیر` | `AddDeputyM`, `SetDeputyMusic` | reply / id / @user |
| deputy remove | `عزل معاون موزیک`, `حذف معاون پلیر` | `RemDeputyMusic`, `DemDeputyPlayer` | reply / id / @user |
| deputy list/clear | `لیست معاون پلیر`, `پاکسازی لیست معاون …` | `DeputyListM`, `ClearDeputyListM`, … | — |
| mod add | `ترفیع موزیک`, `ترفیع پلیر`, `ارتقا مقام پلیر` | `PromoteM`, `PromoteMusic`, `PromotePlayer` | reply / id / @user |
| mod remove | `عزل موزیک`, `عزل مقام پلیر` | `DemoteM`, `DemoteMusic`, `DemotePlayer` | reply / id / @user |
| mod list/clear | `لیست مدیر موزیک`, `پاکسازی لیست مدیران موزیک` | `ModListM`, `ClearModListM`, … | — |

Deputies persist in `player_vips`; owners in `player_owners`; mods in `music_admins`.

## Role management — legacy (`promotion.py`)

Group; `@player_owner_or_above`. Target: **reply** or `user_id` / `@username` after command.

**Note:** `ترفیع موزیک` / `عزل موزیک` are also registered in manager mod commands and take precedence when both parsers match. Use distinct legacy-only commands below when you need promotion.py behavior explicitly.

| FA | EN | Role |
| --- | --- | --- |
| `ترفیع موزیک` | `promotmusic` | music admin |
| `عزل موزیک` | `demotemusic` | demote music admin |
| `ترفیع ویدیو` | `promotvideo` | video admin |
| `عزل ویدیو` | `demotevideo` | demote video admin |
| `ترفیع مالک` | `setcreator` | player owner |
| `عزل مالک` | `delcreator` | demote owner |
| `ترفیع ویژه` | `promotevip` | VIP |
| `عزل ویژه` | `demotevip` | demote VIP |
| `پیکربندی موزیک` | `configmusic` | bulk: TG admins → music admin |
| `پاکسازی مدیران موزیک` | `delconfigmusic` | clear music admins |
| `لیست مدیران موزیک` | `listmusic` | list music admins |
| `پیکربندی ویدیو` | `configvideo` | bulk video admins |
| `پاکسازی مدیران ویدیو` | `delconfigvideo` | clear video admins |
| `لیست مدیران ویدیو` | `listvideo` | list video admins |

## Credit charge (`credit_commands.py`)

Group; sudo+ with `can_manage_credit`.

| FA | EN | Example |
| --- | --- | --- |
| `آپدیت شارژ N` | `update charge N` | `آپدیت شارژ 30` |
| `آپدیت شارژ ویدیو N` | `update charge video N` | `آپدیت شارژ ویدیو 15` |

## Help & helper

| Command | Handler | Scope |
| --- | --- | --- |
| `راهنما` / `کمک` / `help` / `/help` | `help_center.py` | all |
| `افزودن هلپر` / `/addhelper` | `add_helper.py` | group |

## Wizard cancel compatibility

Inline Back/Cancel buttons are the primary UX for broadcast and ask flows.
The text aliases `cancel`, `/cancel`, and `لغو` remain supported only as hidden compatibility fallbacks for active sessions.

## Text normalization

Help, panel, charge, and play text commands are matched after **NFKC + ZWNJ collapse** (`app/utils/text_commands.py`). Persian/Arabic-Indic digits in charge commands (e.g. `آپدیت شارژ ۳۰`) are accepted. Slash aliases (`/help`, `/panel`, `/play`) work regardless of privacy mode.

## Telegram privacy mode

Plain-text group commands (`راهنما`, `پنل`, `پخش`, …) require the bot to **receive all group messages**. Disable [privacy mode](https://core.telegram.org/bots/features#privacy-mode) via [@BotFather](https://t.me/BotFather), or use slash aliases / reply-to-bot. No code change can bypass Telegram privacy when enabled.

## Group command prerequisites

1. Bot is **full admin** in the group.
2. Group has **credit** (`credit_days > 0`).
3. If `security_call_enabled`: only admins, VIP, player owners, sudo+.
4. Text commands must appear at **start of message** (not mid-sentence).
5. `download_enabled` affects **download** only — not voice-chat playback.

## Related docs

- [playback.md](playback.md) — play pipeline, queue, errors
- [group_settings.md](group_settings.md) — panel toggles
- [group_text_commands.md](group_text_commands.md) — call command persistence
- [manager_text_commands.md](manager_text_commands.md) — install/charge semantics
- [group_role_commands.md](group_role_commands.md) — owner/deputy/mod matrix
- [credit_and_daily_deduct.md](credit_and_daily_deduct.md) — charging model
- Help Center i18n: `app/resources/i18n/*/system/help.json`
