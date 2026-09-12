# Manager Text Commands

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> 
> Back to [index.md](index.md) | Related: [group_role_commands.md](group_role_commands.md), [credit_and_charge.md](credit_and_charge.md), [group_text_commands.md](group_text_commands.md)

Slash-free manager/admin commands are implemented in:

- Parser: `app/utils/manager_text_commands.py`
- Handler: `app/handlers/manager_text_commands.py`
- Service: `app/services/manager_command_service.py`
- Repository: `app/repositories/manager_command_repo.py`

All commands are plain text. They do not require `/`.

## Normalization

The parser applies NFKC normalization, maps Persian and Arabic-Indic digits to ASCII, normalizes Arabic `ي/ك` to Persian `ی/ک`, removes zero-width/ZWNJ separators, collapses whitespace, and matches aliases anchored to the full command. English aliases are case-insensitive. Existing playback and group-call commands are not matched by this parser.

## Command Matrix

| Family | Persian aliases | English aliases | Scope | Permission | Persistence |
| --- | --- | --- | --- | --- | --- |
| install | `افزودن موزیک`, `نصب موزیک`, `نصب پلیر` | `Addm`, `AddMusic` | group | developer, owner, sudo with group management | `groups`, `chat_settings`, `group_credits`, `install_logs`; opens install-player setup panel when policy requires charge/access/language selection |
| uninstall | `حذف نصب موزیک`, `حذف نصب پلیر`, `حذف موزیک` | `RemM`, `RemMusic`, `RemPlayer` | group | developer, owner, sudo with remove-bot permission | marks `groups.status=inactive`; clears group settings, role rows, helper binding, playback state, playlist, Call Security settings, and group-call `bot_settings` |
| leave | `خروج موزیک`, `ترک گروه موزیک` | `LeaveM`, `LeaveMusic`, `LeavePlayer` | group | developer, owner, sudo with remove-bot permission | same cleanup as uninstall, then calls main bot leave and helper leave if bound |
| charge | `شارژ موزیک`, `شارژ پلیر` | `ChargeMusic`, `ChargePlayer`, `ChargeM` | group/private | developer, owner, sudo with credit permission | requires active `groups.status`; updates `group_credits` and appends `credit_history` through the shared managed credit service |
| add helper | `افزودن کمکی موزیک`, `افزودن کمکی پلیر` | `AddhelperMusic`, `AddhelperM` | group | developer, owner, sudo with chat-settings permission, or stored music admin/owner | uses `ensure_helper_present_for_group`; persists `helper_chat_bindings` |
| config | `پیکربندی پلیر`, `پیکربندی موزیک` | `ConfigMusic`, `ConfigPlayer` | group | developer, owner, sudo admin bypass, player owner | imports Telegram admins into `music_admins` |
| expire | `اعتبار موزیک`, `اعتبار پلیر` | `MusicExpire`, `ExpirePlayer` | group | group users | reads active group and `group_credits`; no mutation |

## Charge Semantics

Plain numeric amounts are additive because the existing product behavior is additive. New manager charge commands and the old `آپدیت شارژ N` command now share the same managed credit source: an active `groups.status='active'` row plus the matching `group_credits(chat_id, 'group')` row. Inactive/unmanaged groups are refused visibly; charge commands do not silently reinstall groups.

These forms are equivalent increases:

- `شارژ موزیک 100`
- `شارژ پلیر 100+`
- `ChargeMusic +100`
- `ChargePlayer 100`

Explicit minus forms decrease:

- `شارژ موزیک 100-`
- `ChargeMusic -100`

Unlimited forms set the existing `group_credits` row to `status="unlimited"` and `credit_days=36500`:

- `شارژ پلیر نامحدود`
- `ChargePlayer Unlimit`

Private manager charge requires an explicit managed group id:

- `شارژ موزیک 1001234567890- 20` parses as group `-1001234567890`, increase `20`.
- `ChargeMusic -1001234567890 +20` parses the same way.
- `ChargePlayer -1001234567890 -20` decreases `20`.

## Install / Uninstall / Leave

Install is idempotent. It activates or creates the managed group row, creates default chat settings, creates an initial credit row if one is missing, and invalidates settings/credit caches.

Uninstall marks the group inactive and clears per-group settings and runtime/player state. Cleanup removes `chat_settings`, `music_admins`, `video_admins`, `player_owners`, `player_vips`, `helper_chat_bindings`, `playback_states`, `playlists`, `call_security_settings`, and `bot_settings` keys matching `group_text_call:{chat_id}:*`; it also invalidates settings/credit caches. The main bot stays in the group.

Leave performs the same cleanup first, then calls the main bot leave API. If a helper was bound, it also calls the helper leave service. If helper leave fails, the reply says it was partial. If the main bot leave fails, cleanup remains persisted and the reply reports the Telegram leave failure.

Deputy rows are stored in `player_vips`; add/remove/clear and uninstall cleanup invalidate the matching Redis VIP permission cache keys so `admin_repo.is_vip()` reflects changes immediately.

## Install Player Setup Panel

When install policy requires operator choices, `افزودن موزیک` / `نصب پلیر` / `AddMusic` opens the **install-player setup panel** instead of immediately activating the group:

- Handler: `group_panel.reply_install_player_setup_panel` (invoked from `manager_text_commands.py`)
- State builder: `manager_command_service.build_install_player_setup_state`
- Callback prefix: `Add:Fa:` (`CB['INSTALL_SETUP_PREFIX']` in `app/utils/ui.py`)
- Submenus: charge selector, access toggles (music/video/download), language (`fa`/`en`), confirm
- Keyboards: `KeyboardFactory.install_player_setup_panel`, `install_player_charge_menu`, `install_player_access_menu`, `install_player_language_menu`

Malformed or expired `Add:Fa:*` callbacks are answered with a safe error (`install_player_setup_malformed`).

## Helper / Config

`Addhelper*` reuses `ensure_helper_present_for_group`, so it uses the same helper resolver and binding behavior as live call commands. It returns visible already-present, unavailable, or join-failed states.

`Config*` reads Telegram group administrators through `get_chat_members(..., ADMINISTRATORS)`, skips bots, clears existing `music_admins`, and imports the current Telegram admins. Player owners are not removed.

## Manual Smoke Checklist

1. In a group, send `افزودن موزیک`.
2. Send `اعتبار موزیک`.
3. Send `شارژ موزیک 100+`.
4. Check `group_credits.credit_days`.
5. Send `افزودن کمکی موزیک`.
6. Confirm `helper_chat_bindings.chat_id`.
7. Send `پیکربندی پلیر`.
8. Confirm imported rows in `music_admins`.
9. Send `حذف نصب موزیک` and verify the bot remains.
10. Reinstall, then send `خروج موزیک` and verify leave paths.

See also Help Center → **Manager / Install** and group panel help → **Manager commands** (`help_content.manager_commands`).
