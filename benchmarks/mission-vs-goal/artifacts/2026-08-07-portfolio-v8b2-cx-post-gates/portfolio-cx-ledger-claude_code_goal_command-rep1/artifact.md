# portfolio-cx-ledger — claude_code_goal_command — rep1

## Goal

Reconcile the release ledger across five fixture documents (`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md` under `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`), identify every cross-document inconsistency with quoted evidence from both sides, and reject apparent inconsistencies that are explained inside the documents. Produce this single artifact with the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a reconciliation table, a confirmed-inconsistencies section, and a rejected-candidates section.

## Result

All five fixtures were read in full. **6 confirmed cross-document inconsistencies** and **4 rejected candidates** were identified. Details and quotes are in the Evidence section below.

Confirmed (summary):

1. Release notes present 2.32.1 as "current GA" while the ops log records it was rolled back fleet-wide.
2. Release notes claim 2.32.0 was deployed with "zero downtime" while the ops log records a 6-minute full outage (OUT-88) during that deploy.
3. Migration MIG-2207 was applied to prod per the ops log but is absent from the migration index, which declares itself the authoritative pre-deploy registry.
4. Hotfix 2.31.4 was deployed (ops log, support ticket SUP-1189) but has no entry in the changelog or the customer release notes.
5. Release notes state the bundled fastcsv license is "MIT, unchanged since 2025" while support ticket SUP-1204 records upstream relicensed to BUSL-1.1 as of fastcsv 1.8.0 and the bundled version (1.8.3 per migration-index manifest) is affected.
6. The changelog claims CVE-2026-4417 was fixed in 2.31.2 via a fastcsv upgrade, but SUP-1188 states remediation requires fastcsv >= 1.9.0 and the manifest snapshot pins fastcsv 1.8.3 (< 1.9.0).

## Evidence

### Reconciliation table

| # | Item | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md | Status |
|---|---|---|---|---|---|---|---|
| 1 | 2.32.1 status | listed 2026-06-24 | "current GA" | rolled back 2026-06-25, fleet pinned to 2.32.0 | — | — | **Confirmed inconsistency (C1)** |
| 2 | 2.32.0 deploy quality | listed 2026-06-17 | "zero downtime" | OUT-88: 6-minute full outage | — | — | **Confirmed inconsistency (C2)** |
| 3 | MIG-2207 | — | — | applied to prod 2026-06-17 | **absent** (authoritative list) | — | **Confirmed inconsistency (C3)** |
| 4 | Version 2.31.4 | **absent** | **absent** | hotfix deploy 2026-06-02 (EU shard) | — | SUP-1189: shipped 2026-06-02; notes no changelog entry | **Confirmed inconsistency (C4)** |
| 5 | fastcsv license | — | "MIT, unchanged since 2025" | — | fastcsv 1.8.3 in manifest | SUP-1204: BUSL-1.1 as of 1.8.0; bundled version affected | **Confirmed inconsistency (C5)** |
| 6 | CVE-2026-4417 remediation | "fixed ... (upgrade fastcsv)" in 2.31.2 | "Security maintenance release" | — | manifest pins fastcsv 1.8.3 | SUP-1188: requires fastcsv >= 1.9.0 | **Confirmed inconsistency (C6)** |
| 7 | Missing 2.30.x versions | gap 2.29.6 → 2.31.0, explained in-document (RFC-77) | — | — | — | — | **Rejected (R1)** |
| 8 | MIG-2199 / MIG-2183 registration timing | — | — | applied 2026-05-28 / 2026-05-14 | registered 2026-05-27 / 2026-05-13 | — | **Rejected (R2)** |
| 9 | EXP-380 in both 2.31.0 and 2.32.0 | GA in 2.32.0 | preview in 2.31.0 | — | — | — | **Rejected (R3)** |
| 10 | SUP-1197 row-limit ticket | — | — | — | — | "no defect" | **Rejected (R4)** |

### Confirmed inconsistencies

**C1 — 2.32.1 rolled back but still published as "current GA".**
- release-notes.md: "## 2.32.1 (current GA) / Export pagination hotfix. Recommended for all tenants."
- ops-log.md (2026-06-25): "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
- The ops log itself flags that the incident report is unpublished ("OUT-91 draft, not yet published"), so no document explains away the customer-facing "current GA" claim.

