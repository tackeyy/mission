"""#670: evidence CLI commands use the v5 lifecycle repository."""

import json


def _v5_env(tmp_path):
    """v5 state 生成用: MISSION_* を絞り、version-skew 警告も抑制する。"""
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
    }


def _init_v5(run_cli, tmp_path, *, mission="v5 evidence mission"):
    env = _v5_env(tmp_path)
    run_cli(
        "init",
        mission,
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
        check=True,
    )
    head = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text()
    )
    assert head["schema"] == "mission-head/1"
    return env


def test_v5_context_manifest_publishes_and_records_manifest(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path)
    result = run_cli(
        "context-manifest",
        "--iteration",
        "1",
        "--out",
        "reports/context.json",
        cwd=tmp_path,
        env_extra=env,
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    manifest = json.loads((tmp_path / "reports" / "context.json").read_text())
    assert manifest["schema"] == "mission-context-manifest/1"


def test_v5_artifact_and_progress_commands_publish_and_update_state(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path)
    commands = (
        ("artifact", "init", "--title", "Evidence", "--json"),
        (
            "artifact",
            "append",
            "--section",
            "evidence",
            "--text",
            "v5 regression evidence",
            "--json",
        ),
        ("artifact", "render", "--redaction-status", "reviewed", "--json"),
        (
            "artifact",
            "export",
            "--to",
            "reports/artifact.md",
            "--redaction-status",
            "reviewed",
            "--json",
        ),
        (
            "artifact",
            "publish",
            "--provider",
            "local",
            "--require-confirm",
            "--approval-text",
            "approved for regression test",
            "--json",
        ),
        ("progress", "update", "--total", "2", "--completed", "1", "--json"),
        ("progress", "clear", "--json"),
    )

    for command in commands:
        result = run_cli(*command, cwd=tmp_path, env_extra=env)
        assert result.returncode == 0, (
            f"{' '.join(command)} failed\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    assert (tmp_path / "reports" / "artifact.md").exists()
