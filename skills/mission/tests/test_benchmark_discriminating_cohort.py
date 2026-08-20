"""Issue #262: discriminating cohort — 品質天井の解消 + iter>=2 強制.

openworld-v1 (2026-07-22) で全 records が marker 1.0 / 分散 0 となり cohort が
sonnet-5 に対して判別力を失った実害への対策。Contract under test:

1. `tasks.discriminating.json` は 5 tasks / answer-key 自己隠蔽 / prompt_rules を持つ
2. 判別力: 各 task は quality_markers >= 6 (recall が分布する) + forbidden_markers >= 1
3. fail-first: `fail_first: true` の task が 2 件以上あり、mission_max_iter >= 3
4. fixture 自己整合: 各 quality_marker は最低 1 pattern が当該 task の fixture 本文に
   実在する (答えの実在保証 = marker が fixture から発見可能)
5. N>=10 採用判定 runbook が存在しコマンドとゲート基準を含む
"""

import json
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
TASKS_PATH = BENCHMARK_DIR / "tasks.discriminating.json"


def _runner():
    path = BENCHMARK_DIR / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _data() -> dict:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def test_discriminating_cohort_structure():
    data = _data()
    assert data["benchmark"] == "mission-vs-goal-pilot"
    assert data["cohort"] == "discriminating"
    assert data["arms"] == ["claude_code_goal_command", "mission"]
    assert data["task_count"] == 5
    assert len(data["tasks"]) == 5
    assert len({t["id"] for t in data["tasks"]}) == 5
    assert "benchmarks/mission-vs-goal/tasks.discriminating.json" in data["hidden_paths"]
    assert data["prompt_rules"]
    required = {
        "id", "category", "difficulty", "prompt", "validator", "primary_metric",
        "hypothesis", "fixtures", "first_pass_failure_design",
        "quality_markers", "forbidden_markers", "markers_hidden",
    }
    for task in data["tasks"]:
        assert required <= task.keys(), f"{task.get('id')} missing keys"
        assert task["id"].startswith("disc-")
        assert task["markers_hidden"] is True
        assert task["mission_complexity"] in {"Complex", "Critical"}


def test_discrimination_marker_density():
    """天井飽和対策: 各 task は marker >= 6、decoy (forbidden) >= 1."""
    for task in _data()["tasks"]:
        assert len(task["quality_markers"]) >= 6, task["id"]
        assert len(task["forbidden_markers"]) >= 1, task["id"]


# #562 で quality_markers は regex 化された。パターン文字列そのものを本文として
# 使う旧方式は成立しない (regex は自分自身にマッチしない) ため、各タスクの
# 「正しい発見を 1 件だけ述べた最小テキスト」を明示的に持つ。
# forbidden_markers は substr のままなので decoy はリテラルを連結すればよい。
_CLEAN_FINDING_TEXT = {
    "disc-config-sprawl": "auth session_ttl_sec shows an undocumented divergence from the platform default.",
    "disc-release-ledger": "mig-2207 is applied in the ops log but missing from the migration index.",
    "disc-contract-drift": "client-go sends x-signature-v2 instead of the spec header x-sig.",
    "disc-metrics-reconcile": "finance revenue 48,210 is overstated because refunded orders are not excluded.",
    "disc-policy-exceptions": "req-02 is a violation: the approver role had expired.",
}


def test_planted_defect_marker_is_strictly_lower_than_clean_without_saturation():
    """decoy を finding として述べると score が下がり、どちらも飽和しない。

    旧実装は `marker["patterns"][0]` を本文として連結していたが、#562 の
    regex 化でパターン文字列は本文として意味を持たなくなった。意図
    (誤主張は減点される / 合成テキストは満点にならない) はそのまま、
    実際の文章で再表現する。
    """
    runner = _runner()
    for task in _data()["tasks"]:
        clean_text = _CLEAN_FINDING_TEXT[task["id"]]
        marker_name = task["planted_defect_marker"]
        defect = next(
            marker for marker in task["forbidden_markers"] if marker["name"] == marker_name
        )
        planted_text = f"{clean_text} {defect['patterns'][0]}."

        clean = runner.evaluate_quality_markers(clean_text, task)
        planted = runner.evaluate_quality_markers(planted_text, task)

        # 前提: clean テキストは実際に正しい発見を 1 件拾えている
        assert clean["quality_markers_matched"], task["id"]
        assert clean["forbidden_markers_matched"] == [], task["id"]
        # decoy を述べた側だけが減点される
        assert planted["forbidden_markers_matched"] == [marker_name], task["id"]
        assert planted["quality_marker_score"] < clean["quality_marker_score"], task["id"]
        # 合成テキストはごく一部の marker しか満たさないので満点にならない
        assert clean["quality_marker_score"] < 1.0, task["id"]
        assert planted["quality_marker_score"] < 1.0, task["id"]


