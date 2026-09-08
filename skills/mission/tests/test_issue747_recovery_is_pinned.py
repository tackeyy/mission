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
