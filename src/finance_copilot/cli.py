"""Layer 4 — ``finance`` CLI entry-point.

Commands:
- ``finance auth``   — OAuth PKCE consent flow; persists token to DB
- ``finance sync``   — Run a transaction sync; prints summary
- ``finance status`` — Show last sync run and DB counts
"""

from __future__ import annotations

import argparse
import secrets
import sys
import urllib.parse
from typing import Any

from finance_copilot.config import Settings
from finance_copilot.sync.orchestrator import SyncOrchestrator
from finance_copilot.truelayer import oauth
from finance_copilot.truelayer.errors import AuthError, TransientError

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_AUTH = 2
EXIT_TRANSIENT = 3
EXIT_UNEXPECTED = 99


def _build_components(settings: Any) -> dict[str, Any]:
    """Construct all DB and repository components from settings."""
    import httpx

    from finance_copilot.db import check_schema_version, init_db, make_engine
    from finance_copilot.repositories.accounts import AccountRepository
    from finance_copilot.repositories.sync_runs import SyncRunRepository
    from finance_copilot.repositories.tokens import TokenRepository
    from finance_copilot.repositories.transactions import TransactionRepository

    engine = make_engine(str(settings.db_path))
    init_db(engine)
    check_schema_version(engine)

    return {
        "engine": engine,
        "account_repo": AccountRepository(engine),
        "transaction_repo": TransactionRepository(engine),
        "sync_run_repo": SyncRunRepository(engine),
        "token_repo": TokenRepository(engine),
        "http_client": httpx.Client(timeout=30.0),
    }


def cmd_auth(args: argparse.Namespace) -> int:
    """``finance auth`` — initiate OAuth consent and persist token."""
    import contextlib
    import webbrowser

    settings = Settings()
    components = _build_components(settings)
    token_repo = components["token_repo"]
    http_client = components["http_client"]

    verifier, challenge = oauth.generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    parsed = urllib.parse.urlparse(settings.truelayer_redirect_uri)
    listen_host = parsed.hostname or "localhost"
    listen_port = parsed.port or 8080

    auth_url = oauth.build_auth_url(
        auth_host=settings.auth_host,
        client_id=settings.truelayer_client_id,
        redirect_uri=settings.truelayer_redirect_uri,
        challenge=challenge,
        providers=settings.providers,
        state=state,
        enable_mock=settings.truelayer_environment == "sandbox",
    )

    print(f"Environment : {settings.truelayer_environment}")
    print()
    print("Open this URL in your browser to authorise:")
    print(f"  {auth_url}")
    print()

    if not getattr(args, "no_browser", False):
        with contextlib.suppress(Exception):
            webbrowser.open(auth_url)

    try:
        code = oauth.wait_for_authorisation_code(
            listen_host,
            listen_port,
            expected_state=state,
            timeout_seconds=300,
        )
    except RuntimeError as exc:
        print(f"Auth failed: {exc}", file=sys.stderr)
        return EXIT_AUTH

    token_dict = oauth.exchange_code(
        http_client,
        auth_host=settings.auth_host,
        client_id=settings.truelayer_client_id,
        client_secret=settings.truelayer_client_secret,
        code=code,
        redirect_uri=settings.truelayer_redirect_uri,
        verifier=verifier,
    )

    token_repo.put(
        "truelayer",
        token_dict["access_token"],
        token_dict["refresh_token"],
        token_dict["expires_at"],
        token_dict["obtained_at"],
    )
    print("Token stored successfully.")
    return EXIT_OK


def cmd_sync(args: argparse.Namespace) -> int:
    """``finance sync`` — sync transactions from TrueLayer."""
    from datetime import date as date_type

    settings = Settings()
    components = _build_components(settings)

    explicit_from: date_type | None = None
    from_str: str | None = getattr(args, "from_date", None)
    if from_str:
        try:
            explicit_from = date_type.fromisoformat(from_str)
        except ValueError:
            print(f"Invalid date format: {from_str!r}. Use YYYY-MM-DD.", file=sys.stderr)
            return EXIT_VALIDATION

    orch = SyncOrchestrator(
        account_repo=components["account_repo"],
        transaction_repo=components["transaction_repo"],
        sync_run_repo=components["sync_run_repo"],
        token_repo=components["token_repo"],
        auth_host=settings.auth_host,
        api_host=settings.api_host,
        client_id=settings.truelayer_client_id,
        client_secret=settings.truelayer_client_secret,
        redirect_uri=settings.truelayer_redirect_uri,
        http_client=components["http_client"],
    )

    result = orch.run_one(explicit_from=explicit_from)

    print(f"Sync {result.status}")
    print(f"  Accounts  : {result.accounts_succeeded}/{result.accounts_attempted} succeeded")
    print(f"  Inserted  : {result.transactions_inserted}")
    print(f"  Skipped   : {result.transactions_skipped_duplicate} (duplicate)")
    if result.error_summary:
        print(f"  Errors    : {result.error_summary}", file=sys.stderr)

    if result.status == "failed":
        return EXIT_TRANSIENT
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    """``finance status`` — show last sync and DB counts."""
    settings = Settings()
    components = _build_components(settings)

    account_repo = components["account_repo"]
    transaction_repo = components["transaction_repo"]
    sync_run_repo = components["sync_run_repo"]

    account_count = account_repo.count()
    transaction_count = transaction_repo.count()
    latest_run = sync_run_repo.latest()

    print(f"DB path     : {settings.db_path}")
    print(f"Accounts    : {account_count}")
    print(f"Transactions: {transaction_count}")
    print()
    if latest_run:
        print("Last sync run")
        print(f"  Run ID    : {latest_run['run_id']}")
        print(f"  Status    : {latest_run['status']}")
        print(f"  Started   : {latest_run['started_at']}")
        print(f"  Finished  : {latest_run.get('finished_at', 'N/A')}")
        print(f"  Inserted  : {latest_run['transactions_inserted']}")
        print(f"  Skipped   : {latest_run['transactions_skipped_duplicate']}")
        if latest_run.get("error_summary"):
            print(f"  Errors    : {latest_run['error_summary']}", file=sys.stderr)
    else:
        print("No sync runs recorded yet.")

    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="finance",
        description="Finance Copilot — personal finance + landlord/tax tool.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # auth
    auth_p = sub.add_parser("auth", help="Authenticate against TrueLayer (one-time consent)")
    auth_p.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser")
    auth_p.set_defaults(func=cmd_auth)

    # sync
    sync_p = sub.add_parser("sync", help="Sync transactions from TrueLayer to local DB")
    sync_p.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Override the from-date for the transactions fetch",
    )
    sync_p.set_defaults(func=cmd_sync)

    # status
    status_p = sub.add_parser("status", help="Show last sync status and DB counts")
    status_p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point — called by the ``finance`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AuthError:
        print("Authentication error. Run: finance auth", file=sys.stderr)
        return EXIT_AUTH
    except TransientError:
        print("Transient error — please retry.", file=sys.stderr)
        return EXIT_TRANSIENT
    except Exception:
        import traceback

        traceback.print_exc()
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
