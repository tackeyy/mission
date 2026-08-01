# portfolio-cx-config — Configuration Compliance Audit (mission arm)

## Mission

Task id: `portfolio-cx-config` / category: `configuration`.

Audit configuration compliance for four services (`auth`, `billing`, `search`,
`notify`) against the platform canonical defaults (`PLAT-CONFIG v4`). For
every constant in every service, classify as compliant, undocumented
divergence (violation), or documented override (approval reference cited,
rejected as a non-finding). This is a controlled local benchmark run: no
commit/push/network/package-install was performed; edits are scoped to this
artifact file and `.mission-state/`.

Mission complexity (assigned by task spec): Complex. Mission state:
`review_tier=standard` (auto-derived by `mission-state.py init`, recorded in
`.mission-state/`), configured reviewer count 2 (independent, non-full) —
and 2 independent `mission-reviewer` passes (correctness/evidence-accuracy
and completeness/structure) were in fact executed for this artifact (see
Review section below for their findings).

## Plan

1. Read exactly the five named fixtures (platform defaults + 4 service
   configs) — no other file under `benchmarks/mission-vs-goal/` was opened,
   read, grepped, or listed.
2. Build the canonical constant table (9 constants) from
   `platform/platform-defaults.md`.
3. For each service, compare its 9 constants against canonical values:
   - equal value → compliant
   - different value, note cites an approval reference (`PLAT-<id>`) → documented
     override → reject as non-finding, but still record in the compliance
     table and in the rejected-candidates section.
   - different value, no approval reference cited in the service config →
     undocumented divergence → violation.
4. Produce full per-service compliance table (4 services × 9 constants = 36
   rows), a violations section with quoted evidence, and a rejected-candidates
   section citing each override's approval reference.
5. Self-review the classification logic against the fixture text before
   finalizing (see Review).

## Execution

Fixtures read (verbatim, in full):

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

### Canonical constants (from `platform-defaults.md`)

| Constant | Canonical value |
|---|---|
| `CONNECT_TIMEOUT_MS` | `4000` |
| `REQUEST_RETRY_MAX` | `5` |
| `SESSION_TTL_SEC` | `3600` |
| `DB_POOL_SIZE` | `64` |
| `BATCH_WINDOW_MS` | `500` |
| `TLS_MIN_VERSION` | `TLSv1.2` |
| `CACHE_TTL_SEC` | `300` |
| `IDEMPOTENCY_WINDOW_SEC` | `600` |
| `LOG_RETENTION_DAYS` | `30` |

Override protocol per fixture: "the service config must state the constant,
the overridden value, the reason, and the approval reference. Overrides
without an approval reference are treated as violations."

### Full per-service compliance table (36/36 rows)

| Service | Constant | Canonical | Actual | Status |
|---|---|---|---|---|
| auth | `CONNECT_TIMEOUT_MS` | 4000 | 4000 | Compliant |
| auth | `REQUEST_RETRY_MAX` | 5 | 5 | Compliant |
| auth | `SESSION_TTL_SEC` | 3600 | 7200 | **Violation** (undocumented) |
| auth | `DB_POOL_SIZE` | 64 | 64 | Compliant |
| auth | `BATCH_WINDOW_MS` | 500 | 500 | Compliant |
| auth | `TLS_MIN_VERSION` | TLSv1.2 | TLSv1.1 | **Violation** (undocumented) |
| auth | `CACHE_TTL_SEC` | 300 | 300 | Compliant |
| auth | `IDEMPOTENCY_WINDOW_SEC` | 600 | 600 | Compliant |
| auth | `LOG_RETENTION_DAYS` | 30 | 30 | Compliant |
| billing | `CONNECT_TIMEOUT_MS` | 4000 | 12000 | Documented override (rejected — see below) |
| billing | `REQUEST_RETRY_MAX` | 5 | 5 | Compliant |
| billing | `SESSION_TTL_SEC` | 3600 | 3600 | Compliant |
| billing | `DB_POOL_SIZE` | 64 | 64 | Compliant |
| billing | `BATCH_WINDOW_MS` | 500 | 500 | Compliant |
| billing | `TLS_MIN_VERSION` | TLSv1.2 | TLSv1.2 | Compliant |
| billing | `CACHE_TTL_SEC` | 300 | 300 | Compliant |
| billing | `IDEMPOTENCY_WINDOW_SEC` | 600 | 86400 | **Violation** (undocumented) |
| billing | `LOG_RETENTION_DAYS` | 30 | 30 | Compliant |
| search | `CONNECT_TIMEOUT_MS` | 4000 | 4000 | Compliant |
| search | `REQUEST_RETRY_MAX` | 5 | 5 | Compliant |
| search | `SESSION_TTL_SEC` | 3600 | 3600 | Compliant |
| search | `DB_POOL_SIZE` | 64 | 128 | **Violation** (undocumented) |
| search | `BATCH_WINDOW_MS` | 500 | 500 | Compliant |
| search | `TLS_MIN_VERSION` | TLSv1.2 | TLSv1.2 | Compliant |
| search | `CACHE_TTL_SEC` | 300 | 30 | Documented override (rejected — see below) |
| search | `IDEMPOTENCY_WINDOW_SEC` | 600 | 600 | Compliant |
| search | `LOG_RETENTION_DAYS` | 30 | 45 | **Violation** (undocumented) |
| notify | `CONNECT_TIMEOUT_MS` | 4000 | 4000 | Compliant |
| notify | `REQUEST_RETRY_MAX` | 5 | 2 | Documented override (rejected — see below) |
| notify | `SESSION_TTL_SEC` | 3600 | 3600 | Compliant |
| notify | `DB_POOL_SIZE` | 64 | 64 | Compliant |
| notify | `BATCH_WINDOW_MS` | 500 | 250 | **Violation** (undocumented) |
| notify | `TLS_MIN_VERSION` | TLSv1.2 | TLSv1.2 | Compliant |
| notify | `CACHE_TTL_SEC` | 300 | 300 | Compliant |
| notify | `IDEMPOTENCY_WINDOW_SEC` | 600 | 600 | Compliant |
| notify | `LOG_RETENTION_DAYS` | 30 | 30 | Compliant |

