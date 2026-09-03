"""#711: evidence publish が UoW へ渡す publication path と blob 識別子.

publication path と blob id は intent / generation / prepare / commit の各
レコードへ永続化される。式が動くと既存レコードを読めなくなるため、導出は
この 1 箇所に置き、呼び出し側で再導出しない。
"""
import hashlib
import json
from pathlib import PurePosixPath

REPOSITORY_ROOT_NAME = ".mission-state"
BLOB_ID_PREFIX = "evidence:"


class EvidencePublicationError(Exception):
    """Reject one publication path that the unit of work cannot carry."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def canonical_publication_path(relative_path: str) -> str:
    """Return the one canonical form both the claim and the binding carry.

    The unit of work resolves projections against the project root and
    refuses its own repository subtree, so the same restriction has to hold
    before the path reaches a binding.
    """
    if not isinstance(relative_path, str) or not relative_path:
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path must be a non-empty string"
        )
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute():
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path must be relative"
        )
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path has an empty or relative segment"
        )
    if relative_path.count("//"):
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path has an empty segment"
        )
    if parts[0] == REPOSITORY_ROOT_NAME:
        raise EvidencePublicationError(
            "publication-path-invalid", "publication path is inside the repository root"
        )
    return "/".join(parts)


def derive_blob_id(canonical_path: str) -> str:
    """Return the blob identifier bound to one canonical publication path.

    Nothing but the path takes part: a timestamp or an iteration would make
    the identifier differ between the prepare and the retry that follows it.
    """
    if canonical_publication_path(canonical_path) != canonical_path:
        raise EvidencePublicationError(
            "publication-path-invalid", "blob id requires the canonical publication path"
        )
    digest = hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()
    return BLOB_ID_PREFIX + digest


BLOB_ORIGINS = frozenset({"captured", "generated"})
LEGACY_BLOB_ORIGIN = "captured"
SEMANTIC_CLAIM_FIELDS = ("kind", "target", "publication_path")


def project_semantic_claim(claim: dict) -> dict:
    """Return the part of one effect claim that decides which operation this is.

    ``digest`` and ``size`` describe what the operation produced, not what it
    was asked to do, so a retry that materializes the same request must not
    look like a different operation because the bytes moved.
    """
    if not isinstance(claim, dict):
        raise EvidencePublicationError("effect-claim-invalid", "effect claim must be a mapping")
    missing = [name for name in SEMANTIC_CLAIM_FIELDS if name not in claim]
    if missing:
        raise EvidencePublicationError(
            "effect-claim-invalid", "effect claim is missing " + ", ".join(missing)
        )
    canonical = canonical_publication_path(claim["publication_path"])
    if claim["target"] != PurePosixPath(canonical).name:
        raise EvidencePublicationError(
            "effect-claim-invalid", "effect target is not the publication basename"
        )
    return {name: claim[name] for name in SEMANTIC_CLAIM_FIELDS}


def blob_origin_of(record: dict) -> str:
    """Return the origin one persisted blob record carries.

    Records written before this field existed only ever held captured input,
    so their absence is a known value rather than an unknown one.  An origin
    that is present but unrecognised is refused instead: defaulting it would
    silently reclassify a blob the writer meant to distinguish.
    """
    if not isinstance(record, dict):
        raise EvidencePublicationError("blob-origin-invalid", "blob record must be a mapping")
    if "origin" not in record:
        return LEGACY_BLOB_ORIGIN
    origin = record["origin"]
    if origin not in BLOB_ORIGINS:
        raise EvidencePublicationError("blob-origin-invalid", "blob origin is not recognised")
    return origin


KNOWN_RECORD_VERSIONS = (1, 2)
MATERIALIZATION_RECORD_VERSION = 2


def record_version(document: dict, record_name: str) -> int:
    """Return which generation of one persisted record this document is.

    The blob shape changes between generations, so every reader has to know
    which one it holds before it looks for a field.  Deciding this from the
    code path that produced the read would be wrong: a v4 repository keeps no
    operation record at all, and the records that do exist were written by an
    older v5.
    """
    if not isinstance(document, dict):
        raise EvidencePublicationError("record-invalid", "record must be a mapping")
    schema = document.get("schema")
    if not isinstance(schema, str):
        raise EvidencePublicationError("record-invalid", "record schema is not a string")
    name, separator, version_text = schema.rpartition("/")
    if not separator or name != record_name:
        raise EvidencePublicationError(
            "record-invalid", "record schema is not " + record_name
        )
    try:
        version = int(version_text)
    except ValueError:
        raise EvidencePublicationError(
            "record-invalid", "record schema version is not an integer"
        ) from None
    if version not in KNOWN_RECORD_VERSIONS:
        raise EvidencePublicationError(
            "record-invalid", "record schema version is not recognised"
        )
    return version


def expects_materialization(version: int) -> bool:
    """Say whether one record generation carries the materialization binding."""
    return version >= MATERIALIZATION_RECORD_VERSION


SEMANTIC_INTENT_SCHEMA = "mission-intent/2"
BINDING_FIELDS = ("blob_id", "digest", "kind", "relative_path", "size")


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _partition_bindings(bindings) -> tuple[list, list]:
    captured, generated = [], []
    for record in bindings:
        origin = blob_origin_of(record)
        missing = [name for name in BINDING_FIELDS if name not in record]
        if missing:
            raise EvidencePublicationError(
                "blob-binding-invalid", "binding is missing " + ", ".join(missing)
            )
        projected = {name: record[name] for name in BINDING_FIELDS}
        (generated if origin == "generated" else captured).append(projected)
    key = lambda item: item["blob_id"]
    return sorted(captured, key=key), sorted(generated, key=key)


def semantic_intent_digest(inputs: dict) -> str:
    """Return the digest of what the caller asked for, not of what came out.

    Generated bindings are left out on purpose: their digests only exist once
    the operation has run, so including them would make the same request look
    like a different operation every time its output moved.  Captured input
    stays in, because a different input is a different request.
    """
    captured, _generated = _partition_bindings(inputs["bindings"])
    return "sha256:" + hashlib.sha256(
        _canonical(
            {
                "blobs": captured,
                "command": inputs["command"],
                "lease_owner_session_id": inputs["lease_owner_session_id"],
                "operation_id": inputs["operation_id"],
                "schema": SEMANTIC_INTENT_SCHEMA,
                "session_id": inputs["session_id"],
            }
        )
    ).hexdigest()


def materialization_binding(
    *,
    bindings,
    base_head_digest: str,
    base_generation: int,
    state_digest: str,
) -> dict:
    """Return what this run actually produced, against the base it saw.

    Kept apart from the semantic digest so a replay can check that the commit
    it is about to return holds the same bytes this run just prepared.
    """
    _captured, generated = _partition_bindings(bindings)
    if type(base_generation) is not int:
        raise EvidencePublicationError(
            "materialization-invalid", "base generation is not an integer"
        )
    return {
        "base_generation": base_generation,
        "base_head_digest": base_head_digest,
        "blobs": generated,
        "state_digest": state_digest,
    }


def assert_replay_materializes(*, recorded, prepared: dict) -> None:
    """Refuse a replay whose commit does not hold what this run prepared.

    A replay means the same operation already committed.  It does not mean it
    committed the same bytes: without this check a re-run that would have
    written different content returns success while its content never lands.

    Only the blobs take part.  The base a run saw is allowed to differ,
    because the whole point of a replay is that the base has moved on.
    """
    if not isinstance(recorded, dict):
        raise EvidencePublicationError(
            "replay-materialization-mismatch",
            "the recorded operation carries no materialization to compare",
        )
    if recorded.get("blobs") != prepared.get("blobs"):
        raise EvidencePublicationError(
            "replay-materialization-mismatch",
            "the recorded commit does not hold the prepared content",
        )


MAX_BASE_RETRIES = 3
BASE_IDENTIFIERS = ("base_head_digest", "base_generation")


def base_agrees(*, observed: dict, admitted: dict) -> bool:
    """Say whether the base this run prepared against is the admitted one.

    Both identifiers have to match.  Either one alone can repeat across a
    move -- a generation is only unique within a lineage, and a head digest
    says nothing about how far the lineage has advanced -- so a single
    agreement is not evidence that the base held still.
    """
    for side in (observed, admitted):
        if not isinstance(side, dict):
            raise EvidencePublicationError("base-invalid", "base must be a mapping")
        missing = [name for name in BASE_IDENTIFIERS if name not in side]
        if missing:
            raise EvidencePublicationError(
                "base-invalid", "base is missing " + ", ".join(missing)
            )
    return all(observed[name] == admitted[name] for name in BASE_IDENTIFIERS)


def next_attempt(attempt: int) -> int:
    """Return the next attempt number, or refuse to keep going.

    Running out of attempts ends the operation without publishing.  Falling
    through to a publish would place a file against a base nobody confirmed.
    """
    if type(attempt) is not int or not 1 <= attempt <= MAX_BASE_RETRIES:
        raise EvidencePublicationError(
            "base-retry-invalid", "attempt is outside the retry budget"
        )
    if attempt == MAX_BASE_RETRIES:
        raise EvidencePublicationError(
            "base-retry-exhausted", "the base moved on every attempt; nothing was published"
        )
    return attempt + 1


OPERATION_RECORD_KEYS = {
    1: frozenset(
        {"commit_digest", "intent_digest", "operation_id", "result", "schema", "session_id"}
    ),
    2: frozenset(
        {
            "commit_digest",
            "intent_digest",
            "materialization",
            "operation_id",
            "result",
            "schema",
            "session_id",
        }
    ),
}


def operation_record_keys(version: int) -> frozenset:
    """Return the exact key set one generation of the operation record holds.

    The reader compares keys exactly, so a record gains a field only by
    gaining a version.  Writing the new field into a v1 record would make
    every existing reader refuse it.
    """
    if type(version) is not int or version not in OPERATION_RECORD_KEYS:
        raise EvidencePublicationError(
            "record-invalid", "operation record version is not recognised"
        )
    return OPERATION_RECORD_KEYS[version]


def read_operation_record(document: dict) -> dict:
    """Parse one operation record of either generation.

    The key set is checked exactly against the generation the record names,
    so a v1 record that somehow carries a materialization is refused rather
    than read as a v2: a record whose shape and version disagree was not
    written by anything this code understands.
    """
    version = record_version(document, "mission-operation")
    expected = operation_record_keys(version)
    present = frozenset(document)
    if present != expected:
        raise EvidencePublicationError(
            "record-invalid",
            "operation record keys differ from version %d: %r" % (
                version, sorted(present ^ expected)
            ),
        )
    return {
        "version": version,
        "materialization": document["materialization"] if version >= 2 else None,
    }


def replay_requires_materialization(version: int) -> bool:
    """Say whether one replay has to prove it holds the prepared content.

    Records written before the field existed cannot carry it.  Demanding it
    of them would strand every mission that ran before this change, so the
    obligation starts with the generation that has somewhere to put it.
    """
    if type(version) is not int or version not in KNOWN_RECORD_VERSIONS:
        raise EvidencePublicationError(
            "record-invalid", "operation record version is not recognised"
        )
    return expects_materialization(version)
