"""#283: transactional コマンド review-finalize / closeout.

mission run は 52-56 turns (goal 比 6x) で、1 turn ≈ 107K context の再処理を伴う。
Phase 5 の aggregate-reviews → push-score、Phase 6 の mark-passes → next の
頻出連鎖を 1 コマンド化して orchestration turn を削減する。
既存 validator をそのまま内部呼び出しし、gate 意味論は不変。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location(
        "mission_state_issue283_transaction", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def _two_reviews(tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 4.4,
        "accuracy": 4.2,
        "completeness": 4.0,
        "usability": 3.8,
    })
    return a, b


def _reviewer_windows():
    return (
        "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
        "--reviewer-window", "B=2026-07-25T10:00:30Z..2026-07-25T10:04:00Z",
    )


def _finalize_args(review, out, *, event_id="finalize-transaction"):
    return argparse.Namespace(
        iteration=1,
        input=[str(review)],
        input_refs=[],
        out=str(out),
        min_reviewers=None,
        reviewer_windows=[],
        base_sha=None,
        head_sha=None,
        notes=None,
        resubmit_reason=None,
        event_id=event_id,
        root_event_id=f"{event_id}-root",
        attempt=1,
        retry_of=None,
    )


# ===== review-finalize =====


def test_review_finalize_aggregates_and_pushes_in_one_command(state_dir, run_cli, read_state, tmp_path):
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
                "--out", str(out), "--min-reviewers", "2", *_reviewer_windows(), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["ok"] is True
    assert result["aggregate"]["out"] == str(out)
    entry = read_state(state_dir)["score_history"][0]
    assert entry["score_source"] == "scoring-json"
    assert entry["composite"] == result["push"]["appended"]["composite"]
    assert entry["items"]["mission_achievement"] == 4.5
    assert read_state(state_dir)["command_outcomes"] == [result["outcome"]]


@pytest.mark.parametrize("failure_kind", ["lease-takeover", "io-error"])
def test_review_finalize_has_no_post_push_transaction_failure_window(
    state_dir, tmp_path, monkeypatch, capsys, failure_kind,
):
    """A committed score must not be followed by a second outcome transaction."""
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "single.json", perspective="quality")
    original_write = module.atomic_write_json
    score_publish_count = 0
    publish_candidates = []

    def reject_second_score_transaction(path, data, **kwargs):
        nonlocal score_publish_count
        publish_candidates.append(copy.deepcopy(data))
        if data.get("score_history"):
            score_publish_count += 1
            if score_publish_count == 2:
                if failure_kind == "lease-takeover":
                    raise module.CommandOutcomeExit(2, "expected-gate")
                raise OSError("simulated post-push publish failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", reject_second_score_transaction)

    module.cmd_review_finalize(
        _finalize_args(review, tmp_path / "score.json", event_id=failure_kind),
    )

    payload = json.loads(capsys.readouterr().out)
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    assert payload["ok"] is True
    assert state["score_history"] == [payload["push"]["appended"]]
    outcomes = [
        record for record in state.get("command_outcomes", [])
        if record.get("command") == "review-finalize"
    ]
    assert outcomes == [payload["outcome"]]
    score_candidates = [item for item in publish_candidates if item.get("score_history")]
    assert len(score_candidates) == 1
    assert score_candidates[0]["command_outcomes"][-1] == payload["outcome"]


def test_review_finalize_combined_score_outcome_publish_failure_is_atomic(
    state_dir, tmp_path, monkeypatch,
):
    """The single score/outcome publish leaves neither record after an I/O failure."""
    module = _load_mission_state()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    review = _review(tmp_path, "single.json", perspective="quality")
    original_write = module.atomic_write_json
    state_path = state_dir / "sessions" / "test.json"
    state_after_aggregate = None

    def fail_combined_publish(path, data, **kwargs):
        nonlocal state_after_aggregate
        if data.get("score_history"):
            finalize_outcomes = [
                record for record in data.get("command_outcomes", [])
                if record.get("command") == "review-finalize"
            ]
            assert len(finalize_outcomes) == 1
            raise OSError("simulated combined publish failure")
        result = original_write(path, data, **kwargs)
        if path == state_path:
            state_after_aggregate = state_path.read_bytes()
        return result

    monkeypatch.setattr(module, "atomic_write_json", fail_combined_publish)

    with pytest.raises(OSError, match="simulated combined publish failure"):
        module.cmd_review_finalize(
            _finalize_args(review, tmp_path / "score.json", event_id="atomic-failure"),
        )

    assert state_after_aggregate is not None
    assert state_path.read_bytes() == state_after_aggregate
    state = json.loads(state_path.read_text())
    assert state["score_history"] == []
    assert not [
        record for record in state.get("command_outcomes", [])
        if record.get("command") == "review-finalize"
    ]


def test_review_finalize_min_reviewers_failure_is_atomic(state_dir, run_cli, read_state, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a),
                "--min-reviewers", "2", "--event-id", "minimum-gate", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "reviewer 数不足" in r.stderr
    payload = json.loads(r.stdout)
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["outcome"]["event_id"] == "minimum-gate"
    # 集計に失敗したら score は push されない (atomic)
    assert read_state(state_dir)["score_history"] == []
    assert state_file.read_bytes() == before


def test_review_finalize_passes_reviewer_windows_through(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
                "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
                "--reviewer-window", "B=2026-07-25T10:06:00Z..2026-07-25T10:10:00Z",
                cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr and "直列" in r.stderr
    assert json.loads(r.stdout)["aggregate"]["parallel_execution"] is False


def test_review_finalize_reviewer_window_gate_emits_own_typed_outcome_once(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--event-id", "finalize-event", "--root-event-id", "finalize-root", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["outcome"]["command"] == "review-finalize"
    assert payload["outcome"]["event_id"] == "finalize-event"
    assert state_file.read_bytes() == before
    token = hashlib.sha256(b"test").hexdigest()[:16]
    sidecar = json.loads(
        (state_dir / "telemetry" / "command-outcomes" / f"{token}.json").read_text(encoding="utf-8")
    )
    assert sidecar["records"] == [payload["outcome"]]


def test_review_finalize_nested_invalid_review_maps_to_invalid_input(state_dir, run_cli, tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema":"wrong"}', encoding="utf-8")
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(invalid),
        "--event-id", "finalize-invalid", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert payload["outcome"]["command"] == "review-finalize"
    assert state_file.read_bytes() == before


def test_review_finalize_gate_values_match_split_commands(state_dir, run_cli, read_state, tmp_path):
    """review-finalize の gate 値は aggregate-reviews → push-score 分割実行と一致する."""
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
            "--out", str(out), *_reviewer_windows(), cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]

    assert entry["composite"] == 4.2
    assert entry["min_item"] == 3.9
    assert entry["open_high"] == 0
    assert entry["review_agreement"] == 5.0
    assert entry["findings_evidence_path"]


# ===== closeout =====


def _push_passing_score(run_cli, state_dir, tmp_path):
    a, b = _two_reviews(tmp_path)
    run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
            *_reviewer_windows(), cwd=state_dir.parent, check=True)


def test_closeout_marks_passes_and_returns_next(state_dir, run_cli, read_state, tmp_path):
    _push_passing_score(run_cli, state_dir, tmp_path)

    r = run_cli("closeout", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["ok"] is True
    assert result["mark_passes"]["passes"] is True
    assert result["next"]["next_action"] == "report-complete"
    data = read_state(state_dir)
    assert data["passes"] is True
    assert data["loop_active"] is False


def test_closeout_gate_failure_keeps_state_and_returns_guidance(state_dir, run_cli, read_state, tmp_path):
    low = _review(tmp_path, "low.json", perspective="A", scores={
        "mission_achievement": 3.0,
        "accuracy": 3.2,
        "completeness": 3.4,
        "usability": 3.6,
    })
    run_cli("review-finalize", "--iteration", "1", "--input", str(low), cwd=state_dir.parent, check=True)

    r = run_cli("closeout", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "threshold" in r.stderr
    result = json.loads(r.stdout)
    assert result["ok"] is False
    assert "next" in result and result["next"]["next_action"]
    data = read_state(state_dir)
    assert data["passes"] is False
    assert data["loop_active"] is True


def test_closeout_without_score_fails_closed(state_dir, run_cli, read_state, tmp_path):
    r = run_cli("closeout", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "採点未実施" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_closeout_rejects_force_flag(state_dir, run_cli, tmp_path):
    """closeout は標準経路専用。override は mark-passes --force を直接使う."""
    r = run_cli("closeout", "--force", cwd=state_dir.parent)

    assert r.returncode == 2


def test_review_finalize_push_failure_after_aggregate_keeps_history(state_dir, run_cli, read_state, tmp_path):
    """aggregate 成功 → push-score 失敗 (同一 iteration 再 push を --resubmit-reason なし) でも
    score_history は増えない (#122 の再 push 保護が review-finalize 経由でも効く)."""
    a, b = _two_reviews(tmp_path)
    run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
            *_reviewer_windows(), cwd=state_dir.parent, check=True)

    before = list(read_state(state_dir)["score_history"])
    r = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
        *_reviewer_windows(), "--event-id", "push-gate", "--root-event-id", "push-root",
        cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "resubmit-reason" in r.stderr
    payload = json.loads(r.stdout)
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["outcome"]["command"] == "review-finalize"
    assert payload["outcome"]["event_id"] == "push-gate"
    assert read_state(state_dir)["score_history"] == before
    token = hashlib.sha256(b"test").hexdigest()[:16]
    sidecar = json.loads(
        (state_dir / "telemetry" / "command-outcomes" / f"{token}.json").read_text(encoding="utf-8")
    )
    assert sidecar["records"] == [payload["outcome"]]


def test_review_finalize_push_invalid_input_emits_own_outcome_once(
    state_dir, run_cli, read_state, tmp_path,
):
    if not Path("/dev/null").exists():
        pytest.skip("requires a null device")
    a = _review(tmp_path, "a.json", perspective="A")
    before = list(read_state(state_dir)["score_history"])

    result = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(a),
        "--out", "/dev/null", "--event-id", "push-invalid",
        "--root-event-id", "push-invalid-root", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert payload["outcome"]["command"] == "review-finalize"
    assert payload["outcome"]["event_id"] == "push-invalid"
    assert read_state(state_dir)["score_history"] == before
    token = hashlib.sha256(b"test").hexdigest()[:16]
    sidecar = json.loads(
        (state_dir / "telemetry" / "command-outcomes" / f"{token}.json").read_text(encoding="utf-8")
    )
    assert sidecar["records"] == [payload["outcome"]]
