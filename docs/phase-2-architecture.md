# Phase 2 — Architecture

**Status:** design only — no implementation. Approval gate before any code.

**Outcome statement:**

> *I can connect Finance Copilot to multiple UK banks (First Direct, NatWest,
> Amex) and sync transactions from all of them with a single `finance sync`
> invocation. When a connection's 90-day PSD2 consent expires mid-sync, the
> CLI prompts me to re-authorise without losing the rest of the sync.*

Phase 2 ends when a user can:

1. List multiple banks in their `creds` file and run `finance auth` once to
   consent to all of them (one browser flow per bank).
2. Run `finance sync` and have transactions from every active connection
   land in `finance.db` in a single invocation.
3. Continue using `finance sync` when one connection's refresh token has
   expired — the orchestrator auto-reauths the affected connection and
   resumes the sync.
4. Inspect per-connection state (last sync, days-until-expiry, account
   counts) via `finance status`.

This document is read top-to-bottom before Phase 2 work begins. The
**Testing strategy — TDD ordering** section (§9) is the implementation
spine: each layer's tests are written and committed before the production
code they cover.

The intended implementer is the **Sonnet 4.6** model working with the
Edit/Write/Bash tools. Every component is specified with concrete class
names, method signatures, column types, and acceptance criteria so that
Sonnet does not need to make design decisions during implementation.

---

## 1. In scope

| Capability | Notes |
|---|---|
| Multiple TrueLayer connections | One PSD2 consent per bank — First Direct, NatWest, Amex (live); mock providers (sandbox) |
| `connections` table | Parent entity for tokens + accounts; tracks consent lifecycle |
| Creds-driven auth | `TRUELAYER_BANKS=uk-ob-first-direct,uk-ob-natwest,uk-cs-amex` drives the auth loop |
| Per-connection sync | One `sync_run` row per (connection, invocation); per-connection partial-failure isolation |
| `finance sync --bank <provider_account>` | Filters to one connection |
| Auto-reauth mid-sync | When refresh fails, orchestrator triggers OAuth flow for the affected connection and resumes |
| Schema migration v1 → v2 | Existing First Direct connection preserved; no data loss |
| Per-connection observability | Structured logs include `connection_id` and `provider_account`; `finance status` shows per-connection state |
| 90-day expiry tracking | `connections.expires_at`; logged warning at <14 days remaining |
| Rolling DB backups | `finance.db.bak-<ts>` written before every sync and migration; rolling retention of 5 |

## 2. Out of scope (deferred — do not let these creep in)

| Item | Defer to |
|---|---|
| Non-TrueLayer providers (Plaid, direct PSD2) | Phase 3+ (`provider` column makes this possible later) |
| Transaction categorisation | Phase 3 |
| Landlord / tax-year reports | Phase 3+ |
| Web UI / HTTP API | Future |
| CSV import adapter | Future |
| Headless/non-interactive auth | Future — Phase 2 requires a browser-capable host for `finance auth` |
| Concurrent multi-connection sync | Sync is sequential per connection — no threading |
| Token encryption | Same posture as Phase 1 — filesystem permissions only |
| Domain model adoption in repositories | Phase 1 added `Account`/`Transaction` dataclasses but kept repository return types as `dict[str, Any]` — that stays. Domain models will be wired into repository return types in Phase 3 |

## 3. Decisions (locked, from design Q&A)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Token storage model | `connections` table as parent; `oauth_tokens` and `accounts` carry `connection_id` FK | Cleanest abstraction; future-proof for non-TrueLayer providers |
| D2 | Auth UX | Banks listed in `creds`; `finance auth` loops over them | Most automated; user defines bank set once |
| D3 | Re-consent handling | Auto-trigger re-auth mid-sync when refresh fails | Sync survives expiry without manual intervention |
| D4 | Sync scope default | Sync all active connections by default; `--bank` filters to one | Mirrors `git pull` / `git push --all` semantics |
| D5 | Sync run granularity | One `sync_run` row per connection per invocation | Per-connection failure isolation visible in audit trail |
| D6 | Auth invocation source | Auth flow extracted into a service callable from both CLI and orchestrator | Auto-reauth requires orchestrator to invoke OAuth; service avoids duplicating logic |

---

## 4. Schema changes (SQLAlchemy Core 2.0)

### New table — `connections`

```python
connections_table = Table(
    "connections",
    metadata,
    Column("connection_id", Text, primary_key=True),  # uuid4 string
    Column("provider", Text, nullable=False),           # 'truelayer'
    Column("provider_account", Text, nullable=False),   # 'uk-ob-first-direct', 'uk-ob-natwest', 'uk-cs-amex'
    Column("display_name", Text, nullable=False),       # 'First Direct', 'NatWest', 'American Express'
    Column("status", Text, nullable=False),             # 'active' | 'revoked'
    Column("consented_at", Text, nullable=False),       # ISO-8601 UTC
    Column("expires_at", Text, nullable=False),         # consented_at + 90 days (ISO-8601 UTC)
)

Index(
    "ix_connections_provider_account",
    connections_table.c.provider,
    connections_table.c.provider_account,
    unique=True,  # one connection per (provider, provider_account) at any time
)
```

