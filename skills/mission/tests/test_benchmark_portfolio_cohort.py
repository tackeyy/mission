"""V1 portfolio cohort — routing 込みの実効オーバーヘッド測定用混合 cohort.

Contract under test:
1. tasks.portfolio.json は 8 tasks (Simple 3 / Standard 3 / Complex 2)
2. Simple 3 tasks は mission_complexity=Simple (adaptive routing #276 の発火対象)
3. answer key 自己隠蔽 + 再利用 fixture の answer key (tasks.discriminating.json) も隠蔽
4. 全 fixture が実在し prompt から参照されている
5. 全 quality_marker は fixture 本文から発見可能
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_PATH = REPO_ROOT / "benchmarks" / "mission-vs-goal" / "tasks.portfolio.json"


def _data() -> dict:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def test_portfolio_structure():
    data = _data()
    assert data["cohort"] == "portfolio"
    assert data["task_count"] == 8
    assert len(data["tasks"]) == 8
    counts = {}
    for t in data["tasks"]:
        counts[t["mission_complexity"]] = counts.get(t["mission_complexity"], 0) + 1
    assert counts == {"Simple": 3, "Standard": 3, "Complex": 2}


def test_answer_keys_hidden_including_reused_fixture_keys():
    hidden = _data()["hidden_paths"]
    assert "benchmarks/mission-vs-goal/tasks.portfolio.json" in hidden
    # 再利用 fixture の marker は discriminating の task 定義に載っているため両方隠す
    assert "benchmarks/mission-vs-goal/tasks.discriminating.json" in hidden


def test_fixtures_exist_and_referenced():
    for task in _data()["tasks"]:
        assert task["fixtures"], task["id"]
        for rel in task["fixtures"]:
            assert (REPO_ROOT / rel).is_file(), f"{task['id']}: missing {rel}"
            assert rel in task["prompt"], f"{task['id']}: {rel} not in prompt"


def _subject_tokens(name: str) -> list[str]:
    """marker 名から主題トークン (識別子・数値) を抜き出す。"""
    import re as _re

    tokens = _re.findall(r"[A-Za-z_][A-Za-z0-9_-]{3,}|[0-9][0-9,\.]{2,}", name.lower())
    return [t.rstrip(".,") for t in tokens]


def test_markers_discoverable_in_fixtures():
    """marker の**主題**が fixture 内に存在すること (タスクが解答可能であること)。

    #562 で marker は regex 化され、判定語 (violation / diverge 等) は
    **解析者が書く語**であって fixture には無い。したがって「パターン全体が
    fixture の部分文字列であること」はもはや要求できない。要求すると
    「fixture 丸写しで満点」という #562 が塞いだ欠陥に戻ってしまう。

    ここで検証したいのは「fixture に存在しないものを marker が要求していない」
    ことなので、marker 名の主題トークンが fixture に現れることを確認する。
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
                f"{task['id']}: marker '{marker['name']}' に主題トークンが無く、"
                "fixture 由来かを検証できない")
            assert any(t in corpus for t in tokens), (
                f"{task['id']}: marker '{marker['name']}' の主題が fixture に無い "
                f"(tokens={tokens})")


def test_simple_tasks_have_no_fail_first():
    for task in _data()["tasks"]:
        if task["mission_complexity"] == "Simple":
            assert not task.get("fail_first"), task["id"]
            assert task["mission_max_iter"] == 1, task["id"]
