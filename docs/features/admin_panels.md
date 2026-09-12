# Admin Panels

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md) | Related: [../features/owner_sudo_permissions.md](../features/owner_sudo_permissions.md)

## Overview

Admin panels (developer, owner, sudo, helper, analytics, broadcast, force-join, call security)
use **inline keyboards only** — no text commands for entry (except `/start` for PM root).

## Role Hierarchy

```
Developer  (DEVELOPER_ID env var, comma-separated for multi-dev)
    └── Owner       (installed/managed by developer)
            └── Sudo        (added by developer; scoped to owner's installs)
                    └── Music Admin / Video Admin / VIP  (per-group)
```

## Developer Panel (`app/handlers/dev_panel.py`)

Entry: `/start` in PM for developer users.

### Categories

| Category callback | Subcategory content |
|-------------------|---------------------|
| `DEV_CAT_CREDIT` | Increase/decrease credit per chat, invoice history |
| `DEV_CAT_RATES` | Base/music/video/call-security/sell rates |
| `DEV_CAT_BROADCAST` | Broadcast wizard entry, history |
| `DEV_CAT_LISTS` | Groups, channels, no-credit, renewal lists (paginated) |
| `DEV_CAT_SETTINGS` | Global toggles: bot on/off, sudo panel on/off, force-join, auto-leave, trial |
| `DEV_CAT_USERS` | Owner/sudo management, admin titles, global ban (ban-all) |
| `DEV_CAT_TEXTS` | Text/link fields (developer scope) |

### Settings Toggles (Developer)

Uses `toggle_label(lang, base_key, enabled)` with ✅/☑️ suffix:

| Toggle | Callback | Field |
|--------|----------|-------|
| فعال بودن ربات | `DEV_BOT_ENABLED_TOGGLE` | `bot_settings.bot_enabled` |
| پنل سودو | `DEV_SUDO_PANEL_ENABLED_TOGGLE` | `bot_settings.sudo_panel_enabled` |
| فورس جوین | `DEV_FORCE_JOIN_TOGGLE` | `bot_settings.force_join_enabled` |
| ترک خودکار | `DEV_AUTO_LEAVE_TOGGLE` | `bot_settings.auto_leave_enabled` |
| آزمایش رایگان | `DEV_TRIAL_TOGGLE` | `bot_settings.trial_enabled` |

### Webservice / Media Health

- `dev:media_health` — yt-dlp/ffmpeg versions, downloads stats, active calls.
- `dev:media:export` — bounded JSON report delivered as Telegram document.
- `dev:media:cleanup:preview` / `:do|no` — safe stale-file cleanup with confirmation.
- `dev:media:ranking` — URL fingerprint play/download statistics.

### Bot Update Panel

`dev:bot_update` — runtime diagnostics, safe reload via sentinel file or external command with user-bound confirmation.

### Admin Titles

Developer can set stored admin titles for developer/owners/sudos and apply them to Telegram with `phone.PromoteChannelMember`.

### Global Ban (Ban-All)

`dev:banall:*` — developer-only. Bans users globally and best-effort removes them from active installs.
Table: `global_bans`. Guard: `app/handlers/global_ban_guard.py`.

## Owner Panel (`app/handlers/owner_panel.py`)

Entry: `/start` in PM for owner users.

- Lists own installs (groups/channels) from `owner_scope_service` (install lineage via `installed_by` + `Sudo.added_by`).
- Per-install credit management.
- Sudo management scoped to own installs (add, remove, permission toggles `own:sp:*`).
- Text/link fields (owner scope, separate from developer global fields).
- Billing bypass: `compute_install_cost` returns 0 for `sudo` role (same as developer/owner).

## Sudo Panel (`app/handlers/sudo_panel.py`)

Entry: `/start` in PM for sudo users (when `sudo_panel_enabled` is on).

- View own assigned stats (`SUDO_MY_STATS`).
- Manage credit for installs under their scope.
- Credit charge text command: `آپدیت شارژ N` / `update charge N` (group, requires `can_manage_credit`).

## Analytics Panel (`app/handlers/analytics_panel.py`)

Developer-only. Accessed from dev panel.

| Callback | Content |
|----------|---------|
| `AN_HOME` | Today's stats |
| `AN_YESTERDAY` | Yesterday |
| `AN_7DAYS` | 7-day window |
| `AN_14DAYS` | 14-day window |
| `AN_30DAYS` | 30-day window |
| `AN_PEAK` | Peak usage |
| `AN_ERRORS` | Error summary |
| `AN_BY_FEATURE` | Feature breakdown |
| `AN_BY_CHAT_TYPE` | Group vs channel |
| `AN_BY_ROLE` | Role breakdown |
| `SUDO_MY_STATS` | Sudo's own stats |

## Help Center (`app/handlers/help_center.py`)

Entry: `راهنما` / `کمک` / `help` / `/help` in any chat.

Help sections (callbacks `h:*`):
- `h:home` — Help home
- `h:start` — Getting started
- `h:play` — Playback
- `h:ctrl` — Controls
- `h:plist` — Playlist
- `h:radio` — Radio/TV
- `h:dl` — Downloads
- `h:fj` — Force-join
- `h:trouble` — Troubleshooting
- `h:about` — About

Role-specific help sections (accessed from PM role panels):
- `h:grp` / `h:sudo` / `h:own` / `h:dev` — panel-specific help

> **Note:** `h:home` ≠ `hlp:home`. `hlp:home` is the **Helper management panel** home (different feature).

## Helper Management Callback (`hlp:home`)

Developer panel → **مدیریت هلپرها** uses callback data `hlp:home`.

- Handler: `app/handlers/helper_panel.py::hlp_home`
- Scope: developer-only, private chat only
- Behavior: answers the callback before helper DB/count work, clears helper
  wizard state, then renders helper counts and diagnostic notices.
- Availability: the panel opens even when no helper is available or every helper
  is quarantined. It does not call helper join/session/group binding paths just
  to render the management home.

Callback tracing is centralized in `app/utils/callback_trace.py` and the global
callback safety wrapper in `app/handlers/__init__.py`. Logs use `cbid`, callback
data prefix, user/chat context, handler/module, guard result, answer/edit
result, duration, and safe exception class/message. Unknown callbacks are logged
as `callback.unhandled route=post_dispatch_unhandled handler=none` instead of a
misleading pre-dispatch `selected_handler=unknown`.

## Panel Edit-First UX

Panel callbacks, toggles, back/home, and ask-flow outcomes **edit the stored panel message** instead of sending new messages.

Redis anchor key: `panelmsg:{chat_id}:{user_id}`

- `app/utils/telegram_message.py` — safe edit helpers.
- `app/services/panel_message_service.py` — panel message store/retrieve.

## Keyboard Layout Conventions

As of 2026-06-11 keyboard UI cleanup:
- Related buttons **paired** (2 per row) when labels are short.
- Triple row for short toggles (e.g., `show_id` / `show_photo` / `show_text`).
- Full-width for long actions and sub-panel entries.
- Navigation row: Back + Home or Back + Close on one row (`_nav_row()`).
- Toggle suffix: `✅` (active) / `☑️` (inactive) via `toggle_label()`.
- Decorative frames only on **message titles**, not buttons.

## Callback Data Stability

All `CB[...]` values are stable ASCII English identifiers. Label text changes do not change callback data. This is enforced by `tests/test_ui_contract.py`.
