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

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Union

__all__ = [
    "ProjectionRejection",
    "progress_mission_segment",
    "resolve_internal_artifact_path",
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

_PROGRESS_MISSION_SEGMENT_RE = re.compile(r"\A[0-9a-f]{1,8}\Z")
_PROGRESS_ABSENT_MISSION_SEGMENT = "unknown"
_ARTIFACT_DIRECTORY = "artifacts"
_ARTIFACT_BASENAME = "mission-artifact.md"


def progress_mission_segment(mission_id: object) -> str:
    """Give the rule above a segment it accepts, for any state that can exist.

    The rule and its generator live together so that neither can move
    without the other.  Every state ``init`` writes carries a sha256 digest
    and the generic ``set`` refuses to replace the field, so the leading
    eight characters are hex on every path the CLI reaches.  The v5 decoder
    is wider -- it admits any non-empty string -- so a state from an older
    version, a migration, or one placed by hand can carry something else.
    Those states received their progress file from the legacy publisher, and
    refusing them here would take that away from their owner.

    Deriving a digest, rather than escaping per character, keeps one mission
    to one file name: an escape maps two ids onto one name only by accident,
    and changes the name whenever the escaping does.
    """
    if not mission_id:
        return _PROGRESS_ABSENT_MISSION_SEGMENT
    head = str(mission_id)[:8]
    if _PROGRESS_MISSION_SEGMENT_RE.match(head):
        return head
    return hashlib.sha256(str(mission_id).encode("utf-8")).hexdigest()[:8]


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


def resolve_internal_artifact_path(
    candidate: PurePosixPath, *, root_name: str
) -> Union[ProjectionRejection, tuple[str, ...]]:
    """Return the one in-root artifact destination the generator may use."""
    if candidate.is_absolute():
        return ProjectionRejection("absolute")
    parts = candidate.parts
    if not parts or any(part in _RELATIVE_SEGMENTS for part in parts):
        return ProjectionRejection("empty-or-relative-segment")
    if parts[0] != root_name:
        return ProjectionRejection("outside-root")
    if len(parts) != 4 or parts[1] != _ARTIFACT_DIRECTORY or parts[3] != _ARTIFACT_BASENAME:
        return ProjectionRejection("not-the-artifact-path")
    segment = parts[2]
    if not segment or "/" in segment or "\\" in segment or segment.startswith(".") or segment != segment.rstrip():
        return ProjectionRejection("not-the-artifact-path")
    return parts
