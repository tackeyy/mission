"""Issue #502: private staging and immutable generation publication."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import generate_cli_state_bytes


PROPOSED_MAX_BLOB_COUNT = 64
PROPOSED_MAX_TOTAL_BLOB_BYTES = 16 * 1024 * 1024


def test_capture_uses_exact_cli_writer_bytes_and_binds_identity(tmp_path):
    from mission_persistence.local_uow import BlobSource, capture_verified_blob_set

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")

    captured = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="current-state",
                kind="mission-state",
                relative_path="sessions/test.json",
                source_path=state_path,
            ),
        )
    )

    assert len(captured.blobs) == 1
    blob = captured.blobs[0]
    assert blob.content == emitted
    assert blob.binding.digest == "sha256:" + hashlib.sha256(emitted).hexdigest()
    assert blob.binding.size == len(emitted)


def test_capture_rejects_path_escape_before_read(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
    )

    source = tmp_path / "evidence.json"
    source.write_bytes(b"{}")

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        capture_verified_blob_set(
            (
                BlobSource(
                    blob_id="evidence",
                    kind="review-input",
                    relative_path="../outside.json",
                    source_path=source,
                ),
            )
        )

    assert rejected.value.code == "unsafe-relative-path"


@pytest.mark.parametrize("attack", ["symlink", "fifo", "hardlink", "oversize"])
def test_capture_rejects_unsafe_or_oversize_sources(tmp_path, attack):
    from mission_kernel.errors import StrictReadError
    from mission_persistence.local_uow import BlobSource, capture_verified_blob_set

    source = tmp_path / "source.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"review":"outside"}')
    if attack == "symlink":
        source.symlink_to(outside)
    elif attack == "fifo":
        os.mkfifo(source)
    elif attack == "hardlink":
        os.link(outside, source)
    else:
        source.write_bytes(b"x" * 9)

    with pytest.raises(StrictReadError) as rejected:
        capture_verified_blob_set(
            (
                BlobSource(
                    blob_id="review-evidence",
                    kind="review-input",
                    relative_path="review/review.json",
                    source_path=source,
                    limit=8,
                ),
            )
        )

    expected = "record-too-large" if attack == "oversize" else "not-regular-single-link"
    assert rejected.value.code == expected


def test_capture_rejects_source_identity_swap_during_read(monkeypatch, tmp_path):
    import mission_persistence.strict_reader as strict_reader
    from mission_kernel.errors import StrictReadError
    from mission_persistence.local_uow import BlobSource, capture_verified_blob_set

    source = tmp_path / "source.json"
    source.write_bytes(b"old-bytes")
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"new-bytes")
    original_read = strict_reader.os.read
    swapped = False

    def swap_after_read(descriptor, size):
        nonlocal swapped
        content = original_read(descriptor, size)
        if content and not swapped:
            os.replace(replacement, source)
            swapped = True
        return content

    monkeypatch.setattr(strict_reader.os, "read", swap_after_read)
    with pytest.raises(StrictReadError) as rejected:
        capture_verified_blob_set(
            (
                BlobSource(
                    blob_id="review-evidence",
                    kind="review-input",
                    relative_path="review/review.json",
                    source_path=source,
                ),
            )
        )

    assert rejected.value.code == "identity-changed"


@pytest.mark.parametrize(
    "limit",
    [True, -1, 4 * 1024 * 1024 + 1],
    ids=["bool", "negative", "above-maximum"],
)
def test_capture_rejects_invalid_or_unbounded_source_limit_before_read(tmp_path, limit):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
    )

    missing = tmp_path / "must-not-be-read.json"
    with pytest.raises(LocalUnitOfWorkError) as rejected:
        capture_verified_blob_set(
            (
                BlobSource(
                    blob_id="review-evidence",
                    kind="review-input",
                    relative_path="review/review.json",
                    source_path=missing,
                    limit=limit,
                ),
            )
        )

    assert rejected.value.code == "blob-source-limit-invalid"


def test_capture_rejects_duplicate_blob_ids_as_a_complete_set(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
    )

    source = tmp_path / "evidence.json"
    source.write_bytes(b"{}")
    duplicated = BlobSource(
        blob_id="review-evidence",
        kind="review-input",
        relative_path="review/review.json",
        source_path=source,
    )

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        capture_verified_blob_set((duplicated, duplicated))

    assert rejected.value.code == "blob-binding-mismatch"


def test_stage_is_private_single_link_digest_bound_and_not_public(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        capture_verified_blob_set,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="current-state-evidence",
                kind="mission-state-evidence",
                relative_path="sessions/test.json",
                source_path=state_path,
            ),
        )
    )
    repository = tmp_path / "uow"

    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )

    assert staged.root.parent == repository / "transactions"
    assert staged.root.stat().st_dev == repository.stat().st_dev
    assert stat.S_IMODE(staged.root.stat().st_mode) == 0o700
    assert not (repository / "generations").exists()
    assert staged.state_path.read_bytes() == emitted
    assert staged.blob_paths[0].read_bytes() == emitted
    for directory in (staged.root, staged.state_path.parent, staged.blob_paths[0].parent):
        metadata = directory.stat()
        assert stat.S_ISDIR(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == 0o700
    for path in (staged.state_path, staged.blob_paths[0], staged.manifest_path):
        metadata = path.stat()
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert stat.S_IMODE(metadata.st_mode) == 0o600

    manifest = json.loads(staged.manifest_bytes)
    assert staged.manifest_path.read_bytes() == staged.manifest_bytes
    assert manifest["state"] == {
        "digest": "sha256:" + hashlib.sha256(emitted).hexdigest(),
        "object": staged.state_path.relative_to(staged.root).as_posix(),
        "size": len(emitted),
    }
    assert manifest["blobs"][0]["blob_id"] == "current-state-evidence"
    assert manifest["blobs"][0]["digest"] == blobs.blobs[0].binding.digest
    assert manifest["blobs"][0]["size"] == len(emitted)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "duplicate", "digest", "size", "kind", "reference"],
)
def test_stage_rejects_non_bijective_or_mismatched_effect_bindings(tmp_path, mutation):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="current-state-evidence",
                kind="mission-state-evidence",
                relative_path="sessions/test.json",
                source_path=state_path,
            ),
        )
    )
    binding = blobs.blobs[0].binding
    if mutation == "missing":
        effects = ()
    elif mutation == "extra":
        effects = (binding, replace(binding, blob_id="extra"))
    elif mutation == "duplicate":
        effects = (binding, binding)
    elif mutation == "digest":
        effects = (replace(binding, digest="sha256:" + "0" * 64),)
    elif mutation == "size":
        effects = (replace(binding, size=binding.size + 1),)
    elif mutation == "kind":
        effects = (replace(binding, kind="other"),)
    else:
        effects = (replace(binding, relative_path="sessions/other.json"),)
    repository = tmp_path / "uow"

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        stage_generation(repository, state_bytes=emitted, effects=effects, blobs=blobs)

    assert rejected.value.code == "blob-binding-mismatch"
    assert not (repository / "generations").exists()
    transactions = repository / "transactions"
    assert not transactions.exists() or list(transactions.iterdir()) == []


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "content"])
def test_stage_rejects_missing_extra_duplicate_or_mutated_captured_blobs(tmp_path, mutation):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        VerifiedBlobSet,
        capture_verified_blob_set,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    captured = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=state_path,
            ),
        )
    )
    blob = captured.blobs[0]
    if mutation == "missing":
        blobs = VerifiedBlobSet(())
    elif mutation == "extra":
        blobs = VerifiedBlobSet(
            (blob, replace(blob, binding=replace(blob.binding, blob_id="extra-evidence")))
        )
    elif mutation == "duplicate":
        blobs = VerifiedBlobSet((blob, blob))
    else:
        blobs = VerifiedBlobSet((replace(blob, content=blob.content + b"mutated"),))
    repository = tmp_path / "uow"

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        stage_generation(
            repository,
            state_bytes=emitted,
            effects=(blob.binding,),
            blobs=blobs,
        )

    assert rejected.value.code == "blob-binding-mismatch"
    transactions = repository / "transactions"
    assert not transactions.exists() or list(transactions.iterdir()) == []


def test_publish_generation_is_content_addressed_immutable_and_idempotent(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="current-state-evidence",
                kind="mission-state-evidence",
                relative_path="sessions/test.json",
                source_path=state_path,
            ),
        )
    )
    repository = tmp_path / "uow"
    first_stage = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )

    first = publish_generation(repository, first_stage)
    manifest_before = first.manifest_path.read_bytes()
    manifest_inode = first.manifest_path.stat().st_ino
    second_stage = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    second = publish_generation(repository, second_stage)

    assert first.reused is False
    assert second.reused is True
    assert first.generation_digest == second.generation_digest
    assert first.manifest_path == second.manifest_path
    assert first.manifest_path.name == first.generation_digest.removeprefix("sha256:") + ".json"
    assert first.manifest_path.read_bytes() == manifest_before
    assert first.manifest_path.stat().st_ino == manifest_inode
    assert all(path.read_bytes() for path in first.object_paths)
    for path in (first.manifest_path, *first.object_paths):
        metadata = path.stat()
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert not (repository / "current.json").exists()
    assert state_path.read_bytes() == emitted


@pytest.mark.parametrize("collision", ["object", "generation"])
def test_publish_rejects_same_content_address_with_different_bytes(tmp_path, collision):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=state_path,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    if collision == "object":
        directory = repository / "objects"
        directory.mkdir(mode=0o700)
        target = directory / (hashlib.sha256(emitted).hexdigest() + ".blob")
    else:
        directory = repository / "generations"
        directory.mkdir(mode=0o700)
        target = directory / (staged.generation_digest.removeprefix("sha256:") + ".json")
    target.write_bytes(b"competitor-bytes")

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        publish_generation(repository, staged)

    assert rejected.value.code == "immutable-generation-collision"
    assert target.read_bytes() == b"competitor-bytes"
    if collision == "object":
        assert list((repository / "generations").iterdir()) == []
    else:
        assert list((repository / "objects").iterdir()) == []


def test_publish_does_not_repair_incomplete_preexisting_generation(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=state_path,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    generations = repository / "generations"
    generations.mkdir(mode=0o700)
    manifest_path = generations / (staged.generation_digest.removeprefix("sha256:") + ".json")
    manifest_path.write_bytes(staged.manifest_bytes)

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        publish_generation(repository, staged)

    assert rejected.value.code == "immutable-generation-changed"
    assert manifest_path.read_bytes() == staged.manifest_bytes
    objects = repository / "objects"
    assert list(objects.iterdir()) == []


def test_published_manifest_references_content_addressed_public_objects(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"verified"}')
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )

    published = publish_generation(repository, staged)

    manifest = json.loads(published.manifest_path.read_bytes())
    references = [manifest["state"]["object"]]
    references.extend(item["object"] for item in manifest["blobs"])
    assert references == [
        "objects/" + hashlib.sha256(emitted).hexdigest() + ".blob",
        "objects/" + hashlib.sha256(evidence.read_bytes()).hexdigest() + ".blob",
    ]
    assert [repository / reference for reference in references] == list(published.object_paths)
    assert [path.read_bytes() for path in published.object_paths] == [
        emitted,
        evidence.read_bytes(),
    ]


def test_publish_rejects_staged_content_mutation_against_manifest(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"captured"}')
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    staged.blob_paths[0].write_bytes(b'{"review":"mutated"}')

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        publish_generation(repository, staged)

    assert rejected.value.code == "staged-object-changed"
    assert not (repository / "generations").exists()


@pytest.mark.parametrize("attack", ["symlink", "fifo", "hardlink"])
def test_publish_rejects_linked_or_non_regular_staged_objects(tmp_path, attack):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"captured"}')
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    target = staged.blob_paths[0]
    target.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(blobs.blobs[0].content)
    if attack == "symlink":
        target.symlink_to(outside)
    elif attack == "fifo":
        os.mkfifo(target)
    else:
        os.link(outside, target)

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        publish_generation(repository, staged)

    assert rejected.value.code == "staged-object-changed"
    assert not (repository / "generations").exists()


def test_publish_directory_swap_cannot_redirect_immutable_link(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"captured"}')
    blobs = local_uow.capture_verified_blob_set(
        (
            local_uow.BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = local_uow.stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    victim = tmp_path / "victim"
    victim.mkdir()
    detached = repository / "objects-detached"
    original_link = local_uow.os.link
    swapped = False

    def swap_destination_directory(*arguments, **keywords):
        nonlocal swapped
        if not swapped:
            (repository / "objects").rename(detached)
            (repository / "objects").symlink_to(victim, target_is_directory=True)
            swapped = True
        return original_link(*arguments, **keywords)

    monkeypatch.setattr(local_uow.os, "link", swap_destination_directory)
    with pytest.raises(local_uow.LocalUnitOfWorkError):
        local_uow.publish_generation(repository, staged)

    assert swapped is True
    assert list(victim.iterdir()) == []
    assert list(detached.iterdir()) == []
    assert list((repository / "generations").iterdir()) == []


def test_stage_root_swap_before_cleanup_never_deletes_unrelated_directory(
    monkeypatch,
    tmp_path,
):
    import mission_persistence.local_uow as local_uow

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    blobs = local_uow.capture_verified_blob_set(
        (
            local_uow.BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=state_path,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = local_uow.stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    displaced = staged.root.with_name(staged.root.name + "-displaced")
    attacker = tmp_path / "attacker-stage"
    attacker.mkdir()
    sentinel = attacker / "sentinel.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    original_publish = local_uow._publish_immutable_file
    swapped = False

    def publish_then_swap(source, destination, content, **kwargs):
        nonlocal swapped
        result = original_publish(source, destination, content, **kwargs)
        if destination.parent.name == "generations" and not swapped:
            staged.root.rename(displaced)
            attacker.rename(staged.root)
            swapped = True
        return result

    monkeypatch.setattr(local_uow, "_publish_immutable_file", publish_then_swap)
    with pytest.raises(local_uow.LocalUnitOfWorkError):
        local_uow.publish_generation(repository, staged)

    assert swapped is True
    assert (staged.root / "sentinel.txt").read_text(encoding="utf-8") == "must survive"
    assert displaced.is_dir()


def test_stage_manifest_excludes_source_paths_and_ambient_command_values(tmp_path):
    from mission_persistence.local_uow import BlobSource, capture_verified_blob_set, stage_generation

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    secret_marker = "secret-bearing-command-value"
    source = tmp_path / secret_marker / "evidence.json"
    source.parent.mkdir()
    source.write_bytes(b'{"review":"captured"}')
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=source,
            ),
        )
    )

    staged = stage_generation(
        tmp_path / "uow",
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )

    assert os.fsencode(source) not in staged.manifest_bytes
    assert secret_marker.encode("utf-8") not in staged.manifest_bytes


def test_stage_uses_captured_bytes_after_original_source_mutates(tmp_path):
    from mission_persistence.local_uow import BlobSource, capture_verified_blob_set, stage_generation

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"captured"}')
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    captured = blobs.blobs[0].content
    evidence.write_bytes(b'{"review":"source-mutated"}')

    staged = stage_generation(
        tmp_path / "uow",
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )

    assert staged.blob_paths[0].read_bytes() == captured
    assert staged.blob_paths[0].read_bytes() != evidence.read_bytes()


def test_failure_at_every_staging_fsync_leaves_no_public_reference(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"captured"}')
    blobs = local_uow.capture_verified_blob_set(
        (
            local_uow.BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    real_fsync = local_uow._fsync

    for fault_index in range(1, 8):
        repository = tmp_path / f"uow-{fault_index}"
        calls = 0
        fault_triggered = False

        def fail_selected_fsync(descriptor):
            nonlocal calls, fault_triggered
            calls += 1
            if calls == fault_index:
                fault_triggered = True
                raise OSError(f"staging fsync fault {fault_index}")
            real_fsync(descriptor)

        monkeypatch.setattr(local_uow, "_fsync", fail_selected_fsync)
        with pytest.raises(OSError, match="staging fsync fault"):
            local_uow.stage_generation(
                repository,
                state_bytes=emitted,
                effects=(blobs.blobs[0].binding,),
                blobs=blobs,
            )

        assert fault_triggered is True
        assert calls >= fault_index
        assert not (repository / "generations").exists()
        assert not (repository / "objects").exists()
        assert not (repository / "current.json").exists()
        transactions = repository / "transactions"
        assert not transactions.exists() or list(transactions.iterdir()) == []


def test_stage_failure_cleanup_never_recursively_deletes_swapped_root(
    monkeypatch,
    tmp_path,
):
    import mission_persistence.local_uow as local_uow

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b'{"review":"captured"}')
    blobs = local_uow.capture_verified_blob_set(
        (
            local_uow.BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    attacker = tmp_path / "attacker-stage"
    attacker.mkdir()
    (attacker / "sentinel.txt").write_text("must survive", encoding="utf-8")
    original_fsync_directory = local_uow._fsync_directory
    swapped_root = None
    displaced = None

    def swap_before_directory_fsync(path):
        nonlocal swapped_root, displaced
        if path.name == "objects" and swapped_root is None:
            swapped_root = path.parent
            displaced = swapped_root.with_name(swapped_root.name + "-displaced")
            swapped_root.rename(displaced)
            attacker.rename(swapped_root)
            raise OSError("simulated staging directory fsync failure")
        original_fsync_directory(path)

    monkeypatch.setattr(local_uow, "_fsync_directory", swap_before_directory_fsync)
    with pytest.raises(OSError, match="simulated staging directory fsync failure"):
        local_uow.stage_generation(
            tmp_path / "uow",
            state_bytes=emitted,
            effects=(blobs.blobs[0].binding,),
            blobs=blobs,
        )

    assert swapped_root is not None
    assert (swapped_root / "sentinel.txt").read_text(encoding="utf-8") == "must survive"
    assert displaced is not None and displaced.is_dir()


def test_transactions_parent_swap_before_stage_creation_fails_closed(
    monkeypatch,
    tmp_path,
):
    import mission_persistence.local_uow as local_uow

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    displaced = repository / "transactions-displaced"
    real_create = local_uow._create_private_stage_root

    def swap_parent_before_create(transactions, descriptor, identity):
        transactions.rename(displaced)
        transactions.mkdir(mode=0o700)
        return real_create(transactions, descriptor, identity)

    monkeypatch.setattr(
        local_uow,
        "_create_private_stage_root",
        swap_parent_before_create,
    )
    with pytest.raises(local_uow.LocalUnitOfWorkError) as rejected:
        local_uow.stage_generation(
            repository,
            state_bytes=emitted,
            effects=(),
            blobs=local_uow.VerifiedBlobSet(()),
        )

    assert rejected.value.code == "stage-directory-changed"
    assert displaced.is_dir()
    assert list(displaced.iterdir()) == []
    assert list((repository / "transactions").iterdir()) == []
    assert not (repository / "generations").exists()
    assert not (repository / "objects").exists()
    assert not (repository / "current.json").exists()


def test_repository_swap_before_final_fsync_fails_closed(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    displaced = tmp_path / "uow-displaced"
    real_fsync = local_uow._fsync_pinned_repository

    def swap_repository_before_fsync(*args):
        repository.rename(displaced)
        repository.mkdir(mode=0o700)
        return real_fsync(*args)

    monkeypatch.setattr(
        local_uow,
        "_fsync_pinned_repository",
        swap_repository_before_fsync,
    )
    with pytest.raises(local_uow.LocalUnitOfWorkError) as rejected:
        local_uow.stage_generation(
            repository,
            state_bytes=emitted,
            effects=(),
            blobs=local_uow.VerifiedBlobSet(()),
        )

    assert rejected.value.code == "stage-directory-changed"
    assert displaced.is_dir()
    assert list(repository.iterdir()) == []
    assert not (repository / "generations").exists()
    assert not (repository / "objects").exists()
    assert not (repository / "current.json").exists()


def test_stage_rejects_oversize_manifest_before_writing_it(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow
    from mission_persistence.local_uow import (
        BlobBinding,
        LocalUnitOfWorkError,
        VerifiedBlob,
        VerifiedBlobSet,
        stage_generation,
    )

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    empty_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
    bindings = tuple(
        BlobBinding(
            blob_id=f"evidence-{index}",
            kind="review-input",
            relative_path=f"review/{index}-" + "x" * 4000,
            digest=empty_digest,
            size=0,
        )
        for index in range(1030)
    )
    blobs = VerifiedBlobSet(tuple(VerifiedBlob(binding=binding, content=b"") for binding in bindings))
    repository = tmp_path / "uow"
    monkeypatch.setattr(local_uow, "MAX_BLOB_COUNT", len(bindings))

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        stage_generation(
            repository,
            state_bytes=emitted,
            effects=bindings,
            blobs=blobs,
        )

    assert rejected.value.code == "record-too-large"
    transactions = repository / "transactions"
    assert not transactions.exists() or list(transactions.iterdir()) == []


@pytest.mark.parametrize(
    "case",
    ["mutable-sources", "mutable-effects", "mutable-blob-set", "mutable-state"],
)
def test_public_api_rejects_mutable_container_inputs(tmp_path, case):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        VerifiedBlobSet,
        capture_verified_blob_set,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    source = BlobSource(
        blob_id="review-evidence",
        kind="review-input",
        relative_path="review/review.json",
        source_path=state_path,
    )
    if case == "mutable-sources":
        with pytest.raises(LocalUnitOfWorkError) as rejected:
            capture_verified_blob_set([source])
    else:
        blobs = capture_verified_blob_set((source,))
        effects = [blobs.blobs[0].binding] if case == "mutable-effects" else (blobs.blobs[0].binding,)
        if case == "mutable-blob-set":
            blobs = VerifiedBlobSet(list(blobs.blobs))
        state_bytes = bytearray(emitted) if case == "mutable-state" else emitted
        with pytest.raises(LocalUnitOfWorkError) as rejected:
            stage_generation(
                tmp_path / "uow",
                state_bytes=state_bytes,
                effects=effects,
                blobs=blobs,
            )

    assert rejected.value.code == "immutable-input-required"


def test_publish_rejects_same_bytes_staged_inode_replacement_after_stage(tmp_path):
    from mission_persistence.local_uow import (
        LocalUnitOfWorkError,
        VerifiedBlobSet,
        publish_generation,
        stage_generation,
    )

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(),
        blobs=VerifiedBlobSet(()),
    )
    replacement = tmp_path / "same-bytes-replacement.json"
    replacement.write_bytes(emitted)
    replacement.chmod(0o600)
    os.replace(replacement, staged.state_path)

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        publish_generation(repository, staged)

    assert rejected.value.code == "staged-object-changed"
    assert not (repository / "objects").exists()
    assert not (repository / "generations").exists()


def test_publish_rejects_same_bytes_inode_replacement_between_validation_and_link(
    monkeypatch,
    tmp_path,
):
    import mission_persistence.local_uow as local_uow

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    staged = local_uow.stage_generation(
        repository,
        state_bytes=emitted,
        effects=(),
        blobs=local_uow.VerifiedBlobSet(()),
    )
    original_publish = local_uow._publish_immutable_file
    swapped = False

    def replace_after_validation(source, destination, content, **kwargs):
        nonlocal swapped
        if source == staged.state_path and not swapped:
            replacement = tmp_path / "same-bytes-raced-replacement.json"
            replacement.write_bytes(content)
            replacement.chmod(0o600)
            os.replace(replacement, source)
            swapped = True
        return original_publish(source, destination, content, **kwargs)

    monkeypatch.setattr(local_uow, "_publish_immutable_file", replace_after_validation)
    with pytest.raises(local_uow.LocalUnitOfWorkError) as rejected:
        local_uow.publish_generation(repository, staged)

    assert swapped is True
    assert rejected.value.code == "staged-object-changed"
    assert not (repository / "objects" / (hashlib.sha256(emitted).hexdigest() + ".blob")).exists()
    assert list((repository / "generations").iterdir()) == []


def test_publish_late_object_collision_rolls_back_state_link(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
        publish_generation,
        stage_generation,
    )

    state_path, emitted = generate_cli_state_bytes(tmp_path / "cli")
    evidence = tmp_path / "evidence.json"
    evidence_bytes = b'{"review":"verified"}'
    evidence.write_bytes(evidence_bytes)
    blobs = capture_verified_blob_set(
        (
            BlobSource(
                blob_id="review-evidence",
                kind="review-input",
                relative_path="review/review.json",
                source_path=evidence,
            ),
        )
    )
    repository = tmp_path / "uow"
    staged = stage_generation(
        repository,
        state_bytes=emitted,
        effects=(blobs.blobs[0].binding,),
        blobs=blobs,
    )
    objects = repository / "objects"
    objects.mkdir(mode=0o700)
    collision = objects / (hashlib.sha256(evidence_bytes).hexdigest() + ".blob")
    collision.write_bytes(b"different-existing-object")

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        publish_generation(repository, staged)

    assert rejected.value.code == "immutable-generation-collision"
    assert collision.read_bytes() == b"different-existing-object"
    assert not (objects / (hashlib.sha256(emitted).hexdigest() + ".blob")).exists()
    assert list((repository / "generations").iterdir()) == []


def test_publish_link_interrupt_rolls_back_link_created_before_tracking(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    class InterruptAfterLink(BaseException):
        pass

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    staged = local_uow.stage_generation(
        repository,
        state_bytes=emitted,
        effects=(),
        blobs=local_uow.VerifiedBlobSet(()),
    )
    original_link = local_uow.os.link

    def link_then_interrupt(*args, **kwargs):
        original_link(*args, **kwargs)
        raise InterruptAfterLink()

    monkeypatch.setattr(local_uow.os, "link", link_then_interrupt)
    with pytest.raises(InterruptAfterLink):
        local_uow.publish_generation(repository, staged)

    state_object = repository / "objects" / (hashlib.sha256(emitted).hexdigest() + ".blob")
    assert not state_object.exists()
    assert list((repository / "generations").iterdir()) == []


def test_blob_aggregate_limits_match_reviewed_design_values():
    from mission_persistence.local_uow import MAX_BLOB_COUNT, MAX_TOTAL_BLOB_BYTES

    assert MAX_BLOB_COUNT == PROPOSED_MAX_BLOB_COUNT
    assert MAX_TOTAL_BLOB_BYTES == PROPOSED_MAX_TOTAL_BLOB_BYTES


def test_capture_and_stage_accept_exact_blob_count_limit(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    source = tmp_path / "empty.json"
    sources = tuple(
        local_uow.BlobSource(
            blob_id="evidence-" + str(index),
            kind="review-input",
            relative_path="review/" + str(index) + ".json",
            source_path=source,
        )
        for index in range(PROPOSED_MAX_BLOB_COUNT)
    )
    calls = []
    original_read = local_uow.read_stable_bytes

    def read_empty(path, *, limit):
        calls.append((path, limit))
        return b""

    monkeypatch.setattr(local_uow, "read_stable_bytes", read_empty)
    captured = local_uow.capture_verified_blob_set(sources)
    monkeypatch.setattr(local_uow, "read_stable_bytes", original_read)
    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    staged = local_uow.stage_generation(
        tmp_path / "uow",
        state_bytes=emitted,
        effects=tuple(blob.binding for blob in captured.blobs),
        blobs=captured,
    )

    assert len(captured.blobs) == PROPOSED_MAX_BLOB_COUNT
    assert len(staged.blob_paths) == PROPOSED_MAX_BLOB_COUNT
    assert len(calls) == PROPOSED_MAX_BLOB_COUNT
    assert all(limit == local_uow.STATE_LIMIT for _, limit in calls)


def test_capture_and_stage_accept_exact_total_blob_bytes_limit(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    content = b"x" * local_uow.STATE_LIMIT
    sources = tuple(
        local_uow.BlobSource(
            blob_id="evidence-" + str(index),
            kind="review-input",
            relative_path="review/" + str(index) + ".json",
            source_path=tmp_path / ("source-" + str(index) + ".json"),
        )
        for index in range(PROPOSED_MAX_TOTAL_BLOB_BYTES // len(content))
    )
    limits = []
    original_read = local_uow.read_stable_bytes

    def read_full_budget(_path, *, limit):
        limits.append(limit)
        return content

    monkeypatch.setattr(local_uow, "read_stable_bytes", read_full_budget)
    captured = local_uow.capture_verified_blob_set(sources)
    monkeypatch.setattr(local_uow, "read_stable_bytes", original_read)
    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    staged = local_uow.stage_generation(
        tmp_path / "uow",
        state_bytes=emitted,
        effects=tuple(blob.binding for blob in captured.blobs),
        blobs=captured,
    )

    assert sum(blob.binding.size for blob in captured.blobs) == PROPOSED_MAX_TOTAL_BLOB_BYTES
    assert len(staged.blob_paths) == len(sources)
    assert limits == [local_uow.STATE_LIMIT] * len(sources)


def test_capture_rejects_blob_count_over_limit_without_reading(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    sources = tuple(
        local_uow.BlobSource(
            blob_id="evidence-" + str(index),
            kind="review-input",
            relative_path="review/" + str(index) + ".json",
            source_path=tmp_path / "must-not-be-read.json",
        )
        for index in range(PROPOSED_MAX_BLOB_COUNT + 1)
    )

    def reject_read(*_args, **_kwargs):
        raise AssertionError("read_stable_bytes must not be called")

    monkeypatch.setattr(local_uow, "read_stable_bytes", reject_read)
    with pytest.raises(local_uow.LocalUnitOfWorkError) as rejected:
        local_uow.capture_verified_blob_set(sources)

    assert rejected.value.code == "blob-set-too-large"


def test_capture_applies_remaining_budget_when_total_would_exceed(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow
    from mission_kernel.errors import StrictReadError

    sources = tuple(
        local_uow.BlobSource(
            blob_id="evidence-" + str(index),
            kind="review-input",
            relative_path="review/" + str(index) + ".json",
            source_path=tmp_path / ("source-" + str(index) + ".json"),
        )
        for index in range(5)
    )
    limits = []

    def read_to_budget(_path, *, limit):
        limits.append(limit)
        if len(limits) < 4:
            return b"x" * local_uow.STATE_LIMIT
        if len(limits) == 4:
            return b"x" * (local_uow.STATE_LIMIT - 1)
        raise StrictReadError("record-too-large", "source exceeds remaining budget")

    monkeypatch.setattr(local_uow, "read_stable_bytes", read_to_budget)
    with pytest.raises(local_uow.LocalUnitOfWorkError) as rejected:
        local_uow.capture_verified_blob_set(sources)

    assert rejected.value.code == "blob-set-too-large"
    assert limits == [local_uow.STATE_LIMIT] * 4 + [1]


def test_capture_rejects_blob_count_over_proposed_aggregate_limit_before_staging(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
    )

    sources = []
    for index in range(PROPOSED_MAX_BLOB_COUNT + 1):
        source = tmp_path / ("source-" + str(index) + ".json")
        source.write_bytes(b"")
        sources.append(
            BlobSource(
                blob_id="evidence-" + str(index),
                kind="review-input",
                relative_path="review/" + str(index) + ".json",
                source_path=source,
            )
        )

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        capture_verified_blob_set(tuple(sources))

    assert rejected.value.code == "blob-set-too-large"


def test_capture_rejects_total_bytes_over_proposed_aggregate_limit_before_staging(tmp_path):
    from mission_persistence.local_uow import (
        BlobSource,
        LocalUnitOfWorkError,
        capture_verified_blob_set,
    )
    from mission_persistence.strict_reader import STATE_LIMIT

    content = b"x" * STATE_LIMIT
    source_count = PROPOSED_MAX_TOTAL_BLOB_BYTES // len(content) + 1
    assert source_count < PROPOSED_MAX_BLOB_COUNT
    sources = []
    for index in range(source_count):
        source = tmp_path / ("source-" + str(index) + ".json")
        source.write_bytes(content)
        sources.append(
            BlobSource(
                blob_id="evidence-" + str(index),
                kind="review-input",
                relative_path="review/" + str(index) + ".json",
                source_path=source,
            )
        )

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        capture_verified_blob_set(tuple(sources))

    assert rejected.value.code == "blob-set-too-large"


@pytest.mark.parametrize("limit_kind", ["count", "total-bytes"])
def test_stage_rejects_forged_verified_blob_set_over_proposed_aggregate_limits(
    tmp_path,
    limit_kind,
):
    from mission_persistence.local_uow import (
        BlobBinding,
        LocalUnitOfWorkError,
        VerifiedBlob,
        VerifiedBlobSet,
        stage_generation,
    )
    from mission_persistence.strict_reader import STATE_LIMIT

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    if limit_kind == "count":
        content = b""
        blob_count = PROPOSED_MAX_BLOB_COUNT + 1
    else:
        content = b"x" * STATE_LIMIT
        blob_count = PROPOSED_MAX_TOTAL_BLOB_BYTES // len(content) + 1
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    blobs = VerifiedBlobSet(
        tuple(
            VerifiedBlob(
                binding=BlobBinding(
                    blob_id="evidence-" + str(index),
                    kind="review-input",
                    relative_path="review/" + str(index) + ".json",
                    digest=digest,
                    size=len(content),
                ),
                content=content,
            )
            for index in range(blob_count)
        )
    )
    effects = tuple(blob.binding for blob in blobs.blobs)
    repository = tmp_path / "uow"

    with pytest.raises(LocalUnitOfWorkError) as rejected:
        stage_generation(
            repository,
            state_bytes=emitted,
            effects=effects,
            blobs=blobs,
        )

    assert rejected.value.code == "blob-set-too-large"
    assert not repository.exists()


def test_publish_rejects_inode_swap_inside_link_syscall_window(monkeypatch, tmp_path):
    import mission_persistence.local_uow as local_uow

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    staged = local_uow.stage_generation(
        repository,
        state_bytes=emitted,
        effects=(),
        blobs=local_uow.VerifiedBlobSet(()),
    )
    original_link = local_uow.os.link
    swapped = False

    def replace_staged_state_immediately_before_link(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            replacement = tmp_path / "link-window-replacement.json"
            replacement.write_bytes(emitted)
            replacement.chmod(0o600)
            os.replace(replacement, staged.state_path)
            swapped = True
        return original_link(*args, **kwargs)

    monkeypatch.setattr(
        local_uow.os,
        "link",
        replace_staged_state_immediately_before_link,
    )
    with pytest.raises(local_uow.LocalUnitOfWorkError) as rejected:
        local_uow.publish_generation(repository, staged)

    state_object = repository / "objects" / (hashlib.sha256(emitted).hexdigest() + ".blob")
    assert swapped is True
    assert rejected.value.code == "immutable-generation-changed"
    assert not state_object.exists()
    assert list((repository / "generations").iterdir()) == []


def test_publish_interrupt_after_link_return_rolls_back_untracked_link_and_descriptors(
    monkeypatch,
    tmp_path,
):
    import mission_persistence.local_uow as local_uow

    class InterruptAfterPublishedLink(BaseException):
        pass

    def descriptor_count():
        descriptor_directory = Path("/dev/fd")
        if not descriptor_directory.is_dir():
            return None
        try:
            return len(list(descriptor_directory.iterdir()))
        except OSError:
            return None

    _, emitted = generate_cli_state_bytes(tmp_path / "cli")
    repository = tmp_path / "uow"
    staged = local_uow.stage_generation(
        repository,
        state_bytes=emitted,
        effects=(),
        blobs=local_uow.VerifiedBlobSet(()),
    )
    original_publish = local_uow._publish_immutable_file
    descriptors_before = descriptor_count()

    def interrupt_after_published_link(*args, **kwargs):
        published = original_publish(*args, **kwargs)
        assert isinstance(published, local_uow._PublishedLink)
        raise InterruptAfterPublishedLink()

    monkeypatch.setattr(
        local_uow,
        "_publish_immutable_file",
        interrupt_after_published_link,
    )
    with pytest.raises(InterruptAfterPublishedLink):
        local_uow.publish_generation(repository, staged)

    descriptors_after = descriptor_count()
    state_object = repository / "objects" / (hashlib.sha256(emitted).hexdigest() + ".blob")
    if descriptors_before is None or descriptors_after is None:
        assert not state_object.exists()
    else:
        assert not state_object.exists() and descriptors_after == descriptors_before, (
            "untracked link or descriptors remained: "
            + str(descriptors_before)
            + " -> "
            + str(descriptors_after)
        )
    assert list((repository / "generations").iterdir()) == []
