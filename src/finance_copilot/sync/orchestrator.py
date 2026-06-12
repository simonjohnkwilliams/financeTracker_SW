"""Layer 3 — SyncOrchestrator: drives a full sync_run end-to-end."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from finance_copilot.log_config import get_logger
from finance_copilot.ports import TrueLayerClientFactory, TrueLayerClientPort
from finance_copilot.repositories.accounts import AccountRepository
from finance_copilot.repositories.sync_runs import SyncRunRepository
from finance_copilot.repositories.tokens import TokenRepository
from finance_copilot.repositories.transactions import TransactionRepository
from finance_copilot.sync import incremental, mapping
from finance_copilot.truelayer import oauth
from finance_copilot.truelayer.client import TrueLayerClient
from finance_copilot.truelayer.errors import (
    AuthError,
    MappingError,
    RateLimitError,
    SyncBlockedError,
    TransactionWriteError,
    TransientError,
)

PROVIDER_TRUELAYER = "truelayer"

log = get_logger("sync.orchestrator")

_SYNC_ACCOUNT_ERRORS = (
    RateLimitError,
    TransientError,
    MappingError,
    TransactionWriteError,
    SQLAlchemyError,
)


@dataclass
class SyncRunSummary:
    """Return value from :meth:`SyncOrchestrator.run_one`."""

    run_id: str
    status: str
    accounts_attempted: int
    accounts_succeeded: int
    transactions_inserted: int
    transactions_skipped_duplicate: int
    error_summary: str | None


class SyncOrchestrator:
    """Drives a single sync invocation from token check through to DB write."""

    def __init__(
        self,
        *,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        sync_run_repo: SyncRunRepository,
        token_repo: TokenRepository,
        auth_host: str,
        api_host: str,
        client_id: str,
        client_secret: str,
        http_client: httpx.Client,
        tl_client_factory: TrueLayerClientFactory | None = None,
    ) -> None:
        self._account_repo = account_repo
        self._transaction_repo = transaction_repo
        self._sync_run_repo = sync_run_repo
        self._token_repo = token_repo
        self._auth_host = auth_host
        self._api_host = api_host
        self._client_id = client_id
        self._client_secret = client_secret
        self._http_client = http_client
        self._tl_client_factory: TrueLayerClientFactory = (
            tl_client_factory if tl_client_factory is not None else self._make_default_factory()
        )

    def _make_default_factory(self) -> TrueLayerClientFactory:
        api_host = self._api_host
        http_client = self._http_client

        def _create(*, access_token: str) -> TrueLayerClientPort:
            return TrueLayerClient(
                api_host=api_host,
                access_token=access_token,
                http_client=http_client,
            )

        return _create

    def run_one(self, *, explicit_from: date | None = None) -> SyncRunSummary:
        """Execute one sync run.

        Workflow:
        1. Guard against concurrent runs.
        2. Open sync_run row (status=running).
        3. Refresh token if near-expiry.
        4. Fetch accounts → upsert each.
        5. Per-account (isolated): fetch transactions, map, bulk_insert.
        6. Close sync_run with final status.
        7. Return SyncRunSummary.

        Status logic:
        - All accounts succeeded → ``"succeeded"``
        - Some failed → ``"partial"``
        - AuthError during token refresh → ``"failed"`` (exception re-raised)
        - All accounts failed → ``"failed"``
        """
        if self._sync_run_repo.has_running_run():
            raise SyncBlockedError(
                "A sync run is already in progress. Wait for it to complete or expire."
            )

        run_id = str(uuid.uuid4())
        log.info("sync.start", run_id=run_id)
        self._sync_run_repo.open_run(run_id)

        try:
            result = self._execute(run_id, explicit_from=explicit_from)
        except AuthError:
            self._sync_run_repo.close_run(
                run_id,
                status="failed",
                finished_at=datetime.now(UTC).isoformat(),
                accounts_attempted=0,
                accounts_succeeded=0,
                transactions_inserted=0,
                transactions_skipped_duplicate=0,
                error_summary="Authentication failed — token may have been revoked",
            )
            raise
        except Exception as exc:
            log.exception("sync.crashed", run_id=run_id, error_type=type(exc).__name__)
            self._sync_run_repo.close_run(
                run_id,
                status="failed",
                finished_at=datetime.now(UTC).isoformat(),
                accounts_attempted=0,
                accounts_succeeded=0,
                transactions_inserted=0,
                transactions_skipped_duplicate=0,
                error_summary=f"{type(exc).__name__}: {exc}",
            )
            raise

        log.info(
            "sync.complete",
            run_id=run_id,
            status=result.status,
            inserted=result.transactions_inserted,
            skipped=result.transactions_skipped_duplicate,
        )
        return result

    def _execute(self, run_id: str, *, explicit_from: date | None) -> SyncRunSummary:
        """Orchestrate one sync: token refresh, fetch, write, close run."""
        access_token = self._ensure_fresh_token()
        tl_client: TrueLayerClientPort = self._tl_client_factory(access_token=access_token)

        now_str = datetime.now(UTC).isoformat()
        account_payloads = tl_client.fetch_accounts()
        for payload in account_payloads:
            self._account_repo.upsert(mapping.map_account(payload, now=now_str))

        total_inserted, total_skipped, accounts_succeeded, errors = self._sync_all_accounts(
            tl_client, account_payloads, explicit_from=explicit_from
        )

        accounts_attempted = len(account_payloads)
        status = _derive_status(accounts_attempted, accounts_succeeded)
        error_summary: str | None = "; ".join(errors) if errors else None

        self._sync_run_repo.close_run(
            run_id,
            status=status,
            finished_at=datetime.now(UTC).isoformat(),
            accounts_attempted=accounts_attempted,
            accounts_succeeded=accounts_succeeded,
            transactions_inserted=total_inserted,
            transactions_skipped_duplicate=total_skipped,
            error_summary=error_summary,
        )
        return SyncRunSummary(
            run_id=run_id,
            status=status,
            accounts_attempted=accounts_attempted,
            accounts_succeeded=accounts_succeeded,
            transactions_inserted=total_inserted,
            transactions_skipped_duplicate=total_skipped,
            error_summary=error_summary,
        )

    def _ensure_fresh_token(self) -> str:
        """Refresh the TrueLayer token if near-expiry and return the access token.

        Raises ``AuthError`` if no token is stored or refresh is rejected.
        """
        if self._token_repo.is_due_for_refresh(PROVIDER_TRUELAYER):
            token_row = self._token_repo.get(PROVIDER_TRUELAYER)
            if token_row is None:
                raise AuthError("No token stored — run `finance auth` first")
            new_token = oauth.refresh_token(
                self._http_client,
                auth_host=self._auth_host,
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token_value=token_row["refresh_token"],
            )
            self._token_repo.put(
                PROVIDER_TRUELAYER,
                new_token["access_token"],
                new_token["refresh_token"],
                new_token["expires_at"],
                new_token["obtained_at"],
            )
        token_row = self._token_repo.get(PROVIDER_TRUELAYER)
        return token_row["access_token"] if token_row else ""

    def _sync_all_accounts(
        self,
        tl_client: TrueLayerClientPort,
        account_payloads: list[dict[str, Any]],
        *,
        explicit_from: date | None,
    ) -> tuple[int, int, int, list[str]]:
        """Sync transactions for every account; isolate per-account failures.

        Returns ``(total_inserted, total_skipped, accounts_succeeded, error_messages)``.
        """
        total_inserted = 0
        total_skipped = 0
        accounts_succeeded = 0
        errors: list[str] = []

        for payload in account_payloads:
            account_id = str(payload.get("account_id", ""))
            try:
                last_str = self._transaction_repo.max_booking_date(account_id)
                last_booking: date | None = date.fromisoformat(last_str) if last_str else None
                from_date = incremental.sync_from_date(last_booking, explicit_from=explicit_from)
                txn_payloads = tl_client.fetch_transactions(account_id, from_date=from_date)
                ingested_at = datetime.now(UTC).isoformat()
                rows = [
                    mapping.map_transaction(t, account_id=account_id, ingested_at=ingested_at)
                    for t in txn_payloads
                ]
                result = self._transaction_repo.bulk_insert(rows)
                total_inserted += result.inserted
                total_skipped += result.skipped_duplicate
                accounts_succeeded += 1
                log.debug(
                    "account.sync",
                    account_id=account_id,
                    inserted=result.inserted,
                    skipped=result.skipped_duplicate,
                )
            except _SYNC_ACCOUNT_ERRORS as exc:
                log.warning("account.failed", account_id=account_id, error_type=type(exc).__name__)
                errors.append(f"{account_id}: {type(exc).__name__}: {exc}")

        return total_inserted, total_skipped, accounts_succeeded, errors


def _derive_status(accounts_attempted: int, accounts_succeeded: int) -> str:
    """Map attempt/success counts to a sync_run status string."""
    if accounts_succeeded == accounts_attempted:
        return "succeeded"
    if accounts_succeeded == 0:
        return "failed"
    return "partial"
