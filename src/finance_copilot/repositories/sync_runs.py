"""Layer 1d — SyncRunRepository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from finance_copilot.db import sync_runs_table

_RUNNING_RUN_TIMEOUT_MINUTES = 10


class SyncRunRepository:
    """Persistent audit trail for sync invocations."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def open_run(self, run_id: str) -> None:
        """Insert a ``sync_run`` row with ``status='running'`` and ``started_at=now``."""
        with self._engine.begin() as conn:
            conn.execute(
                sync_runs_table.insert().values(
                    run_id=run_id,
                    started_at=datetime.now(UTC).isoformat(),
                    finished_at=None,
                    status="running",
                    accounts_attempted=0,
                    accounts_succeeded=0,
                    transactions_inserted=0,
                    transactions_skipped_duplicate=0,
                    error_summary=None,
                )
            )

    def close_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str,
        accounts_attempted: int,
        accounts_succeeded: int,
        transactions_inserted: int,
        transactions_skipped_duplicate: int,
        error_summary: str | None,
    ) -> None:
        """Update the sync_run row with final status and counters."""
        with self._engine.begin() as conn:
            conn.execute(
                sync_runs_table.update()
                .where(sync_runs_table.c.run_id == run_id)
                .values(
                    status=status,
                    finished_at=finished_at,
                    accounts_attempted=accounts_attempted,
                    accounts_succeeded=accounts_succeeded,
                    transactions_inserted=transactions_inserted,
                    transactions_skipped_duplicate=transactions_skipped_duplicate,
                    error_summary=error_summary,
                )
            )

    def latest(self) -> dict[str, Any] | None:
        """Return the most recently started sync_run row, or ``None`` if no rows exist."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(sync_runs_table)
                .order_by(sync_runs_table.c.started_at.desc(), sync_runs_table.c.run_id.desc())
                .limit(1)
            ).mappings().first()
        return dict(row) if row is not None else None

    def has_running_run(self) -> bool:
        """Return ``True`` if a ``status='running'`` sync_run started within the last 10 minutes."""
        cutoff = (datetime.now(UTC) - timedelta(minutes=_RUNNING_RUN_TIMEOUT_MINUTES)).isoformat()
        with self._engine.connect() as conn:
            row = conn.execute(
                select(sync_runs_table).where(
                    sync_runs_table.c.status == "running",
                    sync_runs_table.c.started_at >= cutoff,
                )
            ).first()
        return row is not None
