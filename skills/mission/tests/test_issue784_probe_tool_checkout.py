"""#784: the probe measures a ref with its own instrument, not the ref's.

The probe job used to check out the ref under test and run
`scripts/probe_classify.py` / `scripts/probe_cell.py` from *that* checkout.
Two consequences, neither of which any review caught:

  * a ref that predates the scripts cannot be measured at all -- the
    classifier is missing, every run dies after a full `make test`, and both
    arms report nothing.  That is exactly the comparison #782 needs
    (`fbe89f8` against `1708410`, both older than the scripts);
  * two refs carrying different versions of the classifier are compared with
    two different instruments, so a difference between refs can be a
    difference between classifiers.  It is the confounding the workflow's
    header forbids for hosts, arriving through the tool instead.

These tests run the probe step's shell with a stubbed `make`, against a
subject directory and a separate tools directory.  The subject's copy of the
classifier is deliberately wrong -- it answers `passed` no matter what -- so a
probe that reaches for it reports a pass for a run that failed.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE = (ROOT / ".github/workflows/flaky-probe.yml").read_text(encoding="utf-8")
TEST_FILE = "skills/mission/tests/test_example.py"

LYING_CLASSIFIER = """import sys
print("passed")
sys.exit(0)
"""


def _step_body() -> str:
    """Lift the probe step's shell out of the workflow.

    Fails rather than skipping when the step moves: a check that quietly
    stops running is the failure this file exists to prevent.
    """
    lines = PROBE.splitlines()
    name = next(
        (i for i, line in enumerate(lines) if line.strip() == "- name: Repeat the suite as CI runs it"),
        None,
    )
    assert name is not None, "the probe step was renamed or removed"
    run = next(i for i in range(name, len(lines)) if lines[i].strip() == "run: |")
    run_indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _harness(tmp_path: Path, *, makefile_reports: bool, has_test_file: bool, subject_scripts: bool):
    subject = tmp_path / "subject"
    tools = tmp_path / "probe-tools"
    bindir = tmp_path / "bin"
    for directory in (subject, tools / "scripts", bindir):
        directory.mkdir(parents=True)

    # The instrument: the real classifier and cell writer from this checkout.
    for name in ("probe_classify.py", "probe_cell.py"):
        shutil.copy(ROOT / "scripts" / name, tools / "scripts" / name)

    # The subject.  Its Makefile either knows MISSION_SUITE_REPORT or it does
    # not; the probe must be able to tell without running it.
    recipe = "test:\n\t@echo run\n"
    if makefile_reports:
        recipe = "# writes $(MISSION_SUITE_REPORT) when set\n" + recipe
    (subject / "Makefile").write_text(recipe, encoding="utf-8")
    if has_test_file:
        (subject / TEST_FILE).parent.mkdir(parents=True)
        (subject / TEST_FILE).write_text("def test_x():\n    pass\n", encoding="utf-8")
    if subject_scripts:
        (subject / "scripts").mkdir()
        (subject / "scripts" / "probe_classify.py").write_text(LYING_CLASSIFIER, encoding="utf-8")
        shutil.copy(ROOT / "scripts" / "probe_cell.py", subject / "scripts" / "probe_cell.py")

    make_log = tmp_path / "make.log"
    make_log.touch()
    _executable(
        bindir / "make",
        """#!/bin/sh
echo "$PWD" >> "$MAKE_LOG"
report=
for arg in "$@"; do
  case "$arg" in MISSION_SUITE_REPORT=*) report="${arg#MISSION_SUITE_REPORT=}";; esac
