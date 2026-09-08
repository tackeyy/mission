"""#747: recovery acts through the descriptor it opened, not the name.

Resolving a path and then acting on it by name leaves a window: the
directories along the way can be replaced between the two, and the operation
lands somewhere the check never saw.  The commit side already walks with
``O_NOFOLLOW`` and pinned inodes; recovery did not.

These fix that recovery uses the pinned route.  The structural assertions are
here because the failure mode is an operation *not* taking that route, which
no ordinary run distinguishes -- both spellings behave identically until
someone swaps a directory mid-flight.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_persistence import fenced_commit  # noqa: E402

RECOVERY_METHODS = (
    "_verify_rolled_back_projections_unlocked",
    "_rollback_projections_unlocked",
    "_cleanup_one_projection_unlocked",
)


def _source(name):
    return inspect.getsource(getattr(fenced_commit.LocalFencedRepository, name))


def test_recovery_never_resolves_the_target_by_name():
    """``_projection_target`` resolves once and returns a path; that is the window."""
    for name in RECOVERY_METHODS:
        assert "self._projection_target(" not in _source(name), name


def _reference_check_arguments(name):
    """Return the first argument of every path-taking reference check."""
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(_source(name)))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "_require_projection_ref"
            and node.args
        ):
            found.append(ast.unparse(node.args[0]))
    return found


def test_the_path_form_is_used_only_for_the_transaction_s_own_bundle():
    """The bundle is private to the transaction and named from the root.

    Those entries keep the path form; the *target* never does, because the
    target is the one the walk had to reach through directories a writer
    outside the transaction can replace.
    """
    for name in RECOVERY_METHODS:
        for argument in _reference_check_arguments(name):
            assert argument in {"after_path", "base_path"}, (name, argument)


def test_the_target_reference_check_is_always_the_pinned_one():
    for name in ("_verify_rolled_back_projections_unlocked",
                 "_rollback_projections_unlocked",
                 "_cleanup_one_projection_unlocked"):
        assert "self._require_pinned_projection_ref(" in _source(name), name


def test_the_mutating_operations_carry_a_directory_descriptor():
    source = _source("_rollback_projections_unlocked")
    assert "os.unlink(pinned.target_name, dir_fd=pinned.descriptor)" in source
    assert "dst_dir_fd=pinned.descriptor" in source
    # Syncing the directory by name would reopen it, which is the same window.
    assert "self._fsync_projection_directory(target.parent)" not in source
    # Every one of them: two after an unlink, one after the restore.  A
    # substring check passes with any single one removed.
    assert source.count("os.fsync(pinned.descriptor)") == 3


def test_every_recovery_method_opens_the_pinned_walk():
    for name in RECOVERY_METHODS:
        source = _source(name)
        if name == "_cleanup_one_projection_unlocked":
            # It receives the pinned target rather than opening one, and only
            # when the target is authoritative -- the other branch reads the
            # transaction's own bundle and has no target to reach.
            assert "pinned: Optional[_PinnedProjectionTarget]" in source
            continue
        assert "with self._pinned_projection_target(projection) as pinned:" in source, name


def test_the_pinned_helpers_read_through_the_descriptor():
    source = inspect.getsource(
        fenced_commit.LocalFencedRepository._pinned_projection_exists
    )
    assert "dir_fd=pinned.descriptor" in source
    require = inspect.getsource(
        fenced_commit.LocalFencedRepository._require_pinned_projection_ref
    )
    assert "self._read_pinned_projection_bytes(" in require
    assert "self._read_projection_bytes(" not in require


def _repository_with_projection(tmp_path):
    """A repository and one projection record whose parent exists on disk."""
    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_persistence.fenced_commit import (
        ProjectionFileRef,
        ProjectionRecord,
        _directory_identity,
    )

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    parent = local.root.parent / "build"
    parent.mkdir(parents=True, exist_ok=True)
    record = ProjectionRecord(
        after=ProjectionFileRef(
            digest="sha256:" + "0" * 64,
            identity=(0, 0, 0, 0, 0),
            name="after.blob",
            size=0,
        ),
        base=None,
        blob_id="b" * 32,
        parent_identity=_directory_identity(parent.lstat()),
        relative_path="build/m.json",
    )
    return local, record


def test_a_failure_inside_the_pin_keeps_its_own_error(tmp_path):
    """The pin maps its own failures, not the caller's.

    ``OSError`` while the caller holds the pin -- a refused unlink, a full
    filesystem -- has nothing to do with whether the parent could be pinned.
    Mapping it to ``repository-changed`` renames the fault, and recovery reads
    that name to decide what happened.
    """
    import errno

    import pytest

    from mission_persistence.fenced_commit import FencedCommitError

    local, record = _repository_with_projection(tmp_path)
    raised = PermissionError(errno.EACCES, "refused")
    with local._lock(create=True):
        with pytest.raises(PermissionError) as caught:
            with local._pinned_projection_target(record):
                raise raised
    assert caught.value is raised


def test_a_fenced_error_inside_the_pin_is_not_rewritten(tmp_path):
    import pytest

    from mission_persistence.fenced_commit import FencedCommitError

    local, record = _repository_with_projection(tmp_path)
    with local._lock(create=True):
        with pytest.raises(FencedCommitError) as caught:
            with local._pinned_projection_target(record):
                raise FencedCommitError(
                    "recovery-blocked", "the caller's own verdict"
                )
    assert caught.value.code == "recovery-blocked"


def test_the_descriptors_are_closed_even_when_the_body_raises(tmp_path):
    """The pin holds file descriptors; a failing body must not leak them."""
    import pytest

    local, record = _repository_with_projection(tmp_path)
    held = []
    with local._lock(create=True):
        with pytest.raises(RuntimeError):
            with local._pinned_projection_target(record) as pinned:
                held.extend(pinned.descriptors)
                raise RuntimeError("stop")
    assert held
    for descriptor in held:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_a_parent_that_cannot_be_pinned_still_maps(tmp_path):
    """Narrowing the mapping must not remove it from the walk itself."""
    import pytest

    from mission_persistence.fenced_commit import FencedCommitError

    local, record = _repository_with_projection(tmp_path)
    (local.root.parent / "build").rmdir()
    with local._lock(create=True):
        with pytest.raises(FencedCommitError) as caught:
            with local._pinned_projection_target(record):
                pass
    assert caught.value.code in {"projection-invalid", "repository-changed"}


def test_the_closing_verification_still_maps_its_own_failure(tmp_path):
    """Narrowing the mapping keeps it where the walk asks its own question.

    The verification that runs after the body is this walk's, not the
    caller's: if the filesystem refuses it, the parent is exactly what could
    not be confirmed, and ``repository-changed`` is the right name.
    """
    import errno

    import pytest

    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    local, record = _repository_with_projection(tmp_path)
    calls = []
    original = LocalFencedRepository._verify_pinned_projection_target

    def _spy(self, pinned):
        calls.append(pinned)
        if len(calls) > 1:
            raise OSError(errno.EIO, "the filesystem refused")
        return original(self, pinned)

    with local._lock(create=True):
        LocalFencedRepository._verify_pinned_projection_target = _spy
        try:
            with pytest.raises(FencedCommitError) as caught:
                with local._pinned_projection_target(record):
                    pass
        finally:
            LocalFencedRepository._verify_pinned_projection_target = original
    assert caught.value.code == "repository-changed"
    assert len(calls) == 2, "the closing verification did not run"


def _open_descriptor_count():
    """How many file descriptors this process holds right now."""
    return len(os.listdir("/dev/fd"))


def test_no_descriptor_leaks_on_any_path(tmp_path):
    """The pin holds directory descriptors; every exit closes them.

    The mapping above names some failures and not others.  A descriptor's
    lifetime must not depend on which name a failure got: closing inside the
    named branches leaves the ones nobody named -- ``MemoryError``, an
    interrupt -- holding two of them open.
    """
    import errno

    import pytest

    from mission_persistence.fenced_commit import FencedCommitError

    local, record = _repository_with_projection(tmp_path)

    def _measure(raise_inside):
        with local._lock(create=True):
            before = _open_descriptor_count()
            try:
                with local._pinned_projection_target(record):
                    if raise_inside is not None:
                        raise raise_inside
            except BaseException:
                pass
            return _open_descriptor_count() - before

    assert _measure(None) == 0, "the normal exit leaks"
    assert _measure(RuntimeError("stop")) == 0, "a body error leaks"
    assert _measure(OSError(errno.EACCES, "refused")) == 0, "a body OSError leaks"
    assert _measure(FencedCommitError("recovery-blocked", "verdict")) == 0
    # The exceptions the mapping does not mention are the ones that leaked.
    assert _measure(MemoryError()) == 0, "an unnamed exception leaks"
    assert _measure(KeyboardInterrupt()) == 0, "an interrupt leaks"


def test_no_descriptor_leaks_when_the_walk_itself_fails(tmp_path):
    """Construction can fail part way, with some descriptors already open."""
    import pytest

    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    local, record = _repository_with_projection(tmp_path)
    original = LocalFencedRepository._verify_pinned_projection_target

    def _raises(self, pinned):
        raise MemoryError()

    with local._lock(create=True):
        before = _open_descriptor_count()
        LocalFencedRepository._verify_pinned_projection_target = _raises
        try:
            with pytest.raises(MemoryError):
                with local._pinned_projection_target(record):
                    pass
        finally:
            LocalFencedRepository._verify_pinned_projection_target = original
        assert _open_descriptor_count() - before == 0


def test_a_parent_swapped_mid_rollback_lands_in_the_directory_that_was_opened(
    tmp_path,
):
    """The point of the pin, made deterministic.

    The fault hook inside the rollback's pinned body is the moment the window
    used to be open: the path had been resolved, the operations had not run.
    Swapping the parent there sent the restore into whatever answered to the
    name afterwards.  Through the descriptor the walk opened, the restore
    lands in the directory that was checked, and the closing verification --
    which asks the same question of the same descriptor -- refuses to call the
    transaction resolved.
    """
    import os

    import pytest

    from mission_persistence.fenced_commit import FencedCommitError

    from .test_issue504_crash_recovery import (
        _cli_mutation_from_bytes,
        _commit_cli_init,
        _kill_during_commit,
    )

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(
        tmp_path
    )
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-projection:0",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )
    assert killed.returncode == 91, killed.stderr

    original_parent = projection.parent
    detached_parent = repository.parent / "compatibility-detached"
    swapped = []

    def swap_parent(point: str) -> None:
        if point == "during-projection-rollback:0" and not swapped:
            swapped.append(point)
            original_parent.rename(detached_parent)
            original_parent.mkdir()

    local.fault_injector = swap_parent
    try:
        with pytest.raises(FencedCommitError) as blocked:
            local.recover("test")
    finally:
        local.fault_injector = None

    assert swapped, "the rollback did not reach the point the window was at"
    # The transaction is not resolved, and the prepare survives for a later
    # recovery to decide.
    assert blocked.value.code in {"recovery-ambiguous", "repository-changed"}
    assert next((repository / "transactions" / "prepared").glob("*.json")).exists()
    # The restore went into the directory the walk opened, which is now the
    # detached one -- not into whatever took over the name.
    assert (detached_parent / "state.json").read_bytes() == base_bytes
    assert not (original_parent / "state.json").exists()
    assert os.listdir(original_parent) == []


def test_a_deleted_parent_now_blocks_the_rollback(tmp_path):
    """The one place this changes what a non-competing run does.

    Before, the rollback resolved the name each time and would create the
    parent again on the way; the transaction resolved.  Now the walk has to
    open the directory the prepare recorded, and a parent that is gone -- or
    replaced by a new one with a new inode -- is not that directory, so
    recovery refuses to decide rather than restoring into a stranger.

    This is reachable without a competitor: deleting the projection directory
    is enough.  It is also how the rollforward arm has always behaved, which
    is why the two now agree.
    """
    import pytest

    from mission_persistence.fenced_commit import FencedCommitError

    from .test_issue504_crash_recovery import (
        _cli_mutation_from_bytes,
        _commit_cli_init,
        _kill_during_commit,
    )

    for removal in ("delete", "recreate"):
        root = tmp_path / removal
        root.mkdir()
        local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(
            root
        )
        clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
        target_bytes = _cli_mutation_from_bytes(
            root / "actual-cli-target",
            base_bytes,
            lease_id="fixture-lease",
            now=clock_text,
            phase="reviewing",
        )
        target_path = root / "actual-cli-target-state.json"
        target_path.write_bytes(target_bytes)
        projection = repository.parent / "compatibility" / "state.json"
        projection.parent.mkdir(parents=True)
        projection.write_bytes(base_bytes)
        killed = _kill_during_commit(
            repository,
            target_path,
            clock_text=clock_text,
            fault_point="after-projection:0",
            projection_source=target_path,
            projection_relative_path="compatibility/state.json",
        )
        assert killed.returncode == 91, killed.stderr

        for child in projection.parent.iterdir():
            child.unlink()
        projection.parent.rmdir()
        if removal == "recreate":
            projection.parent.mkdir()

        with pytest.raises(FencedCommitError) as blocked:
            local.recover("test")
        # Which of the two names it gets depends on whether anything answers
        # to the name at all; both say the transaction is not resolved.
        assert blocked.value.code in {"recovery-ambiguous", "repository-changed"}, (
            removal,
            blocked.value.code,
        )
        assert next(
            (repository / "transactions" / "prepared").glob("*.json")
        ).exists(), removal