def test_fail_first_tasks_present():
    """iter>=2 強制: fail_first task が 2 件以上、mission_max_iter >= 3."""
    fail_first = [t for t in _data()["tasks"] if t.get("fail_first") is True]
    assert len(fail_first) >= 2
    for task in fail_first:
        assert task["mission_max_iter"] >= 3, task["id"]


def test_fixtures_exist_and_referenced():
    """全 fixture が実在し、prompt から参照されている."""
    for task in _data()["tasks"]:
        assert task["fixtures"], task["id"]
        for rel in task["fixtures"]:
            path = REPO_ROOT / rel
            assert path.is_file(), f"{task['id']}: fixture missing {rel}"
            assert rel in task["prompt"], f"{task['id']}: fixture not in prompt {rel}"


def _subject_tokens(name: str) -> list:
    """marker 名から主題トークン (識別子・数値) を抜き出す。"""
    import re as _re

    tokens = _re.findall(r"[A-Za-z_][A-Za-z0-9_-]{3,}|[0-9][0-9,\.]{2,}", name.lower())
    return [t.rstrip(".,") for t in tokens]


def test_quality_marker_patterns_discoverable_in_fixtures():
    """marker の**主題**が fixture 本文に実在すること (答えの実在保証)。

    #562 で marker は regex 化され、判定語 (violation / diverge 等) は
    **解析者が書く語**であって fixture には無い。「パターンが fixture の
    部分文字列であること」を要求すると、fixture 丸写しで満点という
    #557 の元の欠陥に戻ってしまうため、主題の存在確認へ意味論を変更する。
    """
    generic = {"violation", "drift", "mismatch", "cause", "impact", "claim", "false"}
    for task in _data()["tasks"]:
        corpus = "".join(
            (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
            for rel in task["fixtures"]
        )
        for marker in task["quality_markers"]:
            tokens = [t for t in _subject_tokens(marker["name"]) if t not in generic]
            assert tokens, (
                f"{task['id']}: marker '{marker['name']}' に主題トークンが無い")
            assert any(t in corpus for t in tokens), (
                f"{task['id']}: marker '{marker['name']}' の主題が fixture に無い "
                f"(tokens={tokens})")


def test_no_trivially_short_patterns():
    """1 文字 pattern は偶然一致するため禁止."""
    for task in _data()["tasks"]:
        for group in (task["quality_markers"], task["forbidden_markers"]):
            for marker in group:
                for p in marker["patterns"]:
                    assert len(p) >= 2, f"{task['id']}: pattern too short: {p!r}"


def test_adoption_runbook_exists():
    """N>=10 採用判定 runbook がコマンドとゲート基準を含む."""
    runbook = BENCHMARK_DIR / "discriminating-cohort-runbook.ja.md"
    assert runbook.is_file()
    text = runbook.read_text(encoding="utf-8")
    assert "tasks.discriminating.json" in text
    assert "--repeats" in text
    assert "N≥10" in text or "N>=10" in text
    # 採用ゲート: 分散の解消と iter>=2 の観測を要求
    assert "marker_score_variance" in text
    assert "iteration" in text or "iter" in text


def test_audit_kpi_contract_is_documented_in_both_runbooks():
    for name in ("discriminating-cohort-runbook.md", "discriminating-cohort-runbook.ja.md"):
        text = (BENCHMARK_DIR / name).read_text(encoding="utf-8")
        assert "benchmark_kpi" in text
        assert "expected-gate" in text
        assert "blocked" in text
        assert "mission-planning-provider-kpi/1" in text
