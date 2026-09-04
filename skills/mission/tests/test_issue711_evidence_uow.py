"""#711: evidence publish を v5 UoW へ取り込む（第 1 段）.

第 1 段は publication path の正規化と blob 識別子の導出を固定する。
どちらも intent / generation / prepare / commit の各レコードへ永続化される
ため、式が動くと既存レコードを読めなくなる。
"""
import hashlib
import json

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
    for candidate in ("../outside.json", "build/../../outside.json", "x.json/.."):
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


def _binding(path, origin, digest_byte="0"):
    return {
        "blob_id": "evidence:" + hashlib.sha256(path.encode("utf-8")).hexdigest(),
        "digest": "sha256:" + digest_byte * 64,
        "kind": "context-manifest",
        "origin": origin,
        "relative_path": path,
        "size": 12,
    }


def _intent_inputs(bindings=None):
    return {
        "session_id": "s-1",
        "lease_owner_session_id": "s-1",
        "operation_id": "op-1",
        "command": {"schema": "mission-command-intent/1", "type": "context-manifest"},
        "bindings": bindings if bindings is not None else (),
    }


def test_semantic_intent_ignores_generated_bindings():
    from mission_application.evidence_publication import semantic_intent_digest

    without = semantic_intent_digest(_intent_inputs())
    with_generated = semantic_intent_digest(
        _intent_inputs((_binding("build/manifest.json", "generated"),))
    )
    assert without == with_generated


def test_semantic_intent_depends_on_captured_bindings():
    from mission_application.evidence_publication import semantic_intent_digest

    without = semantic_intent_digest(_intent_inputs())
    with_captured = semantic_intent_digest(
        _intent_inputs((_binding("input/source.json", "captured"),))
    )
    assert without != with_captured


def test_semantic_intent_is_stable_when_generated_content_changes():
    from mission_application.evidence_publication import semantic_intent_digest

    first = semantic_intent_digest(
        _intent_inputs((_binding("build/manifest.json", "generated", "0"),))
    )
    second = semantic_intent_digest(
        _intent_inputs((_binding("build/manifest.json", "generated", "1"),))
    )
    assert first == second


def test_semantic_intent_changes_when_the_command_changes():
    from mission_application.evidence_publication import semantic_intent_digest

    other = _intent_inputs()
    other["command"] = {"schema": "mission-command-intent/1", "type": "claims-ledger"}
    assert semantic_intent_digest(_intent_inputs()) != semantic_intent_digest(other)


def test_materialization_binding_holds_only_generated_bindings():
    from mission_application.evidence_publication import materialization_binding

    bound = materialization_binding(
        bindings=(
            _binding("build/manifest.json", "generated"),
            _binding("input/source.json", "captured"),
        ),
        base_head_digest="sha256:" + "a" * 64,
        base_generation=7,
        state_digest="sha256:" + "b" * 64,
    )
    assert [item["relative_path"] for item in bound["blobs"]] == ["build/manifest.json"]
    assert bound["base_generation"] == 7


def test_materialization_binding_orders_blobs_by_identifier():
    from mission_application.evidence_publication import materialization_binding

    bindings = (
        _binding("build/zzz.json", "generated"),
        _binding("build/aaa.json", "generated"),
    )
    bound = materialization_binding(
        bindings=bindings,
        base_head_digest="sha256:" + "a" * 64,
        base_generation=1,
        state_digest="sha256:" + "b" * 64,
    )
    identifiers = [item["blob_id"] for item in bound["blobs"]]
    assert identifiers == sorted(identifiers)


def test_materialization_binding_refuses_an_unknown_origin():
    from mission_application.evidence_publication import materialization_binding

    with pytest.raises(EvidencePublicationError):
        materialization_binding(
            bindings=(_binding("build/manifest.json", "derived"),),
            base_head_digest="sha256:" + "a" * 64,
            base_generation=1,
            state_digest="sha256:" + "b" * 64,
        )


