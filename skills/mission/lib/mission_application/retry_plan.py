"""#747: retry しても同じ操作であり続けるための、データだけの契約.

任意 callback を受ける API では純粋性を強制できない。呼び出し側が渡すのが
データだけなら、executor は何度でも同じ結果を再現できる。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from mission_application.evidence_publication import (
    EvidencePublicationError,
    canonical_publication_path,
    relative_publication_path,
)
from mission_kernel.evidence import (
    EvidenceRuleError,
    context_iteration_value,
    context_output_path_text,
    context_timestamp_text,
)
from mission_kernel.identifiers import is_token128

RETRY_PLAN_SCHEMA = "mission-retry-plan/1"


def _refuse_unless_timestamp(now: object) -> None:
    # The kernel's rule, not a restatement of it: a second copy drifted once
    # already (it let a NUL byte through that the callback route refused).
    try:
        context_timestamp_text(now)
    except EvidenceRuleError as exc:
        raise EvidencePublicationError(
            exc.code, "plan requires a usable timestamp"
        ) from exc


def _refuse_unless_iteration(iteration: object) -> None:
    if iteration is None:
        return
    try:
        context_iteration_value(iteration)
    except EvidenceRuleError as exc:
        raise EvidencePublicationError(
            exc.code, "plan iteration must be a positive integer or None"
        ) from exc


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
        # Everything is checked here, where a refusal is still a refusal.
        # An unchecked value escapes later as a TypeError, which no caller
        # handles: the callback route reports refusals as EvidenceFailure, and
        # a crash is not that.
        _refuse_unless_timestamp(self.now)
        _refuse_unless_iteration(self.iteration)
        # The record refuses anything but a Token128 as `record-invalid`,
        # after the attempts have run.  Refusing it here, by the plan's own
        # name, is what makes the caller id part of the plan contract.
        if self.operation_id is not None and not is_token128(self.operation_id):
            raise EvidencePublicationError(
                "retry-plan-invalid",
                "plan operation id must be a Token128 or None",
            )
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

    @classmethod
    def for_request(
        cls,
        *,
        now: object,
        iteration: object,
        publication_path: object,
        project_root: object = None,
        operation_id: object = None,
    ) -> "ContextManifestRetryPlan":
        """Build the plan in the order the callback route checks its input.

        That route checks the timestamp, then the iteration, then the path as
        text, and only then makes the path relative to the project.  A caller
        that gets a different refusal for the same input from the plan route
        has been sent to the wrong place, so the order is kept here rather
        than left to whoever assembles the fields.
        """
        _refuse_unless_timestamp(now)
        _refuse_unless_iteration(iteration)
        try:
            path_text = context_output_path_text(publication_path)
        except EvidenceRuleError as exc:
            raise EvidencePublicationError(
                exc.code, "context output path must be a usable file path"
            ) from exc
        if project_root is not None:
            path_text = relative_publication_path(project_root, path_text)
        return cls(
            now=now,  # type: ignore[arg-type]
            iteration=iteration,  # type: ignore[arg-type]
            publication_path=path_text,
            operation_id=operation_id,  # type: ignore[arg-type]
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
