# Changelog

## [Unreleased] - 2026-07-18

### Fixed
- **Repository-wide handler group safety**: generated a real-registration inventory of all 610 startup handlers plus dynamic Pyromod/`safe_ask` listener families (`docs/reports/current/handler_group_inventory.{json,md}`), replaced undocumented numeric groups with canonical constants and an allowed-class topology, and extended the callback matrix with Raw/Message first-match replay. Fixed a private-input shadow where the earlier `otp_text_handler` normal return in group `-90` prevented `hlp_proxy_input` from running; proxy input now uses group `-89`. Also fixed `safe_stop_listening(..., user_id=...)` to try precise user-scoped Pyromod cleanup before chat-wide compatibility fallbacks. The separate call-security raw and exact-object `safe_ask` cleanup causes remain distinct.
- **Project-wide callback routing audit (dispatch matrix)**: replayed 1014 representative callback samples over all 521 handlers through faithful group-ordered dispatch (fresh, stale `message=None`, denied-actor probes; `scripts/audit_callback_dispatch_matrix.py`, report in `docs/reports/current/callback_dispatch_matrix.{json,md}`). Fixed all four proven defect classes: (1) non-privileged group members tapping `grp:*`/`pg:vip:*` panel buttons now get an explicit `no_access` alert via the new `grp_permission_or_unknown_fallback` family fallback (`GROUP_FAMILY_FALLBACK_GROUP = 900`, route_seen-suppressed) instead of `unknown_callback`; (2) six panel roots (`fm:panel`, `dev:banall:home`, `dev:texts:home`/`dev:texts_links`, `own:texts:home`/`own:texts_links`, `yts:home`, `fct:home`) converted to identity-only filters with explicit in-handler role/scope/stale rejection so stale taps answer instead of leaking to the unknown fallback; (3) `yts:` added to `_KNOWN_CB_PREFIX` so the family joins the two-tier fallback design; (4) `NAV_START` documented in the risk audit's `IGNORED_CROSS_PANEL_KEYS` (generic navigation owned by `callbacks.nav_start`), taking `audit_callback_risk.py --check` to zero cross-panel risks. Added the project-wide dispatch regression suite `tests/test_project_callback_routing_dispatch.py` (critical routes fresh/stale/denied, parser round-trips, keyboard completeness, cross-module duplicate-owner guard with documented intentional chains). No callback identities renamed; no fallback weakened.

### Documentation
- **Update-pack consolidation and `/docs` cleanup**: consolidated the `update7-8-2026/` start-customization planning pack (10-phase execution plan + client materials) into one canonical `docs/PROJECT_UPDATE_HISTORY.md` that preserves delivered decisions, the eight-slot start-menu contract, schema (`0029_start_customization_schema`), service/handler/runtime symbols, the Developer-only credit-renewal policy, verification results, and known live-only gaps — distinguishing current vs historical vs superseded claims (e.g. head moved `0029`→`0033`); then removed the `update7-8-2026/` directory. Deleted the stale, machine-unconsumed `docs/docs_manifest.json` (named Alembic head `0027`, dated 2026-06-26) as superseded by the current `docs/index.md` (head `0033`). Linked the new history doc from `docs/index.md` and `docs/README.md`. No runtime code, migrations, or generated reports changed.

### Changed
- **Group support privacy**: removed the Developer contact button from the group support menu while keeping Bot Creator, guide-channel, support-group, and Back actions available.
- **Private admin UX**: Developer, Owner, Sudo, helper, broadcast, and settings input flows now reuse one editable panel, clean up temporary replies, and keep action-first navigation without redundant Back/Home/Cancel combinations.
- **Privileged `/start` UX**: Developer, Owner, and Sudo users now see the regular home screen first with one authorized management entry instead of opening a control panel immediately.

### Fixed
- **Wizard message cleanup**: helper OTP/import, proxy, install-policy, text/link, token, cookie-session, and broadcast flows now retain their panel anchor, recover cleanly from invalid input, and suppress duplicate cancellation or expiry notices.
- **Automatic start-button colors**: Advanced mode now falls back to the `RBGNNNNR` palette when no custom colors are stored, preserves Owner-over-Developer precedence and explicit `N` overrides, and explains that color cleanup restores the automatic palette on newly rendered `/start` menus.
- **Callback route isolation and diagnostics**: successful Help/panel callbacks now stop propagation before lower-priority handlers can reinterpret them, while startup/offline route checks use current installer and Sudo-permission payload shapes and reject routes that resolve to unknown/malformed handlers.
- **Helper Add callback routing**: `hlp:add`, `hlp:add:otp`, and `hlp:import` now use the dedicated panel callback group, startup route diagnostics cover all three entry points, and pre-dispatch guard/listener groups no longer consume their callback answers before the real handlers or fallback can respond.
- **Fast-Creat reliability and security**: hardened provider error/failover handling, TikTok mixed-media normalization, direct-file validation and cleanup, Spotify fallback after unusable direct files, concurrent duplicate-token handling, cooldown/status accounting, callback freshness, URL credential rejection, and model/migration consistency.

## [Unreleased] - 2026-07-15

### Added
- **Fast-Creat token pools and group downloads**: added encrypted, audited, round-robin Owner/Developer-managed token pools for Instagram, TikTok, and Spotify; `پخش <social-link>` now safely downloads and sends supported social media in groups, with provider-specific token failover, one-minute rate-limit cooldowns, and Spotify title-to-YouTube fallback.

## [Unreleased] - 2026-07-14

### Added
- **SoundCloud track playback and downloads**: direct track links now resolve through the existing safe yt-dlp paths for Voice Chat playback (`پخش <URL>`) and audio-file download (`دانلود <URL>`), with localized rejection of profiles, sets, and playlists.
- **Name-based YouTube playback**: `پخش <نام آهنگ>` and `play <song name>` now resolve the first yt-dlp YouTube result before joining the group Voice Chat.

## [Unreleased] - 2026-07-13

### Added
- **YouTube cookie-session pool**: added encrypted, audited Developer/Owner-managed cookie jars with safe validation, fair rotation, cooldown handling, and bounded failover for yt-dlp YouTube media requests.
- **YouTube direct-download format choice**: added user-bound, one-time Audio/Video selection for YouTube download links while preserving existing non-YouTube download behavior.
- **YouTube production hardening**: added yt-dlp-ejs/runtime preflight, audited rekey confirmation, expiry metadata, and deduplicated session-pool alerts for active Developers and Owners.

### Changed
- **Media health**: added non-sensitive YouTube session-pool counts plus yt-dlp-ejs and JavaScript-runtime readiness to the Developer media health report.

## [Unreleased] - 2026-07-08

### Added
- **Start customization schema foundation**: added durable start message pool, start button slot, and start style configuration tables with repository coverage and migration/drift validation.
- **Start customization service foundation**: added effective scoped start customization reads, actor-aware write wrappers, deterministic message selection, install-policy-aware Test gating, and render descriptors for the fixed eight-slot start menu without changing runtime `/start` wiring.
- **Start customization reply commands**: added Developer/Owner reply-command ingestion for start/category message pools and start button text/color/emoji settings, with trusted report-group validation and focused parser/authorization coverage.
- **Start customization runtime**: wired regular private `/start` to configured start content and rendered start buttons, added `start:cat:*` internal category callbacks with safe fallbacks, and covered the new runtime path in callback routing verification.
- **Start customization panel toggles**: added Developer global and Owner-scoped start style mode controls inside the existing text/link hub, with current-mode display and callback routing coverage.

### Fixed
- **Group wizard navigation propagation**: hardened unauthorized group navigation callbacks to stop Kurigram handler propagation after denial alerts.
- **Sudo bulk leave ownership scope**: rechecked current active group/channel ownership before destructive legacy Sudo leave operations.

## [Unreleased] - 2026-07-07

### Added
- **Graphify project map**: generated a no-visualization knowledge graph under `graphify-out/` for the current repository snapshot.

### Changed
- **Graphify project map**: rebuilt the full-repository no-visualization knowledge graph from scratch.

### Fixed
- **Group wizard navigation authorization**: enforced group panel permission checks on `wz:home` and `wz:back:*` group navigation before rendering the group panel.

## [Unreleased] - 2026-07-05

### Changed
- **Sudo private panel expansion**: rebuilt the Sudo PM root around status, managed groups, credit, own permissions, and own statistics/lists with current `sudo:*` callbacks and PM-only scoped handlers.

### Fixed
- **Sudo permission enforcement**: aligned player bypass checks with the `auto_admin_bypass` Sudo permission so the displayed permission state matches runtime playback/security authorization.
- **Sudo cross-role verification**: tightened in-group Sudo decorator bypass checks to honor `auto_admin_bypass` and added scoped regression coverage for Sudo routes, credit, and legacy bulk leave.
- **Owner panel verification hardening**: added private-chat-only Owner callback coverage and aligned stale Owner navigation/text-link tests with the current confirmed destructive flows.
- **Telegram promotion confirmation text**: restored target user, target chat, privilege summary, and stored-title status in Developer/Owner Telegram admin promotion confirmations.

## [Unreleased] - 2026-07-04

### Changed
- **Owner private panel expansion**: rebuilt the Bot Creator panel around current `own:*` routes for operational groups, credit, sudo management, sudo titles, start text, force join, broadcast, inventory, moderation, media settings, and reports without emitting legacy Owner category callbacks.
- **Owner runtime safeguards**: added owner-scoped confirmation flows for sudo removal, force-join removal, broadcast cancellation, and Ban All sweeps; Owner Ban All now previews target user and chat count before execution.
- **Owner runtime controls**: enabled owner-scoped start-text overrides with deterministic single-owner resolution, runtime-backed media toggles, managed-resource credit changes, Owner-created broadcast history/cancel controls, and separate stored sudo title versus Telegram promotion/title application flows.
- **Callback authoring instructions**: documented callback and inline-keyboard rules in `AGENTS.md` so visible buttons must keep stable callback data, route to real handlers, handle malformed payloads safely, and pass callback routing coverage before delivery.
- **Developer resource list item UI**: compacted developer resource list pages to three resources per page with shorter action labels, cleaner item text, RTL-friendly action order, and link previews disabled in list messages.

## [Unreleased] - 2026-07-03

### Fixed
- **Analytics home callback priority**: `an:home` now routes through `PANEL_CALLBACK_GROUP`, marks the analytics route, and answers before rendering so analytics Back buttons cannot be shadowed by generic group-0 shortcuts.
- **Startup callback diagnostics safety**: callback route diagnostics now run inside a non-fatal startup guard and include representative analytics, call-stats, helper, Help, navigation, playback, broadcast, dev/owner/sudo, Force Join, post-install, install, and search samples.
- **Offline callback routing coverage**: added a global verifier for static and dynamic visible callback samples, with focused tests that fail if current visible callbacks route only to fallback or miss a dedicated handler.
- **Callback route-first handling**: `an:*` analytics callbacks and `grp:callstats:sel:*` call-stats selectors now route to dedicated handlers before authorization or chat-scope denial, preventing visible buttons from falling through to the unknown-callback fallback.
- **Private callback language lookup**: private callback pre-dispatch language binding no longer reads group chat settings for the private user id.

## [Unreleased] - 2026-07-02

### Added
- **Developer resource lists expansion**: rebuilt `dev:cat:lists` as the full "لیست منابع" panel with nine new schema-backed lists (unlimited, call-security, playback, trial, music, video, inactive groups, inactive channels, active/inactive global users), new `admin_report_repo` paginated queries, compact `pg:dx:`/`pg:dux:` pagination, FA/EN labels, and focused UI/repository/handler tests.
- **Bot panel requirements**: added `docs/BOT_PANEL_REQUIREMENTS_AND_BUGFIXES.md` covering developer panel cleanup, bot creator access, sudo simplification, monthly invoicing, Help scope, group replies, ping behavior, and UI cleanup requirements.
- **Monthly invoice foundation**: added creator-only monthly invoice storage, render-only invoice preview service, global monthly amount setting key, Persian/English invoice templates, and focused repository/service/schema tests.
- **Monthly invoice developer UI**: added a developer-only panel for monthly invoice configuration, amount setting, idempotent current-month preparation, recent invoice listing, and invoice detail previews.
- **Monthly invoice delivery**: added developer-triggered manual delivery for prepared creator monthly invoices with active-owner validation, duplicate-send protection, and delivery status tracking.
- **Monthly invoice scheduler**: added disabled-by-default automatic monthly invoice preparation and delivery with developer auto-send controls and focused scheduler tests.
- **Help panel restriction**: limited Help panel entry and callbacks to group contexts, hid the unavailable serial Help button, and added focused Help safety tests.

### Changed
- **Bot panel phase 2 cleanup**: separated developer force-join and moderation sections, clarified owner-scoped sudo access labels, removed duplicate developer diagnostics entry, and tightened admin-title navigation.
- **Resource statistics and ping display**: clarified owner/developer resource statistics labels, showed existing resource status/credit details in lists, and updated ping output to active-state plus response latency.

### Fixed
- **Call Stats selection no-op**: the nine `grp:callstats:sel:*` buttons rendered one identical empty text for every audience/period when no call data existed, so repeat presses hit MESSAGE_NOT_MODIFIED and looked dead; the empty state now includes the scope title, a new period line, and the Jalali start date so each selection renders distinct visible text (new `call_stats_panel.period_*` FA/EN keys).
- **Analytics report failure safety**: `an:*` and sudo my-stats handlers now catch report-generation failures and render a truthful "analytics unavailable" message (new `admin.analytics.unavailable` FA/EN key) instead of silently doing nothing when the database is unreachable.
- **Visible callback inventory guard**: added `tests/test_broken_callback_audit.py` — all nine call-stats selections with negative chat IDs, malformed/unauthorized safety, `an:*` routing and unavailable fallback, `hlp:*` regression guard, and a global AST-based check that every statically visible `callback_data` matches a dedicated registered handler.
- **Code-referenced i18n coverage**: completed missing runtime FA/EN keys and hardened the i18n usage checker for dynamic families, manifest coverage, and placeholder parity.
- **Developer Panel regression cleanup**: restored missing admin/report i18n keys and corrected Force Join and Moderation back/cancel navigation targets.
- **Call Stats i18n and callbacks**: registered Call Stats public-command resources, clarified the Call Stats period menu, and blocked ID Call Stats toggles while Call Stats is disabled.
- **Helper i18n validation**: restored the helper join progress resource key required by the existing split-i18n critical-key check.
- **Bot panel phase 1 cleanup**: routed developer `درباره` to About text, hid sudo invite/top-up UI, simplified value-entry navigation, and added random exact `ربات` group replies.
- **Bot panel phase 1 stabilization**: restored panel and text/link i18n labels and active install policy resources required by focused panel tests.
- **Phase 4B stabilization**: restored typed-command-first playback Help copy in Persian and English while preserving existing non-slash aliases.
- **Phase 5B hygiene**: removed final trailing-whitespace blockers and tightened deploy packaging exclusions for local test/scratch artifacts.

## [Unreleased] - 2026-06-26

### Added
- **Player role ladder**: added explicit PlayerDeputy role storage, timed/permanent VIP database support, role cache keys, and role-tagged player configuration output.
- **Project deep context report**: added `docs/PROJECT_DEEP_CONTEXT_REPORT.md` — implementation-grounded onboarding handoff (runtime architecture, handler routing, permissions, data model, tests, deployment, risks); expanded §4.11 for call-stats panel and Id command; linked from `docs/README.md`, `docs/index.md`, and `AGENTS.md`.

### Changed
- **Group player permissions**: `پنل` opens the full group/player panel for all roles above VIP (Developer, Sudo, Bot Owner, Telegram creator, PlayerOwner, PlayerDeputy, MusicAdmin); Developer and active Sudo are unconditional in-group bypass roles (no sudo permission flags or local role rows required).
- **Call Security access**: `امنیت کال` is available to Developer, Sudo, Bot Owner, Telegram creator, PlayerOwner, and PlayerDeputy; MusicAdmin and VIP are denied; `owner_access_enabled` still gates owner mute privileges only.
- **Player role permissions**: separated Deputy from VIP, restricted VIP to playback/help access, allowed Admin to manage VIP, allowed Deputy to manage Admin/VIP, and aligned group/player panel permission gates.
- **Documentation refresh (second pass)**: Corrected remaining staleness — Docker policy (native-only production), unlimited credit (`status="unlimited"`, `MAX_CREDIT_DAYS`), immediate End Call (`پایان کال` without minutes), ID/user-info commands in `commands.md`, split-i18n path in `playback.md`, Alembic head note in `now_playing_cover_security.md`, handler counts 86/384 via `audit_handler_map.py`, Call Stats **call admin** permission wording.
- **Documentation refresh (full `/docs` audit)**: Re-verified all 87 Markdown files against handlers, services, repos, models, migrations, and scripts; updated Alembic head `0026_call_reports_helper_idx`, Call Stats panel, Id output mode, install-player `Add:Fa:*` setup panel, GroupCredit idempotency, deploy zip exclusions, and help/callback coverage; fixed broken links in `docs/README.md`; expanded `DATABASE_AND_SCHEMA.md`, `REDIS_AND_CACHE.md`, `group_settings.md`, and `manager_text_commands.md`; refreshed `docs/reports/current/documentation_refresh_report.md`.

### Fixed
- **Helper panel i18n**: restored split Persian/English helper panel title and summary strings used by helper callback routing.
- **Promotion list commands**: gated legacy `listmusic` / `listvideo` handlers with `group_music_admin` / `group_video_admin` so normal members cannot enumerate player admins.
- **Role ladder audit**: added `docs/ROLE_LADDER_POST_IMPLEMENTATION_AUDIT.md` with post-implementation verification results.

