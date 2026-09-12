# Group Role Commands

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> Back to [index.md](index.md) | Related: [manager_text_commands.md](manager_text_commands.md), [owner_sudo_permissions.md](owner_sudo_permissions.md)

Slash-free group role commands are implemented by the manager text command layer:

- Parser: `app/utils/manager_text_commands.py`
- Handler: `app/handlers/manager_text_commands.py`
- Repository: `app/repositories/manager_command_repo.py`

## Role Storage

No migration is required. Existing role tables are used:

| Product role | Table | Notes |
| --- | --- | --- |
| player owner | `player_owners` | highest per-group player role |
| deputy | `player_vips` | mapped to the existing separate VIP role table; writes invalidate the `admin_repo.is_vip()` Redis cache |
| mod/admin | `music_admins` | music-player manager list |

Deputies are intentionally separate from owners and mods. The implementation does not create a new deputy table.

## Command Matrix

| Family | Persian aliases | English aliases | Scope | Permission | Persistence |
| --- | --- | --- | --- | --- | --- |
| owner add | `ارتقا مالک موزیک`, `افزودن مالک پلیر` | `AddOwnerM`, `SetOwnerMusic` | group | developer, owner, sudo admin bypass, player owner | insert into `player_owners` |
| owner remove | `عزل مالک موزیک`, `حذف مالک پلیر` | `RemOwnerMusic`, `DemOwnerpPlayer`, `DemOwnerPlayer` | group | same as owner add | delete from `player_owners`, except protected roles |
| owner list | `لیست مالک پلیر`, `لیست مالکان موزیک` | `OwnerListM`, `OwnerListMusic`, `OwnerListPlayer` | group | same as owner add | read `player_owners` |
| owner clear | `پاکسازی لیست مالک موزیک`, `پاکسازی لیست مالکان پلیر` | `ClearOwnerListM`, `ClearOwnerListMusic`, `ClearOwnerListPlayer` | group | same as owner add | delete unprotected `player_owners` rows |
| deputy add | `ارتقا معاون موزیک`, `افزودن معاون پلیر` | `AddDeputyM`, `SetDeputyMusic` | group | developer, owner, sudo admin bypass, player owner | insert into `player_vips` |
| deputy remove | `عزل معاون موزیک`, `حذف معاون پلیر` | `RemDeputyMusic`, `DemDeputypPlayer`, `DemDeputyPlayer` | group | same as deputy add | delete from `player_vips`, except protected roles |
| deputy list | `لیست معاون پلیر`, `لیست معاونین موزیک` | `DeputyListM`, `DeputyListMusic`, `DeputyListPlayer` | group | same as deputy add | read `player_vips` |
| deputy clear | `پاکسازی لیست معاون موزیک`, `پاکسازی لیست معاونین پلیر` | `ClearDeputyListM`, `ClearDeputyListMusic`, `ClearDeputyListPlayer` | group | same as deputy add | delete unprotected `player_vips` rows |
| mod add | `ترفیع موزیک`, `ترفیع پلیر`, `ارتقا مقام پلیر` | `PromoteM`, `PromoteMusic`, `PromotePLayer`, `PromotePlayer` | group | developer, owner, sudo admin bypass, player owner, deputy | insert into `music_admins` |
| mod remove | `عزل موزیک`, `عزل مقام پلیر` | `DemoteM`, `DemoteMusic`, `DemotePlayer` | group | same as mod add | delete from `music_admins`, except protected roles |
| mod list | `لیست مدیر موزیک`, `لیست مدیران پلیر` | `ModListM`, `ModListMusic`, `ModListPlayer` | group | same as mod add | read `music_admins` |
| mod clear | `پاکسازی لیست مدیران موزیک` | `ClearModListM`, `ClearModListPlayer` | group | same as mod add | delete unprotected `music_admins` rows |

## Target Resolution

Add/remove commands resolve the target from:

1. reply target,
2. numeric user id,
3. `@username` via `client.get_users`,
4. text mention entities.

If the target cannot be resolved, the bot replies visibly.

## Duration

The current implementation promotes mods permanently. No existing duration syntax was present in the project command layer, so temporary/time-limited mod syntax was not introduced in this change.

## Protections

Developer IDs, global owners, global sudos, and higher per-group roles are protected from accidental removal. Mod clearing preserves users that are also player owners or deputies. Deputy clearing preserves player owners. Owner clearing preserves global protected roles.

Because runtime deputy permission is checked through `admin_repo.is_vip()`, deputy add/remove/clear invalidates the same Redis VIP cache keys used by the runtime helper. Uninstall/leave cleanup also invalidates deleted deputy cache entries before a group can be reinstalled.

## Manual Smoke Checklist

1. Reply to a user with `ارتقا مالک موزیک`.
2. Send `لیست مالکان موزیک`.
3. Send `AddDeputyM USER_ID`.
4. Send `DeputyListMusic`.
5. Send `PromoteM USER_ID`.
6. Send `ModListPlayer`.
7. Remove each role with the matching remove command.
8. Clear each list and verify owners/deputies/mods stay separate in DB.
