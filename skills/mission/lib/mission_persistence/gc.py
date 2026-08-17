"""Reference-safe mark, quarantine, and purge for v5 generation manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mission_kernel.json_codec import STATE_LIMIT, decode_json_object, encode_json_object
from worktree_archive import validate_worktree_archive_bundle


# Measured on 2026-08-16 with generate_cli_state_corpus(): the longest natural
# sequence of production CLI writes was 10 seconds. Provider fixtures with a
# fixed 2026 timestamp and the explicit 2099 takeover fixture are not commit
# intervals. The planned one-hour grace is at least 10x the observed maximum;
# purge keeps the separately planned one-day second grace.
GRACE_SECONDS = 60 * 60
PURGE_GRACE_SECONDS = 24 * 60 * 60
PRIOR_SAFETY_COUNT = 1

_MANIFEST_NAME_RE = r"[0-9a-f]{64}\.json"
_GENERATION_NAME_RE = re.compile(_MANIFEST_NAME_RE)
_COMMIT_NAME_RE = re.compile(_MANIFEST_NAME_RE)
_PREPARE_NAME_RE = re.compile(r"[0-9a-f]{32}\.json")
_SESSION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json")


class GarbageCollectionError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RetentionPolicy:
    grace_seconds: int = GRACE_SECONDS
    purge_grace_seconds: int = PURGE_GRACE_SECONDS
    dry_run: bool = True
    destructive: bool = False


@dataclass(frozen=True)
class GCReport:
    dry_run: bool
    candidates: tuple[str, ...]
    quarantined: tuple[str, ...]
    purged: tuple[str, ...]
    changed: tuple[str, ...]


@dataclass(frozen=True)
class _Generation:
    digest: str
    path: Path
    identity: tuple[int, int, int, int, int, int, int]
    modified_at: float
    manifest_bytes: bytes


@dataclass(frozen=True)
class _Quarantine:
    digest: str
    path: Path
    identity: tuple[int, int, int, int, int, int, int]
    quarantined_at: float
    unchanged: bool


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _scan_directory(path: Path) -> tuple[Path, ...]:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise GarbageCollectionError(
                "gc-scan-incomplete", "GC scan root is not a regular directory"
            )
        with os.scandir(path) as entries:
            names = sorted(entry.name for entry in entries)
    except GarbageCollectionError:
        raise
    except OSError as exc:
        raise GarbageCollectionError(
            "gc-scan-incomplete", "GC directory cannot be scanned completely"
        ) from exc
    return tuple(path / name for name in names)


def _read_regular(path: Path, *, limit: int) -> tuple[bytes, os.stat_result]:
    descriptor = None
    try:
        named = path.lstat()
        if (
            stat.S_ISLNK(named.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or named.st_size > limit
        ):
            raise GarbageCollectionError(
                "gc-record-invalid", "GC record identity is invalid"
            )
        descriptor = os.open(
            os.fspath(path),
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
        )
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(named):
            raise GarbageCollectionError(
                "gc-record-invalid", "GC record identity changed while opening"
            )
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        final_opened = os.fstat(descriptor)
        final_named = path.lstat()
        if (
            len(content) > limit
            or len(content) != named.st_size
            or _identity(final_opened) != _identity(named)
            or _identity(final_named) != _identity(named)
        ):
            raise GarbageCollectionError(
                "gc-record-invalid", "GC record changed while reading"
            )
        return content, named
    except GarbageCollectionError:
        raise
    except OSError as exc:
        raise GarbageCollectionError(
            "gc-record-invalid", "GC record cannot be read safely"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validated_generation(path: Path) -> _Generation:
    if _GENERATION_NAME_RE.fullmatch(path.name) is None:
        raise GarbageCollectionError(
            "gc-scan-incomplete", "generation inventory contains an unexpected entry"
        )
    content, metadata = _read_regular(path, limit=STATE_LIMIT)
    hex_digest = hashlib.sha256(content).hexdigest()
    if path.name != hex_digest + ".json":
        raise GarbageCollectionError(
            "gc-digest-mismatch", "generation filename does not match its bytes"
        )
    try:
        document = decode_json_object(content, limit=STATE_LIMIT)
        if encode_json_object(document) != content:
            raise ValueError("generation manifest is not canonical JSON")
        thawed = json.loads(content.decode("utf-8"))
    except Exception as exc:
        raise GarbageCollectionError(
            "gc-record-invalid", "generation manifest is invalid"
        ) from exc
    if (
        not isinstance(thawed, dict)
        or set(thawed) != {"schema", "state", "blobs"}
        or thawed.get("schema") != "mission-generation/1"
        or not isinstance(thawed.get("state"), dict)
        or not isinstance(thawed.get("blobs"), list)
    ):
        raise GarbageCollectionError(
            "gc-record-invalid", "generation manifest schema is invalid"
        )
    return _Generation(
        "sha256:" + hex_digest,
        path,
        _identity(metadata),
        metadata.st_mtime,
        content,
    )


def _scan_generations(repository) -> tuple[_Generation, ...]:
    generations = tuple(
        _validated_generation(path)
        for path in _scan_directory(repository.root / "generations")
    )
    for generation in generations:
        # Reuse the U2 manifest parser so GC cannot accept a looser generation
        # shape than the authoritative reader.
        repository._manifest_records(generation.manifest_bytes)
    return generations


def _scan_quarantine(root: Path) -> tuple[_Quarantine, ...]:
    records = []
    for path in _scan_directory(root / "transactions" / "quarantine"):
        if _GENERATION_NAME_RE.fullmatch(path.name) is None:
            raise GarbageCollectionError(
                "gc-scan-incomplete", "quarantine inventory contains an unexpected entry"
            )
        content, metadata = _read_regular(path, limit=STATE_LIMIT)
        actual = hashlib.sha256(content).hexdigest()
        expected = path.name[:-5]
        records.append(
            _Quarantine(
                "sha256:" + expected,
                path,
                _identity(metadata),
                metadata.st_ctime,
                actual == expected,
            )
        )
    return tuple(records)


def _archive_generation_roots(root: Path) -> frozenset[str]:
    archive = root / "archive"
    try:
        archive.lstat()
    except FileNotFoundError:
        return frozenset()
    except OSError as exc:
        raise GarbageCollectionError(
            "gc-root-ambiguous", "archive root cannot be inspected"
        ) from exc
    roots = set()
    for bundle in _scan_directory(archive):
        try:
            metadata = bundle.lstat()
        except OSError as exc:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive entry cannot be inspected"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive inventory contains a link"
            )
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        pointer = bundle / "current.json"
        try:
            pointer.lstat()
        except FileNotFoundError:
            # Legacy archive entries have no immutable pointer and therefore no
            # v5 generation reference to retain.
            continue
        except OSError as exc:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive pointer cannot be inspected"
            ) from exc
        validation = validate_worktree_archive_bundle(bundle)
        if validation.status != "valid" or validation.state is None:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive pointer cannot be validated"
            )
        reference = validation.state.get("state_generation")
        # Legacy archive state has no v5 reference. When the compatibility
        # archive carries one, it is a retention root and must be exact; an
        # ambiguous optional value is never treated as "not referenced".
        if reference is None:
            continue
        if not isinstance(reference, dict) or set(reference) != {"digest", "path", "size"}:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive generation reference is ambiguous"
            )
        digest = reference.get("digest")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or reference.get("path")
            != "generations/" + digest.removeprefix("sha256:") + ".json"
            or type(reference.get("size")) is not int
            or reference["size"] < 0
        ):
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive generation reference is invalid"
            )
        referenced_path = root / reference["path"]
        content, metadata = _read_regular(referenced_path, limit=STATE_LIMIT)
        if (
            metadata.st_size != reference["size"]
            or "sha256:" + hashlib.sha256(content).hexdigest() != digest
        ):
            raise GarbageCollectionError(
                "gc-root-ambiguous", "archive generation reference differs"
            )
        roots.add(digest)
    return frozenset(roots)


def _retention_roots(repository) -> frozenset[str]:
    from .fenced_commit import FencedCommitError

    roots = set(_archive_generation_roots(repository.root))
    snapshots = []
    for path in _scan_directory(repository.root / "sessions"):
        if _SESSION_NAME_RE.fullmatch(path.name) is None:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "session head inventory contains an unexpected entry"
            )
        session_id = path.name[:-5]
        head, head_bytes, head_digest = repository._read_head_unlocked(session_id)
        if head is None or head_bytes is None or head_digest is None:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "session head disappeared during root scan"
            )
        snapshot = repository._read_snapshot_from_head_unlocked(
            session_id, head, head_bytes, head_digest
        )
        snapshots.append(snapshot)
        roots.add(snapshot.head.state_generation.digest)

    commit_facts = []
    for path in _scan_directory(repository.root / "commits"):
        if _COMMIT_NAME_RE.fullmatch(path.name) is None:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "commit inventory contains an unexpected entry"
            )
        commit_facts.append(repository._gc_commit_fact_unlocked(path.name))

    if PRIOR_SAFETY_COUNT != 1:
        raise GarbageCollectionError(
            "gc-policy-invalid", "unsupported prior safety count"
        )
    for snapshot in snapshots:
        base_head_digest = snapshot.commit.base.head_digest
        if snapshot.head.generation == 1:
            if base_head_digest is not None:
                raise GarbageCollectionError(
                    "gc-root-ambiguous", "genesis commit has a prior head"
                )
            continue
        matches = [
            fact
            for fact in commit_facts
            if fact[0].session_id == snapshot.head.session_id
            and fact[0].target_generation == snapshot.head.generation - 1
            and fact[1] == base_head_digest
        ]
        if len(matches) != 1:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "prior safety generation cannot be resolved exactly"
            )
        prior_reference = matches[0][0].generation
        prior_content, prior_metadata = _read_regular(
            repository.root / prior_reference.path,
            limit=STATE_LIMIT,
        )
        if (
            prior_metadata.st_size != prior_reference.size
            or "sha256:" + hashlib.sha256(prior_content).hexdigest()
            != prior_reference.digest
        ):
            raise GarbageCollectionError(
                "gc-root-ambiguous", "prior safety generation differs"
            )
        roots.add(prior_reference.digest)

    prepared_paths = _scan_directory(repository.root / "transactions" / "prepared")
    if len(prepared_paths) > 1:
        # _lock() makes prepared-record publication exclusive, so >1 means the
        # invariant is already broken; abort instead of guessing which record to
        # trust and contradicting the open-transaction scan.
        raise GarbageCollectionError(
            "gc-root-ambiguous", "multiple open recovery records are ambiguous"
        )
    for path in prepared_paths:
        if _PREPARE_NAME_RE.fullmatch(path.name) is None:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "prepare inventory contains an unexpected entry"
            )
        try:
            prepare, _content = repository._read_prepare_unlocked(path.name)
        except FencedCommitError as exc:
            raise GarbageCollectionError(
                "gc-root-ambiguous", "prepare record cannot be read safely"
            ) from exc
        roots.add(prepare.generation.digest)
    return frozenset(roots)


def _validate_policy(policy: RetentionPolicy) -> None:
    if not isinstance(policy, RetentionPolicy):
        raise GarbageCollectionError("gc-policy-invalid", "retention policy type is invalid")
    if (
        type(policy.grace_seconds) is not int
        or policy.grace_seconds < 0
        or type(policy.purge_grace_seconds) is not int
        or policy.purge_grace_seconds < 0
        or type(policy.dry_run) is not bool
        or type(policy.destructive) is not bool
    ):
        raise GarbageCollectionError("gc-policy-invalid", "retention policy value is invalid")
    if not policy.dry_run and not policy.destructive:
        raise GarbageCollectionError(
            "gc-destructive-flag-required", "destructive collection requires an explicit flag"
        )
    if policy.dry_run and policy.destructive:
        raise GarbageCollectionError(
            "gc-policy-invalid", "dry-run and destructive modes conflict"
        )


def _read_pinned_regular(repository, pinned, name: str) -> tuple[bytes, os.stat_result]:
    content = repository._read_pinned_file(pinned, name, limit=STATE_LIMIT)
    if content is None:
        raise GarbageCollectionError("gc-scan-changed", "GC record disappeared")
    try:
        metadata = os.stat(name, dir_fd=pinned.descriptor, follow_symlinks=False)
        repository._verify_pinned_directory(pinned)
    except OSError as exc:
        raise GarbageCollectionError(
            "gc-scan-changed", "GC record cannot be restated safely"
        ) from exc
    return content, metadata


def _fsync_pinned_directory(repository, pinned) -> None:
    try:
        os.fsync(pinned.descriptor)
        repository._verify_pinned_directory(pinned)
    except OSError as exc:
        raise GarbageCollectionError(
            "gc-write-failed", "GC directory cannot be synchronized"
        ) from exc


def collect_locked(repository, policy: RetentionPolicy) -> GCReport:
    """Collect while the repository's StateLock is held by the caller."""
    _validate_policy(policy)
    now = repository.clock()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise GarbageCollectionError("gc-policy-invalid", "GC clock must be timezone-aware")
    now_seconds = now.astimezone(timezone.utc).timestamp()

    roots = _retention_roots(repository)
    generations = _scan_generations(repository)
    quarantine = _scan_quarantine(repository.root)
    candidates = tuple(
        generation
        for generation in generations
        # A generation must satisfy every conjunct. In particular, age alone
        # never overrides a current, prior, archive, or recovery reference.
        if generation.digest not in roots
        and now_seconds - generation.modified_at > policy.grace_seconds
    )
    changed = tuple(sorted(item.digest for item in quarantine if not item.unchanged))
    purgeable = tuple(
        item
        for item in quarantine
        if item.unchanged
        and item.digest not in roots
        and now_seconds - item.quarantined_at > policy.purge_grace_seconds
    )
    candidate_ids = tuple(sorted(item.digest for item in candidates))
    if policy.dry_run:
        return GCReport(True, candidate_ids, (), (), changed)

    repository._fault("before-gc-revalidate")
    refreshed_roots = _retention_roots(repository)
    refreshed_generations = _scan_generations(repository)
    if {
        item.digest: item.identity for item in refreshed_generations
    } != {item.digest: item.identity for item in generations}:
        raise GarbageCollectionError(
            "gc-scan-changed", "generation inventory changed before quarantine"
        )

    purged = []
    quarantined = []
    changed_during_purge = set(changed)
    # StateLock coordinates repository writers, while pinned descriptors make
    # hostile parent-directory replacement unable to redirect unlink/rename to
    # a different tree between validation and the destructive syscall.
    with repository._pinned_directory("generations") as generations_pinned:
        with repository._pinned_directory(
            "transactions", "quarantine"
        ) as quarantine_pinned:
            for item in purgeable:
                # Quarantine is not authority: a generation that becomes a
                # current, archive, prior-safety, or recovery root survives.
                if item.digest in refreshed_roots:
                    continue
                content, metadata = _read_pinned_regular(
                    repository, quarantine_pinned, item.path.name
                )
                if _identity(metadata) != item.identity:
                    changed_during_purge.add(item.digest)
                    continue
                if (
                    hashlib.sha256(content).hexdigest()
                    != item.digest.removeprefix("sha256:")
                ):
                    changed_during_purge.add(item.digest)
                    continue
                repository._verify_pinned_directory(quarantine_pinned)
                try:
                    os.unlink(item.path.name, dir_fd=quarantine_pinned.descriptor)
                    _fsync_pinned_directory(repository, quarantine_pinned)
                except OSError as exc:
                    raise GarbageCollectionError(
                        "gc-write-failed", "quarantine entry cannot be purged"
                    ) from exc
                purged.append(item.digest)
                repository._fault(
                    "after-gc-purge:" + item.digest.removeprefix("sha256:")
                )

            for candidate in candidates:
                if candidate.digest in refreshed_roots:
                    continue
                content, metadata = _read_pinned_regular(
                    repository, generations_pinned, candidate.path.name
                )
                if (
                    _identity(metadata) != candidate.identity
                    or "sha256:" + hashlib.sha256(content).hexdigest()
                    != candidate.digest
                ):
                    raise GarbageCollectionError(
                        "gc-scan-changed", "generation changed before quarantine"
                    )
                repository._verify_pinned_directory(generations_pinned)
                repository._verify_pinned_directory(quarantine_pinned)
                try:
                    os.stat(
                        candidate.path.name,
                        dir_fd=quarantine_pinned.descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise GarbageCollectionError(
                        "gc-write-failed",
                        "quarantine destination cannot be inspected",
                    ) from exc
                else:
                    raise GarbageCollectionError(
                        "gc-write-failed", "quarantine destination already exists"
                    )
                repository._verify_pinned_directory(generations_pinned)
                repository._verify_pinned_directory(quarantine_pinned)
                try:
                    os.replace(
                        candidate.path.name,
                        candidate.path.name,
                        src_dir_fd=generations_pinned.descriptor,
                        dst_dir_fd=quarantine_pinned.descriptor,
                    )
                    _fsync_pinned_directory(repository, generations_pinned)
                    _fsync_pinned_directory(repository, quarantine_pinned)
                except OSError as exc:
                    raise GarbageCollectionError(
                        "gc-write-failed", "generation cannot be quarantined"
                    ) from exc
                quarantined.append(candidate.digest)
                repository._fault(
                    "after-gc-quarantine:"
                    + candidate.digest.removeprefix("sha256:")
                )

    return GCReport(
        False,
        candidate_ids,
        tuple(sorted(quarantined)),
        tuple(sorted(purged)),
        tuple(sorted(changed_during_purge)),
    )
