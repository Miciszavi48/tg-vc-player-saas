# Playback Architecture

> Last verified against repository: 2026-07-19

> Back to [../index.md](../index.md) | Related: [../features/playback.md](../features/playback.md)

## Overview

Playback routes media (audio or video) into a Telegram Voice Chat via PyTgCalls,
using a helper Telegram user account as the streaming endpoint.

## Voice Stack

| Component | Role |
|-----------|------|
| **Kurigram** (custom Pyrogram fork) | Bot API + raw MTProto (helper clients) |
| **PyTgCalls** | Voice chat stream management (join, leave, play, next) |
| **helper accounts** | Telegram user clients that join voice chats and stream |
| `app/utils/voice_stack.py` | Version-compatibility shims (`MediaStream` builders, v2 API) |
| `app/services/call_service.py` | Orchestration: join, play, stop, leave, repeat state |

Diagnostic: `python scripts/check_voice_stack.py`

## Playback Pipeline (end-to-end)

```
User sends "پخش نام آهنگ" or /play or replies to media + "پخش"
        ↓
app/handlers/playback.py   (play_audio / play_slash_command)
        ↓
app/utils/playback_auth.py  authorize_playback_action()
  - credit > 0
  - bot is full admin
  - if security_call_enabled: role must be admin/VIP/sudo+
  - NOTE: download_enabled is NOT checked here (only in download.py)
        ↓
app/utils/playback_commands.py  parse_play_command()
  - NFKC + ZWNJ normalize
  - detect: query text / URL / reply-to-media (audio/voice/video/document)
  - detect: dedication (@user / user_id / برای pattern)
  - detect: /play@bot variants via message.command
        ↓
app/services/media_service.py  download_audio() / download_video()
  - yt-dlp (YouTube, SoundCloud, etc.)
  - direct URL download
  - Telegram file download (from reply)
        ↓
app/services/transcode_pool.py  pre_transcode()
  - bounded concurrency (TRANSCODE_POOL_SIZE semaphore)
  - audio: opus/48k/stereo
  - video: libx264/ultrafast/480p + aac
  - graceful passthrough if transcode fails
        ↓
CallService.join_voice_chat()
  - atomic helper reservation (helper_pool_service)
  - _ensure_helper_in_chat()
  - PyTgCalls play() / MediaStream
  - temp file cleanup on failure
        ↓
NowPlayingRenderer.render()  →  reply_now_playing_with_optional_cover()
  - per-chat flags: show_cover, show_now_playing_text, show_track_id
  - cover art: cover_art_service (reply-media thumb; Telegram-native only)
  - inline controls: pause/resume/stop/vol/next/prev/repeat/fav/speed
```

## Audio vs Video Routing

| Setting | Trigger | Behavior |
|---------|---------|---------|
| `default_media_type = audio` | No explicit type given | Audio playback |
| `default_media_type = video` | No explicit type given | Video playback |
| `پخش ویدیو` / `playvideo` | Explicit | Always video |
| `پخش` + query | Uses `default_media_type` | Per-group default |
| `video_enabled = False` | Any video request | Blocked with user message |

`default_media_type` is stored in `chat_settings.default_media_type` (migration `0020`).
`video_enabled` is `chat_settings.video_enabled`.

## Queue on Busy (`smart_radio_enabled`)

When another track is playing and `smart_radio_enabled` is on:
- HTTP(S) URL sources: queued in `playlists` table.
- Non-URL sources (searches, yt-dlp): returns `playback_cmd.already_playing`.

Managed in `app/services/playback_dispatch_service.py`.

## Repeat State (session-only)

Repeat is **in-memory** per chat (`CallService.get_repeat_state` / `set_repeat_state`).
- Toggled via now-playing button `pb:repeat` → `pb_repeat` callback.
- **Not** persisted to DB. Not a `chat_settings` column.
- `vote_skip_enabled` column exists in DB but is the old mislabeled column for repeat; hidden from UI.

## Now Playing

| Feature | Module |
|---------|--------|
| Text rendering | `app/services/now_playing_renderer.py` |
| Cover art | `app/services/cover_art_service.py` |
| Buttons (controls) | `app/handlers/callbacks.py` (`pb:*` callbacks) |

Cover art uses **Telegram-native** reply photo from reply-media thumbnails only.
No external HTTP fetch for covers (SSRF risk mitigated by design).

## Error Mapping

`app/utils/playback_errors.py` maps join/stream failures to Persian user messages:
- Helper unavailable
- No active voice chat
- PyTgCalls not available
- Stream build failure

## Temp File Cleanup

On playback failure (one-shot, not playlist):
- Temporary download files under `DOWNLOADS_PATH/{chat_id}/` are deleted.
- Playlist and download-command files are **not** deleted on playback failure.
- Stale downloads in nested dirs cleaned by `cleanup_downloads` scheduler job.

## Free Mode Restrictions

Free-install chats (policy: free/open/hybrid/whitelist):
- Audio playback, radio, download: **allowed**.
- Video, TV, satellite: **blocked** until paid credit added.
- Checked via `app/services/media_capability_service.py`.

## Playback Auth Gates

`app/utils/playback_auth.py` — `authorize_playback_action()`:

1. Bot enabled globally (`bot_settings.bot_enabled`).
2. Group credit > 0.
3. Bot is full admin in chat.
4. If `security_call_enabled` (قفل پخش): user must be music admin, VIP, player owner, or sudo+.
5. `download_enabled` is **NOT** checked here — only `download.py` checks it.

## Tests

| Test file | Coverage |
|-----------|---------|
| `tests/test_playback_command_ux_alignment.py` | Command parsing alignment |
| `tests/test_reply_playback_command_ux.py` | Reply + `پخش` routing |
| `tests/test_playback_error_mapping.py` | Error messages |
| `tests/test_play_audio_document_parity.py` | Audio document reply |
| `tests/test_playback_routing_audio_video.py` | Audio/video routing |
| `tests/test_playback_video_call_flow.py` | Video call full flow |
| `tests/test_queue_on_busy_phaseb4.py` | Queue behavior |
| `tests/test_repeat_runtime_lock_phaseb61.py` | In-memory repeat |
| `tests/test_now_playing_cover_phaseb54.py` | Cover art |
| `tests/test_voice_stack_diagnostic.py` | Voice stack compatibility |
