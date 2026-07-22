# Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v1/disc-policy-exceptions-claude_code_goal_command.md`
and includes the headings Goal, Result, Evidence, Assumptions, and Stop
Condition.

Task: Audit six access exception requests (REQ-01 through REQ-06) against
the Data Access Exception Policy (v3), using the approver roster to check
role validity, and issue a compliant/violation verdict for every request
with cited policy section and roster/policy evidence. Requests that look
irregular but are permitted by an explicit policy clause must be marked
compliant with the permitting clause cited.

Only these three fixtures were read, exactly as instructed:
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

No other paths under `benchmarks/mission-vs-goal/` were opened, read,
grepped, or listed.

# Result

All six requests received a verdict. Summary:

| Request | Verdict | Policy Section(s) | Notes |
|---|---|---|---|
| REQ-01 | Compliant | §4.2 (Emergency clause) | Access preceded approval, but SEV-1 incident + filed within 24h + incident id referenced |
| REQ-02 | Violation | §2.1 | Approver's `data-steward` validity had expired before approval date |
| REQ-03 | Compliant | §3.1, §2.1, §4.1 | 2 datasets (at the explicit cap), valid approver, correct sequencing |
| REQ-04 | Compliant | §3.2 (Delegation clause) | Cross-team approval, explicitly permitted |
| REQ-05 | Violation | §3.1 | 3 datasets in one request, exceeds the 2-dataset cap |
| REQ-06 | Violation | §4.3 | Access began before approval, no SEV-1 incident declared — retroactive approval forbidden |

3 compliant (1 fully clean, 2 compliant-but-suspicious via explicit
clauses), 3 violations.

# Evidence

## Policy sections used (quoted from `access-policy.md`)

- §2.1: "An exception request MUST be approved by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster."
- §3.1: "A single exception request MAY grant access to at most two
  datasets. Broader access requires separate requests per dataset pair."
- §3.2: "a `data-steward` MAY approve requests originating from any team,
  not only their own. Cross-team approval is explicitly permitted."
- §4.1: "Approval MUST precede access."
- §4.2: "during a declared SEV-1 incident, access MAY begin before
  approval, provided the request is filed within 24 hours of access and
  references the incident id. Such requests are compliant."
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is
  forbidden. A request filed after access has begun, without a qualifying
  incident reference, is a violation regardless of later approval."

## Roster data used (quoted from `approver-roster.md`)

- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "T. Okafor | data-steward | 2026-06-10 — present"
- "Role validity ends on the date listed; approvals dated after the
  validity end are not covered by the role."

## Per-request analysis

**REQ-01** — quoted: "Access began 2026-06-03 02:10 during incident
SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018;
approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Access (2026-06-03 02:10) preceded approval (2026-06-03) — on its face
  this looks like a §4.1 violation.
- However, §4.2 permits exactly this: a declared incident (`SEV1-2026-018`
  is named), the request was filed the same day (well within 24 hours of
  access), and it references the incident id.
- Approver R. Duval is `data-steward` "2026-02-01 — present", valid on
  2026-06-03.
- Dataset count: 1 (payments-raw), within the §3.1 cap of 2.
- **Verdict: Compliant — permitted by §4.2 emergency clause.**

**REQ-02** — quoted: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06.
Datasets: user-profiles. Access began 2026-06-07."
- Sequencing is correct: approval (2026-06-06) precedes access
  (2026-06-07), satisfying §4.1.
- Dataset count: 1, within the §3.1 cap.
- Approver check: roster lists "K. Ilyes | data-steward | 2026-01-15 —
  2026-05-31." The approval date (2026-06-06) is after the validity end
  (2026-05-31). Per the roster note, "approvals dated after the validity
  end are not covered by the role."
- **Verdict: Violation — §2.1. K. Ilyes was not a `data-steward` at the
  time of approval (role validity ended 2026-05-31, approval dated
  2026-06-06).**

