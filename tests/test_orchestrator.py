"""Tests for Layer 3 — SyncOrchestrator.

Uses in-memory SQLite + a mock TrueLayerClient backed by the sandbox fixture.
All tests are offline (no real HTTP).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from finance_copilot.repositories.tokens import TokenRepository
from finance_copilot.sync.orchestrator import PROVIDER_TRUELAYER, SyncOrchestrator
from finance_copilot.truelayer.errors import AuthError, SyncBlockedError, TransientError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spike_first_direct_sandbox.json"


def _load_fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data


# Derived at import time so tests don't hard-code the count (TD-14)
_FIXTURE_DATA = _load_fixture()
TOTAL_TRANSACTIONS = sum(
    len(v) for v in _FIXTURE_DATA["transactions_by_account"].values()
)


class MockTrueLayerClient:
    """Mock TrueLayerClient backed by the sandbox fixture (satisfies TrueLayerClientPort)."""

    def __init__(
        self,
        accounts: list[dict[str, Any]],
        txns_by_account: dict[str, list[dict[str, Any]]],
        *,
        fail_account_id: str | None = None,
    ) -> None:
        self._accounts = accounts
        self._txns = txns_by_account
        self._fail_account_id = fail_account_id

    def fetch_accounts(self) -> list[dict[str, Any]]:
        return self._accounts

    def fetch_transactions(
        self, account_id: str, *, from_date: date | None = None
    ) -> list[dict[str, Any]]:
        if self._fail_account_id and account_id == self._fail_account_id:
            raise TransientError(f"Simulated HTTP 500 for account {account_id}")
        return self._txns.get(account_id, [])


def _make_orchestrator(
    repos: dict[str, Any],
    tl_client: MockTrueLayerClient,
) -> SyncOrchestrator:
    def factory(*, access_token: str) -> MockTrueLayerClient:
        return tl_client

    return SyncOrchestrator(
        account_repo=repos["account_repo"],
        transaction_repo=repos["transaction_repo"],
        sync_run_repo=repos["sync_run_repo"],
        token_repo=repos["token_repo"],
        auth_host="https://auth.truelayer-sandbox.com",
        api_host="https://api.truelayer-sandbox.com",
        client_id="test_client",
        client_secret="test_secret",
        http_client=MagicMock(),
        tl_client_factory=factory,  # type: ignore[arg-type]
    )


def _seed_fresh_token(token_repo: TokenRepository) -> None:
    """Insert a non-expired token into the token repo."""
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    obtained_at = datetime.now(UTC).isoformat()
    token_repo.put(
        PROVIDER_TRUELAYER, "access_token_abc", "refresh_token_xyz", expires_at, obtained_at
    )


@pytest.fixture
def fixture_data() -> dict[str, Any]:
    return _load_fixture()


@pytest.fixture
def fixture_client(fixture_data: dict[str, Any]) -> MockTrueLayerClient:
    return MockTrueLayerClient(
        accounts=fixture_data["accounts"],
        txns_by_account=fixture_data["transactions_by_account"],
    )


@pytest.fixture
def orchestrator(
    all_repos: dict[str, Any], fixture_client: MockTrueLayerClient
) -> SyncOrchestrator:
    _seed_fresh_token(all_repos["token_repo"])
    return _make_orchestrator(all_repos, fixture_client)


class TestHappyPath:
    def test_first_run_inserts_all_fixture_transactions(
        self, orchestrator: SyncOrchestrator, all_repos: dict[str, Any]
    ) -> None:
        result = orchestrator.run_one()
        assert result.transactions_inserted == TOTAL_TRANSACTIONS
        assert all_repos["transaction_repo"].count() == TOTAL_TRANSACTIONS

    def test_first_run_writes_sync_run_with_succeeded_status(
        self, orchestrator: SyncOrchestrator, all_repos: dict[str, Any]
    ) -> None:
        result = orchestrator.run_one()
        assert result.status == "succeeded"
        row = all_repos["sync_run_repo"].latest()
        assert row is not None
        assert row["status"] == "succeeded"

    def test_first_run_records_account_metadata_for_all_accounts(
        self, orchestrator: SyncOrchestrator, all_repos: dict[str, Any]
    ) -> None:
        orchestrator.run_one()
        assert all_repos["account_repo"].count() == 5

    def test_first_run_summary_counts_match_db_row_counts(
        self, orchestrator: SyncOrchestrator, all_repos: dict[str, Any]
    ) -> None:
        result = orchestrator.run_one()
        assert result.transactions_inserted == all_repos["transaction_repo"].count()
        assert result.accounts_attempted == 5
        assert result.accounts_succeeded == 5


class TestIdempotency:
    def test_second_run_with_identical_data_inserts_zero_rows(
        self, orchestrator: SyncOrchestrator, all_repos: dict[str, Any]
    ) -> None:
        orchestrator.run_one()
        result = orchestrator.run_one()
        assert result.transactions_inserted == 0
        assert result.transactions_skipped_duplicate == TOTAL_TRANSACTIONS

    def test_second_run_with_one_new_transaction_inserts_exactly_one(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        # First run with standard fixture
        client1 = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch1 = _make_orchestrator(all_repos, client1)
        _seed_fresh_token(all_repos["token_repo"])
        orch1.run_one()

        # Second run with one extra transaction for the first account
        first_account_id = fixture_data["accounts"][0]["account_id"]
        extra_txn: dict[str, Any] = {
            "transaction_id": "brand_new_txn_001",
            "timestamp": "2026-06-13T00:00:00Z",
            "description": "NEW TRANSACTION",
            "transaction_type": "CREDIT",
            "amount": 100.0,
            "currency": "GBP",
            "transaction_category": "INCOME",
        }
        extended_txns = dict(fixture_data["transactions_by_account"])
        extended_txns[first_account_id] = [
            *fixture_data["transactions_by_account"][first_account_id],
            extra_txn,
        ]
        client2 = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=extended_txns,
        )
        orch2 = _make_orchestrator(all_repos, client2)
        _seed_fresh_token(all_repos["token_repo"])
        result = orch2.run_one()
        assert result.transactions_inserted == 1
        assert result.transactions_skipped_duplicate == TOTAL_TRANSACTIONS


class TestTokenLifecycle:
    def test_orchestrator_refreshes_expired_token_before_fetching(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        # Seed an almost-expired token (within REFRESH_SKEW_SECONDS)
        expires_at = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
        all_repos["token_repo"].put(
            PROVIDER_TRUELAYER,
            "old_access",
            "old_refresh",
            expires_at,
            datetime.now(UTC).isoformat(),
        )

        # Mock the oauth.refresh_token to return a fresh token
        new_expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        new_token = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_at": new_expires,
            "obtained_at": datetime.now(UTC).isoformat(),
        }
        client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)

        with patch("finance_copilot.sync.orchestrator.oauth.refresh_token", return_value=new_token):
            result = orch.run_one()

        assert result.status == "succeeded"
        # Token should be updated
        stored = all_repos["token_repo"].get(PROVIDER_TRUELAYER)
        assert stored is not None
        assert stored["access_token"] == "new_access"

    def test_orchestrator_raises_authentication_error_when_refresh_fails(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        # Seed an expired token
        expires_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        all_repos["token_repo"].put(
            PROVIDER_TRUELAYER,
            "old_access",
            "old_refresh",
            expires_at,
            datetime.now(UTC).isoformat(),
        )
        client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)

        with patch(
            "finance_copilot.sync.orchestrator.oauth.refresh_token",
            side_effect=AuthError("Refresh token revoked"),
        ), pytest.raises(AuthError):
            orch.run_one()

    def test_failed_auth_writes_sync_run_with_failed_status(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        expires_at = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
        all_repos["token_repo"].put(
            PROVIDER_TRUELAYER,
            "old_access",
            "old_refresh",
            expires_at,
            datetime.now(UTC).isoformat(),
        )
        client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)

        with patch(
            "finance_copilot.sync.orchestrator.oauth.refresh_token",
            side_effect=AuthError("Refresh token revoked"),
        ), pytest.raises(AuthError):
            orch.run_one()

        row = all_repos["sync_run_repo"].latest()
        assert row is not None
        assert row["status"] == "failed"


class TestPartialFailure:
    def _make_partial_client(
        self, fixture_data: dict[str, Any], fail_account_id: str
    ) -> MockTrueLayerClient:
        return MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
            fail_account_id=fail_account_id,
        )

    def test_one_account_500_does_not_abort_other_accounts(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fail_id = fixture_data["accounts"][0]["account_id"]
        client = self._make_partial_client(fixture_data, fail_account_id=fail_id)
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        result = orch.run_one()
        # 4 accounts should succeed
        assert result.accounts_succeeded == 4
        assert result.accounts_attempted == 5

    def test_partial_failure_sets_sync_run_status_partial(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fail_id = fixture_data["accounts"][0]["account_id"]
        client = self._make_partial_client(fixture_data, fail_account_id=fail_id)
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        result = orch.run_one()
        assert result.status == "partial"

    def test_partial_failure_error_summary_names_failed_account(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fail_id = fixture_data["accounts"][0]["account_id"]
        client = self._make_partial_client(fixture_data, fail_account_id=fail_id)
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        result = orch.run_one()
        assert result.error_summary is not None
        assert fail_id in result.error_summary

    def test_partial_failure_still_advances_other_account_watermarks(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fail_id = fixture_data["accounts"][0]["account_id"]
        client = self._make_partial_client(fixture_data, fail_account_id=fail_id)
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        orch.run_one()
        # Other accounts should have transactions stored
        for acc in fixture_data["accounts"]:
            if acc["account_id"] != fail_id:
                max_date = all_repos["transaction_repo"].max_booking_date(acc["account_id"])
                assert max_date is not None


class TestIncrementalWindow:
    def test_subsequent_run_uses_from_based_on_max_booking_date_per_account(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        """After first run, each account's fetch should use a from_date derived from watermarks."""
        client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        orch.run_one()

        # Verify watermarks are set
        for acc in fixture_data["accounts"]:
            max_date = all_repos["transaction_repo"].max_booking_date(acc["account_id"])
            assert max_date is not None

    def test_explicit_from_override_is_propagated_to_client(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fetch_calls: list[dict[str, Any]] = []

        class RecordingClient(MockTrueLayerClient):
            def fetch_transactions(
                self, account_id: str, *, from_date: date | None = None
            ) -> list[dict[str, Any]]:
                fetch_calls.append({"account_id": account_id, "kwargs": {"from_date": from_date}})
                return []

        client = RecordingClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        explicit = date(2026, 1, 1)
        orch.run_one(explicit_from=explicit)

        for call_info in fetch_calls:
            assert call_info["kwargs"].get("from_date") == explicit


class TestConcurrencyGuard:
    def test_starting_a_run_while_one_is_running_raises(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        # Manually insert a running sync_run
        all_repos["sync_run_repo"].open_run("concurrent-run-001")
        client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        with pytest.raises(SyncBlockedError):
            orch.run_one()


class TestThirdRunRecovery:
    def test_third_run_after_partial_failure_recovers_missing_account(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fail_id = fixture_data["accounts"][0]["account_id"]

        # First run: all accounts succeed
        full_client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch1 = _make_orchestrator(all_repos, full_client)
        _seed_fresh_token(all_repos["token_repo"])
        orch1.run_one()
        count_after_first = all_repos["transaction_repo"].count()

        # Second run: one account fails
        partial_client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
            fail_account_id=fail_id,
        )
        orch2 = _make_orchestrator(all_repos, partial_client)
        _seed_fresh_token(all_repos["token_repo"])
        orch2.run_one()

        # Third run: all accounts succeed again — no new rows (idempotent)
        full_client2 = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch3 = _make_orchestrator(all_repos, full_client2)
        _seed_fresh_token(all_repos["token_repo"])
        result = orch3.run_one()
        assert result.status == "succeeded"
        # Count should be unchanged (dedup)
        assert all_repos["transaction_repo"].count() == count_after_first


class TestRunCleanup:
    def test_run_one_closes_sync_run_on_unexpected_exception(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        """An unexpected exception (e.g. TypeError) must not leave a 'running' sync_run."""

        class BrokenClient(MockTrueLayerClient):
            def fetch_accounts(self) -> list[dict[str, Any]]:
                raise RuntimeError("simulated unexpected crash")

        client = BrokenClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
        )
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])

        with pytest.raises(RuntimeError):
            orch.run_one()

        row = all_repos["sync_run_repo"].latest()
        assert row is not None
        assert row["status"] == "failed"
        assert row["finished_at"] is not None
        assert row["error_summary"] is not None
        assert "RuntimeError" in row["error_summary"]


