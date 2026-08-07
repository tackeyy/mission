# portfolio-cx-ledger — claude_code_goal_command — rep1

## Goal

Reconcile the release ledger across five fixture documents (`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md` under `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`), find every cross-document inconsistency with quoted evidence from both sides, and reject apparent inconsistencies that are explained inside the documents, citing the explanation. Produce this single artifact with headings Goal, Result, Evidence, Assumptions, and Stop Condition.

## Result

All five named fixtures were read in full. The reconciliation identified **6 confirmed cross-document inconsistencies** and **3 rejected candidates** (apparent inconsistencies explained within the documents). Details, quotes, and the reconciliation table are in the Evidence section.

Summary of confirmed inconsistencies:

1. Release notes claim 2.32.0 deployed with "zero downtime"; ops log records a 6-minute full outage (OUT-88) during that deploy.
2. Migration MIG-2207 was applied to prod per the ops log but is absent from the migration index, which declares itself the authoritative list requiring registration before deploy.
3. Release notes list 2.32.1 as "current GA" and "Recommended for all tenants"; the ops log records 2.32.1 rolled back on all production tenants with the fleet pinned to 2.32.0.
4. Release notes state the bundled fastcsv license is "MIT, unchanged since 2025"; support ticket SUP-1204 records that fastcsv relicensed to BUSL-1.1 as of 1.8.0 and the bundled version (1.8.3 per the migration index manifest) is affected.
5. Changelog 2.31.2 claims CVE-2026-4417 was fixed via a fastcsv upgrade; support ticket SUP-1188 records that remediation requires fastcsv >= 1.9.0, while the migration index manifest snapshot shows fastcsv 1.8.3 shipped.
6. Hotfix 2.31.4 was deployed per the ops log and support ticket SUP-1189, but has no entry in the changelog or the customer release notes.

## Evidence

### Reconciliation table

| Item | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md | Status |
|---|---|---|---|---|---|---|
| 2.32.1 release | `2.32.1 — 2026-06-24` (EXP-441 fix) | `2.32.1 (current GA)` | `Rollback executed: 2.32.1 rolled back on all production tenants` (2026-06-25) | — | — | **Confirmed inconsistency (C3)** |
| 2.32.0 deploy quality | `2.32.0 — 2026-06-17` | `deployed with zero downtime` | `Incident OUT-88: 6-minute full outage` | — | — | **Confirmed inconsistency (C1)** |
| MIG-2207 | — | — | `Migration MIG-2207 applied to prod` (2026-06-17) | Not listed (only MIG-2199, MIG-2183, MIG-2160) | — | **Confirmed inconsistency (C2)** |
| fastcsv license | — | `MIT, unchanged since 2025` | — | `fastcsv 1.8.3` in manifest | SUP-1204: `relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0` | **Confirmed inconsistency (C4)** |
| CVE-2026-4417 remediation | `fixed CVE-2026-4417 ... (upgrade fastcsv)` | `Security maintenance release` (2.31.2) | — | `fastcsv 1.8.3` in manifest | SUP-1188: `remediation requires fastcsv >= 1.9.0` | **Confirmed inconsistency (C5)** |
| 2.31.4 hotfix | No entry | No entry | `Hotfix deploy 2.31.4` (2026-06-02) | — | SUP-1189: `Engineering shipped hotfix 2.31.4` | **Confirmed inconsistency (C6)** |
| 2.30.x version gap | `2.29.6` → `2.31.0`, note re RFC-77 | — | — | — | — | **Rejected (R1)** — explained in changelog |
| EXP-380 in 2.31.0 and 2.32.0 | `Bulk export API GA (EXP-380)` (2.32.0) | `EXP-380 preview` (2.31.0) | — | — | — | **Rejected (R2)** — preview vs GA, no contradiction |
| 2.29.6 deploy / MIG-2160 not in ops log | `2.29.6 — 2026-04-30` | — | Excerpt starts 2026-05-14 | `MIG-2160 / 2.29.6 / 2026-04-29` | — | **Rejected (R3)** — ops log is a labeled excerpt |
| MIG-2199 for 2.31.2 | `2.31.2 — 2026-05-28` | `2.31.2` listed | `Deploy 2.31.2. Migration MIG-2199 applied.` | `MIG-2199 / 2.31.2 / 2026-05-27` (registered before deploy) | — | Consistent |
| MIG-2183 for 2.31.0 | `2.31.0 — 2026-05-14` | `2.31.0` listed | `Deploy 2.31.0. Migration MIG-2183 applied.` | `MIG-2183 / 2.31.0 / 2026-05-13` (registered before deploy) | — | Consistent |
| EXP-441 fix in 2.32.1 | `Fix export pagination off-by-one (EXP-441)` | `Export pagination hotfix.` | — | — | — | Consistent |

### Confirmed inconsistencies

**C1. 2.32.0 "zero downtime" vs. 6-minute full outage (OUT-88)**
- release-notes.md (2.32.0): "This release was deployed with zero downtime."
- ops-log.md (2026-06-17): "Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."
- Direct contradiction: a 6-minute full outage is not zero downtime.

