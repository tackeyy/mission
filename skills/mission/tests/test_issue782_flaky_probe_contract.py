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


def test_the_verdict_is_decided_where_a_test_can_reach_it():
    """The classification must not live in the workflow's shell block.

    It did, and two defects rode in with it: the snippet was indented in a way
    the pinned 3.12 refuses, and it read the report before the exit status, so
    a failing run (which never gets a report) would have counted as "nothing
    ran". Neither was reachable by a test while it sat in YAML.

    `test_issue782_probe_classify.py` exercises the rules themselves; this only
    fixes that the workflow keeps delegating to them.
    """
    assert "scripts/probe_classify.py" in PROBE
    assert "MISSION_SUITE_REPORT" in PROBE
    # A verdict the classifier did not produce must stop the probe rather than
    # be counted as anything.
    assert "the probe is broken" in PROBE


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

    The body is delimited by indentation, not by what the lines look like: an
    earlier version ended the block at the first line matching `key:`, which
    the `try:` inside an embedded Python snippet satisfied -- and the ~70 lines
    after it went unchecked. Two planted interpolations survived that version.
    """
    lines = PROBE.splitlines()
    offenders: list[str] = []
    body_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if body_indent is not None:
            if indent > body_indent:
                if "${{" in line:
                    offenders.append(stripped)
                continue
            body_indent = None  # dedented back to the step: the body ended
        if re.match(r"^(- )?run: \|", stripped):
            body_indent = indent
    assert not offenders, offenders


def test_the_summary_is_computed_where_a_test_can_reach_it():
    """The bound decides how the run is read, so it must be testable.

    The first version computed it in the workflow from the inputs alone and
    never read what the cells reported. `test_issue782_probe_summarise.py`
    exercises the rules; this fixes that the workflow keeps delegating.
    """
    assert "scripts/probe_summarise.py" in PROBE
    assert "download-artifact" in PROBE
    assert "cell.json" in PROBE


def test_artifact_names_survive_a_slash_in_the_ref():
    """Artifact names reject `/`, and this repo's branches contain one.

    Without the substitution the upload fails exactly when there is something
    worth uploading.
    """
    assert 'safe_ref="${REF//\\//-}"' in PROBE
    assert "cell-${{ steps.probe.outputs.safe_ref }}" in PROBE


def test_the_probe_writes_no_dependency_cache():
    """This job runs an arbitrary ref's code on a trusted trigger.

    `workflow_dispatch` is a trusted write trigger, so a cache saved here
    lands in the default branch's scope -- the same key space `ci.yml`
    restores from. Caching under those two facts turns the probe into a way
    to poison every later CI run, which merging it would be what opens.
    """
    executable = "\n".join(
        line for line in PROBE.splitlines() if not line.lstrip().startswith("#")
    )
    assert "cache:" not in executable, "the probe caches on a trusted trigger"
    assert "cache-dependency-path" not in executable


def test_pull_request_refs_are_refused():
    """`refs/pull/*` is code from anyone who can open a PR."""
    assert 'r.startswith("refs/pull/")' in PROBE


def test_one_runner_per_ref_is_refused():
    """The confounding the header names must be refused, not just described."""
    assert re.search(r"if runners < 2:", PROBE), "runners: 1 passes validation"


def test_the_summary_is_told_which_refs_were_asked_for():
    """Without it, a ref whose cells all died leaves the table silently.

    The surviving ref then prints `measured == requested` with a clean bound,
    which reads as the answer to a comparison that never happened. It is the
    guaranteed shape of the first dispatch: the base ref does not carry these
    scripts yet.
    """
    # Read the executable lines only. The comment above the call explains why
    # `--refs` is passed, and a check over the whole file would be satisfied by
    # that explanation alone: deleting the flag from the call survived this
    # test until the comments were excluded.
    executable = "\n".join(
        line for line in PROBE.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--refs" in executable, "the summary is no longer told what was requested"
    assert "REFS: ${{ inputs.refs }}" in executable
