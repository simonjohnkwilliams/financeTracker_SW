"""Layer 1b — AccountRepository."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from finance_copilot.db import accounts_table


class AccountRepository:
    """Persistent storage for TrueLayer account metadata."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, row: dict[str, Any]) -> None:
        """Insert or update an account row.

        On conflict (``account_id``), all columns are updated *except* ``first_seen_at``,
        which is preserved from the initial insert.
        """
        stmt = insert(accounts_table).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["account_id"],
            set_={k: v for k, v in row.items() if k not in ("account_id", "first_seen_at")},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all accounts ordered by ``first_seen_at`` ascending."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(accounts_table).order_by(accounts_table.c.first_seen_at)
            ).mappings().all()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """Return the total number of accounts stored."""
        from sqlalchemy import func
        from sqlalchemy import select as sa_select

        with self._engine.connect() as conn:
            result = conn.execute(sa_select(func.count()).select_from(accounts_table))
            return int(result.scalar() or 0)
