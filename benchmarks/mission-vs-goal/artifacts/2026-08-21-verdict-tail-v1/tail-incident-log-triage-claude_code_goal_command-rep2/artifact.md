# tail-incident-log-triage — claude_code_goal_command (rep2)

- Task id: `tail-incident-log-triage`
- Category: debugging
- Arm: `claude_code_goal_command`
- Fixtures read (only these): `benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/incident-log.md`, `benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/change-history.md`, `benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/oncall-notes.md`

## Goal

Triage incident 2417 from the three named fixtures: identify every independent contributing cause with quoted log-line evidence and timestamps, propose the smallest safe remediation per cause, explicitly reject candidate explanations the evidence does not support, and emit exactly one machine-checkable findings table covering the five adjudication items.

## Result

Incident 2417 is a **three-cause** failure. The causes are independent: each has a distinct trigger, a distinct failure signature in the log, and would have produced customer-visible errors on its own. They overlapped in time and jointly pushed checkout error rate to the 34% paging threshold at 02:18.

**Confirmed cause 1 — checkout DB connection pool exhaustion caused by the `worker_concurrency` 8→16 rollout against an unchanged pool.**
The config rollout doubled demand for pool slots at 01:55:31; pool utilization jumped to 88% within ~3 minutes and the pool was exhausted at 02:02:17, before the reindex job could plausibly be the driver of pool *acquisition* pressure. Change history confirms the pool ceiling did not move: "`worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40)."

**Confirmed cause 2 — `nightly-reindex` taking table locks on `orders` during peak checkout traffic.**
The job started at 02:00:00 on `orders, order_items`, and `orders-api` began emitting `lock wait timeout exceeded on table orders` at 02:04:52, recurring at 02:09:41 and 02:24:40. Change history states the job "takes table locks in v1 mode", and on-call notes record the schedule move: "last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero." This is a separate failure signature (lock wait, `orders-api`) from cause 1 (pool acquire, `checkout-db`).

**Confirmed cause 3 — expired `payments-gw.internal` x509 certificate breaking payment authorization.**
`payments-gw` logged `x509: certificate has expired (peer: payments-gw.internal)` at 02:13:20 and again at 02:22:09, and one second after the first occurrence `checkout-api` logged `payment authorization failed: TLS handshake` at 02:13:21. This cause is fully independent of DB load: it is a standing certificate lifecycle failure ("Issued 90 days ago; renewal ticket open, unassigned"), not a consequence of concurrency or locking.

**Rejected candidates (not causal): the 01:50 `assets-web` deploy, and the recurring clock skew warnings.** Reasons in the Evidence section below.

### Smallest safe remediation, one per cause

| Cause | Smallest safe remediation |
|---|---|
| 1. Pool exhaustion from concurrency rollout | Roll `worker_concurrency` back from 16 to 8 for `checkout-workers` (revert the 01:55 config change only). This is a single config value with a known-good prior value and restores the pre-incident demand/pool ratio without touching pool sizing, schema, or code. |
| 2. Reindex lock contention | Stop the in-flight `nightly-reindex` run and restore its schedule to 04:00 (the last-known-good window). No index definition, job mode, or table change required. |
| 3. Expired certificate | Renew/replace the `payments-gw.internal` certificate and reload `payments-gw` — i.e. assign and execute the already-open renewal ticket. No trust-store or protocol change; certificate rotation is the minimal action that clears an expired-cert handshake failure. |

Note on ordering: remediation 3 is independent and can proceed in parallel. Remediations 1 and 2 both relieve `checkout-db`; either alone is insufficient because the two failure signatures are distinct.

## Evidence

All quotes below are verbatim from the named fixtures. Times are UTC as recorded in the log header ("aggregated log excerpt (times UTC)").

### Cause 1 — worker_concurrency rollout vs. fixed pool (max 40)

- `01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)` — incident-log.md
- `01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)` — incident-log.md (≈3 min after rollout, and **before** the 02:00:00 reindex start)
- `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire` — incident-log.md
- `02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db` — incident-log.md
- `02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire` — incident-log.md (recurrence)
- change-history.md: "`worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40)."
- oncall-notes.md: "DB team says pool limit is 40 per the capacity doc and was not changed tonight." — corroborates that the ceiling was static while demand doubled.

Independence argument: the 88% utilization warning at 01:58:44 precedes `nightly-reindex started` at 02:00:00, so pool pressure was already near saturation from the concurrency change alone.

### Cause 2 — nightly-reindex table locks on `orders` during traffic

- `02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)` — incident-log.md
- `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders` — incident-log.md
- `02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders` — incident-log.md
- `02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders` — incident-log.md
- change-history.md: "Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode."
- oncall-notes.md: "The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."

Independence argument: `lock wait timeout exceeded` is a lock-manager failure on `orders`, emitted by `orders-api`, and is not the same condition as `connection pool exhausted ... rejecting acquire` on `checkout-db`. Reverting concurrency would not remove a table lock held by the reindex job.

### Cause 3 — expired payments-gw certificate

- `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)` — incident-log.md
- `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake` — incident-log.md (1 second later; the downstream customer-visible effect)
- `02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)` — incident-log.md (recurrence)
- change-history.md: "payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned." — standing condition, not a tonight change.
- oncall-notes.md: "Payments vendor status page shows green all night." — consistent with the failure being on our internal peer (`payments-gw.internal`), not the vendor.

Independence argument: certificate expiry is time-based and unrelated to DB pool slots or table locks; it would have fired regardless of causes 1 and 2.

### Rejected candidates

