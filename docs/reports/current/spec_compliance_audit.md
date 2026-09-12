# Spec Compliance Audit

> **Record status (2026-07-19): VALID HISTORICAL SNAPSHOT.** The original date and conclusion below are preserved. Use the canonical documentation index and current source/tests for present runtime behavior.
## Gap Closure Batch - DB Start Settings, Role Gates, Helper Add Hardening (2026-02-27)

| Gap ID | Area | Status | Evidence | Test |
|--------|------|--------|----------|------|
| GAP-SET-1 | `/start` links/text still ENV-bound | DONE | `app/services/bot_settings_service.py` + `app/services/panel_router.py` + `app/handlers/callbacks.py` now read `bot_settings` first | `tests/test_start_db_settings.py::test_build_start_links_uses_db_values` |
| GAP-SET-2 | Empty public links should not produce broken URL buttons | DONE | `app/utils/ui.py:KeyboardFactory.start_menu` now conditionally renders URL rows only for configured links | `tests/test_start_db_settings.py::test_start_menu_hides_empty_link_buttons` |
| GAP-SET-3 | Group support link callbacks bypassed DB settings | DONE | `app/handlers/group_panel.py` uses DB link getters and localized not-configured fallback | `tests/test_ui_behavioral_e2e.py::test_category_pages_show_summary_not_empty` |
| GAP-ROLE-1 | Owner-gate accepted sudo users in filter layer | DONE | `app/utils/filters.py:owner_filter` now checks owner-only (+developer) | `tests/test_role_permissions.py::test_restricted_callback_denies_sudo_for_owner_actions` |
| GAP-ROLE-2 | Unauthorized callback attempts lacked explicit deny response | DONE | `app/utils/decorators.py` now returns localized no-access answer/reply for denied actions | `tests/test_role_permissions.py::test_restricted_callback_denies_non_developer` |
| GAP-HLP-1 | Helper add finalize had no concurrency lock | DONE | `app/handlers/helper_otp_wizard.py` uses `acquire_lock/release_lock` + `helper_add_lock_key` | `tests/test_helper_add_flow.py::test_helper_add_success` |
| GAP-HLP-2 | Helper add duplicate checks incomplete | DONE | OTP finalize now re-checks `phone`, `tg_user_id`, and existing session material before insert | `tests/test_helper_add_flow.py::test_helper_add_duplicate_rejected` |
| GAP-HLP-3 | Helper add cancel/success flows lacked explicit navigation affordances | DONE | OTP cancel/failure/success responses always include Back/Home; success includes helper-detail CTA | `tests/test_helper_add_flow.py::test_helper_add_cancel_returns_to_panel` |
| GAP-HLP-4 | Hardcoded helper panel labels/details | DONE | Added i18n keys: `admin.helpers.set_proxy_btn`, `detail_antiban`, `detail_proxy`, `open_detail_btn`, etc. | `tests/test_helper_management.py::test_helper_i18n_parity` |

## Gap Closure Batch - UI Hardening Pass (2026-02-27)

