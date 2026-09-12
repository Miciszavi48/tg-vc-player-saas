from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories import admin_repo, favorite_repo


def _mock_session(execute_results: list) -> tuple[AsyncMock, list]:
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    execute_calls: list = []

    async def _execute(_stmt):
        idx = len(execute_calls)
        execute_calls.append(_stmt)
        mock_result = MagicMock()
        payload = execute_results[idx] if idx < len(execute_results) else []
        if isinstance(payload, int):
            mock_result.scalar.return_value = payload
        else:
            mock_result.scalars.return_value.all.return_value = payload
        return mock_result

    sess.execute = _execute
    return sess, execute_calls


def _favorite_row(fid: int, user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        id=fid,
        user_id=user_id,
        chat_id=None,
        title=f"track-{fid}",
        added_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


def _vip_row(vid: int, chat_id: int = -100) -> SimpleNamespace:
    return SimpleNamespace(
        id=vid,
        chat_id=chat_id,
        user_id=1000 + vid,
        username=f"u{vid}",
    )


@pytest.mark.asyncio
async def test_count_favorites_empty():
    sess, _calls = _mock_session([0])
    with patch("app.repositories.favorite_repo.async_session", return_value=sess):
        assert await favorite_repo.count_favorites(42) == 0


@pytest.mark.asyncio
async def test_get_favorites_page_zero_returns_empty():
    sess, _calls = _mock_session([0])
    with patch("app.repositories.favorite_repo.async_session", return_value=sess):
        rows, total_pages, page = await favorite_repo.get_favorites_page(42, 0)
    assert rows == []
    assert total_pages == 1
    assert page == 0


@pytest.mark.asyncio
async def test_get_favorites_page_size_and_offset():
    page0 = [_favorite_row(i + 1) for i in range(8)]
    page1 = [_favorite_row(i + 1) for i in range(8, 12)]
    sess, calls = _mock_session([12, page0, 12, page1])

    with patch("app.repositories.favorite_repo.async_session", return_value=sess):
        rows0, tp0, p0 = await favorite_repo.get_favorites_page(1, 0, page_size=8)
        rows1, tp1, p1 = await favorite_repo.get_favorites_page(1, 1, page_size=8)

    assert len(rows0) == 8
    assert p0 == 0
    assert tp0 == 2
    assert len(rows1) == 4
    assert p1 == 1
    assert tp1 == 2
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_clamp_favorites_negative_page():
    assert favorite_repo.clamp_favorites_page(-3, 20, 8) == 0
    assert favorite_repo.clamp_favorites_page(99, 20, 8) == 2


@pytest.mark.asyncio
async def test_count_player_vips_empty():
    sess, _calls = _mock_session([0])
    with patch("app.repositories.admin_repo.async_session", return_value=sess):
        assert await admin_repo.count_player_vips(-100) == 0


@pytest.mark.asyncio
async def test_get_player_vips_page_size():
    page0 = [_vip_row(i) for i in range(1, 11)]
    sess, _calls = _mock_session([15, page0])

    with patch("app.repositories.admin_repo.async_session", return_value=sess):
        rows, total_pages, page = await admin_repo.get_player_vips_page(-100, 0, page_size=10)

    assert len(rows) == 10
    assert total_pages == 2
    assert page == 0


@pytest.mark.asyncio
async def test_clamp_vip_page_after_last_item_removed():
    # 11 VIPs, page 1 had one row; after demote 10 remain -> page 1 invalid -> page 0
    assert admin_repo.clamp_vip_list_page(1, 10, 10) == 0
    assert admin_repo.clamp_vip_list_page(0, 0, 10) == 0
    assert admin_repo.clamp_vip_list_page(2, 25, 10) == 2
