# Phase 1 — Principal Engineer Code Review

**Reviewer:** Principal Engineer (acting)
**Scope:** Implementation in `src/finance_copilot/` + `tests/` reviewed against `docs/phase-1-architecture.md` and the engineering principles in `CLAUDE.md`.
**Date:** 2026-06-12

---

## Verdict

# ⚠ **PASS WITH RECOMMENDATIONS**

The implementation is functionally correct, all 148 tests pass, mypy strict
and ruff are clean, and all functional acceptance criteria (AC1–AC11) are
demonstrably satisfied. The work is mergeable and Phase 2 architecture
discussion may begin.

However, three classes of issues prevent an unqualified PASS:

1. **AC12 is vacuously satisfied, not actively satisfied.** The structured
   logger exists; the orchestrator never calls it. There is currently no
   operational observability of sync runs at all.
2. **Test concerns leak into production code.** A `_tl_client_override`
   constructor parameter and `Any` typing on the substitution slot mean
   substitutability is not enforced by the type system.
3. **The implementation deviates from one item in §3 of the architecture
   spec** (`models/` is empty — chosen to use row dicts instead of typed
   dataclasses). This deviation is reasonable for Python idiom but is a
   visible departure from "maintainable by a senior Java developer."

Recommendations are itemised below with severity. **High-severity items
should be addressed before Phase 2 implementation begins. Medium and low
items can be tracked as technical debt.**

---

## 1. Architecture compliance review

Section-by-section assessment against `docs/phase-1-architecture.md`.

### §1 In scope — fully compliant

All 11 capabilities listed in the in-scope table are present. Specifically
checked:

| Spec capability | Status | Where |
|---|---|---|
| OAuth flow + token persistence | ✅ | `truelayer/oauth.py`, `repositories/tokens.py`, `cli.cmd_auth` |
| Account metadata persistence | ✅ | `repositories/accounts.py` |
| Transaction ingestion | ✅ | `repositories/transactions.py` |
| Incremental fetch | ✅ | `sync/incremental.py` + orchestrator |
| Dedup at write | ✅ | `bulk_insert` pre-filters by `dedup_key` |
| Sync audit trail | ✅ | `repositories/sync_runs.py` |
| Token refresh (transparent) | ✅ | `SyncOrchestrator._ensure_fresh_token` |
| Partial-failure isolation | ✅ | `SyncOrchestrator._sync_all_accounts` per-account try/except |
| Structured logging | ⚠ | Configured (`log_config.py`) but **not used** (see §3 below) |
| CLI: auth, sync, status | ✅ | `cli.py` |
| Repository abstraction | ⚠ | Concrete classes only; no Protocol/ABC layer |

### §2 Out of scope — fully compliant

No deferred items have crept in. Verified that there is no Alembic, no
async, no scheduler, no multi-provider abstraction, no encryption-at-rest
for tokens, no CSV adapter, no pagination handling, no AI categorisation.

### §3 Components — one deviation

The architecture lists `src/finance_copilot/models/{account,transaction,
sync_run,token}.py` with "frozen dataclass + row mapping helpers." The
implementation has only `src/finance_copilot/models/__init__.py` (empty).
Row dicts (`dict[str, Any]`) are passed everywhere instead.

**Impact:** Type safety is reduced — all repository methods accept and
return `dict[str, Any]`, which mypy strict cannot enforce shape on. A
senior Java developer reviewing this would expect entity classes.

**Mitigation in current code:** none. mypy doesn't flag missing/extra keys
in these dicts. Bugs from key typos would surface only at runtime.

This deviation was an agent-authored interpretation of the spec; the trade-off
is brevity vs. type safety. Documented here so it can be reviewed and
either ratified or reversed before Phase 2.

### §4 Schema — fully compliant

All 5 tables match the architecture column-for-column. FK enforcement via
PRAGMA confirmed. Schema version seeded. Indexes present. UNIQUE on
`dedup_key` enforced.

### §5 Data flow — fully compliant

