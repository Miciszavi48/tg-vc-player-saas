# UX Flows — Telegram Music Player Bot

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Complete step-by-step user experience documentation for every feature.
> Aligned with the three specification documents (priority order):
> 1. `music_bot_missing_items_addendum.md`
> 2. `music_bot_frontend_UI_FA_BI.md`
> 3. `music_bot_complete_docs_EN_FINAL.md`
>
> **Implementation status (2026-07-19): PARTIAL.** This is a product-flow specification. Core navigation, panels, callbacks, and cancel behavior are implemented, but individual flows may be deferred or superseded. Current handler reachability and user-visible behavior are authoritative in `docs/features/`, `docs/architecture/handler_routing.md`, and the generated callback matrix.

---

## A) Global Navigation Rules

### A.1 Back / Home / Cancel

| Button | Label key | callback_data | Behavior |
|---|---|---|---|
| **Back** | `common.buttons.back` | `nav:back` | Return to the previous screen. In PM: role-based panel. In group: group panel. |
| **Close** | `common.buttons.close` | `nav:close` | Delete the inline-keyboard message entirely. |
| **Cancel** | `common.buttons.cancel` / `common.buttons.cancel_inline` | `wz:cancel:<return_to>` or flow-specific cancel callbacks | Cancel any active ask() conversation, clear session state, and return to the relevant panel. |

**Home** is the role-based root:
- Developer → Developer Panel (`panels.developer.title`)
- Owner → Owner Panel (`panels.owner.title`)
- Sudo → Sudo Panel (`panels.sudo.title`)
- Regular user → Start Menu (`start.welcome`)
- Group context → Group Panel (`panels.group.title`)

Typed `cancel`, `/cancel`, and `لغو` remain hidden compatibility fallbacks for active ask/wizard flows. Visible prompts should direct users to inline Back/Cancel buttons instead of instructing them to type a command.

### A.2 Context Rules

| Context | Entry point | Panels shown | Inline keyboards |
|---|---|---|---|
| **PM (Private Message)** | `/start` command | Developer / Owner / Sudo / Start Menu | All admin panels |
| **Group** | `/settings` command or text commands | Group Panel, Playback controls | Settings, playback, management |
| **Channel** | Bot added event | No interactive panel (channels cannot receive inline KB clicks) | Install message only |

**Never mix**: PM handlers must not appear in group context and vice versa. Cross-context actions (e.g., a sudo charges credit from PM for a group) are done by asking for a `chat_id` in PM.

### A.3 Pagination Rules

For any list that may exceed one screen:
1. Show items 1–N (default N=20 for lists, N=8 for satellite channels).
2. Show `[Prev]` if page > 0, `[Next]` if more items exist.
3. Always show `[Back]` at the bottom of paginated lists.
4. Callback format: `{base}:page:{N}` (e.g., `pb:sat:page:2`).
5. Current page number shown in text: `t("tv_radio.sat_page", page=P, total=T)`.

### A.4 Error Handling Rules

| Error | User sees | Key | Behavior |
|---|---|---|---|
| Permission denied | "You do not have access." | `common.errors.no_access` | Alert popup, no navigation change |
| Invalid input | "Invalid number." | `common.errors.invalid_number` | Stay on same screen, ask again |
| Timeout (ask) | "Response timed out." | `ask.timeout` | Return to Home |
| FloodWait | "Telegram rate limit." | `common.errors.flood_wait` | Alert popup |
| Not installed | "Player is not installed." | `common.errors.not_installed` | Alert popup |
| Helper unavailable | "Helper is unavailable." | `common.errors.helper_unavailable` | Alert popup |

### A.5 Confirmation Rules for Destructive Actions

These actions require confirmation before execution:
- Leave group/channel (dev/sudo panels)
- Blacklist a user/group/channel
- Clear all admins/owners/VIPs
- Wallet top-up (shows amount before confirming)
- Deduct credit

Pattern: Action button → Confirmation message with `[Confirm]` + `[Cancel]` → Execute or cancel.

> **NOTE:** The current code does NOT implement confirmation steps for destructive actions. This is marked where applicable. Adding confirmation is recommended.

---

## B) Entry Points

### B.1 `/start` in PM

- **Context:** PM
- **Who:** Any user
- **Flow:**
  1. User sends `/start` to the bot.
  2. Bot calls `user_repo.upsert_user()` to save/update user record.
  3. Bot checks force-join requirement. If user has not joined required channels → show force-join prompt with channel buttons + "Check" button (`start:force_join`). Stop.
  4. Bot detects user role:
     - `is_developer(user_id)` (configured `DEVELOPER_ID` env, comma-separated allowed) → Developer
     - `user_repo.is_owner()` → Owner
     - `user_repo.is_sudo()` → Sudo
     - Otherwise → Regular user
  5. Bot sends welcome message + role-appropriate keyboard:
     - Developer → Developer Panel keyboard
     - Owner → Owner Panel keyboard
     - Sudo → Sudo Panel keyboard
     - Regular → Start Menu keyboard (URL buttons)
- **Handler:** `app/handlers/start.py::start_handler`
- **Tests:** `test_permissions_matrix.py`

### B.2 Bot Added to Group

