from __future__ import annotations

import re
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.utils.redis_keys import (
    bcw_state_key,
    helper_otp_state_key,
    helper_proxy_state_key,
    wizard_return_key,
)
from app.utils.ui import CB, KeyboardFactory

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _hlp_patterns() -> list[re.Pattern[str]]:
    return [
        re.compile(rf"^{re.escape(CB['HLP_HOME'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_ADD'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_LIST'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_ROTATE_KEY'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_HEALTH_CHECK'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_STATS'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_ADD_OTP'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_IMPORT_SESSION'])}$"),
        re.compile(rf"^{re.escape(CB['HLP_DETAIL_PREFIX'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_SET_PROXY_PREFIX'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_ENABLE'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_DISABLE'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_QUARANTINE'])}\d+$"),
        re.compile(rf"^{re.escape(CB['HLP_UNQUARANTINE'])}\d+$"),
        re.compile(r"^hlp:proxy:cancel$"),
        re.compile(r"^hlp:otp:cancel$"),
        re.compile(r"^hlp:otp:back:phone$"),
        re.compile(r"^hlp:otp:back:code$"),
        re.compile(r"^hlp:imp:back:session$"),
        re.compile(r"^hlp:imp:back:phone$"),
        re.compile(r"^hlp:imp:back:max_calls$"),
    ]


def _sample_helper_callbacks() -> set[str]:
    from app.handlers.helper_otp_wizard import _nav_kb, _success_kb

    keyboards = [
        KeyboardFactory.helper_home("en", 1, 0, 0),
        KeyboardFactory.helper_add_menu("en"),
        KeyboardFactory.helper_detail("en", 5, "active"),
        KeyboardFactory.helper_detail("en", 5, "disabled"),
        KeyboardFactory.helper_detail("en", 5, "quarantined"),
        _success_kb(5),
        _nav_kb("hlp:otp:back:phone"),
        _nav_kb("hlp:otp:back:code"),
        _nav_kb("hlp:imp:back:session"),
        _nav_kb("hlp:imp:back:phone"),
        _nav_kb("hlp:imp:back:max_calls"),
    ]
    callbacks: set[str] = set()
    for kb in keyboards:
        callbacks.update(_callback_data_set(kb))
    callbacks.add("hlp:proxy:cancel")
    return {cb for cb in callbacks if cb.startswith("hlp:")}


def test_helper_callbacks_have_registered_patterns_and_no_overlap():
    callbacks = _sample_helper_callbacks()
    patterns = _hlp_patterns()

    for cb in sorted(callbacks):
        matched = [rx.pattern for rx in patterns if rx.match(cb)]
        assert matched, f"no handler pattern for callback: {cb}"
        assert len(matched) == 1, f"ambiguous callback route for {cb}: {matched}"


def test_helper_panel_registers_hlp_list_and_hlp_health_handlers():
    from app.handlers import helper_panel

    bot = _RecorderBot()
    helper_panel.register(bot, None)
    names = {fn.__name__ for fn in bot.callback_handlers}

    assert "hlp_list" in names
    assert "hlp_health" in names


def test_helper_panel_is_in_handler_manifest():
    import app.handlers as handlers_package
    from app.handlers import helper_otp_wizard, helper_panel

    assert helper_panel in handlers_package._MODULES
    assert helper_otp_wizard in handlers_package._MODULES


def test_helper_add_registers_in_dedicated_panel_group():
    from app.handlers import helper_panel
    from app.handlers.priority import PANEL_CALLBACK_GROUP

    bot = _RecorderBot()
    helper_panel.register(bot, None)
    groups_by_name = {
        fn.__name__: meta["kwargs"].get("group", 0)
        for fn, meta in zip(bot.callback_handlers, bot.callback_meta, strict=True)
    }

    assert CB["HLP_ADD"] == "hlp:add"
    assert groups_by_name["hlp_add"] == PANEL_CALLBACK_GROUP


def test_helper_add_methods_register_in_dedicated_panel_group():
    from app.handlers import helper_otp_wizard
    from app.handlers.priority import PANEL_CALLBACK_GROUP

    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    groups_by_name = {
        fn.__name__: meta["kwargs"].get("group", 0)
        for fn, meta in zip(bot.callback_handlers, bot.callback_meta, strict=True)
    }

    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"
    assert CB["HLP_IMPORT_SESSION"] == "hlp:import"
    assert groups_by_name["otp_start"] == PANEL_CALLBACK_GROUP
    assert groups_by_name["import_start"] == PANEL_CALLBACK_GROUP


def test_startup_diagnostics_probe_helper_add_routes():
    from app.handlers.callback_route_diag import STARTUP_CALLBACK_ROUTE_SAMPLES

    assert ("hlp:add", CB["HLP_ADD"]) in STARTUP_CALLBACK_ROUTE_SAMPLES
    assert ("hlp:add:otp", CB["HLP_ADD_OTP"]) in STARTUP_CALLBACK_ROUTE_SAMPLES
    assert ("hlp:import", CB["HLP_IMPORT_SESSION"]) in STARTUP_CALLBACK_ROUTE_SAMPLES
    for callback in ("hlp:list", "hlp:health", "hlp:rotkey", "hlp:stats"):
        assert (callback, callback) in STARTUP_CALLBACK_ROUTE_SAMPLES


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.callback_meta: list[dict] = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def decorator(fn):
            self.callback_handlers.append(fn)
            self.callback_meta.append({"args": args, "kwargs": kwargs})
            return fn

        return decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return decorator


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
async def test_cancel_and_wz_home_clear_helper_wizard_states():
    from app.services import wizard_ui
    from app.handlers import callbacks

    user_id = 4242
    redis = AsyncMock()
    redis.delete = AsyncMock()
    client = AsyncMock()
    client.stop_listening = AsyncMock()

    with (
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.wizard_ui.resolve_navigation_payload", AsyncMock(return_value=("ok", None))),
    ):
        await wizard_ui.cancel_and_resolve(
            client,
            user_id,
            user_id,
            "private",
            lang="en",
            return_to=wizard_ui.TOKEN_HELPER_HOME,
        )

    delete_args = redis.delete.await_args.args
    assert helper_otp_state_key(user_id) in delete_args
    assert helper_proxy_state_key(user_id) in delete_args
    assert bcw_state_key(user_id) in delete_args
    assert wizard_return_key(user_id) in delete_args

    bot = _RecorderBot()
    callbacks.register(bot, None)
    wz_home = _handler_by_name(bot.callback_handlers, "wz_home")

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Dev"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()) as clear_mock,
        patch("app.handlers.callbacks.resolve_navigation_payload", AsyncMock(return_value=("home", MagicMock()))),
    ):
        cb_client = AsyncMock()
        await wz_home(cb_client, query)

    clear_mock.assert_awaited_once_with(ANY, user_id, user_id)
