"""Tests for Layer 1b — AccountRepository."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy.engine import Engine

from finance_copilot.db import init_db, make_engine
from finance_copilot.repositories.accounts import AccountRepository


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> AccountRepository:
    return AccountRepository(engine)


SAMPLE_ROW: dict[str, object] = {
    "account_id": "acc001",
    "provider_id": "mock",
    "account_type": "TRANSACTION",
    "display_name": "Current Account",
    "currency": "GBP",
    "first_seen_at": "2026-01-01T00:00:00+00:00",
    "last_seen_at": "2026-01-01T00:00:00+00:00",
    "raw_payload": "{}",
}


class TestAccountRepositoryUpsert:
    def test_upsert_inserts_new_account(self, repo: AccountRepository) -> None:
        repo.upsert(dict(SAMPLE_ROW))
        assert repo.count() == 1

    def test_upsert_updates_last_seen_on_existing_account(
        self, repo: AccountRepository
    ) -> None:
        repo.upsert(dict(SAMPLE_ROW))
        updated = dict(SAMPLE_ROW)
        updated["last_seen_at"] = "2026-06-12T10:00:00+00:00"
        repo.upsert(updated)
        assert repo.count() == 1
        rows = repo.list_all()
        assert rows[0]["last_seen_at"] == "2026-06-12T10:00:00+00:00"

    def test_upsert_preserves_first_seen_on_update(self, repo: AccountRepository) -> None:
        repo.upsert(dict(SAMPLE_ROW))
        updated = dict(SAMPLE_ROW)
        updated["first_seen_at"] = "2099-01-01T00:00:00+00:00"
        updated["last_seen_at"] = "2026-06-12T10:00:00+00:00"
        repo.upsert(updated)
        rows = repo.list_all()
        # first_seen_at must remain the original value
        assert rows[0]["first_seen_at"] == "2026-01-01T00:00:00+00:00"

    def test_upsert_multiple_accounts(self, repo: AccountRepository) -> None:
        for i in range(3):
            row = dict(SAMPLE_ROW)
            row["account_id"] = f"acc{i:03d}"
            repo.upsert(row)
        assert repo.count() == 3


class TestAccountRepositoryList:
    def test_list_returns_all_accounts_ordered_by_first_seen(
        self, repo: AccountRepository
    ) -> None:
        for ts, acc_id in [
            ("2026-03-01T00:00:00+00:00", "acc_c"),
            ("2026-01-01T00:00:00+00:00", "acc_a"),
            ("2026-02-01T00:00:00+00:00", "acc_b"),
        ]:
            row = dict(SAMPLE_ROW)
            row["account_id"] = acc_id
            row["first_seen_at"] = ts
            row["last_seen_at"] = ts
            repo.upsert(row)
        rows = repo.list_all()
        assert [r["account_id"] for r in rows] == ["acc_a", "acc_b", "acc_c"]

    def test_list_returns_empty_when_no_accounts(self, repo: AccountRepository) -> None:
        assert repo.list_all() == []
