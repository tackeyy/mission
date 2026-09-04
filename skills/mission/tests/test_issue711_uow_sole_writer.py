"""#711 第 2 段 #1: blob を持つ経路で UoW を唯一の writer にする.

第 1 段の時点では、旧 publisher が UoW の stage より先に公開ファイルを
書いていた（異系統レビューの実測で ``exists_at_uow_stage=True``）。記録は
UoW に入るが書き手が二重で、プロセス停止時に durable prepare の外へ公開が
残る。
"""
import pytest


def _prepare_context(state, publication_path="build/m.json"):
    """Build the operation production builds.

    The kernel regenerates the manifest from state and requires the claim to
    match that content, so a hand-written effect is rejected before the
    publish is ever reached.  Only the real prepare produces an acceptable
    claim.
    """
    from mission_application.evidence import prepare_context_manifest

    return prepare_context_manifest(
        state,
        now="2026-01-01T00:00:00Z",
        iteration=1,
        publication_path=publication_path,
        project_root=".",
    )


def _prepare_progress(state):
    from mission_application.evidence import prepare_progress_update

    return prepare_progress_update(
        state,
        now="2026-01-01T00:00:00Z",
        total=1,
        completed=0,
        batch_size=1,
        last_unit=None,
        artifact_path=None,
        iteration=1,
        evidence_path="progress.json",
    )


def _repository(tmp_path, *, effect_transaction):
    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    return V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        effect_transaction=effect_transaction,
    )


def _prepared(command, effects):
    from mission_application.evidence import PreparedEvidenceOperation

    return PreparedEvidenceOperation(command, effects, {})


def test_the_legacy_publisher_is_not_called_for_a_blob_bearing_path(tmp_path):
    """The proof is that it is not called, not that a file is absent.

    A file check cannot tell "the unit of work wrote it" from "the legacy
    publisher wrote it and the unit of work agreed".
    """
    import contextlib

    calls = []

    @contextlib.contextmanager
    def _spy(effects, prepared):
        calls.append(effects)
        yield effects

    repository = _repository(tmp_path, effect_transaction=_spy)
    _prepared_result, execution = repository.execute_evidence_transition_effects(
        lambda state: _prepare_context(state)
    )
    # Without this the test passes when the decision is rejected: nothing is
    # published either way, and the spy stays empty for the wrong reason.
    assert execution.decision is None or execution.decision.accepted, execution.decision
    assert calls == [], "the legacy publisher ran for a blob-bearing path"


def test_the_legacy_publisher_still_runs_for_a_path_less_command(tmp_path):
    """Artifact and progress keep the old route until the later stage."""
    import contextlib

    calls = []

    @contextlib.contextmanager
    def _spy(effects, prepared):
        calls.append(effects)
        yield effects

    repository = _repository(tmp_path, effect_transaction=_spy)
    _prepared_result, execution = repository.execute_evidence_transition_effects(
        lambda state: _prepare_progress(state)
    )
    assert execution.decision is None or execution.decision.accepted, execution.decision
    assert calls, "the path-less route lost its publisher"


class _Killed(Exception):
    """Stand in for the process ending at one fault point."""


def _publish_target(tmp_path, relative="build/m.json"):
    """Where the projection lands.

    The fixture puts the repository at ``<tmp>/repository/.mission-state``, so
    projections resolve against ``<tmp>/repository`` -- not against ``<tmp>``.
    """
    from pathlib import Path

    return Path(tmp_path) / "repository" / relative


def _run_until(tmp_path, point, *, existing=None):
    """Drive one publish and stop at `point`, returning what is on disk.

    Observing the file is the only way to tell when the publish happens: both
    orders produce the same document once the run completes.
    """
    import contextlib
    import os

    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    target = _publish_target(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    before = None
    if existing is not None:
        target.write_bytes(existing)
        before = (target.read_bytes(), os.stat(target).st_ino)

    def _injector(reached):
        if reached == point:
            raise _Killed(point)

    local.fault_injector = _injector

    @contextlib.contextmanager
    def _never(effects, prepared):  # pragma: no cover - must not run
        raise AssertionError("the legacy publisher ran on a blob-bearing path")
        yield

    repository = V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        effect_transaction=_never,
    )
    killed = False
    try:
        repository.execute_evidence_transition_effects(
            lambda state: _prepare_context(state)
        )
    except _Killed:
        killed = True
    except Exception:
        raise
    return {
        "killed": killed,
        "exists": target.exists(),
        "content": target.read_bytes() if target.exists() else None,
        "inode": os.stat(target).st_ino if target.exists() else None,
        "before": before,
    }


