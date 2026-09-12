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


@pytest.mark.parametrize(
    ("output", "failure_line"),
    (
        ("[ERROR] MavenCase\nordinary tail\n", "[ERROR] MavenCase"),
        ("--- FAIL: GoCase\nordinary tail\n", "--- FAIL: GoCase"),
    ),
)
def test_excerpt_classifies_each_decorated_failure_word_in_the_matched_section(
    output, failure_line
):
    """ERROR and FAIL must not be demoted to the tail section."""
    excerpt = gate.suite_failure_excerpt(output, "", limit=4000)
    matched_section = excerpt.split("[suite_failure] output tail:\n", 1)[0]

    assert "[suite_failure] " + failure_line in matched_section


def test_excerpt_does_not_treat_punctuation_between_tap_words_as_adjacent():
    """Only adjacent TAP words identify a TAP failure."""
    excerpt = gate.suite_failure_excerpt("not --- ok 3\n", "", limit=4000)

    assert "matched failure lines:" not in excerpt


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


def test_excerpt_does_not_repeat_a_matched_final_line_under_truncation():
    """A reserved tail record must not displace an earlier nonmatching line."""
    excerpt = gate.suite_failure_excerpt("pppp\nFAILED f", "", limit=82)

    assert excerpt.count("[suite_failure] FAILED f") == 1
    assert "[suite_failure] pppp" in excerpt


def test_excerpt_keeps_matches_in_the_match_section_when_the_full_output_fits():
    """Tail capacity must not consume the sole failure record before matching."""
    excerpt = gate.suite_failure_excerpt(
        "ok line 1\nFAILED tests/test_x.py::test_a\nok line 2\nok line 3\n",
        "",
        limit=4000,
    )
    matched_section, tail_section = excerpt.split("[suite_failure] output tail:\n", 1)

    assert "[suite_failure] FAILED tests/test_x.py::test_a" in matched_section
    assert "FAILED tests/test_x.py::test_a" not in tail_section


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


def test_excerpt_marks_truncation_in_the_matched_failure_section():
    """A truncated matching excerpt must identify its omitted records."""
    output = "\n".join(
        "FAILED marker-{:03d} {}".format(index, "y" * 20) for index in range(400)
    )

    excerpt = gate.suite_failure_excerpt(output, "", limit=4000)
    assert "[suite_failure] output truncated" in excerpt


def test_excerpt_marks_truncation_at_the_declared_4000_character_limit():
    """A long no-match log must not silently omit its truncation marker."""
    output = "\n".join("l{:03d} {}".format(index, "x" * 25) for index in range(145))

    excerpt = gate.suite_failure_excerpt(output, "", limit=4000)

    assert len(excerpt) <= 4000
    assert "output truncated" in excerpt


def test_excerpt_keeps_the_actual_final_line_when_every_line_matches():
    """The tail is selected before duplicate matched records are removed."""
    excerpt = gate.suite_failure_excerpt(
        "".join("FAILED marker-{}\n".format(index) for index in range(600)),
        "",
        limit=4000,
    )

    assert excerpt.count("FAILED marker-599") == 1
    assert "output tail:" in excerpt


def test_excerpt_uses_the_final_nonmatching_lines_for_its_tail():
    """Tail context must remain useful when earlier output is much longer."""
    excerpt = gate.suite_failure_excerpt(
        "FAILED marker\n" + "old context " * 30 + "\nfinal tail sentinel\n",
        "",
        limit=180,
    )

    assert "final tail sentinel" in excerpt


def test_excerpt_uses_final_no_match_lines_for_its_tail():
    """Runners without English markers still need their final diagnostics."""
    output = "\n".join("診断行 {:02d}".format(index) for index in range(40))

    excerpt = gate.suite_failure_excerpt(output, "", limit=180)

    assert "診断行 39" in excerpt
    assert "診断行 00" not in excerpt


def test_excerpt_keeps_tail_lines_in_input_order():
    """A multi-line tail retains the order needed to read diagnostics forward."""
    excerpt = gate.suite_failure_excerpt(
        "x-000\nx-001\nx-002\nx-003\nx-004\nx-005\nx-006\n", "", limit=128
    )

    assert excerpt.index("x-004") < excerpt.index("x-005") < excerpt.index("x-006")


