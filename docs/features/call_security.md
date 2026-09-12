# Call Security (امنیت کال)

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> 
> Back to [../index.md](../index.md) | Related: [group_settings.md](group_settings.md) | [../features/credit_and_charge.md](credit_and_charge.md)

**Canonical feature doc.** Supersedes any mention of global «امنیت کانال».

## Summary

Per-chat voice-call security for **groups and channels**. Configured from **تنظیمات گروه → امنیت کال** (`grp:callsec`) or **پنل پلیر** in channels.

Separate from **playback lock** (`security_call_enabled` / `grp:set:security_call` / «قفل پخش»).

## Deprecated / legacy

| Item | Status |
| --- | --- |
| Global `channel_security_enabled` / «امنیت کانال» UI | Removed; handlers show deprecation redirect |
| `account_age_days` (migration 0024) | Legacy column; runtime uses `membership_age_days` |
| Telegram user-id age estimation | **Not used** by Call Security |

## Database

- Table: `call_security_settings` (migration `0024_call_security_settings`)
- Membership tracking: `group_member_memberships` (migration `0025_group_member_membership_tracking`)
- Project Alembic head: **`0039_hot_seat`**

Fields (toggles): `enabled`, `owner_access_enabled`, `mute_incoming_enabled`, `summary_enabled`, `report_enabled`, `membership_age_days` (default 7).

## Permissions

- **Developer**, **bot owner**, and **active sudo**: full in-group bypass for Call Security (no local role rows or sudo permission flags required).
- **Telegram group/channel creator**, **PlayerOwner**, and **PlayerDeputy**: open and manage Call Security.
- **Installer** (user who installed bot in chat): full panel including **دسترسی مالکان** toggle.
- **Music admin** and **VIP**: denied from Call Security panel.
- `owner_access_enabled` still controls whether **PlayerOwner** receives mute/speak privileges in runtime enforcement (`is_privileged_member`), not panel manage access.
- Callbacks use `query.message.chat.id` only (no user-supplied chat id in callback data).

## Membership age (قدمت عضویت)

- Measures **time in the same group**, not Telegram account age.
- Unknown membership age **fails closed** (no auto-unmute).
- Members present before tracking started are not guessed as “old”.
- Sources: `ChatMemberUpdated`, service messages, `group_guard` first-seen, call participant events.

## Mute enforcement

High-level PyTgCalls does not expose participant moderation. When Kurigram raw MTProto is available:

- `phone.EditGroupCallParticipant` via **helper** MTProto clients (`group_call_moderation_service.py`).
- Requires active call + eligible helper bound to chat.
- Panel shows honest status: unsupported / no active call / no helper / permission denied.

Detection and live reports remain partly **report-only** depending on raw payload fields.

## Redis keys

Defined in `app/utils/redis_keys.py`:

- `callsec:{chat_id}:state`
- `callsec:{chat_id}:user:{user_id}`
- `callsec:report:{chat_id}:{user_id}:{reason}:{bucket}`
- `callsec:active:{chat_id}`
- `callsec:callmap:{call_id}`

TTLs: state 24h; report cooldown 1h.

## Code map

| Area | Module |
| --- | --- |
| Panel UI | `app/handlers/call_security_panel.py` |
| Runtime / raw updates | `app/handlers/call_security_runtime.py` |
| Business logic | `app/services/call_security_service.py` |
| Mute MTProto | `app/services/group_call_moderation_service.py` |
| Membership age | `app/services/group_membership_age_service.py` |
| Repository | `app/repositories/call_security_repo.py` |

## Manual smoke checklist

1. Open group → `تنظیمات` → تنظیمات گروه → **امنیت کال**.
2. Toggle **فعال** (requires group credit).
3. Set **قدمت عضویت** via ask flow.
4. Start voice chat; verify reports/summary if enabled.
5. Confirm **قفل پخش** still gates playback separately.

## Related docs

- [commands-and-panels.md](commands-and-panels.md)
- [../architecture/system-design.md](../architecture/system-design.md)
