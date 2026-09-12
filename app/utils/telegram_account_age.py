"""Approximate Telegram account age from user ID (heuristic only)."""

from __future__ import annotations

import datetime

# Reference points: (min_user_id, approximate_registration_date)
_ID_EPOCH_POINTS: tuple[tuple[int, datetime.date], ...] = (
    (0, datetime.date(2013, 8, 1)),
    (100_000_000, datetime.date(2015, 1, 1)),
    (500_000_000, datetime.date(2017, 6, 1)),
    (1_000_000_000, datetime.date(2018, 6, 1)),
    (3_000_000_000, datetime.date(2020, 1, 1)),
    (5_000_000_000, datetime.date(2021, 6, 1)),
    (7_000_000_000, datetime.date(2023, 1, 1)),
    (10_000_000_000, datetime.date(2025, 1, 1)),
)


def estimate_account_age_days(user_id: int) -> int | None:
    """Estimate account age in days from Telegram user ID.

    This is a rough heuristic, not an official Telegram registration date.
    Lower IDs generally indicate older accounts, but accuracy is not guaranteed.

    Args:
        user_id: Telegram user identifier.

    Returns:
        Estimated age in days, or None when input is invalid.
    """
    if user_id <= 0:
        return None

    today = datetime.date.today()
    if user_id < _ID_EPOCH_POINTS[0][0]:
        return max(0, (today - _ID_EPOCH_POINTS[0][1]).days)

    reg_date = _ID_EPOCH_POINTS[0][1]
    for idx in range(1, len(_ID_EPOCH_POINTS)):
        lo_id, lo_date = _ID_EPOCH_POINTS[idx - 1]
        hi_id, hi_date = _ID_EPOCH_POINTS[idx]
        if user_id < hi_id:
            span_ids = max(1, hi_id - lo_id)
            span_days = max(1, (hi_date - lo_date).days)
            offset = int((user_id - lo_id) * span_days / span_ids)
            reg_date = lo_date + datetime.timedelta(days=offset)
            break
    else:
        lo_id, lo_date = _ID_EPOCH_POINTS[-1]
        ids_per_day = 2_000_000
        reg_date = lo_date + datetime.timedelta(days=max(0, (user_id - lo_id) // ids_per_day))

    return max(0, (today - reg_date).days)


def is_account_age_at_least(user_id: int, threshold_days: int) -> bool | None:
    """Return whether estimated account age meets threshold.

    Args:
        user_id: Telegram user identifier.
        threshold_days: Minimum account age in days.

    Returns:
        True/False when estimate is available, None when age is unknown.
    """
    age = estimate_account_age_days(user_id)
    if age is None:
        return None
    return age >= threshold_days
