# Finance Copilot — Claude Code context

## What this project is

Personal finance + landlord/tax reporting proof-of-concept. Ingests Open Banking
transaction data via TrueLayer AIS, stores it in a local SQLite database, and
will eventually provide AI-assisted categorisation and tax-year summaries.

Owner: Simon Williams (`s.williams@sportradar.com`)

---

## Current milestone

**Phase 1 fully signed off** (2026-06-12) — PASS (unqualified):

- SQLite schema: `accounts`, `transactions`, `sync_runs`, `oauth_tokens`, `schema_version` tables.
- Repository pattern: `AccountRepository`, `TransactionRepository`, `SyncRunRepository`, `TokenRepository`.
- Domain models: `Account`, `Transaction` frozen dataclasses in `src/finance_copilot/models/`.
- Ports: `TrueLayerClientPort`, `TrueLayerClientFactory`, repository Protocols in `src/finance_copilot/ports.py`.
- TrueLayer ingestion pipeline: OAuth PKCE flow, token refresh, incremental sync with dedup, pagination warning.
- Structured logging via structlog: `sync.start`, `sync.complete`, `account.failed`, `pagination.detected`.
- CLI: `finance auth`, `finance sync`, `finance status`.
- 174 tests green. mypy strict + ruff clean.
- Gates passed: G2 (sandbox smoke) + G3 (live First Direct smoke).

**Phase 2 in design — Multi-provider sync** (NatWest + Amex alongside First Direct).
See `docs/phase-2-architecture.md` for the full spec (schema, components, TDD
ordering, acceptance criteria). Implementation has not yet started; this
document is the contract for the implementing model.

**Do not implement beyond Phase 2 scope** as defined in
`docs/phase-2-architecture.md`. If you find an ambiguity not covered there,
STOP and surface it to the user — do not invent a design decision.

---

## Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.12 (pinned `>=3.12,<3.13`) |
| Package manager | uv (`C:/Users/simon/.local/bin/uv.exe`) |
| HTTP client | httpx |
| Database | SQLite via SQLAlchemy Core 2.0 |
| Data validation (planned M3) | pydantic |
| Linter | ruff (line length 100) |
| Type checker | mypy (strict) |
| Test runner | pytest 9.0+ |
| Build backend | hatchling |

### uv quirk — corporate SSL

`[tool.uv] system-certs = true` is set in `pyproject.toml` because Sportradar
corporate network performs SSL inspection. **Do not remove this line.**

### git quirk — corporate SSL

`http.sslBackend = schannel` is set globally in git config for the same reason.
If pushes fail with SSL errors, run: `git config --global http.sslBackend schannel`

---

## Engineering Principles

* Follow SOLID principles.
* Prefer simple, explicit solutions.
* Separate orchestration, persistence and integration concerns.
* Prefer composition over inheritance.
* Single responsibility per class.
* Maximum class size: 500 lines.
* Maximum method size: 50 lines.
* Build only the currently approved phase.
* Code should be maintainable by a senior Java developer.

---

## Review Process

Major implementation phases must be reviewed against:

* Architecture documents
* Acceptance criteria
* Engineering principles

Review outcomes:

* PASS
* PASS WITH RECOMMENDATIONS
* FAIL

Implementation is not complete until review passes.

## Definition of Done

A phase is complete when:

* Acceptance criteria pass.
* Tests pass.
* Ruff passes.
* Mypy passes.
* Documentation is updated.
* Architecture remains compliant.

---

## Repository layout

