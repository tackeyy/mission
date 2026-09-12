"""Where a projection may be written, decided once (#747 P1).

Three places asked the same question and each wrote its own answer: the
publication contract in the application layer and the two path resolvers in
persistence.  The answers agree today, but they are three copies, and the
next change has to find all three.  #761 added a further condition to the two
in persistence and wrote it at three call sites there; the application layer,
which asks the same question, did not get it.

**This decides only what the parts can tell.**  Whether the input is a string
at all, and what a refusal is called, stay with the caller: the application
layer refuses a non-string with its own message, while persistence lets
``PurePosixPath`` raise.  Folding those in here would change which exception
each caller raises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Union

__all__ = [
    "ProjectionRejection",
    "resolve_internal_archive_path",
    "resolve_projection_path",
]

_RELATIVE_SEGMENTS = frozenset({"", ".", ".."})

# #747 3a: the one name ``update-progress`` may write, spelled once.  The
# generator fills the mission id with ``sha256(mission)[:16]`` truncated to
# eight, so the accepted alphabet is that of a hex digest and nothing wider --
# plus the literal the generator substitutes when the state carries no mission
# id at all.  A name outside this shape did not come from the generator, and
# is refused rather than escaped: escaping would give one mission two file
# names, and the checkpoints written under the old one would not be found.
_ARCHIVE_DIRECTORY = "archive"
_PROGRESS_BASENAME_RE = re.compile(
    r"\Aiter-[0-9]+-(?:[0-9a-f]{1,8}|unknown)-progress\.md\Z"
)


@dataclass(frozen=True)
class ProjectionRejection:
    """Why the parts cannot name a projection, in the caller's own terms.

    ``reason`` is one of ``absolute``, ``empty-or-relative-segment`` and
    ``inside-root``.  The caller turns it into its own exception and wording.
    """

    reason: str


def resolve_projection_path(
    candidate: PurePosixPath, *, root_name: str
) -> Union[ProjectionRejection, tuple[str, ...]]:
    """Return the parts a projection may be written to, or why it may not.

    ``candidate`` is already parsed: every caller builds it the way it always
    did, so a caller that refuses a non-string keeps refusing it, and one that
    lets ``PurePosixPath`` raise keeps raising.
    """
    if candidate.is_absolute():
        return ProjectionRejection("absolute")
    parts = candidate.parts
    if not parts or any(part in _RELATIVE_SEGMENTS for part in parts):
        return ProjectionRejection("empty-or-relative-segment")
    if parts[0] == root_name:
        return ProjectionRejection("inside-root")
    return parts


def resolve_internal_archive_path(
    candidate: PurePosixPath, *, root_name: str
) -> Union[ProjectionRejection, tuple[str, ...]]:
    """Return the parts of a generated file written inside the repository.

    This is the other half of :func:`resolve_projection_path`, not a relaxation
    of it: that one asks whether a path may go *outside* the repository, and
    this one asks whether it is the single in-root destination a generated
    file may take.  A path is accepted by at most one of them.

    ``archive/`` is shared -- worktree archives, compaction output and review
    evidence live there too, and the unit of work's ``transactions/`` and
    ``commits/`` sit beside it -- so the whole name is checked, not the prefix.
    A directory allowed by prefix would grow silently as the repository gains
    subtrees, and the growth would be the hole.
    """
    if candidate.is_absolute():
        return ProjectionRejection("absolute")
    parts = candidate.parts
    if not parts or any(part in _RELATIVE_SEGMENTS for part in parts):
        return ProjectionRejection("empty-or-relative-segment")
    if parts[0] != root_name:
        # Not inside the repository at all.  ``resolve_projection_path``
        # answers for those, including the alias spellings #761 closed: a
        # different spelling of the root is not this rule's to accept.
        return ProjectionRejection("outside-root")
    if (
        len(parts) != 3
        or parts[1] != _ARCHIVE_DIRECTORY
        or _PROGRESS_BASENAME_RE.match(parts[2]) is None
    ):
        return ProjectionRejection("not-the-progress-archive")
    return parts
