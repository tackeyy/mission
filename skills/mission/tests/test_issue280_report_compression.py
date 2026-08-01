"""#280: artifact/最終報告の narrative 圧縮規律の構造テスト.

discriminating-v2 実測 (2026-07-23) で実行時間が総生成トークン量にほぼ比例した。
orchestrator 出力の主な水増し源は、最終報告/artifact への
レビュアー出力の逐語再掲と Plan/Execution 散文の再掲 (archive に保存済みの二重出力)。
本テストは圧縮規律が SKILL.md の報告フォーマット節に配線されていることを検証する。
"""
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"


def _report_section(txt: str) -> str:
    assert "## 報告フォーマット" in txt, "報告フォーマット節が SKILL.md にない"
    return txt.split("## 報告フォーマット", 1)[1]


def test_report_compression_discipline_documented():
    """報告フォーマット節に出力圧縮規律 (逐語再掲禁止 + archive 参照) がある."""
    txt = SKILL_MD.read_text()
    section = _report_section(txt)
    assert "逐語再掲" in section, "レビュアー出力の逐語再掲禁止が明記されていない"
    assert "archive" in section, "archive 参照パスでの代替が明記されていない"


def test_report_compression_scopes_artifact_too():
    """規律の対象が最終報告だけでなく artifact にも及ぶ."""
    txt = SKILL_MD.read_text()
    section = _report_section(txt)
    assert "artifact" in section.lower(), "artifact への適用が明記されていない"


def test_report_compression_keeps_evidence_gates():
    """圧縮はフォーマットのみで、evidence 全量保存と gate 値の提示は維持される."""
    txt = SKILL_MD.read_text()
    section = _report_section(txt)
    # tool-computed な gate 値の提示は維持 (捏造防止規律と両立)
    assert "ゲート値" in section or "gate" in section.lower(), (
        "tool-computed ゲート値の提示維持が明記されていない"
    )
    # evidence の全量は archive 側に残す (削るのは転記であって証跡ではない)
    assert "全量" in section, "archive への evidence 全量保存の維持が明記されていない"