**C2 — 2.32.0 "zero downtime" claim contradicted by outage OUT-88.**
- release-notes.md: "## 2.32.0 / Bulk export API is now generally available. This release was deployed with zero downtime."
- ops-log.md (2026-06-17): "Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."

**C3 — MIG-2207 applied to prod but not registered in the authoritative migration index.**
- ops-log.md (2026-06-17): "Migration MIG-2207 applied to prod (bulk export tables)."
- migration-index.md: header states "authoritative list of applied schema migrations" and "All production schema changes MUST be registered here before deploy." Its table contains only "MIG-2199", "MIG-2183", "MIG-2160" — MIG-2207 is missing.

**C4 — Version 2.31.4 deployed but absent from changelog and customer release notes.**
- ops-log.md (2026-06-02): "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."
- support-tickets.md (SUP-1189): "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02."
- changelog.md contains no 2.31.4 entry (versions listed: 2.32.1, 2.32.0, 2.31.2, 2.31.0, 2.29.6); release-notes.md likewise has none (2.32.1, 2.32.0, 2.31.2, 2.31.0).
- Note: SUP-1189 itself observes "Note: no changelog entry was published for 2.31.4." This acknowledges the omission but does not explain or justify it, so the cross-document gap stands as a confirmed inconsistency rather than a rejected candidate.

**C5 — fastcsv license: NOTICE claim (MIT) contradicted by relicensing record.**
- release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
- support-tickets.md (SUP-1204): "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."
- migration-index.md manifest snapshot (2026-06-24): "fastcsv 1.8.3" — i.e. ≥ 1.8.0, so the bundled version is in the BUSL-1.1 range.

**C6 — CVE-2026-4417 remediation claim contradicted by the shipped fastcsv pin.**
- changelog.md (2.31.2): "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
- support-tickets.md (SUP-1188): "remediation requires fastcsv >= 1.9.0; verify the shipped pin."
- migration-index.md manifest snapshot (2026-06-24): "fastcsv 1.8.3" — below the required 1.9.0, so the "fixed" claim is contradicted by the recorded dependency pin.

### Rejected candidates

**R1 — Version gap 2.29.6 → 2.31.0 (no 2.30.x anywhere).**
Rejected: explained inside changelog.md — "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."

**R2 — Migration registration dates precede deploy dates (MIG-2199 registered 2026-05-27 vs deployed 2026-05-28; MIG-2183 registered 2026-05-13 vs deployed 2026-05-14).**
Rejected: this is the expected order per migration-index.md's own rule — "All production schema changes MUST be registered here before deploy." Registration one day before deploy is compliance, not contradiction.

**R3 — EXP-380 appears in both 2.31.0 (release notes) and 2.32.0 (changelog).**
Rejected: the documents themselves distinguish the two stages — release-notes.md 2.31.0 says "the new audit export (EXP-380 preview)" while changelog.md 2.32.0 says "Bulk export API GA (EXP-380)". A preview followed by GA is a consistent progression, not a contradiction. (Measurement note: changelog.md 2.31.0 lists only RET-201 and omits the EXP-380 preview; whether an "excerpt" changelog is required to list preview features is unmeasured, so this is not claimed as a confirmed inconsistency.)

**R4 — SUP-1197 (bulk export row limit question, 2026-06-19).**
Rejected: the ticket itself resolves it — "Answered from documentation; no defect." No cross-document entry contradicts it.

## Assumptions

- Only the five named fixture files were read; nothing else under `benchmarks/mission-vs-goal/` was opened, per the run rules.
- changelog.md and ops-log.md are labeled "excerpt"; findings of the form "absent from document X" assume the excerpt covers the relevant version range (both excerpts span 2026 Q2 and include neighboring versions, so the absences of 2.31.4 and MIG-2207 are treated as real omissions, not truncation artifacts).
- "Cross-document inconsistency" is interpreted as: an entry recorded in one document that is missing or contradicted in another, per the task prompt.
- No external verification (network, package registries, CVE databases) was performed; all claims rest solely on the fixture text. Whether fastcsv 1.8.3 actually remediates CVE-2026-4417 in reality is unmeasured — only the documents' internal contradiction is asserted.

## Stop Condition

This run stops when this artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-ledger-claude_code_goal_command-rep1.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, plus the validator-required reconciliation table, confirmed-inconsistencies section with quoted evidence from both sides, and rejected-candidates section citing in-document explanations. All conditions are now satisfied; no further edits, commits, or network operations are performed.
