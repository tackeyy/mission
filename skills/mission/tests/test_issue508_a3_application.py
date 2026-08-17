from __future__ import annotations

import copy
from contextlib import contextmanager
import ast
import importlib.util
import os
from pathlib import Path
import sys

import pytest

from mission_application.artifact import (
    EvidenceFailure,
    artifact_append,
    artifact_export,
    artifact_init,
    artifact_publish,
    artifact_render,
    context_manifest,
    make_evidence_effect,
    progress_clear,
    progress_update,
)
from mission_persistence.legacy_v4 import LegacyV4Repository


NOW = "2026-08-17T09:00:00Z"
ARTIFACT_PATH = ".mission-state/artifacts/test/mission-artifact.md"


def _state() -> dict:
    return {
        "schema_version": 4,
        "session_id": "test",
        "mission_id": "a" * 16,
        "mission": "A3 extraction",
        "iteration": 1,
        "phase": "executing",
        "score_history": [],
    }


def _render(state: dict, artifact: dict) -> bytes:
    return (
        f"# {artifact['title']}\nstatus={artifact['status']}\n"
        f"blocks={len(artifact.get('blocks', []))}\n"
    ).encode()


def _state_module():
    script = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("state_issue508_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a3_application_module_has_no_filesystem_dependency_or_write_call():
    source = Path(sys.modules[artifact_init.__module__].__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in (
            ([alias.name.split(".")[0] for alias in node.names])
            if isinstance(node, ast.Import)
            else ([(node.module or "").split(".")[0]])
        )
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert not ({"os", "pathlib", "shutil", "tempfile"} & imported)
    assert not ({"write_text", "write_bytes", "mkdir", "unlink", "replace"} & calls)


def test_artifact_use_case_is_pure_and_returns_immutable_bound_effect():
    original = _state()
    result = artifact_init(
        original,
        now=NOW,
        artifact_path=ARTIFACT_PATH,
        format="markdown",
        title="Evidence",
        redaction_status="unchecked",
        required_for_pass=True,
        render=_render,
    )

    assert original == _state()
    assert result.state["artifact_applicability"] == "producing"
    assert result.state["artifact"]["path"] == ARTIFACT_PATH
    assert result.state["artifact"]["digest"] == result.effects[0].digest.removeprefix("sha256:")
    assert result.effects[0].content == b"# Evidence\nstatus=draft\nblocks=0\n"
    assert isinstance(result.effects, tuple)
    assert set(result.result) == {"artifact"}


def test_invalid_artifact_request_returns_no_effect_and_does_not_mutate_input():
    original = _state()
    before = copy.deepcopy(original)

    with pytest.raises(EvidenceFailure, match="artifact-redaction-invalid"):
        artifact_init(
            original,
            now=NOW,
            artifact_path=ARTIFACT_PATH,
            format="markdown",
            title="Evidence",
            redaction_status="forged",
            required_for_pass=False,
            render=_render,
        )

    assert original == before


def test_artifact_append_render_export_and_publish_keep_consent_only_contract():
    initialized = artifact_init(
        _state(), now=NOW, artifact_path=ARTIFACT_PATH, format="markdown",
        title="Evidence", redaction_status="unchecked", required_for_pass=False,
        render=_render,
    ).state
    appended = artifact_append(
        initialized, now=NOW, section="evidence", content="green\n",
        source=None, label="pytest",
    )
    assert appended.effects == ()
    rendered = artifact_render(
        appended.state, now=NOW, redaction_status="reviewed", render=_render,
    )
    exported = artifact_export(
        rendered.state, now=NOW, destination="reports/evidence.md",
        redaction_status="reviewed", render=_render,
    )
    assert [effect.target for effect in exported.effects] == [
        ARTIFACT_PATH, "reports/evidence.md",
    ]
    assert exported.effects[0].content == exported.effects[1].content
    published = artifact_publish(
        exported.state, now=NOW, provider="local", destination=None,
        approval_text="approved", render=_render,
    )
    event = published.result["publish_event"]
    assert event["status"] == "publish-prepared"
    assert "remote_send" not in published.result
    assert not ({"phase", "passes", "score_history", "reviews"} & set(published.result))


def test_evidence_effect_rejects_mutable_bytes_and_digest_tampering():
    with pytest.raises(EvidenceFailure, match="effect-content-invalid"):
        make_evidence_effect("evidence", "out.json", bytearray(b"x"))
    for target in ("/tmp/out.json", "../out.json", "a/../../out.json"):
        with pytest.raises(EvidenceFailure, match="effect-target-invalid"):
            make_evidence_effect("evidence", target, b"x")
    effect = make_evidence_effect("evidence", "out.json", b"x")
    forged = effect.__class__(
        kind=effect.kind, target=effect.target, content=effect.content,
        digest="sha256:" + "0" * 64, size=effect.size,
    )
    with pytest.raises(ValueError, match="effect-binding-invalid"):
        LegacyV4Repository.validate_effects((forged,))


def test_repository_loads_and_checks_lease_before_starting_effect_transaction():
    calls: list[str] = []

    def rejected_load():
        calls.append("load")
        raise ValueError("foreign-lease")

    @contextmanager
    def effects(_requests):
        calls.append("effects")
        yield []

    repository = LegacyV4Repository(
        lock=lambda: _recording_context(calls, "lock"),
        read_state=rejected_load,
        write_state=lambda _state: calls.append("save"),
        backup_state=lambda: calls.append("backup"),
    )

    with pytest.raises(ValueError, match="foreign-lease"):
        repository.execute_effects(
            lambda state: artifact_init(
                state, now=NOW, artifact_path=ARTIFACT_PATH, format="markdown",
                title="Evidence", redaction_status="unchecked",
                required_for_pass=False, render=_render,
            ),
            effect_transaction=effects,
        )

    assert calls == ["lock-enter", "load", "lock-exit"]


def test_repository_rolls_back_all_effects_when_state_save_fails():
    published: dict[str, bytes] = {"first": b"old"}
    calls: list[str] = []

    @contextmanager
    def effects(requests):
        before = dict(published)
        try:
            for request in requests:
                published[request.target] = request.content
            calls.append("effects-published")
            yield list(requests)
        except BaseException:
            published.clear()
            published.update(before)
            calls.append("effects-rolled-back")
            raise

    repository = LegacyV4Repository(
        lock=lambda: _recording_context(calls, "lock"),
        read_state=_state,
        write_state=lambda _state: (_ for _ in ()).throw(RuntimeError("save-failed")),
        backup_state=lambda: calls.append("backup"),
    )

    def decide(state):
        return artifact_export(
            artifact_init(
                state, now=NOW, artifact_path="first", format="markdown",
                title="Evidence", redaction_status="reviewed",
                required_for_pass=False, render=_render,
            ).state,
            now=NOW, destination="second", redaction_status="reviewed", render=_render,
        )

    with pytest.raises(RuntimeError, match="save-failed"):
        repository.execute_effects(decide, effect_transaction=effects)

    assert published == {"first": b"old"}
    assert "effects-rolled-back" in calls


def test_real_v4_adapter_rolls_back_artifact_when_state_save_fails(
    state_dir, monkeypatch
):
    module = _state_module()
    root = state_dir.parent
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    artifact_path = root / ARTIFACT_PATH

    def fail_state_write(*_args, **_kwargs):
        raise RuntimeError("state-save-failed")

    monkeypatch.setattr(module, "atomic_write_json", fail_state_write)

    with pytest.raises(RuntimeError, match="state-save-failed"):
        module.cmd_artifact_init(type("Args", (), {
            "format": "markdown",
            "title": "Evidence",
            "redaction_status": "unchecked",
            "required_for_pass": False,
            "json": True,
        })())

    assert not artifact_path.exists()


@pytest.mark.parametrize("replacement", ["symlink", "fifo", "hardlink"])
def test_real_v4_effect_publisher_rejects_link_and_fifo_targets(
    tmp_path, replacement
):
    module = _state_module()
    target = tmp_path / "evidence.bin"
    source = tmp_path / "source.bin"
    source.write_bytes(b"private")
    if replacement == "symlink":
        target.symlink_to(source)
    elif replacement == "fifo":
        os.mkfifo(target)
    else:
        os.link(source, target)
    effect = make_evidence_effect("evidence", target.name, b"public")

    with pytest.raises(ValueError):
        with module._publish_evidence_effects(tmp_path, (effect,)):
            pass

    assert source.read_bytes() == b"private"


@pytest.mark.parametrize("artifact_path", ["/tmp/outside.md", "../outside.md"])
def test_artifact_render_rejects_non_relative_persisted_identity(artifact_path):
    state = _state()
    state["artifact"] = {
        "status": "draft",
        "format": "markdown",
        "title": "Evidence",
        "path": artifact_path,
        "exports": [],
        "publish_events": [],
        "redaction_status": "reviewed",
        "required_for_pass": False,
        "blocks": [],
        "created_at": NOW,
        "updated_at": NOW,
    }

    with pytest.raises(EvidenceFailure, match="effect-target-invalid"):
        artifact_render(state, now=NOW, redaction_status=None, render=_render)


def test_progress_update_and_clear_only_change_progress_observation():
    state = _state()
    updated = progress_update(
        state, now=NOW, total=7, completed=3, batch_size=2,
        last_unit="u3", artifact_path="reports/progress.md", iteration=1,
        evidence_path=".mission-state/archive/progress.md",
    )
    assert updated.state["progress"]["remaining"] == 4
    assert updated.effects[0].kind == "progress"
    assert b"completed: 3" in updated.effects[0].content
    for field in ("phase", "score_history"):
        assert updated.state[field] == state[field]
    cleared = progress_clear(updated.state, now=NOW)
    assert "progress" not in cleared.state
    assert cleared.effects == ()


def test_context_manifest_is_bound_evidence_without_authority_mutation():
    state = _state()
    state["score_history"] = [
        {"findings_summary": [{"id": "f1", "severity": "Medium"}]}
    ]
    result = context_manifest(
        state, now=NOW, iteration=1, output_path=".mission-state/context.json",
    )

    assert result.effects[0].kind == "context-manifest"
    assert result.result["findings_count"] == 1
    assert result.state["context_manifests"]["1"]["digest"] == result.effects[0].digest
    for field in ("phase", "score_history"):
        assert result.state[field] == state[field]


@contextmanager
def _recording_context(calls: list[str], name: str):
    calls.append(f"{name}-enter")
    try:
        yield None
    finally:
        calls.append(f"{name}-exit")
