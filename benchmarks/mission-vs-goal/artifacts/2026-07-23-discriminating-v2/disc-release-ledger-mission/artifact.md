# disc-release-ledger — Mission Artifact

## Mission

Reconcile the release ledger across five fixture documents and surface every
cross-document inconsistency, with exact-identifier evidence from both sides
of each discrepancy. Apparent inconsistencies that a document itself explains
must be rejected as non-findings, with the explanation quoted.

Task id: `disc-release-ledger`. Category: `reconciliation`. Arm: `mission`.
Scope: exactly these five fixtures —
`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`,
`support-tickets.md` (all under
`benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`).

## Plan

1. Read all five fixtures in full (done — see Evidence for raw excerpts used).
2. Build an identifier inventory: release versions, migration IDs, ticket IDs,
   incident IDs, dependency names/versions.
3. For each identifier, compare every claim made about it across documents.
4. Classify each apparent discrepancy as CONFIRMED (contradiction with no
   in-document resolution) or REJECTED (the apparent gap/contradiction is
   explicitly explained by text inside one of the documents).
5. Draft the reconciliation table, confirmed-inconsistencies section, and
   rejected-candidates section, each with exact quotes.
6. Spawn one independent reviewer subagent — scoped to read only the five
   named fixtures plus this draft artifact, nothing else under
   `benchmarks/mission-vs-goal/` — to independently re-derive the
   inconsistencies and check the draft's 6 confirmed findings and 4 rejected
   candidates for false positives, misquotes, and omissions.
7. Write Score and Stop Decision based on the reviewer's verdict.

(Note on mission tooling: `scripts/mission-state.py` does not exist in this
repository and no `.mission-state/` machinery was available to drive phase
gates automatically. This Plan/Execution/Review/Score/Stop-Decision structure
was followed manually and is reported transparently in Assumptions below.)

## Execution

### Identifier inventory built from the fixtures

- **Versions**: 2.29.6, 2.30.x (never released), 2.31.0, 2.31.2, 2.31.4,
  2.32.0, 2.32.1
- **Migrations**: MIG-2160, MIG-2183, MIG-2199, MIG-2207
- **Feature/ticket refs in changelog/release-notes**: RET-201, EXP-380,
  EXP-441
- **Incidents (ops-log)**: OUT-88, OUT-91
- **Support tickets**: SUP-1188, SUP-1189, SUP-1197, SUP-1204
- **Dependencies**: fastjson, fastcsv, libxmlq

Each was cross-checked across all five documents (full method: grep-by-eye
over the already-loaded fixture text, one identifier at a time, both for its
presence/absence and for contradictory claims about its status).

## Review

### Reconciliation table

