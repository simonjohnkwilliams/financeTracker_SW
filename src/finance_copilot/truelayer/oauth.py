"""Layer 2a — TrueLayer OAuth helpers: PKCE generation, auth URL, code exchange, token refresh."""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from finance_copilot.truelayer.errors import AuthError, TransientError

SANDBOX_AUTH_HOST = "https://auth.truelayer-sandbox.com"
LIVE_AUTH_HOST = "https://auth.truelayer.com"

DEFAULT_SCOPES = "info accounts balance transactions cards offline_access"
DEFAULT_REDIRECT_URI = "http://localhost:8080/oauth2/callback"
SANDBOX_DEFAULT_PROVIDERS = "uk-cs-mock uk-ob-all uk-oauth-all"


# ---------------------------------------------------------------------------
# PKCE helpers (moved from spike_first_direct.py)
# ---------------------------------------------------------------------------


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE ``(verifier, challenge)`` pair using SHA-256 / S256 method."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return verifier, challenge


def build_auth_url(
    *,
    auth_host: str,
    client_id: str,
    redirect_uri: str,
    challenge: str,
    providers: str = "",
    scope: str = DEFAULT_SCOPES,
    state: str | None = None,
    enable_mock: bool = False,
) -> str:
    """Build the TrueLayer authorisation URL."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "scope": scope,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if providers:
        params["providers"] = providers
    if state:
        params["state"] = state
    if enable_mock:
        params["enable_mock"] = "true"
    return f"{auth_host}/?{urllib.parse.urlencode(params)}"


def wait_for_authorisation_code(
    host: str,
    port: int,
    *,
    expected_state: str | None = None,
    timeout_seconds: int = 300,
) -> str:
    """Listen on ``host:port`` for the OAuth redirect and return the authorisation code."""
    captured: dict[str, Any] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            if "code" in params:
                captured["code"] = params["code"][0]
                captured["state"] = params.get("state", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h1>OK</h1><p>Authorisation received. You can close this window.</p>"
                )
            elif "error" in params:
                captured["error"] = params["error"][0]
                captured["error_description"] = params.get("error_description", [""])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<h1>OAuth error</h1><p>{params['error'][0]}</p>".encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.HTTPServer((host, port), _Handler)

    def _serve() -> None:
        while "code" not in captured and "error" not in captured:
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    server.server_close()

    if "error" in captured:
        raise RuntimeError(
            f"OAuth error: {captured['error']} - {captured.get('error_description', '')}"
        )
    if "code" not in captured:
        raise RuntimeError(f"Timed out after {timeout_seconds}s waiting for authorisation code")
    if expected_state is not None and captured.get("state") != expected_state:
        raise RuntimeError(
            f"State mismatch: expected {expected_state!r}, got {captured.get('state')!r}"
        )
    return cast(str, captured["code"])


# ---------------------------------------------------------------------------
# Token exchange and refresh
# ---------------------------------------------------------------------------


def _build_token_dict(body: dict[str, Any], obtained_at: datetime) -> dict[str, Any]:
    expires_in: int = int(body["expires_in"])
    expires_at = obtained_at + timedelta(seconds=expires_in)
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": expires_at.isoformat(),
        "obtained_at": obtained_at.isoformat(),
    }


def exchange_code(
    client: httpx.Client,
    *,
    auth_host: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> dict[str, Any]:
    """POST to ``/connect/token`` with the ``authorization_code`` grant.

    Returns a dict with ``access_token``, ``refresh_token``, ``expires_at``
    (absolute UTC ISO-8601 string) and ``obtained_at``.
    """
    obtained_at = datetime.now(UTC)
    response = client.post(
        f"{auth_host}/connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": verifier,
        },
    )
    if response.status_code != 200:
        raise AuthError(
            f"Token exchange failed with status {response.status_code}: {response.text}"
        )
    return _build_token_dict(response.json(), obtained_at)


def refresh_token(
    client: httpx.Client,
    *,
    auth_host: str,
    client_id: str,
    client_secret: str,
    refresh_token_value: str,
) -> dict[str, Any]:
    """POST to ``/connect/token`` with the ``refresh_token`` grant.

    Raises:
        AuthError: if the response is 400 with ``error=invalid_grant``.
        TransientError: if the response is 5xx.
    """
    obtained_at = datetime.now(UTC)
    response = client.post(
        f"{auth_host}/connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token_value,
        },
    )

    if response.status_code == 400:
        body = response.json()
        if body.get("error") == "invalid_grant":
            raise AuthError(f"Refresh token rejected by TrueLayer: {body.get('error_description')}")
        # Other 400 — raise generic error
        raise AuthError(f"TrueLayer token endpoint returned 400: {body}")

    if response.status_code >= 500:
        raise TransientError(f"TrueLayer token endpoint returned {response.status_code}")

    if response.status_code != 200:
        raise TransientError(f"TrueLayer token endpoint returned {response.status_code}")

    return _build_token_dict(response.json(), obtained_at)