def _materialization(paths, digest_byte="0", generation=1):
    from mission_application.evidence_publication import materialization_binding

    return materialization_binding(
        bindings=tuple(_binding(path, "generated", digest_byte) for path in paths),
        base_head_digest="sha256:" + "a" * 64,
        base_generation=generation,
        state_digest="sha256:" + "b" * 64,
    )


def test_replay_accepts_a_commit_that_holds_the_same_bytes():
    from mission_application.evidence_publication import assert_replay_materializes

    assert_replay_materializes(
        recorded=_materialization(["build/manifest.json"]),
        prepared=_materialization(["build/manifest.json"]),
    )


def test_replay_refuses_a_commit_whose_content_differs():
    from mission_application.evidence_publication import assert_replay_materializes

    with pytest.raises(EvidencePublicationError) as excinfo:
        assert_replay_materializes(
            recorded=_materialization(["build/manifest.json"], "0"),
            prepared=_materialization(["build/manifest.json"], "1"),
        )
    assert excinfo.value.code == "replay-materialization-mismatch"


def test_replay_refuses_a_commit_that_wrote_a_different_path():
    from mission_application.evidence_publication import assert_replay_materializes

    with pytest.raises(EvidencePublicationError):
        assert_replay_materializes(
            recorded=_materialization(["build/manifest.json"]),
            prepared=_materialization(["build/other.json"]),
        )


def test_replay_refuses_a_commit_that_wrote_a_different_number_of_blobs():
    from mission_application.evidence_publication import assert_replay_materializes

    with pytest.raises(EvidencePublicationError):
        assert_replay_materializes(
            recorded=_materialization(["build/a.json", "build/b.json"]),
            prepared=_materialization(["build/a.json"]),
        )


def test_replay_compares_content_and_not_the_base_it_ran_against():
    from mission_application.evidence_publication import assert_replay_materializes

    assert_replay_materializes(
        recorded=_materialization(["build/manifest.json"], generation=3),
        prepared=_materialization(["build/manifest.json"], generation=9),
    )


def test_replay_refuses_a_record_without_a_materialization():
    from mission_application.evidence_publication import assert_replay_materializes

    with pytest.raises(EvidencePublicationError):
        assert_replay_materializes(
            recorded=None, prepared=_materialization(["build/manifest.json"])
        )


def _base(head_byte="a", generation=1):
    return {"base_head_digest": "sha256:" + head_byte * 64, "base_generation": generation}


def test_base_agrees_when_both_identifiers_match():
    from mission_application.evidence_publication import base_agrees

    assert base_agrees(observed=_base(), admitted=_base()) is True


def test_base_disagrees_when_only_the_head_digest_moved():
    from mission_application.evidence_publication import base_agrees

    assert base_agrees(observed=_base("a"), admitted=_base("c")) is False


def test_base_disagrees_when_only_the_generation_moved():
    from mission_application.evidence_publication import base_agrees

    assert base_agrees(observed=_base(generation=1), admitted=_base(generation=2)) is False


def test_base_comparison_refuses_a_missing_identifier():
    from mission_application.evidence_publication import base_agrees

    with pytest.raises(EvidencePublicationError):
        base_agrees(observed={"base_generation": 1}, admitted=_base())


def test_retry_budget_allows_three_attempts():
    from mission_application.evidence_publication import MAX_BASE_RETRIES, next_attempt

    assert MAX_BASE_RETRIES == 3
    assert next_attempt(1) == 2
    assert next_attempt(2) == 3


def test_retry_budget_stops_rather_than_publishing():
    from mission_application.evidence_publication import next_attempt

    with pytest.raises(EvidencePublicationError) as excinfo:
        next_attempt(3)
    assert excinfo.value.code == "base-retry-exhausted"


def test_retry_budget_refuses_an_attempt_outside_the_budget():
    from mission_application.evidence_publication import next_attempt

    for attempt in (0, -1, 4):
        with pytest.raises(EvidencePublicationError):
            next_attempt(attempt)


