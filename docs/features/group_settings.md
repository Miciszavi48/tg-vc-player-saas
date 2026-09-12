# Group Settings Panel

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md) | Related: [call_security.md](call_security.md) | [../architecture/handler_routing.md](../architecture/handler_routing.md)

Group admins configure per-chat behavior from **تنظیمات** / `/settings` → **تنظیمات گروه** (`grp:settings`).


## Entry points

| Entry | Handler |
| --- | --- |
| Text `تنظیمات`, `/settings` | `group_panel.settings_command` → root panel |
| Callback `grp:settings` | Full settings submenu |
| **امنیت کال** | `grp:callsec` → Call Security panel (see [call_security.md](call_security.md)) |

## Navigation

- **بازگشت** (`nav:back`) and **منوی اصلی** (`wz:home`) use priority handlers in `group_panel.py` (group `-850`) to return to the group root panel.
- Management submenu uses `nav:back` only.

## Visible toggles (`KeyboardFactory.group_settings`)

Mapped in `group_panel._SETTING_TOGGLE_MAP` / `_GRP_VISIBLE_SETTING_TOGGLES`:

| UI label (FA) | Callback | DB field |
| --- | --- | --- |
| قفل پخش | `grp:set:security_call` | `security_call_enabled` |
| دانلود رسانه | `grp:set:download_users` | `download_enabled` |
| حذف پیام‌های اضافه | `grp:set:auto_clean` | `filter_enabled` |
| پیام کال | `grp:set:call_message` | `announce_enabled` |
| ماندن در ویس چت | `grp:set:auto_ready_call` | `auto_leave_enabled` |
| پخش ویدیو | `grp:set:music_video` | `video_enabled` |
| زبان پنل | `grp:set:language` | `language` (fa/en) |
| نوع پیش‌فرض پخش | `grp:set:default_media` | `default_media_type` (migration `0020`) |
| گزارشات کال | `grp:set:call_report` | `buttons_enabled` |
| لیست انتظار | `grp:set:queue` | `smart_radio_enabled` |
| شناسه ترک | `grp:set:show_id` | `show_track_id` (now-playing track ID display) |
| کاور | `grp:set:show_photo` | `show_cover` |
| متن پخش | `grp:set:show_text` | `show_now_playing_text` |
| آمار کال | `grp:set:call_stats` | `bot_settings` key `call_stats:{chat_id}:enabled` (default on) |
| آمار کال در شناسه | `grp:set:id_call_stats` | `bot_settings` key `id_command:{chat_id}:show_call_stats` (default on when call stats enabled) |

**Id command output mode** (`simple` vs `photo`) is stored in `bot_settings` key `id_command:{chat_id}:output_mode` and set via text commands `وضعیت نمایش شناسه عکس` / `وضعیت نمایش شناسه ساده` (not the `grp:set:show_id` toggle). The `id` / `آیدی` command uses `user_info_formatter_service` and optionally appends call stats when both call-stats toggles are on.

Hidden from keyboard but still in DB/callback map: `vote_skip_enabled` (legacy `grp:set:repeat`), `lyrics_enabled`.

## Now playing flags

Migration `0021_now_playing_flags` adds cover/text/track-id columns consumed by `now_playing_renderer.py` and `cover_art_service.py`. See [now_playing_cover_security.md](now_playing_cover_security.md).

## Repeat status

- Session repeat: **now-playing** button `pb:repeat` → in-memory `CallService` state (B6-1).
- Group «تکرار» toggle hidden; no `repeat_enabled` column.

## Permissions

- Message commands: `@group_music_admin` decorator.
- Many callbacks: `music_admin_filter` + `@group_music_admin`.
- Sudo bypass when `can_use_sudo_admin_bypass` is enabled.

## Tests

- `tests/test_group_settings_deep_audit.py`
- `tests/test_group_settings_command_aliases.py`
- `tests/test_default_media_type_group_settings_phaseb3.py`
- `tests/test_group_panel_navigation_phase.py`