Both flows (`finance auth`, `finance sync`) match the diagrams. Incremental
window logic in `sync/incremental.py` correctly implements
`max(last_booking − 7d, today − 90d)`.

### §6 Token lifecycle — fully compliant

All 5 states from the table are handled. 60s skew buffer verified in
`TokenRepository.is_due_for_refresh`. `AuthError` for revoked refresh
verified in tests.

### §7 TDD ordering — fully compliant

All 5 layers exist. Test files match the layer naming convention. Tests
were authored before code (per agent report; structure of `conftest.py` is
consistent with this claim).

### §8 Risks — 14 of 15 mitigations present

| Risk | Status |
|---|---|
| R1 (revoked refresh) | ✅ AuthError → exit 2 |
| R2 (rate limits) | ✅ Backoff in `TrueLayerClient._get` |
| R3 (partial fail) | ✅ Per-account try/except |
| R4 (schema mutation) | ✅ `schema_version` + `check_schema_version` |
| R5 (concurrent sync) | ✅ `has_running_run` check |
| R6 (late postings) | ✅ `--from` CLI flag |
| R7 (float precision) | ✅ `parse_float=Decimal` in `client._get` |
| R8 (sensitive data in logs) | ⚠ Processor exists but only redacts flat keys; nested dicts not traversed |
| R9 (pagination) | ⚠ No WARN log; the orchestrator doesn't log anything |
| R10 (token file perms) | ✅ Documented gap |
| R11 (free-tier cap) | ✅ Manual cadence documented |
| R12 (clock skew) | ✅ 60s buffer |
| R13 (live payload divergence) | ⏸ Pending Gate 3 |
| R14 (missing `transaction_id`) | ✅ `mapping.map_transaction` raises ValueError |
| R15 (IntegrityError leaks data) | ❌ **Not mitigated** — see §6 below |

### §9 / §10 Validation gates & acceptance criteria

| AC | Status | Notes |
|---|---|---|
| AC1 `finance auth` | ✅ | `cmd_auth` + tests |
| AC2 `finance sync` | ✅ | `cmd_sync` + tests |
| AC3 Idempotent sync | ✅ | `test_second_run_with_identical_data_inserts_zero_rows` |
| AC4 N new = N inserted | ✅ | `test_second_run_with_one_new_transaction_inserts_exactly_one` |
| AC5 `finance status` | ✅ | `cmd_status` + tests; output formatting is functional but plain text |
| AC6 Token refresh < 60s of expiry | ✅ | `is_due_for_refresh` + test |
| AC7 Per-account isolation | ✅ | `test_one_account_500_does_not_abort_other_accounts` |
| AC8 Failed sync persists row | ✅ | `test_failed_auth_writes_sync_run_with_failed_status` |
| AC9 Revocation → exit 2 + message | ✅ | `test_auth_error_exits_with_code_2_and_prompts_reauth` |
| AC10 pytest/mypy/ruff clean | ✅ | 148 / 0 / clean |
| AC11 All tests offline | ✅ | Verified — no live HTTP in any automated test |
| AC12 No sensitive data in logs | ⚠ | **Vacuously true:** no logs are emitted by the orchestrator. The redaction processor is configured but unused. |
| AC13 DB stays < 50MB | ⏸ | Cannot verify without Gate 3 data |
| AC14 Sandbox sync < 30s | ⏸ | Pending Gate 2 |
| AC15 CLAUDE.md updated | ✅ | Verified |
| AC16 .gitignore | ✅ | `*.db` already covers `finance.db` |
| AC17 Live fixture | ⏸ | Pending Gate 3 |

---

## 2. Object-oriented design review

### SOLID assessment

#### S — Single Responsibility

Most classes hold one responsibility cleanly:

| Class | Responsibility | Verdict |
|---|---|---|
| `AccountRepository` | Account row CRUD | ✅ |
| `TransactionRepository` | Transaction bulk-insert + watermark | ✅ |
| `SyncRunRepository` | Sync audit row CRUD + concurrency check | ✅ |
| `TokenRepository` | Token persistence + freshness | ✅ |
| `TrueLayerClient` | HTTP wrapper with retry | ✅ |
| `SyncOrchestrator` | Drive one sync run end-to-end | ⚠ (does too much; see below) |
| `Settings` | Config load | ✅ |
| `SyncRunSummary` | Result value | ✅ |

**`SyncOrchestrator` has scope creep:** it owns OAuth refresh, HTTP client
construction, account upsert, per-account fetch loop, status derivation,
sync_run lifecycle, AND error reporting. The recent refactor split
`_execute` into three methods, which helps, but the **class still holds 11
fields** and constructs its own `TrueLayerClient`. A cleaner split would
extract:

- `TokenService` — wraps `TokenRepository + oauth.refresh_token`
- `TrueLayerClientFactory` — produces an authenticated client given a token
- `SyncOrchestrator` — drives the loop, owns neither auth nor HTTP details

This is not a blocker but is the single largest OO concern in the codebase.

#### O — Open/Closed

The TrueLayer-specific code path is hardcoded throughout. Adding a second
provider (e.g., Plaid, Open Banking direct) would require modification of:

- `SyncOrchestrator._ensure_fresh_token` (provider key `"truelayer"`)
- `cmd_auth` in `cli.py` (hardcoded TrueLayer flow)
- `TokenRepository` calls (provider key)

The architecture explicitly **defers multi-provider** to a future phase, so
this is a **documented constraint**, not a violation. However, the
`provider` PK on `oauth_tokens` and `provider_id` column on `accounts`
correctly preserve the option to extend without schema migration.

#### L — Liskov Substitution

No deep inheritance. The mock client in `test_orchestrator.py`
(`MockTrueLayerClient`) is a duck-typed substitute, not a subclass. There
is no Protocol/ABC that both the real and mock client implement, so
substitution is by convention, not by contract.

#### I — Interface Segregation

**No Protocol or ABC layer exists.** The architecture says "Repository
abstraction" — what is implemented is the *pattern* (repository class
isolating persistence) but not the *abstraction* (an interface that
multiple implementations could satisfy).

For Phase 1, this is acceptable: there is only one implementation of each
repository. For Phase 2 (if e.g. a memory-backed repository is needed for
faster CLI integration tests, or a future Postgres backend is added),
introducing protocols becomes necessary.

A senior Java developer would expect:

```python
class TransactionRepositoryPort(Protocol):
    def bulk_insert(self, rows: list[Transaction]) -> InsertResult: ...
    def max_booking_date(self, account_id: str) -> str | None: ...
    def count(self) -> int: ...

class SqlAlchemyTransactionRepository(TransactionRepositoryPort):
    ...
```

This is a **recommendation, not a defect.**

#### D — Dependency Inversion

The orchestrator depends on concrete classes:

```python
from finance_copilot.truelayer.client import TrueLayerClient   # concrete
from finance_copilot.truelayer import oauth                    # concrete module
```

And builds its own `TrueLayerClient` inside `_execute`:

```python
tl_client: Any = self._tl_client_override or TrueLayerClient(
    api_host=self._api_host, ...
)
```

The `_tl_client_override: Any | None` parameter is the test-substitution
hatch. This achieves DI in practice, but:

- The type is `Any`, so mypy cannot verify the substitute implements the
  same interface.
- The constructor parameter name starts with `_` (private convention) yet
  is part of the public constructor — confusing.

**Recommended pattern:**

```python
class TrueLayerClientFactory(Protocol):
    def __call__(self, *, access_token: str) -> TrueLayerClientPort: ...

class SyncOrchestrator:
    def __init__(self, *, ..., tl_client_factory: TrueLayerClientFactory, ...):
        ...
```

This eliminates the `_tl_client_override` smell and makes the dependency
explicit.

### Class responsibilities

`SyncOrchestrator` aside, the responsibilities are clean and SRP-aligned.

### Repository pattern