**REQ-03** — quoted: "Filed 2026-06-09; approved by M. Sato 2026-06-09.
Datasets: churn-model, support-transcripts. Access began 2026-06-10."
- Sequencing correct: approval (2026-06-09) precedes access (2026-06-10).
- Dataset count: 2 (churn-model, support-transcripts) — exactly at the
  §3.1 cap of "at most two," which is explicitly allowed, not exceeded.
- Approver M. Sato is `data-steward` "2025-11-01 — present," valid on
  2026-06-09.
- **Verdict: Compliant.** (Fully compliant; the 2-dataset count is at the
  ceiling but not suspicious since §3.1 explicitly allows exactly two.)

**REQ-04** — quoted: "Filed by the growth team 2026-06-12; approved by
R. Duval (platform team) 2026-06-12. Datasets: campaign-events. Access
began 2026-06-13."
- On its face, a platform-team approver signing off on a growth-team
  request looks like an authority mismatch.
- §3.2 explicitly permits this: "a `data-steward` MAY approve requests
  originating from any team, not only their own. Cross-team approval is
  explicitly permitted."
- R. Duval is `data-steward` "2026-02-01 — present," valid on 2026-06-12.
- Sequencing correct: approval (2026-06-12) precedes access (2026-06-13).
- Dataset count: 1, within cap.
- **Verdict: Compliant — permitted by §3.2 delegation clause.**

**REQ-05** — quoted: "Filed 2026-06-16; approved by T. Okafor 2026-06-16.
Datasets: payments-raw, user-profiles, campaign-events. Access began
2026-06-17."
- Dataset count: 3 (payments-raw, user-profiles, campaign-events).
- §3.1: "A single exception request MAY grant access to at most two
  datasets. Broader access requires separate requests per dataset pair."
  Three datasets in one request exceeds this cap.
- Approver T. Okafor is `data-steward` "2026-06-10 — present," valid on
  2026-06-16 — this part is fine, and sequencing (approval precedes
  access) is also fine.
- **Verdict: Violation — §3.1. Request grants 3 datasets in a single
  request, exceeding the 2-dataset cap; no clause permits broader scope
  in one request.**

**REQ-06** — quoted: "Access began 2026-06-19 (no incident declared);
request filed 2026-06-20; approved by M. Sato 2026-06-21. Datasets:
support-transcripts."
- Access (2026-06-19) began before the request was even filed (2026-06-20)
  and well before approval (2026-06-21).
- No incident is declared ("no incident declared" is stated explicitly in
  the fixture), so the §4.2 emergency clause does not apply.
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is
  forbidden. A request filed after access has begun, without a qualifying
  incident reference, is a violation regardless of later approval."
- M. Sato's role validity ("2025-11-01 — present") does cover the
  2026-06-21 approval date, but this is irrelevant — §4.3 makes the
  violation unconditional on later approval.
- **Verdict: Violation — §4.3. Access began before filing/approval with
  no declared incident; retroactive approval does not cure this.**

## Violations (confirmed)

1. **REQ-02** — §2.1 violation. Roster evidence: "K. Ilyes | data-steward
   | 2026-01-15 — 2026-05-31" combined with "approvals dated after the
   validity end are not covered by the role." Approval was dated
   2026-06-06, six days after validity ended.
2. **REQ-05** — §3.1 violation. Fixture evidence: "Datasets: payments-raw,
   user-profiles, campaign-events" (three datasets) against policy text
   "at most two datasets."
3. **REQ-06** — §4.3 violation. Fixture evidence: "Access began 2026-06-19
   (no incident declared); request filed 2026-06-20" against policy text
   "A request filed after access has begun, without a qualifying incident
   reference, is a violation regardless of later approval."

## Compliant but suspicious (permitted by explicit clause)