OPERATION_V1_KEYS = frozenset(
    {"commit_digest", "intent_digest", "operation_id", "result", "schema", "session_id"}
)
OPERATION_V2_KEYS = OPERATION_V1_KEYS | {"materialization"}


def test_operation_record_key_sets_are_declared_per_version():
    from mission_application.evidence_publication import operation_record_keys

    assert operation_record_keys(1) == OPERATION_V1_KEYS
    assert operation_record_keys(2) == OPERATION_V2_KEYS


def test_operation_v2_adds_exactly_one_key():
    from mission_application.evidence_publication import operation_record_keys

    assert operation_record_keys(2) - operation_record_keys(1) == {"materialization"}


def test_operation_record_keys_refuse_an_unknown_version():
    from mission_application.evidence_publication import operation_record_keys

    for version in (0, 3, "2"):
        with pytest.raises(EvidencePublicationError):
            operation_record_keys(version)


def test_the_repository_no_longer_parses_the_operation_record_itself():
    """The repository must delegate, or the shared rules never run.

    Replacing the delegating call with a constant left every pre-existing
    test green, so the absence of a second parser is worth holding.
    """
    from pathlib import Path

    import mission_persistence.fenced_commit as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "read_operation_record(document, repository_root_name=" in source
    assert 'if document["schema"] != "mission-operation/1"' not in source


def _operation_document(version, materialization=None):
    document = {
        "commit_digest": "sha256:" + "c" * 64,
        "intent_digest": "sha256:" + "d" * 64,
        "operation_id": "op-1",
        "result": {},
        "schema": "mission-operation/%d" % version,
        "session_id": "s-1",
    }
    if version == 2:
        document["materialization"] = materialization or _materialization(["build/x.json"])
    return document


def test_operation_reader_accepts_a_version_one_record():
    from mission_application.evidence_publication import read_operation_record

    parsed = read_operation_record(_operation_document(1))
    assert parsed["version"] == 1 and parsed["materialization"] is None


def test_operation_reader_accepts_a_version_two_record():
    from mission_application.evidence_publication import read_operation_record

    parsed = read_operation_record(_operation_document(2))
    assert parsed["version"] == 2 and parsed["materialization"] is not None


def test_operation_reader_refuses_a_version_one_record_carrying_materialization():
    from mission_application.evidence_publication import read_operation_record

    document = _operation_document(1)
    document["materialization"] = _materialization(["build/x.json"])
    with pytest.raises(EvidencePublicationError):
        read_operation_record(document)


def test_operation_reader_refuses_a_version_two_record_without_materialization():
    from mission_application.evidence_publication import read_operation_record

    document = _operation_document(2)
    del document["materialization"]
    with pytest.raises(EvidencePublicationError):
        read_operation_record(document)


def test_operation_reader_refuses_an_extra_key_in_either_version():
    from mission_application.evidence_publication import read_operation_record

    for version in (1, 2):
        document = dict(_operation_document(version), surprise=True)
        with pytest.raises(EvidencePublicationError):
            read_operation_record(document)


def test_a_version_one_replay_is_not_asked_to_prove_its_content():
    """A record written before materialization existed cannot carry it.

    Refusing those replays would make every mission that ran before this
    change unable to resume, so the check applies from version two onward.
    """
    from mission_application.evidence_publication import replay_requires_materialization

    assert replay_requires_materialization(1) is False
    assert replay_requires_materialization(2) is True


