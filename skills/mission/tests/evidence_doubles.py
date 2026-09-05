"""Shared test doubles for the v5 evidence seam (#747 item 7).

Two doubles used to live inside individual test modules and were copied
between them.  Every time the executor's contract grew (#711 added
``read_snapshot``, ``load(blobs=...)``, ``begin().request`` /
``.precondition``, ``read().head_digest`` / ``.head.generation``) each copy
had to be found and patched by hand -- five times during the first stage.
The next stage adds three more evidence routes, so the doubles live here,
once, and ``test_issue747_evidence_doubles.py`` pins them against the real
classes they stand in for.

Rules for growing them:

* Add a field here, not in a test module.  If a test needs a variation,
  give the factory a keyword argument rather than forking the class.
* Every attribute a double exposes has to exist on the production object
  it imitates.  ``PRODUCTION_SHAPES`` below lists those pairs and the
  contract test checks them, so a rename in production fails here first.
"""

from __future__ import annotations

import copy
import json
import types
from contextlib import contextmanager

ZERO_DIGEST = "sha256:" + "0" * 64

# (double, attribute path on the double) -> (production class, field names)
# The contract test resolves each production class and asserts the fields
# exist, so the double cannot drift from what the executor really reads.
PRODUCTION_SHAPES = (
    ("FakeFencedRepository.read()", "mission_persistence.fenced_commit:RepositorySnapshot",
     ("state", "head_digest", "head")),
    ("FakeFencedRepository.read().head", "mission_persistence.fenced_commit:HeadRecord",
     ("generation",)),
    ("FakeFencedRepository.begin()", "mission_persistence.fenced_commit:AdmittedSnapshot",
     ("base", "pending_lease", "request", "precondition")),
    ("FakeFencedRepository.begin().request", "mission_persistence.fenced_commit:ExecutionRequest",
     ("blobs",)),
    ("FakeFencedRepository.begin().precondition",
     "mission_persistence.fenced_commit:CommitPrecondition",
     ("base_head_digest", "base_generation")),
    ("FakeFencedRepository.begin().pending_lease", "mission_persistence.fenced_commit:PendingLease",
     ("target",)),
    ("FakeFencedRepository._stage_persistence()", "mission_persistence.fenced_commit:PreparedCommit",
     ("precondition", "state_bytes")),
)

# Private state and methods the V5 executor touches on a repository.  The
# in-memory double has to define all of them, and the real class has to
# still have them; the contract test checks both directions.
V5_EXECUTOR_SURFACE = (
    "_callback_depth",
    "_admitted",
    "_observed_base",
    "_replayed",
    "transaction",
    "load",
    "read_snapshot",
    "execute",
    "observed_base",
    "operation_replayed",
    "execute_evidence_transition_effects",
)


class FakeFencedRepository:
    """Minimal fenced backend for the V5 compatibility seam.

    The real ``LocalFencedRepository`` needs lease and generation files on
    disk.  Tests of the compatibility seam only need to observe the commit
    contract (stage -> commit -> aggregate), so this fake answers the shapes
    the seam reads and records what it was asked to commit.  The fenced
    backend itself is covered by the #542 family.
    """

    def __init__(self, state):
        self._state = state
        self.commits = []

    def read(self, _session_id):
        # #711: the seam reads before it admits, so the fake answers the same
        # state it would admit.
        return types.SimpleNamespace(
            state=self._state,
            head_digest=ZERO_DIGEST,
            head=types.SimpleNamespace(generation=0),
        )

    def begin(self, _request):
        from mission_persistence.local_uow import VerifiedBlobSet

        return types.SimpleNamespace(
            base=types.SimpleNamespace(state=self._state),
            pending_lease=types.SimpleNamespace(target=self._state.lease),
            # #711: the stage takes its effects from the admission, so the
            # fake carries the request the real snapshot holds.
            request=types.SimpleNamespace(blobs=VerifiedBlobSet(())),
            # #711: the executor compares the base it read against the one
            # it admitted, so the fake carries the same precondition shape.
            precondition=types.SimpleNamespace(
                base_head_digest=ZERO_DIGEST, base_generation=0
            ),
        )

    def _stage_persistence(self, _admitted, *, state_bytes, effects):
        assert effects == ()
        return types.SimpleNamespace(precondition=object(), state_bytes=state_bytes)

    def commit(self, prepared, precondition):
        assert precondition is prepared.precondition
        self.commits.append(json.loads(prepared.state_bytes))


def fake_fenced_repository(state):
    """Return a :class:`FakeFencedRepository` over one decoded mission state."""
    return FakeFencedRepository(state)


def in_memory_v5_repository(current, *, replayed=False):
    """Return a ``V5CompatibilityRepository`` that never reaches a backend.

    It is built without ``__init__`` and given just the private state the
    executor reads (``V5_EXECUTOR_SURFACE``).  ``execute`` refuses to run:
    these doubles exist for tests of what happens *before* a commit --
    rejection, replay, claim validation -- so reaching ``execute`` is itself
    the failure.
    """
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = object.__new__(V5CompatibilityRepository)
    repository._callback_depth = 0
    # #711: the executor reads the base before it admits, so the double has
    # to carry what that read observed.
    repository._admitted = None
    repository._observed_base = {
        "base_head_digest": ZERO_DIGEST,
        "base_generation": 0,
    }
    repository._replayed = object() if replayed else None

    @contextmanager
    def transaction():
        yield

    repository.transaction = transaction
    # #711: the executor admits with the blobs prepare produced, so ``load``
    # accepts them; it reads before admitting, so ``read_snapshot`` answers
    # the same document.  Returning the same object keeps what these tests
    # observe.
    repository.load = lambda **_kwargs: current
    repository.read_snapshot = lambda: current
    repository.execute = lambda _command: (_ for _ in ()).throw(
        AssertionError("rejected or replayed evidence must not execute")
    )
    return repository


def decoded_state(document):
    """Decode one legacy document into the kernel state the fenced fake holds."""
    from mission_kernel import decode_mission_state

    return decode_mission_state(
        json.dumps(
            copy.deepcopy(document), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
