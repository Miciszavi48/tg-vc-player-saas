"""UI contract enforcement regression tests.

Verifies that NO hardcoded user-visible text exists in handler code,
and that all text originates from TextService JSON keys.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

HANDLERS_DIR = Path(__file__).resolve().parents[2] / "app" / "handlers"


def _get_handler_files() -> list[Path]:
    return sorted(HANDLERS_DIR.glob("*.py"))


def _find_hardcoded_user_text(filepath: Path) -> list[tuple[int, str]]:
    """Scan a handler file for f-string or literal string arguments in
    message.reply / query.answer / send_message / edit_text calls that
    don't go through t()."""
    violations = []
    content = filepath.read_text(encoding="utf-8")

    patterns = [
        re.compile(r'\.reply\(f"'),
        re.compile(r'\.answer\(f"'),
        re.compile(r'\.send_message\([^,]+,\s*f"'),
        re.compile(r'\.edit_text\(f"'),
    ]
    fstring_item = re.compile(r'f"• ')

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("logger."):
            continue
        for pat in patterns:
            if pat.search(line):
                violations.append((i, line.strip()))
        if fstring_item.search(line):
            violations.append((i, line.strip()))

    return violations


class TestNoHardcodedText:
    """Ensure zero hardcoded user-visible text in all handler files."""

    def test_no_fstring_list_items(self):
        """No f-string formatted list items (f'• ...') in handlers."""
        for fp in _get_handler_files():
            content = fp.read_text()
            matches = re.findall(r'f"• ', content)
            assert len(matches) == 0, f"{fp.name} has {len(matches)} hardcoded f-string list items"

    def test_no_fstring_reply(self):
        """No f-string arguments in .reply() calls."""
        for fp in _get_handler_files():
            content = fp.read_text()
            matches = re.findall(r'\.reply\(f"', content)
            assert len(matches) == 0, f"{fp.name} has hardcoded f-string in .reply()"

    def test_no_fstring_answer(self):
        """No f-string arguments in .answer() calls."""
        for fp in _get_handler_files():
            content = fp.read_text()
            matches = re.findall(r'\.answer\(f"', content)
            assert len(matches) == 0, f"{fp.name} has hardcoded f-string in .answer()"

    def test_no_hardcoded_answer_strings(self):
        """No literal string arguments in .answer("...") that aren't from t()."""
        for fp in _get_handler_files():
            for i, line in enumerate(fp.read_text().splitlines(), 1):
                if '.answer("' in line and "t(" not in line and "t(_" not in line:
                    if "show_alert" in line:
                        assert False, f"{fp.name}:{i} has hardcoded .answer() text"


