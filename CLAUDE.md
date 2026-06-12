# Finance Copilot — Claude Code context

## What this project is

Personal finance + landlord/tax reporting proof-of-concept. Ingests Open Banking
transaction data via TrueLayer AIS, stores it in a local SQLite database, and
will eventually provide AI-assisted categorisation and tax-year summaries.

Owner: Simon Williams (`s.williams@sportradar.com`)

---

## Current milestone

**Phase 1 complete** (as of 2026-06-12):

- SQLite schema: `accounts`, `transactions`, `sync_runs`, `oauth_tokens`, `schema_version` tables.
- Repository pattern: `AccountRepository`, `TransactionRepository`, `SyncRunRepository`, `TokenRepository`.
- TrueLayer ingestion pipeline: OAuth PKCE flow, token refresh, incremental sync with dedup.
- CLI: `finance auth`, `finance sync`, `finance status`.
- 148 tests green (36 Phase-0 + 112 Phase-1). mypy strict + ruff clean.

**Phase 2 not yet started.** Do not implement beyond Phase 1 scope without
explicit sign-off on the next milestone.

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
  models/                   # (reserved for future typed models)
  repositories/
    accounts.py             # AccountRepository
    transactions.py         # TransactionRepository (dedup-aware bulk_insert)
    sync_runs.py            # SyncRunRepository
    tokens.py               # TokenRepository (single-row per provider)
  truelayer/
    errors.py               # AuthError, RateLimitError, TransientError, …
    oauth.py                # PKCE helpers, exchange_code, refresh_token
    client.py               # TrueLayerClient with retry
  sync/
    incremental.py          # sync_from_date() — pure window calculation
    mapping.py              # TrueLayer payload → DB row dict
    orchestrator.py         # SyncOrchestrator.run_one()
  cli.py                    # finance auth | sync | status

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

## Phase 2 planned scope (not yet implemented)

TBD — to be drafted after Phase 1 smoke-test sign-off (Gate 2 + Gate 3 from
`docs/phase-1-architecture.md`).
