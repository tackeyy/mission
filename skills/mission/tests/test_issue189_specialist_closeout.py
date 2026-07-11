"""Issue #189: optional specialist の selection→invocation クローズアウト可視化。

実運用で specialist-invocation-gap (selected だが terminal invocation ログのない
optional specialist) が 9 sessions 検出された。required/高リスク specialist は
既存の accounting_required/result_required gate が exit 2 で止めるため、mark-passes
成功後に残るのは常に optional。ここでは hard gate ではなく WARN + next の hint で
可視化する (optional specialist の graceful degradation を維持するため)。
"""
import json


def _prep_pass(state_dir, run_cli):
    """mark-passes が成功する最小限の score_history を作る (findings gate 不要な legacy evidence)."""
    r = run_cli(
        "push-score", "--iteration", "1",
        "--items", '{"mission_achievement":4.5,"accuracy":4.5,"completeness":4.5,"usability":4.5}',
        "--composite", "4.5", "--min-item", "4.5",
        "--scoring-output", str(state_dir.parent / "scoring.md"),
        cwd=state_dir.parent,
    )
    (state_dir.parent / "scoring.md").write_text("dummy")
    r = run_cli(
        "push-score", "--iteration", "1",
        "--items", '{"mission_achievement":4.5,"accuracy":4.5,"completeness":4.5,"usability":4.5}',
        "--composite", "4.5", "--min-item", "4.5",
        "--scoring-output", str(state_dir.parent / "scoring.md"),
        "--resubmit-reason", "test setup",
        cwd=state_dir.parent, check=True,
    )
    return r


def test_mark_passes_warns_on_selected_specialist_without_invocation(state_dir, run_cli, read_state):
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["specialists_selected"] = [{"skill": "doc-writer", "role": "doc-writer"}]
    data["task_profile"] = {"primary": "documentation"}
    data["specialists_decision"] = {"policy": "auto"}
    sf.write_text(json.dumps(data))

    _prep_pass(state_dir, run_cli)
    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, f"optional specialist の未クローズは gate にしない: {r.stderr}"
    assert "WARNING [#189]" in r.stderr
    assert "doc-writer" in r.stderr
    assert "log-invocation" in r.stderr
    s = read_state(state_dir)
    assert s["passes"] is True


def test_mark_passes_no_warning_when_specialist_invocation_logged(state_dir, run_cli, read_state):
    """selected specialist に (どの status でも) invocation ログがあれば WARN しない."""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["specialists_selected"] = [{"skill": "doc-writer", "role": "doc-writer"}]
    data["specialist_invocations"] = [{
        "iteration": 1, "phase": "review", "role": "doc-writer", "skill": "doc-writer",
        "mode": "skill-tool", "status": "skipped", "reason": "not needed for this diff",
        "timestamp": "2026-05-25T00:00:00Z",
    }]
    data["task_profile"] = {"primary": "documentation"}
    data["specialists_decision"] = {"policy": "auto"}
    sf.write_text(json.dumps(data))

    _prep_pass(state_dir, run_cli)
    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0
    assert "WARNING [#189]" not in r.stderr


def test_mark_passes_no_warning_for_phase_plan_only_specialist(state_dir, run_cli, read_state):
    """specialists_phase_plan にのみ登場する specialist (specialists_selected にはない) は
    未クローズ扱いしない (mission-audit.py の specialist_invocation_gap_skills と同じ除外)."""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["specialists_selected"] = [{"skill": "code-review-provider", "role": "code-review"}]
    data["specialist_invocations"] = [{
        "iteration": 1, "phase": "review", "role": "code-review", "skill": "code-review-provider",
        "mode": "skill-tool", "status": "completed", "timestamp": "2026-05-25T00:00:00Z",
    }]
    data["specialists_phase_plan"] = [{
        "phase": "execution", "providers": ["integration-test-provider"],
        "roles": ["integration-test"], "max_providers": 1,
    }]
    data["task_profile"] = {"primary": "documentation"}
    data["specialists_decision"] = {"policy": "auto"}
    sf.write_text(json.dumps(data))

    _prep_pass(state_dir, run_cli)
    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0
    assert "WARNING [#189]" not in r.stderr, \
        "phase_plan にのみ存在する integration-test-provider を偽陽性検出している"


def test_mark_passes_no_warning_when_no_specialists_selected(state_dir, run_cli, read_state):
    _prep_pass(state_dir, run_cli)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0
    assert "WARNING [#189]" not in r.stderr


def test_next_surfaces_unclosed_specialists_before_mark_passes(state_dir, run_cli):
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["specialists_selected"] = [{"skill": "doc-writer", "role": "doc-writer"}]
    sf.write_text(json.dumps(data))

    _prep_pass(state_dir, run_cli)
    r = run_cli("next", cwd=state_dir.parent, check=True)
    out = json.loads(r.stdout)

    assert out["next_action"] == "mark-passes"
    assert out["details"]["unclosed_specialists"] == ["doc-writer"]


def test_next_omits_unclosed_specialists_key_when_none(state_dir, run_cli):
    _prep_pass(state_dir, run_cli)
    r = run_cli("next", cwd=state_dir.parent, check=True)
    out = json.loads(r.stdout)
    assert out["next_action"] == "mark-passes"
    assert "unclosed_specialists" not in out.get("details", {})


def test_mark_passes_force_does_not_emit_unclosed_warning(state_dir, run_cli, read_state):
    """#189 review fix: --force は accounting gate ごと skip するため unclosed に required
    specialist が混入し得る。「optional のため」という文言が誤りになるので --force 経路では
    このWARN自体を出さない。"""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["specialists_selected"] = [{"skill": "security-reviewer", "role": "security-reviewer"}]
    data["score_history"] = []
    sf.write_text(json.dumps(data))

    r = run_cli("mark-passes", "--force", "--reason", "no reviewer available",
                cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr
    assert "WARNING [#189]" not in r.stderr
    assert read_state(state_dir)["passes"] is True
