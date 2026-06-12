"""Tests for domain models — Account and Transaction frozen dataclasses (TD-4)."""
from __future__ import annotations

from typing import Any, ClassVar

import pytest

from finance_copilot.models.account import Account
from finance_copilot.models.transaction import Transaction


class TestAccount:
    _ROW: ClassVar[dict[str, Any]] = {
        "account_id": "acc001",
        "provider_id": "mock",
        "account_type": "TRANSACTION",
        "display_name": "Current Account",
        "currency": "GBP",
        "first_seen_at": "2026-06-12T00:00:00+00:00",
        "last_seen_at": "2026-06-12T00:00:00+00:00",
        "raw_payload": "{}",
    }

    def test_from_row_creates_account_with_correct_fields(self) -> None:
        account = Account.from_row(self._ROW)
        assert account.account_id == "acc001"
        assert account.provider_id == "mock"
        assert account.currency == "GBP"

    def test_to_row_roundtrips_identity(self) -> None:
        account = Account.from_row(self._ROW)
        assert account.to_row() == self._ROW

    def test_account_is_frozen(self) -> None:
        account = Account.from_row(self._ROW)
        with pytest.raises((AttributeError, TypeError)):
            account.account_id = "changed"  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        a1 = Account.from_row(self._ROW)
        a2 = Account.from_row(self._ROW)
        assert a1 == a2


class TestTransaction:
    _ROW: ClassVar[dict[str, Any]] = {
        "account_id": "acc001",
        "dedup_key": "tl:txn001",
        "source_transaction_id": "txn001",
        "booking_date": "2026-06-12",
        "value_date": None,
        "amount": "-12.50",
        "currency": "GBP",
        "transaction_type": "DEBIT",
        "description": "TESCO",
        "provider_category": "PURCHASE",
        "raw_payload": "{}",
        "ingested_at": "2026-06-12T10:00:00+00:00",
    }

    def test_from_row_creates_transaction_with_correct_fields(self) -> None:
        txn = Transaction.from_row(self._ROW)
        assert txn.dedup_key == "tl:txn001"
        assert txn.amount == "-12.50"
        assert txn.transaction_type == "DEBIT"

    def test_to_row_roundtrips_identity(self) -> None:
        txn = Transaction.from_row(self._ROW)
        assert txn.to_row() == self._ROW

    def test_optional_fields_accept_none(self) -> None:
        row = {**self._ROW, "value_date": None, "provider_category": None}
        txn = Transaction.from_row(row)
        assert txn.value_date is None
        assert txn.provider_category is None

    def test_transaction_is_frozen(self) -> None:
        txn = Transaction.from_row(self._ROW)
        with pytest.raises((AttributeError, TypeError)):
            txn.amount = "0"  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        t1 = Transaction.from_row(self._ROW)
        t2 = Transaction.from_row(self._ROW)
        assert t1 == t2
