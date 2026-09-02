"""Pure application policy and ports for the Issue #665 integration gate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
    changeset_digest: str


@dataclass(frozen=True)
class GateRuntimeServices:
    lease: Callable[[], ContextManager[None]]
    resolve_origin_identity: Callable[[], str]
    resolve_default_branch: Callable[[], str]
    fetch_base: Callable[[str], None]
    current_base_sha: Callable[[], str]
    read_pull_request: Callable[..., PullRequestSnapshot]
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
    expected_changeset_digest: Optional[str] = None


@dataclass(frozen=True)
class IntegrationGateServices:
    execute: Callable[
        [str, str, Optional[str], Optional[str], Optional[str]],
        Dict[str, object],
    ]


@dataclass(frozen=True)
class ChangesetDigestRequest:
    repository_root: str
    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class ChangesetDigestServices:
    execute: Callable[[str, str, str], Dict[str, object]]


def _valid_changeset_digest(value: object) -> bool:
    """sha256 の小文字 16 進 64 桁だけを受理する。

    大文字・前後空白・短長を許すと、同じ変更集合が複数の表記を持つ。表記が割れると
    「一致しなかったのは表記のせいか、変更が動いたのか」を区別できなくなり、
    運用側が不一致を握り潰す方向へ倒れる。
    """
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def compute_changeset_digest(patch: bytes) -> str:
    """`git diff <merge-base>...<head>` のパッチ本文から digest を導出する。

    入力は decode しない生の bytes。decode を挟むと、不正な符号列の置換や改行変換で
    同じ変更集合が別の digest になりうる。
    """
    if not isinstance(patch, (bytes, bytearray)):
        raise TypeError("changeset patch must be bytes")
    return hashlib.sha256(bytes(patch)).hexdigest()


def _valid_sha(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_open_base(
    snapshot: PullRequestSnapshot,
    *,
    expected_number: int,
    default_branch: str,
    step: int,
    reason: str,
) -> None:
    """PR が「default branch を base に持つ open な PR」であることを要求する。

    default_branch は PR の申告値ではなく origin から解決した値である。PR 由来値を
    信じると、queue の accepted_base_sha と同じ SHA を指す非 default branch へ
    retarget することで別 branch へ merge できてしまう (queue は SHA しか持たない)。
    """
    if (
        snapshot.number != expected_number
        or snapshot.base_ref_name != default_branch
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
    expected_changeset_digest: Optional[str] = None,
) -> Dict[str, object]:
    if not isinstance(pr_ref, str) or not pr_ref.isdigit() or int(pr_ref) <= 0:
        raise IntegrationGateFailure(1, "invalid-pr-ref", "pull request reference must be a positive number")
    if expected_head_sha is not None and not _valid_sha(expected_head_sha):
        raise IntegrationGateFailure(1, "invalid-expected-head", "expected head sha is invalid")
    if expected_base_sha is not None and not _valid_sha(expected_base_sha):
        raise IntegrationGateFailure(1, "invalid-expected-base", "expected base sha is invalid")
    # digest は任意。渡さない場合は緩和が適用されず、既存の厳格な要求（base 不動・
    # head 不動）がそのまま残る。参照実装 (company-os verify-exact-head.mjs) と同じ
    # 意味論で、「渡さないと止まる」ではなく「渡さないと厳しい側に倒れる」。
    if expected_changeset_digest is not None and not _valid_changeset_digest(
        expected_changeset_digest
    ):
        raise IntegrationGateFailure(
            1,
            "invalid-expected-changeset-digest",
            "reviewed changeset digest is not a lowercase sha256 hex digest",
        )
    expected_number = int(pr_ref)
    with services.lease():
        logger("lease=acquired")
        # origin と gh の操作先を同一 identity へ固定する。GH_REPO / GH_HOST が
        # ローカル解決を上書きできるため、fetch 元と PR 操作先が乖離しうる。
        identity = services.resolve_origin_identity()
        logger("repository={}".format(identity))
        # base は PR の申告値ではなく origin から解決する (retarget 対策)
        default_branch = services.resolve_default_branch()
        logger("default_branch={} source=ls-remote-symref".format(default_branch))
        services.fetch_base(default_branch)
        base_before = services.current_base_sha()
        logger("base_sha(step=2)={}".format(base_before))
        if expected_base_sha is not None and base_before != expected_base_sha:
            raise IntegrationGateFailure(
                2,
                "accepted-base-moved",
                "origin/main differs from the queue-verified accepted base",
            )
        initial = services.read_pull_request(pr_ref, step=3)
        _require_open_base(
            initial,
            expected_number=expected_number,
            default_branch=default_branch,
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
        logger("changeset_digest={}".format(observation.changeset_digest))
        if expected_changeset_digest is not None:
            # 算出不能を「検査不要」に読み替えない。digest を渡した以上、一致を
            # 証明できない統合ツリーで merge してはならない。
            if not _valid_changeset_digest(observation.changeset_digest):
                raise IntegrationGateFailure(
                    4,
                    "changeset-digest-unavailable",
                    "integrated changeset digest could not be observed",
                )
            if observation.changeset_digest != expected_changeset_digest:
                raise IntegrationGateFailure(
                    4,
                    "changeset-digest-mismatch",
                    "changeset differs from the reviewed one; re-review before merging",
                )
        # git 操作は可変な remote 名 `origin` を使うため、identity を再解決して
        # 初回値との一致を要求する。これをしないと「fetch は B・gh は A」が成立する。
        identity_after = services.resolve_origin_identity()
        if identity_after != identity:
            raise IntegrationGateFailure(
                5,
                "origin-identity-moved",
                "origin repository identity changed; restart the gate",
            )
        default_branch_after = services.resolve_default_branch()
        if default_branch_after != default_branch:
            raise IntegrationGateFailure(
                5,
                "default-branch-moved",
                "repository default branch changed; restart the gate",
            )
        services.fetch_base(default_branch)
        base_after = services.current_base_sha()
        logger("base_sha(step=5)={}".format(base_after))
        if base_after != base_before:
            raise IntegrationGateFailure(5, "base-moved", "origin/main moved; restart from step 2")
        latest = services.read_pull_request(pr_ref, step=6)
        _require_open_base(
            latest,
            expected_number=expected_number,
            default_branch=default_branch,
            step=6,
            reason="pull-request-changed",
        )
        if latest.head_sha != initial.head_sha:
            raise IntegrationGateFailure(6, "head-changed", "pull request head changed; restart the gate")
        services.merge_pull_request(pr_ref, initial.head_sha)
        merged = services.read_pull_request(pr_ref, step=7)
        if (
            merged.number != expected_number
            or merged.base_ref_name != default_branch
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
            "changeset_digest": observation.changeset_digest,
            "merge_commit_sha": merged.merge_commit_sha,
        }


def run_changeset_digest(
    request: ChangesetDigestRequest,
    services: ChangesetDigestServices,
) -> Dict[str, object]:
    return services.execute(
        request.repository_root,
        request.base_sha,
        request.head_sha,
    )


def run_integration_gate(
    request: IntegrationGateRequest,
    services: IntegrationGateServices,
) -> Dict[str, object]:
    return services.execute(
        request.repository_root,
        request.pr_ref,
        request.expected_head_sha,
        request.expected_base_sha,
        request.expected_changeset_digest,
    )
