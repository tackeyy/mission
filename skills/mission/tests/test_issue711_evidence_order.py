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


def _effect(target="m.json", content=b"{}"):
    import hashlib

    from mission_application.artifact import EvidenceEffect

    return EvidenceEffect(
        kind="context-manifest",
        target=target,
        content=content,
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _context_command(publication_path="build/m.json", target="m.json"):
    from mission_kernel.commands import (
        ContextManifestEffectClaim,
        GenerateContextManifest,
    )

    claim = ContextManifestEffectClaim(
        "context-manifest", target, publication_path, "sha256:" + "0" * 64, 2
    )
    return GenerateContextManifest("2026-01-01T00:00:00Z", 1, claim)


def test_the_blob_set_binds_the_effect_to_the_declared_path():
    """`EvidenceEffect` names a target, not where it is published.

    The path lives on the command's claim, so the two have to be brought
    together; taking the basename from the effect alone would publish beside
    the repository instead of where the command asked.
    """
    from mission_persistence.evidence_order import blob_set_from_effects

    blobs = blob_set_from_effects((_effect(),), _context_command())
    assert len(blobs.blobs) == 1
    binding = blobs.blobs[0].binding
    assert binding.relative_path == "build/m.json"
    assert binding.digest == _effect().digest
    assert blobs.blobs[0].content == b"{}"


def test_the_blob_identifier_comes_from_the_declared_path():
    from mission_application.evidence_publication import derive_blob_id
    from mission_persistence.evidence_order import blob_set_from_effects

    blobs = blob_set_from_effects((_effect(),), _context_command())
    assert blobs.blobs[0].binding.blob_id == derive_blob_id("build/m.json")


def test_a_command_without_a_declared_path_produces_no_blobs():
    """Artifact and progress publish through their own path in this stage."""
    from mission_kernel.commands import ProgressEffectClaim, UpdateProgress
    from mission_persistence.evidence_order import blob_set_from_effects

    claim = ProgressEffectClaim("progress", "p.json", "sha256:" + "0" * 64, 2)
    command = UpdateProgress("2026-01-01T00:00:00Z", 1, 0, 1, None, None, 1, claim)
    assert blob_set_from_effects((_effect(target="p.json"),), command).blobs == ()


def test_no_effects_produce_no_blobs():
    from mission_persistence.evidence_order import blob_set_from_effects

    assert blob_set_from_effects((), _context_command()).blobs == ()


def test_an_effect_that_does_not_match_the_claim_is_refused():
    """The claim names one target; an effect for another is not its content."""
    from mission_application.evidence_publication import EvidencePublicationError
    from mission_persistence.evidence_order import blob_set_from_effects

    with pytest.raises(EvidencePublicationError):
        blob_set_from_effects((_effect(target="other.json"),), _context_command())


def test_a_path_inside_the_repository_root_is_refused():
    from mission_application.evidence_publication import EvidencePublicationError
    from mission_persistence.evidence_order import blob_set_from_effects

    with pytest.raises(EvidencePublicationError):
        blob_set_from_effects(
            (_effect(),), _context_command(publication_path=".mission-state/m.json")
        )


def test_the_admission_carries_the_prepared_blobs(tmp_path):
    """The whole stage exists so that this request is not empty.

    Observing the request `begin` receives is the only way to see it: the
    published document is identical either way.
    """
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    seen = []

    class _Watched(V5CompatibilityRepository):
        def _request(self, *, blobs=None):
            seen.append(blobs)
            return super()._request(blobs=blobs)

    repository = _Watched(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )

    def _prepare(state):
        from mission_application.evidence import PreparedEvidenceOperation

        return PreparedEvidenceOperation(_context_command(), (_effect(),), {})

    repository.execute_evidence_transition_effects(_prepare)

    assert seen, "the executor never built an admission request"
    carried = [blobs for blobs in seen if blobs is not None and blobs.blobs]
    assert carried, "the admission request carried no blobs: %r" % (seen,)
    assert carried[0].blobs[0].binding.relative_path == "build/m.json"


def test_the_published_manifest_lands_in_a_generation(raw_run_cli, tmp_path):
    """Drive the command production runs, not a state a test invented.

    An in-process attempt rejected the command before it reached `save`,
    which would have passed for the wrong reason: nothing staged, so nothing
    disagreed.  The CLI reaches the publish.
    """
    started = raw_run_cli("init", "p", "--complexity", "Standard")
    assert started.returncode == 0, started.stderr

    published = raw_run_cli(
        "context-manifest", "--iteration", "1", "--out", "cm.json"
    )
    assert published.returncode == 0, published.stderr
    assert (tmp_path / "cm.json").is_file()

    objects = tmp_path / ".mission-state" / "objects"
    assert objects.is_dir(), "the repository kept no object store"
    manifest = (tmp_path / "cm.json").read_bytes()
    import hashlib

    digest = hashlib.sha256(manifest).hexdigest()
    stored = [path for path in objects.rglob("*") if path.is_file()]
    assert any(
        digest in path.name or path.read_bytes() == manifest for path in stored
    ), "the published manifest is not part of any generation"


def test_an_absolute_path_inside_the_project_becomes_relative():
    """The CLI receives `--out` as the caller typed it, often absolute.

    A projection target is relative to the project, so the absolute form has
    to be converted rather than refused: refusing it broke every caller that
    passes a path built from a temporary directory.
    """
    from pathlib import Path

    from mission_application.evidence_publication import relative_publication_path

    root = Path("/tmp/project")
    assert relative_publication_path(root, "/tmp/project/build/m.json") == "build/m.json"
    assert relative_publication_path(root, "build/m.json") == "build/m.json"


def test_an_absolute_path_outside_the_project_is_refused():
    from pathlib import Path

    from mission_application.evidence_publication import (
        EvidencePublicationError,
        relative_publication_path,
    )

    with pytest.raises(EvidencePublicationError):
        relative_publication_path(Path("/tmp/project"), "/etc/passwd")


def test_the_repository_subtree_is_still_refused_when_absolute():
    from pathlib import Path

    from mission_application.evidence_publication import (
        EvidencePublicationError,
        relative_publication_path,
    )

    with pytest.raises(EvidencePublicationError):
        relative_publication_path(
            Path("/tmp/project"), "/tmp/project/.mission-state/m.json"
        )


def test_the_rejection_names_a_path_that_would_work():
    """A caller whose command stops working needs the way out in the message.

    The runbooks and other repositories that call this were not touched by
    the change, so the error is the only place the new rule reaches them.
    """
    from mission_application.evidence_publication import (
        EvidencePublicationError,
        canonical_publication_path,
    )

    with pytest.raises(EvidencePublicationError) as excinfo:
        canonical_publication_path(".mission-state/context/manifest.json")
    detail = excinfo.value.detail
    assert "context/manifest.json" in detail
    assert "outside" in detail


def test_the_application_relativizes_the_path_it_was_handed(tmp_path):
    """Hold the wiring, not only the helper.

    Removing the call from `prepare_context_manifest` left every test in this
    file green: the helper was still correct, but nothing used it.
    """
    from mission_application.evidence import prepare_context_manifest

    state = {
        "mission": "m",
        "mission_id": "abc12345",
        "iteration": 1,
        "session_id": "test",
    }
    prepared = prepare_context_manifest(
        state,
        now="2026-01-01T00:00:00Z",
        iteration=1,
        publication_path=str(tmp_path / "build" / "manifest.json"),
        project_root=tmp_path,
    )
    assert prepared.command.effect.publication_path == "build/manifest.json"


def test_the_application_refuses_the_repository_subtree_it_was_handed(tmp_path):
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import prepare_context_manifest

    state = {
        "mission": "m",
        "mission_id": "abc12345",
        "iteration": 1,
        "session_id": "test",
    }
    with pytest.raises(EvidenceFailure):
        prepare_context_manifest(
            state,
            now="2026-01-01T00:00:00Z",
            iteration=1,
            publication_path=str(tmp_path / ".mission-state" / "manifest.json"),
            project_root=tmp_path,
        )


def test_the_migration_hint_reaches_the_caller(raw_run_cli, tmp_path):
    """The message is the only place this change reaches a runbook.

    The helper builds a detail naming a path that would work, but the
    application replaced it with a bare code, so the CLI printed nothing the
    caller could act on.
    """
    started = raw_run_cli("init", "p", "--complexity", "Standard")
    assert started.returncode == 0, started.stderr

    refused = raw_run_cli(
        "context-manifest",
        "--iteration",
        "1",
        "--out",
        ".mission-state/context/manifest.json",
    )
    assert refused.returncode == 2
    assert "outside" in refused.stderr, refused.stderr
    assert "context/manifest.json" in refused.stderr, refused.stderr


def test_the_read_reports_the_base_it_saw(tmp_path):
    """Prepare runs against a base; admit has to check it is still that one."""
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )
    with repository.transaction():
        repository.read_snapshot()
        observed = repository.observed_base()
    assert set(observed) == {"base_head_digest", "base_generation"}
    assert isinstance(observed["base_generation"], int)
    assert observed["base_head_digest"].startswith("sha256:")


