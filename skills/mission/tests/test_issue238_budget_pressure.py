"""Issue #238 (S6): 時間予算の宣言と budget pressure シグナル — 予算切れ全損の防止.

背景: fable-5 実測 (2026-07-22) で mission が max_budget 到達により成果物ゼロの
blocked で終了し、投下コスト ($6/$14 の2回) が全損した。CLI の max-budget kill は
外側からの即死であり、mission 内部には「予算が尽きかけている」ことを知る手段が
なかった。

mission-state.py が自力で計測できる予算は時間 (started_at からの経過) なので、
`init --budget-minutes N` で宣言し、read-only の `next` が budget_pressure を返す:

- 80% 以上: warn (新規 optional spawn を控える advisory)
- 100% 以上: spawn 系 next_action (run-planner/run-executor/run-reviewers) を
  consider-halt へ override し、成果物確定 + `mark-halt --category partial-done`
  を促す。aggregate-reviews / mark-passes 等の安価なローカル完結手は override
  しない (誠実な終端を優先)。

ゲート意味論 (threshold / open_high / agreement / evidence) は変更しない。
"""
import json


def _read(tmp_path):
    return json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())


def _set_started_at(tmp_path, iso):
    p = tmp_path / ".mission-state" / "sessions" / "test.json"
    s = json.loads(p.read_text())
    s["started_at"] = iso
    p.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def test_init_records_budget_minutes(run_cli, tmp_path):
    run_cli("init", "budget test", "--budget-minutes", "30", cwd=tmp_path, check=True)
    assert _read(tmp_path)["budget_minutes"] == 30


def test_init_rejects_invalid_budget_minutes(run_cli, tmp_path):
    for bad in ("0", "-5", "abc", "inf", "nan"):
        r = run_cli("init", f"budget test {bad}", "--budget-minutes", bad, cwd=tmp_path)
        assert r.returncode != 0, f"budget-minutes={bad} should be rejected"


def test_next_without_budget_has_no_pressure(run_cli, tmp_path):
    run_cli("init", "budget test", cwd=tmp_path, check=True)
    r = run_cli("next", cwd=tmp_path, check=True)
    out = json.loads(r.stdout)
    assert out.get("budget_pressure") is None


def test_next_reports_ok_pressure_within_budget(run_cli, tmp_path):
    run_cli("init", "budget test", "--budget-minutes", "600", cwd=tmp_path, check=True)
    r = run_cli("next", cwd=tmp_path, check=True)
    out = json.loads(r.stdout)
    bp = out["budget_pressure"]
    assert bp["budget_minutes"] == 600
    assert bp["level"] == "ok"
    assert bp["pressure_pct"] < 80


def test_next_warns_at_80_percent(run_cli, tmp_path):
    """started_at を過去に置いて 80% 帯の warn を確認する."""
    run_cli("init", "budget test", "--budget-minutes", "10", cwd=tmp_path, check=True)
    # 8.5 分前開始 → 85%
    import datetime
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=8.5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_started_at(tmp_path, past)
    r = run_cli("next", cwd=tmp_path, check=True)
    out = json.loads(r.stdout)
    bp = out["budget_pressure"]
    assert bp["level"] == "warn", bp
    # warn は next_action を変えない (advisory のみ)
    assert out["next_action"] == "run-planner"


def test_next_overrides_spawn_action_to_consider_halt_when_exceeded(run_cli, tmp_path):
    run_cli("init", "budget test", "--budget-minutes", "10", cwd=tmp_path, check=True)
    import datetime
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_started_at(tmp_path, past)
    r = run_cli("next", cwd=tmp_path, check=True)
    out = json.loads(r.stdout)
    assert out["budget_pressure"]["level"] == "exceeded"
    assert out["next_action"] == "consider-halt"
    assert "partial-done" in (out.get("command_hint") or "") or "partial-done" in (out.get("summary") or "")


def test_next_does_not_override_cheap_finishing_actions(run_cli, tmp_path):
    """予算超過でも scoring 完了間際 (aggregate-reviews 等のローカル手) は override しない。
    誠実な終端 (採点して pass/fail を確定する) を優先する."""
    run_cli("init", "budget test", "--budget-minutes", "10", cwd=tmp_path, check=True)
    run_cli("set", "phase=scoring", cwd=tmp_path, check=True)
    import datetime
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_started_at(tmp_path, past)
    r = run_cli("next", cwd=tmp_path, check=True)
    out = json.loads(r.stdout)
    assert out["budget_pressure"]["level"] == "exceeded"
    assert out["next_action"] == "aggregate-reviews"


def test_exceeded_override_does_not_touch_terminal_or_await(run_cli, tmp_path):
    """halt 済み (report-blocker) は budget override の対象外."""
    run_cli("init", "budget test", "--budget-minutes", "10", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "blocked", "--category", "blocked-external",
            cwd=tmp_path, check=True)
    import datetime
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_started_at(tmp_path, past)
    r = run_cli("next", cwd=tmp_path, check=True)
    out = json.loads(r.stdout)
    assert out["next_action"] == "report-blocker"


def test_next_survives_naive_started_at_timestamp(run_cli, tmp_path):
    """timezone なしの started_at (手動破損・旧経路) でも next がクラッシュせず、
    naive/aware 正規化のうえ pressure を返す (レビュー指摘)."""
    run_cli("init", "budget test", "--budget-minutes", "10", cwd=tmp_path, check=True)
    import datetime
    naive_past = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(minutes=12)).strftime("%Y-%m-%dT%H:%M:%S")  # Z なし
    _set_started_at(tmp_path, naive_past)
    r = run_cli("next", cwd=tmp_path)
    assert r.returncode == 0, f"next crashed: {r.stderr}"
    out = json.loads(r.stdout)
    assert out["budget_pressure"] is not None
    assert out["budget_pressure"]["level"] == "exceeded"