def test_a_corrupted_operation_record_is_refused_when_the_replay_reads_it(tmp_path):
    """Exercise the reader on the path that actually reaches it.

    An earlier version of this test drove the CLI twice and asserted a
    non-zero exit.  That exit came from ``session-already-initialized``,
    which is raised before the operation record is ever opened, so the test
    passed while the parser was replaced by a constant.  Reaching the reader
    needs a genuine replay: the same operation id presented again to a
    repository that already committed it.
    """
    import json

    from mission_persistence.fenced_commit import CommitResult, FencedCommitError

    from .test_issue503_fenced_commit import _commit_cli_init, _request

    local, repository, _clock, _state_path, state_document_bytes, _result = _commit_cli_init(
        tmp_path
    )
    lease_id = json.loads(state_document_bytes.decode("utf-8"))["lease_id"]
    same_request = _request(
        operation_id="operation-init",
        lease_id=lease_id,
        argv=("init", "Issue 500 CLI corpus"),
        command_type="init",
        event_types=("mission-initialized",),
    )

    assert isinstance(local.begin(same_request), CommitResult), (
        "the fixture is expected to replay before the record is corrupted"
    )

    operations = sorted((repository / "operations").glob("*.json"))
    assert len(operations) == 1
    document = json.loads(operations[0].read_text(encoding="utf-8"))
    document["schema"] = "mission-operation/9"
    operations[0].write_bytes(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )

    with pytest.raises(FencedCommitError) as excinfo:
        local.begin(same_request)
    assert excinfo.value.code == "record-invalid", (
        "the record has to fail the schema check, not the canonical-form check"
    )


def test_record_version_refuses_a_padded_version_number():
    """The reader it replaced compared the schema string exactly.

    Parsing the tail as an integer accepts `/01`, which the old exact
    comparison refused, so a record no writer of ours produced would replay.
    """
    from mission_application.evidence_publication import record_version

    for schema in ("mission-commit/01", "mission-commit/+1", "mission-commit/1 "):
        with pytest.raises(EvidencePublicationError):
            record_version({"schema": schema}, "mission-commit")


def test_canonical_path_uses_the_repository_root_it_is_given():
    """The unit of work refuses its own root by name, not by a fixed string."""
    from mission_application.evidence_publication import canonical_publication_path

    assert canonical_publication_path(
        ".mission-state/x.json", repository_root_name="other-root"
    ) == ".mission-state/x.json"
    with pytest.raises(EvidencePublicationError):
        canonical_publication_path("other-root/x.json", repository_root_name="other-root")


def test_canonical_path_collapses_what_the_projection_collapses():
    """`a//b` reaches the projection as `a/b`, so it has to survive here too."""
    from mission_application.evidence_publication import canonical_publication_path

    assert canonical_publication_path("build//manifest.json") == "build/manifest.json"


def test_semantic_claim_returns_the_canonical_publication_path():
    from mission_application.evidence_publication import project_semantic_claim

    projected = project_semantic_claim(
        dict(_claim_fields(), publication_path="build//manifest.json")
    )
    assert projected["publication_path"] == "build/manifest.json"


def test_operation_reader_refuses_a_materialization_that_is_not_a_binding():
    """Parsing a field without checking it leaves the replay unguarded.

    A record whose materialization is null, a string, or an object missing
    its parts still reached the replay and succeeded, because nothing looked
    inside it.
    """
    from mission_application.evidence_publication import read_operation_record

    for broken in (None, "materialized", [], {}, {"blobs": []}, {"blobs": "x"}):
        document = _operation_document(2)
        document["materialization"] = broken
        with pytest.raises(EvidencePublicationError):
            read_operation_record(document)


def test_operation_reader_accepts_a_well_formed_materialization():
    from mission_application.evidence_publication import read_operation_record

    parsed = read_operation_record(_operation_document(2))
    assert parsed["materialization"]["blobs"]


def test_materialization_must_name_every_part():
    from mission_application.evidence_publication import read_materialization

    complete = _materialization(["build/x.json"])
    for absent in ("base_generation", "base_head_digest", "blobs", "state_digest"):
        partial = {k: v for k, v in complete.items() if k != absent}
        with pytest.raises(EvidencePublicationError):
            read_materialization(partial)


def test_materialization_requires_at_least_one_generated_blob():
    """An empty blob list would make every replay comparison trivially agree."""
    from mission_application.evidence_publication import read_materialization

    with pytest.raises(EvidencePublicationError) as excinfo:
        read_materialization(dict(_materialization(["build/x.json"]), blobs=[]))
    assert excinfo.value.code == "materialization-invalid"