## [Unreleased] - 2026-06-21

### Changed
- **Voice call stats deploy readiness**: panel total duration and user percentages now use full SQL aggregate for the scope/period; display list remains top-10 with optional top-500 list cap note.
- **Voice call stats audit**: callback permission gate aligned with command gate; ID stats block avoids redundant empty-check query; added hardening tests; updated stale call-stats docs to reflect admin-gated panel (no day-suffix commands).
- **Id command output mode**: `Show Id Status Photo` / `Show Id Status Simple` now set a dedicated per-chat Id output mode (`simple` vs `photo`) via BotSetting KV; `id`/`آیدی` returns rich user info with optional voice-call stats; group settings panel adds call-stats-in-ID toggle; no longer toggles now-playing cover/track ID flags.
- **Help Center customer copy sync**: synced Persian Help detail pages in `app/resources/i18n/*/system/help.json` to customer-provided copy (commands, separators, section wording); added `#کاربردی` utility Help page with home navigation and user-bound `h:utility:U{id}` routing; preserved serial coming-soon note and incomplete utility placeholders; documented `شناسه کانال` / `Channel id` as Help-listed only.
- **Remaining Help commands verification**: added focused safety tests for VIP clear stale/forged callbacks, Telegram callback length, and public exact-command trailing-argument rejection; audit confirmed speed/seek/volume/info/VIP implementations with no runtime code changes required.

## [Unreleased] - 2026-06-20

### Fixed
- **Help Center `h:*:U{id}` callback routing (production)**: registered nine explicit Help regex handlers at priority group `-845` (same Kurigram string-regex style as working `grp:*`); patched pyromod `CallbackQueryHandler.check` so stale chat/message listeners cannot veto a matching Help handler; callback trace now clears chat-wide and message-scoped pyromod listeners; added `help_diag` startup probes with real `CallbackQuery` checks and fallback routing evidence logs.
- **Help Center blocked by stale pyromod listeners**: callback trace now clears all `CALLBACK_QUERY` listeners on the tapped message (not only the clicking user), fixing `h:*:U{id}` taps that fell through to unknown-callback fallback when another user's wizard left a listener on the same panel message.
- **Group `nav:back` StopPropagation noise**: callback safety wrapper and `grp_nav_back` no longer log successful navigation as ERROR when `stop_propagation()` stops lower-priority handlers.

## [Unreleased] - 2026-06-18

### Fixed
- **Helper voice chat join without playback**: playback now starts the helper PyTgCalls pool before group membership checks (no ephemeral helper session during play); `vc_join` only leaves stale ntgcalls state when already connected; `play()` uses `GroupCallConfig(auto_start=False)` after preflight; failed joins call `vc_leave` to avoid zombie "Live" helpers; group-call preflight verifies cache after create; server clock skew corrected (NTP was unsynchronised, ~32s RTC drift).
- **Voice/WebRTC UDP host networking**: tuned `rp_filter`, UDP conntrack timeouts, and ephemeral port range via `/etc/sysctl.d/99-telegram-voice-udp.conf`; added explicit UFW before-rules for inbound UDP from Telegram relay ranges (`91.108.0.0/16`, `149.154.0.0/16`, `2001:67c:4e8::/48`). Post-change packet capture still shows zero inbound UDP from `91.108.9.7:32001` while STUN replies work — provider edge firewall / anti-DDoS is the remaining blocker.
- **Playback inline callbacks (`pb:*`)**: introduced a typed parser/dispatcher for playback controls, unified group callback routing for `pb:vol:*`, `pb:speed:*`, `pb:prev`, `pb:next`, `pb:repeat`, `pb:fav:add`, `pb:fav:play`, `pb:pause`, `pb:resume`, and `pb:stop`, added safer edge handling (no active playback, paused/resumed idempotency, favorites empty), implemented previous-track replay from in-memory history, and expanded callback tests including keyboard/dispatcher sync checks; removed duplicate legacy `pb:repeat` handler so repeat toggle routes only through the dispatcher; consolidated now-playing controls into a single regex handler; repeat toggle refreshes the inline keyboard; fixed `CallService.play_next` queue playback body that had been orphaned inside `play_previous`.

## [Unreleased] - 2026-06-17

### Fixed
- **Voice chat join reliability**: group-call preflight now uses the pooled helper PyTgCalls client (`get_input_call` / `get_call` / `create_group_call`) instead of a separate ephemeral helper session; `vc_join` leaves stale call state before `play()`; playback aborts with `voice_chat_connect_failed` when preflight cannot ensure an active call; added `GROUP_CALL_CREATE_SETTLE_SECONDS` setting.

### Added
- **Graphify (Cursor integration)**: installed `graphifyy` 0.8.41 via pipx with `[sql,postgres]` extras; registered project-scoped Cursor rule at `.cursor/rules/graphify.mdc` for query-first codebase exploration.

### Changed
- **Graphify knowledge graph rebuild**: ran `graphify update .` (568 files, 15,317 nodes, 34,728 edges, 701 communities); refreshed `graphify-out/graph.json` and `GRAPH_REPORT.md`; prior graph backed up to `graphify-out/2026-06-17/`.

## [Unreleased] - 2026-06-16

### Added
- **Command documentation sync**: expanded Help Center and group panel help for manager, call, group, and PM commands; updated `docs/features/commands.md` with PM commands, full role alias tables, manager-vs-promotion precedence, and inline-only radio/satellite note.

### Fixed
- **Playback VC join post-helper-pool (stream-end + concurrency)**: `register_stream_end_handler` now uses `pytgcalls.filters.stream_end()` for py-tgcalls 2.3+ (fixes `TypeError` on group-call participant updates); per-chat join-in-progress guard rejects concurrent `پخش` before temp files are deleted; `vc_join` retries `TelegramServerError`; playback pre-creates group calls via helper MTProto when none is active; added i18n keys `voice_chat_connect_failed` and `source_missing`.
- **Playback voice join early `call_py is None` guard**: `join_voice_chat` no longer aborts when the legacy bot-scoped `call_py` argument is `None`; it checks helper-pool PyTgCalls availability instead so helper-bound streaming can proceed after the architecture change.
- **Playback voice join (`BOT_METHOD_INVALID`)**: PyTgCalls now starts on helper user sessions via `HelperPyTgCallsPool` instead of the main bot client; `CallService.join_voice_chat` and VC controls resolve the bound helper engine per chat, preventing `phone.CreateGroupCall` bot rejections when no active group call exists.
- **Helper panel callback routing (`hlp:list`, `hlp:health`)**: removed strict decorator-level `_pm_dev` filtering for these two callbacks so they are always routed to their handlers first, then consistently enforced via `@developer_only` and `_guard_private`; this prevents valid helper-family taps from falling through to generic `unknown_callback` fallback and keeps denied paths explicit (`no_access` / `private_only`).

## [Unreleased] - 2026-06-15

### Fixed
- **Helper admin promotion after join**: main bot idempotently promotes helper users with call-management rights (`can_manage_video_chats`); skips re-join when already in group but still ensures admin; `/addhelper` and manager text commands surface `promote_failed`; playback path logs promote failures without blocking stream.
- **Helper group join via bot invite link (Pyrogram)**: helpers no longer `join_chat(chat_id)` directly (which caused `CHANNEL_INVALID`); main bot exports invite link, logs URL, waits 1s (`HELPER_JOIN_INVITE_SETTLE_SECONDS`), then helper joins via link; infra failures skip quarantine; `/addhelper` shows specific i18n errors (`invite_failed`, `channel_invalid`, `bot_unavailable`).
- **Handler/callback routing audit (codebase-wide)**: added `scripts/audit_callback_risk.py` for cross-panel shortcut and edit-path risks; dev shortcuts for `bcw:start` and `bc:history`; `render_install_policy_panel`; PM `wz:cancel`/`wz:back`/`wz:home` and group `nav:back` now use `panel_callback_edit`; analytics sub-pages, help/helper edits, and owner/sudo home panels unified on `panel_callback_edit`; extended routing regression tests and handler routing docs.
- **Dev panel shortcut callbacks (`hlp:home`, `an:home`, `h:home`, `nav:back`)**: registered developer-panel tool shortcuts in `dev_panel.py` using shared `render_*_panel` helpers and `panel_callback_edit` (same path as `dev:cat:*`); PM `nav:back` now uses `panel_callback_edit`; `callbacks` module registers earlier; `PYROMOD_CALLBACK_GROUP` (-980) excluded from `mark_route_seen`; `safe_stop_listening` tries all user/chat signatures before returning.
- **Callback routing hardening (`hlp:home`, `nav:back`, and similar)**: moved global ban guard to group -995 (before pyromod at -980); clear stale pyromod ask/listen waiters at trace group -990; stop marking guard group as a routed handler so known-prefix fallbacks are not suppressed; extended known callback prefixes (`nav:`, `an:`, `bc:`, `pg:`, `postinst:`, `noop`); aligned `helper_panel` and `analytics_panel` developer callbacks with `_pm_dev` (`dev_filter` + `private_chat_filter`) plus `@developer_only`.

## [Unreleased] - 2026-06-14

### Changed
- **Helper panel callback observability**: added structured callback tracing (`cbid`, route/start/done/fail, guard, answer/edit, unhandled) and hardened `hlp:home` so Developer → `مدیریت هلپرها` answers early and opens from read-only helper rows even with no available/quarantined helpers. Helper pre-stream join failures now preserve safe `HelperJoinResult` metadata in logs/events before quarantine. No migration, callback data, or command alias changes.
- **Group credit source-of-truth hardening**: made `groups.status='active'` the authoritative group runtime state for credit/status/panel/playback paths; old `آپدیت شارژ` and slash-free manager charge commands now share a managed credit service and refuse inactive/unmanaged groups instead of updating orphan `group_credits`; scheduler warnings and daily deduct now require active group/channel install rows; panel, playback, download, playlist, Call Security, and group-call text command gates now agree with `اعتبار موزیک/پلیر`. Added `tests/test_group_credit_source_of_truth_real_behavior.py` and report `docs/reports/current/group_credit_source_of_truth_audit_report.md`. No migration, callback data, or alias changes.
- **Logging pipeline audit**: centralized sink config verified; `setup_logger()` now creates the log directory and derives the error log path safely; pg_dump failures no longer log raw stderr (possible credential leak); added `mask_connection_url()` in `diagnostic_logging.py`; expanded `scripts/check_logging_pipeline.py` AST scan; added `tests/test_logging_pipeline.py` and `tests/test_logging_safety.py`; updated `docs/operations/logging_safety.md` and `docs/reports/current/logging_pipeline_audit_report.md`.
- **Logging hardening (second pass)**: added masking helpers (`mask_token`, `mask_session`, `mask_proxy_url`, `mask_authorization_header`, `safe_subprocess_error_summary`, `redact_freeform_text`); fixed unsafe exception/subprocess logging in `main.py`, `voice_stack.py`, `broadcast_service.py`, `media_health_service.py`, and `bot_update_service.py`; redacted disposable Alembic drift command print; added `app/utils/logging_pipeline_scan.py` and expanded checker/tests/docs.

## [Unreleased] - 2026-06-11

### Changed
- **Cancel Back-button verification**: re-audited ask/wizard Back/Cancel UX end-to-end; `wz:back:*` now clears/stops active runtime state before navigation, broadcast wizard Back/Cancel handlers use safe edit/send fallback, and helper OTP/import Back/Cancel handlers use safe edit/send fallback. Added `tests/test_cancel_back_buttons_end_to_end.py` and report `docs/reports/current/cancel_back_button_verification_report.md`.
- **Cancel UX migration**: visible user-input prompts no longer instruct users to type `/cancel`; generic asks, broadcast wizard, Call Security membership-age input, helper OTP/import/proxy prompts, and text/link editor prompts now point users to inline Back/Cancel buttons. Hidden fallback text commands `/cancel`, `cancel`, and `لغو` remain supported. Call Security membership-age ask now includes a scoped inline Back callback (`grp:callsec:age:cancel`). Report: `docs/reports/current/cancel_inline_button_migration_report.md`.
- **Inline keyboard UI cleanup**: paired related buttons across group, developer, owner, sudo, helper, install-policy, broadcast-wizard, and call-security panels; added `_chunk_buttons`, `_chunk_by_width`, and `_nav_row` helpers in `app/utils/ui.py`; short Persian nav labels (`panels.group.nav.*`) separate from decorative message titles; group settings use semantic multi-column rows; dev/owner settings toggles use `toggle_label()` with ✅/☑️ via `status_indicator`; shortened FA/EN button microcopy; report `docs/reports/current/keyboard_ui_cleanup_report.md`.
- **`toggle_label` i18n**: `toggle_label(lang, base, enabled)` reads `status_indicator.active/inactive` (✅/☑️) from split fragments; shared by toggles and panel status text.

### Added
- **Manager/group text command real-behavior audit**: added deep SQLite-backed verification for slash-free manager commands covering install/uninstall/leave, charge, helper binding, Telegram admin config, role/permission linkage, parser collisions, and visible failure paths. Fixed uninstall/leave cleanup to remove group-call `bot_settings` and `call_security_settings`, and fixed deputy `player_vips` mutations to invalidate the runtime VIP permission cache. Added six `tests/test_manager_text_commands_real_behavior_*` / collision suites and report `docs/reports/current/manager_group_text_commands_real_behavior_audit_report.md`. No migration, callback data, or alias changes.
- **Slash-free manager/group text commands**: implemented the `new_command.md` manager/admin and group-management aliases without `/` (`AddMusic`/`RemMusic`/`LeaveMusic`, `ChargeMusic`/`ChargePlayer`, `AddhelperMusic`, `ConfigMusic`, `MusicExpire`, owner/deputy/mod role families). Added anchored NFKC/ZWNJ/digit-normalized parser, high-priority handler, service/repository layer, split-only i18n messages, docs, implementation report, and focused DB persistence tests. No callback data changed; no migration required.
- **Slash-free group call text commands**: implemented `دریافت پنل`/`GetPanel`, scheduled `EndCall`/`DiscardCall`, `StartCall`, mute/unmute, invite user/admins/recent/special, `AutoCallStatis`, `CallStatis`, `CallMute`, `CallComment`, `SetTitleCall`/`SetTitle`, and `GetCallLink`; added centralized parser, high-priority handler, `bot_settings` persistence keys, scheduler restore for scheduled call-end jobs, split-only i18n keys, docs, and real SQLite persistence tests. No callback data changed; no schema migration required.
- **Group call text command real-action hardening**: re-audited slash-free call commands so live-action commands use raw Kurigram/Pyrogram MTProto helper paths (`CreateGroupCall`, `DiscardGroupCall`, `InviteToGroupCall`, `EditGroupCallTitle`, `ExportGroupCallInvite`, `ToggleGroupCallSettings`) or return visible unsupported/no-active/helper/API errors. `عنوان کال` no longer stores fake DB-only success; `لینک کال` no longer fabricates `?videochat` URLs; auto stats now has a scheduler reader path. Added `tests/test_group_text_call_commands_real_actions.py` and report `docs/reports/current/group_text_call_commands_real_action_audit_report.md`.
- **Helper-bound raw group-call command execution**: raw slash-free group call actions now resolve the same-group helper before every `phone.*` call, reuse `helper_chat_bindings` first, verify helper membership and admin/call-management permission, re-resolve helpers for scheduled call-end execution, and return visible no-helper/session/join/not-in-group/not-admin errors instead of fake success. Added `tests/test_helper_group_binding_for_call_commands.py` and report `docs/reports/current/helper_group_binding_call_commands_audit_report.md`.
- **Handler/command/callback/playback routing fix**: shared `app/utils/text_commands.py` normalizer; `PRIORITY_COMMAND_GROUP` (-15) for help/charge/panel/play; visible blacklist denial (`common.errors.blacklisted`); unknown-callback fallback (`common.errors.unknown_callback`); FSM escape via `clear_wizard_and_allow_command`; help callback edit→reply fallback; `scripts/audit_handler_map.py`; six focused test modules; report `docs/reports/current/handler_command_callback_playback_fix_report.md`.

### Changed
- **Playback auth**: `download_enabled` no longer blocks voice-chat playback (download gate remains in `download.py` only); charge/help/panel text commands accept NFKC/ZWNJ normalization and Persian digits.

### Added
- **i18n usage coverage audit**: new read-only `scripts/check_i18n_usage.py` AST-scans `app/` for `t()`/`label()` usages and validates every literal key plus all 19 registered dynamic key families (`DYNAMIC_ALLOWLIST`) against the split FA/EN trees; unregistered dynamic f-string patterns fail the run. Audit found zero missing keys (856 literal `t()` keys, 17 observed dynamic patterns, all covered). New tests in `tests/test_i18n_usage_coverage.py` (22 tests); docs updated (`app/resources/i18n/README.md`, `docs/features/i18n.md`); report `docs/reports/current/i18n_usage_coverage_report.md`. No fragment changes, no key renames, no callback/alias changes.

### Changed
- **i18n split-only migration**: removed legacy monolith files `app/resources/strings/fa.json` and `en.json`; `TextService` now loads exclusively from `app/resources/i18n/` (manifest + fragments) with fatal errors on missing manifest, fragments, or duplicate leaf paths; no legacy fallback. `scripts/split_i18n_resources.py` is now read-only validation only (default and `--check`). Tests updated via `tests/i18n_test_utils.py`; `tests/test_i18n.py` updated for split-only lang resolution; report `docs/reports/current/i18n_split_only_migration_report.md`.

## [Unreleased] - 2026-06-11 (addendum cleanup)

