"""Tests for Layer 2a — OAuth exchange and refresh."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest

from finance_copilot.truelayer.errors import AuthError, TransientError
from finance_copilot.truelayer.oauth import exchange_code, refresh_token


def _mock_response(status_code: int, json_body: dict[str, object]) -> httpx.Response:
    return httpx.Response(status_code, json=json_body)


class TestExchangeCode:
    def test_exchange_code_returns_token_with_absolute_expires_at(self) -> None:
        before = datetime.now(UTC)
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(
            200,
            {
                "access_token": "access_abc",
                "refresh_token": "refresh_xyz",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )
        result = exchange_code(
            mock_client,
            auth_host="https://auth.truelayer-sandbox.com",
            client_id="my_client",
            client_secret="my_secret",
            code="auth_code_123",
            redirect_uri="http://localhost:8080/oauth2/callback",
            verifier="pkce_verifier",
        )
        after = datetime.now(UTC)

        assert result["access_token"] == "access_abc"
        assert result["refresh_token"] == "refresh_xyz"
        assert "expires_at" in result
        assert "obtained_at" in result

        expires_at = datetime.fromisoformat(result["expires_at"])
        obtained_at = datetime.fromisoformat(result["obtained_at"])

        # expires_at should be approximately obtained_at + 3600s
        diff = expires_at - obtained_at
        assert abs(diff.total_seconds() - 3600) < 2

        # obtained_at should be within the test window
        assert before <= obtained_at <= after

    def test_exchange_code_posts_to_correct_endpoint(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(
            200,
            {"access_token": "a", "refresh_token": "r", "token_type": "Bearer", "expires_in": 100},
        )
        exchange_code(
            mock_client,
            auth_host="https://auth.truelayer-sandbox.com",
            client_id="my_client",
            client_secret="my_secret",
            code="code",
            redirect_uri="http://localhost:8080/oauth2/callback",
            verifier="verifier",
        )
        call_kwargs = mock_client.post.call_args
        assert "https://auth.truelayer-sandbox.com/connect/token" in str(call_kwargs)

    def test_exchange_code_sends_correct_grant_type(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(
            200,
            {"access_token": "a", "refresh_token": "r", "token_type": "Bearer", "expires_in": 100},
        )
        exchange_code(
            mock_client,
            auth_host="https://auth.truelayer-sandbox.com",
            client_id="my_client",
            client_secret="my_secret",
            code="code",
            redirect_uri="http://localhost:8080/oauth2/callback",
            verifier="verifier",
        )
        call_kwargs = mock_client.post.call_args
        assert "authorization_code" in str(call_kwargs)


class TestRefreshToken:
    def test_refresh_token_sends_grant_type_refresh_token(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(
            200,
            {"access_token": "new_access", "refresh_token": "new_refresh", "expires_in": 3600},
        )
        refresh_token(
            mock_client,
            auth_host="https://auth.truelayer-sandbox.com",
            client_id="my_client",
            client_secret="my_secret",
            refresh_token_value="old_refresh",
        )
        call_kwargs = mock_client.post.call_args
        assert "refresh_token" in str(call_kwargs)

    def test_refresh_token_raises_auth_error_on_invalid_grant_400(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(
            400,
            {"error": "invalid_grant", "error_description": "Refresh token expired"},
        )
        with pytest.raises(AuthError):
            refresh_token(
                mock_client,
                auth_host="https://auth.truelayer-sandbox.com",
                client_id="my_client",
                client_secret="my_secret",
                refresh_token_value="expired_refresh",
            )

    def test_refresh_token_raises_transient_error_on_5xx(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(500, {"error": "server_error"})
        with pytest.raises(TransientError):
            refresh_token(
                mock_client,
                auth_host="https://auth.truelayer-sandbox.com",
                client_id="my_client",
                client_secret="my_secret",
                refresh_token_value="some_refresh",
            )

    def test_refresh_token_returns_token_dict_on_success(self) -> None:
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = _mock_response(
            200,
            {"access_token": "new_access", "refresh_token": "new_refresh", "expires_in": 3600},
        )
        result = refresh_token(
            mock_client,
            auth_host="https://auth.truelayer-sandbox.com",
            client_id="my_client",
            client_secret="my_secret",
            refresh_token_value="old_refresh",
        )
        assert result["access_token"] == "new_access"
        assert "expires_at" in result
        assert "obtained_at" in result
