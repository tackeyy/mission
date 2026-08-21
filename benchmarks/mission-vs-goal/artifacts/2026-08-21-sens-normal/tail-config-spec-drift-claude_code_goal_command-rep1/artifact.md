# tail-config-spec-drift — configuration drift audit

Arm: `claude_code_goal_command` · Task id: `tail-config-spec-drift` · Category: configuration

## Goal

Audit the two implementation fixtures and the runbook against the canonical spec
(`spec.md`), report every place where they contradict the spec (quoting key and both
values), and explicitly reject the entries that only *look* contradictory but reconcile
after a unit or aggregate conversion. Produce this single artifact at
`benchmarks/mission-vs-goal/run-output/2026-08-21-sens-normal/tail-config-spec-drift-claude_code_goal_command-rep1.md`
with the headings Goal, Result, Evidence, Assumptions, Stop Condition, plus the required
machine-checkable findings table.

## Result

Seven confirmed drifts against the canonical spec; four candidates evaluated and rejected.

Confirmed drift (source of truth = `spec.md` in every case):

1. **`impl-alpha.md` — `request_timeout_ms`**: spec `3000`, actual `27000`.
   Spec row: `` | `request_timeout_ms` | 3000 | Per-request upstream timeout. | ``
   Alpha line: `requestTimeoutMs   = 27000`.
   Both sides are already expressed in milliseconds (spec key name and Alpha key name both
   end in `_ms`/`Ms`), so no conversion reconciles them: Alpha is 9× the contracted timeout.

2. **`impl-alpha.md` — `queue_max_depth`**: spec `10000`, actual `1250`.
   Spec row: `` | `queue_max_depth` | 10000 | Requests beyond depth are shed. | ``
   Alpha line: `MAX_QUEUE_DEPTH    = 1250`.
   The spec measures depth in requests ("Requests beyond depth are shed"), a countable unit
   with no alternate scale, and Alpha declares no unit convention of its own — its only note
   is "values above are read at boot; there is no runtime override layer in Alpha". So the
   8× gap is a value contradiction, not a unit difference. (See rejected candidate R4 for why
   the exact 8× ratio was examined and still fails.)

3. **`impl-alpha.md` — `enable_legacy_auth`**: spec `false`, actual `true`.
   Spec row: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | ``
   Alpha line: `enableLegacyAuth   = true`.
   Alpha's own note confirms the deviation is unintentional and unreviewed: "The legacy auth
   flag was toggled during the March incident bridge and has not been revisited since."

4. **`impl-beta.md` — `retry_backoff`**: spec `exponential, base 250ms`, actual
   `constant-interval` (base 250ms).
   Spec row: `` | `retry_backoff` | exponential, base 250ms | Jitter enabled. | ``
   Beta lines: `RETRY_BACKOFF_STRATEGY=constant-interval` and `RETRY_BACKOFF_BASE_MS=250`.
   The base is compliant; the *strategy* is not. Beta's note pins the vocabulary — "Backoff
   strategy names follow the retry library's enum (`constant-interval`, `exponential`,
   `decorrelated-jitter`)" — so `exponential` exists as a distinct selectable value in the
   same enum. `constant-interval` is therefore a different algorithm, not a synonym.

5. **`impl-beta.md` — `health_check_interval_s`**: spec `15`, actual `75`.
   Spec row: `` | `health_check_interval_s` | 15 | Liveness probe cadence. | ``
   Beta line: `HEALTH_CHECK_INTERVAL_SECONDS=75`.
   Both keys name seconds explicitly (`_s` / `_SECONDS`). Beta's tick convention ("Beta counts
   idle time in scheduler ticks") is scoped to *idle time* only and does not apply to this key,
   which is already in seconds. 75 s is a 5× slower probe cadence.

