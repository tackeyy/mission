# Mission Artifact: Cross-Document Inconsistency Analysis
## release-ledger fixture set — portfolio-cx-ledger-mission-rep1

---

## Mission

Identify every cross-document inconsistency across five fixture documents:

- `changelog.md`
- `release-notes.md`
- `ops-log.md`
- `migration-index.md`
- `support-tickets.md`

(All located under `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`)

Produce an artifact containing: reconciliation table, confirmed-inconsistencies with verbatim quotes from both sides, and rejected-candidates with document-internal explanation quotes. Network access prohibited; commit prohibited; no benchmarks/ files other than the five fixtures and this output may be read.

---

## Plan

1. Read all five fixture documents in full.
2. Extract key data per document: version numbers, dates, dependency versions, deployment claims, migration registrations.
3. Cross-pair all data points that reference the same entity (version, migration ID, dependency, CVE).
4. For each candidate pair: classify as confirmed (factual contradiction with no document-internal reconciliation) or rejected (apparent discrepancy explained within the corpus or non-contradictory lifecycle stages).
5. Build reconciliation table over confirmed items.
6. Write artifact with all required sections.

---

## Execution

### Step 1–2: Key data extraction

| Document | Versions mentioned | Dates | Dependencies | Migrations | Notable claims |
|---|---|---|---|---|---|
| changelog.md | 2.32.1, 2.32.0, 2.31.2, 2.31.0, 2.29.6 | 2026-06-24, 06-17, 05-28, 05-14, 04-30 | fastjson 3.2→3.3 | — | CVE-2026-4417 "fixed"; 2.30.x never released |
| release-notes.md | 2.32.1, 2.32.0, 2.31.2, 2.31.0 | — | fastcsv MIT "unchanged since 2025" | — | 2.32.0 "zero downtime"; 2.32.1 "current GA"; EXP-380 preview in 2.31.0 |
| ops-log.md | 2.32.1, 2.32.0, 2.31.4, 2.31.2, 2.31.0 | 2026-06-25, 06-17, 06-02, 05-28, 05-14 | — | MIG-2207, MIG-2199, MIG-2183 applied | 2.32.1 rolled back; OUT-88 6-min outage during 2.32.0 deploy; 2.31.4 EU-only hotfix |
| migration-index.md | 2.31.2, 2.31.0, 2.29.6 | registered 05-27, 05-13, 04-29 | fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4 | MIG-2199, MIG-2183, MIG-2160 | "MUST be registered before deploy" |
| support-tickets.md | 2.31.4 | 2026-06-01, 06-02, 06-19, 06-21, 05-30 | fastcsv BUSL-1.1 as of 1.8.0 | — | No changelog for 2.31.4; CVE fix requires fastcsv ≥ 1.9.0 |

### Step 3–4: Candidate evaluation — see Evidence section for verbatim quotes

Six candidates confirmed as genuine inconsistencies. Four candidates rejected.

---

## Review

### Reconciliation Table

| # | Entity | Document A | Document A claim | Document B | Document B claim | Verdict |
|---|---|---|---|---|---|---|
| C-1 | 2.32.0 deployment downtime | release-notes.md | "deployed with zero downtime" | ops-log.md | "6-minute full outage during the schema migration window (18:04–18:10 UTC)" | **CONFIRMED** |
| C-2 | MIG-2207 registration | ops-log.md | "Migration MIG-2207 applied to prod" (2026-06-17) | migration-index.md | MIG-2207 absent; only MIG-2199, MIG-2183, MIG-2160 listed | **CONFIRMED** |
| C-3 | fastcsv license | release-notes.md | "fastcsv … MIT, unchanged since 2025" | support-tickets.md SUP-1204 | "relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected." | **CONFIRMED** |
| C-4 | CVE-2026-4417 remediation status | changelog.md | "fixed CVE-2026-4417 … (upgrade fastcsv)" | support-tickets.md SUP-1188 + migration-index.md | "remediation requires fastcsv >= 1.9.0"; shipped pin is fastcsv 1.8.3 | **CONFIRMED** |
| C-5 | 2.31.4 changelog coverage | ops-log.md | "Hotfix deploy 2.31.4 to tenants on the EU shard only" (2026-06-02) | changelog.md + support-tickets.md | No changelog entry for 2.31.4; SUP-1189: "no changelog entry was published for 2.31.4" | **CONFIRMED** |
| C-6 | Current GA version | release-notes.md | "2.32.1 (current GA)" | ops-log.md | "2.32.1 rolled back on all production tenants … Fleet pinned to 2.32.0." (2026-06-25) | **CONFIRMED** |
| R-1 | EXP-380 status across releases | release-notes.md 2.31.0 | "EXP-380 preview" | changelog.md 2.32.0 | "Bulk export API GA (EXP-380)" | **REJECTED** — normal preview→GA lifecycle |
| R-2 | MIG-2199 registration vs. deploy date | migration-index.md | registered 2026-05-27 | ops-log.md | deployed 2026-05-28 | **REJECTED** — registration before deploy is required by policy; dates are consistent |
| R-3 | 2.31.4 absent from migration-index | ops-log.md | 2.31.4 deployed EU shard | migration-index.md | no MIG entry for 2.31.4 | **REJECTED** — hotfix may carry no schema migration; ops-log does not assert a migration was applied for 2.31.4 |
| R-4 | Version gap 2.29.6 → 2.31.0 (no 2.30.x anywhere) | changelog.md | versions jump from 2.29.6 to 2.31.0 | release-notes.md / ops-log.md / migration-index.md | 2.30.x absent from all documents | **REJECTED** — explained in changelog.md: "2.30.x was never released" per RFC-77 renumbering |

