"""Modern device fingerprint pool for helper anti-ban protection.

Each fingerprint mimics a real high-end Android device with a plausible
Telegram client version.  A fingerprint is assigned ONCE at helper
creation and reused on every subsequent login — never randomized.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceFingerprint:
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str = "en-US"


_POOL: list[DeviceFingerprint] = [
    DeviceFingerprint("Samsung SM-S928B", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Samsung SM-S926B", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Samsung SM-S921B", "Android 14", "10.14.4", "en"),
    DeviceFingerprint("Samsung SM-F946B", "Android 14", "10.14.3", "en"),
    DeviceFingerprint("Samsung SM-S918B", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Google Pixel 8 Pro", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Google Pixel 8", "Android 14", "10.14.4", "en"),
    DeviceFingerprint("Google Pixel 8a", "Android 14", "10.14.3", "en"),
    DeviceFingerprint("Google Pixel 7 Pro", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Xiaomi 14 Pro", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Xiaomi 14", "Android 14", "10.14.4", "en"),
    DeviceFingerprint("Xiaomi 13 Pro", "Android 14", "10.14.3", "en"),
    DeviceFingerprint("Xiaomi 13T Pro", "Android 14", "10.14.2", "en"),
    DeviceFingerprint("OnePlus 12", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("OnePlus 11 5G", "Android 14", "10.14.4", "en"),
    DeviceFingerprint("OnePlus Open", "Android 14", "10.14.3", "en"),
    DeviceFingerprint("Sony Xperia 1 VI", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Sony Xperia 1 V", "Android 14", "10.14.3", "en"),
    DeviceFingerprint("OPPO Find X7 Ultra", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("OPPO Find X6 Pro", "Android 14", "10.14.2", "en"),
    DeviceFingerprint("Motorola Edge 50 Ultra", "Android 14", "10.14.4", "en"),
    DeviceFingerprint("Nothing Phone (2)", "Android 14", "10.14.3", "en"),
    DeviceFingerprint("ASUS ROG Phone 8 Pro", "Android 14", "10.14.5", "en"),
    DeviceFingerprint("Huawei Mate 60 Pro", "HarmonyOS 4.0", "10.14.1", "en"),
    DeviceFingerprint("Samsung SM-A556B", "Android 14", "10.14.2", "fa"),
    DeviceFingerprint("Samsung SM-A356B", "Android 14", "10.14.1", "fa"),
    DeviceFingerprint("Xiaomi Redmi Note 13 Pro+", "Android 14", "10.14.3", "fa"),
    DeviceFingerprint("Xiaomi POCO X6 Pro", "Android 14", "10.14.2", "fa"),
    DeviceFingerprint("Samsung SM-S928B", "Android 14", "10.14.5", "fa"),
    DeviceFingerprint("Google Pixel 8 Pro", "Android 14", "10.14.5", "fa"),
]


def _profile_candidates() -> list[Path]:
    app_dir = Path(__file__).resolve().parents[1]
    repo_dir = app_dir.parent
    return [
        repo_dir / "app_information.json",
        app_dir / "app_information.json",
        app_dir / "resources" / "app_information.json",
    ]


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _profile_from_mapping(entry: object) -> DeviceFingerprint | None:
    if not isinstance(entry, dict):
        return None
    device_model = _clean_text(entry.get("device_model"))
    system_version = _clean_text(entry.get("system_version"))
    app_version = _clean_text(entry.get("app_version"))
    if not device_model or not system_version or not app_version:
        return None
    lang_code = _clean_text(entry.get("lang_code")) or "en"
    system_lang_code = _clean_text(entry.get("system_lang_code")) or "en-US"
    return DeviceFingerprint(
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code=lang_code,
        system_lang_code=system_lang_code,
    )


def load_device_fingerprints(path: str | Path | None = None) -> list[DeviceFingerprint]:
    """Load optional app information profiles from JSON.

    The file is optional and must contain either a JSON list of profile objects or
    an object with a ``profiles`` list. Invalid entries are skipped.
    """
    selected: Path | None
    if path is not None:
        selected = Path(path)
    else:
        selected = next((candidate for candidate in _profile_candidates() if candidate.is_file()), None)
    if selected is None:
        return []

    try:
        raw = json.loads(selected.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "device fingerprint app information ignored path=%s error=%s",
            selected,
            type(exc).__name__,
        )
        return []

    if isinstance(raw, dict):
        raw = raw.get("profiles")
    if not isinstance(raw, list):
        logger.warning("device fingerprint app information ignored path=%s error=invalid_schema", selected)
        return []

    profiles: list[DeviceFingerprint] = []
    seen: set[tuple[str, str, str]] = set()
    for profile in (_profile_from_mapping(entry) for entry in raw):
        if profile is None:
            continue
        dedupe_key = (profile.device_model, profile.system_version, profile.app_version)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        profiles.append(profile)
    if not profiles:
        logger.warning("device fingerprint app information has no valid profiles path=%s", selected)
    return profiles


def _runtime_pool() -> list[DeviceFingerprint]:
    return load_device_fingerprints() or _POOL


def pick_random_fingerprint() -> DeviceFingerprint:
    """Select a random fingerprint for a new helper account."""
    return random.choice(_runtime_pool())


def get_pool_size() -> int:
    return len(_runtime_pool())
