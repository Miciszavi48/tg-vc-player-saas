# Text Links & Start UX

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md)

## Private `/start`

- Welcome from developer-global `start_text` (`bot_settings` / texts hub).
- Dynamic inline keyboard: empty links hidden; secondary buttons two-per-row where configured.
- Creator CTA: `developer_pv_link` then `developer_link`.

## Owner overrides (Phase C)

- Table: `owner_text_links` (migration `0022_owner_text_links`).
- Owner-scoped links/texts via owner panel; runtime resolution in `owner_text_link_service.py` and `owner_scope_service.py`.
- Developer-global fields remain authoritative for some keys; see `texts_links_ui.py` field usage matrix.

## Storage-only fields

`texts_links_ui` documents fields that appear in the editor but have **no runtime wiring** (or caption-only media). Tests: `tests/test_text_link_field_usage_alignment.py`.

## Playback help alignment

Start/help content references typed Persian playback patterns (not legacy `/skip` claims). See [playback.md](playback.md).

## Tests

- `tests/test_start_text_and_buttons_alignment.py`
- `tests/test_owner_text_link_panel_phasec32.py`
- `tests/test_owner_text_link_runtime_phasec33.py`
- `tests/test_dev_texts_links_editor.py`
