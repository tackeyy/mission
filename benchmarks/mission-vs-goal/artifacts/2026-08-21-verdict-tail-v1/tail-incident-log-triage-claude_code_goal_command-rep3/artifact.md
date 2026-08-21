# tail-incident-log-triage — incident 2417 triage

Arm: `claude_code_goal_command`
Task id: `tail-incident-log-triage`
Category: debugging

## Goal

Triage incident 2417 using exactly the three named fixtures
(`incident-log.md`, `change-history.md`, `oncall-notes.md`), identify every
independent contributing cause with quoted log-line evidence and timestamps,
propose one smallest-safe remediation per cause, and explicitly reject the
candidate explanations the evidence does not support — plus emit the required
machine-checkable findings table.

## Result

Incident 2417 is **not single-cause**. Three independent failure chains
overlapped inside the same window and jointly produced the 02:18 page
(`02:18:00 alerting PAGE checkout error rate 34% (threshold 5%)`).

**Confirmed contributing causes (3, independent):**

1. **Worker concurrency doubled without a matching DB pool increase** —
   `worker_concurrency 8 -> 16` at 01:55 against an unchanged `max 40` pool
   drove pool utilization to 88% and then exhaustion. Chain:
   pool exhausted → checkout-api upstream timeouts.
2. **Nightly reindex moved into peak checkout traffic and taking table locks** —
   the 02:00 `nightly-reindex` takes locks on `orders` in v1 mode, producing
   repeated `lock wait timeout exceeded on table orders` on orders-api.
3. **Expired `payments-gw.internal` TLS certificate** — an unassigned renewal
   ticket left an expired cert; payments-gw fails `x509: certificate has
   expired` and checkout-api's payment authorization fails on TLS handshake.

They are independent because each has its own trigger (a config rollout, a
schedule, a standing certificate lifecycle), its own error signature, and
removing any one would not silence the other two. Note in particular that the
certificate expiry is unrelated to DB load: it would have fired regardless of
concurrency or the reindex job.

**Rejected candidates (2):** the 01:50 `assets-web 2024.11.3` deploy, and the
recurring `clock skew` warnings. Rationale in the section below.

### Smallest safe remediation, one per cause

| # | Cause | Smallest safe remediation |
|---|---|---|
| 1 | worker_concurrency 8 → 16 with `max 40` pool unchanged | Roll `worker_concurrency` back to `8` for checkout-workers (revert the 01:55 config rollout only). This is a config-value revert, needs no schema/deploy change, and restores the pre-incident pool ratio. Any pool-size increase is a larger, capacity-doc-owned change and is not the minimal step. |
| 2 | nightly-reindex at 02:00 taking table locks on `orders` | Reschedule the `nightly-reindex` job back to `04:00` (its prior slot, where per the on-call note checkout traffic is near zero). Schedule-only change; no change to the job's v1 locking mode is required to stop the contention. |
| 3 | expired `payments-gw.internal` certificate | Assign and execute the already-open renewal ticket to reissue/rotate the `payments-gw.internal` certificate. Certificate replacement only; no code, config, or vendor change. |

## Evidence

All quotes below are verbatim from the named fixtures. Timestamps are UTC as
labelled by `incident-log.md` ("times UTC").

### Cause 1 — worker concurrency rollout vs. fixed DB pool

- `01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`
- `01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)` — 3m13s after the rollout, before the reindex job starts.
- `02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
- `02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db`
- `02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire`
- change-history.md: `| 01:55 | checkout-workers config rollout | `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40). |`
- oncall-notes.md corroborates that the pool itself is not the changed variable: "DB team says pool limit is 40 per the capacity doc and was not changed tonight." The pool being constant is precisely why doubling concurrency exhausts it.

Ordering argument: the 88% utilization warning at `01:58:44` precedes
`02:00:00 job-runner INFO nightly-reindex started`, so pool pressure is
established before the reindex job exists. This is what makes cause 1
independent of cause 2 rather than a downstream effect of it.

### Cause 2 — nightly-reindex table lock contention at peak

- `02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)`
- `02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders`
- `02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders`
- `02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders`
- change-history.md: `| 02:00 | nightly-reindex scheduled job | Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode. |`
- oncall-notes.md: "The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."

The lock-wait signature (`lock wait timeout exceeded on table orders`) appears
only after `02:00:00` and names exactly the table the change history says the
job locks. The job itself is unchanged; what changed is the schedule slot
relative to traffic.

### Cause 3 — expired payments-gw certificate

- `02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`
- `02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake` — 1 second later, same chain.
- `02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)`
- change-history.md: `| (standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned. |`
- oncall-notes.md: "Payments vendor status page shows green all night." — this rules out a vendor-side outage and localizes the failure to the *internal* peer `payments-gw.internal`, consistent with the 90-day-old cert and the unassigned renewal ticket.

### Rejected candidates

