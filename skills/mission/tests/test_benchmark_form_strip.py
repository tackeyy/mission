"""Issue #154: form-stripped marker scoring — remove structure-credit circularity.

Contract under test:
- `strip_form(text)` exists identically in both runners and removes structural
  lines (markdown headings, label-only lines, horizontal rules, table
  separator rows) while keeping body prose and table data rows.
- `evaluate_run` scores quality markers against the stripped body, so a bare
  `## Rejected Hypotheses` heading earns nothing; the unstripped score is
  still recorded as `quality_marker_score_raw` for comparability.
- `quality_score_method` records the stripped method; the schema accepts both
  the old and new method values.
"""

import json
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"


def _load(name: str):
    path = BENCHMARK_DIR / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _both_runners():
    return (_load("run_claude_goal_vs_mission.py"), _load("run_paired_pilot.py"))


def test_strip_form_removes_headings_and_keeps_body():
    for module in _both_runners():
        text = "## Rejected Hypotheses\n\nWe rejected the cache hypothesis.\n"
        stripped = module.strip_form(text)
        assert "Rejected Hypotheses" not in stripped
        assert "We rejected the cache hypothesis." in stripped


def test_strip_form_removes_label_only_lines_but_keeps_labeled_content():
    for module in _both_runners():
        text = (
            "**Evidence**\n"
            "Evidence:\n"
            "- Evidence: the log shows a timeout at 02:13\n"
            "Assumptions: none beyond the fixtures\n"
        )
        stripped = module.strip_form(text)
        # Bare labels carry no content and must not earn marker matches.
        assert "**Evidence**" not in stripped
        assert "\nEvidence:\n" not in f"\n{stripped}"
        # Lines with content after the label are body and must survive.
        assert "the log shows a timeout at 02:13" in stripped
        assert "none beyond the fixtures" in stripped


def test_strip_form_removes_rules_and_table_separators_keeps_rows():
    for module in _both_runners():
        text = (
            "---\n"
            "| Key | Value |\n"
            "|---|---:|\n"
            "| request_timeout_ms | 27000 |\n"
            "***\n"
        )
        stripped = module.strip_form(text)
        assert "---" not in stripped
        assert "***" not in stripped
        assert "| request_timeout_ms | 27000 |" in stripped
        assert "| Key | Value |" in stripped


def test_strip_form_is_identical_across_runners():
    official, paired = _both_runners()
    sample = (
        "# Title\n"
        "## Evidence Map\n"
        "**Stop Decision**\n"
        "Residual risk: none identified.\n"
        "|---|---|\n"
        "| a | b |\n"
        "---\n"
        "Body line.\n"
    )
    assert official.strip_form(sample) == paired.strip_form(sample)


def test_evaluate_run_scores_markers_on_stripped_body(tmp_path):
    module = _load("run_claude_goal_vs_mission.py")
    task = {
        "id": "form-strip-check",
        "quality_markers": [
            {"name": "Rejected Hypotheses", "patterns": ["rejected hypotheses"]},
        ],
    }
    headings = (
        "## Goal\n## Result\n## Evidence\n## Assumptions\n## Stop Condition\n"
    )

    # Heading-only coverage: the marker appears only as a bare heading.
    out_rel = "heading-only.md"
    (tmp_path / out_rel).write_text(
        headings + "## Rejected Hypotheses\n\nbody without the phrase\n",
        encoding="utf-8",
    )
    heading_only = module.evaluate_run(
        tmp_path, task, "claude_code_goal_command", out_rel,
        returncode=0, stdout="", stderr="", timed_out=False,
    )
    assert heading_only["validator_pass"] is True
    assert heading_only["quality_marker_score"] == 0.0
    assert heading_only["quality_marker_score_raw"] == 1.0
    # #247 gradient v2: markered task で marker 0 の pass は 2.0 (旧 4.0)。
    # 構造だけでは中位スコアに届かない。
    assert heading_only["human_quality_score"] == 2.0
    assert heading_only["quality_score_method"] == (
        "automated_heuristic_form_stripped_gradient_v2_not_blind_human"
    )

    # Body coverage: the phrase appears in prose and earns the marker.
    out_rel = "body.md"
    (tmp_path / out_rel).write_text(
        headings + "We list rejected hypotheses with reasons in prose.\n",
        encoding="utf-8",
    )
    body = module.evaluate_run(
        tmp_path, task, "claude_code_goal_command", out_rel,
        returncode=0, stdout="", stderr="", timed_out=False,
    )
    assert body["quality_marker_score"] == 1.0
    assert body["human_quality_score"] == 5.0


def test_result_schema_accepts_form_stripped_method_and_raw_score():
    schema = json.loads((BENCHMARK_DIR / "result.schema.json").read_text(encoding="utf-8"))
    methods = schema["properties"]["quality_score_method"]["enum"]
    assert "automated_heuristic_form_stripped_not_blind_human" in methods
    # Old records remain valid.
    assert "automated_heuristic_not_blind_human" in methods
    assert schema["properties"]["quality_marker_score_raw"]["maximum"] == 1


def test_paired_runner_also_records_form_stripped_method():
    source = (BENCHMARK_DIR / "run_paired_pilot.py").read_text(encoding="utf-8")
    assert "automated_heuristic_form_stripped_not_blind_human" in source
    assert "quality_marker_score_raw" in source
    assert "strip_form(" in source


def test_benchmark_readmes_document_form_stripped_scoring():
    en = (BENCHMARK_DIR / "README.md").read_text(encoding="utf-8")
    ja = (BENCHMARK_DIR / "README.ja.md").read_text(encoding="utf-8")
    for text in (en, ja):
        assert "form-stripped" in text
        assert "quality_marker_score_raw" in text
