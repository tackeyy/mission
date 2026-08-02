# Portfolio CX Ledger — Release Ledger Reconciliation (claude_code_goal_command arm)

## Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v4/portfolio-cx-ledger-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: reconcile the release ledger across five documents (changelog, release notes, ops log, migration index, support tickets) for 2026 Q2. Find every cross-document inconsistency, quoting exact identifiers from both sides. Apparent inconsistencies that are explained inside the documents must be rejected as non-findings, with the in-document explanation cited.

Only the five named fixtures were read:
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/changelog.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/release-notes.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/ops-log.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/migration-index.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/support-tickets.md`

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed.

## Result

Full read of all five fixtures completed. Five confirmed cross-document inconsistencies identified, and one apparent inconsistency rejected as explained in-document. Reconciliation table and both required sections are below.

### Reconciliation Table

| Item | Changelog | Release Notes | Ops Log | Migration Index | Support Tickets | Status |
|---|---|---|---|---|---|---|
| 2.32.1 release | `2.32.1 — 2026-06-24`: "Fix export pagination off-by-one (EXP-441)" | "2.32.1 (current GA) ... Recommended for all tenants" | 2026-06-25: "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx ... Fleet pinned to 2.32.0" | — | — | **Confirmed inconsistency** |
| 2.32.0 deploy / downtime | `2.32.0 — 2026-06-17`: "Bulk export API GA (EXP-380)" | "2.32.0 ... This release was deployed with zero downtime" | 2026-06-17: "Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)" | — | — | **Confirmed inconsistency** |
| MIG-2207 registration | — | — | 2026-06-17: "Migration MIG-2207 applied to prod (bulk export tables)" | Table lists only MIG-2199, MIG-2183, MIG-2160 — no MIG-2207 row | — | **Confirmed inconsistency** |
| fastcsv version vs CVE remediation | `2.31.2 — 2026-05-28`: "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)" | — | — | "Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4" | SUP-1188: "remediation requires fastcsv >= 1.9.0; verify the shipped pin" | **Confirmed inconsistency** |
| fastcsv license | — | "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)" | — | fastcsv 1.8.3 (same snapshot as above) | SUP-1204: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected ... NOTICE file update pending" | **Confirmed inconsistency** |
| 2.31.4 hotfix | No entry for 2.31.4 anywhere in the changelog | — | 2026-06-02: "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)" | — | SUP-1189: "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02 ... no changelog entry was published for 2.31.4" | **Confirmed inconsistency** |
| 2.30.x version gap | "Note on version numbering: 2.30.x was never released. Version renumbering ... was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x." | (no 2.30.x entry) | (no 2.30.x entry) | (no 2.30.x entry) | — | **Rejected — explained in-document** |
| MIG-2199 registration timing | `2.31.2 — 2026-05-28` | — | 2026-05-28: "Deploy 2.31.2. Migration MIG-2199 applied." | `MIG-2199 \| 2.31.2 \| 2026-05-27` | — | Consistent, no finding |
| MIG-2183 registration timing | `2.31.0 — 2026-05-14` | — | 2026-05-14: "Deploy 2.31.0. Migration MIG-2183 applied." | `MIG-2183 \| 2.31.0 \| 2026-05-13` | — | Consistent, no finding |
| MIG-2160 registration timing | `2.29.6 — 2026-04-30`: "Minor bug fixes" | — | (outside ops-log excerpt window) | `MIG-2160 \| 2.29.6 \| 2026-04-29` | — | Consistent, no finding |

## Evidence

### Confirmed Inconsistencies

1. **2.32.1 marketed as current GA vs. fleet-wide rollback.**
   - Release notes: "## 2.32.1 (current GA) — Export pagination hotfix. Recommended for all tenants."
   - Ops log: "2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."
   - The release notes present `2.32.1` as the currently recommended GA build, but the ops log records that it was rolled back fleet-wide the next day and production is pinned to `2.32.0`. The rollback incident report (`OUT-91`) is explicitly noted as "not yet published," so there is no in-document explanation reconciling the two — this is an unresolved staleness/contradiction, not an explained one.

2. **"Zero downtime" claim vs. recorded 6-minute outage.**
   - Release notes: "## 2.32.0 ... This release was deployed with zero downtime."
   - Ops log: "2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."
   - Direct contradiction: the customer-facing release notes claim zero downtime for the exact release (`2.32.0`) that the ops log ties to a named 6-minute outage incident (`OUT-88`).

3. **MIG-2207 applied to production but absent from the migration index.**
   - Ops log: "2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."
   - Migration index: table rows are only `MIG-2199`, `MIG-2183`, `MIG-2160` — no `MIG-2207` entry, despite the index's own stated policy: "All production schema changes MUST be registered here before deploy."
   - `MIG-2207` is recorded as applied in the ops log but is missing from the authoritative migration index, violating the index's own registration requirement.

4. **CVE-2026-4417 "fixed" claim vs. shipped fastcsv version below remediation threshold.**
   - Changelog: "2.31.2 — 2026-05-28 ... Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."
   - Support tickets: SUP-1188 — "Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."
   - Migration index: "Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4."
   - The changelog states the CVE is fixed via a fastcsv upgrade, but the migration index's dependency snapshot (dated after the fix, 2026-06-24) shows the bundled version is `fastcsv 1.8.3` — below the `>= 1.9.0` threshold security engineering says is required for full remediation (per SUP-1188). No document resolves this gap; SUP-1188 explicitly flags it as unverified.

5. **fastcsv license claim ("MIT, unchanged since 2025") vs. reported relicense to BUSL-1.1.**
   - Release notes: "Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."
   - Support tickets: SUP-1204 — "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."
   - Migration index confirms the bundled version is `fastcsv 1.8.3`, which is at or above the `1.8.0` relicense point cited in SUP-1204. The release notes' claim of an unchanged MIT license is therefore contradicted by the support ticket, and the ticket itself confirms the NOTICE file has not yet been corrected ("update pending") — i.e., the discrepancy is acknowledged as open, not resolved or explained away.

6. **Hotfix 2.31.4 deployed but missing from the changelog.**
   - Ops log: "2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."
   - Support tickets: SUP-1189 — "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."
   - Changelog: no `2.31.4` entry exists anywhere in the excerpt (entries jump from `2.31.2 — 2026-05-28` to `2.32.0 — 2026-06-17`).
   - Both the ops log and the support ticket record a real, deployed hotfix (`2.31.4`) that never appears in the changelog. SUP-1189 states the fact of the omission but gives no policy reason (e.g., no "EU-shard-only hotfixes are excluded from the public changelog by policy" statement anywhere) — it merely confirms the gap rather than explaining it, so this is treated as a confirmed inconsistency rather than a rejected candidate.

### Rejected Candidates

1. **Apparent version-numbering gap: no `2.30.x` release appears in any document.**
   - Candidate concern: the changelog jumps from `2.29.6 — 2026-04-30` to `2.31.0 — 2026-05-14` with no `2.30.x` entry, and no `2.30.x` version appears in release notes, ops log, or migration index either — which could look like a missing/undocumented release.
   - In-document explanation (changelog.md): "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."
   - **Rejected as a non-finding.** The changelog itself explains the gap is intentional (approved renumbering per RFC-77), not a missing or undocumented release, so its absence from the other four documents is expected rather than contradictory.

## Assumptions

- All five fixtures are explicitly marked as "(excerpt, 2026 Q2)" or "(published)" / "(authoritative list)" — gaps caused purely by excerpt scope (e.g., migration-index not listing pre-2026-Q2 migrations, ops-log starting at 2026-05-14) are not treated as inconsistencies unless a specific entry from one document is directly contradicted or omitted from another within the overlapping Q2 window.
- `EXP-380` appearing as "preview" in release-notes' `2.31.0` entry and as "GA" in changelog's `2.32.0` entry is treated as a normal preview→GA progression, not a contradiction, since no document claims GA status for `2.31.0` or preview status for `2.32.0`.
- `EXP-441` (changelog `2.32.1`) and `SUP-1197` (bulk export row-limit question, "no defect") are not treated as reconciliation findings since no other document makes a claim that contradicts them.
- The migration index's own registration policy ("All production schema changes MUST be registered here before deploy") is treated as the ground truth for what "should" be registered; `MIG-2207`'s absence is evaluated against that stated policy, not against speculation about undocumented internal process exceptions.
- Whether `OUT-91` (referenced in ops-log as a "draft, not yet published" incident report) would, if published, contain an explanation reconciling the 2.32.1 GA-vs-rollback contradiction is unmeasured — the fixtures provided do not include its contents, so the contradiction is reported as unresolved based on what is actually in the five documents.

## Stop Condition

This task is complete: all five named fixtures were read in full, a reconciliation table covering every entry across the documents was produced, a confirmed-inconsistencies section with quoted evidence from both sides of each finding is included (6 findings), and a rejected-candidates section citing the in-document explanation (RFC-77 version-renumbering note) is included (1 rejection). No other files under `benchmarks/mission-vs-goal/` were accessed. No commit, push, package install, or network access was performed.
