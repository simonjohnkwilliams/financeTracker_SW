"""Tests for Layer 1d — SyncRunRepository."""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine

from finance_copilot.db import init_db, make_engine
from finance_copilot.repositories.sync_runs import SyncRunRepository


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> SyncRunRepository:
    return SyncRunRepository(engine)


class TestOpenRun:
    def test_open_run_writes_running_row_with_started_at(
        self, repo: SyncRunRepository
    ) -> None:
        repo.open_run("run-001")
        row = repo.latest()
        assert row is not None
        assert row["run_id"] == "run-001"
        assert row["status"] == "running"
        assert row["started_at"] is not None

    def test_open_run_records_zero_counts(self, repo: SyncRunRepository) -> None:
        repo.open_run("run-001")
        row = repo.latest()
        assert row is not None
        assert row["accounts_attempted"] == 0
        assert row["transactions_inserted"] == 0


class TestCloseRun:
    def test_close_run_sets_status_and_finished_at(self, repo: SyncRunRepository) -> None:
        repo.open_run("run-001")
        finished = datetime.now(UTC).isoformat()
        repo.close_run(
            "run-001",
            status="succeeded",
            finished_at=finished,
            accounts_attempted=5,
            accounts_succeeded=5,
            transactions_inserted=2190,
            transactions_skipped_duplicate=0,
            error_summary=None,
        )
        row = repo.latest()
        assert row is not None
        assert row["status"] == "succeeded"
        assert row["finished_at"] == finished
        assert row["transactions_inserted"] == 2190

    def test_close_run_stores_error_summary(self, repo: SyncRunRepository) -> None:
        repo.open_run("run-001")
        repo.close_run(
            "run-001",
            status="partial",
            finished_at=datetime.now(UTC).isoformat(),
            accounts_attempted=5,
            accounts_succeeded=4,
            transactions_inserted=100,
            transactions_skipped_duplicate=0,
            error_summary="acc002 failed: HTTP 500",
        )
        row = repo.latest()
        assert row is not None
        assert row["error_summary"] == "acc002 failed: HTTP 500"


class TestLatest:
    def test_latest_run_returns_most_recent(self, repo: SyncRunRepository) -> None:
        repo.open_run("run-001")
        time.sleep(0.01)  # ensure different started_at timestamps
        repo.open_run("run-002")
        row = repo.latest()
        assert row is not None
        assert row["run_id"] == "run-002"

    def test_latest_returns_none_when_no_runs(self, repo: SyncRunRepository) -> None:
        assert repo.latest() is None


class TestHasRunningRun:
    def test_running_run_within_10_minutes_blocks_new_run(
        self, repo: SyncRunRepository
    ) -> None:
        repo.open_run("run-001")
        assert repo.has_running_run() is True

    def test_old_running_run_does_not_block(
        self, repo: SyncRunRepository, engine: Engine
    ) -> None:
        from finance_copilot.db import sync_runs_table

        # Insert a run that started 11 minutes ago with status=running
        old_start = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sync_runs_table.insert().values(
                    run_id="old-run",
                    started_at=old_start,
                    finished_at=None,
                    status="running",
                    accounts_attempted=0,
                    accounts_succeeded=0,
                    transactions_inserted=0,
                    transactions_skipped_duplicate=0,
                    error_summary=None,
                )
            )
        assert repo.has_running_run() is False

    def test_completed_run_does_not_block(self, repo: SyncRunRepository) -> None:
        repo.open_run("run-001")
        repo.close_run(
            "run-001",
            status="succeeded",
            finished_at=datetime.now(UTC).isoformat(),
            accounts_attempted=0,
            accounts_succeeded=0,
            transactions_inserted=0,
            transactions_skipped_duplicate=0,
            error_summary=None,
        )
        assert repo.has_running_run() is False
