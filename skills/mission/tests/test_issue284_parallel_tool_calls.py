"""#284: 独立 tool call の同一メッセージ並列発行規律.

mission run のターン数 (52-56, goal 比 6x) には fixture/対象ファイルの逐次 Read や
state 照合の逐次 Bash が含まれる。相互依存のない呼び出しを 1 メッセージに
まとめる規律を Compact Instructions に置き、compaction 後も生存させる。
"""
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"


def _compact_instructions(txt: str) -> str:
    return txt.split("## state.json 操作", 1)[0]


def test_parallel_tool_call_discipline_in_compact_instructions():
    """並列発行規律が Compact Instructions (compaction 耐性セクション) にある."""
    compact = _compact_instructions(SKILL_MD.read_text())
    assert "並列発行" in compact, "独立 tool call の並列発行規律が Compact Instructions にない"
    assert "単一メッセージ" in compact, "単一メッセージでの発行が明記されていない"


def test_parallel_tool_call_discipline_keeps_dependency_order():
    """依存関係のある操作 (書き込み→再取得) は順次のままであることを明記する."""
    compact = _compact_instructions(SKILL_MD.read_text())
    assert "依存" in compact and "順次" in compact, (
        "依存操作の順次実行の但し書きがない (並列化の過剰適用を防ぐ)"
    )