| Gap ID | Area | Status | Evidence | Test |
|--------|------|--------|----------|------|
| GAP-UIH-1 | TV/Radio/SAT post-action dead-ends | DONE | `app/handlers/tv_radio.py` now returns Back/Home on all success/failure paths, with now-playing controls retained | `tests/test_ui_hardening_pass.py::test_tv_radio_sat_success_has_navigation` |
| GAP-UIH-2 | Scheduled/recurring broadcast confirm dead-ends | DONE | `app/handlers/broadcast_wizard.py` now adds done navigation keyboard for scheduled + recurring confirmations | `tests/test_ui_hardening_pass.py::test_broadcast_confirm_scheduled_and_recurring_end_with_nav` |
| GAP-UIH-3 | Broadcast cancel did not restore menu | DONE | `bcw_cancel` now resolves and restores `TOKEN_DEV_BROADCAST` payload (menu + buttons) | `tests/test_ui_hardening_pass.py::test_broadcast_cancel_returns_menu_with_buttons` |
| GAP-UIH-4 | Legacy Owner flat-wall root reachable | DONE | `KeyboardFactory.owner_panel` converted to categorized root; Owner category callbacks wired in `app/handlers/owner_panel.py` | `tests/test_ui_behavioral_e2e.py::test_back_never_loads_legacy_keyboard` |
| GAP-UIH-5 | Hardcoded `fa` bypassed language settings | DONE | Added runtime language binding (`app/services/language_service.py`) + i18n runtime override (`app/utils/i18n.py`) + global binder in `app/handlers/callbacks.py` | `tests/test_ui_hardening_pass.py::test_language_resolution_prefers_chat_settings_en` |
| GAP-UIH-6 | Helper OTP/proxy wizard state collision risk | DONE | Added `helper_proxy_state_key` and switched proxy wizard state off OTP namespace | `tests/test_ui_hardening_pass.py::test_helper_wizard_states_are_isolated_and_cancel_scoped` |
| GAP-UIH-7 | Group settings callback order-dependence | DONE | Replaced generic `^grp:set:` route with explicit `_GROUP_SETTING_TOGGLE_REGEX` route | `tests/test_ui_hardening_pass.py::test_group_settings_regex_excludes_default_media_toggle` |
| GAP-UIH-8 | Cancel-path duplicate response risk | DONE | Local `/cancel` handlers now call `stop_propagation()` in helper OTP/proxy text flows | `tests/test_ui_hardening_pass.py::test_cancel_handlers_stop_propagation_without_duplicates` |
| GAP-UIH-9 | Callback coverage drift after owner/group route changes | DONE | Updated callback-audit logic to account for explicit group-toggle regex + new owner category routes | `tests/test_ui_audit.py::TestCallbackCoverage::test_all_keyboard_cbs_have_handlers` |

## Gap Closure Batch - UX/CRUD Wiring Audit (2026-02-27)

| Gap ID | Area | Status | Evidence | Test |
|--------|------|--------|----------|------|
| GAP-UX-1 | Missing dev callback wiring (`DEV_BLACKLIST`) | DONE | `app/handlers/dev_panel.py:dev_blacklist` wired to `blacklist_repo` list/add/remove path | `tests/test_ui_action_audit_regression.py::test_dev_blacklist_flow_has_navigation_buttons` |
| GAP-UX-2 | Owner dead-end admin flows | DONE | Owner handlers now return done nav keyboards after prompt-based operations (`own_set_media_policy`, `own_install_limits`, `own_filters`, `own_sudo_manage`, `own_blacklist`) | `tests/test_ui_action_audit_regression.py::test_owner_blacklist_flow_has_navigation_buttons` |
| GAP-UX-3 | Group category pages lacked state summary text | DONE | Group settings/management pages render computed status/count summaries | `tests/test_ui_action_audit_regression.py::test_group_management_page_has_summary_and_keyboard` |
| GAP-UX-4 | Global callback coverage drift | DONE | KeyboardFactory callbacks audited against handlers; only helper dynamic prefixes are intentionally regex-driven | `tests/test_ui_action_audit_regression.py::test_keyboard_callbacks_have_handler_coverage` |
| GAP-UX-5 | `t(..., key=...)` formatting runtime collision | DONE | Summary row rendering switched to template `.format(...)` in dev/owner/group/wizard summary builders | `tests/test_admin_wizard_navigation.py::test_cancel_returns_to_previous_menu` |

## Gap Closure Batch 1 — Core Flows

| Gap ID | Spec Ref | Feature | Status | Evidence | Test |
|--------|----------|---------|--------|----------|------|
| GAP-1 | §25 Auto-Leave | `auto_leave_check()` | DONE | `credit_service.py:auto_leave_check` | `test_auto_leave_triggers_when_enabled` |
| GAP-2 | §18+§25 | daily_deduct triggers auto-leave | DONE | `scheduler.py:midnight_credit_deduct` processes `credit:expired_pending_leave` set | `test_auto_leave_skips_when_disabled` |
| GAP-3 | §18 | charge_with_wallet wallet check | DONE (was already implemented) | `credit_service.py:134-136` raises ValueError | `test_charge_with_wallet_method_exists` |
| GAP-7 | UI | Dev panel entry for Help/Analytics/Helpers | DONE | `ui.py:developer_panel` adds HLP_HOME, AN_HOME, HELP_HOME | `test_dev_panel_has_help_analytics_helpers` |
| GAP-8 | — | SPEC_COMPLIANCE_AUDIT.md up to date | DONE | This file | `test_spec_compliance_audit_exists` |

