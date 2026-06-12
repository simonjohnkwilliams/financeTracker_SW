"""Domain model for a financial transaction."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Transaction:
    """Immutable representation of a financial transaction record."""

    account_id: str
    dedup_key: str
    source_transaction_id: str
    booking_date: str
    value_date: str | None
    amount: str
    currency: str
    transaction_type: str
    description: str
    provider_category: str | None
    raw_payload: str
    ingested_at: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Transaction:
        """Construct a Transaction from a database row dict."""
        return cls(
            account_id=row["account_id"],
            dedup_key=row["dedup_key"],
            source_transaction_id=row["source_transaction_id"],
            booking_date=row["booking_date"],
            value_date=row.get("value_date"),
            amount=row["amount"],
            currency=row["currency"],
            transaction_type=row["transaction_type"],
            description=row["description"],
            provider_category=row.get("provider_category"),
            raw_payload=row["raw_payload"],
            ingested_at=row["ingested_at"],
        )

    def to_row(self) -> dict[str, Any]:
        """Convert to a database row dict."""
        return dataclasses.asdict(self)