| Identifier | Document A claim | Document B claim | Verdict |
|---|---|---|---|
| `2.29.6` | changelog.md: "## 2.29.6 — 2026-04-30 / Minor bug fixes" | migration-index.md: "MIG-2160 \| 2.29.6 \| 2026-04-29" | Consistent (migration registered day before release) |
| `2.30.x` | changelog.md: no `2.30.x` entry in the version list | changelog.md (same doc): "2.30.x was never released... the train jumped from 2.29.x directly to 2.31.x" | Non-finding — explained in-document |
| `2.31.0` | changelog.md: "## 2.31.0 — 2026-05-14 / New retention settings page (RET-201)" | ops-log.md: "2026-05-14 \| Deploy 2.31.0. Migration MIG-2183 applied." | Consistent |
| `2.31.0` (migration) | migration-index.md: "MIG-2183 \| 2.31.0 \| 2026-05-13" | ops-log.md: deploy dated 2026-05-14 | Consistent (registered day before deploy) |
| `EXP-380` | release-notes.md 2.31.0: "Includes the new audit export (EXP-380 preview)" | changelog.md 2.32.0: "Bulk export API GA (EXP-380)" | Consistent (preview → GA progression across releases) |
| `2.31.2` | changelog.md: "## 2.31.2 — 2026-05-28 / Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)" | release-notes.md: "2.31.2 / Security maintenance release. Customers on 2.31.x should upgrade." | Consistent framing |
| `2.31.2` (migration) | migration-index.md: "MIG-2199 \| 2.31.2 \| 2026-05-27" | ops-log.md: "2026-05-28 \| Deploy 2.31.2. Migration MIG-2199 applied." | Consistent (registered day before deploy) |
| `2.31.2` / CVE-2026-4417 remediation | changelog.md: CVE-2026-4417 "fixed... (upgrade fastcsv)" | support-tickets.md SUP-1188: "remediation requires fastcsv >= 1.9.0; verify the shipped pin" vs. migration-index.md dependency snapshot: "fastcsv 1.8.3" | **CONFIRMED inconsistency** |
| `2.31.4` | ops-log.md: "2026-06-02 \| Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)." | changelog.md / release-notes.md: no `2.31.4` entry anywhere in either document | **CONFIRMED inconsistency** |
| `SUP-1189` / `2.31.4` | support-tickets.md: "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02... Note: no changelog entry was published for 2.31.4." | changelog.md: confirms no `2.31.4` heading exists | Corroborates the CONFIRMED `2.31.4` finding above (same finding, both sides agree the gap exists) |
| `2.32.0` (deploy/downtime) | ops-log.md: "2026-06-17 \| Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)." | release-notes.md: "2.32.0 ... This release was deployed with zero downtime." | **CONFIRMED inconsistency** |
| `2.32.0` (migration) | ops-log.md: "2026-06-17 \| Migration MIG-2207 applied to prod (bulk export tables)." | migration-index.md table: only lists MIG-2199, MIG-2183, MIG-2160 (no MIG-2207) | **CONFIRMED inconsistency** |
| `2.32.0` (fastjson) | changelog.md: "Dependency upgrades: fastjson 3.2 -> 3.3" | migration-index.md: "Dependency manifest snapshot (2026-06-24): fastjson 3.3" | Consistent |
| `2.32.1` (feature) | changelog.md: "## 2.32.1 — 2026-06-24 / Fix export pagination off-by-one (EXP-441)" | release-notes.md: "2.32.1 (current GA) / Export pagination hotfix. Recommended for all tenants." | Consistent framing between these two |
| `2.32.1` (current status) | release-notes.md: "2.32.1 (current GA) ... Recommended for all tenants." | ops-log.md: "2026-06-25 \| Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx... Fleet pinned to 2.32.0." | **CONFIRMED inconsistency** |
| `OUT-88` | ops-log.md: incident recorded during the 2.32.0 deploy window | changelog.md / release-notes.md: no mention of `OUT-88` | Subsumed into the 2.32.0 "zero downtime" CONFIRMED finding above; not a separate standalone finding |
| `OUT-91` | ops-log.md: "Rollback executed... (see OUT-91 draft, not yet published)" | changelog.md / release-notes.md: no `OUT-91` entry | Non-finding — explained in-document ("not yet published") |
| `MIG-2160` | migration-index.md: "MIG-2160 \| 2.29.6 \| 2026-04-29" | ops-log.md: no corresponding entry (earliest ops-log row is 2026-05-14) | Non-finding — ops-log.md is explicitly headed "(excerpt, 2026 Q2)"; the missing 2026-04-29/04-30 row is outside what the excerpt claims to cover, not a contradiction |
| `MIG-2183` | migration-index.md: registered 2026-05-13 | ops-log.md: deploy 2026-05-14 references "Migration MIG-2183 applied" | Consistent |
| `MIG-2199` | migration-index.md: registered 2026-05-27 | ops-log.md: deploy 2026-05-28 references "Migration MIG-2199 applied" | Consistent |
| `MIG-2207` | ops-log.md: applied 2026-06-17 | migration-index.md: absent from the table entirely | **CONFIRMED inconsistency** (same finding as the `2.32.0` migration row above) |
| `RET-201` | changelog.md 2.31.0: "New retention settings page (RET-201)" | release-notes.md 2.31.0: "Retention settings page" (same feature, no ticket ID cited, no contradiction) | Consistent |
| `EXP-441` | changelog.md 2.32.1: "Fix export pagination off-by-one (EXP-441)" | release-notes.md 2.32.1: "Export pagination hotfix." | Consistent |
| fastcsv (version vs. required) | migration-index.md: dependency snapshot "fastcsv 1.8.3" | support-tickets.md SUP-1188: CVE-2026-4417 full remediation "requires fastcsv >= 1.9.0" | **CONFIRMED inconsistency** (same finding as the CVE-2026-4417 row above) |
| fastcsv (license) | release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)" | support-tickets.md SUP-1204: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending." | **CONFIRMED inconsistency** |
| fastjson | changelog.md: "fastjson 3.2 -> 3.3" | migration-index.md: "fastjson 3.3" | Consistent |
| libxmlq | migration-index.md: "libxmlq 2.4" | no other document references `libxmlq` | No finding — single-source item, nothing to contradict |
| `SUP-1197` | support-tickets.md: "Bulk export row limit question... Answered from documentation; no defect." | (no cross-document claim exists to contradict) | No finding — informational ticket, no defect asserted |

