"""Tests for Layer 1c — TransactionRepository."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine

from finance_copilot.db import accounts_table, init_db, make_engine
from finance_copilot.repositories.transactions import InsertResult, TransactionRepository

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spike_first_direct_sandbox.json"


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> TransactionRepository:
    return TransactionRepository(engine)


def _seed_account(engine: Engine, account_id: str = "acc001") -> None:
    with engine.begin() as conn:
        conn.execute(
            accounts_table.insert().values(
                account_id=account_id,
                provider_id="mock",
                account_type="TRANSACTION",
                display_name="Test",
                currency="GBP",
                first_seen_at="2026-06-12T00:00:00+00:00",
                last_seen_at="2026-06-12T00:00:00+00:00",
                raw_payload="{}",
            )
        )


def _make_txn_row(
    *,
    dedup_key: str = "tl:txn001",
    account_id: str = "acc001",
    booking_date: str = "2026-06-12",
) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "dedup_key": dedup_key,
        "source_transaction_id": dedup_key.replace("tl:", ""),
        "booking_date": booking_date,
        "value_date": None,
        "amount": "-10.00",
        "currency": "GBP",
        "transaction_type": "DEBIT",
        "description": "Test",
        "provider_category": None,
        "raw_payload": "{}",
        "ingested_at": "2026-06-12T10:00:00+00:00",
    }


class TestBulkInsert:
    def test_bulk_insert_returns_inserted_and_skipped_counts(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        _seed_account(engine)
        rows = [_make_txn_row(dedup_key=f"tl:txn{i:03d}") for i in range(5)]
        result = repo.bulk_insert(rows)
        assert isinstance(result, InsertResult)
        assert result.inserted == 5
        assert result.skipped_duplicate == 0

    def test_bulk_insert_skips_duplicates_by_dedup_key(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        _seed_account(engine)
        rows = [_make_txn_row(dedup_key=f"tl:txn{i:03d}") for i in range(3)]
        repo.bulk_insert(rows)
        # Insert again — all 3 should be skipped
        result = repo.bulk_insert(rows)
        assert result.inserted == 0
        assert result.skipped_duplicate == 3

    def test_bulk_insert_partial_duplicates(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        _seed_account(engine)
        rows = [_make_txn_row(dedup_key=f"tl:txn{i:03d}") for i in range(3)]
        repo.bulk_insert(rows)
        # Add 2 new rows alongside 1 existing
        new_rows = [
            _make_txn_row(dedup_key="tl:txn000"),  # duplicate
            _make_txn_row(dedup_key="tl:txn100"),  # new
            _make_txn_row(dedup_key="tl:txn101"),  # new
        ]
        result = repo.bulk_insert(new_rows)
        assert result.inserted == 2
        assert result.skipped_duplicate == 1

    def test_bulk_insert_is_atomic_on_unexpected_error(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        _seed_account(engine)
        # Build a batch where the last row has a deliberately bad account_id (FK violation)
        rows = [_make_txn_row(dedup_key=f"tl:ok{i:03d}") for i in range(3)]
        bad_row = _make_txn_row(dedup_key="tl:bad999", account_id="no_such_account")
        all_rows = [*rows, bad_row]
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            repo.bulk_insert(all_rows)
        # Nothing should have been persisted
        assert repo.count() == 0

    def test_bulk_insert_empty_list(self, repo: TransactionRepository) -> None:
        result = repo.bulk_insert([])
        assert result.inserted == 0
        assert result.skipped_duplicate == 0

    def test_bulk_insert_handles_2190_row_batch(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        """Exercise the full sandbox fixture batch of 2190 rows."""
        # Seed all 5 accounts from fixture
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        with engine.begin() as conn:
            for acc in fixture["accounts"]:
                conn.execute(
                    accounts_table.insert().values(
                        account_id=acc["account_id"],
                        provider_id=acc.get("provider", {}).get("provider_id", "mock"),
                        account_type=acc["account_type"],
                        display_name=acc["display_name"],
                        currency=acc["currency"],
                        first_seen_at="2026-06-12T00:00:00+00:00",
                        last_seen_at="2026-06-12T00:00:00+00:00",
                        raw_payload=json.dumps(acc),
                    )
                )
        from finance_copilot.sync.mapping import map_transaction

        all_rows = []
        for acc_id, txns in fixture["transactions_by_account"].items():
            for txn in txns:
                row = map_transaction(
                    txn, account_id=acc_id, ingested_at="2026-06-12T10:00:00+00:00"
                )
                all_rows.append(row)

        result = repo.bulk_insert(all_rows)
        assert result.inserted == 2190
        assert result.skipped_duplicate == 0
        assert repo.count() == 2190


class TestMaxBookingDate:
    def test_max_booking_date_for_account_returns_latest_iso_date(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        _seed_account(engine)
        rows = [
            _make_txn_row(dedup_key="tl:t1", booking_date="2026-06-10"),
            _make_txn_row(dedup_key="tl:t2", booking_date="2026-06-12"),
            _make_txn_row(dedup_key="tl:t3", booking_date="2026-06-11"),
        ]
        repo.bulk_insert(rows)
        assert repo.max_booking_date("acc001") == "2026-06-12"

    def test_max_booking_date_returns_none_for_unseen_account(
        self, repo: TransactionRepository
    ) -> None:
        assert repo.max_booking_date("does_not_exist") is None

    def test_max_booking_date_returns_none_when_no_transactions(
        self, engine: Engine, repo: TransactionRepository
    ) -> None:
        _seed_account(engine)
        assert repo.max_booking_date("acc001") is None
