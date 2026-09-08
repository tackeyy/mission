"""#747 3a: one canonicaliser answers for every generated file's path.

``canonical_publication_path`` answers for projections and refuses anything
inside the repository.  Progress writes inside it, so a second shape exists,
and three places have to agree on both: the blob set built from the effects,
the identifier derived from the path, and the binding read back from a
persisted record.  Splitting the rule between them is how the writer and the
reader drift apart, which is the fault this module's own docstring names.

The permission to use the in-root shape is a separate question and is not
decided here: this says which paths are *well-formed*, not who may write one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_application.evidence_publication import (  # noqa: E402
    EvidencePublicationError,
    canonical_generated_path,
    canonical_publication_path,
    derive_blob_id,
)

ROOT_NAME = ".mission-state"
INTERNAL = ".mission-state/archive/iter-4-abcdef01-progress.md"
EXTERNAL = "docs/evidence/manifest.json"


def _canonical(text):
    return canonical_generated_path(text, repository_root_name=ROOT_NAME)


def test_the_external_shape_is_unchanged():
    """Delegation, not reimplementation: the projection answer is the answer."""
    assert _canonical(EXTERNAL) == canonical_publication_path(
        EXTERNAL, repository_root_name=ROOT_NAME
    )
    assert _canonical("build//x/./y.md") == "build/x/y.md"


def test_the_internal_shape_is_accepted():
    assert _canonical(INTERNAL) == INTERNAL
    assert _canonical(".mission-state/./archive/iter-0-a-progress.md") == (
        ".mission-state/archive/iter-0-a-progress.md"
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        ".mission-state/archive/worktree-x/current.json",
        ".mission-state/commits/x",
        ".mission-state/transactions/x",
        ".mission-state/archive/progress.md",
        ".mission-state",
        "/abs/x.md",
        "a/../b",
        "",
    ],
)
def test_neither_shape_accepts_these(relative_path):
    with pytest.raises(EvidencePublicationError) as caught:
        _canonical(relative_path)
    assert caught.value.code == "publication-path-invalid"


def test_a_refused_in_root_path_is_still_told_how_to_get_out():
    """The projection wording survives for paths that are not the archive.

    A caller writing to the wrong in-root path has the same problem it had
    before this change, and the message that names the way out is the only
    place that reaches a runbook.
    """
    with pytest.raises(EvidencePublicationError) as caught:
        _canonical(".mission-state/evidence/output.json")
    assert "outside it" in str(caught.value)


def test_non_string_is_refused_the_way_it_always_was():
    for value in (None, 123, b"x", Path("x")):
        with pytest.raises(EvidencePublicationError) as caught:
            _canonical(value)
        assert caught.value.code == "publication-path-invalid"


def test_the_identifier_formula_did_not_move():
    """Existing blob ids are persisted, so the formula cannot move.

    Measured on ``c8ee1730`` before this change and pinned here: a
    reformulation that happens to accept the same paths would still break
    every record already written.
    """
    assert derive_blob_id(EXTERNAL, repository_root_name=ROOT_NAME) == (
        "evidence:a1661dbd5923bdc87f1a2fef92dbccc8727c6e23fcdd7cf189b44692c7275456"
    )


def test_the_identifier_accepts_the_internal_shape():
    internal_id = derive_blob_id(INTERNAL, repository_root_name=ROOT_NAME)
    external_id = derive_blob_id(EXTERNAL, repository_root_name=ROOT_NAME)
    assert internal_id != external_id
    # Derived from the path alone, so it is stable across a retry.
    assert internal_id == derive_blob_id(INTERNAL, repository_root_name=ROOT_NAME)


def test_the_identifier_still_refuses_a_non_canonical_path():
    with pytest.raises(EvidencePublicationError):
        derive_blob_id(
            ".mission-state/./archive/iter-4-abcdef01-progress.md",
            repository_root_name=ROOT_NAME,
        )


def test_the_binding_reader_accepts_the_internal_shape():
    from mission_application.evidence_publication import _canonical_binding

    record = {
        "blob_id": derive_blob_id(INTERNAL, repository_root_name=ROOT_NAME),
        "digest": "sha256:" + "0" * 64,
        "kind": "progress",
        "relative_path": INTERNAL,
        "size": 0,
    }
    assert _canonical_binding(record, repository_root_name=ROOT_NAME) == record


def test_the_binding_reader_still_binds_the_identifier_to_the_path():
    from mission_application.evidence_publication import _canonical_binding

    record = {
        "blob_id": derive_blob_id(EXTERNAL, repository_root_name=ROOT_NAME),
        "digest": "sha256:" + "0" * 64,
        "kind": "progress",
        "relative_path": INTERNAL,
        "size": 0,
    }
    with pytest.raises(EvidencePublicationError) as caught:
        _canonical_binding(record, repository_root_name=ROOT_NAME)
    assert caught.value.code == "blob-binding-invalid"