**Invariant:** at most one row per `(provider, provider_account)` with `status='active'`. When re-auth happens, the same row is updated in place (PK stable).

### Modified table — `oauth_tokens`

```python
oauth_tokens_table = Table(
    "oauth_tokens",
    metadata,
    Column("connection_id", Text, ForeignKey("connections.connection_id"), primary_key=True),
    Column("access_token", Text, nullable=False),
    Column("refresh_token", Text, nullable=False),
    Column("expires_at", Text, nullable=False),    # access-token expiry (ISO-8601 UTC)
    Column("obtained_at", Text, nullable=False),
)
```

**Changes vs Phase 1:** PK changes from `provider` to `connection_id`. The `provider` string column is dropped (now derivable via JOIN on `connections`).

### Modified table — `accounts`

```python
accounts_table = Table(
    "accounts",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("connection_id", Text, ForeignKey("connections.connection_id"), nullable=False),  # NEW
    Column("provider_id", Text, nullable=False),       # unchanged — still the TrueLayer provider_id
    Column("account_type", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("currency", Text, nullable=False),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("raw_payload", Text, nullable=False),
)
```

### Unchanged tables — `transactions`, `sync_runs`, `schema_version`

`transactions` keeps `account_id` FK (to `accounts`); no `connection_id` needed (joinable through `accounts`).

`sync_runs` gets a new column `connection_id`:

```python
sync_runs_table = Table(
    "sync_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("connection_id", Text, ForeignKey("connections.connection_id"), nullable=False),  # NEW
    Column("started_at", Text, nullable=False),
    Column("finished_at", Text),
    Column("status", Text, nullable=False),
    Column("accounts_attempted", Integer, nullable=False),
    Column("accounts_succeeded", Integer, nullable=False),
    Column("transactions_inserted", Integer, nullable=False),
    Column("transactions_skipped_duplicate", Integer, nullable=False),
    Column("error_summary", Text),
)
```

### Bump `CURRENT_SCHEMA_VERSION`

```python
CURRENT_SCHEMA_VERSION = 2  # was 1 in Phase 1
```

---

## 5. Migration plan — v1 → v2

Phase 1 left databases at `schema_version=1` with one `oauth_tokens` row (`provider='truelayer'`) and accounts populated from a single TrueLayer connection. The migration must preserve that existing First Direct connection without re-consenting.

### Algorithm (executed once when `init_db` detects `schema_version=1`)

```
0. create_backup(db_path)  -- writes finance.db.bak-YYYYMMDD-HHMMSS, rotates to 5
1. Begin transaction.
2. CREATE TABLE connections (...).
3. CREATE TABLE oauth_tokens_v2 (...).
4. CREATE TABLE accounts_v2 (...).
5. CREATE TABLE sync_runs_v2 (...).
6. If oauth_tokens has any rows:
   For each existing oauth_tokens row r:
     a. Pick one account a from accounts where exists (heuristic: provider_id from any account row).
        (Phase 1 only allowed one TrueLayer connection so all accounts share the same provider_id.)
     b. connection_id = str(uuid.uuid4())
     c. provider_account = a.provider_id  (e.g. 'uk-ob-first-direct' or whatever TrueLayer returned)
     d. display_name = _display_name_for(provider_account)  -- best-effort lookup table; fallback to provider_account
     e. consented_at = r.obtained_at
     f. expires_at = r.obtained_at + 90 days
     g. INSERT INTO connections (connection_id, provider='truelayer', provider_account, display_name,
                                  status='active', consented_at, expires_at).
     h. INSERT INTO oauth_tokens_v2 (connection_id, access_token=r.access_token, refresh_token=r.refresh_token,
                                      expires_at=r.expires_at, obtained_at=r.obtained_at).
     i. INSERT INTO accounts_v2 SELECT account_id, <connection_id>, provider_id, ... FROM accounts.
     j. INSERT INTO sync_runs_v2 SELECT run_id, <connection_id>, started_at, ... FROM sync_runs.
7. DROP TABLE oauth_tokens.
8. DROP TABLE accounts.
9. DROP TABLE sync_runs.
10. ALTER TABLE oauth_tokens_v2 RENAME TO oauth_tokens.
11. ALTER TABLE accounts_v2 RENAME TO accounts.
12. ALTER TABLE sync_runs_v2 RENAME TO sync_runs.
13. Recreate indexes (ix_transactions_account_booking, ix_connections_provider_account).
14. UPDATE schema_version SET version=2.
15. Commit.
```

### Display-name lookup

A small in-code lookup table that maps known TrueLayer `provider_id` values to friendly names:

```python
_DISPLAY_NAMES = {
    "uk-ob-first-direct": "First Direct",
    "uk-ob-natwest": "NatWest",
    "uk-cs-amex": "American Express",
    "uk-cs-mock": "Mock Bank (sandbox)",
    "uk-ob-all": "Sandbox UK OB",
    "uk-oauth-all": "Sandbox UK OAuth",
    # extend as needed
}
def _display_name_for(provider_account: str) -> str:
    return _DISPLAY_NAMES.get(provider_account, provider_account)
```

### Backup policy (applies to all write paths, not just migrations)

Before any operation that materially mutates the database — schema
migration OR `finance sync` invocation — the implementation writes a
snapshot copy of `finance.db` next to the original:

```
finance.db.bak-YYYYMMDD-HHMMSS
```

A rolling retention policy keeps only the **5 most recent** backups.
Older backups are deleted automatically after each successful backup
write. The retention scan matches only the `<db_name>.bak-*` filename
pattern in the same directory; backups of other databases are not
touched.

A single helper module owns the policy:

```python
# src/finance_copilot/backup.py
RETENTION_COUNT = 5

def create_backup(db_path: Path) -> Path | None:
    """Snapshot db_path to db_path.bak-YYYYMMDD-HHMMSS; rotate retention.

    Returns the path of the new backup file, or None if db_path does
    not exist (e.g. fresh project before any sync). Idempotent if
    called twice in the same second (timestamp suffix may collide —
    the helper appends a uniqueness counter if needed).
    """

def list_backups(db_path: Path) -> list[Path]:
    """All backup files for db_path, newest first."""

def rotate_backups(db_path: Path) -> list[Path]:
    """Delete all but the RETENTION_COUNT most recent backups.
    Returns the list of deleted Paths."""
```

The backup helper is invoked from:

- `cli.cmd_sync` — once, before the orchestrator runs (covers all
  connections in a single invocation).
- `db.init_db` — once, before any v1→v2 migration steps execute.

If `create_backup` itself raises (disk full, permissions denied), the
sync/migration aborts before touching the DB. This is intentional: no
backup → no write.

### Migration tests

- `test_db_migration.py::test_v1_to_v2_creates_synthetic_connection` — seed a v1 DB, run init_db, assert one row in connections with status='active' and matching tokens
- `test_db_migration.py::test_v1_to_v2_preserves_transactions` — assert all transactions survive
- `test_v1_to_v2_preserves_accounts_with_connection_fk` — assert all accounts now reference the synthetic connection
- `test_v1_to_v2_creates_backup_file` — assert `finance.db.v1.bak` exists after migration
- `test_v1_to_v2_is_idempotent_on_v2_database` — re-running init_db on an already-v2 DB is a no-op
- `test_v1_to_v2_handles_empty_v1_database` — empty v1 DB migrates cleanly to empty v2

---

## 6. Components

### 6.1 `ConnectionRepository` (new)

`src/finance_copilot/repositories/connections.py`

```python
class ConnectionRepository:
    def __init__(self, engine: Engine) -> None: ...

    def add(self, *, connection_id: str, provider: str, provider_account: str,
            display_name: str, consented_at: str, expires_at: str) -> None:
        """Insert a new connection row with status='active'. Raises if (provider, provider_account)
        already exists with status='active'."""

    def get(self, connection_id: str) -> dict[str, Any] | None: ...

    def get_by_provider_account(self, provider: str, provider_account: str) -> dict[str, Any] | None: ...

    def list_active(self) -> list[dict[str, Any]]:
        """Return all active connections, ordered by consented_at ASC."""

    def list_all(self) -> list[dict[str, Any]]:
        """Return all connections including revoked, ordered by consented_at ASC."""

    def mark_revoked(self, connection_id: str) -> None: ...

    def update_consent(self, connection_id: str, *, consented_at: str, expires_at: str) -> None:
        """Mark a previously-revoked connection as active again with refreshed consent dates."""
```

### 6.2 `TokenRepository` (modified)

Existing class, signature changes:

```python
class TokenRepository:
    def put(self, connection_id: str, access_token: str, refresh_token: str,
            expires_at: str, obtained_at: str) -> None: ...   # was put(provider, ...)
    def get(self, connection_id: str) -> dict[str, Any] | None: ...
    def is_due_for_refresh(self, connection_id: str) -> bool: ...
```

All test files using `token_repo.put("truelayer", ...)` must be updated.

### 6.3 `AccountRepository` (modified)

```python
class AccountRepository:
    def upsert(self, row: dict[str, Any]) -> None:
        """row must include 'connection_id'."""

    def count(self) -> int: ...  # unchanged

    def list_by_connection(self, connection_id: str) -> list[dict[str, Any]]:
        """NEW — used by orchestrator to iterate accounts of one connection."""
```

### 6.4 `SyncRunRepository` (modified)

Adds `connection_id` to open_run and close_run signatures:

```python
def open_run(self, run_id: str, *, connection_id: str) -> None: ...
def latest_for_connection(self, connection_id: str) -> dict[str, Any] | None: ...
```

### 6.5 `AuthService` (new)

