# Portfolio CX Ledger Reconciliation — claude_code_goal_command

## Goal

Reconcile the release ledger across five documents (`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`) covering 2026 Q2. Find every cross-document inconsistency — entries recorded in one document but missing or contradicted in another — quoting exact identifiers from both sides. Apparent inconsistencies that are explained inside the documents themselves must be rejected as non-findings, with the explanation cited.

## Result

- **5 confirmed inconsistencies** across the five documents (release-status claims, incident/downtime claims, migration registration, dependency license claims, and CVE remediation claims).
- **3 candidate inconsistencies rejected** as non-findings because the documents themselves supply an explanation.
- All findings below are based solely on the text of the five named fixture files; no other files were read.

## Evidence

### Reconciliation Table

| Identifier / Topic | Changelog | Release Notes | Ops Log | Migration Index | Support Tickets | Status |
|---|---|---|---|---|---|---|
| 2.32.1 (EXP-441) | Listed as latest fix, 2026-06-24 | "current GA... Recommended for all tenants" | Rolled back 2026-06-25, fleet pinned to 2.32.0 | — | — | **Confirmed inconsistency (A)** |
| 2.32.0 deploy (EXP-380, 2026-06-17) | Bulk export API GA | "deployed with zero downtime" | Incident OUT-88, 6-minute full outage 18:04–18:10 UTC | — | — | **Confirmed inconsistency (B)** |
| MIG-2207 | — | — | "Migration MIG-2207 applied to prod (bulk export tables)" on 2026-06-17 | Not present in table (only MIG-2199, MIG-2183, MIG-2160) | — | **Confirmed inconsistency (C)** |
| fastcsv license | "upgrade fastcsv" (2.31.2, CVE fix) | "NOTICE file (MIT, unchanged since 2025)" | — | fastcsv 1.8.3 in dependency snapshot | SUP-1204: relicensed MIT→BUSL-1.1 as of 1.8.0; bundled version affected; NOTICE update pending | **Confirmed inconsistency (D)** |
| CVE-2026-4417 remediation | "Security: fixed CVE-2026-4417 ... (upgrade fastcsv)" (2.31.2) | "Security maintenance release" | — | fastcsv 1.8.3 (2026-06-24 snapshot) | SUP-1188: remediation requires fastcsv >= 1.9.0; "verify the shipped pin" | **Confirmed inconsistency (E)** |
| Version gap 2.29.6 → 2.31.0 | Explicit note: RFC-77 renumbering, 2.30.x never released | — | — | — | — | **Rejected (explained in-document, #1)** |
| 2.31.4 EU hotfix | No entry | No entry | "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)" 2026-06-02 | — | SUP-1189: "no changelog entry was published for 2.31.4" | **Rejected (explained in-document, #2)** |
| EXP-380 preview vs GA | GA (EXP-380) at 2.32.0 | "EXP-380 preview" at 2.31.0; GA language at 2.32.0 | — | — | — | **Rejected (explained in-document, #3)** |

### Confirmed Inconsistencies

**A. 2.32.1 presented as current, recommended GA release, but ops log shows it was rolled back the next day.**
- Release notes: `## 2.32.1 (current GA)\nExport pagination hotfix. Recommended for all tenants.`
- Ops log: `2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0.`
- Nothing in the release notes or changelog reflects the rollback; the release-notes document still frames 2.32.1 as the tenant-recommended current GA version while the operational record shows the fleet was pinned back to 2.32.0.

**B. Release notes claim "zero downtime" for the 2.32.0 deploy; ops log records a full outage during that same deploy.**
- Release notes: `## 2.32.0\nBulk export API is now generally available. This release was deployed with zero downtime.`
- Ops log: `2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC).`
- Both entries key off the same 2026-06-17 / 2.32.0 deploy event; the "zero downtime" claim directly contradicts the logged 6-minute full outage (OUT-88).

**C. Migration MIG-2207 was applied to production but never registered in the authoritative migration index.**
- Ops log: `2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables).`
- Migration index: table lists only `MIG-2199 | 2.31.2 | 2026-05-27`, `MIG-2183 | 2.31.0 | 2026-05-13`, `MIG-2160 | 2.29.6 | 2026-04-29` — MIG-2207 does not appear.
- Migration index states: `All production schema changes MUST be registered here before deploy.` MIG-2207 breaks this rule with no note explaining the omission.

**D. Release notes claim the bundled fastcsv license is unchanged (MIT) since 2025; a support ticket documents a relicense affecting the bundled version.**
- Release notes: `Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025).`
- Support tickets (SUP-1204): `Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending.`
- Migration index confirms the bundled version is `fastcsv 1.8.3` (post-1.8.0, i.e., within the relicensed range), so the "MIT, unchanged" claim in the release notes is contradicted by the ticket's own statement that the NOTICE file update is still pending.

**E. Changelog claims CVE-2026-4417 was "fixed" in 2.31.2, but the shipped fastcsv version does not meet the remediation threshold cited in a support ticket.**
- Changelog: `## 2.31.2 — 2026-05-28\n- Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)`
- Support tickets (SUP-1188): `Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin.`
- Migration index: `Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4.` — the shipped pin (1.8.3) is below the 1.9.0 threshold the ticket says is required for full remediation, contradicting the changelog's unqualified "fixed" claim.

### Rejected Candidates (Explained In-Document)

**1. Version numbering gap: 2.29.6 followed directly by 2.31.0 (no 2.30.x).**
- Apparent issue: no 2.30.x release appears in the changelog between 2.29.6 (2026-04-30) and 2.31.0 (2026-05-14).
- Explanation cited (changelog.md): `Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x.`
- Rejected: the documents themselves account for the gap.

**2. 2.31.4 EU-shard hotfix present in ops log but absent from changelog and release notes.**
- Apparent issue: ops log (`2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression).`) has no corresponding changelog or release-notes entry.
- Explanation cited (support-tickets.md, SUP-1189): `Note: no changelog entry was published for 2.31.4.`
- Rejected: the omission is explicitly acknowledged in the support ticket digest rather than being an undocumented discrepancy.

**3. EXP-380 labeled "preview" in the 2.31.0 release notes but "GA" in the 2.32.0 changelog/release notes.**
- Apparent issue: same feature identifier (EXP-380) carries different maturity labels across two versions.
- Explanation cited: release-notes.md 2.31.0 entry — `Includes the new audit export (EXP-380 preview).` — versus changelog.md 2.32.0 entry — `Bulk export API GA (EXP-380)` — and release-notes.md 2.32.0 — `Bulk export API is now generally available.`
- Rejected: read together, the documents describe a normal preview → GA progression across sequential releases, not a contradiction.

## Assumptions

- The five fixture files as read represent the complete and correct excerpts for this task; no other version of these documents exists elsewhere in the repository.
- "Unmeasured" applies to anything outside the quoted text: e.g., whether OUT-91 (referenced as a "draft, not yet published" in ops-log.md) would, once published, additionally explain or contextualize finding A — this is unmeasured because OUT-91's content was not provided in any of the five fixtures.
- No claim is made about which document is "authoritative" beyond what the documents state explicitly (e.g., migration-index.md self-declares as the authoritative migration list; that framing is taken from its own text, not assumed independently).
- No fixture, benchmark task-definition, or scoring file was opened, read, grepped, or listed beyond the five named fixtures and this output file, per task constraints.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v5-speed/portfolio-cx-ledger-claude_code_goal_command.md` and includes the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a reconciliation table, a confirmed-inconsistencies section with quoted evidence from both sides of each inconsistency, and a rejected-candidates section citing the in-document explanation for each rejected candidate. No commits, pushes, package installs, or network access were used. No files were read/grepped/listed under `benchmarks/mission-vs-goal/` other than the five named fixtures and this output file. Task complete.
