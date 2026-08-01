# Portfolio CX Ledger Reconciliation — Release Ledger (2026 Q2)

## Goal

Reconcile the release ledger across five documents (`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`) and find every cross-document inconsistency — entries recorded in one document but missing or contradicted in another. Quote the exact identifiers from both sides of every inconsistency. Apparent inconsistencies that are explained inside the documents must be rejected as non-findings, citing the explanation.

## Result

Five fixtures were read in full. Six confirmed cross-document inconsistencies were found and two apparent inconsistencies were rejected as non-findings because the documents themselves contain an explanation. A reconciliation table covering all version/entity references across the five documents is included below.

## Reconciliation Table

| Identifier / Version | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md |
|---|---|---|---|---|---|
| 2.29.6 | "## 2.29.6 — 2026-04-30 / - Minor bug fixes" | not mentioned | not mentioned | "MIG-2160 \| 2.29.6 \| 2026-04-29" | not mentioned |
| 2.30.x | "2.30.x was never released... RFC-77" | not mentioned | not mentioned | not mentioned | not mentioned |
| 2.31.0 / MIG-2183 | "## 2.31.0 — 2026-05-14 / - New retention settings page (RET-201)" | "## 2.31.0 / Retention settings page. Includes the new audit export (EXP-380 preview)." | "2026-05-14 \| Deploy 2.31.0. Migration MIG-2183 applied." | "MIG-2183 \| 2.31.0 \| 2026-05-13" | not mentioned |
| 2.31.2 / MIG-2199 | "## 2.31.2 — 2026-05-28 / - Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)" | "## 2.31.2 / Security maintenance release. Customers on 2.31.x should upgrade." | "2026-05-28 \| Deploy 2.31.2. Migration MIG-2199 applied." | "MIG-2199 \| 2.31.2 \| 2026-05-27" | SUP-1188: "whether 2.31.2 fully remediates CVE-2026-4417... remediation requires fastcsv >= 1.9.0; verify the shipped pin" |
| 2.31.4 | not mentioned | not mentioned | "2026-06-02 \| Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)." | not mentioned | SUP-1189: "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02... no changelog entry was published for 2.31.4." |
| 2.32.0 / MIG-2207 / EXP-380 | "## 2.32.0 — 2026-06-17 / - Bulk export API GA (EXP-380) / - Dependency upgrades: fastjson 3.2 -> 3.3" | "## 2.32.0 / Bulk export API is now generally available. This release was deployed with zero downtime." | "2026-06-17 \| Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)." / "2026-06-17 \| Migration MIG-2207 applied to prod (bulk export tables)." | no MIG-2207 row (table lists only MIG-2199, MIG-2183, MIG-2160); "Dependency manifest snapshot (2026-06-24): fastjson 3.3..." | not mentioned |
| 2.32.1 / EXP-441 | "## 2.32.1 — 2026-06-24 / - Fix export pagination off-by-one (EXP-441)" | "## 2.32.1 (current GA) / Export pagination hotfix. Recommended for all tenants." | "2026-06-25 \| Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0." | not mentioned | not mentioned |
| fastcsv (license) | "upgrade fastcsv" (2.31.2, no version given) | "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)." | not mentioned | "Dependency manifest snapshot (2026-06-24): ... fastcsv 1.8.3 ..." | SUP-1204: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending." |

## Confirmed Inconsistencies

### 1. MIG-2207 applied to prod but absent from the migration index

- `migration-index.md` states the policy: "All production schema changes MUST be registered here before deploy," and its table lists only `MIG-2199`, `MIG-2183`, `MIG-2160`.
- `ops-log.md` records: "2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."
- **Inconsistency**: `MIG-2207` was applied to production per the ops log but has no row in the migration index, violating the index's own stated registration requirement. No explanation for the omission appears in any document.

### 2. Release notes claim "zero downtime" for 2.32.0; ops log records a 6-minute outage

- `release-notes.md`: "## 2.32.0 / Bulk export API is now generally available. This release was deployed with zero downtime."
- `ops-log.md`: "2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."
- **Inconsistency**: Direct contradiction between "zero downtime" and a documented 6-minute full outage (`OUT-88`) during the same release's deploy window.

### 3. Release notes claim 2.32.1 is "current GA"; ops log records it was rolled back

