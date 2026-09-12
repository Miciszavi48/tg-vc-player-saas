# i18n Architecture

> Last verified against repository: 2026-07-19

> Back to [../index.md](../index.md) | Related: [../features/i18n.md](../features/i18n.md) | [../../app/resources/i18n/README.md](../../app/resources/i18n/README.md)

## Overview

The bot uses a **split-only** i18n loader. All localized strings live under
`app/resources/i18n/` as manifest-driven JSON fragments. **There is no legacy
monolith fallback.** The files `app/resources/strings/fa.json` and
`app/resources/strings/en.json` were permanently removed (2026-06-11).

## Fragment Layout

```
app/resources/i18n/
├── manifest.json           ← fragment registry (version, languages, fragments array)
├── fa/                     ← Persian fragments
│   ├── common.json         ← meta, common, status_indicator, list_fmt
│   ├── labels.json         ← labels enum mappings
│   ├── ask.json            ← ask flow strings
│   ├── panels/
│   │   ├── developer.json  ← panels.developer
│   │   ├── owner.json      ← panels.owner
│   │   ├── sudo.json       ← panels.sudo
│   │   └── group.json      ← panels.group
│   ├── features/
│   │   ├── start.json      ← start
│   │   ├── playback.json   ← playback, now_playing
│   │   ├── playback_commands.json  ← playback_cmd, playlist_cmd, tv_radio, search, download_cmd
│   │   ├── call_security.json      ← call_security
│   │   ├── helper.json     ← helper
│   │   ├── broadcast.json  ← broadcast, promotion
│   │   ├── force_join.json ← force_join_mgmt, fm
│   │   ├── text_links.json ← texts_links, log_channel, free_mode
│   │   ├── install.json    ← install, credit, wallet
│   │   ├── group_mgmt.json ← sudo_mgmt, sudo_permissions, owner_mgmt, filter_mgmt, blacklist_mgmt, global_ban, admin_titles, sudo_enforce
│   │   └── favorites.json  ← favorites
│   └── system/
│       ├── admin.json      ← admin, gc
│       ├── notifications.json ← notifications
│       ├── status.json     ← status, status_summary
│       ├── media_health.json ← media_health
│       ├── bot_update.json ← bot_update, recovery
│       ├── reports.json    ← reports
│       ├── errors.json     ← errors
│       └── help.json       ← help, help_content
└── en/                     ← English (exact same structure as fa/)
```

## manifest.json Fields

| Field | Description |
|-------|-------------|
| `version` | Schema version (`"1.0"`) |
| `languages` | Supported language codes (`["fa", "en"]`) |
| `deprecated_keys` | Keys scheduled for removal (currently empty) |
| `fragments` | Array of `{path, namespaces}` entries |

Each fragment `path` is relative to the language directory (e.g. `panels/group.json`).
`namespaces` lists the top-level keys stored in that file.

## Runtime Loader (`app/utils/i18n.py` — `TextService`)

1. Reads `manifest.json` → fatal `I18nResourceError` if missing or invalid.
2. For each fragment listed: loads `{lang}/{path}` → fatal if missing.
3. Deep-merges fragments in manifest order → fatal on duplicate leaf paths.
4. Caches merged tree per language.

**No fallback to legacy files.** Missing fragment = bot won't start.

### Public API

```python
from app.utils.i18n import texts, t, label, AUTO_LANG

texts.t("fa", "common.buttons.back")          # → "بازگشت"
texts.t("fa", "start.welcome", mention="Ali") # → formatted with kwargs
texts.t("fa", "missing.key")                  # → "[missing:missing.key]"
texts.t("fa", "common.buttons")               # → "[invalid:common.buttons]" (not a leaf)

t("fa", "start.welcome")                      # module-level shortcut
label("fa", "broadcast_mode", "groups")       # enum label or raw value
```

### Auto-Language (`AUTO_LANG`)

`AUTO_LANG` is a sentinel that resolves via `ContextVar` to the current request language.
Set in handler entry points; consumed by `t(AUTO_LANG, key)`.

```python
from app.utils.i18n import AUTO_LANG, set_current_lang, reset_current_lang

token = set_current_lang("en")
# handler code uses t(AUTO_LANG, key) → resolves to "en"
reset_current_lang(token)
```

## Toggle Labels and `status_indicator`

Toggle button labels use `toggle_label(lang, base_key, enabled)` in `app/utils/ui.py`:

- `status_indicator.active` = `✅`
- `status_indicator.inactive` = `☑️`

The suffix is read from fragments. No bracket-style `[فعال]/[غیرفعال]` in current code.

> **Note on Call Security toggles:** `build_panel_keyboard` in `call_security_service.py`
> uses 🟢/🔴 suffix (not ✅/☑️) for that panel's specific toggle style. The
> `status_indicator` fragment drives dev/owner/sudo/group settings toggles.

## Validation Scripts

Both are **read-only** — no file writes, no DB, no bot.

```bash
# Structure/parity validation
python scripts/split_i18n_resources.py --check
python scripts/split_i18n_resources.py --check --verbose

# Code/resource key coverage audit (AST scan)
python scripts/check_i18n_usage.py
python scripts/check_i18n_usage.py --verbose
```

### `split_i18n_resources.py --check` checks:
- Valid JSON for manifest and all fragments
- All manifest fragments exist for each language
- No duplicate leaf paths across fragments
- FA/EN leaf-path parity
- All leaves are strings

Exit `0` only when all checks pass.

### `check_i18n_usage.py` checks:
- Every literal `t(lang, "dot.path")` call in `app/**/*.py` resolves in FA+EN
- Every literal `label(lang, group, value)` resolves
- Dynamic f-string key families registered in `DYNAMIC_ALLOWLIST`
- Unregistered dynamic patterns → exit `1`

Current status: **856 literal keys, 19 dynamic families, 0 missing keys.**
Enforced via `tests/test_i18n_usage_coverage.py` (22 tests).

## Adding / Editing Strings

1. Edit the relevant fragment under `app/resources/i18n/fa/` **and** `en/`.
2. Maintain FA/EN leaf-path parity.
3. Run `python scripts/split_i18n_resources.py --check`.
4. If the key is a dynamic f-string family: register it in `DYNAMIC_ALLOWLIST`
   in `scripts/check_i18n_usage.py` and run `python scripts/check_i18n_usage.py`.

> Do **not** add strings to a removed `app/resources/strings/` monolith.
> Do **not** delete `app/resources/i18n/` — there is no fallback.

## Failure Behavior

| Failure | Effect |
|---------|--------|
| Missing/invalid `manifest.json` | `I18nResourceError` at startup (fatal) |
| Missing language dir or fragment | `I18nResourceError` at startup (fatal) |
| Duplicate leaf path across fragments | `I18nResourceError` at startup (fatal) |
| Missing key at runtime | `"[missing:key.path]"` returned (non-fatal) |
| Non-string leaf at runtime | `"[invalid:key.path]"` returned (non-fatal) |

## CI Enforcement

| Test file | What it checks |
|-----------|---------------|
| `tests/test_i18n_hybrid_split.py` | Fragment structure, parity, `--check` subprocess |
| `tests/test_i18n_usage_coverage.py` | All literal and dynamic keys resolve in FA+EN |
| `tests/test_i18n.py` | TextService split-only resolution behavior |

## Rollback

Restore `app/resources/i18n/**` from git. **Do not** expect a monolith fallback.
