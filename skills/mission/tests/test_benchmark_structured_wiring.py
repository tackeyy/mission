"""#587: 構造化 findings 採点をベンチランナーへ配線する。

既存の marker 採点は**削除せず併記**する (新旧の相関を観測するため)。
新採点は正解キーがあるタスクにのみ適用し、無いタスクでは null にする。
"""

import importlib.util
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _runner():
    spec = importlib.util.spec_from_file_location(
        "run_claude_goal_vs_mission", BENCH / "run_claude_goal_vs_mission.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASK = {"id": "t1", "category": "c", "prompt": "p", "validator": "v"}

TABLE = (
    "| location | key | expected | actual | verdict |\n"
    "|---|---|---|---|---|\n"
    "| spec.md | request_timeout_ms | 3000 | 27000 | drift |\n"
)

KEY = {"defects": [{"location": "spec.md", "key": "request_timeout_ms",
                    "expected": "3000", "actual": "27000"}],
       "decoys": []}


# --- プロンプト ----------------------------------------------------------------

def test_findings_table_is_requested_from_both_arms():
    """採点対象の形式を片方の arm にだけ要求すると比較が成立しない。"""
    module = _runner()
    for arm in ("mission", "claude_code_goal_command"):
        prompt = module.build_prompt(TASK, arm, "out.md", require_findings_table=True)
        assert "| location | key | expected | actual | verdict |" in prompt
        assert "no-finding" in prompt


def test_findings_table_not_requested_by_default():
    """既存 cohort の挙動を変えない (追加は opt-in)。"""
    module = _runner()
    prompt = module.build_prompt(TASK, "mission", "out.md")
    assert "| location | key | expected | actual | verdict |" not in prompt


def test_degraded_prompt_restricts_readable_fixtures():
    module = _runner()
    prompt = module.build_prompt(
        TASK, "mission", "out.md",
        degraded_readable_fixtures=["fixtures/tail/x/spec.md"],
    )
    assert "fixtures/tail/x/spec.md" in prompt
    assert "Read only" in prompt


# --- 採点 ----------------------------------------------------------------------

def test_structured_score_is_recorded_alongside_marker_score():
    module = _runner()
    result = module.evaluate_structured_findings(TABLE, KEY)
    assert result["f1"] == 1.0
    assert result["scoring_method"] == "structured_findings_exact_match_v1"


def test_missing_answer_key_yields_null_not_zero():
    """正解キーが無いタスクを 0 点にしない (採点対象外である)。"""
    module = _runner()
    assert module.evaluate_structured_findings(TABLE, None) is None


def test_unparseable_artifact_is_reported_as_error_not_zero():
    """findings 表が無いことを「発見ゼロ」と混同しない。"""
    module = _runner()
    result = module.evaluate_structured_findings("no table here", KEY)
    assert result["f1"] is None
    assert result["structured_findings_error"]


def test_answer_keys_resolve_for_configured_tail_tasks():
    module = _runner()
    key = module.load_task_answer_key("tail-config-spec-drift")
    assert key is not None and len(key["defects"]) == 7
    assert module.load_task_answer_key("tail-does-not-exist") is None
