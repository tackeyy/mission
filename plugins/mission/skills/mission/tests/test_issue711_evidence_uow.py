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


def _claim_fields():
    return {
        "kind": "context-manifest",
        "target": "manifest.json",
        "publication_path": "build/manifest.json",
        "digest": "sha256:" + "0" * 64,
        "size": 12,
    }


def test_semantic_claim_keeps_only_what_decides_the_operation():
    from mission_application.evidence_publication import project_semantic_claim

    assert project_semantic_claim(_claim_fields()) == {
        "kind": "context-manifest",
        "target": "manifest.json",
        "publication_path": "build/manifest.json",
    }


def test_semantic_claim_drops_the_generated_content_fields():
    from mission_application.evidence_publication import project_semantic_claim

    projected = project_semantic_claim(_claim_fields())
    assert "digest" not in projected and "size" not in projected


def test_semantic_claim_is_stable_when_only_the_content_changes():
    from mission_application.evidence_publication import project_semantic_claim

    first = _claim_fields()
    second = dict(first, digest="sha256:" + "1" * 64, size=999)
    assert project_semantic_claim(first) == project_semantic_claim(second)


def test_semantic_claim_changes_when_the_destination_changes():
    from mission_application.evidence_publication import project_semantic_claim

    first = _claim_fields()
    second = dict(first, publication_path="build/other.json", target="other.json")
    assert project_semantic_claim(first) != project_semantic_claim(second)


def test_semantic_claim_rejects_a_claim_whose_target_is_not_the_basename():
    from mission_application.evidence_publication import project_semantic_claim

    with pytest.raises(EvidencePublicationError):
        project_semantic_claim(dict(_claim_fields(), target="mismatch.json"))


def test_blob_origin_values_are_closed():
    from mission_application.evidence_publication import BLOB_ORIGINS

    assert BLOB_ORIGINS == frozenset({"captured", "generated"})


def test_legacy_records_default_to_captured():
    from mission_application.evidence_publication import blob_origin_of

    assert blob_origin_of({"blob_id": "evidence:x"}) == "captured"
    assert blob_origin_of({"blob_id": "evidence:x", "origin": "generated"}) == "generated"


def test_unknown_origin_is_refused_rather_than_defaulted():
    from mission_application.evidence_publication import blob_origin_of

    with pytest.raises(EvidencePublicationError):
        blob_origin_of({"blob_id": "evidence:x", "origin": "derived"})


PERSISTED_RECORD_SCHEMAS = {
    "mission-commit": "carries effects",
    "mission-prepare": "carries effects and projections",
    "mission-generation": "carries blobs",
    "mission-intent": "digests blob bindings",
    "mission-operation": "records the replay result",
    "mission-recovery": "reconstructs a prepare",
    "mission-recovery-operation": "reconstructs an operation",
}
UNVERSIONED_BY_BLOB_SHAPE = {
    "mission-prepared-binding": "binds stage identity, not blob content",
    "mission-operation-key": "derives a filename from session and operation only",
    "mission-head": "refers to a commit and a generation, holds no effects",
    "mission-recovery-operation-index": "marks the index as ready, holds no payload",
}


def _fenced_commit_source():
    from pathlib import Path

    import mission_persistence.fenced_commit as module

    return Path(module.__file__).read_text(encoding="utf-8")


def test_every_persisted_schema_is_classified():
    import re

    source = _fenced_commit_source()
    found = {match.group(1) for match in re.finditer(r'"(mission-[a-z-]+)/\d+"', source)}
    classified = set(PERSISTED_RECORD_SCHEMAS) | set(UNVERSIONED_BY_BLOB_SHAPE)
    assert found == classified, (
        "a persisted record schema appeared or disappeared; classify it before "
        "changing the blob shape: " + repr(found ^ classified)
    )


def test_records_outside_the_blob_shape_are_named_with_a_reason():
    for name, reason in UNVERSIONED_BY_BLOB_SHAPE.items():
        assert reason and name not in PERSISTED_RECORD_SCHEMAS


def test_record_version_accepts_both_generations():
    from mission_application.evidence_publication import record_version

    assert record_version({"schema": "mission-commit/1"}, "mission-commit") == 1
    assert record_version({"schema": "mission-commit/2"}, "mission-commit") == 2


def test_record_version_refuses_another_record_name():
    from mission_application.evidence_publication import record_version

    with pytest.raises(EvidencePublicationError):
        record_version({"schema": "mission-prepare/1"}, "mission-commit")


def test_record_version_refuses_an_unknown_generation():
    from mission_application.evidence_publication import record_version

    for schema in ("mission-commit/0", "mission-commit/3", "mission-commit"):
        with pytest.raises(EvidencePublicationError):
            record_version({"schema": schema}, "mission-commit")


def test_record_version_refuses_a_missing_or_non_string_schema():
    from mission_application.evidence_publication import record_version

    for document in ({}, {"schema": 2}, {"schema": None}):
        with pytest.raises(EvidencePublicationError):
            record_version(document, "mission-commit")


def test_version_one_records_carry_no_materialization():
    from mission_application.evidence_publication import expects_materialization

    assert expects_materialization(1) is False
    assert expects_materialization(2) is True
