"""Tests for Layer 0a — incremental window calculation (pure function, no DB/HTTP)."""

from __future__ import annotations

from datetime import date, timedelta

from finance_copilot.sync.incremental import sync_from_date


class TestFirstSync:
    def test_first_sync_uses_initial_window_when_no_last_seen(self) -> None:
        today = date(2026, 6, 12)
        result = sync_from_date(None, today=today)
        assert result == today - timedelta(days=90)

    def test_window_uses_utc_today_not_local(self) -> None:
        # When today is not supplied, the function must derive it from UTC.
        # We can't assert the exact date without mocking, but we can confirm it
        # returns a date object and is approximately "today - 90 days".
        result = sync_from_date(None)
        from datetime import UTC, datetime

        utc_today = datetime.now(UTC).date()
        assert result == utc_today - timedelta(days=90)


class TestSubsequentSync:
    def test_subsequent_sync_uses_lookback_window(self) -> None:
        today = date(2026, 6, 12)
        last_booking = date(2026, 6, 1)
        result = sync_from_date(last_booking, today=today)
        # lookback_days=7 by default: 2026-06-01 - 7 = 2026-05-25
        assert result == date(2026, 5, 25)

    def test_lookback_does_not_predate_initial_window(self) -> None:
        # last_booking_date is old enough that last_booking - 7 < today - 90
        today = date(2026, 6, 12)
        initial_window_start = today - timedelta(days=90)  # 2026-03-14
        # Put last_booking before the initial window start
        old_last_booking = date(2026, 1, 1)
        result = sync_from_date(old_last_booking, today=today)
        # max(old_last_booking - 7, today - 90) = max(2025-12-25, 2026-03-14) = 2026-03-14
        assert result == initial_window_start

    def test_explicit_from_short_circuits_calculation(self) -> None:
        today = date(2026, 6, 12)
        explicit = date(2026, 1, 1)
        result = sync_from_date(None, explicit_from=explicit, today=today)
        assert result == explicit

    def test_explicit_from_overrides_last_booking_date(self) -> None:
        today = date(2026, 6, 12)
        last_booking = date(2026, 6, 1)
        explicit = date(2026, 3, 1)
        result = sync_from_date(last_booking, explicit_from=explicit, today=today)
        assert result == explicit

    def test_custom_initial_window_days(self) -> None:
        today = date(2026, 6, 12)
        result = sync_from_date(None, today=today, initial_window_days=30)
        assert result == today - timedelta(days=30)

    def test_custom_lookback_days(self) -> None:
        today = date(2026, 6, 12)
        last_booking = date(2026, 6, 1)
        result = sync_from_date(last_booking, today=today, lookback_days=14)
        # 2026-06-01 - 14 = 2026-05-18
        assert result == date(2026, 5, 18)

    def test_lookback_prefers_more_recent_when_within_initial_window(self) -> None:
        today = date(2026, 6, 12)
        # last_booking is recent, so last_booking - 7 is within initial window range
        last_booking = date(2026, 6, 10)
        result = sync_from_date(last_booking, today=today)
        # max(2026-06-03, 2026-03-14) = 2026-06-03
        assert result == date(2026, 6, 3)