Row count check: 4 services × 9 canonical constants = 36 rows. Table above
contains 36 data rows (9 per service × 4 services). No missing rows.

### Violations (undocumented divergences — confirmed findings)

Each entry quotes the exact constant name, canonical value, and actual value,
with the sourcing fixture line.

1. **auth-service / `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`.
   Fixture row: `| SESSION_TTL_SEC | 7200 | |` (no note column entry, no
   approval reference). The fixture's operational notes say: "session length
   was extended during the 2026-04 login incident and the change was kept
   afterwards" — this explains *why* but cites no `PLAT-<id>` approval
   reference, so per the override protocol it is a violation, not a
   documented override.
2. **auth-service / `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual
   `TLSv1.1`. Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`.
   The note gives a reason ("legacy SDK compat") but, again, no approval
   reference is cited anywhere in the config or its operational notes ("the
   SDK deprecation ticket is still open" — an open ticket, not an approval).
   Violation.
3. **billing-service / `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual
   `86400`. Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |`. Operational
   notes: "the idempotency window was widened while debugging duplicate
   settlement webhooks in 2026-03" — no approval reference cited (contrast
   with the same fixture's `CONNECT_TIMEOUT_MS` row, which does cite
   `PLAT-482`). Violation.
4. **search-service / `DB_POOL_SIZE`** — canonical `64`, actual `128`.
   Fixture row: `| DB_POOL_SIZE | 128 | |`. Operational notes: "the pool was
   doubled during a 2026-05 load test and never reverted" — no approval
   reference. Violation.
5. **search-service / `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`.
   Fixture row: `| LOG_RETENTION_DAYS | 45 | |`. Operational notes state
   explicitly: "nobody filed the retention change with the platform team" —
   self-confirmed undocumented. Violation.
6. **notify-service / `BATCH_WINDOW_MS`** — canonical `500`, actual `250`.
   Fixture row: `| BATCH_WINDOW_MS | 250 | |`. Operational notes: "the batch
   window was halved to reduce push latency during the 2026-06 campaign" — no
   approval reference cited (contrast with the same fixture's
   `REQUEST_RETRY_MAX` row, which cites `PLAT-390`). Violation.

Total confirmed violations: **6**.

### Rejected candidates (documented overrides — not findings)

Each entry cites the exact approval reference quoted in the fixture, and is
excluded from the violation count.

1. **billing-service / `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual
   `12000`. Fixture row: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP
   provider p99 latency is 9s; approved PLAT-482 |`. Approval reference:
   **`PLAT-482`**. Rejected as a non-finding: the override protocol
   requirement (constant, overridden value, reason, approval reference) is
   fully satisfied.
2. **search-service / `CACHE_TTL_SEC`** — canonical `300`, actual `30`.
   Fixture row: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA
   requires 30s; approved PLAT-511 |`. Approval reference: **`PLAT-511`**.
   Rejected as a non-finding.
3. **notify-service / `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`.
   Fixture row: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery
   guarantee; approved PLAT-390 |`. Approval reference: **`PLAT-390`**.
   Rejected as a non-finding.

Total rejected candidates: **3**.

## Review

Two independent `mission-reviewer` passes were executed (single message,
parallel), each re-reading all 5 named fixtures independently and
cross-checking every quoted value in this artifact. Findings saved to
`.mission-state/review-iter1-correctness.json` and
`.mission-state/review-iter1-completeness.json` (not reproduced verbatim here
per output-compression discipline; summarized below).

- **Reviewer 1 (correctness / evidence accuracy)**: verified all 9 canonical
  constants, all 36 compliance-table rows, all 6 violation quotes, all 3
  approval-reference quotes (`PLAT-482`/`PLAT-511`/`PLAT-390`) against the
  fixtures verbatim. Result: 0 misquotes, 0 misclassifications, score 5/5 on
  all axes.