**R1 — "the 01:50 deploy broke checkout" (assets-web 2024.11.3).**
Why it looked suspicious: it is the closest preceding change to the incident
(`01:50:02 deploy-bot INFO assets-web release 2024.11.3 rolled out (static
bundle only)`), and it was the channel's first guess — oncall-notes.md: "First
guess in the channel: \"the 01:50 deploy broke checkout\" — nobody has verified
what that deploy actually contained."
Why it is not causal: the change history states its scope explicitly —
`| 01:50 | assets-web 2024.11.3 | Static asset bundle only; no API, config, or
schema changes. |` A static asset bundle cannot open DB connections, take
table locks, or participate in the payments TLS handshake, so it cannot produce
any of the three observed error signatures. The log line itself corroborates
the scope with "(static bundle only)". Additionally, no error appears between
`01:50:02` and `01:58:44`; the first anomaly is the pool-utilization warning
that tracks the 01:55 config rollout, not the deploy.

**R2 — clock skew against the NTP pool.**
Why it looked suspicious: skew warnings bracket the incident window
(`01:42:10 api-edge WARN clock skew 12ms against ntp pool (recurring)` and
`02:07:33 api-edge WARN clock skew 11ms against ntp pool (recurring)`), and
clock problems are a plausible-sounding explanation for certificate expiry
errors. oncall-notes.md records that "Someone also pointed at the clock skew
warnings".
Why it is not causal: (a) both lines are self-labelled `(recurring)` and
oncall-notes.md states "they have appeared every night this week without
customer impact" — a signal present on non-incident nights cannot explain an
incident-only outcome; (b) the magnitude is 11–12 **milliseconds**, which is
many orders of magnitude too small to flip a certificate validity check from
valid to expired; and (c) the warnings are on `api-edge`,
while the failures are on `checkout-db`, `orders-api`, and `payments-gw`.

## Findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| change-history.md | assets_web_deploy | A change is causal only if its scope can produce an observed failure signature | `Static asset bundle only; no API, config, or schema changes.` — cannot cause pool exhaustion, table locks, or TLS failure; no errors logged 01:50:02–01:58:44 | no-finding |
| change-history.md | cause_nightly_reindex_lock_contention | Index rebuild scheduled outside peak checkout traffic (prior slot 04:00) | Ran at `02:00:00 job-runner INFO nightly-reindex started (tables: orders, order_items)` and `takes table locks in v1 mode`, followed by `02:04:52`, `02:09:41`, `02:24:40 orders-api ERROR lock wait timeout exceeded on table orders` | drift |
| change-history.md | cause_worker_concurrency_rollout | Concurrency increase accompanied by a matching DB connection pool increase | `worker_concurrency raised from 8 to 16; DB pool size unchanged (max 40)` → `01:58:44 ... utilization 88% (max 40)`, `02:02:17` and `02:15:48 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire` | drift |
| incident-log.md | cause_certificate_expiry | Valid, unexpired TLS certificate on the internal payments peer | `02:13:20` and `02:22:09 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)`; `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake`; renewal ticket `open, unassigned` | drift |
| incident-log.md | clock_skew | A causal signal must be incident-specific and of sufficient magnitude | `clock skew 12ms` (01:42:10) / `clock skew 11ms` (02:07:33), both `(recurring)`; on-call notes: appeared "every night this week without customer impact" — non-causal background noise | no-finding |

## Assumptions

- The three fixtures are the complete and authoritative evidence set; nothing
  outside them was opened, read, grepped, or listed (benchmark metadata,
  answer keys, and scoring configuration were treated as out of bounds).
- Timestamps are UTC as declared by the `incident-log.md` heading, and the log
  is in chronological order as presented. Clock accuracy across the emitting
  services is **unmeasured** beyond the api-edge skew warnings themselves.
- `verdict = drift` is interpreted as "I assert a defect / this is a real
  contributing cause", and `no-finding` as "evaluated and not a defect / not
  causal". Under this mapping the two red herrings are `no-finding`.
- Independence of the three causes is inferred from distinct triggers, distinct
  error signatures, and the ordering shown above. No counterfactual replay or
  experiment was run: the causal claims rest on log evidence and stated change
  scope, and the exact quantitative contribution of each cause to the 34% error
  rate is **unmeasured**.
- Customer impact, request volume, retry behaviour, and whether the pool
  exhaustion and lock waits amplified each other beyond simple co-occurrence are
  all **unmeasured** — the fixtures contain no throughput or latency series.
- Remediation proposals are reasoned from the fixtures' stated scopes; none were
  executed or validated in any environment (no commits, installs, or network
  access were performed).

## Stop Condition

Satisfied when this artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-incident-log-triage-claude_code_goal_command-rep3.md`
with the headings Goal, Result, Evidence, Assumptions, and Stop Condition; each
independent cause attributed with quoted log evidence and timestamps; exactly
one smallest-safe remediation per cause; a rejected-candidates section stating
why each red herring is not causal; and exactly one findings table carrying the
required header with one row per adjudicated item and a verdict of exactly
`drift` or `no-finding`. All of the above are present. No other files were
written, and no benchmark superiority claim is made.