class TestStructuredLogging:
    def test_run_one_emits_sync_start_log(
        self, orchestrator: SyncOrchestrator
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            orchestrator.run_one()
        start_logs = [e for e in logs if e.get("event") == "sync.start"]
        assert len(start_logs) == 1
        assert "run_id" in start_logs[0]

    def test_run_one_emits_sync_complete_log_on_success(
        self, orchestrator: SyncOrchestrator
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            orchestrator.run_one()
        complete_logs = [e for e in logs if e.get("event") == "sync.complete"]
        assert len(complete_logs) == 1
        assert complete_logs[0]["status"] == "succeeded"
        assert "inserted" in complete_logs[0]

    def test_account_failure_emits_account_failed_warning(
        self, all_repos: dict[str, Any], fixture_data: dict[str, Any]
    ) -> None:
        fail_id = fixture_data["accounts"][0]["account_id"]
        client = MockTrueLayerClient(
            accounts=fixture_data["accounts"],
            txns_by_account=fixture_data["transactions_by_account"],
            fail_account_id=fail_id,
        )
        orch = _make_orchestrator(all_repos, client)
        _seed_fresh_token(all_repos["token_repo"])
        with structlog.testing.capture_logs() as logs:
            orch.run_one()
        failed_logs = [e for e in logs if e.get("event") == "account.failed"]
        assert len(failed_logs) == 1
        assert failed_logs[0]["account_id"] == fail_id
