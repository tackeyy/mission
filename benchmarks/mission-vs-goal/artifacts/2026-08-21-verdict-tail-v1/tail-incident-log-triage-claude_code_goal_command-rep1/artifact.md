# Incident 2417 Triage — tail-incident-log-triage (arm: claude_code_goal_command)

## Goal

Produce this single benchmark artifact triaging incident 2417 using exactly the three named
fixtures (`incident-log.md`, `change-history.md`, `oncall-notes.md`), identifying every
independent contributing cause with quoted log-line evidence and timestamps, giving one
smallest-safe remediation per cause, explicitly rejecting unsupported candidate explanations,
and emitting one machine-checkable findings table with the required header and the five
required rows.

## Result

The failure is **not single-cause**. Three independent contributing causes are confirmed, and
three candidate explanations are rejected as non-causal.

**Confirmed independent causes**

1. **DB connection pool exhaustion caused by the `worker_concurrency` 8 → 16 rollout without a
   matching pool increase** (checkout path). Doubling worker concurrency at 01:55 against an
   unchanged pool of `max 40` drove utilization to 88% at 01:58:44 and to exhaustion at
   02:02:17, before the reindex job's lock contention appeared. Independent of causes 2 and 3.
2. **Table-lock contention from `nightly-reindex` running at 02:00 against live checkout
   traffic** (`orders` table). This produces a distinct error class — `lock wait timeout
   exceeded on table orders` — that pool exhaustion does not produce, and it recurs at
   02:24:40 after the incident is already underway. Independent of causes 1 and 3.
3. **Expired `payments-gw.internal` x509 certificate breaking payment authorization.** This
   fails the TLS handshake on the payments path and is entirely outside the checkout-db /
   reindex blast radius. Independent of causes 1 and 2.

Together these produce the 02:18 page: `checkout error rate 34% (threshold 5%)`.

**Rejected candidates**: the 01:50 `assets-web` deploy, the recurring NTP clock skew, and a
payments-vendor outage. See "Rejected candidates" below.

**Explicitly unmeasured**: this triage is a document-only reading of the three fixtures. No
metric system, database, TLS endpoint, or code path was queried or reproduced. The relative
contribution of each cause to the 34% error rate is **unmeasured** — the fixtures contain no
per-cause error breakdown. Whether the pool would have survived 16 workers absent the reindex
job (and vice versa) is also **unmeasured**; the claim of independence rests on the distinct
error classes and their timestamps, not on an experiment.

### Cause 1 — worker_concurrency rollout outran a fixed DB pool

Evidence (`incident-log.md`):

- `01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`
- `01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)`
- `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
- `02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db`
- `02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`

Corroboration (`change-history.md`): `| 01:55 | checkout-workers config rollout | `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40). |`

Corroboration (`oncall-notes.md`): `DB team says pool limit is 40 per the capacity doc and was not changed tonight.` — the pool did not shrink; the demand side doubled.

Timing argument: utilization reached 88% at **01:58:44**, i.e. **before** `nightly-reindex started`
at **02:00:00** and before the first lock-wait error at 02:04:52. The pool pressure therefore
predates and does not depend on the reindex job.

**Smallest safe remediation**: roll `worker_concurrency` back from 16 to 8 for
`checkout-workers` — reverting the single 01:55 config change restores the pre-incident
demand/pool ratio without touching database capacity, schema, or deploys.

### Cause 2 — nightly-reindex takes table locks on `orders` during live traffic

Evidence (`incident-log.md`):

- `02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)`
- `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders`
- `02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders`
- `02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders`

Corroboration (`change-history.md`): `| 02:00 | nightly-reindex scheduled job | Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode. |`

Corroboration (`oncall-notes.md`): `The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero.` — the job is unchanged; its **schedule** now overlaps live checkout traffic.

Independence argument: `lock wait timeout exceeded on table orders` is a lock-manager error
distinct from `connection pool exhausted`. It first appears at 02:04:52, exactly after the
02:00:00 job start, and recurs at 02:24:40. Removing the pool pressure would not remove a table
lock held by a v1-mode index rebuild.

**Smallest safe remediation**: move the `nightly-reindex` schedule back to 04:00 (its
previously-safe slot per the on-call note) — a schedule change only, with no job logic, index
definition, or locking-mode change.