```
scripts/                    # run-once operational scripts (NOT on pythonpath)
  spike_first_direct.py     # canonical Phase-0 spike (also copied to tests/)

src/finance_copilot/        # application package
  config.py                 # pydantic-settings: env + DB path
  log_config.py             # structlog config + sensitive-field redaction
  db.py                     # SQLAlchemy Core metadata, engine factory, init_db
  ports.py                  # Protocol types (TrueLayerClientPort, TrueLayerClientFactory, repo ports)
  models/
    account.py              # Account frozen dataclass
    transaction.py          # Transaction frozen dataclass
  repositories/
    accounts.py             # AccountRepository
    transactions.py         # TransactionRepository (dedup-aware bulk_insert; wraps IntegrityError)
    sync_runs.py            # SyncRunRepository
    tokens.py               # TokenRepository (Phase 1: keyed by provider; Phase 2: by connection_id)
  truelayer/
    errors.py               # AuthError, RateLimitError, TransientError, TransactionWriteError, …
    oauth.py                # PKCE helpers, exchange_code, refresh_token
    client.py               # TrueLayerClient with retry + pagination.detected warning
  sync/
    incremental.py          # sync_from_date() — pure window calculation
    mapping.py              # TrueLayer payload → DB row dict (Decimal-safe via default=str)
    orchestrator.py         # SyncOrchestrator with structured logging + factory injection
  cli.py                    # finance auth | sync | status
scripts/
  spike_first_direct.py     # canonical Phase-0 spike (also copied to tests/)
  spot_check.py             # operational helper — prints 10 most recent transactions

tests/
  conftest.py               # shared fixtures (in-memory engine, fixture data)
  spike_first_direct.py     # Phase-0 spike module (on pythonpath)
  test_spike_first_direct.py  # 21 Phase-0 unit tests
  test_spike_e2e.py           # 15 Phase-0 behavioural/e2e tests
  test_incremental.py         # Layer 0a
  test_mapping.py             # Layer 0b+0c
  test_db_schema.py           # Layer 1a
  test_repo_accounts.py       # Layer 1b
  test_repo_transactions.py   # Layer 1c
  test_repo_sync_runs.py      # Layer 1d
  test_repo_tokens.py         # Layer 1e
  test_oauth.py               # Layer 2a
  test_truelayer_client.py    # Layer 2b
  test_orchestrator.py        # Layer 3
  test_cli.py                 # Layer 4
  fixtures/
    spike_first_direct_sandbox.json  # real TrueLayer sandbox output — do NOT gitignore

docs/
  delivery.md                      # milestone delivery plan
  spike-architecture-options.md    # ADR: TrueLayer AIS decision
  phase-1-architecture.md          # Phase 1 architecture + acceptance criteria

output/                     # gitignored — runtime spike output goes here
creds                       # gitignored — TrueLayer credentials (key=value lines)
.env                        # gitignored — alternative credentials source
.env.example                # committed — template showing required keys
```

### pythonpath

pytest is configured with `pythonpath = ["src", "tests"]` so both
`from spike_first_direct import ...` and `from finance_copilot import ...` resolve correctly.

---

## Never commit

- `creds` — contains live TrueLayer client ID + secret
- `finance.db` — covered by `*.db` in `.gitignore`; may contain real transaction data
- `output/` — runtime spike JSON (may contain real transaction data)
- `.env` / `.env.*` (except `.env.example`)

This is a **public repo**. The `.gitignore` excludes the above. Verify before
every commit with `git status`.

---

## TrueLayer integration — key facts

### Authentication
- Protocol: OAuth 2.0 authorisation-code + PKCE (S256)
- Sandbox auth host: `https://auth.truelayer-sandbox.com`
- Live auth host: `https://auth.truelayer.com`
- Token endpoint: `{auth_host}/connect/token`
- Default redirect URI: `http://localhost:8080/oauth2/callback` (registered in TrueLayer console)
- **Sandbox requires** `enable_mock=true` AND a non-empty `providers` param or the
  provider picker returns 400. Use `SANDBOX_DEFAULT_PROVIDERS = "uk-cs-mock uk-ob-all uk-oauth-all"`.

### Data API
- Sandbox API host: `https://api.truelayer-sandbox.com`
- Live API host: `https://api.truelayer.com`
- Accounts: `GET {api_host}/data/v1/accounts`
- Transactions: `GET {api_host}/data/v1/accounts/{account_id}/transactions`
- Response envelope: `{"results": [...]}` — use `.get("results", [])` defensively.
- Token refresh skew: 60 seconds (refresh before actual expiry).
- Retry policy: 4 total attempts, backoff delays 1s / 4s / 16s on 429 and 5xx.

