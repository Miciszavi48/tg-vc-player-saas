from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.handlers import helper_otp_wizard


def _message(text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        text=text,
        reply=AsyncMock(),
        delete=AsyncMock(),
        chat=SimpleNamespace(id=42, type=SimpleNamespace(value="private")),
    )


def _session_factory(*, duplicate_identity=None, assigned_id: int = 55):
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
    existing_scalars.all.return_value = []
    existing_result.scalars.return_value = existing_scalars

    session.execute = AsyncMock(side_effect=[dup_result, existing_result])
    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock(side_effect=_flush)
    return session, added


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+989123456789", "+989123456789"),
        ("989123456789", "+989123456789"),
        ("+98 912 345 6789", "+989123456789"),
    ],
)
def test_normalize_import_phone_equivalence(raw: str, expected: str) -> None:
    assert helper_otp_wizard._normalize_import_phone(raw) == expected


@pytest.mark.asyncio
async def test_import_succeeds_when_phones_match():
    from app.handlers.helper_otp_wizard import _finalize_import_helper

    msg = _message()
    state = {
        "phone": "+989123456789",
        "import_session_enc": "enc-session",
        "max_concurrent_calls": 12,
        "max_joins_per_hour": 240,
    }
    session, added = _session_factory(assigned_id=77)

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="BQ" + "x" * 40),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", AsyncMock()),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
        patch(
            "app.handlers.helper_otp_wizard._verify_import_session",
            AsyncMock(return_value=(501, "helper501", "Helper 501", "+989123456789")),
        ),
    ):
        await _finalize_import_helper(AsyncMock(), msg, state)

    assert added
    assert added[0].phone == "+989123456789"
    assert added[0].tg_user_id == 501
    assert added[0].session_fingerprint == "fp-session"


@pytest.mark.asyncio
async def test_import_rejected_on_phone_mismatch():
    from app.handlers.helper_otp_wizard import _finalize_import_helper

    msg = _message()
    state = {
        "phone": "+989111111111",
        "import_session_enc": "enc-session",
        "max_concurrent_calls": 12,
        "max_joins_per_hour": 240,
    }
    session, added = _session_factory()
    clear_mock = AsyncMock()

    log_mock = AsyncMock()
    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="BQ" + "x" * 40),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", log_mock),
        patch("app.handlers.helper_otp_wizard._clear_state", clear_mock),
        patch("app.handlers.helper_otp_wizard.t", lambda _lang, key: key),
        patch(
            "app.handlers.helper_otp_wizard._verify_import_session",
            AsyncMock(return_value=(501, "helper501", "Helper 501", "+989123456789")),
        ),
    ):
        await _finalize_import_helper(AsyncMock(), msg, state)

    assert added == []
    reply_text = str(msg.reply.await_args)
    assert "admin.helpers.import_phone_mismatch" in reply_text
    assert "BQ" not in reply_text
    clear_mock.assert_awaited_once()
    log_mock.assert_awaited()
    assert log_mock.await_args.kwargs.get("metadata") == {"reason": "phone_mismatch"}


@pytest.mark.asyncio
async def test_import_proceeds_when_session_phone_missing():
    from app.handlers.helper_otp_wizard import _finalize_import_helper

    msg = _message()
    state = {
        "phone": "+989123456789",
        "import_session_enc": "enc-session",
        "max_concurrent_calls": 12,
        "max_joins_per_hour": 240,
    }
    session, added = _session_factory(assigned_id=88)

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-session-missing"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="BQ" + "x" * 40),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", AsyncMock()),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
        patch(
            "app.handlers.helper_otp_wizard._verify_import_session",
            AsyncMock(return_value=(501, "helper501", "Helper 501", None)),
        ),
    ):
        await _finalize_import_helper(AsyncMock(), msg, state)

    assert added
    assert added[0].phone == "+989123456789"
    assert added[0].session_fingerprint == "fp-session-missing"
