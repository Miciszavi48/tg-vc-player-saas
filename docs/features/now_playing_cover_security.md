# Now Playing Cover Art (Phase B5-4)

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> **Last updated:** 2026-06-10  
**Status:** **Implemented** — Telegram-native cover only (Option B from the original design)  
**Runtime:** `app/services/cover_art_service.py` + `reply_now_playing_with_optional_cover()` in `app/handlers/playback.py`  
**Schema:** `chat_settings.show_cover` (migration `0021_now_playing_flags`); group toggle **کاور** (`grp:set:show_photo`)

---

## 1. Executive summary (current behavior)

| Question | Answer |
|----------|--------|
| Is cover display implemented? | **Yes** — on **reply-based** playback paths when `show_cover` is enabled |
| What sources are used? | **Telegram-native thumbs only** (`file_id` from replied audio/video/document) |
| External / YouTube thumbnails? | **Not implemented** — still rejected (SSRF and metadata gaps) |
| Search / TV / radio / queue paths? | **Text-only** now-playing (`edit_text`); no cover metadata on those flows |
| Renderer | `now_playing_renderer.py` produces caption text; photo is attached separately by `cover_art_service` |
| User-visible failure mode | **Silent text fallback** — `reply()` with text when no safe thumb or send fails |
| Default `show_cover` | **True** when unset; respects per-chat group setting |

**Security posture (unchanged from design):** Never fetch external thumbnail URLs in B5-4. Any future YouTube/search cover work is a separate **B5-4b** phase with SSRF review.

---

## 1b. Original design audit (historical)

The sections below record the **pre-implementation** security audit (2026-06-05). They remain useful for B5-4b planning but do **not** describe current runtime limits beyond what is stated in §1.

---

## 2. Pre-audit plan table

| File | What we checked | Possible design | Risk | Validation command |
|------|-----------------|-----------------|------|-------------------|
| `app/services/now_playing_renderer.py` | `show_cover` in flags; text-only API | Keep `render_now_playing_text() -> str`; do not embed photo logic | Low | `python -m py_compile app/services/now_playing_renderer.py` |
| `app/handlers/playback.py` | `message.reply(text)`; has `reply_to_message` | B5-4: optional `reply_photo(caption=text)` when TG thumb available | Medium (message type change) | `python -m py_compile app/handlers/playback.py` |
| `app/handlers/search.py` | `query.message.edit_text(text)` after play | **Text-only on edit paths** unless delete+resend approved | High if forced photo edit | `python -m py_compile app/handlers/search.py` |
| `app/handlers/tv_radio.py` | `edit_text` on picker message | Text-only fallback; JSON assets have no images | Low | `python -m py_compile app/handlers/tv_radio.py` |
| `app/handlers/playlist.py` | Generic `playing_list` banner | Out of B5-4 scope; no cover metadata in queue | Low | `python -m py_compile app/handlers/playlist.py` |
| `app/services/media_service.py` | yt-dlp audio/video download only | B5-4b: optional `--write-thumbnail` + validated fetch | **High** (SSRF, disk) | `python -m py_compile app/services/media_service.py` |
| `app/services/media_cache.py` | Audio/video extensions only | Do not store images in media cache | Medium if misused | `python -m py_compile app/services/media_cache.py` |
| `app/services/media_capability_service.py` | `build_now_playing_controls` / `buttons_enabled` | Keyboard independent of cover | Low | `python -m py_compile app/services/media_capability_service.py` |
| `app/utils/media_sources.py` | `validate_safe_url_with_redirects`, trusted paths | Reuse for any future external thumb download | **High** if bypassed | `python -m py_compile app/utils/media_sources.py` |
| `app/utils/ui.py` | `show_photo` visible in group settings | No change; maps to `show_cover` | Low | `python -m py_compile app/utils/ui.py` |
| `app/database/models.py` | `Playlist`, `PlaybackState` | No thumb columns; document limitation for queue auto-advance | Low (product gap) | `python -m py_compile app/database/models.py` |
| `app/resources/i18n/{fa,en}/features/playback.json` | Caption strings via `now_playing.*` | Reuse rendered text as photo caption | Low | `python scripts/split_i18n_resources.py --check` |
| `tests/test_queue_ssrf_validation.py` | SSRF baseline | Extend for thumb URL parity in B5-4b | Low | `pytest tests/test_queue_ssrf_validation.py -q` |
| `tests/test_now_playing_metadata_flags_phaseb53.py` | `show_cover` no fetch | Keep regression until B5-4 lands | Low | `pytest tests/test_now_playing_metadata_flags_phaseb53.py -q` |
| `tests/test_now_playing_cover_design_phaseb54.py` | Design contracts (new) | Static design assertions only | None | `pytest tests/test_now_playing_cover_design_phaseb54.py -q` |