1. **REQ-01** — Looks irregular because access (2026-06-03 02:10) began
   before approval (2026-06-03), which would normally violate §4.1.
   Permitted by the explicit emergency clause, §4.2: "during a declared
   SEV-1 incident, access MAY begin before approval, provided the request
   is filed within 24 hours of access and references the incident id."
   The fixture confirms all three conditions: incident `SEV1-2026-018`
   declared, filed same day (well under 24h), and the request text
   references `SEV1-2026-018`.
2. **REQ-04** — Looks irregular because the approver (R. Duval, platform
   team) is from a different team than the requester (growth team).
   Permitted by the explicit delegation clause, §3.2: "a `data-steward`
   MAY approve requests originating from any team, not only their own.
   Cross-team approval is explicitly permitted."

## Rejected candidates (looked suspicious, not real findings)

1. **REQ-03 dataset count (2 datasets)** — Initially flagged as a
   possible §3.1 concern since it grants access to two datasets at once.
   Rejected: §3.1 says "at most two datasets," so 2 is exactly the
   permitted ceiling, not an excess. No violation.
2. **REQ-01 as a §4.1 timing violation** — Initially flagged because
   access clearly preceded approval. Rejected as a violation once §4.2's
   emergency clause was checked: it is compliant, not a violation, and is
   reported instead under "Compliant but suspicious" above.
3. **REQ-04 as a §2.1/authority violation** — Initially flagged because
   the approver's team (platform) didn't match the requester's team
   (growth), which could look like an out-of-scope approval. Rejected
   because §3.2 explicitly authorizes cross-team approval by any
   `data-steward`; R. Duval held that role on the approval date.
   Reclassified as "Compliant but suspicious" above.
4. **REQ-06 approver validity (M. Sato)** — Initially considered whether
   the violation stemmed from M. Sato's `data-steward` status. Rejected:
   M. Sato's roster entry ("2025-11-01 — present") covers the 2026-06-21
   approval date, so the approver was valid. The real and sufficient
   defect is the §4.3 timing violation (access before filing, no
   incident), independent of approver validity.
5. **REQ-02 dataset count** — Initially checked whether "user-profiles"
   alone might also trip §3.1. Rejected: 1 dataset is well within the cap;
   the only defect in REQ-02 is the expired approver validity (§2.1).

# Assumptions

- Dates given without a time (e.g. "Filed 2026-06-05") are treated as
  same-day events for sequencing purposes; a same-day filed/approved/
  access-began ordering as stated in the fixture text is treated as the
  authoritative sequence (i.e., the order the fixture lists is the actual
  chronological order) since no finer-grained timestamps were given for
  most requests. REQ-01 and REQ-06 include explicit times/qualifiers
  ("02:10", "no incident declared") which were used directly.
- "at the time of approval" (§2.1) is evaluated against the roster's
  `Validity` column using the approval date recorded in each request; role
  start/end boundaries are treated as inclusive (e.g., an approval dated
  exactly on the end date would be covered — this did not affect any of
  the six requests since REQ-02's approval date (2026-06-06) falls six
  days after K. Ilyes's end date (2026-05-31), not on a boundary).
- "at most two datasets" (§3.1) is read as a hard cap per single request;
  REQ-03's 2 datasets are treated as compliant (at the cap) and REQ-05's
  3 datasets as a violation (over the cap).
- No fixture content outside the three named files was consulted; nothing
  about scoring, answer keys, or other task definitions was read or
  inferred.
- Nothing in this task was unmeasured — all six verdicts rely on facts
  directly stated in the three fixtures quoted above.

# Stop Condition

This artifact satisfies the stated stop condition: it exists at the
required path, includes all five required headings (Goal, Result,
Evidence, Assumptions, Stop Condition), provides a verdict for all six
requests (REQ-01 through REQ-06) with policy section and roster/policy
evidence, includes a Violations section with quoted roster/policy
evidence, and includes a Compliant-but-suspicious section citing the
permitting clause for each case. No commits, pushes, package installs, or
network access were performed. Only the three named fixtures and this
output file were read/written; no other path under
`benchmarks/mission-vs-goal/` was opened, grepped, or listed.
