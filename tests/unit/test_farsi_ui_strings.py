"""Focused regression checks for Persian UI string resources."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

_ASCII_WORD = re.compile(r"^[A-Za-z0-9 .,!?:;'\-]+$")


def _leaf_keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= _leaf_keys(value, full)
        else:
            keys.add(full)
    return keys


def test_fa_json_is_valid() -> None:
  data = load_fa_i18n()
  assert isinstance(data, dict)


def test_fa_en_key_parity() -> None:
    fa = load_fa_i18n()
    en = load_en_i18n()
    fa_keys = _leaf_keys(fa)
    en_keys = _leaf_keys(en)
    assert fa_keys == en_keys


@pytest.mark.parametrize(
    "key",
    [
        "force_join_mgmt.menu_title",
        "reports.groups_title",
        "common.buttons.back",
        "blacklist_mgmt.list_title",
        "panels.developer.summary_active_installs",
        "texts_links.group_storage_only",
        "texts_links.runtime_status.storage_only",
        "texts_links.media_runtime.caption_only",
        "texts_links.owner_start_text_developer_only",
        "texts_links.owner_read_only_global",
        "playback_cmd.provide_source",
        "playback_cmd.replay_no_media",
        "playback_cmd.audio_disabled_in_group",
        "playback_cmd.file_disabled_in_group",
        "call_security.feature_membership_age",
        "call_security.btn_membership_age",
    ],
)
def test_critical_fa_keys_are_persian(key: str) -> None:
    from app.utils.i18n import t

    value = t("fa", key)
    assert "[missing:" not in value
    assert not _ASCII_WORD.fullmatch(value.strip()), f"{key} still looks English-only: {value!r}"


def test_label_helper_maps_broadcast_mode() -> None:
    from app.utils.i18n import label

    assert label("fa", "broadcast_mode", "send") == "ارسال مستقیم"
    assert label("fa", "broadcast_scope", "users") == "کاربران"


def test_common_unknown_fallback_key() -> None:
    from app.utils.i18n import t

    assert t("fa", "common.labels.unknown") == "نامشخص"
