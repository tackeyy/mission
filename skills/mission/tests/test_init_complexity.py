"""M7: init --complexity 引数のテスト (2026-06-10 検査レポート).

全ランで complexity が "Unknown" のまま放置され、P3-5 (Simple 限定インライン executor) の
分岐が機能していなかった。init 時に指定可能にし、未指定は WARN で気づかせる。
"""
import json


def _read(tmp_path):
    return json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())


def test_init_with_complexity_sets_field(run_cli, tmp_path):
    run_cli("init", "M7 test mission", "--complexity", "Standard", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["complexity"] == "Standard"
    assert s["reviewer_count"] == 2


def test_init_complexity_sets_reviewer_count_mapping(run_cli, tmp_path):
    """Simple→1 / Complex→3 のマッピングが state に反映される."""
    run_cli("init", "M7 simple mission", "--complexity", "Simple", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["complexity"] == "Simple"
    assert s["reviewer_count"] == 1


def test_init_rejects_invalid_complexity(run_cli, tmp_path):
    r = run_cli("init", "M7 bad mission", "--complexity", "Huge", cwd=tmp_path)
    assert r.returncode != 0


def test_init_without_complexity_warns_and_keeps_unknown(run_cli, tmp_path):
    """未指定時は後方互換で Unknown のまま、stderr に WARN."""
    r = run_cli("init", "M7 legacy mission", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = _read(tmp_path)
    assert s["complexity"] == "Unknown"
    assert "complexity" in r.stderr.lower()


# ===== iter2 (A-M1): set complexity 時の reviewer_count 自動同期 =====


def test_set_complexity_syncs_reviewer_count(run_cli, tmp_path):
    run_cli("init", "M7 sync mission", cwd=tmp_path, check=True)
    run_cli("set", "complexity=Complex", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["complexity"] == "Complex"
    assert s["reviewer_count"] == 3


def test_set_complexity_explicit_reviewer_count_wins(run_cli, tmp_path):
    """reviewer_count を同時に明示した場合はそちらが優先 (Critical+Critic 追加等の運用余地)."""
    run_cli("init", "M7 sync mission 2", cwd=tmp_path, check=True)
    run_cli("set", "complexity=Critical", "reviewer_count=4", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["reviewer_count"] == 4


# ===== M-audit-2 (2026-06-11 監査): --max-iter デフォルトの doc/code 整合 =====
# SKILL.md 引数表は「デフォルト 3」(98 セッション実測の ROI 根拠) だが
# init は default=None で、null 時は stagnation 頼みの上限なしになっていた。


def test_init_default_max_iter_is_3(run_cli, tmp_path):
    """未指定時は SKILL.md の文書どおり max_iter=3."""
    run_cli("init", "audit default mission", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["max_iter"] == 3


def test_init_max_iter_zero_means_unlimited(run_cli, tmp_path):
    """--max-iter 0 は「上限なし (stagnation 3 回で停止)」モード = null を保持."""
    run_cli("init", "audit unlimited mission", "--max-iter", "0", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["max_iter"] is None


def test_init_explicit_max_iter_kept(run_cli, tmp_path):
    run_cli("init", "audit explicit mission", "--max-iter", "7", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["max_iter"] == 7


def test_init_specialist_metadata_defaults(run_cli, tmp_path):
    run_cli("init", "specialist metadata mission", cwd=tmp_path, check=True)
    s = _read(tmp_path)
    assert s["task_profile"] == {}
    assert s["specialists_mode"] == "auto"
    assert s["specialists_selected"] == []
    assert s["specialists_unavailable"] == []


def test_set_records_specialist_metadata_json(run_cli, tmp_path):
    run_cli("init", "specialist metadata mission", cwd=tmp_path, check=True)
    task_profile = {
        "domain": "backend",
        "evidence": ["mission-state.py owns state mutation"],
    }
    selected = [
        {"id": "dev-backend", "reason": "state schema change"},
        {"id": "dev-unit-tester", "reason": "pytest coverage"},
    ]
    unavailable = [
        {"id": "dev-frontend", "reason": "no UI surface touched"},
    ]

    run_cli(
        "set",
        f"task_profile={json.dumps(task_profile)}",
        "specialists_mode=manual",
        f"specialists_selected={json.dumps(selected)}",
        f"specialists_unavailable={json.dumps(unavailable)}",
        cwd=tmp_path,
        check=True,
    )

    s = _read(tmp_path)
    assert s["task_profile"] == task_profile
    assert s["specialists_mode"] == "manual"
    assert s["specialists_selected"] == selected
    assert s["specialists_unavailable"] == unavailable