def test_excerpt_tail_is_a_contiguous_final_input_range():
    """A rejected wide line ends tail selection instead of creating a gap."""
    output = "\n".join(
        [
            "x" * 100 + "-000",
            "x" * 100 + "-001",
            "x" * 100 + "-002",
            "x" * 100 + "-003",
            "4",
            "x" * 30 + "-005",
            "-006",
        ]
    )

    excerpt = gate.suite_failure_excerpt(output, "", limit=120)

    assert excerpt.splitlines() == [
        "[suite_failure] output tail:",
        "[suite_failure] -006",
        "[suite_failure] output truncated",
    ]


def test_excerpt_tail_is_contiguous_after_excluding_a_matched_middle_line():
    """The tail omits a matched middle line without creating any other gap."""
    output = "\n".join(
        ["p-00", "p-01", "FAILED mid", "p-03", "p-04", "p-05", "p-06"]
    )

    excerpt = gate.suite_failure_excerpt(output, "", limit=220)
    _, tail_section = excerpt.split("[suite_failure] output tail:\n", 1)

    assert tail_section.splitlines() == [
        "[suite_failure] p-00",
        "[suite_failure] p-01",
        "[suite_failure] p-03",
        "[suite_failure] p-04",
        "[suite_failure] p-05",
        "[suite_failure] p-06",
    ]


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


def test_excerpt_marks_blank_lines_only_output_as_unavailable():
    assert "output unavailable" in gate.suite_failure_excerpt("\n\n\n", "", limit=4000)


def test_excerpt_rejects_limits_that_cannot_report_truncation():
    """A matching excerpt must always have room to surface truncation."""
    with pytest.raises(ValueError, match="at least"):
        gate.suite_failure_excerpt(
            "FAILED example\n", "", limit=gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT - 1
        )


def test_excerpt_enforces_the_exact_minimum_limit_boundary():
    """The minimum fits one marker; the preceding value is not accepted."""
    minimum = gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT
    with pytest.raises(ValueError, match="at least"):
        gate.suite_failure_excerpt("FAILED example\n", "", limit=minimum - 1)

    excerpt = gate.suite_failure_excerpt(
        "FAILED example\n" + "x" * 200 + "\n", "", limit=minimum
    )

    assert "output truncated" in excerpt


@pytest.mark.parametrize("width", (1, 2, 3))
def test_excerpt_marks_short_lines_near_the_derived_minimum(width):
    """The marker must survive the width-sensitive band that headers used to create."""
    output = "FAILED a\n" + ("x" * width + "\n") * 200

    for limit in range(gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT, gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT + 4):
        excerpt = gate.suite_failure_excerpt(output, "", limit=limit)
        assert len(excerpt) <= limit
        assert "output truncated" in excerpt


def test_excerpt_emits_marker_when_no_content_line_fits():
    """At the minimum, optional headers and all content yield to the marker."""
    excerpt = gate.suite_failure_excerpt(
        "FAILED " + "x" * 150 + "\n" + "y" * 150 + "\n",
        "",
        limit=gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT,
    )

    assert excerpt == "[suite_failure] output truncated"


def test_truncation_marker_outranks_headers_in_the_fallback():
    """The final fallback cannot retain a header by silently losing the marker."""
    header = "[suite_failure] matched failure lines:"

    assert gate._with_truncation_marker(
        header, True, gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT
    ) == "[suite_failure] output truncated"


def test_excerpt_keeps_content_at_the_exact_tail_separator_budget():
    """The final tail separator consumes one character of the common limit."""
    excerpt = gate.suite_failure_excerpt("FAILED x\nFAILED x\n", "", limit=81)

    assert excerpt == "[suite_failure] FAILED x\n[suite_failure] output truncated"


def test_excerpt_accepts_a_full_excerpt_at_its_exact_limit():
    """A full excerpt that exactly fits must not be treated as truncated."""
    expected = "[suite_failure] output tail:\n[suite_failure] x"

    assert gate.suite_failure_excerpt("x\n", "", limit=len(expected)) == expected


def test_excerpt_keeps_a_marked_section_at_its_exact_truncated_limit():
    """An exactly fitting marked section must not collapse to the marker alone."""
    output = "\n".join(
        ["FAILED head " + "q" * 7]
        + ["t" * 7 + "-{:03d}".format(index) for index in range(300)]
    )

    excerpt = gate.suite_failure_excerpt(output, "", limit=4000)

    assert len(excerpt) == 4000
    assert "[suite_failure] FAILED" in excerpt


