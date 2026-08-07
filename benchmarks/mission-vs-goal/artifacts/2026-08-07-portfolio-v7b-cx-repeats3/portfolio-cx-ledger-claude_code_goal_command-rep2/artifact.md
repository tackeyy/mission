# Release Ledger Reconciliation — portfolio-cx-ledger (claude_code_goal_command / rep2)

## Goal

Reconcile the release ledger across five fixture documents
(`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`,
`support-tickets.md` under
`benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`),
identify every cross-document inconsistency with quoted evidence from both
sides, and reject apparent inconsistencies that are explained inside the
documents, citing the explanation. Deliver this artifact at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-ledger-claude_code_goal_command-rep2.md`
with headings Goal, Result, Evidence, Assumptions, and Stop Condition.

## Result

All five fixtures were read in full. The reconciliation produced
**6 confirmed cross-document inconsistencies** and
**3 rejected candidates** (apparent inconsistencies explained inside the
documents). Details with quoted evidence follow in the Evidence section.

### Reconciliation table

| # | Item | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md | Status |
|---|---|---|---|---|---|---|---|
| 1 | 2.32.0 deploy downtime | — | "deployed with zero downtime" | "Incident OUT-88: 6-minute full outage" | — | — | **Confirmed inconsistency (C1)** |
| 2 | MIG-2207 registration | — | — | "Migration MIG-2207 applied to prod" | Not listed (index requires registration before deploy) | — | **Confirmed inconsistency (C2)** |
| 3 | 2.32.1 current status | 2.32.1 listed as latest release | "2.32.1 (current GA)" | "2.32.1 rolled back on all production tenants … Fleet pinned to 2.32.0" | — | — | **Confirmed inconsistency (C3)** |
| 4 | Version 2.31.4 existence | No 2.31.4 entry | No 2.31.4 entry | "Hotfix deploy 2.31.4 to tenants on the EU shard" | — | SUP-1189: "Engineering shipped hotfix 2.31.4" and "no changelog entry was published for 2.31.4" | **Confirmed inconsistency (C4)** |
| 5 | fastcsv license | — | "MIT, unchanged since 2025" | — | bundled "fastcsv 1.8.3" | SUP-1204: "relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0" | **Confirmed inconsistency (C5)** |
| 6 | CVE-2026-4417 remediation | "fixed CVE-2026-4417 … (upgrade fastcsv)" in 2.31.2 | — | — | manifest shows "fastcsv 1.8.3" | SUP-1188: "remediation requires fastcsv >= 1.9.0" | **Confirmed inconsistency (C6)** |
| 7 | Missing 2.30.x versions | Gap between 2.29.6 and 2.31.0 | — | — | — | — | **Rejected (R1)** — explained in changelog |
| 8 | EXP-380 in both 2.31.0 and 2.32.0 | "Bulk export API GA (EXP-380)" in 2.32.0 | "EXP-380 preview" in 2.31.0 | — | — | — | **Rejected (R2)** — preview vs GA, consistent |
| 9 | Rollback incident absent from published docs | — | No OUT-91 mention | "OUT-91 draft, not yet published" | — | — | **Rejected (R3)** — absence explained in ops-log |

## Evidence

### Confirmed inconsistencies

**C1 — 2.32.0 "zero downtime" claim contradicted by a recorded outage.**
- release-notes.md (2.32.0): "This release was deployed with zero downtime."
- ops-log.md (2026-06-17): "Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."
- These directly contradict each other about the same deploy on the same date.

**C2 — Migration MIG-2207 applied to prod but not registered in the authoritative migration index.**
- ops-log.md (2026-06-17): "Migration MIG-2207 applied to prod (bulk export tables)."
- migration-index.md lists only "MIG-2199", "MIG-2183", "MIG-2160" — MIG-2207 is absent, despite the index's own rule: "All production schema changes MUST be registered here before deploy."
- An applied prod migration missing from the mandatory registry is a cross-document inconsistency.

**C3 — 2.32.1 published as "current GA" although it was rolled back fleet-wide.**
- release-notes.md: "## 2.32.1 (current GA) — Export pagination hotfix. Recommended for all tenants."
- ops-log.md (2026-06-25): "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
- The published release notes recommend a version that operations rolled back everywhere; the actual fleet version is 2.32.0.

**C4 — Version 2.31.4 was deployed but is missing from the changelog and release notes.**
- ops-log.md (2026-06-02): "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."
- support-tickets.md (SUP-1189): "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02."
- changelog.md has no 2.31.4 entry (it jumps from "2.32.0 — 2026-06-17" back to "2.31.2 — 2026-05-28"); release-notes.md likewise has no 2.31.4 section.
- SUP-1189 itself confirms the gap: "Note: no changelog entry was published for 2.31.4." This note documents the omission but does not explain or resolve it, so it remains a confirmed inconsistency rather than a rejected candidate.

**C5 — fastcsv license stated as "MIT, unchanged since 2025" but the bundled version is under BUSL-1.1.**
- release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
- support-tickets.md (SUP-1204): "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."
- migration-index.md manifest snapshot (2026-06-24) shows the bundled version is "fastcsv 1.8.3" — at or above 1.8.0, so the published MIT claim contradicts the ticket. The "NOTICE file update pending" line confirms the discrepancy is known and unresolved, not explained away.

**C6 — CVE-2026-4417 claimed fixed in 2.31.2, but the shipped fastcsv pin is below the version required for remediation.**
- changelog.md (2.31.2): "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
- support-tickets.md (SUP-1188): "Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."
- migration-index.md manifest snapshot (2026-06-24): "fastcsv 1.8.3" — below 1.9.0. The changelog's "fixed" claim is contradicted by the security requirement combined with the recorded shipped pin.

### Rejected candidates (apparent inconsistencies explained inside the documents)

**R1 — Version gap: no 2.30.x releases between 2.29.6 and 2.31.0.**
- Apparent issue: changelog.md jumps from "2.29.6 — 2026-04-30" to "2.31.0 — 2026-05-14" with no 2.30.x.
- In-document explanation (changelog.md): "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."
- Rejected: the gap is explicitly explained.

**R2 — EXP-380 appears in both 2.31.0 (release notes) and 2.32.0 (changelog).**
- Apparent issue: release-notes.md 2.31.0 says "Includes the new audit export (EXP-380 preview)" while changelog.md 2.32.0 says "Bulk export API GA (EXP-380)."
- Explanation from the documents' own wording: 2.31.0 shipped EXP-380 as a "preview" and 2.32.0 made it "GA" ("generally available" per release-notes.md 2.32.0). A preview followed by GA is a consistent lifecycle, not a contradiction.
- Rejected: the two mentions describe different stages of the same feature.

**R3 — The 2.32.1 rollback incident (OUT-91) is absent from customer-facing documents.**
- Apparent issue: a fleet-wide rollback appears only in ops-log.md and nowhere in release-notes.md or changelog.md.
- In-document explanation (ops-log.md, 2026-06-25): "see OUT-91 draft, not yet published" — the incident writeup is explicitly still a draft, which explains why no published document mentions OUT-91 itself.
- Rejected as a standalone finding for the incident's absence. Note: the contradiction between the rollback and the "current GA" label is still confirmed separately as C3 — the unpublished draft explains OUT-91's absence, not the stale GA claim.

## Assumptions

- The five named fixture files are the complete and authoritative inputs; no other files under `benchmarks/mission-vs-goal/` were opened, per the run rules.
- The migration-index.md dependency manifest snapshot (2026-06-24) is taken as the record of the shipped fastcsv pin (1.8.3) at that date. Whether a later pin change occurred is unmeasured — no fixture records one.
- "fastcsv 1.8.3 ≥ 1.8.0" (C5) and "fastcsv 1.8.3 < 1.9.0" (C6) are plain version-number comparisons.
- Whether CVE-2026-4417 is actually exploitable on 1.8.3 is unmeasured; C6 rests solely on the documents' own statements (fix claimed in changelog vs. security engineering's ">= 1.9.0" requirement vs. the recorded 1.8.3 pin).
- Runtime, token usage, and any comparison to other benchmark arms are unmeasured; this artifact makes no benchmark-superiority claim.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-ledger-claude_code_goal_command-rep2.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), a reconciliation table, a confirmed-inconsistencies section with
quoted evidence from both sides of each finding, and a rejected-candidates
section citing the in-document explanations. All confirmed findings quote
exact identifiers (OUT-88, MIG-2207, OUT-91, 2.31.4, SUP-1189, SUP-1204,
SUP-1188, CVE-2026-4417, fastcsv 1.8.3). The goal condition is met; no
further edits are required.
