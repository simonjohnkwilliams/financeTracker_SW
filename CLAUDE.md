# Finance Copilot — Claude Code context

## What this project is

Personal finance + landlord/tax reporting proof-of-concept. Ingests Open Banking
transaction data via TrueLayer AIS, stores it in a local SQLite database, and
will eventually provide AI-assisted categorisation and tax-year summaries.

---

## Current milestone

**Phase 0 complete** (as of 2026-06-12):

- TrueLayer sandbox end-to-end spike validated: OAuth PKCE consent flow,
  token exchange, accounts + transactions fetch, JSON dump to `output/`.
- 5 sandbox accounts, 2190 transactions successfully retrieved.
- 36 tests green (21 unit + 15 behavioural/e2e).

**Phase 1 not yet started.** Do not implement beyond Phase 0 scope without
explicit sign-off on the next milestone.

---

## Stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.12 (pinned `>=3.12,<3.13`) |
| Package manager | uv (`C:/Users/simon/.local/bin/uv.exe`) |
| HTTP client | httpx |
| Database (planned M1) | SQLite via SQLAlchemy Core 2.0 |
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

## Repository layout

```
scripts/                    # run-once operational scripts (NOT on pythonpath)
  spike_first_direct.py     # canonical spike implementation (also in tests/)

tests/
  spike_first_direct.py     # copy of scripts/spike_first_direct.py (on pythonpath)
  test_spike_first_direct.py  # 21 unit tests for spike functions
  test_spike_e2e.py           # 15 behavioural/e2e tests using fixture
  fixtures/
    spike_first_direct_sandbox.json  # real TrueLayer sandbox output — do NOT gitignore

src/
  finance_copilot/          # application package (scaffolded in M1)

docs/
  delivery.md               # milestone delivery plan
  spike-architecture-options.md  # ADR: TrueLayer AIS decision

output/                     # gitignored — runtime spike output goes here
creds                       # gitignored — TrueLayer credentials (key=value lines)
.env                        # gitignored — alternative credentials source
.env.example                # committed — template showing required keys
```

### pythonpath

pytest is configured with `pythonpath = ["src", "tests"]` so `from spike_first_direct import ...`
resolves to `tests/spike_first_direct.py`.

---

## Never commit

- `creds` — contains live TrueLayer client ID + secret
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

### TrueLayer transaction shape (from real sandbox)
- `transaction_id` — stable TrueLayer ID (no content-hash dedup needed for TrueLayer path)
- `timestamp` — date-level `2026-06-12T00:00:00Z`; maps to `booking_date`, `value_date NULL`
- `amount` — signed float: negative = DEBIT, positive = CREDIT
- `transaction_type` — `"DEBIT"` | `"CREDIT"`
- `account_number` sub-object — sensitive; store in `raw_payload` only, not normalised columns
- Provider: `mock` in sandbox; real provider IDs in live (e.g. `first-direct`, `natwest`)

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
| Schema abstraction | Repository pattern from M1 | Isolates storage from ingestion logic |
| Transaction dedup key | `source_transaction_id` if present, else `content:<sha256>` | Handles TrueLayer stable IDs and CSV fallback |
| Money representation | `Decimal` (parsed at API boundary) | Avoids float rounding; `parse_float=Decimal` in JSON loads from M3 |
| `account_number` handling | `raw_payload` only | Sensitive; no normalised column |

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
* Prefer explicit domain models over dictionaries and untyped structures.

---

## Phase 1 planned scope (not yet implemented)

Current phase and scope are defined by the approved architecture document for the active milestone.

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
* No critical findings remain from architecture review.
