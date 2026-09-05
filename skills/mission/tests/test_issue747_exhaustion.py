"""#747 項目 2: 予算を使い切ったとき、公開が 1 度も起きていないこと.

「publish 0」は「durable / public publish が 0」の意味である。commit 直前の
競合では private stage への書き込みは起きるので、そこを数えると必ず失敗する。
"""
import pytest


def _plan(publication_path="build/m.json"):
    from mission_application.retry_plan import ContextManifestRetryPlan

    return ContextManifestRetryPlan(
        now="2026-01-01T00:00:00Z",
        iteration=1,
        publication_path=publication_path,
    )


def _observe(tmp_path, *, moves_forever, existing=None):
    """Drive the plan with a base that keeps moving, and report what is left."""
    import contextlib
    import os
    from pathlib import Path

    from .test_issue503_fenced_commit import _commit_cli_init
    from mission_persistence.fenced_commit import (
        PRECONDITION_CAS_CODE,
        FencedCommitError,
    )
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    local, repository_root, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    target = Path(tmp_path) / "repository" / "build" / "m.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    before = None
    if existing is not None:
        target.write_bytes(existing)
        before = (target.read_bytes(), os.stat(target).st_ino)

    head_before = local.read("test").head_digest
    attempts = []
    original_begin = local.begin

    def _begin(request):
        attempts.append(request.operation_id)
        if moves_forever:
            raise FencedCommitError(PRECONDITION_CAS_CODE, "base moved")
        return original_begin(request)

    local.begin = _begin

    @contextlib.contextmanager
    def _never(effects, prepared):  # pragma: no cover - must not run
        raise AssertionError("the legacy publisher ran on a blob-bearing path")
        yield

    plan_repository = V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        effect_transaction=_never,
    )
    failure = None
    try:
        plan_repository.execute_retry_safe_evidence_plan(_plan())
    except Exception as exc:
        failure = exc

    stage_root = repository_root / "transactions"
    residue = (
        sorted(path.name for path in (stage_root / "projections").iterdir())
        if (stage_root / "projections").is_dir()
        else []
    )
    return {
        "failure": failure,
        "attempts": attempts,
        "exists": target.exists(),
        "content": target.read_bytes() if target.exists() else None,
        "inode": os.stat(target).st_ino if target.exists() else None,
        "before": before,
        "head_after": local.read("test").head_digest,
        "head_before": head_before,
        "projection_residue": residue,
    }


def test_a_base_that_never_settles_publishes_nothing(tmp_path):
    # The entry point converts exhaustion into the failure the CLI reports
    # through a controlled exit, so that is the type callers see.
    from mission_application.artifact import EvidenceFailure

    observed = _observe(tmp_path, moves_forever=True)
    assert isinstance(observed["failure"], EvidenceFailure), observed["failure"]
    assert observed["failure"].code == "base-retry-exhausted"
    assert not observed["exists"], "a publish survived an exhausted budget"


def test_an_exhausted_budget_leaves_an_existing_file_alone(tmp_path):
    observed = _observe(tmp_path, moves_forever=True, existing=b"previous")
    assert observed["exists"]
    assert observed["content"] == b"previous"
    assert observed["inode"] == observed["before"][1], "the target was rewritten"


def test_an_exhausted_budget_leaves_the_head_where_it_was(tmp_path):
    observed = _observe(tmp_path, moves_forever=True)
    assert observed["head_after"] == observed["head_before"]


def test_an_exhausted_budget_leaves_no_stage_residue(tmp_path):
    observed = _observe(tmp_path, moves_forever=True)
    assert observed["projection_residue"] == []


def test_every_attempt_carries_the_same_operation(tmp_path):
    """Three attempts are one operation, not three."""
    observed = _observe(tmp_path, moves_forever=True)
    assert len(observed["attempts"]) == 3
    assert len(set(observed["attempts"])) == 1, observed["attempts"]