def test_the_executor_refuses_an_admission_against_a_moved_base(tmp_path):
    """A base that moved between read and admit invalidates what prepare made.

    Without this the run admits against one base while its content was
    prepared against another, and nothing notices.
    """
    from mission_persistence.evidence_order import EvidenceOrderError
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    class _Moved(V5CompatibilityRepository):
        def observed_base(self):
            base = super().observed_base()
            return dict(base, base_generation=base["base_generation"] + 99)

    repository = _Moved(
        repository=_v5_repository(tmp_path),
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
    )

    def _prepare(state):
        from mission_application.evidence import PreparedEvidenceOperation

        return PreparedEvidenceOperation(_context_command(), (_effect(),), {})

    with pytest.raises(EvidenceOrderError) as excinfo:
        repository.execute_evidence_transition_effects(_prepare)
    assert "base" in excinfo.value.detail


def test_the_retry_budget_is_the_shared_one():
    """The gate must not grow a second copy of the retry rule."""
    from pathlib import Path

    import mission_persistence.legacy_v4 as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "base_agrees(" in source
    assert "MAX_BASE_RETRIES" not in source, "the budget belongs to one module"


def test_the_failure_keeps_its_code_stable_and_its_detail_apart():
    """Callers branch on `code`; joining the detail made it unmatchable."""
    from mission_application.artifact import EvidenceFailure

    failure = EvidenceFailure("context-publication-path-invalid", "choose x")
    assert failure.code == "context-publication-path-invalid"
    assert failure.detail == "choose x"
    assert "choose x" in str(failure)