### Changed
- **Toggle label cleanup**: removed redundant state wording from emoji-toggle base labels — FA `فعال/غیرفعال` dropped from `panels.group.settings.{repeat, call_message, call_report, record_call}`, EN ` On/Off` dropped from the same keys plus `auto_clean`/`auto_ready_call`; updated in both legacy monoliths and split fragments; buttons now render e.g. `پیام کال 🟢`.
- **Call Security toggle icons standardized**: `build_panel_keyboard` now uses the shared `toggle_label()` (🟢/🔴 suffix) instead of the local `_state_icon` ✅/☑️ prefix; `_state_icon` removed; callback data, permissions, and panel text unchanged.
- **`/addhelper` progress message**: new i18n key `admin.helpers.join_group_progress` (FA/EN, legacy + fragments) replaces the misused `common.errors.try_later` as the in-progress reply.

### Added
- **`scripts/split_i18n_resources.py --check`** (and `--verbose`): read-only verification mode — validates JSON, fragment existence, duplicate leaf-path collisions, and FA/EN parity; superseded by split-only migration (script is read-only only; legacy monoliths removed).
- **Stronger tests**: `tests/test_i18n_hybrid_split.py` now enforces leaf-level path/value parity (legacy vs split, FA vs EN) and runs `--check` as a subprocess (read-only verified); `tests/test_dynamic_toggle_buttons.py` now rejects raw state wording (`فعال/غیرفعال`, `On/Off`, brackets) in visible toggle labels for FA and EN, and asserts Call Security buttons use 🟢/🔴 suffix with no ✅/☑️; `/addhelper` progress-key tests added; report `docs/reports/current/addendum_revision_cleanup_report.md`.

### Fixed
- Repaired stale test `test_group_settings_summary_omits_repeat_toggle` (`tests/test_repeat_runtime_lock_phaseb61.py`): updated to the current async `_build_group_settings_summary(sd, chat_id)` signature with a mocked Call Security lookup; pre-existing failure unrelated to this cleanup.

## [Unreleased] - 2026-06-10

### Changed
- **Docs audit (`docs/`)**: Updated deployment and operations guides to Alembic head `0025_group_member_membership`; rewrote `now_playing_cover_security.md` for shipped cover art; clarified `multi_tenancy.md` (owner scope vs multi-bot audit); fixed `ops_concurrency.md` trial-expiry lock claim; added `docs/testing.md`; refreshed `docs/README.md` index (i18n, testing, entry points).

### Added
- **Sudo/Owner billing bypass**: `compute_install_cost` now returns 0 for `sudo` role (previously only `developer`/`owner`); dead wallet-deduction block removed from `app/handlers/install.py`; tests in `tests/test_addendum_revision_access_panel_helper.py::TestSudoBypass`.
- **`/panel` and `پنل` commands**: new group panel entry point distinct from `/help`/`راهنما`; pattern uses `\s*$` to reject sub-commands like "پنل پلیر"; registered in `app/handlers/group_panel.py`; tests in `TestCommandRouting`.
- **`/addhelper` command** (`app/handlers/add_helper.py`): `/addhelper` and `افزودن هلپر` trigger `ensure_helper_present_for_group()` (new public wrapper in `app/services/call_service.py`); returns success/already_present/unavailable/failed; new i18n keys under `admin.helpers` in `fa.json`/`en.json`; tests in `TestHelperAutoJoin`.
- **Emoji toggle buttons**: `toggle_label(base, enabled)` helper in `app/utils/ui.py` replaces `[فعال]/[غیرفعال]` bracket style with `🟢`/`🔴` emoji; `KeyboardFactory.group_settings()` updated; tests in `tests/test_dynamic_toggle_buttons.py`.
- **Hybrid i18n split**: `TextService` rewritten with manifest-driven fragment loader (`app/resources/i18n/manifest.json`, 26 fragments × 2 languages); legacy `strings/fa.json`/`en.json` preserved for fallback; `scripts/split_i18n_resources.py` generator; `docs/features/i18n.md` documentation; tests in `tests/test_i18n_hybrid_split.py` (32 tests); report `docs/reports/current/addendum_revision_implementation_report.md`.

### Changed
- Documentation refresh: reorganized Markdown under `docs/` (architecture, deployment, features, operations, specs, reports); added `docs/README.md` index and feature docs (Call Security, playback, group settings, helpers, credit, etc.); moved root deploy/audit reports to `docs/deployment/` and `docs/reports/`; updated `README.md`, `AGENTS.md`, deploy zip paths, and doc-referencing tests; report `docs/reports/current/documentation_refresh_report.md`.
- Repository markdown cleanup: removed superseded audit/verification reports (`FEATURE_VERIFICATION_REPORT.md`, `CHANNEL_SECURITY_AUDIT.md`, `CALL_SECURITY_IMPLEMENTATION_REPORT.md`) and retained deploy guides, specs, Phase 2D closeout, Call Security final reports, and test-referenced design docs; report `MARKDOWN_CLEANUP_REPORT.md`.

### Fixed
- Live `/play` silence: register slash playback with native `filters.command("play")` (same pattern as `/settings`), add `playback_slash_handler_entered` INFO diagnostic, parse `/play` via `message.command` (`parse_slash_play_command`) so `/play@bot` is not treated as dedication, prevent `/playlist` false positives, and add filter-matching tests; report `docs/reports/current/reply_playback_command_fix_report.md` (no migration, callbacks unchanged).
- Reply + `پخش` silent playback: normalized command parser (`app/utils/playback_commands.py`), shared `_handle_play_command` for `پخش`/`play`/`/play`, expanded reply-media detection (audio/voice/video/document/link), visible `replay_no_media` and `audio_enabled`/`file_enabled` gates, filter-words `continue_propagation` hygiene, playback auth/ignore diagnostic logs; tests in `tests/test_reply_playback_command_ux.py`.
- Panel edit-first UX: callback navigation, toggles, back/home, and ask timeout/success outcomes now edit the stored panel message instead of spamming new messages (developer rates/settings, owner/force-join ask flows, Call Security, group settings); Redis panel anchor `panelmsg:{chat_id}:{user_id}`; helpers in `telegram_message.py` and `panel_message_service.py`; report `docs/reports/current/panel_edit_first_ux_report.md`; tests in `tests/test_panel_edit_first_ux.py`.
- Group settings/management **بازگشت** (`nav:back`) and **منوی اصلی** (`wz:home`) now route through priority group handlers (`grp_nav_back`, `grp_wz_home`); navigation edit failures show a visible alert; `wz:home` state cleanup no longer blocks navigation on Redis/listener errors; tests in `tests/test_group_panel_navigation_phase.py`.
- Channel Security final cleanup: removed all active `امنیت کانال` / `Channel Security` i18n labels and status/dashboard references; legacy `dev:channel_security` / `own:channel_security` callbacks now only show a visible redirect to group-panel `امنیت کال` with no DB writes; `channel_security_enabled` is no longer read by runtime or dashboards; playback lock (`security_call_enabled`) and Call Security (`grp:callsec`) unchanged; report `CHANNEL_SECURITY_FINAL_REMOVAL_REPORT.md`; tests in `tests/test_channel_security_final_cleanup.py`.

## [Unreleased] - 2026-06-09

### Added
- Call Security raw mute enforcement: added `group_call_moderation_service` to use Kurigram raw MTProto `phone.EditGroupCallParticipant` through helper user clients; panel now distinguishes raw API unavailable, no active call, helper unavailable, and helper permission requirements; runtime attempts mute for new/unknown-age joiners and unmute for privileged or old-membership users; no callback/data/schema changes; report `CALL_SECURITY_MUTE_ENFORCEMENT_REPORT.md`; tests in `tests/test_call_security_raw_mute_enforcement.py`.
- Call Security (امنیت کال): per-chat `call_security_settings` table (migration `0024`) plus group membership tracking (migration `0025_group_member_membership`), panel handlers (`grp:callsec*`), runtime participant tracking with live reports and end-of-call `.txt` summary, membership-age gate (`قدمت عضویت`) for auto-unmute eligibility, credit check to enable, group/channel `پنل پلیر` entry; global `channel_security_enabled` UI deprecated with guided message; playback lock (`security_call_enabled`) unchanged; report `CALL_SECURITY_IMPLEMENTATION_REPORT.md`; tests in `tests/test_call_security_*_phase.py` and `tests/test_channel_security_deprecation_phase.py`.
- Channel Security audit report `CHANNEL_SECURITY_AUDIT.md`: documents global `channel_security_enabled` (storage-only UI) vs per-chat `security_call_enabled` (runtime playback lock), panel/callback/permission/test coverage, label clarity issues, and recommended fix phases (audit-only; no code/migration/callback changes).

## [Unreleased] - 2026-06-08

### Fixed
- Start/Text-Link/Button Layout and Playback UX alignment: private `/start` now uses developer-global `start_text` plus a deterministic dynamic URL keyboard that hides empty links and groups secondary buttons two per row; owner `start_text` is read-only/developer-only with stale `own:text:*:start_text` callbacks blocked visibly; creator CTA priority is `developer_pv_link` then `developer_link`; Persian playback help now leads with typed `پخش نام آهنگ` / `پخش لینک` / reply + `پخش`; report in `START_AND_TEXT_LINK_UX_ALIGNMENT_REPORT.md`, tests in `tests/test_start_text_and_buttons_alignment.py` and `tests/test_playback_command_ux_alignment.py` (no migration, callbacks unchanged).
- Text/link field usage alignment: `texts_links_ui` now separates runtime-active fields from storage-only fields, shows per-field runtime locations/effects, clarifies owner override impact, marks caption-only media behavior, and renames confusing labels such as creator contact, inactive messenger, helper storage-only, custom start-menu, and sudo buy links; tests in `tests/test_text_link_field_usage_alignment.py` plus updated text/link/UI suites (no migration, callbacks unchanged, no runtime DB wiring added).
- Logging visibility and reliability audit: Loguru `{}` formatting fixes in `main.py` and `scheduler.py`; startup diagnostics line; stdlib/Pyrogram/APScheduler level bridging; callback safety wrapper logs handler/phase/exception class; `create_logged_task` for background jobs; `app/utils/diagnostic_logging.py` helpers; `docs/logging_safety.md` and `scripts/check_logging_pipeline.py`; tests in `tests/test_logging_visibility_and_safety.py` (no migration, callbacks unchanged, no secrets logged).
- Helper OTP send_code post-failure reliability: in-memory pre-auth client registry replaces pre-sign-in session export; phased INFO/WARNING diagnostics via Loguru (stdlib InterceptHandler bridge); specific `otp_send_code_post_failure`, `otp_state_store_failed`, and `otp_auth_context_lost` user messages; read-only `scripts/check_helper_otp_config.py`; tests in `tests/test_helper_otp_send_code_phase_diagnostics.py` (no migration, callbacks unchanged).
- Helper OTP live phone-input silence: OTP text/contact handlers run at priority `group=-90` before pyromod listeners; hardened `safe_stop_listening` (keyword/positional/user_id fallbacks); `/start` during `awaiting_phone` cancels OTP with `otp_cancelled_by_command`; immediate `otp_phone_received` ack before `send_code`; safe debug logging; tests in `tests/test_helper_otp_live_silence_regression.py` (no migration, callbacks unchanged).
- NoSilent-3 Redis FSM expired-state replies: broadcast wizard payload/schedule text handlers and helper proxy input reply with `session_expired`/`proxy_session_expired` when Redis state is missing or on wrong step (conservative heuristics); tests in `tests/test_broadcast_wizard_no_silent_phase_nosilent3.py` and `tests/test_helper_proxy_no_silent_phase_nosilent3.py` (no migration, callbacks unchanged).
- NoSilent-2 shared ask-abort handling: centralized `AskResult`/`notify_ask_abort`/`safe_stop_listening` in `app/utils/ask_result.py`; listener-stopped feedback on high-traffic dev/owner pyromod ask flows; `force_join_panel._ask` abort reply; `stop_listening` on broadcast/proxy wizard entry; tests in `tests/test_dev_owner_ask_abort_phase_nosilent2.py`, `tests/test_force_join_ask_abort_phase_nosilent2.py`, `tests/test_wizard_entry_stop_listening_phase_nosilent2.py` (no migration, callbacks unchanged).
- NoSilent-1 dev ban-all AskResult handling: `dev_banall_add` and `dev_banall_remove` use `resp.message.text` and send abort feedback on `listener_stopped`; tests in `tests/test_dev_banall_ask_phase_nosilent1.py` (no migration, callbacks unchanged).
- LiveBug-3 text/link panel UX: visible save/clear confirmations; non-silent ask abort on listener stop; link fields hide photo/caption button; clearer Persian labels and delete wording; minimal link URL validation; owner global-scope message; tests in `tests/test_text_link_panel_ux_livebug3.py` (no migration, callbacks unchanged).
- LiveBug-2 helper OTP phone wizard no-silent: `stop_listening` on OTP start clears stale pyromod listeners; expired-session and other-wizard-active phone input get explicit replies; contact messages prompt text phone entry; tests in `tests/test_helper_otp_no_silent_phase_livebug2.py` (no migration).
- LiveBug-1 credit warning anti-spam: Redis cooldown (`credit:warn:{chat_id}:{remaining_days}:{date}`, 48h TTL) gates group, log channel, and sudo DM together in `notify_credit_warning`; fail-closed when Redis unavailable; tests in `tests/test_notification_credit_warning_dedup.py` (no migration).

### Added
- Helper OTP config diagnostic script `scripts/check_helper_otp_config.py` (API/session-key/migration checks without secrets or Telegram calls).
- Phase C-3-4 owner text/link close-out: `PHASE_C3_OWNER_TEXT_LINK_CLOSEOUT.md` (C3 feature matrix, migration head `0022`, security/runtime audit, 205-test validation bundle, deployment package and smoke checklist); synced active deployment docs to `0022` (no runtime changes).
- Phase C-3-3 owner text/link runtime reads: `bot_settings_service.get_effective_setting_value` and owner-scoped link/text helpers; group support menu and install success guide links resolve install-lineage owner via `owner_scope_service`; private `/start` and PM about/pricing remain global; tests in `tests/test_owner_text_link_runtime_phasec33.py` (no panel write changes, callbacks unchanged).
- Phase C-3-2 owner text/link panel UI: expose `OWN_CAT_TEXTS` for pure owners; `own:text:*` routes write to `owner_text_link_service` (not global `bot_settings`); scoped hub/field reads with owner/global/empty source labels; owner-specific i18n labels and confirmations; `dev:text:*` unchanged; tests in `tests/test_owner_text_link_panel_phasec32.py` (no runtime reads wired yet, callbacks unchanged).
- Phase C-3-1 owner text/link storage foundation: migration `0022_owner_text_links` adds per-owner override table; ORM `OwnerTextLink`; `owner_text_link_repo` upsert/clear/list; `owner_text_link_service` with `TextLinkValue` and owner→global→empty fallback reads; drift/tooling head sync to `0022`; tests in `tests/test_owner_text_link_repo_phasec31.py` (no UI/runtime wiring, callbacks unchanged).
- Phase C-3-0 owner text/link storage design audit: `PHASE_C3_OWNER_TEXT_LINK_STORAGE_DESIGN.md` (global field inventory, runtime read-path audit, scope resolution, schema options, recommended `owner_text_links` table `0022`, UI/callback strategy, security/tests/roadmap; design-only, no runtime changes).
- Phase C-2 owner-scoped sudo permission UI: owner sudo list/detail with `own:sudo:detail:` and `own:sp:*` permission toggles; scope guards via `owner_scope_service.sudo_belongs_to_actor`; hardened add/remove/top-up flows with NoSilent ask abort; tests in `tests/test_owner_sudo_scope_phasec2.py` (no migration; existing owner callbacks unchanged).
- Phase C-1 owner install lineage resolver: `app/services/owner_scope_service.py` resolves owner scope from `installed_by` + `Sudo.added_by`; owner panel group/channel/credit/no-credit lists and `own:stats` include sudo-installed chats; forged install guard via `assert_owner_install_access` / `owner_mgmt.not_in_scope`; tests in `tests/test_owner_scope_phasec1.py` (no migration, callbacks unchanged).
- Phase C-0 owner-scoped architecture audit: `PHASE_C_OWNER_SCOPED_ARCHITECTURE_AUDIT.md` (role model, data ownership, owner/dev panel route audit, security gaps, hybrid schema design, C1–C7 roadmap; validation: py_compile pass, JSON pass, 35 pytest pass; no runtime changes).
- No-silent input flows close-out report: `NO_SILENT_INPUT_FLOWS_CLOSEOUT.md` (NoSilent-1/2/3 + LiveBug regression status, 102-test validation bundle, deployment package, smoke checklist, safe-to-deploy verdict; no runtime changes).
- No-silent input flows read-only audit: `NO_SILENT_INPUT_FLOWS_AUDIT.md` (52-flow inventory, silent-path classes A–H, cross-wizard collision analysis, phased NoSilent-1..4 roadmap; validation: py_compile pass, JSON pass, 48 pytest pass; no runtime changes).
- Live bugs close-out report: `LIVE_BUGS_CLOSEOUT_REPORT.md` (LiveBug-1/2/3 deployment readiness, test bundle results, upload list, smoke checklist; no runtime changes).
- Live bugs read-only audit: `LIVE_BUGS_CREDIT_HELPER_TEXTLINK_AUDIT.md` (credit warning spam root cause, helper phone wizard silence, developer/owner text-link panel UX; proposed LiveBug-1..4 fix phases; no runtime changes).

## [Unreleased] - 2026-06-05

