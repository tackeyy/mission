"""#268: permission-mode 汚染の検出・記録・防止.

2026-07-23 監査で、CC セッション Bash からの起動により子 `claude -p` の
`--permission-mode acceptEdits` が env scrub で default に強制降格されていた
(7/21 以降の全 24 records、stderr に警告)。Contract under test:

1. detect_permission_degradation: stderr から降格警告を検出する
2. child_env: 子プロセス env に CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0 を明示し
   降格を防止する (既存 env は保持)
3. summarize: アーム別 permission_degraded_records を集計し、degraded が
   1 件でもあれば limitations に警告を追加する
"""

import importlib.util
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    path = BENCH / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


# ===== 1. detect_permission_degradation =====

def test_detects_forced_default_warning():
    stderr = ("⚠ Permission mode forced to default — CLAUDE_CODE_SUBPROCESS_ENV_SCRUB "
              "is set (allowed_non_write_users hardening).")
    assert MODULE.detect_permission_degradation(stderr) is True


def test_clean_stderr_not_degraded():
    assert MODULE.detect_permission_degradation("") is False
    assert MODULE.detect_permission_degradation("some other warning") is False


# ===== 2. child_env =====

def test_child_env_disables_scrub():
    env = MODULE.child_env({"PATH": "/usr/bin", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"})
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "0"
    assert env["PATH"] == "/usr/bin"


def test_child_env_sets_scrub_even_when_absent():
    env = MODULE.child_env({"PATH": "/usr/bin"})
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "0"


# ===== 3. summarize: degraded count + limitations warning =====

def _record(arm, *, degraded=False):
    return {
        "arm": arm,
        "run_status": "completed",
        "comparable_attempt": True,
        "completion": True,
        "validator_pass": True,
        "human_quality_score": 5.0,
        "intervention_count": 0,
        "evidence_completeness": 5.0,
        "quality_marker_score": 1.0,
        "elapsed_minutes": 10.0,
        "total_cost_usd": 2.0,
        "permission_mode_degraded": degraded,
    }


def test_summarize_counts_degraded_records():
    tasks_path = BENCH / "tasks.discriminating.json"
    records = [
        _record("mission", degraded=True),
        _record("claude_code_goal_command", degraded=False),
    ]
    summary = MODULE.summarize(records, [{"id": "t1"}], "rid", "abc1234", tasks_path)
    assert summary["arms"]["mission"]["permission_degraded_records"] == 1
    assert summary["arms"]["claude_code_goal_command"]["permission_degraded_records"] == 0
    assert any("permission" in lim.lower() for lim in summary["limitations"])


def test_summarize_clean_run_has_no_permission_warning():
    tasks_path = BENCH / "tasks.discriminating.json"
    records = [_record("mission"), _record("claude_code_goal_command")]
    summary = MODULE.summarize(records, [{"id": "t1"}], "rid", "abc1234", tasks_path)
    assert summary["arms"]["mission"]["permission_degraded_records"] == 0
    assert not any("permission" in lim.lower() for lim in summary["limitations"])
