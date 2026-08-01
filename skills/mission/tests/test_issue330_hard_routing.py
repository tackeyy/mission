"""#330: routing のコマンド層 hard 化 — set complexity=Simple が verdict を直接実行.

portfolio-v2 (2026-08-02) で #325 の next 駆動 gate は発火 1/3 に留まった
(orchestrator が planning で next を呼ばない経路に bypass される — #309/#326 と
同類型の 3 度目の観測)。prose / guidance は縛れないため、コマンド自身が routing
verdict を実行する。

Contract under test:
1. set complexity=Simple (routing 条件充足) → state が routed-goal で halt され、
   stdout に route:"goal" verdict が出る (orchestrator の next 消費に依存しない)
2. 除外条件で halt しない: issue_ref / force_mission / checker role /
   score_history あり / user 指定 tier / 再導出でシグナルあり
3. set complexity=Standard → halt しない
4. init --complexity Simple の既存 routing (#276/#304) は非退行
5. routed 後の state は loop_active=false + halt_category=routed-goal
"""

import json
from pathlib import Path


def _sessions(tmp_path):
    d = tmp_path / ".mission-state" / "sessions"
    return sorted(p for p in d.glob("*.json") if "assumptions" not in p.name) if d.is_dir() else []


def _init_unknown(run_cli, tmp_path, *extra):
    # complexity 未指定で init (Unknown のまま state 生成 = routing を通らない入口)
    return run_cli("init", "typo を1箇所直す", *extra, cwd=tmp_path, check=True)


def test_set_simple_routes_and_halts(run_cli, tmp_path):
    _init_unknown(run_cli, tmp_path)
    r = run_cli("set", "complexity=Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out.get("route") == "goal", "set がコマンド層で routing verdict を返すべき"
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is False
    assert state["halt_category"] == "routed-goal"


def test_set_simple_with_issue_ref_keeps_loop(run_cli, tmp_path):
    run_cli("init", "typo を1箇所直す", "--issue-ref", "418", cwd=tmp_path, check=True)
    r = run_cli("set", "complexity=Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is True


def test_set_simple_with_force_mission_keeps_loop(run_cli, tmp_path):
    run_cli("init", "typo を1箇所直す", "--force-mission", cwd=tmp_path, check=True)
    r = run_cli("set", "complexity=Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is True


def test_set_simple_checker_role_keeps_loop(run_cli, tmp_path):
    run_cli("init", "PR review", "--role", "checker", cwd=tmp_path, check=True)
    r = run_cli("set", "complexity=Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is True


def test_set_simple_with_signals_keeps_loop(run_cli, tmp_path):
    run_cli("init", "deploy the hotfix to production", cwd=tmp_path, check=True)
    r = run_cli("set", "complexity=Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is True, "不可逆シグナルありは routing しない"


def test_set_simple_user_tier_keeps_loop(run_cli, tmp_path):
    run_cli("init", "typo を1箇所直す", "--review-tier", "light", cwd=tmp_path, check=True)
    r = run_cli("set", "complexity=Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is True


def test_set_standard_keeps_loop(run_cli, tmp_path):
    _init_unknown(run_cli, tmp_path)
    r = run_cli("set", "complexity=Standard", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["loop_active"] is True


def test_init_simple_still_routes_without_state(run_cli, tmp_path):
    """#276/#304 の init 経路 routing は非退行 (state 不生成)."""
    r = run_cli("init", "typo を1箇所直す", "--complexity", "Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["route"] == "goal"
    assert _sessions(tmp_path) == []
