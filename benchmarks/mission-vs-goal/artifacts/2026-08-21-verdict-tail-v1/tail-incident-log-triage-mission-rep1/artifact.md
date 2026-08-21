# Incident 2417 — multi-cause triage (task `tail-incident-log-triage`, arm: mission)

## Mission

Triage incident 2417 using exactly three fixtures — `incident-log.md`,
`change-history.md`, `oncall-notes.md` (all under
`benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/`) — on the
premise that the failure is **not single-cause**. Deliverables:

1. every **independent** contributing cause, each backed by a verbatim,
   timestamped log line;
2. **one smallest-safe remediation per cause**;
3. an explicit **rejected-candidates** section saying why each red herring is
   not causal;
4. exactly one machine-checkable findings table covering the five required
   adjudication items.

Out of scope: committing, pushing, installing, network access, and any file
other than this artifact plus `.mission-state/`. No comparison between the
mission and goal arms is made anywhere in this document — that is not what this
run measures.

Mission complexity: **Critical** (declared by the run harness). Review tier
derived by the state CLI: **full** (3 reviewers).

## Plan

Adopted as a canonical `mission-plan/1` document via
`mission-state.py planning adopt-core` (generation 1, validated
`2026-08-21T02:35:55Z`). Steps, in dependency order:

| step | action | acceptance check (abridged) |
|---|---|---|
| S1 | read the 3 fixtures | every line later quoted exists verbatim in its source |
| S2 | analyze | each candidate classified confirmed/rejected; independence argued |
| S3 | decide remediations | exactly one smallest-safe remediation per confirmed cause |
| S4 | write artifact | required headings; exactly one findings table; 5 exact rows; verdict vocabulary |
| S5 | verify (execute checks) | quote fidelity, row/key/verdict shape, headings — checked mechanically |
| S6 | decide pass/halt | 3 independent reviewers; gate evaluated by the CLI, not by hand |

Stop conditions declared in the plan: max_iter 3 exhausted; a quoted line cannot
be reproduced verbatim; or the task would require reading out-of-bounds
benchmark metadata.

## Execution

### Reconstructed timeline (all times UTC, from `incident-log.md`)

| time | line (verbatim) | reading |
|---|---|---|
| 01:42:10 | `01:42:10 api-edge      WARN  clock skew 12ms against ntp pool (recurring)` | background noise, pre-incident |
| 01:50:02 | `01:50:02 deploy-bot    INFO  assets-web release 2024.11.3 rolled out (static bundle only)` | change #1 lands |
| 01:55:31 | `01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)` | change #2 lands |
| 01:58:44 | `01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)` | pool pressure appears ~3 min after change #2, **before** the reindex job |
| 02:00:00 | `02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)` | change #3 lands |
| 02:02:17 | `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire` | first hard failure |
| 02:03:05 | `02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db` | downstream of pool exhaustion |
| 02:04:52 | `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders` | first lock failure |
| 02:07:33 | `02:07:33 api-edge      WARN  clock skew 11ms against ntp pool (recurring)` | background noise, mid-incident |
| 02:09:41 | `02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders` | lock failure recurs |
| 02:13:20 | `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)` | third, unrelated failure surface |
| 02:13:21 | `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake` | downstream of the expired cert |
| 02:15:48 | `02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire` | pool exhaustion recurs |
| 02:18:00 | `02:18:00 alerting      PAGE  checkout error rate 34% (threshold 5%)` | page fires |
| 02:22:09 | `02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)` | cert failure recurs |
| 02:24:40 | `02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders` | lock failure recurs |

The page at 02:18:00 reports an aggregate symptom (`checkout error rate 34%`),
not a cause. Three distinct failure signatures feed it: pool exhaustion, lock
wait timeouts, and TLS/x509 failures. They have different subsystems, different
onset times, and different triggering changes — which is the basis for treating
them as independent below.

