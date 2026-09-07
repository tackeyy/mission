"""#747: a projection cannot reach the repository through another spelling.

``_projection_target`` refused paths whose first segment equalled the
repository directory's name.  A name is not the directory it opens: on a
filesystem that ignores case, the repository answers to spellings that are not
its own, and a projection written through one of them lands inside the
repository -- over ``commits`` or ``objects`` -- having passed every string
comparison.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_persistence.fenced_commit import (  # noqa: E402
    FencedCommitError,
    LocalFencedRepository,
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
