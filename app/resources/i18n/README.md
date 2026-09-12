# i18n Split Resources

This directory is the **only** source of localization strings for the Telegram
Music Bot. Legacy monolith files under `app/resources/strings/` have been
removed.

## Directory Layout

```
app/resources/i18n/
├── manifest.json          # Master split plan (version, languages, fragments)
├── fa/                    # Persian fragments
│   ├── common.json
│   ├── labels.json
│   ├── ask.json
│   ├── panels/
│   ├── features/
│   └── system/
└── en/                    # English fragments (same structure as fa/)
```

## manifest.json

| Field             | Description                                            |
|-------------------|--------------------------------------------------------|
| `version`         | Schema version of the manifest itself                  |
| `languages`       | List of supported language codes                       |
| `deprecated_keys` | Keys scheduled for removal (currently empty)           |
| `fragments`       | Array of `{path, namespaces}` split entries            |

Each fragment entry:

- `path` — relative path inside the language directory (e.g. `panels/group.json`)
- `namespaces` — source keys included in that file. A dotted namespace like
  `panels.developer` is stored as `{"panels": {"developer": {...}}}`.

## Runtime Loader

`app/utils/i18n.TextService` loads **only** from this directory:

1. Reads `manifest.json` (required; fatal if missing or invalid)
2. Loads every listed fragment for the requested language (fatal if any missing)
3. Deep-merges fragments in manifest order (fatal on duplicate leaf paths)
4. Caches the merged tree per language

There is **no** fallback to legacy monolith JSON files.

Public API (`texts`, `t()`, `label()`, `AUTO_LANG`, `reload()`) is unchanged.

## Adding a New Language

1. Add the language code to `manifest.json` → `languages`.
2. Create `app/resources/i18n/<lang>/` mirroring the `fa/` structure.
3. Populate all fragment files listed in the manifest.
4. Run `python scripts/split_i18n_resources.py --check`.

## Adding or Editing Strings

1. Edit the relevant fragment JSON under `fa/` and `en/`.
2. Keep FA/EN leaf-path parity identical.
3. Run validation:

```bash
python scripts/split_i18n_resources.py --check
python scripts/split_i18n_resources.py --check --verbose
```

The script is **read-only**. It never writes files. Running it without flags
performs the same validation (default behavior).

Checks performed:

- Valid JSON for manifest and every fragment
- All manifest fragments exist for each language
- No duplicate leaf paths across fragments
- FA/EN leaf-path parity
- All leaves are strings

Exit code `0` only when everything passes; any mismatch exits `1`.

## Usage Coverage Audit

`scripts/check_i18n_usage.py` (read-only) verifies that every translation key
used by `app/` code exists here:

```bash
python scripts/check_i18n_usage.py
python scripts/check_i18n_usage.py --verbose
```

- Extracts literal `t(lang, "dot.path")` and `label(lang, "group", "value")`
  keys via AST parsing of all `app/**/*.py` files.
- Dynamic f-string keys (e.g. `t(lang, f"call_security.reason_{reason}")`)
  must be registered in `DYNAMIC_ALLOWLIST` inside the script with their
  enumerated expected keys; unregistered dynamic patterns fail the run.
- Reports variable-key call sites, suspect dotted literals, and unused leaf
  candidates (all informational).
- Exit `0` when all required keys exist in FA and EN; `1` otherwise.

Enforced in CI via `tests/test_i18n_usage_coverage.py`. When adding a new
dynamic key family, register the pattern and its expected keys in
`DYNAMIC_ALLOWLIST`.

## Rollback

Restore this directory from git. Do not delete `i18n/` expecting a monolith
fallback — there is none.
