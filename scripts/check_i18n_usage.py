"""Read-only audit: verify i18n keys used by app code exist in split resources.

Usage:
    python scripts/check_i18n_usage.py
    python scripts/check_i18n_usage.py --verbose

Checks (split-only; the removed legacy monolith directory is never read):
    1. Every literal key in ``t(lang, "dot.path")`` exists as a string leaf
       in both FA and EN merged fragment trees.
    2. Every literal ``label(lang, "group", "value")`` resolves to
       ``labels.{group}.{value}``; every literal group has a non-empty
       ``labels.{group}`` subtree.
    3. Every dynamic f-string ``t()`` pattern is registered in
       DYNAMIC_ALLOWLIST and all of its enumerated expected keys exist
       in both languages.
    4. Every split JSON file is registered in manifest.json, and every
       manifest fragment exists for each language.
    5. Placeholders match between FA/EN for every code-referenced key.
    6. Variable-key ``t()`` call sites are reported (informational).
    7. Unresolved i18n-namespace dotted literals are reported as suspects
       (informational; analytics event names are excluded).
    8. Unused leaf candidates are reported (informational only).

Exit code 0 when all required keys exist, 1 otherwise. Strictly read-only.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_I18N_DIR = _ROOT / "app" / "resources" / "i18n"
_APP_DIR = _ROOT / "app"

# Dynamic f-string t() patterns -> enumerated expected keys.
# A pattern is the f-string with every formatted placeholder replaced by "*".
# Each family lists its runtime value source so drift can be re-audited.
DYNAMIC_ALLOWLIST: dict[str, list[str]] = {
    # app/services/start_customization_service.py — fixed semantic SLOT_KEYS.
    "start.customization.buttons.*": [
        "start.customization.buttons.purchase",
        "start.customization.buttons.test",
        "start.customization.buttons.use",
        "start.customization.buttons.history",
        "start.customization.buttons.ability",
        "start.customization.buttons.commands",
        "start.customization.buttons.support",
        "start.customization.buttons.note",
    ],
    # app/handlers/youtube_session_panel.py — persisted session statuses are
    # constrained by YoutubeCookieSession's database check constraint.
    "youtube_sessions.status_*": [
        "youtube_sessions.status_active",
        "youtube_sessions.status_disabled",
        "youtube_sessions.status_invalid",
    ],
    # app/handlers/fast_creat_token_panel.py — values are constrained by the
    # FastCreatApiToken provider/status database check constraints.
    "fast_creat_tokens.provider_*": [
        "fast_creat_tokens.provider_instagram",
        "fast_creat_tokens.provider_tiktok",
        "fast_creat_tokens.provider_spotify",
    ],
    "fast_creat_tokens.status_*": [
        "fast_creat_tokens.status_active",
        "fast_creat_tokens.status_disabled",
        "fast_creat_tokens.status_invalid",
    ],
    # app/handlers/call_security_runtime.py — SuspiciousEvent reasons
    # (app/services/call_security_service.py detect_* + moderation actions).
    "call_security.reason_*": [
        "call_security.reason_abnormal_time_gap",
        "call_security.reason_multiple_join",
        "call_security.reason_mic_active_on_entry",
        "call_security.reason_multiple_video_joins",
        "call_security.reason_multiple_sources",
        "call_security.reason_multiple_endpoints",
        "call_security.reason_unmute_privileged",
        "call_security.reason_unmute_membership_age",
        "call_security.reason_mute_enforced",
        "call_security.reason_mute_failed",
        "call_security.reason_unknown_membership_age",
    ],
    # app/handlers/call_security_panel.py — _TOGGLE_MAP fields with
    # "_enabled" stripped (owner_access branches to a literal btn key).
    "call_security.feature_*": [
        "call_security.feature_enabled",
        "call_security.feature_mute_incoming",
        "call_security.feature_summary",
        "call_security.feature_report",
        "call_security.feature_membership_age",
    ],
    # app/services/now_playing_renderer.py — _MEDIA_TYPE_KEYS values.
    "now_playing.*": [
        "now_playing.audio",
        "now_playing.video",
        "now_playing.radio",
        "now_playing.tv",
        "now_playing.satellite",
    ],
    # app/services/group_text_call_command_service.py —
    # set_call_stats_reset_cadence() rejects anything outside {daily, monthly}.
    "group_text_call.cadence_*": [
        "group_text_call.cadence_daily",
        "group_text_call.cadence_monthly",
    ],
    # app/services/group_text_call_command_service.py — EQUALIZER_PRESETS;
    # set_equalizer_preset() rejects any other value and get_equalizer_preset()
    # falls back to "normal".
    "group_text_call.eq_preset_*": [
        "group_text_call.eq_preset_normal",
        "group_text_call.eq_preset_bassboost",
        "group_text_call.eq_preset_amplifier",
        "group_text_call.eq_preset_soft",
        "group_text_call.eq_preset_treble",
    ],
    # app/handlers/group_text_call_commands.py — the only caller of
    # notify_owners_of_moderation() passes "mute" or "unmute".
    "group_text_call.call_report_action_*": [
        "group_text_call.call_report_action_mute",
        "group_text_call.call_report_action_unmute",
    ],
    # app/services/hot_seat_service.py — MODE_ALL / MODE_PRIVATE; create_game()
    # rejects any other join mode.
    "hot_seat.mode_*": [
        "hot_seat.mode_all",
        "hot_seat.mode_private",
    ],
    # app/services/texts_links_ui.py — FIELD_SPECS.location_keys union.
    "texts_links.runtime_locations.*": [
        "texts_links.runtime_locations.private_start_message",
        "texts_links.runtime_locations.storage_only",
        "texts_links.runtime_locations.about_callback",
        "texts_links.runtime_locations.tariff_callback",
        "texts_links.runtime_locations.start_buttons",
        "texts_links.runtime_locations.group_support",
        "texts_links.runtime_locations.install_post_install",
    ],
    # app/services/texts_links_ui.py — FIELD_SPECS global/owner effect keys.
    "texts_links.runtime_effects.*": [
        "texts_links.runtime_effects.global_private_start_message",
        "texts_links.runtime_effects.owner_start_text_developer_only",
        "texts_links.runtime_effects.storage_only_no_runtime",
        "texts_links.runtime_effects.global_about_callback",
        "texts_links.runtime_effects.owner_saved_about_unused",
        "texts_links.runtime_effects.global_tariff_callback",
        "texts_links.runtime_effects.owner_saved_tariff_unused",
        "texts_links.runtime_effects.global_creator_link_fallback",
        "texts_links.runtime_effects.owner_group_support_creator",
        "texts_links.runtime_effects.owner_private_start_unused",
        "texts_links.runtime_effects.global_start_button_install_fallback",
        "texts_links.runtime_effects.owner_install_bot_channel",
        "texts_links.runtime_effects.global_start_button_group_fallback",
        "texts_links.runtime_effects.owner_group_support",
        "texts_links.runtime_effects.global_start_button_group_install_fallback",
        "texts_links.runtime_effects.owner_install_guide",
        "texts_links.runtime_effects.global_primary_creator_link",
        "texts_links.runtime_effects.global_custom_start_button",
        "texts_links.runtime_effects.owner_saved_start_button_unused",
        "texts_links.runtime_effects.global_sudo_buy_link",
        "texts_links.runtime_effects.owner_saved_sudo_unused",
    ],
    # app/services/texts_links_ui.py — FIELD_SPECS.note_keys union.
    "texts_links.runtime_notes.*": [
        "texts_links.runtime_notes.developer_link_priority_note",
        "texts_links.runtime_notes.developer_pv_priority_note",
    ],
    # app/services/texts_links_ui.py — FIELD_SPECS.media_note_key values.
    "texts_links.media_runtime.*": [
        "texts_links.media_runtime.caption_only",
        "texts_links.media_runtime.media_storage_only",
    ],
    # app/services/texts_links_ui.py — field_runtime_status_key()
    # (key built in helper, then passed to t() as a variable).
    "texts_links.runtime_status.*": [
        "texts_links.runtime_status.active_global",
        "texts_links.runtime_status.storage_only",
        "texts_links.runtime_status.active_owner_override",
    ],
    # app/services/texts_links_ui.py — _field_kind_key() kinds.
    "texts_links.kind_*": [
        "texts_links.kind_text",
        "texts_links.kind_link",
    ],
    # app/handlers/dev_panel.py and app/handlers/owner_panel.py — saved
    # callback toast labels selected from start_customization_service modes.
    "texts_links.start_style.mode_*": [
        "texts_links.start_style.mode_simple",
        "texts_links.start_style.mode_advanced",
    ],
    # app/utils/ui.py — _GRP_VISIBLE_SETTING_TOGGLES keys, minus the
    # language / default_media_type / call_security branches.
    "panels.group.settings.*": [
        "panels.group.settings.security_call",
        "panels.group.settings.download_users",
        "panels.group.settings.auto_clean",
        "panels.group.settings.call_message",
        "panels.group.settings.auto_ready_call",
        "panels.group.settings.music_video",
        "panels.group.settings.call_report",
        "panels.group.settings.queue",
        "panels.group.settings.show_id",
        "panels.group.settings.show_photo",
        "panels.group.settings.show_text",
    ],
    # app/utils/ui.py — chat language normalized to fa/en.
    "panels.group.settings.language_button_*": [
        "panels.group.settings.language_button_fa",
        "panels.group.settings.language_button_en",
    ],
    # app/handlers/group_panel.py — normalize_lang output (fa/en).
    "panels.group.settings.language_name_*": [
        "panels.group.settings.language_name_fa",
        "panels.group.settings.language_name_en",
    ],
    # app/utils/ui.py — normalize_default_media_type output (audio/video).
    "panels.group.settings.default_media_button_*": [
        "panels.group.settings.default_media_button_audio",
        "panels.group.settings.default_media_button_video",
    ],
    # app/handlers/group_panel.py — default media summary (audio/video).
    "panels.group.settings.default_media_summary_*": [
        "panels.group.settings.default_media_summary_audio",
        "panels.group.settings.default_media_summary_video",
    ],
    # app/services/panel_router.py — panel_key in {developer, owner}.
    "panels.*.title": [
        "panels.developer.title",
        "panels.owner.title",
    ],
    # app/services/panel_router.py — feature_keys i18n suffixes.
    "status_summary.*": [
        "status_summary.feature_bot_enabled",
        "status_summary.feature_sudo_panel",
        "status_summary.feature_forced_membership",
        "status_summary.feature_auto_leave",
        "status_summary.feature_trial",
    ],
    # app/handlers/force_join_panel.py — verify_status guarded to
    # ("ok", "broken", "pending"); other statuses fall back to a literal.
    "admin.fm.badge_*": [
        "admin.fm.badge_ok",
        "admin.fm.badge_broken",
        "admin.fm.badge_pending",
    ],
    # app/handlers/group_panel.py — normalize_default_media_type output.
    "playback.types.*": [
        "playback.types.audio",
        "playback.types.video",
    ],
    # app/handlers/help_center.py — _ADMIN_SECTIONS + _PUBLIC_SECTIONS.
    "help.*": [
        "help.group_panel",
        "help.sudo_panel",
        "help.owner_panel",
        "help.dev_panel",
        "help.getting_started",
        "help.playback",
        "help.controls",
        "help.playlist",
        "help.radio",
        "help.downloads",
        "help.forcejoin",
        "help.troubleshoot",
        "help.about",
    ],
    # app/utils/ui.py — Help keyboard buttons.
    "help.btn.*": [
        "help.btn.promote",
        "help.btn.play",
        "help.btn.public",
        "help.btn.utility",
        "help.btn.close",
        "help.btn.play_reply",
        "help.btn.play_link",
        "help.btn.play_auto_music",
        "help.btn.play_auto_video",
        "help.btn.youtube_search",
        "help.btn.play_radio",
        "help.btn.play_tv",
        "help.btn.play_satellite",
        "help.btn.play_controls",
        "help.btn.back",
        "help.btn.public_group",
        "help.btn.public_user",
        "help.btn.promote_deputy",
        "help.btn.promote_admin",
        "help.btn.promote_vip",
        "help.btn.about",
    ],
    # app/handlers/help_center.py — _DETAIL_PAGES values.
    "help.pages.*": [
        "help.pages.play_reply",
        "help.pages.play_link",
        "help.pages.play_auto_music",
        "help.pages.play_auto_video",
        "help.pages.play_youtube",
        "help.pages.play_radio",
        "help.pages.play_serial",
        "help.pages.play_tv",
        "help.pages.play_satellite",
        "help.pages.play_controls",
        "help.pages.public_group",
        "help.pages.public_user",
        "help.pages.promote_deputy",
        "help.pages.promote_admin",
        "help.pages.promote_vip",
        "help.pages.utility",
    ],
    # app/handlers/dev_panel.py — MonthlyInvoice.status values.
    "monthly_invoice.status.*": [
        "monthly_invoice.status.pending",
        "monthly_invoice.status.sent",
        "monthly_invoice.status.paid",
        "monthly_invoice.status.failed",
        "monthly_invoice.status.cancelled",
    ],
    # app/handlers/group_panel.py — bounded section keys.
    "panels.group.install_setup.*": [
        "panels.group.install_setup.charge_menu_title",
        "panels.group.install_setup.access_menu_title",
        "panels.group.install_setup.language_menu_title",
    ],
    # app/utils/i18n.py — label() implementation; missing leaves fall back
    # to the raw value by design, so no per-value enumeration is required.
    "labels.*.*": [],
}

# First-arg strings of these calls are analytics/audit event names or
# dict lookups that share dotted notation with i18n keys; they are not
# translation keys.
_EVENT_NAME_FUNCS = {"track_event", "log_event", "trace_callback_event", "get"}

_DOTTED_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_PLACEHOLDER_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")


def load_manifest() -> dict:
    return json.loads((_I18N_DIR / "manifest.json").read_text(encoding="utf-8"))


def merge_language(lang: str, manifest: dict) -> dict[str, Any]:
    """Deep-merge all fragments of *lang* into one tree (read-only)."""
    merged: dict[str, Any] = {}
    for frag in manifest["fragments"]:
        frag_path = _I18N_DIR / lang / frag["path"]
        data = json.loads(frag_path.read_text(encoding="utf-8"))

        def _merge(base: dict, overlay: dict) -> None:
            for key, value in overlay.items():
                if isinstance(value, dict):
                    _merge(base.setdefault(key, {}), value)
                else:
                    base[key] = value

        _merge(merged, data)
    return merged


def resolve_leaf(tree: dict, key: str) -> Any:
    """Return the leaf value at dotted *key* or None if not found."""
    cur: Any = tree
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def is_string_leaf(tree: dict, key: str) -> bool:
    return isinstance(resolve_leaf(tree, key), str)


def collect_leaf_paths(tree: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in tree.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= collect_leaf_paths(value, full)
        else:
            paths.add(full)
    return paths


def _collect_registered_fragment_paths(manifest: dict) -> set[str]:
    return {str(frag["path"]).replace("\\", "/") for frag in manifest["fragments"]}


def _collect_actual_fragment_paths(lang: str) -> set[str]:
    root = _I18N_DIR / lang
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.json")
        if path.name != "manifest.json"
    }


def _placeholder_names(value: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(value))


def _joinedstr_pattern(node: ast.JoinedStr) -> str:
    """Render an f-string as a glob-like pattern ('*' per placeholder)."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("*")
    return "".join(parts)


