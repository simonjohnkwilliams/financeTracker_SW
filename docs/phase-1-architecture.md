# Phase 1 — Architecture

**Status:** design only — no implementation. Approval gate before any code.

**Outcome statement:**

> *I can retrieve real transactions from supported accounts (TrueLayer-connected
> institutions) and store them locally, with incremental sync and no duplicates.*

Phase 1 ends when a user can:

1. Authenticate against TrueLayer once (`finance auth`).
2. Run `finance sync` on demand and have new transactions land in `finance.db`.
3. Re-run `finance sync` arbitrarily, with no duplicates and no data loss.
4. Inspect what was loaded via `finance status`.

This document is read top-to-bottom before Phase 1 work begins. The
**Testing strategy — TDD ordering** section (§7) is the implementation
spine: each layer's tests are written and committed before the production
code they cover.

---

## 1. In scope

| Capability | Notes |
|---|---|
| TrueLayer OAuth flow with token persistence | One-time consent via `finance auth`; refresh handled silently |
| Account metadata persistence | `accounts` table; full raw payload preserved |
| Transaction ingestion | `transactions` table; per-account |
| Incremental fetch | Lookback-window strategy on the `from` query param |
| Dedup at write | `dedup_key UNIQUE`; idempotent re-runs |
| Sync audit trail | `sync_runs` table — one row per invocation |
| Token refresh | Automatic, transparent, ahead of 401 |
| Partial-failure isolation | One account's failure does not abort the whole sync |
| Structured logging | structlog with `run_id` correlation; sensitive-field redaction |
| CLI: `finance auth`, `finance sync`, `finance status` | argparse; exit codes mapped to error classes |
| Repository abstraction | `AccountRepository`, `TransactionRepository`, `SyncRunRepository`, `TokenRepository` |

## 2. Out of scope (deferred — do not let these creep in)

| Item | Defer to |
|---|---|
| AI/LLM categorisation | M2 / M3 |
| Tax-year summaries, landlord reports | M2 / M3 |
| Web UI / HTTP API | Future |
| CSV import adapter | M3 (only if needed) |
| Multi-user / multi-tenancy | Out of project scope (single-user POC) |
| Decimal-everywhere refactor | M3 — Phase 1 parses at the API boundary; that is enough |
| Multi-provider abstraction layer | Future — M1 = TrueLayer only; the repository shape supports addition |
| Alembic migrations | M2 if schema evolves; M1 uses `metadata.create_all` + `schema_version` row |
| Encryption-at-rest for tokens | M2 (OS keyring) — known limitation, file-perms only in M1 |
| Async / asyncio | Synchronous httpx is sufficient for nightly single-user sync |
| Real-time webhooks | Not offered on TrueLayer free tier |
| `/transactions` pagination handling | Implement only if live response shows pagination metadata (sandbox returned 2190 in one page) |
| Scheduler / daemon | Manual invocation only in M1 |

## 3. Components

```
src/finance_copilot/
  config.py              # pydantic-settings: env + creds + DB path
  logging.py             # structlog config; redact filter for sensitive keys
  db.py                  # engine factory; SQLAlchemy Core metadata; create_all
  models/
    account.py           # frozen dataclass + row mapping helpers
    transaction.py       # frozen dataclass + row mapping helpers
    sync_run.py
    token.py
  repositories/
    accounts.py          # AccountRepository
    transactions.py      # TransactionRepository (dedup-aware bulk_insert)
    sync_runs.py         # SyncRunRepository
    tokens.py            # TokenRepository (single-row keyed by provider)
  truelayer/
    oauth.py             # auth URL, code exchange, refresh
    client.py            # TrueLayerClient: typed wrappers on /accounts, /transactions
    errors.py            # AuthError, RateLimitError, TransientError
  sync/
    incremental.py       # lookback window calculation (pure)
    mapping.py           # TrueLayer JSON → row dict; dedup_key derivation (pure)
    orchestrator.py      # SyncOrchestrator.run_one() — drives one sync_run
  cli.py                 # finance auth | sync | status
```

### Key interfaces (no implementation here)

