"""#711: A' の実行順序を 1 箇所で宣言し、逸脱を拒否する.

この段の主題は「どの手順がどの手順より先か」である。それを長い method の
本体に暗黙で持たせたことが、現在の順序を読み取りにくくしていた。順序は
ここで名前を付け、実行時に照合する。
"""

from __future__ import annotations

EVIDENCE_STEPS = ("read", "prepare", "begin", "decide", "commit")
REPLAY_STOP_STEP = "begin"


class EvidenceOrderError(Exception):
    """Refuse one evidence run whose steps did not happen in order."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def read_only_snapshot(repository: object, session_id: str):
    """Return the snapshot that precedes prepare, without admitting.

    Prepare runs a caller-supplied callback.  Admitting first would run that
    callback after this session had taken the lease, which is the order this
    stage exists to avoid: a foreign lease has to be refused before any
    caller code runs.
    """
    read = getattr(repository, "read", None)
    if not callable(read):
        raise EvidenceOrderError("repository cannot be read without admitting")
    return read(session_id)


def admit_with_blobs(repository: object, *, build_request, blobs) -> object:
    """Admit the transaction with what prepare produced.

    ``blobs`` is required rather than defaulted: an empty tuple is a decision
    that this operation generates nothing, while a missing value means
    prepare never ran.  Treating the two alike is how the current order got
    an admission with no blobs in it.
    """
    if blobs is None:
        raise EvidenceOrderError("admission requires the blobs prepare produced")
    if not isinstance(blobs, tuple):
        raise EvidenceOrderError("admission requires an immutable blob set")
    begin = getattr(repository, "begin", None)
    if not callable(begin):
        raise EvidenceOrderError("repository cannot admit a transaction")
    return begin(build_request(blobs))


class OrderedEvidenceRun:
    """Track which step of one evidence run has been entered."""

    def __init__(self) -> None:
        self._entered: list[str] = []
        self._replayed = False

    def enter(self, step: str) -> None:
        if step not in EVIDENCE_STEPS:
            raise EvidenceOrderError("unknown evidence step: %r" % (step,))
        if self._replayed:
            raise EvidenceOrderError(
                "a replay commits nothing; %r cannot follow it" % (step,)
            )
        expected = EVIDENCE_STEPS[len(self._entered)] if len(self._entered) < len(
            EVIDENCE_STEPS
        ) else None
        if step != expected:
            raise EvidenceOrderError(
                "expected %r but %r was entered" % (expected, step)
            )
        self._entered.append(step)

    def replayed(self) -> None:
        if self._entered[-1:] != [REPLAY_STOP_STEP]:
            raise EvidenceOrderError(
                "a replay is only known at %r" % (REPLAY_STOP_STEP,)
            )
        self._replayed = True

    def completed(self) -> bool:
        return not self._replayed and tuple(self._entered) == EVIDENCE_STEPS

    def stopped_at(self) -> str | None:
        return self._entered[-1] if self._entered else None