### Added
- Phase B close-out report: `PHASE_B_CLOSEOUT_REPORT.md` (feature matrix, migration head `0021`, callback audit, 159-test regression bundle, deployment checklist, live smoke matrix); staging plan head synced to `0021` (no runtime changes).
- Phase B6-1 session-only repeat lock: group `repeat` toggle stays hidden; stale `grp:set:repeat` → `vote_skip_enabled` documented; help text clarifies repeat is via now-playing buttons only; tests in `tests/test_repeat_runtime_lock_phaseb61.py`; closeout `PHASE_B6_REPEAT_RUNTIME_CLOSEOUT.md` (no migration).
- Phase B6-0 repeat vs vote-skip decision design: `PHASE_B6_REPEAT_VOTE_SKIP_DECISION.md` (in-memory `pb:repeat` audit, `vote_skip_enabled` mislabel, options A–D, B6-1/B6-2 phased recommendation, migration `0022` design only); design-only tests in `tests/test_repeat_vote_skip_design_phaseb60.py` (no runtime changes).
- Phase B5-4 Telegram-native cover display: `app/services/cover_art_service.py` extracts reply-media thumbs and sends `reply_photo` with renderer caption when `show_cover` is enabled; `playback.py` play audio/video/replay success paths use silent text fallback; search/TV/radio/playlist unchanged; tests in `tests/test_now_playing_cover_phaseb54.py` (no migration).
- Phase B5-4-0 cover display security design: `PHASE_B5_COVER_DISPLAY_SECURITY_DESIGN.md` (cover data audit, SSRF/size/MIME risks, product behavior, Option B Telegram-native recommendation, deferred external fetch); design-only tests in `tests/test_now_playing_cover_design_phaseb54.py` (no runtime changes).
- Phase B5-3 now-playing metadata flags runtime: `grp:set:show_id/show_photo/show_text` map to `show_track_id`/`show_cover`/`show_now_playing_text` (legacy columns untouched); toggles re-shown in group settings; renderer respects per-chat flags; tests in `tests/test_now_playing_metadata_flags_phaseb53.py` (requires migration `0021`).
- Phase B5-2 now-playing metadata flags: migration `0021_now_playing_flags` adds `chat_settings.show_track_id` (default false), `show_cover` (default true), and `show_now_playing_text` (default true); ORM `ChatSettings` columns; drift/tooling head sync; tests in `tests/test_now_playing_flags_migration_phaseb52.py` (schema only, no runtime wiring).
- Phase B5-1 now-playing renderer: `app/services/now_playing_renderer.py` centralizes localized now-playing text; playback, search, and TV/radio/satellite success paths use `resolve_lang` per chat; `now_playing.*` i18n keys in fa/en; tests in `tests/test_now_playing_renderer_phaseb51.py` (no migration).
- Phase B5-0 metadata toggles design audit: `PHASE_B5_METADATA_TOGGLES_DESIGN.md` (wrong-column mapping audit, product behavior, Option B migration design, renderer/security analysis, phased B5-1..B5-4 plan; no runtime changes).
- Phase B4 queue-on-busy: `smart_radio_enabled` routes busy playback to `playlists` queue (HTTP URLs only) or `playback_cmd.already_playing`; `grp:set:queue` toggle re-shown; wired in playback, search, TV/radio/satellite, and `play_playlist` guard; tests in `tests/test_queue_on_busy_phaseb4.py` (no migration).
- Phase B3 group settings: migration `0020_chat_default_media_type` adds persisted `chat_settings.default_media_type` (audio/video); `grp:set:default_media` toggle re-shown; ambiguous `پخش` and replay URL paths resolve default media type; search stays audio-only by design; tests in `tests/test_default_media_type_*_phaseb3.py`.
- Phase B2 group settings: `video_enabled` gates video playback (`play_video`, replay-on-reply video, playlist video, `join_voice_chat` defense); `music_video` and `call_report` toggles re-shown in group settings; `buttons_enabled` hides optional now-playing controls (pause/resume/stop remain); tests in `tests/test_video_enabled_gate_phaseb2.py` and `tests/test_now_playing_buttons_enabled_phaseb2.py`.
- Phase B1 group settings: per-chat language toggle (`grp:set:language`, fa↔en) in group settings panel; now-playing keyboard uses in-memory `CallService.get_repeat_state`; `main.py` stream-end repeat uses in-memory state instead of missing `repeat_mode` column; tests in `tests/test_group_settings_phaseb_language.py` and `tests/test_playback_repeat_state_phaseb1.py`.
- Phase B-0 group settings runtime design: `PHASE_B_GROUP_SETTINGS_RUNTIME_DESIGN.md` (toggle audit, migration plan, callback strategy, phased implementation order; no runtime changes).

### Added
- Config-driven safe DB URL loader (`scripts/safe_db_url.py`) for disposable/test validation; `db_schema_drift_check.py` and `validate_daily_deduct_batching.py` auto-load from `TEST_DATABASE_URL`, safe `DATABASE_URL`, or `app/config.env.disposable`; wrapper `scripts/run_disposable_alembic_check.py`; `validate_daily_deduct_batching.py` syncs resolved URL into `settings.DATABASE_URL`; tests in `tests/test_safe_db_url_loader.py`.

### Fixed
- Phase B3 hardening audit: `test_validate_daily_deduct_batching_script` expects current Alembic head; `SERVER_NATIVE_DEPLOYMENT_GUIDE` documents `0020_chat_default_media_type` as expected head; report in `PHASE_B3_DEFAULT_MEDIA_TYPE_HARDENING_AUDIT.md`.
- PyTgCalls voice-stack compatibility: `setup_server.sh` installs `py-tgcalls[pyrogram]` with native `ntgcalls` deps (replaces `--no-deps pytgcalls`); shared `app/utils/voice_stack.py` adds v2 `MediaStream` builders and VC API shims (`play`/`leave_call` fallbacks); `call_service.py` logs stream-candidate failures at debug and version on total failure; audit in `PYTGCALLS_COMPATIBILITY_AUDIT.md`; safe probe `scripts/check_voice_stack.py`; deployment guides updated; tests in `tests/test_voice_stack_diagnostic.py`.
- Phase A-2 playback UX: shared `app/utils/playback_errors.py` maps join failures for search (`search:play:*`), TV/radio/satellite callbacks, and playlist; `pb_tv` menu clears inline spinner with early `query.answer()`; close-out report in `PHASE_A_PLAYBACK_UX_CLOSEOUT.md`; tests in `tests/test_search_playback_error_mapping.py`, `tests/test_tv_radio_playback_error_mapping.py`, `tests/test_callback_query_answer_phasea2.py`.
- Phase A playback: voice-chat join failures now return specific Persian errors (helper unavailable, PyTgCalls down, stream build, no active VC) instead of only generic `playback_cmd.failed`; early return when no helper is available; `پخش` supports audio document replies; group settings keyboard hides storage-only toggles (callbacks unchanged); help text no longer claims `/skip` or `/repeat` commands; tests in `tests/test_playback_error_mapping.py`, `tests/test_play_audio_document_parity.py`, extended `tests/test_playback_temp_cleanup.py` and `tests/test_group_settings_deep_audit.py`.
- Group filter-word auto-delete now respects the group setting `filter_enabled` (`auto_clean` toggle); deep panel audit in `GROUP_SETTINGS_PANEL_DEEP_AUDIT.md`; tests in `tests/test_group_settings_deep_audit.py`.
- Group settings panel opens from Persian text `تنظیمات` and plain `settings` in addition to `/settings`; shared `_reply_group_panel` handler; audit in `GROUP_SETTINGS_PANEL_AUDIT.md`; tests in `tests/test_group_settings_command_aliases.py`.
- Failed voice-chat playback now deletes temporary Telegram download files under `DOWNLOADS_PATH/<chat_id>/` when one-shot playback does not start; playlist and download-command files are unchanged; tests in `tests/test_playback_temp_cleanup.py`.

### Added
- `PROJECT_GAPS_AND_IMPLEMENTATION_ROADMAP.md`: consolidated gap inventory (41 items), role/scope model, owner-scoped and group-settings plans, playback/cleanup/PyTgCalls tracks, and phased implementation roadmap (Phases A–E).
- Multi-developer support: `DEVELOPER_ID` env accepts comma-separated Telegram user IDs; `settings.DEVELOPER_IDS` and `is_developer()` for permissions; bootstrap seeds all configured developers; tests in `tests/test_multi_developer_id.py`.
- `scripts/build_deploy_zip.py`: builds a secret-safe deployment ZIP under `dist/` for manual SFTP upload (excludes venv, caches, sessions, and local env files; pre-zip safety scan).

### Changed
- Persian UI string audit: translated remaining English copies in `fa.json` (reports, force-join management, developer summaries, blacklist); added `labels.*` enum mappings and `label()` helper; localized broadcast confirm text, analytics headers, helper/install notifications, and enum placeholders in panels; tests in `tests/test_farsi_ui_strings.py`.
- `app/config.env.example` and `config.env.example`: server-ready `/opt/musicbot` template with `CHANGE_ME_*` placeholders, production-safe `DB_ALLOW_CREATE_ALL=false`, no test/disposable DB URLs; `setup_server.sh` recognizes `CHANGE_ME_*` placeholders.
- `DEVELOPER_ID` parsing, guards, bootstrap, dev-panel self-target flows, env examples, and `setup_server.sh` placeholder validation for comma-separated developer IDs.
- Post-verify: mock `deny_if_bot_disabled`, `can_use_sudo_admin_bypass`, and `user_repo.is_sudo` in group-panel security tests to avoid accidental DB dependency; mock install policy/links in reachability test; sync `docs/UX_FLOWS_EN.md` role detection with `is_developer()`.
- `SERVER_NATIVE_DEPLOYMENT_GUIDE.md`: added “Create deployment ZIP on Windows” section; command-only fresh install and SFTP update steps (Alembic `0019`, drift checker, systemd).

### Added
- Staging deployment and live Telegram validation plan (`STAGING_DEPLOYMENT_AND_LIVE_VALIDATION_PLAN.md`): operator checklist for staging env, Alembic/drift preflight, and disposable test group/channel live matrix; no runtime changes.
- Phase 2D close-out: `PHASE2D_CLOSEOUT_REPORT.md`; documentation sync for Alembic head `0019_daily_deduct_idem`, daily deduct defaults, helper reservation, and safe validation bundles; disposable test patches `helper_pool_service.async_session` for full-suite compatibility.
- Phase 2D-5C: disposable PostgreSQL integration tests for helper reservation (`tests/test_helper_atomic_reservation_disposable_phase2d5c.py` on `musicbot_disposable` only); fixes `ReservedHelper.current_active_calls` DTO after increment (`session.refresh`); no migration or index.
- Phase 2D-5B: atomic helper reservation (`HelperPoolService.reserve_best_helper` / `release_helper_reservation`) with short-lived `FOR UPDATE SKIP LOCKED` transactions; call path reserves before Telegram/PyTgCalls and releases on setup failure; `tests/test_helper_atomic_reservation_phase2d5b.py`; no migration or index.
- Phase 2D-5A: helper selection concurrency audit (`HELPER_SELECTION_CONCURRENCY_PLAN.md`) and behavior-lock tests (`tests/test_helper_selection_concurrency_phase2d5a.py`); documents non-atomic `get_best_helper` vs `increment_active_calls` race; no locking or routing changes.
- Phase 2D-4B-2: disposable validation harness `scripts/validate_daily_deduct_batching.py` compares legacy vs batched daily deduction on `musicbot_disposable` only; tests in `tests/test_validate_daily_deduct_batching_script.py`; batching still disabled by default.
- Phase 2D-4B-1: migration `0019_daily_deduct_idem` adds `group_credits.last_daily_deducted_on` and partial index `idx_group_credits_daily_deduct_due`; legacy and batched daily deduction use DB same-day idempotency; batching remains disabled by default; tests in `tests/test_daily_deduct_idempotency_phase2d4b1.py`.
- Phase 2D-4B-0: idempotency gate (`DAILY_DEDUCT_IDEMPOTENCY_DECISION.md`); disabled-by-default `DAILY_DEDUCT_BATCHING_ENABLED` / `DAILY_DEDUCT_BATCH_SIZE` with staging-only batched path and Redis `cron:daily_deduct:done:{date}` idempotency; production default remains legacy single transaction; tests in `tests/test_daily_deduct_batching_phase2d4b.py`.
- Phase 2D-4A: behavior-lock tests for midnight `CreditService.daily_deduct_all` and `midnight_credit_deduct` (`tests/test_daily_deduct_behavior_phase2d4a.py`) plus `DAILY_DEDUCT_BATCHING_PLAN.md` for future 2D-4B batching; no deduction, Redis, scheduler timing, or schema changes.
- Read-only Phase 2D database query performance audit (`DB_QUERY_PERFORMANCE_PHASE2D_AUDIT.md`): N+1 owner/sudo credit lists, broadcast load-all, scheduler scans, in-memory pagination candidates, and phased fix plan (2D-1–2D-5).
- Phase 3 startup schema safety: PostgreSQL production startup requires Alembic head (read-only check); `create_all()` only for `TEST_MODE`, SQLite, or `DB_ALLOW_CREATE_ALL=true`.
- `app/database/schema_readiness.py` and `tests/test_database_schema_readiness.py`.

### Changed
- Phase 2D-5C: `HELPER_SELECTION_CONCURRENCY_PLAN.md`, `NO_GIT_DEPLOYMENT_SNAPSHOT.md`, and `SERVER_NATIVE_DEPLOYMENT_GUIDE.md` document helper reservation validation and operational capacity notes.
- Phase 2D-5B: `_ensure_helper_in_chat` and `join_voice_chat` use DB reservation instead of separate `get_best_helper` + post-join `increment_active_calls`; bound helpers must pass capacity check to be reserved; legacy `get_best_helper` unchanged for read paths.
- Phase 2D-4B-1: `CreditService` daily deduct stamps `last_daily_deducted_on` and skips rows already deducted today; batched path no longer uses Redis done-set (DB authoritative); drift checker requires new column and index.
- Phase 2D-3.5: owner no-credit list and no-credit report handlers use bounded `admin_report_repo.get_no_credit_page` (50 rows) instead of loading all zero-credit chats and slicing in Python; no schema, migration, index, or credit deduction changes.
- Phase 2D-3A: `BroadcastServiceV2` uses SQL `COUNT` plus offset/limit recipient batches (500 per batch) instead of materializing all recipient IDs; cursor, cancel-every-50, and progress-every-25 behavior preserved; no schema or send/copy/forward changes.
- Phase 2D-2.5: group/channel credit warning scheduler jobs query expiring credits with SQL `chat_type` filters (10m / 13m schedules unchanged); avoids loading the other chat type per run; no schema, migration, or deduction logic changes.
- Phase 2D-2: favorites list (`callbacks._render_fav_page`) and group VIP list (`group_panel` / `admin_repo`) use SQL `LIMIT`/`OFFSET` pagination instead of loading all rows; no schema, migration, or credit business logic changes.
- Phase 2D-1: owner/sudo panel group, channel, and credit list handlers use `credit_repo.get_credits_by_chat_ids` instead of per-row `get_credit` (one bulk query per panel open); no schema, migration, or credit business logic changes.
- `init_db()` no longer calls `create_all()` on PostgreSQL unless test/dev flags allow it; seeds settings after schema readiness check.
- Migration `0018_credit_history_reconcile_orphan` (Alembic revision `0018_credit_hist_orphan`, ≤32 chars for `alembic_version`): drops empty orphan `credit_history_partitioned` (and its monthly partitions) when row count is zero; aborts if orphan table has data.
- `tests/test_credit_history_reconcile.py` for disposable PostgreSQL checks after 0018.
- Read-only `scripts/db_schema_drift_check.py` to verify Alembic head, critical tables/columns/indexes, and `credit_history` partition drift before production (SELECT-only; refuses unsafe DB names unless `--allow-non-test-db`).
- `app/config.env.disposable.example` and gitignored `app/config.env.disposable` for local `musicbot_disposable` URL; drift checker loads it automatically when `--database-url` is omitted.
- `tests/test_db_schema_drift_check.py` for URL redaction, safety guards, head detection, and status aggregation.
- `SERVER_NATIVE_DEPLOYMENT_GUIDE.md` pre-start schema drift check instructions.

### Changed
- Schema drift checker: orphan empty `credit_history_partitioned` is CRITICAL; canonical partitioned `credit_history` alone is WARNING (ORM PK mismatch deferred); flat `credit_history` is WARNING.
- Docs (`CREDIT_HISTORY_RECONCILIATION_PLAN.md`, `DB_SCHEMA_DRIFT_AND_INDEX_VALIDATION_REPORT.md`): post–2C-1 head `0018_credit_hist_orphan` and checker WARNING semantics.
- Replaced `test_gap_batch2_3` orphan monthly-partition expectation with app-code guard (no `credit_history_partitioned` references).

### Fixed
- Schema drift checker no longer prints misleading “Missing: 0” table/column lines when PostgreSQL connection fails before introspection.

## [Unreleased] - 2026-06-04

### Added
- Free-mode media restrictions: free-install chats (policy free/open, hybrid groups, or whitelist) allow only audio playback, radio, and download; video, TV, and satellite are blocked until paid credit is added.
- `app/services/media_capability_service.py` and `tests/test_free_mode_media_restrictions.py`.
- Free-mode playback type menu hides video, TV, and satellite buttons when capabilities are passed to `KeyboardFactory.playback_type_menu()`.
- Group panel “Playback Commands” (`GRP_PLAY_COMMANDS`) now renders the capability-aware playback type menu in production.
- Global User Ban (ban-all / بن آل): Developer-only panel to ban users globally, block bot access, and best-effort remove them from active installed groups/channels.
- `global_bans` table, `global_ban_repo`, `global_ban_service`, `dev_banall_panel`, `global_ban_guard`, and `tests/test_global_user_ban.py`.

