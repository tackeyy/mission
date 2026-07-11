"""明示的な release 指示を不可逆操作の事前承認として扱う契約を固定する。"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_SKILLS = (
    REPO_ROOT / "skills" / "mission" / "SKILL.md",
    REPO_ROOT / "skills" / "mission-executor" / "SKILL.md",
)
PLUGIN_SKILLS = (
    REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "SKILL.md",
    REPO_ROOT / "plugins" / "mission" / "skills" / "mission-executor" / "SKILL.md",
)


def test_explicit_release_request_is_advance_authorization():
    """対象と操作が一致する明示指示では、release直前の再確認を要求しない。"""
    required = (
        "事前承認として扱う",
        "実行直前に同じ確認を繰り返さない",
        "対象・scope・rollback",
    )

    for path in CANONICAL_SKILLS:
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in required if marker not in text]
        assert not missing, f"{path} is missing release authorization markers: {missing}"


def test_material_authorization_changes_still_require_confirmation():
    """事前承認をscope拡大や未承認の破壊的操作へ流用しない。"""
    mission_text = CANONICAL_SKILLS[0].read_text(encoding="utf-8")
    safety_markers = (
        "scope の拡大",
        "rollback 条件の変更",
        "未承認のDB削除",
        "force push",
        "高額課金",
        "差分を示して再確認する",
    )

    missing = [marker for marker in safety_markers if marker not in mission_text]
    assert not missing, f"mission rule is missing re-confirmation boundaries: {missing}"


def test_release_authorization_rule_is_packaged_without_drift():
    """正典と配布pluginのmission/executorルールを完全一致させる。"""
    for canonical, packaged in zip(CANONICAL_SKILLS, PLUGIN_SKILLS, strict=True):
        assert canonical.read_bytes() == packaged.read_bytes(), (
            f"release authorization rule is not packaged: {canonical} != {packaged}"
        )