### Confirmed inconsistencies (with evidence from both sides)

1. **Undocumented hotfix 2.31.4.**
   - ops-log.md: *"2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."*
   - support-tickets.md (SUP-1189): *"Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."*
   - changelog.md: version list runs `2.31.2` (2026-05-28) directly to `2.32.0` (2026-06-17) — no `2.31.4` heading.
   - release-notes.md: same gap — no `2.31.4` heading anywhere.
   - Verdict: a real, shipped production version (2.31.4) is completely absent from both customer/engineering-facing release documents. This is not explained away — support-tickets.md merely *confirms the gap exists*, it does not justify why the version was never published.

2. **Migration MIG-2207 not registered in the authoritative index.**
   - ops-log.md: *"2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."*
   - migration-index.md header: *"All production schema changes MUST be registered here before deploy."* Its table lists only `MIG-2199` (2.31.2), `MIG-2183` (2.31.0), `MIG-2160` (2.29.6) — `MIG-2207` does not appear.
   - Verdict: a migration ops-log records as applied to production is missing from the document that claims to be the mandatory, authoritative registry of all applied migrations.

3. **"Zero downtime" claim contradicted by a recorded outage.**
   - release-notes.md: *"## 2.32.0 ... This release was deployed with zero downtime."*
   - ops-log.md: *"2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."*
   - Verdict: both entries describe the same 2026-06-17 2.32.0 deploy; one asserts zero downtime, the other records a 6-minute full outage (OUT-88) during that same deploy's migration window.

4. **2.32.1 marketed as current/recommended GA while ops records show a full rollback.**
   - release-notes.md: *"## 2.32.1 (current GA) ... Recommended for all tenants."*
   - ops-log.md: *"2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."*
   - Verdict: release-notes.md presents 2.32.1 as the current, recommended GA release, while ops-log.md records that it was rolled back fleet-wide and production is pinned back to 2.32.0. These cannot both be the current operational state.

5. **fastcsv license claim contradicted by an escalated relicensing issue.**
   - release-notes.md: *"this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."*
   - support-tickets.md (SUP-1204): *"Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."*
   - migration-index.md dependency snapshot (2026-06-24): *"fastcsv 1.8.3"* — i.e., at/above the 1.8.0 relicense threshold SUP-1204 cites.
   - Verdict: release-notes.md's claim that the bundled license is "MIT, unchanged since 2025" is contradicted by the support ticket's statement that the shipped version (1.8.3, per the dependency snapshot) is affected by the upstream MIT→BUSL-1.1 relicense, with the NOTICE file fix still "pending" (i.e., not yet corrected at the time the ticket was logged).

6. **CVE-2026-4417 declared "fixed" while the required dependency version is not confirmed shipped.**
   - changelog.md: *"## 2.31.2 — 2026-05-28 / Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"*
   - support-tickets.md (SUP-1188): *"Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."*
   - migration-index.md dependency snapshot (2026-06-24): *"fastcsv 1.8.3"* — below the `>= 1.9.0` bar SUP-1188 says is required for full remediation.
   - Verdict: the changelog's "fixed" claim for CVE-2026-4417 is contradicted by the combination of (a) security engineering's follow-up stating full remediation needs fastcsv >= 1.9.0, and (b) the most recent dependency snapshot showing only 1.8.3 shipped — the fix is not confirmed complete by the documents' own later evidence.

### Rejected candidates (apparent inconsistencies explained in-document)

1. **Missing `2.30.x` version.** The version sequence jumps from `2.29.6` straight to `2.31.0`, which looks like a missing release. Rejected: changelog.md itself explains this — *"Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."*

