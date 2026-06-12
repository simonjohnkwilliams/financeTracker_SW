# Finance Copilot — delivery plan

This is a working document. It is read at the start of each phase. Built
out as we go — kept narrow on purpose. The broader architecture rationale
is in `docs/spike-architecture-options.md`.

---

## Phase 0 — Spike: First Direct end-to-end via TrueLayer

**Goal:** Prove that we can connect to **First Direct** through TrueLayer
and download transactions to a local file. Nothing more. No database, no
canonical schema, no CLI structure, no dedup, no tests beyond "it ran and
produced a file."

If this works, we will know the rest of the project rests on solid ground
and can plan the proper build with confidence. If it doesn't, we learn
cheaply.

### Scope (in)

- [ ] One self-contained script: `scripts/spike_first_direct.py`.
- [ ] Read `TRUELAYER_CLIENT_ID`, `TRUELAYER_CLIENT_SECRET`,
      `TRUELAYER_ENVIRONMENT` (`sandbox` or `live`), and
      `TRUELAYER_REDIRECT_URI` from `.env`.
- [ ] Auth-code + PKCE flow:
      - Generate the PKCE verifier and challenge.
      - Print the TrueLayer auth URL (and optionally open it).
      - Start a small local HTTP listener on the redirect URI to capture
        the `code`.
      - Exchange the code for `access_token` + `refresh_token`.
- [ ] Hit `GET /data/v1/accounts`.
- [ ] For each returned account, hit
      `GET /data/v1/accounts/{id}/transactions`.
- [ ] Dump the raw JSON (accounts + transactions) to
      `data/spike_first_direct.json`.
- [ ] Print a one-line summary: account count, transaction count, date
      range observed.

### Out of scope

- SQLAlchemy / canonical schema / repository abstraction.
- `finance` CLI plumbing.
- NatWest, Amex.
- Token refresh, retry logic, error recovery beyond raising clearly.
- Tests.
- Reusable abstractions — the script is throwaway.

### Acceptance

`uv run python scripts/spike_first_direct.py` completes successfully:
the consent flow finishes in the browser, the script prints a non-zero
transaction count, and `data/spike_first_direct.json` contains the raw
response payloads.

### User prerequisites

- TrueLayer app credentials (`client_id`, `client_secret`) recorded in
  `.env` based on `.env.example`.
- TrueLayer `redirect_uri` allowed in the console matches what the
  spike will listen on (proposed default: `http://localhost:8765/callback`).
- Decide: run against **sandbox** first (validates flow shape against
  mock data) or straight to **live** (validates real First Direct
  connection)? Recommendation: sandbox first, then live.

### Sign-off pause (after this phase)

Once the file exists, we look at:

1. What the response actually contains — fields, formats, oddities.
2. Whether anything observed changes the canonical-schema thinking from
   the architecture spike.
3. Whether live First Direct behaves materially differently from sandbox.

Then we draft Phase 1 here, informed by what we saw.

---

## Phase 1+ — to be drafted after Phase 0

Deliberately blank. The full milestoned plan was previously sketched
(canonical store, CSV adapters, full TrueLayer client, consent registry,
browser-assisted fallback, reporting) but committing to it now is
premature. It will be re-drafted once the spike proves the technical path.
