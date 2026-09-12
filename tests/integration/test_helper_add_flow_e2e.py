from __future__ import annotations

import json
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.utils.redis_keys import helper_otp_state_key
from app.utils.ui import CB

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                removed += 1
                self.store.pop(key, None)
        return removed


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def decorator(fn):
            self.callback_handlers.append(fn)
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


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _make_query(data: str, user_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Dev"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _make_text_message(text: str, user_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text=text,
        reply=AsyncMock(),
        chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
    )


def _session_factory(*, assigned_id: int = 55):
    added: list[object] = []
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_ctx)

    def _add(obj):
        added.append(obj)

    async def _flush():
        if added and getattr(added[-1], "id", None) is None:
            setattr(added[-1], "id", assigned_id)

    duplicate_result = MagicMock()
    duplicate_result.scalar_one_or_none.return_value = None

    existing_result = MagicMock()
    existing_scalars = MagicMock()
    existing_scalars.all.return_value = []
    existing_result.scalars.return_value = existing_scalars

    session.execute = AsyncMock(side_effect=[duplicate_result, existing_result])
    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock(side_effect=_flush)
    return session, added


@pytest.mark.asyncio
async def test_hlp_home_to_import_add_success_e2e():
    from app.handlers import helper_otp_wizard, helper_panel

    dev_id = settings.DEVELOPER_ID
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    helper_otp_wizard.register(bot, None)

    hlp_home = _handler_by_name(bot.callback_handlers, "hlp_home")
    hlp_add = _handler_by_name(bot.callback_handlers, "hlp_add")
    import_start = _handler_by_name(bot.callback_handlers, "import_start")
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")

    redis = _FakeRedis()
    session, added_rows = _session_factory(assigned_id=55)
    fake_client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )

    with (
        patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_panel._get_counts", AsyncMock(return_value=(0, 0, 0))),
        patch("app.handlers.helper_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.remember_return_token", AsyncMock()),
        patch(
            "app.handlers.helper_otp_wizard._verify_import_session",
            AsyncMock(return_value=(501, "helper501", "Helper 501", "+989123456789")),
        ),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-session-501"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-session-501"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="AQB" + "x" * 40),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", AsyncMock()),
    ):
        q_home = _make_query(CB["HLP_HOME"], dev_id)
        await hlp_home(fake_client, q_home)
        home_kb = q_home.message.edit_text.await_args.kwargs["reply_markup"]
        assert CB["HLP_ADD"] in _callback_data_set(home_kb)

        q_add = _make_query(CB["HLP_ADD"], dev_id)
        await hlp_add(fake_client, q_add)
        add_kb = q_add.message.edit_text.await_args.kwargs["reply_markup"]
        add_cbs = _callback_data_set(add_kb)
        assert CB["HLP_ADD_OTP"] in add_cbs
        assert CB["HLP_IMPORT_SESSION"] in add_cbs

        q_import = _make_query(CB["HLP_IMPORT_SESSION"], dev_id)
        await import_start(fake_client, q_import)
        state_raw = await redis.get(helper_otp_state_key(dev_id))
        assert state_raw is not None
        assert json.loads(state_raw)["step"] == "awaiting_import_session"

        msg_session = _make_text_message("AQB" + "x" * 40, dev_id)
        await otp_text_handler(fake_client, msg_session)
        state_raw = await redis.get(helper_otp_state_key(dev_id))
        assert state_raw is not None
        import_state = json.loads(state_raw)
        assert import_state["step"] == "awaiting_import_phone"
        assert "import_session" not in import_state
        assert import_state.get("import_session_enc") == "enc-session-501"

        msg_phone = _make_text_message("+989123456789", dev_id)
        await otp_text_handler(fake_client, msg_phone)
        state_raw = await redis.get(helper_otp_state_key(dev_id))
        assert state_raw is not None
        assert json.loads(state_raw)["step"] == "awaiting_import_max_calls"

        msg_calls = _make_text_message("12", dev_id)
        await otp_text_handler(fake_client, msg_calls)
        state_raw = await redis.get(helper_otp_state_key(dev_id))
        assert state_raw is not None
        assert json.loads(state_raw)["step"] == "awaiting_import_max_joins"

        msg_joins = _make_text_message("240", dev_id)
        await otp_text_handler(fake_client, msg_joins)

    assert added_rows, "helper row was not inserted"
    helper = added_rows[0]
    assert helper.phone == "+989123456789"
    assert helper.tg_user_id == 501
    assert helper.session_string_enc == "enc-session-501"
    assert helper.session_fingerprint == "fp-session-501"
    assert helper.max_concurrent_calls == 12
    assert helper.max_joins_per_hour == 240

    msg_joins.reply.assert_awaited()
    final_kb = msg_joins.reply.await_args.kwargs["reply_markup"]
    final_cbs = _callback_data_set(final_kb)
    assert f"{CB['HLP_DETAIL_PREFIX']}55" in final_cbs
    assert f"{CB['HLP_SET_PROXY_PREFIX']}55" in final_cbs
    assert "wz:back:helper_home" in final_cbs
    assert CB["WZ_HOME"] in final_cbs
