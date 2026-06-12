"""Tests for Layer 1a — SQLAlchemy Core schema and init_db."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from finance_copilot.db import (
    CURRENT_SCHEMA_VERSION,
    SchemaVersionError,
    accounts_table,
    check_schema_version,
    init_db,
    make_engine,
    schema_version_table,
    transactions_table,
)


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    """In-memory SQLite engine with schema initialised."""
    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


class TestCreateAll:
    def test_create_all_creates_expected_tables(self, engine: Engine) -> None:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        assert "accounts" in table_names
        assert "transactions" in table_names
        assert "sync_runs" in table_names
        assert "oauth_tokens" in table_names
        assert "schema_version" in table_names

    def test_schema_version_seeded_at_create_all(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(schema_version_table.select()).first()
        assert row is not None
        assert row.version == CURRENT_SCHEMA_VERSION

    def test_init_db_idempotent(self) -> None:
        eng = make_engine(":memory:")
        init_db(eng)
        init_db(eng)  # second call must not raise or duplicate the version row
        with eng.connect() as conn:
            rows = conn.execute(schema_version_table.select()).all()
        assert len(rows) == 1
        eng.dispose()


class TestTransactionsConstraints:
    def test_transactions_dedup_key_unique_constraint(self, engine: Engine) -> None:
        # Insert an account first (FK)
        with engine.begin() as conn:
            conn.execute(
                accounts_table.insert().values(
                    account_id="acc1",
                    provider_id="mock",
                    account_type="TRANSACTION",
                    display_name="Test",
                    currency="GBP",
                    first_seen_at="2026-06-12T00:00:00+00:00",
                    last_seen_at="2026-06-12T00:00:00+00:00",
                    raw_payload="{}",
                )
            )
        txn_row = {
            "account_id": "acc1",
            "dedup_key": "tl:txn001",
            "source_transaction_id": "txn001",
            "booking_date": "2026-06-12",
            "value_date": None,
            "amount": "-10.00",
            "currency": "GBP",
            "transaction_type": "DEBIT",
            "description": "Test",
            "provider_category": None,
            "raw_payload": "{}",
            "ingested_at": "2026-06-12T10:00:00+00:00",
        }
        with engine.begin() as conn:
            conn.execute(transactions_table.insert().values(**txn_row))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(transactions_table.insert().values(**txn_row))

    def test_transactions_foreign_key_to_accounts(self, engine: Engine) -> None:
        txn_row = {
            "account_id": "nonexistent_account",
            "dedup_key": "tl:orphan001",
            "source_transaction_id": "orphan001",
            "booking_date": "2026-06-12",
            "value_date": None,
            "amount": "-5.00",
            "currency": "GBP",
            "transaction_type": "DEBIT",
            "description": "Orphan",
            "provider_category": None,
            "raw_payload": "{}",
            "ingested_at": "2026-06-12T10:00:00+00:00",
        }
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(transactions_table.insert().values(**txn_row))


class TestSchemaVersion:
    def test_startup_refuses_when_version_greater_than_code(self, engine: Engine) -> None:
        # Manually bump the version in the DB above the code version
        with engine.begin() as conn:
            conn.execute(
                schema_version_table.update().values(version=CURRENT_SCHEMA_VERSION + 1)
            )
        with pytest.raises(SchemaVersionError):
            check_schema_version(engine)

    def test_startup_passes_when_version_equals_code(self, engine: Engine) -> None:
        # Should not raise
        check_schema_version(engine)

    def test_startup_passes_when_no_version_row(self) -> None:
        eng = make_engine(":memory:")
        from finance_copilot.db import metadata

        metadata.create_all(eng)
        # Don't seed — should not raise (empty table ⇒ no version > code)
        check_schema_version(eng)
        eng.dispose()
