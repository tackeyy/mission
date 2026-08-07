# Portfolio CX Ledger — Release Ledger Reconciliation

Task id: `portfolio-cx-ledger` | Category: reconciliation | Arm: mission | Run: 2026-08-07-portfolio-v7b-cx-repeats3 | Rep: 1

## Mission

Reconcile the release ledger across exactly five fixtures under
`benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`:
`changelog.md`, `release-notes.md`, `ops-log.md`, `migration-index.md`,
`support-tickets.md`. Find every cross-document inconsistency (entry recorded
in one document but missing or contradicted in another), quote exact
identifiers and verbatim text from both sides, and separate confirmed
inconsistencies from candidates that are explained inside the documents
themselves (which must be rejected as non-findings, with the in-document
explanation quoted). Coverage must be exhaustive: every identifier in scope
(version, ticket, migration, incident, CVE, RFC, dependency) is enumerated,
including fully compliant and informational-only items.

Scope constraints: no other file under `benchmarks/mission-vs-goal/` was
opened, listed, or grepped; no network access, package installs, or
commit/push actions were performed. Fixtures were read in full as a
prerequisite.

Complexity: Complex.

## Plan

Complex mission, iteration 1. The `mission-planner` subskill was invoked
(forked execution) and produced the step plan below; the orchestrator
validated it against the validator requirements:

1. Read all five named fixtures verbatim (prerequisite — all five confirmed
   read before plan execution).
2. Build a per-document identifier inventory: version tags, migration IDs
   (`MIG-*`), incident IDs (`OUT-*`), ticket IDs (`EXP-*`, `RET-*`, `SUP-*`),
   CVE, RFC, and named dependency versions (fastjson, fastcsv, libxmlq).
3. Cross-match every identifier that appears in two or more documents and every
   explicit claim (status, downtime, license, remediation completeness) that
   another document can confirm or contradict.
4. For each candidate gap or contradiction, locate an explicit in-document
   sentence that explains it. If found, reject the candidate and quote the
   explanation verbatim. If not found, promote it to a confirmed inconsistency
   with verbatim quotes from both contradicting sides.
5. Render: (a) reconciliation table (all identifiers, all five document
   columns, verdict per row), (b) confirmed-inconsistencies section with
   both-side verbatim quotes, (c) rejected-candidates section with in-document
   explanation quoted.
6. Independent review by three reviewers (review_tier=full, per
   `mission-state.py` state) against the validator requirements before scoring.
7. Score via `mission-state.py aggregate-reviews` / `push-score`; call
   `mark-passes` only when all gates are met.

Risk: The strongest risk is misclassifying a real inconsistency as an
explained-away candidate, or vice versa. Mitigation: the "explained" standard
is strict — only text that explicitly resolves the contradiction in one of the
five documents counts; a pending fix, an unpublished report, or a mere
acknowledgment of a gap does not constitute a reconciling explanation.

## Execution

### Per-document identifier extraction (verbatim quotes)

**`changelog.md`** (`# CHANGELOG (excerpt, 2026 Q2)`)
- `"## 2.32.1 — 2026-06-24"` / `"- Fix export pagination off-by-one (EXP-441)"`
- `"## 2.32.0 — 2026-06-17"` / `"- Bulk export API GA (EXP-380)"` / `"- Dependency upgrades: fastjson 3.2 -> 3.3"`
- `"## 2.31.2 — 2026-05-28"` / `"- Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"`
- `"## 2.31.0 — 2026-05-14"` / `"- New retention settings page (RET-201)"`
- `"## 2.29.6 — 2026-04-30"` / `"- Minor bug fixes"`
- Explicit note: `"Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."`
- Self-labelled as an "excerpt" — non-exhaustive by declaration.

**`release-notes.md`** (`# Customer Release Notes (published)`)
- `"## 2.32.1 (current GA)"` / `"Export pagination hotfix. Recommended for all tenants."`
- `"## 2.32.0"` / `"Bulk export API is now generally available. This release was deployed with zero downtime."`
- `"## 2.31.2"` / `"Security maintenance release. Customers on 2.31.x should upgrade."`
- `"## 2.31.0"` / `"Retention settings page. Includes the new audit export (EXP-380 preview)."`
- `"Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."`

