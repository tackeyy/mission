"""Regression guard for the copied Codex plugin wrapper.

The canonical implementation lives in the repository root `skills/` and
`scripts/` trees. `plugins/mission/` is a copied marketplace wrapper, so CI must
fail if a root change is not synced into the wrapper.
"""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER_ROOT = REPO_ROOT / "plugins" / "mission"


def _is_excluded_skill_file(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT / "skills")
    parts = rel.parts
    return (
        "__pycache__" in parts
        or ".pytest_cache" in parts
        or parts[:2] == ("mission", "tests")
        or rel == Path("mission/pytest.ini")
    )


def _is_excluded_script_file(path: Path) -> bool:
    return path.name == "sync-codex-plugin-wrapper.sh"


def _relative_files(root: Path, exclude) -> list[Path]:
    return sorted(
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not exclude(path)
    )


def test_codex_wrapper_skills_match_canonical_tree():
    root = REPO_ROOT / "skills"
    wrapper = WRAPPER_ROOT / "skills"

    expected = _relative_files(root, _is_excluded_skill_file)
    actual = _relative_files(wrapper, lambda p: False)

    assert actual == expected
    for rel in expected:
        assert (wrapper / rel).read_bytes() == (root / rel).read_bytes(), rel


def test_codex_wrapper_scripts_match_canonical_tree():
    root = REPO_ROOT / "scripts"
    wrapper = WRAPPER_ROOT / "scripts"

    expected = _relative_files(root, _is_excluded_script_file)
    actual = _relative_files(wrapper, lambda p: False)

    assert actual == expected
    for rel in expected:
        assert (wrapper / rel).read_bytes() == (root / rel).read_bytes(), rel