---

## Score

Gated review (iteration 1, tool-computed by `mission-state.py review-finalize` / `closeout`; evidence: `.mission-state/archive/iter-1-8bda57df-scoring.json`, `.mission-state/archive/iter-1-8bda57df-reviews.json`):

| Gate value | Result |
|---|---|
| composite_score | 4.5 (threshold 4.0) |
| items | mission_achievement 5.0 / accuracy 5.0 / completeness 4.0 / usability 4.0 (min 4.0 ≥ 3.5) |
| open_high | 0 |
| max agreement delta | 0.0 (≤ 1.5) |
| reviewers | 3 spawned in parallel (A/B/C, `parallel_execution: true`); scoring perspectives: A. B and C were excluded from score aggregation by the validator ("same-score overall-impression note") but their findings/verification are archived. |
| M6 re-check | Reviewer C's Medium (missing confirmed/rejected section headings) and Low (R-3/R-4 ordering) were fixed inline and re-confirmed by Reviewer C differential re-review (updated review: all issues "修正確認済", findings: []). |

---

## Stop Decision

`closeout` (= mark-passes → next) returned `passes: true`, `next_action: report-complete` at iteration 1 (max-iter 3). Early-stop condition met: composite 4.5 ≥ 4.0 threshold and open_high == 0. Loop stopped after one scored review iteration.

---

## Evidence

### Confirmed Inconsistencies

#### C-1 — 2.32.0 zero-downtime claim vs. 6-minute outage

**release-notes.md (verbatim):**
> "## 2.32.0
> Bulk export API is now generally available. This release was deployed with
> zero downtime."

**ops-log.md (verbatim):**
> "| 2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC). |"

---

#### C-2 — MIG-2207 applied to prod but absent from migration-index

**ops-log.md (verbatim):**
> "| 2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables). |"

**migration-index.md (verbatim — complete entry table):**
> "| Migration | Version | Registered |
> |---|---|---|
> | MIG-2199 | 2.31.2 | 2026-05-27 |
> | MIG-2183 | 2.31.0 | 2026-05-13 |
> | MIG-2160 | 2.29.6 | 2026-04-29 |"

MIG-2207 does not appear. The document's stated policy:
> "All production schema changes MUST be registered here before deploy."

---

#### C-3 — fastcsv license: MIT claim vs. BUSL-1.1 relicensing

**release-notes.md (verbatim):**
> "Dependency notice: this product bundles fastcsv under the license recorded in
> the NOTICE file (MIT, unchanged since 2025)."

**support-tickets.md SUP-1204 (verbatim):**
> "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected.
> Escalated to legal; NOTICE file update pending."

**migration-index.md (verbatim — dependency manifest dated 2026-06-24):**
> "Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4."

fastcsv 1.8.3 ≥ 1.8.0, therefore the bundled version falls under BUSL-1.1, contradicting the MIT assertion in release-notes.

---

#### C-4 — CVE-2026-4417 declared fixed; shipped pin insufficient

**changelog.md (verbatim):**
> "## 2.31.2 — 2026-05-28
> - Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"