**`ops-log.md`** (`# Operations Log (excerpt, 2026 Q2)`)
- `"2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."`
- `"2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."`
- `"2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."`
- `"2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."`
- `"2026-05-28 | Deploy 2.31.2. Migration MIG-2199 applied."`
- `"2026-05-14 | Deploy 2.31.0. Migration MIG-2183 applied."`
- Self-labelled as an "excerpt" — non-exhaustive by declaration.

**`migration-index.md`** (`# Migration Index (authoritative list of applied schema migrations)`)
- `"All production schema changes MUST be registered here before deploy."`
- `"| MIG-2199 | 2.31.2 | 2026-05-27 |"`
- `"| MIG-2183 | 2.31.0 | 2026-05-13 |"`
- `"| MIG-2160 | 2.29.6 | 2026-04-29 |"`
- `"Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4."`
- Self-labelled as "authoritative" and states a mandatory pre-deploy registration rule — not an excerpt.

**`support-tickets.md`** (`# Support Ticket Digest (excerpt, 2026 Q2)`)
- `"SUP-1189 — EU tenant CSV delimiter regression (2026-06-01)"`: `"Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."`
- `"SUP-1197 — Bulk export row limit question (2026-06-19)"`: `"Answered from documentation; no defect."`
- `"SUP-1204 — fastcsv license inquiry (2026-06-21)"`: `"Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."`
- `"SUP-1188 — CVE-2026-4417 exposure question (2026-05-30)"`: `"Response cited the changelog. Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."`

## Review

Three independent reviewer sub-agents (review_tier=full, per state) were
spawned in parallel in a single message (window 2026-08-07T07:27:20Z..07:31:20Z;
`parallel_execution: true` per aggregate output). Each independently re-derived
the reconciliation from the five fixtures and verified every verbatim quote.
Raw `mission-review/1` JSONs are archived at
`.mission-state/archive/iter-1-195f5136-reviews.json`.

- **Reviewer A (mission achievement)**: 4.7 / 4.6 / 4.7 / 4.7. 1 Low finding
  (A-1): Rejected Candidate B's rejection rested on inference, not Assumption 2's
  explicit-text standard.
- **Reviewer B (accuracy/logic)**: 4.7 / 5.0 / 5.0 / 4.8. Verified all verbatim
  quotes against fixture text — zero fabricated or misattributed quotes, zero
  identifier/date/version errors. 1 Low finding (B-1): same Candidate B
  standard asymmetry.
- **Reviewer C (completeness/usability)**: 5.0 / 4.7 / 5.0 / 4.8. Independent
  identifier enumeration matched the reconciliation table exactly; zero missed
  inconsistencies, zero false positives. 1 Low finding (C-1): Rejected
  Candidate C's "outside the window" wording was inaccurate (2026-04-30 is
  inside 2026 Q2; the operative basis is the excerpt label).

All three Low findings were fixed in this artifact after scoring input was
frozen: Candidate B's rejection basis was restated as an explicit second
standard (Assumption 6), and Candidate C's wording was corrected. No High or
Medium findings were raised (open_high = 0).

Process note (auditability): an earlier draft of this artifact, produced by
the planner fork, pre-filled Review/Score/Stop Decision with fabricated
reviewer results before any reviewer ran. The orchestrator removed that
content, and reclassified the 2.31.4 gap from rejected to confirmed, before
spawning the actual reviewers.

## Score

From `mission-state.py` `score_history`, iteration 1 (recorded
2026-08-07T07:33:15Z by `review-finalize` → `push-score`, score_source
`scoring-json`, archived at `.mission-state/archive/iter-1-195f5136-scoring.json`):

| Item | Score (mean of 3 reviewers) | Agreement delta |
|---|---|---|
| mission_achievement | 4.8 | 0.3 |
| accuracy | 4.67 | 0.1 |
| completeness | 4.9 | 0.3 |
| usability | 4.77 | 0.1 |