- **Context:** Group
- **Who:** Any Telegram admin who adds the bot
- **Flow:**
  1. `ChatMemberUpdated` event fires (or `new_chat_members` fallback).
  2. Bot acquires distributed lock `install:{chat_id}`.
  3. Bot checks blacklist → if blacklisted, sends `install.blacklisted`, leaves.
  4. Bot checks member count limit → if exceeded, sends `install.member_limit`, leaves.
  5. Bot determines installer role and computes install cost via `InstallPolicyService`.
  6. If cost > 0 and installer is not Developer/Owner/Sudo with wallet → reject (`install.paid_no_permission`), leave.
  7. If cost > 0 and installer is Sudo → deduct from wallet atomically.
  8. Bot calls `upsert_group()` (or `upsert_channel()` for channels).
  9. Bot creates default chat settings.
  10. Bot activates trial credit (`CreditService.activate_trial()`).
  11. Bot sends install success message + trial info.
  12. Bot logs to `install_logs` table.
  13. Bot sends notification to log channel.
- **Handler:** `app/handlers/install.py::_on_new_install` → `_do_install`
- **Tests:** `test_install_flow_trial_credit.py`, `test_gap_closures.py::TestInstallPolicyEnforcement`

### B.3 Bot Added to Channel

- **Context:** Channel
- **Flow:** Same as group install (B.2), but uses `channel_repo.upsert_channel()`.
- **Note:** Channels cannot have inline keyboards clicked by channel admins (Telegram limitation). The install confirmation is a text-only message.

### B.4 Role Detection Summary

| Role | Detection | Panel shown | Context |
|---|---|---|---|
| Developer | `is_developer(user_id)` | Developer Panel | PM only |
| Owner | `owners` table, `is_active=True` | Owner Panel | PM only |
| Sudo | `sudos` table, `is_active=True` | Sudo Panel | PM only |
| Group Admin | `player_owners` / `music_admins` / `video_admins` tables | Group Panel | Group only |
| Regular | Default | Start Menu | PM only |

---

## C) Developer Panel Flows (PM only)

> **Who can use:** Developer only (`dev_filter() + @developer_only`)
> **Home target:** Developer Panel screen
> **Entry:** `/start` in PM → detected as Developer → shows Developer Panel keyboard

All flows below share:
- **Context:** PM
- **Permission:** Developer
- **Home:** Developer Panel (return via `nav:back`)

### C.1 Bot Status

- **Screen:** Dev Panel → Status
- **Trigger:** Button `panels.developer.status` → `dev:status`
- **Steps:**
  1. Press "وضعیت ربات" button.
  2. Bot queries: active groups count, channels count, users count, sudos count, helpers count, active calls count.
  3. Bot shows formatted text using `status.bot_info` key with all counts.
  4. Keyboard: full Developer Panel remains.
- **Handler:** `dev_panel.py::dev_status`

### C.2 Increase Credit

- **Screen:** Dev Panel → Increase Credit
- **Trigger:** Button `panels.developer.increase_credit` → `dev:credit:inc`
- **Steps:**
  1. Press "افزایش اعتبار" button.
  2. Bot asks: "Enter chat ID" (`ask.chat_id`).
  3. User sends a number (group/channel chat_id).
  4. Bot validates number → if invalid, shows `common.errors.invalid_number`, stops.
  5. Bot asks: "Enter days" (`ask.amount_days`).
  6. User sends a number.
  7. Bot validates → if invalid, error message, stops.
  8. Bot calls `CreditService.charge(target_id, "group", days)`.
  9. Bot sends success message: `credit.charged`.
- **Back:** Developer Panel
- **Handler:** `dev_panel.py::dev_increase_credit`

### C.3 Decrease Credit

- Same flow as C.2 but calls `CreditService.deduct()`.
- **Trigger:** `dev:credit:dec`
- **Success:** `credit.deducted`
- **Recommendation:** Should have confirmation step (not currently implemented).
- **Handler:** `dev_panel.py::dev_decrease_credit`

### C.4 Send Invoice

- **Trigger:** `dev:invoice`
- **Steps:** Same as Increase Credit — asks chat_id and days, then calls `CreditService.charge()`.
- **Handler:** `dev_panel.py::dev_send_invoice`

### C.5–C.8 Rate Settings (Base / Music / Video / Call Security)

- **Trigger:** `dev:rate:base` / `dev:rate:music` / `dev:rate:video` / `dev:rate:call_security`
- **Steps:**
  1. Press the rate button.
  2. Bot asks: "Enter new rate" (`ask.rate_value`).
  3. User sends a number.
  4. Bot validates, saves to `bot_settings` table.
  5. Bot sends: `status.rate_updated`.
- **Handler:** `dev_panel.py::_set_rate` (shared helper)

### C.9–C.14 Broadcast / Forward (Group / Private / Channel)

- **Triggers:** `dev:bc:group`, `dev:fw:group`, `dev:bc:private`, `dev:fw:private`, `dev:bc:channel`, `dev:fw:channel`
- **Steps:**
  1. Press broadcast/forward button.
  2. Bot asks: "Send the message" (`ask.broadcast_msg`).
  3. User sends a message (text, photo, video, etc.).
  4. Bot sends `broadcast.started`.
  5. Bot iterates through target chats, copies/forwards the message.
  6. Bot sends stats: `broadcast.stats` with sent/failed/total.
- **Handler:** `dev_panel.py::_handle_broadcast`

### C.15 Force Join Toggle

- **Trigger:** `dev:force_join`
- **Steps:** Press → toggle `force_join_enabled` in `bot_settings` → alert `status.toggled_on/off`.
- **Handler:** `dev_panel.py::dev_force_join_toggle`

### C.16–C.17 Channel Security Toggle

