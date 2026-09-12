# Owner Scope & Sudo Permissions

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md)

Developer → Owner → Sudo → Group music/video admin → VIP.

## Owner scope (Phase C)

- `owner_scope_service.py` resolves install lineage per chat.
- Owner panels scoped to their installs; developer sees global.
- Tests: `tests/test_owner_scope_phasec1.py`, `tests/test_owner_sudo_scope_phasec2.py`

## Sudo permissions

- DB: `sudo_permissions` (migration `0014_sudo_permissions`)
- UI: `sudo_permission_ui_service.py`
- Guards: `sudo_permissions.py` — `allow_group_chat_settings_change`, `can_use_sudo_admin_bypass`, etc.
- Denied actions call `notify_sudo_permission_denied` (visible alert, not silent).

## Multi-developer

`DEVELOPER_ID` bootstrap + `tests/test_multi_developer_id.py`

## Related

- [architecture/multi_tenancy.md](../architecture/multi_tenancy.md) (single-bot scope vs future multi-bot audit)
- [features/text_links_and_start.md](text_links_and_start.md) (owner text links)