2. **Incident OUT-91 absent from customer-facing documents.** ops-log.md references an incident ID (`OUT-91`) that never appears in changelog.md or release-notes.md, which looks like a missing/suppressed incident disclosure. Rejected: ops-log.md itself explains this in the same line — *"(see OUT-91 draft, not yet published)"* — i.e., the document itself flags that the OUT-91 write-up is a draft awaiting publication, not a contradiction.

3. **No ops-log.md entry for the 2.29.6 / MIG-2160 deploy.** migration-index.md registers `MIG-2160` on 2026-04-29 for version `2.29.6` (released 2026-04-30 per changelog.md), but ops-log.md has no row for that date. Rejected: ops-log.md's own heading is *"# Operations Log (excerpt, 2026 Q2)"* — it is explicitly presented as an excerpt (earliest row is 2026-05-14), not a claim to exhaustively cover every deploy back to late April, so the absence is not a genuine contradiction.

4. **EXP-380 described as "preview" in one document and "GA" in another.** release-notes.md (2.31.0) calls it *"EXP-380 preview"* while changelog.md (2.32.0) calls it *"Bulk export API GA (EXP-380)"* — different status words for the same ticket ID could look like a conflict. Rejected: the two mentions are attached to two different, chronologically sequential releases (2.31.0 released 2026-05-14, then 2.32.0 released 2026-06-17); "preview" progressing to "GA" across a later release is the expected feature lifecycle, not a contradiction.

## Score