**C2. MIG-2207 applied to prod but missing from the authoritative migration index**
- ops-log.md (2026-06-17): "Migration MIG-2207 applied to prod (bulk export tables)."
- migration-index.md: header states "Migration Index (authoritative list of applied schema migrations)" and "All production schema changes MUST be registered here before deploy," yet its table lists only "MIG-2199", "MIG-2183", "MIG-2160" — MIG-2207 is absent.
- Entry recorded in one document (ops log) but missing in the document that claims authority over it.

**C3. 2.32.1 published as "current GA" despite fleet-wide rollback**
- release-notes.md: "2.32.1 (current GA) — Export pagination hotfix. Recommended for all tenants."
- ops-log.md (2026-06-25): "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
- The customer-facing document recommends a version that operations rolled back everywhere. The ops log notes "OUT-91 draft, not yet published," which explains why no incident page exists, but it does not explain or resolve the release notes still recommending 2.32.1 — the contradiction stands.

**C4. fastcsv license: "MIT, unchanged since 2025" vs. BUSL-1.1 relicensing**
- release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
- support-tickets.md (SUP-1204, 2026-06-21): "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."
- migration-index.md manifest snapshot (2026-06-24) confirms the bundled version: "fastcsv 1.8.3" — which is >= 1.8.0, i.e. affected by the relicensing. "NOTICE file update pending" confirms the release-notes claim is currently wrong, it does not excuse it.

**C5. CVE-2026-4417 claimed fixed, but shipped fastcsv pin is below the required version**
- changelog.md (2.31.2): "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
- support-tickets.md (SUP-1188, 2026-05-30): "Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."
- migration-index.md (manifest snapshot 2026-06-24): "fastcsv 1.8.3" — below 1.9.0. The changelog's "fixed" claim is contradicted by the combination of the security-engineering requirement and the shipped pin.

**C6. Hotfix 2.31.4 deployed but absent from changelog and release notes**
- ops-log.md (2026-06-02): "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."
- support-tickets.md (SUP-1189): "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. ... Note: no changelog entry was published for 2.31.4."
- changelog.md and release-notes.md contain no 2.31.4 entry (changelog jumps from "2.32.0 — 2026-06-17" back to "2.31.2 — 2026-05-28"; release notes list only 2.32.1, 2.32.0, 2.31.2, 2.31.0). The support-ticket note acknowledges the gap exists but provides no explanation or justification for it, so this remains a confirmed missing-entry inconsistency rather than a rejected candidate.

### Rejected candidates (explained inside the documents)

**R1. Missing 2.30.x versions (changelog jumps 2.29.6 → 2.31.0)**
- Apparent gap: changelog.md lists "2.31.0 — 2026-05-14" immediately after "2.29.6 — 2026-04-30" with no 2.30.x.
- In-document explanation (changelog.md): "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."
- Rejected: the gap is explicitly explained.

**R2. EXP-380 appearing in both 2.31.0 and 2.32.0**
- Apparent duplicate/contradiction: release-notes.md (2.31.0): "Includes the new audit export (EXP-380 preview)"; changelog.md (2.32.0): "Bulk export API GA (EXP-380)".
- In-document explanation: the 2.31.0 mention is explicitly labeled "(EXP-380 preview)" while 2.32.0 marks it "GA" — a preview followed by general availability is a normal progression, not a contradiction. (The absence of the preview line from changelog 2.31.0 — which lists only "New retention settings page (RET-201)" — is noted, but the changelog is titled "CHANGELOG (excerpt, 2026 Q2)" and the preview/GA labeling resolves the substantive conflict.)
- Rejected: labels within the documents reconcile the two entries.

**R3. 2.29.6 deploy and MIG-2160 application absent from the ops log**
- Apparent gap: migration-index.md lists "MIG-2160 | 2.29.6 | 2026-04-29" and changelog.md lists "2.29.6 — 2026-04-30", but ops-log.md has no corresponding deploy/migration row (its earliest row is 2026-05-14).
- In-document explanation: ops-log.md is titled "Operations Log (excerpt, 2026 Q2)" — it is an explicitly partial excerpt, and its coverage simply starts after the 2.29.6 deploy date.
- Rejected: the "excerpt" labeling explains the absence.

## Assumptions

- The five fixture files are the complete evidence base; no other documents were consulted (benchmark metadata was not read, per the run rules).
- "fastcsv 1.8.3" in the migration-index manifest snapshot (2026-06-24) is taken as the shipped pin referenced by SUP-1188; the fixtures contain no later manifest, so the pin as of the snapshot date is used. Whether the pin changed after 2026-06-24 is unmeasured.
- The support-ticket note "no changelog entry was published for 2.31.4" (SUP-1189) is treated as an acknowledgment of the gap, not an explanation that would justify rejecting C6, because it states the fact of the omission without giving a reason.
- The ops-log note "(see OUT-91 draft, not yet published)" explains only the missing incident publication, not the release notes' continued "current GA / Recommended for all tenants" status for 2.32.1; C3 is therefore kept as confirmed.
- No quantitative benchmark comparison is made; run timing and token usage are unmeasured.

## Stop Condition

This run stops when this artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-ledger-claude_code_goal_command-rep1.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, with a reconciliation table, a confirmed-inconsistencies section quoting both sides of each inconsistency, and a rejected-candidates section citing the in-document explanations. All conditions are met as of this write; no further edits, commits, pushes, installs, or network access are performed.
