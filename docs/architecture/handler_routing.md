# Handler Registration and Routing Model

> Last verified against repository: 2026-07-19

> Back to [../index.md](../index.md) | Related: [../features/commands.md](../features/commands.md)

## Overview

All Telegram message and callback handlers are registered in `app/handlers/`.
The main entry point `app/handlers/__init__.py` defines module load order,
a pyromod async-wrapping fix, a callback auto-answer/safety wrapper, and the
`register_all(bot, call_py)` function called from `app/main.py`.

## Module Registration Order (`_MODULES`)

Modules are registered in this order (order affects priority-group collisions
for the same group number):

```
group_guard          ← global blacklist + force-join gate
global_ban_guard     ← global user ban guard
filter_words         ← auto-delete filter words
start                ← /start
add_helper           ← /addhelper
dev_panel            ← developer panel callbacks (+ hlp/an/h shortcuts)
dev_banall_panel     ← global ban panel
callbacks            ← shared callbacks (nav, pb controls, favorites, etc.)
owner_panel          ← owner panel callbacks
sudo_panel           ← sudo panel callbacks
group_panel          ← group settings/management/help
call_security_panel  ← امنیت کال panel callbacks
call_security_runtime← real-time call participant tracking
playback             ← پخش / /play and controls
playlist             ← playlist commands
tv_radio             ← TV/radio/satellite callbacks
promotion            ← role management commands
broadcast            ← direct broadcast slash commands
force_join           ← force-join verification callback
force_join_panel     ← developer force-join panel
broadcast_panel      ← broadcast history/cancel callbacks
broadcast_wizard     ← advanced broadcast FSM wizard
analytics_panel      ← analytics callbacks
help_center          ← راهنما / /help and help callbacks
helper_otp_wizard    ← helper OTP/import wizard
helper_panel         ← helper management callbacks
install              ← bot join/leave events
search               ← /search command
download             ← دانلود command
credit_commands      ← آپدیت شارژ commands
```

Developer panel tool-row shortcuts (`hlp:home`, `an:home`, `h:home`, `bcw:start`, `bc:history`) are registered in
`dev_panel.py` beside `dev:cat:*` handlers so they use the same `panel_callback_edit` path.

## Panel edit standard (PM)

All private-message panel navigation and cross-module shortcuts should use
`panel_callback_edit` from `app/services/panel_message_service.py`:

- Answers the callback (unless `answer=False` when the handler already answered).
- Calls `remember_panel_from_query` for panel message tracking.
- Edits in-place via `safe_edit_message`; on failure uses `deliver_panel_outcome` fallback.
- Emits `callback.edit.start` / `callback.edit.success` trace events.

Shared render helpers (`render_*_home_panel`, `render_install_policy_panel`, etc.) wrap
payload building + `panel_callback_edit`. Wizard navigation (`nav:back`, `wz:back`, `wz:home`,
`wz:cancel`) in `callbacks.py` uses the same path for PM and group roots.

### Cross-panel shortcut registry (`dev_panel.py`)

| Callback | Handler | Delegates to |
|----------|---------|--------------|
| `hlp:home` | `dev_shortcut_helper_home` | `render_helper_home_panel` |
| `an:home` | `dev_shortcut_analytics_home` | `render_analytics_home_panel` |
| `h:home` | `dev_shortcut_help_about` | `render_help_home_panel` |
| `bcw:start` | `dev_shortcut_bcw_start` | `begin_broadcast_wizard` |
| `bc:history` | `dev_shortcut_bc_history` | `render_broadcast_history_panel` |

When adding a button on a developer/owner keyboard that uses another module's callback prefix,
register a shortcut handler in the keyboard owner's module (early in `_MODULES`).

## Handler Priority Groups

Defined in `app/handlers/priority.py`:

| Constant | Value | Used by |
|----------|-------|---------|
| `LANG_BIND_GROUP` | -1000 | Language binding + message handlers |
| `GLOBAL_BAN_GROUP` | -995 | Global ban guard (message + callback); runs before pyromod |
| `CALLBACK_TRACE_GROUP` | -990 | Callback trace + stale pyromod listener cleanup |
| `PYROMOD_CALLBACK_GROUP` | -980 | pyromod listen; excluded from `mark_route_seen` |
| `BOT_DISABLED_CALLBACK_GROUP` | -950 | Bot-off callback guard |
| `GUARD_GROUP` | -10 | `group_guard` (blacklist, force-join) |
| `PRIORITY_COMMAND_GROUP` | -15 | Help, charge, panel, play text commands |
| `FILTER_WORDS_GROUP` | -5 | Filter-words watcher |
| Normal | 0 | Most handlers |
| `FALLBACK_CALLBACK_GROUP` | 1000 | Unknown-callback fallback |

