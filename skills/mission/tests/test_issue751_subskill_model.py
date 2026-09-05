"""#751: reviewer / planner / critic の subskill は役割別の model を frontmatter で宣言する.

Claude Code 2.1.251 以降のモデル解決順は
per-call model > skill frontmatter の model > CLAUDE_CODE_SUBAGENT_MODEL > 親モデル。
frontmatter が無いと env の既定（本環境では Sonnet 5）で判定してしまう。
"""
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_DIR.parent.parent
SKILLS = REPO_ROOT / "skills"

VALID_ALIASES = {"opus", "fable", "sonnet", "haiku"}
# reviewer / checker 系は opus 以上（owner 決定 2026-09-05）
STRONG_ALIASES = {"opus", "fable"}


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    body = text.split("---\n", 2)[1]
    out = {}
    for line in body.splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


@pytest.mark.parametrize("name", ["mission-reviewer", "mission-planner", "mission-critic"])
def test_forked_subskill_declares_strong_model(name):
    fm = _frontmatter(SKILLS / name / "SKILL.md")
    assert fm.get("context") == "fork", f"{name}: expected context: fork"
    assert "model" in fm, f"{name}: frontmatter lacks model (falls back to CLAUDE_CODE_SUBAGENT_MODEL)"
    assert fm["model"] in STRONG_ALIASES, f"{name}: model must be one of {sorted(STRONG_ALIASES)}, got {fm['model']!r}"


@pytest.mark.parametrize("name", ["mission-executor", "mission-scorer"])
def test_other_forked_subskill_model_is_valid_alias_if_present(name):
    fm = _frontmatter(SKILLS / name / "SKILL.md")
    if "model" in fm:
        assert fm["model"] in VALID_ALIASES, f"{name}: unknown model alias {fm['model']!r}"


def test_orchestrator_documents_model_resolution_order():
    txt = (SKILL_DIR / "SKILL.md").read_text()
    assert "CLAUDE_CODE_SUBAGENT_MODEL" in txt, "SKILL.md must name the env fallback"
    assert "frontmatter" in txt and "model" in txt
    assert "省略時" in txt or "省略" in txt, "SKILL.md must state what happens when model is omitted"