The pattern is **partially implemented**. The structural separation
(persistence isolated in `repositories/`) is correct. What is missing:

- **No abstract type** — each repository is a concrete class with no
  interface.
- **Return types are `dict[str, Any]`** — the repository does not hand back
  domain objects; consumers must know the column names.
- **No `Unit of Work`** — the orchestrator opens transactions implicitly
  via `with engine.begin()` inside each repository call. There is no
  single transaction spanning the whole sync. **This is by design**
  (per-account isolation requires per-account commit boundaries), but it
  should be noted.

### Encapsulation

- All repository methods correctly hide SQL behind a method API.
- `SyncOrchestrator` fields are `_`-prefixed.
- `Settings` uses pydantic-settings — encapsulated correctly.
- Module-level constants (`REFRESH_SKEW_SECONDS`, `MAX_RETRIES`,
  `RETRY_DELAYS`) are correctly module-private and exported by name where
  needed for tests.

### Error handling

| Where | Style | Verdict |
|---|---|---|
| `TrueLayerClient._get` | Maps HTTP codes to specific exception types | ✅ Clear |
| `oauth.refresh_token` | Distinguishes `invalid_grant` (AuthError) from 5xx (TransientError) | ✅ Clear |
| `SyncOrchestrator._sync_all_accounts` | `except Exception` | ⚠ **Too broad** |
| `cli.main` | Catches AuthError, TransientError, then bare Exception → exit 99 | ✅ Acceptable for CLI top-level |
| `TransactionRepository.bulk_insert` | Lets `IntegrityError` bubble | ⚠ R15 — could leak row data into log if caller logs the exception |

The orchestrator's per-account `except Exception` is the right shape (we
do want to isolate failures), but should be tightened to:

```python
except (TrueLayerError, MappingError, IntegrityError, sqlalchemy.exc.SQLAlchemyError) as exc:
```

to avoid swallowing programming errors like `AttributeError` or
`KeyError`, which would currently be silently absorbed into
`error_summary` and reported as a per-account failure.

### Testability

- ✅ Pure functions (incremental, mapping) are trivially testable.
- ✅ Repositories take an injectable `engine`.
- ✅ The TrueLayer client takes an injectable `httpx.Client`.
- ⚠ The orchestrator's test backdoor (`_tl_client_override`) demonstrates
  that the seams aren't quite right yet — see Dependency Inversion above.
- ✅ The CLI is testable via `main(argv)` with patched `Settings` /
  `_build_components`.

---

## 3. Maintainability review

### Class and method sizes (per CLAUDE.md limits)

**Files (max 500 lines):**

| File | Lines |
|---|---|
| `cli.py` | 249 |
| `sync/orchestrator.py` | 225 |
| `truelayer/oauth.py` | 220 |
| `db.py` | 161 |
| `truelayer/client.py` | 119 |
| All others | < 100 |

✅ All files comfortably under 500 lines.

**Methods (max 50 lines):**

All methods checked are within the 50-line limit. The longest is
`SyncOrchestrator._execute` (42 lines after the recent split) and
`cmd_auth` in `cli.py` (~67 lines if counted gross including blank lines
and docstring; ~40 if counted as logic).

✅ No method violates the 50-line rule by logic-line count.

### Nesting depth

No method exceeds 3 levels of nesting. Maximum observed: one `for` →
`try` → `if` in `_sync_all_accounts`. Acceptable.

### God objects / fat classes

- `SyncOrchestrator` is approaching "fat orchestrator" territory (11
  fields, 4 collaborator repositories, OAuth concerns). Not yet a God
  object but trending. Recommended decomposition above.
- `TrueLayerClient` is appropriately thin.
- All repositories are focused.

### Duplicate logic

