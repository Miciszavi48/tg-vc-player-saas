from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories import credit_repo


def _mock_session(execute_results: list) -> tuple[AsyncMock, list]:
    """Build async_session mock that returns execute_results in order."""
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    execute_calls: list = []

    async def _execute(_stmt):
        idx = len(execute_calls)
        execute_calls.append(_stmt)
        mock_result = MagicMock()
        rows = execute_results[idx] if idx < len(execute_results) else []
        mock_result.scalars.return_value.all.return_value = rows
        return mock_result

    sess.execute = _execute
    return sess, execute_calls


@pytest.mark.asyncio
async def test_empty_input_returns_empty_dict_without_session():
    with patch("app.repositories.credit_repo.async_session") as mock_sess:
        result = await credit_repo.get_credits_by_chat_ids([])
        assert result == {}
        mock_sess.assert_not_called()


@pytest.mark.asyncio
async def test_dedupes_input_ids():
    row = SimpleNamespace(chat_id=-100, credit_days=5)
    sess, execute_calls = _mock_session([[row]])

    with patch("app.repositories.credit_repo.async_session", return_value=sess):
        result = await credit_repo.get_credits_by_chat_ids([-100, -100, -100])

    assert result == {-100: row}
    assert len(execute_calls) == 1


@pytest.mark.asyncio
async def test_returns_map_keyed_by_chat_id():
    row_a = SimpleNamespace(chat_id=-1, credit_days=10)
    row_b = SimpleNamespace(chat_id=-2, credit_days=3)
    sess, _execute_calls = _mock_session([[row_a, row_b]])

    with patch("app.repositories.credit_repo.async_session", return_value=sess):
        result = await credit_repo.get_credits_by_chat_ids([-1, -2])

    assert result[-1] is row_a
    assert result[-2] is row_b
    assert len(result) == 2


@pytest.mark.asyncio
async def test_missing_chat_ids_absent_from_result():
    row = SimpleNamespace(chat_id=-1, credit_days=1)
    sess, _execute_calls = _mock_session([[row]])

    with patch("app.repositories.credit_repo.async_session", return_value=sess):
        result = await credit_repo.get_credits_by_chat_ids([-1, -999])

    assert -1 in result
    assert -999 not in result


@pytest.mark.asyncio
async def test_chunking_issues_multiple_execute_calls():
    ids = list(range(1, 502))
    chunk_a = [SimpleNamespace(chat_id=i, credit_days=1) for i in range(1, 501)]
    chunk_b = [SimpleNamespace(chat_id=501, credit_days=1)]
    sess, execute_calls = _mock_session([chunk_a, chunk_b])

    with patch("app.repositories.credit_repo.async_session", return_value=sess):
        result = await credit_repo.get_credits_by_chat_ids(ids)

    assert len(execute_calls) == 2
    assert len(result) == 501
    assert result[501].chat_id == 501


@pytest.mark.asyncio
async def test_get_credit_unchanged_scalar_one_or_none():
    row = SimpleNamespace(chat_id=42, credit_days=7)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row

    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.execute = AsyncMock(return_value=mock_result)

    with patch("app.repositories.credit_repo.async_session", return_value=sess):
        credit = await credit_repo.get_credit(42)

    assert credit is row
    mock_result.scalar_one_or_none.assert_called_once()
    sess.execute.assert_awaited_once()