`src/finance_copilot/auth/service.py`

This service owns the entire OAuth flow for one provider_account. It is invoked from both `cmd_auth` (CLI) and `SyncOrchestrator` (mid-sync auto-reauth).

```python
class AuthService:
    def __init__(
        self,
        *,
        connection_repo: ConnectionRepository,
        token_repo: TokenRepository,
        http_client: httpx.Client,
        settings: Settings,
        browser_opener: Callable[[str], None] = webbrowser.open,
        code_listener: Callable[..., str] = oauth.wait_for_authorisation_code,
    ) -> None:
        """browser_opener and code_listener are injection seams for testing —
        production uses webbrowser.open and the existing HTTP listener."""

    def connect(self, *, provider_account: str, force: bool = False) -> str:
        """Run OAuth flow for one provider_account. Returns connection_id.

        Behaviour:
        - If an active connection already exists for (provider='truelayer', provider_account)
          and force=False, returns the existing connection_id without prompting.
        - Otherwise, generates PKCE pair, opens the browser to the auth URL with
          providers=<provider_account>, waits for callback, exchanges code for token,
          and INSERTS or UPDATES the connection + token rows.

        Raises:
            AuthError: token exchange failed (4xx from /connect/token).
        """

    def reauth(self, connection_id: str) -> None:
        """Re-run OAuth flow for an existing connection. Used after a refresh_token
        invalid_grant. Updates the connection (consented_at, expires_at, status='active')
        and oauth_tokens row.

        Raises:
            AuthError: if the re-auth flow itself fails.
        """
```

**Key design point**: `AuthService.connect` and `reauth` BOTH block on a browser interaction. They are NOT suitable for headless/CI use. Tests must inject `browser_opener` and `code_listener` callbacks that simulate the OAuth response.

### 6.6 `SyncOrchestrator` (modified)

`src/finance_copilot/sync/orchestrator.py`

The orchestrator now syncs ONE connection per `run_one` call:

```python
class SyncOrchestrator:
    def __init__(
        self,
        *,
        connection_repo: ConnectionRepository,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        sync_run_repo: SyncRunRepository,
        token_repo: TokenRepository,
        auth_service: AuthService,                          # NEW — for mid-sync reauth
        api_host: str,
        auth_host: str,                                     # for refresh_token
        client_id: str,
        client_secret: str,
        http_client: httpx.Client,
        tl_client_factory: TrueLayerClientFactory | None = None,
    ) -> None: ...

    def run_one(self, *, connection_id: str, explicit_from: date | None = None) -> SyncRunSummary:
        """Sync transactions for one connection.

        Workflow (unchanged from Phase 1 except for the auto-reauth hook):
        1. Guard against concurrent runs (now per-connection — see has_running_run_for).
        2. Open sync_run row with connection_id.
        3. Refresh token; if refresh fails with invalid_grant → call self._auth_service.reauth(connection_id) → retry refresh.
        4. Fetch accounts → upsert each (with connection_id).
        5. Per-account: fetch transactions, map, bulk_insert.
        6. Close sync_run with final status.
        """

    def _ensure_fresh_token(self, connection_id: str) -> str:
        """Refresh token for a connection; auto-reauth on AuthError from refresh_token."""
```

The existing `SyncRunSummary` dataclass adds `connection_id`:

```python
@dataclass
class SyncRunSummary:
    run_id: str
    connection_id: str  # NEW
    status: str
    accounts_attempted: int
    accounts_succeeded: int
    transactions_inserted: int
    transactions_skipped_duplicate: int
    error_summary: str | None
```

### 6.7 `MultiConnectionSyncer` (new)

`src/finance_copilot/sync/multi.py`

This thin coordinator iterates over connections and calls `SyncOrchestrator.run_one` for each:

```python
@dataclass
class MultiSyncSummary:
    per_connection: list[SyncRunSummary]
    total_inserted: int
    total_skipped: int
    overall_status: str   # 'succeeded' | 'partial' | 'failed'


class MultiConnectionSyncer:
    def __init__(
        self,
        *,
        connection_repo: ConnectionRepository,
        orchestrator: SyncOrchestrator,
    ) -> None: ...

    def run_all(self, *, explicit_from: date | None = None,
                bank_filter: str | None = None) -> MultiSyncSummary:
        """Iterate over active connections (optionally filtered by provider_account)
        and call orchestrator.run_one(connection_id=...) for each.

        Per-connection failures do not abort other connections.
        """
```

### 6.8 Settings (modified)

`src/finance_copilot/config.py`

Add a new field:

```python
truelayer_banks: str = Field(
    default="",
    validation_alias=AliasChoices("TRUELAYER_BANKS", "FINANCE_TRUELAYER_BANKS"),
)

@property
def banks(self) -> list[str]:
    """Parse TRUELAYER_BANKS comma- or space-separated string into a list of provider_account values."""
    raw = self.truelayer_banks.strip()
    if not raw:
        return []
    return [b.strip() for b in raw.replace(",", " ").split() if b.strip()]
```