- **Trigger:** `dev:channel_security`
- **Steps:** Press → toggle `channel_security_enabled` → alert.
- **Note:** Single toggle covers enable and disable.
- **Handler:** `dev_panel.py::dev_channel_security_toggle`

### C.18 Media Policy

- **Trigger:** `dev:media_policy`
- **Steps:** Press → ask text input → save to `bot_settings.media_policy`.
- **Handler:** `dev_panel.py::dev_set_media_policy`

### C.19 Auto-Leave Toggle

- **Trigger:** `dev:auto_leave`
- **Steps:** Press → toggle `auto_leave_enabled` → alert.
- **Handler:** `dev_panel.py::dev_auto_leave_toggle`

### C.20 Trial Toggle

- **Trigger:** `dev:trial`
- **Steps:** Press → toggle `trial_enabled` → alert.
- **Handler:** `dev_panel.py::dev_trial_toggle`

### C.21 List Groups (with credit, link, leave)

- **Trigger:** `dev:list:groups`
- **Steps:**
  1. Press "لیست گروه ها" button.
  2. Bot queries all active groups (up to 50).
  3. For each group: shows title, credit days, invite link.
  4. Format: `list_fmt.group_item` per line.
  5. Keyboard: Developer Panel.
- **Handler:** `dev_panel.py::dev_list_groups`

### C.22 List Channels

- **Trigger:** `dev:list:channels`
- **Flow:** Same pattern as C.21 but queries `channels` table.
- **Handler:** `dev_panel.py::dev_list_channels`

### C.23 List No-Credit Groups/Channels

- **Trigger:** `dev:list:no_credit`
- **Steps:** Query `group_credits` where `credit_days <= 0` → show list.
- **Handler:** `dev_panel.py::dev_list_no_credit`

### C.24–C.26 Filter Words (Add / Remove / List)

- **Trigger:** `dev:filters`
- **Steps (unified handler):**
  1. Press "مدیریت کلمات فیلتر".
  2. Bot shows current filter word list (or "empty" message).
  3. Bot asks: "Enter word" (`ask.word_input`).
  4. User sends:
     - A word → Bot adds it to filter list. Shows `filter_mgmt.added`.
     - `-word` (prefixed with `-`) → Bot removes it. Shows `filter_mgmt.removed`.
  5. Redis cache invalidated.
- **Handler:** `dev_panel.py::dev_filters`

### C.27–C.29 Force Join (Add / Remove / List)

- **Trigger:** `dev:force_join_manage`
- **Steps (unified):**
  1. Press "مدیریت عضویت اجبار".
  2. Bot shows current force-join channel list.
  3. Bot asks: "Enter channel ID" (`ask.channel_id`).
  4. User sends:
     - A number → Bot adds channel. Shows `force_join_mgmt.added`.
     - `-number` → Bot removes channel. Shows `force_join_mgmt.removed`.
- **Handler:** `dev_panel.py::dev_force_join_manage`

### C.30–C.32 Sudo Management (Add / Remove / List)

- **Trigger:** `dev:sudo_manage`
- **Steps (unified):**
  1. Press "مدیریت سودوها".
  2. Bot shows current sudo list.
  3. Bot asks: "Enter user ID" (`ask.user_id`).
  4. User sends:
     - A number → Bot adds sudo. Shows `sudo_mgmt.added`.
     - `-number` → Bot removes sudo. Shows `sudo_mgmt.removed`.
  5. Redis sudolist cache invalidated.
- **Handler:** `dev_panel.py::dev_sudo_manage`

### C.33 Set Owner

- **Trigger:** `dev:set_owner`
- **Steps:** Ask user ID → validate → call `user_repo.add_owner()` → show `owner_mgmt.set`.
- **Handler:** `dev_panel.py::dev_set_owner`

### C.34–C.45 Texts & Links (12 fields)

- **Trigger:** `dev:texts_links`
- **Steps:**
  1. Press "تنظیم متن ها و لینک ها".
  2. Bot shows all 12 text/link fields with current values.
  3. Bot asks: "Enter text" (`ask.text_input`).
  4. User sends: `field_name value` (e.g., `start_text Hello World`).
  5. Bot validates field name exists, saves to `bot_settings`, shows `texts_links.updated`.
- **Fields:** `start_text`, `helper_text`, `about_text`, `developer_link`, `bot_channel_link`, `support_group_link`, `guide_channel_link`, `developer_pv_link`, `broadcast_channel_link`, `custom_link`, `tariff_text`, `helper_start_text`
- **Handler:** `dev_panel.py::dev_texts_links`

### C.46–C.48 Sudo Link Management

- **Triggers:** `dev:sudo:link:set` / `dev:sudo:link:rm` / `dev:sudo:link:list`
- **Status:** **PARTIAL IN CODE** — CB constants exist in `ui.py` but NO handler is registered. The `Sudo.sudo_link` DB column exists.
- **Spec ref:** Main spec §10 Developer Panel, UI spec mentions sudo link management.
- **Expected flow:**
  1. Set: Ask sudo user_id → ask link URL → save to `sudos.sudo_link`.
  2. Remove: Ask sudo user_id → set `sudo_link = NULL`.
  3. List: Query all active sudos with non-null `sudo_link` → display list.

### C.49 Install Limits

- **Trigger:** `dev:install_limits`
- **Steps:** Ask limit value → save to `bot_settings.max_group_members`.
- **Handler:** `dev_panel.py::dev_install_limits`

