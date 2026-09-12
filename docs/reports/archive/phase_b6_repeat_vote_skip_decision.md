# Phase B6-0: Repeat vs Vote-Skip Decision

> **Record status (2026-07-19): VALID HISTORICAL SNAPSHOT.** The original date and conclusion are preserved. Current playback behavior is documented in [../features/playback.md](../../features/playback.md).

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../../DATABASE_AND_SCHEMA.md)
> 
> Archived design note. Current behavior: [features/playback.md](../../features/playback.md), [features/group_settings.md](../../features/group_settings.md).

## Problem

`chat_settings.vote_skip_enabled` was exposed in the group settings UI as «تکرار» (`grp:set:repeat`), but **no vote-skip runtime** reads this field. Session repeat uses in-memory `CallService._repeat_states` via `pb:repeat` only.

## Options

- **Option A (chosen for B6-1):** Hide group «repeat» toggle; keep `vote_skip_enabled` storage-only; document `pb:repeat` as the only repeat UX.
- **Option B:** Rename UI to vote-skip and implement vote-skip runtime (deferred).
- **Option C:** Add `repeat_enabled` column and wire DB repeat (deferred; migration `0022` was reserved for owner text links instead).
- **Option D:** Remove `vote_skip_enabled` entirely (breaking; not chosen).

## Current state

- `repeat_enabled` column **does not exist** in `ChatSettings`.
- `vote_skip_enabled` remains in the model for legacy/stale callbacks.
- Group keyboard hides `repeat` via `_GRP_VISIBLE_SETTING_TOGGLES`.
- Playback handlers do **not** read `vote_skip_enabled` (no runtime).
- **No runtime** vote-skip feature ships in this phase.

## B6-1 / B6-2

- B6-1: Session-only repeat lock tests (`tests/test_repeat_runtime_lock_phaseb61.py`).
- B6-2: Deferred DB `repeat_enabled` if product requires persistent repeat mode.
