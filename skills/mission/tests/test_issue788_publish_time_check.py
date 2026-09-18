"""#788: evidence export pins an external parent without following aliases."""

from __future__ import annotations

import sys
import contextlib
import hashlib
import importlib.util
import os
import stat
from pathlib import Path

import pytest


LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _state_module():
    script = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue788_state", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _effect(path: str, content: bytes = b"# generated\n"):
    from mission_application.artifact import EvidenceEffect

    return EvidenceEffect(
        kind="artifact",
        target=path,
        content=content,
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _publish(module, root: Path, path: str):
    with module._publish_evidence_effects(root, (_effect(path),)):
        pass


def test_external_projection_refuses_a_symlinked_first_component(tmp_path):
    module = _state_module()
    root = tmp_path
    repository = root / ".mission-state"
    repository.mkdir()
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (root / "docs").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ValueError):
        _publish(module, root, "docs/out.md")
    assert not (elsewhere / "out.md").exists()


@pytest.mark.parametrize("target", (".mission-state", "docs/out"))
def test_external_projection_refuses_a_symlinked_parent_to_repository_or_elsewhere(tmp_path, target):
    module = _state_module()
    repository = tmp_path / ".mission-state"
    repository.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "docs").symlink_to(tmp_path / target, target_is_directory=True)

    with pytest.raises(ValueError):
        _publish(module, tmp_path, "docs/out.md")
    assert not (repository / "out.md").exists()
    assert not (elsewhere / "out.md").exists()


@pytest.mark.parametrize("target", (".mission-state", "elsewhere"))
def test_publication_rechecks_after_initial_path_validation_before_opening(tmp_path, monkeypatch, target):
    module = _state_module()
    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "elsewhere").mkdir()
    original = module.open_publication_directory

    @contextlib.contextmanager
    def replace_then_open(root, path, resolve_named):
        # The swap happens after the CLI's name check and before anything is
        # opened, which is the only window the descriptor route has to close.
        (root / "docs").rmdir()
        (root / "docs").symlink_to(root / target, target_is_directory=True)
        with original(root, path, resolve_named) as destination:
            yield destination

    monkeypatch.setattr(module, "open_publication_directory", replace_then_open)
    with pytest.raises(ValueError):
        _publish(module, tmp_path, "docs/out.md")
    assert not (tmp_path / target / "out.md").exists()


@pytest.mark.parametrize("target", ("../elsewhere", "../.mission-state"))
def test_external_projection_refuses_a_symlinked_intermediate_component(tmp_path, target):
    module = _state_module()
    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "elsewhere").mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "out").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError):
        _publish(module, tmp_path, "docs/out/result.md")
    assert not (tmp_path / "elsewhere" / "result.md").exists()
    assert not (tmp_path / ".mission-state" / "result.md").exists()


@pytest.mark.parametrize("path", ("docs/out.md", "a/b/c/d/e/f.md", "out.md"))
def test_external_projection_pins_existing_new_and_project_root_parents(tmp_path, path):
    module = _state_module()
    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "docs").mkdir()
    _publish(module, tmp_path, path)
    assert (tmp_path / path).read_bytes() == b"# generated\n"


def test_external_projection_directory_uses_the_process_umask(tmp_path):
    module = _state_module()
    (tmp_path / ".mission-state").mkdir()
    original_umask = os.umask(0)
    os.umask(original_umask)
    try:
        _publish(module, tmp_path, "docs/deep/out.md")
        expected_mode = 0o777 & ~original_umask
        assert stat.S_IMODE((tmp_path / "docs").stat().st_mode) == expected_mode
        assert stat.S_IMODE((tmp_path / "docs" / "deep").stat().st_mode) == expected_mode
    finally:
        os.umask(original_umask)


def test_external_projection_refuses_a_parent_escape(tmp_path):
    from mission_persistence.evidence_publish_path import EvidencePublishPathError, open_evidence_publish_directory

    (tmp_path / ".mission-state").mkdir()
    with pytest.raises(EvidencePublishPathError):
        with open_evidence_publish_directory(tmp_path, "../outside/out.md"):
            pytest.fail("the helper must enforce its own project-root boundary")


def test_external_projection_refuses_the_replaced_current_repository(tmp_path, monkeypatch):
    from mission_persistence.evidence_publish_path import EvidencePublishPathError, open_evidence_publish_directory
    import mission_persistence.evidence_publish_path as module

    repository = tmp_path / ".mission-state"
    repository.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    original_open = module.os.open
    replaced = False

    def replace_before_component(name, *args, **kwargs):
        nonlocal replaced
        if name == "docs" and not replaced:
            repository.rename(tmp_path / "old-state")
            replacement.rename(repository)
            replaced = True
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", replace_before_component)
    with pytest.raises(EvidencePublishPathError):
        with open_evidence_publish_directory(tmp_path, ".MISSION-STATE/docs/out.md"):
            pytest.fail("the current repository name must be refused before opening")


def test_dotdot_spelling_of_repository_is_refused_by_opened_identity(tmp_path):
    from mission_persistence.evidence_publish_path import (
        EvidencePublishPathError,
        open_evidence_publish_directory,
    )

    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "docs").mkdir()
    with pytest.raises(EvidencePublishPathError):
        with open_evidence_publish_directory(tmp_path, "docs/../.mission-state/out.md"):
            pytest.fail("the repository identity must be refused")


def test_case_alias_of_repository_is_refused_when_the_filesystem_has_one(tmp_path):
    from mission_persistence.evidence_publish_path import (
        EvidencePublishPathError,
        open_evidence_publish_directory,
    )

    (tmp_path / ".mission-state").mkdir()
    if not (tmp_path / ".MISSION-STATE").exists():
        pytest.skip("case-sensitive filesystem")
    with pytest.raises(EvidencePublishPathError):
        with open_evidence_publish_directory(tmp_path, ".MISSION-STATE/out.md"):
            pytest.fail("case alias must resolve to the repository identity")


def test_external_effect_uses_descriptor_resolution_while_in_root_effects_keep_legacy_route(
    tmp_path, monkeypatch
):
    module = _state_module()
    (tmp_path / ".mission-state" / "archive").mkdir(parents=True)
    external_calls = []
    internal_calls = []

    original = module.open_publication_directory

    @contextlib.contextmanager
    def record(root, path, resolve_named):
        # The route is chosen inside the library, so the record is taken from
        # the descriptor it yields: an in-root destination carries none.
        with original(root, path, resolve_named) as destination:
            if destination.directory_fd is None:
                internal_calls.append(path)
            else:
                external_calls.append(path)
            yield destination

    monkeypatch.setattr(module, "open_publication_directory", record)
    _publish(module, tmp_path, "docs/out.md")
    _publish(module, tmp_path, ".mission-state/archive/iter-1-abc12345-progress.md")
    _publish(module, tmp_path, ".mission-state/artifacts/test/mission-artifact.md")

    assert external_calls == ["docs/out.md"]
    assert internal_calls == [
        ".mission-state/archive/iter-1-abc12345-progress.md",
        ".mission-state/artifacts/test/mission-artifact.md",
    ]
    assert (tmp_path / "docs" / "out.md").exists()
    assert (tmp_path / ".mission-state" / "archive" / "iter-1-abc12345-progress.md").exists()
    assert (tmp_path / ".mission-state" / "artifacts" / "test" / "mission-artifact.md").exists()