### C.50 Blacklist (Block / Unblock)

- **Trigger:** `dev:blacklist`
- **Steps (unified):**
  1. Press "مسدود/آزاد".
  2. Bot asks: "Enter user ID" (`ask.user_id`).
  3. User sends:
     - A number → Block. Shows `blacklist_mgmt.blocked`.
     - `-number` → Unblock. Shows `blacklist_mgmt.unblocked`.
- **Handler:** `dev_panel.py::dev_blacklist`

### C.51 Set Log Channel

- **Trigger:** `dev:log_channel`
- **Steps:** Ask channel ID → validate → save to `bot_settings.log_channel_id`.
- **Handler:** `dev_panel.py::dev_set_log_channel`

### C.52 Install Policy Panel

- **Trigger:** `dev:install_policy`
- **Steps:**
  1. Press "سیاست نصب".
  2. Bot shows current policy mode.
  3. Sub-menu with: Set Mode, Set Trial Days, Set Group Fee, Set Channel Fee, Whitelist.
- **Sub-flows:**
  - **Set Mode** (`dev:install_policy:mode`): Ask for mode (paid/free/hybrid/open) → save.
  - **Set Trial** (`dev:install_policy:trial`): Ask days → save.
  - **Set Group Fee** (`dev:install_policy:fee_group`): Ask amount → save.
  - **Set Channel Fee** (`dev:install_policy:fee_chan`): Ask amount → save.
  - **Whitelist** (`dev:install_policy:whitelist`): Sub-menu with Add/Remove/List.
    - Add (`dev:install_policy:wl_add`): Ask chat_id → add to whitelist.
    - Remove (`dev:install_policy:wl_rm`): Ask chat_id → remove.
    - List (`dev:install_policy:wl_list`): Show all whitelist entries.
- **Handler:** `dev_panel.py::dev_install_policy` + 7 sub-handlers

---

## D) Owner Panel Flows (PM only)

> **Who can use:** Owner or Developer (`owner_filter() + @owner_or_above`)
> **Home target:** Owner Panel screen
> **Entry:** `/start` in PM → detected as Owner

Owner Panel mirrors most Developer Panel features EXCEPT:
- ❌ No "Set Owner" (only developer can set owner)
- ❌ No "Set Log Channel" (developer only)
- ❌ No rate setting (base/music/video/call security)
- ❌ No "Send Invoice" button
- ❌ No Install Policy management
- ❌ **No Texts/Links management** — **MISSING IN CODE** (spec requires it; only dev_panel has `DEV_TEXTS_LINKS`)

Owner Panel HAS these extras not in Dev Panel:
- ✅ Bot credit display
- ✅ Increase bot credit
- ✅ Bot invoices list
- ✅ Sudo wallet top-up
- ✅ Install reports
- ✅ No-credit reports
- ✅ Sales report (`own:sales_report`) — CB + button exist, **handler PARTIAL IN CODE** (no query logic)

### D.1–D.2 Stats / Users

- `own:stats` → same format as dev_status
- `own:users` → shows total user count

### D.3–D.4 List Groups / Channels

- `own:list:groups` and `own:list:channels` → same as Dev versions

### D.5–D.10 Broadcast / Forward

- Same 6 handlers as Dev panel with `own:` prefix

### D.11–D.17 Toggles and Settings

- Force join, channel security, media policy, auto-leave, trial → same pattern

### D.18–D.20 List Groups Credit / Channels Credit / No-Credit

- `own:list_groups_credit`, `own:list_channels_credit`, `own:list:no_credit`

### D.21–D.29 Filter / Force Join / Sudo Management

- Same unified handlers as Dev with `own:` prefix

### D.30 Install Reports

- **Trigger:** `own:install_reports`
- **Steps:** Query `install_logs` (all, up to 50) → show formatted list.
- **Handler:** `owner_panel.py::own_install_reports`

### D.31 No-Credit Reports

- **Trigger:** `own:no_credit_reports`
- **Steps:** Query zero-credit chats → show list.
- **Handler:** `owner_panel.py::own_no_credit_reports`

### D.32 Bot Credit Display

- **Trigger:** `own:bot_credit`
- **Steps:** Read `bot_settings.bot_credit` → show in alert.
- **Handler:** `owner_panel.py::own_bot_credit`

### D.33 Increase Bot Credit

- **Trigger:** `own:bot_credit:inc`
- **Steps:** Ask amount → add to current → save.
- **Handler:** `owner_panel.py::own_increase_bot_credit`

### D.34 Bot Invoices

- **Trigger:** `own:bot_invoices`
- **Steps:** Query recent invoices → show formatted list.
- **Handler:** `owner_panel.py::own_bot_invoices`

### D.35 Sudo Wallet Top-Up

- **Trigger:** `own:topup_sudo`
- **Steps:**
  1. Ask sudo user ID.
  2. Validate sudo exists and is active.
  3. Ask amount.
  4. Validate amount > 0.
  5. Acquire wallet lock.
  6. Atomically: create/update wallet + create transaction.
  7. Send success message to owner.
  8. DM sudo: `wallet.topup_notification`.
- **Handler:** `owner_panel.py::own_topup_sudo_wallet`

### D.36 Owner Texts/Links Management

- **Status:** **MISSING IN CODE**
- **Spec ref:** Owner spec lists the same text/link fields as Developer. No `OWN_TEXTS_LINKS` CB constant exists, no handler.
- **Expected:** Same flow as C.34–C.45 but with `own:` prefix and `@owner_or_above` permission.

