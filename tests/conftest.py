"""Shared pytest fixtures for Phase 1 tests.

This file MUST NOT break the existing 36 Phase 0 tests.
All fixtures are scoped to avoid naming conflicts with existing tests.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spike_first_direct_sandbox.json"


@pytest.fixture(scope="session")
def sandbox_fixture() -> dict[str, Any]:
    """Load the real TrueLayer sandbox fixture once per session."""
    data: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def fixture_accounts(sandbox_fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the 5 sandbox account payloads."""
    accounts: list[dict[str, Any]] = sandbox_fixture["accounts"]
    return accounts


@pytest.fixture(scope="session")
def fixture_txns_by_account(
    sandbox_fixture: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Return the transactions_by_account mapping from the sandbox fixture."""
    txns: dict[str, list[dict[str, Any]]] = sandbox_fixture["transactions_by_account"]
    return txns


@pytest.fixture
def in_memory_engine() -> Generator[Engine, None, None]:
    """Provide a fresh in-memory SQLite engine with schema initialised."""
    from finance_copilot.db import init_db, make_engine

    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def all_repos(in_memory_engine: Engine) -> dict[str, Any]:
    """Provide pre-built repository instances sharing a single in-memory engine."""
    from finance_copilot.repositories.accounts import AccountRepository
    from finance_copilot.repositories.sync_runs import SyncRunRepository
    from finance_copilot.repositories.tokens import TokenRepository
    from finance_copilot.repositories.transactions import TransactionRepository

    return {
        "engine": in_memory_engine,
        "account_repo": AccountRepository(in_memory_engine),
        "transaction_repo": TransactionRepository(in_memory_engine),
        "sync_run_repo": SyncRunRepository(in_memory_engine),
        "token_repo": TokenRepository(in_memory_engine),
    }
