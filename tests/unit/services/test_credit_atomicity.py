"""Credit service atomicity tests using mocks (no separate DB engine)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest.mark.asyncio
class TestCreditLockContract:
    async def test_charge_acquires_and_releases_lock(self):
        """CreditService.charge must use acquire_lock/release_lock."""
        from app.services import credit_service as cs_mod

        with patch.object(cs_mod, "acquire_lock", new_callable=AsyncMock, return_value="tok") as mock_acq, \
             patch.object(cs_mod, "release_lock", new_callable=AsyncMock, return_value=True) as mock_rel, \
             patch.object(cs_mod, "async_session") as mock_ss, \
             patch.object(cs_mod, "invalidate_credit", new_callable=AsyncMock), \
             patch.object(cs_mod, "set_credit_cached", new_callable=AsyncMock):

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.begin = AsyncMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
            mock_result = AsyncMock()
            mock_result.scalar_one_or_none = AsyncMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.add = AsyncMock()
            mock_session.flush = AsyncMock()
            mock_session.refresh = AsyncMock()
            mock_ss.return_value = mock_session

            from app.services.credit_service import CreditService
            try:
                await CreditService.charge(-999, "group", 5, operated_by=123456789)
            except Exception:
                pass
            mock_acq.assert_called_once()
            mock_rel.assert_called_once()

    async def test_deduct_acquires_lock(self):
        """CreditService.deduct must acquire lock."""
        from app.services import credit_service as cs_mod

        with patch.object(cs_mod, "acquire_lock", new_callable=AsyncMock, return_value=None):
            from app.services.credit_service import CreditService
            with pytest.raises(RuntimeError, match="Could not acquire"):
                await CreditService.deduct(-998, 1, operated_by=123456789)


@pytest.mark.asyncio
class TestCreditServiceI18n:
    async def test_credit_i18n_keys(self):
        from app.utils.i18n import t
        keys = [
            "credit.charged", "credit.deducted", "credit.insufficient_wallet",
            "credit.update_charge_success", "credit.expired", "credit.warning_24h",
        ]
        for k in keys:
            val = t("fa", k, amount=1, chat_title="x", days=1, chat_id=1, remaining_days=1)
            assert "[missing:" not in val, f"Missing key: {k}"
