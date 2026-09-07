"""#747 P1: where a projection may be written is decided once.

Three places asked the same question -- the publication contract and the two
path resolvers -- and each wrote its own answer.  They agreed, but agreement
between copies is a fact about today, not a property.  These fix what the
extraction must not change: the same input reaches the same verdict at each
of the three, with the wording and the exception type each of them always had.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_application.evidence_publication import (  # noqa: E402
    EvidencePublicationError,
    canonical_publication_path,
)
from mission_kernel.projection_path import (  # noqa: E402
    ProjectionRejection,
    resolve_projection_path,
)
from mission_persistence.fenced_commit import (  # noqa: E402
    FencedCommitError,
    LocalFencedRepository,
    ProjectionFileRef,
    ProjectionRecord,
)

ROOT_NAME = ".mission-state"


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "project" / ROOT_NAME
    root.mkdir(parents=True)
    return LocalFencedRepository(root)


def _record(relative_path):
    return ProjectionRecord(
        after=ProjectionFileRef(
            digest="sha256:" + "0" * 64,
            identity=(0, 0, 0, 0, 0),
            name="after.blob",
            size=0,
        ),
        base=None,
        blob_id="b" * 32,
        parent_identity=(0, 0, 0),
        relative_path=relative_path,
    )


# What the parts alone decide.  Anything the parts cannot answer -- whether
# the input was a string at all -- is left to the caller and appears below.
@pytest.mark.parametrize(
    "relative_path,expected",
    [
        ("docs/x.md", ("docs", "x.md")),
        ("published/x.md", ("published", "x.md")),
        ("x.md", ("x.md",)),
        ("sub/.mission-state/x.md", ("sub", ".mission-state", "x.md")),
        (".MISSION-STATE/x.md", (".MISSION-STATE", "x.md")),
        # ``PurePosixPath`` folds these away before the parts are read.
        ("build//x", ("build", "x")),
        ("a/./b", ("a", "b")),
        ("x/", ("x",)),
        ("/abs/x.md", "absolute"),
        ("", "empty-or-relative-segment"),
        (".", "empty-or-relative-segment"),
        ("..", "empty-or-relative-segment"),
        ("a/../x.md", "empty-or-relative-segment"),
        (".mission-state/x.md", "inside-root"),
        (".mission-state", "inside-root"),
        (".mission-state/published/x.md", "inside-root"),
    ],
)
def test_the_parts_decide_the_same_way_for_every_caller(relative_path, expected):
    resolved = resolve_projection_path(
        PurePosixPath(relative_path), root_name=ROOT_NAME
    )

    if isinstance(expected, tuple):
        assert resolved == expected
    else:
        assert isinstance(resolved, ProjectionRejection)
        assert resolved.reason == expected


@pytest.mark.parametrize(
    "relative_path,detail",
    [
        ("/abs/x.md", "publication path must be relative"),
        (".", "publication path has an empty or relative segment"),
        ("..", "publication path has an empty or relative segment"),
        ("a/../x.md", "publication path has an empty or relative segment"),
    ],
)
def test_the_publication_contract_keeps_its_own_wording(relative_path, detail):
    """One decision, three vocabularies: this caller explains itself."""
    with pytest.raises(EvidencePublicationError) as refusal:
        canonical_publication_path(relative_path, repository_root_name=ROOT_NAME)

    assert refusal.value.code == "publication-path-invalid"
    assert refusal.value.detail == detail


def test_the_publication_contract_still_names_the_way_out():
    """The message a runbook reads when its path stops working."""
    with pytest.raises(EvidencePublicationError) as refusal:
        canonical_publication_path(
            f"{ROOT_NAME}/published/x.md", repository_root_name=ROOT_NAME
        )

    assert refusal.value.detail == (
        "publication path is inside the repository root (.mission-state/); "
        "evidence is published as a projection of the repository, so choose "
        "a path outside it, for example published/x.md"
    )


@pytest.mark.parametrize("relative_path", ["", 123, None, b"bytes"])
def test_a_non_string_stays_the_publication_contracts_own_refusal(relative_path):
    """The parts cannot say this, so the resolver is not asked.

    Folding it in would change which exception the unit of work raises: there
    a non-string reaches ``PurePosixPath`` and comes back as ``TypeError``.
    """
    with pytest.raises(EvidencePublicationError) as refusal:
        canonical_publication_path(relative_path, repository_root_name=ROOT_NAME)

    assert refusal.value.detail == "publication path must be a non-empty string"


@pytest.mark.parametrize("relative_path", [123, None, b"bytes"])
def test_the_unit_of_work_still_lets_a_non_string_raise(repository, relative_path):
    """The other half of the same asymmetry, held from the other side."""
    with pytest.raises(TypeError):
        repository._projection_target(relative_path)


@pytest.mark.parametrize(
    "relative_path",
    ["/abs/x.md", "", ".", "..", "a/../x.md", f"{ROOT_NAME}/x.md", ROOT_NAME],
)
def test_the_unit_of_work_answers_with_one_refusal(repository, relative_path):
    """Its callers are not people, so every reason reads the same."""
    with pytest.raises(FencedCommitError) as refusal:
        repository._projection_target(relative_path)

    assert refusal.value.code == "projection-invalid"
    assert refusal.value.detail == (
        "projection target is outside its compatibility root"
    )


@pytest.mark.parametrize(
    "relative_path",
    ["/abs/x.md", "", ".", "..", "a/../x.md", f"{ROOT_NAME}/x.md", ROOT_NAME],
)
def test_the_pinned_resolver_answers_the_same_way(repository, relative_path):
    """It decides separately, so it is checked separately."""
    with pytest.raises(FencedCommitError) as refusal:
        with repository._pinned_projection_target(_record(relative_path)):
            pass

    assert refusal.value.code == "projection-invalid"


def test_all_three_reach_the_shared_decision(repository, monkeypatch):
    """Replacing the decision changes all three, which is the point of it.

    Three copies agreeing is a fact about today; one decision is a property.
    """
    import mission_kernel.projection_path as module

    calls = []
    real = module.resolve_projection_path

    def _spy(candidate, *, root_name):
        calls.append(str(candidate))
        return real(candidate, root_name=root_name)

    monkeypatch.setattr(module, "resolve_projection_path", _spy)
    for target in ("mission_application.evidence_publication",
                   "mission_persistence.fenced_commit"):
        monkeypatch.setattr(sys.modules[target], "resolve_projection_path", _spy)

    canonical_publication_path("docs/a.md", repository_root_name=ROOT_NAME)
    repository._projection_target("docs/b.md")
    with pytest.raises(FencedCommitError):
        with repository._pinned_projection_target(_record("docs/c.md")):
            pass

    assert calls == ["docs/a.md", "docs/b.md", "docs/c.md"]


def test_the_pinned_target_carries_the_directory_it_started_from(repository):
    """The verifier checks what was opened, not what it assumes was opened."""
    import inspect

    import mission_persistence.fenced_commit as module

    source = inspect.getsource(module.LocalFencedRepository._verify_pinned_projection_target)

    assert "pinned.base" in source
    assert "self.root.parent" not in source
    # ``base`` is ``root.parent`` for every projection today, so no behaviour
    # separates the two; 3a introduces the case that does.
    assert "base" in {field for field in module._PinnedProjectionTarget.__dataclass_fields__}
