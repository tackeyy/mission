# Portfolio CX Ledger — Release Ledger Reconciliation

## Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-cx-ledger-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: reconcile the release ledger across five documents (`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`), find every cross-document inconsistency with quoted identifiers from both sides, and separate confirmed inconsistencies from candidates that are explained inside the documents (rejected as non-findings).

## Result

Read all five fixture files exactly as named in the prompt. Identified **6 confirmed cross-document inconsistencies** and **2 rejected candidates** (explained in-document). Reconciliation table, confirmed findings, and rejected candidates are below.

### Reconciliation Table

| Version / Event | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md |
|---|---|---|---|---|---|
| 2.32.1 | `Fix export pagination off-by-one (EXP-441)` | `2.32.1 (current GA) ... Recommended for all tenants` | `2026-06-25 Rollback executed: 2.32.1 rolled back on all production tenants ... Fleet pinned to 2.32.0` | — | — |
| 2.32.0 | `Bulk export API GA (EXP-380)`; `Dependency upgrades: fastjson 3.2 -> 3.3` | `This release was deployed with zero downtime.` | `2026-06-17 Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)`; `Migration MIG-2207 applied to prod` | fastjson 3.3 present in 2026-06-24 snapshot; **no MIG-2207 row** | — |
| 2.31.4 (hotfix) | *(no entry)* | *(no entry)* | `2026-06-02 Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)` | *(no entry)* | `SUP-1189`: `Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. ... Note: no changelog entry was published for 2.31.4.` |
| 2.31.2 | `Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)` | `Security maintenance release. Customers on 2.31.x should upgrade.` | `2026-05-28 Deploy 2.31.2. Migration MIG-2199 applied` | `MIG-2199 \| 2.31.2 \| 2026-05-27`; dependency snapshot `fastcsv 1.8.3` | `SUP-1188`: `remediation requires fastcsv >= 1.9.0; verify the shipped pin`; `SUP-1204`: `Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected.` |
| 2.31.0 | `New retention settings page (RET-201)` (no EXP-380 mention) | `Retention settings page. Includes the new audit export (EXP-380 preview).` | `2026-05-14 Deploy 2.31.0. Migration MIG-2183 applied` | `MIG-2183 \| 2.31.0 \| 2026-05-13` | — |
| 2.30.x | `2.30.x was never released. ... RFC-77 ... train jumped from 2.29.x directly to 2.31.x.` | *(no entry)* | *(no entry)* | *(no entry)* | *(no entry)* |
| 2.29.6 | `Minor bug fixes` | *(no entry)* | *(no entry)* | `MIG-2160 \| 2.29.6 \| 2026-04-29` | — |
| fastcsv license | *(no mention of license)* | `bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)` | — | — | `SUP-1204`: relicensed MIT → BUSL-1.1 as of 1.8.0; bundled version affected; NOTICE update pending |

### Confirmed Inconsistencies

1. **2.32.0 downtime claim vs. recorded incident.**
   - `release-notes.md`: "This release was deployed with zero downtime."
   - `ops-log.md`: "2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."
   - No document reconciles these two statements; the release notes claim is contradicted by the logged incident for the same version and same date.

2. **2.32.1 "current GA / recommended" claim vs. fleet-wide rollback.**
   - `release-notes.md`: "2.32.1 (current GA) ... Recommended for all tenants."
   - `ops-log.md`: "2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
   - The ops log notes the rollback incident write-up (`OUT-91`) is "not yet published," which explains why release notes have not been *updated*, but it does not resolve the substantive contradiction: release-notes.md actively asserts 2.32.1 is the current, recommended GA version while ops-log.md shows it was pulled from all production tenants and the fleet reverted to 2.32.0.

3. **MIG-2207 applied but absent from the authoritative migration index.**
   - `ops-log.md`: "2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."
   - `migration-index.md` header: "All production schema changes MUST be registered here before deploy," yet its table only lists `MIG-2199`, `MIG-2183`, and `MIG-2160` — `MIG-2207` does not appear.
   - No explanation for the omission appears in any document; this is a registry-completeness violation against the migration index's own stated rule.

4. **fastcsv version insufficient for claimed CVE remediation.**
   - `changelog.md` 2.31.2: "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
   - `support-tickets.md` `SUP-1188`: "remediation requires fastcsv >= 1.9.0; verify the shipped pin."
   - `migration-index.md` dependency snapshot (2026-06-24): "fastcsv 1.8.3" — below the 1.9.0 threshold `SUP-1188` says is required for full remediation.
   - No document shows a later bump of fastcsv to >= 1.9.0 after the 2026-06-24 snapshot, so the changelog's "fixed" claim is contradicted by the shipped pin recorded in the migration index.

