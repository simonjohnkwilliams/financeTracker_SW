"""Layer 0a — incremental sync window calculation.

Pure function: no DB, no HTTP. Computes the ``from`` date to use when calling
TrueLayer's transactions endpoint for an incremental sync.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def sync_from_date(
    last_booking_date: date | None,
    *,
    explicit_from: date | None = None,
    today: date | None = None,
    initial_window_days: int = 90,
    lookback_days: int = 7,
) -> date:
    """Return the ``from`` date to use for the next TrueLayer transactions fetch.

    Rules (in priority order):
    1. If *explicit_from* is given, return it directly.
    2. If *last_booking_date* is ``None`` (first sync), return ``today - initial_window_days``.
    3. Otherwise return ``max(last_booking_date - lookback_days, today - initial_window_days)``.

    ``today`` is injectable for testing; when ``None`` it is derived from
    ``datetime.now(UTC).date()``.
    """
    if explicit_from is not None:
        return explicit_from

    resolved_today: date = today if today is not None else datetime.now(UTC).date()
    initial_start = resolved_today - timedelta(days=initial_window_days)

    if last_booking_date is None:
        return initial_start

    lookback_start = last_booking_date - timedelta(days=lookback_days)
    return max(lookback_start, initial_start)