def test_a_broken_materialization_is_refused_when_the_replay_reads_it(tmp_path):
    """The shape check has to run on the path the repository takes.

    A materialization that is null or empty used to reach the replay and
    succeed, so this drives the same genuine replay as the schema case.
    """
    import json

    from mission_persistence.fenced_commit import CommitResult, FencedCommitError

    from .test_issue503_fenced_commit import _commit_cli_init, _request

    local, repository, _clock, _state_path, state_bytes, _result = _commit_cli_init(tmp_path)
    lease_id = json.loads(state_bytes.decode("utf-8"))["lease_id"]
    same_request = _request(
        operation_id="operation-init",
        lease_id=lease_id,
        argv=("init", "Issue 500 CLI corpus"),
        command_type="init",
        event_types=("mission-initialized",),
    )
    assert isinstance(local.begin(same_request), CommitResult)

    operations = sorted((repository / "operations").glob("*.json"))
    document = json.loads(operations[0].read_text(encoding="utf-8"))
    document["schema"] = "mission-operation/2"
    # Every part is present so the record fails on the empty blob list alone,
    # not on a missing field: the point is that an empty list cannot stand in
    # for content the replay is supposed to prove.
    document["materialization"] = {
        "base_generation": 1,
        "base_head_digest": "sha256:" + "a" * 64,
        "blobs": [],
        "state_digest": "sha256:" + "b" * 64,
    }
    operations[0].write_bytes(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )

    with pytest.raises(FencedCommitError) as excinfo:
        local.begin(same_request)
    assert excinfo.value.code == "record-invalid"


def _production_command(digest_byte="0", size=12):
    """Build the document production actually encodes, not a simplified stand-in.

    An earlier version of these tests used a hand-written command whose only
    fields were a schema and a type.  Nothing about the effect appeared in
    it, so the semantic digest looked stable while the real encoding carried
    the effect digest and size and changed with every byte produced.
    """
    from mission_kernel.commands import (
        ContextManifestEffectClaim,
        GenerateContextManifest,
        encode_kernel_command,
    )
    from mission_kernel.json_codec import thaw_json_object

    claim = ContextManifestEffectClaim(
        "context-manifest",
        "manifest.json",
        "build/manifest.json",
        "sha256:" + digest_byte * 64,
        size,
    )
    return thaw_json_object(
        encode_kernel_command(GenerateContextManifest("2026-01-01T00:00:00Z", 1, claim))
    )


def test_the_production_command_really_carries_the_generated_content():
    """Hold the premise these projection tests rest on."""
    encoded = repr(_production_command())
    assert "sha256:" + "0" * 64 in encoded and "12" in encoded


def test_semantic_command_is_stable_when_only_generated_content_changes():
    from mission_application.evidence_publication import project_semantic_command

    first = project_semantic_command(_production_command("0", 12))
    second = project_semantic_command(_production_command("1", 999))
    assert first == second


def test_semantic_command_still_changes_with_the_destination():
    from mission_application.evidence_publication import project_semantic_command

    from mission_kernel.commands import (
        ContextManifestEffectClaim,
        GenerateContextManifest,
        encode_kernel_command,
    )
    from mission_kernel.json_codec import thaw_json_object

    claim = ContextManifestEffectClaim(
        "context-manifest", "other.json", "build/other.json", "sha256:" + "0" * 64, 12
    )
    elsewhere = thaw_json_object(
        encode_kernel_command(GenerateContextManifest("2026-01-01T00:00:00Z", 1, claim))
    )
    assert project_semantic_command(_production_command()) != project_semantic_command(
        elsewhere
    )


def test_semantic_command_keeps_the_rest_of_the_command():
    from mission_application.evidence_publication import project_semantic_command

    projected = project_semantic_command(_production_command())
    assert projected["type"] and projected["value"]["iteration"] == 1


