# disc-release-ledger — Claude Code `/goal` command arm

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-discriminating-v3/disc-release-ledger-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Reconcile the release ledger across five documents (`changelog.md`,
`release-notes.md`, `ops-log.md`, `migration-index.md`, `support-tickets.md`)
under `benchmarks/mission-vs-goal/fixtures/discriminating/release-ledger/`.
Find every cross-document inconsistency, quote exact identifiers on both
sides, and separate confirmed findings from rejected (in-document-explained)
candidates.

## Result

Read all five fixtures in full. Identified **6 confirmed cross-document
inconsistencies** and **4 rejected candidates** (apparent inconsistencies
that are explained inside the documents themselves). Reconciliation table,
confirmed findings, and rejected candidates are below.

### Reconciliation table

| Identifier | Document A claim | Document B claim | Verdict |
|---|---|---|---|
| 2.32.0 deploy (2026-06-17) | release-notes.md: "This release was deployed with zero downtime." | ops-log.md: "2026-06-17 \| Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)." | **CONFIRMED inconsistency** |
| MIG-2207 | ops-log.md: "2026-06-17 \| Migration MIG-2207 applied to prod (bulk export tables)." | migration-index.md table lists only MIG-2199, MIG-2183, MIG-2160 — MIG-2207 absent, despite migration-index.md stating "All production schema changes MUST be registered here before deploy." | **CONFIRMED inconsistency** |
| 2.31.4 | ops-log.md: "2026-06-02 \| Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)." and support-tickets.md SUP-1189: "Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02." | changelog.md has no 2.31.4 entry (jumps 2.32.0 → 2.31.2); release-notes.md also has no 2.31.4 entry | **CONFIRMED inconsistency** (gap noted but not explained away — see rationale below) |
| fastcsv license | release-notes.md: "this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)." | support-tickets.md SUP-1204: "Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending." | **CONFIRMED inconsistency** |
| 2.32.1 GA status | release-notes.md: "## 2.32.1 (current GA) ... Recommended for all tenants." | ops-log.md: "2026-06-25 \| Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0." | **CONFIRMED inconsistency** |
| CVE-2026-4417 remediation | changelog.md 2.31.2: "Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)." | migration-index.md dependency snapshot (2026-06-24): "fastcsv 1.8.3"; support-tickets.md SUP-1188: "remediation requires fastcsv >= 1.9.0; verify the shipped pin." | **CONFIRMED inconsistency** |
| 2.30.x version gap | Apparent: version sequence jumps 2.29.6 → 2.31.0 (2.30.x missing) | changelog.md: "Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x." | **REJECTED** — explained in-document |
| EXP-380 preview vs GA | release-notes.md 2.31.0: "Includes the new audit export (EXP-380 preview)." | changelog.md 2.32.0: "Bulk export API GA (EXP-380)." | **REJECTED** — not a contradiction; documents themselves label the 2.31.0 mention as "preview" and the 2.32.0 mention as GA, i.e. a stated preview→GA progression, not conflicting claims |
| MIG-2199 registration vs deploy date | migration-index.md: "MIG-2199 \| 2.31.2 \| 2026-05-27" (registered) | ops-log.md: "2026-05-28 \| Deploy 2.31.2. Migration MIG-2199 applied." (deployed) | **REJECTED** — dates are consistent (registration on 05-27 precedes deploy on 05-28), matching migration-index.md's own rule that migrations must be registered before deploy |
| fastjson version | changelog.md 2.32.0: "Dependency upgrades: fastjson 3.2 -> 3.3" | migration-index.md dependency snapshot: "fastjson 3.3" | **Fully compliant** — no inconsistency |

## Evidence

### Confirmed inconsistencies (with quotes from both sides)

1. **2.32.0 "zero downtime" vs OUT-88 outage**
   - release-notes.md: `"Bulk export API is now generally available. This release was deployed with zero downtime."`
   - ops-log.md: `"2026-06-17 | Deploy 2.32.0. Incident OUT-88: 6-minute full outage during the schema migration window (18:04–18:10 UTC)."`
   - These directly contradict: the customer-facing release notes assert zero downtime for the same deploy that the internal ops log records as a 6-minute full outage.