### TrueLayer transaction shape (from real sandbox)
- `transaction_id` — stable TrueLayer ID; dedup_key = `tl:<transaction_id>`
- `timestamp` — date-level `2026-06-12T00:00:00Z`; maps to `booking_date`, `value_date NULL`
- `amount` — signed float parsed as `Decimal` at HTTP boundary; stored as TEXT
- `transaction_type` — `"DEBIT"` | `"CREDIT"`
- `account_number` sub-object — sensitive; store in `raw_payload` only, not normalised columns
- Provider: `mock` in sandbox; real provider IDs in live (e.g. `first-direct`, `natwest`)

### Incremental sync window
- First sync per account: `from = today - 90 days` (configurable via `FINANCE_INITIAL_WINDOW_DAYS`)
- Subsequent syncs: `from = max(last_booking_date - 7 days, today - 90 days)` (configurable)
- Override: `finance sync --from YYYY-MM-DD`

### Credentials file format (`creds`)
```
TRUELAYER_CLIENT_ID=<id>
TRUELAYER_CLIENT_SECRET=<secret>
TRUELAYER_ENVIRONMENT=sandbox   # optional, default sandbox
TRUELAYER_REDIRECT_URI=...      # optional, default http://localhost:8080/oauth2/callback
TRUELAYER_PROVIDERS=...         # optional, overrides sandbox default
```

---

## Architecture decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Open Banking provider | TrueLayer AIS | First Direct + NatWest GA; Amex AIS beta; GoCardless not accepting new devs |
| Local storage | SQLite + SQLAlchemy Core 2.0 | Portable, no server, async-capable later |
| Schema abstraction | Repository pattern | Isolates storage from ingestion logic |
| Transaction dedup key | `tl:<transaction_id>` for TrueLayer; `content:<sha256>` for future CSV | Stable ID for TrueLayer; hash-based for sources without IDs |
| Money representation | `Decimal` parsed at HTTP boundary, stored as TEXT | Avoids float rounding; TEXT is Decimal-safe in SQLite |
| `account_number` handling | `raw_payload` only | Sensitive; no normalised column |
| Token storage | Single-row in `oauth_tokens` table, no encryption | File-system permissions only; OS keyring deferred to M2 |
| Logging | structlog with sensitive-key redaction processor | `raw_payload`, `access_token`, `refresh_token`, `account_number`, `iban` never rendered |

---

## Phase 2 — Multi-provider sync (designed, awaiting implementation)

**Scope:** Extend the TrueLayer pipeline to support multiple PSD2 consents
in parallel — First Direct + NatWest (live), Amex UK (public beta) — with
a `connections` parent table, creds-driven auth loop, per-connection
sync runs, and auto-reauth mid-sync when refresh tokens expire.

**Full spec:** `docs/phase-2-architecture.md` (locked decisions, schema,
v1→v2 migration plan, component signatures, TDD ordering, 24 ACs).

**Key Phase 2 invariants** (lifted into CLAUDE.md so they aren't lost):

- Schema bumps to v2; migration preserves the existing First Direct connection.
- A rolling backup is written before every `finance sync` AND every migration:
  `finance.db.bak-YYYYMMDD-HHMMSS`, retention = 5 most recent, older deleted.
- `oauth_tokens.connection_id` is the new PK; `accounts.connection_id` is FK.
- `AuthService` (new) owns the OAuth flow and is invoked from both CLI and orchestrator.
- `SyncOrchestrator.run_one` is now per-connection; `MultiConnectionSyncer` iterates.
- `TRUELAYER_BANKS` in `creds` lists the provider_account values to connect.

Phase 3 (categorisation, reporting) is deferred until Phase 2 is fully signed off.