def test_semantic_intent_over_the_production_command_ignores_generated_bytes():
    from mission_application.evidence_publication import semantic_intent_digest

    def digest_for(byte, size):
        inputs = _intent_inputs((_binding("build/manifest.json", "generated", byte),))
        inputs["command"] = _production_command(byte, size)
        return semantic_intent_digest(inputs)

    assert digest_for("0", 12) == digest_for("1", 999)


def test_semantic_command_does_not_guess_from_shape():
    """Two different commands must not collapse onto one intent.

    Recognising an effect claim by "it has these five keys" reaches any
    look-alike object a command happens to carry, so a command whose real
    payload contains such an object loses that payload from its intent.
    """
    from mission_application.evidence_publication import project_semantic_command

    look_alike = {
        "digest": "sha256:" + "0" * 64,
        "kind": "context-manifest",
        "publication_path": "build/manifest.json",
        "size": 12,
        "target": "manifest.json",
    }
    first = {
        "schema": "mission-kernel-command/1",
        "type": "set-extension-fields",
        "value": {"fields": dict(look_alike)},
    }
    second = {
        "schema": "mission-kernel-command/1",
        "type": "set-extension-fields",
        "value": {"fields": dict(look_alike, size=999)},
    }
    assert project_semantic_command(first) != project_semantic_command(second)


def test_semantic_command_covers_every_command_that_declares_an_effect():
    """The projection is keyed by command type, so the table has to be complete."""
    import dataclasses

    from mission_kernel import commands as kernel_commands
    from mission_application.evidence_publication import EFFECT_FIELDS_BY_COMMAND_TYPE

    declared = {}
    for name in dir(kernel_commands):
        candidate = getattr(kernel_commands, name)
        if not (isinstance(candidate, type) and dataclasses.is_dataclass(candidate)):
            continue
        fields = tuple(
            field.name
            for field in dataclasses.fields(candidate)
            if field.name.endswith("effect")
        )
        if not fields:
            continue
        instance = candidate.__new__(candidate)
        try:
            command_type = kernel_commands.kernel_command_type(instance)
        except TypeError:
            continue
        declared[command_type] = fields
    assert EFFECT_FIELDS_BY_COMMAND_TYPE == declared


def test_semantic_command_projects_both_effects_of_an_export():
    from mission_application.evidence_publication import project_semantic_command

    claim = {
        "digest": "sha256:" + "0" * 64,
        "kind": "artifact",
        "size": 5,
        "target": "a.json",
    }
    document = {
        "schema": "mission-kernel-command/1",
        "type": "export-artifact",
        "value": {"artifact_effect": dict(claim), "export_effect": dict(claim)},
    }
    projected = project_semantic_command(document)
    for field in ("artifact_effect", "export_effect"):
        assert "digest" not in projected["value"][field]


def test_materialization_checks_the_value_of_each_binding_field():
    """Present-but-null fields used to satisfy the shape check."""
    from mission_application.evidence_publication import read_materialization

    complete = _materialization(["build/x.json"])
    for field in ("blob_id", "digest", "kind", "relative_path", "size"):
        broken = json.loads(json.dumps(complete))
        broken["blobs"][0][field] = None
        with pytest.raises(EvidencePublicationError):
            read_materialization(broken)


def test_materialization_checks_the_binding_container_type():
    from mission_application.evidence_publication import read_materialization

    broken = _materialization(["build/x.json"])
    with pytest.raises(EvidencePublicationError):
        read_materialization(dict(broken, blobs=[["not", "a", "mapping"]]))


def test_materialization_checks_the_base_generation_type():
    from mission_application.evidence_publication import read_materialization

    broken = _materialization(["build/x.json"])
    with pytest.raises(EvidencePublicationError):
        read_materialization(dict(broken, base_generation="1"))


