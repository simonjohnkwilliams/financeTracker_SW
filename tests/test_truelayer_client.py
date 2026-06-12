"""Tests for Layer 2b — TrueLayerClient with mocked httpx."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from finance_copilot.truelayer.client import RETRY_DELAYS, TrueLayerClient
from finance_copilot.truelayer.errors import AuthError, RateLimitError, TransientError

SANDBOX_API = "https://api.truelayer-sandbox.com"
ACCESS_TOKEN = "test_access_token"


def _make_client(http_client: MagicMock) -> TrueLayerClient:
    return TrueLayerClient(
        api_host=SANDBOX_API,
        access_token=ACCESS_TOKEN,
        http_client=http_client,
    )


def _ok_response(body: object) -> httpx.Response:
    return httpx.Response(200, json=body)


def _error_response(status: int) -> httpx.Response:
    request = httpx.Request("GET", "https://api.truelayer-sandbox.com/test")
    return httpx.Response(status, json={"error": "test"}, request=request)


SAMPLE_ACCOUNTS = [
    {
        "account_id": "acc001",
        "account_type": "TRANSACTION",
        "display_name": "Current",
        "currency": "GBP",
        "provider": {"provider_id": "mock"},
    }
]

SAMPLE_TXNS = [
    {
        "transaction_id": "txn001",
        "timestamp": "2026-06-12T00:00:00Z",
        "description": "TESCO",
        "transaction_type": "DEBIT",
        "amount": -12.50,
        "currency": "GBP",
        "transaction_category": "PURCHASE",
    }
]


class TestFetchAccounts:
    def test_fetch_accounts_sends_bearer_token(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": SAMPLE_ACCOUNTS})
        client = _make_client(mock_client)
        client.fetch_accounts()
        auth_header = mock_client.get.call_args.kwargs["headers"]["Authorization"]
        assert auth_header == f"Bearer {ACCESS_TOKEN}"

    def test_fetch_accounts_returns_results_list(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": SAMPLE_ACCOUNTS})
        client = _make_client(mock_client)
        result = client.fetch_accounts()
        assert result == SAMPLE_ACCOUNTS

    def test_fetch_accounts_handles_empty_results(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": []})
        client = _make_client(mock_client)
        assert client.fetch_accounts() == []


class TestFetchTransactions:
    def test_fetch_transactions_sends_from_parameter_when_provided(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": SAMPLE_TXNS})
        client = _make_client(mock_client)
        client.fetch_transactions("acc001", from_date=date(2026, 3, 14))
        params = mock_client.get.call_args.kwargs.get("params") or {}
        assert params.get("from") == "2026-03-14"

    def test_fetch_transactions_omits_from_when_none(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": SAMPLE_TXNS})
        client = _make_client(mock_client)
        client.fetch_transactions("acc001", from_date=None)
        call_kwargs = mock_client.get.call_args
        # The params dict should be absent or empty
        _, kwargs = call_kwargs
        params = kwargs.get("params") or {}
        assert "from" not in params

    def test_fetch_transactions_calls_correct_url(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": []})
        client = _make_client(mock_client)
        client.fetch_transactions("acc001")
        url_arg = str(mock_client.get.call_args)
        assert "acc001/transactions" in url_arg

    def test_client_parses_amounts_as_decimal_at_boundary(self) -> None:
        # The client should use json.loads(parse_float=Decimal) on the raw response
        mock_client = MagicMock(spec=httpx.Client)
        # Return a response whose body has a float amount
        raw_body = '{"results": [{"transaction_id": "t1", "amount": -12.50, "currency": "GBP"}]}'
        response = httpx.Response(200, content=raw_body.encode())
        mock_client.get.return_value = response
        client = _make_client(mock_client)
        txns = client.fetch_transactions("acc001")
        assert isinstance(txns[0]["amount"], Decimal)


class TestRetryLogic:
    def test_client_retries_on_429_with_exponential_backoff(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        # All 3 attempts return 429, then raise RateLimitError
        mock_client.get.return_value = _error_response(429)
        client = _make_client(mock_client)
        with patch("finance_copilot.truelayer.client.time.sleep") as mock_sleep:
            with pytest.raises(RateLimitError):
                client.fetch_accounts()
            # Should have slept between retries
            assert mock_sleep.call_count == len(RETRY_DELAYS)
            sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
            assert sleep_args == RETRY_DELAYS

    def test_client_retries_on_5xx(self) -> None:
        from finance_copilot.truelayer.client import MAX_RETRIES

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _error_response(503)
        client = _make_client(mock_client)
        with (
            patch("finance_copilot.truelayer.client.time.sleep"),
            pytest.raises(TransientError),
        ):
            client.fetch_accounts()
        # Should have made MAX_RETRIES total attempts
        assert mock_client.get.call_count == MAX_RETRIES

    def test_client_succeeds_on_retry_after_transient(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        # First call: 503, second call: 200
        mock_client.get.side_effect = [
            _error_response(503),
            _ok_response({"results": SAMPLE_ACCOUNTS}),
        ]
        client = _make_client(mock_client)
        with patch("finance_copilot.truelayer.client.time.sleep"):
            result = client.fetch_accounts()
        assert result == SAMPLE_ACCOUNTS

    def test_client_does_not_retry_on_4xx_except_429(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _error_response(400)
        client = _make_client(mock_client)
        with (
            patch("finance_copilot.truelayer.client.time.sleep") as mock_sleep,
            pytest.raises(httpx.HTTPStatusError),
        ):
            client.fetch_accounts()
        # No retries for generic 4xx
        assert mock_sleep.call_count == 0
        assert mock_client.get.call_count == 1

    def test_client_raises_auth_error_on_401(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _error_response(401)
        client = _make_client(mock_client)
        with pytest.raises(AuthError):
            client.fetch_accounts()


class TestPaginationDetection:
    def test_fetch_transactions_logs_warning_when_next_key_present(self) -> None:
        import structlog.testing

        mock_client = MagicMock(spec=httpx.Client)
        raw_body = b'{"results": [], "next": "/transactions?cursor=abc123"}'
        mock_client.get.return_value = httpx.Response(200, content=raw_body)
        client = _make_client(mock_client)
        with structlog.testing.capture_logs() as logs:
            client.fetch_transactions("acc001")
        warning_logs = [e for e in logs if e.get("log_level") == "warning"]
        assert any(e.get("event") == "pagination.detected" for e in warning_logs)

    def test_fetch_transactions_no_warning_without_pagination(self) -> None:
        import structlog.testing

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = _ok_response({"results": SAMPLE_TXNS})
        client = _make_client(mock_client)
        with structlog.testing.capture_logs() as logs:
            client.fetch_transactions("acc001")
        pagination_logs = [e for e in logs if e.get("event") == "pagination.detected"]
        assert len(pagination_logs) == 0