The existing `truelayer_providers` field stays — it controls the TrueLayer auth-URL `providers` parameter (sandbox: `uk-cs-mock uk-ob-all uk-oauth-all`). The new `truelayer_banks` is the list of provider_account values to iterate over during `finance auth`.

### 6.9 Ports / Protocol updates

`src/finance_copilot/ports.py` — add:

```python
class ConnectionRepositoryPort(Protocol):
    def add(self, *, connection_id: str, provider: str, provider_account: str,
            display_name: str, consented_at: str, expires_at: str) -> None: ...
    def get(self, connection_id: str) -> dict[str, Any] | None: ...
    def get_by_provider_account(self, provider: str, provider_account: str) -> dict[str, Any] | None: ...
    def list_active(self) -> list[dict[str, Any]]: ...
    def list_all(self) -> list[dict[str, Any]]: ...
    def mark_revoked(self, connection_id: str) -> None: ...
    def update_consent(self, connection_id: str, *, consented_at: str, expires_at: str) -> None: ...


class AuthServicePort(Protocol):
    def connect(self, *, provider_account: str, force: bool = False) -> str: ...
    def reauth(self, connection_id: str) -> None: ...
```

Update `TokenRepositoryPort` and `AccountRepositoryPort` signatures to match the modified implementations.

### 6.10 Backup helper (new)

`src/finance_copilot/backup.py`

```python
from pathlib import Path
from datetime import datetime, UTC
import shutil

RETENTION_COUNT = 5
_TS_FMT = "%Y%m%d-%H%M%S"


def create_backup(db_path: Path) -> Path | None:
    """Snapshot db_path to <db_path>.bak-YYYYMMDD-HHMMSS, then rotate.

    Returns the new backup Path. Returns None if db_path does not exist
    (e.g. first ever invocation on a fresh project).
    """


def list_backups(db_path: Path) -> list[Path]:
    """All backup files matching <db_path.name>.bak-* in db_path.parent,
    sorted newest first by timestamp suffix."""


def rotate_backups(db_path: Path) -> list[Path]:
    """Delete every backup beyond the RETENTION_COUNT most recent.
    Returns the list of deleted Paths."""
```

Invariants:

- `create_backup` MUST call `rotate_backups` after the copy succeeds.
- `list_backups` MUST sort by timestamp suffix, not by filesystem mtime
  (mtime is unreliable on Windows when copies happen rapidly).
- Backups MUST live in the same directory as `db_path` so a user can
  inspect/restore them without hunting.

---

## 7. Data flow

### 7.1 `finance auth` — creds-driven loop

```
read creds → settings.banks = ['uk-ob-first-direct', 'uk-ob-natwest', 'uk-cs-amex']
for bank in settings.banks:
    existing = connection_repo.get_by_provider_account('truelayer', bank)
    if existing and existing.status == 'active' and not args.force:
        print(f"{bank}: already connected (run with --force to re-consent)")
        continue
    print(f"Connecting {bank}...")
    auth_service.connect(provider_account=bank, force=args.force)
    print(f"  connected.")
```

`--force` flag triggers re-consent for already-active connections.

### 7.2 `finance sync` — connection iteration

```
connections = connection_repo.list_active()
if args.bank:
    connections = [c for c in connections if c.provider_account == args.bank]
    if not connections:
        error: "no active connection for bank '<bank>'. Run finance auth first."
        exit 1

for c in connections:
    summary = orchestrator.run_one(connection_id=c.connection_id, explicit_from=args.from_date)
    # per-connection sync_run is already written by orchestrator
    print per-connection summary

print overall summary (totals across connections)
```

### 7.3 Auto-reauth mid-sync

```
SyncOrchestrator.run_one(connection_id='conn-123'):
  try:
    access_token = self._ensure_fresh_token('conn-123')
  except AuthError:
    # invalid_grant — connection has expired
    log.warning("connection.reauth_required", connection_id='conn-123')
    self._auth_service.reauth('conn-123')          # blocks on browser
    log.info("connection.reauth_completed", connection_id='conn-123')
    access_token = self._ensure_fresh_token('conn-123')  # should succeed now
  ...continue normal sync...
```

If `auth_service.reauth` itself fails (user closes browser, network error, user denies consent), AuthError propagates up. The `sync_run` row for that connection closes with `status='failed'`, and `MultiConnectionSyncer` continues with the next connection.

---

## 8. CLI specification

### 8.1 `finance auth [--force] [--bank <provider_account>]`

```
$ finance auth
Environment: live
Banks configured: uk-ob-first-direct, uk-ob-natwest, uk-cs-amex

[1/3] uk-ob-first-direct — already connected (run with --force to re-consent)
[2/3] uk-ob-natwest — connecting...
      Open this URL in your browser to authorise:
        https://auth.truelayer.com/...
      <browser opens>
      Token stored.
[3/3] uk-cs-amex — connecting...
      Open this URL in your browser to authorise:
        https://auth.truelayer.com/...
      <browser opens>
      Token stored.

Done. Active connections: 3
```

