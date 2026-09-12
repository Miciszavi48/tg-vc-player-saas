from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.helper_pool_service import HelperPoolService


def _db_session_for_duplicate(*, fp_exists: bool, legacy_rows: list[object] | None = None):
    session = AsyncMock()
    fp_result = MagicMock()
    fp_result.scalar_one_or_none.return_value = 1 if fp_exists else None
    legacy_result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = legacy_rows or []
    legacy_result.scalars.return_value = scalars
    session.execute = AsyncMock(side_effect=[fp_result, legacy_result])
    return session


def test_session_fingerprint_deterministic() -> None:
    session_string = "AQB" + "x" * 64
    fp1 = HelperPoolService.fingerprint_session(session_string)
    fp2 = HelperPoolService.fingerprint_session(session_string)
    assert fp1 == fp2
    assert len(fp1) == 64


def test_session_fingerprint_not_plaintext() -> None:
    session_string = "AQB" + "x" * 64
    fp = HelperPoolService.fingerprint_session(session_string)
    assert fp != session_string


@pytest.mark.asyncio
async def test_duplicate_session_uses_fingerprint_without_legacy_decrypt() -> None:
    session = _db_session_for_duplicate(fp_exists=True)
    with patch("app.services.helper_pool_service.HelperPoolService.decrypt_session") as dec_mock:
        is_dup = await HelperPoolService.is_duplicate_session(session, "AQB" + "x" * 64)
    assert is_dup is True
    dec_mock.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_session_fallback_for_legacy_null_fingerprint() -> None:
    legacy = [SimpleNamespace(session_string_enc="enc-1", session_fingerprint=None)]
    session = _db_session_for_duplicate(fp_exists=False, legacy_rows=legacy)
    with patch(
        "app.services.helper_pool_service.HelperPoolService.decrypt_session",
        side_effect=["AQB" + "x" * 64],
    ) as dec_mock:
        is_dup = await HelperPoolService.is_duplicate_session(session, "AQB" + "x" * 64)
    assert is_dup is True
    dec_mock.assert_called_once()
