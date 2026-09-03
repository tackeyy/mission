"""#711: evidence publish を v5 UoW へ取り込む（第 1 段）.

第 1 段は publication path の正規化と blob 識別子の導出を固定する。
どちらも intent / generation / prepare / commit の各レコードへ永続化される
ため、式が動くと既存レコードを読めなくなる。
"""
import hashlib

import pytest

from mission_application.evidence_publication import (
    EvidencePublicationError,
    canonical_publication_path,
    derive_blob_id,
)


def test_canonical_path_keeps_a_relative_posix_path():
    assert canonical_publication_path("build/manifest.json") == "build/manifest.json"


def test_canonical_path_rejects_the_repository_root_subtree():
    with pytest.raises(EvidencePublicationError) as excinfo:
        canonical_publication_path(".mission-state/manifest.json")
    assert excinfo.value.code == "publication-path-invalid"


def test_canonical_path_rejects_traversal_and_empty_segments():
    for candidate in ("../outside.json", "build/../../outside.json", "build//x.json"):
        with pytest.raises(EvidencePublicationError):
            canonical_publication_path(candidate)


def test_canonical_path_rejects_an_absolute_path():
    with pytest.raises(EvidencePublicationError):
        canonical_publication_path("/tmp/manifest.json")


def test_blob_id_is_derived_only_from_the_canonical_path():
    canonical = "build/manifest.json"
    expected = "evidence:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert derive_blob_id(canonical) == expected


def test_blob_id_matches_the_binding_identifier_grammar():
    blob_id = derive_blob_id("build/manifest.json")
    assert len(blob_id) == len("evidence:") + 64
    assert blob_id.startswith("evidence:")
    assert all(character in "0123456789abcdef" for character in blob_id[len("evidence:"):])


def test_blob_id_rejects_a_path_that_is_not_canonical():
    with pytest.raises(EvidencePublicationError):
        derive_blob_id(".mission-state/manifest.json")
