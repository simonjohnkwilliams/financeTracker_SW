"""Layer 1c — TransactionRepository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from finance_copilot.db import transactions_table


@dataclass
class InsertResult:
    """Outcome of a ``bulk_insert`` call."""

    inserted: int
    skipped_duplicate: int


class TransactionRepository:
    """Persistent storage for financial transactions with dedup-aware bulk insert."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def bulk_insert(self, rows: list[dict[str, Any]]) -> InsertResult:
        """Insert *rows*, silently skipping any whose ``dedup_key`` already exists.

        The insert is performed inside a single transaction so an unexpected
        integrity error (e.g. FK violation) rolls back the entire batch.

        Returns an :class:`InsertResult` with ``inserted`` and ``skipped_duplicate`` counts.
        """
        if not rows:
            return InsertResult(inserted=0, skipped_duplicate=0)

        with self._engine.begin() as conn:
            dedup_keys = [r["dedup_key"] for r in rows]
            existing: set[str] = {
                row[0]
                for row in conn.execute(
                    select(transactions_table.c.dedup_key).where(
                        transactions_table.c.dedup_key.in_(dedup_keys)
                    )
                )
            }
            new_rows = [r for r in rows if r["dedup_key"] not in existing]
            if new_rows:
                conn.execute(transactions_table.insert(), new_rows)

        return InsertResult(
            inserted=len(new_rows),
            skipped_duplicate=len(rows) - len(new_rows),
        )

    def max_booking_date(self, account_id: str) -> str | None:
        """Return the latest ``booking_date`` for ``account_id``, or ``None`` if no rows exist."""
        with self._engine.connect() as conn:
            result = conn.execute(
                select(func.max(transactions_table.c.booking_date)).where(
                    transactions_table.c.account_id == account_id
                )
            )
            value = result.scalar()
        return str(value) if value is not None else None

    def count(self) -> int:
        """Return the total number of transaction rows stored."""
        with self._engine.connect() as conn:
            result = conn.execute(select(func.count()).select_from(transactions_table))
            return int(result.scalar() or 0)