| Location | Duplication |
|---|---|
| `SyncOrchestrator._ensure_fresh_token` | `self._token_repo.get("truelayer")` called twice (lines 160, 177). Minor inefficiency. |
| `cli.cmd_sync` and `cli.cmd_status` | Both call `_build_components(settings)` separately, redoing engine + repo construction. Acceptable for a CLI (each invocation is short-lived) but noted. |
| Magic string `"truelayer"` | Appears 5+ times across `cli.py`, `orchestrator.py`, and `tests/`. Should be a module-level constant `PROVIDER_TRUELAYER = "truelayer"`. |
| `SyncRunSummary` close + return | `close_run(...)` and `return SyncRunSummary(...)` use the same 7 fields back-to-back in `_execute`. Could build the summary once and pass into close_run. |

### Leaky abstractions

| # | Issue | Severity |
|---|---|---|
| 1 | Repositories return `dict[str, Any]` — callers must know column names | Medium |
| 2 | `SyncOrchestrator._tl_client_override: Any` — type system has no idea what shape this expects | Medium |
| 3 | `Settings.providers` calls inline imports inside its property — circular-import workaround leaking module structure into config | Low |
| 4 | `cmd_*` functions in `cli.py` do their own component construction via untyped `dict[str, Any]` — caller must remember keys | Low |

### Dependency management

- No external runtime dependencies beyond what `pyproject.toml` declares.
- All deps used: `httpx`, `pydantic-settings`, `structlog`, `sqlalchemy`.
- `pydantic` is in deps but only used by `pydantic-settings` (`Field`).
- ✅ No unused imports (ruff verified).

### Documentation

- ✅ Every public class and function has a docstring.
- ✅ Module docstrings present and informative.
- ⚠ No README section for "how to run `finance auth`" — only in the
  delivery doc + architecture doc.