- `TrueLayerClient.fetch_accounts() -> list[AccountPayload]`
- `TrueLayerClient.fetch_transactions(account_id: str, from_date: date | None) -> list[TxnPayload]`
- `TransactionRepository.bulk_insert(rows: list[Transaction]) -> InsertResult` — where `InsertResult` distinguishes `inserted` from `skipped_duplicate`.
- `SyncOrchestrator.run_one(explicit_from: date | None = None) -> SyncRunSummary`

## 4. Schema

All timestamps stored as UTC ISO-8601 TEXT. Amounts stored as TEXT
(Decimal-safe round-trip). DB file location: `./finance.db` (project-local
for POC; revisit in M2).

### `accounts`
| Column | Type | Notes |
|---|---|---|
| account_id | TEXT PK | TrueLayer stable ID |
| provider_id | TEXT | e.g. `first-direct`, `mock` |
| account_type | TEXT | TRANSACTION / SAVINGS / CREDIT_CARD |
| display_name | TEXT | |
| currency | TEXT | ISO-4217 |
| first_seen_at | TEXT (UTC ISO) | Set on first insert; never updated |
| last_seen_at | TEXT (UTC ISO) | Updated every successful sync |
| raw_payload | TEXT (JSON) | **Contains sensitive `account_number` sub-object** |

### `transactions`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| account_id | TEXT FK → `accounts.account_id` | |
| dedup_key | TEXT UNIQUE NOT NULL | `tl:<transaction_id>` for TrueLayer; format reserved for future sources (`csv:<sha256>`) |
| source_transaction_id | TEXT | TrueLayer `transaction_id`, verbatim |
| booking_date | TEXT (date) | Derived from `timestamp` (date portion) |
| value_date | TEXT (date) NULL | Always NULL for TrueLayer (not in payload) |
| amount | TEXT | Decimal serialised as string |
| currency | TEXT | |
| transaction_type | TEXT | DEBIT / CREDIT |
| description | TEXT | |
| provider_category | TEXT | TrueLayer `transaction_category` (no derived categorisation yet) |
| raw_payload | TEXT (JSON) | Verbatim provider payload, **contains sensitive sub-objects** |
| ingested_at | TEXT (UTC ISO) | Set on insert |

Index: `(account_id, booking_date)` for chronological reads.

### `sync_runs`
| Column | Type | Notes |
|---|---|---|
| run_id | TEXT PK | uuid4 |
| started_at | TEXT (UTC ISO) | |
| finished_at | TEXT (UTC ISO) NULL | |
| status | TEXT | `running` / `succeeded` / `partial` / `failed` |
| accounts_attempted | INTEGER | |
| accounts_succeeded | INTEGER | |
| transactions_inserted | INTEGER | |
| transactions_skipped_duplicate | INTEGER | |
| error_summary | TEXT NULL | Short message naming failed account(s) |

### `oauth_tokens`
| Column | Type | Notes |
|---|---|---|
| provider | TEXT PK | Always `truelayer` in M1 |
| access_token | TEXT | |
| refresh_token | TEXT | |
| expires_at | TEXT (UTC ISO) | |
| obtained_at | TEXT (UTC ISO) | |

### `schema_version`
| Column | Type | Notes |
|---|---|---|
| version | INTEGER PK | M1 = 1 |
| applied_at | TEXT (UTC ISO) | |

Startup check: refuse to run if `MAX(version) > 1` (newer code required).

## 5. Data flow

### `finance auth` (one-time)

```
User browser  ──consent──▶  TrueLayer auth host
                                    │
                              code, state
                                    ▼
                       localhost loopback handler
                                    │
                                    ▼
                          oauth.exchange_code()
                                    │
                                    ▼
                       TokenRepository.put(token)
```

### `finance sync` (per invocation)