class UsageVisitor(ast.NodeVisitor):
    """Collect t()/label() usages and dotted string literals from one file."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.literal_keys: dict[str, list[str]] = {}
        self.dynamic_patterns: dict[str, list[str]] = {}
        self.variable_sites: list[str] = []
        self.label_groups: dict[str, list[str]] = {}
        self.label_literal_keys: dict[str, list[str]] = {}
        self.dotted_literals: dict[str, list[str]] = {}
        self._event_name_strings: set[str] = set()

    def _loc(self, node: ast.AST) -> str:
        return f"{self.rel_path}:{getattr(node, 'lineno', 0)}"

    @staticmethod
    def _call_name(node: ast.Call) -> str | None:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def visit_Call(self, node: ast.Call) -> None:
        name = self._call_name(node)
        if name in _EVENT_NAME_FUNCS and node.args:
            # Walk the whole first argument: event names may be built with
            # conditional expressions, e.g. "a.b" if cond else "a.c".
            for sub in ast.walk(node.args[0]):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    self._event_name_strings.add(sub.value)
        if name == "t" and len(node.args) >= 2:
            key_arg = node.args[1]
            if isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str):
                self.literal_keys.setdefault(key_arg.value, []).append(self._loc(node))
            elif isinstance(key_arg, ast.JoinedStr):
                pattern = _joinedstr_pattern(key_arg)
                self.dynamic_patterns.setdefault(pattern, []).append(self._loc(node))
            else:
                self.variable_sites.append(self._loc(node))
        elif name == "label" and len(node.args) >= 3:
            group_arg, value_arg = node.args[1], node.args[2]
            if isinstance(group_arg, ast.Constant) and isinstance(group_arg.value, str):
                group = group_arg.value
                self.label_groups.setdefault(group, []).append(self._loc(node))
                if isinstance(value_arg, ast.Constant) and isinstance(value_arg.value, str):
                    self.label_literal_keys.setdefault(
                        f"labels.{group}.{value_arg.value}", []
                    ).append(self._loc(node))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _DOTTED_RE.fullmatch(node.value):
            self.dotted_literals.setdefault(node.value, []).append(self._loc(node))
        self.generic_visit(node)


def scan_app() -> dict[str, Any]:
    """Scan every app/**/*.py file and aggregate i18n usage data."""
    agg: dict[str, Any] = {
        "literal_keys": {},
        "dynamic_patterns": {},
        "variable_sites": [],
        "label_groups": {},
        "label_literal_keys": {},
        "dotted_literals": {},
        "event_names": set(),
        "fstring_fragments": set(),
    }
    for py in sorted(_APP_DIR.rglob("*.py")):
        rel = py.relative_to(_ROOT).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        visitor = UsageVisitor(rel)
        visitor.visit(tree)
        for attr in (
            "literal_keys",
            "dynamic_patterns",
            "label_groups",
            "label_literal_keys",
            "dotted_literals",
        ):
            for key, locs in getattr(visitor, attr).items():
                agg[attr].setdefault(key, []).extend(locs)
        agg["variable_sites"].extend(visitor.variable_sites)
        agg["event_names"] |= visitor._event_name_strings
        # f-string constant fragments are also visited as Constant nodes;
        # exclude their prefixes from the suspect-literal report.
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        agg["fstring_fragments"].add(value.value)
    return agg


def run_checks(verbose: bool = False) -> int:
    manifest = load_manifest()
    languages: list[str] = manifest["languages"]
    trees = {lang: merge_language(lang, manifest) for lang in languages}
    top_namespaces = {ns for tree in trees.values() for ns in tree}

    usage = scan_app()
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Literal t() keys must exist as string leaves in every language.
    for key, locs in sorted(usage["literal_keys"].items()):
        for lang in languages:
            leaf = resolve_leaf(trees[lang], key)
            if leaf is None:
                errors.append(f"missing [{lang}] t() key: {key}  ({locs[0]})")
            elif not isinstance(leaf, str):
                errors.append(f"invalid non-string [{lang}] t() leaf: {key}  ({locs[0]})")

    # 2. Literal label() groups and group+value pairs.
    for group, locs in sorted(usage["label_groups"].items()):
        for lang in languages:
            subtree = resolve_leaf(trees[lang], f"labels.{group}")
            if not isinstance(subtree, dict) or not subtree:
                errors.append(
                    f"missing/empty [{lang}] labels group: labels.{group}  ({locs[0]})"
                )
    for key, locs in sorted(usage["label_literal_keys"].items()):
        for lang in languages:
            if not is_string_leaf(trees[lang], key):
                errors.append(f"missing [{lang}] label() key: {key}  ({locs[0]})")

    # 3. Dynamic f-string patterns must be allowlisted; expected keys must exist.
    for pattern, locs in sorted(usage["dynamic_patterns"].items()):
        if pattern not in DYNAMIC_ALLOWLIST:
            errors.append(
                f"unregistered dynamic t() pattern: {pattern}  ({locs[0]}) — "
                "add it to DYNAMIC_ALLOWLIST with its enumerated keys"
            )
    for pattern, expected_keys in sorted(DYNAMIC_ALLOWLIST.items()):
        for key in expected_keys:
            for lang in languages:
                if not is_string_leaf(trees[lang], key):
                    errors.append(
                        f"missing [{lang}] allowlisted dynamic key: {key}  (pattern {pattern})"
                    )

    # 4. Manifest coverage for every split JSON fragment.
    registered_fragments = _collect_registered_fragment_paths(manifest)
    for lang in languages:
        actual_fragments = _collect_actual_fragment_paths(lang)
        for path in sorted(actual_fragments - registered_fragments):
            errors.append(f"unregistered [{lang}] i18n fragment: {path}")
        for path in sorted(registered_fragments - actual_fragments):
            errors.append(f"missing [{lang}] manifest fragment: {path}")

    # 5. Placeholder parity for keys that code can render.
    code_referenced_keys: set[str] = set(usage["literal_keys"])
    code_referenced_keys |= set(usage["label_literal_keys"])
    for expected_keys in DYNAMIC_ALLOWLIST.values():
        code_referenced_keys.update(expected_keys)
    for key in sorted(code_referenced_keys):
        leaves = {lang: resolve_leaf(trees[lang], key) for lang in languages}
        if not all(isinstance(value, str) for value in leaves.values()):
            continue
        placeholder_sets = {
            lang: _placeholder_names(value) for lang, value in leaves.items()
        }
        if len({frozenset(names) for names in placeholder_sets.values()}) > 1:
            rendered = ", ".join(
                f"{lang}={sorted(names)}"
                for lang, names in sorted(placeholder_sets.items())
            )
            errors.append(f"placeholder mismatch: {key}  ({rendered})")

    # 6. Variable-key call sites (informational).
    if usage["variable_sites"]:
        warnings.append(
            f"{len(usage['variable_sites'])} variable-key t() call sites "
            "(covered by allowlist families / literal key dicts)"
        )

    # 7. Suspect unresolved dotted literals inside i18n namespaces.
    suspects: dict[str, list[str]] = {}
    for literal, locs in usage["dotted_literals"].items():
        if literal in usage["event_names"]:
            continue
        if literal in usage["fstring_fragments"]:
            continue
        if literal.split(".")[0] not in top_namespaces:
            continue
        if all(resolve_leaf(trees[lang], literal) is not None for lang in trees):
            continue
        suspects[literal] = locs
    if suspects:
        warnings.append(f"{len(suspects)} unresolved i18n-like dotted literals (review)")

    # 8. Unused leaf candidates (informational only — dynamic usage exists).
    used_keys: set[str] = set(usage["literal_keys"])
    used_keys |= set(usage["label_literal_keys"])
    for expected_keys in DYNAMIC_ALLOWLIST.values():
        used_keys.update(expected_keys)
    for literal in usage["dotted_literals"]:
        used_keys.add(literal)
    fa_leaves = collect_leaf_paths(trees[languages[0]])
    unused = {
        leaf
        for leaf in fa_leaves
        if leaf not in used_keys
        and not any(leaf.startswith(f"{used}.") for used in usage["label_groups"])
        and not leaf.startswith("labels.")
    }

    # ── Report ────────────────────────────────────────────────────────────
    print(f"languages           : {', '.join(languages)}")
    print(f"literal t() keys    : {len(usage['literal_keys'])}")
    print(f"label() groups      : {len(usage['label_groups'])}")
    print(f"label() literal keys: {len(usage['label_literal_keys'])}")
    print(f"dynamic patterns    : {len(usage['dynamic_patterns'])}")
    print(f"variable call sites : {len(usage['variable_sites'])}")
    print(f"suspect literals    : {len(suspects)}")
    print(f"unused candidates   : {len(unused)} (informational)")

    if verbose:
        if suspects:
            print("\nSuspect dotted literals (not resolvable as i18n keys):")
            for literal, locs in sorted(suspects.items()):
                print(f"  {literal}  ({locs[0]})")
        if usage["dynamic_patterns"]:
            print("\nDynamic t() patterns observed:")
            for pattern, locs in sorted(usage["dynamic_patterns"].items()):
                status = "allowlisted" if pattern in DYNAMIC_ALLOWLIST else "UNREGISTERED"
                print(f"  {pattern}  [{status}]  ({locs[0]})")
        if usage["variable_sites"]:
            print("\nVariable-key t() call sites:")
            for loc in sorted(usage["variable_sites"]):
                print(f"  {loc}")
        if unused:
            print("\nUnused leaf candidates (verify before removal — dynamic use possible):")
            for leaf in sorted(unused):
                print(f"  {leaf}")

    if warnings:
        print()
        for warning in warnings:
            print(f"WARN: {warning}")

    if errors:
        print()
        for error in errors:
            print(f"ERROR: {error}")
        print(f"\nResult: FAIL ({len(errors)} errors)")
        return 1

    print("\nResult: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verbose", action="store_true", help="print full listings")
    args = parser.parse_args()
    return run_checks(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
