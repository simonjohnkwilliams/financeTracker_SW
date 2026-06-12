"""Spike: pull First Direct transactions via TrueLayer into a local file.

Phase 0 of `docs/delivery.md`. Throwaway by design — no canonical schema,
no database, no CLI plumbing beyond what this one script needs.

Run:
    uv run python scripts/spike_first_direct.py

Reads credentials from `creds` (key=value lines) and/or `.env`. The user
opens the printed auth URL, completes consent (TrueLayer sandbox by
default), and the resulting accounts + transactions are dumped to
`output/spike_first_direct_<timestamp>.json`.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

SANDBOX_AUTH_HOST = "https://auth.truelayer-sandbox.com"
SANDBOX_API_HOST = "https://api.truelayer-sandbox.com"
LIVE_AUTH_HOST = "https://auth.truelayer.com"
LIVE_API_HOST = "https://api.truelayer.com"

DEFAULT_SCOPES = "info accounts balance transactions cards offline_access"
DEFAULT_REDIRECT_URI = "http://localhost:8080/oauth2/callback"
# In sandbox we need the mock providers turned on and a non-empty providers
# filter, otherwise the provider-picker page 400s when fetching the list.
SANDBOX_DEFAULT_PROVIDERS = "uk-cs-mock uk-ob-all uk-oauth-all"


@dataclass(frozen=True)
class Config:
    client_id: str
    client_secret: str
    environment: str
    redirect_uri: str
    providers: str

    @property
    def auth_host(self) -> str:
        return SANDBOX_AUTH_HOST if self.environment == "sandbox" else LIVE_AUTH_HOST

    @property
    def api_host(self) -> str:
        return SANDBOX_API_HOST if self.environment == "sandbox" else LIVE_API_HOST


@dataclass(frozen=True)
class Token:
    access_token: str
    refresh_token: str | None
    expires_in: int
    token_type: str


def parse_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def load_credentials(project_root: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    merged.update(parse_kv_file(project_root / ".env"))
    merged.update(parse_kv_file(project_root / "creds"))
    for key in (
        "TRUELAYER_CLIENT_ID",
        "TRUELAYER_CLIENT_SECRET",
        "TRUELAYER_ENVIRONMENT",
        "TRUELAYER_REDIRECT_URI",
        "TRUELAYER_PROVIDERS",
    ):
        if value := os.environ.get(key):
            merged[key] = value
    return merged


def generate_pkce_pair() -> tuple[str, str]:
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
    host: str, port: int, *, expected_state: str | None = None, timeout_seconds: int = 300
) -> str:
    captured: dict[str, Any] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
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
                self.wfile.write(
                    f"<h1>OAuth error</h1><p>{params['error'][0]}</p>".encode()
                )
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.HTTPServer((host, port), Handler)

    def serve() -> None:
        while "code" not in captured and "error" not in captured:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
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


def exchange_code(
    client: httpx.Client,
    *,
    auth_host: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    verifier: str,
) -> Token:
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
    response.raise_for_status()
    body = response.json()
    return Token(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_in=body["expires_in"],
        token_type=body["token_type"],
    )


def fetch_accounts(
    client: httpx.Client, *, api_host: str, access_token: str
) -> list[dict[str, Any]]:
    response = client.get(
        f"{api_host}/data/v1/accounts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return cast(list[dict[str, Any]], response.json().get("results", []))


def fetch_transactions(
    client: httpx.Client, *, api_host: str, access_token: str, account_id: str
) -> list[dict[str, Any]]:
    response = client.get(
        f"{api_host}/data/v1/accounts/{account_id}/transactions",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return cast(list[dict[str, Any]], response.json().get("results", []))


def dump_output(output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"spike_first_direct_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def build_config(creds: dict[str, str]) -> Config:
    missing = [
        key for key in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET") if key not in creds
    ]
    if missing:
        raise SystemExit(f"Missing credentials in `creds` or env: {missing}")
    environment = creds.get("TRUELAYER_ENVIRONMENT", "sandbox")
    default_providers = SANDBOX_DEFAULT_PROVIDERS if environment == "sandbox" else ""
    return Config(
        client_id=creds["TRUELAYER_CLIENT_ID"],
        client_secret=creds["TRUELAYER_CLIENT_SECRET"],
        environment=environment,
        redirect_uri=creds.get("TRUELAYER_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        providers=creds.get("TRUELAYER_PROVIDERS", default_providers),
    )


def run(
    *,
    config: Config,
    output_dir: Path,
    open_browser: bool = True,
    timeout_seconds: int = 300,
) -> Path:
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    parsed = urllib.parse.urlparse(config.redirect_uri)
    listen_host = parsed.hostname or "localhost"
    listen_port = parsed.port or 8080

    auth_url = build_auth_url(
        auth_host=config.auth_host,
        client_id=config.client_id,
        redirect_uri=config.redirect_uri,
        providers=config.providers,
        challenge=challenge,
        state=state,
        enable_mock=config.environment == "sandbox",
    )

    print(f"Environment      : {config.environment}")
    print(f"Redirect URI     : {config.redirect_uri}")
    print(f"Providers filter : {config.providers or '(picker shown in browser)'}")
    print()
    print("Open this URL in your browser and complete the consent flow:")
    print(f"  {auth_url}")
    print()

    if open_browser:
        with contextlib.suppress(Exception):
            webbrowser.open(auth_url)

    code = wait_for_authorisation_code(
        listen_host, listen_port, expected_state=state, timeout_seconds=timeout_seconds
    )
    print("Authorisation code received.")

    with httpx.Client(timeout=30.0) as client:
        token = exchange_code(
            client,
            auth_host=config.auth_host,
            client_id=config.client_id,
            client_secret=config.client_secret,
            code=code,
            redirect_uri=config.redirect_uri,
            verifier=verifier,
        )
        print(f"Access token obtained (expires in {token.expires_in}s).")

        accounts = fetch_accounts(client, api_host=config.api_host, access_token=token.access_token)
        print(f"Accounts: {len(accounts)}")

        transactions_by_account: dict[str, list[dict[str, Any]]] = {}
        for account in accounts:
            account_id = account.get("account_id") or account.get("id") or ""
            if not account_id:
                continue
            txns = fetch_transactions(
                client,
                api_host=config.api_host,
                access_token=token.access_token,
                account_id=account_id,
            )
            transactions_by_account[account_id] = txns

    total_txns = sum(len(v) for v in transactions_by_account.values())
    print(f"Transactions total: {total_txns}")

    output_path = dump_output(
        output_dir,
        {
            "environment": config.environment,
            "providers": config.providers,
            "fetched_at": datetime.now(UTC).isoformat(),
            "account_count": len(accounts),
            "transaction_count": total_txns,
            "accounts": accounts,
            "transactions_by_account": transactions_by_account,
        },
    )
    print(f"Written: {output_path}")
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 0 spike: TrueLayer -> local JSON.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for the JSON output (default: output/).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser; just print the URL.",
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parent.parent
    creds = load_credentials(project_root)
    config = build_config(creds)

    try:
        run(config=config, output_dir=args.output_dir, open_browser=not args.no_browser)
    except RuntimeError as e:
        print(f"Spike failed: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(
            f"HTTP error {e.response.status_code} from {e.request.url}: {e.response.text}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
