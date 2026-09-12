# Playback

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md) | Related: [commands.md](commands.md) | [../architecture/playback_architecture.md](../architecture/playback_architecture.md)

## Persian typed commands (primary UX)

| Pattern | Behavior |
| --- | --- |
| `پخش نام آهنگ` | Search/play by title |
| `پخش لینک` / URL after پخش | Play from link |
| Reply to audio/document + `پخش` | Play replied media |

Slash commands (`/play`, etc.) remain for compatibility. Help text in split i18n: `app/resources/i18n/*/system/help.json` (`help_content.play_commands`) and `features/playback*.json`.

## Playback lock

- **قفل پخش (فقط ادمین‌ها)** — `chat_settings.security_call_enabled` / `grp:set:security_call`.
- Enforced via `playback_auth.py` / `authorize_playback_action`.
- **Not** the same as Call Security ([call_security.md](call_security.md)).

## Voice stack

- **PyTgCalls** + **Kurigram** (custom Pyrogram fork).
- Diagnostic: `python scripts/check_voice_stack.py` (`app/utils/voice_stack.py`).
- Native `tgcalls` wheel may be missing on some hosts; bot starts without voice features if PyTgCalls fails.

## Now playing

- Renderer: `now_playing_renderer.py`
- Cover art (reply playback only): `cover_art_service.py` → `reply_now_playing_with_optional_cover()` when `show_cover` is on — see [now_playing_cover_security.md](now_playing_cover_security.md)
- Controls: `callbacks.py` (`pb:pause`, `pb:resume`, `pb:stop`, volume, next/prev, favorites, **repeat** `pb:repeat`)
- Repeat is **session-only** (in-memory per chat), not a DB toggle.

## Playback commands

- Normalization and reply-command parsing: `app/utils/playback_commands.py`
- Authorization for typed/reply commands: `playback_auth.py`
- Tests: `tests/test_playback_command_ux_alignment.py`, `tests/test_reply_playback_command_ux.py`

## Default media type

Per-chat `default_media_type` (`audio` | `video`) from group settings (migration `0020`). Used when user does not specify type.

## Queue on busy

When another track is playing, new requests may be queued (`smart_radio_enabled` / queue toggle). See `playback_dispatch_service.py` and `tests/test_queue_on_busy_phaseb4.py`.

## Errors

User-facing Persian errors mapped in `playback_errors.py` (helper unavailable, no VC, PyTgCalls down, etc.).

## Tests

- `tests/test_playback_command_ux_alignment.py`
- `tests/test_playback_error_mapping.py`
- `tests/test_play_audio_document_parity.py`
- `tests/test_repeat_runtime_lock_phaseb61.py`
