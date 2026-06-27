# complex-failing-test-triage — claude_code_goal_command

- Task id: `complex-failing-test-triage`
- Category: testing (multi-cause debugging)
- Arm: `claude_code_goal_command` (Claude Code official built-in `/goal` command as completion controller)
- Run: `2026-06-28-claude-goal-vs-mission-incremental-v1`
- Date: 2026-06-28

## Goal

Produce this single task artifact demonstrating a real failing-test triage:
given a failure with **at least two plausible causes**, isolate the real cause
with measured evidence, fix the smallest surface, capture a before/after
validator narrative, and explicitly document the rejected hypotheses.

The `/goal` Stop hook held the session open with the verbatim condition: the
artifact must exist at
`benchmarks/mission-vs-goal/run-output/2026-06-28-claude-goal-vs-mission-incremental-v1/complex-failing-test-triage-claude_code_goal_command.md`
and include the headings Goal, Result, Evidence, Assumptions, Stop Condition.

## Result

- **Real cause isolated:** `datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")`
  returns a **naive** datetime even though it consumes the literal `Z`. Subtracting
  it from a timezone-aware `now` raises `TypeError: can't subtract offset-naive and
  offset-aware datetimes`.
- **Smallest fix applied:** attach UTC at the single parse site —
  `.replace(tzinfo=timezone.utc)` (one expression, one import added). No callers,
  tests, or arithmetic touched.
- **Validator narrative:** 1 failing → 1 passing. Before: `TypeError` at the
  subtraction line. After: `1 passed in 0.00s`.
- **Two competing hypotheses rejected with evidence** (not by assertion): see
  *Rejected hypotheses* below.

### Scope & honesty note (unmeasured / out of scope)

- The repository's own suite is **green** — `python3 -m pytest skills/mission/tests/`
  reported `402 passed in 27.47s` at the start of this run. There was no naturally
  failing test to triage in-tree.
