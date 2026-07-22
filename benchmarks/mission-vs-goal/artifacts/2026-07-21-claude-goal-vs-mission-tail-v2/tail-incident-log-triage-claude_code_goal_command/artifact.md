# Incident 2417 Triage

## Goal

Triage incident 2417 using exactly the three fixtures `incident-log.md`, `change-history.md`, and `oncall-notes.md`. Identify every independent contributing cause with log-line evidence, propose the smallest safe remediation for each, and explicitly reject candidate explanations that the evidence does not support.

## Result

The incident is **multi-causal**: three independent contributing causes combined to produce the 02:18:00 page (`checkout error rate 34% (threshold 5%)`).

1. **Checkout DB connection-pool exhaustion**, triggered by a worker concurrency increase not matched by a DB pool size increase.
2. **Lock wait timeouts on `orders`**, triggered by the nightly reindex job running during active traffic instead of its usual low-traffic window.
3. **Payment authorization failures**, triggered by an expired TLS certificate on `payments-gw.internal`.

No single one of these three explains the full symptom set.

## Evidence

### Confirmed cause 1 — Connection pool exhaustion from concurrency/pool-size mismatch

- Change: `change-history.md` line 6 — "01:55 | checkout-workers config rollout | `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40)."
- Log, `incident-log.md`:
  - `01:55:31 config-svc INFO rollout complete: worker_concurrency 8 -> 16 (checkout-workers)`
  - `01:58:44 checkout-db WARN connection pool utilization 88% (max 40)`
  - `02:02:17 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire`
  - `02:03:05 checkout-api ERROR upstream timeout talking to checkout-db`
  - `02:15:48 checkout-db ERROR connection pool exhausted (max 40); rejecting acquire`
- `oncall-notes.md` line 8: "DB team says pool limit is 40 per the capacity doc and was not changed tonight" — confirms demand (concurrency) doubled while supply (pool max) stayed fixed.

**Smallest safe remediation:** Roll back `worker_concurrency` from 16 to 8 (revert the 01:55 config change).

### Confirmed cause 2 — Lock wait timeouts from reindex job running during traffic

- Change: `change-history.md` line 7 — "02:00 | nightly-reindex scheduled job | Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode."
- Log, `incident-log.md`:
  - `02:00:00 job-runner INFO nightly-reindex started (tables: orders, order_items)`
  - `02:04:52 orders-api ERROR lock wait timeout exceeded on table orders`
  - `02:09:41 orders-api ERROR lock wait timeout exceeded on table orders`
  - `02:24:40 orders-api ERROR lock wait timeout exceeded on table orders`
- `oncall-notes.md` lines 10–11: "The reindex job ran fine last month, but last month it ran at 04:00, not 02:00, and checkout traffic at 04:00 is near zero."

**Smallest safe remediation:** Move the nightly-reindex job's schedule back to a low-traffic window (e.g., 04:00).

### Confirmed cause 3 — Expired TLS certificate on payments-gw.internal

- Change: `change-history.md` line 8 — "(standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned."
- Log, `incident-log.md`:
  - `02:13:20 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)`
  - `02:13:21 checkout-api ERROR payment authorization failed: TLS handshake`
  - `02:22:09 payments-gw ERROR x509: certificate has expired (peer: payments-gw.internal)`

**Smallest safe remediation:** Renew/rotate the `payments-gw.internal` certificate immediately (execute the already-open, unassigned renewal ticket).

## Rejected candidates

- **"The 01:50 assets-web deploy broke checkout"** — On-call's first guess (`oncall-notes.md` line 4), but `change-history.md` line 5 states the deploy was "Static asset bundle only; no API, config, or schema changes." No log line ties any backend error to this deploy; errors begin 10+ minutes later, tracking the 01:55 and 02:00 changes instead.
- **"Clock skew warnings caused the incident"** — `incident-log.md`: `01:42:10 api-edge WARN clock skew 12ms`, `02:07:33 api-edge WARN clock skew 11ms`. `oncall-notes.md` lines 6-7: these "have appeared every night this week without customer impact." WARN-level recurring noise, not causal.
- **"DB connection pool limit was reduced/misconfigured tonight"** — `oncall-notes.md` line 8: "DB team says pool limit is 40 per the capacity doc and was not changed tonight." The limit itself was not altered; the actual driver is the concurrency-side change (Confirmed cause 1).
- **"Payments vendor outage caused the authorization failures"** — `oncall-notes.md` line 12: "Payments vendor status page shows green all night." The failure is internal (our own expired cert on `payments-gw.internal`), not an external vendor incident.

## Assumptions

- The three named fixtures are treated as the complete and sole evidence base; no other logs or systems were consulted.
- Timestamps are UTC as labeled in `incident-log.md`'s header; no timezone conversion performed.
- `checkout-db` is assumed to back both `checkout-api`/`checkout-workers` and `orders-api` (both act on `orders`/`order_items` per `change-history.md` line 7). Cause 1 (pool exhaustion, driven by connection count) and cause 2 (lock waits, driven by contention timing) are kept as separate findings because they are independent levers — fixing one does not fix the other.
- No fixture data quantifies exact request/error counts beyond the `34%` figure at 02:18:00 and `88%` pool utilization at 01:58:44; any further severity is unmeasured and not claimed.
- "Smallest safe remediation" means the minimal reversible action removing the specific triggering change identified in evidence, not a broader systemic fix.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five required headings, attributes the incident to three independent causes each with quoted log evidence and timestamps, gives one smallest-safe remediation per cause, and includes a rejected-candidates section with a stated reason for each rejected item.
