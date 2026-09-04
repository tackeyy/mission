"""#711 第 1 段の後半: evidence publish を A' の実行順序へ移す.

現行の v5 経路は `load()` の中で `begin` を呼ぶため、content が生成される
前に admission が終わっている。A' は read（副作用なし）→ prepare →
blobs 付き begin の順にする。
"""
import pytest

from mission_application.evidence_publication import EvidencePublicationError


def test_a_read_only_snapshot_admits_nothing():
    """The read that precedes prepare must not take the lease.

    Prepare runs a caller-supplied callback.  If admission already happened,
    that callback runs after this session has claimed the lease, which is the
    order A' exists to avoid: a foreign lease has to be refused before any
    caller code runs.
    """
    from mission_persistence.evidence_order import read_only_snapshot

    calls = []

    class _Repository:
        def read(self, session_id):
            calls.append(("read", session_id))
            return "snapshot"

        def begin(self, request):  # pragma: no cover - must not be reached
            calls.append(("begin", request))
            raise AssertionError("a read-only snapshot must not admit")

    assert read_only_snapshot(_Repository(), "session-1") == "snapshot"
    assert calls == [("read", "session-1")]


def test_the_order_is_declared_and_checked():
    """Name the order once, so a step cannot quietly move.

    The whole point of this stage is which step runs before which; leaving
    that implicit in the body of one long method is how the current order
    became hard to see.
    """
    from mission_persistence.evidence_order import EVIDENCE_STEPS

    assert EVIDENCE_STEPS == (
        "read",
        "prepare",
        "begin",
        "decide",
        "commit",
    )


def test_a_step_out_of_order_is_refused():
    from mission_persistence.evidence_order import EvidenceOrderError, OrderedEvidenceRun

    run = OrderedEvidenceRun()
    run.enter("read")
    with pytest.raises(EvidenceOrderError):
        run.enter("decide")


def test_every_step_runs_once():
    from mission_persistence.evidence_order import EvidenceOrderError, OrderedEvidenceRun

    run = OrderedEvidenceRun()
    run.enter("read")
    with pytest.raises(EvidenceOrderError):
        run.enter("read")


def test_a_completed_run_has_passed_every_step():
    from mission_persistence.evidence_order import EVIDENCE_STEPS, OrderedEvidenceRun

    run = OrderedEvidenceRun()
    for step in EVIDENCE_STEPS:
        run.enter(step)
    assert run.completed() is True


def test_a_replay_stops_after_begin_without_completing():
    """A replay decides and commits nothing, so it is not a completed run."""
    from mission_persistence.evidence_order import OrderedEvidenceRun

    run = OrderedEvidenceRun()
    for step in ("read", "prepare", "begin"):
        run.enter(step)
    run.replayed()
    assert run.completed() is False
    assert run.stopped_at() == "begin"


def test_a_replay_cannot_continue_into_decide():
    from mission_persistence.evidence_order import EvidenceOrderError, OrderedEvidenceRun

    run = OrderedEvidenceRun()
    for step in ("read", "prepare", "begin"):
        run.enter(step)
    run.replayed()
    with pytest.raises(EvidenceOrderError):
        run.enter("decide")


class _FakeRepository:
    """Stand in for the fenced repository, recording the order of calls."""

    def __init__(self, *, admits=True):
        self.calls = []
        self._admits = admits

    def read(self, session_id):
        self.calls.append("read")
        return "snapshot-" + session_id

    def begin(self, request):
        self.calls.append("begin")
        if not self._admits:
            raise AssertionError("begin must not run before prepare")
        return ("admitted", request)


def test_the_admission_carries_what_prepare_produced():
    """`begin` has to see the blobs, which only exist after prepare."""
    from mission_persistence.evidence_order import admit_with_blobs

    repository = _FakeRepository()
    admitted = admit_with_blobs(
        repository, build_request=lambda blobs: {"blobs": blobs}, blobs=("blob",)
    )
    assert admitted == ("admitted", {"blobs": ("blob",)})
    assert repository.calls == ["begin"]


def test_the_admission_refuses_to_run_without_blobs_being_decided():
    """An empty tuple is a decision; `None` means prepare never ran."""
    from mission_persistence.evidence_order import EvidenceOrderError, admit_with_blobs

    # The immutability check would also refuse `None`, so this asserts which
    # check spoke: without that, disabling the "prepare never ran" check
    # leaves the test green.
    with pytest.raises(EvidenceOrderError) as excinfo:
        admit_with_blobs(
            _FakeRepository(), build_request=lambda blobs: blobs, blobs=None
        )
    assert "prepare produced" in excinfo.value.detail

    with pytest.raises(EvidenceOrderError) as mutable:
        admit_with_blobs(
            _FakeRepository(), build_request=lambda blobs: blobs, blobs=["blob"]
        )
    assert "immutable" in mutable.value.detail


def test_reading_never_reaches_begin():
    from mission_persistence.evidence_order import read_only_snapshot

    repository = _FakeRepository(admits=False)
    read_only_snapshot(repository, "s1")
    assert repository.calls == ["read"]