- Admin title Phase 4D-manual: Developer-only manual Telegram admin promotion for developer/owner/sudo with bot promote-rights preflight, minimal fixed privileges, confirmation, and optional stored title apply after promotion.
- `tests/test_admin_title_phase4d_manual_promotion.py`.

## [Unreleased] - 2026-06-03

### Added
- Admin title Phase 4B: nullable stored `admin_title` fields for owners and sudos, `developer_admin_title` read helper, and read-only Developer UI display with Telegram-not-applied note.
- `tests/test_admin_title_phase4b.py`.
- Admin title Phase 4B-write: Developer-only stored title set/clear UI for developer, owners, and sudos with validation and clear confirmations.
- `tests/test_admin_title_phase4b_write.py`.
- Admin title Phase 4C-safe: Developer-only manual Telegram admin title apply for already-admin users with chat preflight, confirmation, and safe Telegram API error handling.
- `tests/test_admin_title_phase4c_apply.py`.

## [Unreleased] - 2026-06-02

### Fixed
- Developer Panel log channel (`bot_settings.log_channel_id`) is now used for all `NotificationService` log messages, with fallback to `LOG_CHANNEL_ID` from environment when unset.
- Scheduler memory-high alerts (`gc_and_memory_check`) now route through `NotificationService.notify_memory_high` instead of sending directly to `settings.LOG_CHANNEL_ID`.
- Log channel notifications added for sudo/owner add/remove (developer panel), privileged `/start` (developer/owner/sudo), and install logs now include installer role and install policy mode.
- Developer Panel regression tests now reflect tracked search playback metadata, hardened sudo-remove confirmation navigation, and enabled-by-default missing toggle summary values.

### Added
- `tests/test_log_channel_notifications.py` for DB/env log channel resolution, event notifications, and developer log-channel save flow.
- Developer Settings toggles for global bot on/off (`bot_enabled`) and sudo panel on/off (`sudo_panel_enabled`), with centralized guards for private routing, group messages, playback, and privileged callbacks.
- `tests/test_global_and_sudo_panel_toggles.py` for developer toggles, bot-off routing, sudo-panel-off access, and playback guard behavior.
- Developer Settings «بررسی وبسرویس» / Webservice Check (`dev:media_health`): read-only yt-dlp/ffmpeg versions, cache and downloads folder stats, active calls, download slots, cached YouTube simulate probe, and last scheduler eviction snapshot.
- `app/services/media_health_service.py` and `tests/test_dev_media_health_panel.py`.
- Developer Webservice panel JSON export (`dev:media:export`): bounded media report with redacted URLs, trusted-root file inventory, playback/queue snapshot, and Telegram document delivery.
- `tests/test_dev_media_json_export.py` for export permissions, redaction, truncation, and temp-file cleanup.
- Developer Webservice safe cleanup: dry-run preview (`dev:media:cleanup:preview`), user-bound confirmation (`dev:media:cleanup:do|no`), trusted-root-only deletion with active playback/queue/call protection, and recursive stale download cleanup in scheduler.
- `tests/test_dev_media_cleanup.py` for preview, confirmation, deletion safety, and scheduler nested downloads.
- Developer Webservice URL ranking: `media_events` table, best-effort play/download tracking with redacted URLs and SHA-256 fingerprints, `dev:media:ranking` panel, and JSON export `ranking` section.
- `tests/test_media_event_tracking.py` and `tests/test_dev_media_ranking_panel.py`.
- Developer Bot Update / Reload panel (`dev:bot_update`): runtime diagnostics, analytics error summary, and configuration-driven safe reload via sentinel file or external command with user-bound confirmation.
- `tests/test_dev_bot_update_panel.py`.
- Developer Lists UX upgrade: inline row actions on group/channel/no-credit/renewal lists (details, open link, credit +/-, leave/deactivate), detail view with metadata, row-level credit with correct chat_type, and preserved pagination context.
- `tests/test_dev_lists_row_actions.py`.
- Sudo permission matrix Phase 1: six boolean permission fields on `sudos` (default enabled), Alembic migration `0014_sudo_permissions`, read-only Developer Panel sudo detail view with per-row Details buttons, and repository read helpers.
- Sudo permission matrix Phase 2: Developer Panel toggle buttons on sudo detail (enable immediate, disable with confirmation), `set_sudo_permission` repository write, and role cache invalidation on update.
- Sudo permission matrix Phase 3: enforce stored sudo permission flags via centralized `sudo_permissions` helpers (install, credit, leave installs, chat settings, auto-admin bypass in decorators/playback).
- Phase 4A: Clarify `auto_admin_bypass` as internal-only in sudo permission labels, denial text, and Developer sudo detail note (no Telegram promotion).
- `tests/test_sudo_permission_matrix_phase1.py`.
- TEST_MODE SQLite fixture now recreates `.pytest-test-mode.db` on startup so additive model columns are reflected (`create_all` does not alter existing tables).

### Fixed
- Scheduler `cleanup_downloads` now removes stale files under nested `DOWNLOADS_PATH/{chat_id}/` directories with the same active-file protections as developer cleanup.

### Fixed
- Helper String Session import now rejects mismatched phone numbers when Telegram exposes the session account phone on `get_me()`.
- Helper add/import duplicate session detection now uses deterministic session fingerprints first, with legacy fallback only for rows missing fingerprints.
- Helper String Session import finalize now handles database duplicate races (`IntegrityError`) with a localized duplicate message instead of a generic error.

## [Unreleased] - 2026-06-01

### Fixed
- Helper add via OTP now supports Telegram 2FA by resuming the pending authorization session between OTP and password steps.
- Helper add/import wizard now keeps imported session text encrypted in Redis state and performs best-effort deletion of sensitive user inputs (session string, OTP, 2FA password).
- Legacy developer broadcast/forward buttons (`dev:bc:*`, `dev:fw:*`) now open the Advanced Broadcast wizard instead of sending immediately after one message.
- Developer text/link clear (`dev:text:clr:*`) requires user-bound confirmation before clearing a field.
- Helper enable/disable/quarantine/unquarantine actions require confirmation; cancel returns to helper detail.
- Developer owner/sudo removal (including sudo manage `-uid`) requires confirmation after parsing the target id.
- Developer Force-Join remove (`dev:fj:rm:*`) now requires a user-bound confirmation step before deleting a channel.
- Legacy forced-membership panel remove (`fm:rm:*`) now requires confirmation (still reachable from stale messages; canonical path remains `dev:fj:*`).
- Install policy ask flows return to the install policy panel on cancel/invalid input; invalid mode shows a dedicated message (`open`, `free`, `paid`, `hybrid`).
- Sudo referral link list back button returns to `dev:sudo:link:menu` instead of PM root.
- Developer credit increase/decrease invalid chat id returns to the Credit submenu.
- Developer sudo manage invalid remove input (e.g. `-garbage`) shows an error instead of failing silently.
- Broadcast cancel confirm refreshes the message to broadcast history after success.
- Playback recovery and `join_voice_chat` now use redirect-aware SSRF validation (`resolve_media_source_for_playback`); unsafe persisted sources are dropped instead of retried every startup.
- Queued and prefetch playback now validate HTTP(S) sources with redirect-aware SSRF checks (`resolve_media_source_for_playback`); unsafe queue heads are skipped without infinite retry.
- Wired developer **Sudo Referral Links** menu (`dev:sudo:link:menu`) from Users sub-panel to the existing sudo-link handlers.
- Exposed **Cancel Broadcast** in broadcast history and detail keyboards for `pending` / `running` jobs only (confirmation flow unchanged).
- Install flow now checks blacklist with `entity_type` matching the target (`group` vs `channel`).

### Added
- `tests/test_dev_panel_destructive_hardening.py` for legacy broadcast redirect, text clear, helper state, and privileged-user removal confirmations.
- `tests/test_dev_panel_hardening.py` for install policy navigation, credit return token, sudo manage errors, and texts hub back wiring.
- `tests/test_reachability_install_correctness.py` for sudo-link reachability, broadcast cancel UI, and install blacklist entity types.

## [Unreleased] - 2026-05-30

### Added
- Initial graphify knowledge graph for the repo (`graphify-out/`: `graph.json`, `graph.html`, `GRAPH_REPORT.md`) via AST/code extraction.
- `.graphifyignore` to exclude venv, caches, and local DB from graph scans.

## [3.0.10] - 2026-02-27

### Fixed
- Closed UI hardening risks across callback/nav/wizard architecture:
  - Added global `NOOP` callback handling for pager indicator buttons (fast `query.answer()`, no message edit).
  - Added single wizard-state authority in `wizard_ui` for detection/cleanup of all known wizard namespaces.
  - Ensured both `WZ_HOME` and `/cancel` clear helper OTP/proxy + broadcast wizard + return-token state.
  - Added safe global nav renderer for text/caption/non-editable messages so Back/Home/Wizard nav does not fail on media previews.
  - Updated callback safety wrapper to answer exactly once (fallback answer only when handler did not answer), preventing double-answer conflicts while preserving alert-capable handler answers.
  - Added active-wizard gating to private text handlers (`helper_otp`, `helper_proxy`, `broadcast_wizard`) to prevent stale-state hijacking.
  - Reworked broadcast confirm flow to emit one aggregated final summary (no per-target overwrite loop).
  - Removed remaining hardcoded `_LANG="fa"` in targeted panels (`help_center`, `sudo_panel`, `force_join_panel`, `broadcast_panel`) in favor of runtime language context (`AUTO_LANG`).
  - Replaced literal callback drift in broadcast callbacks with CB-driven patterns and hardened malformed callback handling.
  - Hardened malformed TV/SAT/Radio + broadcast callback payload handling with defensive parsing and safe navigation fallback.

### Added
- New tests:
  - `tests/test_noop_and_navigation_media.py`
  - `tests/test_wizard_state_authority.py`
  - `tests/test_callback_answer_policy.py`
  - `tests/test_broadcast_confirm_summary.py`
  - `tests/test_callback_coverage_report.py`
  - `tests/test_malformed_callback_payloads.py`
  - `tests/test_runtime_language_panels.py`
- Smoke runner flows:
  - Noop callback page indicator safety.
  - Broadcast confirm final summary.

### Files changed
- `app/handlers/__init__.py`
- `app/handlers/broadcast_panel.py`
- `app/handlers/broadcast_wizard.py`
- `app/handlers/callbacks.py`
- `app/handlers/force_join_panel.py`
- `app/handlers/help_center.py`
- `app/handlers/helper_otp_wizard.py`
- `app/handlers/helper_panel.py`
- `app/handlers/sudo_panel.py`
- `app/handlers/tv_radio.py`
- `app/services/wizard_ui.py`
- `app/utils/pagination.py`
- `app/utils/ui.py`
- `app/resources/strings/en.json`
- `app/resources/strings/fa.json`
- `tests/test_noop_and_navigation_media.py`
- `tests/test_wizard_state_authority.py`
- `tests/test_callback_answer_policy.py`
- `tests/test_broadcast_confirm_summary.py`
- `tests/test_callback_coverage_report.py`
- `tests/test_malformed_callback_payloads.py`
- `tests/test_runtime_language_panels.py`
- `scripts/smoke_ui.py`
- `CHANGELOG.md`

### How to run tests
```bash
ruff check app/handlers/__init__.py app/handlers/broadcast_panel.py app/handlers/broadcast_wizard.py app/handlers/callbacks.py app/handlers/force_join_panel.py app/handlers/help_center.py app/handlers/helper_otp_wizard.py app/handlers/helper_panel.py app/handlers/sudo_panel.py app/handlers/tv_radio.py app/services/wizard_ui.py app/utils/pagination.py app/utils/ui.py tests/test_noop_and_navigation_media.py tests/test_wizard_state_authority.py tests/test_callback_answer_policy.py tests/test_broadcast_confirm_summary.py tests/test_callback_coverage_report.py tests/test_malformed_callback_payloads.py tests/test_runtime_language_panels.py tests/test_helper_add_flow_e2e.py tests/test_helper_callback_coverage.py
TEST_MODE=1 pytest -q tests/test_helper_add_flow_e2e.py tests/test_helper_callback_coverage.py tests/test_noop_and_navigation_media.py tests/test_wizard_state_authority.py tests/test_callback_answer_policy.py tests/test_broadcast_confirm_summary.py tests/test_callback_coverage_report.py tests/test_malformed_callback_payloads.py tests/test_runtime_language_panels.py tests/test_broadcast_wizard.py
python scripts/smoke_ui.py
```

## [3.0.9] - 2026-02-27

### Fixed
- Helper callback family (`hlp:*`) deep-fix:
  - Added missing `hlp:add` route and helper add-method menu wiring.
  - Added in-bot `hlp:import` wizard flow (session import -> phone -> max calls -> max joins -> encrypted DB save).
  - Fixed helper add navigation so each wizard step has inline `Cancel` + `Home` + `Back` (when applicable).
  - Added missing import back-routes: `hlp:imp:back:session`, `hlp:imp:back:phone`, `hlp:imp:back:max_calls`.
  - Added `hlp:home`-area stale-state cleanup to prevent OTP/proxy state leakage after leaving wizard screens.
- Wizard state clearing:
  - `clear_runtime_state` now clears both `wz:helper_otp:{user_id}` and `wz:helper_proxy:{user_id}`.
  - `WZ_HOME` now clears runtime wizard state before routing home.
  - `/cancel` path remains routed through `cancel_and_resolve`, now covering all helper wizard states.

### Added
- Tests:
  - `tests/test_helper_add_flow_e2e.py` (helper panel -> add menu -> import flow -> DB row + encrypted session + final nav buttons).
  - `tests/test_helper_callback_coverage.py` (`hlp:*` callback coverage, overlap check, and state-clear regressions for `/cancel` + `WZ_HOME`).
- i18n helper keys (FA/EN parity):
  - Add-method labels and import wizard prompts/status messages.
- Smoke:
  - Added one smoke flow: `hlp:home -> add helper -> success`.

### Files changed
- `app/utils/ui.py`
- `app/handlers/helper_panel.py`
- `app/handlers/helper_otp_wizard.py`
- `app/handlers/callbacks.py`
- `app/services/wizard_ui.py`
- `app/resources/strings/fa.json`
- `app/resources/strings/en.json`
- `tests/test_antiban.py`
- `tests/test_helper_fixes.py`
- `tests/test_helper_add_flow_e2e.py`
- `tests/test_helper_callback_coverage.py`
- `scripts/smoke_ui.py`
- `CHANGELOG.md`

### How to run tests
```bash
ruff check app/ tests/
pytest -q tests/test_helper_add_flow_e2e.py tests/test_helper_callback_coverage.py
python scripts/smoke_ui.py
```

## [3.0.8] - 2026-02-27

### Fixed
- UI Navigation:
  - Fixed TV/Radio/Satellite dead-ends by attaching Back/Home navigation to success and failure screens, and appending Back/Home beneath now-playing controls.
  - Fixed broadcast wizard dead-ends for scheduled/recurring confirmations by returning done navigation (`Back` to broadcast menu + `Home`).
  - Fixed broadcast cancel flow to restore the broadcast category page instead of leaving a generic cancelled endpoint.
- Owner UX Architecture:
  - Replaced reachable legacy Owner flat-wall root keyboard with categorized Owner root sections.
  - Added Owner sub-keyboards (`installs`, `broadcast`, `settings`, `users`, `reports`, `billing`, `texts`) and wired callbacks.
  - Ensured `Back` from Owner sub-pages returns to categorized Owner root.
- Language Handling:
  - Added runtime language context support in i18n (`AUTO_LANG` + context language override).
  - Added `language_service` resolution path: `chat_settings.language` first, then user language fields (if present), fallback to `fa`.
  - Bound language context on every callback/message in core callbacks registration (group `-1000`) so legacy hardcoded handler calls no longer force Persian output.
  - Localized decorator permission-denied responses using resolved runtime language.
- Wizard State Isolation:
  - Namespaced helper OTP Redis key to `wz:helper_otp:{user_id}`.
  - Added dedicated helper proxy Redis namespace `wz:helper_proxy:{user_id}` and removed shared-state collision with OTP flow.
  - Added dedicated proxy cancel callback (`hlp:proxy:cancel`) that clears only proxy state.
- Group Routing Determinism:
  - Replaced order-dependent generic `^grp:set:` callback route with explicit mutually-exclusive toggle regex based on known toggle callbacks.

### Added
- Added focused UI hardening regression tests in `tests/test_ui_hardening_pass.py` covering:
  - TV/Radio/SAT post-action navigation.
  - Broadcast scheduled/recurring completion navigation.
  - Broadcast cancel return behavior.
  - Language resolution from chat settings (`en`) on rendered callbacks.
  - Wizard state isolation between helper OTP and helper proxy flows.
  - Deterministic group setting regex exclusion for default-media toggle.
  - Cancel handler propagation safety (no duplicate local cancel responses).

## [3.0.7] - 2026-02-27

### Added
- Added `app/services/bot_settings_service.py` for DB-first resolution of `/start` texts/links with ENV fallback only when a DB key is missing.
- Added helper OTP hardening tests in `tests/test_helper_add_flow.py`:
  - `test_helper_add_success`
  - `test_helper_add_duplicate_rejected`
  - `test_helper_add_cancel_returns_to_panel`
  - `test_helper_session_encrypted_at_rest`
  - `test_helper_add_requires_permission`
- Added role/permission regression tests in `tests/test_role_permissions.py` and DB-start-settings tests in `tests/test_start_db_settings.py`.

