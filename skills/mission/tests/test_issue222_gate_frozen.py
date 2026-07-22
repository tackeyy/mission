"""Issue #222 (A-2/A-3): gate-sensitive フィールドの set ガード.

`set` で gate 判定に影響する state フィールドを無条件に書き換えられる穴を塞ぐ。
単純 FROZEN_FIELDS 追加ではなく、正規ワークフロー (complexity/tier と同時の
reviewer_count 明示、loop_active と同時の halt_reason 空化による F-4 再活性化) を
壊さない条件付きガードとして実装する。

- A-2: `set reviewer_count=1` 単独で reviewer を 1 名に落とし agreement gate を
  無効化する攻撃 (delta=0 が数学的に確定) を防ぐ。
- A-3: `set halt_category=stale` で承認 halt を無承認 resume に化かす攻撃、
  `set halt_reason=` 単独クリアで halt 分岐を回避する攻撃を防ぐ。
"""
import json


def _read(tmp_path):
    return json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())


# ===== A-2: reviewer_count =====

def test_set_reviewer_count_alone_rejected(run_cli, tmp_path):
    """reviewer_count 単独 set は reject (agreement gate 無効化の防止)."""
    run_cli("init", "gate test", "--complexity", "Complex", cwd=tmp_path, check=True)
    r = run_cli("set", "reviewer_count=1", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    # state は書き換わっていない (#266: Complex 初期値は 2)
    assert _read(tmp_path)["reviewer_count"] == 2


def test_set_reviewer_count_with_complexity_allowed(run_cli, tmp_path):
    """complexity と同時の reviewer_count 明示は従来どおり許可 (運用余地)."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    r = run_cli("set", "complexity=Critical", "reviewer_count=4", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert _read(tmp_path)["reviewer_count"] == 4


def test_set_reviewer_count_with_review_tier_allowed(run_cli, tmp_path):
    """review_tier と同時の reviewer_count 明示は従来どおり許可."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    r = run_cli("set", "review_tier=full", "reviewer_count=4", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert _read(tmp_path)["reviewer_count"] == 4


# ===== A-3: halt_category / halt_reason =====

def test_set_halt_category_rejected(run_cli, tmp_path):
    """halt_category の set 変更は全面 reject (変更は mark-halt/refresh-pid/resume 専用)."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "waiting external", "--category", "blocked-external",
            cwd=tmp_path, check=True)
    r = run_cli("set", "halt_category=stale", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    assert _read(tmp_path)["halt_category"] == "blocked-external"


def test_set_halt_reason_alone_rejected(run_cli, tmp_path):
    """halt_reason 単独 set は reject (halt 分岐回避の防止)."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "blocked", "--category", "blocked-external",
            cwd=tmp_path, check=True)
    r = run_cli("set", "halt_reason=", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    assert _read(tmp_path)["halt_reason"] == "blocked"


def test_set_halt_reason_with_loop_active_rejected_in_favor_of_reactivate(run_cli, tmp_path):
    """汎用 set は承認監査を迂回するため、halt再活性化を専用コマンドへ限定."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "blocked", "--category", "blocked-external",
            cwd=tmp_path, check=True)
    r = run_cli("set", "loop_active=true", "halt_reason=", cwd=tmp_path)
    assert r.returncode == 2
    assert "reactivate" in r.stderr
    s = _read(tmp_path)
    assert s["loop_active"] is False
    assert s["halt_reason"] == "blocked"


def test_set_loop_active_true_alone_cannot_create_active_halted_state(run_cli, tmp_path):
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    run_cli(
        "mark-halt",
        "--reason",
        "waiting for approval",
        "--category",
        "awaiting-approval",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli("set", "loop_active=true", cwd=tmp_path)

    assert result.returncode == 2
    assert "reactivate" in result.stderr
    state = _read(tmp_path)
    assert state["loop_active"] is False
    assert state["halt_reason"] == "waiting for approval"


def test_set_halt_reason_with_loop_active_false_rejected(run_cli, tmp_path):
    """loop_active=false と同時の halt_reason 空化は reject。
    halt 証跡のない「静かな終端」を作れてしまうため、許可は loop_active=true のみ."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "blocked", "--category", "blocked-external",
            cwd=tmp_path, check=True)
    r = run_cli("set", "loop_active=false", "halt_reason=", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    assert _read(tmp_path)["halt_reason"] == "blocked"


def test_set_reviewer_count_with_halt_reason_still_rejected(run_cli, tmp_path):
    """複合: reviewer_count 単独 + 無関係な halt_reason を混ぜても、
    tier キーがなければ reviewer_count ガードで reject される."""
    run_cli("init", "gate test", "--complexity", "Complex", cwd=tmp_path, check=True)
    r = run_cli("set", "reviewer_count=1", "loop_active=true", "halt_reason=", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    assert _read(tmp_path)["reviewer_count"] == 2  # #266: Complex 初期値


def test_gate_frozen_set_halt_category_blocks_reactivation(run_cli, tmp_path):
    """A-3 エンドツーエンド: halt_category=stale への書き換えが reject され、
    承認 halt が refresh-pid で無承認 reactivate されない."""
    run_cli("init", "gate test", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "awaiting user approval", "--category", "awaiting-approval",
            cwd=tmp_path, check=True)
    r = run_cli("set", "halt_category=stale", cwd=tmp_path)
    assert r.returncode == 2, f"expected reject, got {r.returncode}: {r.stderr}"
    s = _read(tmp_path)
    assert s["halt_category"] == "awaiting-approval"
    assert s["loop_active"] is False
