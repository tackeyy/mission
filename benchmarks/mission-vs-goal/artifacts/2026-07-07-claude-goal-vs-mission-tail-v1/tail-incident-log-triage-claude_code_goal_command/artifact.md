# Incident 2417 Triage

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-07-claude-goal-vs-mission-tail-v1/tail-incident-log-triage-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Triage incident 2417 using exactly the three named fixtures
(`incident-log.md`, `change-history.md`, `oncall-notes.md`). The failure is
not single-cause. Identify every independent contributing cause with
log-line evidence, propose the smallest safe remediation for each, and
explicitly reject candidate explanations the evidence does not support.

## Result

Incident 2417 (checkout error-rate page at `02:18:00`, 34% vs. a 5%
threshold) was caused by **three independent, concurrently-unfolding
contributing causes**, not one. Each has its own evidence trail and its own
remediation; fixing only one would have left the other two active.

### Confirmed cause 1 — checkout-db connection pool exhaustion from an unmatched concurrency increase

- **What happened:** `checkout-workers` `worker_concurrency` was raised from
  8 to 16 at `01:55:31` while the `checkout-db` connection pool ceiling
  stayed at 40. Pool utilization climbed to 88% within ~3 minutes and the
  pool exhausted twice.
- **Log evidence:**
  - `01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`
  - `01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)`
  - `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
  - `02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db`
  - `02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
- **Change-history evidence:** `| 01:55 | checkout-workers config rollout | worker_concurrency raised from 8 to 16; DB pool size unchanged (max 40). |`
- **Smallest safe remediation:** Revert `worker_concurrency` from 16 back to
  8 for `checkout-workers` (roll back the `01:55` config change). This
  restores the prior, already-safe demand/pool ratio without touching the
  DB pool ceiling or any other service.

### Confirmed cause 2 — nightly-reindex job moved into the active-traffic window, causing table lock contention on `orders`

- **What happened:** The nightly-reindex job (which takes table locks in
  "v1 mode" on `orders` and `order_items`) started at `02:00:00`. Per
  on-call notes, this same job ran without incident last month, but at
  `04:00`, when checkout traffic is near zero — tonight it ran two hours
  earlier, directly inside the active checkout window, and `orders-api`
  hit lock-wait timeouts three separate times over the next 25 minutes.
- **Log evidence:**
  - `02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)`
  - `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders`
  - `02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders`
  - `02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders`
- **Change-history evidence:** `| 02:00 | nightly-reindex scheduled job | Rebuilds indexes on orders and order_items; takes table locks in v1 mode. |`
- **On-call-notes evidence:** "The reindex job ran fine last month, but last
  month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near
  zero."