```
SyncOrchestrator.run_one()
  │
  ├─ SyncRunRepository.open()       # row with status=running
  │
  ├─ TokenRepository.get()
  │      └─ if expired (within 60s skew): oauth.refresh()
  │                                       └─ TokenRepository.put()
  │
  ├─ TrueLayerClient.fetch_accounts()
  │      └─ AccountRepository.upsert_each()
  │
  ├─ for each account (isolated try/except):
  │      ├─ from_date = incremental.window(last_seen=max_booking_date(acc))
  │      ├─ TrueLayerClient.fetch_transactions(acc, from_date)
  │      ├─ mapping.to_rows() → list[Transaction] (Decimal-safe)
  │      ├─ TransactionRepository.bulk_insert(rows)
  │      │      ├─ inserted_total  += N
  │      │      └─ skipped_total   += M
  │      └─ on error: capture error, mark account failed, continue
  │
  └─ SyncRunRepository.close(status from per-account outcomes)
```

### Incremental window

- Per account: `from = max(booking_date for that account) - LOOKBACK_DAYS` (default `7`).
- First sync for an account: `from = today - INITIAL_WINDOW_DAYS` (default `90`).
- `to` is omitted (TrueLayer defaults to now).
- `--from YYYY-MM-DD` CLI flag overrides both.

The 7-day lookback handles late-posting transactions. Duplicates from the
overlap are silently skipped by the UNIQUE constraint; cost is one extra
HTTP call per sync.

## 6. Token lifecycle

| State | Trigger | Action |
|---|---|---|
| No token | Fresh install | `finance sync` errors with exit 2 → "run finance auth" |
| Valid token | `now < expires_at - 60s` | Use directly |
| Near-expiry | `now >= expires_at - 60s` | Refresh before next call |
| Refresh failure (invalid_grant) | TL returned 400 | `AuthError` → `finance sync` exits 2 with message |
| Refresh failure (transient) | TL 5xx / network | Retry per backoff policy; if exhausted, fail run with `error_summary` |

## 7. Testing strategy — TDD ordering

Tests are written **before** the production code they exercise. Layers
below depend on the ones above, so build (and test) in this order. A
layer is "done" when all its tests are green AND mypy/ruff are clean for
the code added in that layer.

Use the existing `tests/fixtures/spike_first_direct_sandbox.json`
(5 accounts, 2190 transactions) as the canonical mocked-TrueLayer
response wherever possible.

---

### Layer 0 — Pure functions (no DB, no HTTP)

Write these first; they have no dependencies and define the data
contracts the rest of the system relies on.

**0a. Incremental window calculation** (`sync/incremental.py`)
- `test_first_sync_uses_initial_window_when_no_last_seen` — `last_seen=None` → `today - 90d`
- `test_subsequent_sync_uses_lookback_window` — `last_seen=2026-06-01` → `2026-05-25` (7d earlier)
- `test_lookback_does_not_predate_initial_window` — `min(today - 90d, last_seen - 7d)` boundary
- `test_explicit_from_short_circuits_calculation`
- `test_window_uses_utc_today_not_local`

**0b. TrueLayer → row mapping** (`sync/mapping.py`)
- `test_map_transaction_extracts_required_fields`
- `test_map_transaction_uses_timestamp_date_portion_as_booking_date`
- `test_map_transaction_value_date_is_null`
- `test_map_transaction_preserves_raw_payload_verbatim`
- `test_map_transaction_dedup_key_format_is_tl_prefix_plus_transaction_id`
- `test_map_transaction_raises_when_transaction_id_missing` — fail-fast on bad payload
- `test_map_account_extracts_required_fields`
- `test_map_account_preserves_account_number_in_raw_payload_only` — sensitive fields not surfaced as columns

**0c. Decimal parsing at the boundary**
- `test_amount_parsed_as_decimal_from_truelayer_float`
- `test_amount_stored_as_string_round_trips_exactly`
- `test_negative_amounts_preserve_sign`
- `test_dedup_key_for_csv_source_uses_content_sha256_prefix` — reserved-format test only (mapping function exists; CSV ingester does not)

---

### Layer 1 — Schema & repositories (in-memory SQLite)