@pytest.mark.parametrize("point", ["after-stage", "after-prepare"])
def test_nothing_is_published_before_the_generation_is_durable(tmp_path, point):
    """A new target must not exist while the prepare is not yet durable."""
    observed = _run_until(tmp_path, point)
    assert observed["killed"], "the run did not reach %s" % point
    assert not observed["exists"], "%s already published the target" % point


@pytest.mark.parametrize("point", ["after-stage", "after-prepare"])
def test_an_existing_target_is_untouched_before_the_generation_is_durable(
    tmp_path, point
):
    """Absence alone cannot catch a publisher that replaces an existing file."""
    observed = _run_until(tmp_path, point, existing=b"previous")
    assert observed["killed"], "the run did not reach %s" % point
    assert observed["exists"]
    assert observed["content"] == b"previous", "the target was replaced early"
    assert observed["inode"] == observed["before"][1], "the target was rewritten"


def _run_until_and_recover(tmp_path, point, *, existing=None):
    """Kill at `point`, then recover, and report what survived.

    Stopping is not the interesting part: what matters is whether the publish
    that was already on disk is still there once recovery has decided the
    transaction's fate.
    """
    import contextlib
    import os

    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    local, _repo, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    target = _publish_target(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if existing is not None:
        target.write_bytes(existing)

    def _injector(reached):
        if reached == point:
            raise _Killed(point)

    local.fault_injector = _injector

    @contextlib.contextmanager
    def _never(effects, prepared):  # pragma: no cover - must not run
        raise AssertionError("the legacy publisher ran on a blob-bearing path")
        yield

    repository = V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        effect_transaction=_never,
    )
    killed = False
    try:
        repository.execute_evidence_transition_effects(
            lambda state: _prepare_context(state)
        )
    except _Killed:
        killed = True
    at_kill = target.read_bytes() if target.exists() else None

    local.fault_injector = None
    local.recover("test")
    return {
        "killed": killed,
        "at_kill": at_kill,
        "after_recovery": target.read_bytes() if target.exists() else None,
    }


def test_a_publish_interrupted_before_the_head_moved_does_not_survive(tmp_path):
    """The window this stage exists to close.

    The projection is written before the head is replaced, so a process that
    ends in between leaves a published file whose transaction never
    committed.  Recovery has to take it back.
    """
    observed = _run_until_and_recover(tmp_path, "after-projection:0")
    assert observed["killed"], "the run did not reach after-projection:0"
    assert observed["at_kill"] is not None, "the projection was not applied yet"
    assert observed["after_recovery"] is None, (
        "a publish from an uncommitted transaction survived recovery"
    )


def test_an_interrupted_publish_restores_what_was_there_before(tmp_path):
    """Taking it back means the previous content returns, not that it vanishes."""
    observed = _run_until_and_recover(
        tmp_path, "after-projection:0", existing=b"previous"
    )
    assert observed["killed"]
    assert observed["at_kill"] != b"previous", "the projection was not applied yet"
    assert observed["after_recovery"] == b"previous", (
        "recovery did not restore the base it backed up"
    )


def test_a_publish_that_reached_the_head_replace_is_kept(tmp_path):
    """Once the head names the generation, the publish is the committed one."""
    observed = _run_until_and_recover(tmp_path, "after-head-replace")
    assert observed["killed"], "the run did not reach after-head-replace"
    assert observed["at_kill"] is not None
    assert observed["after_recovery"] == observed["at_kill"], (
        "recovery discarded a committed publish"
    )