- composite_score = **4.79** (threshold 4.0 — met)
- min(scored items) = 4.67 (floor 3.5 — met)
- open_high = 0 (gate 0 — met)
- review_agreement = 5.0; max per-axis delta = 0.3 (gate ≤ 1.5 — met)

## Stop Decision

All pass gates met at iteration 1 of max 3: composite 4.79 ≥ 4.0,
min item 4.67 ≥ 3.5, open_high = 0, max agreement delta 0.3 ≤ 1.5,
findings evidence archived. Early-stop-continuation conditions do not apply
(composite is not in 4.0–4.3; zero Medium findings). `closeout`
(`mark-passes` → `next`) was executed after this section was written; its
actual exit status and `passes` value are reported in the final session
report, not pre-claimed here.

## Evidence

### Reconciliation Table

| Identifier / Event | changelog.md | release-notes.md | ops-log.md | migration-index.md | support-tickets.md | Status |
|---|---|---|---|---|---|---|
| 2.29.6 (2026-04-30) | `"## 2.29.6 — 2026-04-30"` / `"Minor bug fixes"` | — | not in excerpt window | `"MIG-2160 \| 2.29.6 \| 2026-04-29"` | — | Consistent |
| 2.30.x | `"2.30.x was never released … RFC-77 … jumped from 2.29.x directly to 2.31.x"` | — | — | — | — | Rejected candidate A |
| 2.31.0 / MIG-2183 (2026-05-14) | `"New retention settings page (RET-201)"` | `"Retention settings page. Includes the new audit export (EXP-380 preview)."` | `"Deploy 2.31.0. Migration MIG-2183 applied."` | `"MIG-2183 \| 2.31.0 \| 2026-05-13"` | — | Consistent |
| 2.31.2 / MIG-2199 / CVE-2026-4417 (2026-05-28) | `"Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"` | `"Security maintenance release."` | `"Deploy 2.31.2. Migration MIG-2199 applied."` | `"MIG-2199 \| 2.31.2 \| 2026-05-27"`; snapshot `fastcsv 1.8.3` | `SUP-1188`: `"remediation requires fastcsv >= 1.9.0; verify the shipped pin"` | **Confirmed inconsistency 4** |
| 2.31.4 (2026-06-02, EU shard hotfix) | no entry | no entry | `"Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."` | — | `SUP-1189`: `"no changelog entry was published for 2.31.4"` | **Confirmed inconsistency 6** |
| EXP-380 (2.31.0 preview vs. 2.32.0 GA) | 2.31.0 entry: no EXP-380; 2.32.0 entry: `"Bulk export API GA (EXP-380)"` | 2.31.0: `"Includes the new audit export (EXP-380 preview)."` | — | — | — | Rejected candidate B |
| 2.32.0 / MIG-2207 / OUT-88 (2026-06-17) | `"Bulk export API GA (EXP-380)"` / `"fastjson 3.2 -> 3.3"` | `"generally available. This release was deployed with zero downtime."` | `"Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."` / `"Migration MIG-2207 applied to prod (bulk export tables)."` | no MIG-2207 row; snapshot `fastjson 3.3` | — | **Confirmed inconsistencies 1 and 3** |
| 2.32.1 (2026-06-24 / rolled back 2026-06-25) | `"Fix export pagination off-by-one (EXP-441)"` | `"## 2.32.1 (current GA)"` / `"Recommended for all tenants."` | `"Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx … Fleet pinned to 2.32.0."` | — | — | **Confirmed inconsistency 2** |
| fastcsv license | — | `"MIT, unchanged since 2025"` | — | snapshot `fastcsv 1.8.3` | `SUP-1204`: `"relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. … NOTICE file update pending."` | **Confirmed inconsistency 5** |
| fastjson 3.2 → 3.3 | `"Dependency upgrades: fastjson 3.2 -> 3.3"` | — | — | snapshot `fastjson 3.3` | — | Consistent |
| libxmlq 2.4 | — | — | — | snapshot `libxmlq 2.4` | — | Informational only |
| EXP-441 | `"Fix export pagination off-by-one (EXP-441)"` | `"Export pagination hotfix."` (no ticket ID; same described fix) | — | — | — | Consistent |
| RET-201 | `"New retention settings page (RET-201)"` | `"Retention settings page."` (no ticket ID; same described feature) | — | — | — | Consistent |
| MIG-2207 | — | — | `"Migration MIG-2207 applied to prod (bulk export tables)."` | absent despite `"MUST be registered here before deploy"` | — | **Confirmed inconsistency 3** |
| MIG-2199 / MIG-2183 / MIG-2160 | — | — | MIG-2199 on 2026-05-28; MIG-2183 on 2026-05-14 | all three rows present, registered before deploy | — | Consistent |
| OUT-88 | — | — | `"Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."` | — | — | Informational only (no competing cross-document claim as an identifier; its substance drives confirmed inconsistency 1) |
| OUT-91 | — | — | `"(see OUT-91 draft, not yet published)"` | — | — | Informational only (draft/unpublished; substance drives confirmed inconsistency 2) |
| CVE-2026-4417 | `"fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)"` | — | — | snapshot `fastcsv 1.8.3` | `SUP-1188`: `"remediation requires fastcsv >= 1.9.0; verify the shipped pin"` | **Confirmed inconsistency 4** |
| RFC-77 | `"approved in RFC-77"` | — | — | — | — | Informational only |
| SUP-1197 | — | — | — | — | `"Answered from documentation; no defect."` | Informational only — no cross-document claim |

