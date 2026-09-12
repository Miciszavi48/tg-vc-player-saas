import json
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from loguru import logger


class I18nResourceError(Exception):
    """Fatal i18n resource loading error (missing manifest, fragment, or collision)."""


def _collect_leaf_paths(data: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= _collect_leaf_paths(value, full)
        else:
            paths.add(full)
    return paths


def _deep_merge_strict(base: dict, overlay: dict, path: str = "") -> None:
    """Merge *overlay* into *base*, raising on duplicate leaf paths."""
    for key, value in overlay.items():
        full_key = f"{path}.{key}" if path else key
        if key in base:
            if isinstance(base[key], dict) and isinstance(value, dict):
                _deep_merge_strict(base[key], value, full_key)
                continue
            raise I18nResourceError(
                f"duplicate leaf path '{full_key}' across i18n fragments"
            )
        if isinstance(value, dict):
            base[key] = {}
            _deep_merge_strict(base[key], value, full_key)
        else:
            base[key] = value


class TextService:
    """Loads localized strings from split i18n fragments only.

    Requires ``app/resources/i18n/manifest.json`` and all listed fragment
    files for each language. There is no legacy monolith fallback.

    Public API: ``texts``, ``t()``, ``label()``, ``AUTO_LANG``, ``reload()``.
    """

    def __init__(self, i18n_dir: str | Path | None = None):
        if i18n_dir is None:
            self._i18n_dir = (
                Path(__file__).resolve().parent.parent / "resources" / "i18n"
            )
        else:
            self._i18n_dir = Path(i18n_dir)

        self._manifest_path = self._i18n_dir / "manifest.json"
        self._manifest: dict | None = None
        self.cache: dict[str, dict[str, Any]] = {}

    def _get_manifest(self) -> dict:
        if self._manifest is not None:
            return self._manifest

        if not self._manifest_path.is_file():
            raise I18nResourceError(
                f"i18n manifest not found: {self._manifest_path}"
            )

        try:
            raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise I18nResourceError(
                f"i18n manifest invalid JSON: {self._manifest_path} ({exc})"
            ) from exc

        if not isinstance(raw, dict):
            raise I18nResourceError(
                f"i18n manifest must be a JSON object: {self._manifest_path}"
            )

        fragments = raw.get("fragments")
        languages = raw.get("languages")
        if not isinstance(fragments, list) or not fragments:
            raise I18nResourceError("i18n manifest missing non-empty 'fragments'")
        if not isinstance(languages, list) or not languages:
            raise I18nResourceError("i18n manifest missing non-empty 'languages'")

        self._manifest = raw
        return self._manifest

    def _load_fragments(self, lang: str) -> dict[str, Any]:
        """Load and deep-merge all fragment files for *lang*."""
        manifest = self._get_manifest()
        lang_dir = self._i18n_dir / lang
        if not lang_dir.is_dir():
            raise I18nResourceError(
                f"i18n fragment directory not found for lang={lang}: {lang_dir}"
            )

        merged: dict[str, Any] = {}
        seen_paths: dict[str, str] = {}

        for frag_spec in manifest["fragments"]:
            if not isinstance(frag_spec, dict) or "path" not in frag_spec:
                raise I18nResourceError(
                    f"i18n manifest fragment entry missing 'path': {frag_spec!r}"
                )
            rel_path: str = frag_spec["path"]
            frag_file = lang_dir / rel_path
            if not frag_file.is_file():
                raise I18nResourceError(
                    f"i18n fragment missing for lang={lang}: {rel_path}"
                )
            try:
                frag_data = json.loads(frag_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise I18nResourceError(
                    f"i18n fragment invalid JSON ({lang}/{rel_path}): {exc}"
                ) from exc
            if not isinstance(frag_data, dict):
                raise I18nResourceError(
                    f"i18n fragment must be a JSON object ({lang}/{rel_path})"
                )

            for leaf_path in _collect_leaf_paths(frag_data):
                if leaf_path in seen_paths:
                    raise I18nResourceError(
                        f"duplicate leaf path '{leaf_path}' in '{seen_paths[leaf_path]}' "
                        f"and '{rel_path}' (lang={lang})"
                    )
                seen_paths[leaf_path] = rel_path

            _deep_merge_strict(merged, frag_data)

        if not merged:
            raise I18nResourceError(f"i18n merged tree empty for lang={lang}")

        return merged

    def load(self, lang: str) -> dict[str, Any]:
        if lang in self.cache:
            return self.cache[lang]

        data = self._load_fragments(lang)
        logger.debug(f"i18n: loaded {lang} from split fragments")
        self.cache[lang] = data
        return data

    def reload(self, lang: str | None = None) -> None:
        """Clear the cache so the next ``load()`` re-reads from disk."""
        if lang:
            self.cache.pop(lang, None)
            self._manifest = None
        else:
            self.cache.clear()
            self._manifest = None

    def t(self, lang: str, key: str, **kwargs) -> str:
        data = self.load(lang)
        cur: Any = data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return f"[missing:{key}]"
            cur = cur[part]
        if not isinstance(cur, str):
            return f"[invalid:{key}]"
        try:
            return cur.format(**kwargs)
        except Exception:
            return cur


AUTO_LANG = "__auto__"
_SUPPORTED_LANGS = {"fa", "en"}
_CURRENT_LANG: ContextVar[str] = ContextVar("current_lang", default="fa")


def normalize_lang(lang: str | None) -> str:
    value = (lang or "fa").strip().lower()
    if value in _SUPPORTED_LANGS:
        return value
    return "fa"


def set_current_lang(lang: str | None) -> Token:
    return _CURRENT_LANG.set(normalize_lang(lang))


def reset_current_lang(token: Token | None) -> None:
    if token is not None:
        _CURRENT_LANG.reset(token)


def get_current_lang() -> str:
    return normalize_lang(_CURRENT_LANG.get())


def get_chat_lang(chat_settings) -> str:
    return normalize_lang(getattr(chat_settings, "language", None))


# Singleton instance
texts = TextService()


def t(lang: str, key: str, **kwargs) -> str:
    runtime_lang = get_current_lang()
    if lang == AUTO_LANG:
        resolved_lang = runtime_lang
    else:
        resolved_lang = normalize_lang(lang)
        # Legacy handlers hardcoded to "fa" should still respect runtime language.
        if resolved_lang == "fa" and runtime_lang != "fa":
            resolved_lang = runtime_lang
    return texts.t(resolved_lang, key, **kwargs)


def label(lang: str, group: str, value: str | None) -> str:
    """Resolve a localized display label for an internal enum/token value.

    Args:
        lang: Language code.
        group: Label group under ``labels.*`` in string resources.
        value: Raw internal value; falls back to itself when unmapped.

    Returns:
        Localized label when defined, otherwise the original value or empty string.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    out = t(lang, f"labels.{group}.{raw}")
    if out.startswith("[missing:"):
        return raw
    return out