def test_excerpt_reserves_the_tail_share_when_matches_fill_the_limit():
    """The two-thirds matching budget preserves substantial final context."""
    matched = ["FAILED " + "m" * 23 for _ in range(100)]
    tail = ["t" * 30 for _ in range(100)]

    excerpt = gate.suite_failure_excerpt("\n".join(matched + tail), "", limit=4000)

    assert excerpt.count("[suite_failure] FAILED " + "m" * 23) == 56
    assert excerpt.count("[suite_failure] " + "t" * 30) >= 20


def test_excerpt_deducts_the_marker_before_allocating_the_matching_share():
    """The two-thirds share includes the marker reservation at its boundary."""
    matched_line = "FAILED " + "m" * 3
    output = "\n".join([matched_line] * 100 + ["t" * 30] * 100)

    excerpt = gate.suite_failure_excerpt(output, "", limit=4000)

    assert excerpt.count("[suite_failure] " + matched_line) == 97
    assert excerpt.count("[suite_failure] " + "t" * 30) == 27


def test_excerpt_selects_the_first_matching_line_when_only_one_fits():
    """Matching records retain forward selection order under truncation."""
    excerpt = gate.suite_failure_excerpt(
        "FAILED first\nFAILED second\n" + "x" * 200 + "\n", "", limit=76
    )

    assert "[suite_failure] FAILED first" in excerpt
    assert "[suite_failure] FAILED second" not in excerpt


def test_excerpt_omits_headers_when_that_is_needed_to_keep_a_content_line():
    """At the accepted lower band, headers yield before the sole tail line."""
    excerpt = gate.suite_failure_excerpt("x\nx\n", "", limit=61)

    assert "[suite_failure] x" in excerpt
    assert "[suite_failure] output tail:" not in excerpt
    assert "[suite_failure] output truncated" in excerpt


@pytest.mark.parametrize("width", (5, 40, 150))
def test_excerpt_never_silently_truncates_across_content_widths(width):
    """Changing line widths cannot move a silent-truncation band into a new limit."""
    lines = ["FAILED first"] + ["x" * width + "-{:03d}".format(index) for index in range(200)]
    output = "\n".join(lines) + "\n"

    for limit in (gate._MIN_SUITE_FAILURE_EXCERPT_LIMIT, 100, 180, 400):
        excerpt = gate.suite_failure_excerpt(output, "", limit=limit)
        emitted = sum("[suite_failure] " + line in excerpt for line in lines)
        if emitted < len(lines):
            assert "output truncated" in excerpt
        assert len(excerpt) <= limit


def test_excerpt_never_exceeds_limit_when_tail_budget_is_exact():
    """The inter-section separator is part of the common excerpt limit."""
    matched = "FAILED matched"
    final_tail_line = "x" * 10
    matched_section_size = len("[suite_failure] matched failure lines:\n[suite_failure] " + matched)
    tail_budget = len("[suite_failure] output tail:\n[suite_failure] " + final_tail_line + "\n[suite_failure] output truncated") - 1
    limit = matched_section_size + 1 + tail_budget

    excerpt = gate.suite_failure_excerpt(
        matched + "\n" + "y" * 200 + "\n" + final_tail_line + "\n", "", limit=limit
    )

    assert len(excerpt) <= limit


def test_excerpt_preserves_stdout_before_stderr():
    """The diagnostic keeps process-stream order when both streams have output."""
    excerpt = gate.suite_failure_excerpt("stdout context\n", "stderr context\n", limit=4000)

    assert excerpt.index("stdout context") < excerpt.index("stderr context")


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
    assert logged[1].index("FAILED from stdout") < logged[1].index("ERROR from stderr")


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


def test_excerpt_resolves_the_failure_classifier_through_the_module_attribute(monkeypatch):
    """The formatter intentionally uses the module-level classifier seam."""
    monkeypatch.setattr(gate, "_is_failure_line", lambda line: "FAIL" in line)

    excerpt = gate.suite_failure_excerpt("FAILURE is not a marker\n", "", limit=4000)

    assert "matched failure lines:" in excerpt
