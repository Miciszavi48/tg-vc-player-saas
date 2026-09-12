"""Phase B5-4-0: design-only assertions for cover display security (no implementation)."""

from __future__ import annotations

from pathlib import Path


def test_design_document_exists():
    doc = Path("docs/features/now_playing_cover_security.md")
    assert doc.is_file(), "docs/features/now_playing_cover_security.md must exist"
    text = doc.read_text(encoding="utf-8")
    assert "Option B" in text
    assert "Telegram-native" in text
    assert "Reject" in text or "reject" in text


def test_renderer_remains_text_only_no_photo_send():
    source = Path("app/services/now_playing_renderer.py").read_text(encoding="utf-8")
    assert "send_photo" not in source
    assert "reply_photo" not in source
    assert "show_cover" in source
    assert "B5-4" in source


def test_media_service_does_not_extract_thumbnails_yet():
    source = Path("app/services/media_service.py").read_text(encoding="utf-8")
    assert "thumbnail" not in source.lower()
    assert "write-thumbnail" not in source.lower()


def test_media_cache_photo_extensions_are_explicit_for_direct_social_media():
    source = Path("app/services/media_cache.py").read_text(encoding="utf-8")
    assert 'if media_type == "photo"' in source
    assert '[".jpg", ".jpeg", ".png", ".webp"]' in source


def test_playlist_model_has_no_cover_column():
    source = Path("app/database/models.py").read_text(encoding="utf-8")
    assert "show_cover" in source
    assert "thumb" not in source.lower() or "show_cover" in source


def test_design_recommends_fail_closed_external_fetch():
    doc = Path("docs/features/now_playing_cover_security.md").read_text(encoding="utf-8")
    assert "validate_safe_url_with_redirects" in doc
    assert "fail closed" in doc.lower() or "text-only" in doc


def test_cover_art_service_implemented_telegram_native_only():
    path = Path("app/services/cover_art_service.py")
    assert path.is_file()
    source = path.read_text(encoding="utf-8")
    assert "extract_telegram_cover_from_reply" in source
    assert "aiohttp" not in source.lower()
    assert "validate_safe_url" not in source
