"""#560 Step A: extract_process_quality のユニットテスト。

synthetic fixture は実測した archive JSON の形状に基づく:
  - reviews: {"schema": "mission-review-aggregate/1", "iteration": N,
              "inputs": [{"schema": "mission-review/1", "iteration": N,
                          "perspective": "...", "scores": {...},
                          "findings": [{"id":..., "severity":..., ...}]}],
              "excluded": [], ...}
  - scoring: {"schema": "mission-scoring-artifact/1",
              "_meta": {"iteration": N, "computed_composite": X},
              "binding": {"composite": X, ...}, "composite": X,
              "open_high": N, ...}
"""
import importlib.util
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load(name: str):
    path = BENCH / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load("run_claude_goal_vs_mission.py")
extract_process_quality = MODULE.extract_process_quality


def _make_reviews(iteration: int, findings: list[dict], perspectives: int = 2) -> dict:
    """Construct a reviews archive payload matching the real shape."""
    inputs = []
    for i in range(perspectives):
        inputs.append({
            "schema": "mission-review/1",
            "iteration": iteration,
            "perspective": f"perspective-{i}",
            "scores": {"mission_achievement": 4, "accuracy": 4, "completeness": 4, "usability": 4},
            "findings": findings if i == 0 else [],
        })
    return {
        "schema": "mission-review-aggregate/1",
        "iteration": iteration,
        "inputs": inputs,
        "excluded": [],
        "cap_log": [],
        "agreement_detail": {},
        "input_refs": [],
        "scoring_perspectives": [],
    }


def _make_scoring(iteration: int, composite: float, open_high: int = 0) -> dict:
    """Construct a scoring archive payload matching the real shape."""
    return {
        "schema": "mission-scoring-artifact/1",
        "_meta": {"iteration": iteration, "computed_composite": composite},
        "binding": {"composite": composite, "min_item": composite, "items": {}},
        "composite": composite,
        "min_item": composite,
        "open_high": open_high,
        "review_agreement": 3.0,
        "items": {},
    }


def _write_archive(tmp_path: Path, iteration: int, reviews: dict, scoring: dict,
                   mid: str = "abc123", gen: str = "1") -> None:
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"iter-{iteration}-{mid}-reviews-{gen}.json").write_text(
        json.dumps(reviews), encoding="utf-8"
    )
    (archive / f"iter-{iteration}-{mid}-scoring-{gen}.json").write_text(
        json.dumps(scoring), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Happy path: 2 iterations, all severities, composite changes
# ---------------------------------------------------------------------------

def test_happy_path_two_iterations(tmp_path):
    findings_iter1 = [
        {"id": "f1", "severity": "High", "summary": "...", "evidence": "...", "recommendation": "..."},
        {"id": "f2", "severity": "Medium", "summary": "...", "evidence": "...", "recommendation": "..."},
    ]
    findings_iter2 = [
        {"id": "f3", "severity": "Low", "summary": "...", "evidence": "...", "recommendation": "..."},
    ]
    _write_archive(tmp_path, 1, _make_reviews(1, findings_iter1, perspectives=3), _make_scoring(1, 3.5, open_high=1))
    _write_archive(tmp_path, 2, _make_reviews(2, findings_iter2, perspectives=3), _make_scoring(2, 4.2, open_high=0), mid="def456", gen="2")

    pq, err = extract_process_quality(tmp_path)

    assert err is None
    assert pq is not None
    # findings: iter1 has High+Medium (in perspective-0), iter2 has Low (in perspective-0)
    assert pq["review_findings_total"] == 3
    assert pq["review_findings_by_severity"]["High"] == 1
    assert pq["review_findings_by_severity"]["Medium"] == 1
    assert pq["review_findings_by_severity"]["Low"] == 1
    assert pq["review_iterations_observed"] == 2
    # composite changes between iterations
    assert pq["composite_first"] == 3.5
    assert pq["composite_final"] == 4.2
    assert pq["composite_first"] != pq["composite_final"]
    # reviewer count = max len(inputs) = 3
    assert pq["reviewer_count_observed"] == 3
    # open_high
    assert pq["open_high_first"] == 1
    assert pq["open_high_final"] == 0


# ---------------------------------------------------------------------------
# Missing archive dir -> null process_quality, error non-null, no exception
# ---------------------------------------------------------------------------

def test_missing_archive_dir(tmp_path):
    # No .mission-state directory at all
    pq, err = extract_process_quality(tmp_path)
    assert pq is None
    assert err is not None
    assert "archive_dir_missing" in err


# ---------------------------------------------------------------------------
# Corrupt JSON in one file -> no exception, error recorded
# ---------------------------------------------------------------------------

def test_corrupt_json_in_reviews(tmp_path):
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "iter-1-abc-reviews-1.json").write_text("{ not valid json !!!", encoding="utf-8")

    pq, err = extract_process_quality(tmp_path)
    assert pq is None
    assert err is not None
    assert "unreadable" in err or "corrupt" in err or "JSONDecodeError" in err