def test_the_repository_root_name_reaches_every_entry_point():
    """A root name honoured by one function and ignored by the next is worse
    than none: the same path is accepted here and refused there.
    """
    from mission_application.evidence_publication import (
        derive_blob_id,
        project_semantic_claim,
        semantic_intent_digest,
    )

    root = "other-root"
    inside = "other-root/manifest.json"

    with pytest.raises(EvidencePublicationError):
        derive_blob_id(inside, repository_root_name=root)
    with pytest.raises(EvidencePublicationError):
        project_semantic_claim(
            {
                "kind": "context-manifest",
                "target": "manifest.json",
                "publication_path": inside,
                "digest": "sha256:" + "0" * 64,
                "size": 12,
            },
            repository_root_name=root,
        )
    with pytest.raises(EvidencePublicationError):
        semantic_intent_digest(
            _intent_inputs((_binding(inside, "captured"),)), repository_root_name=root
        )


def test_the_default_root_name_still_applies_when_none_is_given():
    from mission_application.evidence_publication import derive_blob_id

    with pytest.raises(EvidencePublicationError):
        derive_blob_id(".mission-state/manifest.json")


def test_a_path_named_after_another_root_is_ordinary_here():
    from mission_application.evidence_publication import derive_blob_id

    assert derive_blob_id(".mission-state/x.json", repository_root_name="other-root")


def _real_command(command_type):
    """Build the command production encodes, for one command type.

    The coverage test that checked only names passed while five of the seven
    types raised, because the claims are not one shape: artifact and progress
    claims carry no publication path at all.
    """
    from mission_kernel import commands as kernel

    artifact = kernel.ArtifactEffectClaim("artifact", "a.md", "sha256:" + "0" * 64, 5)
    progress = kernel.ProgressEffectClaim("progress", "p.json", "sha256:" + "0" * 64, 5)
    context = kernel.ContextManifestEffectClaim(
        "context-manifest", "m.json", "build/m.json", "sha256:" + "0" * 64, 5
    )
    ledger = kernel.ClaimsLedgerEffectClaim(
        "claims-ledger", "l.json", "build/l.json", "sha256:" + "0" * 64, 5
    )
    at = "2026-01-01T00:00:00Z"
    built = {
        "export-artifact": lambda: kernel.ExportArtifact(
            at, "drive", "reviewed", artifact, artifact
        ),
        "generate-claims-ledger": lambda: kernel.GenerateClaimsLedger(
            at, 1, "sha256:" + "d" * 64, ledger
        ),
        "generate-context-manifest": lambda: kernel.GenerateContextManifest(at, 1, context),
        "initialize-artifact": lambda: kernel.InitializeArtifact(
            at, "a.md", "markdown", "title", "reviewed", True, artifact
        ),
        "record-artifact-publication": lambda: kernel.RecordArtifactPublication(
            at, "provider", "destination", "approved", True, artifact
        ),
        "render-artifact": lambda: kernel.RenderArtifact(at, "reviewed", artifact),
        "update-progress": lambda: kernel.UpdateProgress(
            at, 1, 0, 1, None, None, 1, progress
        ),
    }[command_type]()
    from mission_kernel.json_codec import thaw_json_object

    return thaw_json_object(kernel.encode_kernel_command(built))


@pytest.mark.parametrize("command_type", sorted(
    {
        "export-artifact",
        "generate-claims-ledger",
        "generate-context-manifest",
        "initialize-artifact",
        "record-artifact-publication",
        "render-artifact",
        "update-progress",
    }
))
def test_every_declared_command_type_projects(command_type):
    from mission_application.evidence_publication import (
        EFFECT_FIELDS_BY_COMMAND_TYPE,
        project_semantic_command,
    )

    document = _real_command(command_type)
    projected = project_semantic_command(document)
    for field in EFFECT_FIELDS_BY_COMMAND_TYPE[command_type]:
        claim = projected["value"][field]
        assert "digest" not in claim and "size" not in claim
        assert claim["kind"] and claim["target"]


