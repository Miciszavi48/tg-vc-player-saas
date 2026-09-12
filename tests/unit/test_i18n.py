from __future__ import annotations

import pytest

from app.utils.i18n import I18nResourceError, TextService, normalize_lang, t


class TestTextService:
    def test_load_fa_json(self, text_service: TextService):
        data = text_service.load("fa")
        assert isinstance(data, dict)
        assert "common" in data
        assert "buttons" in data["common"]

    def test_t_basic_key(self, text_service: TextService):
        result = text_service.t("fa", "common.buttons.back")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "بازگشت" in result

    def test_t_missing_key(self, text_service: TextService):
        result = text_service.t("fa", "nonexistent.deep.key")
        assert result == "[missing:nonexistent.deep.key]"

    def test_t_format(self, text_service: TextService):
        result = text_service.t("fa", "start.welcome", mention="TestUser")
        assert "TestUser" in result

    def test_normalize_lang_unknown_defaults_to_fa(self):
        assert normalize_lang("xx") == "fa"

    def test_module_t_fallback_to_fa_for_unknown_lang(self, text_service: TextService):
        result = t("xx", "common.buttons.back")
        fa_result = text_service.t("fa", "common.buttons.back")
        assert result == fa_result

    def test_load_unknown_lang_raises(self, text_service: TextService):
        with pytest.raises(I18nResourceError, match="lang=xx"):
            text_service.load("xx")