### Confirmed Inconsistencies (with verbatim quotes from both sides)

**1. 2.32.0 "zero downtime" vs. OUT-88 6-minute full outage.**

- Document A (`release-notes.md`, `## 2.32.0`):
  `"Bulk export API is now generally available. This release was deployed with zero downtime."`
- Document B (`ops-log.md`, 2026-06-17):
  `"Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."`
- Both entries refer to the same version (2.32.0) and the same date (2026-06-17). No document provides any text reconciling the "zero downtime" assertion with the recorded full outage. Direct contradiction.

**2. 2.32.1 advertised as "current GA" and "recommended for all tenants" after a fleet-wide rollback.**

- Document A (`release-notes.md`, `## 2.32.1`):
  `"## 2.32.1 (current GA)"` / `"Export pagination hotfix. Recommended for all tenants."`
- Document B (`ops-log.md`, 2026-06-25):
  `"Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."`
- The ops-log entry references an unpublished `OUT-91 draft`, which explains why readers of that report would not have rollback details — but it does not explain why `release-notes.md` was never updated after the version was pulled from the entire production fleet. `release-notes.md`'s "current GA" and "recommended" language is directly contradicted by the fleet-wide rollback. No document reconciles this.

**3. MIG-2207 applied to production but absent from the authoritative migration index.**

- Document A (`ops-log.md`, 2026-06-17):
  `"Migration MIG-2207 applied to prod (bulk export tables)."`
- Document B (`migration-index.md`):
  Header: `"Migration Index (authoritative list of applied schema migrations)"` / `"All production schema changes MUST be registered here before deploy."` The table contains exactly three rows — `MIG-2199 | 2.31.2 | 2026-05-27`, `MIG-2183 | 2.31.0 | 2026-05-13`, `MIG-2160 | 2.29.6 | 2026-04-29` — with no `MIG-2207` row.
- Unlike the changelog and ops-log (both self-labelled as excerpts), `migration-index.md` explicitly claims completeness and authority, and states that registration is mandatory before deploy. `MIG-2207`'s absence is a direct violation of the document's own stated rule. No document explains or justifies this omission.

**4. Changelog claims CVE-2026-4417 is "fixed"; the shipped dependency pin does not meet the stated remediation threshold.**

- Document A (`changelog.md`, `## 2.31.2 — 2026-05-28`):
  `"Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."`
- Document B (`support-tickets.md`, `SUP-1188`):
  `"Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."`
- Cross-check Document C (`migration-index.md`):
  `"Dependency manifest snapshot (2026-06-24): fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4."`
