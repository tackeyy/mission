"""Issue #3: push-score の --open-high フラグと mark-passes の High 件数 gate のテスト。"""
import json


def _push_score(run_cli, state_dir, iteration=1, composite=None, min_item=None,
                open_high=0, items=None):
    # #122: composite/min_item must not inflate above the items detail. Derive them
    # from items by default so these gate tests stay consistent with the new check.
    items = items or {"mission_achievement": 4.5, "accuracy": 4.0}
    numeric = [v for v in items.values() if isinstance(v, (int, float))]
    if composite is None:
        composite = round(sum(numeric) / len(numeric), 2)
    if min_item is None:
        min_item = min(numeric)
    args = [
        "push-score",
        "--iteration", str(iteration),
        "--composite", str(composite),
        "--min-item", str(min_item),
        "--items", json.dumps(items),
        "--open-high", str(open_high),
    ]
    return run_cli(*args, cwd=state_dir.parent, check=True)


def test_push_score_saves_open_high(state_dir, run_cli):
    """push-score --open-high N が score_history に保存される。"""
    _push_score(run_cli, state_dir, open_high=2)
    data = json.loads((state_dir.parent / ".mission-state" / "sessions" / "test.json").read_text())
    latest = data["score_history"][-1]
    assert latest["open_high"] == 2


def test_push_score_open_high_default_zero(state_dir, run_cli):
    """--open-high を指定しない場合は 0 で保存される (後方互換)。"""
    items = {"mission_achievement": 4.5, "accuracy": 4.0}
    run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.25",
        "--min-item", "4.0",
        "--items", json.dumps(items),
        cwd=state_dir.parent,
        check=True,
    )
    data = json.loads((state_dir.parent / ".mission-state" / "sessions" / "test.json").read_text())
    latest = data["score_history"][-1]
    assert latest.get("open_high", 0) == 0


def test_mark_passes_rejects_when_open_high_nonzero(state_dir, run_cli, read_state):
    """open_high > 0 なら mark-passes は exit 2。"""
    _push_score(run_cli, state_dir, open_high=2)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "未解決 High" in r.stderr, f"stderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is False


def test_mark_passes_passes_when_open_high_zero(state_dir, run_cli, read_state):
    """open_high=0 なら mark-passes は通過する。"""
    _push_score(run_cli, state_dir, open_high=0, composite=4.25, min_item=4.0)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, f"expected 0, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True


def test_mark_passes_backward_compat_no_open_high_field(state_dir, run_cli, read_state):
    """score_history に open_high フィールドがない既存形式は 0 扱いで通過する。"""
    # open_high なしで手動挿入 (旧形式のシミュレーション)
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["score_history"].append({
        "iteration": 1,
        "composite": 4.5,
        "min_item": 4.0,
        "items": {"mission_achievement": 4.5},
        "timestamp": "2026-01-01T00:00:00Z",
        # open_high フィールドなし
    })
    sf.write_text(json.dumps(data))

    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, f"後方互換で通過すべき、got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True


def test_mark_passes_force_bypasses_open_high_gate(state_dir, run_cli, read_state):
    """(c) --force バイパス: open_high=2 でも --force --reason "x" なら exit0 かつ passes=True."""
    _push_score(run_cli, state_dir, open_high=2)
    r = run_cli("mark-passes", "--force", "--reason", "emergency override for test",
                cwd=state_dir.parent)
    assert r.returncode == 0, f"--force は open_high gate を bypass すべき, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True


def test_push_score_rejects_negative_open_high(state_dir, run_cli):
    """(d) 負値 reject: push-score --open-high -1 が exit2 で拒否される (Fix #4 対応)."""
    items = {"mission_achievement": 4.5, "accuracy": 4.0}
    r = run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.5",
        "--min-item", "4.0",
        "--items", json.dumps(items),
        "--open-high", "-1",
        cwd=state_dir.parent,
    )
    assert r.returncode == 2, f"--open-high -1 は exit 2 で reject すべき, got {r.returncode}\nstderr: {r.stderr}"
    assert "0 以上" in r.stderr or "open-high" in r.stderr, f"エラーメッセージ不正: {r.stderr}"