### Cause 3 — expired payments-gw.internal certificate

Evidence (`incident-log.md`):

- `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`
- `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake`
- `02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`

Corroboration (`change-history.md`): `| (standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned. |` — a standing condition that matured into a failure, not a tonight-change.

Independence argument: the failure is in the TLS handshake to `payments-gw.internal` and is
time-triggered by certificate expiry, not by database load. It first appears at 02:13:20 —
roughly eleven minutes after the first pool exhaustion and thirteen after the reindex start —
and neither rolling back concurrency nor rescheduling the reindex would restore a valid
certificate.

**Smallest safe remediation**: renew/reissue the `payments-gw.internal` certificate and reload
the payments gateway's TLS material (i.e. action the already-open, unassigned renewal ticket) —
no change to trust policy, verification settings, or the payments code path.

## Rejected candidates

- **`assets-web` 2024.11.3 deploy at 01:50 broke checkout** — *why it looked suspicious*: it is
  the closest preceding change to the incident window and was the channel's first guess
  (`oncall-notes.md`: `First guess in the channel: "the 01:50 deploy broke checkout" — nobody has verified what that deploy actually contained.`).
  *Why it is not causal*: the deploy is scoped to static assets only —
  `01:50:02 deploy-bot    INFO  assets-web release 2024.11.3 rolled out (static bundle only)` and
  `change-history.md`: `Static asset bundle only; no API, config, or schema changes.` A static
  bundle cannot open DB connections, take table locks, or present a TLS certificate. No error
  line in the log references `assets-web` after 01:50:02.
- **NTP clock skew on `api-edge`** — *why it looked suspicious*: skew warnings bracket the
  incident (`01:42:10 api-edge      WARN  clock skew 12ms against ntp pool (recurring)` and
  `02:07:33 api-edge      WARN  clock skew 11ms against ntp pool (recurring)`), and clock skew is
  a plausible cause of certificate-validity errors, so it can be mistaken for the driver of
  cause 3. *Why it is not causal*: both lines are self-labelled `(recurring)` and
  `oncall-notes.md` records `note they have appeared every night this week without customer impact.`
  The magnitude is 11–12 **milliseconds**, which cannot make a certificate appear expired
  (expiry is a day-scale boundary), and the skew is on `api-edge` while the x509 error is
  reported by `payments-gw`. They are WARN-level and produced no corresponding error line.
- **Payments vendor outage** — *why it looked suspicious*: `checkout-api ERROR payment authorization failed: TLS handshake`
  at 02:13:21 is a payment-path failure and could read as a third-party incident. *Why it is not
  causal*: `oncall-notes.md` states `Payments vendor status page shows green all night.`, and the
  underlying error is local — `x509: certificate has expired (peer: payments-gw.internal)`, an
  `.internal` peer whose certificate is our own standing renewal ticket.

## Evidence

Every quoted line above is verbatim from the three named fixtures. Source map:

| Claim | Fixture | Quoted evidence (verbatim) | Timestamp |
|---|---|---|---|
| Concurrency doubled | incident-log.md | `rollout complete: worker_concurrency 8 -> 16 (checkout-workers)` | 01:55:31 |
| Pool pressure precedes reindex | incident-log.md | `connection pool utilization 88% (max 40)` | 01:58:44 |
| Pool exhausted | incident-log.md | `connection pool exhausted (max 40); rejecting acquire` | 02:02:17, 02:15:48 |
| Checkout impact from pool | incident-log.md | `upstream timeout talking to checkout-db` | 02:03:05 |
| Pool unchanged tonight | change-history.md | `` `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40). `` | 01:55 |
| Reindex started | incident-log.md | `nightly-reindex started (tables: orders, order_items)` | 02:00:00 |
| Lock contention | incident-log.md | `lock wait timeout exceeded on table orders` | 02:04:52, 02:09:41, 02:24:40 |
| Reindex takes locks | change-history.md | `Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode.` | 02:00 |
| Schedule moved into traffic | oncall-notes.md | `last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero` | n/a |
| Certificate expired | incident-log.md | `x509: certificate has expired (peer: payments-gw.internal)` | 02:13:20, 02:22:09 |
| Payment path broken | incident-log.md | `payment authorization failed: TLS handshake` | 02:13:21 |
| Cert renewal outstanding | change-history.md | `Issued 90 days ago; renewal ticket open, unassigned.` | (standing) |
| Deploy was static-only | incident-log.md / change-history.md | `assets-web release 2024.11.3 rolled out (static bundle only)` / `no API, config, or schema changes.` | 01:50:02 / 01:50 |
| Skew is benign background | incident-log.md / oncall-notes.md | `clock skew 12ms against ntp pool (recurring)` / `every night this week without customer impact` | 01:42:10, 02:07:33 |
| Vendor healthy | oncall-notes.md | `Payments vendor status page shows green all night.` | n/a |
| Page fired | incident-log.md | `checkout error rate 34% (threshold 5%)` | 02:18:00 |

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| change-history.md | assets_web_deploy | A causal change would have to touch the checkout API, config, or schema | `Static asset bundle only; no API, config, or schema changes.` and `01:50:02 deploy-bot INFO assets-web release 2024.11.3 rolled out (static bundle only)`; no post-01:50 error line references assets-web | no-finding |
| change-history.md | cause_nightly_reindex_lock_contention | Scheduled maintenance must not take table locks on `orders` during live checkout traffic | The 02:00 nightly-reindex job `Rebuilds indexes on orders and order_items; takes table locks in v1 mode.` ran at 02:00 against live traffic, yielding `lock wait timeout exceeded on table orders` at 02:04:52, 02:09:41, 02:24:40 | drift |
| change-history.md | cause_worker_concurrency_rollout | Raising `worker_concurrency` must be accompanied by matching DB pool capacity | `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40)`, followed by `connection pool utilization 88% (max 40)` at 01:58:44 and `connection pool exhausted (max 40); rejecting acquire` at 02:02:17 and 02:15:48 | drift |
| incident-log.md | cause_certificate_expiry | The `payments-gw.internal` certificate must be valid so payment authorization TLS handshakes succeed | `02:13:20 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)` (again at 02:22:09) with `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake`; renewal ticket open, unassigned | drift |
| incident-log.md | clock_skew | A causal skew would be non-recurring, error-level, and large enough to affect the failing path | `clock skew 12ms against ntp pool (recurring)` at 01:42:10 and `clock skew 11ms against ntp pool (recurring)` at 02:07:33 — WARN-level, 11–12ms, self-labelled recurring, and `every night this week without customer impact` | no-finding |

## Assumptions

- All timestamps are UTC, as declared by the fixture title `Incident 2417 — aggregated log excerpt (times UTC)`.
- The log excerpt is representative of the incident window; entries outside 01:42:10–02:24:40 are
  not available and were not assumed to exist.
- `oncall-notes.md` is self-labelled `(raw, unverified)`. Its statements are used only as
  corroboration for conclusions that the log and change history already support on their own
  (pool size unchanged, reindex previously at 04:00, vendor status green), never as the sole
  basis for a confirmed cause.
- "Independent cause" is taken to mean a cause with its own distinct error class whose
  remediation does not remediate the others. Causal independence was reasoned from timestamps
  and error classes, **not** measured by isolation or replay.
- The three causes are assumed to compound into the single 02:18 page; the fixtures do not
  attribute portions of the 34% error rate to specific causes, so that split is unmeasured.
- No repository code, monitoring system, database, or certificate store was inspected — only the
  three named fixture files were read, per the run rules.

## Stop Condition

Stop when all of the following hold, and no further:

1. This artifact exists at
   `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-incident-log-triage-claude_code_goal_command-rep1.md`
   and is the only file written by this run. ✅
2. It contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition. ✅
3. Every confirmed independent cause is attributed with verbatim quoted log evidence and
   timestamps (3 causes). ✅
4. Exactly one smallest-safe remediation is given per confirmed cause (3 remediations). ✅
5. A rejected-candidates section states, for each red herring, why it looked suspicious and why
   it is not causal (3 rejections). ✅
6. Exactly one markdown table uses the header `| location | key | expected | actual | verdict |`,
   with one row per adjudicated item, using the exact required `location`/`key` strings and a
   `verdict` of exactly `drift` or `no-finding` (5 rows). ✅
7. No commit, push, package install, or network access was performed; no benchmark metadata
   outside the three named fixtures and this output file was read. ✅

No claim of benchmark superiority is made in this artifact; it reports only this task's triage.
