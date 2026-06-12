"""Domain model for a bank account."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Account:
    """Immutable representation of a bank account record."""

    account_id: str
    provider_id: str
    account_type: str
    display_name: str
    currency: str
    first_seen_at: str
    last_seen_at: str
    raw_payload: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Account:
        """Construct an Account from a database row dict."""
        return cls(
            account_id=row["account_id"],
            provider_id=row["provider_id"],
            account_type=row["account_type"],
            display_name=row["display_name"],
            currency=row["currency"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            raw_payload=row["raw_payload"],
        )

    def to_row(self) -> dict[str, Any]:
        """Convert to a database row dict."""
        return dataclasses.asdict(self)
