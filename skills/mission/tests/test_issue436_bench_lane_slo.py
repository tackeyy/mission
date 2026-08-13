"""#436: bench runner lane-report回収とSLO baseline comparison."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"
BASELINE_JSON = BENCH / "results" / "2026-08-13-lane-slo-baseline.json"


def _load(name: str):
    path = BENCH / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load("run_paired_pilot.py")
AUDIT = _load("benchmark_audit.py")


def _write_state(tmp_path: Path, session_id: str) -> None:
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(
        json.dumps(
            {
                "mission": "lane duration test",
                "mission_id": "lane-slo",
                "session_id": session_id,
                "session_role": "implementer",
                "phase": "done",
                "passes": True,
                "loop_active": False,
                "halt_reason": "",
                "started_at": "2026-08-13T00:00:00Z",
                "updated_at": "2026-08-13T00:10:00Z",
                "project_root": str(tmp_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_lane_report_artifact_is_saved_with_slo_minutes(tmp_path, monkeypatch):
    worktree = tmp_path / "repo"
    artifact_dir = tmp_path / "artifacts"
    _write_state(worktree, "impl")

    calls: list[dict] = []

    def fake_run(args, cwd=None, text=None, stdout=None, stderr=None, timeout=None, check=None):
        calls.append(
            {
                "args": args,
                "cwd": cwd,
                "text": text,
                "stdout": stdout,
                "stderr": stderr,
                "timeout": timeout,
                "check": check,
            }
        )
        payload = {
            "sessions": [
                {
                    "session_id": "impl",
                    "slo_breached": True,
                    "wall_clock_sec": 600.0,
                }
            ],
            "rendezvous_loss_sec": 148.0,
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(payload) + "\n", "")

    monkeypatch.setattr(RUNNER.subprocess, "run", fake_run)

    report, artifact_path = RUNNER.run_lane_report(worktree, artifact_dir)

    assert report["sessions"][0]["slo_breached"] is True
    assert artifact_path.name == "lane-report.json"
    assert artifact_path.exists()
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == report
    assert len(calls) == 1
    assert "lane-report" in calls[0]["args"]
    assert "--json" in calls[0]["args"]
    assert "--slo-minutes" in calls[0]["args"]
    assert calls[0]["args"][calls[0]["args"].index("--slo-minutes") + 1] == "15"
    assert calls[0]["cwd"] == worktree


def test_summary_adds_slo_breached_count_and_wall_clock_median():
    records = [
        {
            "arm": "goal_only",
            "run_status": "completed",
            "completion": True,
            "validator_pass": True,
            "human_quality_score": 4.0,
            "intervention_count": 0,
            "evidence_completeness": 4.0,
            "elapsed_minutes": 8.0,
            "artifacts": [],
            "notes": "",
        },
        {
            "arm": "mission",
            "run_status": "completed",
            "completion": True,
            "validator_pass": True,
            "human_quality_score": 4.0,
            "intervention_count": 0,
            "evidence_completeness": 4.0,
            "elapsed_minutes": 9.0,
            "artifacts": [],
            "notes": "",
        },
        {
            "arm": "mission",
            "run_status": "completed",
            "completion": True,
            "validator_pass": True,
            "human_quality_score": 4.0,
            "intervention_count": 0,
            "evidence_completeness": 4.0,
            "elapsed_minutes": 10.0,
            "artifacts": [],
            "notes": "",
        },
    ]
    lane_reports = [
        {"sessions": [{"session_id": "goal-1", "slo_breached": False, "wall_clock_sec": 480.0}]},
        {"sessions": [{"session_id": "mission-1", "slo_breached": True, "wall_clock_sec": 600.0}]},
        {"sessions": [{"session_id": "mission-2", "slo_breached": False, "wall_clock_sec": 300.0}]},
    ]

    summary = RUNNER.summarize(
        records,
        lane_reports,
        tasks=[{"id": "lane-task"}],
        run_id="lane-slo-fixture",
        starting_commit="abcdef0",
        tasks_path=BENCH / "tasks.json",
    )

    assert summary["slo"] == {
        "breached_records": 1,
        "wall_clock_median_sec": 480.0,
    }


def test_compare_lane_slo_baseline_reports_numeric_deltas():
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    observed = dict(baseline)
    observed["checker_active_sec"] = 150

    diff = AUDIT.compare_lane_slo_baseline(observed, baseline)

    assert diff["schema"] == "mission-lane-slo-baseline-diff/1"
    assert diff["missing_fields"] == []
    assert diff["fields"]["checker_active_sec"] == {
        "baseline": 152,
        "observed": 150,
        "delta": -2,
    }
