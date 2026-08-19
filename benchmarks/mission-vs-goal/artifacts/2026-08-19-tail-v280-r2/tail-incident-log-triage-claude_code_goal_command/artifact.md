# Incident 2417 — Triage (task id: tail-incident-log-triage, arm: claude_code_goal_command)

## Goal

Triage incident 2417 using exactly three fixtures (`incident-log.md`, `change-history.md`, `oncall-notes.md` under `benchmarks/mission-vs-goal/fixtures/tail/incident-log-triage/`), treating the failure as multi-cause: identify every independent contributing cause with quoted log-line evidence and timestamps, propose one smallest-safe remediation per cause, and explicitly reject the candidate explanations the evidence does not support. Produce exactly one artifact at
`benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280-r2/tail-incident-log-triage-claude_code_goal_command.md`
with the headings Goal, Result, Evidence, Assumptions, Stop Condition. No commits, no network, no benchmark metadata access, no superiority claims.

## Result

Incident 2417 (page at `02:18:00`, `checkout error rate 34% (threshold 5%)`) is **not single-cause**. Three independent failure paths overlapped in the same window. Each produces a distinct error class on a distinct component, and each would still fire if the other two were removed.

### Confirmed cause 1 — checkout-db connection pool exhausted by the 01:55 concurrency doubling

`worker_concurrency` for checkout-workers was raised `8 -> 16` at 01:55 while the DB pool ceiling stayed at 40. Pool utilization was already 88% three minutes later, and the pool went to hard exhaustion at 02:02:17, before the reindex could have taken any lock-driven effect on pool holding, and 11 minutes before the first TLS error.