def test_the_run_records_the_order_the_repository_saw():
    """Hold the sequence itself, not only that each step happened."""
    from mission_persistence.evidence_order import (
        EVIDENCE_STEPS,
        OrderedEvidenceRun,
        admit_with_blobs,
        read_only_snapshot,
    )

    repository = _FakeRepository()
    run = OrderedEvidenceRun()

    run.enter("read")
    read_only_snapshot(repository, "s1")
    run.enter("prepare")
    blobs = ("blob",)
    run.enter("begin")
    admit_with_blobs(repository, build_request=lambda b: b, blobs=blobs)
    run.enter("decide")
    run.enter("commit")

    assert repository.calls == ["read", "begin"]
    assert run.completed() is True
    assert EVIDENCE_STEPS.index("prepare") < EVIDENCE_STEPS.index("begin")


def _v5_repository(tmp_path):
    """Build the repository production uses, not a stand-in."""
    from .test_issue503_fenced_commit import _commit_cli_init

    local, _repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(
        tmp_path
    )
    return local


def test_the_request_carries_the_blobs_it_is_given(tmp_path):
    """`_request` fixed an empty blob set, so no caller could supply one."""
    from mission_persistence.fenced_commit import VerifiedBlobSet
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob

    content = b"{}"
    import hashlib

    binding = BlobBinding(
        blob_id="evidence:" + hashlib.sha256(b"build/x.json").hexdigest(),
        kind="context-manifest",
        relative_path="build/x.json",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    filled = VerifiedBlobSet((VerifiedBlob(binding, content),))
    assert repository._request(blobs=filled).blobs == filled


def test_the_request_still_defaults_to_an_empty_blob_set(tmp_path):
    """Every path but the evidence one has no blobs to give."""
    from mission_persistence.fenced_commit import VerifiedBlobSet
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    assert repository._request().blobs == VerifiedBlobSet(())


def test_the_intent_digest_follows_the_blobs(tmp_path):
    """A different blob set is a different request, not the same one."""
    from mission_persistence.fenced_commit import VerifiedBlobSet
    from mission_persistence.legacy_v4 import V5CompatibilityRepository
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob

    from mission_kernel.json_codec import decode_json_object

    # The operation id is random when it is not pinned, so two requests would
    # differ whatever the blobs did.  Pinning it leaves the blob set as the
    # only thing that can move the digest.
    command = decode_json_object(
        b'{"schema":"mission-command-intent/1","type":"compatibility-mutation"}'
    )
    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        operation_id="fixed-operation",
        operation_command=command,
        operation_command_type="compatibility-mutation",
    )
    content = b"{}"
    import hashlib

    binding = BlobBinding(
        blob_id="evidence:" + hashlib.sha256(b"build/x.json").hexdigest(),
        kind="context-manifest",
        relative_path="build/x.json",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    filled = VerifiedBlobSet((VerifiedBlob(binding, content),))
    assert repository._request().intent_digest != repository._request(
        blobs=filled
    ).intent_digest


def test_the_snapshot_read_does_not_admit_the_transaction(tmp_path):
    """The repository must expose a read that leaves the lease untaken."""
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    with repository.transaction():
        document = repository.read_snapshot()
        assert isinstance(document, dict) and document
        # `load` refuses a second admission, so an untaken lease is what lets
        # the real admission still happen after prepare has run.
        assert repository._admitted is None
        assert repository.operation_replayed is False


def test_reading_the_snapshot_twice_is_allowed(tmp_path):
    """Reading has no side effect, so it cannot be a one-shot."""
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    with repository.transaction():
        assert repository.read_snapshot() == repository.read_snapshot()


def test_the_snapshot_read_requires_an_active_transaction(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    with pytest.raises(FencedCommitError) as excinfo:
        repository.read_snapshot()
    assert "transaction" in excinfo.value.detail


def test_admitting_after_a_snapshot_read_still_works(tmp_path):
    """The read must not consume the admission it precedes."""
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    with repository.transaction():
        before = repository.read_snapshot()
        after = repository.load()
        assert after == before
        assert repository._admitted is not None


def test_the_evidence_executor_prepares_before_it_admits(tmp_path):
    """Hold the order the whole stage exists to change.

    Recording which repository calls happened, and in which order, is the
    only way to see this: both orders produce the same document, so a test
    that only checks the result passes either way.
    """
    from mission_persistence.legacy_v4 import (
        PreparedEvidenceOperation,
        V5CompatibilityRepository,
    )

    order = []

    class _Watched(V5CompatibilityRepository):
        def read_snapshot(self):
            order.append("read")
            return super().read_snapshot()

        def load(self):
            order.append("admit")
            return super().load()

    repository = _Watched(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )

    def _prepare(state):
        order.append("prepare")
        raise _StopAfterPrepare()

    with pytest.raises(_StopAfterPrepare):
        repository.execute_evidence_transition_effects(_prepare)

    assert order.index("read") < order.index("prepare"), order
    assert "admit" not in order[: order.index("prepare")], order


class _StopAfterPrepare(Exception):
    """End the run once the order under test has been observed."""