### D.37–D.39 Sudo Link Management

- **Status:** **PARTIAL IN CODE** — Same as Dev panel: CB constants exist, no handlers.

### D.40 Sales Report

- **Trigger:** `own:sales_report`
- **Status:** **PARTIAL IN CODE** — CB constant and keyboard button exist, but NO callback handler is registered. `OwnerSale` model exists in DB.
- **Expected flow:** Query `owner_sales` table for current owner → show formatted list with totals.

---

## E) Sudo Panel Flows (PM only)

> **Who can use:** Sudo, Owner, or Developer (`sudo_filter() + @sudo_or_above`)
> **Home target:** Sudo Panel screen
> **Entry:** `/start` in PM → detected as Sudo

### E.1 Install Reports (Own Installs)

- **Trigger:** `sudo:installs_report`
- **Steps:**
  1. Query `install_logs` filtered by `sudo_id = current_user`.
  2. Count groups and channels.
  3. Show using `reports.install_report`.
- **Handler:** `sudo_panel.py::sudo_installs_report`

### E.2 Credit Report (Own Installs)

- **Trigger:** `sudo:credit_report`
- **Steps:**
  1. Get chat_ids from own install_logs.
  2. For each, get credit balance.
  3. Show formatted list.
- **Handler:** `sudo_panel.py::sudo_credit_report`

### E.3 Stats (Own Installs)

- **Trigger:** `sudo:stats`
- **Steps:** Count groups/channels from own install_logs → show totals.
- **Handler:** `sudo_panel.py::sudo_stats`

### E.4 Leave Own Installs

- **Trigger:** `sudo:leave_installs`
- **Steps:**
  1. Get all chat_ids from own install_logs where `action=install`.
  2. For each: leave voice chat, leave chat, deactivate group.
  3. Show count of left chats.
- **Recommendation:** Should have confirmation step.
- **Handler:** `sudo_panel.py::sudo_leave_installs`

### E.5 Low Credit Alerts

- **Trigger:** `sudo:low_credit`
- **Steps:**
  1. Get chat_ids from own install_logs.
  2. Filter those with `credit_days <= 3`.
  3. Show formatted list.
- **Handler:** `sudo_panel.py::sudo_low_credit`

### E.6 In-Group Credit Command (Cross-Context)

- **Context:** Group (not PM)
- **Trigger:** Text message matching `آپدیت شارژ N` or `update charge N` in a group.
- **Who:** Sudo or above
- **Steps:**
  1. User types `آپدیت شارژ 30` in the group.
  2. Bot checks: is user sudo or above?
  3. If Developer/Owner: directly charge.
  4. If Sudo: charge from wallet (atomic deduction + credit add).
  5. Send success or insufficient wallet error.
- **Handler:** `app/handlers/credit_commands.py::handle_charge_music`
- **Tests:** `test_batch1.py`

---

## F) Group Panel Flows (Group only)

> **Context:** Group chat only
> **Entry:** `/settings` command or callback from another group screen
> **Who:** Group music admin or above (`music_admin_filter() + @group_music_admin`)
> **Home target:** Group Panel main screen

### F.1 Group Panel Main Screen

- **Trigger:** `/settings` command OR `grp:settings` (from sub-screen Back)
- **Buttons:** Settings | Management | Help | Support | Close
- **Handler:** `group_panel.py::settings_command`

### F.2 Settings (Toggle Flows)

Each setting is a toggle button in the Settings sub-screen.

- **Entry:** Press "تنظیمات" → `grp:settings`
- **Screen:** Shows all toggles with current `[On/Off]` status.
- **Toggle action:** Press any toggle → value flips in DB → keyboard refreshes with new state.
- **Back:** Group Panel

| Feature | CB constant | DB field | Handler |
|---|---|---|---|
| Music Video on/off | `grp:set:music_video` | `video_enabled` | `grp_toggle_setting` |
| Security Call on/off | `grp:set:security_call` | `security_call_enabled` | same |
| Repeat on/off | `grp:set:repeat` | `vote_skip_enabled` | same |
| Download for users | `grp:set:download_users` | `download_enabled` | same |
| Call message on/off | `grp:set:call_message` | `announce_enabled` | same |
| Auto-clean on/off | `grp:set:auto_clean` | `filter_enabled` | same |
| Queue on/off | `grp:set:queue` | `smart_radio_enabled` | same |
| Auto-ready call | `grp:set:auto_ready_call` | `auto_leave_enabled` | same |
| Call report on/off | `grp:set:call_report` | `buttons_enabled` | same |
| Record call on/off | `grp:set:record_call` | `lyrics_enabled` | same |
| Show ID | `grp:set:show_id` | `inline_enabled` | same |
| Show photo | `grp:set:show_photo` | `soundcloud_enabled` | same |
| Show text | `grp:set:show_text` | `spotify_enabled` | same |
| Default media type | `grp:set:default_media` | (cycles audio/video) | `grp_toggle_default_media` |

### F.3 Management

- **Entry:** Press "مدیریت" → `grp:management`
- **Screen:** Owners List | Admins List | VIP List | Clear All | Back
- **Sub-flows:**
  - **Owners List** (`grp:mgmt:owners`): Query `player_owners` for this chat → show list. Back → Management.
  - **Admins List** (`grp:mgmt:admins`): Query `music_admins` + `video_admins` → show combined. Back → Management.
  - **VIP List** (`grp:mgmt:vip`): Query `player_vips` → show list. Back → Management.
  - **Clear All** (`grp:mgmt:clear_all`): Clear `music_admins` + `video_admins` for this chat. Alert "Updated".