> **PRIORITY_COMMAND_GROUP (-15) runs before all normal handlers (0)** but after
> the global guards. This ensures `راهنما`, `پنل`, `آپدیت شارژ`, and `پخش` text
> commands cannot be swallowed by normal-priority FSM listeners at -85/-90.

Group-level nav callbacks (`nav:back`, `wz:home`) for group chats run at **-850**
in `group_panel.py` to override the generic PM-context `nav_back` handler.

## FSM Priority (Pyromod Listeners)

Private-message FSM listeners registered by pyromod run at:
- `-90` — OTP text/contact handlers (`helper_otp_wizard.py`)
- `-90` — Proxy input handler (`helper_panel.py`)
- `-85` — Broadcast wizard text input
- `-80` — Broadcast wizard payload capture

`PRIORITY_COMMAND_GROUP = -15` sits between -10 (GUARD) and -85/-90 (listeners),
so help/panel/charge text commands run **before** the FSM listeners and can escape wizard state.

## Pyromod Async Fix

`_fix_pyromod_async_wrapping()` in `__init__.py` repairs a kurigram incompatibility:
pyromod's `patch_into` wraps async handler methods with `async_to_sync`, causing
kurigram's dispatcher to use `run_in_executor` (wrong event loop).
The fix restores the original `async def` for each patched method.

## Callback Auto-Answer / Safety Wrapper

`apply_callback_safety_wrapper(bot)` wraps every registered `CallbackQueryHandler`:
- Patches `query.answer` to **answer exactly once** (prevents double-answer).
- Catches `MessageNotModified` / `MESSAGE_NOT_MODIFIED` silently.
- On other exceptions: logs via `log_callback_failure()` then re-raises.

Called from `app/main.py` after `bot.start()` (with 0.5s delay for dispatcher population).

## Text Command Normalization

`app/utils/text_commands.py`:
- `normalize_command_text(message)` → NFKC + ZWNJ collapse (delegates to `playback_commands.normalize_playback_text`).
- `normalize_digits(text)` → Persian/Arabic-Indic digits → ASCII.
- `is_escape_command(text)` → True for hidden compatibility escape aliases `/cancel`, `cancel`, `لغو`, plus `/help`, `help`, `راهنما`, `کمک`, `/panel`, `پنل`, `/start`, `start`. Visible ask/wizard prompts should use inline Back/Cancel buttons instead of telling users to type these aliases.
- `is_help_command(text)` → True for help aliases.
- `help_command_filter()` → Pyrogram filter using normalized matching.
- `anchored_regex_filter(pattern)` → Full-match filter on normalized text.

## Callback Routing Model

All callback data constants live in the `CB` dict in `app/utils/ui.py`.
Callback filters use `filters.regex(f"^{CB['KEY']}$")` for exact match or
regex patterns for parameterized callbacks (e.g. `dev:sudo:detail:-?\d+:\d+`).

Routing chain for a callback query:

1. `_bind_lang_callback` (-1000) — set language context var.
2. `global_ban_callback_guard` (-995) — reject globally banned users.
3. `_trace_callback_received` (-990) — structured trace + `safe_stop_listening()` before pyromod.
4. pyromod listen handler (-980, kurigram patch) — resolves active ask/listen futures or no-ops.
5. `_bot_operational_callback_guard` (-950) — reject when bot is disabled.
6. `grp_nav_back` / `grp_wz_home` (-850, group only) — group nav shortcuts.
7. Normal-priority handlers (0) — panel-specific matches.
8. `unknown_callback_fallback` (1000) — visible `common.errors.unknown_callback` alert for unmatched callbacks.

The auto-answer wrapper does **not** mark `GLOBAL_BAN_GROUP` (-995) or `PYROMOD_CALLBACK_GROUP` (-980) as routed handlers, so pyromod no-op at -980 cannot suppress known-prefix fallbacks for `hlp:*` and similar families.

## Help Callback Routing

Help Center callbacks use the `h:` prefix with user binding (`:U{user_id}`) so only the user who opened Help can navigate its menus. Example: `h:promote:U123456789`.

