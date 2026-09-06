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
* ``V5_EXECUTOR_SURFACE`` is compared for equality against the set derived
  from the executor's own source, so the executor cannot start reading a
  new attribute without this file changing in the same commit.
"""

from __future__ import annotations

import copy
import json
import types
from contextlib import contextmanager

ZERO_DIGEST = "sha256:" + "0" * 64

# (double, attribute path on the double) -> (production class, field names)
# The contract test checks both sides against this one declaration: every
# name has to be a ``dataclasses.fields`` entry of the production class, and
# the object the double really returns has to expose exactly these names.
# Deleting a name here therefore fails (the double still answers it), and
# so does a rename in production.
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

# Everything the V5 executor reads off ``self`` inside
# ``execute_evidence_transition_effects``.  The contract test derives the
# real set from that method's source and requires it to equal this tuple, so
# the executor cannot start reading a new attribute without this list -- and
# therefore the double -- being updated in the same change.
V5_EXECUTOR_SURFACE = (
    "_admitted",
    "_callback_guard",
    "_effect_transaction",
    "_guarded_context",
    "_reject_reentrant_entry",
    "_replayed_state_document",
    "_repository",
    "execute",
    "load",
    "observed_base",
    "operation_replayed",
    "read_snapshot",
    "transaction",
    "validate_effects",
)

# Instance attributes the executor reads that ``__init__`` would normally set.
# The double is built without ``__init__``, so it sets these itself; the
# contract test checks that every non-callable name in the surface is here.
V5_EXECUTOR_INSTANCE_STATE = (
    "_admitted",
    "_callback_depth",
    "_effect_transaction",
    "_observed_base",
    "_replayed",
    "_repository",
)


def _executor_source():
    import inspect
    import textwrap

    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    return textwrap.dedent(
        inspect.getsource(V5CompatibilityRepository.execute_evidence_transition_effects)
    )


# Attribute names through which code reaches *other* attributes by a computed
# name.  Deriving them as the accessed attribute would be wrong, so they count
# as unsupported uses of `self` instead (fail-closed).
_REFLECTIVE = frozenset({
    "__getattribute__", "__getattr__", "__setattr__", "__delattr__", "__dict__", "__class__",
})


def _self_uses(source: str):
    """Classify every use of ``self`` in one method body, by syntax not by regex.

    Returns ``(derived, unsupported)``: ``derived`` is the set of attribute
    names reached through ``self.<name>`` or ``getattr(self, "<literal>", ...)``,
    and ``unsupported`` is every other appearance of ``self`` -- an alias, a
    hand-off to a function, ``getattr(self, expr)`` with a non-literal first
    argument, ``hasattr`` / ``setattr`` -- as ``(lineno, col_offset)`` pairs.  A
    regex cannot tell ``getattr(self, "_x")`` from ``getattr(self, "_x" + y)``;
    the parser can, which is why this is not a regex.
    """
    import ast

    tree = ast.parse(source)
    function = tree.body[0]
    derived: set = set()
    unsupported: list = []
    handled: set = set()  # ids of Name nodes already accounted for
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            if node.attr in _REFLECTIVE:
                # `self.__getattribute__("_x")` / `self.__dict__["_x"]` reach an
                # attribute this walk cannot name; left unhandled so it is reported.
                continue
            derived.add(node.attr)
            handled.add(id(node.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "self"
        ):
            if (
                len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value not in _REFLECTIVE
            ):
                derived.add(node.args[1].value)
                handled.add(id(node.args[0]))
            # a non-literal name, or a reflective one (`getattr(self,
            # "__getattribute__")`), is left unhandled and reported below
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id == "self" and id(node) not in handled:
            unsupported.append((node.lineno, node.col_offset))
    # The parameter `self` itself is an ast.arg, not an ast.Name, so it never
    # appears here.  Sorted by position: ast.walk is breadth-first.
    return frozenset(derived), tuple(sorted(unsupported))


def executor_surface_from_source() -> frozenset:
    """Return every attribute of ``self`` the V5 executor's entry method touches.

    Derived by parsing the method, so the two supported spellings are exact:
    ``self.<name>`` and ``getattr(self, "<literal>", ...)``.  Any other use of
    ``self`` is reported by :func:`executor_unsupported_self_uses`, and the
    contract test requires that list to be empty -- otherwise this set could
    be complete for the spellings it understands and still miss an access.
    """
    derived, _unsupported = _self_uses(_executor_source())
    return derived


def executor_unsupported_self_uses() -> tuple:
    """Return every use of ``self`` the derivation does not understand."""
    _derived, unsupported = _self_uses(_executor_source())
    return unsupported


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


def in_memory_v5_repository(current, *, replayed=False, replayed_document=None, read_calls=None):
    """Return a ``V5CompatibilityRepository`` that never reaches a backend.

    It is built without ``__init__`` and given just the private state the
    executor reads (``V5_EXECUTOR_SURFACE``).  ``execute`` refuses to run:
    these doubles exist for tests of what happens *before* a commit --
    rejection, replay, claim validation -- so reaching ``execute`` is itself
    the failure.

    On a replay the executor reads the state the replayed operation committed
    (#747 item 6) through ``self._repository.read_operation_state`` with the
    admitted request's identity, so a replaying double carries a stub backend
    that answers ``replayed_document`` (``current`` by default) and a
    ``_replay_request``.  ``read_calls``, when given, records each read.
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
    # #747 P2: the executor reads ``.result`` / ``.intent_digest`` /
    # ``.record_version`` off the replay it holds, so the double carries the
    # production shape (a version-2 record, nothing generated).
    repository._replayed = (
        types.SimpleNamespace(
            result=types.SimpleNamespace(
                commit_digest=ZERO_DIGEST, generation=1,
                head_digest=ZERO_DIGEST, state_generation_digest=ZERO_DIGEST,
            ),
            intent_digest=ZERO_DIGEST, record_version=2, materialization=None,
        )
        if replayed else None
    )
    repository._replay_request = (
        types.SimpleNamespace(session_id="portable", operation_id="op", intent_digest=ZERO_DIGEST)
        if replayed else None
    )
    repository._session_id = "portable"
    repository._transaction_active = False

    def _read_operation_state(
        result, *, session_id, operation_id, intent_digest, record_version
    ):
        if read_calls is not None:
            read_calls.append({
                "result": result, "session_id": session_id, "operation_id": operation_id,
                "intent_digest": intent_digest, "record_version": record_version,
                "inside_transaction": repository._transaction_active,
            })
        return decoded_state(current if replayed_document is None else replayed_document)
    # Read on the legacy-publisher branch (effects without blobs).  The double
    # never reaches a publish, so ``None`` is the honest value: reaching that
    # branch then fails as ``evidence-effect-transaction-missing``, a refusal
    # the tests can name, rather than an AttributeError deep in the executor.
    repository._effect_transaction = None
    # Read through ``getattr(self, "_repository", None)`` for the root name,
    # and on a replay for ``read_operation_state``.  A non-replaying double has
    # no backend (``None`` takes the executor down the default-name path).
    repository._repository = (
        types.SimpleNamespace(root=None, read_operation_state=_read_operation_state)
        if replayed else None
    )

    @contextmanager
    def transaction():
        repository._transaction_active = True
        try:
            yield
        finally:
            repository._transaction_active = False

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
