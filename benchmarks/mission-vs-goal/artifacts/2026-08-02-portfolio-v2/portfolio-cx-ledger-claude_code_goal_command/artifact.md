# Portfolio CX Ledger — Release Ledger Reconciliation

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-cx-ledger-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: reconcile the release ledger across five documents (`changelog.md`,
`release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`)
under `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`,
find every cross-document inconsistency, quote exact identifiers from both
sides of each inconsistency, and reject apparent inconsistencies that are
explained inside the documents (citing the explanation).

## Result

Read all five named fixtures. Found **5 confirmed cross-document
inconsistencies** and **2 rejected candidates** (apparent inconsistencies that
are explained in-document). Reconciliation table, confirmed findings, and
rejected candidates are below.

### Reconciliation table

| Item | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md | Status |
|---|---|---|---|---|---|---|
| 2.32.1 | "Fix export pagination off-by-one (EXP-441)" (2026-06-24) | "current GA... Recommended for all tenants" | "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx... Fleet pinned to 2.32.0" (2026-06-25) | — | — | **Confirmed inconsistency (#3)** |
| 2.32.0 / MIG-2207 | "Bulk export API GA (EXP-380)"; dependency upgrade fastjson 3.2→3.3 | "deployed with zero downtime" | "Incident OUT-88: 6-minute full outage during the schema migration window"; "Migration MIG-2207 applied to prod (bulk export tables)" (2026-06-17) | fastjson 3.3 confirmed in dependency snapshot; **MIG-2207 absent from table** | — | **Confirmed inconsistency (#1)**; fastjson entry consistent |
| 2.31.4 (EU hotfix) | *no entry* | *no entry* | "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)" (2026-06-02) | — | SUP-1189: "Engineering shipped hotfix 2.31.4 to the EU shard... Note: no changelog entry was published for 2.31.4" | **Confirmed inconsistency (#2)** |
| 2.31.2 / CVE-2026-4417 | "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)" (2026-05-28) | "Security maintenance release" | "Deploy 2.31.2. Migration MIG-2199 applied" (2026-05-28) | dependency snapshot (2026-06-24): "fastcsv 1.8.3" | SUP-1188: "remediation requires fastcsv >= 1.9.0; verify the shipped pin" | **Confirmed inconsistency (#4)** |
| fastcsv license | — | "bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)" | — | fastcsv 1.8.3 in dependency snapshot | SUP-1204: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected... NOTICE file update pending" | **Confirmed inconsistency (#5)** |
| MIG-2199 | — | — | "Deploy 2.31.2. Migration MIG-2199 applied" (2026-05-28) | "MIG-2199 \| 2.31.2 \| 2026-05-27" | — | Consistent (registered day before deploy) |
| MIG-2183 | — | — | "Deploy 2.31.0. Migration MIG-2183 applied" (2026-05-14) | "MIG-2183 \| 2.31.0 \| 2026-05-13" | — | Consistent (registered day before deploy) |
| 2.30.x version gap | "2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77" | — | — | table jumps MIG-2160 (2.29.6) → MIG-2183 (2.31.0) with no 2.30.x row | — | **Rejected candidate (#a)** — explained |
| EXP-380 | "Bulk export API GA (EXP-380)" (2.32.0) | "Includes the new audit export (EXP-380 preview)" under 2.31.0 | — | — | — | **Rejected candidate (#b)** — explained (preview vs GA) |

### Confirmed inconsistencies

**#1 — MIG-2207 applied but not registered in the authoritative migration index**
- ops-log.md: `"2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."`
- migration-index.md header: `"All production schema changes MUST be registered here before deploy."` — the table lists only `MIG-2199`, `MIG-2183`, `MIG-2160`; **`MIG-2207` does not appear**.
- No document offers an explanation for the omission. This is notable alongside ops-log's same-day entry `"Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)"` for the 2.32.0 deploy.

**#2 — Hotfix 2.31.4 deployed and ticketed, but absent from the changelog**
- ops-log.md: `"2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."`
- support-tickets.md (SUP-1189): `"Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."`
- changelog.md has no `2.31.4` entry at all — it jumps from `## 2.31.2 — 2026-05-28` directly to `## 2.32.0 — 2026-06-17`.
- The support ticket records the *fact* of the gap but gives no justification for why the changelog was never updated, so this is not treated as explained/rejected.

**#3 — Release notes still present 2.32.1 as current/recommended after it was rolled back**
- release-notes.md: `"## 2.32.1 (current GA) Export pagination hotfix. Recommended for all tenants."`
- ops-log.md: `"2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."`
- The parenthetical `"OUT-91 draft, not yet published"` only explains why no separate incident report exists — it does not explain why the customer-facing release notes were left recommending a version that has since been rolled back fleet-wide. Treated as a live, unexplained contradiction.

**#4 — Changelog claims CVE-2026-4417 is "fixed", but the shipped fastcsv version does not meet the required remediation threshold**
- changelog.md (2.31.2): `"Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"`.
- migration-index.md dependency snapshot (2026-06-24): `"fastcsv 1.8.3"`.
- support-tickets.md (SUP-1188): `"Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."`
- `1.8.3 < 1.9.0`, so the changelog's "fixed" claim is contradicted by the dependency snapshot and the security follow-up in the same ticket set.

**#5 — Release notes' license claim contradicts the support-ticket relicensing finding**
- release-notes.md: `"Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."`
- support-tickets.md (SUP-1204): `"Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."`
- migration-index.md confirms the bundled version is `fastcsv 1.8.3`, which is ≥ 1.8.0 and therefore within the affected range per SUP-1204. The release notes' "MIT, unchanged since 2025" claim is contradicted by the ticket, and the ticket itself flags the NOTICE file as not yet corrected (`"NOTICE file update pending"`).

### Rejected candidates (explained in-document)

**#a — Apparent version-numbering gap (2.30.x missing)**
- Candidate signal: migration-index.md's table jumps from `MIG-2160 | 2.29.6` straight to `MIG-2183 | 2.31.0`, with no `2.30.x` migration or changelog entry anywhere.
- Explanation cited: changelog.md states directly: `"Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."`
- **Rejected** — the gap is explicitly accounted for by an approved renumbering decision (RFC-77), not a missing/lost record.

**#b — Apparent contradiction on EXP-380's release status**
- Candidate signal: release-notes.md's `2.31.0` entry says `"Includes the new audit export (EXP-380 preview)"`, while changelog.md's `2.32.0` entry says `"Bulk export API GA (EXP-380)"` — same identifier, two different maturity states (preview vs. GA).
- Explanation cited: read together, the two entries describe a normal feature lifecycle — release-notes.md explicitly labels the 2.31.0 appearance as a `"preview"`, and changelog.md's 2.32.0 entry marks the same feature reaching `"GA"`. The documents are not contradicting each other; they describe sequential stages of the same feature (preview → GA), consistent with the version ordering (2.31.0 precedes 2.32.0).
- **Rejected** — the two states are explained by normal preview-to-GA progression, not a factual conflict.

## Evidence

All evidence is quoted verbatim above from the five required fixtures:
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/changelog.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/release-notes.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/ops-log.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/migration-index.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/support-tickets.md`

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or
listed during this task, per the task rules (only the five named fixtures and
this output file were accessed).

## Assumptions

- "Registered" dates in migration-index.md that precede the corresponding
  ops-log.md deploy/apply dates by one day (MIG-2199: registered 2026-05-27,
  applied 2026-05-28; MIG-2183: registered 2026-05-13, applied 2026-05-14) are
  assumed to reflect normal pre-deploy registration workflow, not an
  inconsistency — this is unmeasured against any explicit process document
  beyond migration-index.md's own header requirement ("MUST be registered
  here before deploy"), which both entries satisfy.
- No production/runtime state (actual deployed version, actual NOTICE file
  contents, actual fastcsv pin in a lockfile) was inspected — all findings are
  based solely on cross-referencing the five text fixtures as instructed, not
  on independently verifying which document is "correct."
- MIG-2160/2.29.6 predates the reconciliation window implied by the task
  (2026 Q2 hotfix/security items) and is included only for completeness of
  the migration-index cross-check; it did not surface any inconsistency.

## Stop Condition

This artifact is complete: all five required headings (Goal, Result,
Evidence, Assumptions, Stop Condition) are present, a reconciliation table is
included, a confirmed-inconsistencies section with quoted evidence from both
sides of each of the 5 confirmed findings is included, and a
rejected-candidates section citing the in-document explanation for each of
the 2 rejected candidates is included. No further reads, edits, commits, or
network access are needed to satisfy the task or the `/goal` completion
condition.
