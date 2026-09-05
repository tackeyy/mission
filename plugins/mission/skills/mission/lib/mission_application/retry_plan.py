"""#747: retry しても同じ操作であり続けるための、データだけの契約.

任意 callback を受ける API では純粋性を強制できない。呼び出し側が渡すのが
データだけなら、executor は何度でも同じ結果を再現できる。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from mission_application.evidence_publication import canonical_publication_path

RETRY_PLAN_SCHEMA = "mission-retry-plan/1"


@dataclass(frozen=True)
class ContextManifestRetryPlan:
    """One context-manifest operation, described without running anything.

    ``now`` is the logical start of the operation, not the moment of the
    commit: an attempt that follows a moved base records the time the caller
    asked for the work, so the retries stay one operation rather than
    becoming several.

    ``iteration`` may be ``None``.  That is a value, not an omission: it
    means "resolve it from the snapshot this attempt sees".
    """

    now: str
    iteration: Optional[int]
    publication_path: str
    operation_id: Optional[str] = None
    _resolved_operation_id: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Normalise once, here, rather than on every attempt: a path that
        # differs between attempts would make them different operations.
        object.__setattr__(
            self,
            "publication_path",
            canonical_publication_path(self.publication_path),
        )
        object.__setattr__(
            self,
            "_resolved_operation_id",
            self.operation_id or "context-manifest:" + self.semantic_intent()[7:39],
        )

    def semantic_intent(self) -> str:
        """Return what makes this the operation it is.

        The produced bytes are deliberately absent: they only exist once an
        attempt has run, and including them would make every retry a
        different operation.
        """
        return "sha256:" + hashlib.sha256(
            json.dumps(
                {
                    "iteration": self.iteration,
                    "now": self.now,
                    "publication_path": self.publication_path,
                    "schema": RETRY_PLAN_SCHEMA,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    def resolved_operation_id(self) -> str:
        """Return the identifier every attempt of this plan shares."""
        return self._resolved_operation_id
