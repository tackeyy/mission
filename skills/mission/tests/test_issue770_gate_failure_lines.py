"""Issue #770: surface bounded, safe diagnostics when a declared suite fails."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import integration_gate as gate  # noqa: E402


def test_excerpt_includes_matching_failure_lines_and_the_tail():
    """A match alone is not enough context to diagnose every runner's failure."""
    excerpt = gate.suite_failure_excerpt(
        "setup\nFAILED tests/unit.py::test_result\ncleanup\n",
        "traceback detail\n",
        limit=4000,
    )

    assert "FAILED tests/unit.py::test_result" in excerpt
    assert "traceback detail" in excerpt


def test_excerpt_finds_decorated_and_tap_failure_words_in_both_streams():
    """A declared suite may use any runner, so its failure format is not fixed."""
    excerpt = gate.suite_failure_excerpt(
        "--- FAIL: GoCase\n[ERROR] MavenCase\n> Task :test FAILED\nnot ok 3 - TAP case\n",
        "FAILED stderr-case\n",
        limit=4000,
    )

    for line in (
        "--- FAIL: GoCase",
        "[ERROR] MavenCase",
        "> Task :test FAILED",
        "not ok 3 - TAP case",
        "FAILED stderr-case",
    ):
        assert line in excerpt


def test_excerpt_classifies_ascii_punctuation_wrapped_markers_as_matches():
    """Decorated runner markers must not be demoted to ordinary tail output."""
    excerpt = gate.suite_failure_excerpt(
        "[ERROR] MavenCase\n--- FAIL: GoCase\nordinary tail\n", "", limit=4000
    )

    assert "[suite_failure] matched failure lines:" in excerpt
    assert "[suite_failure] [ERROR] MavenCase" in excerpt
    assert "[suite_failure] --- FAIL: GoCase" in excerpt


def test_excerpt_classifies_tap_not_ok_as_a_match():
    """TAP failures need the same priority as other runner failure markers."""
    excerpt = gate.suite_failure_excerpt(
        "not ok 3 - TAP case\nordinary tail\n", "", limit=4000
    )

    assert "[suite_failure] matched failure lines:" in excerpt
    assert "[suite_failure] not ok 3 - TAP case" in excerpt


def test_excerpt_keeps_stdout_and_stderr_failure_records_separate_without_newlines():
    """Stream boundaries must not turn two independently useful lines into one."""
    excerpt = gate.suite_failure_excerpt("FAILED stdout", "ERROR stderr", limit=4000)

    assert "[suite_failure] FAILED stdout" in excerpt
    assert "[suite_failure] ERROR stderr" in excerpt


def test_excerpt_does_not_match_failure_words_embedded_in_other_words():
    """Word fragments would turn ordinary diagnostics into false failure locations."""
    excerpt = gate.suite_failure_excerpt(
        "FAILURE is not a marker\nerror is ordinary prose\nnot okay either\n",
        "",
        limit=4000,
    )

    assert "matched failure lines:" not in excerpt
    assert "FAILURE is not a marker" in excerpt


def test_excerpt_labels_matches_and_tail_without_repeating_the_same_line():
    """Both views retain context without spending the bounded output twice."""
    excerpt = gate.suite_failure_excerpt("FAILED only-line\n", "", limit=4000)

    assert "matched failure lines:" in excerpt
    assert "output tail:" in excerpt
    assert excerpt.count("FAILED only-line") == 1


def test_excerpt_reserves_space_for_tail_and_marks_truncation():
    """A noisy matcher must not hide the final diagnostic that explains the stop."""
    excerpt = gate.suite_failure_excerpt(
        "".join("FAILED marker-{}\n".format(index) for index in range(30))
        + "tail sentinel\n",
        "",
        limit=240,
    )

    assert len(excerpt) <= 240
    assert "output truncated" in excerpt
    assert "tail sentinel" in excerpt


def test_excerpt_uses_the_final_nonmatching_lines_for_its_tail():
    """Tail context must remain useful when earlier output is much longer."""
    excerpt = gate.suite_failure_excerpt(
        "FAILED marker\n" + "old context " * 30 + "\nfinal tail sentinel\n",
        "",
        limit=180,
    )

    assert "final tail sentinel" in excerpt


def test_excerpt_removes_controls_and_every_emitted_line_has_the_fixed_prefix():
    """Only newline may separate records after untrusted suite output is surfaced."""
    controls = "\x00\x01\x1b\r\x7f\u0085\u009b\u009d\u2028\u2029"
    excerpt = gate.suite_failure_excerpt(
        "FAILED unsafe{}\n".format(controls),
        "suite_exit=0\u0085forged\n",
        limit=4000,
    )

    assert not any(character in excerpt for character in controls)
    assert all(line.startswith("[suite_failure] ") for line in excerpt.splitlines())


def test_excerpt_marks_empty_output_as_unavailable():
    assert "output unavailable" in gate.suite_failure_excerpt("", "", limit=4000)


def test_failed_suite_logs_exit_and_safe_failure_excerpt(tmp_path):
    """The new diagnosis augments the existing failure, rather than changing it."""
    logged = []

    def runner(command, cwd, env=None):
        return gate.CommandResult(3, "FAILED from stdout\n", "ERROR from stderr\n")

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.run_declared_suite(
            ("suite",),
            runner=runner,
            cwd=tmp_path,
            report_path=tmp_path / "report.json",
            expected_tree_sha="a" * 40,
            step=4,
            logger=logged.append,
        )

    assert captured.value.reason == "suite-failed"
    assert "suite_exit=3" in logged
    assert any("FAILED from stdout" in line for line in logged)
    assert any("ERROR from stderr" in line for line in logged)


def test_failed_suite_logs_an_excerpt_capped_at_the_declared_limit(tmp_path):
    """The gate call site, not only the formatter, must enforce the 4,000-char cap."""
    logged = []
    noisy_output = "".join("FAILED marker-{}\n".format(index) for index in range(600))

    def runner(command, cwd, env=None):
        return gate.CommandResult(3, noisy_output, "")

    with pytest.raises(gate.IntegrationGateError):
        gate.run_declared_suite(
            ("suite",),
            runner=runner,
            cwd=tmp_path,
            report_path=tmp_path / "report.json",
            expected_tree_sha="a" * 40,
            step=4,
            logger=logged.append,
        )

    assert len(logged) == 2
    assert len(logged[1]) <= 4000
    assert "output truncated" in logged[1]


def test_successful_suite_does_not_add_logger_output(tmp_path):
    """Failure diagnostics are deliberately absent from the success path."""
    report_path = tmp_path / "report.json"
    logged = []

    def runner(command, cwd, env=None):
        report_path.write_text(
            '{{"schema":"mission-suite-report/1","tree_sha":"{}",'
            '"executed":1,"status":"complete"}}'.format("a" * 40),
            encoding="utf-8",
        )
        return gate.CommandResult(0, "FAILED but successful output", "")

    assert gate.run_declared_suite(
        ("suite",), runner=runner, cwd=tmp_path, report_path=report_path,
        expected_tree_sha="a" * 40, step=4, logger=logged.append,
    ) == 1
    assert logged == []


def test_the_embedded_word_guard_would_reject_a_substring_matching_mutation(monkeypatch):
    """This regression test is intentionally sensitive to a substring matcher."""
    monkeypatch.setattr(gate, "_is_failure_line", lambda line: "FAIL" in line)

    excerpt = gate.suite_failure_excerpt("FAILURE is not a marker\n", "", limit=4000)

    assert "matched failure lines:" in excerpt
