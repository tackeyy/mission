# Goal

Reproduce a small parser bug with a failing targeted assertion, identify the minimal fix, and leave evidence that the targeted parser behavior passes after the fix.

# Result

Completed the benchmark artifact for the `goal_only` arm. The small parser bug is in `scripts/mission-audit.py::parse_dt`: valid ISO timestamps fail when the input string has leading or trailing whitespace.

The minimal fix is to strip the value before parsing:

```python
datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
```

Per the controlled-run rule to keep edits scoped to benchmark output files, I did not edit source or test files in this checkout. The targeted before/after validator was run as local inline Python evidence instead.

# Evidence

Current working tree before artifact creation:

```text
$ git status --short --branch
## HEAD (no branch)
```

Existing related test file still passes:

```text
$ python3 -m pytest skills/mission/tests/test_mission_audit.py -q
....................................                                     [100%]
36 passed in 2.58s
```

Targeted failing reproduction against the current parser:

```text
$ python3 - <<'PY'
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('mission_audit', Path('scripts/mission-audit.py'))
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
assert mod.parse_dt('2026-06-27T00:00:00Z') is not None
assert mod.parse_dt(' 2026-06-27T00:00:00Z ') is not None
PY
Traceback (most recent call last):
  File "<stdin>", line 9, in <module>
AssertionError
```

Targeted passing result with the minimal fix applied in the local test harness:

```text
$ python3 - <<'PY'
import importlib.util
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('mission_audit', Path('scripts/mission-audit.py'))
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
original = mod.parse_dt

def parse_dt_fixed(value):
    if not value:
        return None
    return original(value.strip())

assert parse_dt_fixed('2026-06-27T00:00:00Z') is not None
assert parse_dt_fixed(' 2026-06-27T00:00:00Z ') is not None
assert parse_dt_fixed('not-a-date') is None
print('focused parser regression passed with strip-before-parse fix')
PY
focused parser regression passed with strip-before-parse fix
```

Non-artifact source diff check before writing this file:

```text
$ git diff --stat -- . ':!benchmarks/mission-vs-goal/run-output'
```

No output was produced.

# Assumptions

- The benchmark instruction "Keep edits narrowly scoped to benchmark output files" overrides the coding task's normal expectation to modify source and test files.
- Network access was not used, so the AGENTS.md `git pull --ff-only` startup rule was intentionally not run.
- The validator signal is represented by the focused before/after inline assertions rather than a committed regression test, because this `goal_only` run was instructed to write exactly one benchmark artifact.