Independent reviewer subagent verdict (full transcript summary retained
below; the reviewer was scoped to read only the five fixtures plus this
artifact's Review section, and was asked to independently re-derive the
inconsistencies rather than merely trust this draft's framing):

> "All six confirmed findings are genuine, all four rejected candidates are
> correctly classified, every quoted passage matches the source fixture
> verbatim, and no finding was missed. No corrections are required." —
> reviewer's overall judgment, verbatim.

Reviewer's per-item verdicts: all 6 confirmed inconsistencies —
`confirmed-correct` (quotes verified exactly against fixture text, no
misquotes found); all 4 rejected candidates — `correctly rejected`; missed
inconsistencies — `none found`.

| Criterion | Assessment |
|---|---|
| Completeness (every fixture identifier enumerated) | All 23 distinct identifiers found in the five fixtures (7 versions, 4 migrations, 3 changelog/release-notes ticket refs, 2 incidents, 4 support tickets, 3 dependencies) are covered in the reconciliation table above, including fully compliant ones. Independent reviewer found zero missed inconsistencies after its own re-derivation. |
| Evidence quality | Every confirmed finding and every rejected candidate quotes exact fixture text, not paraphrase. Reviewer independently checked each quote against source text: no misquotes found. |
| Confirmed vs. rejected separation | 6 confirmed inconsistencies, 4 rejected candidates, kept in clearly separate sections as required. Reviewer validated the classification of all 10 items. |
| False positives avoided | The 2.30.x gap and OUT-91 absence are the two most "obvious-looking" apparent inconsistencies in this fixture set; both were checked against in-document text and correctly rejected rather than over-reported. Reviewer independently confirmed both rejections as correct. |

## Stop Decision

Iteration 1 of 3 (max-iter). Time used: well under the 30-minute budget (no
long-running or network operations were performed; work was fixture reading
plus authoring).

**Mission-tooling gap**: `scripts/mission-state.py` does not exist in this
repository, so `init` / `permission-preflight` / `advance` / `aggregate-reviews`
/ `push-score` / `mark-passes` could not be executed as literal commands. No
`.mission-state/state.json` machine-verified pass/fail gate was produced.
This is stated plainly rather than fabricated.

In place of the automated `aggregate-reviews` / `push-score --scoring-json`
commands, one independent reviewer subagent was spawned (scope-limited to
the five named fixtures plus this artifact's own Review section — no other
path under `benchmarks/mission-vs-goal/` was made available to it). It
independently re-derived the cross-document inconsistencies from the raw
fixtures and checked every confirmed finding, every rejected candidate, and
every quote for accuracy. Its verdict (quoted in full in Score above): all 6
confirmed findings genuine and correctly quoted, all 4 rejected candidates
correctly classified, zero missed inconsistencies, no corrections required.

Stop condition met: all five fixtures fully read, all identifiers
enumerated, reconciliation table complete, confirmed/rejected sections both
populated with quoted evidence verified by an independent reviewer, no
unresolved ambiguity about who said what, no reviewer-flagged corrections
outstanding. **Decision: STOP — artifact considered complete for this run.**
No further iteration was judged necessary (early-stop; iteration 1 of 3
used; reviewer required zero corrections, so no iteration-2 rework loop was
triggered).

## Evidence

Raw fixture excerpts relied upon (already quoted inline above; reproduced
here per-document for audit convenience):

**changelog.md**
```
## 2.32.1 — 2026-06-24
- Fix export pagination off-by-one (EXP-441)

## 2.32.0 — 2026-06-17
- Bulk export API GA (EXP-380)
- Dependency upgrades: fastjson 3.2 -> 3.3

## 2.31.2 — 2026-05-28
- Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)

## 2.31.0 — 2026-05-14
- New retention settings page (RET-201)

## 2.29.6 — 2026-04-30
- Minor bug fixes

Note on version numbering: 2.30.x was never released. Version renumbering to
align with the platform train was approved in RFC-77; the train jumped from
2.29.x directly to 2.31.x.
```

**release-notes.md**
```
## 2.32.1 (current GA)
Export pagination hotfix. Recommended for all tenants.

## 2.32.0
Bulk export API is now generally available. This release was deployed with
zero downtime.

## 2.31.2
Security maintenance release. Customers on 2.31.x should upgrade.

## 2.31.0
Retention settings page. Includes the new audit export (EXP-380 preview).

Dependency notice: this product bundles fastcsv under the license recorded in
the NOTICE file (MIT, unchanged since 2025).
```

**ops-log.md**
```
| 2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0. |
| 2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC). |
| 2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables). |
| 2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression). |
| 2026-05-28 | Deploy 2.31.2. Migration MIG-2199 applied. |
| 2026-05-14 | Deploy 2.31.0. Migration MIG-2183 applied. |
```

**migration-index.md**
```
| MIG-2199 | 2.31.2 | 2026-05-27 |
| MIG-2183 | 2.31.0 | 2026-05-13 |
| MIG-2160 | 2.29.6 | 2026-04-29 |

Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3,
libxmlq 2.4.
```

**support-tickets.md**
```
## SUP-1189 — EU tenant CSV delimiter regression (2026-06-01)
... Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02.
Customer confirmed resolution. Note: no changelog entry was published for
2.31.4.

## SUP-1197 — Bulk export row limit question (2026-06-19)
Answered from documentation; no defect.

## SUP-1204 — fastcsv license inquiry (2026-06-21)
... Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0.
Our bundled version is affected. Escalated to legal; NOTICE file update
pending.

## SUP-1188 — CVE-2026-4417 exposure question (2026-05-30)
... Follow-up from security engineering: remediation requires fastcsv >=
1.9.0; verify the shipped pin.
```

## Assumptions

1. **Mission control-plane unavailable.** `scripts/mission-state.py` is not
   present anywhere in this repository checkout (confirmed by `find` before
   starting). No `.mission-state/` directory existed at task start. This
   artifact therefore documents the mission Plan → Execution → Review →
   Score → Stop Decision structure manually, without a machine-verified
   `passes: true` gate. This is reported as an explicit limitation rather
   than papered over.
2. **"Fully compliant" identifiers are listed, not omitted.** Per the task's
   exhaustiveness requirement, the reconciliation table includes identifiers
   with no discrepancy (e.g., `fastjson`, `RET-201`, `EXP-441`, `MIG-2183`,
   `MIG-2199`) alongside the ones with confirmed problems, so absence of an
   identifier from the "confirmed" list is visible as a deliberate
   "consistent" verdict, not an oversight.
3. **`OUT-88` and `MIG-2207` (fastcsv version/CVE) findings are folded into
   single narrative entries** even though they touch multiple identifiers
   (e.g., the CVE-2026-4417 finding cites both `SUP-1188` and the
   `fastcsv 1.8.3` dependency-snapshot value), because they are one
   underlying inconsistency, not several independent ones. The
   reconciliation table still lists each identifier's row individually for
   traceability.
4. **Scope discipline.** Only the five named fixture files under
   `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/` were
   read. No other file under `benchmarks/mission-vs-goal/` (task definitions,
   scoring configuration, answer keys, or sibling task directories) was
   opened, grepped, or listed, per the task's stated constraint.
5. **No network, commit, or package-install actions were taken or needed**
   for this task; all work was local file reads and one local file write.
