"""#276: mission 起動時の Simple→goal adaptive routing.

discriminating-v2 (品質同点・mission 5.4x) と実運用 95% iter1 素通しの実測に基づき、
ゲートが仕事をしないタスクを goal へルーティングする。Contract under test:

1. Simple + リスクシグナルなし + 強制なし → init は route:"goal" を返し
   session state を作らない (pass-rate 統計を汚さず偽 pass も作らない)
2. Simple でも不可逆キーワードあり → mission 維持 (安全側エスカレータ)
3. Standard/Complex/Critical → 従来どおり mission
4. --force-mission → Simple でも mission
5. --review-tier 明示 (ユーザー意思) → mission
"""

import json
from pathlib import Path


def _sessions(tmp_path) -> list:
    d = tmp_path / ".mission-state" / "sessions"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def test_simple_no_signals_routes_to_goal(run_cli, tmp_path):
    """Simple + シグナルなし → route:"goal" + state 不生成."""
    r = run_cli("init", "typo を1箇所直す", "--complexity", "Simple", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["route"] == "goal"
    assert out["complexity"] == "Simple"
    assert _sessions(tmp_path) == [], "route=goal で session state を作ってはならない"


def test_simple_with_irreversible_signal_stays_mission(run_cli, tmp_path):
    """Simple + 不可逆キーワード → mission 維持 (state 生成)."""
    r = run_cli("init", "deploy the hotfix to production", "--complexity", "Simple",
                cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(_sessions(tmp_path)) == 1
    state = json.loads(_sessions(tmp_path)[0].read_text())
    assert state["complexity"] == "Simple"
    assert state["review_tier_signals"], "シグナルが記録されているべき"


def test_standard_stays_mission(run_cli, tmp_path):
    """Standard → 従来どおり mission."""
    r = run_cli("init", "some mission", "--complexity", "Standard", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(_sessions(tmp_path)) == 1


def test_force_mission_overrides_routing(run_cli, tmp_path):
    """--force-mission → Simple でも mission ループ."""
    r = run_cli("init", "typo を1箇所直す", "--complexity", "Simple",
                "--force-mission", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(_sessions(tmp_path)) == 1


def test_user_review_tier_overrides_routing(run_cli, tmp_path):
    """--review-tier 明示はユーザーが mission 機構を求めた意思 → routing しない."""
    r = run_cli("init", "typo を1箇所直す", "--complexity", "Simple",
                "--review-tier", "light", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert len(_sessions(tmp_path)) == 1


def test_routed_output_contains_goal_contract_guidance(run_cli, tmp_path):
    """route 出力は goal 契約 (5見出し) での完遂指示を含む (SKILL.md 消費用)."""
    r = run_cli("init", "typo を1箇所直す", "--complexity", "Simple", cwd=tmp_path)
    out = json.loads(r.stdout)
    assert "Stop Condition" in out["guidance"]
    assert "goal" in out["guidance"]
