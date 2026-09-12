#!/usr/bin/env python
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


FLOWS: list[tuple[str, str]] = [
    ("Start as Developer sends one message", "tests/test_start_navigation_regression.py::test_start_single_message_developer_pm"),
    ("Start as Owner sends one message", "tests/test_start_navigation_regression.py::test_start_single_message_owner_pm"),
    ("Start as Sudo sends one message", "tests/test_start_navigation_regression.py::test_start_single_message_sudo_pm"),
    ("Start as Regular sends one message", "tests/test_start_navigation_regression.py::test_start_single_message_regular_pm"),
    ("Back returns to Developer role root", "tests/test_start_navigation_regression.py::test_back_returns_to_new_dev_root"),
    ("Back returns to Owner role root", "tests/test_start_navigation_regression.py::test_back_returns_to_owner_root"),
    ("Back returns to Sudo role root", "tests/test_start_navigation_regression.py::test_back_returns_to_sudo_root"),
    ("Wizard remove-sudo flow has no dead-end", "tests/test_admin_wizard_navigation.py::test_remove_sudo_flow_no_dead_end"),
    ("Wizard cancel returns previous menu", "tests/test_admin_wizard_navigation.py::test_cancel_returns_to_previous_menu"),
    ("Wizard home returns role root", "tests/test_admin_wizard_navigation.py::test_role_root_navigation_after_done"),
    ("Force-Join dev add/list/remove flow", "tests/test_force_join_admin_reports.py::test_force_join_add_remove_list_dev"),
    ("Force-Join owner list flow", "tests/test_force_join_admin_reports.py::test_force_join_list_owner"),
    ("Force-Join denied flow returns nav", "tests/test_force_join_admin_reports.py::test_force_join_denied_sudo_if_applicable"),
    ("Force-Join cache invalidation", "tests/test_force_join_admin_reports.py::test_force_join_cache_invalidation"),
    ("Developer group report paging", "tests/test_force_join_admin_reports.py::test_dev_list_groups_paginated"),
    ("Developer no-credit report data", "tests/test_force_join_admin_reports.py::test_dev_list_no_credit"),
    ("Developer renewal report data", "tests/test_force_join_admin_reports.py::test_dev_list_renewal"),
    ("Leave flow confirm keyboard", "tests/test_force_join_admin_reports.py::test_dev_leave_group_confirm"),
    ("Texts hub opens with navigation", "tests/test_dev_texts_links_editor.py::test_dev_texts_hub_opens"),
    ("Texts set-text persists", "tests/test_dev_texts_links_editor.py::test_set_start_text_updates_db_and_returns_buttons"),
    ("Texts set-media persists", "tests/test_dev_texts_links_editor.py::test_set_start_media_saves_file_id_and_caption"),
    ("Texts cancel returns previous page", "tests/test_dev_texts_links_editor.py::test_cancel_returns_to_previous_page"),
    ("Force-Join enforcement blocks regular user", "tests/test_ui_behavioral_e2e.py::test_force_join_enforcement_blocks_regular_user"),
    ("Dev lists match seeded data + paging", "tests/test_ui_behavioral_e2e.py::test_dev_lists_match_db_seed_and_paging"),
    ("Leave execute updates install state", "tests/test_ui_behavioral_e2e.py::test_leave_flow_confirm_and_db_update"),
    ("Helper add flow via import session", "tests/test_helper_add_flow_e2e.py::test_hlp_home_to_import_add_success_e2e"),
    ("Noop callback page indicator safety", "tests/test_noop_and_navigation_media.py::test_noop_callback_answers_without_edit"),
    ("Broadcast confirm final summary", "tests/test_broadcast_confirm_summary.py::test_broadcast_confirm_now_emits_single_final_summary"),
]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.setdefault("TEST_MODE", "1")

    total = len(FLOWS)
    print(f"Running UI smoke flow suite ({total} flows, fully mocked, no Telegram network).")
    for idx, (label, _) in enumerate(FLOWS, start=1):
        print(f"{idx:02d}. {label}")

    cmd = [sys.executable, "-m", "pytest", "-q", *[node_id for _, node_id in FLOWS]]
    result = subprocess.run(cmd, cwd=repo_root, env=env, check=False)

    if result.returncode == 0:
        print(f"Smoke suite passed: {total}/{total} flows.")
    else:
        print("Smoke suite failed. Review pytest output above.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