- Trigger: `01:55:31 config-svc INFO rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`
- Leading indicator: `01:58:44 checkout-db WARN connection pool utilization 88% (max 40)`
- Failure: `02:02:17 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire`, recurring at `02:15:48`
- Downstream symptom: `02:03:05 checkout-api ERROR upstream timeout talking to checkout-db`
- Change-history corroboration: `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40).

**Smallest safe remediation:** roll the checkout-workers config back to `worker_concurrency 8` (revert the 01:55 rollout only). This is smaller and safer than raising the pool ceiling above 40, which would change a documented capacity limit under load; the pool value was not what changed tonight.

### Confirmed cause 2 — nightly-reindex moved into peak checkout traffic, taking table locks on `orders`

The reindex job started at 02:00 and rebuilds indexes on `orders` / `order_items` in v1 mode, which takes table locks. Lock wait timeouts on `orders` begin 4m52s after job start and persist past the page, including at `02:24:40` — after the last pool-exhaustion line and on a different component (`orders-api`, not `checkout-db`), so it is an independent path rather than a symptom of cause 1.

- Trigger: `02:00:00 job-runner INFO nightly-reindex started (tables: orders, order_items)`
- Failures: `02:04:52 orders-api ERROR lock wait timeout exceeded on table orders`; `02:09:41 orders-api ERROR lock wait timeout exceeded on table orders`; `02:24:40 orders-api ERROR lock wait timeout exceeded on table orders`
- Change-history corroboration: "Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode."
- On-call corroboration that the *schedule*, not the job, is the new variable: "The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."

**Smallest safe remediation:** move the nightly-reindex schedule back to 04:00 (its previously-working window). This changes only the trigger time — no job rewrite, no lock-mode migration, no index change.

### Confirmed cause 3 — expired x509 certificate on `payments-gw.internal`

Payment authorization fails on TLS, one second after the certificate expiry error on the gateway. This is unrelated to DB pool capacity and unrelated to table locks: it is a standing, unassigned certificate renewal that came due.

- Failure: `02:13:20 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)`, recurring at `02:22:09`
- Downstream symptom (1 second later): `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake`
- Change-history corroboration: "payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned."

**Smallest safe remediation:** renew/reissue the `payments-gw.internal` certificate and reload the gateway's TLS material (i.e. assign and execute the already-open renewal ticket). Do **not** disable peer verification as a workaround — that removes a security control rather than fixing the expiry.

### Why the three are independent

- Distinct components and error classes: `checkout-db` pool acquire rejection, `orders-api` lock wait timeout, `payments-gw` x509 expiry.
- Distinct triggers with distinct timestamps: 01:55 config rollout, 02:00 scheduled job, standing certificate lifetime.
- Non-overlapping onset: pool exhaustion at `02:02:17` precedes the first lock timeout (`02:04:52`), which precedes the first TLS failure (`02:13:20`). Removing any one trigger leaves the other two error streams intact in the log.

## Evidence

All quoted lines are verbatim from the three named fixtures. Timestamps are UTC per the fixture header ("aggregated log excerpt (times UTC)").

| Time (UTC) | Quoted line | Maps to |
|---|---|---|
| 01:42:10 | `01:42:10 api-edge WARN clock skew 12ms against ntp pool (recurring)` | Rejected candidate R2 |
| 01:50:02 | `01:50:02 deploy-bot INFO assets-web release 2024.11.3 rolled out (static bundle only)` | Rejected candidate R1 |
| 01:55:31 | `01:55:31 config-svc INFO rollout complete: worker_concurrency 8 -> 16 (checkout-workers)` | Cause 1 trigger |
| 01:58:44 | `01:58:44 checkout-db WARN connection pool utilization 88% (max 40)` | Cause 1 leading indicator |
| 02:00:00 | `02:00:00 job-runner INFO nightly-reindex started (tables: orders, order_items)` | Cause 2 trigger |
| 02:02:17 | `02:02:17 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire` | Cause 1 failure |
| 02:03:05 | `02:03:05 checkout-api ERROR upstream timeout talking to checkout-db` | Cause 1 downstream |
| 02:04:52 | `02:04:52 orders-api ERROR lock wait timeout exceeded on table orders` | Cause 2 failure |
| 02:07:33 | `02:07:33 api-edge WARN clock skew 11ms against ntp pool (recurring)` | Rejected candidate R2 |
| 02:09:41 | `02:09:41 orders-api ERROR lock wait timeout exceeded on table orders` | Cause 2 failure |
| 02:13:20 | `02:13:20 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)` | Cause 3 failure |
| 02:13:21 | `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake` | Cause 3 downstream |
| 02:15:48 | `02:15:48 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire` | Cause 1 recurrence |
| 02:18:00 | `02:18:00 alerting PAGE checkout error rate 34% (threshold 5%)` | Incident page |
| 02:22:09 | `02:22:09 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)` | Cause 3 recurrence |
| 02:24:40 | `02:24:40 orders-api ERROR lock wait timeout exceeded on table orders` | Cause 2, post-page persistence |

Supporting non-log evidence (quoted):

- change-history, 01:50 row: "Static asset bundle only; no API, config, or schema changes."
- change-history, 01:55 row: "`worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40)."
- change-history, 02:00 row: "Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode."
- change-history, standing row: "Issued 90 days ago; renewal ticket open, unassigned."
- oncall-notes: "DB team says pool limit is 40 per the capacity doc and was not changed tonight."
- oncall-notes: "Payments vendor status page shows green all night."
- oncall-notes header: "On-call notes (raw, unverified)".

### Rejected candidates

**R1 — "the 01:50 deploy broke checkout" (assets-web 2024.11.3).**
Why it looked suspicious: it is the closest preceding deploy to the page (`01:50:02`, 28 minutes before), and it was the first hypothesis raised in the channel — "First guess in the channel: 'the 01:50 deploy broke checkout' — nobody has verified what that deploy actually contained."
Why it is not causal: the release is scoped to static assets only. The log line itself says `(static bundle only)` and change-history states "Static asset bundle only; no API, config, or schema changes." A static bundle cannot produce DB pool acquire rejection, `orders` table lock waits, or an x509 expiry. No error line between `01:50:02` and `02:02:17` references assets-web.

**R2 — clock skew on api-edge.**
Why it looked suspicious: WARN lines appear inside the incident window at `01:42:10` and `02:07:33`, and skew can plausibly break certificate validity checks, which pairs temptingly with the `x509: certificate has expired` errors.
Why it is not causal: the lines are self-labelled `(recurring)`, and oncall-notes states they "have appeared every night this week without customer impact." The magnitudes are 12ms and 11ms — far too small to move a certificate across a 90-day validity boundary, and unrelated to pool capacity or table locks. Recurring-without-impact plus millisecond magnitude makes this background noise, not a contributor.