## Gap Closure — HIGH Priority (Spec §§10,12,13,20,25)

| Gap ID | Spec Ref | Feature | Status | Evidence | Test |
|--------|----------|---------|--------|----------|------|
| GAP-H1 | §10 | Server CPU/RAM/disk/uptime in status | DONE | `dev_panel.py:dev_status` + psutil via run_in_executor | `test_status_template_has_server_fields` |
| GAP-H2 | §10 | Invoice history per chat_id + Jalali | DONE | `dev_panel.py:dev_invoice_history` + jdatetime | `test_invoice_history_cb_constant` |
| GAP-H3 | §20 | VIP promote/demote commands | DONE | `promotion.py:promote_vip/demote_vip` + `admin_repo.py:is_vip/promote_vip/demote_vip` | `test_vip_commands_defined` |
| GAP-H4 | §25 | Auto-leave sends DM to sudo | DONE | `credit_service.py:auto_leave_check` queries install_logs for sudo_id | `test_auto_leave_sends_sudo_dm` |
| GAP-H5 | §13 | Call security enforcement | DONE | `playback.py:_check_prerequisites` checks security_call_enabled + is_vip | `test_check_prerequisites_has_security_call_check` |

## Gap Closure — MEDIUM + LOW Priority (Final Batch)

| Gap ID | Spec Ref | Feature | Status | Evidence | Test |
|--------|----------|---------|--------|----------|------|
| GAP-M3 | §13 | VIP list with inline demote buttons | DONE | `group_panel.py:grp_vip_list` + `grp_vip_demote` with `GRP_VIP_DEMOTE_PREFIX` | `test_vip_demote_cb_constant` |
| GAP-M6 | §10 | Channel admin count install limit | DONE | `install.py:_do_install` checks `MAX_CHANNEL_ADMINS` | `test_install_handler_has_admin_limit_check` |
| GAP-L1 | §7 | credit_history partition by month | DONE | Migration 0007, `credit_history_partitioned` + 5 monthly partitions | `test_credit_history_partitioned_exists` |
| GAP-L3 | §12+§26 | Jalali timestamps in notifications | DONE | `notification_service.py:_jalali_now()`, templates include `{timestamp}` | `test_jalali_now_returns_string` |

---

# Spec Compliance Audit — Analytics & Reporting

Source: `docs/spec_analytics_reporting.md`

## Instrumentation Coverage

| Category | Event | Status | Evidence |
|----------|-------|--------|----------|
| Core | `bot.start` | DONE | `app/handlers/start.py:92` |
| Playback | `playback.play_audio` | DONE | `app/handlers/playback.py:145` |
| Playback | `playback.play_video` | DONE | `app/handlers/playback.py:193` |
| Playback | `playback.stop` | DONE | `app/handlers/playback.py:271,279` |
| Playback | `playback.pause` | DONE | `app/handlers/playback.py:290` |
| Playback | `playback.resume` | DONE | `app/handlers/playback.py:301` |
| Playback | error path | DONE | `app/handlers/playback.py:148,198` (`errors.pytgcalls`) |
| Install | `install.created.{type}` | DONE | `app/handlers/install.py:171` |
| Install | `install.failed` | DONE | `app/handlers/install.py:106` |
| Install | `credit.trial_activated` | DONE | `app/handlers/install.py:175` |
| Credit | `credit.charge` | DONE | `app/services/credit_service.py:92` |
| Credit | `credit.deduct` | DONE | `app/services/credit_service.py:230` |
| Broadcast | `broadcast.created.{mode}` | DONE | `app/services/broadcast_service_v2.py:104` |
| Broadcast | `broadcast.sent` | DONE | `app/services/broadcast_service_v2.py:158` |
| Broadcast | `broadcast.failed` | DONE | `app/services/broadcast_service_v2.py:160` |
| FM | `fm.check` | DONE | `app/handlers/force_join.py:39` |
| FM | `fm.passed` | DONE | `app/handlers/force_join.py:41` |
| FM | `fm.blocked` | DONE | `app/handlers/force_join.py:44` |
| Errors | `track_error()` helper | DONE | `app/services/analytics_service.py:61` |

