from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_deploy_zip.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_deploy_zip", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_deploy_zip"] = module
    spec.loader.exec_module(module)
    return module


def test_env_backups_are_never_packaged() -> None:
    mod = _load_module()

    blocked = [
        "app/config.env.bak.20260606185201",
        "app/config.env.bak.before_real_values.20260605-074232",
        "config.env.bak.20260606185201",
        ".env.local",
    ]

    for rel in blocked:
        assert mod.should_exclude_file(REPO_ROOT / rel)


def test_env_examples_remain_packaged() -> None:
    mod = _load_module()

    allowed = [
        "app/config.env.example",
        "app/config.env.disposable.example",
        "app/config.env.test.example",
        "config.env.example",
    ]

    for rel in allowed:
        assert not mod.should_exclude_file(REPO_ROOT / rel)
