# Incident 2417 Triage

## Goal

Triage incident 2417 using exactly the fixtures `incident-log.md`, `change-history.md`, and `oncall-notes.md` (path: `benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/`). The failure is not single-cause: identify every independent contributing cause with log-line evidence, propose the smallest safe remediation for each, and explicitly reject candidate explanations the evidence does not support.

## Result

Three independent, causally-supported contributing causes were identified. Each maps to a distinct symptom stream in the log and is corroborated by `change-history.md` and/or `oncall-notes.md`. Four candidate explanations raised in the on-call channel were checked against the evidence and rejected (see Evidence section Rejected Candidates below).

### Confirmed cause 1 - checkout-workers concurrency doubled without a matching DB pool increase -> connection pool exhaustion

- **What changed:** `01:55:31 config-svc INFO rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`, confirmed by change-history row `01:55 | checkout-workers config rollout | worker_concurrency raised from 8 to 16; DB pool size unchanged (max 40).`
- **Effect:** `01:58:44 checkout-db WARN connection pool utilization 88% (max 40)` immediately after the rollout, followed by `02:02:17 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire`, and a repeat at `02:15:48 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire`. Downstream: `02:03:05 checkout-api ERROR upstream timeout talking to checkout-db`.
- **Smallest safe remediation:** Roll back `worker_concurrency` from 16 to 8 on `checkout-workers` (revert the 01:55 config change) to restore the ratio of workers to the unchanged 40-connection pool max. This is a config revert, not a pool resize, so it carries no schema/infra risk.

### Confirmed cause 2 - nightly-reindex scheduled at 02:00 instead of its usual 04:00, taking table locks on orders during live traffic

- **What changed:** `02:00:00 job-runner INFO nightly-reindex started (tables: orders, order_items)`, confirmed by change-history row `02:00 | nightly-reindex scheduled job | Rebuilds indexes on orders and order_items; takes table locks in v1 mode.` On-call notes state: `The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero.`
- **Effect:** `02:04:52 orders-api ERROR lock wait timeout exceeded on table orders`, repeated at `02:09:41 orders-api ERROR lock wait timeout exceeded on table orders` and again at `02:24:40 orders-api ERROR lock wait timeout exceeded on table orders` - three separate lock-wait failures on the same table the reindex job locks, spanning the whole incident window.
- **Smallest safe remediation:** Reschedule `nightly-reindex` back to 04:00 (its previously-safe low-traffic slot) rather than changing its locking mode. This is a schedule-only change; it defers work rather than altering reindex behavior.

### Confirmed cause 3 - payments-gw.internal TLS certificate expired (standing, pre-existing issue)

- **What changed:** No log-visible "change" - this is a standing condition. Change-history row: `(standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned.`
- **Effect:** `02:13:20 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)`, immediately followed by `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake`, and a repeat at `02:22:09 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)`. This is an independent failure mode (TLS/auth) distinct from both the pool-exhaustion and lock-wait error streams - different service (`payments-gw`), different error class (`x509`/TLS handshake vs. connection-pool/lock-wait).
- **Smallest safe remediation:** Rotate/renew the `payments-gw.internal` certificate by actioning the already-open renewal ticket (assign it and execute the renewal). No code or config change beyond the cert artifact itself.

### Composite effect

`02:18:00 alerting PAGE checkout error rate 34% (threshold 5%)` fires after all three failure streams (DB pool exhaustion, orders lock waits, payment TLS failures) are already active (first errors at 02:02:17, 02:04:52, and 02:13:20 respectively), consistent with three concurrently-contributing causes rather than one.

## Evidence

### Timeline (all times UTC, quoted verbatim from incident-log.md)