- **Smallest safe remediation:** Move the nightly-reindex job's schedule
  back to `04:00` (or skip/reschedule tonight's already-running instance).
  This restores the previously-safe low-traffic execution window without
  changing the reindex job's locking behavior or index logic.

### Confirmed cause 3 — expired internal TLS certificate on `payments-gw.internal` breaking payment authorization

- **What happened:** The `payments-gw.internal` certificate (issued 90 days
  ago, with an open but unassigned renewal ticket) expired, causing TLS
  handshake failures and payment authorization errors, independent of the
  DB/lock issues above.
- **Log evidence:**
  - `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`
  - `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake`
  - `02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`
- **Change-history evidence:** `| (standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned. |`
- **On-call-notes evidence (rules out an external cause):** "Payments vendor
  status page shows green all night" — confirming the failure is the
  internal certificate, not a vendor-side outage.
- **Smallest safe remediation:** Rotate/renew the `payments-gw.internal`
  certificate now (action the existing, unassigned renewal ticket
  immediately) rather than waiting for the standard renewal cycle. No code
  or config change is needed beyond issuing and deploying the new cert.

### How the three causes combine into the paged symptom

All three land in the same short window and each independently degrades
`checkout-api`'s success rate: pool exhaustion produces upstream timeouts,
lock waits stall order writes, and the expired cert blocks payment
authorization. Together they push the aggregate checkout error rate to 34%,
triggering `02:18:00 alerting PAGE checkout error rate 34% (threshold 5%)`.
None of the three, alone, is stated or shown in the fixtures to be
sufficient on its own to reach 34% — that composite claim is unmeasured
from these three fixtures.

## Evidence

All quotes below are verbatim from the three named fixtures.

**`incident-log.md`** (fenced code block, `01:42:10`–`02:24:40` UTC):
```text
01:42:10 api-edge      WARN  clock skew 12ms against ntp pool (recurring)
01:50:02 deploy-bot    INFO  assets-web release 2024.11.3 rolled out (static bundle only)
01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)
01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)
02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)
02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire
02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db
02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders
02:07:33 api-edge      WARN  clock skew 11ms against ntp pool (recurring)
02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders
02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)
02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake
02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire
02:18:00 alerting      PAGE  checkout error rate 34% (threshold 5%)
02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)
02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders
```

**`change-history.md`** (table rows):
- `| 01:50 | assets-web 2024.11.3 | Static asset bundle only; no API, config, or schema changes. |`
- `| 01:55 | checkout-workers config rollout | worker_concurrency raised from 8 to 16; DB pool size unchanged (max 40). |`
- `| 02:00 | nightly-reindex scheduled job | Rebuilds indexes on orders and order_items; takes table locks in v1 mode. |`
- `| (standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned. |`

**`oncall-notes.md`** (raw, unverified notes):
- "Pager fired at 02:18 for checkout error rate."
- "First guess in the channel: 'the 01:50 deploy broke checkout' — nobody has verified what that deploy actually contained."
- "Someone also pointed at the clock skew warnings; note they have appeared every night this week without customer impact."
- "DB team says pool limit is 40 per the capacity doc and was not changed tonight."
- "The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."
- "Payments vendor status page shows green all night."

## Rejected candidates

- **"The `01:50` deploy broke checkout" (assets-web 2024.11.3).**
  Suspicious because it's the most recent deploy before the page and was
  the on-call team's first guess. **Rejected** because `change-history.md`
  states plainly: "Static asset bundle only; no API, config, or schema
  changes." A static-asset-only deploy has no mechanism to cause DB pool
  exhaustion, table lock waits, or TLS certificate expiry. Oncall notes
  also flag this guess as unverified speculation, not confirmed root
  cause.

- **Clock skew warnings (`api-edge`, 11–12ms).**
  Suspicious because they appear twice in the log window bracketing the
  incident (`01:42:10` and `02:07:33`) and read as anomalies. **Rejected**
  because `oncall-notes.md` states they "have appeared every night this
  week without customer impact" — i.e., this is a pre-existing, benign,
  recurring condition with no established correlation to checkout errors,
  not a new event introduced around the incident.

- **DB connection pool ceiling misconfigured/lowered.**
  Suspicious because `checkout-db` pool-exhaustion errors are the first
  hard errors in the log. **Rejected as a standalone cause** because both
  the log (`max 40` in every pool message) and `oncall-notes.md` ("DB team
  says pool limit is 40 per the capacity doc and was not changed tonight")
  confirm the pool ceiling itself was never changed. The pool exhaustion is
  real, but its cause is the demand-side `worker_concurrency` change
  (Confirmed cause 1), not a pool-config regression.

- **Payments vendor-side outage.**
  Suspicious because `payments-gw` TLS/auth errors look like a third-party
  dependency failure. **Rejected** because `oncall-notes.md` states:
  "Payments vendor status page shows green all night," and the log's own
  error text ("x509: certificate has expired") identifies an internal
  certificate problem, not a vendor incident.

## Assumptions

- All timestamps in `incident-log.md`, `change-history.md`, and
  `oncall-notes.md` are already in UTC and refer to the same incident
  window on the same calendar date; no timezone conversion was applied.
- `change-history.md`'s `02:00` entry for the nightly-reindex job is read as
  describing tonight's run (matching the `02:00:00 job-runner` log line),
  and the on-call note about last month's `04:00` run is read as evidence
  that the job's schedule shifted earlier, not as an unrelated job.
  This inference (schedule moved from 04:00 to 02:00) is not stated
  verbatim as a single fact in any one fixture — it is derived by
  combining the `change-history.md` row with the `oncall-notes.md` sentence.
  If that combination is wrong, cause 2 would need re-examination against
  a duty log for scheduler changes, which is not part of the three named
  fixtures and was not consulted.
- The exact quantitative contribution of each of the three confirmed causes
  to the 34% aggregate checkout error rate is **unmeasured** — the fixtures
  give error-type counts and timestamps but no per-cause error-rate
  breakdown, so the claim that all three contribute is qualitative
  (based on distinct, non-overlapping error signatures per cause), not a
  measured decomposition of the 34% figure.
- No fixture or system outside the three named files was opened, read, or
  used as evidence, per task constraints.

## Stop Condition

This artifact is complete: it exists at the required path, contains all
five required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), attributes the incident to three independent causes each with
quoted log-line evidence and timestamps, gives one smallest-safe
remediation per cause, and includes a rejected-candidates section stating
why each red herring is not causal. No further fixture reads, edits, or
network/network-adjacent actions are needed to satisfy the task prompt or
the `/goal` condition.