- **Keyboard generation:** [`KeyboardFactory.help_home()`](../../app/utils/ui.py) and related helpers append `:U{user_id}` via `_help_btn()`.
- **Parser:** `parse_help_callback()` in [`help_center.py`](../../app/handlers/help_center.py) strips the binding and rejects taps from other users.
- **Router:** a single handler `help_callback_router` registered with `filters.regex(HELP_CALLBACK_PATTERN)` at group 0 (same kurigram dispatch path as working `grp:*` handlers). Wrong-user denial and malformed `:U` rejection stay in `parse_help_callback()` inside `handle_help_callback()`.
- **Group entry:** `grp:help` in the group panel delegates to `render_help_home_panel()` (same path as the `راهنما` text command).
- **Post-start verify:** `verify_help_callback_router(bot)` runs after `apply_callback_safety_wrapper()`; logs ERROR when `Handler.check` fails.

Group panel help sub-pages use separate static callbacks (`grp:help:promote_demote`, etc.) — distinct from Help Center `h:*` navigation.

`hlp:home` is the **helper panel** home (developer-only, different feature).

### Production dispatch diagnostic

After deploy, confirm Help is registered and kurigram `Handler.check` matches user-bound callbacks (read-only; no Telegram/Redis/DB):

```bash
grep -n "help_callback_router" /opt/musicbot/app/handlers/help_center.py
grep -n "HELP_CALLBACK_PATTERN" /opt/musicbot/app/handlers/help_center.py
grep -n "def register" /opt/musicbot/app/handlers/help_center.py
find /opt/musicbot/app/handlers -name "*.pyc" -delete
systemctl restart musicbot
journalctl -u musicbot -f
```

```bash
cd /opt/musicbot
/opt/musicbot/venv/bin/python3 <<'PY'
import asyncio
from app.handlers import register_all
from app.handlers.help_center import verify_help_callback_router
from pyrogram import Client

async def main():
    bot = Client("diag", api_id=1, api_hash="x", bot_token="1:xx", in_memory=True)
    register_all(bot, None)
    await asyncio.sleep(0.1)
    ok = await verify_help_callback_router(bot, sample_user_id=123456789)
    print("verify=", ok)

asyncio.run(main())
PY
```

Expected: `verify= True` and startup log `help_center verify help_callback_router group=0 check=True`. If `verify=False`, check pattern vs keyboard callbacks.

Startup log (after restart): `help_center registered help_callback_router group=0 pattern=^h:(?:home|play|...`

Live tap on `h:promote:U{id}`: expect `callback.route … handler=help_callback_router` and `selected_handler=help` — not `callback.unhandled … reason=no_registered_or_filter_denied`.

## FSM Escape (`clear_wizard_and_allow_command`)

When a text command matches `PRIORITY_COMMAND_GROUP` during an active wizard:
- `clear_wizard_and_allow_command()` clears all known wizard namespaces from Redis.
- Does **not** call `stop_propagation` — allows the handler chain to continue.
- Applied in help/panel/play handlers that must escape active wizards.

Inline Back/Cancel buttons are the primary ask/wizard escape UX:
- `wz:cancel:<return_to>` clears runtime state, stops listeners, and returns to the resolved panel.
- `wz:back:<return_to>` now also clears/stops active runtime state before returning, so an ask prompt cannot leave a stale listener after the user presses Back.
- Flow-specific callbacks (`bcw:cancel`, `hlp:otp:cancel`, `hlp:proxy:cancel`, `grp:callsec:age:cancel`) answer visibly and clear the state namespace they own.
- Broadcast wizard and helper OTP/import back-step callbacks preserve the step data needed to go backward, while using safe edit/send fallback for panel updates.

## Audit Tool

`scripts/audit_handler_map.py` — read-only; prints a table of all registered
message and callback handlers with their module, function, group, filter, and line number.

`scripts/audit_callback_risk.py` — detects cross-panel shortcut risks, duplicate exact-match
handlers, and raw `edit_text` gaps. Run with `--check` in CI; writes
`docs/reports/current/callback_risk_audit.md`.

Current audit summaries (2026-07-19):
- `audit_handler_map.py`: **87 message handlers**, **512 callback handlers** found by static registration scan.
- `audit_handler_group_topology.py`: **610 startup handlers** from real in-memory registration: **521 callback**, **87 message**, **1 raw**, and **1 chat-member** handler.

Re-run both audits after handler registration, group, listener, or module-order changes.