Flags:
- `--force` — re-consent for already-active connections
- `--bank <provider_account>` — only auth that specific bank (skips loop over all)

### 8.2 `finance sync [--bank <provider_account>] [--from YYYY-MM-DD]`

```
$ finance sync
[1/3] First Direct (uk-ob-first-direct) ...
        Inserted: 12, Skipped: 158
[2/3] NatWest (uk-ob-natwest) ...
        Inserted: 47, Skipped: 0
[3/3] American Express (uk-cs-amex) ...
        Connection expired — re-authorising...
        <browser opens>
        Re-auth complete.
        Inserted: 89, Skipped: 0

Sync summary
  Connections : 3/3 succeeded
  Inserted    : 148
  Skipped     : 158 (duplicate)
```

If `--bank` is given but no matching active connection exists, the CLI exits 1 with a message.

### 8.3 `finance status`

```
$ finance status
DB path     : finance.db
Accounts    : 9
Transactions: 4823

Connections (3 active)
  First Direct          (uk-ob-first-direct)  active   expires in 84d   accounts: 5  txns: 2190
  NatWest               (uk-ob-natwest)       active   expires in 12d   accounts: 2  txns: 1450  ⚠ re-consent soon
  American Express      (uk-cs-amex)          active   expires in 90d   accounts: 2  txns: 1183

Last sync runs (per connection)
  First Direct          succeeded   2026-06-12T15:00 — inserted=0 skipped=2190
  NatWest               succeeded   2026-06-12T15:01 — inserted=47 skipped=0
  American Express      succeeded   2026-06-12T15:02 — inserted=89 skipped=0
```

The `⚠ re-consent soon` marker appears when `expires_at` is within 14 days of `now`.

### 8.4 Exit codes (unchanged from Phase 1)

```
0  — success
1  — validation/usage error
2  — auth error (token exchange failed, user denied consent, etc.)
3  — transient error (network, rate limit, server error)
99 — unexpected error
```

---

## 9. Testing strategy — TDD ordering

Each layer = `tests/<file>` written first, then `src/finance_copilot/<file>` to satisfy it. Layers are numbered to match commit ordering.

### Layer 0 — Pure functions (no DB, no HTTP)

| Test file | Production file | What it covers |
|---|---|---|
| `tests/test_settings_banks.py` | `src/finance_copilot/config.py` | `Settings.banks` parses comma/space-separated string into a list |
| `tests/test_mapping_with_connection.py` | `src/finance_copilot/sync/mapping.py` | `map_account` now accepts and emits `connection_id` |
| `tests/test_display_name_lookup.py` | (small module — co-located with migration) | `_display_name_for` returns mapped names + fallback |
| `tests/test_backup.py` | `src/finance_copilot/backup.py` | `create_backup` writes timestamped file; `list_backups` sorts newest-first; `rotate_backups` keeps exactly RETENTION_COUNT; `create_backup` returns None when db_path missing |

### Layer 1 — Schema, migration, repositories

| Test file | Production file | What it covers |
|---|---|---|
| `tests/test_db_schema_v2.py` | `src/finance_copilot/db.py` | v2 schema: connections table exists, oauth_tokens PK is connection_id, accounts has connection_id FK, sync_runs has connection_id |
| `tests/test_db_migration.py` | `src/finance_copilot/db.py` (migration code) | v1→v2 migration: synthetic connection created, tokens preserved, accounts re-linked, backup written, idempotent on v2 |
| `tests/test_repo_connections.py` | `src/finance_copilot/repositories/connections.py` | All `ConnectionRepository` methods |
| `tests/test_repo_tokens_v2.py` | `src/finance_copilot/repositories/tokens.py` | `TokenRepository` signature change: keyed by `connection_id` |
| `tests/test_repo_accounts_v2.py` | `src/finance_copilot/repositories/accounts.py` | `upsert` requires connection_id; `list_by_connection` returns expected rows |
| `tests/test_repo_sync_runs_v2.py` | `src/finance_copilot/repositories/sync_runs.py` | `open_run`/`close_run`/`latest_for_connection` with connection_id |

### Layer 2 — AuthService

| Test file | Production file | What it covers |
|---|---|---|
| `tests/test_auth_service.py` | `src/finance_copilot/auth/service.py` | `AuthService.connect` (new + idempotent + force), `AuthService.reauth` (updates existing connection), AuthError surfacing |

Tests inject `browser_opener` (no-op) and `code_listener` (returns a fixed code) so no real browser opens during tests.

### Layer 3 — SyncOrchestrator (modified)

| Test file | Production file | What it covers |
|---|---|---|
| `tests/test_orchestrator_v2.py` | `src/finance_copilot/sync/orchestrator.py` | All Phase 1 tests adapted to per-connection signature; new tests for auto-reauth on AuthError; logs include `connection_id` |