done
if [ "${STUB_MAKE_STATUS:-0}" != 0 ]; then exit "$STUB_MAKE_STATUS"; fi
[ -n "$report" ] && printf '{"executed": 3}' > "$report"
exit 0
""",
    )
    # `timeout` is GNU coreutils; the runner has it, a developer machine may not.
    _executable(bindir / "timeout", "#!/bin/sh\nshift\nexec \"$@\"\n")

    probe_dir = tmp_path / "probe"
    output = tmp_path / "github_output"
    output.touch()
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "MAKE_LOG": str(make_log),
        "TEST_FILE": TEST_FILE,
        "REPEATS": "2",
        "REF": "feat/example",
        "RUNNER_INDEX": "1",
        "PROBE_DIR": str(probe_dir),
        "PROBE_TOOLS": str(tools),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    script = tmp_path / "step.sh"
    script.write_text(_step_body(), encoding="utf-8")
    return subject, env, script, make_log, probe_dir


def _run(subject: Path, env: dict, script: Path, **extra):
    return subprocess.run(
        ["bash", str(script)],
        cwd=subject,
        env={**env, **extra},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _cell(probe_dir: Path) -> dict:
    return json.loads((probe_dir / "cell.json").read_text(encoding="utf-8"))


def test_a_failing_run_is_classified_by_the_probes_classifier(tmp_path):
    """The subject's classifier lies; the probe must not ask it.

    `make test` fails on every run.  The probe's own classifier calls that a
    failure.  The subject's copy answers `passed` regardless -- a probe that
    uses it reports a clean ref.
    """
    subject, env, script, _, probe_dir = _harness(
        tmp_path, makefile_reports=True, has_test_file=True, subject_scripts=True
    )
    result = _run(subject, env, script, STUB_MAKE_STATUS="2")
    assert (probe_dir / "cell.json").exists(), result.stdout + result.stderr
    cell = _cell(probe_dir)
    assert cell["failed"] == 2, f"the subject's classifier was used: {cell}"
    assert cell["passed"] == 0, cell


def test_a_ref_older_than_the_scripts_is_still_measured(tmp_path):
    """`fbe89f8` and `1708410` carry no probe scripts; #782 needs both."""
    subject, env, script, make_log, probe_dir = _harness(
        tmp_path, makefile_reports=True, has_test_file=True, subject_scripts=False
    )
    result = _run(subject, env, script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _cell(probe_dir) == {
        "ref": "feat/example",
        "runner": 1,
        "failed": 0,
        "no_result": 0,
        "passed": 2,
    }
    assert make_log.read_text(encoding="utf-8").count("\n") == 2, "make ran a different number of times"


def test_make_runs_in_the_subject_not_in_the_tools(tmp_path):
    """The suite being measured is the ref's; only the instrument is the probe's."""
    subject, env, script, make_log, _ = _harness(
        tmp_path, makefile_reports=True, has_test_file=True, subject_scripts=False
    )
    _run(subject, env, script)
    ran_in = set(make_log.read_text(encoding="utf-8").split())
    assert ran_in == {str(subject)}, ran_in


def test_a_makefile_that_cannot_write_a_report_is_refused_before_make_runs(tmp_path):
    """Without a report, every run is a no-result -- a full suite spent per run.

    `MISSION_SUITE_REPORT` arrived in #740. A ref before it exits 0 and writes
    nothing, so the probe would spend a complete `make test` on each repeat to
    learn nothing.  Refuse the cell up front instead.
    """
    subject, env, script, make_log, probe_dir = _harness(
        tmp_path, makefile_reports=False, has_test_file=True, subject_scripts=False
    )
    result = _run(subject, env, script)
    assert result.returncode != 0, result.stdout
    assert "suite report" in result.stdout, result.stdout
    assert make_log.read_text(encoding="utf-8") == "", "make ran before the refusal"
    assert not (probe_dir / "cell.json").exists(), "a refused cell must report nothing"


def test_a_subject_without_the_test_file_is_refused_before_make_runs(tmp_path):
    subject, env, script, make_log, probe_dir = _harness(
        tmp_path, makefile_reports=True, has_test_file=False, subject_scripts=False
    )
    result = _run(subject, env, script)
    assert result.returncode != 0, result.stdout
    assert "cannot be measured" in result.stdout, result.stdout
    assert make_log.read_text(encoding="utf-8") == "", "make ran before the refusal"
    assert not (probe_dir / "cell.json").exists()


def test_the_harness_reaches_the_step_it_claims_to(tmp_path):
    """A control: the lifted body is the probe step, and the stubs are on PATH.

    Without this, every test above could pass against a body that no longer
    contains the loop -- the refusals would "succeed" for any reason.
    """
    body = _step_body()
    assert "make test" in body and "for i in" in body, body[:400]
    subject, env, script, make_log, _ = _harness(
        tmp_path, makefile_reports=True, has_test_file=True, subject_scripts=False
    )
    _run(subject, env, script)
    assert make_log.read_text(encoding="utf-8"), "the stub make was never reached"


def test_a_classifier_that_does_not_answer_stops_the_probe(tmp_path):
    """The error branch must fail, under every bash the step can meet.

    It used `${verdict@Q}`, which needs bash 4.4.  Under an older bash the
    expansion is a `bad substitution`, and the step exited **0** -- a probe
    whose instrument returned nonsense read as a probe that finished.  The
    runner has bash 5, so CI never showed it; a developer machine did.
    """
    subject, env, script, _, probe_dir = _harness(
        tmp_path, makefile_reports=True, has_test_file=True, subject_scripts=False
    )
    tools = Path(env["PROBE_TOOLS"])
    (tools / "scripts" / "probe_classify.py").write_text('print("maybe")\n', encoding="utf-8")
    result = _run(subject, env, script)
    assert result.returncode != 0, f"a nonsense verdict was accepted: {result.stdout}"
    assert "the probe is broken" in result.stdout, result.stdout + result.stderr
    assert "bad substitution" not in result.stderr, result.stderr
    assert not (probe_dir / "cell.json").exists(), "a run with no verdict was recorded"