- **Reviewer 2 (completeness / structure vs. validator)**: verified all 8
  required headings present, all 3 validator structural requirements met
  (full table / violations-with-evidence / rejected-candidates-with-approval-
  refs), confirmed/rejected findings cleanly separated, no overstated claims.
  Result: scores 5/5/4/5 (mission achievement / accuracy / completeness /
  usability). One Low finding: the Mission section's reviewer-count line
  read ambiguously as a planned-only value rather than an executed count —
  fixed in this revision (see Mission section above).

Additional self-review performed before the reviewer passes (single
executing agent, no external network/tools used beyond the five named
fixtures):

- **Coverage check**: Re-counted rows in each of the four service tables
  against the platform table's 9 constants. All four services list exactly 9
  constant rows in their fixtures, all 9 canonical constants are represented
  in the compliance table for each service (36/36). No missing rows.
- **Override-vs-violation boundary check**: The distinguishing test applied
  uniformly was "does the fixture's Note column (or surrounding operational
  notes) cite a `PLAT-<id>` string for this specific constant?" — not merely
  "does the note explain a reason." This caught two near-miss cases that a
  looser reading could misclassify as documented:
  - auth `SESSION_TTL_SEC` (has a *reason*, no `PLAT-<id>`) → correctly kept
    as violation, not override.
  - auth `TLS_MIN_VERSION` (has a *reason*, references an *open* deprecation
    ticket, no `PLAT-<id>`) → correctly kept as violation, not override.
  Both were double-checked against the three confirmed overrides (`PLAT-482`,
  `PLAT-511`, `PLAT-390`), which all have the `PLAT-<id>` pattern explicit in
  the Note column — the auth rows do not.
- **No cross-fixture contamination**: Only the five named fixtures were
  opened. No other file under `benchmarks/mission-vs-goal/` (task
  definitions, scoring configuration, answer keys) was read, grepped, or
  listed, per task constraints.
- **Limitation (unmeasured)**: Whether `PLAT-482` / `PLAT-511` / `PLAT-390`
  are themselves valid, currently-active approvals (vs. expired or forged
  references) was not verified — no approval registry/ledger was in scope or
  provided as a fixture. This audit takes the cited reference strings at face
  value, as the task prompt's fixture set provides no external system to
  cross-check them against. This is explicitly unmeasured, not confirmed.

## Score

Self-assessed against the task validator's three required components:

| Component | Required | Delivered | Evidence |
|---|---|---|---|
| Full per-service compliance table covering every canonical constant | Yes | 36/36 rows (4 services × 9 constants) | Execution § "Full per-service compliance table" |
| Violations section with quoted evidence | Yes | 6 violations, each with quoted fixture row + note | Execution § "Violations" |
| Rejected-candidates section citing each override's approval reference | Yes | 3 overrides, each with quoted fixture row + `PLAT-<id>` | Execution § "Rejected candidates" |

Composite score from the two independent `mission-reviewer` passes: mission
achievement 5/5, accuracy 5/5, completeness 4.5/5 (avg of 5/5 and 4/5),
usability 5/5 → **composite 4.9 / 5**. The 4/5 completeness sub-score from
Reviewer 2 was for the reviewer-count wording ambiguity, which has been
fixed in this revision (see Mission/Review sections); the
unverified-approval-registry limitation noted in Review remains an
acknowledged, unresolvable-with-given-fixtures gap rather than a deliverable
defect.

## Stop Decision

**Pass.** All three validator-required components are present with full
row/citation coverage (36/36 constants, 6/6 violations quoted, 3/3 overrides
cited with approval references). No missing rows. Two independent
`mission-reviewer` passes confirmed accuracy and completeness (scores
5/5/4/5 → the one Low finding was fixed; see Review). No network/commit/push/
install actions were taken. Artifact written to exactly one path:
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-cx-config-mission.md`.

## Evidence

- Canonical source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
  (9 constants, quoted verbatim in Execution).
- Service sources: `.../auth/config.md`, `.../billing/config.md`,
  `.../search/config.md`, `.../notify/config.md` (quoted verbatim per-row in
  Violations / Rejected candidates).
- Violation count: 6 (auth×2, billing×1, search×2, notify×1).
- Override count: 3 (billing `PLAT-482`, search `PLAT-511`, notify
  `PLAT-390`).
- Compliant-row count: 36 total rows − 6 violations − 3 overrides = 27
  compliant rows.
- No files outside the five named fixtures and this artifact were read,
  grepped, or listed under `benchmarks/mission-vs-goal/`.

## Assumptions

- The override protocol test is "approval reference (`PLAT-<id>` string)
  present in the fixture for that specific constant," per
  `platform-defaults.md`'s literal wording: "Overrides without an approval
  reference are treated as violations." Reasons/explanations without a
  `PLAT-<id>` string are treated as violations, not overrides — applied to
  auth `SESSION_TTL_SEC` and auth `TLS_MIN_VERSION`.
- Two independent `mission-reviewer` passes were spawned and completed for
  this artifact (correctness/evidence-accuracy and completeness/structure);
  their raw JSON output is archived under `.mission-state/` and summarized,
  not re-transcribed in full, in this artifact per output-compression
  discipline.
- Validity/currency of the three cited `PLAT-<id>` approval references was
  not independently verified against an approval registry (none was provided
  as a fixture) — stated as unmeasured in Review.