---

## 3. Current cover data availability (Part A)

### 3.1 Which playback sources expose thumbnail/cover metadata?

| Source | Cover available today? | Where / notes |
|--------|------------------------|---------------|
| **Telegram audio** (reply) | **Yes (in-message, unused)** | Pyrogram `Message.audio` may expose `thumb` (and related thumb metadata). Handler downloads audio via `download_trusted_telegram_media` but never reads thumb. |
| **Telegram video** (reply) | **Yes (in-message, unused)** | `Message.video.thumb` typically present. `play_video` / replay paths use `title_from_telegram_reply` only. |
| **Telegram document** (audio/video mime) | **Sometimes (in-message, unused)** | `Message.document.thumb` for some uploads. Parity path in `play_audio` for `audio/*` documents. |
| **YouTube / yt-dlp URL** | **Not in runtime** | `MediaService` runs yt-dlp for stream/download only; no `--write-thumbnail` or JSON thumb field. yt-dlp *can* provide `thumbnail` URL in JSON — **not collected**. |
| **Search result play** | **No** | `_yt_search` stores `id`, `title`, `duration`, `url` only. Callback carries `video_id` only. |
| **TV / radio / satellite JSON** | **No** | `app/assets/tv_channels.json`, `radio_stations.json`, `satellite_channels.json` contain `name` + `url` (+ `id`/`group` for satellite) — **no image/icon fields**. |
| **Playlist queue item** | **No** | `Playlist` model: `title`, `stream_url`, `file_path`, `duration_seconds`, `media_type` — **no thumb URL or file_id column**. |
| **Queue auto-advance (`play_next`)** | **No user message** | `CallService.play_next` changes stream only; does not send now-playing chat messages. |

### 3.2 Is thumbnail URL currently stored anywhere?

**No.** No DB column, Redis key, or in-memory playback struct stores thumbnail URLs. `PlaybackState` persists `source` and `title` only — not cover.

### 3.3 Is thumbnail file path currently stored anywhere?

**No.** `media_cache` and `DOWNLOADS_PATH` store audio/video playback files only. `_extensions()` in `media_cache.py` lists `.opus`, `.ogg`, `.mp3`, `.m4a`, `.wav`, `.mp4`, `.mkv`, `.webm` — **no image extensions**.

### 3.4 Does `media_service.py` extract thumbnails?

**No.** `_download()` and `get_stream_url()` invoke yt-dlp for media streams only. No thumbnail extraction, no `--write-thumbnail`, no `thumbnail` field parsing.

### 3.5 Does `media_cache.py` cache images or only audio/video files?

**Only audio/video files.** Content-addressed cache keys URLs to downloaded playback media. Images are out of scope.

### 3.6 Does queue persistence preserve cover metadata?

**No.** `enqueue_playback_item()` / `playlist_repo.add_to_queue()` persist `title`, `stream_url`/`file_path`, `duration`, `media_type`, `added_by`. Cover is not enqueued. When `play_next` advances, cover cannot be reconstructed unless re-derived from source (YouTube id → extra fetch — not implemented).

### 3.7 Does now-playing renderer have access to cover info today?

**No.** `NowPlayingContext` fields: `title`, `media_type`, `duration`, `requester_name`, `requester_id`, `track_id`. `render_now_playing_text()` reads `show_cover` via flags but **explicitly reserves it for B5-4** with no photo branch. Grep shows **no** `.thumb` / `thumbs` usage anywhere under `app/`.

### 3.8 Does Telegram message reply media contain reusable thumbnail data?

**Yes, at handler scope only.** When user plays via reply to audio/video/document:

- Thumb is already on Telegram CDN as a **file reference** (`PhotoSize` / thumb `file_id`).
- Reuse via `send_photo(photo=file_id)` or `InputMediaPhoto(media=file_id)` **without bot-side HTTP fetch** to arbitrary URLs.
- **Safety:** file_id is opaque to users in caption; do not echo file_id in text. Validate message is the trusted `reply_to_message` from the same play command context — not user-supplied callback strings.

**Not available** for: bare URL play, search callback, TV/radio/satellite picker, playlist queue items without original Telegram message context.

---

## 4. Security and SSRF risk audit (Part B)

| # | Risk | Severity | Current state | Future rule |
|---|------|----------|---------------|-------------|
| 1 | **SSRF from external thumbnail URLs** | **Critical** | No thumb fetch today | Any HTTP thumb must pass `validate_safe_url_with_redirects`; fail → text-only |
| 2 | **Downloading large image files** | **High** | N/A | Cap e.g. **2 MB**; check `Content-Length` when present; abort oversized body while streaming |
| 3 | **Non-image content disguised as image** | **High** | N/A | Require `Content-Type` starts with `image/`; sniff first bytes (JPEG/PNG/WebP/GIF magic) before send |
| 4 | **File path leaks** | **Critical** | Renderer blocks paths in text | Never put `DOWNLOADS_PATH`, cache paths, or `file_path` in caption or logs shown to users |
| 5 | **Local file access** | **Critical** | `ensure_trusted_local_media_path` for playback | Cover temp files only under `DOWNLOADS_PATH/<chat_id>/` or dedicated `covers/` subdir; same trusted-root checks |
| 6 | **Telegram file_id leakage** | **Medium** | Not displayed today | Use file_id only as `send_photo` argument; never append to caption text |
| 7 | **Cache poisoning** | **Medium** | Media cache is URL-hash keyed | Do not mix thumb bytes into audio/video cache; separate temp cover files with short TTL |
| 8 | **Untrusted yt-dlp/search URLs** | **High** | Search URLs validated for playback | Thumbnail URL from yt-dlp is a **new trust boundary** — treat as untrusted until validated |
| 9 | **Remote URL direct to Telegram** | **Medium** | Not used in playback | `send_photo(photo=https://...)` delegates fetch to Telegram servers — still leaks URL to Telegram, bypasses bot size/type controls; **reject** for v1 |
| 10 | **Cleanup of downloaded thumbnails** | **Medium** | `safe_unlink_temp_media` exists for playback | Mirror pattern: `cleanup_prepared_cover()` in `finally` after send attempt |
| 11 | **Rate limits and spam** | **Medium** | One now-playing per successful play | Cover send only on successful join; no extra messages on failure; edit paths must not delete+resend in a loop |
| 12 | **Privacy of requester/source** | **Medium** | Renderer hides URLs/ids | Cover must not embed EXIF or URL bars; caption uses same redaction rules as text renderer |

### 4.1 Mandatory implementation rules (future)

1. Never show local paths in user-visible output.
2. Never show raw source URLs in caption or photo.
3. Never fetch arbitrary URLs without `validate_safe_url_with_redirects` (and redirect cap).
4. Enforce `image/*` content type and max size (recommend **2 MB**).
5. Clean temporary thumbnails after send (success or failure).
6. Must not break text-only now-playing when `show_cover=False` or cover unavailable.
7. Graceful fallback to text-only; log at debug/warning only.
8. Persian default fallback for captions via existing `resolve_lang` / `render_now_playing_text`.

---

## 5. Product behavior proposal (Part C)

### 5.1 `show_cover=False`

Always send/edit **text-only** now-playing message (current behavior). No photo attempt.

### 5.2 `show_cover=True`

| Condition | Behavior |
|-----------|----------|
| Safe cover available | Send photo with caption **or** text-only if message path cannot safely attach photo (see §5.3) |
| No cover available | Text-only, no error |
| Cover resolve/download fails | Text-only, debug/warning log |
| User-facing errors | **None** for missing cover |

### 5.3 Design decisions