**1a. Schema**
- `test_create_all_creates_expected_tables` — accounts, transactions, sync_runs, oauth_tokens, schema_version
- `test_transactions_dedup_key_unique_constraint` — second insert with same `dedup_key` raises `IntegrityError`
- `test_transactions_foreign_key_to_accounts` — orphan `account_id` rejected (with PRAGMA foreign_keys=ON)
- `test_schema_version_seeded_at_create_all` — version=1 row exists
- `test_startup_refuses_when_version_greater_than_code`

**1b. AccountRepository**
- `test_upsert_inserts_new_account`
- `test_upsert_updates_last_seen_on_existing_account`
- `test_upsert_preserves_first_seen_on_update`
- `test_list_returns_all_accounts_ordered_by_first_seen`

**1c. TransactionRepository**
- `test_bulk_insert_returns_inserted_and_skipped_counts`
- `test_bulk_insert_skips_duplicates_by_dedup_key`
- `test_bulk_insert_is_atomic_on_unexpected_error` — non-dedup failure rolls back the batch
- `test_max_booking_date_for_account_returns_latest_iso_date`
- `test_max_booking_date_returns_none_for_unseen_account`
- `test_bulk_insert_handles_2190_row_batch` — exercises fixture-scale path

**1d. SyncRunRepository**
- `test_open_run_writes_running_row_with_started_at`
- `test_close_run_sets_status_and_finished_at`
- `test_latest_run_returns_most_recent`
- `test_running_run_within_10_minutes_blocks_new_run`

**1e. TokenRepository**
- `test_put_then_get_round_trip`
- `test_get_returns_none_when_no_token`
- `test_is_due_for_refresh_true_within_skew_window`
- `test_is_due_for_refresh_false_when_fresh`
- `test_put_overwrites_existing_row_for_same_provider`

---

### Layer 2 — TrueLayer client (mocked httpx)

**2a. OAuth**
- `test_exchange_code_returns_token_with_absolute_expires_at` — `expires_in` seconds → UTC timestamp
- `test_refresh_token_sends_grant_type_refresh_token`
- `test_refresh_token_raises_auth_error_on_invalid_grant_400`
- `test_refresh_token_raises_transient_error_on_5xx`

**2b. TrueLayerClient**
- `test_fetch_transactions_sends_from_parameter_when_provided`
- `test_fetch_transactions_omits_from_when_none`
- `test_fetch_accounts_sends_bearer_token`
- `test_client_retries_on_429_with_exponential_backoff` — 3 attempts: 1s / 4s / 16s
- `test_client_retries_on_5xx`
- `test_client_does_not_retry_on_4xx_except_429`
- `test_client_raises_auth_error_on_401` — orchestrator handles
- `test_client_parses_amounts_as_decimal_at_boundary` — `json.loads(parse_float=Decimal)`

---

### Layer 3 — Sync orchestrator (mocked TrueLayerClient, real in-memory DB)

Uses the existing sandbox fixture as the TrueLayerClient stand-in.

**3a. Happy path**
- `test_first_run_inserts_all_fixture_transactions` — 5 accounts, 2190 rows
- `test_first_run_writes_sync_run_with_succeeded_status`
- `test_first_run_records_account_metadata_for_all_accounts`
- `test_first_run_summary_counts_match_db_row_counts`

**3b. Idempotency**
- `test_second_run_with_identical_data_inserts_zero_rows` — skipped_duplicate=2190
- `test_second_run_with_one_new_transaction_inserts_exactly_one`
- `test_third_run_after_partial_failure_recovers_missing_account`

**3c. Token lifecycle**
- `test_orchestrator_refreshes_expired_token_before_fetching`
- `test_orchestrator_raises_authentication_error_when_refresh_fails`
- `test_failed_auth_writes_sync_run_with_failed_status`

**3d. Partial failure**
- `test_one_account_500_does_not_abort_other_accounts`
- `test_partial_failure_sets_sync_run_status_partial`
- `test_partial_failure_error_summary_names_failed_account`
- `test_partial_failure_still_advances_other_account_watermarks`

**3e. Incremental window**
- `test_subsequent_run_uses_from_based_on_max_booking_date_per_account`
- `test_explicit_from_override_is_propagated_to_client`

**3f. Concurrency guard**
- `test_starting_a_run_while_one_is_running_raises`