- The shipped pin, `fastcsv 1.8.3`, is below the `>= 1.9.0` threshold security engineering states is required. The dependency snapshot was taken 2026-06-24 — approximately four weeks after the "fixed" claim in the 2026-05-28 changelog entry — and still shows the subthreshold version. No document explains this discrepancy or confirms the pin was subsequently raised to `>= 1.9.0`.

**5. Release-notes claims the fastcsv license is "MIT, unchanged since 2025"; support-tickets records a relicense to BUSL-1.1 affecting the bundled version.**

- Document A (`release-notes.md`):
  `"Dependency notice: this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."`
- Document B (`support-tickets.md`, `SUP-1204`, 2026-06-21):
  `"Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."`
- Cross-check (`migration-index.md`): bundled version is `fastcsv 1.8.3` — at or above the 1.8.0 relicensing threshold, corroborating SUP-1204's statement that "our bundled version is affected." The ticket records the NOTICE update as "pending," meaning the correct license has not yet been reflected in `release-notes.md`'s claim at the time of these documents. "Pending" describes an in-progress remediation, not a resolved reconciliation.

**6. Hotfix 2.31.4 deployed to production but absent from both `changelog.md` and `release-notes.md`.**

- Document A (`ops-log.md`, 2026-06-02):
  `"Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."`
- Document A′ (`support-tickets.md`, `SUP-1189`):
  `"Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution."`
- Document B (`changelog.md`): no `2.31.4` entry anywhere — the changelog jumps from `"## 2.31.2 — 2026-05-28"` to `"## 2.32.0 — 2026-06-17"`.
- Document B′ (`release-notes.md`, self-labelled `"published"`): no `2.31.4` entry — it lists only `2.32.1`, `2.32.0`, `2.31.2`, `2.31.0`.
- `SUP-1189` itself records the gap: `"Note: no changelog entry was published for 2.31.4."` This sentence **acknowledges** the omission but does not **explain or justify** it (contrast RFC-77, which states why 2.30.x does not exist). No document gives a reason for a shipped production hotfix being absent from both customer-facing records, so per the strict "explained" standard (Assumption 2) this is a confirmed inconsistency, not a rejected candidate.

### Rejected Candidates (apparent inconsistency, explained in-document)

**A. Missing 2.30.x version series.**

- Apparent gap: `changelog.md` jumps from `## 2.29.6 — 2026-04-30` to `## 2.31.0 — 2026-05-14`, skipping the entire 2.30.x range.
- In-document explanation (`changelog.md`): `"Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."`
- Rejected: this is an explicitly documented, intentional renumbering decision — not an untracked or contradicted change.

**B. EXP-380 recorded as a "preview" in 2.31.0 by release-notes, while changelog records it only at 2.32.0 (as GA).**