**R1 — "The 01:50 assets-web deploy broke checkout" (rejected).**
Why it looked suspicious: it is the closest preceding deploy to the incident window, and it was the channel's first hypothesis — oncall-notes.md: "First guess in the channel: \"the 01:50 deploy broke checkout\" — nobody has verified what that deploy actually contained."
Why it is not causal: the change record scopes it to static assets with no server-side surface — change-history.md: "Static asset bundle only; no API, config, or schema changes." The log entry itself annotates the same scope: `01:50:02 deploy-bot    INFO  assets-web release 2024.11.3 rolled out (static bundle only)`. No error line in the log is attributed to `assets-web` or to asset loading; every error is emitted by `checkout-db`, `checkout-api`, `orders-api`, or `payments-gw`. A static bundle cannot exhaust a DB connection pool, take a table lock, or expire a certificate. The on-call note also records that the hypothesis was never verified, i.e. it is an unsupported guess rather than evidence.

**R2 — "Clock skew against the NTP pool caused the failures" (rejected).**
Why it looked suspicious: skew warnings bracket the incident window and appear in the same aggregated log — `01:42:10 api-edge      WARN  clock skew 12ms against ntp pool (recurring)` and `02:07:33 api-edge      WARN  clock skew 11ms against ntp pool (recurring)`. Clock skew is a plausible generic cause of TLS validity-window errors, so it superficially pairs with the x509 line.
Why it is not causal: (a) both lines are `WARN`, self-labelled `(recurring)`, and oncall-notes.md states "note they have appeared every night this week without customer impact" — a condition present on non-incident nights cannot explain tonight's deviation; (b) the magnitude is 11–12 **milliseconds**, which cannot move a certificate across an expiry boundary; (c) the certificate error is `certificate has expired`, and change-history.md independently corroborates real expiry ("Issued 90 days ago; renewal ticket open, unassigned") rather than a validity-window misread. Skew is background noise here, not a contributing cause.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| change-history.md | assets_web_deploy | A causal change would have to touch API, config, or schema surfaces implicated in the errors | `Static asset bundle only; no API, config, or schema changes.` and log line `01:50:02 deploy-bot    INFO  assets-web release 2024.11.3 rolled out (static bundle only)`; no error line attributed to assets-web | no-finding |
| change-history.md | cause_nightly_reindex_lock_contention | Reindex should not hold `orders` table locks during peak checkout traffic | `Rebuilds indexes on \`orders\` and \`order_items\`; takes table locks in v1 mode.` run at `02:00:00`, followed by `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders` (recurring 02:09:41, 02:24:40); notes: "last month it ran at 04:00, not 02:00" | drift |
| change-history.md | cause_worker_concurrency_rollout | Doubling worker concurrency requires a matching DB pool ceiling | `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40) → `01:58:44 ... connection pool utilization 88% (max 40)` then `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire` | drift |
| incident-log.md | cause_certificate_expiry | `payments-gw.internal` must present a valid, unexpired certificate | `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)` (again 02:22:09) → `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake`; cert `Issued 90 days ago; renewal ticket open, unassigned` | drift |
| incident-log.md | clock_skew | A causal factor must be specific to this incident and large enough to produce the observed errors | `01:42:10 ... clock skew 12ms against ntp pool (recurring)` / `02:07:33 ... clock skew 11ms against ntp pool (recurring)` — WARN-level, 11–12 ms, and "they have appeared every night this week without customer impact" | no-finding |

## Assumptions

- Timestamps are read as UTC, per the incident-log header "aggregated log excerpt (times UTC)". The three fixtures are assumed to share this timeline; no cross-source clock reconciliation was performed and none is available in the fixtures.
- "Independent cause" is interpreted as: a distinct trigger with a distinct failure signature that would produce customer-visible errors without the other causes present. Whether the causes also interacted (e.g. pool pressure worsening lock waits) is **unmeasured** — the fixtures contain no per-query latency, per-connection, or transaction-level data to quantify interaction.
- The exact expiry timestamp of the `payments-gw.internal` certificate is **unmeasured**; the fixtures give only "Issued 90 days ago" and the first observed error at 02:13:20. The precise moment of expiry between 02:00 and 02:13:20 is not determinable from the given evidence.
- Traffic volume at 02:00 versus 04:00 is **unmeasured** in absolute terms; the comparison rests solely on the on-call statement "checkout traffic at 04:00 is near zero", which the notes header labels "raw, unverified".
- Remediation efficacy is **unverified**: no rollback, reschedule, or certificate renewal was executed or observed in this run. The proposals are reasoned from the fixture evidence, not from tested outcomes.
- oncall-notes.md is explicitly "raw, unverified"; it is used here only as corroboration or as the source of a rejected hypothesis, never as the sole basis for a confirmed cause.
- No files under `benchmarks/mission-vs-goal/` were opened other than the three named fixtures and this output file. No commits, pushes, installs, or network access occurred. No claim of benchmark superiority is made.

## Stop Condition

Met when all of the following hold, and they do:

1. This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-incident-log-triage-claude_code_goal_command-rep2.md` — satisfied.
2. It contains the headings Goal, Result, Evidence, Assumptions, Stop Condition — satisfied.
3. Every independent contributing cause is attributed with quoted log evidence and timestamps — satisfied (three causes, each with verbatim log lines and UTC times).
4. Exactly one smallest-safe remediation is given per cause — satisfied (three remediations, one per cause).
5. A rejected-candidates section states why each red herring is not causal — satisfied (R1 assets-web deploy, R2 clock skew).
6. Exactly one markdown table uses the header `| location | key | expected | actual | verdict |`, with one row per adjudication item and `verdict` values drawn only from `drift` / `no-finding` — satisfied (5 rows: 3 `drift`, 2 `no-finding`).

No further work is required; this is the only artifact produced by this run.