| # | Decision | Recommendation |
|---|----------|----------------|
| 1 | `send_photo` vs text-only? | Use **`reply_photo` / `send_photo` with caption** when cover exists on **new message** paths; caption = output of `render_now_playing_text()` |
| 2 | New messages only vs edits? | **Photo only on new `message.reply()` paths** (`playback.py`). **Search / TV/radio use `edit_text`** — cannot safely upgrade text→photo without `edit_message_media` complexity or delete+resend; **default: text-only on edit paths** for B5-4 v1 |
| 3 | Edit text message to photo? | **Avoid in v1** — Telegram/Pyrogram constraints and spam risk; document as known limitation |
| 4 | Queue-on-busy interaction | Unchanged — busy paths return early with queue/already-playing text; **no cover** on those responses. Queued items **lose** cover unless B5-4b adds metadata columns |
| 5 | `buttons_enabled` | **Keyboard only** — `build_now_playing_controls` unchanged; attach same `reply_markup` to photo message |
| 6 | Language | Caption uses same `resolve_lang` + `render_now_playing_text` as today |
| 7 | Radio/TV/satellite static assets | **No** — JSON has no images; text-only unless future asset pack adds vetted static files in repo (out of scope) |

### 5.4 `show_now_playing_text=False` with cover

When text flag is off but cover is on: caption must still include **minimal safe content** — `now_playing.minimal_title` plus optional title line and optional track id (same as current minimal text mode). Never empty caption (Telegram may reject).

---

## 6. Architecture options (Part D)

### Option A: Do not implement cover yet

| Pros | Cons |
|------|------|
| Safest; zero SSRF | `show_cover=true` default is misleading until implemented |
| No code churn | Product gap vs user expectation |

**Use:** if security bandwidth is zero. **Not recommended** as final state — Option B is acceptable risk.

### Option B: Telegram-native thumbnails only (**recommended for B5-4**)

| Pros | Cons |
|------|------|
| No external HTTP from bot | Limited to reply-to-Telegram-media plays |
| Reuse Telegram CDN `file_id` | No YouTube/search/TV art |
| Low SSRF surface | Queue items lack thumbs |

**Use:** first implementation slice.

### Option C: Safe external thumbnail download

| Pros | Cons |
|------|------|
| YouTube/search cover possible | SSRF, size, MIME sniffing, temp file lifecycle |
| Reuses existing URL validator | Extra yt-dlp round-trip latency |

**Use:** **defer** to B5-4b with explicit security tests; not default.

### Option D: yt-dlp thumbnail URL directly

| Pros | Cons |
|------|------|
| Simple | Unvalidated CDN/redirect chains; Telegram URL fetch bypasses bot controls |

**Use:** **Reject.**

### 6.1 Recommended path

**Implement Option B in B5-4** with scope:

- `playback.py` success paths that call `message.reply()` and have `reply_to_message` with thumb.
- Check `show_cover` via existing flag resolution.
- Fall back to `message.reply(text)` on any failure.

**Defer Option C** until product prioritizes YouTube art and queue thumb persistence is designed.

---

## 7. Renderer / message API design (Part F)

### 7.1 Current API

```python
async def render_now_playing_text(...) -> str
```

### 7.2 Recommended future split

| Layer | Responsibility |
|-------|----------------|
| `now_playing_renderer.py` | **Text/caption only** — flags, i18n, redaction |
| `cover_art_service.py` (new) | Resolve/prepare/cleanup cover candidates |
| Handlers | Orchestrate: text = renderer; cover = service; send photo or text |

**Do not** extend renderer to return photos — keeps redaction rules in one place and avoids Pyrogram types in renderer.

### 7.3 Optional future helper module (design only — not implemented)

```python
# app/services/cover_art_service.py (proposed)

@dataclass(frozen=True)
class CoverCandidate:
    source_type: str  # "telegram_thumb" | "external_url" (B5-4b)
    telegram_file_id: str | None = None
    url: str | None = None
    local_path: str | None = None

@dataclass(frozen=True)
class PreparedCover:
    send_as: str  # "file_id" | "path"
    value: str
    temp_path: str | None = None  # for cleanup when send_as == "path"

async def resolve_cover_candidate(
    *,
    reply_message: object | None = None,
    video_id: str | None = None,
    allow_external: bool = False,
) -> CoverCandidate | None:
    """B5-4: telegram_thumb from reply only. B5-4b: optional validated external."""
    ...

async def prepare_cover_for_send(candidate: CoverCandidate) -> PreparedCover | None:
    """Validate size/type for path-based covers; pass through file_id."""
    ...

async def cleanup_prepared_cover(cover: PreparedCover | None) -> None:
    """Delete temp files; no-op for file_id."""
    ...
```

