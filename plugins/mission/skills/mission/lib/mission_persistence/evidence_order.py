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


def published_binding_type():
    """Return the binding type that also answers to the claim comparison.

    ``stage`` requires the transition effects to be the very bindings the
    blob set carries, while ``bind_transition_effects`` compares them against
    the claim by ``target``.  ``BlobBinding`` has no target, so a subclass
    carries it: an ``isinstance`` check accepts the subclass, and using one
    instance for both sides keeps the equality that ``stage`` demands.
    """
    from dataclasses import dataclass

    from mission_persistence.local_uow import BlobBinding

    global _PUBLISHED_BINDING
    if _PUBLISHED_BINDING is None:

        @dataclass(frozen=True)
        class PublishedBlobBinding(BlobBinding):
            target: str = ""

        _PUBLISHED_BINDING = PublishedBlobBinding
    return _PUBLISHED_BINDING


_PUBLISHED_BINDING = None


def blob_set_from_effects(effects, command, *, repository_root_name=None):
    """Bind each prepared effect to the path its command declared.

    ``EvidenceEffect`` names a target, not where it is published; the path
    lives on the command's claim.  Taking the basename from the effect alone
    would place the file beside the repository instead of where the command
    asked, so the two are brought together here rather than guessed apart.

    Commands whose claims carry no publication path produce nothing: in this
    stage they still publish through their own route.
    """
    from mission_application.evidence_publication import (
        EvidencePublicationError,
        REPOSITORY_ROOT_NAME,
        canonical_publication_path,
        derive_blob_id,
    )
    from mission_kernel.commands import kernel_command_type
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet

    root = REPOSITORY_ROOT_NAME if repository_root_name is None else repository_root_name
    try:
        command_type = kernel_command_type(command)
    except TypeError:
        return VerifiedBlobSet(())
    claims = _publication_claims(command, command_type)
    if not claims or not effects:
        return VerifiedBlobSet(())
    by_target = {claim.target: claim for claim in claims}
    blobs = []
    for effect in effects:
        claim = by_target.get(effect.target)
        if claim is None:
            raise EvidencePublicationError(
                "effect-claim-invalid",
                "no publication claim names the effect target %r" % (effect.target,),
            )
        canonical = canonical_publication_path(
            claim.publication_path, repository_root_name=root
        )
        blobs.append(
            VerifiedBlob(
                published_binding_type()(
                    blob_id=derive_blob_id(canonical, repository_root_name=root),
                    kind=effect.kind,
                    relative_path=canonical,
                    digest=effect.digest,
                    size=effect.size,
                    target=effect.target,
                ),
                effect.content,
            )
        )
    return VerifiedBlobSet(tuple(blobs))


def _publication_claims(command, command_type):
    """Return the claims of one command that name a publication path."""
    from mission_application.evidence_publication import (
        EFFECT_FIELDS_BY_COMMAND_TYPE,
        PATH_BEARING_COMMAND_TYPES,
    )

    if command_type not in PATH_BEARING_COMMAND_TYPES:
        return ()
    return tuple(
        getattr(command, field)
        for field in EFFECT_FIELDS_BY_COMMAND_TYPE.get(command_type, ())
    )


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