@pytest.mark.parametrize("command_type", ["initialize-artifact", "update-progress"])
def test_a_claim_without_a_publication_path_still_projects(command_type):
    from mission_application.evidence_publication import project_semantic_command

    projected = project_semantic_command(_real_command(command_type))
    field = "effect"
    assert "publication_path" not in projected["value"][field]


def test_partition_refuses_a_binding_whose_identifier_does_not_match_its_path():
    from mission_application.evidence_publication import semantic_intent_digest

    forged = dict(_binding("input/source.json", "captured"), blob_id="evidence:" + "0" * 64)
    with pytest.raises(EvidencePublicationError):
        semantic_intent_digest(_intent_inputs((forged,)))


def test_materialization_refuses_null_base_and_state_digests():
    from mission_application.evidence_publication import read_materialization

    complete = _materialization(["build/x.json"])
    for field in ("base_head_digest", "state_digest"):
        with pytest.raises(EvidencePublicationError):
            read_materialization(dict(complete, **{field: None}))


PATH_BEARING_COMMAND_TYPES = ("generate-context-manifest", "generate-claims-ledger")


@pytest.mark.parametrize("command_type", PATH_BEARING_COMMAND_TYPES)
def test_a_command_that_must_name_a_path_is_refused_without_one(command_type):
    """Optional everywhere is the same as required nowhere.

    The evidence commands publish to a path they declare; dropping it has to
    fail for them even though the artifact and progress claims never carry
    one.
    """
    from mission_application.evidence_publication import project_semantic_command

    document = _real_command(command_type)
    stripped = json.loads(json.dumps(document))
    del stripped["value"]["effect"]["publication_path"]
    with pytest.raises(EvidencePublicationError):
        project_semantic_command(stripped)


@pytest.mark.parametrize("command_type", ["initialize-artifact", "update-progress"])
def test_a_command_that_never_names_a_path_is_refused_with_one(command_type):
    from mission_application.evidence_publication import project_semantic_command

    document = json.loads(json.dumps(_real_command(command_type)))
    document["value"]["effect"]["publication_path"] = "build/x.json"
    with pytest.raises(EvidencePublicationError):
        project_semantic_command(document)


def test_the_writer_cannot_produce_what_the_reader_refuses():
    """A round trip has to hold, or a record is written that cannot be read."""
    from mission_application.evidence_publication import (
        materialization_binding,
        read_materialization,
    )

    for absent in ("base_head_digest", "state_digest"):
        with pytest.raises(EvidencePublicationError):
            materialization_binding(
                bindings=(_binding("build/x.json", "generated"),),
                base_head_digest=None if absent == "base_head_digest" else "sha256:" + "a" * 64,
                base_generation=1,
                state_digest=None if absent == "state_digest" else "sha256:" + "b" * 64,
            )

    written = materialization_binding(
        bindings=(_binding("build/x.json", "generated"),),
        base_head_digest="sha256:" + "a" * 64,
        base_generation=1,
        state_digest="sha256:" + "b" * 64,
    )
    assert read_materialization(written) == written


def test_a_non_canonical_path_does_not_survive_the_round_trip():
    """`build//x.json` used to be accepted going out and coming back in."""
    from mission_application.evidence_publication import (
        derive_blob_id,
        materialization_binding,
        read_materialization,
    )

    binding = {
        "blob_id": derive_blob_id("build/x.json"),
        "digest": "sha256:" + "0" * 64,
        "kind": "context-manifest",
        "origin": "generated",
        "relative_path": "build//x.json",
        "size": 12,
    }
    written = materialization_binding(
        bindings=(binding,),
        base_head_digest="sha256:" + "a" * 64,
        base_generation=1,
        state_digest="sha256:" + "b" * 64,
    )
    assert written["blobs"][0]["relative_path"] == "build/x.json"
    assert read_materialization(written) == written


def test_the_repository_passes_its_own_root_name_to_the_reader():
    from pathlib import Path

    import mission_persistence.fenced_commit as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "read_operation_record(document, repository_root_name=self.root.name)" in source
