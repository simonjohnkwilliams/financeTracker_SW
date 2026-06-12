"""Layer 3 — SyncOrchestrator: drives a full sync_run end-to-end."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from finance_copilot.repositories.accounts import AccountRepository
from finance_copilot.repositories.sync_runs import SyncRunRepository
from finance_copilot.repositories.tokens import TokenRepository
from finance_copilot.repositories.transactions import TransactionRepository
from finance_copilot.sync import incremental, mapping
from finance_copilot.truelayer import oauth
from finance_copilot.truelayer.client import TrueLayerClient
from finance_copilot.truelayer.errors import AuthError, SyncBlockedError


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
    """Drives a single sync invocation from token check through to DB write.

    The ``_tl_client_override`` constructor parameter is for testing only.
    Pass an instance of a mock TrueLayerClient to bypass real HTTP.
    """

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
        redirect_uri: str,
        http_client: httpx.Client,
        _tl_client_override: Any | None = None,
    ) -> None:
        self._account_repo = account_repo
        self._transaction_repo = transaction_repo
        self._sync_run_repo = sync_run_repo
        self._token_repo = token_repo
        self._auth_host = auth_host
        self._api_host = api_host
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http_client = http_client
        self._tl_client_override = _tl_client_override

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
        # --- Concurrency guard ---
        if self._sync_run_repo.has_running_run():
            raise SyncBlockedError(
                "A sync run is already in progress. Wait for it to complete or expire."
            )

        run_id = str(uuid.uuid4())
        self._sync_run_repo.open_run(run_id)

        try:
            return self._execute(run_id, explicit_from=explicit_from)
        except AuthError:
            # Close the run as failed before re-raising
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

    def _execute(self, run_id: str, *, explicit_from: date | None) -> SyncRunSummary:
        """Inner execution — token refresh, account/txn fetch, DB writes."""
        now_str = datetime.now(UTC).isoformat()

        # --- Token management ---
        if self._token_repo.is_due_for_refresh("truelayer"):
            token_row = self._token_repo.get("truelayer")
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
                "truelayer",
                new_token["access_token"],
                new_token["refresh_token"],
                new_token["expires_at"],
                new_token["obtained_at"],
            )

        token_row = self._token_repo.get("truelayer")
        access_token: str = token_row["access_token"] if token_row else ""

        # Build (or use override) TrueLayerClient
        tl_client: Any
        if self._tl_client_override is not None:
            tl_client = self._tl_client_override
        else:
            tl_client = TrueLayerClient(
                api_host=self._api_host,
                access_token=access_token,
                http_client=self._http_client,
            )

        # --- Fetch accounts ---
        account_payloads = tl_client.fetch_accounts()
        now_str = datetime.now(UTC).isoformat()
        for payload in account_payloads:
            row = mapping.map_account(payload, now=now_str)
            self._account_repo.upsert(row)

        # --- Per-account transaction sync ---
        total_inserted = 0
        total_skipped = 0
        accounts_succeeded = 0
        failed_account_ids: list[str] = []

        for payload in account_payloads:
            account_id: str = str(payload.get("account_id", ""))
            try:
                last_booking_date_str = self._transaction_repo.max_booking_date(account_id)
                from datetime import date as date_type

                last_booking: date_type | None = None
                if last_booking_date_str:
                    last_booking = date_type.fromisoformat(last_booking_date_str)

                from_date = incremental.sync_from_date(last_booking, explicit_from=explicit_from)

                txn_payloads = tl_client.fetch_transactions(
                    account_id, from_date=from_date
                )
                ingested_at = datetime.now(UTC).isoformat()
                rows = [
                    mapping.map_transaction(t, account_id=account_id, ingested_at=ingested_at)
                    for t in txn_payloads
                ]
                result = self._transaction_repo.bulk_insert(rows)
                total_inserted += result.inserted
                total_skipped += result.skipped_duplicate
                accounts_succeeded += 1

            except Exception as exc:
                failed_account_ids.append(f"{account_id}: {exc}")

        # --- Determine status ---
        accounts_attempted = len(account_payloads)
        if accounts_succeeded == accounts_attempted:
            status = "succeeded"
        elif accounts_succeeded == 0:
            status = "failed"
        else:
            status = "partial"

        error_summary: str | None = None
        if failed_account_ids:
            error_summary = "; ".join(failed_account_ids)

        # --- Close sync_run ---
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
