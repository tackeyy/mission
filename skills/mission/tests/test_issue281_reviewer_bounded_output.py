"""#281: mission-reviewer 出力契約の bounded 化の構造テスト.

discriminating-v2 実測 (2026-07-23) で reviewer 側の出力だけで 5K-32K トークン/run。
aggregate-reviews が strict 検証するのは mission-review/1 JSON のみであり、
再導出の網羅的な散文レポートは品質ゲートに寄与しない二重出力。
独立再導出そのもの (Maker-Checker) は不変で、出力フォーマットのみを bounded 化する。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
REVIEWER_MD = REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md"


def _read() -> str:
    return REVIEWER_MD.read_text()


def test_reviewer_output_bounds_documented():
    """出力境界 (#281): 返却を採点テーブル + Issues + JSON に限定する規律がある."""
    txt = _read()
    assert "出力境界" in txt, "出力境界セクションがない"
    assert "散文" in txt, "網羅的な散文レポートの禁止が明記されていない"


def test_reviewer_rederivation_stays_internal():
    """再導出は内部で行い、全過程の書き出しはしない (独立性は不変)."""
    txt = _read()
    assert "再導出" in txt, "再導出の内部化が明記されていない"
    # 独立性の既存規律が残っていること
    assert "独立性" in txt


def test_reviewer_evidence_contract_unchanged():
    """High/Medium finding の evidence 非空必須・verbatim 引用の既存契約は不変."""
    txt = _read()
    assert "evidence` 非空必須" in txt or "evidence 非空必須" in txt
    assert "verbatim" in txt
    assert "mission-review/1" in txt


def test_reviewer_output_template_still_has_json():
    """アウトプット形式に fenced JSON の契約が残っている."""
    txt = _read()
    assert '"schema": "mission-review/1"' in txt
