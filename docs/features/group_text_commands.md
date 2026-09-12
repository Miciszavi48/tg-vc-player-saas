# Group Text Call Commands

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> Back to [index.md](index.md) | Related: [commands.md](commands.md)

These commands are plain group text, not slash commands. Telegram privacy mode must allow the bot to receive group text messages.

## Capability Check

Installed local package check:

- `pyrogram.__version__`: `2.2.18`
- Raw functions present under `pyrogram.raw.functions.phone`: `CreateGroupCall`, `DiscardGroupCall`, `EditGroupCallParticipant`, `EditGroupCallTitle`, `InviteToGroupCall`, `ExportGroupCallInvite`, `ToggleGroupCallSettings`.
- High-level `Client` methods for create/discard/invite/title/link were not present in the installed package, so live actions use raw MTProto through configured helper user clients (`HelperPoolService`).

If raw support, active-call resolution, helper session, or Telegram permissions are unavailable, the command returns a visible unsupported/no-helper/no-active/API-error message. It does not report fake success.

See also Help Center → **Call Commands** (`help.call_commands`) and group panel help → **Call commands** (`help_content.call_commands`).

## Helper Execution Contract

Live Telegram group-call actions use a helper **user** account client, not the bot client. `app/services/group_call_moderation_service.py` resolves the helper before every raw `phone.*` action:

1. Prefer the existing `helper_chat_bindings` row for the target group.
2. If the bound helper is unavailable or quarantined, try an eligible fallback helper from the pool.
3. Call `HelperPoolService.ensure_helper_joined(helper_id, chat_id)`.
4. Start the helper user client and re-check `get_chat_member(chat_id, "me")`.
5. Require helper admin/call-management permission for raw call-management actions.
6. Bind the successful helper back to the group.

Failure statuses are visible to the requester: no helper, helper session unavailable, join failed, helper not in group, helper not admin, no active call, raw API unavailable, or API failed. Session strings and secrets are not logged.

## Command Matrix

| Family | Persian aliases | English aliases | Permission | Live action | Persistence |
| --- | --- | --- | --- | --- | --- |
| Current playback panel | `دریافت پنل` | `GetPanel` | Any group user | Rebuilds and sends current now-playing panel with inline controls | Reads `playback_states`; no write |
| Immediate call end | `پایان کال`, `بستن کال` | `EndCall`, `DiscardCall` | Call admin | No minutes suffix → `end_call_now()`; same-group admin helper calls raw `phone.DiscardGroupCall` immediately, then cleans local PyTgCalls playback | Clears any pending `scheduled_end` |
| Scheduled call end | `پایان کال N`, `بستن کال N` | `EndCall N`, `DiscardCall N` | Call admin | `N` = minutes (max 1440); resolves same-group admin helper before scheduling; scheduled job re-resolves helper and calls raw `phone.DiscardGroupCall`, then cleans local PyTgCalls playback | `bot_settings` key `group_text_call:{chat_id}:scheduled_end` |
| Start call | `شروع ویس چت`, `شروع کال` | `StartCall` | Call admin | Same-group admin helper calls raw `phone.CreateGroupCall`; applies stored title and live settings when possible | Reads stored title/mute/comment settings |
| Mute participant | `بیصدا کال USER` | `MuteCall USER` | Call admin | Same-group admin helper calls raw `phone.EditGroupCallParticipant(muted=True)` | No command write |
| Unmute participant | `حذف بیصدا کال USER` | `UnmuteCall USER` | Call admin | Same-group admin helper calls raw `phone.EditGroupCallParticipant(muted=False)` | No command write |
| Invite to call | `دعوت کال USER`, `دعوت کال مدیران`, `دعوت کال اخیر`, `دعوت کال ویژه` | `InviteCall USER`, `InviteCall Admins`, `InviteCall Recent`, `InviteCall Special` | Call admin | Same-group admin helper calls raw `phone.InviteToGroupCall` | Reads target lists from Telegram/DB |
| Auto call stats | `آمار خودکار کال فعال`, `آمار خودکار کال غیرفعال` | `AutoCallStatis Active`, `AutoCallStatis Inactive` | Call admin | No immediate Telegram action; scheduler reads this setting before sending stats | `group_text_call:{chat_id}:auto_stats_enabled` |
| Call stats panel | `امار کال`, `آمار کال`, `Call Stats`, `Voice Call Stats`, `CallStatis` (exact only) | same | Call admin | Opens inline scope/period selection panel; no day-suffix forms | Reads `call_reports` via panel service; gated by `call_stats:{chat_id}:enabled` |
| Call mute mode | `سکوت کال فعال`, `سکوت کال غیرفعال` | `CallMute Active`, `CallMute Inactive` | Call admin | Persists setting; if a call is active, same-group admin helper attempts raw `phone.ToggleGroupCallSettings(join_muted=...)` | `group_text_call:{chat_id}:call_mute_enabled` |
| Call comments | `کامنت کال فعال`, `کامنت کال غیرفعال` | `CallComment Active`, `CallComment Inactive` | Call admin | Persists setting; if a call is active, same-group admin helper attempts raw `phone.ToggleGroupCallSettings(messages_enabled=...)` | `group_text_call:{chat_id}:call_comment_enabled` |
| Set call title | `عنوان کال TITLE`, `تنظیم تایتل TITLE`, `تنظیم عنوان کال TITLE` | `SetTitleCall TITLE`, `SetTitle TITLE` | Call admin | Requires active call; same-group admin helper calls raw `phone.EditGroupCallTitle`; persists only after live success | `group_text_call:{chat_id}:title` after live success |
| Get call link | `لینک کال`, `دریافت لینک کال` | `GetCallLink` | Any group user | Same-group admin helper calls raw `phone.ExportGroupCallInvite`; no synthetic URL fallback | No write |

