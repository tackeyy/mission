"""#747 3a: where a generated file may go inside the repository.

``resolve_projection_path`` answers where a projection may go *outside* the
repository.  Progress writes inside it, into ``archive/``, which that rule
refuses by design.  This fixes the separate rule that says which in-root
paths a generated file may take -- and, just as importantly, which it may
not: ``archive/`` also holds worktree archives, compaction output and review
evidence, and the unit of work's own ``transactions/`` and ``commits/`` sit
beside it.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_kernel.projection_path import (  # noqa: E402
    ProjectionRejection,
    resolve_internal_archive_path,
)

ROOT_NAME = ".mission-state"


def _resolve(text):
    return resolve_internal_archive_path(PurePosixPath(text), root_name=ROOT_NAME)


# The shape the generator produces, and nothing else.
@pytest.mark.parametrize(
    "relative_path,expected",
    [
        (".mission-state/archive/iter-0-abcdef01-progress.md",
         (".mission-state", "archive", "iter-0-abcdef01-progress.md")),
        (".mission-state/archive/iter-12-0a1b2c3d-progress.md",
         (".mission-state", "archive", "iter-12-0a1b2c3d-progress.md")),
        # The generator truncates to 8, but a shorter mission id is still its
        # own output rather than someone else's file.
        (".mission-state/archive/iter-1-a-progress.md",
         (".mission-state", "archive", "iter-1-a-progress.md")),
        # The generator substitutes this when the state carries no mission id.
        (".mission-state/archive/iter-1-unknown-progress.md",
         (".mission-state", "archive", "iter-1-unknown-progress.md")),
        # ``PurePosixPath`` folds these away before the parts are read, so
        # they are the same path -- the same reading the projection rule has.
        (".mission-state/./archive/iter-1-abcdef01-progress.md",
         (".mission-state", "archive", "iter-1-abcdef01-progress.md")),
        (".mission-state//archive/iter-1-abcdef01-progress.md",
         (".mission-state", "archive", "iter-1-abcdef01-progress.md")),
    ],
)
def test_the_generator_s_own_output_resolves(relative_path, expected):
    assert _resolve(relative_path) == expected


@pytest.mark.parametrize(
    "relative_path,reason",
    [
        # Outside the repository: that is the projection rule's question.
        ("docs/x.md", "outside-root"),
        ("archive/iter-1-abcdef01-progress.md", "outside-root"),
        # Case is not identity: #761 showed the same directory answers to a
        # spelling the name check would let through.
        (".MISSION-STATE/archive/iter-1-abcdef01-progress.md", "outside-root"),
        ("/.mission-state/archive/iter-1-abcdef01-progress.md", "absolute"),
        (".mission-state/archive/../commits/x", "empty-or-relative-segment"),
        # The unit of work's own subtrees.
        (".mission-state/transactions/x", "not-the-progress-archive"),
        (".mission-state/commits/x", "not-the-progress-archive"),
        (".mission-state/state.json", "not-the-progress-archive"),
        # Inside archive/, but not this command's file.
        (".mission-state/archive/worktree-x/current.json",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-1-abcdef01-reviews.json",
         "not-the-progress-archive"),
        (".mission-state/archive/progress.md", "not-the-progress-archive"),
        (".mission-state/archive/iter--abcdef01-progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-+1-abcdef01-progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter--1-abcdef01-progress.md",
         "not-the-progress-archive"),
        # A mission id the generator cannot produce.  Refused, not escaped:
        # escaping would give one mission two names.
        (".mission-state/archive/iter-1-ABCDEF01-progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-1-UNKNOWN-progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-1-unknown2-progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-1-abcdef012-progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-1--progress.md",
         "not-the-progress-archive"),
        (".mission-state/archive/iter-1-abcdef01-progress.md.bak",
         "not-the-progress-archive"),
        # The accepted basename, but not where it is accepted.  Checking the
        # depth and the directory separately matters: each alone lets one of
        # these through.
        (".mission-state/archive/iter-1-abcdef01-progress.md/child",
         "not-the-progress-archive"),
        (".mission-state/archive/sub/iter-1-abcdef01-progress.md",
         "not-the-progress-archive"),
        (".mission-state/commits/iter-1-abcdef01-progress.md",
         "not-the-progress-archive"),
        (".mission-state/transactions/iter-1-abcdef01-progress.md",
         "not-the-progress-archive"),
        # The relative segment sits between the root and the accepted name,
        # so a rule that only looked at the ends would take it.
        (".mission-state/archive/../archive/iter-1-abcdef01-progress.md",
         "empty-or-relative-segment"),
        ("/.mission-state/archive/iter-2-abcdef01-progress.md", "absolute"),
        (".mission-state", "not-the-progress-archive"),
        (".mission-state/archive", "not-the-progress-archive"),
        ("", "empty-or-relative-segment"),
    ],
)
def test_everything_else_is_refused(relative_path, reason):
    resolved = _resolve(relative_path)
    assert isinstance(resolved, ProjectionRejection)
    assert resolved.reason == reason


def test_a_path_the_projection_rule_accepts_is_refused_here():
    """The two rules partition; neither answers the other's question."""
    from mission_kernel.projection_path import resolve_projection_path

    outside = PurePosixPath("docs/x.md")
    assert resolve_projection_path(outside, root_name=ROOT_NAME) == ("docs", "x.md")
    assert isinstance(
        resolve_internal_archive_path(outside, root_name=ROOT_NAME),
        ProjectionRejection,
    )


def test_the_progress_file_the_projection_rule_refuses_resolves_here():
    from mission_kernel.projection_path import resolve_projection_path

    inside = PurePosixPath(".mission-state/archive/iter-3-abcdef01-progress.md")
    refused = resolve_projection_path(inside, root_name=ROOT_NAME)
    assert isinstance(refused, ProjectionRejection)
    assert refused.reason == "inside-root"
    assert resolve_internal_archive_path(inside, root_name=ROOT_NAME) == (
        ".mission-state", "archive", "iter-3-abcdef01-progress.md",
    )


def test_the_generator_and_the_rule_agree(tmp_path):
    """The rule is derived from the generator, so the two cannot drift apart.

    Reading the generator here rather than restating its format is the point:
    a change to either side that the other does not follow shows up as a
    refusal of the generator's own output.
    """
    import importlib.util

    bin_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("_mission_state_probe", bin_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    for mission_id in (
        "abcdef0123456789",   # what ``mission_id()`` produces
        "0" * 16,
        None,                 # the state that has none
        "",                   # and the state whose value is empty
    ):
        for iteration in (0, 1, 12, 4096):
            produced = module._progress_archive_path(
                tmp_path, {"mission_id": mission_id}, iteration
            )
            assert resolve_internal_archive_path(
                PurePosixPath(produced), root_name=ROOT_NAME
            ) == tuple(PurePosixPath(produced).parts), produced


def test_the_generator_produces_a_hex_fragment_of_the_real_mission_id(tmp_path):
    """The alphabet the rule accepts is the one the id generator emits."""
    import importlib.util

    bin_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("_mission_state_probe2", bin_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    generated = module.mission_id("any mission text")
    assert len(generated) == 16
    assert set(generated) <= set("0123456789abcdef")