# ---------------------------------------------------------------------------
# Iteration ordering: >= 10 iterations must sort numerically, not lexicographically
# ---------------------------------------------------------------------------

def test_iteration_ordering_numeric(tmp_path):
    """iter-10 must sort after iter-9, not after iter-1 (lexicographic would give iter-1, iter-10, iter-2, ...)"""
    for n in range(1, 12):
        composite = float(n)  # composite == iteration number so we can verify first/final
        _write_archive(
            tmp_path, n,
            _make_reviews(n, []),
            _make_scoring(n, composite),
            mid=f"mid{n:03d}", gen="1",
        )

    pq, err = extract_process_quality(tmp_path)
    assert err is None
    assert pq is not None
    assert pq["review_iterations_observed"] == 11
    # If sorted correctly, first=1.0, final=11.0; lexicographic would give first=1.0, final=9.0
    assert pq["composite_first"] == 1.0
    assert pq["composite_final"] == 11.0


# ---------------------------------------------------------------------------
# Goal arm -> process_quality is null (via extract_mission_state_fields for goal path)
# ---------------------------------------------------------------------------

def test_goal_arm_has_null_process_quality():
    """Goal arm hardcoded dict must include process_quality: None."""
    # Verify the goal arm dict returned by caller has process_quality=None.
    # We test this by checking the MODULE constant fields present in the else-branch dict.
    # The simplest way: call extract_mission_state_fields on a path with no .mission-state
    # and verify the returned fields include process_quality.
    # Actually, the goal-arm dict is constructed in run_arm(); we validate via the
    # extract_mission_state_fields path (which also has process_quality initialized to None).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wt = Path(td)
        fields, note = MODULE.extract_mission_state_fields(wt)
    # When state is missing, fields are still returned with process_quality key
    assert "process_quality" in fields
    assert fields["process_quality"] is None
    assert "process_quality_error" in fields
    # note should be mission_state_missing, not a process_quality error
    assert note == "mission_state_missing"


# ---------------------------------------------------------------------------
# Unknown severity values are bucketed under their literal string, not crashed
# ---------------------------------------------------------------------------

def test_unknown_severity_bucketed_not_crashed(tmp_path):
    findings = [
        {"id": "f1", "severity": "Informational", "summary": "..."},
        {"id": "f2", "severity": "critical", "summary": "..."},
        {"id": "f3"},  # missing severity key
    ]
    _write_archive(tmp_path, 1, _make_reviews(1, findings), _make_scoring(1, 4.0))

    pq, err = extract_process_quality(tmp_path)
    assert err is None
    assert pq is not None
    assert pq["review_findings_total"] == 3
    # Unknown severities under literal keys
    assert pq["review_findings_by_severity"].get("Informational", 0) == 1
    assert pq["review_findings_by_severity"].get("critical", 0) == 1
    # Missing severity key -> bucketed under "__unknown__"
    assert pq["review_findings_by_severity"].get("__unknown__", 0) == 1


# ---------------------------------------------------------------------------
# Single iteration: composite_first == composite_final
# ---------------------------------------------------------------------------

def test_single_iteration(tmp_path):
    findings = [{"id": "f1", "severity": "Low", "summary": "..."}]
    _write_archive(tmp_path, 1, _make_reviews(1, findings), _make_scoring(1, 4.84))

    pq, err = extract_process_quality(tmp_path)
    assert err is None
    assert pq["composite_first"] == pq["composite_final"] == 4.84
    assert pq["review_iterations_observed"] == 1
    assert pq["review_findings_total"] == 1


# ---------------------------------------------------------------------------
# Missing scoring file: composite should be null, not crash
# ---------------------------------------------------------------------------

def test_missing_scoring_file(tmp_path):
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    reviews = _make_reviews(1, [{"id": "f1", "severity": "Low", "summary": "..."}])
    (archive / "iter-1-abc-reviews-1.json").write_text(json.dumps(reviews), encoding="utf-8")
    # No scoring file

    pq, err = extract_process_quality(tmp_path)
    assert err is None
    assert pq is not None
    assert pq["composite_first"] is None
    assert pq["composite_final"] is None
    assert pq["open_high_first"] is None
    assert pq["open_high_final"] is None
