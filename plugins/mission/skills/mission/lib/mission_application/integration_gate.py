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
    # #701: the base branch tip as the API reports it.  git's view of the same
    # branch is resolved through url.<base>.insteadOf and can therefore point
    # at a different repository; this one cannot.
    base_ref_oid: Optional[str]


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
    claimed_digest_source: Optional[str] = None


@dataclass(frozen=True)
class IntegrationGateServices:
    execute: Callable[
        [str, str, Optional[str], Optional[str], Optional[str], Optional[str]],
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


CLAIMED_DIGEST_SOURCES = ("checker-comment", "argv-manual")


def _valid_claimed_digest_source(value: object) -> bool:
    """申告値は閉じた列挙だけを受理する。

    未知の値を通すと、記録の意味が呼び出し側ごとに変わる。表記揺れ (大文字・前後空白) も
    受理しない。後から集計するとき、同じ申告が複数の表記へ割れると数えられなくなる。
    """
    return value in CLAIMED_DIGEST_SOURCES


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


def _require_agreeing_base(
    snapshot: "PullRequestSnapshot",
    base_sha: str,
    services: "GateRuntimeServices",
    default_branch: str,
    *,
    step: int,
) -> None:
    """Require git's base observation to match the one the API reports (#701).

    ``url.<base>.insteadOf`` rewrites where git resolves a URL to, so a
    verified URL is not by itself a verified destination.  ``gh`` addresses the
    repository by its verified identity and is not subject to those rules, so
    the two observations are independent.  A rewrite makes them disagree.

    A disagreement has two possible causes, and they need different answers.
    The base may simply have moved, which the gate already reports as
    ``base-moved``; or git may be resolving somewhere else.  Re-fetching tells
    them apart: if git's own view moved, this was movement, and reporting it as
    a rewriting problem would send the operator after a security ghost.

    An observation that cannot be compared -- absent, or not a sha -- is not
    treated as "nothing to check".  It stops the gate under its own reason, so
    that a missing check is distinguishable from a failed one.
    """
    if not isinstance(snapshot.base_ref_oid, str) or not _valid_sha(snapshot.base_ref_oid):
        raise IntegrationGateFailure(
            step,
            "base-observation-unusable",
            "pull request base observation is missing or unusable",
        )
    if snapshot.base_ref_oid == base_sha:
        return
    services.fetch_base(default_branch)
    if services.current_base_sha() != base_sha:
        raise IntegrationGateFailure(
            step, "base-moved", "origin/main moved; restart from step 2"
        )
    raise IntegrationGateFailure(
        step,
        "base-observation-disagrees",
        "git and the API report different base commits while git's own view is "
        "stable; check url.insteadOf rewriting",
    )


def run_gate_and_merge(
    pr_ref: str,
    services: GateRuntimeServices,
    logger: Callable[[str], None],
    expected_head_sha: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
    expected_changeset_digest: Optional[str] = None,
    claimed_digest_source: Optional[str] = None,
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
    # 申告と digest は双方向で対になる。片方だけを許すと、申告のない digest が
    # 記録なしで通るか、検証していない merge に出所だけが付く。どちらも、後から
    # 「この digest はどこから来たか」を読む側に誤った手掛かりを残す。
    #
    # **gate が記録するのは出所ではなく出所の申告である。** producer が自算した値へ
    # `checker-comment` と申告することは可能で、gate はそれを検出しない (#727)。
    if claimed_digest_source is not None and not _valid_claimed_digest_source(
        claimed_digest_source
    ):
        raise IntegrationGateFailure(
            1,
            "invalid-claimed-digest-source",
            "claimed digest source must be one of: {}".format(
                ", ".join(CLAIMED_DIGEST_SOURCES)
            ),
        )
    if expected_changeset_digest is not None and claimed_digest_source is None:
        raise IntegrationGateFailure(
            1,
            "claimed-digest-source-missing",
            "a reviewed changeset digest requires the source it was claimed to come from",
        )
    if claimed_digest_source is not None and expected_changeset_digest is None:
        raise IntegrationGateFailure(
            1,
            "reviewed-changeset-digest-missing",
            "a claimed digest source requires the reviewed changeset digest it describes",
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
        _require_agreeing_base(initial, base_before, services, default_branch, step=3)
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
        _require_agreeing_base(latest, base_after, services, default_branch_after, step=6)
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
            # 申告が無かったことも記録する。キーごと落とすと、申告できない古い
            # 呼び出しと「申告せずに通した merge」を後から区別できない。
            "claimed_digest_source": claimed_digest_source,
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
        request.claimed_digest_source,
    )
