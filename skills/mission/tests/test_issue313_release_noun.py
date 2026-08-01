"""#313 (F5): "release" キーワードの名詞参照 (文書名・版名) を suppress する.

実運用監査 (2026-08-01): エスカレータのシグナル 11 件中 4 件が "Release brief" /
"Release 6" 等の文書名・バージョン名への substring マッチによる過剰発火 (FP 36%)。
Standard mission が full tier (3名) へ誤昇格しレビューコスト増。

Contract under test:
1. 監査で実測した FP 4 パターン (数字 / brief / notes / Mission 参照) は suppress
2. 動詞的使用 ("release the hotfix" / "deploy and release") は従来どおり included
3. suppress は signal_details に監査記録され、tier は standard に留まる
4. release 以外のキーワード (deploy 等) には影響しない
"""

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load()


def _tier_and_signals(mission: str, complexity: str = "Standard"):
    return MS.derive_review_tier(mission, complexity)


# ===== 1. 監査 FP fixture: 名詞参照は suppress =====

def test_release_number_reference_suppressed():
    """'Release 6 scope contract' (company-os cc-521 実例) → エスカレートしない."""
    tier, signals = _tier_and_signals("Issue #521: rename the Release 6 scope contract key")
    assert tier == "standard"
    assert not any("release" in s for s in signals)


def test_release_brief_reference_suppressed():
    """'Release brief' (実例 2 件) → エスカレートしない."""
    tier, signals = _tier_and_signals("update docs referenced by the Release brief")
    assert tier == "standard"
    assert not any("release" in s for s in signals)


def test_release_notes_reference_suppressed():
    tier, signals = _tier_and_signals("sync the release notes section with the changelog")
    assert tier == "standard"
    assert not any("release" in s for s in signals)


def test_release_mission_reference_suppressed():
    """'Release Mission #582 Review' (実例) → エスカレートしない."""
    tier, signals = _tier_and_signals("Release Mission #582 Review evidence check")
    assert tier == "standard"
    assert not any("release" in s for s in signals)


# ===== 2. 動詞的使用は従来どおり included =====

def test_release_verb_still_escalates():
    tier, signals = _tier_and_signals("release the hotfix to customers")
    assert tier == "full"
    assert any("release" in s for s in signals)


def test_deploy_and_release_still_escalates():
    tier, signals = _tier_and_signals("deploy and release the new billing pipeline")
    assert tier == "full"


# ===== 3. suppress の監査記録 =====

def test_noun_reference_recorded_in_signal_details():
    decision = MS.derive_review_tier_decision("update the Release brief for Q3", "Standard")
    assert decision["tier"] == "standard"
    suppressed = [d for d in decision["signal_details"]
                  if d.get("decision") == "suppressed" and d.get("keyword") == "release"]
    assert suppressed, "名詞参照 suppress は signal_details に監査記録されるべき"
    assert suppressed[0]["reason"] == "noun-reference-non-operation"


# ===== 4. 他キーワードへの非影響 =====

def test_deploy_unaffected_by_noun_rule():
    tier, signals = _tier_and_signals("deploy 6 replicas to the cluster")
    assert tier == "full"
    assert any("deploy" in s for s in signals)