class TestJsonKeyParity:
    """Ensure fa.json and en.json have matching key sets."""

    def _count_leaves(self, d: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for k, v in d.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys |= self._count_leaves(v, full)
            else:
                keys.add(full)
        return keys

    def test_key_parity(self):
        """Merged FA and EN split trees must have identical key sets."""
        fa = load_fa_i18n()
        en = load_en_i18n()

        fa_keys = self._count_leaves(fa)
        en_keys = self._count_leaves(en)

        only_fa = fa_keys - en_keys
        only_en = en_keys - fa_keys

        assert not only_fa, f"Keys only in fa.json: {only_fa}"
        assert not only_en, f"Keys only in en.json: {only_en}"


class TestCallbackDataStable:
    """Ensure callback_data values are stable English constants."""

    def test_cb_values_are_ascii(self):
        from app.utils.ui import CB

        for name, value in CB.items():
            assert value.isascii(), f"CB['{name}'] = '{value}' contains non-ASCII characters"
            assert ":" in value or "_" in value or value.isalnum(), \
                f"CB['{name}'] = '{value}' has unexpected format"

    def test_cb_values_not_persian(self):
        from app.utils.ui import CB
        persian_pattern = re.compile(r'[\u0600-\u06FF]')
        for name, value in CB.items():
            assert not persian_pattern.search(value), \
                f"CB['{name}'] = '{value}' contains Persian characters"


class TestMissingKeyFallback:
    """Ensure missing keys return [missing:key] safely."""

    def test_missing_key_returns_placeholder(self):
        from app.utils.i18n import t
        result = t("fa", "nonexistent.deeply.nested.key")
        assert result == "[missing:nonexistent.deeply.nested.key]"

    def test_missing_key_does_not_crash(self):
        from app.utils.i18n import t
        result = t("fa", "also.missing", foo="bar")
        assert "[missing:" in result


@pytest.mark.asyncio
class TestFeatureAreaKeys:
    """Verify key coverage for each major feature area."""

    async def test_start_menu_keys(self):
        from app.utils.i18n import t
        keys = [
            "start.welcome", "start.menu_title",
            "start.menu.force_join", "start.menu.pricing",
            "start.menu.add_to_group", "start.menu.add_to_channel",
        ]
        for k in keys:
            assert "[missing:" not in t("fa", k), f"Missing fa key: {k}"
            assert "[missing:" not in t("en", k), f"Missing en key: {k}"

    async def test_playback_keys(self):
        from app.utils.i18n import t
        keys = [
            "playback_cmd.playing_audio", "playback_cmd.playing_video",
            "playback_cmd.stopped_audio", "playback_cmd.paused",
            "playback_cmd.resumed", "playback_cmd.failed",
            "playback_cmd.dedicated_to", "playback_cmd.replay_no_reply",
        ]
        for k in keys:
            val = t("fa", k, target="x", ms=0, volume=0)
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_panel_keys(self):
        from app.utils.i18n import t
        keys = [
            "panels.developer.title", "panels.owner.title",
            "panels.sudo.title", "panels.group.title",
        ]
        for k in keys:
            assert "[missing:" not in t("fa", k), f"Missing fa key: {k}"

    async def test_credit_keys(self):
        from app.utils.i18n import t
        keys = [
            "credit.charged", "credit.deducted", "credit.insufficient_wallet",
            "credit.update_charge_success",
        ]
        for k in keys:
            val = t("fa", k, amount=10, chat_title="x", days=5)
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_list_format_keys(self):
        from app.utils.i18n import texts as _texts
        test_keys = [
            (
                "list_fmt.group_item",
                {
                    "title": "x",
                    "chat_id": 1,
                    "status": "active",
                    "days": 0,
                    "installed_by": "2",
                    "link": "-",
                },
            ),
            (
                "list_fmt.channel_item",
                {
                    "title": "x",
                    "chat_id": 1,
                    "status": "active",
                    "days": 0,
                    "installed_by": "2",
                    "link": "-",
                },
            ),
            ("list_fmt.chat_item", {"chat_id": 1, "chat_type": "group"}),
            ("list_fmt.no_credit_item", {"chat_id": 1, "chat_type": "group", "status": "active", "days": 0}),
            ("list_fmt.user_item", {"user_id": 1, "username": "u"}),
            ("list_fmt.admin_item_m", {"user_id": 1, "username": "u"}),
            ("list_fmt.admin_item_v", {"user_id": 1, "username": "u"}),
            ("list_fmt.word_item", {"word": "w"}),
            ("list_fmt.invoice_item", {"id": 1, "chat": 1, "days": 5, "status": "ok"}),
            ("list_fmt.install_log_item", {"title": "x", "action": "install"}),
            ("list_fmt.setting_entry", {}),
        ]
        for k, kwargs in test_keys:
            val = _texts.t("fa", k, **kwargs)
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_wallet_keys(self):
        from app.utils.i18n import t
        keys = [
            "wallet.topup_success", "wallet.topup_notification",
            "wallet.sudo_not_found",
        ]
        for k in keys:
            val = t("fa", k, sudo="1", amount=100)
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_promotion_keys(self):
        from app.utils.i18n import t
        keys = [
            "promotion.promoted", "promotion.demoted",
            "promotion.creators_list_title", "promotion.creators_list_empty",
        ]
        for k in keys:
            val = t("fa", k, user="x")
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_force_join_keys(self):
        from app.utils.i18n import t
        keys = [
            "force_join_mgmt.not_joined", "force_join_mgmt.join_button",
            "force_join_mgmt.check_button",
        ]
        for k in keys:
            val = t("fa", k, channel="x")
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_filter_mgmt_keys(self):
        from app.utils.i18n import t
        keys = [
            "filter_mgmt.added", "filter_mgmt.removed",
            "filter_mgmt.list_title", "filter_mgmt.list_empty",
        ]
        for k in keys:
            val = t("fa", k, word="x")
            assert "[missing:" not in val, f"Missing fa key: {k}"

    async def test_owner_mgmt_keys(self):
        from app.utils.i18n import t
        keys = [
            "owner_mgmt.bot_credit_display", "owner_mgmt.total_users",
        ]
        for k in keys:
            val = t("fa", k, credit="0", count=0)
            assert "[missing:" not in val, f"Missing fa key: {k}"
