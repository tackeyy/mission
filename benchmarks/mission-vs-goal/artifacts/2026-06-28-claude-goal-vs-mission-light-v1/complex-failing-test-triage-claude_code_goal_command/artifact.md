# complex-failing-test-triage — claude_code_goal_command

- Task id: `complex-failing-test-triage`
- Category: testing (multi-cause debugging)
- Arm: `claude_code_goal_command` (Claude Code official built-in `/goal` command as completion controller)
- Run: `2026-06-28-claude-goal-vs-mission-light-v1`
- Date: 2026-06-28

## Goal

Produce this single task artifact demonstrating a real failing-test triage:
given a failure with **at least two plausible causes**, isolate the real cause
with measured evidence, identify the smallest fix surface, capture a
before/after validator narrative, and explicitly document the rejected
hypotheses — separating observed evidence from hypotheses.

The `/goal` Stop hook held the session open with the verbatim condition: the
artifact must exist at
`benchmarks/mission-vs-goal/run-output/2026-06-28-claude-goal-vs-mission-light-v1/complex-failing-test-triage-claude_code_goal_command.md`
and include the headings Goal, Result, Evidence, Assumptions, Stop Condition.

## Result

- **The in-tree suite is green** (`402 passed`, E1). Rather than inject a bug into
  tracked source (forbidden by the run rules), I triaged a **real failure that the
  green suite is one environment variable away from**: `test_codex_thread_id`
  in `skills/mission/tests/test_session_id_env.py`.
- **Observed failure (E3):** with the test harness's isolation fixture bypassed,
  `test_codex_thread_id` fails:
  `AssertionError: assert 'cc-1048c950-a2c2-4a7e-94aa-46f502c36fea' == 'cx-cxid'`.
- **Two genuinely competing causes** for that assertion:
  - H1 — product regression: `resolve_session_id()` mis-orders Codex vs Claude Code,
    or Codex detection is broken.
  - H2 — test-environment leakage: the ambient shell exports
    `CLAUDE_CODE_SESSION_ID`, which **legitimately** outranks `CODEX_THREAD_ID`, and
    the failure only appears because the isolation fixture was disabled.
- **Real cause isolated → H2.** The leaked value in the assertion
  (`cc-1048c950-…`) is this session's *actual* `CLAUDE_CODE_SESSION_ID`, and the
  documented priority order (`MISSION_SESSION_ID > CLAUDE_CODE_SESSION_ID >
  CODEX_THREAD_ID`, E5) makes that the **correct** product behavior. The product is
  not buggy; the determinism comes entirely from the `conftest.py` autouse fixture.
- **Smallest fix surface (E6):** the load-bearing isolation already exists
  (`conftest._isolate_session_env`); the observed failure is produced only by
  bypassing it. The smallest *hardening* that closes the broader latent class is a
  ~2-line change in that one fixture (explicit 5-name denylist → `MISSION_*`-prefix
  sweep, matching what `run_cli` already does for subprocesses). **This source edit
  was NOT applied** — this arm forbids edits outside benchmark output.
- **Validator narrative (E2 ↔ E3):** AFTER (isolation active, default) `6 passed`;
  BEFORE (isolation disabled via `--noconftest`) `1 failed, 5 passed`.

### Scope & honesty note (unmeasured / out of scope)

- All command output quoted below was **observed in this session**. Environment:
  `python3` + `pytest 9.0.2` (E0). No timing, coverage, or CI-environment behavior
  beyond what is shown was measured.
- Per the run rules ("write exactly one task artifact"; "keep edits narrowly scoped
  to benchmark output files"; this is **not** the mission arm, so `.mission-state/`
  is not in scope either), I edited **no** product or test source and wrote **no**
  scratch files. The before/after was therefore produced with `pytest --noconftest`
  (a read-only invocation flag), not by mutating code.
- I did **not** run the whole suite under a deliberately polluted environment via an
  `env VAR=val pytest …` invocation: that command form required an interactive
  approval that is unavailable in this autonomous run. That full-suite pollution
  sweep is therefore **unmeasured** (see Assumptions). The single-file `--noconftest`
  probe (E3) is what stands in for it, and it is sufficient to isolate the cause.
- The broader latent gap (`MISSION_SKILL_ROOTS`, `MISSION_FORCE_PID_IS_AGENT` are
  read by product but absent from the in-process denylist, E4) is **not currently
  observed to break any test** — `test_tier4_coverage` patches `_pid_is_agent`
  directly, and the subprocess specialist tests pass `--no-default-skill-roots` /
  override `HOME`. It is a latent-risk finding, partly **unmeasured** (I did not
  exhaustively prove no in-process test is affected).

## Evidence

(All blocks below are verbatim captured output observed in this session.)

### E0 — Toolchain

```
$ python3 -m pytest --version
pytest 9.0.2
```