6. **`runbook.md` — `max_retries`**: spec `3`, actual `6`.
   Spec row: `` | `max_retries` | 3 | Applies to idempotent requests only. | ``
   Runbook text: "the gateway will retry idempotent requests up to 6 times before shedding.
   Do not raise this further during incidents".
   The runbook matches the spec's idempotent-only scope but doubles the count, and instructs
   operators to treat 6 as the standing ceiling.

7. **`runbook.md` — `tls_min_version`**: spec `1.3`, actual `1.2`.
   Spec row: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | ``
   Runbook text: "set the load balancer TLS floor to 1.2 first so older internal probes keep
   passing during the rotation window".
   The spec calls 1.3 a "Hard floor for all listeners" and grants no rotation-window exception,
   so a documented procedure that lowers the floor to 1.2 contradicts it.

Rejected candidates (looked contradictory, are not drift) — see the Evidence section.

**Spec constraints violated.** Six of the ten canonical rows are violated somewhere:
`request_timeout_ms` (Alpha), `queue_max_depth` (Alpha), `enable_legacy_auth` (Alpha — and
with it the note "Must stay false"), `retry_backoff` (Beta), `health_check_interval_s` (Beta),
`max_retries` (runbook), and `tls_min_version` (runbook — specifically the "Hard floor for all
listeners" qualifier). The four rows with no violation anywhere are `idle_timeout_s`,
`log_level`, `db_pool_size_per_replica`, and — for the implementations only — `queue_max_depth`
and `tls_min_version` in Beta.

## Evidence

### Rejected candidates

**R1 — `impl-beta.md`, `idle_timeout_s`: `IDLE_TIMEOUT_TICKS=5400` vs spec `90`.**
Looks like a 60× blowout of the connection idle close. It is not: Beta states "Beta counts
idle time in scheduler ticks; the scheduler runs at 60 ticks per second", and the key itself
is named `IDLE_TIMEOUT_TICKS`, not `_S`. Conversion: 5400 ticks ÷ 60 ticks/s = **90 s**, which
equals the spec's `` | `idle_timeout_s` | 90 | Connection idle close. | ``. Compliant.

**R2 — `runbook.md`, `db_pool_size_per_replica`: "64 pooled connections in total" vs spec `32`.**
Looks like double the contracted pool size. It is not: the spec value is explicitly *per
replica* and its note says "Two replicas run in production", while the runbook figure is
explicitly an aggregate — "the two replicas hold 64 pooled connections in total. Alert
thresholds are derived from that aggregate figure." Conversion: 32 per replica × 2 replicas =
**64 total**. Compliant.

**R3 — `runbook.md`, `log_level`: "DEBUG is allowed only on a single canary replica" vs spec `info`.**
Looks like a permitted deviation from the production log level. It is not drift: the runbook's
standing instruction matches the spec — "Run all services at INFO verbosity in production" vs
spec `` | `log_level` | info | Production default. | `` — and the spec's own note frames `info`
as the *default*, not an absolute floor. The DEBUG allowance is bounded on both scope and time
("a single canary replica for up to one hour"), so it is a scoped exception to a default rather
than a contradicting configured value. Judgment call, stated openly: if the spec's "Production
default" were read as a hard constraint, this would flip to drift.

**R4 — `impl-alpha.md`, `queue_max_depth`: is `1250` a unit conversion of `10000`?**
Examined because 10000 ÷ 8 = 1250 exactly, which would be the arithmetic of a bits→bytes (or
items→slots-of-8) rescale, mirroring the tick conversion in R1. Rejected as an explanation, so
the row stays **drift** (item 2 above): the spec's unit is requests, not a storage quantity
("Requests beyond depth are shed"), Alpha's key is `MAX_QUEUE_DEPTH` with no unit suffix, and
Alpha publishes no conversion convention anywhere in the fixture — unlike Beta, which states its
tick rate, and the runbook, which labels its figure as an aggregate. Without a declared
convention, an exact ratio alone is not evidence of a conversion.

**R5 — `impl-beta.md`, `retry_backoff` base: `RETRY_BACKOFF_BASE_MS=250`.**
Evaluated alongside the strategy mismatch; the base value matches the spec's "base 250ms"
exactly. The drift in item 4 is confined to the strategy name.

### Keys absent from a fixture (evaluated, not asserted as drift)

`impl-alpha.md` contains no `health_check_interval_s` and no `idle_timeout_s` line. The file is
labelled "excerpt from deployed config", so absence from an excerpt is not evidence that the
deployed value contradicts the spec. Alpha's effective values for these two keys are
**unmeasured** by this fixture set; they are recorded as `no-finding` rather than `drift`.
Likewise, `runbook.md` covers only retries, TLS, logging, DB connections, and health, so the
remaining spec keys are simply out of its scope.

The runbook's Health section is compliant and asserts nothing of its own: "Liveness probes are
configured centrally; see the spec for cadence. If probes flap during deploys, extend the grace
period rather than the cadence." It defers to the spec, so there is no value to contradict.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-beta.md | retry_backoff (strategy) | exponential | constant-interval | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks ÷ 60 ticks/s = 90 | no-finding |
| runbook.md | db_pool_size_per_replica | 32 per replica | 64 total ÷ 2 replicas = 32 | no-finding |
| runbook.md | log_level | info | INFO in production, DEBUG canary-only ≤1h | no-finding |
| impl-alpha.md | max_retries | 3 | 3 | no-finding |
| impl-alpha.md | retry_backoff | exponential, base 250ms | exponential, base 250ms | no-finding |
| impl-alpha.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-alpha.md | log_level | info | info | no-finding |
| impl-alpha.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| impl-alpha.md | health_check_interval_s | 15 | absent from excerpt (unmeasured) | no-finding |
| impl-alpha.md | idle_timeout_s | 90 | absent from excerpt (unmeasured) | no-finding |
| impl-beta.md | request_timeout_ms | 3000 | 3000 | no-finding |
| impl-beta.md | max_retries | 3 | 3 | no-finding |
| impl-beta.md | retry_backoff (base) | 250ms | 250 | no-finding |
| impl-beta.md | queue_max_depth | 10000 | 10000 | no-finding |
| impl-beta.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-beta.md | enable_legacy_auth | false | false | no-finding |
| impl-beta.md | log_level | info | info | no-finding |
| impl-beta.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| runbook.md | health_check_interval_s | 15 | defers to spec, asserts no value | no-finding |

## Assumptions

- `spec.md` is the sole source of truth; where a fixture disagrees with it, the fixture is the
  defect, regardless of which side looks more operationally reasonable.
- A key expressed in a different naming style (`requestTimeoutMs` vs `request_timeout_ms`,
  `HEALTH_CHECK_INTERVAL_SECONDS` vs `health_check_interval_s`) refers to the same contract row.
- A unit conversion is only accepted when the fixture itself declares the convention (Beta's
  "60 ticks per second"; the runbook's "in total" / "aggregate figure"). Numeric coincidence
  alone does not clear a row — this is why R4 remains drift.
- A key missing from a file labelled "excerpt" is treated as unmeasured, not as drift.
- The runbook's DEBUG canary allowance is judged against the spec's wording "Production
  default" (R3). This is the one verdict that depends on reading `log_level` as a default
  rather than a hard constraint.
- No runtime, deployed, or production state was inspected. Every claim here rests on the four
  named fixture files only; actual running behaviour is unmeasured.

## Stop Condition

Stop when: all four named fixtures have been read in full; every one of the ten canonical spec
rows has been checked against each fixture that mentions it; each evaluated item appears exactly
once in the single machine-checkable findings table with verdict `drift` or `no-finding`; every
confirmed drift quotes the key and both values; every rejected candidate shows its conversion or
reasoning; the violated spec constraints are stated explicitly; and this artifact exists at the
required path with the headings Goal, Result, Evidence, Assumptions, and Stop Condition.

All of the above are satisfied. No files outside the four named fixtures and this output file
were opened, and nothing was committed, pushed, installed, or fetched over the network.