5. **fastcsv license claim contradicts support escalation.**
   - `release-notes.md`: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
   - `support-tickets.md` `SUP-1204`: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."
   - `migration-index.md` confirms the bundled version is `fastcsv 1.8.3` (≥ 1.8.0, i.e., within the affected range per `SUP-1204`). The release notes' "MIT, unchanged since 2025" claim directly contradicts the support ticket's statement that the bundled version is affected by the relicense and that the NOTICE file update is still pending (not yet done).

6. **EXP-380 attributed to 2.31.0 in release notes but not recorded until 2.32.0 in the changelog.**
   - `release-notes.md` 2.31.0: "Retention settings page. Includes the new audit export (EXP-380 preview)."
   - `changelog.md` 2.31.0 entry only lists: "New retention settings page (RET-201)" — no `EXP-380` mention.
   - `changelog.md` 2.32.0 entry: "Bulk export API GA (EXP-380)" — this is the only changelog entry that references `EXP-380`, and it is recorded as the 2.32.0 GA milestone, not a 2.31.0 preview.
   - No document explains why release-notes.md attributes an `EXP-380` preview to the 2.31.0 release when the changelog's only `EXP-380` record is against 2.32.0.

### Rejected Candidates (Explained In-Document)

1. **Candidate:** No changelog entry exists for version `2.31.4`, even though `ops-log.md` records a hotfix deploy ("2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)").
   - **Rejected because:** `support-tickets.md` `SUP-1189` explicitly states the reason: "Note: no changelog entry was published for 2.31.4." This is an acknowledged, explained gap rather than an unexplained inconsistency.

2. **Candidate:** No document contains a `2.30.x` version entry, which looks like a gap in the version sequence (changelog jumps from `2.29.6` to `2.31.0`).
   - **Rejected because:** `changelog.md` explicitly explains this: "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x." This is a documented renumbering decision, not a missing/contradicted entry.

## Evidence

All evidence is quoted verbatim from the three fixture files below (only files named in the task prompt were read):

- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/changelog.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/release-notes.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/ops-log.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/migration-index.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/support-tickets.md`

Exact quotes supporting each confirmed finding and each rejected candidate are inlined above, next to each claim, rather than collected separately, so that each piece of evidence stays attached to the specific inconsistency it supports. No claim in this artifact relies on unquoted paraphrase of a document's content — version numbers, incident IDs (`OUT-88`, `OUT-91`), migration IDs (`MIG-2207`, `MIG-2199`, `MIG-2183`, `MIG-2160`), ticket IDs (`SUP-1188`, `SUP-1189`, `SUP-1204`, `SUP-1197`), and feature IDs (`EXP-380`, `EXP-441`, `RET-201`) are quoted directly from the source documents where they appear.

**Unmeasured / out of scope:** Whether `MIG-2207`'s absence from `migration-index.md` was a documentation oversight versus a policy violation with downstream consequences is unmeasured — no fixture document states the cause, only the fact of the omission. Whether fastcsv was subsequently upgraded past 1.9.0 after the 2026-06-24 snapshot is unmeasured — no later snapshot exists in the fixtures provided.

## Assumptions

- The five fixture files as given are the complete and final versions for this reconciliation; no other document versions or later updates exist beyond what was provided.
- "Contradicted in another" is interpreted to include cases where one document's factual claim (e.g., "zero downtime," "current GA," "MIT, unchanged") is directly negated by another document's more specific operational record (incident log, ticket, migration table), even when the contradiction is partly attributable to one document lagging in updates (e.g., the 2.32.1/OUT-91 case, where the draft-not-yet-published status explains the *lag* but not the underlying factual conflict).
- Version-to-file mapping (e.g., matching `migration-index.md`'s "2026-06-24" dependency snapshot against `changelog.md`'s 2.32.1 released the same date) is treated as intentionally contemporaneous, since no fixture states otherwise.
- No file outside the five named fixtures and this task's own output file was opened, read, grepped, or listed, per the task's scope restriction.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), includes a reconciliation table, a confirmed-inconsistencies section with quoted evidence from both sides of each inconsistency, and a rejected-candidates section citing the in-document explanation for each rejected candidate. No commit, push, package install, or network access was performed. No file outside the five named fixtures and this output file was opened.