2. **MIG-2207 not registered in migration-index.md**
   - ops-log.md: `"2026-06-17 | Migration MIG-2207 applied to prod (bulk export tables)."`
   - migration-index.md header: `"All production schema changes MUST be registered here before deploy."` — table rows are only `MIG-2199 | 2.31.2 | 2026-05-27`, `MIG-2183 | 2.31.0 | 2026-05-13`, `MIG-2160 | 2.29.6 | 2026-04-29`. `MIG-2207` does not appear.
   - This is a violation of migration-index.md's own stated registration requirement.

3. **2.31.4 hotfix omitted from changelog.md and release-notes.md**
   - ops-log.md: `"2026-06-02 | Hotfix deploy 2.31.4 to tenants on the EU shard only (CSV delimiter regression)."`
   - support-tickets.md SUP-1189: `"Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02. Customer confirmed resolution. Note: no changelog entry was published for 2.31.4."`
   - changelog.md's version list goes `2.32.1 → 2.32.0 → 2.31.2 → 2.31.0 → 2.29.6` with no 2.31.4 entry; release-notes.md likewise has no 2.31.4 section.
   - Note: SUP-1189 *states the fact* that no changelog entry exists but does not supply a substantive reason (e.g., an approved policy) for omitting a shipped, customer-facing hotfix from the changelog. Per the task rule, only inconsistencies with an in-document *explanation* are rejected; this one is only acknowledged, not explained, so it is kept as confirmed.

4. **fastcsv license: "MIT, unchanged since 2025" vs relicensed to BUSL-1.1**
   - release-notes.md: `"this product bundles fastcsv under the license recorded in the NOTICE file (MIT, unchanged since 2025)."`
   - support-tickets.md SUP-1204: `"Upstream fastcsv relicensed from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected. Escalated to legal; NOTICE file update pending."`
   - migration-index.md confirms the bundled version is `fastcsv 1.8.3`, i.e. at or above the 1.8.0 relicense threshold cited in SUP-1204 — reinforcing that the release-notes.md MIT claim is stale/incorrect for the actually-shipped version.

