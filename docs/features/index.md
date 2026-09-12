# Features Index

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> 
> Feature docs describe **current code behavior**. For product requirements, see [../specs/](../specs/).  
> Back to [../index.md](../index.md).

## Feature Documents

| Document | Topic |
|----------|-------|
| [**commands-and-panels.md**](commands-and-panels.md) | **(Canonical)** Complete command catalog, slash-free triggers, and admin panel guide |
| [commands.md](commands.md) | All text and slash commands (FA/EN aliases, normalization) |
| [group_text_commands.md](group_text_commands.md) | Slash-free group call commands, permissions, persistence |
| [manager_text_commands.md](manager_text_commands.md) | Slash-free manager/admin install, charge, helper, config, expire commands |
| [group_role_commands.md](group_role_commands.md) | Slash-free owner/deputy/mod commands and role persistence |
| [playback.md](playback.md) | Playback commands, audio/video routing, queue, errors |
| [group_settings.md](group_settings.md) | Group settings panel, visible toggles, navigation |
| [call_security.md](call_security.md) | Call Security (امنیت کال), mute enforcement, membership age |
| [helper_bot.md](helper_bot.md) | Helper accounts: OTP wizard, join logic, pool management |
| [i18n.md](i18n.md) | i18n split-only architecture (quick reference) |
| [admin_panels.md](admin_panels.md) | Developer / owner / sudo panel structure and capabilities |
| [credit_and_charge.md](credit_and_charge.md) | Credit model, `آپدیت شارژ` command, daily deduct |
| [broadcast.md](broadcast.md) | Advanced broadcast FSM wizard |
| [force_join.md](force_join.md) | Force-join channel management |
| [now_playing_cover_security.md](now_playing_cover_security.md) | Now-playing renderer, cover art, metadata flags |
| [text_links_and_start.md](text_links_and_start.md) | Developer/owner text link panel, /start UX |
| [owner_sudo_permissions.md](owner_sudo_permissions.md) | Role hierarchy, sudo permission matrix, owner scope |
| [helper_accounts.md](helper_accounts.md) | Legacy alias → see helper_bot.md |

## Key Feature Facts

- **Persian is primary UX.** All text commands have Persian aliases; English aliases for compatibility.
- **Toggle icons:** ✅ = enabled, ☑️ = disabled (via `toggle_label()` + `status_indicator` i18n).
- **Repeat is session-only** (in-memory; no DB toggle).
- **`download_enabled` ≠ playback gate.** Download toggle only blocks `دانلود` command.
- **Call Security ≠ Playback Lock.** Two separate features, different DB fields, different callbacks.
- **Call Stats** (`آمار کال`) opens an admin-gated inline panel; gated by `call_stats:{chat_id}:enabled` in `bot_settings`.
- **Id output mode** (`simple`/`photo`) and optional call-stats block are per-chat `bot_settings`, not now-playing flags.
- **Install-player setup** uses `Add:Fa:*` callbacks when install policy requires charge/access/language selection.
- **Help and Panel are separate.** `/help`→ Help Center; `/panel`→ Group Settings panel.