### Fixed
- Fixed public `/start` menu links to load from `bot_settings` instead of direct ENV/hardcoded defaults (`panel_router` + `KeyboardFactory.start_menu`).
- Fixed `/start` About/Pricing rendering to read DB-configured texts (`about_text`, `tariff_text`) with i18n-safe fallback.
- Fixed Group support callbacks (creator/guide/support) to read DB links and return a localized “not configured” error when unset.
- Fixed owner visibility gate so `owner_filter()` no longer treats sudo users as owner-level.
- Fixed helper OTP race conditions by adding a distributed lock (`helper_add_lock_key`) around finalize/write and duplicate checks on `phone`, `tg_user_id`, and session material.
- Fixed helper OTP dead-end/error UX by ensuring failure/cancel paths return navigation keyboards (`Back/Home`), and by adding a success CTA to open helper detail.
- Fixed hardcoded helper UI text in helper detail/proxy buttons by moving labels to i18n keys.
- Fixed decorator authorization UX for forged/manual callbacks by returning localized no-access responses for denied callback/message actions.

## [3.0.6] - 2026-02-27

### Added
- Added missing Developer blacklist callback wiring (`DEV_BLACKLIST`) with repository-backed list/add/remove flow and wizard-safe navigation.
- Added callback wiring audit regression tests that verify KeyboardFactory callback coverage (including dynamic helper-prefix routes) and panel-level callback health for Dev/Owner/Sudo/Group.
- Added `tests/test_ui_behavioral_e2e.py` with 10 behavioral regression tests for start single-send, back/home safety, wizard navigation completion, force-join enforcement/CRUD, seeded report paging, leave confirmation/update flow, texts/media persistence, and category summary rendering.
- Added `scripts/smoke_ui.py` (single-command mocked smoke runner) that executes 25 critical UI/backend flows without Telegram network access.
- Added local smoke infra templates: `docker-compose.test.yml` and `app/config.env.test.example`.

### Fixed
- Fixed dead-end owner/admin management paths by attaching consistent Back/Home done keyboards in Owner media-policy, install-limits, filters, sudo-manage, and blacklist flows.
- Fixed category dead text in Group panel by rendering settings/management summaries with live state/count data instead of empty title-only bodies.
- Fixed callback spinner regressions in shared callbacks (`nav_back`, wizard cancel/back/home, favorites pagination, post-install help) by explicit early `query.answer()`.
- Fixed runtime i18n formatting bug where `t(..., key=...)` collided with the translator signature; summary rows now format `list_fmt.setting_entry` safely.
- Fixed `TEST_MODE` DB bootstrap by making `app/database/engine.py` dialect-aware; SQLite now skips PostgreSQL-only pool arguments so mocked CI smoke runs succeed.
- Fixed start navigation regression mock shape in `tests/test_start_navigation_regression.py` by providing `query.answer` on synthetic callback queries.

### Improved
- Improved maintainability by aligning dev/owner blacklist flows to existing repository/service patterns and keeping callback routing strictly centralized around stable `CB` constants.

## [3.0.5] - 2026-02-27

### Fixed
- Fixed Force-Join management gaps by adding Developer/Owner CRUD flows (add/list/remove required channels), Redis cache invalidation on mutations, and explicit sudo denial handling in owner-scope force-join actions.
- Fixed broken Developer report callbacks (`dev:list:groups`, `dev:list:channels`, `dev:list:no_credit`, `dev:list:renewal`) and `dev:leave_group` with paginated DB-backed rendering, leave confirmation, and safe uninstall/deactivation updates.
- Fixed empty category/back-navigation content by returning non-empty summary payloads for Developer category routes (`credit`, `lists`, `settings`, `users`) through both direct callbacks and wizard back/cancel navigation.

### Improved
- Improved admin report/query performance with repository-level paginated queries and short-lived Redis-cached dashboard summaries.
- Improved Force-Join data access with repository pagination/count methods used by new management UI paths.

## [3.0.4] - 2026-02-27

### Fixed
- Fixed dead-end wizard/admin flows by adding consistent inline recovery actions (`❌ انصراف`, `🔙 بازگشت`, `🏠 منوی اصلی`) and routing cancel/back/home through a shared wizard navigation handler.
- Fixed `/cancel` recovery to clear wizard runtime state (listeners + Redis FSM keys) and return users to the correct menu instead of leaving text-only endpoints.

### Added
- Added dedicated management actions for Developer/Owner panels: list owners, list sudos, remove owner, remove sudo.
- Added paginated owner/sudo listings with Back/Home navigation and stable callback routing (`pg:dev:*`, `pg:own:*`, `wz:*`).
## [3.0.3] - 2026-02-26

### Fixed
- Fixed duplicate `/start` risk by enforcing a single authoritative start registration and stopping message propagation after the single routed reply.
- Fixed back navigation regression by routing `nav:back` through the same role-aware root payload builder used by `/start`, preventing legacy panel resurrection.
- Added regression tests for one-message `/start` behavior (developer/owner/sudo/regular), role-correct back routing (developer/owner/sudo), single `/start` handler registration, and legacy-flat callback exclusion from developer root.

## [3.0.2] - 2026-02-26

### 🌐 Zero-Hardcode i18n Audit

#### Hardcoded Strings Extracted
- OTP wizard success summary: 11-line f-string → `admin.helpers.otp_success_summary` key (8 template vars)
- Callback handler: `": آپدیت شارژ N"` → `install.increase_credit_hint` key
- Config default: Persian `START_TEXT` → empty string (uses DB bot_settings)

#### Bug Found & Fixed
- `{lang}` template variable in i18n string collided with `t(lang, key)` first parameter → `TypeError`. Renamed to `{lang_code}`

#### Parity
- 637 keys in both `fa.json` and `en.json` — 100% match, zero mismatches
- Full report in `I18N_AUDIT_REPORT.md`

## [3.0.1] - 2026-02-26

### 🧪 E2E Edge-Case Tests + Proxy Regex Bug Fix

#### Bug Fix: Proxy Validation Regex
- Fixed regex allowing partial auth (`socks5://user@host:port` without password) — would corrupt `proxy_username` field in DB
- Tightened hostname pattern to `[A-Za-z0-9._-]+` to reject IPv6 brackets and special characters

#### E2E Edge-Case Tests (`tests/test_helper_e2e_edge_cases.py` — 26 tests)
- **FSM State Bleeding (4 tests):** Back clears old phone/fingerprint, cancel wipes all state, TTL parameter verified, no-state is no-op
- **Telegram API Exceptions (6 tests):** PhoneNumberInvalid, PhoneCodeInvalid, SessionPasswordNeeded→2FA transition, PasswordHashInvalid, FloodWait with wait time, short code rejection
- **Proxy Validation (10 tests):** Valid socks5/http/mtproto, reject garbage/missing-port/IPv6/missing-scheme/empty/https/partial-auth
- **Fingerprint Persistence (5 tests):** 10x build_client always same fingerprint, no randomization in build_client source, pick_random only in OTP, independent helpers get independent fingerprints, null fingerprint omits kwargs
- **Full Happy Path (1 test):** phone → send_code → code → sign_in → finalize with fingerprint in summary

## [3.0.0] - 2026-02-26

### 🛡 Anti-Ban Fingerprinting, Proxy Isolation & OTP Polish

#### Database Upgrade (Migration 0009)
- Added 9 columns to `helper_accounts`: `device_model`, `system_version`, `app_version`, `lang_code`, `proxy_type`, `proxy_host`, `proxy_port`, `proxy_username`, `proxy_password`

#### Device Fingerprint Pool (`app/utils/device_spoof.py`)
- 30 modern high-end device profiles (Samsung S24 Ultra, Pixel 8 Pro, Xiaomi 14, OnePlus 12, Sony Xperia, etc.)
- Android 13–14 with Telegram 10.14.x client versions
- Fingerprint assigned ONCE at helper creation, reused on every subsequent login

#### Proxy Isolation Engine
- `HelperPoolService.build_client()` — central Client builder applies fingerprint + proxy from DB
- Refactored `join_chat_as_helper`, `leave_chat_as_helper`, `ensure_helper_joined` to use `build_client`
- "🌐 Set Proxy" button in helper detail panel with format parser (socks5/http/mtproto)

#### OTP Wizard Polish
- Back button (🔙) at every step: Phone → Code → 2FA
- Fingerprint automatically assigned during OTP login
- Success summary shows device model, Android version, app version, Telegram ID
- 4 new i18n keys for proxy UI in both fa.json and en.json

#### Tests
- 15 new tests in `tests/test_antiban.py`: fingerprint pool diversity, model columns, migration, build_client fingerprint/proxy, OTP nav buttons, proxy button, i18n parity

## [2.9.0] - 2026-02-26

### 🔐 Helper Pool Bug Fixes & In-Bot OTP Wizard

#### Bug 1: Redis Key Fix
- Moved `idem:helper:join:{helper_id}:{chat_id}` to central `redis_keys.py` registry
- Added `helper_join_idem_key()` factory function

#### Bug 2: Load Balancing Counter Fix (CRITICAL)
- Added `HelperPoolService.increment_active_calls(chat_id)` — called after successful `join_group_call`
- Added `HelperPoolService.decrement_active_calls(chat_id)` — called on `leave_voice_chat`, clamped to zero
- The least-loaded selection algorithm now reflects actual helper load

#### Bug 3: Health Watchdog Upgrade
- Watchdog now performs **full `get_me()` login test** instead of just decryption
- Fatal errors (`UserDeactivated`, `UserBanned`, `AuthKeyUnregistered`, `SessionRevoked`, `SessionExpired`) → immediate `status='disabled'`
- Non-fatal errors → quarantine 30 min (unchanged)
- Successful probes update `tg_user_id`, `username`, `display_name`, `last_login_at`

#### In-Bot OTP Wizard (New Feature)
- `app/handlers/helper_otp_wizard.py` — 5-step FSM for adding helpers via Telegram
- Step 1: Phone number → Step 2: `send_code` → Step 3: OTP (spaced/dashed format) → Step 4: 2FA if needed → Step 5: `export_session_string` + encrypt + save
- Handles `PhoneCodeInvalid`, `SessionPasswordNeeded`, `FloodWait` gracefully
- Cancel button at every step, 5-minute FSM state TTL
- "➕ Add New Helper" button added to helper panel home keyboard
- 13 new i18n keys in both `fa.json` and `en.json`

#### Tests
- 13 new tests in `tests/test_helper_fixes.py`

## [2.8.0] - 2026-02-26

### 🚀 P0 + P1 + P2 Production Optimizations

#### P0: Content-Addressed Media Cache + LRU Disk Guard
- `app/services/media_cache.py`: SHA-256 URL hash deduplication — identical URLs across groups share a single cached file
- Canonical URL normalization strips tracking params (`si=`, `list=`, `t=`, etc.) before hashing
- Two-level shard directory structure (`/cache/ab/abcdef...ext`) for filesystem scalability
- **LRU eviction**: configurable `MEDIA_CACHE_MAX_GB` (default 10 GB) with eviction to `MEDIA_CACHE_TARGET_GB` (default 8 GB)
- **TTL sweep**: hourly cron deletes files untouched for `MEDIA_CACHE_MAX_AGE_HOURS` (default 24h)
- Wired into `media_service.py` — cache_lookup before download, cache_store after, async evict_lru on store
- Scheduler job `media_cache_eviction` runs hourly (TTL + LRU combined)

#### P1: Zero-Gap Prefetch Pipeline
- Background `asyncio.Task` downloads + transcodes the next playlist item while current track plays
- `_prefetch_cache` dict stores ready-to-play transcoded file path per chat
- `play_next` checks prefetch cache first → instant track transition on hit
- Skip/leave/cancel gracefully cancels prefetch tasks and cleans up files
- `_trigger_prefetch` called after both `join_voice_chat` and `play_next`

#### P2: Pre-Transcode Worker Pool
- `app/services/transcode_pool.py`: bounded `asyncio.Semaphore` pool (default 4 workers)
- Audio: `-vn -c:a libopus -b:a 48k -ar 48000 -ac 2 -threads 1`
- Video: `-c:v libx264 -preset ultrafast -maxrate 1500k -vf scale=-2:480 -c:a aac -b:a 128k -threads 2`
- Graceful fallback: FFmpeg failure or timeout → original file used unchanged
- `cleanup_transcoded()` removes `.tc.ogg`/`.tc.mp4` derivatives on leave
- Wired into `call_service.py` `join_voice_chat` and `play_next`

#### New Config Settings
- `MEDIA_CACHE_PATH`, `MEDIA_CACHE_MAX_GB`, `MEDIA_CACHE_TARGET_GB`, `MEDIA_CACHE_MAX_AGE_HOURS`, `TRANSCODE_POOL_SIZE`

#### New Redis Keys
- `media_cache:evict`, `media_cache:hit:{hash}`, `prefetch:{chat_id}`
- `credit_lock_key`, `install_lock_key`, `install_fee_lock_key`, `wallet_lock_key`, `chat_lock_key`

#### Tests
- 28 new tests in `tests/test_p0_p1_p2.py` covering cache CRUD, dedup, LRU eviction, TTL sweep, prefetch lifecycle, transcode pool, fallback paths, scheduler job, settings, Redis keys

## [2.7.0] - 2026-02-26

### 🎬 Video Pipeline Fix, Final Redis Sweep & Creative Optimizations

#### Video Pipeline Fix (CRITICAL)
- Restored video streaming support — previous `-vn` flag was applied globally, breaking music video playback
- Audio-only: `-vn -c:a libopus -b:a 48k -threads 1` (unchanged)
- Video: `-c:v libx264 -preset ultrafast -maxrate 1500k -bufsize 3000k -vf scale=-2:480 -c:a aac -b:a 128k -threads 2`
- Dynamic parameter selection based on `media_type` in `media_service.py`

#### Final Redis Key Sweep
- Migrated 12 additional raw Redis key patterns to `redis_keys.py`:
  - `credit_lock_key`, `install_lock_key`, `install_fee_lock_key`, `wallet_lock_key`, `chat_lock_key`, `FM_VERIFY_LOCK`
- Refactored `credit_service.py` (4 occurrences), `install.py` (2), `owner_panel.py` (1), `install_policy_service.py` (1), `broadcast_service_v2.py` (2), `forced_membership_service.py` (2), `playlist.py` (3)
- **Zero raw Redis key strings remain** in services/handlers — all centralized in `redis_keys.py`

#### CREATIVE_OPTIMIZATIONS.md
- Proposal 1: Content-addressed media cache (SHA-256 URL hash dedup) — 40% bandwidth reduction
- Proposal 2: FFmpeg process pool with Unix pipe streaming — 75% RAM reduction
- Proposal 3: Predictive N+1 prefetch pipeline — zero inter-track silence gap

## [2.6.0] - 2026-02-26

### 🏗️ Redis Key Registry, FFmpeg Optimization & Live Stress Test

#### Redis Key Consolidation
- Created `app/utils/redis_keys.py` — central registry with 30+ key templates, factory functions, and TTL constants
- Refactored `cache.py` to import all keys from the registry — zero hardcoded strings
- Refactored `analytics_service.py`, `broadcast_wizard.py`, `credit_service.py`, `scheduler.py`, `helper_pool_service.py`, `seek_tracker.py`, `playlist.py`, `admin_repo.py` to use registry keys

#### Import Optimization
- Moved all module-level imports to PEP 8 top-of-file position
- Removed unused imports

#### FFmpeg Audio Pipeline Optimization
- Changed download codec from MP3/quality-0 to Opus/48k with `-vn -c:a libopus -b:a 48k -threads 1`
- Audio-only extraction (`-vn`) saves ~40% CPU vs default
- Single-threaded FFmpeg to eliminate context-switching overhead

#### Live Cursor Cloud Stress Test
- Created `tools/benchmark_stream.py` — spawns N concurrent FFmpeg processes with psutil monitoring
- Tested 10 → 30 → 50 → 100 → 150 → 200 → 250 → 300 → 330 concurrent streams
- Results: 46.7 MB/stream RSS, 0.22% CPU/stream
- Safe limit: **250 streams** on 4 vCPU / 16 GB (25% headroom)
- RAM is the bottleneck (not CPU)
- Full report in `PERFORMANCE_REPORT.md`

## [2.5.0] - 2026-02-26

### 🔧 SRE Reliability & Resource Optimization

#### Bug Hunt & Audit
- Verified all 100+ `async_session()` calls use `async with` context managers — no session leaks
- Verified all 14 `acquire_lock/release_lock` pairs have `finally` blocks with reasonable TTLs (5s–600s)
- No synchronous blocking calls found in async handlers (no `time.sleep`, no `requests.*`, no blocking file I/O)

#### Connection Pool Optimization
- Added `pool_pre_ping=True` to SQLAlchemy engine — auto-detects and recycles stale DB connections
- Added `statement_cache_size=0` and `prepared_statement_cache_size=0` connect args — prevents asyncpg prepared-statement cache conflicts under high concurrency

#### Redis Caching Layer (Zero-Impact)
- Wired `is_sudo_or_above()` to Redis role cache (`role:{user_id}`, 600s TTL) — eliminates 2 DB queries per authorization check on every message
- Wired `get_chat_settings()` to Redis settings cache (`settings:{chat_id}`, configurable TTL) — skips DB for frequent settings lookups
- Wired `is_vip()` to Redis VIP cache (`vip:{chat_id}:{user_id}`, 300s TTL) — avoids per-message DB hit
- Added smart cache invalidation: `add_sudo`, `remove_sudo`, `add_owner`, `remove_owner` invalidate role cache; `update_setting` invalidates settings cache; `promote_vip`, `demote_vip` invalidate VIP cache

#### Broadcast Filter Engine
- Wired `filter_type` (all, 7d, 30d) into `BroadcastServiceV2._get_recipients()` — audience segmentation now filters by `last_seen`/`last_activity`
- Users query now filters out banned users (`is_banned=False`)

