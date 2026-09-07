"""#747: a projection cannot reach the repository through another spelling.

``_projection_target`` refused paths whose first segment equalled the
repository directory's name.  A name is not the directory it opens: on a
filesystem that ignores case, the repository answers to spellings that are not
its own, and a projection written through one of them lands inside the
repository -- over ``commits`` or ``objects`` -- having passed every string
comparison.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_persistence.fenced_commit import (  # noqa: E402
    FencedCommitError,
    LocalFencedRepository,
    ProjectionFileRef,
    ProjectionRecord,
)

ROOT_NAME = ".mission-state"
ALIAS = ".MISSION-STATE"


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "project" / ROOT_NAME
    root.mkdir(parents=True)
    return LocalFencedRepository(root)


def _ignores_case(root: Path) -> bool:
    return (root.parent / ALIAS).exists()


def test_the_refusal_is_about_identity_not_spelling():
    """The unit the other tests reach only where the filesystem cooperates.

    A second name for one directory needs either a filesystem that ignores
    case or a privilege this suite does not have, so the decision itself is
    exercised here, where every platform can run it.
    """
    from mission_persistence.fenced_commit import _refuse_repository_alias

    root_identity = (1, 2, 0o040700)
    same = os.stat_result((0o040700, 2, 1, 1, 0, 0, 0, 0, 0, 0))
    other = os.stat_result((0o040700, 3, 1, 1, 0, 0, 0, 0, 0, 0))

    _refuse_repository_alias(other, root_identity)  # a different directory

    with pytest.raises(FencedCommitError) as refusal:
        _refuse_repository_alias(same, root_identity)
    assert refusal.value.code == "projection-invalid"


def _report_as_the_repository(monkeypatch, repository, disguised: Path):
    """Make one directory answer with the repository's identity, and no other.

    Replacing the identity of everything would pass whatever metadata the
    caller happened to hand over, so a resolver that compared the wrong
    directory would still look correct.  Only ``disguised`` is changed.
    """
    import mission_persistence.fenced_commit as module

    real = module._directory_identity
    root_identity = real(repository.root.lstat())
    disguised_identity = real(disguised.lstat())

    def _identity(metadata):
        found = real(metadata)
        return root_identity if found == disguised_identity else found

    monkeypatch.setattr(module, "_directory_identity", _identity)


def test_the_resolver_refuses_a_parent_that_is_the_repository(
    repository, monkeypatch
):
    """Runs anywhere: the second name is arranged rather than found."""
    outside = repository.root.parent / "outside"
    outside.mkdir()
    (repository.root.parent / "elsewhere").mkdir()
    _report_as_the_repository(monkeypatch, repository, outside)

    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target("outside/x.md")

    assert refusal.value.code == "projection-invalid"
    # And a directory that is not the repository still resolves, so this
    # says the refusal is about the directory rather than about refusing.
    assert repository._projection_target("elsewhere/x.md") == (
        repository.root.parent / "elsewhere" / "x.md"
    )


def test_the_resolver_refuses_a_target_that_is_the_repository(
    repository, monkeypatch
):
    """The single-segment path has no parent to catch it."""
    outside = repository.root.parent / "outside"
    outside.mkdir()
    (repository.root.parent / "elsewhere").mkdir()
    _report_as_the_repository(monkeypatch, repository, outside)

    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target("outside")

    assert refusal.value.code == "projection-invalid"
    assert repository._projection_target("elsewhere") == (
        repository.root.parent / "elsewhere"
    )


def test_the_pinned_resolver_refuses_the_same_directory(repository, monkeypatch):
    """The pinned path decides separately, so it is checked separately."""
    outside = repository.root.parent / "outside"
    outside.mkdir()
    record = ProjectionRecord(
        after=ProjectionFileRef(
            digest="sha256:" + "0" * 64,
            identity=(0, 0, 0, 0, 0),
            name="after.blob",
            size=0,
        ),
        base=None,
        blob_id="b" * 32,
        parent_identity=(0, 0, 0),
        relative_path="outside/x.md",
    )
    elsewhere = repository.root.parent / "elsewhere"
    elsewhere.mkdir()
    _report_as_the_repository(monkeypatch, repository, outside)

    with pytest.raises(FencedCommitError) as refusal:
        with repository._pinned_projection_target(record):
            pass

    assert refusal.value.code == "projection-invalid"

    # The same shape against a directory that is not the repository gets past
    # this decision; whatever stops it later is a different check.
    other = replace(record, relative_path="elsewhere/x.md")
    try:
        with repository._pinned_projection_target(other):
            pass
    except FencedCommitError as later:
        assert later.code != "projection-invalid"


def test_the_resolver_refuses_the_repository_at_any_depth(repository, monkeypatch):
    """Checking only the first segment would leave every deeper one open."""
    nested = repository.root.parent / "a" / "b" / "outside"
    nested.mkdir(parents=True)
    (repository.root.parent / "a" / "b" / "elsewhere").mkdir()
    _report_as_the_repository(monkeypatch, repository, nested)

    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target("a/b/outside/x.md")

    assert refusal.value.code == "projection-invalid"
    assert repository._projection_target("a/b/elsewhere/x.md") == (
        repository.root.parent / "a" / "b" / "elsewhere" / "x.md"
    )


def test_the_resolver_refuses_a_deep_target_that_is_the_repository(
    repository, monkeypatch
):
    """The target is the last segment wherever the path leads."""
    nested = repository.root.parent / "a" / "b" / "outside"
    nested.mkdir(parents=True)
    _report_as_the_repository(monkeypatch, repository, nested)

    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target("a/b/outside")

    assert refusal.value.code == "projection-invalid"


def test_the_pinned_resolver_refuses_the_repository_at_any_depth(
    repository, monkeypatch
):
    """The pinned walk opens one directory per segment; each one is asked."""
    nested = repository.root.parent / "a" / "b" / "outside"
    nested.mkdir(parents=True)
    record = ProjectionRecord(
        after=ProjectionFileRef(
            digest="sha256:" + "0" * 64,
            identity=(0, 0, 0, 0, 0),
            name="after.blob",
            size=0,
        ),
        base=None,
        blob_id="b" * 32,
        parent_identity=(0, 0, 0),
        relative_path="a/b/outside/x.md",
    )
    _report_as_the_repository(monkeypatch, repository, nested)

    with pytest.raises(FencedCommitError) as refusal:
        with repository._pinned_projection_target(record):
            pass

    assert refusal.value.code == "projection-invalid"


def test_the_refusal_looks_at_the_device_as_well_as_the_inode():
    """An inode number means nothing without the device that issued it."""
    from mission_persistence.fenced_commit import _refuse_repository_alias

    root_identity = (1, 2, 0o040700)
    other_device = os.stat_result((0o040700, 2, 9, 1, 0, 0, 0, 0, 0, 0))

    _refuse_repository_alias(other_device, root_identity)


@pytest.mark.parametrize(
    "relative_path",
    [
        f"{ALIAS}/commits/x.md",
        f"{ALIAS}/objects/x.md",
        f"{ALIAS}/x.md",
        ALIAS,
    ],
)
def test_a_spelling_that_opens_the_repository_is_refused(repository, relative_path):
    """The check is on the directory the path opens, not on how it is written."""
    if not _ignores_case(repository.root):
        pytest.skip("this filesystem distinguishes the two spellings")

    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target(relative_path)

    assert refusal.value.code == "projection-invalid"


def test_the_repositorys_own_name_is_still_refused(repository):
    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target(f"{ROOT_NAME}/x.md")

    assert refusal.value.code == "projection-invalid"


@pytest.mark.parametrize(
    "relative_path",
    ["docs/x.md", "published/x.md", f"sub/{ALIAS}/x.md"],
)
def test_a_path_outside_the_repository_still_resolves(repository, relative_path):
    """Only the repository itself is refused, not everything that looks alike.

    The third case matters: the same spelling deeper in the tree names a
    directory that has nothing to do with the repository.
    """
    target = repository._projection_target(relative_path)

    assert target == repository.root.parent.joinpath(*relative_path.split("/"))


def test_a_filesystem_that_distinguishes_the_spellings_is_unaffected(repository):
    """Where the two names are two directories, the alias is an ordinary path.

    Refusing it there would break a projection that is outside the repository
    by every measure, and would strand any prepare already holding one.
    """
    if _ignores_case(repository.root):
        pytest.skip("this filesystem treats the two spellings as one directory")

    target = repository._projection_target(f"{ALIAS}/archive/x.md")

    assert target == repository.root.parent / ALIAS / "archive" / "x.md"


def test_a_directory_that_is_the_repository_by_link_is_refused(repository, tmp_path):
    """A hard link to the repository is the repository, whatever it is called."""
    twin = repository.root.parent / "twin"
    try:
        twin.hardlink_to(repository.root)
    except OSError:
        pytest.skip("this filesystem does not allow hard links to directories")

    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target("twin/x.md")

    assert refusal.value.code == "projection-invalid"
