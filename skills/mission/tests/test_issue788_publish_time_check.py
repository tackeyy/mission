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


def test_a_refusal_releases_every_descriptor_it_opened(tmp_path):
    """A refusal leaves nothing open.

    Cross-model review round 2: the descriptors were released only after the
    body ran, so a refusal raised while opening kept the project root and the
    repository open.  The rule is checked by repetition -- a leak of two per
    call shows up long before the process limit does.
    """
    from mission_persistence.evidence_publish_path import (
        EvidencePublishPathError,
        open_evidence_publish_directory,
    )

    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "docs").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    def open_descriptors() -> int:
        return len(os.listdir("/dev/fd"))

    before = open_descriptors()
    for _ in range(64):
        with pytest.raises(EvidencePublishPathError):
            with open_evidence_publish_directory(tmp_path, "docs/out.md"):
                pass
    assert open_descriptors() <= before + 1


def test_a_dot_component_is_refused_before_the_path_is_parsed(tmp_path):
    """``PurePosixPath`` drops ``.``, so the written form is what is checked.

    Cross-model review round 2: checking the parsed parts accepted
    ``docs/./out.md``, because the parser had already removed the component
    this walk refuses.
    """
    from mission_persistence.evidence_publish_path import (
        EvidencePublishPathError,
        open_evidence_publish_directory,
    )

    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "docs").mkdir()

    # The library is called directly: the CLI route canonicalises the path
    # first, so the parser would have removed the component before it arrived.
    with pytest.raises(EvidencePublishPathError):
        with open_evidence_publish_directory(tmp_path, "docs/./out.md"):
            pass
    assert not (tmp_path / "docs" / "out.md").exists()


def test_a_repository_that_takes_the_parents_name_is_refused_by_identity(tmp_path, monkeypatch):
    """Only the identity comparison can refuse this one.

    The component is spelled ``docs``, so neither the name check nor
    ``O_NOFOLLOW`` applies: the directory simply *is* the repository by the
    time it is opened.  Cross-model review round 2 showed this is
    constructible, which the design had said it was not.
    """
    from mission_persistence import evidence_publish_path as library

    (tmp_path / ".mission-state").mkdir()
    (tmp_path / "docs").mkdir()
    original_open = library.os.open

    def open_then_move_the_repository(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        if path == ".mission-state":
            # After the repository is pinned, it takes the parent's name.  The
            # component is then spelled ``docs``, so neither the name check nor
            # O_NOFOLLOW applies: only the identity comparison can refuse it.
            (tmp_path / "docs").rmdir()
            (tmp_path / ".mission-state").rename(tmp_path / "docs")
        return opened

    monkeypatch.setattr(library.os, "open", open_then_move_the_repository)
    with pytest.raises(library.EvidencePublishPathError):
        with library.open_evidence_publish_directory(tmp_path, "docs/out.md"):
            pass
    monkeypatch.undo()
    assert not (tmp_path / "docs" / "out.md").exists()


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
