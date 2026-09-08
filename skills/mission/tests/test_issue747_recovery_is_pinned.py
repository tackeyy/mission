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
    assert "os.fsync(pinned.descriptor)" in source


def test_every_recovery_method_opens_the_pinned_walk():
    for name in RECOVERY_METHODS:
        source = _source(name)
        if name == "_cleanup_one_projection_unlocked":
            # It receives the pinned target rather than opening one.
            assert "pinned: _PinnedProjectionTarget" in source
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
