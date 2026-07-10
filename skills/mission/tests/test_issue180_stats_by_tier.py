"""#180: stats に by_review_tier / iteration_by_review_tier 内訳を追加.

fixture 流儀は test_issue6_stats_breakdown.py に準拠:
  - _state() で最小限の state dict を生成
  - _aggregate() を直接呼んで JSON 出力キーを確認

カバー範囲:
  - review_tier あり (light / full 混在): by_review_tier の total/pass/halt 件数
  - review_tier なし (フィールド欠損): "unknown" キーに落ちる後方互換
  - iteration_by_review_tier の形式: {tier: {iter_key: count}}
  - 空 states: by_review_tier={} / iteration_by_review_tier={}
  - _format_text に by_review_tier / iteration_by_review_tier セクションが出る
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "mission_state", Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _state(review_tier, passes, iteration, halt_reason="", composite=4.2, loop_active=False):
    """最小 state dict。review_tier=None でフィールド欠損を再現する。"""
    s = {
        "project_root": "/project/alpha",
        "complexity": "Standard",
        "agent": "claude-code",
        "passes": passes,
        "loop_active": loop_active,
        "halt_reason": halt_reason,
        "iteration": iteration,
        "score_history": [{"composite": composite, "min_item": 4.0}] if composite else [],
        "mission": "test mission",
        "mission_id": "m-001",
        "session_id": "s-001",
        "started_at": "2026-07-10T00:00:00",
    }
    if review_tier is not None:
        s["review_tier"] = review_tier
    return s


# ============================================================
# by_review_tier テスト
# ============================================================


def test_aggregate_by_review_tier_counts():
    """light 2件 (1 pass / 1 halt) / full 1件 (1 pass) が by_review_tier に集計される。"""
    states = [
        _state("light", True, 1),
        _state("light", False, 0, halt_reason="blocked"),
        _state("full", True, 2),
    ]
    agg = ms._aggregate(states)
    brt = agg["by_review_tier"]

    assert "light" in brt, "light キーが存在する"
    assert brt["light"]["total"] == 2
    assert brt["light"]["pass"] == 1
    assert brt["light"]["halt"] == 1
    assert brt["light"]["incomplete"] == 0
    assert brt["light"]["abandoned"] == 0

    assert "full" in brt
    assert brt["full"]["total"] == 1
    assert brt["full"]["pass"] == 1


def test_aggregate_by_review_tier_missing_field_goes_to_unknown():
    """review_tier フィールドが無い旧 state は "unknown" に集計される (後方互換)。"""
    states = [
        _state(None, True, 1),   # フィールドなし
        _state(None, False, 0, halt_reason="err"),  # フィールドなし
        _state("standard", True, 1),
    ]
    agg = ms._aggregate(states)
    brt = agg["by_review_tier"]

    assert "unknown" in brt, "review_tier なし → unknown キー"
    assert brt["unknown"]["total"] == 2
    assert brt["unknown"]["pass"] == 1
    assert brt["unknown"]["halt"] == 1

    assert "standard" in brt
    assert brt["standard"]["total"] == 1


def test_aggregate_empty_states_has_by_review_tier_key():
    """states=[] でも by_review_tier キーが存在し空 dict を返す。"""
    agg = ms._aggregate([])
    assert "by_review_tier" in agg
    assert agg["by_review_tier"] == {}


# ============================================================
# iteration_by_review_tier テスト
# ============================================================


def test_aggregate_iteration_by_review_tier_histogram():
    """tier ごとに iteration ヒストグラムが作られる。
    iteration_histogram と同じバケット規則 (1/2/3 → そのまま、4+ 以上 → '4+')。
    """
    states = [
        _state("light", True, 1),
        _state("light", True, 1),
        _state("light", False, 3, halt_reason="x"),
        _state("full", True, 2),
        _state("full", True, 5),   # 4+ バケット
    ]
    agg = ms._aggregate(states)
    ibrt = agg["iteration_by_review_tier"]

    # light: iter=1 が 2件, iter=3 が 1件
    assert ibrt["light"]["1"] == 2
    assert ibrt["light"]["3"] == 1
    assert "2" not in ibrt["light"]

    # full: iter=2 が 1件, iter=5 → '4+' が 1件
    assert ibrt["full"]["2"] == 1
    assert ibrt["full"]["4+"] == 1


def test_aggregate_empty_states_has_iteration_by_review_tier_key():
    """states=[] でも iteration_by_review_tier キーが存在し空 dict を返す。"""
    agg = ms._aggregate([])
    assert "iteration_by_review_tier" in agg
    assert agg["iteration_by_review_tier"] == {}


def test_iteration_by_review_tier_unknown_bucket_for_missing_tier():
    """review_tier なし state の iteration も 'unknown' バケットに入る。"""
    states = [
        _state(None, True, 2),
    ]
    agg = ms._aggregate(states)
    ibrt = agg["iteration_by_review_tier"]
    assert "unknown" in ibrt
    assert ibrt["unknown"]["2"] == 1


# ============================================================
# _format_text に新セクションが出るか
# ============================================================


def test_format_text_renders_by_review_tier():
    """_format_text の出力に by_review_tier: セクションが含まれる。"""
    states = [
        _state("light", True, 1),
        _state("full", False, 2, halt_reason="err"),
    ]
    agg = ms._aggregate(states)
    txt = ms._format_text(agg, None, None)
    assert "by_review_tier:" in txt
    assert "light" in txt
    assert "full" in txt


def test_format_text_renders_iteration_by_review_tier():
    """_format_text の出力に iteration_by_review_tier: セクションが含まれる。"""
    states = [
        _state("light", True, 1),
        _state("full", True, 2),
    ]
    agg = ms._aggregate(states)
    txt = ms._format_text(agg, None, None)
    assert "iteration_by_review_tier:" in txt


# ============================================================
# 既存キーへの影響なし (回帰)
# ============================================================


def test_existing_keys_not_removed():
    """by_review_tier / iteration_by_review_tier 追加後も既存キーが残る。"""
    states = [
        _state("standard", True, 1),
        _state("light", False, 0, halt_reason="x"),
    ]
    agg = ms._aggregate(states)
    for key in ("by_project", "by_complexity", "iteration_histogram",
                "by_agent", "total_sessions", "pass_count", "halt_count"):
        assert key in agg, f"既存キー {key!r} が消えていない"
