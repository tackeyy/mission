"""#282: reviewer 並列実行の観測ガード.

discriminating-v2 実測 (2026-07-23) で、並列の重なりが観測されたのは 5 run 中 1 run のみ。
orchestrator が reviewer の実行時間帯を `--reviewer-window` で申告し、
aggregate-reviews が重なりを計算して parallel_execution を evidence に記録する。
観測のみでゲート不変: 直列でも WARN + exit 0。review JSON の verbatim 契約は変えない。
"""

from __future__ import annotations

import json


def _review(tmp_path, name, *, perspective="A", scores=None):
    payload = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": 1,
        "scores": scores if scores is not None else {
            "mission_achievement": 4.6,
            "accuracy": 4.4,
            "completeness": 4.2,
            "usability": 4.0,
        },
        "findings": [],
        "same_score_note": None,
        "notes": f"{perspective} review",
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _two_reviews(tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 4.4,
        "accuracy": 4.2,
        "completeness": 4.0,
        "usability": 3.8,
    })
    return a, b


def test_overlapping_windows_record_parallel_true(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--out", str(out),
        "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
        "--reviewer-window", "B=2026-07-25T10:00:30Z..2026-07-25T10:04:00Z",
        "--json", cwd=state_dir.parent,
    )

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["parallel_execution"] is True
    evidence = _load(state_dir.parent / result["findings_evidence_path"])
    assert evidence["parallel_execution"] is True
    assert {w["perspective"] for w in evidence["reviewer_windows"]} == {"A", "B"}


def test_disjoint_windows_warn_but_exit_zero_and_keep_gates(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--out", str(out),
        "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
        "--reviewer-window", "B=2026-07-25T10:06:00Z..2026-07-25T10:10:00Z",
        "--json", cwd=state_dir.parent,
    )

    # 観測のみ: 直列でも exit 0、WARN を stderr へ
    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr and "直列" in r.stderr
    result = json.loads(r.stdout)
    assert result["parallel_execution"] is False
    # ゲート関連の値は window の有無に影響されない
    payload = _load(out)
    assert payload["items"]["mission_achievement"] == 4.5
    assert payload["review_agreement"] == 5.0
    assert payload["open_high"] == 0


def test_no_windows_is_rejected_for_multiple_reviewers(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--out", str(out), "--json", cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "不足 perspective: A, B" in r.stderr


def test_single_window_is_rejected_for_multiple_reviewers(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--out", str(out),
        "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
        "--json", cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "不足 perspective: B" in r.stderr


def test_malformed_window_rejected(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--reviewer-window", "A=not-a-time..also-bad", cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "reviewer-window" in r.stderr


def test_window_end_before_start_rejected(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--reviewer-window", "A=2026-07-25T10:05:00Z..2026-07-25T10:00:00Z", cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "reviewer-window" in r.stderr


def test_window_for_unknown_perspective_rejected(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--reviewer-window", "Z=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z", cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "Z" in r.stderr


def test_mixed_naive_and_aware_timestamps_do_not_crash(state_dir, run_cli, tmp_path):
    """naive (TZ なし) と aware (Z 付き) の混在で TypeError にならず、naive は UTC 扱い."""
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--out", str(out),
        "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
        "--reviewer-window", "B=2026-07-25T10:00:30..2026-07-25T10:04:00",
        "--json", cwd=state_dir.parent,
    )

    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["parallel_execution"] is True
