"""Issue #353: reviewer output boundary observability remains non-gating."""

from __future__ import annotations

import json
from pathlib import Path


WARN = "WARN #353: reviewer output exceeds bounded template guidance"


def _review(perspective: str, *, iteration: int = 1) -> dict:
    return {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": iteration,
        "scores": {
            "mission_achievement": 4.6,
            "accuracy": 4.4,
            "completeness": 4.2,
            "usability": 4.0,
        },
        "findings": [],
        "same_score_note": None,
        "notes": f"{perspective} review",
    }


def _write_output(path: Path, perspective: str, prose: str = "") -> Path:
    review_json = json.dumps(_review(perspective), ensure_ascii=False, indent=2)
    path.write_text(
        "\n".join([
            f"## レビュー結果 (担当観点: {perspective})",
            "### 採点",
            prose,
            "```json",
            review_json,
            "```",
            "",
        ]),
        encoding="utf-8",
    )
    return path


def _aggregate(run_cli, state_dir, tmp_path, *inputs: Path):
    out = tmp_path / "scoring.json"
    window_args = []
    if len(inputs) >= 2:
        # #350: 複数 reviewer 時は --reviewer-window 申告が必須
        for perspective in ("A", "B"):
            window_args += [
                "--reviewer-window",
                f"{perspective}=2026-08-07T10:00:00Z..2026-08-07T10:05:00Z",
            ]
    result = run_cli(
        "aggregate-reviews", "--iteration", "1",
        *(arg for path in inputs for arg in ("--input", str(path))),
        *window_args,
        "--out", str(out), "--json", cwd=state_dir.parent,
    )
    return result, out


def _evidence(result) -> dict:
    payload = json.loads(result.stdout)
    return json.loads(Path(payload["findings_evidence_path"]).read_text(encoding="utf-8"))


def test_template_headings_are_excluded_but_external_prose_is_measured(
    state_dir, run_cli, tmp_path,
):
    source = _write_output(tmp_path / "review.md", "A", "short rationale")

    result, _ = _aggregate(run_cli, state_dir, tmp_path, source)

    assert result.returncode == 0, result.stderr
    metric = _evidence(result)["reviewer_output_metrics"][0]
    assert metric["perspective"] == "A"
    assert metric["json_bytes"] > 0
    assert metric["prose_bytes"] == len("short rationale".encode("utf-8"))
    assert 0 < metric["prose_ratio"] < 1
    assert WARN not in result.stderr


def test_json_only_has_zero_prose_and_no_warning(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    source.write_text(json.dumps(_review("A"), ensure_ascii=False), encoding="utf-8")

    result, _ = _aggregate(run_cli, state_dir, tmp_path, source)

    metric = _evidence(result)["reviewer_output_metrics"][0]
    assert metric["prose_bytes"] == 0
    assert metric["prose_ratio"] == 0
    assert WARN not in result.stderr


def test_oversize_prose_warns_without_changing_scoring(state_dir, run_cli, tmp_path):
    pure = tmp_path / "pure.json"
    pure.write_text(json.dumps(_review("A"), ensure_ascii=False), encoding="utf-8")
    baseline_result, baseline_out = _aggregate(run_cli, state_dir, tmp_path, pure)
    baseline = json.loads(baseline_out.read_text(encoding="utf-8"))

    oversized = _write_output(tmp_path / "oversized.md", "A", "x" * 20_001)
    result, out = _aggregate(run_cli, state_dir, tmp_path, oversized)

    assert result.returncode == 0
    assert WARN in result.stderr
    actual = json.loads(out.read_text(encoding="utf-8"))
    for key in ("items", "review_agreement", "open_high"):
        assert actual[key] == baseline[key]
    assert baseline_result.returncode == 0


def test_only_oversize_perspective_warns_in_mixed_reviewers(state_dir, run_cli, tmp_path):
    normal = _write_output(tmp_path / "a.md", "A", "compact")
    oversized = _write_output(tmp_path / "b.md", "B", "z" * 20_001)

    result, _ = _aggregate(run_cli, state_dir, tmp_path, normal, oversized)

    warnings = [line for line in result.stderr.splitlines() if WARN in line]
    assert len(warnings) == 1
    assert "perspective=B" in warnings[0]


def test_high_prose_ratio_warns_below_byte_limit(state_dir, run_cli, tmp_path):
    source = _write_output(tmp_path / "ratio.md", "A", "r" * 2_000)

    result, _ = _aggregate(run_cli, state_dir, tmp_path, source)

    metric = _evidence(result)["reviewer_output_metrics"][0]
    assert metric["prose_bytes"] < 20_000
    assert metric["prose_ratio"] > 0.7
    assert WARN in result.stderr


def test_stats_reports_reviewer_output_distribution(state_dir, run_cli, tmp_path):
    normal = _write_output(tmp_path / "a.md", "A", "a" * 100)
    oversized = _write_output(tmp_path / "b.md", "B", "b" * 20_001)
    result, _ = _aggregate(run_cli, state_dir, tmp_path, normal, oversized)
    assert result.returncode == 0

    stats_result = run_cli(
        "stats", "--json", "--root", str(state_dir.parent), cwd=state_dir.parent,
    )

    stats = json.loads(stats_result.stdout)["reviewer_output_stats"]
    assert stats == {
        "records": 2,
        "oversize_warns": 1,
        "prose_bytes_p50": 100,
        "prose_bytes_p90": 20_001,
    }


def test_reviewer_guidance_targets_zero_template_external_prose():
    reviewer_skill = Path(__file__).resolve().parents[2] / "mission-reviewer" / "SKILL.md"

    text = reviewer_skill.read_text(encoding="utf-8")

    assert "scoring/issues テンプレ + mission-review/1 JSON 以外の散文を出力しない" in text
    assert "テンプレ外散文 0" in text