---

### Layer 4 — CLI

**4a. Command dispatch**
- `test_finance_auth_invokes_oauth_flow_and_persists_token`
- `test_finance_sync_invokes_orchestrator_and_exits_zero_on_success`
- `test_finance_sync_exits_nonzero_on_failed_run`
- `test_finance_status_reads_from_repositories_and_prints_summary`

**4b. Error → exit code mapping**
- `test_auth_error_exits_with_code_2_and_prompts_reauth`
- `test_transient_error_exits_with_code_3`
- `test_validation_error_exits_with_code_1`
- `test_unexpected_error_exits_with_code_99_and_logs_traceback`

**4c. Flag parsing**
- `test_sync_accepts_explicit_from_flag`
- `test_status_accepts_no_arguments`

---

### Layer 5 — Smoke (manual, not in CI)

- `manual_test_finance_auth_against_sandbox` — token row written
- `manual_test_finance_sync_against_sandbox` — DB has 5 accounts, ~2190 transactions
- `manual_test_finance_sync_twice_no_duplicates` — second run reports skipped_duplicate=2190
- `manual_test_finance_status_against_seeded_db`
- `manual_test_finance_sync_against_live_first_direct` — owner runs against real account; spot-check 5 known transactions vs First Direct app

---

### Test discipline

- Each layer fully green before the next begins.
- No live HTTP in any automated test. Fixture is the source of truth.
- One behavioural focus per test (multiple `assert` lines OK if probing the same behaviour).
- All automated tests run in under 5 seconds total (in-memory SQLite, mocked HTTP).
- Coverage target: ≥ 90% on `src/finance_copilot/`. Argparse boilerplate / `__main__` may sit below.
- Every test added is committed in the same PR slice as the code it covers.

## 8. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | Refresh token revoked (inactivity, password change, user-side disconnect) | Sync fails until re-auth | `AuthError` → CLI prints actionable message → exit 2 |
| R2 | TrueLayer rate limits hit during initial bulk sync | Sync stalls / partials | Exponential backoff (3 attempts, 1s/4s/16s) on 429 + 5xx; partial run recorded |
| R3 | One account fails mid-sync | Whole sync would abort, losing good data | Per-account try/except in orchestrator; mark run `partial` |
| R4 | Schema changes after data accumulates | Manual migration risk | `schema_version` table + startup check; M2 introduces Alembic if/when needed |
| R5 | Concurrent `finance sync` invocations | Race on inserts; duplicate sync_runs | Refuse to start if a `running` sync_run < 10 min old exists (Layer 3f test) |
| R6 | Lookback window misses transactions posted >7 days late | Silent data gap | `--from` flag for ad-hoc reconciliation; document the window |
| R7 | Float precision corruption from TrueLayer payload | Money rounding errors | Parse `amount` as `Decimal` at client boundary (`json.loads(parse_float=Decimal)`); store as TEXT |
| R8 | Sensitive data (account_number, IBAN, tokens) leaked via logs | Privacy / compliance | structlog redaction processor strips `raw_payload`, `access_token`, `refresh_token`, `account_number`, `iban` before render |
| R9 | TrueLayer paginates `/transactions` and we silently miss pages | Data loss | On first live sync, log WARN if any pagination metadata present in response; implement before declaring Phase 1 done if so |
| R10 | Token file readable by other OS users | Credential exposure on shared machines | Documented as known gap; M2 → OS keyring |
| R11 | TrueLayer free-tier daily call cap (verify exact number against current docs) | Sync may exceed quota | Default cadence = manual / once-per-day; record the cap in CLAUDE.md once verified |
| R12 | Clock skew between machine and TrueLayer | Token marked expired prematurely or used past expiry | 60s skew buffer on `expires_at` |
| R13 | Live First Direct payload diverges from sandbox shape | Mapping crashes | Smoke test gate (§9 Gate 3); commit redacted live fixture for future tests |
| R14 | Provider returns transactions with no `transaction_id` | Dedup key cannot be derived | Mapping raises; surfaces as account-level failure (R3 mitigation absorbs it) |
| R15 | `sqlite3.IntegrityError` on bulk insert leaks raw row data in the exception message into logs | Privacy | Catch + re-raise as `DuplicateTransactionError` carrying only the dedup_key |