### F.4 Help

- **Entry:** Press "راهنما پلیر" → `grp:help`
- **Screen:** Promote/Demote | Play Commands | General Commands | Support Request | Back
- **Sub-flows:**
  - **Promote/Demote** (`grp:help:promote_demote`): Show `help_content.promote_demote` text. Back → Help.
  - **Play Commands** (`grp:help:play_commands`): Show `help_content.play_commands`. Back → Help.
  - **General Commands** (`grp:help:general_commands`): Show `help_content.general_commands`. Back → Help.
  - **Support Request** (`grp:help:support_request`): Show support request text. Back → Help.
- **Note:** Help section has NO permission filter — any group member can view.

### F.5 Support

- **Entry:** Press "پشتیبانی ها" → `grp:support`
- **Screen:** Creator | Sudo | Guide Channel | Support Group | Back
- **Sub-flows:**
  - **Creator** (`grp:support:creator`): Show developer link in alert popup.
  - **Sudo** (`grp:support:sudo`): Show player owners for this chat in alert.
  - **Guide Channel** (`grp:support:guide_channel`): Show guide channel link in alert.
  - **Support Group** (`grp:support:support_group`): Show support group link in alert.
- **Note:** No permission filter — any group member can view.

---

## G) Start Menu Flows (PM only)

> **Context:** PM
> **Who:** Regular users (non-admin)
> **Home:** Start Menu
> **Entry:** `/start` when role = regular

### G.1 Start Menu Screen

Shows welcome message + inline keyboard with URL buttons and one callback button.

| Button | Type | Target |
|---|---|---|
| Force Join Status | Callback `start:force_join` | Check/show force-join channels |
| Pricing | Callback `start:pricing` | Show rates in alert popup |
| Buy from Creator | URL | `bot_settings.developer_link` |
| Buy from Sudo 1 | URL | `bot_settings.developer_link` (or sudo_1 link) |
| Buy from Sudo 2 | URL | same |
| Guide Channel | URL | `bot_settings.guide_channel_link` |
| Bot Channel | URL | `bot_settings.bot_channel_link` |
| Support Group | URL | `bot_settings.support_group_link` |
| Custom Link | URL | `bot_settings.custom_link` |
| Add to Group | URL | `https://t.me/{bot_username}?startgroup=true` |
| Add to Channel | URL | `https://t.me/{bot_username}?startchannel=true` |

### G.2 About

- **Status:** **MISSING IN CODE** — JSON key `start.menu.about` exists but no `START_ABOUT` CB constant, no button in keyboard, no handler.
- **Spec ref:** UI spec §7.1 lists `start.menu.about`.

### G.3 Force Join Check

- **Trigger:** `start:force_join`
- **Steps:**
  1. Bot lists force-join channels with "Join" URL buttons.
  2. User joins channels, comes back, presses "Check" button.
  3. Bot verifies membership for each channel.
  4. If all joined → proceed to start menu.
  5. If not → show remaining channels.
- **Handler:** `app/handlers/force_join.py::on_check_force_join`

### G.4 Pricing

- **Trigger:** `start:pricing`
- **Steps:** Bot reads base/music/video rates from `bot_settings` → shows in alert popup.
- **Handler:** `app/handlers/callbacks.py::start_pricing`

---

## H) Playback UX Flows (Group only)

> **Context:** Group chat
> **Who:** Users with appropriate permissions (music admin or above, or any user if `download_enabled`)
> **Prerequisites:** Bot must be admin in group. Group must have credit > 0.

### H.1 Playback Type Selection Menu

- **Trigger:** The `playback_type_menu` keyboard is built by `KeyboardFactory.playback_type_menu()`.
- **Screen:** 6 buttons in a 2×3 grid + Back
  - Audio (`pb:type:audio`) | Video (`pb:type:video`)
  - TV (`pb:type:tv`) | Satellite (`pb:type:satellite`)
  - Radio (`pb:type:radio`) | Download (`pb:type:download`)
  - Back (`nav:back`)

### H.2 Audio Play Flow

- **Trigger:** Text command `پخش <query>` or `play <query>` in group
- **Steps:**
  1. Bot checks prerequisites (admin status, credit, user permission).
  2. Bot parses source: from command text, reply-to-audio, reply-to-link.
  3. If source is a URL → resolve via `MediaService.get_stream_url()`.
  4. Bot joins voice chat and starts audio stream.
  5. Bot sends "Playing audio" message with now-playing controls keyboard.
  6. If dedication target specified (`پخش @user <query>`) → append dedication text.
- **Handler:** `playback.py::play_audio`
- **Tests:** `test_playback_controls.py`

### H.3 Video Play Flow

- **Trigger:** `پخش ویدیو <query>` or `playvideo <query>`
- **Steps:** Same as audio but media_type = "video".
- **Handler:** `playback.py::play_video`

### H.4 TV Flow

- **Trigger:** `پخش تیوی` text command OR `pb:type:tv` callback
- **Steps:**
  1. Load `tv_channels.json`.
  2. Show channel list as inline keyboard buttons.
  3. User presses a channel → `pb:tv:{channel_id}`.
  4. Bot joins voice chat with channel stream URL.
  5. Shows "Playing: {channel_name}".
- **Handler:** `callbacks.py::pb_tv` + `tv_radio.py::on_tv_select`