**support-tickets.md SUP-1188 (verbatim):**
> "Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."

**migration-index.md (verbatim):**
> "fastcsv 1.8.3"

fastcsv 1.8.3 < 1.9.0 required for remediation. The changelog's claim that the CVE is "fixed" is contradicted by the security engineering follow-up and the confirmed shipped version.

---

#### C-5 — 2.31.4 deployed to EU shard with no changelog entry

**ops-log.md (verbatim):**
> "| 2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression). |"

**support-tickets.md SUP-1189 (verbatim):**
> "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02.
> Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."

**changelog.md:** Contains no entry for version 2.31.4 (entries jump from 2.31.2 to 2.32.0). A production version was deployed to customers with no corresponding changelog disclosure.

---

#### C-6 — "Current GA" label on 2.32.1 vs. post-rollback fleet state

**release-notes.md (verbatim):**
> "## 2.32.1 (current GA)
> Export pagination hotfix. Recommended for all tenants."

**ops-log.md (verbatim):**
> "| 2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0. |"

The release notes present 2.32.1 as the current generally available release, but the ops log records that 2.32.1 was subsequently rolled back and the entire production fleet was pinned to 2.32.0. The "current GA" designation is no longer accurate as of 2026-06-25.

---

### Rejected Candidates

#### R-1 — EXP-380 preview vs. GA (REJECTED)

**release-notes.md 2.31.0 (verbatim):**
> "Includes the new audit export (EXP-380 preview)."

**changelog.md 2.32.0 (verbatim):**
> "Bulk export API GA (EXP-380)"

**Rejection reason:** This describes a standard preview → general-availability lifecycle across two consecutive releases. No factual contradiction exists; the feature was explicitly labeled preview in 2.31.0 and promoted to GA in 2.32.0.

---

#### R-2 — MIG-2199 registration date one day before deploy (REJECTED)

**migration-index.md (verbatim):**
> "| MIG-2199 | 2.31.2 | 2026-05-27 |"

**ops-log.md (verbatim):**
> "| 2026-05-28 | Deploy 2.31.2. Migration MIG-2199 applied. |"

**Rejection reason:** Registration on 2026-05-27 followed by deployment on 2026-05-28 is fully consistent with the migration-index rule: "All production schema changes MUST be registered here before deploy." This is the expected sequence, not a contradiction.

---

#### R-3 — 2.31.4 absent from migration-index (REJECTED)

**ops-log.md (verbatim):**
> "| 2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression). |"

**migration-index.md:** No entry for 2.31.4.

**Rejection reason:** The ops-log does not assert that a schema migration was applied during the 2.31.4 deployment. A code-only hotfix (CSV delimiter behaviour fix) requires no schema change. The absence from migration-index is expected for a schema-migration-free release. Compare with 2.32.0, where the ops-log explicitly states "Migration MIG-2207 applied to prod" — that explicit statement is absent for 2.31.4. This candidate is superseded by C-5 (changelog omission), which is the genuine process gap for 2.31.4.

---

#### R-4 — Version gap 2.29.6 → 2.31.0, no 2.30.x release (REJECTED)

**changelog.md (verbatim — the apparent gap):**
> "## 2.31.0 — 2026-05-14
> - New retention settings page (RET-201)
>
> ## 2.29.6 — 2026-04-30
> - Minor bug fixes"

No 2.30.x version appears in any of the five documents.

**Rejection reason — in-document explanation (changelog.md, verbatim):**
> "Note on version numbering: 2.30.x was never released. Version renumbering to
> align with the platform train was approved in RFC-77; the train jumped from
> 2.29.x directly to 2.31.x."

The gap is an approved, documented renumbering (RFC-77), not a missing or unrecorded release.

---

## Assumptions

- The migration-index.md claims to be the "authoritative list of applied schema migrations." It is treated as the ground truth for what migrations are officially registered, not necessarily what was operationally applied.
- The release-notes.md does not carry an explicit publication date. The "current GA" label (C-6) is assessed against the ops-log rollback event dated 2026-06-25, which post-dates the release-notes content; the inconsistency stands regardless of when the notes were drafted.
- "fastcsv 1.8.3" in the migration-index dependency manifest is taken as the pinned shipped version for 2.31.2+ releases in the absence of a version-specific dependency file.
- No network lookups were performed. All findings derive solely from the five fixture documents.