#### E2E Broadcast Tests
- 21 new tests in `tests/test_broadcast_e2e.py` covering:
  - 5000-user broadcast simulation (0.99s execution with mocked sleep)
  - FloodWait retry logic with exponential backoff
  - Mid-broadcast cancel detection
  - Lock-based double-execution prevention
  - Audience filtering (30d groups, all groups)
  - Forward mode, photo mode, progress tracking, cursor resume
  - APScheduler job execution and exception swallowing
  - Redis cache hit/miss for `is_sudo_or_above`, `is_vip`, settings
  - Cache invalidation on sudo/owner/vip/settings mutation

## [2.4.0] - 2026-02-26

### 📢 Advanced Broadcast Wizard Engine

- Added 5-step interactive FSM broadcast wizard (payload capture → mode → checkbox targets → audience filter → scheduling)
- Checkbox-style inline keyboard for multi-target selection (users, groups, channels) with optimistic UI toggle
- Jalali/Shamsi datetime input support via `jdatetime` library for scheduled broadcasts
- Four scheduling modes: Send Now, Send at Date/Time (Jalali), Send after N hours, Recurring every N hours
- Full back navigation across all wizard steps preserving state
- APScheduler integration for delayed (`date`) and recurring (`interval`) broadcast jobs
- New DB columns on `broadcasts` table: `run_at`, `interval_hours`, `target_types_json`, `filter_type`, `source_admin_chat_id`, `source_admin_msg_id`
- Alembic migration `0008_broadcast_scheduling` for new schema
- 20 new CB constants (`BCW_*`) and 4 back-navigation constants
- 30+ new i18n keys under `broadcast.wizard.*` in both `fa.json` and `en.json`
- Wizard entry point added to dev broadcast sub-menu panel
- Redis-backed FSM state with 10-minute TTL auto-expiry
- 17 new tests covering CB constants, keyboard builders, FSM state, Jalali parsing, confirm text, model columns, i18n parity, migration, and panel integration

## [2.3.0] - 2026-02-25

### 🎨 Premium UX Overhaul

#### Gap 1 — Panel Sub-Menu Restructuring
- Developer panel reduced from **40 buttons → 12 category buttons** with 7 focused sub-menus
- New categories: 💳 Credit, 💰 Rates, 📢 Broadcasts, 📋 Lists, ⚙️ Settings, 👥 Users, ✏️ Texts
- Each sub-menu has clean Back → Main Panel navigation
- 7 new CB constants (`DEV_CAT_*`) + 7 new keyboard builders (`dev_sub_*`)
- 14 new i18n keys for category titles (both fa + en)

#### Gap 2 — Sudo Welcome DM
- New sudos now receive a structured welcome DM explaining their role, charge commands, and how to access the Sudo Panel
- Uses `safe_send_message` — graceful if sudo blocked the bot
- New i18n key: `sudo_mgmt.welcome_dm`

#### Gap 3 — Actionable Playback Error
- "Playback failed" error now provides 3 actionable steps: check link validity, try another track, contact support
- Updated in both fa.json and en.json

#### Gap 4 — Jargon-Free Labels
- "فوروارد" → "ارسال نقل‌قولی" (6 instances)
- "امنیت کانال" → "قفل پخش (فقط ادمین‌ها)"
- "خروج خودکار بدون اعتبار" → "ترک خودکار هنگام اتمام اعتبار"
- "پاکسازی خودکار" → "حذف پیام‌های اضافه ربات"
- "آماد کال خودکار" → "ماندن در ویس چت بعد از اتمام"

## [2.2.1] - 2026-02-25

### ✨ Frontend Completion — 100% Callback Coverage
- **PB_FAV_LIST**: Paginated favorites list with inline 🗑 Remove buttons per item, Prev/Next navigation, and elegant header from i18n
- **PB_FAV_RM**: Optimistic-update remove — deletes the favorite, shows a toast notification, and instantly re-renders the current page without closing the menu
- **POST_INSTALL_GUIDE**: Verified already a URL button (`_url_btn`) — no callback needed
- **POST_INSTALL_ADD_HELPER**: Verified not in any keyboard — reserved constant only
- New CB constants: `PAGE_FAV`, `FAV_RM_PREFIX`
- New i18n keys: `favorites.list_header`, `favorites.remove_btn`, `favorites.removed_toast` (both fa + en)
- 6 new tests in `tests/test_frontend_final.py`
- **Frontend coverage: 100%** — all 219 CB constants now have working handlers or are correctly URL buttons/reserved

## [2.2.0] - 2026-02-25

### 🚀 Deployment Infrastructure (Production-Ready)
- **Bootstrapper** (`app/bootstrap.py`): Auto-seeds `DEVELOPER_ID` into `users` and `owners` tables on first run. Idempotent — safe on every restart. Wired into `main.py` startup sequence after `init_db()`.
- **config.env.example**: Comprehensive reference with all 35+ environment variables, grouped by category with explanatory comments.
- **deployment/musicbot.service**: Production systemd template with `Restart=always`, `RestartSec=5`, `LimitNOFILE=100000`, security hardening (`ProtectSystem=strict`, `PrivateTmp=true`, `NoNewPrivileges=true`).
- **deploy.sh**: Zero-downtime deployment script with 4 modes (`--install`, `--upgrade`, `--migrate`, `--restart`). Creates venv, installs deps, runs migrations, restarts service.

## [2.1.0] - 2026-02-25

### 🛡️ Architectural Hardening (Ultra-Deep Audit Fixes)

#### Phase 1 — Survival Engine
- **RISK-1**: Memory threshold safeguard — `gc_and_memory_check` now compares `psutil.virtual_memory().percent` against 70% threshold; triggers watchdog + log channel alert when exceeded
- **RISK-2**: `_recycle_pending` flag set added to `call_service.py` for graceful PyTgCalls idle recycle support
- **RISK-3**: Aggressive disk cleanup — `_cleanup_source_file()` deletes downloaded media immediately when a stream ends (on `leave_voice_chat`); only targets local files, ignores URLs
- **RISK-4**: Playlist lock TTL extended to 15 seconds (from default 5s) to prevent lock expiry during slow operations
- **DEV-1**: Process registry now tracks `last_activity` timestamp and `download_task` reference per active call

#### Phase 2 — Presentation Layer
- **LIMIT-1 + LIMIT-2**: Universal pagination utility (`app/utils/pagination.py`) with `paginate_text()` and `paginate_keyboard()` — chunks output to <3800 chars, generates Prev/Next inline navigation
- Dev panel group list refactored to use pagination with `PAGE_DEV_GROUPS` callback prefix

#### Phase 3 — Business Logic
- **DEV-2**: `auto_leave_check` now respects `announce_enabled` — groups with disabled call messages no longer receive the credit expired notification
- **DEV-3**: `charge_on_install` check confirmed already present at `install_policy_service.py:35`

#### Tests
- 16 new tests in `tests/test_ultra_deep_fixes.py` covering all fixes

## [2.0.0] - 2026-02-25

### 🚀 Production Release — Final Polish

### Fixed (from Deep Gap Analysis)
- **CRITICAL**: All ~68 callback handlers now auto-answer queries instantly via `_wrap_callback_handlers_with_auto_answer` — zero manual edits, applied as a post-registration hook that wraps every `CallbackQueryHandler` to call `query.answer()` first and catch `MessageNotModified`
- **MEDIUM**: Created `app/utils/safe_sender.py` — centralized `safe_send_message()` that catches `UserIsBlocked`, `ChatWriteForbidden`, `PeerIdInvalid`, `FloodWait` and marks unreachable chats/users in DB
- **MEDIUM**: Refactored `NotificationService` and `auto_leave_check` to use `safe_send_message` — all DM and group notification flows are now crash-proof
- **LOW**: Added `cleanup_expired_whitelists` cron job (daily 03:00) — bulk deletes expired `free_install_whitelist` rows per addendum §3.2.2
- 11 new tests in `tests/test_final_polish.py`

## [2.0.0-rc1] - 2026-02-25

### 🚀 Production Release Candidate

The project is now feature-complete and deployment-ready, fully aligned with all three specification documents.

### Deployment Infrastructure
- `deploy.sh` — One-command deployment script (install/upgrade/migrate/restart)
- `deployment/musicbot.service` — Production-grade systemd service template with security hardening, file descriptor limits, and auto-restart
- `config.env.example` — Comprehensive environment variable reference with all 35+ settings documented
- `requirements.txt` — Synchronized with all runtime dependencies (kurigram, pytgcalls, psutil, jdatetime, cryptography, etc.)

### Feature Summary (since v1.0)
- **Core**: Playback (audio/video/TV/radio/satellite), playlist queue, search, downloads
- **Credit System**: Charge/deduct/trial, daily deduction cron, auto-leave on zero credit
- **Role Hierarchy**: Developer > Owner > Sudo > PlayerOwner > MusicAdmin > VideoAdmin > VIP > Regular
- **Admin Panels**: Developer, Owner, Sudo, Group panels with full inline keyboard UI
- **Forced Membership**: Add/remove/verify targets, user gate with join buttons, Redis caching
- **Broadcast System V2**: Send/forward modes, entity preservation, progress tracking, cancellation
- **Install Policy**: Free/paid/hybrid modes, whitelist exceptions, sudo wallet integration
- **Channel Security**: Call security toggle, VIP bypass, admin-only playback enforcement
- **Analytics & Reporting**: Hourly aggregation, Redis counters, 4 report types, 3 drilldowns
- **Helper Pool Management**: CLI (17 commands), in-bot panel, join/leave/bind, quarantine, key rotation
- **Help Center**: Role-aware content, 13 sections, inline keyboard navigation
- **Observability**: Structured logging, health checks, watchdog, Jalali timestamps

### Known Pre-existing Test Failures
- `test_developer_id_from_config` — hardcoded DEVELOPER_ID in test vs real config.env
- `test_distributed_lock_acquire_release` — intermittent Redis state from test pollution

## [1.5.0] - 2026-02-25

### Added — Final Gap Closure (Batch 2 + 3)
- **GAP-M3**: VIP list with inline demote buttons — group panel VIP list now shows each VIP with a ❌ Remove button; demoting refreshes the list in-place
- **GAP-M6**: Channel admin count install limit — enforces `MAX_CHANNEL_ADMINS` during channel installation; new i18n key `install.admin_limit`
- **GAP-L1**: credit_history partition — Alembic migration 0007 creates `credit_history_partitioned` table with monthly range partitions (Jan–May 2026 + default)
- **GAP-L3**: Jalali timestamps in all charge notifications — `jdatetime` integrated into `NotificationService`; install, credit_charge, and uninstall notifications now include `{timestamp}` in Shamsi format
- New CB constant: `GRP_VIP_DEMOTE_PREFIX`
- 9 new tests in `tests/test_gap_batch2_3.py`

## [1.4.0] - 2026-02-25

### Added — HIGH-Priority Gap Closure (Spec §§10,12,13,20,25)
- **GAP-H1**: Server metrics (CPU/RAM/disk/uptime) in dev status page via `psutil` (non-blocking `run_in_executor`)
- **GAP-H2**: Invoice history per chat_id with Jalali dates (`jdatetime`), new `DEV_INVOICE_HISTORY` callback
- **GAP-H3**: VIP promote/demote commands (`ترفیع ویژه`/`promotevip`, `عزل ویژه`/`demotevip`) + `is_vip`, `promote_vip`, `demote_vip` repo methods
- **GAP-H4**: Auto-leave now sends DM to responsible sudo with chat name/ID + renewal prompt; handles `UserIsBlocked` gracefully
- **GAP-H5**: Call security enforcement in `_check_prerequisites`: when `security_call_enabled`, only admin/VIP/owner/sudo can use playback
- 10 new tests in `tests/test_gap_batch1_h.py`

### Changed
- `status.bot_info` i18n template now includes 🖥 Server section (CPU, RAM, Disk, Uptime) in both fa + en

## [1.3.1] - 2026-02-25

### Added — Gap Closure Batch 1 (Spec Compliance)
- **GAP-1**: `auto_leave_check()` in `credit_service.py` — implements spec §25: checks `auto_leave_enabled`, leaves voice call, sends notification when credit hits zero
- **GAP-2**: `midnight_credit_deduct` now collects expired chat IDs into Redis set `credit:expired_pending_leave` and processes auto-leave after deduction
- **GAP-7**: Developer panel keyboard now includes Helper Management, Analytics, and Help Center entry buttons

### Verified as Already Done
- **GAP-3**: `charge_with_wallet` already checks wallet balance (lines 131-136)

### Changed
- `SPEC_COMPLIANCE_AUDIT.md` updated with Gap Closure Batch 1 results
- 7 new tests in `tests/test_gap_closure_batch1.py`

## [1.3.0] - 2026-02-25

### Added — Help Center
- **In-bot Help Center** (`app/handlers/help_center.py`):
  - `/help` command + Persian aliases (`راهنما`, `کمک`, `help`)
  - Role-aware content: Regular sees 9 sections, Developer sees all 13
  - 13 help sections: Getting Started, Playback, Controls, Playlist, Radio/TV/Satellite, Downloads, Group Panel, Sudo Panel, Owner Panel, Dev Panel, Force Join, Troubleshooting, About
  - Inline keyboard navigation with Back/Home on every screen
  - "Open in Private" button in group chats
  - Access-denied screen for unauthorized admin section access
- **17 CB constants**: HELP_HOME through HELP_OPEN_PRIVATE
- **Keyboard builders**: `help_home(role, is_group)`, `help_section_back()`
- **i18n keys**: 30 keys under `help.*` (both fa.json + en.json with parity)
- **9 tests**: CB constants, keyboard structure, role detection, access gating, i18n parity, no-hardcoded-text lint

## [1.2.1] - 2026-02-25

### Added
- **CLI commands**: show, add-batch, delete (with --force), import-session, export-session (with --i-know-what-im-doing), quarantine, unquarantine, list-events, purge-events — all output JSON for scripting
- **Pre-stream join check**: `_ensure_helper_in_chat()` in `call_service.py` ensures assigned helper is joined before streaming, with retry + quarantine on failure
- **FEATURE_VERIFICATION_REPORT.md**: evidence-based verification of all helper features with handler paths, CB constants, test mappings

### Changed
- All existing CLI commands now output JSON summaries and write audit events to `helper_events`
- `delete` command refuses if helper has active bindings unless `--force` is passed
- `set-capacity` validates range 1–1000

### Fixed
- Hardcoded "Not found" string in helper_panel.py replaced with i18n key
- Missing `HLP_ROTATE_KEY` callback handler (caught by ui_audit test)

## [1.2.0] - 2026-02-25

### Added — Multi-Helper Account Management
- **DB Migration 0006**: Extends `helper_accounts` (tg_user_id, display_name, username, quarantine_count), extends `helper_chat_bindings` (bound_by, binding_state, last_error), creates `helper_events` audit log table
- **Service layer**: `join_chat_as_helper()`, `leave_chat_as_helper()`, `ensure_helper_joined()` in HelperPoolService with Redis locks, FloodWait handling, idempotency keys
- **Quarantine improvement**: `quarantine_count` auto-incremented on each quarantine
- **Helper event repo**: `app/repositories/helper_event_repo.py` — append-only audit log for all helper operations
- **In-bot admin panel**: `app/handlers/helper_panel.py` — Developer-only panel with:
  - Helper Home (summary counts), List Helpers (paginated), Helper Detail (enable/disable/quarantine/unquarantine), Health Check, Rotate Key, Stats
  - Private-only guard on all callbacks (i18n warning + URL button to open PM)
  - Back/Home navigation on every screen
- **CB constants**: HLP_HOME, HLP_LIST, HLP_ADD, HLP_IMPORT_SESSION, HLP_ROTATE_KEY, HLP_HEALTH_CHECK, HLP_STATS, HLP_DETAIL_PREFIX, HLP_ENABLE, HLP_DISABLE, HLP_QUARANTINE, HLP_UNQUARANTINE
- **Keyboard builders**: helper_home(), helper_detail()
- **i18n keys**: 27 keys under `admin.helpers.*` (both fa.json + en.json with parity)
- **Tests**: 13 new tests (encryption roundtrip, key rotation fallback, CLI parser, model columns, service methods, CB constants, private-only guard, i18n parity, audit event write)

## [1.1.1] - 2026-02-25

### Known Pre-existing Test Failures (not introduced by this PR)
- `tests/integration/test_permissions_matrix.py::test_developer_id_from_config` — hardcoded `DEVELOPER_ID=123456789` in test conflicts with real credentials in `config.env` (see AGENTS.md)
- `tests/test_cache.py::test_distributed_lock_acquire_release` — intermittent Redis state issue from prior test pollution

### Added — Gap Closure Sprint
- **G1 Instrumentation**: Wired `track_event()` into production flows:
  - `playback.py`: play_audio, play_video, stop, pause, resume + error tracking
  - `install.py`: install.created.{group|channel}, install.failed, credit.trial_activated
  - `credit_service.py`: credit.charge, credit.deduct
  - `broadcast_service_v2.py`: broadcast.created.{send|forward}, broadcast.sent, broadcast.failed
  - `analytics_service.py`: added `track_error()` helper
- **G2 Private-only guard**: All analytics callbacks detect group/channel context and show i18n warning + URL button to open private chat
- **G3 Navigation**: All analytics screens have Back (to AN_HOME) + Home navigation. Drilldowns return to analytics home
- **G4 Tests**: 8 new tests covering private guard, instrumentation wiring, track_error helper, and i18n parity
- i18n: added `open_private_btn` key (both fa + en)

## [1.1.0] - 2026-02-25