## 9. Validation gates

Three gates. Phase 1 is not done until all three are green.

### Gate 1 — Automated suite green

- `uv run pytest` → 100% pass (existing 36 + ~80 new)
- `uv run mypy src tests` → 0 errors
- `uv run ruff check src tests` → clean
- Coverage report ≥ 90% on `src/finance_copilot/`

### Gate 2 — Sandbox smoke (layer 5, sandbox subset)

- `finance auth` against sandbox → token persisted to DB
- `finance sync` → DB shows 5 accounts, ≈2190 transactions
- `finance sync` again → 0 inserted, 2190 skipped
- `finance status` reports correct counts and timestamp

### Gate 3 — Live smoke (layer 5, live subset)

- Owner runs `finance auth` against live First Direct
- Owner runs `finance sync`
- Owner spot-checks 5 recent transactions vs the First Direct app/statement
- One account end-to-end constitutes pass; full multi-bank coverage is M2

## 10. Acceptance criteria

### Functional

| # | Criterion |
|---|---|
| AC1 | `finance auth` initiates OAuth, captures `code` on localhost loopback, persists tokens. |
| AC2 | `finance sync` retrieves accounts and transactions from TrueLayer and writes them to `finance.db`. |
| AC3 | `finance sync` is idempotent: re-running with no new TL data inserts 0 rows. |
| AC4 | `finance sync` with N new TL transactions inserts exactly N rows (verified by counter and DB state). |
| AC5 | `finance status` reports: last sync timestamp, status, account count, transaction count, last error if any. |
| AC6 | Access tokens within 60 seconds of expiry are refreshed before the next HTTP call, transparently. |
| AC7 | One account's HTTP failure does not prevent other accounts from syncing in the same run. |
| AC8 | A failed sync writes a `sync_runs` row with `status=failed` and a non-empty `error_summary`. |
| AC9 | Token revocation produces exit code 2 with message instructing the user to run `finance auth`. |

### Non-functional

| # | Criterion |
|---|---|
| AC10 | `uv run pytest`, `uv run mypy`, `uv run ruff check` all clean. |
| AC11 | All automated tests run offline; no test depends on live TrueLayer. |
| AC12 | INFO-level logs contain no transaction amounts, descriptions, account numbers, IBANs, or tokens. DEBUG may carry summaries (counts, IDs) but never raw payloads. |
| AC13 | Database file stays under 50 MB for 5 accounts × 12 months of typical personal volume. |
| AC14 | Initial sandbox sync (5 accounts, 2190 txns) completes under 30s; no-op subsequent sync under 5s. |

### Operational

| # | Criterion |
|---|---|
| AC15 | `CLAUDE.md` is updated to reflect Phase 1 completion (post-merge). |
| AC16 | `.gitignore` continues to exclude `finance.db`, `creds`, `output/`, `.env`. |
| AC17 | Redacted live payload, if captured during Gate 3, is committed as a second fixture for M2 testing. |

## 11. Open decisions (confirm during build)

| Decision | Default | Confirmation point |
|---|---|---|
| DB file location | `./finance.db` (project-local) | First Gate 2 run; revisit in M2 |
| Initial sync window | 90 days | After Gate 3 (extend if user wants more history) |
| Lookback window | 7 days | After Gate 3 (extend if late postings observed) |
| Token storage encryption | None — file perms only | Known M2 gap, documented |
| `finance sync` cadence | Manual (no scheduler) | M2 if scheduling needed |
| Exit code for partial-success run | `0` (some data is better than none) | First Layer-4 review |

---

## Sign-off pause

Before implementation begins, confirm:

1. The scope in §1 and §2 is correct.
2. The schema in §4 is acceptable.
3. The TDD ordering in §7 is the path you want to follow.
4. The open decisions in §11 are acceptable as stated, or call out overrides.

When confirmed, work starts at **Layer 0a** — `tests/test_incremental.py`.
