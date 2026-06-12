"""Protocol types for Finance Copilot.

Defines abstract interfaces (Ports) that concrete implementations satisfy
structurally, enabling dependency inversion and typed test doubles (TD-5).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from finance_copilot.repositories.transactions import InsertResult


class TrueLayerClientPort(Protocol):
    """Protocol for the TrueLayer Data API client."""

    def fetch_accounts(self) -> list[dict[str, Any]]: ...

    def fetch_transactions(
        self, account_id: str, *, from_date: date | None = None
    ) -> list[dict[str, Any]]: ...


class TrueLayerClientFactory(Protocol):
    """Protocol for a factory that produces an authenticated TrueLayer client."""

    def __call__(self, *, access_token: str) -> TrueLayerClientPort: ...


class AccountRepositoryPort(Protocol):
    """Protocol for the account repository."""

    def upsert(self, row: dict[str, Any]) -> None: ...

    def count(self) -> int: ...


class TransactionRepositoryPort(Protocol):
    """Protocol for the transaction repository."""

    def bulk_insert(self, rows: list[dict[str, Any]]) -> InsertResult: ...

    def max_booking_date(self, account_id: str) -> str | None: ...

    def count(self) -> int: ...


class SyncRunRepositoryPort(Protocol):
    """Protocol for the sync run audit repository."""

    def open_run(self, run_id: str) -> None: ...

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
    ) -> None: ...

    def has_running_run(self) -> bool: ...

    def latest(self) -> dict[str, Any] | None: ...


class TokenRepositoryPort(Protocol):
    """Protocol for the OAuth token repository."""

    def put(
        self,
        provider: str,
        access_token: str,
        refresh_token: str,
        expires_at: str,
        obtained_at: str,
    ) -> None: ...

    def get(self, provider: str) -> dict[str, Any] | None: ...

    def is_due_for_refresh(self, provider: str) -> bool: ...
