"""Tests for Layer 1e — TokenRepository."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine

from finance_copilot.db import init_db, make_engine
from finance_copilot.repositories.tokens import REFRESH_SKEW_SECONDS, TokenRepository


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> TokenRepository:
    return TokenRepository(engine)


def _make_token(
    *,
    provider: str = "truelayer",
    expires_in_seconds: int = 3600,
    obtained_offset_seconds: int = 0,
) -> dict[str, str]:
    now = datetime.now(UTC)
    obtained_at = (now + timedelta(seconds=obtained_offset_seconds)).isoformat()
    expires_at = (now + timedelta(seconds=expires_in_seconds)).isoformat()
    return {
        "provider": provider,
        "access_token": "access_abc",
        "refresh_token": "refresh_xyz",
        "expires_at": expires_at,
        "obtained_at": obtained_at,
    }


class TestPutAndGet:
    def test_put_then_get_round_trip(self, repo: TokenRepository) -> None:
        token = _make_token()
        repo.put(
            token["provider"],
            token["access_token"],
            token["refresh_token"],
            token["expires_at"],
            token["obtained_at"],
        )
        row = repo.get("truelayer")
        assert row is not None
        assert row["access_token"] == "access_abc"
        assert row["refresh_token"] == "refresh_xyz"

    def test_get_returns_none_when_no_token(self, repo: TokenRepository) -> None:
        assert repo.get("truelayer") is None

    def test_put_overwrites_existing_row_for_same_provider(
        self, repo: TokenRepository
    ) -> None:
        token = _make_token()
        repo.put(
            token["provider"],
            "old_access",
            "old_refresh",
            token["expires_at"],
            token["obtained_at"],
        )
        repo.put(
            token["provider"],
            "new_access",
            "new_refresh",
            token["expires_at"],
            token["obtained_at"],
        )
        row = repo.get("truelayer")
        assert row is not None
        assert row["access_token"] == "new_access"


class TestIsDueForRefresh:
    def test_is_due_for_refresh_false_when_fresh(self, repo: TokenRepository) -> None:
        token = _make_token(expires_in_seconds=3600)
        repo.put(
            token["provider"],
            token["access_token"],
            token["refresh_token"],
            token["expires_at"],
            token["obtained_at"],
        )
        assert repo.is_due_for_refresh("truelayer") is False

    def test_is_due_for_refresh_true_within_skew_window(
        self, repo: TokenRepository
    ) -> None:
        # Token expires in less than REFRESH_SKEW_SECONDS
        token = _make_token(expires_in_seconds=REFRESH_SKEW_SECONDS - 1)
        repo.put(
            token["provider"],
            token["access_token"],
            token["refresh_token"],
            token["expires_at"],
            token["obtained_at"],
        )
        assert repo.is_due_for_refresh("truelayer") is True

    def test_is_due_for_refresh_true_when_no_token(self, repo: TokenRepository) -> None:
        assert repo.is_due_for_refresh("truelayer") is True

    def test_is_due_for_refresh_true_when_expired(self, repo: TokenRepository) -> None:
        token = _make_token(expires_in_seconds=-10)
        repo.put(
            token["provider"],
            token["access_token"],
            token["refresh_token"],
            token["expires_at"],
            token["obtained_at"],
        )
        assert repo.is_due_for_refresh("truelayer") is True
