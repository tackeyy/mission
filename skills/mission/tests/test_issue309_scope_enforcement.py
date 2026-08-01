"""#309 (F4): critic_has_new_scope の state 駆動強制.

実運用監査 (2026-08-01, 115 sessions) で critic_has_new_scope の設定が 0 件、
iter>=2 の全 7 sessions が None のまま進行し #240/#241 が一度も発火していなかった。
prose (SKILL.md #258) では実行されないため、next の guidance 層で機械的に強制する。

Contract under test:
1. phase=reviewing + iteration>=2 + critic_has_new_scope 未設定 (None)
   → next は run-reviewers ではなく record-critic-scope を返す
2. 設定済み false → run-reviewers + reviewer_count 削減 + context_mode=bounded (#240/#241)
3. 設定済み true → run-reviewers + full reviewer_count + context_mode=full
4. iteration 1 → 未設定でも run-reviewers (従来どおり要求しない)
5. pass gate 意味論は不変 (guidance 層のみの変更)
"""

import json
import importlib.util
from pathlib import Path


def _load_mission_state():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load_mission_state()


def _data(*, iteration=2, critic_has_new_scope="ABSENT", reviewer_count=3):
    d = {
        "mission": "test mission",
        "mission_id": "scope1234abcdef",
        "pid": 12345,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "phase": "reviewing",
        "iteration": iteration,
        "reviewer_count": reviewer_count,
    }
    if critic_has_new_scope != "ABSENT":
        d["critic_has_new_scope"] = critic_has_new_scope
    return d


def test_iter2_unset_scope_returns_record_critic_scope():
    """iter>=2 + 未設定 → record-critic-scope (run-reviewers を返さない)."""
    result = MS._derive_next_action(_data(iteration=2))
    assert result["next_action"] == "record-critic-scope"
    assert "critic_has_new_scope" in result["command_hint"]


def test_iter3_unset_scope_also_enforced():
    """iter=3 でも同様に強制される."""
    result = MS._derive_next_action(_data(iteration=3))
    assert result["next_action"] == "record-critic-scope"


def test_iter2_scope_false_returns_reviewers_bounded():
    """設定済み false → run-reviewers + 2名 + bounded (#240/#241 発火)."""
    result = MS._derive_next_action(_data(iteration=2, critic_has_new_scope=False))
    assert result["next_action"] == "run-reviewers"
    assert result["details"]["reviewer_count"] == 2
    assert result["details"]["context_mode"] == "bounded"


def test_iter2_scope_true_returns_reviewers_full():
    """設定済み true → run-reviewers + full 3名 + full context."""
    result = MS._derive_next_action(_data(iteration=2, critic_has_new_scope=True))
    assert result["next_action"] == "run-reviewers"
    assert result["details"]["reviewer_count"] == 3
    assert result["details"]["context_mode"] == "full"


def test_iter1_unset_scope_not_required():
    """iteration 1 は critic 前なので要求しない (従来どおり run-reviewers)."""
    result = MS._derive_next_action(_data(iteration=1))
    assert result["next_action"] == "run-reviewers"


def test_explicit_none_treated_as_unset():
    """critic_has_new_scope=null (明示 None) も未設定として強制対象."""
    result = MS._derive_next_action(_data(iteration=2, critic_has_new_scope=None))
    assert result["next_action"] == "record-critic-scope"