### E1 — Repo suite is green (no in-tree failure to triage)

```
$ python3 -m pytest -q skills/mission
........................................................................ [ 17%]
........................................................................ [ 35%]
........................................................................ [ 53%]
........................................................................ [ 71%]
........................................................................ [ 89%]
..........................................                               [100%]
402 passed in 32.93s
```

### E2 — AFTER: validator green with isolation fixture active (default)

```
$ python3 -m pytest -q skills/mission/tests/test_session_id_env.py
......                                                                   [100%]
6 passed in 0.01s
```

### E3 — BEFORE: validator red when the isolation fixture is bypassed

`--noconftest` disables `conftest.py`, so the autouse `_isolate_session_env`
fixture does not run and the ambient environment leaks into the in-process test:

```
$ python3 -m pytest -q --noconftest skills/mission/tests/test_session_id_env.py
..F...                                                                   [100%]
=================================== FAILURES ===================================
_____________________________ test_codex_thread_id _____________________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x1092ab0b0>

    def test_codex_thread_id(monkeypatch):
        m = _load()
        monkeypatch.setenv("CODEX_THREAD_ID", "cxid")
>       assert m.resolve_session_id() == "cx-cxid"
E       AssertionError: assert 'cc-1048c950-...-46f502c36fea' == 'cx-cxid'
E
E         - cx-cxid
E         + cc-1048c950-a2c2-4a7e-94aa-46f502c36fea

skills/mission/tests/test_session_id_env.py:31: AssertionError
=========================== short test summary info ============================
FAILED skills/mission/tests/test_session_id_env.py::test_codex_thread_id - As...
1 failed, 5 passed in 0.02s
```

Key observed facts:
- Exactly **one** test flips: `test_codex_thread_id` — the only test in the file
  that sets `CODEX_THREAD_ID` *without* also setting `MISSION_SESSION_ID` or
  `CLAUDE_CODE_SESSION_ID`. Every other test sets a higher-priority var itself, so
  it is immune to the leak. This selective failure pattern is itself diagnostic.
- The "wrong" value is **not arbitrary**: `cc-1048c950-a2c2-4a7e-94aa-46f502c36fea`
  is the ambient `CLAUDE_CODE_SESSION_ID` (this is a Claude Code session), `cc-`
  prefix and all.

### E4 — Product env reads vs. harness isolation (the gap, from source)

Product reads (grep over `skills/mission/bin`, `skills/mission/lib`, `scripts`):

```
mission-state.py:338:    env = os.environ.get("MISSION_SEARCH_ROOTS")
mission-state.py:397:    sid = os.environ.get("MISSION_SESSION_ID")
mission-state.py:400:    cc  = os.environ.get("CLAUDE_CODE_SESSION_ID")
mission-state.py:403:    cx  = os.environ.get("CODEX_THREAD_ID")
mission-state.py:754:    env = os.environ.get("MISSION_SKILL_ROOTS")
mission-state.py:2727:   if os.environ.get("MISSION_FORCE_PID_IS_AGENT") == "1":
```

Two isolation mechanisms, **asymmetric** coverage:

- `conftest._isolate_session_env` (in-process, autouse) deletes an **explicit list**:
  `MISSION_MULTI_SESSION, MISSION_SESSION_ID, MISSION_SEARCH_ROOTS,
  CLAUDE_CODE_SESSION_ID, CODEX_THREAD_ID`. It does **not** cover
  `MISSION_SKILL_ROOTS` or `MISSION_FORCE_PID_IS_AGENT`.
- `conftest.run_cli` (subprocess) masks the **whole `MISSION_*` prefix** plus the
  session vars: `{k: v for k, v in os.environ.items() if not k.startswith("MISSION_")
  and k not in _SESSION_ENV_VARS}`.

So `test_codex_thread_id` is protected *today* (CC id is on the explicit list and is
deleted when conftest runs), which is why E2 is green; E3 just removes that
protection. The two uncovered vars are the latent extension of the same gap.

### E5 — `resolve_session_id` priority is intentional (rejects H1)

```python
# skills/mission/bin/mission-state.py:393-406
def resolve_session_id() -> str:
    """...優先順: MISSION_SESSION_ID(明示) > Claude Code/Codex の native session env ..."""
    sid = os.environ.get("MISSION_SESSION_ID")
    if sid:
        return _sanitize_sid(sid)
    cc = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if cc:
        return f"cc-{_sanitize_sid(cc)}"
    cx = os.environ.get("CODEX_THREAD_ID")
    if cx:
        return f"cx-{_sanitize_sid(cx)}"
    return f"pid-{find_agent_pid()}"
```

The CC-over-Codex ordering is the **documented contract**, and tests
`test_priority_mission_session_id_wins` and `test_cc_wins_over_codex` (both green in
E2) assert exactly this order. Returning `cc-…` when a CC id is present is therefore
correct behavior, not a regression.