`call admin` means developer, owner, sudo with chat-settings permission, stored player owner/music admin/video admin, or Telegram group admin/creator with call-management-like privileges.

## Parser Behavior

- Uses NFKC Unicode normalization.
- Collapses whitespace and zero-width joiner variants.
- Normalizes Persian and Arabic-Indic digits to ASCII.
- English aliases are case-insensitive.
- Matching is anchored; unrelated text is ignored.
- Longer aliases are checked before shorter aliases, including `تنظیم عنوان کال` before `عنوان کال`.
- The parser does not capture playback commands such as `پخش` or credit commands such as `آپدیت شارژ ویدیو N`.

## Target Resolution

Mute, unmute, and user invite commands resolve targets from explicit numeric user id, `@username`, reply target, or Telegram mention/text-mention entities. Missing or unresolved targets get a visible error.

## Persistence Keys

No schema migration is required. The commands use the existing `bot_settings` key-value table:

```text
group_text_call:{chat_id}:auto_stats_enabled
group_text_call:{chat_id}:call_mute_enabled
group_text_call:{chat_id}:call_comment_enabled
group_text_call:{chat_id}:scheduled_end
group_text_call:{chat_id}:title
```

`scheduled_end` stores JSON with `chat_id`, `minutes`, `end_at`, and `requested_by`. APScheduler restores pending call-end jobs on startup and each restored job calls the raw discard service.

Helper binding uses the existing `helper_chat_bindings` table. Slash-free group-call actions do not store helper sessions or secrets in `scheduled_end`; the scheduled job re-runs helper resolution at execution time.

`auto_stats_enabled` is read by `run_auto_call_stats_report()` through scheduler jobs `send_auto_call_stats_morning`, `send_auto_call_stats_evening`, and `send_auto_call_stats_weekly`. The morning/evening jobs send current-day stats; the Friday weekly job passes `days=7`.

## Validation

```bash
python -m py_compile app/utils/group_text_commands.py app/handlers/group_text_call_commands.py app/repositories/group_text_call_command_repo.py app/services/group_text_call_command_service.py app/services/call_service.py app/services/group_call_moderation_service.py app/scheduler.py
python scripts/split_i18n_resources.py --check
python scripts/check_i18n_usage.py
TEST_MODE=1 pytest tests/test_group_text_call_commands.py -q
TEST_MODE=1 pytest tests/test_helper_group_binding_for_call_commands.py -q
TEST_MODE=1 pytest tests/test_group_text_call_commands_db.py -q
TEST_MODE=1 pytest tests/test_group_text_call_commands_real_actions.py -q
```

Run pytest commands serially because the default `TEST_MODE=1` SQLite file is shared per process.

## Manual Smoke Checklist

1. In a group, send `دریافت پنل`.
2. Send `پایان کال` (immediate end — no minutes).
3. Send `پایان کال 25`.
4. Send `پایان کال ۲۵`.
5. Confirm the helper account is a member of the group and promoted to admin for call-management actions.
6. Send `شروع کال`.
7. Send `بیصدا کال USER_ID`.
8. Send `دعوت کال مدیران`.
9. Send `دعوت کال اخیر`.
10. Send `دعوت کال ویژه`.
11. Send `آمار خودکار کال فعال`.
12. Check `bot_settings` key `group_text_call:{chat_id}:auto_stats_enabled`.
13. Send `آمار کال` and confirm the selection panel opens.
14. Send `سکوت کال فعال`.
15. Check `bot_settings` key `group_text_call:{chat_id}:call_mute_enabled`.
16. Send `کامنت کال فعال`.
17. Check `bot_settings` key `group_text_call:{chat_id}:call_comment_enabled`.
18. Send `عنوان کال شب سرد` while an active call exists.
19. Verify the actual Telegram call title changed or a visible unsupported/no-active-call/no-helper/not-admin response was returned.
20. Send `لینک کال`.
21. Confirm no silence and no `[missing:...]`.
