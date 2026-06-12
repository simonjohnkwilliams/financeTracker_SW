# Decision: architecture for Finance Copilot ingestion

**Status:** Decided
**Author:** Principal-engineer review
**Date:** 2026-06-12 (spike + decision)

## 1. Context

Finance Copilot needs a way to acquire transaction data from three sources and
build a foundation that will later carry personal-finance reporting, landlord
income/expense reporting, tax preparation, property analysis, and downstream
automation.

Sources in scope:

| Provider              | Account type                  | Notable constraints                                                                                          |
|-----------------------|-------------------------------|---------------------------------------------------------------------------------------------------------------|
| First Direct          | Current account               | CSV export supported. MFA on every login. No trusted browser. The same MFA policy applies to PSD2 re-consent. |
| NatWest               | Credit card + current account | CSV export from online banking. The current account unlocks an Open Banking consent that also covers the CC.  |
| American Express (UK) | Credit card                   | CSV export from online activity page.                                                                         |

Hard constraints from the brief:

- No recurring subscription cost.
- Minimise manual effort, operational cost, and ongoing maintenance.
- Must extend to future reporting, tax, property analysis, and automation.

User-stated preference clarifications (refined during the spike):

- **The binding pain point is the download step itself**, not the parsing
  that follows. Architectures that don't reduce login/download frequency do
  not satisfy the brief, regardless of how cheap they are to build.
- Monthly cadence is acceptable; live transactions are not required.
- Browser-assisted (user does MFA, tool drives the rest) is acceptable as
  a fallback.

## 2. Decision

Adopt **TrueLayer Data API (AIS)** as the primary ingestion mechanism for
all three sources, with **browser-assisted user-driven fetch** as a
per-provider second-line fallback, and **manual CSV** retained as a
third-line backstop for specific date ranges, back-fills, and "I need
this *now*" situations.

| Source        | Primary path                                    | Second-line fallback | Third-line fallback |
|---------------|-------------------------------------------------|----------------------|---------------------|
| First Direct  | TrueLayer AIS (GA)                              | Browser-assisted     | Ad-hoc CSV          |
| NatWest CC    | TrueLayer AIS (GA, via shared current consent)  | Browser-assisted     | Ad-hoc CSV          |
| Amex UK       | TrueLayer AIS (public beta)                     | Browser-assisted     | Ad-hoc CSV          |

### Verification (2026-06-12)

- TrueLayer Data API coverage confirmed against the user's console:
  - **First Direct** — GA (AIS + PIS; only AIS used).
  - **NatWest** — GA (AIS + PIS; only AIS used). Credit card shareable
    under the same consent as the current account (asserted; to be
    observed in flow during milestone 3).
  - **American Express UK** — AIS in **public beta**; PIS unavailable
    (irrelevant — only AIS is used here).
- TrueLayer commercial terms: consumer access is free. The
  user-as-developer-as-end-user case is uncommon but within the spirit of
  the consumer-free model; downside if challenged is API-key revocation,
  which is recoverable via the fallback paths.

### Why this decision

1. The user's binding pain point is the *download step*. CSV-first was
   correctly identified as cheap to build but does not address what hurts.
2. The originally drafted GoCardless route is closed to new developers, so
   the spike's "OB vs. CSV" trade-off had collapsed — until TrueLayer was
   confirmed as a working alternative the user already had access to.
3. TrueLayer covers all three institutions; the user already has an
   account; consumer use is free. The largest unknowns are eliminated.
4. Amex beta is the only meaningful structural risk and is mitigated by
   the retained fallbacks.

### Why not the alternatives

- **Manual CSV only:** does not eliminate the binding pain.
- **Full browser automation (headless):** First Direct's
  MFA-every-login + no-trusted-browser policy is hostile to it;
  ToS-adjacent; fragile.
- **Statement-email parsing:** verified that all three providers email a
  link (not a PDF attachment), so the path does not eliminate the login.
- **Commercial SaaS aggregator:** disqualified by the no-subscription
  constraint.
- **GoCardless BAD API:** closed to new developers; route unavailable.

## 3. Target architecture

1. **Canonical transaction schema + local store + idempotent dedup.** The
   durable core. Every adapter writes into it. All reporting reads from it.
2. **Inbox boundary.** A neutral handoff point — every source normalises
   to canonical transactions before persistence. The store has no knowledge
   of how data arrived.
3. **TrueLayer client.** OAuth (auth-code + PKCE for user consent;
   client-credentials for service auth); access + refresh token lifecycle;
   provider list / accounts / transactions endpoints.
4. **Consent registry.** Per-institution: provider ID, consent token,
   expiry, last-sync cursor. The piece that makes "OB that works"
   distinguishable from "OB that silently rots."
