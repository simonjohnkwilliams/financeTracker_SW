"""Tests for Layer 0b+0c — TrueLayer payload mapping and Decimal handling."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from finance_copilot.sync.mapping import make_content_dedup_key, map_account, map_transaction

SAMPLE_TXN: dict[str, object] = {
    "transaction_id": "abc123",
    "timestamp": "2026-06-12T00:00:00Z",
    "description": "TESCO STORES",
    "transaction_type": "DEBIT",
    "transaction_category": "PURCHASE",
    "amount": -12.50,
    "currency": "GBP",
}

SAMPLE_ACCOUNT: dict[str, object] = {
    "account_id": "acc001",
    "account_type": "TRANSACTION",
    "display_name": "Current Account",
    "currency": "GBP",
    "account_number": {
        "iban": "GB08CLRB04066800003435",
        "number": "12345678",
        "sort_code": "01-02-03",
    },
    "provider": {
        "provider_id": "mock",
        "display_name": "MOCK",
    },
}


class TestMapTransaction:
    def _map(self, txn: dict[str, object] | None = None) -> dict[str, object]:
        payload = txn if txn is not None else dict(SAMPLE_TXN)
        return map_transaction(
            payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00"
        )

    def test_map_transaction_extracts_required_fields(self) -> None:
        row = self._map()
        assert row["account_id"] == "acc001"
        assert row["source_transaction_id"] == "abc123"
        assert row["description"] == "TESCO STORES"
        assert row["transaction_type"] == "DEBIT"
        assert row["currency"] == "GBP"
        assert row["ingested_at"] == "2026-06-12T10:00:00+00:00"

    def test_map_transaction_uses_timestamp_date_portion_as_booking_date(self) -> None:
        row = self._map()
        assert row["booking_date"] == "2026-06-12"

    def test_map_transaction_value_date_is_null(self) -> None:
        row = self._map()
        assert row["value_date"] is None

    def test_map_transaction_preserves_raw_payload_verbatim(self) -> None:
        payload = dict(SAMPLE_TXN)
        row = map_transaction(
            payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00"
        )
        # raw_payload must be the JSON-encoded original payload
        assert json.loads(str(row["raw_payload"])) == payload

    def test_map_transaction_dedup_key_format_is_tl_prefix_plus_transaction_id(self) -> None:
        row = self._map()
        assert row["dedup_key"] == "tl:abc123"

    def test_map_transaction_raises_when_transaction_id_missing(self) -> None:
        bad_payload = {k: v for k, v in SAMPLE_TXN.items() if k != "transaction_id"}
        with pytest.raises(ValueError, match="transaction_id"):
            map_transaction(
                bad_payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00"
            )

    def test_map_transaction_includes_provider_category(self) -> None:
        row = self._map()
        assert row["provider_category"] == "PURCHASE"

    def test_map_transaction_provider_category_none_when_absent(self) -> None:
        payload = {k: v for k, v in SAMPLE_TXN.items() if k != "transaction_category"}
        row = map_transaction(payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00")
        assert row["provider_category"] is None


class TestMapAccount:
    def _map(self, account: dict[str, object] | None = None) -> dict[str, object]:
        payload = account if account is not None else dict(SAMPLE_ACCOUNT)
        return map_account(payload, now="2026-06-12T10:00:00+00:00")

    def test_map_account_extracts_required_fields(self) -> None:
        row = self._map()
        assert row["account_id"] == "acc001"
        assert row["account_type"] == "TRANSACTION"
        assert row["display_name"] == "Current Account"
        assert row["currency"] == "GBP"
        assert row["provider_id"] == "mock"
        assert row["first_seen_at"] == "2026-06-12T10:00:00+00:00"
        assert row["last_seen_at"] == "2026-06-12T10:00:00+00:00"

    def test_map_account_preserves_account_number_in_raw_payload_only(self) -> None:
        row = self._map()
        # account_number must NOT appear as a top-level column
        assert "account_number" not in row
        # But the raw_payload must contain the full original payload including account_number
        raw = json.loads(str(row["raw_payload"]))
        assert "account_number" in raw
        assert raw["account_number"]["iban"] == "GB08CLRB04066800003435"


class TestDecimalHandling:
    def test_amount_parsed_as_decimal_from_truelayer_float(self) -> None:
        row = map_transaction(
            dict(SAMPLE_TXN), account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00"
        )
        # amount in the row should be a string representation of the Decimal
        amount_str = str(row["amount"])
        d = Decimal(amount_str)
        assert isinstance(d, Decimal)

    def test_amount_stored_as_string_round_trips_exactly(self) -> None:
        payload = dict(SAMPLE_TXN)
        payload["amount"] = -12.50
        row = map_transaction(payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00")
        # Must round-trip without float imprecision
        amount_str = str(row["amount"])
        assert Decimal(amount_str) == Decimal(str(Decimal(str(-12.50))))

    def test_negative_amounts_preserve_sign(self) -> None:
        payload = dict(SAMPLE_TXN)
        payload["amount"] = -99.99
        row = map_transaction(payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00")
        assert Decimal(str(row["amount"])) < 0

    def test_positive_amounts_preserve_sign(self) -> None:
        payload = dict(SAMPLE_TXN)
        payload["amount"] = 50.00
        payload["transaction_type"] = "CREDIT"
        row = map_transaction(payload, account_id="acc001", ingested_at="2026-06-12T10:00:00+00:00")
        assert Decimal(str(row["amount"])) > 0


class TestContentDedupKey:
    def test_dedup_key_for_csv_source_uses_content_sha256_prefix(self) -> None:
        data = {"date": "2026-01-01", "description": "TEST", "amount": "-10.00"}
        key = make_content_dedup_key(data)
        assert key.startswith("content:")
        assert len(key) > len("content:") + 10  # has actual hash content

    def test_dedup_key_is_deterministic(self) -> None:
        data = {"a": "1", "b": "2"}
        assert make_content_dedup_key(data) == make_content_dedup_key(data)

    def test_different_data_produces_different_keys(self) -> None:
        data1 = {"a": "1"}
        data2 = {"a": "2"}
        assert make_content_dedup_key(data1) != make_content_dedup_key(data2)