### H.5 Radio Flow

- **Trigger:** `pb:type:radio` callback
- **Steps:**
  1. Load `radio_stations.json`.
  2. Show station list as inline buttons.
  3. User presses a station → `pb:radio:{station_id}`.
  4. Bot joins voice chat with station stream URL (audio).
  5. Shows "Playing: {station_name}".
- **Handler:** `tv_radio.py::on_radio_menu` + `on_radio_select`

### H.6 Satellite Flow

- **Trigger:** `pb:type:satellite` callback
- **Steps:**
  1. Load `satellite_channels.json`.
  2. Show paginated channel list (8 per page).
  3. Navigation: Prev/Next page buttons if applicable.
  4. User presses a channel → `pb:sat:{channel_id}`.
  5. Bot joins voice chat with satellite stream URL (video).
  6. Shows "Playing: {channel_name}".
- **Handler:** `tv_radio.py::on_satellite_menu` + `on_satellite_select`
- **Country selection:** **MISSING IN CODE** — radio JSON has `country` field but no country filter UI.

### H.7 Download Media Flow

- **Trigger:** `pb:type:download` callback OR `/download` command
- **Steps:**
  1. User sends `/download` as reply to an audio/video message.
  2. Bot checks: is download enabled for this group? (`download_enabled` setting)
  3. Bot downloads the media file.
  4. Bot sends file back to user.
- **Handler:** `app/handlers/download.py`

### H.8 Replay-on-Reply Flow

- **Trigger:** `پخش ریپلای` or `replayreply` text command
- **Steps:**
  1. User replies to a message containing audio/video/voice/document/link.
  2. Bot checks prerequisites.
  3. Bot extracts media from the replied message.
  4. Bot determines media type (audio or video).
  5. Bot joins voice chat and starts streaming.
  6. Shows now-playing controls.
- **Handler:** `playback.py::replay_on_reply`
- **Tests:** `test_batch3.py::TestG4ReplayOnReply`

### H.9 Now Playing Controls

Shown after any successful play command as an inline keyboard on the "Now Playing" message.

| Button | CB | Handler | Behavior |
|---|---|---|---|
| Volume Down | `pb:vol:-` | `callbacks.py::pb_vol_down` | Set volume to 80 |
| Volume Up | `pb:vol:+` | `callbacks.py::pb_vol_up` | Set volume to 120 |
| Speed Down | `pb:speed:-` | `callbacks.py::pb_speed_down` | Answer with label (stub) |
| Speed Up | `pb:speed:+` | `callbacks.py::pb_speed_up` | Answer with label (stub) |
| Previous | `pb:prev` | `callbacks.py::pb_prev` | Answer with label (stub) |
| Next | `pb:next` | `callbacks.py::pb_next` | Play next in queue |
| Repeat Toggle | `pb:repeat` | `callbacks.py::pb_repeat` | Answer with label (**PARTIAL** — does not toggle state) |
| Add to Favorites | `pb:fav:add` | `callbacks.py::pb_fav_add` | Answer with label (**PARTIAL** — does not persist to DB) |
| Play Favorites | `pb:fav:play` | `callbacks.py::pb_fav_play` | Answer with label (**PARTIAL** — does not query/play) |
| Pause | `pb:pause` | `callbacks.py::pb_pause` | Pause playback |
| Resume | `pb:resume` | `callbacks.py::pb_resume` | Resume playback |
| Stop | `pb:stop` | `callbacks.py::pb_stop` | Leave voice chat, delete message |

- **Back:** From now-playing, `nav:back` returns to group panel (or deletes message context).
- **Metadata display (artist, duration, requested_by):** **PARTIAL IN CODE** — JSON keys exist (`playback.track_card.*`) but handler sends simple status text without metadata extraction.

---

## I) Trial + Install Post-Install Panel Flows

### I.1 3-Day Trial Activation

- **Context:** Group/Channel (automatic on bot install)
- **Who:** Automatic — triggered when bot becomes admin.
- **Flow:**
  1. Bot is added to group and becomes member/admin.
  2. Install handler triggers (see B.2).
  3. `CreditService.activate_trial()` is called.
  4. Trial days loaded from `install_policy_settings.trial_days` (default 3).
  5. `group_credits` row created with `credit_days=3, is_trial=True, trial_expire_at=now+3d`.
  6. Bot sends: `install.success` + `install.trial_activated`.
  7. All features are active during trial.
  8. When trial expires (checked by `check_trial_expiry` scheduler job every 18 min), status set to "expired".
- **Handler:** `install.py::_do_install` + `credit_service.py::activate_trial`
- **Tests:** `test_install_flow_trial_credit.py`

### I.2 Post-Install Panel

- **Status:** **PARTIAL IN CODE** — The spec describes a dedicated inline keyboard panel shown after installation by Owner or Sudo, containing credit/helper/panel/help buttons. Current code sends a text message only; no inline panel keyboard is assembled.
- **Spec ref:** Main spec §19 "Installation Flow & Trial Period"
- **What currently happens:** Text message with `install.success` + `install.trial_activated`.
- **What the spec expects:**

| Button | Expected behavior | Implementation status |
|---|---|---|
| Increase player credit | Charge credit in-group | **Available via text command** (`آپدیت شارژ N`) |
| Decrease player credit | Deduct credit | **Available via dev panel only** |
| Add helper | Add helper account | **CLI only** (`helper_pool_cli.py`) — **NOT in UI** |
| Player panel | Open group settings | **Available via** `/settings` |
| Player help | Open help section | **Available via** group panel Help button |
| Current credit display | Show current credit | **Available via** credit service (not in a dedicated button) |
| Charged by display | Show who charged | **PARTIAL** — DB field exists, not shown |
| Guide channel | Link to guide | **Available via** group support section |

