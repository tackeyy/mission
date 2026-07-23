# Release Ledger Reconciliation — disc-release-ledger (claude_code_goal_command)

## Goal

Reconcile the release ledger across five documents — `changelog.md`,
`release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`
(all under `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`)
— and find every cross-document inconsistency: entries recorded in one
document but missing or contradicted in another. Every confirmed finding
must quote the exact identifier/value from both sides. Apparent
inconsistencies that are explained inside the documents themselves must be
rejected as non-findings, with the explanation cited. Coverage must be
exhaustive: every identifier in scope is enumerated below, including fully
compliant ones.

## Result

- **7 confirmed cross-document inconsistencies** (see Evidence → Confirmed
  Inconsistencies).
- **2 rejected candidates** — apparent gaps that are explicitly explained
  inside the documents (see Evidence → Rejected Candidates).
- All other identifiers cross-checked below are consistent, or exist in only
  one document with no contradicting claim elsewhere (listed as "no
  cross-reference" in the table, not treated as findings).

## Evidence

### Reconciliation Table

| Identifier | Document A claim | Document B claim | Verdict |
|---|---|---|---|
| `2.32.1` | release-notes.md: "## 2.32.1 (current GA) ... Recommended for all tenants." | ops-log.md 2026-06-25: "Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx ... Fleet pinned to 2.32.0." | **Contradiction (confirmed, see D)** |
| `2.32.0` | release-notes.md: "## 2.32.0 ... This release was deployed with zero downtime." | ops-log.md 2026-06-17: "Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)." | **Contradiction (confirmed, see C)** |
| `MIG-2207` | ops-log.md 2026-06-17: "Migration MIG-2207 applied to prod (bulk export tables)." | migration-index.md table: only lists `MIG-2199`, `MIG-2183`, `MIG-2160`; `MIG-2207` absent. Header rule: "All production schema changes MUST be registered here before deploy." | **Missing registration (confirmed, see A)** |
| `2.31.4` | ops-log.md 2026-06-02: "Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)." | changelog.md: no `2.31.4` heading anywhere between `2.32.0` and `2.31.2`. support-tickets.md SUP-1189: "Note: no changelog entry was published for 2.31.4." | **Missing changelog entry (confirmed, see B)** |
| `EXP-380` | changelog.md `2.32.0`: "Bulk export API GA (EXP-380)" | release-notes.md `2.31.0`: "Includes the new audit export (EXP-380 preview)." changelog.md `2.31.0` entry says only "New retention settings page (RET-201)" — no `EXP-380` mention. | **Contradiction (confirmed, see G)** |
| CVE-2026-4417 remediation | changelog.md `2.31.2`: "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)" | support-tickets.md SUP-1188: "remediation requires fastcsv >= 1.9.0; verify the shipped pin." migration-index.md: "Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4." | **Contradiction (confirmed, see E)** |
| fastcsv license | release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)." | support-tickets.md SUP-1204: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. ... NOTICE file update pending." | **Contradiction (confirmed, see F)** |
| `2.30.x` version gap | changelog.md: no `2.30.x` heading between `2.31.0` and `2.29.6`. | migration-index.md: no row between `MIG-2183 / 2.31.0` and `MIG-2160 / 2.29.6`. | **Rejected — explained (see Rejected #1)** |
| `OUT-91` | ops-log.md 2026-06-25: "(see OUT-91 draft, not yet published)" | No other document references `OUT-91`. | **Rejected — explained (see Rejected #2)** |
| `EXP-441` | changelog.md `2.32.1`: "Fix export pagination off-by-one (EXP-441)" | release-notes.md `2.32.1`: "Export pagination hotfix." (matching description, no ticket ID given) | Consistent — no contradiction |
| `RET-201` | changelog.md `2.31.0`: "New retention settings page (RET-201)" | release-notes.md `2.31.0`: "Retention settings page." (matching description) | Consistent — no contradiction |
| fastjson `3.2 -> 3.3` | changelog.md `2.32.0`: "Dependency upgrades: fastjson 3.2 -> 3.3" | migration-index.md snapshot (2026-06-24): "fastjson 3.3" | Consistent — no contradiction |
| `MIG-2199` | ops-log.md 2026-05-28: "Deploy 2.31.2. Migration MIG-2199 applied." | migration-index.md: "MIG-2199 \| 2.31.2 \| 2026-05-27" (registered one day before deploy) | Consistent — registered before deploy, per rule |
| `MIG-2183` | ops-log.md 2026-05-14: "Deploy 2.31.0. Migration MIG-2183 applied." | migration-index.md: "MIG-2183 \| 2.31.0 \| 2026-05-13" (registered one day before deploy) | Consistent — registered before deploy, per rule |
| `MIG-2160` | migration-index.md: "MIG-2160 \| 2.29.6 \| 2026-04-29" | ops-log.md excerpt begins 2026-05-14; no 2026-04-30-or-earlier row exists to compare against. | No cross-reference — outside ops-log.md excerpt window; not a contradiction |
| `libxmlq 2.4` | migration-index.md snapshot only | Not mentioned in changelog.md, release-notes.md, ops-log.md, or support-tickets.md | No cross-reference — single-document mention only |
| `SUP-1197` | support-tickets.md: "Bulk export row limit question (2026-06-19). Answered from documentation; no defect." | Consistent with `2.32.0` bulk export GA (2026-06-17), no contradiction elsewhere | Consistent — no defect, no contradiction |
| `RFC-77` | changelog.md only: "Version renumbering to align with the platform train was approved in RFC-77" | Not mentioned elsewhere | No cross-reference — single-document mention only |

### Confirmed Inconsistencies

**A. `MIG-2207` applied to prod but never registered in the authoritative migration index.**
- ops-log.md: `"2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."`
- migration-index.md header rule: `"All production schema changes MUST be registered here before deploy."` — yet the table only contains `MIG-2199`, `MIG-2183`, `MIG-2160`; `MIG-2207` does not appear anywhere in migration-index.md.
- No document explains this omission. This is a genuine policy violation: a migration ops-log records as applied is absent from the document that is supposed to be the authoritative, pre-registered list.

**B. `2.31.4` hotfix deployed but has no changelog.md entry.**
- ops-log.md: `"2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."`
- changelog.md: no `2.31.4` heading exists (the document jumps straight from `## 2.32.0 — 2026-06-17` to `## 2.31.2 — 2026-05-28`).
- support-tickets.md SUP-1189 confirms the deploy occurred and flags the gap itself: `"Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."` The support ticket surfaces the gap as an open fact, not a resolution — nothing in any document justifies why a shipped production hotfix was excluded from the changelog, so this stands as a confirmed inconsistency rather than an explained one.

**C. Release notes claim "zero downtime" for `2.32.0`; ops log records a 6-minute outage on the same deploy.**
- release-notes.md: `"## 2.32.0 ... This release was deployed with zero downtime."`
- ops-log.md: `"2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."`
- Both entries describe the same deploy event (`2.32.0`, 2026-06-17). No document reconciles the two claims.

**D. Release notes list `2.32.1` as current, recommended GA; ops log records it rolled back fleet-wide.**
- release-notes.md: `"## 2.32.1 (current GA) Export pagination hotfix. Recommended for all tenants."`
- ops-log.md: `"2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."`
- The ops-log parenthetical `"(see OUT-91 draft, not yet published)"` explains only why the *incident report* isn't public — it does not explain why release-notes.md still presents `2.32.1` as the current, recommended GA build after it was pulled from production. The underlying contradiction (customer-facing "current GA" vs. internal "rolled back / pinned to 2.32.0") is unresolved by any document.

**E. Changelog claims CVE-2026-4417 "fixed"; support follow-up and the dependency snapshot indicate incomplete remediation.**
- changelog.md `2.31.2`: `"Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"`
- support-tickets.md SUP-1188: `"Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."`
- migration-index.md: `"Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4."` — the shipped pin (`1.8.3`) is below the `>= 1.9.0` threshold SUP-1188 says is required for full remediation, and the snapshot postdates the `2.31.2` release (2026-05-28) by nearly a month with no later fastcsv bump recorded anywhere.
- No document shows fastcsv reaching `1.9.0`, so the changelog's "fixed" claim is contradicted by the security follow-up and the actual shipped version.

**F. Release notes claim fastcsv license is unchanged MIT; a support ticket says the bundled version is relicensed and affected.**
- release-notes.md: `"Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."`
- support-tickets.md SUP-1204: `"Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."`
- migration-index.md confirms the bundled version is `fastcsv 1.8.3`, which is at or above the `1.8.0` relicensing threshold SUP-1204 cites, corroborating that the bundled build is the affected (BUSL-1.1) one. SUP-1204's `"NOTICE file update pending"` shows this is an acknowledged, *unresolved* discrepancy — not an explanation that neutralizes it.

**G. `EXP-380` is described as a `2.31.0` preview in release notes but changelog.md first introduces it at `2.32.0` as a GA feature with no prior `2.31.0` mention.**
- release-notes.md `2.31.0`: `"Retention settings page. Includes the new audit export (EXP-380 preview)."`
- changelog.md `2.31.0`: `"New retention settings page (RET-201)"` — this is the complete entry; no `EXP-380` or "audit export" is mentioned.
- changelog.md `2.32.0`: `"Bulk export API GA (EXP-380)"` — the only changelog.md appearance of `EXP-380`, introduced here for the first time and described as "Bulk export API," not "audit export."
- No document explains why release-notes.md attributes an `EXP-380` preview to `2.31.0` when changelog.md's `2.31.0` entry is silent on it, nor why the feature name differs ("audit export" vs. "Bulk export API") between the two mentions.

### Rejected Candidates

**1. Apparent missing `2.30.x` version line (changelog.md and migration-index.md both skip straight from the `2.29.x` line to `2.31.x`).**
- Looks suspicious because both changelog.md (`2.32.1 → 2.32.0 → 2.31.2 → 2.31.0 → 2.29.6`) and migration-index.md (`2.31.2 → 2.31.0 → 2.29.6`) skip `2.30.x` entirely, which could look like missing/lost entries.
- **Rejected**: changelog.md explicitly explains this: `"Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."` Since `2.30.x` was never released, its absence from migration-index.md is expected, not a gap.

**2. `OUT-91` referenced but not documented anywhere else.**
- Looks suspicious because `OUT-91` is cited as the source for the `2.32.1` rollback but no incident document with that ID exists among the five fixtures, which could look like a dangling/missing reference.
- **Rejected**: ops-log.md itself explains the absence inline: `"(see OUT-91 draft, not yet published)"` — the document explicitly states the incident report is still a draft and not yet published, so its absence from the ledger is expected, not an inconsistency. (Note: this explains only the *missing incident report*; it does not resolve the separate, confirmed contradiction in Finding D about release-notes.md still marketing `2.32.1` as current/recommended GA.)

## Assumptions

- The five fixtures are treated as the complete record for this reconciliation; no other version of these documents was consulted, and nothing outside the named fixture paths was opened, per the task's scope restriction.
- ops-log.md is explicitly an "excerpt," and its earliest row is 2026-05-14. Any identifier that would only appear before that date (e.g., a hypothetical `2.29.6` deploy row for `MIG-2160`) is treated as **unmeasured / out of excerpt range**, not as a missing-entry finding, since the document does not claim completeness before that date.
- `EXP-441` and `RET-201` are treated as matching across changelog.md and release-notes.md based on matching feature descriptions ("export pagination" / "retention settings page") even though release-notes.md does not repeat the ticket IDs verbatim — release-notes.md is written for a customer audience and is assumed not to be expected to cite internal ticket IDs for identifiers it does corroborate by description.
- For Finding G, whether "audit export" and "Bulk export API" are literally the same feature (vs. two features that happen to share ticket ID `EXP-380`) is not determinable from the fixtures alone; either interpretation still yields a genuine, unexplained cross-document inconsistency, so it is reported as confirmed rather than speculated further.
- No production system, ticketing system, or version-control history was queried; this reconciliation is based solely on the text of the five fixture documents as given.

## Stop Condition

This artifact is complete: all five required fixtures were read in full, every identifier found across the five documents (version numbers, ticket IDs, migration IDs, incident IDs, dependency versions, and the RFC reference) is enumerated in the Reconciliation Table, 7 confirmed inconsistencies are documented with quoted evidence from both sides, 2 rejected candidates are documented with their in-document explanations quoted, and the required headings (Goal, Result, Evidence, Assumptions, Stop Condition) are present. No further action is pending.
