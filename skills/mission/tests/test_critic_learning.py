"""Static distribution contract for recurrence-aware critic guidance (#375)."""
from pathlib import Path


def test_recurring_rule_requires_concrete_critic_action():
    text = (Path(__file__).resolve().parents[2] / "mission-critic" / "SKILL.md").read_text(encoding="utf-8")
    assert "failure_ledger" in text
    assert "recurrence_count > 0" in text
    assert "既存のGeneral Fix Ruleがなぜ防止できなかったか" in text
    assert "具体的な改訂action" in text
    assert "汎用 `set`" in text
