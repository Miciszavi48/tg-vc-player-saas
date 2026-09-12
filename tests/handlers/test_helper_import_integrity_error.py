from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.handlers import helper_otp_wizard


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        text="",
        reply=AsyncMock(),
        delete=AsyncMock(),
        chat=SimpleNamespace(id=42, type=SimpleNamespace(value="private")),
    )


def _session_factory(*, flush_raises: Exception | None = None):
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
        if flush_raises is not None:
            raise flush_raises

    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None

    session.execute = AsyncMock(return_value=dup_result)
    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock(side_effect=_flush)
    return session, added


@pytest.mark.asyncio
async def test_finalize_import_helper_catches_integrity_error():
    from app.handlers.helper_otp_wizard import _finalize_import_helper

    msg = _message()
    state = {
        "phone": "+989123456789",
        "import_session_enc": "enc-session",
        "max_concurrent_calls": 12,
        "max_joins_per_hour": 240,
    }
    session, added = _session_factory(flush_raises=IntegrityError("stmt", {}, Exception("dup")))
    clear_mock = AsyncMock()
    release_mock = AsyncMock(return_value=True)
    log_mock = AsyncMock()

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", release_mock),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="BQ" + "x" * 40),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", log_mock),
        patch("app.handlers.helper_otp_wizard._clear_state", clear_mock),
        patch("app.handlers.helper_otp_wizard.t", lambda _lang, key, **kwargs: key),
        patch(
            "app.handlers.helper_otp_wizard._verify_import_session",
            AsyncMock(return_value=(501, "helper501", "Helper 501", "+989123456789")),
        ),
    ):
        await _finalize_import_helper(AsyncMock(), msg, state)

    assert added, "insert was attempted before flush raised IntegrityError"
    reply_text = str(msg.reply.await_args)
    assert "admin.helpers.import_duplicate_session" in reply_text
    assert "import_success_summary" not in reply_text
    assert "IntegrityError" not in reply_text
    assert "BQ" not in reply_text
    assert "unexpected_error" not in reply_text
    clear_mock.assert_awaited_once()
    release_mock.assert_awaited_once()
    log_mock.assert_awaited()
    assert log_mock.await_args.kwargs.get("metadata") == {"reason": "duplicate_integrity"}
