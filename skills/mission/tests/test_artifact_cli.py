import importlib.util
import argparse
import json
from pathlib import Path
import sys

import pytest


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


def _state_module():
    script = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("state_artifact_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _artifact_path(root):
    return root / ".mission-state" / "artifacts" / "test" / "mission-artifact.md"


def _export_path(root):
    return root / "docs" / "generated-artifact-smoke.md"


def _set_foreign_lease(state_dir):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "owner_session_id": "foreign",
        "lease_id": "foreign-lease",
        "fencing_epoch": 7,
        "lease_expires_at": "2099-01-01T00:00:00Z",
    })
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_path


def _forbid_artifact_write(module, monkeypatch):
    calls = []

    def fail_publish_output(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("artifact publication must not start before lease validation")

    monkeypatch.setattr(module, "_publish_output_transaction", fail_publish_output)
    return calls


def test_artifact_init_rejects_foreign_lease_without_creating_file(state_dir, run_cli, read_state):
    root = state_dir.parent
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()
    artifact_path = _artifact_path(root)

    result = run_cli(
        "artifact",
        "init",
        "--title",
        "Artifact Smoke",
        "--required-for-pass",
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "lease" in result.stderr.lower()
    assert not artifact_path.exists()
    assert state_path.read_bytes() == state_before
    assert read_state(state_dir) == json.loads(state_before)


def test_artifact_render_rejects_foreign_lease_without_mutating_artifact_file(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()

    result = run_cli(
        "artifact",
        "render",
        "--redaction-status",
        "reviewed",
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "lease" in result.stderr.lower()
    assert artifact_path.read_bytes() == artifact_before
    assert state_path.read_bytes() == state_before
    assert read_state(state_dir)["artifact"]["status"] == "draft"


def test_artifact_export_rejects_foreign_lease_without_mutating_artifact_file(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    assert run_cli("artifact", "render", "--json", cwd=root).returncode == 0
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    export_path = _export_path(root)
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()

    result = run_cli(
        "artifact",
        "export",
        "--to",
        "docs/generated-artifact-smoke.md",
        "--redaction-status",
        "reviewed",
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "lease" in result.stderr.lower()
    assert artifact_path.read_bytes() == artifact_before
    assert not export_path.exists()
    assert state_path.read_bytes() == state_before


def test_artifact_append_rejects_foreign_lease_without_mutating_state(
    state_dir, run_cli
):
    root = state_dir.parent
    assert run_cli("artifact", "init", "--json", cwd=root).returncode == 0
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()

    result = run_cli(
        "artifact",
        "append",
        "--section",
        "evidence",
        "--text",
        "must not be appended",
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "lease" in result.stderr.lower()
    assert state_path.read_bytes() == state_before


def test_artifact_init_does_not_publish_before_foreign_lease_rejection(state_dir, monkeypatch, capsys):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()
    artifact_path = _artifact_path(root)
    calls = _forbid_artifact_write(module, monkeypatch)

    with pytest.raises(module.CommandOutcomeExit) as excinfo:
        module.cmd_artifact_init(argparse.Namespace(
            format="markdown",
            title="Artifact Smoke",
            required_for_pass=True,
            redaction_status="unchecked",
            json=True,
        ))

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "lease" in captured.err.lower()
    assert calls == []
    assert not artifact_path.exists()
    assert state_path.read_bytes() == state_before


def test_artifact_render_does_not_publish_before_foreign_lease_rejection(state_dir, monkeypatch, capsys):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    assert module.cmd_artifact_init(argparse.Namespace(
        format="markdown",
        title="Artifact Smoke",
        required_for_pass=True,
        redaction_status="unchecked",
        json=True,
    )) is None
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()
    calls = _forbid_artifact_write(module, monkeypatch)

    with pytest.raises(module.CommandOutcomeExit) as excinfo:
        module.cmd_artifact_render(argparse.Namespace(
            redaction_status="reviewed",
            json=True,
        ))

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "lease" in captured.err.lower()
    assert calls == []
    assert artifact_path.read_bytes() == artifact_before
    assert state_path.read_bytes() == state_before


def test_artifact_export_does_not_publish_before_foreign_lease_rejection(state_dir, monkeypatch, capsys):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    assert module.cmd_artifact_init(argparse.Namespace(
        format="markdown",
        title="Artifact Smoke",
        required_for_pass=True,
        redaction_status="unchecked",
        json=True,
    )) is None
    assert module.cmd_artifact_render(argparse.Namespace(
        redaction_status="reviewed",
        json=True,
    )) is None
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    export_path = _export_path(root)
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()
    calls = _forbid_artifact_write(module, monkeypatch)

    with pytest.raises(module.CommandOutcomeExit) as excinfo:
        module.cmd_artifact_export(argparse.Namespace(
            to="docs/generated-artifact-smoke.md",
            redaction_status="reviewed",
            json=True,
        ))

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "lease" in captured.err.lower()
    assert calls == []
    assert artifact_path.read_bytes() == artifact_before
    assert not export_path.exists()
    assert state_path.read_bytes() == state_before


def test_artifact_publish_does_not_publish_before_foreign_lease_rejection(state_dir, monkeypatch, capsys):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    assert module.cmd_artifact_init(argparse.Namespace(
        format="markdown",
        title="Artifact Smoke",
        required_for_pass=True,
        redaction_status="unchecked",
        json=True,
    )) is None
    assert module.cmd_artifact_render(argparse.Namespace(
        redaction_status="reviewed",
        json=True,
    )) is None
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()
    calls = _forbid_artifact_write(module, monkeypatch)

    with pytest.raises(module.CommandOutcomeExit) as excinfo:
        module.cmd_artifact_publish(argparse.Namespace(
            provider="claude-code",
            destination=None,
            require_confirm=True,
            approval_text="user approved artifact publish preparation",
            json=True,
        ))

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "lease" in captured.err.lower()
    assert calls == []
    assert artifact_path.read_bytes() == artifact_before
    assert state_path.read_bytes() == state_before


def test_artifact_init_rolls_back_when_identity_refresh_fails(state_dir, tmp_path, monkeypatch):
    module = _state_module()
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    artifact_path = _artifact_path(state_dir.parent)

    def fail_refresh(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "_bind_artifact_publication", fail_refresh)

    with pytest.raises(RuntimeError, match="boom"):
        module.cmd_artifact_init(argparse.Namespace(
            format="markdown",
            title="Artifact Smoke",
            required_for_pass=True,
            redaction_status="unchecked",
            json=True,
        ))

    assert not artifact_path.exists()


def test_artifact_render_rolls_back_when_identity_refresh_fails(state_dir, monkeypatch):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    assert module.cmd_artifact_init(argparse.Namespace(
        format="markdown",
        title="Artifact Smoke",
        required_for_pass=True,
        redaction_status="unchecked",
        json=True,
    )) is None
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()

    def fail_refresh(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "_bind_artifact_publication", fail_refresh)

    with pytest.raises(RuntimeError, match="boom"):
        module.cmd_artifact_render(argparse.Namespace(
            redaction_status="reviewed",
            json=True,
        ))

    assert artifact_path.read_bytes() == artifact_before


def test_artifact_export_rolls_back_when_identity_refresh_fails(state_dir, monkeypatch):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    assert module.cmd_artifact_init(argparse.Namespace(
        format="markdown",
        title="Artifact Smoke",
        required_for_pass=True,
        redaction_status="unchecked",
        json=True,
    )) is None
    assert module.cmd_artifact_render(argparse.Namespace(
        redaction_status="reviewed",
        json=True,
    )) is None
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    export_path = _export_path(root)

    def fail_refresh(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "_bind_artifact_publication", fail_refresh)

    with pytest.raises(RuntimeError, match="boom"):
        module.cmd_artifact_export(argparse.Namespace(
            to="docs/generated-artifact-smoke.md",
            redaction_status="reviewed",
            json=True,
        ))

    assert artifact_path.read_bytes() == artifact_before
    assert not export_path.exists()


@pytest.mark.parametrize("fail_on", (1, 2))
def test_artifact_export_rolls_back_when_either_effect_publish_fails(
    state_dir, monkeypatch, fail_on
):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    module.cmd_artifact_init(argparse.Namespace(
        format="markdown", title="Artifact Smoke", required_for_pass=True,
        redaction_status="unchecked", json=True,
    ))
    module.cmd_artifact_render(argparse.Namespace(
        redaction_status="reviewed", json=True,
    ))
    artifact_path = _artifact_path(root)
    export_path = _export_path(root)
    state_path = state_dir / "sessions" / "test.json"
    artifact_before = artifact_path.read_bytes()
    state_before = state_path.read_bytes()
    original_publish = module._publish_output_transaction
    calls = 0

    def fail_selected_publish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise RuntimeError("publish-failed")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(module, "_publish_output_transaction", fail_selected_publish)

    with pytest.raises(RuntimeError, match="publish-failed"):
        module.cmd_artifact_export(argparse.Namespace(
            to="docs/generated-artifact-smoke.md",
            redaction_status="reviewed",
            json=True,
        ))

    assert artifact_path.read_bytes() == artifact_before
    assert not export_path.exists()
    assert state_path.read_bytes() == state_before


def test_artifact_export_rolls_back_both_effects_when_state_save_fails(
    state_dir, monkeypatch
):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    module.cmd_artifact_init(argparse.Namespace(
        format="markdown", title="Artifact Smoke", required_for_pass=True,
        redaction_status="unchecked", json=True,
    ))
    module.cmd_artifact_render(argparse.Namespace(
        redaction_status="reviewed", json=True,
    ))
    artifact_path = _artifact_path(root)
    export_path = _export_path(root)
    state_path = state_dir / "sessions" / "test.json"
    artifact_before = artifact_path.read_bytes()
    state_before = state_path.read_bytes()

    monkeypatch.setattr(
        module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("state-save-failed")
        ),
    )

    with pytest.raises(RuntimeError, match="state-save-failed"):
        module.cmd_artifact_export(argparse.Namespace(
            to="docs/generated-artifact-smoke.md",
            redaction_status="reviewed",
            json=True,
        ))

    assert artifact_path.read_bytes() == artifact_before
    assert not export_path.exists()
    assert state_path.read_bytes() == state_before


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


def test_artifact_required_for_pass_blocks_until_rendered(state_dir, run_cli, read_state, push_provenance_score):
    root = state_dir.parent
    run_cli(
        "artifact",
        "init",
        "--required-for-pass",
        "--json",
        cwd=root,
        check=True,
    )
    push_provenance_score(root)

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


def test_artifact_publish_rejects_foreign_lease_without_mutating_artifact_file(
    state_dir, run_cli, read_state
):
    root = state_dir.parent
    run_cli("artifact", "init", "--json", cwd=root, check=True)
    run_cli("artifact", "render", "--redaction-status", "reviewed", cwd=root, check=True)
    artifact_path = _artifact_path(root)
    artifact_before = artifact_path.read_bytes()
    state_path = _set_foreign_lease(state_dir)
    state_before = state_path.read_bytes()

    result = run_cli(
        "artifact",
        "publish",
        "--provider",
        "claude-code",
        "--require-confirm",
        "--approval-text",
        "user approved artifact publish preparation",
        "--json",
        cwd=root,
    )

    assert result.returncode == 2
    assert "lease" in result.stderr.lower()
    assert artifact_path.read_bytes() == artifact_before
    assert state_path.read_bytes() == state_before
    assert read_state(state_dir)["artifact"]["status"] == "rendered"