- `release-notes.md`: "## 2.32.1 (current GA) / Export pagination hotfix. Recommended for all tenants."
- `ops-log.md`: "2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
- **Inconsistency**: `release-notes.md` presents `2.32.1` as the current, recommended GA release, while `ops-log.md` records that `2.32.1` was rolled back fleet-wide and production is pinned to `2.32.0`. The ops log itself notes the incident writeup (`OUT-91`) is "draft, not yet published" — this explains *why release-notes.md was not updated*, but it does not resolve the contradiction between what the two documents currently assert; the rollback is recorded as having already executed on 2026-06-25.

### 4. fastcsv version shipped does not meet the version required to remediate the CVE the changelog claims is fixed

- `changelog.md` (2.31.2): "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
- `migration-index.md`: "Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4."
- `support-tickets.md` (SUP-1188): "Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."
- **Inconsistency**: The changelog asserts the CVE is fixed as of 2.31.2, but the dependency snapshot shows the bundled `fastcsv` at `1.8.3`, below the `>= 1.9.0` that support/security engineering states is required for remediation. This is an open, unresolved discrepancy, not an explained one — the ticket flags it as something to "verify," not something already confirmed consistent.

### 5. Release notes state fastcsv license is MIT; support ticket records it relicensed to BUSL-1.1 for the shipped version

- `release-notes.md`: "Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
- `support-tickets.md` (SUP-1204): "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."
- **Inconsistency**: The bundled version per `migration-index.md` is `fastcsv 1.8.3`, which is `>= 1.8.0` and therefore, per SUP-1204, is affected by the MIT→BUSL-1.1 relicense. `release-notes.md` nonetheless states the license is "MIT, unchanged since 2025." SUP-1204 itself flags this as unresolved ("NOTICE file update pending"), confirming the release-notes claim is currently inaccurate rather than reconciled.

### 6. EXP-380 is described as two different features across documents

- `release-notes.md` (2.31.0): "Includes the new audit export (EXP-380 preview)."
- `changelog.md` (2.32.0): "Bulk export API GA (EXP-380)."
- **Inconsistency**: The same ticket identifier `EXP-380` is labeled "audit export" in `release-notes.md` and "Bulk export API" in `changelog.md`. No document reconciles these two descriptions of the same identifier (e.g., no note stating "audit export" was renamed to or is part of "Bulk export API").

## Rejected Candidates (explained in-document — not findings)

### A. Missing version block 2.30.x in the changelog

- **Apparent inconsistency**: `changelog.md` jumps from `2.29.6` to `2.31.0` with no `2.30.x` entries, which could look like missing/lost release records.
- **Rejected because**: `changelog.md` itself states: "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x." This explicitly explains the gap.

### B. 2.31.4 hotfix present in ops-log/support-tickets but absent from changelog

- **Apparent inconsistency**: `ops-log.md` records "2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)," but `changelog.md` has no `2.31.4` entry at all.
- **Rejected because**: `support-tickets.md` (SUP-1189) explicitly states: "Note: no changelog entry was published for 2.31.4." The absence is acknowledged and explained in-document, not an unexplained discrepancy.

## Evidence

All confirmed and rejected items above quote the exact fixture text from at least two of the five documents as evidence. Source files read in full:
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/changelog.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/release-notes.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/ops-log.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/migration-index.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/support-tickets.md`

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed, per task constraints.

## Assumptions

- "Explained inside the documents" was interpreted strictly: an explanation must appear as text within one of the five fixtures (e.g., an explicit note, RFC reference, or ticket annotation), not inferred from general plausibility (e.g., "preview features often become GA later" was not accepted as an implicit explanation for the EXP-380 label mismatch, since no document states that connection).
- `libxmlq 2.4`, listed in the `migration-index.md` dependency snapshot, is not cross-referenced by any other document and was therefore not treated as an inconsistency (unmeasured/unverifiable against other sources — not evidence of contradiction, simply undiscussed elsewhere).
- SUP-1197 (bulk export row limit question) was reviewed but excluded from findings — it is explicitly marked "Answered from documentation; no defect" and does not reference any other document's entries in a way that creates a cross-document contradiction.
- Where an ops-log entry cites its own incident report as unpublished (`OUT-91` draft), this was treated as explaining the *timing lag* in release-notes, not as retracting the underlying factual contradiction — since this task's validator requires explanations to reject a candidate, not merely to contextualize it, this item was kept as a confirmed finding rather than rejected.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-cx-ledger-claude_code_goal_command.md`, contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), and additionally contains a reconciliation table, a confirmed-inconsistencies section with quoted evidence from both sides for each of the 6 findings, and a rejected-candidates section citing the in-document explanation for each of the 2 rejected items. No commits, pushes, package installs, or network access were performed. Task complete.
