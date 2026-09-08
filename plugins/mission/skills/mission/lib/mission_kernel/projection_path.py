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

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Union

__all__ = ["ProjectionRejection", "resolve_projection_path"]

_RELATIVE_SEGMENTS = frozenset({"", ".", ".."})


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
