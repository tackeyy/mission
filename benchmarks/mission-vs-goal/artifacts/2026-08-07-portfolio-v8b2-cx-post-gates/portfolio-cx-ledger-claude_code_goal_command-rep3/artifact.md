# portfolio-cx-ledger — claude_code_goal_command — rep3

## Goal

Reconcile the release ledger across five fixture documents and produce a single task artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-ledger-claude_code_goal_command-rep3.md` containing a reconciliation table, a confirmed-inconsistencies section with quoted evidence from both sides, and a rejected-candidates section citing in-document explanations. Headings Goal / Result / Evidence / Assumptions / Stop Condition must be present.

## Result

All five fixtures were read in full (`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`). The reconciliation found **6 confirmed cross-document inconsistencies** and **4 rejected candidates** (apparent inconsistencies explained or resolved inside the documents). Details with quoted evidence follow in the Evidence section.

## Evidence

### Reconciliation table

| Item | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md | Status |
|---|---|---|---|---|---|---|
| 2.32.1 | `2.32.1 — 2026-06-24` (EXP-441 fix) | `2.32.1 (current GA) ... Recommended for all tenants` | `Rollback executed: 2.32.1 rolled back on all production tenants` (2026-06-25) | — | — | **Confirmed inconsistency (C1)** |
| 2.32.0 deploy quality | listed | `deployed with zero downtime` | `Incident OUT-88: 6-minute full outage` | — | — | **Confirmed inconsistency (C2)** |
| MIG-2207 | — | — | `Migration MIG-2207 applied to prod` (2026-06-17) | absent (list ends at MIG-2199) | — | **Confirmed inconsistency (C3)** |
| 2.31.4 hotfix | absent | absent | `Hotfix deploy 2.31.4 to tenants on the EU shard only` (2026-06-02) | — | SUP-1189: hotfix shipped; `no changelog entry was published for 2.31.4` | **Confirmed inconsistency (C4)** |
| fastcsv license | — | `MIT, unchanged since 2025` | — | `fastcsv 1.8.3` (snapshot 2026-06-24) | SUP-1204: `relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0` | **Confirmed inconsistency (C5)** |
| CVE-2026-4417 remediation | 2.31.2: `fixed CVE-2026-4417 ... (upgrade fastcsv)` | 2.31.2: `Security maintenance release` | — | `fastcsv 1.8.3` (snapshot 2026-06-24) | SUP-1188: `remediation requires fastcsv >= 1.9.0` | **Confirmed inconsistency (C6)** |
| 2.30.x version gap | gap 2.29.6 → 2.31.0, note: never released (RFC-77) | — | — | — | — | **Rejected (R1)** — explained in-document |
| MIG-2199 / 2.31.2 | 2.31.2 dated 2026-05-28 | 2.31.2 listed | `Deploy 2.31.2. Migration MIG-2199 applied` (2026-05-28) | `MIG-2199 / 2.31.2 / Registered 2026-05-27` | — | Consistent (R2) |
| MIG-2183 / 2.31.0 | 2.31.0 dated 2026-05-14 | 2.31.0 listed | `Deploy 2.31.0. Migration MIG-2183 applied` (2026-05-14) | `MIG-2183 / 2.31.0 / Registered 2026-05-13` | — | Consistent (R2) |
| fastjson version | 2.32.0: `fastjson 3.2 -> 3.3` | — | — | `fastjson 3.3` | — | Consistent (R3) |
| EXP-380 preview → GA | 2.32.0: `Bulk export API GA (EXP-380)` | 2.31.0: `EXP-380 preview` | — | — | SUP-1197: `no defect` | **Rejected (R4)** — preview→GA progression, not a contradiction |

### Confirmed inconsistencies

**C1 — 2.32.1 rollback vs. published release notes claiming it is current GA.**
- ops-log.md (2026-06-25): "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
- release-notes.md: "## 2.32.1 (current GA) — Export pagination hotfix. Recommended for all tenants."
- The fleet is actually pinned to 2.32.0, but customer-facing notes still recommend 2.32.1 as current GA. The ops-log note that OUT-91 is "not yet published" explains *why* the notes are stale, but does not resolve the contradiction between the two documents' stated current state — so this remains a confirmed inconsistency.

**C2 — 2.32.0 "zero downtime" vs. a recorded 6-minute full outage.**
- release-notes.md (2.32.0): "This release was deployed with zero downtime."
- ops-log.md (2026-06-17): "Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."

**C3 — Migration MIG-2207 applied to prod but not registered in the authoritative migration index.**
- ops-log.md (2026-06-17): "Migration MIG-2207 applied to prod (bulk export tables)."
- migration-index.md: header states "All production schema changes MUST be registered here before deploy," yet its table contains only "MIG-2199", "MIG-2183", "MIG-2160" — MIG-2207 is absent.

**C4 — Hotfix 2.31.4 deployed but absent from the changelog (and release notes).**
- ops-log.md (2026-06-02): "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."
- support-tickets.md (SUP-1189): "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02."
- changelog.md: contains no 2.31.4 entry (versions jump 2.31.2 → 2.32.0); release-notes.md likewise has no 2.31.4 entry.
- Note: SUP-1189 itself states "no changelog entry was published for 2.31.4." This is an acknowledgment that the gap exists — it confirms rather than explains away the inconsistency, so it stays confirmed.

**C5 — fastcsv license: NOTICE claims MIT unchanged, but the bundled version is under BUSL-1.1.**
- release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
- support-tickets.md (SUP-1204): "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected."
- migration-index.md manifest snapshot (2026-06-24): "fastcsv 1.8.3" — which is ≥ 1.8.0, corroborating that the bundled version falls under the relicense. SUP-1204's "NOTICE file update pending" confirms the discrepancy is real and unresolved.

**C6 — CVE-2026-4417 claimed fixed in 2.31.2, but the shipped fastcsv pin does not meet the remediation requirement.**
- changelog.md (2.31.2): "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
- support-tickets.md (SUP-1188): "remediation requires fastcsv >= 1.9.0; verify the shipped pin."
- migration-index.md manifest snapshot (2026-06-24): "fastcsv 1.8.3" — below 1.9.0. The changelog's "fixed" claim is contradicted by the recorded dependency pin. (Whether the CVE is actually exploitable in the shipped build is unmeasured; the documented contradiction between "fixed" and the pin requirement is the finding.)

### Rejected candidates

**R1 — Missing 2.30.x releases in the changelog.** The version sequence jumps 2.29.6 → 2.31.0, which looks like missing entries. Rejected because the changelog explains it in-document: "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."

**R2 — Migration registration dates precede deploy dates (MIG-2199, MIG-2183).** MIG-2199 registered 2026-05-27 vs. deployed 2026-05-28; MIG-2183 registered 2026-05-13 vs. deployed 2026-05-14. Not an inconsistency: migration-index.md requires registration "before deploy," and both registrations precede their deploys by one day. Consistent as documented.

**R3 — fastjson version.** changelog.md 2.32.0 records "fastjson 3.2 -> 3.3" and the migration-index.md manifest snapshot records "fastjson 3.3". The documents agree; no finding.

**R4 — EXP-380 appearing in both 2.31.0 and 2.32.0.** release-notes.md 2.31.0 says "Includes the new audit export (EXP-380 preview)" while changelog.md 2.32.0 says "Bulk export API GA (EXP-380)". Rejected as a contradiction: the release notes explicitly label the 2.31.0 inclusion as "preview" and the changelog labels 2.32.0 as "GA" — a preview-to-GA progression documented consistently on both sides. (Relatedly, SUP-1197 is explicitly closed in-document as "Answered from documentation; no defect.")

## Assumptions

- The five named fixture files are the complete evidence universe; nothing outside them was read (benchmark metadata, task definitions, and answer keys were not opened, per the run rules).
- migration-index.md's self-description ("authoritative list of applied schema migrations") is taken at face value when judging C3.
- The dependency manifest snapshot in migration-index.md (dated 2026-06-24) is treated as the record of the shipped pins for C5/C6. Actual runtime behavior (e.g., real CVE exploitability, actual license posture after legal review) is unmeasured; findings are limited to documented contradictions.
- "Registered one day before deploy" (R2) is interpreted as satisfying the index's "before deploy" requirement.

## Stop Condition

This artifact exists at the required path and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a reconciliation table, a confirmed-inconsistencies section quoting both sides of each finding, and a rejected-candidates section citing the in-document explanations. No commits, pushes, package installs, or network access were performed; edits were limited to this single output file. The goal condition is met and the run stops here.
