"""#782: what the probe workflow must keep, pinned where the guard does not reach.

`test_actions_cost_guard.py` reads `ci.yml` and nothing else, so a second
workflow is outside it: `on: [push]` could be added here and every test would
stay green.  The probe's whole justification for living outside `ci.yml` is
that it costs nothing until asked for, and that justification is only worth
writing down if something checks it.

The rest of what is pinned here is the handful of properties that cross-model
review and two reviewers found missing in the first version, each of which
would let the probe report `0 failed` having measured nothing:

  * building the pytest command here instead of calling `make test` (CI runs
    the suite with `-n auto --dist loadfile`; running the test alone measures
    a different system);
  * counting exit codes without asking how many tests executed;
  * giving each ref a single runner, which confounds the ref with the host.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROBE_PATH = ROOT / ".github/workflows/flaky-probe.yml"
PROBE = PROBE_PATH.read_text(encoding="utf-8")


def test_the_probe_runs_only_when_asked():
    """The reason it may live outside ci.yml is that nothing schedules it.

    Read as text rather than through PyYAML: the CI image installs only what
    `.github/requirements-ci.txt` lists (pytest and pytest-xdist), so a test
    that imports yaml would skip in exactly the place this needs to hold.
    A check that cannot run where it matters is not a check.
    """
    lines = PROBE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == "on:")
    triggers = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" "):
            break  # back to column 0: the `on:` block ended
        match = re.fullmatch(r"  ([a-z_]+):", line.rstrip())
        if match:
            triggers.append(match.group(1))
    assert triggers == ["workflow_dispatch"], triggers


def test_the_suite_command_comes_from_the_makefile():
    """A probe that builds its own pytest call measures a different system.

    CI runs `-n auto --dist loadfile` (Makefile). The failure this probe was
    written for appeared under that parallelism, so the probe has to reach it
    through `make`, not reimplement it.
    """
    # Comments are allowed to name the flags -- explaining why they are not
    # here is the point of them.  What must not appear is a call that runs
    # pytest directly, so the check reads the executable lines only.
    executable = "\n".join(
        line for line in PROBE.splitlines() if not line.lstrip().startswith("#")
    )
    assert "make test" in executable
    for reimplemented in ("-m pytest", "--dist loadfile", "-n auto"):
        assert reimplemented not in executable, (
            f"the probe spells out {reimplemented!r}; it must come from the Makefile"
        )


def test_each_run_checks_how_many_tests_executed():
    """`Makefile` says an exit code cannot tell a pass from a run that did nothing.

    The same holds one level up: a run that skips at runtime exits 0, and
    counting that as a pass is how "0 failures" comes to mean "never measured".
    """
    assert "MISSION_SUITE_REPORT" in PROBE
    assert '"executed"' in PROBE
    assert "no_result" in PROBE


def test_repeats_are_spread_over_several_runners():
    """One runner per ref confounds the ref with the machine it ran on."""
    assert "runners" in PROBE
    assert re.search(r"for r in refs for i in range\(1, runners \+ 1\)", PROBE), (
        "the matrix no longer crosses refs with runners"
    )


def test_a_single_ref_is_refused():
    """One ref produces numbers, but not a comparison."""
    assert "one ref is not a comparison" in PROBE


def test_inputs_never_reach_the_shell_through_interpolation():
    """`${{ }}` inside a `run:` body is evaluated before the shell sees it.

    Every input here is user-supplied, including the ones that arrive back
    through `matrix`, so they are passed as environment variables instead.
    """
    in_run_block = False
    offenders: list[str] = []
    for line in PROBE.splitlines():
        stripped = line.strip()
        if re.match(r"^(run:|- run:)", stripped) or stripped.endswith("run: |"):
            in_run_block = True
            continue
        if in_run_block:
            # A new key at step level ends the block.
            if re.match(r"^- |^[a-z-]+:", stripped) and "${{" not in stripped:
                in_run_block = False
                continue
            if "${{" in line:
                offenders.append(stripped)
    assert not offenders, offenders


def test_zero_failures_is_reported_as_a_bound_not_as_absence():
    """Without this the reader takes two zeros as "the change is fine"."""
    assert "does not mean the test is sound" in PROBE
    assert "0.05 ** (1 / n)" in PROBE
    assert "undetected, not absent" in PROBE
