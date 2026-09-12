"""Tests for split-only i18n infrastructure."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "999888777")

import pytest

from tests.i18n_test_utils import I18N_DIR, MANIFEST_PATH, build_merged_i18n_tree

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "split_i18n_resources.py"


def _collect_leaf_paths(data: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= _collect_leaf_paths(value, full)
        else:
            paths.add(full)
    return paths


class TestManifest:
    def test_manifest_exists(self):
        assert MANIFEST_PATH.is_file(), "manifest.json must exist"

    def test_manifest_valid_json(self):
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_manifest_has_required_fields(self):
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert "version" in data
        assert "languages" in data
        assert "fragments" in data
        assert "deprecated_keys" in data

    def test_manifest_languages(self):
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        assert set(data["languages"]) >= {"fa", "en"}

    def test_manifest_fragments_have_path(self):
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for frag in data["fragments"]:
            assert "path" in frag, f"Fragment missing 'path': {frag}"


class TestFragmentFiles:
    def test_all_fa_fragments_exist(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        missing = [
            str(I18N_DIR / "fa" / frag["path"])
            for frag in manifest["fragments"]
            if not (I18N_DIR / "fa" / frag["path"]).is_file()
        ]
        assert not missing, f"Missing FA fragments: {missing}"

    def test_all_en_fragments_exist(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        missing = [
            str(I18N_DIR / "en" / frag["path"])
            for frag in manifest["fragments"]
            if not (I18N_DIR / "en" / frag["path"]).is_file()
        ]
        assert not missing, f"Missing EN fragments: {missing}"

    def test_all_fa_fragments_valid_json(self):
        for f in (I18N_DIR / "fa").rglob("*.json"):
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                pytest.fail(f"Invalid JSON in {f}: {exc}")

    def test_all_en_fragments_valid_json(self):
        for f in (I18N_DIR / "en").rglob("*.json"):
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                pytest.fail(f"Invalid JSON in {f}: {exc}")

    def test_no_duplicate_leaf_paths_fa(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        seen: dict[str, str] = {}
        for frag_spec in manifest["fragments"]:
            frag_file = I18N_DIR / "fa" / frag_spec["path"]
            if not frag_file.is_file():
                continue
            frag_data = json.loads(frag_file.read_text(encoding="utf-8"))
            for p in _collect_leaf_paths(frag_data):
                if p in seen:
                    pytest.fail(
                        f"Duplicate FA leaf path '{p}' in "
                        f"'{frag_spec['path']}' and '{seen[p]}'"
                    )
                seen[p] = frag_spec["path"]

    def test_no_duplicate_leaf_paths_en(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        seen: dict[str, str] = {}
        for frag_spec in manifest["fragments"]:
            frag_file = I18N_DIR / "en" / frag_spec["path"]
            if not frag_file.is_file():
                continue
            frag_data = json.loads(frag_file.read_text(encoding="utf-8"))
            for p in _collect_leaf_paths(frag_data):
                if p in seen:
                    pytest.fail(
                        f"Duplicate EN leaf path '{p}' in "
                        f"'{frag_spec['path']}' and '{seen[p]}'"
                    )
                seen[p] = frag_spec["path"]


class TestSplitParity:
    def test_fa_en_parity_split_leaf_paths(self):
        fa = _collect_leaf_paths(build_merged_i18n_tree("fa"))
        en = _collect_leaf_paths(build_merged_i18n_tree("en"))
        assert fa == en, (
            f"Split FA/EN leaf mismatch — FA only: {sorted(fa - en)[:20]}, "
            f"EN only: {sorted(en - fa)[:20]}"
        )


class TestSplitScriptCheckMode:
    def test_split_check_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(_ROOT),
        )
        assert result.returncode == 0, (
            f"--check failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
        assert "Result: OK" in result.stdout

    def test_split_check_default_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(_ROOT),
        )
        assert result.returncode == 0
        assert "Result: OK" in result.stdout

    def test_split_check_does_not_modify_fragments(self):
        sample = I18N_DIR / "fa" / "common.json"
        before = sample.read_bytes()
        before_mtime = sample.stat().st_mtime_ns
        subprocess.run(
            [sys.executable, str(_SCRIPT), "--check"],
            capture_output=True,
            cwd=str(_ROOT),
        )
        assert sample.read_bytes() == before
        assert sample.stat().st_mtime_ns == before_mtime

    def test_check_fails_on_missing_fragment(self, tmp_path: Path):
        import shutil

        fake_i18n = tmp_path / "i18n"
        shutil.copytree(I18N_DIR, fake_i18n)
        (fake_i18n / "fa" / "common.json").unlink()

        script = _SCRIPT.read_text(encoding="utf-8")
        patched = script.replace(
            'I18N_DIR = ROOT / "app" / "resources" / "i18n"',
            f'I18N_DIR = Path(r"{fake_i18n}")',
        )
        runner = tmp_path / "run_check.py"
        runner.write_text(patched, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(runner), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(_ROOT),
        )
        assert result.returncode != 0
        assert "fragment missing" in result.stdout.lower() or "[FAIL]" in result.stdout

    def test_check_fails_on_duplicate_leaf_path(self, tmp_path: Path):
        import shutil

        fake_i18n = tmp_path / "i18n"
        shutil.copytree(I18N_DIR, fake_i18n)
        dup = {"common": {"buttons": {"back": "DUPLICATE"}}}
        (fake_i18n / "fa" / "ask.json").write_text(
            json.dumps(dup, ensure_ascii=False),
            encoding="utf-8",
        )

        script = _SCRIPT.read_text(encoding="utf-8")
        patched = script.replace(
            'I18N_DIR = ROOT / "app" / "resources" / "i18n"',
            f'I18N_DIR = Path(r"{fake_i18n}")',
        )
        runner = tmp_path / "run_check_dup.py"
        runner.write_text(patched, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(runner), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(_ROOT),
        )
        assert result.returncode != 0
        assert "duplicate leaf path" in result.stdout.lower()


class TestTextServiceSplitOnly:
    @pytest.fixture
    def ts(self):
        from app.utils.i18n import TextService

        svc = TextService()
        svc.reload()
        return svc

    @pytest.mark.parametrize(
        "key",
        [
            "common.buttons.back",
            "panels.group.settings.queue",
            "panels.developer.title",
            "call_security.panel_title",
            "playback_cmd.provide_source",
            "texts_links.title",
            "help.playback",
            "admin.helpers.join_group_progress",
        ],
    )
    def test_fa_critical_key_resolves(self, ts, key):
        val = ts.t("fa", key)
        assert not val.startswith("[missing:"), f"FA key missing: {key}"
        assert not val.startswith("[invalid:"), f"FA key invalid: {key}"

    @pytest.mark.parametrize(
        "key",
        [
            "common.buttons.back",
            "panels.group.settings.queue",
            "panels.developer.title",
            "call_security.panel_title",
            "playback_cmd.provide_source",
            "texts_links.title",
            "help.playback",
            "admin.helpers.join_group_progress",
        ],
    )
    def test_en_critical_key_resolves(self, ts, key):
        val = ts.t("en", key)
        assert not val.startswith("[missing:"), f"EN key missing: {key}"
        assert not val.startswith("[invalid:"), f"EN key invalid: {key}"

    def test_missing_key_returns_missing_marker(self, ts):
        val = ts.t("fa", "completely.nonexistent.key.xyz")
        assert val == "[missing:completely.nonexistent.key.xyz]"

    def test_invalid_leaf_returns_invalid_marker(self, ts):
        val = ts.t("fa", "common.buttons")
        assert val.startswith("[invalid:")

    def test_format_kwargs_applied(self, ts):
        val = ts.t("fa", "start.welcome", mention="TestUser")
        assert "TestUser" in val

    def test_reload_clears_cache(self, ts):
        val1 = ts.t("fa", "common.buttons.back")
        ts.reload("fa")
        val2 = ts.t("fa", "common.buttons.back")
        assert val1 == val2

    def test_label_function_resolves(self):
        from app.utils.i18n import label

        for lang in ("fa", "en"):
            val = label(lang, "broadcast_scope", "groups")
            assert val != "", f"label() returned empty for lang={lang}"

    def test_callback_data_not_in_i18n(self, ts):
        val = ts.t("fa", "grp:settings")
        assert val.startswith("[missing:")

    def test_loader_fails_without_manifest(self, tmp_path: Path):
        from app.utils.i18n import I18nResourceError, TextService

        empty = tmp_path / "empty_i18n"
        empty.mkdir()
        svc = TextService(i18n_dir=empty)
        with pytest.raises(I18nResourceError, match="manifest not found"):
            svc.load("fa")

    def test_loader_fails_on_missing_fragment(self, tmp_path: Path):
        import shutil

        from app.utils.i18n import I18nResourceError, TextService

        fake = tmp_path / "i18n"
        shutil.copytree(I18N_DIR, fake)
        (fake / "fa" / "common.json").unlink()
        svc = TextService(i18n_dir=fake)
        with pytest.raises(I18nResourceError, match="fragment missing"):
            svc.load("fa")

    def test_loader_fails_on_duplicate_leaf(self, tmp_path: Path):
        import shutil

        from app.utils.i18n import I18nResourceError, TextService

        fake = tmp_path / "i18n"
        shutil.copytree(I18N_DIR, fake)
        dup = {"common": {"buttons": {"back": "DUPLICATE"}}}
        (fake / "fa" / "ask.json").write_text(
            json.dumps(dup, ensure_ascii=False),
            encoding="utf-8",
        )
        svc = TextService(i18n_dir=fake)
        with pytest.raises(I18nResourceError, match="duplicate leaf path"):
            svc.load("fa")

    def test_no_legacy_monolith_files(self):
        legacy_dir = _ROOT / "app" / "resources" / "strings"
        assert not (legacy_dir / "fa.json").is_file(), "legacy fa.json must be removed"
        assert not (legacy_dir / "en.json").is_file(), "legacy en.json must be removed"


class TestNoLegacyReferencesInLoader:
    def test_i18n_module_has_no_load_legacy(self):
        source = (_ROOT / "app" / "utils" / "i18n.py").read_text(encoding="utf-8")
        assert "_load_legacy" not in source
        assert "resources/strings" not in source
