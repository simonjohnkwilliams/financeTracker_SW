"""Happy-path tests for the Phase 0 First Direct spike.

These tests don't talk to TrueLayer. The real test is running
`scripts/spike_first_direct.py` against the sandbox and inspecting
`output/spike_first_direct_*.json`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from spike_first_direct import (
    DEFAULT_REDIRECT_URI,
    DEFAULT_SCOPES,
    LIVE_API_HOST,
    LIVE_AUTH_HOST,
    SANDBOX_API_HOST,
    SANDBOX_AUTH_HOST,
    SANDBOX_DEFAULT_PROVIDERS,
    build_auth_url,
    build_config,
    dump_output,
    exchange_code,
    fetch_accounts,
    fetch_transactions,
    generate_pkce_pair,
    load_credentials,
    parse_kv_file,
)


def test_generate_pkce_pair_shape() -> None:
    verifier, challenge = generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier
    assert "=" not in challenge
    assert len(challenge) == 43  # SHA-256 base64url-encoded without padding


def test_pkce_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected


def test_pkce_pair_is_random_each_call() -> None:
    v1, c1 = generate_pkce_pair()
    v2, c2 = generate_pkce_pair()
    assert v1 != v2
    assert c1 != c2


def test_build_auth_url_required_params() -> None:
    url = build_auth_url(
        auth_host=SANDBOX_AUTH_HOST,
        client_id="cid",
        redirect_uri=DEFAULT_REDIRECT_URI,
        challenge="abc",
        providers="uk-ob-firstdirect",
        state="state-xyz",
    )
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.truelayer-sandbox.com"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["cid"]
    assert query["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    assert query["scope"] == [DEFAULT_SCOPES]
    assert query["code_challenge"] == ["abc"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["providers"] == ["uk-ob-firstdirect"]
    assert query["state"] == ["state-xyz"]


def test_build_auth_url_omits_providers_when_empty() -> None:
    url = build_auth_url(
        auth_host=SANDBOX_AUTH_HOST,
        client_id="cid",
        redirect_uri=DEFAULT_REDIRECT_URI,
        challenge="abc",
    )
    assert "providers=" not in url


def test_build_auth_url_enables_mock_when_requested() -> None:
    url = build_auth_url(
        auth_host=SANDBOX_AUTH_HOST,
        client_id="cid",
        redirect_uri=DEFAULT_REDIRECT_URI,
        challenge="abc",
        enable_mock=True,
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["enable_mock"] == ["true"]


def test_build_auth_url_omits_enable_mock_by_default() -> None:
    url = build_auth_url(
        auth_host=SANDBOX_AUTH_HOST,
        client_id="cid",
        redirect_uri=DEFAULT_REDIRECT_URI,
        challenge="abc",
    )
    assert "enable_mock=" not in url


def test_parse_kv_file_handles_leading_whitespace_and_comments(tmp_path: Path) -> None:
    creds = tmp_path / "creds"
    creds.write_text(
        " TRUELAYER_CLIENT_ID=cid-with-space\n"
        "TRUELAYER_CLIENT_SECRET=secret-value\n"
        "# a comment\n"
        "\n"
        " RedirectURLS=a;b;c\n",
        encoding="utf-8",
    )
    parsed = parse_kv_file(creds)
    assert parsed["TRUELAYER_CLIENT_ID"] == "cid-with-space"
    assert parsed["TRUELAYER_CLIENT_SECRET"] == "secret-value"
    assert parsed["RedirectURLS"] == "a;b;c"


def test_parse_kv_file_missing_returns_empty(tmp_path: Path) -> None:
    assert parse_kv_file(tmp_path / "nope") == {}


def test_load_credentials_env_overrides_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "creds").write_text("TRUELAYER_CLIENT_ID=from-file\n", encoding="utf-8")
    monkeypatch.setenv("TRUELAYER_CLIENT_ID", "from-env")
    creds = load_credentials(tmp_path)
    assert creds["TRUELAYER_CLIENT_ID"] == "from-env"


def test_load_credentials_falls_back_to_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "creds").write_text("TRUELAYER_CLIENT_ID=from-file\n", encoding="utf-8")
    monkeypatch.delenv("TRUELAYER_CLIENT_ID", raising=False)
    creds = load_credentials(tmp_path)
    assert creds["TRUELAYER_CLIENT_ID"] == "from-file"


def test_build_config_defaults_to_sandbox_with_mock_providers() -> None:
    config = build_config({"TRUELAYER_CLIENT_ID": "cid", "TRUELAYER_CLIENT_SECRET": "cs"})
    assert config.environment == "sandbox"
    assert config.auth_host == SANDBOX_AUTH_HOST
    assert config.api_host == SANDBOX_API_HOST
    assert config.redirect_uri == DEFAULT_REDIRECT_URI
    assert config.providers == SANDBOX_DEFAULT_PROVIDERS


def test_build_config_live_environment_switches_hosts_and_empties_providers() -> None:
    config = build_config(
        {
            "TRUELAYER_CLIENT_ID": "cid",
            "TRUELAYER_CLIENT_SECRET": "cs",
            "TRUELAYER_ENVIRONMENT": "live",
        }
    )
    assert config.auth_host == LIVE_AUTH_HOST
    assert config.api_host == LIVE_API_HOST
    assert config.providers == ""


def test_build_config_explicit_providers_override_sandbox_default() -> None:
    config = build_config(
        {
            "TRUELAYER_CLIENT_ID": "cid",
            "TRUELAYER_CLIENT_SECRET": "cs",
            "TRUELAYER_PROVIDERS": "uk-cs-mock",
        }
    )
    assert config.providers == "uk-cs-mock"


def test_build_config_raises_when_credentials_missing() -> None:
    with pytest.raises(SystemExit):
        build_config({})


def _ok_response(payload: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_exchange_code_posts_expected_form_and_returns_token() -> None:
    client = MagicMock()
    client.post.return_value = _ok_response(
        {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )

    token = exchange_code(
        client,
        auth_host=SANDBOX_AUTH_HOST,
        client_id="cid",
        client_secret="cs",
        code="the-code",
        redirect_uri=DEFAULT_REDIRECT_URI,
        verifier="the-verifier",
    )

    assert token.access_token == "AT"
    assert token.refresh_token == "RT"
    assert token.expires_in == 3600
    assert token.token_type == "Bearer"

    client.post.assert_called_once()
    args, kwargs = client.post.call_args
    assert args[0] == f"{SANDBOX_AUTH_HOST}/connect/token"
    assert kwargs["data"] == {
        "grant_type": "authorization_code",
        "client_id": "cid",
        "client_secret": "cs",
        "redirect_uri": DEFAULT_REDIRECT_URI,
        "code": "the-code",
        "code_verifier": "the-verifier",
    }


def test_fetch_accounts_returns_results_list_and_sends_bearer() -> None:
    client = MagicMock()
    client.get.return_value = _ok_response(
        {"results": [{"account_id": "a1"}, {"account_id": "a2"}]}
    )

    accounts = fetch_accounts(client, api_host=SANDBOX_API_HOST, access_token="AT")
    assert [a["account_id"] for a in accounts] == ["a1", "a2"]

    args, kwargs = client.get.call_args
    assert args[0] == f"{SANDBOX_API_HOST}/data/v1/accounts"
    assert kwargs["headers"] == {"Authorization": "Bearer AT"}


def test_fetch_transactions_targets_correct_account_path() -> None:
    client = MagicMock()
    client.get.return_value = _ok_response(
        {
            "results": [
                {"transaction_id": "t1", "amount": -12.34, "currency": "GBP"},
                {"transaction_id": "t2", "amount": 100.0, "currency": "GBP"},
            ]
        }
    )

    txns = fetch_transactions(
        client, api_host=SANDBOX_API_HOST, access_token="AT", account_id="acct-99"
    )
    assert len(txns) == 2
    args, _ = client.get.call_args
    assert args[0] == f"{SANDBOX_API_HOST}/data/v1/accounts/acct-99/transactions"


def test_fetch_accounts_returns_empty_when_no_results_key() -> None:
    client = MagicMock()
    client.get.return_value = _ok_response({})
    assert fetch_accounts(client, api_host=SANDBOX_API_HOST, access_token="AT") == []


def test_dump_output_writes_timestamped_json(tmp_path: Path) -> None:
    payload = {
        "environment": "sandbox",
        "accounts": [{"account_id": "a1"}],
        "transactions_by_account": {"a1": [{"transaction_id": "t1"}]},
    }
    path = dump_output(tmp_path, payload)

    assert path.parent == tmp_path
    assert path.name.startswith("spike_first_direct_")
    assert path.name.endswith(".json")
    assert json.loads(path.read_text(encoding="utf-8")) == payload


def test_dump_output_creates_directory_if_missing(tmp_path: Path) -> None:
    output_dir = tmp_path / "nested" / "output"
    assert not output_dir.exists()
    path = dump_output(output_dir, {})
    assert output_dir.is_dir()
    assert path.exists()
