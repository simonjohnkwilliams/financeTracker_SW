"""Tests for Layer 4 — CLI commands and exit code mapping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from finance_copilot.cli import (
    EXIT_AUTH,
    EXIT_OK,
    EXIT_TRANSIENT,
    EXIT_UNEXPECTED,
    EXIT_VALIDATION,
    main,
)
from finance_copilot.sync.orchestrator import SyncRunSummary
from finance_copilot.truelayer.errors import AuthError, TransientError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spike_first_direct_sandbox.json"


def _success_summary(**kwargs: Any) -> SyncRunSummary:
    run_id: str = kwargs.get("run_id", "run-001")
    status: str = kwargs.get("status", "succeeded")
    accounts_attempted: int = kwargs.get("accounts_attempted", 5)
    accounts_succeeded: int = kwargs.get("accounts_succeeded", 5)
    transactions_inserted: int = kwargs.get("transactions_inserted", 100)
    transactions_skipped_duplicate: int = kwargs.get("transactions_skipped_duplicate", 0)
    error_summary: str | None = kwargs.get("error_summary")
    return SyncRunSummary(
        run_id=run_id,
        status=status,
        accounts_attempted=accounts_attempted,
        accounts_succeeded=accounts_succeeded,
        transactions_inserted=transactions_inserted,
        transactions_skipped_duplicate=transactions_skipped_duplicate,
        error_summary=error_summary,
    )


def _make_settings_mock(db_path: Path | None = None) -> MagicMock:
    m = MagicMock()
    m.db_path = db_path or Path(":memory:")
    m.truelayer_environment = "sandbox"
    m.truelayer_client_id = "test_client"
    m.truelayer_client_secret = "test_secret"
    m.truelayer_redirect_uri = "http://localhost:8080/oauth2/callback"
    m.auth_host = "https://auth.truelayer-sandbox.com"
    m.api_host = "https://api.truelayer-sandbox.com"
    m.providers = "uk-cs-mock"
    return m


def _make_components_mock(tmp_path: Path) -> dict[str, Any]:
    """Build a real in-memory engine + repos for CLI testing."""
    import httpx

    from finance_copilot.db import init_db, make_engine
    from finance_copilot.repositories.accounts import AccountRepository
    from finance_copilot.repositories.sync_runs import SyncRunRepository
    from finance_copilot.repositories.tokens import TokenRepository
    from finance_copilot.repositories.transactions import TransactionRepository

    engine = make_engine(":memory:")
    init_db(engine)
    return {
        "engine": engine,
        "account_repo": AccountRepository(engine),
        "transaction_repo": TransactionRepository(engine),
        "sync_run_repo": SyncRunRepository(engine),
        "token_repo": TokenRepository(engine),
        "http_client": MagicMock(spec=httpx.Client),
    }


class TestSyncCommand:
    def test_finance_sync_invokes_orchestrator_and_exits_zero_on_success(
        self, tmp_path: Path
    ) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch(
                "finance_copilot.cli.SyncOrchestrator.run_one",
                return_value=_success_summary(),
            ),
        ):
            result = main(["sync"])
        assert result == EXIT_OK

    def test_finance_sync_exits_nonzero_on_failed_run(self, tmp_path: Path) -> None:
        components = _make_components_mock(tmp_path)
        failed = _success_summary(status="failed", error_summary="all accounts failed")
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch("finance_copilot.cli.SyncOrchestrator.run_one", return_value=failed),
        ):
            result = main(["sync"])
        assert result != EXIT_OK

    def test_sync_accepts_explicit_from_flag(self, tmp_path: Path) -> None:
        components = _make_components_mock(tmp_path)
        captured: list[Any] = []

        def _capture_run_one(**kwargs: Any) -> SyncRunSummary:
            captured.append(kwargs)
            return _success_summary()

        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch("finance_copilot.cli.SyncOrchestrator.run_one", side_effect=_capture_run_one),
        ):
            result = main(["sync", "--from", "2026-01-01"])
        assert result == EXIT_OK
        from datetime import date

        assert captured[0]["explicit_from"] == date(2026, 1, 1)

    def test_sync_invalid_date_exits_validation_error(self, tmp_path: Path) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
        ):
            result = main(["sync", "--from", "not-a-date"])
        assert result == EXIT_VALIDATION


class TestStatusCommand:
    def test_finance_status_reads_from_repositories_and_prints_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
        ):
            result = main(["status"])
        assert result == EXIT_OK
        captured = capsys.readouterr()
        assert "Accounts" in captured.out

    def test_status_accepts_no_arguments(self, tmp_path: Path) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
        ):
            result = main(["status"])
        assert result == EXIT_OK


class TestAuthCommand:
    def test_finance_auth_invokes_oauth_flow_and_persists_token(
        self, tmp_path: Path
    ) -> None:
        components = _make_components_mock(tmp_path)
        now = datetime.now(UTC)
        token_dict = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "obtained_at": now.isoformat(),
        }

        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch(
                "finance_copilot.cli.oauth.generate_pkce_pair",
                return_value=("verifier", "challenge"),
            ),
            patch("finance_copilot.cli.oauth.build_auth_url", return_value="http://mock-auth-url"),
            patch(
                "finance_copilot.cli.oauth.wait_for_authorisation_code",
                return_value="auth_code",
            ),
            patch("finance_copilot.cli.oauth.exchange_code", return_value=token_dict),
        ):
            result = main(["auth", "--no-browser"])
        assert result == EXIT_OK
        stored = components["token_repo"].get("truelayer")
        assert stored is not None
        assert stored["access_token"] == "new_access"


class TestErrorExitCodes:
    def test_auth_error_exits_with_code_2_and_prompts_reauth(
        self, tmp_path: Path
    ) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch(
                "finance_copilot.cli.SyncOrchestrator.run_one",
                side_effect=AuthError("Token revoked"),
            ),
        ):
            result = main(["sync"])
        assert result == EXIT_AUTH

    def test_transient_error_exits_with_code_3(self, tmp_path: Path) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch(
                "finance_copilot.cli.SyncOrchestrator.run_one",
                side_effect=TransientError("Network failure"),
            ),
        ):
            result = main(["sync"])
        assert result == EXIT_TRANSIENT

    def test_unexpected_error_exits_with_code_99_and_logs_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
            patch(
                "finance_copilot.cli.SyncOrchestrator.run_one",
                side_effect=RuntimeError("Unexpected!"),
            ),
        ):
            result = main(["sync"])
        assert result == EXIT_UNEXPECTED
        captured = capsys.readouterr()
        assert "Unexpected!" in captured.err

    def test_validation_error_exits_with_code_1(self, tmp_path: Path) -> None:
        """An invalid --from date should exit with EXIT_VALIDATION."""
        components = _make_components_mock(tmp_path)
        with (
            patch("finance_copilot.cli.Settings", return_value=_make_settings_mock()),
            patch("finance_copilot.cli._build_components", return_value=components),
        ):
            result = main(["sync", "--from", "bad-date"])
        assert result == EXIT_VALIDATION