### Added
- **Analytics & Reporting system** implementing `docs/spec_analytics_reporting.md`:
  - `analytics_hourly` table with composite unique constraint and 3 indexes (migration 0005)
  - `AnalyticsHourly` SQLAlchemy model
  - `analytics_service.py`: `track_event()` (Redis pipeline INCRBY), `flush_analytics()` (GETDEL + Postgres UPSERT), report generators with caching
  - `analytics_panel.py` handler: Yesterday, Last 7/14/30 Days, Peak Hours, Errors, drilldowns (by feature/chat_type/role), Sudo self-stats
  - Keyboard builders: `analytics_home`, `analytics_report`, `analytics_drilldown_back`
  - 30 i18n keys in both `fa.json` and `en.json` under `admin.analytics.*`
  - CB constants: `AN_HOME`, `AN_YESTERDAY`, `AN_7DAYS`, `AN_14DAYS`, `AN_30DAYS`, `AN_PEAK`, `AN_ERRORS`, `AN_BY_FEATURE`, `AN_BY_CHAT_TYPE`, `AN_BY_ROLE`, `SUDO_MY_STATS`
  - APScheduler flush job running every 5 minutes with distributed lock
  - Event tracking wired into `/start` and forced membership gate
  - 9 automated tests (unit + integration + i18n parity)

## [1.0.9] - 2026-02-25

### Fixed
- Callback buttons on developer and owner panels no longer show "query too old" / timeout errors — all conversation-based handlers now acknowledge the callback immediately before starting the conversation flow
- `/start` now cancels any pending conversation listeners to prevent duplicate messages and stale listener interference
- `_ask()` helper now cancels stale pyromod listeners before registering a new one to prevent listener accumulation
- Fixed spurious "زمان پاسخ‌دهی به پایان رسید" messages: `ListenerStopped` is now caught separately from real timeouts — cancelled listeners exit silently instead of sending a timeout message
- Fixed pyromod/kurigram event loop incompatibility: pyromod's `async_to_sync` wrapping made handler callbacks appear non-async to kurigram's dispatcher, forcing execution in a thread pool with a different event loop (`RuntimeError: Future attached to a different loop`). Added unwrapping in `app/handlers/__init__.py` to restore async identity on all pyromod-patched handler methods
- Applied consistent `ListenerStopped` handling and `stop_listening` cleanup to all `_ask()` helpers across `broadcast.py` and `safe_ask.py`

## [Unreleased]

### Security
- Fixed callback spoofing vulnerability: `NAV_BACK` and `WZ_HOME` callbacks now strictly enforce `can_open_group_panel`, preventing VIPs and standard users from rendering the full group/player settings panel.
- Fixed installer privilege escalation: Removed `is_installer` bypass from Call Security panel gates (`_can_open_panel`, `can_manage`, `can_toggle_owner_access`). Call Security now strictly requires Deputy-or-above (or Sudo/Developer) status, preventing standard users who added the bot from managing sensitive security options.

## [1.0.8] - 2026-02-25

### Changed
- **Complete UI text redesign (fa.json + en.json)** — professional, high-end style across all 423 keys:
  - Box-drawing headers: `┈┅┅━━| title |━━┅┅┈` for all panels, reports, and banners
  - Consistent bullet style: `⊹` for data fields, `◂` for prompts, `◆` for confirmations
  - Elegant section separators: `─━━━━━━━━━━━━━━━━━━━━━━━━━─`
  - Respectful tone: "کاربر گرامی", "مدیر گرامی", "همکار گرامی"
  - All ask() prompts now include `/cancel` instructions
  - Status page massively expanded with sections (Network, Team, Services)
  - Now-playing banner with elegant frame
  - All error messages rewritten to be friendly and actionable
  - Credit warnings include urgency messaging
  - Persian + English texts maintain perfect parity (423 keys each)

## [1.0.7] - 2026-02-25

### Changed
- **Keyboards centralized**: Group management/help/support inline keyboards moved from inline construction in `group_panel.py` to `KeyboardFactory` methods (`group_management_menu`, `group_help_menu`, `group_support_menu`) — all keyboards now consistently built in `ui.py`
- **Context-aware nav:back**: Back button now checks GROUP context FIRST (goes to group panel), then PM role detection — fixes issue where a developer pressing Back in a group would see the dev panel instead of group panel
- **Group panel context filters**: All group panel sub-handlers now use `group_chat_filter()` — help/support sections restricted to groups only
- **Confirm dialog handlers**: Added `CONFIRM_YES` and `CONFIRM_NO` callback handlers — closes the gap where confirm buttons had no handlers
- **Cancel support in ask()**: `_ask()` recognizes `/cancel`, `cancel`, and `لغو` — sends `common.cancelled` message
- **Timeout feedback**: `_ask()` sends `ask.timeout` on conversation timeout instead of silent failure
- **Context separation enforced across all panels**: dev=private, owner=private, sudo=private, group=group, playback=group, start=private

### Files changed
- `app/utils/ui.py` — 3 new KeyboardFactory methods for group submenus
- `app/handlers/group_panel.py` — use KeyboardFactory, add group_chat_filter to all handlers
- `app/handlers/callbacks.py` — context-aware nav:back, confirm handlers, group/PM filters
- `app/handlers/dev_panel.py` — private_chat_filter, cancel/timeout
- `app/handlers/owner_panel.py` — private_chat_filter, cancel/timeout
- `app/handlers/sudo_panel.py` — private_chat_filter

## [1.0.5] - 2026-02-25

### Added
- Sudo link management handlers: set (`dev:sudo:link:set`), remove (`dev:sudo:link:rm`), list (`dev:sudo:link:list`) in `dev_panel.py`
- Owner texts/links handler (`own:texts_links`) in `owner_panel.py` — same 12 fields as dev panel
- Owner sales report handler (`own:sales_report`) with total aggregation in `owner_panel.py`
- Start menu "About" button (`start:about`) + handler in `callbacks.py`
- Favorites persistence: `pb_fav_add` writes to `favorites` table, `pb_fav_play` queries and displays
- Repeat toggle real state: `CallService.get/set_repeat_state()`, honored by `play_next` (replays current track)
- Post-install panel: `KeyboardFactory.post_install_panel()` with credit display, charged-by, increase/decrease, panel, help, guide buttons
- Post-install panel handlers: `POST_INSTALL_PANEL`, `POST_INSTALL_HELP`, `POST_INSTALL_INC_CREDIT`, `POST_INSTALL_DEC_CREDIT`
- New CB constants: `START_ABOUT`, `OWN_TEXTS_LINKS`, `POST_INSTALL_*`, `PB_FAV_LIST`, `PB_FAV_RM`, `CONFIRM_YES/NO`
- 26 new i18n keys in both `fa.json` and `en.json` (423 keys total, full parity)
- `tests/test_ui_audit.py`: 32 behavioral tests — CB coverage, Back/Home presence, pagination, repeat toggle, favorites DB, sudo links DB, post-install panel rendering, i18n key verification

### Changed
- `install.py::_do_install`: shows post-install panel keyboard when installer is owner/sudo
- `callbacks.py`: removed old stub handlers for `pb_fav_add`, `pb_fav_play`, `pb_repeat`; replaced with real DB/state implementations
- `call_service.py::play_next`: now checks repeat state before advancing queue

### Files changed
- `app/handlers/dev_panel.py` — sudo link handlers
- `app/handlers/owner_panel.py` — texts/links + sales report
- `app/handlers/callbacks.py` — about, favorites, repeat, post-install
- `app/handlers/install.py` — post-install panel
- `app/services/call_service.py` — repeat state
- `app/utils/ui.py` — new CB constants, keyboards
- `app/resources/strings/fa.json` — new keys
- `app/resources/strings/en.json` — new keys
- `tests/test_ui_audit.py` — new
- `FEATURE_VERIFICATION_REPORT.md` — refreshed
- `CHANGELOG.md` — this entry

### Docs
- Refreshed `FEATURE_VERIFICATION_REPORT.md`: PRESENT=152, PARTIAL=3, MISSING=1 (was 126/18/12)

### How to run tests
```bash
DATABASE_URL=postgresql+asyncpg://musicbot:devpassword@localhost/musicbot_dev \
REDIS_URL=redis://localhost:6379/0 \
BOT_TOKEN=test API_ID=12345 API_HASH=testhash DEVELOPER_ID=123456789 \
pytest tests/ -v
```

## [1.0.4] - 2026-02-25

### Docs
- Added `docs/UX_FLOWS_EN.md` — complete UX flow documentation for all bot features
  - Covers: Developer Panel (52 flows), Owner Panel (40 flows), Sudo Panel (6 flows), Group Panel (30+ flows), Start Menu (12 flows), Playback (12 flows), Trial/Install (2 flows)
  - Includes: global navigation rules, context separation (PM/Group/Channel), pagination rules, error handling, confirmation steps
  - Includes: flow map showing full screen-to-screen navigation tree
  - Flags 10 implementation gaps as MISSING/PARTIAL IN CODE with spec references

## [1.0.3] - 2026-02-25

### Added
- GAP 1: Install policy enforcement in `install.py::_do_install` — calls `compute_install_cost()`, rejects unpayable installs, deducts from sudo wallet atomically
- GAP 1: `InstallPolicyService.deduct_install_fee()` and `determine_installer_role()` methods
- GAP 1: Redis caching for `install_policy_settings` (60s TTL)
- GAP 3: Sudo DM on credit warning/expired via `NotificationService._dm_responsible_sudo()`
- GAP 3: `log_repo.get_install_logs_for_chat()` for sudo lookup
- GAP 4: `OWN_SALES_REPORT` CB constant and button in owner panel keyboard
- GAP 5: `app/utils/safe_ask.py` — pyromod fallback helper with manual wait-for-message
- GAP 6: `app/assets/satellite_channels.json` with schema (id, name, url, group)
- GAP 6: Satellite TV handler (PB_SAT) with pagination in `tv_radio.py`
- GAP 6: `KeyboardFactory.satellite_channels_menu()` with pagination support
- GAP 7: Alembic migration `0003_credit_history_partition_prep` — partitions empty table or logs notice
- GAP 8: `DEV_SUDO_LINK_SET`, `DEV_SUDO_LINK_RM`, `DEV_SUDO_LINK_LIST` CB constants
- GAP 9: `app/repositories/channel_repo.py` — `upsert_channel()`, `get_channel()`, `deactivate_channel()`
- GAP 9: Install flow now calls `upsert_channel` for channels, `upsert_group` for groups
- GAP 10: `app/services/pytgcalls_recycle.py` — `RecycleManager` with idle-only recycle logic
- 25+ new i18n keys in both `fa.json` and `en.json` (key parity maintained: 397 keys each)
- `tests/test_gap_closures.py` — 41 behavioral tests covering all 10 gaps

### Changed
- GAP 2: `scheduler.py` line 187 — hardcoded health degraded string replaced with `t("fa", "notifications.health_degraded")`
- GAP 2: `scheduler.py` lines 301-302 — hardcoded Persian wallet alert replaced with `t("fa", "wallet.low_balance_alert")`

### How to run tests
```bash
DATABASE_URL=postgresql+asyncpg://musicbot:devpassword@localhost/musicbot_dev \
REDIS_URL=redis://localhost:6379/0 \
BOT_TOKEN=test API_ID=12345 API_HASH=testhash DEVELOPER_ID=123456789 \
pytest tests/ -v
```

### Files changed
- `app/scheduler.py`
- `app/handlers/install.py`
- `app/handlers/tv_radio.py`
- `app/services/install_policy_service.py`
- `app/services/notification_service.py`
- `app/services/pytgcalls_recycle.py` (new)
- `app/repositories/channel_repo.py` (new)
- `app/repositories/log_repo.py`
- `app/utils/ui.py`
- `app/utils/safe_ask.py` (new)
- `app/assets/satellite_channels.json` (new)
- `app/database/migrations/versions/0003_credit_history_partition_prep.py` (new)
- `app/resources/strings/fa.json`
- `app/resources/strings/en.json`
- `tests/test_gap_closures.py` (new)

## [1.0.2] - 2026-02-25

### Changed
- SPEC_COMPLIANCE_AUDIT.md: strict re-verification with evidence; downgraded 2 false DONE claims to PARTIAL; identified 2 UI contract violations in scheduler.py; added test quality audit and risk map

### Fixed
- Audit accuracy: install policy enforcement correctly marked PARTIAL (not wired in install flow); scheduler hardcoded text flagged as UI violations

## [1.0.1] - 2026-02-25

### Added
- Organized test directory structure: `tests/fakes/`, `tests/unit/services/`, `tests/handlers/panels/`, `tests/handlers/playback/`, `tests/handlers/moderation/`, `tests/integration/`
- `tests/fakes/pyrogram_objects.py` — FakeUser, FakeChat, FakeMessage, FakeCallbackQuery, FakeClient for handler testing
- `tests/factories.py` — DB factory functions for all major models
- `tests/integration/test_permissions_matrix.py` — role hierarchy verification (developer, owner, sudo, admin, regular)
- `tests/integration/test_install_flow_trial_credit.py` — end-to-end install → trial → charge → deduct → expire
- `tests/handlers/panels/test_developer_panel.py` — 35+ CB constant verification, keyboard rendering, i18n key coverage
- `tests/handlers/panels/test_group_panel.py` — 14 setting toggles, default_media_type, help content keys, support keys
- `tests/handlers/playback/test_playback_controls.py` — all command lists, dedication parsing, i18n keys, CB constants, now-playing keyboard
- `tests/handlers/moderation/test_force_join_blacklist_filter.py` — force-join gate, blacklist enforcement, filter words auto-delete
- `tests/unit/services/test_credit_atomicity.py` — lock contract verification, lock failure handling, credit i18n keys

### Fixed
- Test infrastructure: conftest.py now patches the app-level engine singleton to use `NullPool` at import time, eliminating asyncpg event-loop contamination when running the full test suite
- conftest.py `db_engine` fixture uses NullPool (was using default pool, causing stale connections)
- Removed `drop_all` from db_engine teardown (was destroying shared dev database tables)

### Migration Notes
- No app code changes; test infrastructure only

## [1.0.0] - 2026-02-25

Initial production release. Complete rewrite from monolithic SQLite codebase to a modular, horizontally-scalable SaaS architecture.

### Architecture
- **31+ database tables** — PostgreSQL 16 with SQLAlchemy 2.0 async ORM
- **Redis 7** — caching, distributed locks (with Postgres advisory lock fallback)
- **Alembic** — versioned schema migrations (2 migrations: initial + spec indexes)
- **Multi-instance safe** — all critical operations protected by distributed locks
- **Modular layers** — handlers → services → repositories → database

### Features
- Audio, video, TV, satellite, and radio streaming via PyTgCalls + yt-dlp + FFmpeg
- Full playlist/queue management with distributed lock protection
- Track dedication (`پخش @user song`), replay-on-reply (`پخش ریپلای`)
- Per-group/channel credit subscription with daily deduction cron
- 3-day free trial with configurable policy (free/paid/hybrid)
- 4-tier role hierarchy: Developer → Owner → Sudo → Group Admin
- Developer, Owner, Sudo, and Group admin panels with 150+ inline keyboard actions
- Sudo wallet system with atomic charge-with-wallet flow
- Multi-helper account pool with Fernet-encrypted sessions, load balancing, and auto-quarantine
- Broadcast system (send/forward to groups, channels, users)
- Force-join membership gate (global enforcement on all group commands)
- Filter words auto-delete (global group text watcher)
- Blacklist enforcement (global, silently blocks all handlers)
- Install policy layer with free-install whitelist
- 10 scheduled cron jobs (credit deduction, health checks, backups, watchdog)
- Startup state recovery with staggered FloodWait-safe re-joins
- Seek state persistence (Redis 5s / DB 30s / immediate on stop)
- Streaming watchdog (orphan calls, zombie FFmpeg, stale states, helper restore)
- Auto-quarantine on FloodWait/PeerFlood/UserRestricted in playback pipeline
- Safe shutdown with seek position persistence for recovery
- i18n system (TextService + KeyboardFactory) — 330+ keys in fa.json + en.json
- Zero hardcoded user-visible text (enforced by regression tests)
- 150+ stable callback_data constants (never localized)
- SQLite → PostgreSQL migration script with --dry-run
- CLI tool for helper pool management (add/list/disable/enable/test/rotate)
- Docker Compose deployment (Postgres + Redis + bot)
- systemd service file template
- deploy.sh upgrade script (pull → deps → migrate → restart)

### Testing
- **110 automated tests** covering:
  - Credit service (charge, deduct, trial, wallet enforcement)
  - i18n (loading, keys, placeholders, fallback)
  - DB models (all table types)
  - Redis cache and distributed locks
  - Concurrency stress tests (10-way credit, playlist races, singleton cron)
  - Postgres advisory lock fallback
  - UI contract enforcement (no hardcoded text, key parity, CB stability)
  - Panel coverage (all 4 panels, all buttons, all i18n keys)
  - Streaming reliability (watchdog, quarantine, FFmpeg cleanup)
  - Utilities (helpers, parsers, date formatting)

### Migration from v1 (SQLite)
```bash
python -m app.database.migrate_sqlite_to_pg \
    --sqlite /path/to/old/database.sqlite \
    --pg "postgresql://user:pass@host/musicbot_db"
```

### Fresh Install
```bash
cp app/config.env.example app/config.env
# Edit: BOT_TOKEN, API_ID, API_HASH, DEVELOPER_ID
docker compose up -d --build
```

### Config Changes from Legacy
- `HELPER_PHONE` replaced by multi-helper pool (use CLI to add helpers)
- `HELPER_SESSION_KEY_CURRENT` required for session encryption (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- `DATABASE_URL` now uses `postgresql+asyncpg://` (was SQLite)
- `REDIS_URL` required (was not used in v1)
- All UI text moved from `config.env` to `resources/strings/fa.json`