## Analytics UI

| Feature | Status | Evidence |
|---------|--------|----------|
| Analytics Home screen | DONE | `app/handlers/analytics_panel.py:an_home` |
| Yesterday report | DONE | `app/handlers/analytics_panel.py:an_yesterday` |
| Last 7/14/30 days | DONE | `app/handlers/analytics_panel.py:an_7d/an_14d/an_30d` |
| Peak Hours | DONE | `app/handlers/analytics_panel.py:an_peak` |
| Errors/Health | DONE | `app/handlers/analytics_panel.py:an_errors` |
| Drilldown: by feature | DONE | `app/handlers/analytics_panel.py:an_by_feature` |
| Drilldown: by chat type | DONE | `app/handlers/analytics_panel.py:an_by_chattype` |
| Drilldown: by role | DONE | `app/handlers/analytics_panel.py:an_by_role` |
| Sudo self-stats | DONE | `app/handlers/analytics_panel.py:sudo_my_stats` |
| Private-only guard | DONE | `app/handlers/analytics_panel.py:_guard_private` — all 11 callbacks guarded |
| Back/Home navigation | DONE | Every screen has Back to AN_HOME via keyboard builders |

## Data Layer

| Feature | Status | Evidence |
|---------|--------|----------|
| analytics_hourly table | DONE | Migration 0005, verified in DB |
| AnalyticsHourly model | DONE | `app/database/models.py` |
| Redis counters (an:*) | DONE | `app/services/analytics_service.py:track_event` |
| GETDEL flush | DONE | `app/services/analytics_service.py:flush_analytics` |
| Distributed lock | DONE | Uses `acquire_lock("analytics:flush")` |
| Report caching (300s) | DONE | `cache:analytics:report:*` keys |
| APScheduler flush job | DONE | `app/scheduler.py:flush_analytics_counters` (5min) |

## i18n

| Feature | Status | Evidence |
|---------|--------|----------|
| fa.json analytics keys | DONE | 31 keys under `admin.analytics.*` |
| en.json analytics keys | DONE | 31 keys (exact parity) |
| Parity test | DONE | `tests/test_analytics.py:test_analytics_i18n_parity` + `tests/test_analytics_gaps.py:test_analytics_i18n_parity_extended` |

## Tests

| Test | Status | File |
|------|--------|------|
| track_event increments Redis | DONE | `tests/test_analytics.py` |
| track_event swallows errors | DONE | `tests/test_analytics.py` |
| flush upserts and clears | DONE | `tests/test_analytics.py` |
| flush idempotent | DONE | `tests/test_analytics.py` |
| report yesterday | DONE | `tests/test_analytics.py` |
| peak hours | DONE | `tests/test_analytics.py` |
| report caching | DONE | `tests/test_analytics.py` |
| concurrent flush lock | DONE | `tests/test_analytics.py` |
| i18n parity | DONE | `tests/test_analytics.py` |
| private-only guard (group) | DONE | `tests/test_analytics_gaps.py` |
| private guard passes PM | DONE | `tests/test_analytics_gaps.py` |
| playback instrumentation | DONE | `tests/test_analytics_gaps.py` |
| broadcast instrumentation | DONE | `tests/test_analytics_gaps.py` |
| credit import check | DONE | `tests/test_analytics_gaps.py` |
| install import check | DONE | `tests/test_analytics_gaps.py` |
| track_error helper | DONE | `tests/test_analytics_gaps.py` |
| i18n parity extended | DONE | `tests/test_analytics_gaps.py` |

## Pre-existing Failures (NOT caused by analytics changes)

- `test_developer_id_from_config` — hardcoded DEVELOPER_ID in test vs real config.env
- `test_distributed_lock_acquire_release` — intermittent Redis state issue
