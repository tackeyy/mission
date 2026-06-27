import json
from pathlib import Path


def _json_result(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _passing_score_args():
    return (
        "push-score",
        "--iteration",
        "1",
        "--composite",
        "4.4",
        "--min-item",
        "4.0",
        "--items",
        '{"mission_achievement":4.5,"accuracy":4.5,"completeness":4.2,"usability":4.4,"reviewer_consensus":4.3}',
        "--open-high",
        "0",
    )


def test_artifact_init_append_render_and_export(state_dir, run_cli, read_state):
    root = state_dir.parent

    init = _json_result(run_cli(
        "artifact",
        "init",
        "--title",
        "Artifact Smoke",
        "--required-for-pass",
        "--json",
        cwd=root,
    ))
    artifact_path = root / init["artifact"]["path"]

    assert artifact_path.exists()
    assert init["artifact"]["required_for_pass"] is True
    assert init["artifact"]["status"] == "draft"

    append = _json_result(run_cli(
        "artifact",
        "append",
        "--section",
        "evidence",
        "--text",
        "pytest artifact smoke passed",
        "--label",
        "pytest",
        "--json",
        cwd=root,
    ))
    assert append["section"] == "evidence"

    rendered = _json_result(run_cli(
        "artifact",
        "render",
        "--redaction-status",
        "reviewed",
        "--json",
        cwd=root,
    ))
    text = artifact_path.read_text(encoding="utf-8")
    state = read_state(state_dir)

    assert rendered["path"] == ".mission-state/artifacts/test/mission-artifact.md"
    assert state["artifact"]["status"] == "rendered"
    assert state["artifact"]["redaction_status"] == "reviewed"
    assert "## Evidence" in text
    assert "pytest artifact smoke passed" in text
    assert "redaction_status: reviewed" in text

    exported = _json_result(run_cli(
        "artifact",
        "export",
        "--to",
        "docs/generated-artifact-smoke.md",
        "--redaction-status",
        "reviewed",
        "--json",
        cwd=root,
    ))
    export_path = root / "docs" / "generated-artifact-smoke.md"

    assert export_path.exists()
    assert exported["export"]["path"] == "docs/generated-artifact-smoke.md"
    assert read_state(state_dir)["artifact"]["status"] == "exported"


def test_artifact_required_for_pass_blocks_until_rendered(state_dir, run_cli, read_state):
    root = state_dir.parent
    run_cli(
        "artifact",
        "init",
        "--required-for-pass",
        "--json",
        cwd=root,
        check=True,
    )
    run_cli(*_passing_score_args(), cwd=root, check=True)

    blocked = run_cli("mark-passes", cwd=root)
    assert blocked.returncode == 2
    assert "artifact is required" in blocked.stderr

    run_cli("artifact", "render", "--redaction-status", "not-needed", cwd=root, check=True)
    passed = run_cli("mark-passes", cwd=root)

    assert passed.returncode == 0, passed.stderr
    state = read_state(state_dir)
    assert state["passes"] is True
    assert state["loop_active"] is False


def test_artifact_publish_requires_explicit_consent(state_dir, run_cli, read_state):
    root = state_dir.parent
    run_cli("artifact", "init", "--json", cwd=root, check=True)
    run_cli("artifact", "render", "--redaction-status", "reviewed", cwd=root, check=True)

    blocked = run_cli("artifact", "publish", "--provider", "claude-code", cwd=root)
    assert blocked.returncode == 2
    assert "requires --require-confirm and --approval-text" in blocked.stderr

    prepared = _json_result(run_cli(
        "artifact",
        "publish",
        "--provider",
        "claude-code",
        "--require-confirm",
        "--approval-text",
        "user approved artifact publish preparation",
        "--json",
        cwd=root,
    ))

    event = prepared["publish_event"]
    assert event["provider"] == "claude-code"
    assert event["status"] == "publish-prepared"
    assert event["artifact_path"] == ".mission-state/artifacts/test/mission-artifact.md"
    assert read_state(state_dir)["artifact"]["publish_events"][0]["approval_text"] == (
        "user approved artifact publish preparation"
    )
    assert "status: publish-prepared" in (
        root / ".mission-state" / "artifacts" / "test" / "mission-artifact.md"
    ).read_text(encoding="utf-8")