### E6 — Smallest fix surface (described, NOT applied)

The observed failure (E3) is cured by the fixture that already exists — re-enabling
conftest (the default invocation) turns E3 back into E2. To close the **broader
latent class** (E4) at the smallest surface, the one fixture would change from an
explicit denylist to a prefix sweep, mirroring `run_cli`:

```python
# conftest.py  _isolate_session_env  (proposed; NOT applied in this arm)
for k in list(os.environ):
    if k.startswith("MISSION_") or k in _SESSION_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
```

Surface: **one fixture, ~2 lines**, in `skills/mission/tests/conftest.py`. No product
code, no test bodies, no public contract touched. Not applied here because this arm
permits edits only to benchmark output files.

## Rejected hypotheses (kept separate from the observed evidence above)

Both were genuinely plausible *a priori*; each is rejected by a specific observation,
not by intuition.

### H2 (ACCEPTED — real cause)
The ambient environment leaks `CLAUDE_CODE_SESSION_ID`, which by design outranks
`CODEX_THREAD_ID`; the test only fails when the in-process isolation fixture is
bypassed. → Confirmed by E3 (the leaked value *is* the real CC id), E5 (priority is
the intended contract), and E2 (with isolation active the same test is green).

### H1 (REJECTED) — "product regression: Codex session id is no longer honored / priority is wrong"
*Why it was plausible:* the failing assertion is literally `... == "cx-cxid"` not
matching — read naively, "the Codex thread id isn't being used," which looks like a
Codex-detection or ordering bug in `resolve_session_id()`.
*Rejected by:* E5 + E2. The CC-over-Codex order is the documented contract and is
independently asserted by `test_cc_wins_over_codex` / `test_priority_…`, both of which
**pass** in the same E2 run. If priority were broken those would fail too; they do
not. The product returned the *correct* value for the environment it was given.

### H3 (REJECTED) — "a real bug planted in tracked tests/source is failing"
*Why it was plausible:* the task asks to triage "a failure," implying a broken test
in-tree.
*Rejected by:* E1. `python3 -m pytest -q skills/mission` reports `402 passed`; there
is no naturally failing in-tree test. The failure had to be surfaced by removing an
isolation guarantee (E3), not by a defect in committed code.

### H-aux (UNMEASURED) — "MISSION_SKILL_ROOTS / MISSION_FORCE_PID_IS_AGENT actively break an in-process test"
*Status:* the denylist gap is real (E4), but I did **not** observe any current test
flipping from those two vars (`test_tier4_coverage` patches `_pid_is_agent`;
subprocess specialist tests pass `--no-default-skill-roots` / override `HOME`).
Treated as latent risk, **not** a confirmed active failure. Marked unmeasured rather
than asserted either way.

## Assumptions

- "A failure with ≥2 plausible causes" is satisfiable by a **real environment-leak
  failure surfaced read-only** (E3), because the run rules forbid injecting a defect
  into tracked source. Treated as a fixed constraint of this arm.
- The ambient `CLAUDE_CODE_SESSION_ID` (`1048c950-…`) is specific to this run. On a
  clean CI shell that variable is absent, so the in-tree suite's greenness there is
  partly the result of a clean environment, **not** proof of complete in-process
  isolation against the E4 gap. The CI-environment behavior was **not** re-measured.
- "Smallest surface" = fewest lines that fully and durably resolve the failure
  without changing the public contract. For the observed failure that is the
  already-present autouse fixture; for the broader class it is the E6 prefix sweep in
  the same fixture. The product priority logic is excluded because E5 shows it is
  correct.
- The full-suite pollution sweep (`env MISSION_…=… pytest`) is **unmeasured** due to
  the autonomous approval gate; the `--noconftest` single-file probe substitutes for
  it and is sufficient to isolate the cause.
- No claim of benchmark superiority is made; this artifact only completes the task.

## Stop Condition

Stop when **all** hold (all now satisfied):

1. Artifact exists at the required path with headings Goal, Result, Evidence,
   Assumptions, Stop Condition. ✅
2. Observed evidence (E0–E6) is separated from rejected hypotheses (H1/H3/H-aux), and
   a before/after validator narrative is present (E3 red ↔ E2 green). ✅
3. Exactly one real cause is isolated (H2) and at least one rival cause is rejected
   with a concrete observation (H1 via E5+E2; H3 via E1). ✅
4. The smallest fix surface is identified (E6), with no out-of-scope source edits and
   no network/install/commit actions. ✅
5. Every unmeasured item is labeled as unmeasured (Result, H-aux, Assumptions). ✅

The `/goal` Stop hook auto-clears once condition 1 holds; conditions 2–5 are the
task-quality bar this artifact was written to meet.