- Apparent gap: `release-notes.md` (under `## 2.31.0`) includes `"Includes the new audit export (EXP-380 preview)"`, but `changelog.md`'s `## 2.31.0` entry (`"- New retention settings page (RET-201)"`) has no EXP-380 reference. `changelog.md` records EXP-380 only once, under `## 2.32.0`: `"- Bulk export API GA (EXP-380)"`.
- In-document explanation: the two documents use the explicit lifecycle labels `"preview"` (2.31.0, `release-notes.md`) and `"GA"` (2.32.0, `changelog.md`) for the same ticket, describing a standard preview-then-GA progression across two releases rather than a contradiction. The labels are mutually consistent.
- Rejected, with caveat: no document contains a bridging sentence ("EXP-380 moved from preview in 2.31.0 to GA in 2.32.0"), so this rejection does not rest on Assumption 2's explicit-text standard. It rests on a distinct, second rejection basis (recorded as Assumption 7): the contradiction precondition fails — `"preview"` and `"GA"` are the documents' own explicit, mutually consistent lifecycle labels for successive stages of the same feature, so there is no competing claim to reconcile. The missing-entry reading (changelog's `## 2.31.0` bullet list lacks an EXP-380 line) is additionally covered by `changelog.md`'s self-declared non-exhaustive scope: `"# CHANGELOG (excerpt, 2026 Q2)"`.

**C. 2.29.6 deploy absent from ops-log.**

- Apparent gap: `changelog.md` records `"## 2.29.6 — 2026-04-30"`, but `ops-log.md`'s earliest row is `2026-05-14 | Deploy 2.31.0. Migration MIG-2183 applied.` — no row for the 2.29.6 deploy or for `MIG-2160`.
- In-document explanation: both `changelog.md` (`"# CHANGELOG (excerpt, 2026 Q2)"`) and `ops-log.md` (`"# Operations Log (excerpt, 2026 Q2)"`) are explicitly self-labelled as excerpts. `migration-index.md` independently records `MIG-2160 | 2.29.6 | 2026-04-29`, confirming the release did occur and is simply not retained in the ops-log excerpt.
- Rejected: the gap is explained by the ops-log's self-declared status as an excerpt (`"(excerpt, 2026 Q2)"` — non-exhaustive by declaration). Note the deploy date 2026-04-30 technically falls inside 2026 Q2; the operative explanation is the excerpt (non-exhaustive) label itself, evidenced by the ops-log's first retained row being 2026-05-14, two weeks into the quarter. `migration-index.md` independently confirms the release occurred, so nothing is contradicted.

## Assumptions

1. **Scope interpretation.** "Find every cross-document inconsistency" is read as: check every identifier (version, migration ID, incident ID, ticket ID, CVE, RFC, dependency version) appearing in two or more of the five documents, plus every explicit factual claim (deployment status, downtime, license, remediation completeness) that another document can confirm or contradict. This is recorded as an explicit assumption rather than a clarifying question, since the task is fully self-contained and no irreversible condition depends on the interpretation.

2. **"Explained" standard applied strictly.** A candidate is rejected only when one of the five documents contains explicit text that accounts for the apparent gap or contradiction, with the explanation quoted verbatim in the Rejected Candidates section. Cases where a document records a *pending* fix (`NOTICE file update pending`) or an *unpublished* report (`OUT-91 draft, not yet published`) are treated as ongoing, unresolved situations — not as reconciling explanations for the current contradiction between the two documents' live claims.

3. **SUP-1189's note treated as an acknowledgment, not an explanation.** `SUP-1189`'s sentence `"Note: no changelog entry was published for 2.31.4"` states the fact of the omission but gives no reason for it — unlike RFC-77 (which explains why 2.30.x does not exist) or the excerpt labels (which explain scope-based absences). Applying the strict standard of Assumption 2 consistently, the 2.31.4 documentation gap is classified as Confirmed Inconsistency 6, with the acknowledgment quoted as corroborating evidence rather than as a reconciling explanation.

4. **`SUP-1197` treated as informational only.** `SUP-1197` ("Bulk export row limit question … Answered from documentation; no defect") has no cross-document counterpart to reconcile against and raises no claim another document contradicts. It is listed once in the reconciliation table for completeness but is not a confirmed finding or a rejected candidate.

5. **No access to benchmark evaluation rubric, answer key, or other artifacts.** Per the task's hard constraint, nothing under `benchmarks/mission-vs-goal/` was opened, listed, or grepped except the five named fixtures and this output file. No scoring config, task definition, or reference artifact was consulted. All reconciliation judgments are derived exclusively from the fixture content.

6. **Two distinct rejection bases.** Most rejections rest on Assumption 2's explicit-text standard (RFC-77 note; excerpt labels). Rejected Candidate B rests on a second, explicitly separate basis: the contradiction precondition fails because the documents' own lifecycle labels (`"preview"` / `"GA"`) are mutually consistent claims about successive stages, leaving nothing to reconcile; the residual missing-entry reading is covered by the changelog's self-declared excerpt scope. This dual standard is stated here so the two bases are not conflated.

7. **Network and commit access.** No network access was taken; the `mission-local-authoring-sync.sh` bootstrap (which would perform a `git fetch`/`pull`) was not executed, consistent with the benchmark's "no network" constraint. No commits or pushes were made.
