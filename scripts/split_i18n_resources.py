"""
Validate split i18n resources under app/resources/i18n/.

Usage:
    python scripts/split_i18n_resources.py            # read-only validation (default)
    python scripts/split_i18n_resources.py --check    # same as default
    python scripts/split_i18n_resources.py --check --verbose

This script never writes files. Legacy monolith generation from
app/resources/strings/ has been removed; split fragments are the only source.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = ROOT / "app" / "resources" / "i18n"
MANIFEST_PATH = I18N_DIR / "manifest.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_leaves(data: dict, prefix: str = "") -> dict[str, object]:
    """Flatten *data* into a {dotted.leaf.path: value} mapping."""
    leaves: dict[str, object] = {}
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.update(collect_leaves(value, full))
        else:
            leaves[full] = value
    return leaves


def collect_leaf_paths(data: dict, prefix: str = "") -> set[str]:
    return set(collect_leaves(data, prefix))


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge *overlay* into *base* (overlay wins on leaf conflicts)."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def merge_language_tree(manifest: dict, lang: str) -> tuple[dict, list[str], list[str]]:
    """Merge fragments for *lang*; return (tree, missing_fragments, collisions)."""
    merged: dict = {}
    seen_paths: dict[str, str] = {}
    missing_fragments: list[str] = []
    collisions: list[str] = []

    for frag_spec in manifest["fragments"]:
        rel_path: str = frag_spec["path"]
        frag_file = I18N_DIR / lang / rel_path
        if not frag_file.is_file():
            missing_fragments.append(rel_path)
            continue
        frag_data = load_json(frag_file)
        for path in collect_leaf_paths(frag_data):
            if path in seen_paths:
                collisions.append(f"{path} ({seen_paths[path]} vs {rel_path})")
            else:
                seen_paths[path] = rel_path
        deep_merge(merged, frag_data)

    return merged, missing_fragments, collisions


def run_check(manifest: dict, verbose: bool) -> int:
    """Read-only validation of split i18n resources. Returns exit code."""
    languages: list[str] = manifest["languages"]
    fragments: list[dict] = manifest["fragments"]
    failed = False
    split_leaf_paths: dict[str, set[str]] = {}

    if not fragments:
        print("[FAIL] manifest 'fragments' is empty")
        return 1

    for lang in languages:
        print(f"\n=== Checking language: {lang} ===")
        lang_dir = I18N_DIR / lang
        if not lang_dir.is_dir():
            print(f"  [FAIL] language directory not found: {lang_dir}")
            failed = True
            continue

        merged, missing_fragments, collisions = merge_language_tree(manifest, lang)
        split_leaves = collect_leaves(merged)
        split_leaf_paths[lang] = set(split_leaves)

        non_string_leaves = [
            k for k, v in split_leaves.items() if not isinstance(v, str)
        ]

        print(f"  split leaves  : {len(split_leaves)}")
        print(f"  collisions    : {len(collisions)}")
        print(f"  missing frags : {len(missing_fragments)}")
        print(f"  non-string    : {len(non_string_leaves)}")

        if missing_fragments:
            failed = True
            for p in missing_fragments:
                print(f"  [FAIL] fragment missing: {p}")
        if collisions:
            failed = True
            for c in collisions if verbose else collisions[:10]:
                print(f"  [FAIL] duplicate leaf path: {c}")
        if non_string_leaves:
            failed = True
            for k in non_string_leaves if verbose else non_string_leaves[:10]:
                print(f"  [FAIL] non-string leaf: {k}")
        if not split_leaves:
            failed = True
            print("  [FAIL] merged tree has zero leaves")

    if len(split_leaf_paths) >= 2:
        langs = sorted(split_leaf_paths)
        base_lang = langs[0]
        for other in langs[1:]:
            only_base = sorted(split_leaf_paths[base_lang] - split_leaf_paths[other])
            only_other = sorted(split_leaf_paths[other] - split_leaf_paths[base_lang])
            if only_base or only_other:
                failed = True
                print(f"\n[FAIL] {base_lang}/{other} parity mismatch:")
                for k in (only_base if verbose else only_base[:10]):
                    print(f"  only in {base_lang}: {k}")
                for k in (only_other if verbose else only_other[:10]):
                    print(f"  only in {other}: {k}")
            else:
                print(f"\n{base_lang}/{other} leaf parity: OK")

    print(f"\nResult: {'FAIL' if failed else 'OK'}")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate split i18n resources (read-only).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate split fragments (default behavior; kept for CI compatibility).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full mismatch lists.",
    )
    args = parser.parse_args()

    if not MANIFEST_PATH.is_file():
        print(f"[ERROR] manifest not found: {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)

    try:
        manifest = load_json(MANIFEST_PATH)
    except json.JSONDecodeError as exc:
        print(f"[ERROR] invalid manifest JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    sys.exit(run_check(manifest, verbose=args.verbose))


if __name__ == "__main__":
    main()