- ⚠ No code-level comments explaining the **why** of the
  `_tl_client_override` parameter (only the docstring says "for testing
  only").

---

## 4. Test quality review

### Coverage

- **148 tests, all passing.**
- Layer breakdown: 26 (L0), 40 (L1), 15 (L2), 17 (L3), 11 (L4), 39 (Phase 0).
- All acceptance criteria AC1–AC9 have at least one corresponding test.
- Edge cases covered: empty input, missing transaction_id, expired token,
  partial failure, concurrent sync, 2190-row batch.

### Test structure

- ✅ `conftest.py` provides shared fixtures with session scope for read-only
  data and function scope for engines.
- ✅ Tests are grouped into classes by behaviour (`TestHappyPath`,
  `TestIdempotency`, etc.) — readable.
- ✅ Fixture data is the real sandbox JSON, not a hand-rolled fake.

### Quality concerns

| # | Test | Issue | Severity |
|---|---|---|---|
| T1 | `test_truelayer_client.py:64` etc. | Bearer-token check uses `assert "Bearer X" in str(call_kwargs)` — fragile stringification | Low |
| T2 | `test_truelayer_client.py:167` | `pytest.raises(Exception)` with `# noqa: B017` — should be specific to `httpx.HTTPStatusError` | Low |
| T3 | `test_repo_sync_runs.py` (per agent report) | Uses `time.sleep(0.01)` to ensure distinct timestamps — potentially flaky on slow runners | Low |
| T4 | `test_finance_status_reads_from_repositories_and_prints_summary` | Accepts `tmp_path` parameter but doesn't use it for the DB path | Low |
| T5 | `test_partial_failure_still_advances_other_account_watermarks` | Asserts watermarks exist but not what they are | Medium — weak assertion |
| T6 | `test_orchestrator.py` — `MockTrueLayerClient` | Not a typed protocol implementation; if `TrueLayerClient`'s signature changes, mock won't break compile-time | Medium |
| T7 | Magic number `2190` | Hardcoded in many places; should be derived from the fixture | Low |
| T8 | No test for `redact_sensitive` processor | The redaction logic in `log_config.py` has zero coverage | **High** |
| T9 | No test for `check_schema_version` newer-DB rejection path | Function exists but the "DB version > code version" branch has no test | Medium |
| T10 | No test for `wait_for_authorisation_code` | Reused from spike — covered by Phase-0 tests indirectly but not directly | Low |

### Test smells

- ⚠ Heavy reliance on `patch("finance_copilot.cli.X", ...)` patching paths.
  This is necessary for the CLI but couples tests to module structure.
- ⚠ `_make_components_mock` in `test_cli.py` builds real repositories + a
  `MagicMock(spec=httpx.Client)`. Hybrid mock/real setup is fine but
  could be simplified by a shared fixture (it duplicates conftest's
  `all_repos`).

### Performance

- All 148 tests run in **2.39 seconds** — well under any reasonable
  threshold. The 2190-row insert test contributes ~0.5s.

---

## 5. Technical debt review

Items recorded as TD for future tracking, **not blocking Phase 2 start**
unless flagged otherwise:

### High severity (address before Phase 2 begins)

1. **TD-1: Orchestrator has no structured logging.** AC12 is vacuously
   satisfied. There is no INFO log per sync run, no per-account WARN on
   failure, no DEBUG breadcrumb for token refresh. Operators have no
   visibility beyond the printed summary and the `sync_runs` row. Risk: in
   live use, a half-stuck sync produces no log trail.

2. **TD-2: No test coverage for `redact_sensitive`.** The processor is a
   safety-critical component (R8) — if it silently regresses to no-op
   behaviour, sensitive data would land in logs. Coverage must exist
   before any logging is added (per TD-1).

3. **TD-3: `_tl_client_override: Any` test backdoor.** Replace with a
   factory protocol; the current pattern teaches future contributors that
   adding test seams via private constructor args is acceptable.

### Medium severity

4. **TD-4: No domain models.** `models/` directory is empty. Reintroduce
   typed entities (`Account`, `Transaction`) — at minimum as frozen
   dataclasses — to provide compile-time shape enforcement.

5. **TD-5: No Repository protocol layer.** Add `Protocol` types so
   `MockTrueLayerClient` and any future in-memory repos satisfy a typed
   contract.

6. **TD-6: `except Exception` is too broad in `_sync_all_accounts`.**
   Narrow to `(TrueLayerError, MappingError, IntegrityError,
   SQLAlchemyError)`.

7. **TD-7: `IntegrityError` may leak row data via its message (R15).**
   Wrap in a `TransactionWriteError(dedup_key=...)` that carries only the
   dedup_key for logging.

8. **TD-8: Magic-string `"truelayer"` provider key.** Promote to module
   constant.

### Low severity

9. **TD-9: `cli.py` lazy imports inside functions.** Resolve circular
   import root cause and move imports to module top.

10. **TD-10: Pagination not detected.** Per R9, log a WARN if the
    transactions endpoint returns any pagination-related metadata.

11. **TD-11: `redirect_uri` stored on orchestrator but never used.** Drop
    the field.

12. **TD-12: Status command output is plain text.** Acceptable for M1; if
    needed later, support `--json`.

13. **TD-13: Test bearer-token assertions via string match.** Replace with
    `mock.call_args.kwargs["headers"]["Authorization"]`.

14. **TD-14: Hardcoded `2190` in multiple tests.** Derive from fixture.

### Out-of-scope (documented gaps from architecture)

15. No encryption-at-rest for tokens (M2 — OS keyring).
16. No multi-provider abstraction (deferred — schema supports it).
17. No Alembic (M2 if schema changes).
18. No async (intentional).

---

## 6. Refactoring recommendations (prioritised)

### Must-do before Phase 2 (block Phase 2 implementation)

**R-1: Add structured logging to the orchestrator.**

```python
# in sync/orchestrator.py
from finance_copilot.log_config import get_logger
log = get_logger("sync.orchestrator")

def run_one(self, ...):
    log.info("sync.start", run_id=run_id)
    ...
    log.info("sync.complete", run_id=run_id, status=status,
             inserted=total_inserted, skipped=total_skipped)

def _sync_all_accounts(self, ...):
    for payload in account_payloads:
        try:
            ...
            log.debug("account.sync", account_id=account_id,
                      inserted=result.inserted, skipped=result.skipped_duplicate)
        except Exception as exc:
            log.warning("account.failed", account_id=account_id,
                        error_type=type(exc).__name__)
```

**Add tests for `redact_sensitive`** to ensure the safety processor is
correct before relying on it.

**R-2: Replace `_tl_client_override` with a factory.**

```python
class TrueLayerClientFactory(Protocol):
    def __call__(self, *, access_token: str) -> TrueLayerClient: ...

def _default_factory(self) -> TrueLayerClientFactory:
    def make(*, access_token: str) -> TrueLayerClient:
        return TrueLayerClient(
            api_host=self._api_host,
            access_token=access_token,
            http_client=self._http_client,
        )
    return make

class SyncOrchestrator:
    def __init__(self, *, tl_client_factory: TrueLayerClientFactory | None = None, ...):
        self._tl_client_factory = tl_client_factory or self._default_factory()
```

Test code then provides a factory that returns the mock, removing the
`Any`-typed backdoor.

### Should-do before Phase 2 (recommended)

**R-3: Tighten the per-account exception filter** in `_sync_all_accounts`
to a specific tuple of expected error types.

**R-4: Introduce a `TransactionWriteError`** that carries only the
dedup_key, and wrap `IntegrityError` from `bulk_insert` to prevent any
risk of row-data leaking into log strings (R15).

**R-5: Promote `"truelayer"` to a module-level constant** `PROVIDER_TRUELAYER`.

### Nice-to-have (Phase 2 candidates)

**R-6: Introduce domain models** — frozen dataclasses for `Account`,
`Transaction`, with `from_row` / `to_row` classmethods. Reduces
`dict[str, Any]` throughout.

**R-7: Introduce repository protocols** — `AccountRepositoryPort`,
`TransactionRepositoryPort`, etc. Concrete classes implement these.
Easier to test, easier to mock, clearer contract.

**R-8: Extract a `TokenService`** that owns `TokenRepository +
oauth.refresh_token`. Removes the `_auth_host`, `_client_id`,
`_client_secret`, `_http_client` quartet from `SyncOrchestrator`.

**R-9: Resolve `cli.py` lazy imports** — likely caused by `Settings`
property methods importing from `truelayer/`. Restructure to eliminate.

---

## 7. Summary table — issues by severity

| Severity | Count | Items |
|---|---|---|
| **High** | 3 | TD-1, TD-2, TD-3 |
| **Medium** | 5 | TD-4, TD-5, TD-6, TD-7, T5/T6 |
| **Low** | 9 | TD-8 through TD-14, T1–T4, T7–T10 |
| **Documented gaps** | 4 | TD-15 through TD-18 |

---

## 8. Sign-off conditions

To upgrade this review from **PASS WITH RECOMMENDATIONS** to **PASS** for
the purpose of *closing Phase 1*, the following must be addressed:

1. R-1 implemented: orchestrator emits structured logs at run boundaries
   and per-account failure events.
2. Test coverage added for `redact_sensitive` processor.
3. R-2 implemented: `_tl_client_override` replaced with a typed factory.
4. Gate 2 (sandbox smoke test) executed successfully — see
   §9 of the architecture doc.
5. Gate 3 (live smoke test against First Direct) executed successfully.

Phase 2 architecture work may proceed in parallel with the above remediations.

---

## 9. Reviewer notes

- The agent-led implementation is consistent and disciplined: 148 tests,
  zero mypy errors, ruff clean, all functional ACs met. This is materially
  better than a typical first cut.
- The largest area for improvement is **the gap between "code that
  works" and "code a senior Java developer would call maintainable"** —
  primarily the missing protocols/abstractions and the empty `models/`
  directory.
- The TDD ordering shows in the test names and the layer separation.
- No regressions in the 36 Phase 0 tests.

**Verdict: PASS WITH RECOMMENDATIONS.**

Address TD-1, TD-2, TD-3 before Phase 2 implementation. Schedule Gates 2
and 3. Track remaining items as ordinary technical debt.
