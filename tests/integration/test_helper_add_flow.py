from __future__ import annotations

import sys
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.utils.i18n import t

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


def _message(user_id: int = 42, text: str = ""):
    msg = SimpleNamespace()
    msg.from_user = SimpleNamespace(id=user_id)
    msg.text = text
    msg.reply = AsyncMock()
    return msg


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _session_factory(
    *,
    duplicate_identity: object | None = None,
    existing_helpers: list[object] | None = None,
    assigned_id: int = 77,
):
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

    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = duplicate_identity

    existing_result = MagicMock()
    existing_scalars = MagicMock()
    existing_scalars.all.return_value = existing_helpers or []
    existing_result.scalars.return_value = existing_scalars

    session.execute = AsyncMock(side_effect=[dup_result, existing_result])
    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock(side_effect=_flush)
    return session, added


class _RecorderBot:
    def __init__(self):
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


@pytest.mark.asyncio
async def test_helper_add_success():
    from app.handlers.helper_otp_wizard import _finalize_helper

    msg = _message()
    temp_client = AsyncMock()
    temp_client.get_me = AsyncMock(return_value=SimpleNamespace(id=501, username="helper501", first_name="Helper"))
    temp_client.export_session_string = AsyncMock(return_value="plain-session")
    temp_client.disconnect = AsyncMock()
    state = {
        "phone": "+989123456789",
        "fp_device": "Pixel 8",
        "fp_system": "Android 14",
        "fp_app": "10.14.5",
        "fp_lang": "en",
    }
    session, added = _session_factory(duplicate_identity=None, existing_helpers=[], assigned_id=55)

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)) as release_mock,
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-value"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-value"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value=None),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", AsyncMock()),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
    ):
        await _finalize_helper(AsyncMock(), msg, temp_client, state)

    assert added, "helper row was not inserted"
    helper = added[0]
    assert helper.phone == "+989123456789"
    assert helper.tg_user_id == 501
    assert helper.session_string_enc == "enc-value"
    assert helper.session_fingerprint == "fp-value"

    msg.reply.assert_awaited()
    reply_kwargs = msg.reply.await_args.kwargs
    cbs = _callback_data_set(reply_kwargs["reply_markup"])
    assert "hlp:d:55" in cbs
    assert "wz:back:helper_home" in cbs
    assert "wz:home" in cbs
    release_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_helper_add_duplicate_rejected():
    from app.handlers.helper_otp_wizard import _finalize_helper

    msg = _message()
    temp_client = AsyncMock()
    temp_client.get_me = AsyncMock(return_value=SimpleNamespace(id=501, username="helper501", first_name="Helper"))
    temp_client.export_session_string = AsyncMock(return_value="plain-session")
    temp_client.disconnect = AsyncMock()
    state = {"phone": "+989123456789"}
    session, added = _session_factory(duplicate_identity=SimpleNamespace(id=1), existing_helpers=[])

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-value"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-value"),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
    ):
        await _finalize_helper(AsyncMock(), msg, temp_client, state)

    assert added == []
    msg.reply.assert_awaited_once()
    assert "ثبت" in str(msg.reply.await_args.args[0]) or "registered" in str(msg.reply.await_args.args[0]).lower()


@pytest.mark.asyncio
async def test_helper_add_cancel_returns_to_panel():
    from app.handlers import helper_otp_wizard

    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "otp_cancel")

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        data="hlp:otp:cancel",
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=settings.DEVELOPER_ID, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )

    with patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()):
        await handler(AsyncMock(), query)

    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    cbs = _callback_data_set(kb)
    assert "wz:back:helper_home" in cbs
    assert "wz:home" in cbs


@pytest.mark.asyncio
async def test_helper_session_encrypted_at_rest():
    from app.handlers.helper_otp_wizard import _finalize_helper

    msg = _message()
    temp_client = AsyncMock()
    temp_client.get_me = AsyncMock(return_value=SimpleNamespace(id=700, username="helper700", first_name="Helper"))
    temp_client.export_session_string = AsyncMock(return_value="plain-session-700")
    temp_client.disconnect = AsyncMock()
    state = {"phone": "+989987654321"}
    session, added = _session_factory(duplicate_identity=None, existing_helpers=[], assigned_id=88)

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="encrypted-700"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-700"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value=None),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", AsyncMock()),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
    ):
        await _finalize_helper(AsyncMock(), msg, temp_client, state)

    assert added
    helper = added[0]
    assert helper.session_string_enc == "encrypted-700"
    assert helper.session_string_enc != "plain-session-700"
    assert helper.session_fingerprint == "fp-700"


@pytest.mark.asyncio
async def test_helper_add_requires_permission():
    from app.handlers import helper_otp_wizard

    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "otp_start")

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=900001),
        data="hlp:add:otp",
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=900001, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )

    with (
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()) as clear_state,
        patch("app.handlers.helper_otp_wizard._set_state", AsyncMock()) as set_state,
        patch("app.handlers.helper_otp_wizard.remember_return_token", AsyncMock()) as remember,
    ):
        await handler(AsyncMock(), query)

    query.message.edit_text.assert_not_called()
    clear_state.assert_not_awaited()
    set_state.assert_not_awaited()
    remember.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")
    assert query.answer.await_args.kwargs["show_alert"] is True
