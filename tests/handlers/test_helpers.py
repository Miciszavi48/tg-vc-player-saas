from __future__ import annotations


from app.utils.helpers import format_duration, mask_phone, parse_user_id, persian_date


class TestHelpers:
    def test_format_duration(self):
        assert format_duration(0) == "0:00"
        assert format_duration(65) == "1:05"
        assert format_duration(3661) == "1:01:01"
        assert format_duration(-5) == "0:00"

    def test_parse_user_id(self):
        assert parse_user_id("user123abc") == 123
        assert parse_user_id("no_digits") is None
        assert parse_user_id("42") == 42

    def test_mask_phone(self):
        masked = mask_phone("+989123456789")
        assert masked.endswith("6789")
        assert "*" in masked

    def test_persian_date(self):
        result = persian_date()
        assert "/" in result
        parts = result.split("/")
        assert len(parts) == 3