```
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

### Corroborating change-history rows (from change-history.md)

| Time (UTC) | Change | Scope |
|---|---|---|
| 01:50 | assets-web 2024.11.3 | Static asset bundle only; no API, config, or schema changes. |
| 01:55 | checkout-workers config rollout | worker_concurrency raised from 8 to 16; DB pool size unchanged (max 40). |
| 02:00 | nightly-reindex scheduled job | Rebuilds indexes on orders and order_items; takes table locks in v1 mode. |
| (standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned. |

### Corroborating on-call notes (from oncall-notes.md)

- "Someone also pointed at the clock skew warnings; note they have appeared every night this week without customer impact."
- "DB team says pool limit is 40 per the capacity doc and was not changed tonight."
- "The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."
- "Payments vendor status page shows green all night."

### Rejected Candidates

1. **01:50 assets-web deploy ("the 01:50 deploy broke checkout")** - This is the on-call channel's first guess (oncall-notes.md: "First guess in the channel: \"the 01:50 deploy broke checkout\" - nobody has verified what that deploy actually contained."). Rejected because change-history.md explicitly scopes it: "Static asset bundle only; no API, config, or schema changes." A static-bundle-only deploy cannot cause backend connection-pool exhaustion, table lock waits, or TLS certificate errors - none of the affected services (checkout-db, orders-api, payments-gw) are in its blast radius per the documented scope. It was suspicious only because it was the first event chronologically and closest in time to the on-call team's attention.

2. **Recurring clock skew warnings on api-edge** - Appears twice in the log (01:42:10 and 02:07:33, both WARN ... (recurring)). Rejected because oncall-notes.md states these "have appeared every night this week without customer impact", i.e., they are a known baseline noise pattern, not correlated with the incident's onset or its specific error classes (pool exhaustion, lock waits, TLS). No log line shows api-edge errors or any causal link from clock skew to the observed failures. It looked suspicious only because it's a WARN-level anomaly appearing near the incident window.

3. **DB connection pool limit being lowered/misconfigured** - The checkout-db errors ("pool exhausted", "max 40") could suggest the pool cap itself was reduced. Rejected because oncall-notes.md confirms: "DB team says pool limit is 40 per the capacity doc and was not changed tonight." The pool cap was not the change; the change was the demand side (worker concurrency 8 to 16, see Confirmed cause 1). This is a genuine contributing mechanism but the "pool was misconfigured/reduced" framing specifically is not supported - the cap was always 40 and unchanged.

4. **Payments vendor outage as the cause of TLS/payment failures** - Given payments-gw ERROR x509: certificate has expired and checkout-api ERROR payment authorization failed: TLS handshake, a vendor-side outage is a plausible alternate explanation. Rejected because oncall-notes.md states: "Payments vendor status page shows green all night." The failure is specifically an x509: certificate has expired error, which is a local/internal certificate lifecycle failure (matching the standing, unassigned renewal ticket in change-history.md), not a vendor availability issue.

## Assumptions

- The three fixture files are the complete and only evidence base for this triage, per task instructions; no other logs, dashboards, or metrics were consulted (none were provided).
- Log timestamps in incident-log.md and change-history times in change-history.md are in the same UTC clock and are trustworthy as recorded (the clock-skew warnings are on api-edge only, at single-digit-millisecond magnitude, and are noted as a nightly-recurring baseline - this is judged immaterial to interpreting the minute-granularity timestamps used throughout this triage, but it is explicitly unmeasured whether any log-ingestion pipeline itself was affected by skew).
- "Independent contributing cause" is interpreted as: a distinct root condition with its own change/standing-state origin and its own directly-attributable error signature in the log, as opposed to a symptom of one of the other two causes. Cross-effects between the three causes (e.g., whether pool exhaustion indirectly worsened lock-wait duration) are not measured by the available fixtures and are not claimed.
- The relative severity/contribution-percentage of each cause to the 34% error rate is unmeasured - the fixtures give error events, not a breakdown of the error-rate composition, so no such split is claimed.
- "Smallest safe remediation" is scoped to immediate mitigation (revert/reschedule/rotate) rather than long-term structural fixes (e.g., pool auto-scaling, reindex online-mode migration, cert auto-renewal automation), since the task asks for the smallest safe fix per cause, not a long-term hardening plan.

## Stop Condition

This task artifact is complete: all three fixtures were read, three independent causes were each tied to quoted log-line evidence with timestamps and corroborating change-history/on-call-notes evidence, one smallest-safe remediation was proposed per cause, and four rejected candidates were documented with the specific evidence contradicting each. No further fixture reads or edits are needed to satisfy the task prompt and validator criteria.
