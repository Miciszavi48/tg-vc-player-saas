"""Coverage tests: every i18n key used by app code exists in split resources.

Backed by scripts/check_i18n_usage.py (read-only AST audit). Verifies literal
t()/label() keys, dynamic-family allowlists, and that the audit script itself
is split-only and side-effect free.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from typing import Any

import pytest

from tests.i18n_test_utils import I18N_DIR, ROOT, load_en_i18n, load_fa_i18n

_SCRIPT = ROOT / "scripts" / "check_i18n_usage.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_i18n_usage", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


@pytest.fixture(scope="module")
def fa_tree() -> dict:
    return load_fa_i18n()


@pytest.fixture(scope="module")
def en_tree() -> dict:
    return load_en_i18n()


@pytest.fixture(scope="module")
def usage() -> dict[str, Any]:
    return checker.scan_app()


def _resolve(tree: dict, key: str) -> Any:
    cur: Any = tree
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class TestUsageScript:
    def test_script_exits_zero_and_is_readonly(self):
        fragments = sorted(I18N_DIR.rglob("*.json"))
        before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in fragments}

        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=120,
        )
        assert result.returncode == 0, f"check_i18n_usage failed:\n{result.stdout}\n{result.stderr}"
        assert "Result: OK" in result.stdout

        after = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in fragments}
        assert before == after, "check_i18n_usage.py must not modify fragments"

    def test_script_does_not_reference_legacy_monoliths(self):
        source = _SCRIPT.read_text(encoding="utf-8")
        assert "resources/strings" not in source
        assert "strings/fa.json" not in source
        assert "strings/en.json" not in source

    def test_all_split_fragments_are_registered(self):
        manifest = checker.load_manifest()
        registered = checker._collect_registered_fragment_paths(manifest)
        missing: list[str] = []
        orphaned: list[str] = []
        for lang in manifest["languages"]:
            actual = checker._collect_actual_fragment_paths(lang)
            missing.extend(f"[{lang}] {path}" for path in sorted(registered - actual))
            orphaned.extend(f"[{lang}] {path}" for path in sorted(actual - registered))
        assert not missing, f"manifest references missing fragments: {missing}"
        assert not orphaned, f"unregistered split i18n fragments: {orphaned}"


class TestLiteralKeyCoverage:
    def test_all_literal_t_keys_exist_fa_en(self, usage, fa_tree, en_tree):
        missing: list[str] = []
        for key, locs in sorted(usage["literal_keys"].items()):
            for name, tree in (("fa", fa_tree), ("en", en_tree)):
                leaf = _resolve(tree, key)
                if not isinstance(leaf, str):
                    missing.append(f"[{name}] {key} ({locs[0]})")
        assert not missing, f"missing/invalid literal t() keys: {missing}"

    def test_all_literal_label_keys_exist_fa_en(self, usage, fa_tree, en_tree):
        missing: list[str] = []
        for key, locs in sorted(usage["label_literal_keys"].items()):
            for name, tree in (("fa", fa_tree), ("en", en_tree)):
                if not isinstance(_resolve(tree, key), str):
                    missing.append(f"[{name}] {key} ({locs[0]})")
        assert not missing, f"missing literal label() keys: {missing}"

    def test_all_label_groups_exist_fa_en(self, usage, fa_tree, en_tree):
        missing: list[str] = []
        for group, locs in sorted(usage["label_groups"].items()):
            for name, tree in (("fa", fa_tree), ("en", en_tree)):
                subtree = _resolve(tree, f"labels.{group}")
                if not isinstance(subtree, dict) or not subtree:
                    missing.append(f"[{name}] labels.{group} ({locs[0]})")
        assert not missing, f"missing/empty labels groups: {missing}"

    def test_extractor_found_meaningful_volume(self, usage):
        # Regression guard: extraction breaking would silently pass coverage.
        assert len(usage["literal_keys"]) > 800
        assert len(usage["dynamic_patterns"]) >= 15


class TestDynamicAllowlist:
    def test_every_observed_dynamic_pattern_is_allowlisted(self, usage):
        unknown = sorted(
            pattern
            for pattern in usage["dynamic_patterns"]
            if pattern not in checker.DYNAMIC_ALLOWLIST
        )
        assert not unknown, f"unregistered dynamic t() patterns: {unknown}"

    def test_all_allowlisted_keys_exist_fa_en(self, fa_tree, en_tree):
        missing: list[str] = []
        for pattern, keys in sorted(checker.DYNAMIC_ALLOWLIST.items()):
            for key in keys:
                for name, tree in (("fa", fa_tree), ("en", en_tree)):
                    if not isinstance(_resolve(tree, key), str):
                        missing.append(f"[{name}] {key} (pattern {pattern})")
        assert not missing, f"missing allowlisted dynamic keys: {missing}"

    @pytest.mark.parametrize(
        "key",
        [
            "call_security.reason_mute_enforced",
            "call_security.reason_multiple_join",
            "call_security.feature_membership_age",
            "now_playing.satellite",
            "panels.group.settings.language_button_en",
            "panels.group.settings.default_media_summary_video",
            "admin.fm.badge_broken",
            "playback.types.video",
            "help.troubleshoot",
            "help.btn.play_link",
            "help.pages.play_controls",
            "monthly_invoice.status.pending",
            "panels.group.install_setup.charge_menu_title",
            "status_summary.feature_trial",
            "texts_links.runtime_status.active_owner_override",
            "texts_links.kind_link",
        ],
    )
    def test_critical_dynamic_keys_exist(self, key, fa_tree, en_tree):
        assert isinstance(_resolve(fa_tree, key), str)
        assert isinstance(_resolve(en_tree, key), str)

    def test_code_referenced_placeholders_match_fa_en(self, usage, fa_tree, en_tree):
        keys = set(usage["literal_keys"])
        keys |= set(usage["label_literal_keys"])
        for allowlisted in checker.DYNAMIC_ALLOWLIST.values():
            keys.update(allowlisted)

        mismatched: list[str] = []
        for key in sorted(keys):
            fa_value = _resolve(fa_tree, key)
            en_value = _resolve(en_tree, key)
            if not isinstance(fa_value, str) or not isinstance(en_value, str):
                continue
            fa_placeholders = checker._placeholder_names(fa_value)
            en_placeholders = checker._placeholder_names(en_value)
            if fa_placeholders != en_placeholders:
                mismatched.append(
                    f"{key}: fa={sorted(fa_placeholders)} en={sorted(en_placeholders)}"
                )
        assert not mismatched, f"placeholder mismatches: {mismatched}"


class TestRenderedOutput:
    def test_no_missing_or_invalid_marker_for_used_keys(self, usage):
        from app.utils.i18n import texts

        bad: list[str] = []
        for key in usage["literal_keys"]:
            for lang in ("fa", "en"):
                rendered = texts.t(lang, key)
                if rendered.startswith("[missing:") or rendered.startswith("[invalid:"):
                    bad.append(f"[{lang}] {key} -> {rendered}")
        assert not bad, f"runtime keys render markers: {bad}"

    def test_allowlisted_dynamic_keys_render_clean(self):
        from app.utils.i18n import texts

        bad: list[str] = []
        for keys in checker.DYNAMIC_ALLOWLIST.values():
            for key in keys:
                for lang in ("fa", "en"):
                    rendered = texts.t(lang, key)
                    if rendered.startswith("[missing:") or rendered.startswith("[invalid:"):
                        bad.append(f"[{lang}] {key} -> {rendered}")
        assert not bad, f"dynamic keys render markers: {bad}"
