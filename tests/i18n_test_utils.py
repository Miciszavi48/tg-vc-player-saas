"""Shared helpers for tests that need the merged split i18n tree."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = ROOT / "app" / "resources" / "i18n"
MANIFEST_PATH = I18N_DIR / "manifest.json"


def _deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def build_merged_i18n_tree(lang: str) -> dict:
    """Deep-merge all manifest fragments for *lang* into one dict."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    merged: dict = {}
    for frag_spec in manifest["fragments"]:
        frag_file = I18N_DIR / lang / frag_spec["path"]
        if not frag_file.is_file():
            raise FileNotFoundError(f"Missing fragment: {frag_file}")
        _deep_merge(merged, json.loads(frag_file.read_text(encoding="utf-8")))
    return merged


def load_fa_i18n() -> dict:
    """Return merged FA i18n tree from split fragments."""
    return build_merged_i18n_tree("fa")


def load_en_i18n() -> dict:
    """Return merged EN i18n tree from split fragments."""
    return build_merged_i18n_tree("en")