- Per the benchmark rules ("write exactly one task artifact"; "keep edits narrowly
  scoped to benchmark output files"), I did **not** introduce a bug into tracked
  source. Instead the triage was run on a **self-contained reproduction fixture** in
  an ephemeral scratch dir (`/tmp/triage-fixture/`, not committed). The fixture
  deliberately mirrors a real construct in this repo: the staleness/`updated_at`
  UTC-parsing that `scripts/mission-stop-guard.sh` performs (it relies on `date -u`
  to force `Z` = UTC; the same naive-vs-aware hazard exists in Python).
- All command output quoted below was **observed** in this session. No timing,
  coverage, or cross-environment behavior beyond what is shown was measured.
- Environment: Python 3.14.5, pytest 9.0.2.

## Evidence (observed)

All blocks below are verbatim captured output from this session.

### E1 — Repo suite is green (no in-tree failure to triage)

```
$ python3 -m pytest skills/mission/tests/ -q
........................................................................ [ 17%]
...
402 passed in 27.47s
```

### E2 — Reproduction fixture (the unit under triage)

`/tmp/triage-fixture/staleness.py` (pre-fix), modeling the stop-guard staleness math:

```python
from datetime import datetime

def minutes_idle(updated_at, now):
    parsed = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
    delta = now - parsed
    return int(delta.total_seconds() // 60)
```

`/tmp/triage-fixture/test_staleness.py`:

```python
from datetime import datetime, timezone
from staleness import minutes_idle

def test_idle_two_hours_utc():
    now = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)
    assert minutes_idle("2026-06-28T10:00:00Z", now) == 120
```

### E3 — BEFORE: validator is red (observed failure mode)

```
$ python3 -m pytest /tmp/triage-fixture/test_staleness.py -q
F                                                                        [100%]
=================================== FAILURES ===================================
___________________________ test_idle_two_hours_utc ____________________________
    def minutes_idle(updated_at, now):
        parsed = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")
>       delta = now - parsed
E       TypeError: can't subtract offset-naive and offset-aware datetimes
/tmp/triage-fixture/staleness.py:13: TypeError
=========================== short test summary info ============================
FAILED .../test_staleness.py::test_idle_two_hours_utc
1 failed in 0.01s
```

Key observed facts:
- The exception is a **`TypeError`**, raised at the **subtraction** line
  (`delta = now - parsed`), *not* an `AssertionError` about a wrong number.
- `parsed` is naive; `now` is `tzinfo=datetime.timezone.utc` (aware). The mismatch
  is the trigger.

### E4 — Isolation probe: is the caller's `now` to blame? (counter-evidence)

Re-ran the **original buggy parse** against a *correct, timezone-aware UTC* `now`,
to test whether the defect lives in callers rather than in `minutes_idle`:

```
$ python3  # buggy parse, correct tz-aware caller
with correct tz-aware caller: STILL FAILS -> can't subtract offset-naive and offset-aware datetimes
```

Observed: even when the caller supplies a flawless tz-aware UTC `now`, the buggy
parse **still raises the same `TypeError`**. The trigger is internal to
`minutes_idle`.

### E5 — Smallest-surface fix

One parse site changed (plus one import); diff intent:

```python
# from datetime import datetime
from datetime import datetime, timezone
...
    parsed = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
```

No change to: the test, the `now` callers, the `total_seconds() // 60` arithmetic,
or the public signature.

### E6 — AFTER: validator is green

```
$ python3 -m pytest /tmp/triage-fixture/test_staleness.py -q
.                                                                        [100%]
1 passed in 0.00s
```

## Rejected hypotheses (kept separate from observed evidence above)

Both were genuinely plausible *a priori*; each is rejected by a specific observation,
not by intuition.

### H-A (ACCEPTED — real cause)
`strptime("...%SZ")` discards the timezone: the literal `Z` is matched as a character
but produces a **naive** datetime. → Confirmed by E3 (TypeError at subtraction) and by
E6 (attaching UTC at the parse site turns the suite green). This is the contract
violation: the function advertises UTC (`Z`) input but drops the zone.

### H-B (REJECTED) — "the minutes math is wrong (`// 60` floor division / off-by-one)"
*Why it was plausible:* integer floor division and `total_seconds()` rounding are a
classic source of off-by-one minute errors, and `120` is exactly the kind of boundary
value that exposes them.
*Rejected by:* E3. The failure is a **`TypeError` raised before any minutes arithmetic
executes** — control never reaches `total_seconds() // 60`. A rounding bug would surface
as an `AssertionError` comparing two integers, which was never observed. The arithmetic
line was therefore left untouched, and E6 confirms it was already correct (`== 120`).

### H-C (REJECTED) — "callers pass a bad `now`; fix every call site"
*Why it was plausible:* a naive-vs-aware `TypeError` can equally be caused by a caller
passing `datetime.now()` (naive) instead of `datetime.now(timezone.utc)`. The natural
"fix" would be to normalize `now` at all call sites.
*Rejected by:* E4. With a **correct tz-aware UTC caller**, the buggy parse *still* raises
the identical `TypeError`. Callers are not the trigger, so editing them would (1) be a
strictly larger surface and (2) leave the real contract violation in place. Smallest
correct surface = the single parse site (E5).

## Assumptions

- The benchmark intent is to demonstrate triage **discipline and evidence**, not to
  inject a defect into tracked source — so the reproduction fixture lives in an
  uncommitted scratch dir and only this one artifact file is written. Treated as a
  fixed constraint from the run rules.
- `now` in the failing scenario is timezone-aware UTC, matching how the real
  `mission-stop-guard.sh` derives "now" from `date +%s` / `date -u`. Assumed
  representative; the cross-shell `date` path itself was **not** re-measured here.
- "Smallest surface" = fewest production lines that fully resolve the failure without
  changing the public contract. Normalizing at the parse site satisfies this; the
  arithmetic and callers are out of scope because evidence (E3, E4) excludes them.
- No claim of benchmark superiority is made; this artifact only completes the task.

## Stop Condition

Stop when **all** hold (all now satisfied):

1. Artifact exists at the required path with headings Goal, Result, Evidence,
   Assumptions, Stop Condition. ✅
2. Observed evidence is separated from rejected hypotheses, and a before/after
   validator narrative is present (E3 red → E6 green). ✅
3. Exactly one real cause is isolated (H-A) and at least one rival cause is rejected
   with a concrete observation (H-B via E3, H-C via E4). ✅
4. The fix is the smallest surface that turns the validator green (E5 → E6), with no
   out-of-scope edits and no network/install/commit actions. ✅
5. Unmeasured items are labeled as unmeasured (Result + Assumptions). ✅

The `/goal` Stop hook auto-clears once condition 1 holds; conditions 2–5 are the
task-quality bar this artifact was written to meet.