5. **2.32.1 "current GA" / "Recommended for all tenants" vs rolled back**
   - release-notes.md: `"## 2.32.1 (current GA) Export pagination hotfix. Recommended for all tenants."`
   - ops-log.md: `"2026-06-25 | Rollback executed: 2.32.1 rolled back on all production tenants after elevated 5xx (see OUT-91 draft, not yet published). Fleet pinned to 2.32.0."`
   - release-notes.md presents 2.32.1 as the current, recommended GA release; ops-log.md records that it was rolled back on all production tenants three days later (per the ops-log's own date ordering, 2026-06-25 is after the 2026-06-24 changelog.md entry for 2.32.1), with the fleet pinned back to 2.32.0. The customer-facing release notes do not reflect this rollback.

6. **CVE-2026-4417 "fixed" vs shipped fastcsv version below required remediation threshold**
   - changelog.md 2.31.2: `"Security: fixed CVE-2026-4417 in the CSV parsing path (upgrade fastcsv)."`
   - support-tickets.md SUP-1188: `"Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."`
   - migration-index.md dependency snapshot (2026-06-24, i.e. after the 2.31.2 "fixed" claim): `"fastcsv 1.8.3"`.
   - The changelog claims the CVE is fixed, but the actually-shipped dependency pin (`1.8.3`) is below the version security engineering says is required for full remediation (`>= 1.9.0`), per SUP-1188's own follow-up note.

### Rejected candidates (with in-document explanation cited)

1. **Version sequence gap (2.29.6 → 2.31.0, no 2.30.x)**
   - Looks suspicious because a whole minor version line appears skipped.
   - Rejected because changelog.md explains it directly: `"Note on version numbering: 2.30.x was never released. Version renumbering to align with the platform train was approved in RFC-77; the train jumped from 2.29.x directly to 2.31.x."`

2. **EXP-380 mentioned in both 2.31.0 (release-notes.md) and 2.32.0 (changelog.md)**
   - Looks suspicious as if the same feature were claimed released twice under different versions.
   - Rejected because release-notes.md itself labels the 2.31.0 mention as a preview — `"Includes the new audit export (EXP-380 preview)."` — and changelog.md labels the 2.32.0 mention as general availability — `"Bulk export API GA (EXP-380)"`. This is a stated preview-then-GA progression, not a conflicting claim.

3. **MIG-2199 registered 2026-05-27 but deployed 2026-05-28**
   - Looks suspicious as a date mismatch between migration-index.md and ops-log.md.
   - Rejected because the dates are in the order migration-index.md itself requires: registration (`2026-05-27`) precedes deploy (`2026-05-28`), consistent with `"All production schema changes MUST be registered here before deploy."` No contradiction — registration and deployment are simply different events on different dates by design.

4. **SUP-1188 "Response cited the changelog" as if confirming full remediation**
   - Looks suspicious as though support closed the loop endorsing changelog.md's "fixed" claim.
   - Rejected as a *standalone* candidate because support-tickets.md itself immediately qualifies it: `"Follow-up from security engineering: remediation requires fastcsv >= 1.9.0; verify the shipped pin."` The ticket does not claim full remediation is confirmed — it explicitly flags it needs verification. (This same tension is what drives confirmed finding #6 above, once cross-checked against migration-index.md's actual shipped version.)

### Full coverage — all items enumerated (including fully compliant)

- **changelog.md** entries: 2.32.1 (EXP-441), 2.32.0 (EXP-380, fastjson 3.2→3.3), 2.31.2 (CVE-2026-4417 fix), 2.31.0 (RET-201), 2.29.6, plus the 2.30.x/RFC-77 note. All enumerated above; all cross-checked.
- **release-notes.md** entries: 2.32.1 (current GA), 2.32.0 (zero downtime claim), 2.31.2 (security maintenance), 2.31.0 (EXP-380 preview), fastcsv NOTICE/MIT claim. All enumerated above; all cross-checked.
- **ops-log.md** rows: 2026-06-25 rollback/OUT-91, 2026-06-17 deploy 2.32.0/OUT-88, 2026-06-17 MIG-2207, 2026-06-02 hotfix 2.31.4, 2026-05-28 deploy 2.31.2/MIG-2199, 2026-05-14 deploy 2.31.0/MIG-2183. All enumerated above; all cross-checked.
- **migration-index.md** rows: MIG-2199/2.31.2, MIG-2183/2.31.0, MIG-2160/2.29.6, plus dependency snapshot (fastjson 3.3, fastcsv 1.8.3, libxmlq 2.4). MIG-2160/2.29.6 registered 2026-04-29, one day before changelog.md's 2.29.6 entry (2026-04-30) — consistent with register-before-deploy, no finding. `libxmlq 2.4` has no corresponding mention in any other document, so it cannot be cross-checked either way — **unmeasured** (not a finding, not confirmable as compliant or inconsistent from the given fixtures).
- **support-tickets.md** entries: SUP-1189 (2.31.4/EU CSV delimiter — matches ops-log.md exactly, fully compliant), SUP-1197 (bulk export row limit, "no defect" — consistent with EXP-380 GA already shipped in changelog.md 2.32.0), SUP-1204 (fastcsv relicense — drives finding #4), SUP-1188 (CVE remediation follow-up — drives finding #6). All enumerated above; all cross-checked.

## Assumptions

- Treated changelog.md's own in-document notes (the 2.30.x/RFC-77 note) as authoritative explanations, since the task instructs that inconsistencies explained inside the documents must be rejected.
- Treated migration-index.md's stated policy ("All production schema changes MUST be registered here before deploy") as the governing rule for judging the MIG-2207 omission as a real inconsistency rather than an acceptable gap.
- For the 2.31.4 changelog omission, treated SUP-1189's note ("no changelog entry was published for 2.31.4") as an *acknowledgment* of the gap rather than a substantive *explanation* for it, since no reason is given — this is a judgment call; a stricter reading could treat the explicit acknowledgment itself as sufficient to reject it as a non-finding, but the task defines rejection as requiring an "explanation," which is absent here.
- `libxmlq 2.4` in migration-index.md's dependency snapshot has no counterpart in any other fixture; this is explicitly noted as unmeasured/not cross-checkable rather than silently omitted.
- Did not access any file under `benchmarks/mission-vs-goal/` other than the five named fixtures and this output file, per task constraints.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), includes a reconciliation table (identifier / document A claim / document B claim / verdict), a confirmed-inconsistencies section with quoted evidence from both sides for each of the 6 confirmed findings, and a rejected-candidates section citing the in-document explanation for each of the 4 rejected candidates. No further edits are planned under this task; the `/goal` completion controller can verify satisfaction against this file.
