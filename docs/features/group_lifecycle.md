# Group Lifecycle

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> 
> Back to [index.md](index.md) | Related: [manager_text_commands.md](manager_text_commands.md), [credit_and_charge.md](credit_and_charge.md)

## Source Of Truth

For group runtime behavior, `groups.status = 'active'` is authoritative. A
`group_credits` row stores balance, but it does not make an inactive or missing
group usable.

## Install

`افزودن موزیک` / `AddMusic` creates or reactivates the `groups` row, creates
default `chat_settings`, ensures a `group_credits(chat_id, 'group')` row, logs
the install, and invalidates settings/credit caches. Repeating install is
idempotent.

## Charge And Status

`آپدیت شارژ N` and slash-free manager charge commands both require an active
managed group. On success they update the same `group_credits` row read by
runtime status and append `credit_history`. They refuse inactive/unmanaged groups
instead of silently reactivating them.

`اعتبار موزیک` / `اعتبار پلیر` reads the same managed runtime state. If the
group is inactive, it reports unmanaged. If the group is active but credit is
missing, it reports a visible credit-initialization error.

## Uninstall And Leave

Uninstall marks `groups.status='inactive'`, removes per-group settings, player
roles, helper binding, playback state, playlist, Call Security settings, and
group-call `bot_settings`, then invalidates settings/credit caches. The bot
stays in the group.

Leave performs the same cleanup first, then calls main bot leave and helper
leave when a helper was bound. Telegram leave failures are reported visibly; DB
cleanup remains persisted.

## Runtime Effects

After uninstall, these readers agree that the group is inactive:

- scheduler credit warnings
- daily credit deduct
- `اعتبار موزیک` / `اعتبار پلیر`
- full group settings panel
- old and new charge commands
- playback/download/playlist credit checks
- group call text commands
- Call Security active-credit checks
