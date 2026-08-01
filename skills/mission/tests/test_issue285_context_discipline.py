"""#285: per-turn context 削減の規律.

mission run の per-turn cache read は ~107K (goal ~38K)。cache 済みでも TTFT は
context 長に依存するため、毎ターン context に入る量を減らす規律を置く:
state 全文 echo の禁止 (`get --field` 使用)、review JSON の再読・転記禁止、
refs の lazy-load。あわせて SKILL.md 本体の行数に regression guard を張る。
"""
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"


def _compact_instructions(txt: str) -> str:
    return txt.split("## state.json 操作", 1)[0]


def test_context_discipline_in_compact_instructions():
    """context 規律が Compact Instructions にある."""
    compact = _compact_instructions(SKILL_MD.read_text())
    assert "context 規律" in compact, "context 規律の項目がない"
    assert "get --field" in compact, "state 全文でなく get --field を使う指示がない"
    assert "再読" in compact, "大型ファイル・review JSON の再読禁止がない"


def test_skillmd_line_budget_tightened():
    """#285: SKILL.md は 260 行未満を維持する (#125 の 300 行制限より厳しい regression guard)."""
    n = len(SKILL_MD.read_text().splitlines())
    assert n < 260, f"SKILL.md is {n} lines (target: < 260, per-turn context 削減 #285)"


def test_bootstrap_fail_closed_contract_survives_compression():
    """圧縮後も bootstrap の fail-closed 契約キーワードが残る (test_local_authoring_sync と二重ガード)."""
    txt = SKILL_MD.read_text()
    section = txt.split("## Local authoring source bootstrap", 1)[1].split("## Compact Instructions", 1)[0]
    for kw in ("mission-local-authoring-sync.sh", "fail-closed", "fallback", "読み直"):
        assert kw in section, f"bootstrap 契約キーワード {kw!r} が圧縮で失われた"