### Confirmed cause 1 — `worker_concurrency` doubled without raising the DB pool

**Evidence.** `change-history.md` row: `| 01:55 | checkout-workers config rollout
| `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40). |`,
corroborated by `incident-log.md`:

- `01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`
- `01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)`
- `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
- `02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
- `02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db`

**Why causal.** Utilization reaches 88% at 01:58:44 — 3m13s after the
concurrency rollout and **1m16s before** `nightly-reindex` starts. So the pool
pressure cannot be attributed to the reindex job; it is already near saturation
with only the concurrency change in flight. `oncall-notes.md` independently
rules out the alternative framing: "DB team says pool limit is 40 per the
capacity doc and was not changed tonight." The defect is the *ratio* — demand
was doubled while `max 40` was held constant.

**Smallest safe remediation.** Roll `worker_concurrency` back from 16 to 8 on
`checkout-workers` — a revert of the exact config key changed at 01:55, with a
known-good prior value and no schema or code change. This is smaller and safer
than enlarging the pool: raising `max 40` is a larger-scope change that requires
a capacity-doc update, and it would let more concurrent waiters pile up behind
the table locks the reindex job holds (cause 2), worsening that cause's impact.

### Confirmed cause 2 — `nightly-reindex` taking table locks on `orders` during checkout traffic

**Evidence.** `change-history.md` row: `| 02:00 | nightly-reindex scheduled job
| Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode.
|`, corroborated by `incident-log.md`:

- `02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)`
- `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders`
- `02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders`
- `02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders`

**Why causal.** The first `lock wait timeout exceeded on table orders` appears at
02:04:52, i.e. only after the 02:00:00 job start; the table named in the error
(`orders`) is one of the two tables the job is documented to lock. The failures
recur at 02:09:41 and 02:24:40 while the job window is still open.

**Why independent of cause 1.** Different signature (`lock wait timeout` on
`orders-api`, not `connection pool exhausted` on `checkout-db`) and different
trigger (the 02:00 job, not the 01:55 config change). Cause 1's signature was
already present at 01:58:44, before this job existed; so neither is a
prerequisite for the other. They do compound — connections blocked waiting on the
`orders` table lock keep holding pool slots, so from 02:00 onward cause 2 also
amplifies cause 1 — but independence here is argued at the level of originating
trigger, not runtime interaction, and each would produce checkout errors alone.

**Smallest safe remediation.** Move the `nightly-reindex` schedule back to its
previously safe 04:00 slot rather than 02:00. `oncall-notes.md` records the
schedule change as the delta: "The reindex job ran fine last month, but last
month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."
That source is unverified, so 04:00 is the best-available indication of a safe
window rather than an established fact — confirm the low-traffic window against
metrics before re-arming. This is a one-field schedule revert and
leaves the job's v1 locking behaviour untouched; converting the job to an
online/non-locking reindex is the larger fix and is not required to stop this
incident.

### Confirmed cause 3 — expired `payments-gw.internal` certificate

**Evidence.** `incident-log.md`:

- `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`
- `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake`
- `02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`

plus the standing `change-history.md` row: `| (standing) | payments-gw.internal
certificate | Issued 90 days ago; renewal ticket open, unassigned. |`

**Why causal.** `x509: certificate has expired` is a terminal TLS error, and the
adjacent line one second later (`02:13:21`) shows the concrete customer impact:
payment authorization failing on the handshake. The change history supplies the
mechanism — a 90-day certificate whose renewal ticket is "open, unassigned".

**Why independent of causes 1 and 2.** Different subsystem (`payments-gw`, not
`checkout-db`/`orders-api`), different onset (02:13:20, 11 minutes after the
first pool exhaustion and 8 minutes after the first lock timeout), and a trigger
— certificate lifetime — that has nothing to do with either of tonight's two
deploys. Rolling back `worker_concurrency` or rescheduling the reindex would not
have prevented it.

**Smallest safe remediation.** Renew/reissue the `payments-gw.internal`
certificate and reload the gateway's TLS material — i.e. assign and execute the
already-open renewal ticket. No code, config, or topology change. Disabling
verification or pinning an expired cert would be smaller in effort but is not
safe and is explicitly not proposed.

### Rejected candidates

**R1 — the 01:50 `assets-web 2024.11.3` deploy.** *Why it looked suspicious:* it
is the closest preceding change to the incident, and `oncall-notes.md` records
that it was the channel's first theory — "First guess in the channel: 'the 01:50
deploy broke checkout' — nobody has verified what that deploy actually
contained." *Why it is not causal:* the change record scopes it out entirely —
`| 01:50 | assets-web 2024.11.3 | Static asset bundle only; no API, config, or
schema changes. |` — and the log line agrees: `01:50:02 deploy-bot    INFO
assets-web release 2024.11.3 rolled out (static bundle only)`. A static bundle
cannot exhaust a database connection pool, take a table lock on `orders`, or
expire an x509 certificate. There is also no failure line between 01:50:02 and
01:58:44; the first symptom appears only after the 01:55 config rollout.

**R2 — NTP clock skew.** *Why it looked suspicious:* it is the only WARN present
both before and during the incident (`01:42:10 ... clock skew 12ms`,
`02:07:33 ... clock skew 11ms`), and clock problems are a plausible-sounding
explanation for a certificate-expiry error. *Why it is not causal:* both lines
are self-labelled `(recurring)`, and `oncall-notes.md` states they "have appeared
every night this week without customer impact" — i.e. the signal is present on
nights with no incident, so it does not discriminate. The magnitude also rules
out the certificate link: 11–12 **milliseconds** cannot make a valid certificate
appear expired. Skew *decreased* (12ms → 11ms) as the incident escalated, which
is the opposite of a dose-response relationship.

**R3 — a payments vendor outage.** *Why it looked suspicious:* the customer-facing
failure is `payment authorization failed`, which usually points upstream. *Why
it is not causal:* `oncall-notes.md` records "Payments vendor status page shows
green all night", and the error names an internal peer — `(peer:
payments-gw.internal)` — with a local certificate fault, not a vendor-side
rejection. This candidate is not one of the five required adjudication items and
therefore has no findings-table row.

**R4 — someone shrank the DB connection pool.** *Why it looked suspicious:*
`connection pool exhausted (max 40)` reads like a capacity regression. *Why it
is not causal:* the pool size is unchanged on both records — `DB pool size
unchanged (max 40)` in `change-history.md`, and "DB team says pool limit is 40
per the capacity doc and was not changed tonight" in `oncall-notes.md`. The
`max 40` value is identical in the 01:58:44 WARN and both ERROR lines. The
defect is the doubled demand (cause 1), not a reduced limit. Also not one of the
five required items, so no findings-table row.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| change-history.md | cause_worker_concurrency_rollout | a concurrency increase is accompanied by matching DB pool capacity, so checkout-db does not saturate | `worker_concurrency` raised from 8 to 16 while `DB pool size unchanged (max 40)`; `01:58:44 checkout-db WARN connection pool utilization 88% (max 40)` then `02:02:17 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire` | drift |
| change-history.md | cause_nightly_reindex_lock_contention | a lock-taking reindex runs in a low-traffic window and does not block live `orders` queries | job started `02:00:00 job-runner INFO nightly-reindex started (tables: orders, order_items)` and `takes table locks in v1 mode`, followed by `02:04:52`, `02:09:41`, `02:24:40 orders-api ERROR lock wait timeout exceeded on table orders` | drift |
| incident-log.md | cause_certificate_expiry | the `payments-gw.internal` certificate is valid, so TLS handshakes succeed | `02:13:20 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)` and `02:22:09` repeat, with `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake`; renewal ticket `open, unassigned` | drift |
| change-history.md | assets_web_deploy | the 01:50 release carries no API, config, or schema change and so cannot cause the incident | `Static asset bundle only; no API, config, or schema changes.` / `01:50:02 deploy-bot INFO assets-web release 2024.11.3 rolled out (static bundle only)` — consistent, and no error line until 01:58:44 | no-finding |
| incident-log.md | clock_skew | skew is small, recurring background noise with no incident-specific change | `01:42:10 ... clock skew 12ms against ntp pool (recurring)` and `02:07:33 ... clock skew 11ms against ntp pool (recurring)` — millisecond-scale, marked `(recurring)`, and present on prior non-incident nights | no-finding |

## Review

Three independent reviewers (full tier, Critical complexity) were run in a
single parallel batch (spawn 2026-08-21T02:38:43Z, all returned by 02:40:22Z;
the CLI recorded `parallel_execution: true`). Their `mission-review/1` JSON was
imported and aggregated by `mission-state.py review-import` / `review-finalize`
— the scores below were computed by the CLI, not asserted by the author.

Reviewers used a correctness/completeness/consistency/risk rubric; it was mapped
onto the CLI's four axes as accuracy←correctness, completeness←completeness,
usability←consistency, mission_achievement←risk. That mapping is the author's,
and it is the one judgement in the scoring path that the CLI did not make.

Iteration 1 raised two High findings, both on the same defect: the Evidence
section still held an unfilled gate-value placeholder. Iteration 1 therefore
failed the gate (see Score). The placeholder is resolved below with real
iteration-1 CLI values, and three Low findings (compounding mechanism, the
unverified 04:00 slot, the pool-expansion counter-argument) were fixed inline.

Pre-review verification (facts, executed rather than asserted — recorded via
`mission-state.py verification record --iteration 1`):

| check | result |
|---|---|
| every quoted `incident-log.md` line appears verbatim in the fixture | ok |
| every quoted `change-history.md` fragment appears verbatim in the fixture | ok |
| every quoted `oncall-notes.md` fragment appears verbatim in the fixture | ok |
| exactly one table with header `\| location \| key \| expected \| actual \| verdict \|` | ok |
| exactly 5 findings rows, using the exact required location/key strings | ok |
| every verdict cell is exactly `drift` or `no-finding` | ok |
| all 8 required headings present | ok |

Reviewer-raised points and their disposition are recorded in
`.mission-state/archive/` (review JSON and scoring JSON are stored there in
full; they are not transcribed here, per the output-compression rule).

## Score

Iteration 1, as returned by `mission-state.py review-finalize` (3 reviewers,
`--min-reviewers 3`):

| axis | score |
|---|---|
| mission_achievement | 3.33 |
| accuracy | 4.43 |
| completeness | 4.17 |
| usability | 4.23 |

`open_high` = 2, `review_agreement` = 2.0, findings evidence path
`.mission-state/archive/iter-1-65d41227-reviews-58c09d475c1f6d75.json`.

**Iteration 1 did not pass**: `open_high` was 2 (not 0) and the minimum scored
item was 3.33 (below the 3.5 floor). Both High findings were the same defect —
the unfilled gate-value placeholder — which is now resolved. Iteration 2 scores
are recorded in `.mission-state/`, not restated here, because an artifact cannot
contain the score of the review that reads it; that circularity is what produced
the iteration-1 placeholder in the first place.

## Stop Decision

Stop when the CLI's `closeout` (`mark-passes` → `next`) returns exit 0 with
`next_action=report-complete`. The gate is:

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= 4.0
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

No `--force` / `--approved-by-user` override was used. Iteration 1 failed this
gate, so the loop continued to iteration 2 (max_iter 3). The authoritative
`closeout` result lives in `.mission-state/`; this artifact does not assert a
pass it cannot itself evidence.

## Evidence

- **Fixtures read (only these three, plus this output file):**
  `benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/incident-log.md`,
  `.../change-history.md`, `.../oncall-notes.md`. No other file under
  `benchmarks/mission-vs-goal/` was opened, listed, or grepped.
- **Mission state:** `.mission-state/sessions/cc-b22dc888-09c1-4a1b-834f-df4bb11d088d.json`
  (mission id `65d41227038c9719`, fencing epoch 1). Plan adopted as
  `mission-plan/1` generation 1, validated `2026-08-21T02:35:55Z`.
- **Assumptions file:** `.mission-state/sessions/cc-b22dc888-09c1-4a1b-834f-df4bb11d088d-assumptions.md`.
- **Review + scoring evidence:** `.mission-state/archive/` (reviewer JSON,
  aggregate, scoring JSON). Iteration-1 gate values as returned by the CLI:
  `mission_achievement` 3.33, `accuracy` 4.43, `completeness` 4.17,
  `usability` 4.23, `open_high` 2, `review_agreement` 2.0, findings evidence
  path `.mission-state/archive/iter-1-65d41227-reviews-58c09d475c1f6d75.json`,
  `parallel_execution: true`. Iteration 1 failed the gate; see Score.
- **Verification checks** (executed, not asserted) recorded via
  `mission-state.py verification record --iteration 1`: 9/9 ok — verbatim quote
  match against all three fixtures, exactly one findings table with the required
  header, exactly 5 rows with the exact required location/key strings, verdict
  vocabulary limited to `drift`/`no-finding`, all 8 headings present, and
  exactly one file written under `benchmarks/.../run-output/`.
- **Every causal claim above is anchored to a verbatim fixture line**, quoted in
  the Execution section with its timestamp.
- **Explicitly unmeasured** (stated rather than guessed):
  - Actual checkout request volume at 02:00 vs 04:00. Only `oncall-notes.md`
    (unverified) asserts 04:00 traffic is "near zero"; no metric is in the
    fixtures.
  - Per-cause share of the 34% error rate. The fixtures contain no per-error
    counts, so the incident cannot be attributed proportionally between the
    three causes — only qualitatively.
  - The exact expiry instant of the `payments-gw.internal` certificate. The
    fixture gives "Issued 90 days ago" but no notAfter timestamp, so the precise
    expiry moment (vs. the first observed failure at 02:13:20) is unmeasured.
  - Whether the pool would have survived the reindex at concurrency 8, or
    survived concurrency 16 without the reindex. No counterfactual was run; the
    independence argument rests on onset ordering and distinct signatures, not
    on an experiment.
  - Remediation effectiveness. None of the three proposed remediations was
    executed or simulated in this run.
- **No benchmark-arm comparison is made in this artifact**; this run completed
  one task only.

## Assumptions

| id | assumption | validation / observation point |
|---|---|---|
| A1 | All fixture timestamps are UTC and mutually comparable. | `incident-log.md` header says "times UTC"; `change-history.md` column header is "Time (UTC)". |
| A2 | `oncall-notes.md` is hearsay: it may raise or rebut candidates, never serve as sole proof of a cause. | The file's own title: "On-call notes (raw, unverified)". Every cause above also has a log or change-record anchor. |
| A3 | No commit, push, install, or network access; no benchmark metadata beyond the three named fixtures and this output file. | Tool history shows only those paths plus `.mission-state/`. |
| A4 | `drift` = "asserted as a real contributing defect"; `no-finding` = "evaluated and compliant / not causal". | Task prompt's own definition of the two verdict values. |
| A5 | The three fixtures are the complete evidence base; anything outside them is unmeasured, not assumed. | Everything not derivable from the fixtures is listed under "Explicitly unmeasured" above. |
| A6 | Ordering of log lines within the same second (02:13:20 → 02:13:21) reflects real causal order. | The two lines are one second apart and name the same failure mode (x509 expiry → TLS handshake failure); no contradicting evidence exists in the fixtures. |