Existing `tests/test_orchestrator.py` is replaced/superseded — old tests using `provider="truelayer"` and no `connection_id` will not compile.

### Layer 4 — MultiConnectionSyncer

| Test file | Production file | What it covers |
|---|---|---|
| `tests/test_multi_syncer.py` | `src/finance_copilot/sync/multi.py` | Iterates active connections; per-connection failure isolation; `bank_filter` selects one; empty connection list returns succeeded with totals=0 |

### Layer 5 — CLI

| Test file | Production file | What it covers |
|---|---|---|
| `tests/test_cli_v2.py` | `src/finance_copilot/cli.py` | `finance auth` loop over `settings.banks`; `finance auth --force`; `finance sync --bank`; `finance status` per-connection output; expiry warning |

Existing `tests/test_cli.py` is updated in-place — its mocks for Settings, components, and SyncOrchestrator need the new shapes.

### Test conventions

- All tests run **offline**. No live HTTP. `AuthService` and `TrueLayerClient` are mocked via injected callables / Protocol mocks.
- Browser interaction is faked. `AuthService` accepts `browser_opener` and `code_listener` callables — tests pass lambdas.
- 90-day expiry is mocked by freezing `datetime.now(UTC)` via a `clock` injection or `freezegun`-style fixture. If freezegun is not yet a dep, add it.
- Fixture: an in-memory v1 database seeded with realistic Phase 1 data (1 oauth_token, 5 accounts, 2190 transactions) — used by migration tests. Build once per session.

---

## 10. Acceptance criteria

Each AC must have at least one corresponding automated test. Manual gates (G2, G3, G4) are validated by humans, not tests.

| # | Acceptance criterion |
|---|---|
| AC1 | `Settings.banks` correctly parses comma- and space-separated `TRUELAYER_BANKS` strings into a `list[str]` |
| AC2 | The `connections` table exists in v2 schema with all columns specified in §4 |
| AC3 | v1→v2 migration creates exactly one synthetic connection per existing oauth_tokens row, preserving all accounts and transactions |
| AC4 | A backup file `finance.db.bak-YYYYMMDD-HHMMSS` is written before each v1→v2 migration AND before each `finance sync` invocation |
| AC4a | `rotate_backups` retains exactly the 5 most recent backups for a given DB path; older backups are deleted after each new backup write |
| AC4b | `create_backup` returns `None` (no error raised) when invoked on a non-existent DB path (fresh-project case) |
| AC5 | `ConnectionRepository.add` raises if a duplicate `(provider, provider_account)` active row exists |
| AC6 | `TokenRepository.put/get/is_due_for_refresh` all operate on `connection_id`, not provider strings |
| AC7 | `AuthService.connect(provider_account='X')` is a no-op if an active connection for X exists and `force=False` |
| AC8 | `AuthService.connect(provider_account='X', force=True)` re-runs the OAuth flow and updates consent dates |
| AC9 | `AuthService.reauth(connection_id)` updates the existing connection in place; the same `connection_id` is preserved |
| AC10 | `SyncOrchestrator.run_one` requires a `connection_id` parameter; writes a `sync_run` row with that connection_id |
| AC11 | When `oauth.refresh_token` raises AuthError, `SyncOrchestrator._ensure_fresh_token` invokes `auth_service.reauth` and retries the refresh |
| AC12 | `MultiConnectionSyncer.run_all()` syncs every active connection; one failed connection does not block others |
| AC13 | `MultiConnectionSyncer.run_all(bank_filter='uk-ob-natwest')` syncs exactly that connection if active; raises `SyncBlockedError` or returns empty summary if no matching active connection (TBD by implementer — pick one and test it) |
| AC14 | `finance auth` reads `settings.banks` and calls `AuthService.connect` for each in order, skipping already-active ones |
| AC15 | `finance auth --force` re-consents for all banks regardless of current status |
| AC16 | `finance sync` invokes `MultiConnectionSyncer.run_all(bank_filter=None)` |
| AC17 | `finance sync --bank X` invokes `MultiConnectionSyncer.run_all(bank_filter='X')` |
| AC18 | `finance status` displays one row per connection with days-until-expiry; the `⚠` marker appears when `expires_at - now < 14 days` |
| AC19 | Per-connection `sync.start` / `sync.complete` log events include `connection_id` and `provider_account` |
| AC20 | All 174 Phase 1 tests either pass unchanged or are updated to the v2 signatures (no test deletions without justification) |
| AC21 | `pytest tests/ -q` passes with 0 failures |
| AC22 | `mypy src/` clean under strict |
| AC23 | `ruff check src/ tests/` clean |
| AC24 | All tests run without network access (verify with `pytest -p no:network` or by manual inspection) |

---

## 11. Validation gates (manual)