**Constants (proposed):** `MAX_COVER_BYTES = 2 * 1024 * 1024`, `COVER_DOWNLOAD_TIMEOUT_SEC = 15`, allowed MIME prefixes `image/jpeg`, `image/png`, `image/webp`.

---

## 8. Future tests (Part G)

### 8.1 Design-only (B5-4-0)

File: `tests/test_now_playing_cover_design_phaseb54.py`

- Design doc exists and mandates Option B first.
- Renderer remains text-only (no `send_photo` in renderer).
- `show_cover` present in flags; no thumbnail fetch in renderer.

### 8.2 Implementation tests (future B5-4)

| # | Test |
|---|------|
| 1 | `show_cover=False` never calls cover resolver |
| 2 | `show_cover=True` + no candidate → text-only `reply` |
| 3 | Telegram reply with thumb → `reply_photo` with caption |
| 4 | External URL rejected when `allow_external=False` |
| 5 | External URL (B5-4b) passes `validate_safe_url_with_redirects` before download |
| 6 | Non-image content-type rejected |
| 7 | Oversized image rejected |
| 8 | Caption never contains local path segments |
| 9 | Caption never contains `http://` / `https://` |
| 10 | Temp thumb file removed after successful send |
| 11 | Temp thumb file removed after send failure |
| 12 | Queue enqueue does not store cover (document limitation) or column added in later phase |
| 13 | `buttons_enabled=False` → minimal keyboard on photo message |
| 14 | `show_now_playing_text=False` + cover → minimal caption content |
| 15 | `lang=en` caption uses English `now_playing.*` strings |

**No live network** in unit tests — mock `validate_safe_url_with_redirects`, aiohttp, and Pyrogram send methods.

---

## 9. Deployment impact

| Item | B5-4-0 (this doc) | Future B5-4 (Option B) |
|------|-------------------|-------------------------|
| Migration | **None** | **None** |
| Alembic head | `0021_now_playing_flags` added `show_cover` column (mid-chain); **project head today:** `0033_instance_database_ownership` | Same |
| Env vars | None | Optional `COVER_MAX_BYTES` later |
| Restart | Not required | Bot restart after deploy |
| Callback data | **Unchanged** | **Unchanged** |
| Default `show_cover=true` | No effect | Shows TG thumbs when available; else text |

---

## 10. Exact next prompt (if Option B approved)

```
Implement Phase B5-4 only: Telegram-native cover display (Option B).

Scope:
- Add app/services/cover_art_service.py with resolve_cover_candidate (telegram_thumb only), prepare/cleanup stubs for file_id path.
- Wire playback.py message.reply success paths only (play_audio, play_video, replay_on_reply when reply_to_message has thumb).
- Respect show_cover via existing flag resolution; show_cover=False → current text reply.
- Caption = await render_now_playing_text(...); reply_markup = build_now_playing_controls unchanged.
- search.py and tv_radio.py stay text-only (edit_text paths) — document in CHANGELOG.
- No external URL fetch; allow_external=False hardcoded.
- No migration; no callback changes; no playlist/queue/play_next cover.
- Tests: tests/test_now_playing_cover_phaseb54.py (mocked Pyrogram); extend test_now_playing_metadata_flags regression.
- Persian default; TEST_MODE=1 for tests.
- Do not start B6; do not implement owner-scoped features; do not run bot/live Telegram.

Validation:
pytest tests/test_now_playing_cover_phaseb54.py tests/test_now_playing_metadata_flags_phaseb53.py -q
python -m py_compile app/services/cover_art_service.py app/handlers/playback.py
```

---

## Appendix: B5 phase context (reports consolidated)

| Phase | Status | Cover relevance |
|-------|--------|-----------------|
| B5-0 | Complete | Identified `show_photo` → cover; SSRF notes |
| B5-1 | Complete | Central renderer; text only |
| B5-2 | Complete | `show_cover` column default `true` |
| B5-3 | Complete | Flags wired; `show_cover` intentionally no-op |
| **B5-4-0** | **This document** | Security gate before implementation |
| B5-4 | Pending | Option B recommended |
| B5-4b | Optional later | Option C external thumbs + queue metadata |
| B6 | Out of scope | Repeat vs vote-skip |

---

*End of Phase B5-4-0 design document.*
