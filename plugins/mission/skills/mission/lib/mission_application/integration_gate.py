"""Pure application policy and ports for the Issue #665 integration gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, ContextManager, Dict, Optional


class IntegrationGateFailure(RuntimeError):
    """Typed failure that a CLI adapter may render and map to a non-zero exit."""

    def __init__(self, step: int, reason: str, message: str):
        super().__init__(message)
        self.step = step
        self.reason = reason


@dataclass(frozen=True)
class PullRequestSnapshot:
    number: int
    head_sha: str
    base_ref_name: str
    state: str
    merged_at: Optional[str]
    merge_commit_sha: Optional[str]


@dataclass(frozen=True)
class IntegrationObservation:
    scope: str
    targets: str
    tree_sha: str


@dataclass(frozen=True)
class GateRuntimeServices:
    lease: Callable[[], ContextManager[None]]
    fetch_main: Callable[[], None]
    current_base_sha: Callable[[], str]
    read_pull_request: Callable[[str], PullRequestSnapshot]
    fetch_pull_request_head: Callable[[PullRequestSnapshot], None]
    integrate_and_test: Callable[
        [str, str, Callable[[str], None]],
        IntegrationObservation,
    ]
    merge_pull_request: Callable[[str, str], None]


@dataclass(frozen=True)
class IntegrationGateRequest:
    repository_root: str
    pr_ref: str
    expected_head_sha: Optional[str] = None
    expected_base_sha: Optional[str] = None


@dataclass(frozen=True)
class IntegrationGateServices:
    execute: Callable[[str, str, Optional[str], Optional[str]], Dict[str, object]]


def _valid_sha(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_open_main(
    snapshot: PullRequestSnapshot,
    *,
    expected_number: int,
    step: int,
    reason: str,
) -> None:
    if (
        snapshot.number != expected_number
        or snapshot.base_ref_name != "main"
        or snapshot.state != "OPEN"
        or not _valid_sha(snapshot.head_sha)
    ):
        raise IntegrationGateFailure(
            step,
            reason,
            "pull request identity, base, state, or head changed",
        )


def run_gate_and_merge(
    pr_ref: str,
    services: GateRuntimeServices,
    logger: Callable[[str], None],
    expected_head_sha: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
) -> Dict[str, object]:
    if not isinstance(pr_ref, str) or not pr_ref.isdigit() or int(pr_ref) <= 0:
        raise IntegrationGateFailure(1, "invalid-pr-ref", "pull request reference must be a positive number")
    if expected_head_sha is not None and not _valid_sha(expected_head_sha):
        raise IntegrationGateFailure(1, "invalid-expected-head", "expected head sha is invalid")
    if expected_base_sha is not None and not _valid_sha(expected_base_sha):
        raise IntegrationGateFailure(1, "invalid-expected-base", "expected base sha is invalid")
    expected_number = int(pr_ref)
    with services.lease():
        logger("lease=acquired")
        services.fetch_main()
        base_before = services.current_base_sha()
        logger("base_sha(step=2)={}".format(base_before))
        if expected_base_sha is not None and base_before != expected_base_sha:
            raise IntegrationGateFailure(
                2,
                "accepted-base-moved",
                "origin/main differs from the queue-verified accepted base",
            )
        initial = services.read_pull_request(pr_ref)
        _require_open_main(
            initial,
            expected_number=expected_number,
            step=3,
            reason="pull-request-not-mergeable",
        )
        if expected_head_sha is not None and initial.head_sha != expected_head_sha:
            raise IntegrationGateFailure(
                3,
                "head-changed",
                "pull request head differs from the verified queue entry",
            )
        services.fetch_pull_request_head(initial)
        observation = services.integrate_and_test(initial.head_sha, base_before, logger)
        logger("test_scope={} test_targets={}".format(observation.scope, observation.targets))
        services.fetch_main()
        base_after = services.current_base_sha()
        logger("base_sha(step=5)={}".format(base_after))
        if base_after != base_before:
            raise IntegrationGateFailure(5, "base-moved", "origin/main moved; restart from step 2")
        latest = services.read_pull_request(pr_ref)
        _require_open_main(
            latest,
            expected_number=expected_number,
            step=6,
            reason="pull-request-changed",
        )
        if latest.head_sha != initial.head_sha:
            raise IntegrationGateFailure(6, "head-changed", "pull request head changed; restart the gate")
        services.merge_pull_request(pr_ref, initial.head_sha)
        merged = services.read_pull_request(pr_ref)
        if (
            merged.number != expected_number
            or merged.base_ref_name != "main"
            or merged.state != "MERGED"
            or not merged.merged_at
            or not _valid_sha(merged.head_sha)
            or not _valid_sha(merged.merge_commit_sha or "")
            or merged.head_sha != initial.head_sha
        ):
            raise IntegrationGateFailure(
                7,
                "merge-readback-failed",
                "merge read-back did not confirm the expected repository, base, and head",
            )
        logger("merge_readback=confirmed merge_commit_sha={}".format(merged.merge_commit_sha))
        return {
            "status": "merged",
            "pr_number": merged.number,
            "base_sha": base_after,
            "head_sha": merged.head_sha,
            "tested_tree_sha": observation.tree_sha,
            "test_scope": observation.scope,
            "test_targets": observation.targets,
            "merge_commit_sha": merged.merge_commit_sha,
        }


def run_integration_gate(
    request: IntegrationGateRequest,
    services: IntegrationGateServices,
) -> Dict[str, object]:
    return services.execute(
        request.repository_root,
        request.pr_ref,
        request.expected_head_sha,
        request.expected_base_sha,
    )