| Gate | Owner | Done when |
|---|---|---|
| G1: schema + migration smoke | Implementer | Run `uv run finance status` on the existing First Direct DB after the migration code lands; existing 5 accounts + 2190 transactions still visible; one connection in the `connections` table; verify a `finance.db.bak-<ts>` file was written next to the original DB |
| G2: dual-provider sandbox smoke | Implementer | Add a second mock provider to `TRUELAYER_BANKS`, run `finance auth` (two browser flows), `finance sync`; two `sync_runs` rows, accounts from two distinct `provider_account`s |
| G3: dual-provider live smoke (First Direct + NatWest) | User | After implementation: live consent for both banks; `finance sync` populates both; spot-check 5 NatWest transactions against the NatWest app |
| G4: auto-reauth smoke | User | Manually invalidate a refresh token (delete the row in `oauth_tokens` and revert connection.status to `revoked`, OR wait for natural 90-day expiry); run `finance sync`; browser opens for re-auth; sync resumes successfully |
| G5: tri-provider live smoke (add Amex) | User | If Amex AIS public beta is functional, add `uk-cs-amex` to TRUELAYER_BANKS; auth; sync; verify Amex transactions present and amounts/dates plausible |

---

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | SQLite ALTER TABLE limitations break the migration | Migration uses table-recreate pattern (new tables → copy → drop → rename) inside a transaction; backup written before; explicit tests cover empty DB, populated DB, and re-run cases |
| R2 | Existing First Direct connection gets the wrong synthetic `provider_account` | Migration reads `accounts.provider_id` from the existing DB and uses that exact string; if multiple distinct provider_ids exist (shouldn't in Phase 1 but defensive), migration aborts with a clear error and writes a diagnostic file listing what it saw |
| R3 | Auto-reauth in a non-interactive environment hangs | `AuthService.connect` has a 5-minute timeout on `code_listener` (already in Phase 1's `wait_for_authorisation_code`); if it times out, AuthError raised → connection marked revoked → next sync prompts user explicitly |
| R4 | Amex AIS payload diverges from First Direct shape and breaks mapping | New test fixture for an Amex transaction payload (capture from sandbox/live during implementation); `map_transaction` must handle missing/extra fields gracefully (`provider_category=None`, etc.); mapping tests cover both bank shapes |
| R5 | 90-day expiry detection drift due to clock skew or DB string format | `expires_at` stored as ISO-8601 UTC; comparisons via `datetime.fromisoformat`; tests use frozen clock |
| R6 | Race: two `finance sync` invocations on different connections at the same time | Concurrency guard (`has_running_run_for`) is per-connection. Same connection cannot be synced twice concurrently (10-minute timeout from Phase 1 carries over); different connections can. Test coverage required |
| R7 | TrueLayer `providers` parameter syntax for live banks not yet verified | Implementer runs Gate G2 first against sandbox to confirm provider-id format. If `uk-ob-first-direct` is not the right token, error surfaces immediately with a clear message |
| R8 | Mid-sync reauth introduces a long pause that exceeds the running-run 10-min cutoff | If reauth blocks >10 min, `has_running_run_for` would consider the run dead. Mitigation: open_run timestamps the `started_at`; refresh `started_at` after successful reauth before continuing the sync |
| R9 | User has secrets in their existing connection that don't match their current live credentials (after rotating client_secret) | `AuthService.reauth` uses current `settings.truelayer_client_secret` — automatic, no manual edit needed |
| R10 | Test fakes diverge from real httpx behaviour | All tests using mocked httpx responses must construct `httpx.Response` with a `request` attached (lesson learned from Phase 1) |

---

## 13. Definition of done

Phase 2 is complete when all of the following are true:

- AC1–AC24 verified by automated tests (pytest passes).
- Gates G1, G2, G3, G4 signed off by the user (G5 optional if Amex unavailable).
- mypy strict + ruff clean.
- No regressions in Phase 1 functionality: `finance auth` for a single bank still works; `finance sync` produces identical results when only one connection exists.
- `docs/phase-2-review.md` produced by a Principal Engineer review pass (matching Phase 1's process).
- CLAUDE.md updated to reflect Phase 2 completion + Phase 3 deferred.
- Project memory updated with the connection-model architecture decision.

---

## 14. Notes for the implementer

- Read this whole document before writing any code. Re-read §9 (TDD ordering) before starting each layer.
- Match Phase 1's commit pattern: one commit per layer, with the test commit immediately before the production commit (or combined into one commit per layer with both).
- Do NOT refactor Phase 1 patterns that aren't directly affected (e.g. don't rename `SyncOrchestrator` to `ConnectionSyncer` — keep the existing name with the new signature).
- When implementing `AuthService`, copy the PKCE/listener pattern from `cmd_auth` in Phase 1's `cli.py` — that code is correct and well-tested via the spike module.
- All structured logs include `connection_id` and (where known) `provider_account` as kwargs. Existing `sync.start` / `sync.complete` keep their event names, just gain extra fields.
- If you find an ambiguity not covered here, STOP and ask. Do not invent a design decision mid-implementation.
- After Layer 1 (schema + migration) is complete, run `finance status` against the existing DB to verify migration ran correctly. This is Gate G1.