**R3 — "the DB pool size was reduced / misconfigured tonight."**
Why it looked suspicious: every pool error names the ceiling (`max 40`), which reads like a limit that someone lowered.
Why it is not causal: 40 is the documented steady-state value, not a change. change-history's 01:55 row says "DB pool size unchanged (max 40)" and oncall-notes says "DB team says pool limit is 40 per the capacity doc and was not changed tonight." The variable that moved was demand (`worker_concurrency 8 -> 16`), not supply. This is why cause 1's remediation reverts concurrency rather than raising the pool.

**R4 — payments vendor outage.**
Why it looked suspicious: `payment authorization failed` at `02:13:21` looks like a third-party payment provider incident, the usual first suspicion for payment failures.
Why it is not causal: oncall-notes records "Payments vendor status page shows green all night," and the error is `x509: certificate has expired (peer: payments-gw.internal)` — an `.internal` peer, i.e. our own gateway endpoint, with a locally-owned expiry ("renewal ticket open, unassigned"). The failure mode is TLS handshake at our boundary, not an authorization decline returned by the vendor.

**R5 — "the reindex job itself is broken / regressed."**
Why it looked suspicious: the lock timeouts start 4m52s after the job starts, so the job is clearly implicated.
Why it is partially rejected: the job's behaviour is unchanged — "The reindex job ran fine last month" and v1 lock-taking is its documented normal mode. What changed is the schedule ("last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero"). The job is a contributor only in combination with the peak-traffic window; the causal delta is the schedule, which is why the remediation is a schedule revert rather than a job change. This is listed here to prevent the mis-scoped fix of rewriting/disabling the reindex.

## Assumptions

- The three fixtures named in the task prompt are the complete evidence set. No other file was read; no repository code, benchmark metadata, or external source was consulted.
- Timestamps are UTC and the aggregated log is chronologically ordered and complete for the excerpt window `01:42:10`–`02:24:40`. Gaps outside that window are unknown.
- oncall-notes is explicitly "raw, unverified"; its statements are used as corroboration for rejections (R1, R2, R3, R4, R5), not as sole proof of a confirmed cause. Every confirmed cause is anchored to at least one log line plus one change-history row.
- The 1-second gap between `02:13:20` (x509 expired) and `02:13:21` (payment authorization failed: TLS handshake) is treated as a causal chain rather than coincidence, based on the shared TLS failure mode. This is an inference from adjacency and error semantics, not from a trace ID — no correlation identifier exists in the fixture.

### Explicitly unmeasured

- **Per-cause contribution to the 34% error rate.** The fixture reports one aggregate figure (`checkout error rate 34% (threshold 5%)`) with no breakdown by error class. The split across pool exhaustion, lock waits, and TLS failures is unmeasured.
- **Connections held per worker.** The claim that doubling `worker_concurrency` exhausts a 40-connection pool is supported by the observed 88%→exhaustion progression, but the per-worker connection count is not stated anywhere in the fixtures and is unmeasured.
- **Reindex job duration and lock hold time.** No completion line for `nightly-reindex` appears in the excerpt; whether it was still running at `02:24:40` is unmeasured (the lock timeout at that time is consistent with, but does not prove, an ongoing job).
- **Customer/revenue impact, request volume, and latency percentiles.** Not present in any fixture; unmeasured.
- **Certificate exact expiry instant.** Only "Issued 90 days ago" and the first observed error at `02:13:20` are available; the precise notAfter time is unmeasured, so whether expiry occurred earlier in a quiet period is unknown.
- **Remediation effectiveness.** No remediation was applied or tested in this run; the proposed fixes are unverified.

## Stop Condition

This run stops when all of the following hold, and no further action is taken:

1. This single artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280-r2/tail-incident-log-triage-claude_code_goal_command.md` and contains the headings Goal, Result, Evidence, Assumptions, Stop Condition. — met.
2. Every independent contributing cause is attributed with at least one verbatim-quoted log line and its timestamp. — met (3 causes, 16 quoted log lines).
3. Each confirmed cause has exactly one smallest-safe remediation. — met (config revert; schedule revert to 04:00; certificate renewal).
4. A rejected-candidates section states, per candidate, why it looked suspicious and why the evidence does not support it. — met (R1–R5).
5. No commit, push, package install, or network access occurred; no file outside the artifact path was written; nothing under `benchmarks/mission-vs-goal/` was read except the three named fixtures and this output file. — met.
6. No benchmark superiority claim is made anywhere in this artifact. — met.

Out of scope by design: applying any remediation, verifying fixes, or measuring the unmeasured items listed above.
