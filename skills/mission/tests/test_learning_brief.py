"""Issue #457: learning brief command and archive compaction collection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from review_learning import reduce_failure_ledger


def _review(*, iteration: int, perspective: str, phase: str, rule: str, cause: str) -> dict:
    review = {
        "schema": "mission-review/1",
        "iteration": iteration,
        "perspective": perspective,
        "scores": {
            "mission_achievement": 4.0,
            "accuracy": 4.0,
            "completeness": 4.0,
            "usability": 4.0,
        },
        "same_score_note": "axis-specific fixture review",
        "findings": [{
            "id": f"{perspective}-{iteration}",
            "severity": "Medium",
            "axis": "accuracy",
            "summary": "Observed failure",
            "evidence": "bounded evidence",
            "recommendation": "fix it",
            "cause": cause,
            "general_fix_rule": rule,
            "weak_phase": phase,
        }],
        "learning_schema": "mission-review-learning/1",
    }
    return review


def _state(session_id: str, mission_id: str, project_root: Path, ledger: dict | None = None) -> dict:
    return {
        "mission": "learning brief fixture",
        "mission_id": mission_id,
        "session_id": session_id,
        "project_root": str(project_root),
        "loop_active": False,
        "passes": False,
        "halt_reason": "fixture complete",
        "halt_category": "stagnation",
        "phase": "halted",
        "iteration": 1,
        "score_history": [],
        "started_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:01:00Z",
        "schema_version": 4,
        "failure_ledger": ledger or {"schema": "mission-failure-ledger/1", "patterns": []},
    }


def _write_state(root: Path, state: dict, name: str) -> None:
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / name).write_text(json.dumps(state), encoding="utf-8")


def test_learning_brief_cli_outputs_json_and_text(tmp_path: Path, run_cli):
    ledger_a = reduce_failure_ledger([
        {"iteration": 1, "review": _review(
            iteration=1,
            perspective="A",
            phase="planning",
            rule="Validate every boundary",
            cause="The validation boundary was omitted",
        ), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "a" * 64}},
        {"iteration": 2, "review": _review(
            iteration=2,
            perspective="A",
            phase="planning",
            rule="Validate every boundary",
            cause="The validation boundary was omitted again",
        ), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "b" * 64}},
    ])
    ledger_b = reduce_failure_ledger([
        {"iteration": 1, "review": _review(
            iteration=1,
            perspective="B",
            phase="execution",
            rule="Keep the loop closed",
            cause="The loop was not closed",
        ), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "c" * 64}},
    ])
    _write_state(tmp_path, _state("a", "m1", tmp_path, ledger_a), "a.json")
    _write_state(tmp_path, _state("b", "m2", tmp_path, ledger_b), "b.json")

    json_result = run_cli("learning", "brief", "--json", cwd=tmp_path, check=True)
    text_result = run_cli("learning", "brief", cwd=tmp_path, check=True)

    payload = json.loads(json_result.stdout)
    assert payload == {
        "schema": "mission-learning-brief/1",
        "rules": [
            {
                "general_fix_rule": "validate every boundary",
                "weak_phase": "planning",
                "recurrence": 1,
                "sessions": 1,
            },
            {
                "general_fix_rule": "keep the loop closed",
                "weak_phase": "execution",
                "recurrence": 0,
                "sessions": 1,
            },
        ],
    }
    assert "recurrence=1 sessions=1 weak_phase=planning general_fix_rule=validate every boundary" in text_result.stdout
    assert "recurrence=0 sessions=1 weak_phase=execution general_fix_rule=keep the loop closed" in text_result.stdout


def test_learning_brief_cli_supports_phase_filter_and_limit(tmp_path: Path, run_cli):
    ledger = reduce_failure_ledger([
        {"iteration": 1, "review": _review(
            iteration=1,
            perspective="A",
            phase="planning",
            rule="Validate every boundary",
            cause="The validation boundary was omitted",
        ), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "d" * 64}},
        {"iteration": 1, "review": _review(
            iteration=1,
            perspective="A",
            phase="execution",
            rule="Keep the loop closed",
            cause="The loop was not closed",
        ), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "e" * 64}},
    ])
    _write_state(tmp_path, _state("a", "m1", tmp_path, ledger), "a.json")

    result = run_cli("learning", "brief", "--weak-phase", "planning", "--limit", "1", "--json", cwd=tmp_path, check=True)
    payload = json.loads(result.stdout)
    assert payload["rules"] == [{
        "general_fix_rule": "validate every boundary",
        "weak_phase": "planning",
        "recurrence": 0,
        "sessions": 1,
    }]


def test_learning_brief_collects_archive_generation_compaction(tmp_path: Path):
    mission_state_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_learning_brief", mission_state_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    live = _state("live", "live-mission", tmp_path)
    _write_state(tmp_path, live, "live.json")

    archived = _state(
        "archive",
        "archive-mission",
        tmp_path,
        reduce_failure_ledger([
            {"iteration": 1, "review": _review(
                iteration=1,
                perspective="A",
                phase="execution",
                rule="Keep the loop closed",
                cause="The loop was not closed",
            ), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "f" * 64}},
        ]),
    )
    canonical_bytes = json.dumps(archived, ensure_ascii=False).encode("utf-8")
    fake_compaction = SimpleNamespace(
        records=(
            {
                "canonical_path": ".mission-state/archive/compaction/generations/gen-1/state.json",
                "mission_id": "archive-mission",
                "session_id": "archive",
                "superseded": [],
            },
        ),
    )

    def fake_compaction_reader(state_root, verify_superseded=False):
        assert state_root == tmp_path / ".mission-state"
        return fake_compaction

    def fake_state_reader(project_root, reference):
        assert project_root == tmp_path
        assert reference == ".mission-state/archive/compaction/generations/gen-1/state.json"
        return canonical_bytes

    module.read_state_archive_compaction = fake_compaction_reader
    module.read_state_archive_file_bytes = fake_state_reader

    states = module._collect_learning_brief_states([tmp_path])
    brief = module.summarize_learning_brief(states)

    assert any(state.get("session_id") == "archive" for state in states)
    assert brief["rules"] == [{
        "general_fix_rule": "keep the loop closed",
        "weak_phase": "execution",
        "recurrence": 0,
        "sessions": 1,
    }]
