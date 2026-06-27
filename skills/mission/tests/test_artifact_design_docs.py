from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_mission_artifact_design_docs_are_linked_from_public_docs():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_ja = (REPO_ROOT / "README.ja.md").read_text(encoding="utf-8")
    loop_doc = (REPO_ROOT / "docs" / "LOOP_ENGINEERING.md").read_text(encoding="utf-8")

    assert "docs/MISSION_ARTIFACTS.md" in readme
    assert "docs/MISSION_ARTIFACTS.ja.md" in readme_ja
    assert "docs/MISSION_ARTIFACTS.md" in loop_doc


def test_mission_artifact_design_is_explicit_about_implemented_local_scope():
    design = (REPO_ROOT / "docs" / "MISSION_ARTIFACTS.md").read_text(encoding="utf-8")
    design_ja = (REPO_ROOT / "docs" / "MISSION_ARTIFACTS.ja.md").read_text(encoding="utf-8")

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Artifact support" in readme
    assert "implemented as a local Markdown artifact" in readme
    assert "implemented local artifact contract" in design
    assert "remote Claude Code Artifact URL" in design
    assert "local artifact contract は実装済み" in design_ja
    assert "remote Claude Code Artifact URL を黙って作成するものではありません" in design_ja


def test_mission_artifact_design_uses_claude_code_best_practices_without_lock_in():
    design = (REPO_ROOT / "docs" / "MISSION_ARTIFACTS.md").read_text(encoding="utf-8")
    design_ja = (REPO_ROOT / "docs" / "MISSION_ARTIFACTS.ja.md").read_text(encoding="utf-8")

    assert "https://code.claude.com/docs/en/artifacts" in design
    assert "https://code.claude.com/docs/en/goal" in design
    assert "local-first" in design
    assert "Do not make Claude Code Artifacts a hard runtime dependency" in design
    assert "Do not publish anything remotely without explicit user approval" in design
    assert ".mission-state/artifacts/<session_id>/mission-artifact.md" in design
    assert "redaction_status" in design
    assert "artifact init --required-for-pass" in design
    assert "mark-passes` refuses missing or unrendered artifacts" in design

    assert "https://code.claude.com/docs/ja/artifacts" in design_ja
    assert "https://code.claude.com/docs/ja/goal" in design_ja
    assert "local-first" in design_ja
    assert "Claude Code hosting を必須依存にはしません" in design_ja
    assert "明示許可なしに remote publish しない" in design_ja
    assert ".mission-state/artifacts/<session_id>/mission-artifact.md" in design_ja


def test_mission_artifact_design_links_smoke_evidence_without_paired_claim():
    design = (REPO_ROOT / "docs" / "MISSION_ARTIFACTS.md").read_text(encoding="utf-8")
    loop_doc = (REPO_ROOT / "docs" / "LOOP_ENGINEERING.md").read_text(encoding="utf-8")
    smoke_path = (
        REPO_ROOT
        / "benchmarks"
        / "mission-vs-goal"
        / "results"
        / "2026-06-28-mission-artifact-required-smoke.json"
    )

    assert smoke_path.exists()
    assert "2026-06-28-mission-artifact-required-smoke.json" in design
    assert "not a paired `/goal` comparison" in design
    assert "not a paired `/goal`" in loop_doc