---

## Flow Map

```
/start (PM)
├── Role: Developer → Developer Panel
│   ├── Status
│   ├── Credit (Increase / Decrease / Invoice)
│   ├── Rates (Base / Music / Video / Call Security / Music Sell / Video Sell)
│   ├── Broadcast (Group/Private/Channel × Copy/Forward)
│   ├── Toggles (Force Join / Channel Security / Auto-Leave / Trial)
│   ├── Media Policy
│   ├── Lists (Groups / Channels / No-Credit / Renewal)
│   ├── Filters (Add / Remove / List)
│   ├── Force Join (Add / Remove / List)
│   ├── Sudo Management (Add / Remove / List)
│   ├── Set Owner
│   ├── Texts & Links (12 fields)
│   ├── Sudo Link Set/Remove/List [PARTIAL - no handler]
│   ├── Install Limits
│   ├── Blacklist (Block / Unblock)
│   ├── Log Channel
│   ├── Install Policy
│   │   ├── Set Mode
│   │   ├── Set Trial Days
│   │   ├── Set Group Fee
│   │   ├── Set Channel Fee
│   │   └── Whitelist (Add / Remove / List)
│   ├── Leave Group
│   └── Send to Sudo
│
├── Role: Owner → Owner Panel
│   ├── Stats / Users
│   ├── Lists (Groups / Channels / Credit / No-Credit)
│   ├── Broadcast (same 6)
│   ├── Toggles (same 5)
│   ├── Filters / Force Join / Sudo (same)
│   ├── Install Limits / Blacklist
│   ├── Install Reports / No-Credit Reports
│   ├── Bot Credit / Increase Bot Credit / Bot Invoices
│   ├── Sudo Wallet Top-Up
│   ├── Sales Report [PARTIAL - no handler]
│   ├── Credit & Links
│   └── Texts & Links [MISSING - not in owner panel]
│
├── Role: Sudo → Sudo Panel
│   ├── Install Reports (own)
│   ├── Credit Report (own)
│   ├── Stats (own)
│   ├── Leave Installs (own)
│   └── Low Credit Alerts
│
└── Role: Regular → Start Menu
    ├── Force Join Check
    ├── Pricing
    ├── Buy from Creator (URL)
    ├── Buy from Sudo 1/2 (URL)
    ├── Guide/Bot Channel/Support (URLs)
    ├── Custom Link (URL)
    ├── Add to Group/Channel (URLs)
    └── About [MISSING]

/settings (Group)
└── Group Panel
    ├── Settings (14 toggles)
    ├── Management
    │   ├── Owners List
    │   ├── Admins List
    │   ├── VIP List
    │   └── Clear All
    ├── Help
    │   ├── Promote/Demote
    │   ├── Play Commands
    │   ├── General Commands
    │   └── Support Request
    └── Support
        ├── Creator Contact
        ├── Sudo Contact
        ├── Guide Channel
        └── Support Group

Playback (Group - text commands)
├── پخش / play → Audio play + Now Playing Controls
├── پخش ویدیو / playvideo → Video play
├── پخش ریپلای / replayreply → Replay-on-reply
├── پخش تیوی / playtv → TV menu → Channel selection
├── توقف / stop → Leave voice chat
├── مکث / pause → Pause
├── ازسرگیری / resume → Resume
├── بیصدا / باصدا → Mute/Unmute
├── صدای موزیک/ویدیو N → Set volume
├── پینگ / ping → Latency check
├── ربات / bot → Bot info
├── لیست مالکان / creatorslist → Show owners
├── آپدیت شارژ N → Charge credit (sudo+)
└── Playback Type Menu (callbacks)
    ├── Audio → (same as text command)
    ├── Video → (same)
    ├── TV → Channel list → Play
    ├── Satellite → Paginated list → Play
    ├── Radio → Station list → Play
    └── Download → Download flow

Bot Added (Group/Channel - automatic)
└── Install Flow
    ├── Blacklist check
    ├── Member limit check
    ├── Install policy cost check
    ├── Wallet deduction (if paid + sudo)
    ├── Upsert group/channel
    ├── Create settings
    ├── Activate trial
    ├── Send success message
    └── Log + notify
```

---

## Implementation Gap Summary

Items marked in this document that differ from the current codebase:

| Item | Section | Status |
|---|---|---|
| Sudo link set/remove/list handlers | C.46–C.48, D.37–D.39 | PARTIAL — CB exists, no handler |
| Owner texts/links management | D.36 | MISSING — no CB, no handler |
| Owner sales report handler | D.40 | PARTIAL — CB+button exist, no handler |
| Start menu "About" button | G.2 | MISSING — key exists, no CB/button/handler |
| Repeat toggle state persistence | H.9 | PARTIAL — stub handler |
| Favorites add/play persistence | H.9 | PARTIAL — stub handlers |
| Now-playing metadata (artist, duration, user) | H.9 | PARTIAL — keys exist, not wired |
| Post-install inline panel keyboard | I.2 | PARTIAL — text only, no inline buttons |
| Country selection for radio | H.6 | MISSING — no UI |
| Confirmation for destructive actions | A.5 | MISSING — no confirmation pattern |