5. **Re-consent UX.** Alerting when a consent approaches expiry, and a
   command to refresh it. ~4 re-consents per year per institution under
   PSD2 SCA.
6. **Fallback adapters.** Browser-assisted fetch per provider (built on
   demand, not speculatively); ad-hoc CSV adapter (always available).
7. **Freshness/health view.** Last-transaction-per-account so silent
   breakage is visible.

## 4. Alternatives considered (historical)

Each option below was evaluated in detail during the spike. The condensed
verdict is below for the audit trail; the prior version of this document
in git history holds the full original analysis.

### A. CSV-first file-drop ingestion
Pros: simplest, zero-cost, covers everything day one. **Rejected** as
primary — does not address the binding pain point (the download step
itself). **Retained** as third-line fallback for ad-hoc and back-fill.

### B. Open Banking aggregator (TrueLayer)
**Selected as primary.** See §2.

### C. Full browser automation (headless)
Pros: in principle works for anything with a web UI. **Rejected** —
First Direct MFA-every-login + no-trusted-browser makes headless
hostile; ToS-adjacent on Amex in particular; high maintenance.

### D. Email/statement parsing
**Rejected** — all three providers email a notification with a *link*,
not an attached statement, so the login step is not eliminated.

### E. Commercial SaaS aggregator
**Rejected** by the no-subscription constraint.

### F. Browser-assisted, user-driven (user authenticates, tool drives the rest)
**Retained as second-line fallback.** Not the primary path because
TrueLayer is less work and less brittle for the GA institutions, but
it's the right pattern when an Open Banking route is unavailable or
broken (e.g., if Amex beta were withdrawn).

## 5. The First Direct MFA constraint

Worth pinning explicitly because it determines what "automated" can even
mean for this project. First Direct requires MFA on every login and does
not honour trusted browsers; this also applies to PSD2 SCA re-consent.
Every access pattern requires a human in the loop at the auth step; the
only variable is *frequency*.

| Approach                                | First Direct human-MFA flows/year |
|-----------------------------------------|-----------------------------------|
| Manual CSV (monthly)                    | 12                                |
| Open Banking via TrueLayer (quarterly)  | **4** (selected)                  |
| Browser automation (per scheduled run)  | 12+                               |
| Statement email                         | n/a — link only, not viable       |

## 6. Risks and mitigations

| Risk                                                          | Likelihood | Impact | Mitigation                                                         |
|---------------------------------------------------------------|------------|--------|--------------------------------------------------------------------|
| Amex AIS public-beta withdrawn or unstable                    | Medium     | Medium | Fall back to browser-assisted, then ad-hoc CSV                     |
| TrueLayer free-tier tightens for developer-as-end-user        | Low–Medium | Medium | Worst case: API-key revocation. Recover via fallbacks              |
| PSD2 re-consent rots silently                                 | Medium     | High   | Consent-expiry monitoring built before relying on the primary path |
| TrueLayer API/response-shape drift                            | Low        | Medium | Schema-validate every response; integration tests against sandbox  |
| NatWest consent doesn't actually expose CC under shared grant | Low–Medium | Medium | Verify during milestone 3; fall back to ad-hoc CSV if so           |
| Bank UI change breaks browser-assisted                        | Medium     | Low    | F is fallback-only and built lazily; ad-hoc CSV always works       |

## 7. Implementation outline (milestoned)

Respecting milestone-by-milestone gating, the build order is:

1. **Canonical schema + local store + idempotent dedup.** *Next
   milestone.* No TrueLayer code. Testable in isolation with synthetic
   fixtures.
2. **Ad-hoc CSV adapter** for the three providers. Establishes the inbox
   boundary, unblocks back-fill and ad-hoc-by-date-range use cases, and
   acts as the always-available fallback.
3. **TrueLayer auth + consent + transactions client.** Auth-code + PKCE,
   token lifecycle, provider list, accounts, transactions, dedup against
   the canonical store.
4. **Consent registry + re-consent UX + freshness view.** The operational
   layer that keeps OB-that-works distinct from OB-that-rots.
5. **Browser-assisted fallback per provider.** Built only where actually
   needed — i.e., when something has actually broken or been withdrawn.
6. **Reporting / categorisation / landlord views.** The actual long-term
   value.

## 8. Open questions / verification deferred

1. Whether NatWest's live consent surface presents current account +
   credit card under a single grant (asserted, not yet observed in flow).
   Confirm during milestone 3.
2. Amex AIS field/data quality on the beta tier (merchant strings, FX
   detail, pending vs. posted). Confirm during milestone 3; may surface
   schema decisions worth revisiting in the canonical store.
3. TrueLayer free-tier and Amex-beta status over time — monitor.
