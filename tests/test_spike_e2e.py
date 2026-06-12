"""Behavioural end-to-end tests for spike_first_direct.

Uses tests/fixtures/spike_first_direct_sandbox.json — the actual TrueLayer
sandbox output (5 accounts, 2190 transactions) — as mock HTTP response data.
All network calls are intercepted; these tests run offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from spike_first_direct import (
    SANDBOX_API_HOST,
    SANDBOX_AUTH_HOST,
    Config,
    build_config,
    run,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sandbox_fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES_DIR / "spike_first_direct_sandbox.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture
def sandbox_config() -> Config:
    return build_config({"TRUELAYER_CLIENT_ID": "test-cid", "TRUELAYER_CLIENT_SECRET": "test-cs"})


def _ok_response(payload: dict[str, Any]) -> MagicMock:
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def _build_mock_client(fixture: dict[str, Any]) -> MagicMock:
    accounts = fixture["accounts"]
    txns_by_account = fixture["transactions_by_account"]

    mock_client = MagicMock()
    mock_client.post.return_value = _ok_response(
        {
            "access_token": "test-AT",
            "refresh_token": "test-RT",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )

    def mock_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/accounts"):
            return _ok_response({"results": accounts})
        for acc in accounts:
            if url.endswith(f"/accounts/{acc['account_id']}/transactions"):
                return _ok_response({"results": txns_by_account[acc["account_id"]]})
        return _ok_response({})

    mock_client.get.side_effect = mock_get
    return mock_client


def _make_ctx(mock_client: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_client)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _run_with_mocks(config: Config, fixture: dict[str, Any], output_dir: Path) -> Path:
    mock_client = _build_mock_client(fixture)
    with (
        patch("spike_first_direct.wait_for_authorisation_code", return_value="auth-code"),
        patch("spike_first_direct.httpx.Client", return_value=_make_ctx(mock_client)),
    ):
        return run(config=config, output_dir=output_dir, open_browser=False)


# ---------------------------------------------------------------------------
# Fixture shape: validates the real sandbox payload matches TrueLayer contract
# ---------------------------------------------------------------------------


def test_fixture_top_level_keys(sandbox_fixture: dict[str, Any]) -> None:
    expected = {
        "environment", "providers", "fetched_at",
        "account_count", "transaction_count", "accounts", "transactions_by_account",
    }
    assert expected <= sandbox_fixture.keys()


def test_fixture_account_count(sandbox_fixture: dict[str, Any]) -> None:
    assert sandbox_fixture["account_count"] == 5
    assert len(sandbox_fixture["accounts"]) == 5


def test_fixture_transaction_count_is_consistent(sandbox_fixture: dict[str, Any]) -> None:
    total = sum(len(v) for v in sandbox_fixture["transactions_by_account"].values())
    assert total == sandbox_fixture["transaction_count"]
    assert total == 2190


def test_fixture_accounts_have_required_fields(sandbox_fixture: dict[str, Any]) -> None:
    required = {"account_id", "account_type", "display_name", "currency"}
    for acc in sandbox_fixture["accounts"]:
        missing = required - acc.keys()
        assert not missing, f"Account {acc.get('account_id')} missing: {missing}"


def test_fixture_transactions_have_required_fields(sandbox_fixture: dict[str, Any]) -> None:
    required = {
        "transaction_id", "timestamp", "amount", "currency",
        "transaction_type", "description", "transaction_category",
    }
    for acc_id, txns in sandbox_fixture["transactions_by_account"].items():
        for txn in txns[:3]:
            missing = required - txn.keys()
            assert not missing, f"Txn in {acc_id} missing: {missing}"


def test_fixture_debit_amounts_are_negative(sandbox_fixture: dict[str, Any]) -> None:
    for txns in sandbox_fixture["transactions_by_account"].values():
        for txn in txns:
            if txn["transaction_type"] == "DEBIT":
                assert txn["amount"] < 0, f"DEBIT not negative: {txn['transaction_id']}"


def test_fixture_credit_amounts_are_positive(sandbox_fixture: dict[str, Any]) -> None:
    for txns in sandbox_fixture["transactions_by_account"].values():
        for txn in txns:
            if txn["transaction_type"] == "CREDIT":
                assert txn["amount"] > 0, f"CREDIT not positive: {txn['transaction_id']}"


def test_fixture_transaction_ids_are_unique(sandbox_fixture: dict[str, Any]) -> None:
    all_ids = [
        txn["transaction_id"]
        for txns in sandbox_fixture["transactions_by_account"].values()
        for txn in txns
    ]
    assert len(all_ids) == len(set(all_ids)), "Transaction IDs are not unique across accounts"


def test_fixture_all_accounts_have_transactions_entry(sandbox_fixture: dict[str, Any]) -> None:
    for acc in sandbox_fixture["accounts"]:
        assert acc["account_id"] in sandbox_fixture["transactions_by_account"]


# ---------------------------------------------------------------------------
# Behavioural end-to-end: run() orchestrates the full flow with mocked HTTP
# ---------------------------------------------------------------------------


def test_e2e_run_writes_named_output_file(
    sandbox_fixture: dict[str, Any], sandbox_config: Config, tmp_path: Path
) -> None:
    output_path = _run_with_mocks(sandbox_config, sandbox_fixture, tmp_path)
    assert output_path.exists()
    assert output_path.name.startswith("spike_first_direct_")
    assert output_path.suffix == ".json"


def test_e2e_run_output_structure_matches_fixture(
    sandbox_fixture: dict[str, Any], sandbox_config: Config, tmp_path: Path
) -> None:
    output_path = _run_with_mocks(sandbox_config, sandbox_fixture, tmp_path)
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["environment"] == "sandbox"
    assert result["account_count"] == sandbox_fixture["account_count"]
    assert result["transaction_count"] == sandbox_fixture["transaction_count"]
    assert len(result["accounts"]) == len(sandbox_fixture["accounts"])


def test_e2e_run_output_preserves_all_transaction_ids(
    sandbox_fixture: dict[str, Any], sandbox_config: Config, tmp_path: Path
) -> None:
    output_path = _run_with_mocks(sandbox_config, sandbox_fixture, tmp_path)
    result = json.loads(output_path.read_text(encoding="utf-8"))
    fixture_ids = {
        txn["transaction_id"]
        for txns in sandbox_fixture["transactions_by_account"].values()
        for txn in txns
    }
    result_ids = {
        txn["transaction_id"]
        for txns in result["transactions_by_account"].values()
        for txn in txns
    }
    assert result_ids == fixture_ids


def test_e2e_run_posts_auth_code_to_token_endpoint(
    sandbox_fixture: dict[str, Any], sandbox_config: Config, tmp_path: Path
) -> None:
    mock_client = _build_mock_client(sandbox_fixture)
    with (
        patch("spike_first_direct.wait_for_authorisation_code", return_value="the-code-xyz"),
        patch("spike_first_direct.httpx.Client", return_value=_make_ctx(mock_client)),
    ):
        run(config=sandbox_config, output_dir=tmp_path, open_browser=False)

    args, kwargs = mock_client.post.call_args
    assert args[0] == f"{SANDBOX_AUTH_HOST}/connect/token"
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "the-code-xyz"
    assert kwargs["data"]["client_id"] == "test-cid"


def test_e2e_run_fetches_transactions_for_all_accounts(
    sandbox_fixture: dict[str, Any], sandbox_config: Config, tmp_path: Path
) -> None:
    mock_client = _build_mock_client(sandbox_fixture)
    with (
        patch("spike_first_direct.wait_for_authorisation_code", return_value="auth-code"),
        patch("spike_first_direct.httpx.Client", return_value=_make_ctx(mock_client)),
    ):
        run(config=sandbox_config, output_dir=tmp_path, open_browser=False)

    get_urls = [call.args[0] for call in mock_client.get.call_args_list]
    for acc in sandbox_fixture["accounts"]:
        expected = f"{SANDBOX_API_HOST}/data/v1/accounts/{acc['account_id']}/transactions"
        assert expected in get_urls, f"Missing transactions fetch for {acc['account_id']}"


def test_e2e_run_sends_bearer_token_on_all_data_requests(
    sandbox_fixture: dict[str, Any], sandbox_config: Config, tmp_path: Path
) -> None:
    mock_client = _build_mock_client(sandbox_fixture)
    with (
        patch("spike_first_direct.wait_for_authorisation_code", return_value="auth-code"),
        patch("spike_first_direct.httpx.Client", return_value=_make_ctx(mock_client)),
    ):
        run(config=sandbox_config, output_dir=tmp_path, open_browser=False)

    for call in mock_client.get.call_args_list:
        _, kwargs = call
        assert kwargs.get("headers", {}).get("Authorization") == "Bearer test-AT"
