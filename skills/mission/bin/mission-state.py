#!/usr/bin/env python3
"""mission-state.py — /mission skill の state.json 管理ユーティリティ

責務:
- state.json の atomic write (Phase B-2: fsync + replace)
- ファイルロック (Phase B-1: fcntl)
- A-4: 更新前に .bak を自動生成
- A-1: project_root の自動 stamp
- A-2: pid / hostname / session_id の自動 stamp
- A-3: 空 .mission-state/ ディレクトリの cleanup

ユーザビリティ:
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py init <mission> [--threshold X] [--max-iter N] [--files a.py,b.py]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py get [--field key]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py set key=value [key=value ...]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py artifact init --title <text> [--required-for-pass]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py artifact append --section evidence --text <text>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-passes
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-halt --reason <text>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py reactivate --approved-by-user --expected-category <category> --reason <text>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py cleanup-empty <path>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py list   # 全プロジェクト active 一覧 (C-4)
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py halt --reason <text> [--all]
"""

# Issue #99: PEP 604 union 注釈 (X | None) を Python 3.9 (macOS Xcode CLT の python3) でも
# パース可能にする。これが無いとモジュール読み込み時点で TypeError になり全コマンドが全滅する。
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import copy
import contextlib
import errno
import fcntl
import hashlib
from collections import Counter
import importlib.metadata
import importlib.util
import io
import json
import math
import multiprocessing
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple, Protocol

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from mission_common import (  # noqa: E402
    HALT_CATEGORIES,
    PREPARATION_ONLY_MARKERS,
    SESSION_ROLES,
    SPECIALIST_SELECTION_CHECKPOINT_REQUIRED_AT,
    TERMINAL_OUTCOMES,
    classify_state as _classify,
    correlation_id,
    opaque_token,
    derive_terminal_outcome,
    duration_sec as _duration_sec,
    parse_iso_datetime,
    state_dedupe_rank,
    state_identity,
    summarize_pass_rate_population,
)
from specialist_accounting import (  # noqa: E402
    candidate_accounting_report,
    explicitly_selected_specialist_skills as _accounting_selected_specialist_skills,
    selected_without_terminal_invocations,
    terminal_invoked_specialist_skills as _accounting_terminal_invoked_specialist_skills,
)
from specialist_lifecycle import (  # noqa: E402
    SpecialistLifecycleError,
    invocation_by_id,
    invocation_lifecycle_state,
    is_terminal_invocation,
    new_invocation_id,
    new_selection_id,
    selection_checkpoint,
    validate_invocation_record,
    validate_invocation_transition,
    validate_specialist_lifecycle,
)
from activity_segments import (  # noqa: E402
    ACTIVITY_KINDS,
    PHASE_ACTIVITY_DEFAULTS,
    ACTIVITY_REASONS_BY_KIND,
    ActivityTimingError,
    WAIT_KINDS,
    close_activity_for_resume,
    close_activity_for_terminal,
    end_activity_segment,
    is_phase_default_activity,
    record_activity_event,
    sanitize_activity_detail,
    start_activity_segment,
    start_phase_default_activity,
    summarize_activity_states,
    transition_activity_phase,
    validate_activity,
)
from worktree_archive import (  # noqa: E402
    STATE_ARCHIVE_GENERATION_SCHEMA,
    STATE_ARCHIVE_POINTER_SCHEMA,
    read_state_archive_file_bytes,
    read_state_archive_compaction,
    read_verified_review_input_evidence,
    state_archive_content_digest,
    valid_review_perspective,
    validated_archive_evidence_reader,
    validate_worktree_archive_bundle,
    worktree_archive_lineage_references,
)
from state_snapshot import SnapshotError, consume_snapshot_document  # noqa: E402
from provider_eligibility import (  # noqa: E402
    RegistryContractError,
    _strict_json_loads,
    detect_registry_version,
    evaluate_provider_eligibility,
    normalize_selection_source,
    parse_v2_registry_json,
    registry_entry_digest,
    validate_provider_application,
    value_digest as provider_value_digest,
)
from provider_public_contract import (  # noqa: E402
    SpecialistPublicContractError,
    contains_local_locator,
    redact_local_locators,
    validate_specialist_public_state as _validate_specialist_public_state,
)
from provider_preflight import (  # noqa: E402
    ProviderPreflightError,
    build_preflight,
    dispatch_prepared_packet,
    safe_input_snapshot,
    validate_receipt as validate_provider_receipt,
)
from plan_contract import (  # noqa: E402
    MAX_PLAN_RESULT_BYTES,
    PlanContractError,
    _strict_load as _strict_plan_load,
    _validate_document,
    canonical_plan_bytes,
    parse_provider_result,
)
from planning_lifecycle import (  # noqa: E402
    PlanningLifecycleError,
    canonical_plan_identity,
    derive_planning_lifecycle,
    validate_handoff_step,
)
from planning_provider_metrics import reduce_planning_provider_kpis  # noqa: E402
from review_learning import (  # noqa: E402
    LEARNING_BRIEF_SCHEMA,
    failure_ledger_counts,
    LearningContractError,
    reduce_iteration_recovery,
    reduce_failure_ledger,
    summarize_learning_brief,
    WEAK_PHASES,
    validate_review_learning,
)
from artifact_contract import (  # noqa: E402
    ArtifactContractError,
    artifact_lint_observation_matches,
    artifact_path_from_state,
    canonical_artifact_identity_snapshot,
    capture_artifact_identity,
    invalidate_artifact_lint_observation,
    summarize_artifact_coverage,
    validate_artifact_identity,
    validate_artifact_state_consistency,
)
from scoring_provenance import (  # noqa: E402
    REASON_CODES as _PROVENANCE_REASON_CODES,
    VERIFIER_ID_RE as _APPROVAL_VERIFIER_NAME_RE,
    build_request as build_approval_request,
    digest as provenance_digest,
    classify_score_provenance,
    project_root_from_state_path,
    read_score_provenance_evidence,
    reduce_review_aggregate,
    terminal_state_digest,
    validate_receipt_binding,
    validate_recorded_envelope,
)
from command_outcomes import (  # noqa: E402
    KINDS as COMMAND_OUTCOME_KIND_ORDER,
    OutcomeStoreError,
    append_sidecar as append_command_outcome_sidecar,
    append_state_record as append_command_outcome_state,
    iter_records as iter_command_outcome_records,
    observe_state_only as observe_state_command_outcomes,
    summarize as summarize_command_outcomes,
    summarize_sessions as summarize_command_outcome_sessions,
    validate_observation as validate_command_outcome_observation,
    valid_identifier as _valid_command_outcome_identifier,
)
from error_guidance import build_guidance  # noqa: E402
from evidence_handoff import (  # noqa: E402
    EvidenceHandoffError,
    EvidenceHandoffTimeout,
    await_handoff as await_evidence_handoff,
    load_payload as load_handoff_payload,
    publish as publish_evidence_handoff,
    verify_handoff as verify_evidence_handoff,
)
from pregate_cache import (  # noqa: E402
    PregateCacheError,
    inspect as inspect_pregate_cache,
    lookup as lookup_pregate_cache,
    record as record_pregate_cache,
    subject_digest as subject_digest_pregate_cache,
)
from merge_queue import (  # noqa: E402
    BaseMismatchError,
    MergeQueueError,
    derive_revision_scope_shas as derive_queue_revision_scope_shas,
    enqueue as enqueue_merge_queue,
    mark as mark_merge_queue,
    next_candidate as next_merge_queue_candidate,
    status as status_merge_queue,
    verify as verify_merge_queue,
)

SCHEMA_VERSION = 4  # v4: structured scoring provenance is mandatory for new sessions
GOAL_DISPATCH_MODES = {"inline", "host-native"}


def _new_specialist_selection_checkpoint() -> dict:
    return {
        "policy": "checkpoint",
        "action": "continue-core",
        "decision": "none",
        "reason": "specialist selection has not been evaluated",
        "reason_code": "pending-evaluation",
        "prompted_user": False,
        "lifecycle_state": "terminal",
        "selection_id": new_selection_id(),
    }


def _current_selection_id(data: dict) -> str | None:
    decision = data.get("specialists_decision")
    selection_id = decision.get("selection_id") if isinstance(decision, dict) else None
    return str(selection_id) if isinstance(selection_id, str) else None


def _finalize_specialist_selection_checkpoint(
    decision: dict, candidates: list[dict], selected: list[dict], unavailable: list[dict]
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Bind one recommendation result to one opaque, durable checkpoint."""
    selection_id = new_selection_id()
    if selected:
        checkpoint = {"decision": "selected", "reason_code": "candidate-selected", "lifecycle_state": "selected"}
    elif decision.get("action") == "ask-user":
        checkpoint = {"decision": "none", "reason_code": "awaiting-confirmation", "lifecycle_state": "candidate"}
    elif unavailable:
        checkpoint = {"decision": "unavailable", "reason_code": "provider-unavailable", "lifecycle_state": "terminal"}
    else:
        checkpoint = {
            "decision": "none",
            "reason_code": "no-candidates" if not candidates else "profile-not-applicable",
            "lifecycle_state": "terminal",
        }
    decision = {**decision, **checkpoint, "selection_id": selection_id}
    bind = lambda records: [{**record, "selection_id": selection_id} for record in records]
    return decision, bind(candidates), bind(selected), bind(unavailable)


def _specialist_selection_checkpoint_error(data: dict) -> str | None:
    decision = data.get("specialists_decision")
    if not isinstance(decision, dict) or not decision.get("selection_id"):
        return None  # legacy sessions remain readable and are classified by audit
    try:
        validate_specialist_lifecycle(data)
    except SpecialistLifecycleError as exc:
        return f"specialist selection checkpoint is not terminal or valid: {exc}"
    return None

# #186: 実行中の mission-state.py のバージョン。.claude-plugin/plugin.json 等の manifest と
# 一致させる (release 時に手動 bump。test_doc_consistency.py::test_release_version_paths_are_in_sync
# が manifest 間の一致は既に保証しているため、ここでは manifest との一致のみ追加で固定する)。
# 実行時に manifest ファイルを読みに行かない設計: plugin cache 配布・symlink 配布・単一ファイル
# 実行のいずれでも `.claude-plugin/plugin.json` への相対パスが安定しないため。
MISSION_CLI_VERSION = "2.0.0"

# Tier5: スコア/反復のマジックナンバーを単一定義 (散在防止・閾値変更を1箇所に集約)
DEFAULT_THRESHOLD = 4.0     # 合格 composite 閾値 (init --threshold 未指定時 / mark-passes fallback)
MIN_ITEM_THRESHOLD = 3.5    # 各項目スコアの足切り (これ未満は mark-passes が reject)
DEFAULT_MAX_ITER = 3        # init --max-iter 未指定時の最大反復回数 (0=上限なし)
SCORE_MIN, SCORE_MAX = 0.0, 5.0  # composite/min_item の許容範囲


def _finite_score(value: object) -> bool:
    """Return whether a score is a non-boolean finite number on the 0–5 scale."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and SCORE_MIN <= float(value) <= SCORE_MAX
    )


def _nonnegative_int(value: object) -> bool:
    """Return whether an open High count is an exact non-boolean integer."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0

ARTIFACT_SECTIONS = {
    "mission": "Mission",
    "plan": "Plan",
    "execution": "Execution",
    "evidence": "Evidence",
    "review": "Review",
    "score_gate": "Score Gate",
    "assumptions": "Assumptions",
    "follow_ups": "Follow-ups",
}
ARTIFACT_REDACTION_STATUSES = {"unchecked", "checked", "reviewed", "not-needed"}
ARTIFACT_PUBLISH_PROVIDERS = {"claude-code", "local"}
MAX_REGISTRY_BYTES = 1024 * 1024


BUILTIN_SPECIALIST_CANDIDATES = [
    {
        "role": "doc-writer",
        "skill": "documentation-provider",
        "task_profiles": ["documentation"],
        "phases": ["planning", "execution", "review"],
        "source": "preset:docs",
        "required": False,
        "install_hint": False,
    },
    {
        "role": "frontend",
        "skill": "frontend-provider",
        "task_profiles": ["frontend"],
        "phases": ["planning", "execution"],
        "source": "preset:frontend",
        "required": False,
        "install_hint": False,
    },
    {
        "role": "visual-quality",
        "skill": "visual-quality-provider",
        "task_profiles": ["frontend", "product"],
        "phases": ["planning", "review"],
        "source": "preset:frontend",
        "required": False,
        "install_hint": False,
    },
    {
        "role": "backend",
        "skill": "backend-provider",
        "task_profiles": ["backend", "database"],
        "phases": ["planning", "execution"],
        "source": "preset:backend",
        "required": False,
        "install_hint": False,
    },
    {
        "role": "unit-tester",
        "skill": "unit-test-provider",
        "task_profiles": ["testing", "backend"],
        "phases": ["execution", "review"],
        "source": "preset:testing",
        "required": False,
        "install_hint": False,
    },
    {
        "role": "security-reviewer",
        "skill": "security-review-provider",
        "task_profiles": ["security"],
        "phases": ["planning", "review"],
        "source": "preset:security",
        "required": False,
        "install_hint": False,
    },
    {
        "role": "infra",
        "skill": "infra-provider",
        "task_profiles": ["infra"],
        "phases": ["planning", "execution", "review"],
        "source": "preset:infra",
        "required": False,
        "install_hint": False,
    },
]

PHASE_ROLE_ORDER = {
    "planning": [
        "issue-framing",
        "hypothesis-design",
        "architecture-review",
        "api-design",
        "schema-review",
        "planning",
        "doc-writer",
    ],
    "execution": [
        "implementation",
        "backend",
        "frontend",
        "refactor-review",
        "documentation",
        "market-research",
        "competitor-intelligence",
        "financial-modeling",
        "risk-review",
        "data-analysis",
    ],
    "review": [
        "unit-test",
        "integration-test",
        "unit-tester",
        "code-review",
        "security-review",
        "security-reviewer",
        "performance-review",
        "infra-review",
        "strategy-review",
        "document-review",
        "visual-quality",
    ],
    "synthesis": [
        "quality-synthesis",
        "risk-summary",
        "report-synthesis",
        "strategy-review",
        "document-review",
    ],
}

PROFILE_KEYWORDS = {
    "architecture": (
        "architecture", "architect", "system design", "design review",
        "設計", "アーキテクチャ", "構成", "構造",
    ),
    "documentation": ("readme", "docs", "document", "documentation", "adr", "guide", "reference", "changelog", ".md"),
    "frontend": ("frontend", "react", "vue", "ui", "css", "component", "browser", "screenshot", "accessibility"),
    "backend": ("backend", "api", "endpoint", "service", "worker", "validation", "business logic"),
    "database": ("database", "schema", "migration", "query", "sql", "persistence"),
    "security": ("security", "auth", "permission", "secret", "token", "injection", "pii", "oauth"),
    "testing": ("test", "pytest", "jest", "e2e", "playwright", "coverage", "flaky"),
    "infra": ("deploy", "deployment", "ci", "docker", "cloud", "observability", "terraform", "github actions"),
    "product": ("prd", "ux", "workflow", "acceptance criteria", "product"),
    "research": (
        "research", "market", "competitor", "analysis", "source",
        "市場", "市場規模", "競合", "差別化", "競争優位", "tam", "sam", "som",
        "roi", "npv", "収益性", "投資対効果", "リスク", "規制", "感度分析",
        "戦略", "提案", "executive summary", "recommendation", "positioning",
    ),
    "strategy": (
        "strategy", "strategic", "戦略", "差別化", "競争優位", "positioning",
        "roadmap", "kpi", "提案", "recommendation",
    ),
    "financial": ("roi", "npv", "financial model", "収益性", "投資対効果", "財務", "感度分析"),
    "risk": ("risk", "リスク", "規制", "scenario", "シナリオ", "compliance"),
}

HIGH_RISK_KEYWORDS = (
    # Issue #175 で #174 と同一ポリシーで較正 (2026-07-10)
    # 維持: production, deploy, migration, drop table, delete data, irreversible, payment, security, secret, pii
    # 削除: "prod" ("production" が既にあり冗長。"product"/"productivity" への誤発火源)
    # "auth" → 語幹 (authenticat / authoriz / oauth) に置換 (authority への誤発火を排除)
    # "token" → 複合語 (api token / api-token / api_key / access token / access-token / bearer) に置換
    "production", "deploy", "migration", "drop table", "delete data",
    "irreversible", "payment", "security", "secret", "pii",
    "api token", "api-token", "api_key",
    "access token", "access-token", "bearer",
    "authenticat", "authoriz", "oauth",
)

SPECIALIST_INVOCATION_STATUSES = {
    "selected",
    "started",
    "completed",
    "unvalidated-evidence",
    "prepared",
    "awaiting-input",
    "inline-applied",
    "skill-tool-applied",
    "skipped",
    "unavailable",
    "failed",
}

SPECIALIST_INVOCATION_MODES = {
    "skill-tool",
    "command-provider",
    "codex-inline",
    "natural-language",
    "fallback-core",
}

SPECIALIST_INVOCATION_REASON_REQUIRED_STATUSES = {
    "prepared",
    "awaiting-input",
    "skipped",
    "unavailable",
    "failed",
}

SPECIALIST_SELECTION_SOURCES = {
    "confirmed-user",
    "user-instruction",
    "manual",
    "task-required",
}

APPLIED_SPECIALIST_INVOCATION_STATUSES = {
    "completed",
    "inline-applied",
    "skill-tool-applied",
}

SPECIALIST_SELECTION_CHECKPOINT_COMPLEXITIES = {"Standard", "Complex", "Critical"}
DEFAULT_STALE_ACTIVE_SECONDS = 3 * 60 * 60
DEFAULT_LEASE_TTL_SECONDS = 15 * 60
LEASE_CARRIER_PREFIX = "MISSION_LEASE_CARRIER="
LEASE_STATE_FIELDS = (
    "owner_session_id",
    "lease_id",
    "fencing_epoch",
    "lease_expires_at",
)


class LeaseRejectedError(RuntimeError):
    """A writer does not own the current fenced session lease."""


class LeaseDecision:
    """Result of acquiring, renewing, or taking over a session lease."""

    def __init__(self, action: str, lease_id: str, fencing_epoch: int):
        self.action = action
        self.lease_id = lease_id
        self.fencing_epoch = fencing_epoch


def iso_now() -> str:
    override = os.environ.get("MISSION_STATE_NOW")
    if override:
        parsed = parse_iso_datetime(override)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_dir(cwd: Path) -> Path:
    return cwd / ".mission-state"


def session_dir(cwd: Path) -> Path:
    return state_dir(cwd) / "sessions"


def session_file(cwd: Path, sid: str) -> Path:
    return session_dir(cwd) / f"{sid}.json"


def aggregate_file(cwd: Path) -> Path:
    return state_dir(cwd) / "aggregate.json"


# os.walk スキャン時にプルーニングする巨大・無関係ツリー (この内側に mission state は作られない)。
_PRUNE_DIRS = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".cache", ".pytest_cache", "vendor",
    "target", ".gradle", "Pods", ".build",
})


def _iter_state_files(root: Path, *, include_archive: bool = False):
    """root 配下の全 state ファイルを列挙 (legacy state.json + multi-session sessions/*.json)。

    os.walk で node_modules/.git 等の巨大ツリーをプルーニングするため、~/dev のような
    大規模ディレクトリ (実測 12 万サブディレクトリ) でも高速にスキャンできる
    (rglob 全舐めの ~20 秒 → 0.x 秒)。
    include_archive=True で archive/state-*.json も含める (stats の全履歴収集用)。
    デフォルトは現役ファイルのみ (cleanup/list/halt が誤って archive を拾わないように)。
    """
    root = Path(root)
    if not root.exists():
        return
    # followlinks=False (明示): symlink 先を二重走査しない。rglob も symlink を展開しないため等価。
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        if os.path.basename(dirpath) != ".mission-state":
            continue
        # 子 (sessions/archive) は下で直接 glob するため os.walk の降下を止める (.mission-state は入れ子にならない)
        dirnames[:] = []
        gs = Path(dirpath)
        sf = gs / "state.json"  # legacy 単一 state
        if sf.is_file():
            yield sf
        sessions = gs / "sessions"  # multi-session
        if sessions.is_dir():
            yield from sorted(sessions.glob("*.json"))
        if include_archive:
            archive = gs / "archive"  # 退避済み履歴 (stats 用)
            if archive.is_dir():
                yield from sorted(archive.glob("state-*.json"))
                # Issue #7: worktree サブディレクトリ (archive/worktree-*/) も列挙する
                for sub in sorted(archive.glob("worktree-*")):
                    if sub.is_dir():
                        validation = validate_worktree_archive_bundle(sub)
                        if validation.status == "invalid":
                            continue
                        if validation.status == "valid":
                            yield from validation.state_paths
                            continue
                        worktree_root = validation.root
                        yield from sorted(worktree_root.glob("*.json"))
                        worktree_sessions = worktree_root / "sessions"
                        if worktree_sessions.is_dir():
                            yield from sorted(worktree_sessions.glob("*.json"))


def _default_search_roots() -> list[Path]:
    """list / cleanup-stale / halt --all / stats のデフォルト探索 root。

    環境変数 MISSION_SEARCH_ROOTS (OS のパス区切り文字で複数指定可、~ 展開あり) が
    あればそれを使う。未設定なら現在の作業ディレクトリ (cwd) のみを探索する。
    Path.home() 全体の rglob は低速 (実測 86 秒) なため既定にはしない。複数プロジェクト
    を横断スキャンしたい場合は MISSION_SEARCH_ROOTS を設定するか --root を明示する。
    """
    env = os.environ.get("MISSION_SEARCH_ROOTS")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p.strip()]
    return [Path.cwd()]


def _learning_brief_default_roots(cwd: Path) -> list[Path]:
    """learning brief 専用の default root を返す。

    worktree では main checkout root の .mission-state を読みたいので、
    `git rev-parse --git-common-dir` の親を優先する。git 解決が失敗した場合や
    non-git では、従来どおり cwd を使って fail-safe に落とす。
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return [cwd]
    if result.returncode != 0:
        return [cwd]
    common_dir = result.stdout.strip()
    if not common_dir:
        return [cwd]
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = cwd / common_path
    try:
        checkout_root = common_path.resolve().parent
    except OSError:
        return [cwd]
    # 非 worktree でも common-dir の親は checkout root になるので、そのまま使う。
    return [checkout_root]


def _project_root_of(sf: Path) -> Path:
    """Derive a project root from the state file's nearest valid structure."""
    if sf.parent.name == "sessions" and sf.parent.parent.name == ".mission-state":
        return sf.parent.parent.parent
    if sf.name == "state.json" and sf.parent.name == ".mission-state":
        return sf.parent.parent
    raise ValueError(f"unsupported mission state path: {sf}")


def _add_to_aggregate(cwd: Path, sid: str) -> None:
    """active_sessions に sid を追加 (重複なし)。呼び出し元が StateLock を保持する前提。
    壊れた aggregate.json は空扱いで復旧 (F-6 と同じ堅牢性)。"""
    agg = aggregate_file(cwd)
    data = {}
    if agg.exists():
        try:
            data = json.loads(agg.read_text())
        except Exception:
            data = {}
    sids = data.setdefault("active_sessions", [])
    if sid not in sids:
        sids.append(sid)
        data["updated_at"] = iso_now()
        atomic_write_json(agg, data)


def _remove_from_aggregate(cwd: Path, sid: str) -> None:
    """multi-session 完了/halt 時に aggregate.json の active_sessions から sid を除去 (dead entry 防止)."""
    agg = aggregate_file(cwd)
    if not agg.exists():
        return
    try:
        data = json.loads(agg.read_text())
    except Exception:
        return
    sids = data.get("active_sessions", [])
    if sid in sids:
        sids.remove(sid)
        data["active_sessions"] = sids
        data["updated_at"] = iso_now()
        atomic_write_json(agg, data)


def lock_file(cwd: Path) -> Path:
    return state_dir(cwd) / ".state.lock"


def resolve_session_id() -> str:
    """現セッションの ID を取得。優先順: MISSION_SESSION_ID(明示) > Claude Code/Codex の
    native session env > agent CLI PID fallback。Claude Code/Codex の ID は安定 (resume 後も
    同一・PID 再利用の影響を受けない) ため、ファイル名・session_id フィールドの両方に使う。"""
    sid = os.environ.get("MISSION_SESSION_ID")
    if sid:
        return _sanitize_sid(sid)
    cc = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if cc:
        return f"cc-{_sanitize_sid(cc)}"
    cx = os.environ.get("CODEX_THREAD_ID")
    if cx:
        return f"cx-{_sanitize_sid(cx)}"
    pid = find_agent_pid()
    global _PID_FALLBACK_WARNING_EMITTED
    if not _PID_FALLBACK_WARNING_EMITTED:
        print(
            "WARNING: MISSION_SESSION_ID 未設定のため pid フォールバックを使用しています "
            f"(pid-{pid})。",
            file=sys.stderr,
        )
        _PID_FALLBACK_WARNING_EMITTED = True
    return f"pid-{pid}"  # fallback (env なし環境)


def _lease_ttl_seconds() -> int:
    raw = os.environ.get("MISSION_LEASE_TTL_SECONDS", "")
    try:
        value = int(raw) if raw else DEFAULT_LEASE_TTL_SECONDS
    except ValueError:
        value = DEFAULT_LEASE_TTL_SECONDS
    return value if value > 0 else DEFAULT_LEASE_TTL_SECONDS


def _new_lease_id() -> str:
    return secrets.token_hex(16)


def _lease_expiry(now: datetime) -> str:
    expires = now + timedelta(seconds=_lease_ttl_seconds())
    return expires.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _renewed_lease_expiry(existing_expiry: str, now: datetime) -> str:
    """Renew without moving the fencing deadline backwards on clock rollback."""
    candidate = now + timedelta(seconds=_lease_ttl_seconds())
    existing = parse_iso_datetime(existing_expiry)
    if existing is not None:
        if existing.tzinfo is None:
            existing = existing.replace(tzinfo=timezone.utc)
        candidate = max(candidate, existing.astimezone(timezone.utc))
    return candidate.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lease_fields_present(state: dict) -> bool:
    return all(
        state.get(key) not in (None, "")
        for key in LEASE_STATE_FIELDS
    )


def _lease_now() -> datetime:
    parsed = parse_iso_datetime(iso_now())
    if parsed is None:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def acquire_or_verify_lease(
    state: dict,
    session_id: str,
    *,
    reason: str = "mutating-command",
    lease_id: str | None = None,
) -> LeaseDecision:
    """Acquire or verify the fenced lease while the caller holds StateLock.

    `MISSION_LEASE_ID` is the explicit fencing token contract. Only lease-free
    legacy state may acquire without one. A foreign writer must wait for expiry
    and receives a new token with an incremented epoch.
    """
    now = _lease_now()
    presented_lease_id = lease_id if lease_id is not None else os.environ.get("MISSION_LEASE_ID")

    lease_field_count = sum(state.get(key) not in (None, "") for key in LEASE_STATE_FIELDS)
    if 0 < lease_field_count < len(LEASE_STATE_FIELDS):
        raise LeaseRejectedError("malformed partial session lease")
    if not _lease_fields_present(state):
        lease_id = presented_lease_id or _new_lease_id()
        state["owner_session_id"] = session_id
        state["lease_id"] = lease_id
        state["fencing_epoch"] = 1
        state["lease_expires_at"] = _lease_expiry(now)
        return LeaseDecision("acquired", lease_id, 1)

    owner = str(state["owner_session_id"])
    current_lease_id = str(state["lease_id"])
    try:
        epoch = int(state["fencing_epoch"])
    except (TypeError, ValueError):
        raise LeaseRejectedError(
            f"lease held by {owner} until {state.get('lease_expires_at')} (invalid fencing epoch)"
        )

    same_owner = owner == session_id
    token_matches = presented_lease_id == current_lease_id if same_owner else False
    if same_owner and token_matches:
        state["lease_expires_at"] = _renewed_lease_expiry(
            str(state["lease_expires_at"]), now
        )
        return LeaseDecision("renewed", current_lease_id, epoch)

    expires = parse_iso_datetime(str(state.get("lease_expires_at") or ""))
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    expired = expires is not None and now >= expires.astimezone(timezone.utc)
    if not expired:
        # Same-owner writers without the matching token wait like any foreign
        # writer: after expiry they recover through the fenced takeover below.
        raise LeaseRejectedError(
            f"lease held by {owner} until {state.get('lease_expires_at')}"
        )

    retired_lease_ids = {
        str(item.get("lease_id"))
        for item in state.get("lease_history", [])
        if isinstance(item, dict) and item.get("lease_id")
    }
    if presented_lease_id and (
        presented_lease_id == current_lease_id
        or presented_lease_id in retired_lease_ids
    ):
        raise LeaseRejectedError(
            f"lease held by {owner} until {state.get('lease_expires_at')} (stale fencing token)"
        )
    new_lease_id = presented_lease_id or _new_lease_id()
    state.setdefault("lease_history", []).append({
        "owner_session_id": owner,
        "lease_id": current_lease_id,
        "fencing_epoch": epoch,
        "reason": reason,
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    state["owner_session_id"] = session_id
    state["lease_id"] = new_lease_id
    state["fencing_epoch"] = epoch + 1
    state["lease_expires_at"] = _lease_expiry(now)
    return LeaseDecision("taken-over", new_lease_id, epoch + 1)


def resolve_agent() -> str:
    """state を起動したエージェント種別を判定 (ログでの起動元識別用)。
    session_id とは独立に起動元 env を見るため、MISSION_SESSION_ID 明示時も正しく記録される。"""
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude-code"
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "cli"


def detect_host() -> str:
    """Return the native agent host used by goal dispatch guidance."""
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude-code"
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "unknown"


def _read_routing_config(path: Path, source: str, allowed_root: Path | None = None) -> dict | None:
    """Read the version-1 minimal routing config without a YAML dependency."""
    if allowed_root is not None and path.is_symlink():
        reason = f"routing config symlink rejected at {source}"
        print(f"WARN #355: {reason}; using inline", file=sys.stderr)
        return {"mode": "inline", "source": source, "fallback_reason": reason}
    if allowed_root is not None:
        try:
            resolved_path = path.resolve(strict=False)
            resolved_root = allowed_root.resolve(strict=True)
            resolved_path.relative_to(resolved_root)
        except ValueError:
            reason = f"routing config escapes project root at {source}"
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": source, "fallback_reason": reason}
        except (OSError, RuntimeError) as exc:
            reason = f"routing config path unreadable at {source}: {exc.__class__.__name__}"
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": source, "fallback_reason": reason}
    if not path.is_file():
        return None
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        reason = f"routing config unreadable at {source}: {exc.__class__.__name__}"
        print(f"WARN #355: {reason}; using inline", file=sys.stderr)
        return {"mode": "inline", "source": source, "fallback_reason": reason}
    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            reason = f"invalid routing config syntax at {source}"
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": source, "fallback_reason": reason}
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip("'\"")
        if key not in {"version", "goal_dispatch"}:
            reason = f"unknown routing config key '{key}' at {source}"
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": source, "fallback_reason": reason}
        if key in values:
            reason = f"duplicate routing config key '{key}' at {source}"
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": source, "fallback_reason": reason}
        values[key] = value
    if values.get("version") != "1":
        reason = f"unsupported routing config version '{values.get('version')}' at {source}"
        print(f"WARN #355: {reason}; using inline", file=sys.stderr)
        return {"mode": "inline", "source": source, "fallback_reason": reason}
    mode = values.get("goal_dispatch")
    if mode not in GOAL_DISPATCH_MODES:
        reason = f"invalid goal_dispatch '{mode}' at {source}"
        print(f"WARN #355: {reason}; using inline", file=sys.stderr)
        return {"mode": "inline", "source": source, "fallback_reason": reason}
    return {"mode": mode, "source": source, "fallback_reason": None}


def _routing_config_decision(cwd: Path | None = None) -> dict:
    root = cwd or Path.cwd()
    project = _read_routing_config(
        root / ".mission" / "routing.yml",
        "project:.mission/routing.yml",
        allowed_root=root,
    )
    if project is not None:
        return project
    user = _read_routing_config(
        Path.home() / ".config" / "mission" / "routing.yml",
        "user:~/.config/mission/routing.yml",
    )
    if user is not None:
        return user
    return {"mode": "inline", "source": "default:inline", "fallback_reason": None}


def load_routing_config() -> dict:
    """Load the effective minimal routing config (project > user > inline)."""
    return {"goal_dispatch": _routing_config_decision()["mode"]}


def _mission_goal_dispatch_values(mission: str) -> list[str]:
    """Collect standalone directives outside quoted Markdown blocks."""
    directive_re = re.compile(
        r"(?i)^ {0,3}goal[_ -]dispatch\s*[:=]\s*([a-z][a-z0-9-]*)[ \t]*(?:;|$)"
    )
    fence_re = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
    fence_char: str | None = None
    fence_length = 0
    values = []
    for line in (mission or "").splitlines():
        fence_match = fence_re.match(line)
        if fence_match:
            marker = fence_match.group(1)
            suffix = fence_match.group(2)
            if fence_char is None:
                if marker[0] != "`" or "`" not in suffix:
                    fence_char = marker[0]
                    fence_length = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length and not suffix.strip():
                fence_char = None
                fence_length = 0
            continue
        if fence_char is not None:
            continue
        directive = directive_re.match(line)
        if directive:
            values.append(directive.group(1).lower())
    return values


def _resolve_goal_dispatch(mission: str, cli_mode: str | None, cwd: Path) -> dict:
    explicit_values = _mission_goal_dispatch_values(mission)
    if explicit_values:
        unique_values = list(dict.fromkeys(explicit_values))
        if len(unique_values) > 1:
            reason = (
                "conflicting goal_dispatch directives in mission user instruction: "
                + ", ".join(unique_values)
            )
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": "mission:user-explicit", "fallback_reason": reason}
        mode = unique_values[0]
        if mode not in GOAL_DISPATCH_MODES:
            reason = f"invalid goal_dispatch '{mode}' in mission user instruction"
            print(f"WARN #355: {reason}; using inline", file=sys.stderr)
            return {"mode": "inline", "source": "mission:user-explicit", "fallback_reason": reason}
        return {"mode": mode, "source": "mission:user-explicit", "fallback_reason": None}
    if cli_mode is not None:
        return {"mode": cli_mode, "source": "cli:--goal-dispatch", "fallback_reason": None}
    return _routing_config_decision(cwd)


def _goal_dispatch_route_fields(data: dict) -> dict:
    requested = data.get("goal_dispatch_requested") or "inline"
    source = data.get("goal_dispatch_source") or "default:inline"
    fallback_reason = data.get("goal_dispatch_resolution_fallback_reason")
    host = detect_host()
    effective = requested
    if requested == "host-native" and host == "unknown":
        effective = "inline"
        fallback_reason = "host-native unavailable: host detection returned unknown"
    fields = {
        "goal_dispatch_requested": requested,
        "goal_dispatch_effective": effective,
        "goal_dispatch_source": source,
        "goal_dispatch_host": host,
    }
    if fallback_reason:
        fields["goal_dispatch_fallback_reason"] = fallback_reason
    return fields


def _inline_goal_guidance(prefix: str = "") -> str:
    return (
        f"{prefix}goal 契約の 5 見出し (Goal / Result / Evidence / Assumptions / "
        "Stop Condition) でタスクを直接完遂し、最終報告に goal へルーティングした旨を明記する。"
        "mission の pass は主張しない。mission 機構が必要なら --force-mission で再 init する。"
    )


def _goal_dispatch_guidance(fields: dict, inline_prefix: str = "") -> str:
    if fields["goal_dispatch_effective"] == "host-native":
        if fields["goal_dispatch_host"] == "claude-code":
            return (
                "mission ループを続けず、実行ホストの /goal <目標文> へ目標を委譲して完遂する。"
                "最終報告に goal へルーティングした旨を明記し、mission の pass は主張しない。"
            )
        return (
            "mission ループを続けず、実行ホストの goal mode に目標を登録して完遂する。"
            "最終報告に goal へルーティングした旨を明記し、mission の pass は主張しない。"
        )
    return _inline_goal_guidance(inline_prefix)


def _sanitize_sid(sid: str) -> str:
    """session_id をファイル名安全化 (パストラバーサル防止)。区切り文字を除去。"""
    safe = re.sub(r"[/\\]", "_", sid).strip().lstrip(".")
    return safe or "default"


def _slug_for_filename(value: str) -> str:
    """Archive filename fragment sanitizer for skill names such as github:github."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value or "").strip(".-")
    return safe or "unknown"



def resolve_state_file(cwd: Path) -> Path:
    """全 cmd_* の state ファイル解決の単一窓口。常に sessions/<sid>.json を返す (2026-06-13 legacy 廃止)。"""
    return session_file(cwd, resolve_session_id())


class StateLock:
    """fcntl ベースの排他ロック (Phase B-1)."""

    def __init__(self, lock_path: Path, timeout: float = 5.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.lock_path, "w")
        deadline = time.time() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() > deadline:
                    raise TimeoutError(
                        f"Could not acquire state lock within {self.timeout}s: {self.lock_path}"
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self.fd:
            try:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            finally:
                self.fd.close()


def _atomic_write(path: Path, writer) -> None:
    """同一 directory の排他的な一時ファイルを fsync 後に publish する."""
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def _is_session_state_shape(data: dict) -> bool:
    """#310: session state 形状の判定 (aggregate / manifest / scoring 等を除外)."""
    return isinstance(data, dict) and "mission_id" in data and "loop_active" in data


def _is_session_state_path(path: Path) -> bool:
    return (
        path.suffix == ".json"
        and (
            path.parent.name == "sessions"
            or path.name == "state.json" and path.parent.name == ".mission-state"
        )
    )


_LEASE_KEYS = (*LEASE_STATE_FIELDS, "lease_history")
_LEASE_WRITE_REASON: str | None = None
_PROCESS_LEASE_IDS: dict[str, str] = {}
_LEASE_DECISION_UNSET = object()
_SUPERSEDE_TERMINAL_PATHS: set[str] = set()
_PID_FALLBACK_WARNING_EMITTED = False


@contextlib.contextmanager
def _lease_write_reason(reason: str | None):
    global _LEASE_WRITE_REASON
    previous = _LEASE_WRITE_REASON
    _LEASE_WRITE_REASON = reason
    try:
        yield
    finally:
        _LEASE_WRITE_REASON = previous


def _enforce_session_lease_for_write(path: Path, data: dict) -> LeaseDecision | None:
    """CAS the lease against the latest state immediately before publish."""
    if not (_is_session_state_path(path) and _is_session_state_shape(data)):
        return None
    if str(path.resolve()) in _SUPERSEDE_TERMINAL_PATHS:
        return None
    latest = None
    if path.exists():
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if _is_session_state_shape(candidate):
                latest = candidate
        except (OSError, json.JSONDecodeError):
            latest = None
    lease_state = latest if latest is not None else data
    path_key = str(path.resolve())
    presented_lease_id = os.environ.get("MISSION_LEASE_ID") or _PROCESS_LEASE_IDS.get(path_key)
    try:
        decision = acquire_or_verify_lease(
            lease_state,
            resolve_session_id(),
            reason=_LEASE_WRITE_REASON or "mutating-command",
            lease_id=presented_lease_id,
        )
    except LeaseRejectedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        guidance = build_guidance("lease", "lease-rejected", _guidance_context_for_state(lease_state))
        for line in guidance:
            print(line, file=sys.stderr)
        raise CommandOutcomeExit(2, "expected-gate", guidance=guidance)
    for key in _LEASE_KEYS:
        if key in lease_state:
            data[key] = lease_state[key]
    return decision


def _emit_lease_carrier(data: dict, decision: LeaseDecision | None) -> None:
    """Expose a newly issued token only after its state publish succeeds."""
    if decision is None or decision.action not in {"acquired", "taken-over"}:
        return
    carrier = {
        "schema": "mission-lease-carrier/1",
        "action": decision.action,
        "session_id": str(data.get("session_id") or resolve_session_id()),
        "lease_id": decision.lease_id,
        "fencing_epoch": decision.fencing_epoch,
        "lease_expires_at": data.get("lease_expires_at"),
    }
    print(
        LEASE_CARRIER_PREFIX + json.dumps(carrier, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr,
    )


def atomic_write_json(
    path: Path,
    data: dict,
    *,
    administrative: bool = False,
    lease_decision: LeaseDecision | None | object = _LEASE_DECISION_UNSET,
) -> None:
    """Phase B-2: fsync + os.replace で完全な前 or 後状態を保証.

    #310: session state 形状の書き込みは既定で `last_activity_at` を刻む (エージェント
    活動の実時刻)。cleanup-stale / resolve-archive / halt --all 等の管理系 janitor は
    `administrative=True` で opt-out し、活動時刻を汚染しない。duration / stale 判定は
    last_activity_at を updated_at より優先する (updated_at は resolution batch 書き込みで
    上書きされ壁時計が最大 500 倍膨張した実害があるため)。

    """
    if _is_session_state_shape(data):
        _validate_specialist_public_state(data)
    if lease_decision is _LEASE_DECISION_UNSET:
        lease_decision = _enforce_session_lease_for_write(path, data)
    if not administrative and _is_session_state_shape(data):
        data["last_activity_at"] = iso_now()
    _atomic_write(path, lambda f: json.dump(data, f, indent=2, ensure_ascii=False))
    if isinstance(lease_decision, LeaseDecision):
        _PROCESS_LEASE_IDS[str(path.resolve())] = lease_decision.lease_id
    _emit_lease_carrier(data, lease_decision)


def atomic_write_text(path: Path, content: str) -> None:
    """Text evidence を fsync + replace で atomic に更新する."""
    _atomic_write(path, lambda f: f.write(content))


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Binary evidence を symlink 非追跡の一時ファイルから atomic publish する."""
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            fd = -1
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)


def _probe_directory_write(directory: Path) -> None:
    """Write, fsync, and remove a probe in a mission state directory."""
    probe_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".mission-permission-probe-",
            dir=directory,
            delete=False,
        ) as probe:
            probe_path = Path(probe.name)
            probe.write("permission-preflight\n")
            probe.flush()
            os.fsync(probe.fileno())
    finally:
        if probe_path is not None:
            probe_path.unlink(missing_ok=True)


def _probe_file_write(path: Path) -> None:
    """Perform a content-preserving write and fsync on an evidence file."""
    with path.open("r+b") as target:
        first_byte = target.read(1)
        if first_byte:
            target.seek(0)
            target.write(first_byte)
        else:
            target.write(b"\n")
            target.truncate(0)
        target.flush()
        os.fsync(target.fileno())


def _validated_assumptions_probe_path(cwd: Path, raw_path: str) -> Path:
    """Resolve an existing regular assumptions file inside .mission-state."""
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError("absolute evidence path")
    state_root = state_dir(cwd).absolute()
    candidate = (cwd / candidate).absolute()
    candidate.relative_to(state_root)
    if candidate.is_symlink():
        raise ValueError("symlink evidence path")
    resolved_root = state_root.resolve(strict=True)
    resolved_candidate = candidate.resolve(strict=True)
    resolved_candidate.relative_to(resolved_root)
    if not resolved_candidate.is_file():
        raise ValueError("evidence path is not a regular file")
    return candidate


def _record_permission_preflight_halt(cwd: Path, sf: Path, reason: str) -> bool:
    """Best-effort persisted halt; structured stdout remains the final fallback."""
    try:
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text(encoding="utf-8"))
            now = iso_now()
            data["halt_reason"] = reason
            data["halt_category"] = "blocked-external"
            data["loop_active"] = False
            _transition_phase(data, "halted", now)
            _write_terminal_outcome(data)
            data["updated_at"] = now
            backup_state(sf)
            atomic_write_json(sf, data)
            try:
                _remove_from_aggregate(cwd, resolve_session_id())
            except OSError:
                pass
        return True
    except OSError:
        return False


def _exit_init_evidence_write_failure(target: str) -> None:
    """新 state publish 前の evidence 作成失敗を正しい target で返す."""
    reason = (
        "Phase 0 permission preflight failed before task execution: "
        f"{target} write unavailable"
    )
    print(json.dumps({
        "ok": False,
        "halt_recorded": False,
        "halt_category": "blocked-external",
        "terminal_outcome": "blocked_external",
        "halt_reason": reason,
        "probes": [
            {"target": target, "ok": False, "error": "write-unavailable"}
        ],
    }))
    raise SystemExit(2)


def _exit_init_write_failure(cwd: Path, sf: Path | None = None) -> None:
    """Emit the non-interactive fallback when init cannot persist state."""
    reason = (
        "Phase 0 permission preflight failed before task execution: "
        "state write unavailable"
    )
    halt_recorded = bool(
        sf is not None
        and sf.exists()
        and _record_permission_preflight_halt(cwd, sf, reason)
    )
    print(json.dumps({
        "ok": False,
        "halt_recorded": halt_recorded,
        "halt_category": "blocked-external",
        "terminal_outcome": "blocked_external",
        "halt_reason": reason,
        "probes": [
            {"target": "state", "ok": False, "error": "write-unavailable"}
        ],
    }))
    raise SystemExit(2)


@contextlib.contextmanager
def _guarded_init_state_lock(cwd: Path, sf: Path):
    """Convert every init persistence OSError into structured fallback evidence."""
    try:
        with StateLock(lock_file(cwd)):
            yield
    except OSError:
        _exit_init_write_failure(cwd, sf)


def backup_state(path: Path) -> None:
    """A-4: 更新前に .bak をコピー生成."""
    if path.exists():
        _validate_specialist_public_state(
            json.loads(path.read_text(encoding="utf-8"))
        )
        bak = path.with_suffix(path.suffix + ".bak")
        atomic_write_bytes(bak, path.read_bytes())


def _comm_is_agent(comm: str) -> bool:
    """comm がエージェント CLI (Claude Code / Codex) のプロセス名か判定。

    Codex 対応 (2026-06-13): claude/claude.exe に加えて codex/codex.exe も
    エージェントプロセスとみなす。PID owner 判定の単一の真実源。
    """
    comm = (comm or "").strip()
    if not comm:
        return False
    # basename 一致のみ許可: フルパス (/usr/bin/codex) は末尾 "/codex" で拾い、
    # "notcodex" / "xclaude" のような部分一致 (false positive) は除外する。
    for name in ("claude.exe", "claude", "codex.exe", "codex"):
        if comm == name or comm.endswith("/" + name):
            return True
    return False


_LAST_PID_WAS_FALLBACK: bool = False


def _last_pid_was_fallback() -> bool:
    return _LAST_PID_WAS_FALLBACK


def find_agent_pid() -> int:
    """親プロセスツリーを遡って claude / codex (エージェント CLI) プロセスを見つける。

    Bash 経由で実行された場合、os.getppid() は bash を返してしまうため、
    プロセスツリーを最大 8 階層遡って agent CLI プロセスを特定する。
    見つからなければ os.getppid() (= 直接の親) を fallback で返す。
    #239: fallback した場合は _LAST_PID_WAS_FALLBACK = True を設定する。
    """
    global _LAST_PID_WAS_FALLBACK
    pid = os.getppid()
    for _ in range(8):
        if pid <= 1:
            break
        try:
            r = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
            comm = r.stdout.strip()
            if _comm_is_agent(comm):
                _LAST_PID_WAS_FALLBACK = False
                return pid
            r2 = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
            pid = int(r2.stdout.strip() or 0)
        except Exception:
            break
    _LAST_PID_WAS_FALLBACK = True
    return os.getppid()  # fallback


def stamp_metadata(data: dict, cwd: Path) -> dict:
    """A-1/A-2/B-3: project_root / pid / hostname / session_id / agent を保証."""
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("project_root", str(cwd.resolve()))
    # #12: setdefault は RHS を eager 評価するため、既存キーでも find_agent_pid (ps subprocess)
    # が走り StateLock を最大 8x2s 占有して lock timeout を誘発する。存在チェックで遅延させる。
    if "pid" not in data:
        data["pid"] = find_agent_pid()  # agent CLI プロセスの PID (プロセスツリー遡及で正確に取得)
        data["pid_source"] = "fallback" if _last_pid_was_fallback() else "agent"
    data.setdefault("hostname", socket.gethostname())
    if "session_id" not in data:
        data["session_id"] = resolve_session_id()
    if "agent" not in data:
        data["agent"] = resolve_agent()  # 起動元 (claude-code/codex/cli) をログ識別用に記録
    data.setdefault("created_at_session", iso_now())
    data.setdefault("cli_version", MISSION_CLI_VERSION)  # #186
    return data


def mission_id(mission: str) -> str:
    return hashlib.sha256(mission.encode("utf-8")).hexdigest()[:16]


def _parse_iso_datetime(value: str | None):
    return parse_iso_datetime(value)


def _mission_started_at(data: dict) -> datetime | None:
    for key in ("created_at_session", "started_at", "created_at"):
        started = _parse_iso_datetime(data.get(key))
        if started:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return started.astimezone(timezone.utc)
    return None


def _specialist_selection_checkpoint_expected(data: dict) -> bool:
    if str(data.get("complexity") or "") not in SPECIALIST_SELECTION_CHECKPOINT_COMPLEXITIES:
        return False
    started = _mission_started_at(data)
    return bool(started and started >= SPECIALIST_SELECTION_CHECKPOINT_REQUIRED_AT)


def _has_specialist_selection_checkpoint(data: dict) -> bool:
    task_profile = data.get("task_profile")
    decision = data.get("specialists_decision")
    if not isinstance(task_profile, dict) or not task_profile.get("primary"):
        return False
    if not isinstance(decision, dict) or not decision.get("policy"):
        return False
    return True


def _state_age_since_update_sec(data: dict, *, now: datetime | None = None) -> float | None:
    updated = _parse_iso_datetime(
        data.get("heartbeat_at") or data.get("last_progress_at")
        or data.get("last_activity_at") or data.get("updated_at")  # #310
    )
    if not updated:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    base = now or datetime.now(timezone.utc)
    seconds = (base - updated.astimezone(timezone.utc)).total_seconds()
    return seconds if seconds >= 0 else None


def _stale_active_seconds() -> int:
    raw = os.environ.get("MISSION_STALE_ACTIVE_SECONDS")
    if raw:
        try:
            parsed = int(raw)
            if parsed >= 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_STALE_ACTIVE_SECONDS


def _normalize_issue_ref(value):
    """issue_ref を比較用の正規化キーへ変換する (#295).

    同一 Issue を指す異なる形式 (裸番号 `42` / `#42` / `host:owner/repo#42` /
    `https://.../issues/42`) を同一キーへ畳み込み、形式差による重複見逃しを防ぐ。
    `.mission-state` は project (cwd) 単位のため、比較キーは Issue 番号を基準にする。
    数値を抽出できない参照は小文字化した生値をキーとする (後方互換)。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # URL 形式: .../issues/<n>
    m = re.search(r"/issues/(\d+)", raw)
    if m:
        return m.group(1)
    # 末尾 #<n> (例 host:owner/repo#42, gh:repo#99)
    m = re.search(r"#(\d+)\s*$", raw)
    if m:
        return m.group(1)
    # 裸番号 (先頭 # 任意)
    m = re.fullmatch(r"#?(\d+)", raw)
    if m:
        return m.group(1)
    # 数値を抽出できない参照は生値 (大文字小文字非依存) で比較
    return raw.lower()


def _pregate_state_reference(cwd: Path, issue_ref: Any) -> dict[str, Any] | None:
    record = inspect_pregate_cache(cwd, issue_ref)
    if record is None:
        return None
    return {
        "path": record["path"],
        "subject_digest": record["subject_digest"],
        "verdict": record["verdict"],
        "gate_id": record["gate_id"],
        "evaluated_at": record["evaluated_at"],
    }


def _pregate_verdict_warning(record: dict[str, Any] | None) -> str | None:
    if not record or not isinstance(record, dict):
        return None
    verdict = record.get("verdict")
    if verdict in {None, "accepted"}:
        return None
    return f"WARNING: pregate verdict={verdict}。planning 前に分割を解決してください"


def _ensure_phase_timing(data: dict, now: str | None = None) -> None:
    """phase 別所要時間の計測フィールドを後方互換で初期化する."""
    now = now or iso_now()
    if not isinstance(data.get("phase_durations_sec"), dict):
        data["phase_durations_sec"] = {}
    if not data.get("phase_started_at"):
        data["phase_started_at"] = data.get("started_at") or now


def _finite_nonnegative_phase_seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _record_terminal_phase_anomaly(data: dict) -> None:
    counts = data.get("activity_anomaly_counts")
    if not isinstance(counts, dict):
        counts = {}
        data["activity_anomaly_counts"] = counts
    current = counts.get("invalid-phase-terminal", 0)
    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
        current = 0
    counts["invalid-phase-terminal"] = current + 1


def _accrue_phase_for_terminal_control(
    data: dict, old_phase: object, now: str, *, trusted_boundary: bool
) -> bool:
    """Best-effort phase accrual that can never block terminal control."""
    if isinstance(old_phase, str) and old_phase in {"done", "halted"}:
        return True
    if not isinstance(old_phase, str) or old_phase not in {
        "planning",
        "executing",
        "reviewing",
        "scoring",
    }:
        _record_terminal_phase_anomaly(data)
        return False
    durations = data.get("phase_durations_sec")
    if durations is None:
        durations = {}
        data["phase_durations_sec"] = durations
    if not isinstance(durations, dict):
        _record_terminal_phase_anomaly(data)
        return False
    started_text = data.get("phase_started_at") or data.get("started_at")
    started = _parse_iso_datetime(started_text)
    ended = _parse_iso_datetime(now)
    if not started or not ended or ended < started:
        _record_terminal_phase_anomaly(data)
        return False
    boundary = ended
    if trusted_boundary:
        updated_text = data.get("updated_at")
        updated = _parse_iso_datetime(updated_text)
        if updated_text is not None and not updated:
            _record_terminal_phase_anomaly(data)
            return False
        boundary = updated or started
        if boundary < started or boundary > ended:
            _record_terminal_phase_anomaly(data)
            return False
    current = _finite_nonnegative_phase_seconds(durations.get(old_phase, 0))
    if current is None:
        _record_terminal_phase_anomaly(data)
        return False
    durations[old_phase] = current + (boundary - started).total_seconds()
    return True


def _transition_phase(
    data: dict,
    new_phase: str,
    now: str | None = None,
    *,
    terminal_trusted_boundary: bool = False,
) -> None:
    """phase を変更し、旧 phase の経過秒数を phase_durations_sec に加算する."""
    if new_phase == "reviewing":
        validate_artifact_state_consistency(data, require_resolved=True)
    now = now or iso_now()
    old_phase = data.get("phase")
    if new_phase in {"done", "halted"}:
        old_phase_is_resumable = isinstance(old_phase, str) and old_phase in {
            "planning",
            "executing",
            "reviewing",
            "scoring",
        }
        if new_phase == "halted" and terminal_trusted_boundary and old_phase_is_resumable:
            data["resume_target_phase"] = old_phase
        else:
            data.pop("resume_target_phase", None)
        _accrue_phase_for_terminal_control(
            data,
            old_phase,
            now,
            trusted_boundary=terminal_trusted_boundary,
        )
        # Terminal control must close an open activity even on a repeated
        # terminal command.  Malformed measurement is quarantined as an
        # anomaly by the shared reducer and cannot block pass/halt control.
        transition_activity_phase(
            data,
            new_phase,
            now,
            terminal_trusted_boundary=terminal_trusted_boundary,
        )
        data["phase_started_at"] = now
        data["phase"] = new_phase
        return
    _ensure_phase_timing(data, now)
    if old_phase and old_phase != new_phase:
        if new_phase not in {"done", "halted"}:
            transition_activity_phase(data, new_phase, now)
        started = _parse_iso_datetime(data.get("phase_started_at"))
        ended = _parse_iso_datetime(now)
        if started and ended:
            elapsed = (ended - started).total_seconds()
            if elapsed >= 0:
                durations = data.setdefault("phase_durations_sec", {})
                if not isinstance(durations, dict):
                    raise ActivityTimingError("phase durations are malformed")
                current = _finite_nonnegative_phase_seconds(durations.get(old_phase, 0))
                if current is None:
                    raise ActivityTimingError("phase duration is malformed")
                durations[old_phase] = current + elapsed
        data["phase_started_at"] = now
    data["phase"] = new_phase


def _resume_phase_timing(data: dict, now: str) -> None:
    """Accrue only the last observed pre-resume phase window, then restart it."""
    _ensure_phase_timing(data, now)
    phase = data.get("phase")
    if not phase or phase in {"done", "halted"}:
        return
    started = _parse_iso_datetime(data.get("phase_started_at"))
    updated = _parse_iso_datetime(data.get("updated_at"))
    resumed = _parse_iso_datetime(now)
    if not started or not resumed:
        data["phase_started_at"] = now
        return
    boundary = updated if updated and started <= updated <= resumed else started
    if updated and (updated < started or updated > resumed):
        raise ActivityTimingError("phase resume boundary is inconsistent")
    elapsed = (boundary - started).total_seconds()
    durations = data.setdefault("phase_durations_sec", {})
    current = _finite_nonnegative_phase_seconds(durations.get(phase, 0))
    if current is None:
        raise ActivityTimingError("phase duration is malformed")
    durations[phase] = current + elapsed
    data["phase_started_at"] = now


# #188: set phase= の正規値と別名マップ。実運用で `phase=execution` (typo) が
# 無検証で受理され stats の phase_duration_totals を汚染した実害への対策。
VALID_PHASES = {"planning", "executing", "reviewing", "scoring", "done", "halted"}
PHASE_ALIASES = {
    "execution": "executing",
    "review": "reviewing",
    "plan": "planning",
    "score": "scoring",
}


def _normalize_set_phase_value(value: str) -> str:
    """set phase=<value> の値を検証・正規化する (#188)。

    正規値はそのまま通す。既知の別名は正規化して WARN。それ以外は exit 2。
    push-score の items キーエイリアス正規化 (#H2) と同じ方針。
    """
    if value in VALID_PHASES:
        return value
    if value in PHASE_ALIASES:
        canonical = PHASE_ALIASES[value]
        print(
            f"WARNING [#188]: phase='{value}' は非正規値です。'{canonical}' として保存しました。",
            file=sys.stderr,
        )
        return canonical
    print(
        f"ERROR: phase の値 '{value}' は無効です。有効値: {sorted(VALID_PHASES)}"
        f" (既知の別名: {sorted(PHASE_ALIASES)})",
        file=sys.stderr,
    )
    sys.exit(2)


# #190: HALT_CATEGORIES は skills/mission/lib/mission_common.py で定義し、
# audit 側 (scripts/mission-audit.py) と共有する。実運用で「完了しました」という完了風の
# 自由文 halt_reason が threshold 未達 (min_item/composite gate) と混同され、stats/audit 上は
# 障害 halt と同じ HALT に集計されて root-cause review のたびに人が自由文を読み分けていた実害への対策。


def _normalize_halt_category(value: str | None) -> str:
    """mark-halt --category の検証。省略/不正値は 'other' + WARN (reject しない: halt 自体は
    緊急停止経路であり、カテゴリ不正で halt そのものを妨げるのは本末転倒)。"""
    if value is None:
        print(
            "WARNING [#190]: --category が未指定です。'other' として記録しました。"
            f" 可能ならカテゴリを指定してください: {sorted(HALT_CATEGORIES)}",
            file=sys.stderr,
        )
        return "other"
    if value not in HALT_CATEGORIES:
        print(
            f"WARNING [#190]: --category '{value}' は無効です。'other' として記録しました。"
            f" 有効値: {sorted(HALT_CATEGORIES)}",
            file=sys.stderr,
        )
        return "other"
    return value


def _write_terminal_outcome(data: dict) -> None:
    """Persist the outcome implied by an authorized terminal transition."""
    data.pop("terminal_outcome", None)
    outcome = derive_terminal_outcome(data)
    if outcome is None:
        raise ValueError("terminal transition did not produce a terminal outcome")
    data["terminal_outcome"] = outcome


def _halt_category_for_confirmation(value) -> str:
    """Normalize a persisted category for approval matching without mutating audit data."""
    if isinstance(value, str) and value in HALT_CATEGORIES:
        return value
    return "unknown"


def _is_legacy_stale_halt(category, reason) -> bool:
    """Recognize pre-category stale/orphan state consistently across all recovery paths."""
    category_is_legacy = category is None or category == "" or category == "unknown"
    return (
        category_is_legacy
        and isinstance(reason, str)
        and reason.startswith(("orphan:", "stale:"))
    )


# M7 (2026-06-10): SKILL.md Phase 1 の複雑度→Reviewer 数マッピング
# #266 (2026-07-23): Complex 3→2。discriminating-v1 で reviewer-wait が壁時計の 62% を
# 占め、3人目の限界検出価値がゼロだった実測に基づく。不可逆・security シグナルの
# エスカレータは full (3名) を維持するため、リスクありの Complex は従来どおり 3名。
COMPLEXITY_REVIEWER_COUNT = {"Simple": 1, "Standard": 2, "Complex": 2, "Critical": 3}

# Issue #168: review_tier による適応的レビュー深度
# tier → reviewer_count のマッピング (COMPLEXITY_REVIEWER_COUNT と同値になる設計)
TIER_REVIEWER_COUNT = {"light": 1, "standard": 2, "full": 3}
# complexity → review_tier のベースマッピング (#266: Complex は standard 起点、エスカレータで full へ)
REVIEW_TIER_BASE = {"Simple": "light", "Standard": "standard", "Complex": "standard", "Critical": "full"}

# 不可逆系キーワード (英語) — 小文字化して部分一致
# Issue #174 で 505 mission 遡及分析に基づき較正: push / merge を除外 (標準 dev フロー誤発火)
_RELEASE_NOUN_FOLLOWERS_RE = re.compile(
    r"\A\s+(?:\d|v\d|brief\b|notes\b|mission\b)", re.IGNORECASE
)


def _release_noun_reference(mission_text: str, start: int, end: int) -> bool:
    """#313: "release" の名詞参照 (版名・文書名) 判定。

    実運用監査 (2026-08-01) で FP 36% の実測: "Release 6" / "Release brief" /
    "release notes" / "Release Mission #582" が Standard→full へ誤昇格していた。
    直後が数字・版番号・brief/notes/mission の場合のみ名詞参照として suppress する
    (保守的ホワイトリスト。"release the hotfix" 等の動詞用法は対象外)。
    """
    return bool(_RELEASE_NOUN_FOLLOWERS_RE.match(mission_text[end:]))


_IRREVERSIBLE_KEYWORDS_EN = (
    "deploy", "release", "migration", "drop", "delete",
    "publish", "production",
)
# 不可逆系キーワード (日本語) — そのまま部分一致
# Issue #174 で 505 mission 遡及分析に基づき較正: 単体「削除」を除外し複合語に置換 (可逆なコード変更への誤発火)
_IRREVERSIBLE_KEYWORDS_JA = ("本番", "リリース", "マイグレーション", "データ削除", "レコード削除", "物理削除", "公開", "決済")
# 「公開」複合語抑制の技術名詞 (#450)。「クラス」は否定先読みで「クラスタ/クラスター」
# (cluster = 不可逆な公開操作の対象になり得る) への誤マッチを除外する。
_REVIEW_TECHNICAL_NOUN_SUFFIX_RE = re.compile(
    r"(?:API|Api|api|関数|メソッド|クラス(?![ァ-ヶー])|インターフェース"
    r"|プロパティ|属性|型|モジュール|フィールド)"
)
# セキュリティ系キーワード (英語) — 小文字化して部分一致
# Issue #174 で 505 mission 遡及分析に基づき較正:
#   token → 複合語 (api token / api-token / api_key / access token / access-token / bearer) に置換
#   auth  → 語幹 (authenticat / authoriz / oauth) に置換 (authority 等への誤発火を排除)
_SECURITY_KEYWORDS_EN = (
    "secret", "credential", "password",
    "api token", "api-token", "api_key",
    "access token", "access-token", "bearer",
    "authenticat", "authoriz", "oauth",
)
# セキュリティ系キーワード (日本語) — そのまま部分一致
_SECURITY_KEYWORDS_JA = ("認証", "秘密", "鍵")


_REVIEW_CONTEXT_BOUNDARY_RE = re.compile(
    r"[。.!！?？;；\n]+|(?:だが|けど|けれど|ただし|しかし|一方(?:で)?)[、,]?\s*|"
    r"\band(?:\s+then)?\b\s*|\bbut\b\s*|\bhowever\b[\s,]*",
    re.IGNORECASE,
)
_REVIEW_UNIT_BOUNDARY_RE = re.compile(
    r"\n[ \t]*\n+|\n(?=[ \t]*(?:[-*+>]|#{1,6}\s|\d+[.)])\s*)",
    re.IGNORECASE,
)
_REVIEW_DOUBLE_NEGATION_RE = re.compile(
    r"(?:し|行わ|実行し)ないわけではない|(?:し|行わ|実行し)ないとは限らない|"
    r"(?:しない|行わない|実行しない)\s*"
    r"(?:(?:(?:予定|方針|計画)\s*)?(?:ではない|はない)|とは(?:言って|述べて)いない)|"
    r"なくはない|禁止ではない|対象外ではない|"
    r"(?:\b(?:do|does|did|will|would|should|must|can|could)\s+not\s+not|"
    r"\b[a-z]+n['’]t\s+not|\bcannot\s+not|\bnever\s+not)\s+"
    r"(?:(?:perform|execute)\s+(?:a|an|the)?\s*)?"
    r"(?:production\s+)?(?:deploy(?:ment)?|release|migration|drop|delete|publish)\b|"
    r"\b(?:it(?:(?:\s+(?:is|was)|['’](?:s|d))\s+not\s+"
    r"(?:the\s+case|true)|\s+[a-z]+n['’]t\s+(?:the\s+case|true))\s+that|"
    r"there\s+(?:(?:(?:is|was)|['’](?:s|d))\s+no|"
    r"[a-z]+n['’]t\s+(?:a|any))\s+"
    r"(?:guarantee|assurance|certainty)\s+that|"
    r"(?:i|we|they|he|she|it)\s+(?:(?:(?:am|is|are|was|were)\s+not|"
    r"[a-z]+n['’]t)\s+"
    r"(?:say(?:ing)?|claim(?:ing)?|stat(?:e|ing))|"
    r"(?:cannot|can['’]t)\s+(?:say|claim|state))\s+(?:that\s+)?)"
    r"[^.!?;\n]{0,80}?"
    r"(?:\b(?:do|does|did|will|would|should|must|can|could)\s+not|"
    r"\b[a-z]+n['’]t|\bcannot|\bnever)\s+"
    r"(?:(?:perform|execute)\s+(?:a|an|the)?\s*)?"
    r"(?:production\s+)?(?:deploy(?:ment)?|release|migration|drop|delete|publish)\b|"
    r"\bnot\s+impossible\b|\bnot\s+never\b|\bcannot\s+rule\s+out\b",
    re.IGNORECASE,
)
_REVIEW_CONDITIONAL_RE = re.compile(
    r"必要なら|必要な場合|場合|可能なら|可能性|かもしれ|未確定|検討中|するか|し得|あり得|"
    r"限り|以外|除く|除き|"
    r"原則(?!\s*ではなく(?:[、,]\s*)?絶対に)|ことがある|"
    r"例外(?!\s*なく)|緊急時(?!\s*に?も)|"
    r"\bonly\s+if\b|\bif\b|\bunless\b|\bmay\b|\bmight\b|\bcould\b|\bpossibly\b|\bwhether\b|"
    r"\bexcept\s+(?:(?:when|in)\s+)?(?:emergenc(?:y|ies)|authorized|approved|approval)\b|"
    r"\buntil\b|\bpending\s+(?:approval|authorization|permission)\b|"
    r"\b(?:before|prior\s+to)\s+(?:(?:the|final)\s+)?"
    r"(?:approval|authorization|permission)\b|"
    r"\bwhile\s+(?:(?:the|final)\s+)?(?:approval|authorization|permission)\s+"
    r"(?:is|remains)\s+pending\b|"
    r"\bwithout\s+(?:approval|authorization|permission)\b",
    re.IGNORECASE,
)
_REVIEW_EN_NEGATED_OPERATION_RE = re.compile(
    r"(?:\b(?:do|does|did|will|would|should|must|can|could)\s+not|\bnot|"
    r"\bnever|\b[a-z]+n['’]t|\bcannot|"
    r"\b(?:am|is|are|was|were)\s+not\s+going\s+to)\s+"
    r"(?:(?:perform|execute)\s+(?:a|an|the)?\s*)?"
    r"(?:production\s+)?(?:deploy(?:ment)?|release|migration|drop|delete|publish)"
    r"(?:\s+(?:to|in)\s+(?:(?:our|the)\s+)?(?:target\s+)?production(?:\s+environment)?)?|"
    r"(?:production\s+)?(?:deploy(?:ment)?|release|migration|drop|delete|publish)"
    r"(?:\s+(?:to|in)\s+(?:(?:our|the)\s+)?(?:target\s+)?production(?:\s+environment)?)?\s+"
    r"(?:(?:is|are|was|were)\s+"
    r"(?:not\s+(?:performed|executed|deployed|released|published|planned)|out\s+of\s+scope)|"
    r"(?:will|would|should|must|can|could)\s+not\s+be\s+"
    r"(?:performed|executed|deployed|released|published))\b",
    re.IGNORECASE,
)
_REVIEW_JA_POST_NEGATION_RE = re.compile(
    r"^\s*(?:は|を|も|が|では|には|へ|に|で)?\s*"
    r"(?:(?:実行|実施)\s*)?(?:しない方針|しない|しません|せず)|"
    r"^\s*(?:は|を|も|が|では|には|へ|に|で)?\s*"
    r"(?:行わない|行われない|行いません|行わず|禁止|対象外)|"
    r"^\s*する予定はない",
)
_REVIEW_HONBAN_QUALIFIER_RE = re.compile(
    r"^\s*(?:環境)?(?:への|へ|で|に|の)?\s*(?:deploy|release|publish|リリース|公開)\s*"
    r"(?:は|を)?\s*(?:(?:実行|実施)\s*)?"
    r"(?:しない方針|しない|しません|せず|行わない|行われない|行いません|行わず|"
    r"禁止|対象外|する予定はない)",
    re.IGNORECASE,
)
_REVIEW_GLOBAL_NON_OPERATION_RE = re.compile(
    r"実操作\s*(?:は|を)?\s*(?:行わない|実行しない|しない)|"
    r"\bactual\s+(?:operation|execution)s?\s+(?:will\s+)?not\s+(?:be\s+)?(?:performed|executed)\b|"
    r"\bno\s+actual\s+(?:operation|execution)s?\b",
    re.IGNORECASE,
)
_REVIEW_META_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"(?:review|analy[sz]e|document|describe|explain|inspect)\s+"
    r"(?:the\s+)?(?:deploy(?:ment)?|release|migration|publish|production)\s+"
    r"(?:procedures?|instructions?|settings?|logs?|text)|"
    r"(?:deploy(?:ment)?|release|migration|publish|production)\s+"
    r"(?:procedures?|instructions?|settings?|logs?|text)\s+"
    r"(?:review|analysis|documentation|description|inspection)|"
    r"(?:deploy|release|publish|migration|production|本番|リリース|マイグレーション|公開)"
    r"[^。.!！?？;；\n]{0,48}(?:手順|設定|文言|ログ)\s*(?:を|の)?\s*"
    r"(?:調査|確認|分析|説明|文書化|レビュー)(?:する|します)?"
    r")\s*$",
    re.IGNORECASE,
)
_REVIEW_QUOTED_EXECUTION_BEFORE_RE = re.compile(
    r"\b(?:execute|run|perform)\s+(?:(?:the\s+)?quoted\s+)?$",
    re.IGNORECASE,
)
_REVIEW_QUOTED_EXECUTION_AFTER_RE = re.compile(
    r"^[」』\"`]\s*(?:"
    r"(?:を|は)\s*(?:実際に)?(?:実行|実施|行う)|"
    r"(?:will|must|should)\s+be\s+(?:executed|performed|released|published)\b"
    r")",
    re.IGNORECASE,
)
_REVIEW_QUOTE_ONLY_RE = re.compile(
    r"引用するだけ|引用(?:する|のみ|だけ)|\bquote(?:d|s|ing)?\s+only\b|\bonly\s+quot(?:e|ed|ing)\b",
    re.IGNORECASE,
)
_REVIEW_NEGATION_CUE_RE = re.compile(
    r"しない|行わない|実行しない|ではない|はない|言っていない|述べていない|ない|"
    r"\bnot\b|\bnever\b|\bcannot\b|\b(?:don|won|can)['’]t\b",
    re.IGNORECASE,
)
_REVIEW_CAUSAL_ASSURANCE_AFTER_NEGATION_RE = re.compile(
    r"(?:しない|行わない|実行しない)\s*(?:ので|から|ため)\s*"
    r"(?:問題|支障|懸念|影響)(?:は|が)?\s*ない\s*$"
)
_REVIEW_EXECUTION_CUE_RE = re.compile(
    r"\b(?:execute(?:d)?|run|perform(?:ed)?|carry\s+(?:(?:it|that|this)\s+)?out|"
    r"(?:follow|apply|proceed\s+with)\s+"
    r"(?:it|them|that|this|those|the\s+(?:procedures?|instructions?|steps?))|"
    r"released|published)\b|"
    r"(?:実行|実施|反映|適用)(?:する|します|した|する予定)?|"
    r"(?:それ|それら|これ|これら|その(?:手順|指示|設定))(?:に|を)?従う|"
    r"行う|行います",
    re.IGNORECASE,
)
_REVIEW_NAMED_EXECUTION_RE = re.compile(
    r"\b(?:actually\s+)?(?:execute|run|perform|carry\s+out)\s+"
    r"(?:(?:the|a|an)\s+)?[\"`]?"
    r"(?:deploy(?:ment)?|release|migration|publish|production)\b|"
    r"[\"`]?(?:deploy(?:ment)?|release|migration|publish|production)[\"`]?\s+"
    r"(?:(?:will|must|should)\s+be|(?:is|are|was|were))\s+"
    r"(?:executed|performed|released|published)\b|"
    r"(?:deploy|release|publish|migration|production|本番|リリース|マイグレーション|公開)"
    r"[」』\"`]?\s*(?:を|は|が|も)?\s*"
    r"(?:(?:実行|実施|反映)(?:する|します|した|する予定)?|"
    r"行う|行います)",
    re.IGNORECASE,
)
_REVIEW_QUOTE_RESIDUAL_NOISE_RE = re.compile(
    r"[\s,.;:!?、。；：！？\"`「」『』\-*+>]+|"
    r"\b(?:and|but|then|however)\b|"
    r"(?:という文言を手順書に|という文言を|手順書に|"
    r"だが|けど|けれど|ただし|しかし|その後|は|を|が|も)",
    re.IGNORECASE,
)


def _review_keyword_matches(text: str, keyword: str, *, ignore_case: bool) -> list[re.Match]:
    """Return every literal keyword occurrence in source order."""
    flags = re.IGNORECASE if ignore_case else 0
    return list(re.finditer(re.escape(keyword), text, flags))


def _review_segment_index(text: str, boundary_re: re.Pattern) -> tuple[list[int], list[tuple[int, int]]]:
    """Precompute trimmed text segments once for O(log n) per-match lookup."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for boundary in boundary_re.finditer(text):
        start, end = cursor, boundary.start()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
        cursor = boundary.end()
    start, end = cursor, len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end))
    if not spans:
        spans.append((0, len(text)))
    return [start for start, _ in spans], spans


def _review_segment_span(
    index: tuple[list[int], list[tuple[int, int]]],
    start: int,
    end: int,
) -> tuple[int, int]:
    """Find the precomputed segment containing a source span."""
    starts, spans = index
    position = max(0, bisect_right(starts, start) - 1)
    segment_start, segment_end = spans[position]
    if segment_start <= start and end <= segment_end:
        return segment_start, segment_end
    return start, end


def _review_has_technical_noun_suffix(mission_text: str, start: int, end: int) -> bool:
    """Suppress public-operation keywords when they form a compound technical noun."""
    suffix = mission_text[end:].lstrip()
    return bool(_REVIEW_TECHNICAL_NOUN_SUFFIX_RE.match(suffix))


def _review_quote_span_index(
    text: str,
    offset: int,
) -> tuple[list[int], list[tuple[int, int]]]:
    """Index common quoted ranges once for constant/logarithmic match lookup."""
    spans: list[tuple[int, int]] = []
    paired_open: dict[str, list[int]] = {"「": [], "『": []}
    paired_close = {"」": "「", "』": "『"}
    symmetric_open: dict[str, int | None] = {'"': None, "`": None}
    for position, char in enumerate(text):
        absolute = offset + position
        if char in paired_open:
            paired_open[char].append(absolute)
        elif char in paired_close:
            opening = paired_close[char]
            if paired_open[opening]:
                spans.append((paired_open[opening].pop() + 1, absolute))
        elif char in symmetric_open and (position == 0 or text[position - 1] != "\\"):
            opening = symmetric_open[char]
            if opening is None:
                symmetric_open[char] = absolute
            else:
                spans.append((opening + 1, absolute))
                symmetric_open[char] = None
    absolute_end = offset + len(text)
    for openings in paired_open.values():
        spans.extend((opening + 1, absolute_end) for opening in openings)
    spans.extend(
        (opening + 1, absolute_end)
        for opening in symmetric_open.values()
        if opening is not None
    )
    spans.sort()
    return [start for start, _ in spans], spans


def _review_regex_span_index(
    text: str,
    offset: int,
    pattern: re.Pattern,
) -> tuple[list[int], list[tuple[int, int]]]:
    spans = [(offset + match.start(), offset + match.end()) for match in pattern.finditer(text)]
    return [start for start, _ in spans], spans


def _review_index_container(
    index: tuple[list[int], list[tuple[int, int]]],
    start: int,
    end: int,
) -> tuple[int, int] | None:
    starts, spans = index
    position = bisect_right(starts, start) - 1
    while position >= 0 and spans[position][0] <= start:
        span_start, span_end = spans[position]
        if span_start <= start and end <= span_end:
            return span_start, span_end
        position -= 1
    return None


def _review_index_contains(
    index: tuple[list[int], list[tuple[int, int]]],
    start: int,
    end: int,
) -> bool:
    return _review_index_container(index, start, end) is not None


def _review_text_has_ambiguous_execution(text: str) -> bool:
    """Find execution intent not explained by a directly named operation target."""
    named_targets = _review_regex_span_index(text, 0, _REVIEW_NAMED_EXECUTION_RE)
    quote_spans = _review_quote_span_index(text, 0)
    non_operation_markers = _review_regex_span_index(
        text,
        0,
        _REVIEW_GLOBAL_NON_OPERATION_RE,
    )
    return any(
        not _review_index_contains(named_targets, match.start(), match.end())
        and not _review_index_contains(quote_spans, match.start(), match.end())
        and not _review_index_contains(
            non_operation_markers,
            match.start(),
            match.end(),
        )
        for match in _REVIEW_EXECUTION_CUE_RE.finditer(text)
    )


def _review_text_has_quote_only_unknown_residual(text: str) -> bool:
    """Require quote-only intent plus known named actions to explain the whole unit."""
    quote_spans = _review_quote_span_index(text, 0)[1]
    removable_spans = [
        *quote_spans,
        *[(match.start(), match.end()) for match in _REVIEW_QUOTE_ONLY_RE.finditer(text)],
        *[(match.start(), match.end()) for match in _REVIEW_NAMED_EXECUTION_RE.finditer(text)],
        *[(match.start(), match.end()) for match in _REVIEW_GLOBAL_NON_OPERATION_RE.finditer(text)],
    ]
    masked = list(text)
    for start, end in removable_spans:
        masked[start:end] = " " * (end - start)
    residual = _REVIEW_QUOTE_RESIDUAL_NOISE_RE.sub("", "".join(masked))
    return bool(residual)


def _review_quote_has_execution_target(
    mission_text: str,
    quote_span: tuple[int, int],
) -> bool:
    """Require execution language to grammatically target the quoted command."""
    quote_start, quote_end = quote_span
    before = mission_text[max(0, quote_start - 80):max(0, quote_start - 1)]
    after = mission_text[quote_end:min(len(mission_text), quote_end + 80)]
    return bool(
        _REVIEW_QUOTED_EXECUTION_BEFORE_RE.search(before)
        or _REVIEW_QUOTED_EXECUTION_AFTER_RE.search(after)
    )


def _review_context_analysis(
    mission_text: str,
    context_span: tuple[int, int],
    cache: dict[tuple[int, int], dict],
) -> dict:
    """Compute regex flags and span indexes once for each logical context."""
    if context_span not in cache:
        start, end = context_span
        context = mission_text[start:end]
        quote_index = _review_quote_span_index(context, start)
        cache[context_span] = {
            "text": context,
            "double_negation": bool(_REVIEW_DOUBLE_NEGATION_RE.search(context)),
            "conditional": bool(_REVIEW_CONDITIONAL_RE.search(context)),
            "quote_only": bool(_REVIEW_QUOTE_ONLY_RE.search(context)),
            "quotes": quote_index,
            "meta_only": bool(_REVIEW_META_ONLY_RE.fullmatch(context)),
            "negation_cue_starts": [
                match.start() for match in _REVIEW_NEGATION_CUE_RE.finditer(context)
            ],
            "actual_operation_starts": sorted({
                match.start()
                for keywords, ignore_case in (
                    (_IRREVERSIBLE_KEYWORDS_EN, True),
                    (_IRREVERSIBLE_KEYWORDS_JA, False),
                )
                for keyword in keywords
                for match in _review_keyword_matches(
                    context,
                    keyword,
                    ignore_case=ignore_case,
                )
                if not (
                    keyword == "公開"
                    and _review_has_technical_noun_suffix(context, match.start(), match.end())
                )
            }),
            "negated_operations": _review_regex_span_index(
                context,
                start,
                _REVIEW_EN_NEGATED_OPERATION_RE,
            ),
        }
    return cache[context_span]


def _review_audit_context(text: str, start: int, end: int, *, radius: int = 80) -> str:
    """Keep persisted provenance readable and bounded for long mission descriptions."""
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = "…" if left else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{text[left:right].strip()}{suffix}"


def _review_operation_is_explicitly_negated(
    context: str,
    keyword: str,
    relative_start: int,
    relative_end: int,
    absolute_start: int,
    absolute_end: int,
    negated_operations: tuple[list[int], list[tuple[int, int]]],
) -> bool:
    """Suppress only when negation is grammatically anchored to this operation."""
    after = context[relative_end:min(len(context), relative_end + 64)]
    if _review_index_contains(negated_operations, absolute_start, absolute_end):
        return True
    if _REVIEW_JA_POST_NEGATION_RE.search(after):
        return True
    # 本番 is a qualifier preceding the directly negated operation.
    if keyword == "本番" and _REVIEW_HONBAN_QUALIFIER_RE.search(after):
        return True
    return False


def _review_operation_has_negation_reversal(
    context: str,
    cue_starts: list[int],
    operation_starts: list[int],
    relative_start: int,
    relative_end: int,
) -> bool:
    """Treat multiple post-operation negation cues as ambiguous, never suppressible."""
    next_operation_index = bisect_right(operation_starts, relative_start)
    limit = (
        operation_starts[next_operation_index]
        if next_operation_index < len(operation_starts)
        else None
    )
    first_cue = bisect_left(cue_starts, relative_end)
    final_cue = bisect_left(cue_starts, limit) if limit is not None else len(cue_starts)
    cue_count = final_cue - first_cue
    if cue_count < 2:
        return False
    cue_end = limit if limit is not None else len(context)
    if cue_count == 2 and _REVIEW_CAUSAL_ASSURANCE_AFTER_NEGATION_RE.fullmatch(
        context[cue_starts[first_cue]:cue_end]
    ):
        return False
    return True


def _actual_operation_signal_detail(
    mission_text: str,
    keyword: str,
    match: re.Match,
    signal: str,
    context_index: tuple[list[int], list[tuple[int, int]]],
    unit_index: tuple[list[int], list[tuple[int, int]]],
    context_analysis_cache: dict[tuple[int, int], dict],
    unit_flags_cache: dict[tuple[int, int], dict],
) -> dict:
    start, end = match.span()
    context_start, context_end = _review_segment_span(context_index, start, end)
    context_analysis = _review_context_analysis(
        mission_text,
        (context_start, context_end),
        context_analysis_cache,
    )
    logical_context = context_analysis["text"]
    unit_start, unit_end = _review_segment_span(unit_index, start, end)
    relative_start = start - context_start
    relative_end = end - context_start
    quote_span = _review_index_container(context_analysis["quotes"], start, end)
    quoted = quote_span is not None
    quoted_execution_target = bool(
        quote_span and _review_quote_has_execution_target(mission_text, quote_span)
    )
    quote_only = context_analysis["quote_only"]
    unit_key = (unit_start, unit_end)
    if unit_key not in unit_flags_cache:
        logical_unit = mission_text[unit_start:unit_end]
        unit_flags_cache[unit_key] = {
            "global_markers": _review_regex_span_index(
                logical_unit,
                unit_start,
                _REVIEW_GLOBAL_NON_OPERATION_RE,
            ),
            "double_negation": bool(_REVIEW_DOUBLE_NEGATION_RE.search(logical_unit)),
            "conditional": bool(_REVIEW_CONDITIONAL_RE.search(logical_unit)),
            "ambiguous_execution": bool(
                _review_text_has_ambiguous_execution(logical_unit)
            ),
            "quote_only_unknown_residual": (
                _review_text_has_quote_only_unknown_residual(logical_unit)
            ),
        }
    unit_flags = unit_flags_cache[unit_key]
    global_markers = unit_flags["global_markers"]
    global_non_operation = bool(global_markers[1])
    unit_double_negation = unit_flags["double_negation"]
    unit_conditional = unit_flags["conditional"]
    unit_ambiguous_execution = unit_flags["ambiguous_execution"]
    quote_only_unknown_residual = unit_flags["quote_only_unknown_residual"]

    if keyword == "release" and _release_noun_reference(mission_text, start, end):
        # #313: 版名・文書名への substring マッチはエスカレート対象外 (監査 FP 36%)
        decision, reason = "suppressed", "noun-reference-non-operation"
    elif keyword == "公開" and _review_has_technical_noun_suffix(mission_text, start, end):
        decision, reason = "suppressed", "compound-technical-noun"
    elif quoted and quoted_execution_target:
        decision, reason = "included", "affirmative-actual-operation"
    elif quoted and quote_only and quote_only_unknown_residual:
        decision, reason = "included", "ambiguous-execution-reference"
    elif quoted and unit_ambiguous_execution:
        decision, reason = "included", "ambiguous-execution-reference"
    elif quoted and quote_only:
        decision, reason = "suppressed", "quoted-non-operation"
    elif quoted:
        decision, reason = "included", "quoted-context-conservative"
    elif context_analysis["double_negation"] or _review_operation_has_negation_reversal(
        logical_context,
        context_analysis["negation_cue_starts"],
        context_analysis["actual_operation_starts"],
        relative_start,
        relative_end,
    ):
        decision, reason = "included", "uncertain-or-double-negation"
    elif context_analysis["conditional"] or unit_conditional:
        decision, reason = "included", "conditional-or-uncertain-context"
    elif _review_operation_is_explicitly_negated(
        logical_context,
        keyword,
        relative_start,
        relative_end,
        start,
        end,
        context_analysis["negated_operations"],
    ):
        decision, reason = "suppressed", "negated-actual-operation"
    elif global_non_operation and unit_double_negation:
        decision, reason = "included", "uncertain-or-double-negation"
    elif global_non_operation and unit_conditional:
        decision, reason = "included", "conditional-or-uncertain-context"
    elif global_non_operation and unit_ambiguous_execution:
        decision, reason = "included", "ambiguous-execution-reference"
    elif global_non_operation and context_analysis["meta_only"]:
        decision, reason = "suppressed", "global-explicit-non-operation"
    elif global_non_operation:
        decision, reason = "included", "contradictory-global-operation"
    else:
        decision, reason = "included", "affirmative-actual-operation"

    return {
        "signal": signal,
        "category": "actual-operation",
        "keyword": keyword,
        "match": match.group(0),
        "context": _review_audit_context(mission_text, start, end),
        "decision": decision,
        "reason": reason,
        "source": "mission_text",
        "start": start,
        "end": end,
    }


def _conservative_signal_detail(
    mission_text: str,
    keyword: str,
    match: re.Match,
    signal: str,
) -> dict:
    start, end = match.span()
    return {
        "signal": signal,
        "category": "security",
        "keyword": keyword,
        "match": match.group(0),
        "context": _review_audit_context(mission_text, start, end),
        "decision": "included",
        "reason": "security-context-conservative",
        "source": "mission_text",
        "start": start,
        "end": end,
    }


def derive_review_tier_decision(
    mission_text: str,
    complexity: str | None,
    task_profile_risk: str | None = None,
) -> dict:
    """Derive tier plus additive per-occurrence decision provenance (Issue #209)."""
    base_tier = REVIEW_TIER_BASE.get(complexity or "", "standard")
    signals: list[str] = []
    signal_details: list[dict] = []
    context_index = _review_segment_index(mission_text, _REVIEW_CONTEXT_BOUNDARY_RE)
    unit_index = _review_segment_index(mission_text, _REVIEW_UNIT_BOUNDARY_RE)
    context_analysis_cache: dict[tuple[int, int], dict] = {}
    unit_flags_cache: dict[tuple[int, int], dict] = {}

    if task_profile_risk == "high":
        signal = "task_profile.risk=high"
        signals.append(signal)
        signal_details.append({
            "signal": signal,
            "category": "task-profile-risk",
            "keyword": "high",
            "match": "high",
            "context": "task_profile.risk=high",
            "decision": "included",
            "reason": "task-profile-high-risk",
            "source": "task_profile.risk",
            "start": None,
            "end": None,
        })

    for keywords, ignore_case in (
        (_IRREVERSIBLE_KEYWORDS_EN, True),
        (_IRREVERSIBLE_KEYWORDS_JA, False),
    ):
        for keyword in keywords:
            signal = f"irreversible-keyword:{keyword}"
            details = [
                _actual_operation_signal_detail(
                    mission_text,
                    keyword,
                    match,
                    signal,
                    context_index,
                    unit_index,
                    context_analysis_cache,
                    unit_flags_cache,
                )
                for match in _review_keyword_matches(mission_text, keyword, ignore_case=ignore_case)
            ]
            signal_details.extend(details)
            if any(item["decision"] == "included" for item in details):
                signals.append(signal)

    for keywords, ignore_case in (
        (_SECURITY_KEYWORDS_EN, True),
        (_SECURITY_KEYWORDS_JA, False),
    ):
        for keyword in keywords:
            signal = f"security-keyword:{keyword}"
            matches = _review_keyword_matches(mission_text, keyword, ignore_case=ignore_case)
            signal_details.extend(
                _conservative_signal_detail(mission_text, keyword, match, signal)
                for match in matches
            )
            if matches:
                signals.append(signal)

    tier = "full" if signals and base_tier != "full" else base_tier
    return {
        "mission_text": mission_text,
        "base_tier": base_tier,
        "tier": tier,
        "signals": signals,
        "signal_details": signal_details,
    }


def derive_review_tier(
    mission_text: str,
    complexity: str | None,
    task_profile_risk: str | None = None,
) -> tuple[str, list[str]]:
    """review_tier と signals を導出する純関数 (Issue #168).

    ベースは REVIEW_TIER_BASE から取得し、エスカレータ条件を満たす場合は "full" に昇格する。
    降格ロジックは存在しない。

    Args:
        mission_text: ミッション記述テキスト
        complexity: 複雑度文字列 ("Simple" | "Standard" | "Complex" | "Critical" | None | "Unknown")
        task_profile_risk: task_profile の risk 値 (オプション)

    Returns:
        (tier, signals): tier は "light"/"standard"/"full"、signals はエスカレータ理由リスト
    """
    decision = derive_review_tier_decision(mission_text, complexity, task_profile_risk)
    return decision["tier"], decision["signals"]


def _parse_files_arg(files: str | None) -> list[str]:
    """--files のカンマ区切りを project-root 相対パスのリストに正規化する."""
    if not files:
        return []
    return [p.strip() for p in files.split(",") if p.strip()]


def _warn_s3_file_overlap(cwd: Path, planned_files: list[str], cur_sid: str) -> None:
    """同一 project 内 active session の planned_files 重複を WARN する (reject はしない)."""
    planned = set(planned_files)
    if not planned:
        return
    for sf_other in _iter_state_files(cwd):
        try:
            other = json.loads(sf_other.read_text())
        except Exception:
            continue
        if not other.get("loop_active") or other.get("session_id") == cur_sid:
            continue
        overlap = planned & set(other.get("planned_files") or [])
        if overlap:
            print(
                f"WARNING [S3-files]: active session {other.get('session_id', '?')} "
                f"と対象ファイルが重複: {sorted(overlap)}。マージ衝突の可能性を確認。",
                file=sys.stderr,
            )
            break


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _coerce_scalar(value: str):
    value = value.strip().strip('"').strip("'")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _coerce_yaml_value(value: str):
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_coerce_scalar(v) for v in inner.split(",") if v.strip()]
    return _coerce_scalar(value)


def _parse_specialist_registry_text(txt: str) -> list[dict]:
    """Parse the documented version 1 JSON/limited-YAML registry shape."""
    try:
        data = _strict_json_loads(txt)
        return list(data.get("specialists") or [])
    except json.JSONDecodeError:
        pass

    specialists: list[dict] = []
    in_specialists = False
    cur: dict | None = None
    nested_key: str | None = None
    for raw in txt.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if not raw.startswith(" ") and stripped.endswith(":"):
            in_specialists = stripped == "specialists:"
            nested_key = None
            continue
        if not in_specialists:
            continue
        if stripped.startswith("- "):
            if cur:
                specialists.append(cur)
            cur = {}
            nested_key = None
            rest = stripped[2:].strip()
            if rest and ":" in rest:
                k, v = rest.split(":", 1)
                key = k.strip()
                if v.strip():
                    cur[key] = _coerce_yaml_value(v)
                else:
                    cur[key] = {}
                    nested_key = key
            continue
        if cur is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            key = k.strip()
            if nested_key and indent >= 6:
                nested = cur.setdefault(nested_key, {})
                if isinstance(nested, dict):
                    nested[key] = _coerce_yaml_value(v)
                continue
            if v.strip():
                cur[key] = _coerce_yaml_value(v)
                nested_key = None
            else:
                cur[key] = {}
                nested_key = key
    if cur:
        specialists.append(cur)
    return specialists


def _load_specialist_registry(path: str | None) -> list[dict]:
    """Compatibility loader for callers that explicitly request the legacy parser."""
    if not path:
        return []
    p = Path(path).expanduser()
    try:
        raw = p.read_bytes()
        return _parse_specialist_registry_text(raw.decode("utf-8"))
    except (OSError, UnicodeError):
        return []


def _registry_arg_paths(value) -> list[Path]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    paths: list[Path] = []
    for item in values:
        paths.extend(Path(p).expanduser() for p in _split_csv(item))
    return paths


def _load_registry_candidates(raw: bytes, source: str) -> list[dict]:
    candidates = []
    try:
        items = _parse_specialist_registry_text(raw.decode("utf-8"))
    except UnicodeError:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate["source"] = source
        candidate["registry_version"] = 1
        if "activation" in candidate or "disabled" in candidate:
            candidate["_registry_error"] = "mixed-registry-version"
        candidate["registry_entry_digest"] = registry_entry_digest(candidate)
        candidates.append(candidate)
    return candidates


def _load_v2_registry_candidates(raw: bytes, source: str) -> tuple[list[dict], list[dict]]:
    try:
        items = parse_v2_registry_json(raw.decode("utf-8"))
    except (UnicodeError, RegistryContractError) as error:
        return [], [{
            "provider_id": "<registry>",
            "source": source,
            "reason_code": getattr(error, "code", "invalid-registry-contract"),
            "detail": str(error),
        }]
    candidates = []
    for item in items:
        candidate = dict(item)
        candidate["source"] = source
        candidate["registry_version"] = 2
        candidate["_v2_auto_use_present"] = "auto_use" in item
        candidate["registry_entry_digest"] = registry_entry_digest(candidate)
        candidates.append(candidate)
    return candidates, []


def _portable_registry_identity(path: Path) -> str:
    resolved = Path(os.path.abspath(os.fspath(path.expanduser())))
    for anchor, label in (
        (Path.cwd().resolve(strict=False), "$PROJECT"),
        (Path.home().resolve(strict=False), "$HOME"),
    ):
        try:
            relative = resolved.relative_to(anchor)
        except ValueError:
            continue
        suffix = relative.as_posix()
        return label if suffix == "." else f"{label}/{suffix}"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()
    return f"$EXTERNAL/sha256:{digest}"


def _registry_source(path: Path, kind: str) -> str:
    identity = _portable_registry_identity(path)
    if kind == "explicit":
        return f"registry:{identity}"
    if kind == "installed":
        return f"skill-manifest:{identity}"
    if kind == "skill-root":
        return f"skill-root:{identity}"
    return identity


def _read_registry_input(
    path: Path, source: str, kind: str, version: int, tier: int, order: int
) -> tuple[
    dict,
    bytes | None,
    tuple[int, int] | None,
    RegistryContractError | None,
]:
    record = {
        "kind": kind,
        "source": source,
        "canonical_identity": _portable_registry_identity(path),
        "version": version,
        "precedence_tier": tier,
        "order": order,
        "status": "missing",
        "content_digest": None,
    }
    if record["canonical_identity"].startswith("$EXTERNAL/sha256:"):
        record["resolution_mode"] = "explicit-resupply-required"
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
    except OSError as error:
        if error.errno not in {errno.ENOENT, errno.ENOTDIR}:
            record["status"] = "invalid"
            return (
                record,
                None,
                None,
                RegistryContractError("registry-input-unreadable"),
            )
        return record, None, None, None
    physical_identity: tuple[int, int] | None = None
    try:
        before = os.fstat(fd)
        physical_identity = (before.st_dev, before.st_ino)
        if not stat.S_ISREG(before.st_mode):
            record["status"] = "invalid"
            return (
                record,
                None,
                physical_identity,
                RegistryContractError("registry-input-not-regular"),
            )
        chunks: list[bytes] = []
        remaining = MAX_REGISTRY_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        if len(raw) > MAX_REGISTRY_BYTES:
            record["status"] = "invalid"
            return (
                record,
                None,
                physical_identity,
                RegistryContractError("registry-input-too-large"),
            )
        if (
            (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            != (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            or len(raw) != after.st_size
        ):
            record["status"] = "invalid"
            return (
                record,
                None,
                physical_identity,
                RegistryContractError("registry-input-changed"),
            )
    except OSError:
        record["status"] = "invalid"
        return (
            record,
            None,
            physical_identity,
            RegistryContractError("registry-input-unreadable"),
        )
    finally:
        os.close(fd)
    record["status"] = "present"
    record["content_digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    return record, raw, physical_identity, None


def _resolve_registry_precedence(
    candidates: list[dict], diagnostics: list[dict]
) -> tuple[list[dict], list[dict]]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            int(item.get("_precedence_tier", 99)),
            int(item.get("_input_order", 0)),
            str(item.get("_input_identity") or ""),
            int(item.get("_candidate_order", 0)),
        ),
    )
    conflict_groups: dict[tuple, list[dict]] = {}
    for item in ordered:
        identity = _provider_id(item)
        tier = int(item.get("_precedence_tier", 99))
        explicit_subrank = int(item.get("_input_order", 0)) if tier in {0, 1} else 0
        conflict_groups.setdefault((tier, explicit_subrank, identity), []).append(item)

    conflicted = {
        key for key, items in conflict_groups.items()
        if key[2] and len(items) > 1
    }
    resolved: list[dict] = []
    decided: set[str] = set()
    for item in ordered:
        identity = _provider_id(item)
        if not identity or identity in decided:
            continue
        tier = int(item.get("_precedence_tier", 99))
        explicit_subrank = int(item.get("_input_order", 0)) if tier in {0, 1} else 0
        group_key = (tier, explicit_subrank, identity)
        decided.add(identity)
        if group_key in conflicted:
            diagnostics.append({
                "provider_id": _safe_provider_reference(identity),
                "source": item.get("source"),
                "reason_code": "same-tier-identity-conflict",
                "registry_entry_digest": item.get("registry_entry_digest"),
            })
            resolved.append({**item, "enabled": False, "_projection_state": "conflict"})
            continue
        if item.get("disabled") is True or item.get("enabled") is False:
            projection_state = (
                "tombstone"
                if item.get("registry_version") == 2 and item.get("disabled") is True
                else "disabled"
            )
            diagnostics.append({
                "provider_id": _safe_provider_reference(identity),
                "source": item.get("source"),
                "reason_code": "provider-disabled",
                "registry_entry_digest": item.get("registry_entry_digest"),
            })
            resolved.append({
                **item,
                "enabled": False,
                "_projection_state": projection_state,
            })
            continue
        resolved.append(item)
    return resolved, diagnostics


def _discover_specialist_registry_candidates(args) -> tuple[list[dict], list[dict], dict]:
    """Discover registry candidates in deterministic precedence order.

    Explicit CLI registries have the highest precedence, then project, user, and
    skill/plugin manifests. Built-in presets are appended later by the ranker so
    registry-level `enabled: false` entries can suppress lower-precedence defaults.
    """
    candidates: list[dict] = []
    diagnostics: list[dict] = []
    inputs: list[dict] = []
    invalid_barriers: list[dict] = []

    def register(
        path: Path,
        source: str,
        kind: str,
        version: int,
        tier: int,
        order: int,
        detection_error: RegistryContractError | None = None,
        snapshot: tuple[
            dict,
            bytes | None,
            tuple[int, int] | None,
            RegistryContractError | None,
        ] | None = None,
    ):
        record, raw, _physical_identity, snapshot_error = snapshot or _read_registry_input(
            path, source, kind, version, tier, order
        )
        record.update({
            "source": source,
            "kind": kind,
            "version": version,
            "precedence_tier": tier,
            "order": order,
        })
        inputs.append(record)
        if record["status"] == "missing":
            return record
        if snapshot_error is not None:
            loaded, invalid = [], [{
                "provider_id": "<registry>",
                "source": source,
                "reason_code": snapshot_error.code,
                "detail": str(snapshot_error),
            }]
        elif detection_error is not None:
            loaded, invalid = [], [{
                "provider_id": "<registry>",
                "source": source,
                "reason_code": detection_error.code,
                "detail": str(detection_error),
            }]
        elif raw is not None and version == 2:
            loaded, invalid = _load_v2_registry_candidates(raw, source)
        elif raw is not None:
            try:
                loaded, invalid = _load_registry_candidates(raw, source), []
            except RegistryContractError as error:
                loaded, invalid = [], [{
                    "provider_id": "<registry>",
                    "source": source,
                    "reason_code": error.code,
                }]
        else:
            loaded, invalid = [], []
        if invalid:
            record["status"] = "invalid"
            barrier = {
                "source": source,
                "canonical_identity": record["canonical_identity"],
                "content_digest": record["content_digest"],
                "kind": kind,
                "precedence_tier": tier,
                "order": order,
            }
            invalid_barriers.append(barrier)
            diagnostics.extend({**item, "registry_input": barrier} for item in invalid)
        for candidate_order, candidate in enumerate(loaded):
            candidate["_precedence_tier"] = tier
            candidate["_input_order"] = order
            candidate["_input_identity"] = record["canonical_identity"]
            candidate["_candidate_order"] = candidate_order
            candidates.append(candidate)
        return record

    explicit: list[
        tuple[
            Path,
            int,
            int,
            RegistryContractError | None,
            tuple[
                dict,
                bytes | None,
                tuple[int, int] | None,
                RegistryContractError | None,
            ],
            tuple[int, int] | None,
        ]
    ] = []
    for order, path in enumerate(_registry_arg_paths(getattr(args, "registry", None))):
        source = _registry_source(path, "explicit")
        snapshot = _read_registry_input(path, source, "explicit", 0, 99, order)
        raw_bytes = snapshot[1]
        if snapshot[3] is not None:
            version = 2
            detection_error = snapshot[3]
        else:
            try:
                raw = raw_bytes.decode("utf-8") if raw_bytes is not None else ""
                version = detect_registry_version(raw)
                detection_error = None
            except UnicodeError as error:
                version = 2
                detection_error = RegistryContractError("invalid-registry-contract", str(error))
            except RegistryContractError as error:
                version = 2
                detection_error = error
        physical_identity = snapshot[2]
        explicit.append((path, version, order, detection_error, snapshot, physical_identity))
    duplicate_explicit = {
        identity
        for identity in {item[5] for item in explicit if item[5] is not None}
        if sum(item[5] == identity for item in explicit) > 1
    }
    for path, version, order, detection_error, snapshot, physical_identity in sorted(
        explicit, key=lambda item: (-item[1], item[2])
    ):
        if physical_identity in duplicate_explicit:
            detection_error = RegistryContractError("duplicate-registry-input")
        register(
            path,
            snapshot[0]["source"],
            "explicit",
            version,
            0 if version == 2 else 1,
            order,
            detection_error,
            snapshot,
        )

    project_v2 = Path.cwd() / ".mission" / "specialists-v2.yml"
    register(project_v2, "project:.mission/specialists-v2.yml", "project", 2, 2, 0)

    project_registry = Path.cwd() / ".mission" / "specialists.yml"
    register(project_registry, "project:.mission/specialists.yml", "project", 1, 3, 0)

    if not getattr(args, "no_default_skill_roots", False):
        user_v2 = Path.home() / ".config" / "mission" / "specialists-v2.yml"
        register(user_v2, "user:~/.config/mission/specialists-v2.yml", "user", 2, 4, 0)
        user_registry = Path.home() / ".config" / "mission" / "specialists.yml"
        register(user_registry, "user:~/.config/mission/specialists.yml", "user", 1, 5, 0)

    for root_order, root in enumerate(_skill_roots(args)):
        if not root.is_dir():
            continue
        v2_manifests = sorted(root.glob("*/mission-specialist-v2.yml"))
        v1_manifests = sorted(root.glob("*/mission-specialist.yml"))
        inventory = []
        for manifest_order, manifest in enumerate(v2_manifests):
            record = register(
                manifest,
                _registry_source(manifest, "installed"),
                "installed",
                2,
                6,
                manifest_order,
            )
            inventory.append({
                "identity": record["canonical_identity"],
                "digest": record["content_digest"],
            })
        for manifest_order, manifest in enumerate(v1_manifests):
            record = register(
                manifest,
                _registry_source(manifest, "installed"),
                "installed",
                1,
                7,
                manifest_order,
            )
            inventory.append({
                "identity": record["canonical_identity"],
                "digest": record["content_digest"],
            })
        inputs.append({
            "kind": "skill-root",
            "source": _registry_source(root, "skill-root"),
            "canonical_identity": _portable_registry_identity(root),
            "version": 0,
            "precedence_tier": 6,
            "order": root_order,
            "status": "present",
            "content_digest": provider_value_digest(inventory),
        })

    active_barrier = None
    if invalid_barriers:
        active_barrier = min(
            invalid_barriers,
            key=lambda item: (
                int(item["precedence_tier"]),
                int(item["order"]) if item["kind"] == "explicit" else -1,
            ),
        )

        def is_higher_than_barrier(candidate: dict) -> bool:
            candidate_tier = int(candidate.get("_precedence_tier", 99))
            barrier_tier = int(active_barrier["precedence_tier"])
            if candidate_tier != barrier_tier:
                return candidate_tier < barrier_tier
            return (
                active_barrier["kind"] == "explicit"
                and int(candidate.get("_input_order", 0)) < int(active_barrier["order"])
            )

        candidates = [candidate for candidate in candidates if is_higher_than_barrier(candidate)]

    resolved, diagnostics = _resolve_registry_precedence(candidates, diagnostics)
    if active_barrier is not None:
        resolved.append({
            "_blocks_builtin_candidates": True,
            "_projection_state": "invalid-input-barrier",
            "source": active_barrier["source"],
            "_precedence_tier": active_barrier["precedence_tier"],
            "_input_order": active_barrier["order"],
        })
    effective = [
        {
            "provider_id": _safe_provider_reference(_provider_id(candidate)),
            "source": candidate.get("source"),
            "registry_version": candidate.get("registry_version"),
            "registry_entry_digest": candidate.get("registry_entry_digest"),
            "projection_state": candidate.get("_projection_state", "eligible"),
        }
        for candidate in resolved
        if _provider_id(candidate)
    ]
    if active_barrier is not None:
        effective.append({
            "provider_id": "<registry>",
            "source": active_barrier["source"],
            "projection_state": "invalid-input-barrier",
            "content_digest": active_barrier["content_digest"],
        })
    ordered_inputs = sorted(
        inputs,
        key=lambda item: (
            int(item.get("precedence_tier", 99)),
            int(item.get("order", 0)),
            str(item.get("canonical_identity") or ""),
        ),
    )
    ordered_barriers = sorted(
        invalid_barriers,
        key=lambda item: (
            int(item.get("precedence_tier", 99)),
            int(item.get("order", 0)),
            str(item.get("canonical_identity") or ""),
        ),
    )
    projection_payload = {
        "schema": "mission-specialist-registry-projection/1",
        "ordered_inputs": ordered_inputs,
        "precedence_barriers": ordered_barriers,
        "effective_entries": effective,
    }
    projection = {
        **projection_payload,
        "effective_projection_digest": provider_value_digest(projection_payload),
    }
    for candidate in resolved:
        candidate["registry_projection_digest"] = projection["effective_projection_digest"]
    return resolved, diagnostics, projection


def _skill_roots(args) -> list[Path]:
    roots = [Path(p).expanduser() for p in _split_csv(getattr(args, "skills_dir", None))]
    env = os.environ.get("MISSION_SKILL_ROOTS")
    if env:
        roots.extend(Path(p).expanduser() for p in env.split(os.pathsep) if p)
    if not getattr(args, "no_default_skill_roots", False):
        roots.extend([
            Path.home() / ".codex" / "skills",
            Path.home() / ".claude" / "skills",
        ])
    # Preserve order while deduplicating.
    out: list[Path] = []
    seen = set()
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _discover_installed_skills(args) -> dict[str, dict]:
    installed: dict[str, dict] = {}
    for name in _split_csv(getattr(args, "installed_skills", None)):
        installed[name] = {"skill": name, "source": "argument", "available": True, "description": ""}
    for root in _skill_roots(args):
        if not root.is_dir():
            continue
        for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
            name = skill_dir.name
            description = ""
            try:
                for line in skill_md.read_text(encoding="utf-8", errors="ignore").splitlines()[:40]:
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip('"').strip("'") or name
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip('"').strip("'")
            except OSError:
                continue
            installed.setdefault(name, {
                "skill": name,
                "source": str(root),
                "available": True,
                "description": description,
            })
    return installed


def classify_task_profile(task: str, files: list[str] | None = None) -> dict:
    files = files or []
    haystack = " ".join([task, *files]).lower()
    matches: list[tuple[str, int, list[str]]] = []
    for profile, keywords in PROFILE_KEYWORDS.items():
        signals = [kw for kw in keywords if kw in haystack]
        if signals:
            matches.append((profile, len(signals), signals[:5]))
    matches.sort(key=lambda item: (-item[1], item[0]))
    if not matches:
        return {
            "primary": "general",
            "secondary": [],
            "confidence": 0.3,
            "risk": "low",
            "signals": [],
        }
    primary, top_count, top_signals = matches[0]
    secondary = [p for p, _, _ in matches[1:4]]
    risk = "high" if any(kw in haystack for kw in HIGH_RISK_KEYWORDS) else "medium"
    confidence = min(0.95, 0.55 + (0.1 * top_count))
    if secondary and matches[1][1] == top_count:
        confidence = min(confidence, 0.68)
    return {
        "primary": primary,
        "secondary": secondary,
        "confidence": round(confidence, 2),
        "risk": risk,
        "signals": top_signals,
    }


def _candidate_profiles(candidate: dict) -> list[str]:
    profiles = candidate.get("task_profiles") or candidate.get("profiles") or []
    if isinstance(profiles, str):
        return [profiles]
    return list(profiles)


def _provider_id(candidate: dict) -> str:
    return str(
        candidate.get("provider_id")
        or candidate.get("skill")
        or candidate.get("role")
        or candidate.get("name")
        or candidate.get("command")
        or ""
    )


def _disable_keys(candidate: dict) -> set[str]:
    if candidate.get("registry_version") == 2:
        provider_id = _provider_id(candidate)
        return {provider_id} if provider_id else set()
    return {str(v) for v in (
        candidate.get("provider_id"),
        candidate.get("role"),
        candidate.get("skill"),
        candidate.get("name"),
        candidate.get("command"),
    ) if v}


def _enabled_registry_candidates(registry_candidates: list[dict]) -> list[dict]:
    disabled: set[str] = set()
    enabled_keys: set[str] = set()
    enabled: list[dict] = []
    block_builtins = any(
        isinstance(item, dict) and item.get("_blocks_builtin_candidates") is True
        for item in registry_candidates
    )
    source_candidates = list(registry_candidates)
    if not block_builtins:
        source_candidates.extend(BUILTIN_SPECIALIST_CANDIDATES)
    for raw in source_candidates:
        if not isinstance(raw, dict):
            continue
        keys = _disable_keys(raw)
        if not keys:
            continue
        if raw.get("enabled") is False:
            disabled.update(keys)
            continue
        if keys & disabled or keys & enabled_keys:
            continue
        enabled_keys.update(keys)
        enabled.append(raw)
    return enabled


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _string_map(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if k is not None and v is not None}


def _command_is_available(command: str | None) -> bool:
    if not command:
        return False
    if os.sep in command:
        path = Path(command).expanduser()
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def _portable_provider_identifier(value: object) -> bool:
    text = str(value or "")
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", text))


def _classify_non_portable_execution_config(candidate: dict) -> str | None:
    if any(
        not _portable_provider_identifier(candidate.get(field))
        for field in ("provider_id", "role", "skill")
    ):
        return "provider-identity"
    if candidate.get("kind") != "command":
        return None
    if "$EXTERNAL/sha256:" in str(candidate.get("source") or ""):
        return "external-registry-resupply"
    command = candidate.get("command")
    if not command or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", str(command)):
        return "command-locator"
    args = candidate.get("args") or []
    if args:
        return "argument-locator"
    if candidate.get("env"):
        return "environment-values"
    return None


def _safe_provider_reference(identity: object) -> str:
    canonical_identity = str(identity or "")
    if re.fullmatch(r"provider:sha256:[0-9a-f]{64}", canonical_identity):
        return canonical_identity
    if _portable_provider_identifier(canonical_identity):
        return canonical_identity
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return f"provider:sha256:{digest}"


def _public_specialist_record(record: dict) -> dict:
    """Project one internal provider record onto the portable public contract."""
    identity = _provider_id(record)
    public: dict = {"provider_id": _safe_provider_reference(identity)}
    for field in ("role", "skill"):
        value = record.get(field)
        if value:
            public[field] = (
                str(value)
                if _portable_provider_identifier(value)
                else _safe_provider_reference(value)
            )
    kind = record.get("kind")
    if kind in {"skill", "command"}:
        public["kind"] = kind
    command = record.get("command")
    if command and _portable_provider_identifier(command):
        public["command"] = str(command)
        public["args"] = []
    timeout = record.get("timeout")
    if type(timeout) is int and 1 <= timeout <= 86400:
        public["timeout"] = timeout
    for field in ("task_profiles", "phases", "matched_conditions"):
        values = record.get(field)
        if isinstance(values, list):
            public[field] = [
                str(value)
                for value in values
                if isinstance(value, str)
                and len(value) <= 128
                and not any(ord(char) < 32 or ord(char) == 127 for char in value)
                and "/" not in value
                and "\\" not in value
            ]
    for field in (
        "required",
        "bounded_use",
        "bounded_purpose_required",
        "installed",
        "available",
        "first_use",
    ):
        if type(record.get(field)) is bool:
            public[field] = record[field]
    if (
        type(record.get("max_calls_per_iteration")) is int
        and 1 <= record["max_calls_per_iteration"] <= 1000
    ):
        public["max_calls_per_iteration"] = record["max_calls_per_iteration"]
    for field in (
        "source",
        "registry_entry_digest",
        "registry_projection_digest",
        "context_digest",
        "activation_digest",
        "status",
        "eligibility_reason",
        "selection_source",
        "selection_source_raw",
        "eligibility_selection_source",
        "selection_id",
    ):
        value = record.get(field)
        if isinstance(value, str):
            public[field] = value
    if type(record.get("registry_version")) is int:
        public["registry_version"] = record["registry_version"]
    score = record.get("score")
    if (
        (type(score) is int and 0 <= score <= 1)
        or (type(score) is float and math.isfinite(score) and 0.0 <= score <= 1.0)
    ):
        public["score"] = score
    normalized = record.get("normalized_activation")
    if isinstance(normalized, dict):
        public["normalized_activation"] = {
            key: normalized[key]
            for key in (
                "min_complexity",
                "auto_select_if",
                "explicit_below_min",
                "when_any",
            )
            if key in normalized
        }
    risk = record.get("risk")
    if isinstance(risk, dict):
        public["risk_confirmation_required"] = bool(
            risk.get("first_use_confirmation")
        )
    result_contract = record.get("result_contract")
    if isinstance(result_contract, dict) and result_contract:
        public["result_contract_digest"] = provider_value_digest(result_contract)
    planning = record.get("planning")
    if isinstance(planning, dict) and planning.get("mode") in {"advisory", "primary"}:
        public["planning_mode"] = planning["mode"]
        public["planning_contract_digest"] = provider_value_digest(planning)
    return public


def _public_specialist_records(records: list[dict]) -> list[dict]:
    return [
        _public_specialist_record(record)
        for record in records
        if isinstance(record, dict)
    ]


def _public_registry_input_record(record: dict) -> dict:
    return {
        field: record[field]
        for field in (
            "kind",
            "source",
            "canonical_identity",
            "version",
            "precedence_tier",
            "order",
            "status",
            "content_digest",
            "resolution_mode",
        )
        if field in record
    }


def _public_specialist_diagnostic(record: dict) -> dict:
    identity = str(record.get("provider_id") or "<registry>")
    public = {
        "provider_id": (
            "<registry>"
            if identity == "<registry>"
            else _safe_provider_reference(identity)
        )
    }
    for field in (
        "source",
        "reason_code",
        "field_code",
        "blocked_config_class",
        "registry_entry_digest",
        "registry_projection_digest",
        "context_digest",
        "activation_digest",
        "selection_source",
        "selection_source_raw",
        "requested_phase",
        "current_complexity",
        "minimum_complexity",
    ):
        value = record.get(field)
        if isinstance(value, str):
            public[field] = value
    registry_input = record.get("registry_input")
    if isinstance(registry_input, dict):
        public["registry_input"] = _public_registry_input_record(registry_input)
    return public


def _public_specialist_diagnostics(records: list[dict]) -> list[dict]:
    return [
        _public_specialist_diagnostic(record)
        for record in records
        if isinstance(record, dict)
    ]


def _public_eligibility_context_fields(eligibility: dict, complexity: object) -> dict:
    """Expose only contract enums; bind rejected raw policy through digests."""
    fields: dict = {}
    if complexity in {"Simple", "Standard", "Complex", "Critical"}:
        fields["current_complexity"] = complexity
    minimum = (eligibility.get("normalized_activation") or {}).get("min_complexity")
    if minimum in {"Simple", "Standard", "Complex", "Critical"}:
        fields["minimum_complexity"] = minimum
    elif minimum is not None:
        fields["field_code"] = "activation.min_complexity"
    return fields


def _merge_result_contract(defaults: dict, explicit: dict) -> dict:
    merged = dict(defaults)
    merged.update(explicit)
    markers = [
        *[str(v) for v in defaults.get("forbidden_markers") or []],
        *[str(v) for v in explicit.get("forbidden_markers") or []],
    ]
    if markers:
        merged["forbidden_markers"] = list(dict.fromkeys(markers))
    return merged


def _is_bounded_orchestrator_candidate(candidate: dict) -> bool:
    if any(candidate.get(key) is True for key in ("bounded", "bounded_use", "broad_orchestrator")):
        return True
    notes = str(candidate.get("notes") or "").lower()
    return "broad" in notes and "orchestrator" in notes


def _normalize_candidate(candidate: dict, source: str) -> dict:
    kind = candidate.get("kind") or "skill"
    command = candidate.get("command")
    skill = candidate.get("skill") or candidate.get("name") or candidate.get("role")
    role = candidate.get("role") or skill
    raw_phases = candidate.get("phases")
    phases = (
        ([] if candidate.get("registry_version") == 2 else ["planning", "review"])
        if raw_phases is None
        else raw_phases
    )
    if isinstance(phases, str):
        phases = [phases]
    args = _as_list(candidate.get("args"))
    env = _string_map(candidate.get("env"))
    auto_use = candidate.get("auto_use") if isinstance(candidate.get("auto_use"), dict) else {}
    risk = candidate.get("risk") if isinstance(candidate.get("risk"), dict) else {}
    explicit_result_contract = candidate.get("result_contract") if isinstance(candidate.get("result_contract"), dict) else {}
    result_contract = _merge_result_contract({}, explicit_result_contract)
    if kind == "command" and not skill:
        skill = role or command
    bounded_use = _is_bounded_orchestrator_candidate(candidate)
    if bounded_use:
        phases = [phase for phase in phases if phase != "execution"]
    return {
        "role": role,
        "skill": skill,
        "kind": kind,
        "command": command,
        "args": args,
        "env": env,
        "timeout": candidate.get("timeout"),
        "task_profiles": _candidate_profiles(candidate),
        "phases": phases,
        "required": bool(candidate.get("required", False)),
        "max_calls_per_iteration": candidate.get("max_calls_per_iteration"),
        "source": candidate.get("source") or source,
        "unavailable": candidate.get("unavailable", "continue"),
        "confirm": bool(candidate.get("confirm", False)),
        "auto_use": auto_use,
        "activation": candidate.get("activation"),
        "risk": risk,
        "result_contract": result_contract,
        "planning": candidate.get("planning") if isinstance(candidate.get("planning"), dict) else {},
        "bounded_use": bounded_use,
        "bounded_purpose_required": bool(candidate.get("bounded_purpose_required", bounded_use)),
        "install_hint": bool(candidate.get("install_hint", True)),
        "provider_id": _provider_id(candidate),
        "registry_version": candidate.get("registry_version", 1),
        "registry_entry_digest": candidate.get("registry_entry_digest") or registry_entry_digest(candidate),
        "registry_projection_digest": candidate.get("registry_projection_digest"),
        "_registry_error": candidate.get("_registry_error"),
        "_v2_auto_use_present": candidate.get("_v2_auto_use_present"),
        "_explicit_result_contract_present": "result_contract" in candidate,
    }


def _candidate_source_rank(candidate: dict) -> int:
    source = str(candidate.get("source") or "")
    if source.startswith("registry:"):
        return 0
    if source.startswith("project:"):
        return 1
    if source.startswith("user:"):
        return 2
    if source.startswith("skill-manifest:"):
        return 3
    if source.startswith("preset:"):
        return 4
    return 5


def _default_consent_file() -> Path:
    return Path.home() / ".config" / "mission" / "provider-consent.json"


def _load_provider_consent(path_text: str | None) -> set[str]:
    path = Path(path_text).expanduser() if path_text else _default_consent_file()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    providers = data.get("providers", {})
    if isinstance(providers, dict):
        return {str(k) for k in providers}
    if isinstance(providers, list):
        return {str(v) for v in providers}
    return set()


def rank_specialist_candidates(task_profile: dict, registry_candidates: list[dict],
                               installed: dict[str, dict], first_use: set[str] | None = None,
                               complexity: str | None = None, consented: set[str] | None = None,
                               user_specified: list[str] | None = None,
                               mission_context: dict | None = None) -> tuple[list[dict], list[dict]]:
    first_use = first_use or set()
    consented = consented or set()
    profiles = [task_profile.get("primary"), *(task_profile.get("secondary") or [])]
    ranked = []
    ineligible = []
    named = set(user_specified or [])
    mission_context = mission_context or {
        "complexity": complexity,
        "task_profile": task_profile,
        "iteration": 1,
        "previous_iteration_passed": None,
    }
    for raw in _enabled_registry_candidates(registry_candidates):
        c = _normalize_candidate(raw, raw.get("source", "registry") if isinstance(raw, dict) else "registry")
        skill = c.get("skill")
        if not skill:
            continue
        overlap = [p for p in c.get("task_profiles", []) if p in profiles]
        raw_source = "user-specified" if skill in named else "automatic"
        canonical_source = normalize_selection_source(raw_source)["selection_source"]
        requested_phase = (
            "planning"
            if "complexity" in ((c.get("activation") or {}).get("auto_select_if") or [])
            else None
        )
        blocked_config_class = _classify_non_portable_execution_config(c)
        if blocked_config_class is not None:
            eligibility = evaluate_provider_eligibility(
                c,
                mission_context,
                requested_phase=requested_phase,
                selection_source=raw_source,
            )
            ineligible.append({
                "provider_id": _safe_provider_reference(_provider_id(c)),
                "source": c["source"],
                "registry_entry_digest": c["registry_entry_digest"],
                "selection_source": canonical_source,
                "selection_source_raw": raw_source,
                "requested_phase": requested_phase,
                "reason_code": "non-portable-execution-config",
                "blocked_config_class": blocked_config_class,
                "context_digest": eligibility["context_digest"],
                "activation_digest": eligibility["activation_digest"],
                "registry_projection_digest": c.get("registry_projection_digest"),
                **_public_eligibility_context_fields(eligibility, complexity),
            })
            continue
        if c.get("kind") == "command":
            installed_info = {"available": True} if _command_is_available(c.get("command")) else None
        else:
            installed_info = installed.get(skill)
        c["available"] = bool(installed_info)
        eligibility = evaluate_provider_eligibility(
            c,
            mission_context,
            requested_phase=requested_phase,
            selection_source=raw_source,
        )
        c.update({
            "eligibility_reason": eligibility["reason_code"],
            "matched_conditions": eligibility["matched_conditions"],
            "context_digest": eligibility["context_digest"],
            "activation_digest": eligibility["activation_digest"],
            "normalized_activation": eligibility["normalized_activation"],
            "eligibility_selection_source": canonical_source,
        })
        if not eligibility["eligible"] and eligibility["reason_code"] != "provider-unavailable":
            ineligible.append({
                "provider_id": c["provider_id"],
                "source": c["source"],
                "registry_entry_digest": c["registry_entry_digest"],
                "selection_source": canonical_source,
                "selection_source_raw": raw_source,
                "requested_phase": requested_phase,
                "reason_code": eligibility["reason_code"],
                "context_digest": eligibility["context_digest"],
                "activation_digest": eligibility["activation_digest"],
                "registry_projection_digest": c.get("registry_projection_digest"),
                **_public_eligibility_context_fields(eligibility, complexity),
            })
            continue
        base = 0.45 + (0.25 if task_profile.get("primary") in overlap else 0.1)
        base += min(0.2, 0.05 * len(overlap))
        if installed_info:
            base += 0.1
        if c.get("required"):
            base += 0.05
        provider_id = _provider_id(c)
        needs_first_use = bool(c.get("risk", {}).get("first_use_confirmation")) and provider_id not in consented
        score = min(0.99, round(base * float(task_profile.get("confidence", 0.5)), 3))
        ranked.append({
            **c,
            "score": score,
            "installed": bool(installed_info),
            "available": bool(installed_info),
            "status": "available" if installed_info else "missing",
            "first_use": skill in first_use or provider_id in first_use or needs_first_use,
            "reason": (
                "complexity activation match"
                if "complexity" in eligibility["matched_conditions"]
                else f"{', '.join(overlap)} profile match"
            ),
        })
    ranked.sort(key=lambda c: (-c["score"], _candidate_source_rank(c), c["skill"]))
    return ranked, ineligible


def decide_specialists(task_profile: dict, candidates: list[dict],
                       user_specified: list[str] | None = None) -> dict:
    if not candidates:
        return {
            "policy": "fallback",
            "action": "continue-core",
            "reason": "no specialist candidate matched the task profile",
            "prompted_user": False,
        }
    # Issue #100: ミッション本文でユーザーが名指ししたスキルは実質 confirmed-user。
    # high-risk task profile でも ask-user に倒さず selected として記録する。
    # 安全弁 (名指しでもバイパスしない条件):
    # - required specialist が未インストール → 従来フロー (required-missing はブロッカー)
    # - 名指しに first-use consent が必要な provider が 1 つでも混在 → 全体を従来フローに倒す
    #   (consent 完了後に recommend を再実行すれば user-specified が効く。risk consent は別次元)
    named = [s for s in (user_specified or []) if s]
    if named:
        required_missing_for_named = [c for c in candidates if c.get("required") and not c.get("installed")]
        matched = [c for c in candidates if str(c.get("skill") or "") in named and c.get("installed")]
        if matched and not required_missing_for_named and not any(c.get("first_use") for c in matched):
            skills = [str(c.get("skill")) for c in matched]
            return {
                "policy": "user-specified",
                "action": "select",
                "reason": f"user explicitly named specialists in the mission description: {', '.join(skills)}",
                "prompted_user": False,
                "user_specified": skills,
            }
    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    if task_profile.get("risk") == "high":
        return {
            "policy": "confirm",
            "action": "ask-user",
            "reason": "high-risk task profile requires specialist plan confirmation",
            "prompted_user": True,
        }
    required_missing = [c for c in candidates if c.get("required") and not c.get("installed")]
    if required_missing:
        return {
            "policy": "required-missing",
            "action": "ask-user",
            "reason": f"required specialist is missing: {required_missing[0]['skill']}",
            "prompted_user": True,
        }
    if top.get("first_use") or top.get("confirm"):
        return {
            "policy": "first-use",
            "action": "ask-user",
            "reason": f"specialist requires first-use confirmation: {top['skill']}",
            "prompted_user": True,
        }
    if not top.get("installed") and top.get("kind") == "command":
        return {
            "policy": "provider-unavailable",
            "action": "continue-core",
            "reason": f"top command provider is unavailable: {top['skill']}",
            "prompted_user": False,
        }
    if not top.get("installed"):
        if not top.get("install_hint", True):
            return {
                "policy": "fallback",
                "action": "continue-core",
                "reason": f"top preset specialist is not installed: {top['skill']}",
                "prompted_user": False,
            }
        return {
            "policy": "install-recommended",
            "action": "recommend-install",
            "reason": f"top specialist is missing: {top['skill']}",
            "prompted_user": True,
        }
    if (
        second
        and top.get("installed")
        and second.get("installed")
        and not top.get("required")
        and not second.get("required")
        and abs(top["score"] - second["score"]) <= 0.05
    ):
        return {
            "policy": "auto",
            "action": "select",
            "reason": f"tie-break: auto-selected {top['skill']} over {second['skill']} (score delta <= 0.05)",
            "prompted_user": False,
        }
    if (
        "complexity" not in (top.get("matched_conditions") or [])
        and (task_profile.get("confidence", 0) < 0.5 or top.get("score", 0) < 0.45)
    ):
        return {
            "policy": "fallback",
            "action": "continue-core",
            "reason": "task profile confidence is too low for automatic specialist selection",
            "prompted_user": False,
        }
    return {
        "policy": "auto",
        "action": "select",
        "reason": f"top candidate {top['skill']} is installed with score {top['score']}",
        "prompted_user": False,
    }


def _selection_from_decision(candidates: list[dict], decision: dict) -> tuple[list[dict], list[dict]]:
    unavailable = [c for c in candidates if not c.get("installed")]
    if decision.get("policy") == "user-specified":
        names = set(decision.get("user_specified") or [])
        selected = [
            {
                **c,
                "status": "selected",
                **normalize_selection_source("user-specified"),
            }
            for c in candidates
            if str(c.get("skill") or "") in names and c.get("installed")
        ]
        return selected, unavailable
    if decision.get("policy") == "auto" and candidates:
        return [{
            **candidates[0],
            "status": "selected",
            **normalize_selection_source("automatic"),
        }], unavailable
    return [], unavailable


def build_phase_plan(
    candidates: list[dict],
    complexity: str | None = None,
    mission_context: dict | None = None,
) -> list[dict]:
    """Return a bounded advisory provider plan grouped by mission phase."""
    if not candidates:
        return []
    context = mission_context or {
        "complexity": complexity,
        "task_profile": {"primary": "general", "secondary": [], "confidence": 0.0},
        "iteration": 1,
        "previous_iteration_passed": None,
    }
    max_per_phase = 1 if complexity in {None, "Simple", "Standard"} else 2
    plan: list[dict] = []
    seen_skills: set[str] = set()
    for phase, preferred_roles in PHASE_ROLE_ORDER.items():
        phase_candidates = []
        for candidate in candidates:
            if (
                not candidate.get("installed")
                or phase not in (candidate.get("phases") or [])
                or str(candidate.get("skill") or "") in seen_skills
            ):
                continue
            try:
                eligibility = evaluate_provider_eligibility(
                    candidate,
                    context,
                    requested_phase=phase,
                    selection_source=candidate.get(
                        "eligibility_selection_source", "automatic"
                    ),
                )
            except ValueError:
                continue
            if eligibility["eligible"]:
                phase_candidates.append(candidate)
        if not phase_candidates:
            continue

        def _phase_key(candidate: dict) -> tuple[int, float, str]:
            role = str(candidate.get("role") or candidate.get("skill") or "")
            try:
                role_rank = preferred_roles.index(role)
            except ValueError:
                role_rank = len(preferred_roles)
            return (role_rank, -float(candidate.get("score") or 0), str(candidate.get("skill") or ""))

        selected = sorted(phase_candidates, key=_phase_key)[:max_per_phase]
        for candidate in selected:
            seen_skills.add(str(candidate.get("skill") or ""))
        plan.append({
            "phase": phase,
            "roles": [c.get("role") or c.get("skill") for c in selected],
            "providers": [c.get("skill") for c in selected],
            "max_providers": max_per_phase,
        })
    return plan


def _reject_specialist_state_context_mismatch(
    args, *, state_complexity, state_iteration, observed_complexity, observed_iteration
) -> None:
    result = {
        "ok": False,
        "reason_code": "state-context-mismatch",
        "state_context": {
            "complexity": state_complexity,
            "iteration": state_iteration,
        },
        "observed_context": {
            "complexity": observed_complexity,
            "iteration": observed_iteration,
        },
        "specialists_candidates": [],
        "specialists_selected": [],
        "specialists_unavailable": [],
        "specialists_ineligible": [],
        "specialist_registry_projection": None,
        "specialists_decision": None,
        "specialists_phase_plan": [],
    }
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(
            "ERROR: mission state changed or disagrees with the requested specialist context",
            file=sys.stderr,
        )
    raise SystemExit(2)


def cmd_specialists(args):
    task = getattr(args, "task", "") or ""
    files = _split_csv(getattr(args, "files", None))
    state_context = None
    try:
        state_path = resolve_state_file(Path.cwd())
        if state_path.exists():
            state_context = json.loads(state_path.read_text(encoding="utf-8"))
            _validate_specialist_public_state(state_context)
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
        state_context = None
    state_iteration_snapshot = (
        state_context.get("iteration") if isinstance(state_context, dict) else None
    )
    requested_complexity = getattr(args, "complexity", None)
    record_state = bool(getattr(args, "record_state", False))
    if record_state and isinstance(state_context, dict):
        state_complexity = state_context.get("complexity")
        state_iteration = state_context.get("iteration")
        if requested_complexity is not None and requested_complexity != state_complexity:
            _reject_specialist_state_context_mismatch(
                args,
                state_complexity=state_complexity,
                state_iteration=state_iteration,
                observed_complexity=requested_complexity,
                observed_iteration=state_iteration,
            )
        effective_complexity = state_complexity
    else:
        effective_complexity = requested_complexity
    if effective_complexity is None and isinstance(state_context, dict):
        effective_complexity = state_context.get("complexity")
    iteration = state_context.get("iteration", 1) if isinstance(state_context, dict) else 1
    installed = _discover_installed_skills(args)
    registry_candidates, registry_ineligible, registry_projection = _discover_specialist_registry_candidates(args)
    task_profile = classify_task_profile(task, files)
    mission_context = {
        "complexity": effective_complexity,
        "task_profile": task_profile,
        "iteration": iteration if type(iteration) is int and iteration >= 1 else 1,
        "previous_iteration_passed": (
            bool(state_context.get("passes"))
            if isinstance(state_context, dict) and type(iteration) is int and iteration >= 2
            else None
        ),
    }
    candidates, eligibility_ineligible = rank_specialist_candidates(
        task_profile,
        registry_candidates,
        installed,
        set(_split_csv(getattr(args, "first_use", None))),
        effective_complexity,
        _load_provider_consent(getattr(args, "consent_file", None)),
        _split_csv(getattr(args, "user_specified", None)),
        mission_context,
    )
    decision = decide_specialists(task_profile, candidates,
                                  _split_csv(getattr(args, "user_specified", None)))
    selected, unavailable = _selection_from_decision(candidates, decision)
    decision, candidates, selected, unavailable = _finalize_specialist_selection_checkpoint(
        decision, candidates, selected, unavailable
    )
    phase_plan = build_phase_plan(candidates, effective_complexity, mission_context)
    public_candidates = _public_specialist_records(candidates)
    public_selected = _public_specialist_records(selected)
    public_unavailable = _public_specialist_records(unavailable)
    public_ineligible = _public_specialist_diagnostics(
        [*registry_ineligible, *eligibility_ineligible]
    )
    result = {
        "ok": True,
        "task_profile": task_profile,
        "installed_skills": sorted(
            {_safe_provider_reference(identity) for identity in installed}
        ),
        "specialists_candidates": public_candidates,
        "specialists_selected": public_selected,
        "specialists_unavailable": public_unavailable,
        "specialists_ineligible": public_ineligible,
        "specialist_registry_projection": registry_projection,
        "specialists_decision": decision,
        "specialists_phase_plan": phase_plan,
    }
    _validate_specialist_public_state(result)

    if record_state:
        cwd = Path.cwd()
        sf = resolve_state_file(cwd)
        if not sf.exists():
            print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
            sys.exit(1)
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
            _reject_active_provider_mutation(data, "specialists-recommend")
            _validate_specialist_public_state(data)
            if (
                data.get("complexity") != effective_complexity
                or data.get("iteration") != state_iteration_snapshot
            ):
                _reject_specialist_state_context_mismatch(
                    args,
                    state_complexity=data.get("complexity"),
                    state_iteration=data.get("iteration"),
                    observed_complexity=effective_complexity,
                    observed_iteration=state_iteration_snapshot,
                )
            data["task_profile"] = task_profile
            data["specialists_candidates"] = public_candidates
            data["specialists_selected"] = public_selected
            data["specialists_unavailable"] = public_unavailable
            data["specialists_ineligible"] = public_ineligible
            data["specialist_registry_projection"] = registry_projection
            data["specialists_decision"] = decision
            data["specialists_phase_plan"] = phase_plan
            planning_selected = next((item for item in public_selected if item.get("planning_mode") in {"advisory", "primary"}), None)
            if planning_selected:
                data["planning_strategy"] = "provider-" + planning_selected["planning_mode"]
                data["planning_contract_digest"] = planning_selected["planning_contract_digest"]
                data["planning_provider_binding"] = {
                    key: planning_selected[key]
                    for key in ("provider_id", "selection_id", "planning_contract_digest")
                }
            elif data.get("planning_policy_version") == 1:
                data["planning_strategy"] = "core"
                data.pop("planning_provider_binding", None)
            data["specialists_mode"] = "interactive" if decision.get("prompted_user") else "auto"
            data["updated_at"] = iso_now()
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(data, cwd))

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"profile={task_profile['primary']} confidence={task_profile['confidence']} risk={task_profile['risk']}")
        print(f"decision={decision['policy']} action={decision['action']} reason={decision['reason']}")
        for idx, c in enumerate(candidates[:5], 1):
            print(f"{idx}. {c['skill']} score={c['score']} installed={c['installed']} source={c['source']}")


def cmd_specialists_consent(args):
    provider = args.provider.strip()
    if not provider:
        print("ERROR: --provider is required", file=sys.stderr)
        sys.exit(2)
    path = Path(args.consent_file).expanduser() if args.consent_file else _default_consent_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    providers = data.setdefault("providers", {})
    providers[provider] = {"granted_at": iso_now()}
    atomic_write_json(path, data)
    result = {"ok": True, "provider": provider, "consent_file": str(path)}
    print(json.dumps(result, indent=2 if getattr(args, "json", False) else None, ensure_ascii=False))


def cmd_specialists_accounting(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    data = json.loads(sf.read_text())
    _validate_specialist_public_state(data)
    report = {
        "ok": True,
        "session_id": data.get("session_id") or sf.stem,
        "mission_id": data.get("mission_id") or "",
        **candidate_accounting_report(data),
    }
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    if not report["unaccounted_candidates"]:
        print("specialist accounting: complete")
        return
    print(f"specialist accounting: {report['priority']} unaccounted candidates")
    for candidate in report["unaccounted_candidates"]:
        required = "required" if candidate["requires_accounting"] else "optional"
        print(f"- {candidate['skill']} ({required}): {candidate.get('reason') or 'no reason recorded'}")
    print("Record an explicit used/skipped/unavailable/failed invocation before completion when required.")


def _specialist_kind_for(data: dict, skill: str | None, invocation: dict | None = None) -> str:
    provider = _provider_for_skill(data, skill)
    if provider and provider.get("kind"):
        return str(provider.get("kind"))
    if invocation and invocation.get("provider_kind"):
        return str(invocation.get("provider_kind"))
    if invocation and invocation.get("mode") == "command-provider":
        return "command"
    return "skill"


def _specialist_source_for(data: dict, skill: str | None) -> str:
    provider = _provider_for_skill(data, skill)
    return str(provider.get("source") or "") if provider else ""


def _summary_item(data: dict, invocation: dict) -> dict:
    skill = str(invocation.get("skill") or "")
    return {
        "skill": skill,
        "role": invocation.get("role") or "",
        "kind": _specialist_kind_for(data, skill, invocation),
        "source": _specialist_source_for(data, skill),
        "mode": invocation.get("mode") or "",
        "status": invocation.get("status") or "",
        "selection_source": invocation.get("selection_source") or "",
        "bounded_purpose": invocation.get("bounded_purpose") or "",
        "evidence_path": invocation.get("evidence_path") or "",
    }


def specialist_usage_summary(data: dict) -> dict:
    selected = [
        {
            "skill": item.get("skill") or "",
            "role": item.get("role") or "",
            "kind": item.get("kind") or _specialist_kind_for(data, item.get("skill")),
            "source": item.get("source") or _specialist_source_for(data, item.get("skill")),
            "selection_source": item.get("selection_source") or "",
        }
        for item in data.get("specialists_selected") or []
        if isinstance(item, dict) and item.get("skill")
    ]
    selected_skills = {str(item["skill"]) for item in selected}
    used: list[dict] = []
    degraded: list[dict] = []
    unselected_manual: list[dict] = []
    for invocation in data.get("specialist_invocations") or []:
        if not isinstance(invocation, dict) or not invocation.get("skill"):
            continue
        item = _summary_item(data, invocation)
        status = str(invocation.get("status") or "")
        if status in APPLIED_SPECIALIST_INVOCATION_STATUSES:
            used.append(item)
            if item["skill"] not in selected_skills:
                unselected_manual.append(item)
        elif status in SPECIALIST_INVOCATION_REASON_REQUIRED_STATUSES:
            degraded.append(item)
    return {
        "selected": selected,
        "used": used,
        "degraded": degraded,
        "unselected_manual": unselected_manual,
    }


def _format_summary_items(items: list[dict]) -> str:
    if not items:
        return "none"
    parts = []
    for item in items:
        meta = [item.get("kind") or "skill"]
        if item.get("source"):
            meta.append(str(item["source"]))
        detail = f"{item['skill']}[{' '.join(meta)}"
        if item.get("mode"):
            detail += f" {item['mode']}:{item.get('status') or ''}"
        detail += "]"
        parts.append(detail)
    return ", ".join(parts)


def cmd_specialists_summary(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    data = json.loads(sf.read_text())
    _validate_specialist_public_state(data)
    summary = specialist_usage_summary(data)
    result = {
        "ok": True,
        "session_id": data.get("session_id") or sf.stem,
        "mission_id": data.get("mission_id") or "",
        **summary,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(
        "selected: {selected} / used: {used} / degraded: {degraded} / unselected-manual: {unselected}".format(
            selected=_format_summary_items(summary["selected"]),
            used=_format_summary_items(summary["used"]),
            degraded=_format_summary_items(summary["degraded"]),
            unselected=_format_summary_items(summary["unselected_manual"]),
        )
    )


MAX_SPECIALIST_EVIDENCE_BYTES = 1024 * 1024


class SpecialistEvidenceInputError(ValueError):
    """An evidence input cannot be snapshotted without a disclosure or race."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        self.field_path = "/specialist_invocations/pending/evidence_output"
        super().__init__(reason_code)


def _evidence_error(reason_code: str) -> None:
    raise SpecialistEvidenceInputError(reason_code)


def _read_specialist_evidence_input(path: Path) -> str:
    """Read one bounded, non-symlink regular-file snapshot from a single FD."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            _evidence_error("specialist-evidence-symlink")
        _evidence_error("specialist-evidence-unreadable")
    try:
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                _evidence_error("specialist-evidence-non-regular")
            if before.st_size < 0 or before.st_size > MAX_SPECIALIST_EVIDENCE_BYTES:
                _evidence_error("specialist-evidence-too-large")
            chunks = []
            remaining = MAX_SPECIALIST_EVIDENCE_BYTES + 1
            while remaining:
                chunk = os.read(fd, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(fd)
        except SpecialistEvidenceInputError:
            raise
        except OSError:
            _evidence_error("specialist-evidence-unreadable")
    finally:
        os.close(fd)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if (
        len(raw) > MAX_SPECIALIST_EVIDENCE_BYTES
        or len(raw) != after.st_size
        or before_identity != after_identity
    ):
        _evidence_error("specialist-evidence-changed")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _evidence_error("specialist-evidence-invalid-encoding")
    redacted = _redact_provider_output(text)
    if contains_local_locator(redacted):
        _evidence_error("specialist-evidence-unsafe-content")
    return redacted


def _planned_specialist_archive_path(
    cwd: Path, iteration: int, data: dict, entry: dict
) -> Path:
    archive_dir = state_dir(cwd) / "archive"
    gid = (data.get("mission_id") or "unknown")[:8]
    skill_slug = _slug_for_filename(entry.get("skill") or "unknown")
    return archive_dir / f"iter-{iteration}-{gid}-specialist-{skill_slug}.md"


def _specialist_archive_document(text: str, iteration: int, data: dict, entry: dict) -> str:
    return (
        f"<!-- mission-specialist-meta: session_id={data.get('session_id')} "
        f"agent={data.get('agent') or 'unknown'} mission_id={data.get('mission_id')} "
        f"iteration={iteration} phase={entry.get('phase')} role={entry.get('role')} "
        f"skill={entry.get('skill')} mode={entry.get('mode')} status={entry.get('status')} "
        f"timestamp={entry['timestamp']} -->\n"
        f"{text}"
    )


def _stage_specialist_archive(dst: Path, document: str) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{dst.name}.", dir=dst.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _publish_staged_specialist_archive(temp_path: Path, dst: Path) -> Path | None:
    previous = None
    try:
        if dst.exists() or dst.is_symlink():
            fd, previous_name = tempfile.mkstemp(prefix=f".{dst.name}.previous.", dir=dst.parent)
            os.close(fd)
            previous = Path(previous_name)
            previous.unlink()
            os.replace(dst, previous)
        os.replace(temp_path, dst)
    except BaseException:
        if previous is not None and previous.exists():
            os.replace(previous, dst)
        raise
    return previous


def _rollback_specialist_archive(
    temp_path: Path | None, dst: Path | None, previous: Path | None, published: bool
) -> None:
    if temp_path is not None:
        temp_path.unlink(missing_ok=True)
    if published and dst is not None:
        dst.unlink(missing_ok=True)
    if previous is not None and dst is not None and previous.exists():
        os.replace(previous, dst)


def _commit_specialist_state_with_archive(
    sf: Path,
    cwd: Path,
    data: dict,
    entry: dict,
    iteration: int,
    evidence_text: str | None,
) -> str | None:
    """Publish validated evidence and state together, restoring the archive on failure."""
    _validate_specialist_public_state(data)
    dst = (
        _planned_specialist_archive_path(cwd, iteration, data, entry)
        if evidence_text is not None
        else None
    )
    temp_path = None
    previous = None
    published = False
    try:
        if dst is not None:
            document = _specialist_archive_document(evidence_text, iteration, data, entry)
            temp_path = _stage_specialist_archive(dst, document)
            _validate_specialist_public_state(data)
            previous = _publish_staged_specialist_archive(temp_path, dst)
            temp_path = None
            published = True
        backup_state(sf)
        atomic_write_json(sf, data)
    except BaseException:
        _rollback_specialist_archive(temp_path, dst, previous, published)
        raise
    if previous is not None:
        previous.unlink(missing_ok=True)
    return str(dst) if dst is not None else None


def _find_provider(data: dict, provider_id: str) -> dict | None:
    for provider in [*(data.get("specialists_selected") or []), *(data.get("specialists_candidates") or [])]:
        if provider_id in {
            str(provider.get("role") or ""),
            str(provider.get("skill") or ""),
            str(provider.get("command") or ""),
        }:
            return provider
    return None


def _redact_provider_output(text: str) -> str:
    patterns = [
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s]+)"),
        re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._~+/=-]+)"),
    ]
    redacted = text
    for pattern in patterns:
        redacted = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)
    return redact_local_locators(redacted)


def _non_template_text_length(text: str, forbidden_markers: list[str]) -> int:
    cleaned = text
    for marker in forbidden_markers:
        cleaned = cleaned.replace(marker, "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned)


def _contract_exit_codes(contract: dict, key: str) -> set[int]:
    codes = contract.get(key) or []
    if isinstance(codes, (str, int)):
        codes = [codes]
    result: set[int] = set()
    for value in codes:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _classify_command_provider_result(provider: dict, exit_code: int | None,
                                      stdout: str, stderr: str) -> tuple[str, str | None]:
    explicit_contract = provider.get("result_contract") if isinstance(provider.get("result_contract"), dict) else {}
    contract = _merge_result_contract({}, explicit_contract)
    combined = "\n".join([stdout or "", stderr or ""])
    awaiting_markers = [str(v) for v in contract.get("awaiting_input_markers") or []]
    awaiting_hits = [marker for marker in awaiting_markers if marker and marker in combined]
    if awaiting_hits:
        return "awaiting-input", f"command provider awaiting input: {', '.join(awaiting_hits[:3])}"
    awaiting_exit_codes = _contract_exit_codes(contract, "awaiting_input_exit_codes")
    if exit_code in awaiting_exit_codes:
        return "awaiting-input", f"command provider awaiting input after exit code {exit_code}"
    if exit_code != 0:
        return "failed", f"command provider exited with status {exit_code}"
    forbidden_markers = [str(v) for v in contract.get("forbidden_markers") or PREPARATION_ONLY_MARKERS]
    marker_hits = [marker for marker in forbidden_markers if marker and marker in combined]
    try:
        min_chars = int(contract.get("min_non_template_chars") or 0)
    except (TypeError, ValueError):
        min_chars = 0
    non_template_len = _non_template_text_length(combined, forbidden_markers)
    if marker_hits:
        return "prepared", f"command provider returned preparation-only evidence: {', '.join(marker_hits[:3])}"
    if not explicit_contract:
        return "unvalidated-evidence", "command provider has no explicit result contract"
    if min_chars and non_template_len < min_chars:
        return "prepared", f"command provider evidence below result_contract.min_non_template_chars ({non_template_len} < {min_chars})"
    return "completed", None


def _provider_timeout(provider: dict, override: int | None) -> int:
    value = override if override is not None else provider.get("timeout", 120)
    if value is None:
        value = 120
    if type(value) is not int or not 1 <= value <= 86400:
        raise SpecialistPublicContractError("/specialist_invocations/pending/timeout")
    return value


def _selected_specialist_skills(data: dict) -> set[str]:
    return {
        str(item.get("skill"))
        for item in data.get("specialists_selected") or []
        if isinstance(item, dict) and item.get("skill")
    }


def _confirmed_selection_required(data: dict, skill: str | None, status: str) -> bool:
    if status not in APPLIED_SPECIALIST_INVOCATION_STATUSES or not skill:
        return False
    if skill in _selected_specialist_skills(data):
        return False
    decision = data.get("specialists_decision") if isinstance(data.get("specialists_decision"), dict) else {}
    return decision.get("action") == "ask-user" and decision.get("prompted_user") is True


def _provider_for_skill(data: dict, skill: str | None) -> dict | None:
    if not skill:
        return None
    for provider in [*(data.get("specialists_selected") or []), *(data.get("specialists_candidates") or [])]:
        if isinstance(provider, dict) and str(provider.get("skill") or "") == str(skill):
            return provider
    return None


def _require_current_provider_application(
    data: dict,
    provider: dict | None,
    *,
    requested_phase: str,
    requested_iteration: int,
    application_kind: str,
    selection_source: str | None = None,
    invocation_id: str | None = None,
    cwd: Path | None = None,
    registry_args=None,
) -> dict:
    """Reject an application before it can mutate state or start a process."""
    if provider is None:
        _provider_gate("provider-not-selected")
    if cwd is not None and isinstance(data.get("specialist_registry_projection"), dict):
        provider = _require_current_registry_application(
            data,
            provider,
            cwd=cwd,
            registry_args=registry_args,
            requested_phase=requested_phase,
            requested_iteration=requested_iteration,
            selection_source=selection_source,
        )
    verdict = validate_provider_application(
        provider,
        data,
        requested_phase=requested_phase,
        requested_iteration=requested_iteration,
        application_kind=application_kind,
        selection_source=selection_source,
        invocation_id=invocation_id,
    )
    if not verdict["eligible"]:
        _provider_gate(str(verdict["reason_code"]))
    current = dict(provider)
    current["_application_context_digest"] = verdict["application_context_digest"]
    return current


def _registry_identity_path(identity: str, cwd: Path) -> Path | None:
    if identity == "$PROJECT":
        return cwd
    if identity.startswith("$PROJECT/"):
        return cwd / identity.removeprefix("$PROJECT/")
    if identity == "$HOME":
        return Path.home()
    if identity.startswith("$HOME/"):
        return Path.home() / identity.removeprefix("$HOME/")
    return None


def _require_current_registry_application(
    data: dict,
    provider: dict,
    *,
    cwd: Path,
    registry_args,
    requested_phase: str,
    requested_iteration: int,
    selection_source: str | None,
) -> dict:
    """Re-resolve the recorded registry projection from safe current inputs."""
    recorded = data.get("specialist_registry_projection") or {}
    inputs = recorded.get("ordered_inputs") or []
    supplied = _registry_arg_paths(getattr(registry_args, "registry", None))
    supplied_by_identity = {
        _portable_registry_identity(path): path for path in supplied
    }
    explicit_paths: list[Path] = []
    for item in inputs:
        if not isinstance(item, dict) or item.get("kind") != "explicit":
            continue
        identity = str(item.get("canonical_identity") or "")
        path = supplied_by_identity.get(identity) or _registry_identity_path(identity, cwd)
        if path is None:
            _provider_gate("explicit-registry-resupply-required")
        explicit_paths.append(path)
    if set(supplied_by_identity) - {
        str(item.get("canonical_identity") or "")
        for item in inputs if isinstance(item, dict) and item.get("kind") == "explicit"
    }:
        _provider_gate("registry-input-mismatch")

    recorded_roots = [
        _registry_identity_path(str(item.get("canonical_identity") or ""), cwd)
        for item in inputs
        if isinstance(item, dict) and item.get("kind") == "skill-root"
    ]
    default_roots = {Path.home() / ".codex" / "skills", Path.home() / ".claude" / "skills"}
    custom_roots = [path for path in recorded_roots if path is not None and path not in default_roots]
    probe = argparse.Namespace(
        registry=[str(path) for path in explicit_paths],
        no_default_skill_roots=not any(
            isinstance(item, dict) and item.get("kind") in {"user", "skill-root"}
            for item in inputs
        ),
        skills_dir=os.pathsep.join(str(path) for path in custom_roots) or None,
    )
    current_candidates, _diagnostics, current_projection = _discover_specialist_registry_candidates(probe)
    if current_projection.get("effective_projection_digest") != recorded.get("effective_projection_digest"):
        _provider_gate("registry-projection-mismatch")
    provider_id = str(provider.get("provider_id") or "")
    skill = str(provider.get("skill") or provider.get("role") or "")
    matches = [
        candidate for candidate in current_candidates
        if _safe_provider_reference(_provider_id(candidate)) == provider_id
        and str(candidate.get("skill") or candidate.get("role") or "") == skill
    ]
    if len(matches) != 1:
        _provider_gate("provider-not-selected")
    candidate = matches[0]
    if candidate.get("registry_entry_digest") != provider.get("registry_entry_digest"):
        _provider_gate("registry-entry-mismatch")
    normalized = _normalize_candidate(candidate, str(candidate.get("source") or "registry"))
    for field in ("selection_id", "eligibility_selection_source", "registry_projection_digest", "context_digest", "activation_digest"):
        if field in provider:
            normalized[field] = provider[field]
    current_iteration = data.get("iteration")
    eligibility = evaluate_provider_eligibility(
        normalized,
        {
            "complexity": data.get("complexity"),
            "task_profile": data.get("task_profile") or {},
            "iteration": current_iteration,
            "previous_iteration_passed": (
                bool(data.get("passes")) if isinstance(current_iteration, int) and current_iteration >= 2 else None
            ),
        },
        requested_phase=requested_phase,
        selection_source=(selection_source or provider.get("eligibility_selection_source") or "automatic"),
    )
    if eligibility.get("activation_digest") != provider.get("activation_digest"):
        _provider_gate("activation-digest-mismatch")
    if not eligibility.get("eligible"):
        _provider_gate(str(eligibility.get("reason_code")))
    return normalized


def _is_provider_backed_application(data: dict, skill: str, args, provider: dict | None) -> bool:
    """Keep core activity records compatible; fail closed for any provider signal."""
    selected_provider_signal = bool(
        provider
        and (
            provider.get("kind") == "command"
            or provider.get("registry_projection_digest")
        )
    )
    return bool(
        selected_provider_signal
        or getattr(args, "selection_source", None)
        or getattr(args, "mode", None) == "command-provider"
        or any(
            isinstance(item, dict)
            and str(item.get("skill") or "") == skill
            and (
                item.get("mode") == "command-provider"
                or item.get("provider_kind") == "command"
                or any(key in item for key in ("provider_id", "registry_entry_digest"))
            )
            for item in data.get("specialist_invocations") or []
        )
    )


ACTIVE_PROVIDER_INVOCATION_STATUSES = frozenset({"reserved", "running"})


def _active_provider_invocations(data: dict, *, exclude: str | None = None) -> list[dict]:
    return [
        item for item in data.get("specialist_invocations") or []
        if isinstance(item, dict)
        and item.get("status") in ACTIVE_PROVIDER_INVOCATION_STATUSES
        and item.get("invocation_id") != exclude
    ]


def _reject_active_provider_mutation(data: dict, operation: str, *, exclude: str | None = None) -> None:
    active = _active_provider_invocations(data, exclude=exclude)
    if active:
        print(
            f"ERROR: provider-invocation-active: {operation} is fenced until "
            f"{active[0].get('invocation_id')} becomes terminal",
            file=sys.stderr,
        )
        error = CommandOutcomeExit(2, "expected-gate")
        error.provider_reason_code = "provider-invocation-active"
        raise error


def _replace_provider_invocation(data: dict, entry: dict) -> None:
    for index, item in enumerate(data.get("specialist_invocations") or []):
        if isinstance(item, dict) and item.get("invocation_id") == entry.get("invocation_id"):
            data["specialist_invocations"][index] = entry
            return
    raise SpecialistLifecycleError("invocation_id must identify exactly one invocation")


def _bounded_purpose_required(data: dict, skill: str | None, phase: str, status: str) -> bool:
    if status not in APPLIED_SPECIALIST_INVOCATION_STATUSES:
        return False
    provider = _provider_for_skill(data, skill)
    return bool(provider and provider.get("bounded_use") and provider.get("bounded_purpose_required"))


def _reject_unbounded_orchestrator_execution(data: dict, skill: str | None, phase: str):
    provider = _provider_for_skill(data, skill)
    if provider and provider.get("bounded_use") and phase == "execution":
        print(
            f"ERROR: bounded orchestrator specialist cannot be applied in execution phase: {skill}",
            file=sys.stderr,
        )
        sys.exit(2)


def _add_selected_specialist_metadata(data: dict, entry: dict, selection_source: str,
                                      now: str, provider: dict | None = None,
                                      reason: str | None = None) -> dict | None:
    selected = data.setdefault("specialists_selected", [])
    skill = str(entry.get("skill") or "")
    if not skill or skill in _selected_specialist_skills(data):
        return None
    provider = provider or _provider_for_skill(data, skill) or {}
    selected_entry = {
        "role": entry.get("role") or provider.get("role") or skill,
        "skill": skill,
        "kind": provider.get("kind", "skill"),
        "phases": [entry.get("phase")] if entry.get("phase") else provider.get("phases", []),
        "required": bool(provider.get("required", False)),
        "status": "selected",
        "source": f"{selection_source}:log-invocation",
        "selection_source": selection_source,
        "selected_at": now,
    }
    selection_id = entry.get("selection_id") or _current_selection_id(data)
    if selection_id:
        selected_entry["selection_id"] = selection_id
    for key in (
        "source",
        "command",
        "args",
        "env",
        "timeout",
        "bounded_use",
        "bounded_purpose_required",
        "result_contract",
    ):
        if key in provider and key not in selected_entry:
            selected_entry[key] = provider[key]
    if reason:
        selected_entry["reason"] = reason
    public_entry = _public_specialist_record(selected_entry)
    selected.append(public_entry)
    decision = data.get("specialists_decision")
    if isinstance(decision, dict) and selection_id:
        decision.update({
            "action": "select",
            "decision": "selected",
            "reason_code": "confirmed-selection" if selection_source == "confirmed-user" else "explicit-selection",
            "lifecycle_state": "selected",
            "selection_id": selection_id,
        })
        if selection_source == "confirmed-user":
            decision["confirmation_resolved"] = True
    return public_entry


def _prepare_specialist_invocation_state(
    data: dict,
    entry: dict,
    *,
    cwd: Path,
    iteration: int,
    evidence_planned: bool,
    selection_source: str | None = None,
    provider: dict | None = None,
    selection_reason: str | None = None,
) -> tuple[dict, dict, dict | None]:
    """Build and validate the complete public state before any invocation side effect."""
    prospective = copy.deepcopy(data)
    pending_entry = copy.deepcopy(entry)
    validation_probe = copy.deepcopy(prospective)
    validation_probe.setdefault("specialist_invocations", []).append(
        copy.deepcopy(pending_entry)
    )
    _validate_specialist_public_state(validation_probe)
    if evidence_planned:
        planned = _planned_specialist_archive_path(
            cwd, iteration, prospective, pending_entry
        )
        pending_entry["evidence_path"] = _state_relative_path(cwd, planned)
    selected_entry = None
    if selection_source:
        selected_entry = _add_selected_specialist_metadata(
            prospective,
            pending_entry,
            selection_source,
            pending_entry.get("timestamp") or iso_now(),
            provider,
            selection_reason,
        )
    prospective.setdefault("specialist_invocations", []).append(pending_entry)
    _validate_specialist_public_state(prospective)
    return prospective, pending_entry, selected_entry


def _command_provider_packet(data: dict, provider: dict, args) -> str:
    body = ""
    if getattr(args, "input_file", None):
        body = Path(args.input_file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    packet = {
        "mission": data.get("mission"),
        "mission_id": data.get("mission_id"),
        "correlation": {
            field: data.get(field)
            for field in ("host_run_id", "root_run_id", "parent_run_id", "child_run_id", "logical_group_id")
        },
        "iteration": args.iteration,
        "phase": args.phase,
        "provider": {
            "role": provider.get("role"),
            "skill": provider.get("skill"),
            "kind": provider.get("kind"),
            "source": provider.get("source"),
        },
        "input": body,
    }
    return json.dumps(packet, indent=2, ensure_ascii=False)


def _publish_preflight_pointer_transaction(packet_path: Path, packet_bytes: bytes, commit_state) -> None:
    """Publish a private packet only when its pointer state also commits."""
    published = False
    try:
        atomic_write_bytes(packet_path, packet_bytes)
        published = True
        commit_state()
    except BaseException:
        if published:
            with contextlib.suppress(OSError):
                packet_path.unlink()
        raise


def _dispatch_provider_execution(execution_context: object, packet_bytes: bytes, plain_runner, strict_runner):
    """Route a strict packet exclusively through the host strict runner."""
    if not isinstance(execution_context, dict):
        raise ProviderPreflightError("execution-context-invalid")
    if execution_context.get("isolation") != "strict":
        if not callable(plain_runner):
            raise ProviderPreflightError("execution-context-invalid")
        return plain_runner(packet_bytes)
    attestation = execution_context.get("isolator")
    if not isinstance(attestation, dict) or not isinstance(attestation.get("policy_digest"), str):
        raise ProviderPreflightError("isolator-unavailable")
    if not callable(strict_runner):
        raise ProviderPreflightError("isolator-unavailable")
    return strict_runner(dict(attestation), attestation["policy_digest"], packet_bytes)


def _provider_preflight_subject(data: dict, provider: dict, args) -> dict:
    """Project the exact current command-provider request into #396 inputs."""
    command = provider.get("command")
    if not isinstance(command, str) or not _portable_provider_identifier(command):
        _provider_gate("command-identity-invalid")
    execution_context = {
        "isolation": "declared-ambient", "assurance": "stdin-exact-ambient-declared",
        "cwd": "session-local-empty", "resource_mounts": [],
        "env_allowlist": sorted(_string_map(provider.get("env")).keys()),
        "ambient_scopes": ["inherited-env"], "network_destination_policy": "unverified",
    }
    isolator_name = getattr(args, "execution_isolator", None)
    if isolator_name:
        descriptor = _configured_execution_isolator(Path.cwd(), isolator_name)
        if descriptor is None:
            _provider_gate("isolator-unavailable")
        execution_context = {
            "isolation": "strict", "assurance": "host-attested-execution-isolator/1",
            "cwd": "session-local-empty", "resource_mounts": [], "env_allowlist": [],
            "ambient_scopes": [], "network_destination_policy": "verified",
            "isolator": descriptor["attestation"],
        }
    risk_scopes = (["external-context", "destination-unverified", "inherited-env"]
                   if execution_context["isolation"] == "declared-ambient" else ["external-context"])
    return {
        "session_id": str(data.get("session_id") or resolve_session_id()),
        "mission_id": str(data.get("mission_id") or ""),
        "mission": str(data.get("mission") or ""),
        "correlation": {
            field: data.get(field)
            for field in (
                "host_run_id", "root_run_id", "parent_run_id",
                "child_run_id", "logical_group_id",
            )
        },
        "provider_id": str(provider.get("provider_id") or provider.get("skill") or ""),
        "registry_entry_digest": provider.get("registry_entry_digest"),
        "selection_id": provider.get("selection_id"),
        "selection_source": args.selection_source or provider.get("eligibility_selection_source") or "automatic",
        "invocation_id": getattr(args, "invocation_id", None) or new_invocation_id(),
        "iteration": args.iteration,
        "phase": args.phase,
        "destination": {"kind": "external-service", "display_name": str(provider.get("role") or "provider")},
        "risk_scopes": risk_scopes,
        "quota_mode": "unknown",
        "effective_argv": [command, *[str(value) for value in provider.get("args") or []]],
        "env_keys": sorted(_string_map(provider.get("env")).keys()),
        "execution_context": execution_context,
    }


def _verified_preflight_packet(
    cwd: Path, data: dict, provider: dict, args, *, consuming_invocation_id: str | None = None
) -> tuple[dict, bytes]:
    """Rebuild #396's exact packet and reject every drift before process reservation."""
    pointers = data.get("provider_preflights")
    pointer = pointers.get(args.preflight_id) if isinstance(pointers, dict) else None
    if not isinstance(pointer, dict):
        _provider_gate("approval-required")
    status = pointer.get("status")
    if status == "consuming" and pointer.get("consuming_invocation_id") == consuming_invocation_id:
        pass
    elif status == "consumed":
        _provider_gate("receipt-replayed")
    elif status != "approved":
        _provider_gate("approval-required")
    # An approved state bit is never a receipt.  Receipt issuance is a host
    # verifier responsibility; do not let a hand-edited pointer authorize I/O.
    receipt_ref = pointer.get("receipt")
    if not isinstance(receipt_ref, dict):
        _provider_gate("receipt-invalid")
    if not isinstance(args.input_file, str) or not args.input_file:
        _provider_gate("preflight-input-required")
    try:
        subject = _provider_preflight_subject(data, provider, args)
        subject["invocation_id"] = pointer.get("invocation_id")
        stored_context = pointer.get("execution_context")
        if isinstance(stored_context, dict) and stored_context.get("isolation") == "strict":
            isolator_name = getattr(args, "execution_isolator", None)
            if not isolator_name:
                _provider_gate("isolator-unavailable")
            try:
                live_isolator = _configured_execution_isolator(cwd, isolator_name)
            except ValueError:
                _provider_gate("isolator-unavailable")
            if live_isolator is None or live_isolator.get("attestation") != stored_context.get("isolator"):
                _provider_gate("isolator-drift")
            subject["execution_context"] = stored_context
        rebuilt = build_preflight(subject, [safe_input_snapshot(args.input_file, root=cwd)])
        if (rebuilt["outbound_context_digest"] != pointer.get("outbound_context_digest")
                or rebuilt["outbound_packet_digest"] != pointer.get("outbound_packet_digest")):
            _provider_gate("payload-drift")
        artifact_path = pointer.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            _provider_gate("preflight-artifact-invalid")
        artifact = state_dir(cwd) / artifact_path
        raw = artifact.read_bytes()
        if raw != rebuilt["outbound_packet_bytes"]:
            _provider_gate("payload-drift")
        receipt_path = receipt_ref.get("artifact_path")
        receipt_digest = receipt_ref.get("digest")
        if not isinstance(receipt_path, str) or not isinstance(receipt_digest, str):
            _provider_gate("receipt-invalid")
        receipt_bytes = (state_dir(cwd) / receipt_path).read_bytes()
        if "sha256:" + hashlib.sha256(receipt_bytes).hexdigest() != receipt_digest:
            _provider_gate("receipt-invalid")
        receipt = json.loads(receipt_bytes)
        expected = {
            "preflight_id": args.preflight_id, "session_id": rebuilt["session_id"],
            "mission_id": rebuilt["mission_id"], "outbound_context_digest": rebuilt["outbound_context_digest"],
            "invocation_id": rebuilt["invocation_id"], "outbound_packet_digest": rebuilt["outbound_packet_digest"],
            "registry_entry_digest": rebuilt["registry_entry_digest"], "selection_id": rebuilt["selection_id"],
            "selection_source": rebuilt["selection_source"], "iteration": rebuilt["iteration"], "phase": rebuilt["phase"],
            "risk_scopes": rebuilt["risk_scopes"],
        }
        receipt_preflight = {**expected, "risk_scopes": rebuilt["risk_scopes"]}
        # The verifier registry already pins source/version before receipt
        # creation.  Re-read it here to reject registry churn before spawn.
        provenance = receipt.get("approval_provenance") if isinstance(receipt, dict) else {}
        verifier = provenance.get("verifier_id") if isinstance(provenance, dict) else None
        descriptor = _configured_approval_entry_point(cwd, verifier) if isinstance(verifier, str) else None
        if descriptor is None:
            _provider_gate("verifier-untrusted")
        validate_provider_receipt(receipt_preflight, receipt, trusted_verifiers={verifier: provenance.get("verifier_version")}, now=iso_now())
        return pointer, raw
    except (ProviderPreflightError, ValueError) as error:
        _provider_gate(str(error))


def cmd_verify_provider_approval(args):
    """Ask only a host-registered verifier to turn evidence into a receipt."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        _provider_gate("state-missing")
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text(encoding="utf-8"))
        pointer = (data.get("provider_preflights") or {}).get(args.preflight_id)
        if not isinstance(pointer, dict) or pointer.get("status") != "awaiting-approval":
            _provider_gate("preflight-not-awaiting-approval")
        try:
            packet_path = state_dir(cwd) / str(pointer["artifact_path"])
            packet_bytes = packet_path.read_bytes()
            if "sha256:" + hashlib.sha256(packet_bytes).hexdigest() != pointer["outbound_packet_digest"]:
                _provider_gate("preflight-artifact-invalid")
            packet = json.loads(packet_bytes)
            request = {
                "schema": "mission-provider-approval-request/1", "preflight_id": args.preflight_id,
                "session_id": packet["session_id"], "mission_id": packet["mission_id"],
                "outbound_context_digest": packet["outbound_context_digest"], "invocation_id": packet["invocation_id"],
                "outbound_packet_digest": pointer["outbound_packet_digest"],
                "registry_entry_digest": packet["provider"]["registry_entry_digest"],
                "selection_id": packet["selection"]["id"], "selection_source": packet["selection"]["source"],
                "iteration": packet["iteration"], "phase": packet["phase"], "risk_scopes": packet["risk_scopes"],
                "evidence_ref": args.evidence_ref,
            }
            descriptor = _configured_approval_entry_point(cwd, args.approval_verifier)
            if descriptor is None:
                _provider_gate("verifier-untrusted")
            evidence = _run_approval_verifier(descriptor, request)
            if not isinstance(evidence, dict) or evidence.get("schema") != "approval-evidence/1":
                _provider_gate("approval-evidence-invalid")
            if evidence.get("verifier_id") != args.approval_verifier:
                _provider_gate("verifier-untrusted")
            for key, value in request.items():
                if key != "schema" and evidence.get(key) != value:
                    _provider_gate("approval-evidence-binding-mismatch")
            expires_at = evidence.get("expires_at")
            nonce = evidence.get("single_use_nonce")
            if not isinstance(expires_at, str) or not isinstance(nonce, str) or not re.fullmatch(r"[0-9A-Za-z_-]{32,128}", nonce):
                _provider_gate("approval-evidence-invalid")
            receipt = {
                "schema": "mission-provider-approval-receipt/1",
                **{key: request[key] for key in request if key not in {"schema", "risk_scopes", "evidence_ref"}},
                "approved_scopes": request["risk_scopes"], "expires_at": expires_at,
                "single_use_nonce": nonce, "approval_provenance": {
                    "issuer_id": evidence.get("issuer_id"), "verifier_id": args.approval_verifier,
                    "verifier_version": evidence.get("verifier_version"), "proof_kind": evidence.get("proof_kind"),
                    "proof_digest": evidence.get("proof_digest"), "actor_kind": evidence.get("actor_kind"),
                    "actor_id": evidence.get("actor_id"),
                },
            }
            receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            receipt_dir = state_dir(cwd) / "private-receipts"; receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            receipt_file = receipt_dir / f"{args.preflight_id}.json"; atomic_write_bytes(receipt_file, receipt_bytes)
            pointer["receipt"] = {"artifact_path": str(receipt_file.resolve().relative_to(state_dir(cwd).resolve())),
                                  "digest": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()}
            pointer["status"] = "approved"
            backup_state(sf); atomic_write_json(sf, stamp_metadata(data, cwd))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            # Exception type is bounded and contains no evidence values.
            print(f"ERROR: provider approval verification failed: {type(error).__name__}", file=sys.stderr)
            _provider_gate("approval-evidence-invalid")
    print(json.dumps({"ok": True, "preflight_id": args.preflight_id, "status": "approved"}, ensure_ascii=False))


def cmd_prepare_provider_invocation(args):
    """Create a side-effect-free, private exact-packet preflight pointer."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        _provider_gate("state-missing")
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text(encoding="utf-8"))
        _validate_specialist_public_state(data)
        provider = _require_current_provider_application(
            data, _find_provider(data, args.provider), requested_phase=args.phase,
            requested_iteration=args.iteration, application_kind="preflight",
            selection_source=args.selection_source, cwd=cwd, registry_args=args,
        )
        try:
            snapshot = safe_input_snapshot(args.input_file, root=cwd)
            preflight = build_preflight(_provider_preflight_subject(data, provider, args), [snapshot])
        except ProviderPreflightError as error:
            _provider_gate(str(error))
        except ValueError:
            _provider_gate("isolator-unavailable")
        private_dir = state_dir(cwd) / "private-preflights"
        private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        artifact = private_dir / f"{preflight['preflight_id']}.json"
        # The private artifact is atomically published before the pointer.  If
        # either write fails, no state points at a partial packet.
        pointer = {
            "artifact_path": str(artifact.resolve().relative_to(state_dir(cwd).resolve())),
            "outbound_packet_digest": preflight["outbound_packet_digest"],
            "outbound_context_digest": preflight["outbound_context_digest"],
            "invocation_id": preflight["invocation_id"], "status": "awaiting-approval",
            "execution_context": preflight["outbound_packet"]["execution_context"],
        }
        data.setdefault("provider_preflights", {})[preflight["preflight_id"]] = pointer
        data["updated_at"] = iso_now()
        def commit_pointer_state():
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(data, cwd))
        _publish_preflight_pointer_transaction(artifact, preflight["outbound_packet_bytes"], commit_pointer_state)
    public = {key: value for key, value in preflight.items() if key not in {"outbound_packet_bytes"}}
    print(json.dumps(public, indent=2 if args.json else None, ensure_ascii=False))


def cmd_invoke_command_provider(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    data = json.loads(sf.read_text())
    _validate_specialist_public_state(data)
    provider = _find_provider(data, args.provider)
    if not provider:
        print(f"ERROR: provider not found in mission state: {args.provider}", file=sys.stderr)
        sys.exit(2)
    if provider.get("kind") != "command":
        print(f"ERROR: provider is not kind=command: {args.provider}", file=sys.stderr)
        sys.exit(2)
    if getattr(args, "specialists_cmd", None) != "invoke-prepared" and getattr(args, "preflight_id", None):
        _provider_gate("use-invoke-prepared")
    # #396: any command provider is an external-risk invocation until a
    # verified per-invocation preflight/receipt proves otherwise.  Keep this
    # guard before reservation, state mutation, and subprocess creation.
    if not getattr(args, "preflight_id", None):
        _provider_gate("preflight-required")
    pointer, packet = _verified_preflight_packet(cwd, data, provider, args)
    if _confirmed_selection_required(data, provider.get("skill") or provider.get("role"), "completed") and not args.selection_source:
        print(
            "ERROR: specialists_decision requested user confirmation; pass --selection-source confirmed-user "
            "when invoking an applied command provider after confirmation.",
            file=sys.stderr,
        )
        sys.exit(2)
    _require_current_provider_application(
        data,
        provider,
        requested_phase=args.phase,
        requested_iteration=args.iteration,
        application_kind="preflight",
        selection_source=args.selection_source,
        cwd=cwd,
        registry_args=args,
    )
    _reject_unbounded_orchestrator_execution(data, provider.get("skill") or provider.get("role"), args.phase)

    now = iso_now()
    entry = {
        "invocation_id": pointer["invocation_id"],
        "provider_id": provider.get("provider_id"),
        "iteration": args.iteration,
        "phase": args.phase,
        "role": provider.get("role"),
        "skill": provider.get("skill") or provider.get("role"),
        "mode": "command-provider",
        "status": "reserved",
        "lifecycle_state": "reserved",
        "timestamp": now,
        "transitioned_at": now,
        "reserved_at": now,
        "provider_kind": "command",
        "input_outbound_packet_digest": pointer["outbound_packet_digest"],
        **{
            field: data.get(field)
            for field in ("host_run_id", "root_run_id", "parent_run_id", "child_run_id", "logical_group_id")
            if data.get(field) is not None
        },
    }
    selection_id = _current_selection_id(data)
    if selection_id:
        entry["selection_id"] = selection_id
    timeout = _provider_timeout(provider, args.timeout)
    entry["timeout"] = timeout
    # Reservation and call-slot consumption are one atomic state mutation.
    with StateLock(lock_file(cwd)):
        dispatch_state = json.loads(sf.read_text())
        _validate_specialist_public_state(dispatch_state)
        _reject_active_provider_mutation(dispatch_state, "invoke-command")
        lease_decision = _enforce_session_lease_for_write(sf, dispatch_state)
        provider = _require_current_provider_application(
            dispatch_state,
            _find_provider(dispatch_state, args.provider),
            requested_phase=args.phase,
            requested_iteration=args.iteration,
            application_kind="preflight",
            selection_source=args.selection_source,
            invocation_id=entry["invocation_id"],
            cwd=cwd,
            registry_args=args,
        )
        entry["application_context_digest"] = provider.pop("_application_context_digest")
        entry["reservation_owner_session_id"] = str(dispatch_state.get("owner_session_id") or resolve_session_id())
        entry["fencing_epoch"] = int(dispatch_state.get("fencing_epoch") or lease_decision.fencing_epoch)
        dispatch_state, entry, _ = _prepare_specialist_invocation_state(
            dispatch_state,
            entry,
            cwd=cwd,
            iteration=args.iteration,
            evidence_planned=True,
        )
        preflight_pointer = (dispatch_state.get("provider_preflights") or {}).get(args.preflight_id)
        if not isinstance(preflight_pointer, dict) or preflight_pointer.get("status") != "approved":
            _provider_gate("approval-required")
        preflight_pointer["status"] = "consuming"
        preflight_pointer["consuming_invocation_id"] = entry["invocation_id"]
        record_activity_event(dispatch_state, "specialist", now)
        dispatch_state["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(dispatch_state, cwd))

    # Re-read state and registry immediately before the one allowed spawn.
    running_at = iso_now()
    with StateLock(lock_file(cwd)):
        dispatch_state = json.loads(sf.read_text())
        _validate_specialist_public_state(dispatch_state)
        current_entry = dict(invocation_by_id(dispatch_state, entry["invocation_id"]))
        provider = _require_current_provider_application(
            dispatch_state,
            _find_provider(dispatch_state, args.provider),
            requested_phase=args.phase,
            requested_iteration=args.iteration,
            application_kind="preflight",
            selection_source=args.selection_source,
            invocation_id=entry["invocation_id"],
            cwd=cwd,
            registry_args=args,
        )
        # Re-snapshot payload inputs after the reservation lock acquisition;
        # no byte validated before this point is eligible for subprocess stdin.
        preflight_pointer, packet = _verified_preflight_packet(
            cwd, dispatch_state, provider, args, consuming_invocation_id=entry["invocation_id"]
        )
        if provider.pop("_application_context_digest") != current_entry.get("application_context_digest"):
            rejected = {**current_entry, "status": "rejected", "lifecycle_state": "terminal",
                        "reason_code": "application-context-drift", "completed_at": running_at,
                        "transitioned_at": running_at}
            validate_invocation_transition(current_entry, rejected)
            _replace_provider_invocation(dispatch_state, rejected)
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(dispatch_state, cwd))
            print("ERROR: provider-ineligible: application-context-drift", file=sys.stderr)
            raise SystemExit(2)
        entry = {**current_entry, "status": "running", "lifecycle_state": "running",
                 "running_at": running_at, "started_at": running_at,
                 "transitioned_at": running_at, "heartbeat_at": running_at}
        validate_invocation_transition(current_entry, entry)
        _replace_provider_invocation(dispatch_state, entry)
        dispatch_state["updated_at"] = running_at
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(dispatch_state, cwd))

    command = provider.get("command")
    argv = [command, *[str(a) for a in provider.get("args") or []]]
    command_env = os.environ.copy()
    command_env.update(_string_map(provider.get("env")))
    execution_context = preflight_pointer.get("execution_context") if isinstance(preflight_pointer, dict) else None
    strict_result = None
    if isinstance(execution_context, dict) and execution_context.get("isolation") == "strict":
        try:
            strict_result = _dispatch_provider_execution(
                execution_context, packet,
                lambda _: (_ for _ in ()).throw(ProviderPreflightError("isolator-unavailable")),
                lambda attestation, _policy, exact_packet: _run_strict_provider_backend(
                    _configured_execution_isolator(cwd, args.execution_isolator), exact_packet
                ) if args.execution_isolator else (_ for _ in ()).throw(ProviderPreflightError("isolator-unavailable")),
            )
        except (ProviderPreflightError, ValueError, OSError):
            _provider_gate("isolator-unavailable")
    elif not _command_is_available(command):
        completed_at = iso_now()
        failed = {**entry, "status": "failed-before-start", "lifecycle_state": "terminal",
                  "transitioned_at": completed_at, "completed_at": completed_at,
                  "reason_code": "command-unavailable",
                  "reason": f"command provider is not available: {command}"}
        with StateLock(lock_file(cwd)):
            dispatch_state = json.loads(sf.read_text())
            current_entry = invocation_by_id(dispatch_state, entry["invocation_id"])
            validate_invocation_transition(current_entry, failed)
            _replace_provider_invocation(dispatch_state, failed)
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(dispatch_state, cwd))
        print(json.dumps({"ok": False, "outcome_kind": "external", "entry": failed}, ensure_ascii=False))
        return
    spawn_failed_reason = None
    if strict_result is not None:
        exit_code = strict_result["returncode"]
        stdout = _redact_provider_output(str(strict_result.get("stdout") or ""))
        stderr = _redact_provider_output(str(strict_result.get("stderr") or ""))
    else:
        try:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=command_env)
        except OSError as exc:
            spawn_failed_reason = "spawn-failed"; exit_code = None; stdout = ""; stderr = _redact_provider_output(str(exc))
            completed_at = iso_now()
            entry.update({"status": "failed-before-start", "lifecycle_state": "terminal", "transitioned_at": completed_at,
                          "completed_at": completed_at, "reason_code": "spawn-failed"})
        else:
            entry["child_pid"] = process.pid
            entry["process_identity_digest"] = provider_value_digest({"invocation_id": entry["invocation_id"], "pid": process.pid, "running_at": running_at})
            with StateLock(lock_file(cwd)):
                dispatch_state = json.loads(sf.read_text()); current_entry = dict(invocation_by_id(dispatch_state, entry["invocation_id"]))
                if current_entry.get("status") != "running":
                    process.terminate(); process.wait(timeout=5)
                    print("ERROR: provider-ineligible: invocation-not-running", file=sys.stderr); raise SystemExit(2)
                current_entry.update({"child_pid": entry["child_pid"], "process_identity_digest": entry["process_identity_digest"], "heartbeat_at": iso_now()})
                _replace_provider_invocation(dispatch_state, current_entry); backup_state(sf); atomic_write_json(sf, stamp_metadata(dispatch_state, cwd)); entry = current_entry
            try:
                raw_stdout, raw_stderr = process.communicate(input=packet, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill(); raw_stdout, raw_stderr = process.communicate(); raw_stderr = (raw_stderr or b"") + b"\ncommand provider timed out"
            exit_code = process.returncode
            stdout = _redact_provider_output((raw_stdout or b"").decode("utf-8", errors="replace"))
            stderr = _redact_provider_output((raw_stderr or b"").decode("utf-8", errors="replace"))

    if spawn_failed_reason:
        status, reason = "failed-before-start", stderr
    else:
        status, reason = _classify_command_provider_result(provider, exit_code, stdout, stderr)
    outcome = _command_outcome(
        args, "specialists-invoke-command",
        "ok" if status == "completed" else "external",
    )
    completed_at = iso_now()
    entry.update({
        "status": status,
        "lifecycle_state": "terminal",
        "transitioned_at": completed_at,
        "completed_at": completed_at,
        "exit_code": exit_code,
    })
    if reason:
        entry["reason"] = reason
    evidence = (
        "# Command Provider Evidence\n\n"
        f"- provider: {entry['skill']}\n"
        f"- role: {entry['role']}\n"
        f"- command: {_redact_provider_output(json.dumps(argv, ensure_ascii=False))}\n"
        f"- exit_code: {exit_code}\n\n"
        "## Stdout\n\n"
        f"```text\n{stdout}\n```\n\n"
        "## Stderr\n\n"
        f"```text\n{stderr}\n```\n"
    )
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        _validate_specialist_public_state(data)
        _require_current_provider_application(
            data,
            _find_provider(data, args.provider),
            requested_phase=args.phase,
            requested_iteration=args.iteration,
            application_kind="result-import",
            selection_source=args.selection_source,
            invocation_id=entry["invocation_id"],
            cwd=cwd,
            registry_args=args,
        )
        current = data.get("activity_current")
        if (
            isinstance(current, dict)
            and current.get("kind") == "external-wait"
            and current.get("reason") == "external-command"
            and current.get("started_at") == now
        ):
            end_activity_segment(data, completed_at)
        data["updated_at"] = completed_at
        data = stamp_metadata(data, cwd)
        applied_selection_source = (
            args.selection_source
            if status in APPLIED_SPECIALIST_INVOCATION_STATUSES
            else None
        )
        if applied_selection_source:
            entry["selection_source"] = applied_selection_source
        try:
            current_entry = invocation_by_id(data, entry["invocation_id"])
            validate_invocation_transition(current_entry, entry)
        except SpecialistLifecycleError as exc:
            print(f"ERROR: command invocation checkpoint is invalid: {exc}", file=sys.stderr)
            sys.exit(2)
        selected_entry = None
        if applied_selection_source:
            selected_entry = _add_selected_specialist_metadata(
                data, entry, applied_selection_source, completed_at, provider, reason
            )
        for index, item in enumerate(data["specialist_invocations"]):
            if item.get("invocation_id") == entry["invocation_id"]:
                data["specialist_invocations"][index] = entry
                break
        preflight_pointer = (data.get("provider_preflights") or {}).get(args.preflight_id)
        if isinstance(preflight_pointer, dict) and preflight_pointer.get("status") == "consuming":
            preflight_pointer["status"] = "consumed"
            preflight_pointer["consumed_invocation_id"] = entry["invocation_id"]
        _validate_specialist_public_state(data)
        _append_command_outcome(data, outcome)
        archived_to = _commit_specialist_state_with_archive(
            sf, cwd, data, entry, args.iteration, evidence
        )
    result = {"ok": status == "completed", "outcome_kind": outcome["outcome_kind"], "outcome": outcome, "entry": entry}
    if selected_entry:
        result["selected_entry"] = selected_entry
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))


def _process_identity_is_live(entry: dict) -> bool:
    pid = entry.get("child_pid")
    if type(pid) is not int or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def cmd_reconcile_provider_invocation(args):
    """Fenced terminalization for an orphaned running provider invocation."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        raise SystemExit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        _validate_specialist_public_state(data)
        lease_decision = _enforce_session_lease_for_write(sf, data)
        try:
            existing = dict(invocation_by_id(data, args.invocation_id))
        except SpecialistLifecycleError as exc:
            _provider_gate(str(exc))
        if existing.get("status") != "running":
            _provider_gate("invocation-not-running")
        reservation_epoch = existing.get("fencing_epoch")
        current_epoch = int(data.get("fencing_epoch") or lease_decision.fencing_epoch)
        if args.expected_fencing_epoch != reservation_epoch or current_epoch < reservation_epoch:
            _provider_gate("stale-fencing-epoch")
        reservation_owner = existing.get("reservation_owner_session_id")
        current_owner = str(data.get("owner_session_id") or resolve_session_id())
        if current_epoch == reservation_epoch and current_owner != reservation_owner:
            _provider_gate("reservation-owner-mismatch")
        if current_epoch > reservation_epoch and args.status != "abandoned-unknown":
            _provider_gate("recovered-result-unknown")
        if _process_identity_is_live(existing):
            _provider_gate("process-still-running")
        if args.status in {"completed", "failed"} and not existing.get("process_identity_digest"):
            _provider_gate("process-identity-unknown")
        try:
            evidence = _read_specialist_evidence_input(Path(args.evidence))
        except SpecialistEvidenceInputError as exc:
            _provider_gate(exc.reason_code)
        completed_at = iso_now()
        terminal = {
            **existing,
            "status": args.status,
            "lifecycle_state": "terminal",
            "transitioned_at": completed_at,
            "completed_at": completed_at,
            "result_artifact_digest": provider_value_digest(evidence),
            "reason_code": (
                "reconciled-result" if args.status in {"completed", "failed"}
                else "reconciled-outcome-unknown"
            ),
        }
        if args.status == "abandoned-unknown":
            terminal["reason"] = "operator could not establish a trustworthy child result"
        validate_invocation_transition(existing, terminal)
        _replace_provider_invocation(data, terminal)
        data["updated_at"] = completed_at
        backup_state(sf)
        archived_to = _commit_specialist_state_with_archive(
            sf, cwd, stamp_metadata(data, cwd), terminal, terminal["iteration"], evidence
        )
    print(json.dumps({
        "ok": True,
        "invocation_id": args.invocation_id,
        "status": args.status,
        "evidence_path": str(archived_to),
    }, indent=2 if args.json else None, ensure_ascii=False))


def _state_relative_path(cwd: Path, path_text: str) -> str:
    path = Path(path_text)
    try:
        rel = path.resolve().relative_to(cwd.resolve())
        return str(rel)
    except ValueError:
        return str(path)


WORKTREE_ARCHIVE_SCHEMA = "mission-worktree-archive/1"
WORKTREE_ARCHIVE_POINTER_SCHEMA = "mission-worktree-current/1"


class WorktreeArchiveError(ValueError):
    """A worktree archive cannot be created without losing evidence integrity."""


def _archive_source_file(cwd: Path, reference: str, evidence_kind: str) -> tuple[Path, str]:
    """Resolve one allowlisted state reference without following symlinks or escaping state."""
    if not isinstance(reference, str) or not reference.strip():
        raise WorktreeArchiveError(f"required evidence reference is missing: {evidence_kind}")
    state_root = Path(os.path.abspath(str(state_dir(cwd))))
    raw = Path(reference).expanduser()
    candidate = raw if raw.is_absolute() else cwd / raw
    candidate = Path(os.path.abspath(str(candidate)))
    state_root = Path(os.path.abspath(str(state_root)))
    try:
        relative = candidate.resolve(strict=False).relative_to(state_root.resolve(strict=False))
    except ValueError as exc:
        raise WorktreeArchiveError(
            f"required evidence is outside .mission-state: {evidence_kind}: {reference}"
        ) from exc

    current = Path(candidate.anchor or candidate.root)
    for part in candidate.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise WorktreeArchiveError(f"required evidence must not be a symlink: {evidence_kind}: {reference}")

    current = state_root
    if current.is_symlink():
        raise WorktreeArchiveError("source .mission-state must not be a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WorktreeArchiveError(f"required evidence must not be a symlink: {evidence_kind}: {reference}")
    if not current.exists() or not current.is_file():
        raise WorktreeArchiveError(f"required evidence file is missing: {evidence_kind}: {reference}")
    try:
        current.resolve(strict=True).relative_to(state_root.resolve(strict=True))
    except (FileNotFoundError, ValueError) as exc:
        raise WorktreeArchiveError(
            f"required evidence is outside .mission-state: {evidence_kind}: {reference}"
        ) from exc
    return current, (Path(".mission-state") / relative).as_posix()


def _worktree_bundle_name(cwd: Path) -> str:
    slug = _slug_for_filename(cwd.name)[:48]
    identity = hashlib.sha256(str(cwd.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"worktree-{slug}-{identity}"


def _archive_history_path(kind: str, iteration: int, mission8: str, index: int, suffix: str) -> Path:
    return Path("archive") / "history" / f"iter-{iteration}-{mission8}-{kind}-{index}{suffix}"


def _collect_worktree_archive_specs(cwd: Path, state_file_path: Path, data: dict) -> list[dict]:
    """Collect only current-session evidence references explicitly allowlisted by the state schema."""
    session_id = str(data.get("session_id") or "").strip()
    mission_id = str(data.get("mission_id") or "").strip()
    iteration = data.get("iteration")
    if not session_id or not mission_id:
        raise WorktreeArchiveError("session_id and mission_id are required")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 0:
        raise WorktreeArchiveError("iteration must be a non-negative integer")
    if data.get("loop_active") is True:
        raise WorktreeArchiveError("active session cannot be archived; mark pass or halt first")

    specs: list[dict] = []

    def add(kind: str, reference: str, archive_path: Path, item_iteration: int | None = None) -> None:
        source, normalized_reference = _archive_source_file(cwd, reference, kind)
        effective_iteration = iteration if item_iteration is None else item_iteration
        spec = {
            "evidence_kind": kind,
            "iteration": effective_iteration,
            "source": source,
            "source_reference": normalized_reference,
            "archive_path": archive_path,
        }
        if kind == "review-input":
            matches = [
                item for item in (data.get("review_evidence_refs") or [])
                if isinstance(item, dict)
                and item.get("path") == normalized_reference
                and item.get("iteration") == effective_iteration
            ]
            if len(matches) != 1:
                raise WorktreeArchiveError("review input reference is missing or ambiguous")
            try:
                spec["verified_content"] = read_verified_review_input_evidence(
                    cwd, matches[0], expected_iteration=effective_iteration,
                )
            except ValueError as exc:
                raise WorktreeArchiveError("review input evidence integrity mismatch") from exc
        specs.append(spec)

    add("state", str(state_file_path), Path("sessions") / f"{_sanitize_sid(session_id)}.json")

    assumptions_path = data.get("assumptions_path")
    if assumptions_path:
        add("assumptions", str(assumptions_path), Path("sessions") / Path(str(assumptions_path)).name)

    artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
    artifact_path = artifact.get("path")
    if artifact.get("required_for_pass") and not artifact_path:
        raise WorktreeArchiveError("required evidence reference is missing: artifact")
    if artifact_path:
        artifact_reference = str(artifact_path)
        if _normalized_state_reference(artifact_reference) is not None:
            add("artifact", str(artifact_path), Path("artifacts") / _sanitize_sid(session_id) / Path(str(artifact_path)).name)
        else:
            artifact_candidate = Path(artifact_reference).expanduser()
            if artifact_candidate.is_absolute():
                try:
                    artifact_relative = artifact_candidate.resolve(strict=False).relative_to(cwd.resolve(strict=False))
                except ValueError as exc:
                    raise WorktreeArchiveError(
                        f"required evidence is outside .mission-state: artifact: {artifact_reference}"
                    ) from exc
            else:
                artifact_relative = _safe_archive_relative_path(artifact_reference, "artifact")
            specs.append(
                _tracked_repo_artifact_spec(cwd, artifact_relative.as_posix(), "artifact", iteration)
            )

    history = [entry for entry in (data.get("score_history") or []) if isinstance(entry, dict)]
    if data.get("passes") is True and not history and not data.get("force_approved_by_user"):
        raise WorktreeArchiveError("required evidence reference is missing: scoring")
    last_by_iteration: dict[int, int] = {}
    for index, entry in enumerate(history):
        entry_iteration = entry.get("iteration")
        if isinstance(entry_iteration, int) and not isinstance(entry_iteration, bool) and entry_iteration >= 0:
            last_by_iteration[entry_iteration] = index
    mission8 = mission_id[:8]
    for index, entry in enumerate(history):
        entry_iteration = entry.get("iteration")
        if not isinstance(entry_iteration, int) or isinstance(entry_iteration, bool) or entry_iteration < 0:
            raise WorktreeArchiveError("score_history iteration must be a non-negative integer")
        scoring_reference = str(entry.get("scoring_evidence_path") or "").strip()
        if data.get("passes") is True and not scoring_reference:
            raise WorktreeArchiveError(f"required evidence reference is missing: scoring iteration {entry_iteration}")
        if scoring_reference:
            suffix = Path(scoring_reference).suffix or ".json"
            scoring_path = (
                Path("archive") / f"iter-{entry_iteration}-{mission8}-scoring{suffix}"
                if last_by_iteration.get(entry_iteration) == index
                else _archive_history_path("scoring", entry_iteration, mission8, index, suffix)
            )
            add("scoring", scoring_reference, scoring_path, entry_iteration)

        reviews_reference = str(entry.get("findings_evidence_path") or "").strip()
        if entry.get("score_source") == "scoring-json" and not reviews_reference:
            raise WorktreeArchiveError(f"required evidence reference is missing: reviews iteration {entry_iteration}")
        if reviews_reference:
            suffix = Path(reviews_reference).suffix or ".json"
            reviews_path = (
                Path("archive") / f"iter-{entry_iteration}-{mission8}-reviews{suffix}"
                if last_by_iteration.get(entry_iteration) == index
                else _archive_history_path("reviews", entry_iteration, mission8, index, suffix)
            )
            add("reviews", reviews_reference, reviews_path, entry_iteration)

    specialist_counts: dict[tuple[int, str], int] = {}
    for index, invocation in enumerate(data.get("specialist_invocations") or []):
        if not isinstance(invocation, dict):
            continue
        reference = str(invocation.get("evidence_path") or "").strip()
        if not reference:
            continue
        item_iteration = invocation.get("iteration")
        if not isinstance(item_iteration, int) or isinstance(item_iteration, bool) or item_iteration < 0:
            item_iteration = iteration
        skill = _slug_for_filename(str(invocation.get("skill") or invocation.get("role") or "unknown"))
        key = (item_iteration, skill)
        occurrence = specialist_counts.get(key, 0)
        specialist_counts[key] = occurrence + 1
        suffix = Path(reference).suffix or ".md"
        filename = f"iter-{item_iteration}-{mission8}-specialist-{skill}"
        if occurrence:
            filename += f"-{occurrence}"
        add("specialist", reference, Path("archive") / f"{filename}{suffix}", item_iteration)

    progress = data.get("progress") if isinstance(data.get("progress"), dict) else {}
    for field, kind in (("evidence_path", "progress"), ("artifact_path", "progress-artifact")):
        reference = str(progress.get(field) or "").strip()
        if reference:
            suffix = Path(reference).suffix
            add(kind, reference, Path("archive") / f"iter-{iteration}-{mission8}-{kind}{suffix}")

    # The shared extractor is the archive contract.  Keep the historical
    # human-friendly destinations above, then add every provenance-only
    # reference it identifies using a content-addressed collision-safe name.
    expected = worktree_archive_lineage_references(
        data, f".mission-state/sessions/{state_file_path.name}", repo_root=cwd,
    )
    if expected is None:
        raise WorktreeArchiveError("state lineage references are invalid")
    existing = Counter(
        (spec["evidence_kind"], spec["iteration"], spec["source_reference"])
        for spec in specs
    )
    required = Counter(expected)
    for (kind, item_iteration, reference), count in required.items():
        while existing[(kind, item_iteration, reference)] < count:
            suffix = Path(reference).suffix or ".json"
            identity = hashlib.sha256(
                f"{kind}\0{item_iteration}\0{reference}\0{existing[(kind, item_iteration, reference)]}".encode("utf-8")
            ).hexdigest()[:16]
            if kind == "artifact" and not reference.startswith(".mission-state/"):
                specs.append(_tracked_repo_artifact_spec(cwd, reference, kind, item_iteration))
            else:
                add(
                    kind,
                    reference,
                    Path("archive") / "lineage" / f"iter-{item_iteration}-{mission8}-{kind}-{identity}{suffix}",
                    item_iteration,
                )
            existing[(kind, item_iteration, reference)] += 1

    destinations: dict[str, Path] = {}
    for spec in specs:
        archive_path = (
            spec["archive_path"].as_posix()
            if "archive_path" in spec
            else f"repo-artifact:{spec['source_reference']}"
        )
        if archive_path in destinations:
            raise WorktreeArchiveError(f"duplicate archive path: {archive_path}")
        destinations[archive_path] = spec.get("source")
    return specs


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_relative_path(value: object, field: str, *, state_reference: bool = False) -> Path:
    if not isinstance(value, str) or not value or "://" in value:
        raise WorktreeArchiveError(f"invalid archive manifest {field}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise WorktreeArchiveError(f"invalid archive manifest {field}: {value}")
    if state_reference and (not path.parts or path.parts[0] != ".mission-state"):
        raise WorktreeArchiveError(f"invalid archive manifest {field}: {value}")
    return path


def _normalized_state_reference(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if ".mission-state" not in path.parts:
        return None
    index = path.parts.index(".mission-state")
    return Path(*path.parts[index:]).as_posix()


def _git_command_bytes(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorktreeArchiveError("required evidence is outside .mission-state")
    return result.stdout


def _git_command_text(cwd: Path, *args: str) -> str:
    return _git_command_bytes(cwd, *args).decode("utf-8").strip()


def _tracked_repo_artifact_spec(
    cwd: Path, reference: str, kind: str, iteration: int,
) -> dict[str, Any]:
    relative = _safe_archive_relative_path(reference, kind)
    if not relative:
        raise WorktreeArchiveError(f"required evidence is outside .mission-state: {kind}: {reference}")
    tracked = subprocess.run(
        ["git", "-C", str(cwd), "ls-files", "--error-unmatch", "--", relative.as_posix()],
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise WorktreeArchiveError(f"required evidence is outside .mission-state: {kind}: {reference}")
    head_sha = _git_command_text(cwd, "rev-parse", "HEAD")
    tree_entry = subprocess.run(
        ["git", "-C", str(cwd), "ls-tree", "-l", "HEAD", "--", relative.as_posix()],
        capture_output=True,
        text=True,
        check=False,
    )
    if tree_entry.returncode == 0 and tree_entry.stdout.strip():
        mode = tree_entry.stdout.split(None, 1)[0]
        if mode == "120000":
            raise WorktreeArchiveError(f"repo artifact must not be a symlink: {reference}")
    try:
        content = _git_command_bytes(cwd, "show", f"{head_sha}:{relative.as_posix()}")
    except WorktreeArchiveError as exc:
        raise WorktreeArchiveError(
            f"artifact is staged but not yet committed: {reference}; commit before archiving"
        ) from exc
    digest = hashlib.sha256(content).hexdigest()
    return {
        "evidence_kind": kind,
        "kind": "repo-artifact",
        "iteration": iteration,
        "source_reference": relative.as_posix(),
        "path": relative.as_posix(),
        "digest": digest,
        "head_sha": head_sha,
    }


def _ensure_regular_directory_path(root: Path, relative_parts: tuple[str, ...]) -> Path:
    current = root
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise WorktreeArchiveError(f"archive destination must not contain symlinks: {current}")
        if current.exists() and not current.is_dir():
            raise WorktreeArchiveError(f"archive destination must be a directory: {current}")
    return current


def _git_checkout_identity(path: Path) -> tuple[Path, Path]:
    if not path.exists() or not path.is_dir():
        raise WorktreeArchiveError(
            f"destination must be an existing checkout in the same git common directory: {path}"
        )
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or len(lines) != 2:
        raise WorktreeArchiveError(
            f"destination must be an existing checkout in the same git common directory: {path}"
        )
    checkout_root = Path(lines[0]).resolve()
    if checkout_root != path.resolve():
        raise WorktreeArchiveError(f"archive root must be a git checkout root in the same git common directory: {path}")
    common = Path(lines[1])
    if not common.is_absolute():
        common = path / common
    return checkout_root, common.resolve()


def _validate_archive_git_boundary(source: Path, destination: Path) -> None:
    source_root, source_common = _git_checkout_identity(source)
    destination_root, destination_common = _git_checkout_identity(destination)
    if source_root == destination_root or source_common != destination_common:
        raise WorktreeArchiveError(
            "destination must be a different checkout in the same git common directory"
        )


def _build_worktree_archive_staging(staging: Path, data: dict, specs: list[dict], created_at: str) -> dict:
    evidence: list[dict] = []
    for spec in specs:
        if spec.get("kind") == "repo-artifact":
            evidence.append(
                {
                    "session_id": data["session_id"],
                    "mission_id": data["mission_id"],
                    "iteration": spec["iteration"],
                    "evidence_kind": spec["evidence_kind"],
                    "kind": spec["kind"],
                    "source_reference": spec["source_reference"],
                    "path": spec["path"],
                    "digest": spec["digest"],
                    "head_sha": spec["head_sha"],
                }
            )
            continue
        destination = staging / spec["archive_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        verified_content = spec.get("verified_content")
        if verified_content is not None:
            source_hash = hashlib.sha256(verified_content).hexdigest()
            destination.write_bytes(verified_content)
        else:
            source_hash = _sha256_file(spec["source"])
            shutil.copy2(spec["source"], destination)
        archived_hash = _sha256_file(destination)
        if archived_hash != source_hash:
            raise WorktreeArchiveError(f"checksum mismatch after copy: {spec['source_reference']}")
        evidence.append(
            {
                "session_id": data["session_id"],
                "mission_id": data["mission_id"],
                "iteration": spec["iteration"],
                "evidence_kind": spec["evidence_kind"],
                "source_reference": spec["source_reference"],
                "archive_path": spec["archive_path"].as_posix(),
                "sha256": archived_hash,
                "size": destination.stat().st_size,
            }
        )
    core = {
        "schema": WORKTREE_ARCHIVE_SCHEMA,
        "session_id": data["session_id"],
        "mission_id": data["mission_id"],
        "iteration": data["iteration"],
        "evidence": evidence,
    }
    content_digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {**core, "created_at": created_at, "content_digest": content_digest}
    atomic_write_json(staging / "manifest.json", manifest)
    return manifest


def _existing_archive_manifest(bundle: Path) -> dict | None:
    if not bundle.exists():
        return None
    if bundle.is_symlink() or not bundle.is_dir():
        raise WorktreeArchiveError(f"archive destination is not a regular directory: {bundle}")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise WorktreeArchiveError(f"existing archive manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WorktreeArchiveError(f"existing archive manifest is invalid: {manifest_path}") from exc
    if manifest.get("schema") != WORKTREE_ARCHIVE_SCHEMA:
        raise WorktreeArchiveError(f"unsupported existing archive manifest schema: {manifest.get('schema')}")
    evidence = manifest.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise WorktreeArchiveError(f"existing archive manifest has no evidence: {manifest_path}")
    core = {
        "schema": manifest.get("schema"),
        "session_id": manifest.get("session_id"),
        "mission_id": manifest.get("mission_id"),
        "iteration": manifest.get("iteration"),
        "evidence": evidence,
    }
    expected_digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if manifest.get("content_digest") != expected_digest:
        raise WorktreeArchiveError(f"existing archive manifest digest mismatch: {manifest_path}")
    seen_paths: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise WorktreeArchiveError(f"existing archive manifest evidence is invalid: {manifest_path}")
        _safe_archive_relative_path(item.get("source_reference"), "source_reference", state_reference=True)
        if item.get("kind") == "repo-artifact":
            if (
                not isinstance(item.get("path"), str)
                or not isinstance(item.get("digest"), str)
                or len(item["digest"]) != 64
                or not isinstance(item.get("head_sha"), str)
                or len(item["head_sha"]) not in {40, 64}
                or item.get("archive_path") is not None
                or item.get("sha256") is not None
                or item.get("size") is not None
            ):
                raise WorktreeArchiveError(f"existing archive manifest evidence is invalid: {manifest_path}")
            _safe_archive_relative_path(item.get("path"), "path")
            continue
        relative = _safe_archive_relative_path(item.get("archive_path"), "archive_path")
        if relative.as_posix() in seen_paths:
            raise WorktreeArchiveError(f"duplicate archive path in existing manifest: {relative}")
        seen_paths.add(relative.as_posix())
        archived = bundle / relative
        current = bundle
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise WorktreeArchiveError(f"existing archive evidence must not be a symlink: {relative}")
        expected_size = item.get("size")
        expected_hash = item.get("sha256")
        if (
            not archived.is_file()
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or archived.stat().st_size != expected_size
            or _sha256_file(archived) != expected_hash
        ):
            raise WorktreeArchiveError(f"existing archive evidence integrity mismatch: {relative}")
    return manifest


def _read_archive_pointer(bundle: Path) -> tuple[str, Path, dict] | None:
    if not bundle.exists():
        return None
    if bundle.is_symlink() or not bundle.is_dir():
        raise WorktreeArchiveError(f"archive destination is not a regular directory: {bundle}")
    pointer_path = bundle / "current.json"
    if not pointer_path.exists() and not pointer_path.is_symlink():
        return None
    if pointer_path.is_symlink() or not pointer_path.is_file():
        raise WorktreeArchiveError(f"archive pointer is not a regular file: {pointer_path}")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WorktreeArchiveError(f"archive pointer is invalid: {pointer_path}") from exc
    generation = pointer.get("generation") if isinstance(pointer, dict) else None
    if (
        not isinstance(pointer, dict)
        or pointer.get("schema") != WORKTREE_ARCHIVE_POINTER_SCHEMA
        or not isinstance(generation, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", generation)
        or generation in {".", ".."}
    ):
        raise WorktreeArchiveError(f"archive pointer is invalid: {pointer_path}")
    generation_root = bundle / "generations" / generation
    if generation_root.is_symlink() or not generation_root.is_dir():
        raise WorktreeArchiveError(f"archive generation is missing: {generation_root}")
    manifest = _existing_archive_manifest(generation_root)
    if manifest.get("content_digest") != generation:
        raise WorktreeArchiveError(f"archive generation digest mismatch: {generation_root}")
    return generation, generation_root, manifest


def _atomic_write_archive_pointer(bundle: Path, generation: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", generation) or generation in {".", ".."}:
        raise WorktreeArchiveError(f"invalid archive generation: {generation}")
    atomic_write_json(
        bundle / "current.json",
        {"schema": WORKTREE_ARCHIVE_POINTER_SCHEMA, "generation": generation},
    )


def _publish_archive_generation(staging: Path, bundle: Path, generation: str) -> str:
    """Publish an immutable generation, then atomically advance the stable pointer."""
    existing_pointer = _read_archive_pointer(bundle)
    legacy_exists = bundle.is_dir() and (bundle / "manifest.json").is_file()
    bundle.mkdir(parents=True, exist_ok=True)
    generations = _ensure_regular_directory_path(bundle, ("generations",))
    generations.mkdir(parents=True, exist_ok=True)
    generation_root = generations / generation
    if generation_root.exists() or generation_root.is_symlink():
        if generation_root.is_symlink() or not generation_root.is_dir():
            raise WorktreeArchiveError(f"archive generation is not a regular directory: {generation_root}")
        manifest = _existing_archive_manifest(generation_root)
        if manifest.get("content_digest") != generation:
            raise WorktreeArchiveError(f"archive generation digest mismatch: {generation_root}")
        shutil.rmtree(staging)
    else:
        os.replace(staging, generation_root)

    if existing_pointer and existing_pointer[0] == generation:
        return "unchanged"
    _atomic_write_archive_pointer(bundle, generation)
    return "updated" if existing_pointer or legacy_exists else "created"


def cmd_archive_worktree(args):
    cwd = Path.cwd().resolve()
    destination_root = Path(args.destination_root).expanduser().resolve()
    if destination_root == cwd:
        print("ERROR: destination root must differ from the source worktree", file=sys.stderr)
        sys.exit(2)
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)

    try:
        _validate_archive_git_boundary(cwd, destination_root)
        _ensure_regular_directory_path(destination_root, (".mission-state", "archive"))
        _ensure_regular_directory_path(cwd, (".mission-state",))
    except WorktreeArchiveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    archive_root = state_dir(destination_root) / "archive"
    bundle = archive_root / _worktree_bundle_name(cwd)
    staging: Path | None = None
    try:
        with StateLock(lock_file(destination_root)):
            with StateLock(lock_file(cwd)):
                data = json.loads(sf.read_text(encoding="utf-8"))
                _validate_specialist_public_state(data)
                specs = _collect_worktree_archive_specs(cwd, sf, data)
                if args.dry_run:
                    result = {
                        "ok": True,
                        "action": "dry-run",
                        "bundle_path": str(bundle),
                        "evidence_count": len(specs),
                    }
                    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))
                    return
                archive_root.mkdir(parents=True, exist_ok=True)
                if bundle.is_symlink() or (bundle.exists() and not bundle.is_dir()):
                    raise WorktreeArchiveError(f"archive destination is not a regular directory: {bundle}")
                bundle.mkdir(parents=True, exist_ok=True)
                generations = _ensure_regular_directory_path(bundle, ("generations",))
                generations.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=str(generations)))
                current = _read_archive_pointer(bundle)
                existing = (
                    current[2]
                    if current
                    else _existing_archive_manifest(bundle) if (bundle / "manifest.json").is_file() else None
                )
                created_at = str(existing.get("created_at")) if existing else iso_now()
                manifest = _build_worktree_archive_staging(staging, data, specs, created_at)
                action = _publish_archive_generation(staging, bundle, manifest["content_digest"])
                staging = None
        result = {
            "ok": True,
            "action": action,
            "bundle_path": str(bundle),
            "manifest": manifest,
        }
        print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))
    except (WorktreeArchiveError, json.JSONDecodeError, OSError, TimeoutError) as exc:
        if staging and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)


def _artifact_dir(cwd: Path, session_id: str) -> Path:
    return state_dir(cwd) / "artifacts" / _sanitize_sid(session_id)


def _artifact_path(cwd: Path, session_id: str) -> Path:
    return _artifact_dir(cwd, session_id) / "mission-artifact.md"


def _resolve_project_output_path(cwd: Path, path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve()
    try:
        resolved.relative_to(cwd.resolve())
    except ValueError:
        print(f"ERROR: output path must stay inside project root: {path_text}", file=sys.stderr)
        sys.exit(2)
    return resolved


def _artifact_state(data: dict) -> dict:
    artifact = data.get("artifact")
    return artifact if isinstance(artifact, dict) else {}


def _artifact_blocks(artifact: dict) -> list[dict]:
    blocks = artifact.get("blocks")
    return blocks if isinstance(blocks, list) else []


def _require_artifact(data: dict) -> dict:
    artifact = _artifact_state(data)
    if not artifact:
        print("ERROR: artifact is not initialized. Run `mission-state.py artifact init` first.", file=sys.stderr)
        sys.exit(2)
    return artifact


def _validate_artifact_section(section: str) -> str:
    key = section.strip().lower().replace("-", "_")
    if key not in ARTIFACT_SECTIONS:
        print(
            "ERROR: unknown artifact section. Use one of: "
            + ", ".join(sorted(ARTIFACT_SECTIONS)),
            file=sys.stderr,
        )
        sys.exit(2)
    return key


def _read_artifact_input(args) -> tuple[str, str | None]:
    has_text = getattr(args, "text", None) is not None
    has_file = getattr(args, "file", None) is not None
    if has_text == has_file:
        print("ERROR: provide exactly one of --text or --file", file=sys.stderr)
        sys.exit(2)
    if has_text:
        return args.text, None
    if args.file == "-":
        return sys.stdin.read(), "stdin"
    src = Path(args.file)
    if not (src.exists() and src.is_file()):
        print(f"ERROR: artifact input file not found: {args.file}", file=sys.stderr)
        sys.exit(2)
    return src.read_text(encoding="utf-8"), str(src)


def _format_artifact_block(block: dict) -> str:
    content = str(block.get("content") or "").rstrip()
    source = block.get("source")
    timestamp = block.get("timestamp")
    lines = []
    if timestamp or source:
        meta = []
        if timestamp:
            meta.append(f"timestamp={timestamp}")
        if source:
            meta.append(f"source={source}")
        lines.append(f"<!-- artifact-block: {' '.join(meta)} -->")
    lines.append(content if content else "_No content recorded._")
    return "\n".join(lines).rstrip()


def _render_artifact_markdown(data: dict, artifact: dict) -> str:
    title = artifact.get("title") or data.get("mission") or "Mission Artifact"
    sid = data.get("session_id") or "unknown"
    mission_id_text = data.get("mission_id") or "unknown"
    path = artifact.get("path") or f".mission-state/artifacts/{sid}/mission-artifact.md"
    status = artifact.get("status") or "draft"
    redaction_status = artifact.get("redaction_status") or "unchecked"
    blocks = _artifact_blocks(artifact)
    by_section = {key: [] for key in ARTIFACT_SECTIONS}
    for block in blocks:
        section = block.get("section")
        if section in by_section:
            by_section[section].append(block)

    lines = [
        f"# {title}",
        "",
        "<!-- mission-artifact: generated-by=mission-state.py artifact render -->",
        "",
        "## Metadata",
        "",
        f"- session_id: {sid}",
        f"- mission_id: {mission_id_text}",
        f"- status: {status}",
        f"- artifact_path: {path}",
        f"- redaction_status: {redaction_status}",
        f"- updated_at: {artifact.get('updated_at') or data.get('updated_at') or ''}",
        "",
    ]
    if artifact.get("required_for_pass"):
        lines.extend(["- required_for_pass: true", ""])

    defaults = {
        "mission": data.get("mission") or "",
        "plan": "No plan blocks recorded yet.",
        "execution": "No execution blocks recorded yet.",
        "evidence": "No evidence blocks recorded yet.",
        "review": "No review blocks recorded yet.",
        "score_gate": _score_gate_summary(data),
        "assumptions": f"See `{data.get('assumptions_path')}`." if data.get("assumptions_path") else "",
        "follow_ups": "No follow-ups recorded.",
    }
    for section, heading in ARTIFACT_SECTIONS.items():
        lines.extend([f"## {heading}", ""])
        section_blocks = by_section.get(section) or []
        if section_blocks:
            for i, block in enumerate(section_blocks):
                if i:
                    lines.append("")
                lines.append(_format_artifact_block(block))
        else:
            lines.append(defaults.get(section) or "_No content recorded._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _score_gate_summary(data: dict) -> str:
    history = data.get("score_history") or []
    scored = [entry for entry in history if _is_valid_composite(entry.get("composite"))]
    if not scored:
        return "No score has been recorded yet."
    latest = scored[-1]
    return (
        f"- composite: {latest.get('composite')}\n"
        f"- min_item: {latest.get('min_item')}\n"
        f"- threshold: {data.get('threshold', DEFAULT_THRESHOLD)}\n"
        f"- open_high: {latest.get('open_high', 0)}"
    )


def _write_artifact(cwd: Path, data: dict, artifact: dict) -> Path:
    path_text = artifact.get("path")
    path = _resolve_project_output_path(cwd, path_text) if path_text else _artifact_path(cwd, data.get("session_id") or resolve_session_id())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_artifact_markdown(data, artifact), encoding="utf-8")
    return path


def _refresh_artifact_identity(cwd: Path, data: dict, artifact: dict, path: Path) -> None:
    identity, _ = capture_artifact_identity(
        cwd,
        _state_relative_path(cwd, str(path)),
        str(data.get("session_id") or resolve_session_id()),
    )
    invalidate_artifact_lint_observation(data)
    artifact.update(identity)
    data["artifact_applicability"] = "producing"


def _artifact_gate_error(data: dict, cwd: Path) -> str | None:
    artifact = _artifact_state(data)
    if not artifact.get("required_for_pass"):
        return None
    path_text = artifact.get("path")
    if not path_text:
        return "artifact is required but no artifact.path is recorded"
    path = _resolve_project_output_path(cwd, path_text)
    if not path.exists():
        return f"artifact is required but file is missing: {path_text}"
    if artifact.get("status") not in {"rendered", "exported", "publish-prepared", "published"}:
        return f"artifact is required but status is {artifact.get('status')!r}; run `mission-state.py artifact render`"
    if not artifact.get("last_rendered_at"):
        return "artifact is required but last_rendered_at is missing; run `mission-state.py artifact render`"
    return None


def _artifact_profile_coverage(cwd: Path, data: dict) -> dict:
    states = []
    for path in _iter_state_files(cwd, include_archive=True):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if isinstance(candidate, dict):
            states.append(candidate)
    summary = summarize_artifact_coverage(states)
    profile = data.get("task_profile")
    primary = profile.get("primary") if isinstance(profile, dict) else None
    name = primary.strip() if isinstance(primary, str) and primary.strip() else "unclassified"
    return (summary.get("by_profile") or {}).get(name) or {
        "coverage": None,
        "threshold": summary.get("threshold", 0.95),
        "gate_active": False,
    }


def cmd_artifact_init(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    if args.redaction_status not in ARTIFACT_REDACTION_STATUSES:
        print("ERROR: invalid --redaction-status", file=sys.stderr)
        sys.exit(2)
    now = iso_now()
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        sid = data.get("session_id") or resolve_session_id()
        artifact_path = _artifact_path(cwd, sid)
        artifact = {
            "status": "draft",
            "format": args.format,
            "title": args.title or data.get("mission") or "Mission Artifact",
            "path": _state_relative_path(cwd, str(artifact_path)),
            "exports": [],
            "publish_events": [],
            "redaction_status": args.redaction_status,
            "required_for_pass": bool(args.required_for_pass),
            "blocks": [],
            "created_at": now,
            "updated_at": now,
        }
        data["artifact"] = artifact
        data["artifact_applicability"] = "producing"
        data["updated_at"] = now
        path = _write_artifact(cwd, data, artifact)
        _refresh_artifact_identity(cwd, data, artifact, path)
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "artifact": artifact}, indent=2 if args.json else None, ensure_ascii=False))


def cmd_artifact_append(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    section = _validate_artifact_section(args.section)
    content, source = _read_artifact_input(args)
    now = iso_now()
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        artifact = _require_artifact(data)
        block = {
            "section": section,
            "content": content.rstrip(),
            "timestamp": now,
        }
        if source:
            block["source"] = source
        if args.label:
            block["label"] = args.label
        artifact.setdefault("blocks", []).append(block)
        artifact["status"] = "draft"
        artifact.pop("digest", None)
        artifact.pop("size", None)
        artifact["updated_at"] = now
        invalidate_artifact_lint_observation(data)
        data["artifact"] = artifact
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "section": section, "block": block}, indent=2 if args.json else None, ensure_ascii=False))


def cmd_artifact_render(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    now = iso_now()
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        artifact = _require_artifact(data)
        if args.redaction_status:
            if args.redaction_status not in ARTIFACT_REDACTION_STATUSES:
                print("ERROR: invalid --redaction-status", file=sys.stderr)
                sys.exit(2)
            artifact["redaction_status"] = args.redaction_status
        artifact["status"] = "rendered"
        artifact["last_rendered_at"] = now
        artifact["updated_at"] = now
        data["artifact"] = artifact
        data["updated_at"] = now
        path = _write_artifact(cwd, data, artifact)
        _refresh_artifact_identity(cwd, data, artifact, path)
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    result = {"ok": True, "path": _state_relative_path(cwd, str(path)), "artifact": artifact}
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))


def cmd_artifact_export(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    if args.redaction_status not in ARTIFACT_REDACTION_STATUSES - {"unchecked"}:
        print("ERROR: export requires --redaction-status checked|reviewed|not-needed", file=sys.stderr)
        sys.exit(2)
    now = iso_now()
    dst = _resolve_project_output_path(cwd, args.to)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        artifact = _require_artifact(data)
        artifact["redaction_status"] = args.redaction_status
        artifact["status"] = "exported"
        artifact["last_rendered_at"] = now
        artifact["updated_at"] = now
        src = _write_artifact(cwd, data, artifact)
        _refresh_artifact_identity(cwd, data, artifact, src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        export_entry = {
            "path": _state_relative_path(cwd, str(dst)),
            "timestamp": now,
            "redaction_status": args.redaction_status,
        }
        artifact.setdefault("exports", []).append(export_entry)
        data["artifact"] = artifact
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    result = {"ok": True, "export": export_entry, "artifact": artifact}
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))


def cmd_artifact_publish(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    if args.provider not in ARTIFACT_PUBLISH_PROVIDERS:
        print("ERROR: unsupported artifact publish provider", file=sys.stderr)
        sys.exit(2)
    if not args.require_confirm or not args.approval_text:
        print(
            "ERROR: artifact publish requires --require-confirm and --approval-text. "
            "This command records publish consent; it does not silently publish remotely.",
            file=sys.stderr,
        )
        sys.exit(2)
    now = iso_now()
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        artifact = _require_artifact(data)
        if artifact.get("redaction_status") == "unchecked":
            print("ERROR: publish requires redaction_status other than unchecked", file=sys.stderr)
            sys.exit(2)
        event = {
            "provider": args.provider,
            "timestamp": now,
            "approval_text": args.approval_text,
            "status": "published" if args.destination else "publish-prepared",
        }
        if args.destination:
            event["destination"] = args.destination
        artifact.setdefault("publish_events", []).append(event)
        artifact["status"] = event["status"]
        artifact["updated_at"] = now
        path = _write_artifact(cwd, data, artifact)
        _refresh_artifact_identity(cwd, data, artifact, path)
        event["artifact_path"] = _state_relative_path(cwd, str(path))
        data["artifact"] = artifact
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    result = {"ok": True, "publish_event": event, "artifact": artifact}
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))


def _archive_progress(cwd: Path, data: dict, progress: dict, iteration: int) -> str:
    archive_dir = state_dir(cwd) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    gid = (data.get("mission_id") or "unknown")[:8]
    dst = archive_dir / f"iter-{iteration}-{gid}-progress.md"
    lines = [
        f"<!-- mission-progress-meta: session_id={data.get('session_id')} mission_id={data.get('mission_id')} iteration={iteration} updated_at={progress.get('updated_at')} -->",
        "",
        "# Mission Progress Checkpoint",
        "",
        f"- kind: {progress.get('kind')}",
        f"- total: {progress.get('total')}",
        f"- completed: {progress.get('completed')}",
        f"- remaining: {progress.get('remaining')}",
        f"- batch_size: {progress.get('batch_size')}",
        f"- last_unit: {progress.get('last_unit') or ''}",
        f"- artifact_path: {progress.get('artifact_path') or ''}",
        "",
    ]
    dst.write_text("\n".join(lines), encoding="utf-8")
    return str(dst)


def cmd_progress_update(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    total = args.total
    completed = args.completed
    if total < 0 or completed < 0 or completed > total:
        print("ERROR: --total/--completed must satisfy 0 <= completed <= total", file=sys.stderr)
        sys.exit(2)
    now = iso_now()
    progress = {
        "kind": args.kind,
        "total": total,
        "completed": completed,
        "remaining": total - completed,
        "batch_size": args.batch_size,
        "last_unit": args.last_unit,
        "artifact_path": args.artifact,
        "updated_at": now,
    }
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        iteration = int(args.iteration if args.iteration is not None else data.get("iteration", 0))
        archived_to = _archive_progress(cwd, data, progress, iteration)
        progress["evidence_path"] = _state_relative_path(cwd, archived_to)
        data["progress"] = progress
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "progress": progress}, indent=2 if args.json else None, ensure_ascii=False))


def cmd_progress_get(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    data = json.loads(sf.read_text())
    progress = data.get("progress") or {}
    if args.json:
        print(json.dumps({"ok": True, "progress": progress}, indent=2, ensure_ascii=False))
    elif progress:
        print(f"progress {progress.get('kind')}: {progress.get('completed')}/{progress.get('total')} remaining={progress.get('remaining')}")
    else:
        print("progress: none")


def cmd_progress_clear(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        data.pop("progress", None)
        data["updated_at"] = iso_now()
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True}, indent=2 if args.json else None, ensure_ascii=False))


def cmd_log_specialist_invocation(args):
    """specialist の実呼び出し/inline/skip/unavailable 証跡を append する."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    if not 0 <= args.iteration <= 1_000_000:
        print("ERROR: --iteration は 0..1000000 で指定してください", file=sys.stderr)
        sys.exit(2)
    role = (args.role or "").strip()
    skill = (args.skill or "").strip()
    if not role:
        print("ERROR: --role は空にできません", file=sys.stderr)
        sys.exit(2)
    if not skill:
        print("ERROR: --skill は空にできません", file=sys.stderr)
        sys.exit(2)
    reason = (getattr(args, "reason", None) or "").strip()
    notes = (getattr(args, "notes", None) or "").strip()
    if args.status in SPECIALIST_INVOCATION_REASON_REQUIRED_STATUSES and not (reason or notes):
        print(
            f"ERROR: status={args.status} は --reason か --notes で判断理由を記録してください",
            file=sys.stderr,
        )
        sys.exit(2)

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        _validate_specialist_public_state(data)
        provider = _provider_for_skill(data, skill)
        if _confirmed_selection_required(data, skill, args.status) and not getattr(args, "selection_source", None):
            print(
                "ERROR: specialists_decision requested user confirmation; pass --selection-source confirmed-user "
                "when recording applied specialist evidence after confirmation.",
                file=sys.stderr,
            )
            sys.exit(2)
        if args.status in APPLIED_SPECIALIST_INVOCATION_STATUSES and _is_provider_backed_application(
            data, skill, args, provider
        ):
            _require_current_provider_application(
                data,
                provider,
                requested_phase=args.phase,
                requested_iteration=args.iteration,
                application_kind="result-import",
                selection_source=getattr(args, "selection_source", None),
                invocation_id=getattr(args, "invocation_id", None),
                cwd=cwd,
                registry_args=args,
            )
        _reject_unbounded_orchestrator_execution(data, skill, args.phase)
        if _bounded_purpose_required(data, skill, args.phase, args.status) and not getattr(args, "bounded_purpose", None):
            print(
                f"ERROR: bounded orchestrator specialist requires --bounded-purpose for applied evidence: {skill}",
                file=sys.stderr,
            )
            sys.exit(2)
        now = iso_now()
        invocations = data.setdefault("specialist_invocations", [])
        requested_id = getattr(args, "invocation_id", None)
        existing_index = None
        existing_entry = None
        if requested_id:
            matches = [(index, item) for index, item in enumerate(invocations)
                       if isinstance(item, dict) and item.get("invocation_id") == requested_id]
            if len(matches) != 1:
                print("ERROR: --invocation-id must identify exactly one invocation", file=sys.stderr)
                sys.exit(2)
            existing_index, existing_entry = matches[0]
        entry = {
            **(existing_entry or {}),
            "invocation_id": requested_id or new_invocation_id(),
            "iteration": args.iteration,
            "phase": args.phase,
            "role": role,
            "skill": skill,
            "mode": args.mode,
            "status": args.status,
            "lifecycle_state": invocation_lifecycle_state(args.status),
            "timestamp": (existing_entry or {}).get("timestamp") or now,
            "transitioned_at": now,
        }
        selection_id = _current_selection_id(data)
        if selection_id:
            entry["selection_id"] = selection_id
        if args.started_at:
            entry["started_at"] = args.started_at
        if args.completed_at:
            entry["completed_at"] = args.completed_at
        if notes:
            entry["notes"] = notes
        if reason:
            entry["reason"] = reason
        elif args.status in SPECIALIST_INVOCATION_REASON_REQUIRED_STATUSES and notes:
            entry["reason"] = notes
        if getattr(args, "selection_source", None):
            entry["selection_source"] = args.selection_source
        if getattr(args, "bounded_purpose", None):
            entry["bounded_purpose"] = args.bounded_purpose

        try:
            validate_invocation_record(entry)
            if existing_entry is not None:
                validate_invocation_transition(existing_entry, entry)
        except SpecialistLifecycleError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)

        evidence_src = Path(args.evidence_output) if args.evidence_output else None
        evidence_planned = evidence_src is not None
        data = stamp_metadata(data, cwd)
        data["updated_at"] = now
        if existing_entry is None:
            data, entry, selected_entry = _prepare_specialist_invocation_state(
                data, entry, cwd=cwd, iteration=args.iteration,
                evidence_planned=evidence_planned,
            )
        else:
            selected_entry = None
            invocations[existing_index] = entry
            _validate_specialist_public_state(data)
        evidence_text = None
        if evidence_planned and evidence_src is not None:
            evidence_text = _read_specialist_evidence_input(evidence_src)
        archived_to = _commit_specialist_state_with_archive(
            sf, cwd, data, entry, args.iteration, evidence_text
        )

    result = {"ok": True, "entry": entry}
    if selected_entry:
        result["selected_entry"] = selected_entry
    if archived_to:
        result["archived_to"] = entry["evidence_path"]
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))



def cmd_init(args):
    cwd = Path.cwd()
    goal_dispatch = _resolve_goal_dispatch(
        args.mission,
        getattr(args, "goal_dispatch", None),
        cwd,
    )
    try:
        mission_state_root = _ensure_regular_directory_path(cwd, (".mission-state",))
        mission_state_root.mkdir(parents=True, exist_ok=True)
    except (OSError, WorktreeArchiveError):
        _exit_init_write_failure(cwd)
    planned_files = _parse_files_arg(getattr(args, "files", None))
    now = iso_now()
    try:
        host_run_id = correlation_id(getattr(args, "host_run_id", None))
        root_run_id = correlation_id(getattr(args, "root_run_id", None) or host_run_id)
        parent_run_id = correlation_id(args.parent_run_id) if getattr(args, "parent_run_id", None) else None
        child_run_id = correlation_id(args.child_run_id) if getattr(args, "child_run_id", None) else None
        logical_group_id = opaque_token(args.logical_group_id) if getattr(args, "logical_group_id", None) is not None else None
        review_group_id = opaque_token(args.review_group_id) if getattr(args, "review_group_id", None) is not None else None
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)

    initial = {
        "mission": args.mission,
        "mission_id": mission_id(args.mission),
        "host_run_id": host_run_id,
        "root_run_id": root_run_id,
        "parent_run_id": parent_run_id,
        "child_run_id": child_run_id,
        "logical_group_id": logical_group_id,
        "review_group_id": review_group_id,
        "review_generation": 1 if review_group_id else None,
        "review_perspective": getattr(args, "review_perspective", None),
        "base_sha": getattr(args, "base_sha", None),
        "head_sha": getattr(args, "head_sha", None),
        "supersedes": [],
        "goal_dispatch_requested": goal_dispatch["mode"],
        "goal_dispatch_source": goal_dispatch["source"],
        **(
            {"goal_dispatch_resolution_fallback_reason": goal_dispatch["fallback_reason"]}
            if goal_dispatch.get("fallback_reason")
            else {}
        ),
        "session_role": getattr(args, "session_role", None) or "implementer",  # #311
        **({"force_mission": True} if getattr(args, "force_mission", False) else {}),  # #325
        "subtasks": [],
        "complexity": "Unknown",
        "reviewer_count": 2,
        "task_profile": {},
        "artifact_applicability": getattr(args, "artifact_applicability", "pending"),
        "specialists_mode": "auto",
        "specialists_candidates": [],
        "specialists_selected": [],
        "specialists_unavailable": [],
        "specialists_decision": _new_specialist_selection_checkpoint(),
        "specialist_invocations": [],
        # New sessions opt into the explicit provider-planning lifecycle.  A
        # same-mission init below preserves absence for legacy sessions.
        "planning_policy_version": 1,
        # M-audit-2 (2026-06-11): 未指定は 3 (98 セッション実測で iter>3 の ROI 低下)。
        # 0 は「上限なし (stagnation 停止モード)」として None を保持する。
        "max_iter": (DEFAULT_MAX_ITER if args.max_iter is None else (None if args.max_iter == 0 else args.max_iter)),
        # #238 (S6): 時間予算 (分)。mission-state.py が自力計測できる唯一の予算軸。
        # None = 予算宣言なし。next が budget_pressure を導出する。
        "budget_minutes": _validated_budget_minutes(getattr(args, "budget_minutes", None)),
        "threshold": args.threshold,
        "iteration": 0,
        "phase": "planning",
        "score_history": [],
        "stagnation_count": 0,
        "decisions": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": now,
        "updated_at": now,
        "phase_started_at": now,
        "phase_durations_sec": {},
        "activity_current": None,
        "activity_segments": [],
        "activity_rollup": {
            "observed_total_sec": 0.0,
            "closed_segment_count": 0,
            "activity_duration_totals_sec": {},
            "phase_activity_duration_totals_sec": {},
            "wait_reason_totals_sec": {},
        },
        "activity_unobserved_gap_sec": 0.0,
        "activity_unobserved_gap_reasons_sec": {},
        # S3: issue_ref (未指定 None)。issue_ref_key は #295 の比較用正規化キー。
        "issue_ref": getattr(args, "issue_ref", None),
        "issue_ref_key": _normalize_issue_ref(getattr(args, "issue_ref", None)),
        # S3-files: 同一 project の file-set overlap WARN 用 (未指定は空 list)
        "planned_files": planned_files,
    }
    start_phase_default_activity(initial, now)
    # S3: 同プロジェクト内の active session で同一 issue_ref があれば WARN (reject しない)
    # #295: 形式差 (裸番号 / #番号 / host:owner/repo#番号 / URL) を正規化キーで同一視する
    _issue_ref = getattr(args, "issue_ref", None)
    _issue_ref_key = _normalize_issue_ref(_issue_ref)
    _cur_sid = resolve_session_id()
    if _issue_ref_key:
        for sf_other in _iter_state_files(cwd):
            try:
                other = json.loads(sf_other.read_text())
            except Exception:
                continue
            # 同一セッションの resume では自分自身の旧 state を誤検出しないよう sid 除外
            if other.get("session_id") == _cur_sid:
                continue
            # 旧 state に issue_ref_key が無い場合は生値から正規化 (後方互換)
            _other_key = other.get("issue_ref_key") or _normalize_issue_ref(other.get("issue_ref"))
            if _other_key != _issue_ref_key:
                continue
            # #296: 正常完了 (passes=True) は重複リスクなし。active に限らず halt 中の
            # 未完了 session も対象にする (near-miss は halt 中の session を見逃して発生した)。
            if other.get("passes") is True:
                continue
            if other.get("loop_active"):
                _state_label = "active"
            else:
                # halt / 非稼働。stale 閾値超は引き継ぎ可能な放棄 claim として注記する。
                _age = _state_age_since_update_sec(other)
                _stale = _age is not None and _age >= _stale_active_seconds()
                _state_label = "halted/stale" if _stale else "halted"
            _hint = " stale の場合は claim を引き継げます。" if "stale" in _state_label else ""
            print(
                f"WARNING [S3]: issue_ref='{_issue_ref}' を持つ未完了 session が既に存在します"
                f" (session_id={other.get('session_id', '?')}, 状態={_state_label})。"
                f"重複作業の可能性を確認してください。{_hint}",
                file=sys.stderr,
            )
            break  # 1件見つかれば十分
    _warn_s3_file_overlap(cwd, planned_files, _cur_sid)
    # M7 (2026-06-10): complexity を init 時に指定可能に。未指定は WARN (後方互換で Unknown 維持)
    if getattr(args, "complexity", None):
        initial["complexity"] = args.complexity
        initial["reviewer_count"] = COMPLEXITY_REVIEWER_COUNT[args.complexity]
    else:
        print(
            "WARNING: --complexity 未指定のため 'Unknown' のままです。"
            " Phase 1 判定後に `mission-state.py set complexity=<Simple|Standard|Complex|Critical> reviewer_count=<N>` で必ず更新してください。",
            file=sys.stderr,
        )
    # Issue #168: review_tier の導出・保存
    _user_tier = getattr(args, "review_tier", None)
    if _user_tier:
        # ユーザー明示指定
        initial["review_tier"] = _user_tier
        initial["review_tier_source"] = "user"
        initial["review_tier_signals"] = []
        initial["review_tier_signal_details"] = []
    else:
        # auto 導出: mission 記述と complexity、task_profile の risk を使用
        # (init 時点では task_profile は空 dict のため risk は参照しない)
        _auto_decision = derive_review_tier_decision(
            args.mission,
            initial.get("complexity"),
        )
        initial["review_tier"] = _auto_decision["tier"]
        initial["review_tier_source"] = "auto"
        initial["review_tier_signals"] = _auto_decision["signals"]
        initial["review_tier_signal_details"] = _auto_decision["signal_details"]
    # reviewer_count は review_tier から設定 (COMPLEXITY_REVIEWER_COUNT と同値になる設計)
    initial["reviewer_count"] = TIER_REVIEWER_COUNT[initial["review_tier"]]
    _pregate = _pregate_state_reference(cwd, getattr(args, "issue_ref", None))
    if _pregate is not None:
        initial["pregate"] = _pregate
        _pregate_warning = _pregate_verdict_warning(_pregate)
        if _pregate_warning:
            print(_pregate_warning, file=sys.stderr)

    # #276: adaptive routing — Simple + リスクシグナルなし + 強制なしは goal へ。
    # discriminating-v2 (品質同点・mission 5.4x 時間/4.9x コスト) と実運用 95% の
    # iter1 素通しに基づく。session state を作らないため pass-score 統計を汚さず、
    # mission の pass も主張しない。シグナル付き Simple は安全側で mission 維持。
    # #304: --issue-ref 付き (Issue-bound = 統治要求) は routing 対象外。company-os 等の
    # wrapper は init 直後の strict preflight で active state を要求するため、
    # routed (state 不生成) だと mandatory halt の事故経路になる。
    if (
        initial.get("complexity") == "Simple"
        and not getattr(args, "force_mission", False)
        and not _user_tier
        and not initial.get("review_tier_signals")
        and not getattr(args, "issue_ref", None)
    ):
        dispatch_fields = _goal_dispatch_route_fields(initial)
        print(json.dumps({
            "route": "goal",
            "complexity": "Simple",
            "mission_id": initial["mission_id"],
            "reason": "Simple complexity with no irreversible/security signals (#276)",
            "guidance": _goal_dispatch_guidance(dispatch_fields, "mission ループを起動しない。"),
            **dispatch_fields,
        }, ensure_ascii=False, indent=2))
        return

    initial = stamp_metadata(initial, cwd)

    # multi-session 完全統一 (2026-06-13): 常に sessions/<sid>.json に書く。
    # 各セッションは独立 sid を持つため奪い合いは起きない (同一 sid 再 init は本人の上書き=resume)。
    sid = initial["session_id"]
    initial["assumptions_path"] = f".mission-state/sessions/{sid}-assumptions.md"
    sdir = session_dir(cwd)
    sf_target = session_file(cwd, sid)
    try:
        _ensure_regular_directory_path(cwd, (".mission-state", "sessions"))
        sdir.mkdir(parents=True, exist_ok=True)
    except (OSError, WorktreeArchiveError):
        _exit_init_write_failure(cwd, sf_target)
    agg = aggregate_file(cwd)
    with _guarded_init_state_lock(cwd, sf_target):
        existing_agg = {}
        if agg.exists():
            try:
                existing_agg = json.loads(agg.read_text())
            except json.JSONDecodeError:
                existing_agg = {}  # F-6: 壊れた aggregate は空扱いで復旧 (init を落とさない)
        # Issue #2: 既存 sf_target が別 mission_id を持つ場合、上書き前に archive に退避する。
        # 同一 mission_id (= resume) の場合は退避不要。
        if sf_target.exists():
            existing_mid = ""
            try:
                existing_data = json.loads(sf_target.read_text())
                _validate_specialist_public_state(existing_data)
                existing_mid = existing_data.get("mission_id", "")
                new_mid = initial.get("mission_id", "")
                if existing_mid and new_mid and existing_mid != new_mid:
                    try:
                        archive_dir = _ensure_regular_directory_path(
                            cwd, (".mission-state", "archive")
                        )
                        archive_dir.mkdir(parents=True, exist_ok=True)
                    except (OSError, WorktreeArchiveError) as e:
                        print(f"ERROR: archive destination is unsafe: {e}", file=sys.stderr)
                        sys.exit(2)
                    old_mid8 = existing_mid[:8] if len(existing_mid) >= 8 else existing_mid
                    archive_dest = archive_dir / f"state-{sid}-{old_mid8}.json"
                    try:
                        atomic_write_bytes(archive_dest, sf_target.read_bytes())
                    except OSError:
                        _exit_init_evidence_write_failure("archive")
                    old_assumptions_path = existing_data.get("assumptions_path")
                    if old_assumptions_path:
                        try:
                            old_assumptions = _validated_assumptions_probe_path(
                                cwd, str(old_assumptions_path)
                            )
                        except FileNotFoundError:
                            old_assumptions = None
                        except (OSError, ValueError) as e:
                            print(
                                f"ERROR: 旧ミッション assumptions の退避対象が不正です: {e}",
                                file=sys.stderr,
                            )
                            sys.exit(2)
                        if old_assumptions is not None:
                            assumptions_archive = archive_dir / (
                                f"state-{sid}-{old_mid8}-assumptions.md"
                            )
                            try:
                                atomic_write_bytes(
                                    assumptions_archive, old_assumptions.read_bytes()
                                )
                            except OSError:
                                _exit_init_evidence_write_failure("archive")
                    initial["assumptions_path"] = (
                        f".mission-state/sessions/{sid}-{new_mid[:8]}-"
                        f"{time.time_ns()}-assumptions.md"
                    )
                elif existing_mid and existing_mid == new_mid:
                    if "planning_policy_version" not in existing_data:
                        initial.pop("planning_policy_version", None)
                    else:
                        initial["planning_policy_version"] = existing_data["planning_policy_version"]
                    existing_assumptions_path = existing_data.get("assumptions_path")
                    if existing_assumptions_path:
                        initial["assumptions_path"] = existing_assumptions_path
                    # #211: same-mission init is a resume boundary. Preserve the
                    # bounded activity rollup and close an open segment only up
                    # to the last observed state update; never infer the crash gap.
                    current = existing_data.get("activity_current")
                    if not (
                        isinstance(current, dict) and current.get("started_at") == now
                    ):
                        close_activity_for_resume(existing_data, now)
                    _resume_phase_timing(existing_data, now)
                    for key in (
                        "activity_current",
                        "activity_segments",
                        "activity_rollup",
                        "activity_unobserved_gap_sec",
                        "activity_unobserved_gap_reasons_sec",
                        "activity_anomaly_counts",
                        "phase_durations_sec",
                        "phase",
                        "phase_started_at",
                        "pregate",
                    ):
                        if key in existing_data:
                            initial[key] = existing_data[key]
                    # resume 後も、より新しい pregate 評価があればそちらを優先する
                    if _pregate is not None:
                        initial["pregate"] = _pregate
                    if initial.get("loop_active") is not False and not initial.get("activity_current"):
                        start_phase_default_activity(initial, now)
            except ActivityTimingError as e:
                print(f"ERROR: existing mission timing is invalid: {e}", file=sys.stderr)
                sys.exit(2)
            except SpecialistPublicContractError:
                raise
            except json.JSONDecodeError as e:
                quarantine_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                quarantine = sf_target.with_name(f"{sf_target.name}.corrupt-{quarantine_suffix}")
                try:
                    shutil.move(str(sf_target), str(quarantine))
                    print(
                        f"WARNING: 破損した session JSON を退避しました: {quarantine} ({e})",
                        file=sys.stderr,
                    )
                except Exception as move_error:
                    print(
                        f"WARNING: 破損した session JSON の退避に失敗しました。上書きで復旧します: {move_error}",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(f"WARNING: 旧ミッション (id={existing_mid[:8]}) のアーカイブに失敗。履歴消失の可能性: {e}", file=sys.stderr)
        assumptions_file = cwd / initial["assumptions_path"]
        try:
            if assumptions_file.exists():
                _validated_assumptions_probe_path(
                    cwd, str(initial["assumptions_path"])
                )
            else:
                atomic_write_text(assumptions_file, "# Assumption Registry\n")
        except (OSError, ValueError):
            _exit_init_evidence_write_failure("assumptions")
        # Allocate generations under the same project lock as publication.  A
        # pre-lock max+1 scan lets concurrent sessions choose one generation.
        if initial["review_group_id"]:
            prior_generations = []
            for state_path in _iter_state_files(cwd):
                try:
                    prior = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if prior.get("review_group_id") != initial["review_group_id"]:
                    continue
                generation = prior.get("review_generation")
                if isinstance(generation, int) and not isinstance(generation, bool) and generation > 0:
                    prior_generations.append(generation)
            initial["review_generation"] = max(prior_generations, default=0) + 1
        backup_state(sf_target)
        atomic_write_json(sf_target, initial)
        existing_agg.setdefault("active_sessions", [])
        if sid not in existing_agg["active_sessions"]:
            existing_agg["active_sessions"].append(sid)
        existing_agg["updated_at"] = iso_now()
        atomic_write_json(agg, existing_agg)
    permission_preflight = _permission_preflight(cwd)
    if not permission_preflight["ok"]:
        print(json.dumps(permission_preflight, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps({
        "ok": True,
        "mode": "multi-session",
        "session_file": str(sf_target),
        "session_id": sid,
        "mission_id": initial["mission_id"],
        "lease_id": initial["lease_id"],
        "fencing_epoch": initial["fencing_epoch"],
        "lease_expires_at": initial["lease_expires_at"],
        "permission_preflight": "passed",
    }))


def cmd_pregate(args):
    cwd = Path.cwd()
    if args.pregate_cmd == "record":
        try:
            evaluation = load_handoff_payload(args.input)
            result = record_pregate_cache(cwd, evaluation, issue_ref=args.issue_ref)
        except (PregateCacheError, EvidenceHandoffError, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.pregate_cmd == "digest":
        try:
            payload = load_handoff_payload(args.input)
            result = {"subject_digest": subject_digest_pregate_cache(payload)}
        except (PregateCacheError, EvidenceHandoffError, OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.pregate_cmd == "check":
        try:
            result = lookup_pregate_cache(cwd, args.issue_ref, args.subject_digest)
        except Exception:
            result = {"status": "miss"}
        output = result if result.get("status") == "hit" else {"status": result.get("status", "miss")}
        print(json.dumps(output, indent=2 if getattr(args, "json", False) else None, ensure_ascii=False))
        return
    raise AssertionError(f"unsupported pregate command: {args.pregate_cmd}")


def _resolve_queue_enqueue_shas(cwd: Path, args) -> tuple[str, str]:
    if not getattr(args, "from_state", False):
        if args.head_sha is None or args.base_sha is None:
            raise MergeQueueError("merge queue enqueue requires either --from-state or manual --head-sha/--base-sha")
        return args.base_sha, args.head_sha

    state_path = resolve_state_file(cwd)
    if not state_path.exists():
        raise MergeQueueError(
            "merge queue --from-state requires a current session state file; use manual --head-sha/--base-sha fallback"
        )
    try:
        state = json.loads(state_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeQueueError(
            "merge queue --from-state requires a readable JSON session state; use manual --head-sha/--base-sha fallback"
        ) from exc
    derived_base_sha, derived_head_sha = derive_queue_revision_scope_shas(state)
    if args.base_sha is not None and args.base_sha != derived_base_sha:
        raise MergeQueueError(
            "merge queue --from-state sha mismatch: --base-sha does not match the latest score_history revision_scope; use manual --head-sha/--base-sha fallback"
        )
    if args.head_sha is not None and args.head_sha != derived_head_sha:
        raise MergeQueueError(
            "merge queue --from-state sha mismatch: --head-sha does not match the latest score_history revision_scope; use manual --head-sha/--base-sha fallback"
        )
    return derived_base_sha, derived_head_sha


def cmd_queue(args):
    cwd = Path.cwd()
    try:
        if args.queue_cmd == "enqueue":
            depends_on = []
            if args.depends_on:
                depends_on = [item for item in (part.strip() for part in args.depends_on.split(",")) if item]
            base_sha, head_sha = _resolve_queue_enqueue_shas(cwd, args)
            result = enqueue_merge_queue(
                cwd,
                issue_ref=args.issue_ref,
                pr_ref=args.pr_ref,
                head_sha=head_sha,
                base_sha=base_sha,
                depends_on=depends_on,
                session_id=args.session,
            )
        elif args.queue_cmd == "status":
            result = status_merge_queue(cwd)
        elif args.queue_cmd == "next":
            result = next_merge_queue_candidate(cwd)
        elif args.queue_cmd == "verify":
            result = verify_merge_queue(cwd, queue_id=args.queue_id, current_base_sha=args.current_base_sha)
        elif args.queue_cmd == "mark":
            result = mark_merge_queue(cwd, queue_id=args.queue_id, status_value=args.status, reason=args.reason)
        else:
            raise AssertionError(f"unsupported queue command: {args.queue_cmd}")
    except BaseMismatchError:
        print("ERROR: base changed; refreeze required", file=sys.stderr)
        print("HINT: base 統合 → refreeze（--head-sha を更新して再 enqueue）→ fresh review", file=sys.stderr)
        sys.exit(2)
    except MergeQueueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if "use manual --head-sha/--base-sha fallback" in str(exc):
            print(
                "HINT: 正しい呼び出し例: mission-state.py queue enqueue --issue-ref <ref> --pr-ref <ref> --head-sha <40hex> --base-sha <40hex>",
                file=sys.stderr,
            )
        sys.exit(2)
    print(json.dumps(result, indent=2 if getattr(args, "json", False) else None, ensure_ascii=False))


PARALLEL_GROUP_SCHEMA = "mission-parallel-group/1"
PARALLEL_GROUP_MAX_BYTES = 256 * 1024
STOP_GUARD_SCHEMA = "mission-stop-guard/1"
STOP_GUARD_MAX_BYTES = 64 * 1024


def _parallel_file_identity(metadata: os.stat_result) -> tuple:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _parallel_manifest_path(cwd: Path, group_id: str) -> Path:
    return session_dir(cwd) / f"{group_id}.group.json"


def _parallel_directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _parallel_same_directory(opened: os.stat_result, named: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(opened.st_mode)
        and stat.S_ISDIR(named.st_mode)
        and opened.st_dev == named.st_dev
        and opened.st_ino == named.st_ino
        and opened.st_mode == named.st_mode
    )


def _open_parallel_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        child_fd = os.open(name, _parallel_directory_flags(), dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise ValueError("parallel group directory is missing")
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            child_fd = os.open(name, _parallel_directory_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise ValueError("parallel group directory is unsafe") from exc
    except OSError as exc:
        raise ValueError("parallel group directory is unsafe") from exc
    try:
        opened = os.fstat(child_fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _parallel_same_directory(opened, named):
            raise ValueError("parallel group directory changed")
        return child_fd
    except BaseException:
        os.close(child_fd)
        raise


class _ParallelGroupStore:
    """Hold the project/state/sessions descriptor chain and shared state lock."""

    def __init__(self, cwd: Path, *, create: bool):
        self.cwd = cwd
        self.create = create
        self.root_fd = None
        self.state_fd = None
        self.sessions_fd = None
        self.lock_fd = None

    def __enter__(self):
        try:
            self.root_fd = os.open(os.fspath(self.cwd), _parallel_directory_flags())
            self.state_fd = _open_parallel_child_directory(
                self.root_fd, ".mission-state", create=self.create
            )
            lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            self.lock_fd = os.open(".state.lock", lock_flags, 0o600, dir_fd=self.state_fd)
            opened_lock = os.fstat(self.lock_fd)
            named_lock = os.stat(".state.lock", dir_fd=self.state_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened_lock.st_mode)
                or opened_lock.st_nlink != 1
                or _parallel_file_identity(opened_lock) != _parallel_file_identity(named_lock)
            ):
                raise ValueError("parallel group lock is unsafe")
            deadline = time.time() + 5.0
            while True:
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() > deadline:
                        raise ValueError("parallel group lock timed out")
                    time.sleep(0.05)
            self.sessions_fd = _open_parallel_child_directory(
                self.state_fd, "sessions", create=self.create
            )
            self.verify()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def verify(self) -> None:
        if self.root_fd is None or self.state_fd is None or self.sessions_fd is None:
            raise ValueError("parallel group directory is unavailable")
        root_named = self.cwd.lstat()
        if not _parallel_same_directory(os.fstat(self.root_fd), root_named):
            raise ValueError("parallel group project root changed")
        state_named = os.stat(".mission-state", dir_fd=self.root_fd, follow_symlinks=False)
        if not _parallel_same_directory(os.fstat(self.state_fd), state_named):
            raise ValueError("parallel group state directory changed")
        sessions_named = os.stat("sessions", dir_fd=self.state_fd, follow_symlinks=False)
        if not _parallel_same_directory(os.fstat(self.sessions_fd), sessions_named):
            raise ValueError("parallel group sessions directory changed")

    def __exit__(self, exc_type, exc, tb):
        if self.lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
        for attribute in ("sessions_fd", "lock_fd", "state_fd", "root_fd"):
            descriptor = getattr(self, attribute)
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
                setattr(self, attribute, None)


def _read_parallel_regular_at(directory_fd: int, name: str, *, limit: int) -> tuple[bytes, tuple]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size < 1
                or before.st_size > limit
            ):
                raise ValueError("parallel group file is unsafe")
            remaining = before.st_size
            chunks = []
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            identity = _parallel_file_identity(before)
            if (
                len(payload) != before.st_size
                or os.read(fd, 1)
                or _parallel_file_identity(after) != identity
                or _parallel_file_identity(named) != identity
            ):
                raise ValueError("parallel group file changed while being read")
            return payload, identity
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("parallel group file is missing or unsafe") from exc


def _read_parallel_manifest(directory_fd: int, name: str) -> tuple[bytes, tuple]:
    return _read_parallel_regular_at(
        directory_fd, name, limit=PARALLEL_GROUP_MAX_BYTES
    )


def _validate_parallel_manifest(payload: object, group_id: str) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != PARALLEL_GROUP_SCHEMA:
        raise ValueError("parallel group manifest has an invalid schema")
    allowed = {
        "schema",
        "group_id",
        "created_at",
        "planned_children",
        "status",
        "coverage",
        "outcome",
        "closed_at",
    }
    if set(payload) - allowed or payload.get("group_id") != group_id:
        raise ValueError("parallel group manifest is malformed")
    if parse_iso_datetime(payload.get("created_at")) is None:
        raise ValueError("parallel group manifest created_at is invalid")
    planned = payload.get("planned_children")
    if not isinstance(planned, list) or not planned:
        raise ValueError("parallel group manifest planned children are invalid")
    normalized = []
    for item in planned:
        if not isinstance(item, dict) or set(item) != {"issue_ref"}:
            raise ValueError("parallel group manifest child is invalid")
        key = _normalize_issue_ref(item.get("issue_ref"))
        if key is None:
            raise ValueError("parallel group manifest child issue_ref is invalid")
        normalized.append(key)
    if len(set(normalized)) != len(normalized):
        raise ValueError("parallel group manifest child issue_ref values are duplicated")
    status = payload.get("status")
    if status not in {"running", "terminal"} or not isinstance(payload.get("coverage"), dict):
        raise ValueError("parallel group manifest status is invalid")
    if status == "terminal":
        if payload.get("outcome") not in {"pass", "halt"} or parse_iso_datetime(payload.get("closed_at")) is None:
            raise ValueError("parallel group terminal metadata is invalid")
    elif "outcome" in payload or "closed_at" in payload:
        raise ValueError("running parallel group cannot contain terminal metadata")
    return payload


def _reject_duplicate_parallel_keys(pairs: list[tuple[str, object]]) -> dict:
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"parallel group manifest has duplicate JSON key: {key}")
        document[key] = value
    return document


def _parallel_manifest(store: _ParallelGroupStore, group_id: str) -> tuple[Path, dict, tuple]:
    path = _parallel_manifest_path(store.cwd, group_id)
    content, identity = _read_parallel_manifest(store.sessions_fd, path.name)
    try:
        parsed = json.loads(
            content.decode("utf-8"), object_pairs_hook=_reject_duplicate_parallel_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("parallel group manifest is malformed") from exc
    return path, _validate_parallel_manifest(parsed, group_id), identity


def _write_parallel_temp(directory_fd: int, payload: bytes) -> str:
    name = f".parallel-{secrets.token_hex(12)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("parallel group manifest write made no progress")
            view = view[written:]
        os.fsync(fd)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != len(payload):
            raise ValueError("parallel group manifest temporary file is unsafe")
    except BaseException:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=directory_fd)
        raise
    os.close(fd)
    return name


def _create_parallel_manifest(store: _ParallelGroupStore, path: Path, manifest: dict) -> None:
    store.verify()
    directory_fd = store.sessions_fd
    temporary = None
    try:
        parent_before = os.fstat(directory_fd)
        payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary = _write_parallel_temp(directory_fd, payload)
        try:
            os.link(
                temporary,
                path.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ValueError("parallel group manifest already exists") from exc
        os.unlink(temporary, dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
        parent_after = os.fstat(directory_fd)
        if (parent_before.st_dev, parent_before.st_ino) != (parent_after.st_dev, parent_after.st_ino):
            raise ValueError("parallel group manifest directory changed")
        store.verify()
        _parallel_manifest(store, manifest["group_id"])
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=directory_fd)


def _replace_parallel_manifest(
    store: _ParallelGroupStore, path: Path, manifest: dict, expected_identity: tuple
) -> None:
    store.verify()
    directory_fd = store.sessions_fd
    temporary = None
    try:
        current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if _parallel_file_identity(current) != expected_identity:
            raise ValueError("parallel group manifest changed before closeout")
        payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary = _write_parallel_temp(directory_fd, payload)
        current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if _parallel_file_identity(current) != expected_identity:
            raise ValueError("parallel group manifest changed before publish")
        os.replace(temporary, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
        store.verify()
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=directory_fd)


def cmd_parallel_init(args):
    """Create one immutable planned-child manifest before parallel child init."""
    cwd = Path.cwd()
    try:
        group_id = opaque_token(args.group_id)
        refs = list(args.issue_ref or [])
        keys = [_normalize_issue_ref(ref) for ref in refs]
        if not refs or any(key is None for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("planned issue_ref values must be unique")
        manifest = {
            "schema": PARALLEL_GROUP_SCHEMA,
            "group_id": group_id,
            "created_at": iso_now(),
            "planned_children": [{"issue_ref": ref} for ref in refs],
            "status": "running",
            "coverage": {},
        }
        with _ParallelGroupStore(cwd, create=True) as store:
            _create_parallel_manifest(
                store, _parallel_manifest_path(cwd, group_id), manifest
            )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"ok": True, "manifest": str(_parallel_manifest_path(cwd, group_id)), "group_id": group_id}))


def _parallel_artifact_observed(state: dict) -> bool:
    applicability = state.get("artifact_applicability")
    if applicability == "not-applicable":
        return True
    artifact = state.get("artifact")
    return (
        applicability == "producing"
        and isinstance(artifact, dict)
        and isinstance(artifact.get("path"), str)
        and bool(artifact["path"].strip())
    )


def _parallel_activity_observed(state: dict) -> bool:
    segments = state.get("activity_segments")
    if not isinstance(segments, list) or not segments:
        return False
    return any(
        isinstance(segment, dict)
        and segment.get("kind") in ACTIVITY_KINDS
        and isinstance(segment.get("started_at"), str)
        and bool(segment["started_at"].strip())
        for segment in segments
    )


def _parallel_review_provenance_observed(state: dict) -> bool:
    history = state.get("score_history")
    if not isinstance(history, list):
        return False
    for entry in history:
        if not isinstance(entry, dict):
            continue
        try:
            _validate_provenance(entry.get("score_provenance"), require=True)
        except ValueError:
            continue
        return True
    return False


def _parallel_coverage(records: dict[str, list[dict]], planned: list[str]) -> dict:
    eligible = len(planned)
    observed = {"artifact": 0, "activity": 0, "review_provenance": 0}
    for ref in planned:
        candidates = records.get(_normalize_issue_ref(ref), [])
        if len(candidates) != 1:
            continue
        state = candidates[0]
        observed["artifact"] += int(_parallel_artifact_observed(state))
        observed["activity"] += int(_parallel_activity_observed(state))
        observed["review_provenance"] += int(_parallel_review_provenance_observed(state))
    result = {
        key: {
            "observed": value,
            "eligible": eligible,
            "ratio": round(value / eligible, 4) if eligible else 1.0,
        }
        for key, value in observed.items()
    }
    result["ratio"] = (
        round(sum(observed.values()) / (3 * eligible), 4) if eligible else 1.0
    )
    return result


def _parallel_status(store: _ParallelGroupStore, group_id: str) -> tuple[Path, dict, tuple, dict]:
    path, manifest, manifest_identity = _parallel_manifest(store, group_id)
    planned_children = [item["issue_ref"] for item in manifest["planned_children"]]
    states = []
    for name in sorted(os.listdir(store.sessions_fd)):
        if not name.endswith(".json") or name.endswith(".group.json"):
            continue
        try:
            state_content, _identity = _read_parallel_regular_at(
                store.sessions_fd, name, limit=4 * 1024 * 1024
            )
            state = json.loads(state_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("parallel group session state is malformed or unsafe") from exc
        if not _is_mission_state_record(state) or state.get("logical_group_id") != group_id:
            continue
        state["_mission_source_path"] = str(session_dir(store.cwd) / name)
        states.append(state)
    states, _duplicate_files = _dedupe_states(states)
    by_issue: dict[str, list[dict]] = {}
    for state in states:
        key = _normalize_issue_ref(state.get("issue_ref"))
        if key:
            by_issue.setdefault(key, []).append(state)

    categories = {name: [] for name in ("planned", "running", "waiting", "pass", "halt")}
    children = {}
    active_leases = []
    duplicates = []
    now = datetime.now(timezone.utc)
    for ref in planned_children:
        key = _normalize_issue_ref(ref)
        records = by_issue.get(key, [])
        status = "planned"
        session_ids = [str(record.get("session_id") or "") for record in records]
        if len(records) > 1:
            status = "incomplete"
            duplicates.append(ref)
        elif records:
            state = records[0]
            expires = parse_iso_datetime(state.get("lease_expires_at"))
            lease_active = expires is not None and expires > now
            if lease_active:
                active_leases.append(ref)
            if state.get("loop_active") is True:
                status = "running" if lease_active else "waiting"
            elif state.get("passes") is True:
                status = "pass"
            elif isinstance(state.get("halt_reason"), str) and state["halt_reason"].strip():
                status = "halt"
            else:
                status = "incomplete"
        if status in categories:
            categories[status].append(ref)
        children[str(ref)] = {"status": status, "session_ids": session_ids}

    planned_keys = {_normalize_issue_ref(ref) for ref in planned_children}
    late = sorted(
        str(state.get("issue_ref"))
        for key, records in by_issue.items()
        if key not in planned_keys
        for state in records
    )
    incomplete = [
        ref
        for ref in planned_children
        if children[str(ref)]["status"] not in {"pass", "halt"}
    ]
    coverage = _parallel_coverage(by_issue, planned_children)
    status = {
        "group_id": group_id,
        "manifest_status": manifest["status"],
        "planned_children": planned_children,
        "children": children,
        **categories,
        "terminal": categories["pass"] + categories["halt"],
        "incomplete": incomplete,
        "duplicates": duplicates,
        "late_children": late,
        "active_leases": active_leases,
        "coverage": coverage,
    }
    return path, manifest, manifest_identity, status


def cmd_parallel_status(args):
    try:
        group_id = opaque_token(args.group_id)
        with _ParallelGroupStore(Path.cwd(), create=False) as store:
            _path, _manifest, _identity, status = _parallel_status(store, group_id)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(status, ensure_ascii=False))


def cmd_parallel_closeout(args):
    cwd = Path.cwd()
    try:
        group_id = opaque_token(args.group_id)
        with _ParallelGroupStore(cwd, create=False) as store:
            path, manifest, manifest_identity, status = _parallel_status(store, group_id)
            if manifest["status"] != "running":
                raise ValueError("parallel group is already terminal")
            if status["incomplete"] or status["duplicates"] or status["late_children"] or status["active_leases"]:
                raise ValueError("parallel group has incomplete children or active leases")
            manifest["status"] = "terminal"
            manifest["outcome"] = "halt" if status["halt"] else "pass"
            manifest["closed_at"] = iso_now()
            manifest["coverage"] = status["coverage"]
            _replace_parallel_manifest(store, path, manifest, manifest_identity)
            status["manifest_status"] = "terminal"
            status["outcome"] = manifest["outcome"]
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"ok": True, **status}, ensure_ascii=False))



def _stop_guard_state_name(session_id: str) -> str:
    token = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return f".{token}.stop-guard"


def _validate_stop_guard_state(payload: object, session_id: str) -> dict:
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "session_id", "last_digest", "last_detail_epoch",
        "block_count", "reinjection_count", "detail_count", "heartbeat_count",
    }:
        raise ValueError("stop guard state is malformed")
    if payload.get("schema") != STOP_GUARD_SCHEMA or payload.get("session_id") != session_id:
        raise ValueError("stop guard state identity is invalid")
    digest = payload.get("last_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("stop guard state digest is invalid")
    for key in (
        "last_detail_epoch", "block_count", "reinjection_count",
        "detail_count", "heartbeat_count",
    ):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("stop guard counters are invalid")
    return dict(payload)


def _reject_duplicate_stop_guard_keys(pairs: list[tuple[str, object]]) -> dict:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("stop guard state has duplicate keys")
        document[key] = value
    return document


def _read_stop_guard_state(
    store: _ParallelGroupStore, session_id: str,
) -> tuple[dict | None, tuple | None]:
    name = _stop_guard_state_name(session_id)
    try:
        payload, identity = _read_parallel_regular_at(
            store.sessions_fd, name, limit=STOP_GUARD_MAX_BYTES
        )
    except ValueError as exc:
        try:
            os.stat(name, dir_fd=store.sessions_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None, None
        raise ValueError("stop guard state is unsafe") from exc
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_stop_guard_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("stop guard state is malformed") from exc
    return _validate_stop_guard_state(document, session_id), identity


def _write_stop_guard_state(
    store: _ParallelGroupStore,
    session_id: str,
    document: dict,
    expected_identity: tuple | None,
) -> None:
    store.verify()
    name = _stop_guard_state_name(session_id)
    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporary = _write_parallel_temp(store.sessions_fd, payload)
    try:
        if expected_identity is None:
            try:
                os.link(
                    temporary, name,
                    src_dir_fd=store.sessions_fd, dst_dir_fd=store.sessions_fd,
                )
            except FileExistsError as exc:
                raise ValueError("stop guard state appeared during create") from exc
            os.unlink(temporary, dir_fd=store.sessions_fd)
            temporary = ""
        else:
            current = os.stat(name, dir_fd=store.sessions_fd, follow_symlinks=False)
            if _parallel_file_identity(current) != expected_identity:
                raise ValueError("stop guard state changed before publish")
            os.replace(
                temporary, name,
                src_dir_fd=store.sessions_fd, dst_dir_fd=store.sessions_fd,
            )
            temporary = ""
        os.fsync(store.sessions_fd)
        store.verify()
        current, _identity = _read_stop_guard_state(store, session_id)
        if current != document:
            raise ValueError("stop guard state publish verification failed")
    finally:
        if temporary:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=store.sessions_fd)


def cmd_stop_guard_observe(args):
    """Record a block observation without mutating the fenced mission session."""
    try:
        session_id = opaque_token(args.session_id)
        if session_id is None:
            raise ValueError("stop guard session id is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", args.digest):
            raise ValueError("stop guard digest is invalid")
        if args.now_epoch < 0 or args.ttl_seconds < 1:
            raise ValueError("stop guard time input is invalid")
        with _ParallelGroupStore(Path.cwd(), create=False) as store:
            previous, identity = _read_stop_guard_state(store, session_id)
            changed = previous is None or previous["last_digest"] != args.digest
            ttl_elapsed = (
                previous is not None
                and args.now_epoch - previous["last_detail_epoch"] >= args.ttl_seconds
            )
            mode = "detail" if changed or ttl_elapsed else "heartbeat"
            document = {
                "schema": STOP_GUARD_SCHEMA,
                "session_id": session_id,
                "last_digest": args.digest,
                "last_detail_epoch": (
                    args.now_epoch if mode == "detail" else previous["last_detail_epoch"]
                ),
                "block_count": (previous["block_count"] if previous else 0) + 1,
                "reinjection_count": (previous["reinjection_count"] if previous else 0) + 1,
                "detail_count": (previous["detail_count"] if previous else 0) + int(mode == "detail"),
                "heartbeat_count": (previous["heartbeat_count"] if previous else 0) + int(mode == "heartbeat"),
            }
            _write_stop_guard_state(store, session_id, document, identity)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps({"ok": True, "mode": mode, **document}, ensure_ascii=False))


def cmd_get(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print(json.dumps({"ok": False, "error": "state.json not found"}))
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        _validate_specialist_public_state(data)
    if args.field:
        print(json.dumps(data.get(args.field)))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def _activity_state_file(cwd: Path) -> Path:
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    return sf


def cmd_activity_start(args):
    cwd = Path.cwd()
    sf = _activity_state_file(cwd)
    at = args.at or iso_now()
    try:
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
            changed = start_activity_segment(
                data,
                args.kind,
                args.reason,
                at,
                detail=args.detail,
                resume=args.resume,
                origin="manual",
            )
            if changed:
                data["updated_at"] = at
                backup_state(sf)
                atomic_write_json(sf, stamp_metadata(data, cwd))
    except ActivityTimingError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"ok": True, "changed": changed, "activity_current": data.get("activity_current")}, ensure_ascii=False))


def cmd_activity_end(args):
    cwd = Path.cwd()
    sf = _activity_state_file(cwd)
    at = args.at or iso_now()
    try:
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
            changed = end_activity_segment(data, at)
            if changed:
                data["updated_at"] = at
                backup_state(sf)
                atomic_write_json(sf, stamp_metadata(data, cwd))
    except ActivityTimingError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"ok": True, "changed": changed, "activity_current": data.get("activity_current")}, ensure_ascii=False))


def _validated_budget_minutes(raw) -> float | None:
    """#238 (S6): init --budget-minutes の検証。None は「予算宣言なし」。

    有限かつ正の数のみ受理する。不正値は exit 2 (state を作らない)。
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        print(
            f"ERROR: --budget-minutes は正の有限な分数で指定してください。受領値: '{raw}'",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


# #238 (S6): budget pressure の閾値。80% で warn (optional spawn 抑制の advisory)、
# 100% で spawn 系 next_action を consider-halt へ override する。
BUDGET_PRESSURE_WARN_PCT = 80.0
BUDGET_SPAWN_ACTIONS = {"run-planner", "run-executor", "run-reviewers"}


def _budget_pressure(data: dict, now_iso: str) -> dict | None:
    """時間予算に対する消費率を導出する (read-only・ゲート意味論に影響しない)。

    budget_minutes 未宣言・timestamp 不正のときは None (シグナルなし)。
    """
    budget = data.get("budget_minutes")
    if not isinstance(budget, (int, float)) or isinstance(budget, bool):
        return None
    if not math.isfinite(budget) or budget <= 0:
        return None
    started = _parse_iso_datetime(data.get("started_at"))
    now = _parse_iso_datetime(now_iso)
    if not started or not now:
        return None
    # naive/aware 混在の TypeError を防ぐ (_mission_started_at と同じ正規化)。
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now < started:
        return None
    elapsed_minutes = (now - started).total_seconds() / 60.0
    pressure_pct = round(elapsed_minutes / budget * 100.0, 1)
    if pressure_pct >= 100.0:
        level = "exceeded"
    elif pressure_pct >= BUDGET_PRESSURE_WARN_PCT:
        level = "warn"
    else:
        level = "ok"
    return {
        "budget_minutes": budget,
        "elapsed_minutes": round(elapsed_minutes, 1),
        "pressure_pct": pressure_pct,
        "level": level,
    }


def cmd_advance(args):
    """#237 (F2): phase 遷移と activity 切替を 1 lock で atomic に行う。

    `set phase=` と `activity start` が別コマンドだと「phase だけ進んで activity が
    空」の state を作れてしまい、activity coverage の欠損 (strict cohort 9.96%) を
    生む。advance は両方を単一 write で行い、片方だけ進んだ state を機械的に排除する。

    - terminal phase (done/halted) への遷移は mark-passes / mark-halt 専用 (gate 迂回の防止)。
    - --activity は <kind>:<reason>。検証は lock 取得前に行い、不正入力では一切 write しない。
    """
    cwd = Path.cwd()
    sf = _activity_state_file(cwd)
    state_preview = None
    if sf.exists():
        try:
            state_preview = json.loads(sf.read_text())
        except (OSError, json.JSONDecodeError):
            state_preview = None
    new_phase = _normalize_set_phase_value(args.phase)
    if new_phase in {"done", "halted"}:
        _raise_guided_failure(
            "advance で terminal phase へは遷移できません。"
            " 合格は mark-passes、中断は mark-halt を使ってください。",
            command="advance",
            reason="terminal-phase",
            context=_guidance_context_for_state(state_preview, phase=state_preview.get("phase") if isinstance(state_preview, dict) else new_phase),
            outcome_kind="expected-gate",
        )
    raw = args.activity
    if raw is None:
        default = PHASE_ACTIVITY_DEFAULTS.get(new_phase)
        if default is None:
            print(f"ERROR: phase '{new_phase}' has no default activity.", file=sys.stderr)
            sys.exit(2)
        kind, reason = default
    else:
        kind, sep, reason = raw.partition(":")
        if not sep or not kind or not reason:
            _raise_guided_failure(
                f"--activity は <kind>:<reason> 形式で指定してください (例: active:implementation)。"
                f" 受領値: '{raw}'",
                command="advance",
                reason="activity-format",
                context=_guidance_context_for_state(state_preview, phase=new_phase),
                outcome_kind="invalid-input",
            )
    if not kind or not reason:
        _raise_guided_failure(
            f"--activity は <kind>:<reason> 形式で指定してください (例: active:implementation)。"
            f" 受領値: '{raw}'",
            command="advance",
            reason="activity-format",
            context=_guidance_context_for_state(state_preview, phase=new_phase),
            outcome_kind="invalid-input",
        )
    try:
        validate_activity(kind, reason)
    except ActivityTimingError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
    at = args.at or iso_now()
    try:
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
            _reject_active_provider_mutation(data, "advance")
            if (new_phase == "executing" and data.get("phase") != "executing"
                    and data.get("planning_policy_version") == 1):
                plan = data.get("canonical_plan")
                if not isinstance(plan, dict):
                    _raise_guided_failure(
                        "policy v1 requires a canonical plan before executing",
                        command="advance",
                        reason="missing-canonical-plan",
                        context=_guidance_context_for_state(data, phase=data.get("phase"), iteration=data.get("iteration")),
                        outcome_kind="expected-gate",
                    )
                try:
                    expected_binding = _trusted_canonical_plan_binding(data, plan)
                    _raw_plan, step_ids = canonical_plan_identity(
                        cwd, plan, expected=expected_binding, reader=_read_strict_review_file
                    )
                except (OSError, PlanningLifecycleError) as exc:
                    print(f"ERROR: canonical plan gate failed: {exc}", file=sys.stderr)
                    sys.exit(2)
                if data.get("executor_handoff") is not None:
                    print("ERROR: executor handoff already exists; use handoff resume", file=sys.stderr)
                    sys.exit(2)
                data["executor_handoff"] = {
                    "schema": "mission-executor-handoff/1",
                    "handoff_id": "handoff_" + secrets.token_hex(16),
                    "plan_path": plan["path"], "plan_digest": plan["digest"],
                    "plan_generation": plan["generation"], "plan_source": plan["source"],
                    "source_id": plan["source_id"], "selection_source": plan["selection_source"],
                    "iteration": data["iteration"], "step_ids": step_ids, "status": "prepared",
                }
            requested_applicability = getattr(args, "artifact_applicability", None)
            artifact_path = getattr(args, "artifact_path", None)
            producer_run_id = getattr(args, "producer_run_id", None)
            if requested_applicability == "producing":
                if not artifact_path or not producer_run_id:
                    _raise_guided_failure(
                        "producing artifact handoff requires --artifact-path and --producer-run-id",
                        command="advance",
                        reason="producing-artifact",
                        context=_guidance_context_for_state(data, phase=data.get("phase"), iteration=data.get("iteration")),
                        outcome_kind="invalid-input",
                    )
                try:
                    identity, _ = capture_artifact_identity(
                        cwd, artifact_path, producer_run_id, canonical=True
                    )
                except ArtifactContractError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    sys.exit(2)
                data["artifact"] = identity
                data["artifact_applicability"] = "producing"
                invalidate_artifact_lint_observation(data)
            elif requested_applicability == "not-applicable":
                if artifact_path or producer_run_id:
                    print(
                        "ERROR: not-applicable artifact handoff cannot include artifact identity",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                if data.get("artifact_applicability") == "producing":
                    print(
                        "ERROR: cannot downgrade producing artifact applicability to not-applicable",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                data["artifact_applicability"] = "not-applicable"
            elif artifact_path or producer_run_id:
                print(
                    "ERROR: artifact identity requires --artifact-applicability producing",
                    file=sys.stderr,
                )
                sys.exit(2)
            if (
                data.get("phase") == "executing"
                and new_phase == "reviewing"
                and data.get("artifact_applicability") == "pending"
            ):
                print(
                    "ERROR: artifact applicability is pending; resolve it to producing or not-applicable before review",
                    file=sys.stderr,
                )
                sys.exit(2)
            # 現 segment を先に閉じる。_transition_phase の split (旧 kind/reason の
            # キャリーフォワード) が「旧 reason + 新 phase・0秒」の phantom segment を
            # 作るのを防ぐ。advance は直後に新 segment を開くため carry-forward 不要。
            if (
                isinstance(data.get("activity_current"), dict)
                and data.get("phase") != new_phase
            ):
                end_activity_segment(data, at)
            _transition_phase(data, new_phase, at)
            start_activity_segment(
                data,
                kind,
                reason,
                at,
                detail=args.detail,
                origin="phase-default" if raw is None else None,
            )
            data["updated_at"] = at
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(data, cwd))
    except (ActivityTimingError, ArtifactContractError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
    print(
        json.dumps(
            {"ok": True, "phase": data.get("phase"), "activity_current": data.get("activity_current")},
            ensure_ascii=False,
        )
    )


def _happy_path_sequence(
    phase: str,
    reviewer_count: int,
    *,
    plan_mode: str = "subagent",
    adopt_core: bool = False,
) -> list[str]:
    """#339: 現 phase から closeout までの happy-path コマンド列.

    ゲート失敗 (exit 2) がない限り、orchestrator はこの列を `next` の再呼び出し
    なしで連続実行してよい (ターン圧縮)。ゲート失敗時のみ next を再参照する。
    portfolio-v4 実測: mission 19-31 turns vs goal 5 — 毎ターンの context 再処理が
    時間比とトークン比の乖離 (10-15x vs 4x) の主因。
    """
    plan_step = (
        "plan を artifact に記載 (inline #339)"
        if plan_mode == "inline"
        else "Skill: mission-planner"
    )
    steps = [
        plan_step,
        "mission-state.py advance --phase executing --activity active:implementation",
        "Skill: mission-executor",
        "mission-state.py advance --phase reviewing --activity reviewer-wait:review-response",
        f"Skill: mission-reviewer x{reviewer_count} (1 message, parallel)",
        "mission-state.py review-import --iteration <i> --stdin (reviewer ごとに実行し review_evidence_ref.path を保持)",
        f"mission-state.py review-finalize --iteration <i> --input-ref <review_evidence_ref.path> (全 reviewer 分を反復) --min-reviewers {reviewer_count}",
        "mission-state.py closeout",
    ]
    if adopt_core and phase == "planning":
        steps.insert(1, "mission-state.py planning adopt-core --input <plan.json>")
    start = {"planning": 0, "executing": 2, "reviewing": 4}[phase]
    return steps[start:]


def _trusted_canonical_plan_binding(data: dict, plan: dict) -> dict:
    """Resolve plan lineage from state-owned producer evidence, never CLI input."""
    source = plan.get("source")
    source_id = plan.get("source_id")
    if source == "provider":
        imports = data.get("provider_plan_imports") or {}
        record = imports.get(source_id) if isinstance(imports, dict) else None
        if not isinstance(record, dict):
            raise PlanningLifecycleError("canonical-plan-provider-import-missing")
        if record.get("candidate_path") != plan.get("path") or record.get("candidate_digest") != plan.get("digest"):
            raise PlanningLifecycleError("canonical-plan-provider-candidate-mismatch")
        invocation = invocation_by_id(data, str(source_id))
        if invocation.get("iteration") != data.get("iteration") or invocation.get("phase") != "planning":
            raise PlanningLifecycleError("canonical-plan-provider-invocation-mismatch")
        expected = {"generation": record.get("generation"), "source": source, "source_id": source_id,
                    "selection_source": invocation.get("selection_source") or "automatic", "iteration": data.get("iteration")}
        if plan.get("source_digest") != record.get("raw_result_digest"):
            raise PlanningLifecycleError("canonical-plan-provider-source-digest-mismatch")
    else:
        records = data.get("planning_source_records") or {}
        record = records.get(f"{source}:{source_id}") if isinstance(records, dict) else None
        if not isinstance(record, dict):
            raise PlanningLifecycleError("canonical-plan-source-record-missing")
        expected = {key: record.get(key) for key in ("generation", "source", "source_id", "selection_source", "iteration")}
    return expected


def _require_current_primary_planning_binding(data: dict, provider_id: str | None = None) -> dict:
    binding = data.get("planning_provider_binding")
    selected = data.get("specialists_selected") or []
    if not isinstance(binding, dict):
        _provider_gate("planning-primary-binding-missing")
    matches = [item for item in selected if isinstance(item, dict)
               and item.get("planning_mode") == "primary"
               and item.get("provider_id") == binding.get("provider_id")
               and item.get("selection_id") == binding.get("selection_id")
               and item.get("planning_contract_digest") == binding.get("planning_contract_digest")]
    if len(matches) != 1 or (provider_id is not None and matches[0].get("provider_id") != provider_id):
        _provider_gate("planning-primary-binding-mismatch")
    return matches[0]


def _native_review_handoff_hint(
    iteration: int | str,
    reviewer_count: int | str,
    *,
    resubmit: bool = False,
) -> str:
    """Return staged native commands without temp files or shell composition."""
    resubmit_hint = (
        ' --resubmit-reason "retry with review evidence"' if resubmit else ""
    )
    return (
        f"Step 1 (reviewer ごと): mission-state.py review-import --iteration {iteration} "
        "--stdin; 返却 JSON の review_evidence_ref.path を保持する。 "
        f"Step 2: mission-state.py review-finalize --iteration {iteration} "
        "--input-ref <review_evidence_ref.path> (全 reviewer 分だけ --input-ref を反復) "
        f"--min-reviewers {reviewer_count}{resubmit_hint}。 "
        "Step 3: mission-state.py mark-passes。"
    )


def _expected_context_mode(data: dict, iteration: int) -> str:
    """#352: mirror the #241 bounded-context condition without changing it."""
    return (
        "bounded"
        if iteration >= 2 and data.get("critic_has_new_scope") is False
        else "full"
    )


def _context_manifest_generated(data: dict, iteration: int) -> bool:
    """Return whether the recorded manifest is complete and still verifiable."""
    if type(iteration) is not int or iteration < 1:
        return False
    manifests = data.get("context_manifests")
    record = manifests.get(str(iteration)) if isinstance(manifests, dict) else None
    if not isinstance(record, dict):
        return False
    raw_path = record.get("path")
    digest = record.get("digest")
    generated_at = record.get("generated_at")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return False
    parsed_generated_at = (
        parse_iso_datetime(generated_at) if isinstance(generated_at, str) else None
    )
    if (
        parsed_generated_at is None
        or "T" not in generated_at
        or parsed_generated_at.tzinfo is None
        or parsed_generated_at.utcoffset() is None
    ):
        return False

    try:
        manifest_path = Path(raw_path)
        if not manifest_path.is_absolute():
            project_root = data.get("project_root")
            if not isinstance(project_root, str) or not project_root:
                return False
            manifest_path = Path(project_root) / manifest_path
        raw = manifest_path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError):
        return False
    if hashlib.sha256(raw).hexdigest() != digest.removeprefix("sha256:"):
        return False
    if not isinstance(payload, dict):
        return False
    payload_iteration = payload.get("iteration")
    return (
        payload.get("schema") == "mission-context-manifest/1"
        and type(payload_iteration) is int
        and payload_iteration >= 1
        and payload_iteration == iteration
    )


def _derive_next_action(data: dict) -> dict:
    """ADR-002 Stage 3 (G-3): state から次の 1 手を決定論的に導出する。

    ハーネス非依存の進行ガイド。Stop hook が使えない環境 (Codex 等) や
    compaction 後の復元で、散文指示に依存せず「state を読めば次手が自明」にする。
    分岐は SKILL.md の Phase 0-7 と同じ決定木を機械化したもの。
    """
    halt_reason = data.get("halt_reason") or ""
    if halt_reason:
        halt_category = data.get("halt_category")
        legacy_stale = _is_legacy_stale_halt(halt_category, halt_reason)
        if halt_category == "stale" or legacy_stale:
            recovery_summary = "stale/orphan halt は resume で安全に再開する"
            recovery_hint = "mission-state.py resume"
        else:
            expected_category = _halt_category_for_confirmation(halt_category)
            recovery_summary = "手動 halt は対象操作と state 再活性化の明示承認後に reactivate する"
            recovery_hint = (
                "mission-state.py reactivate --approved-by-user "
                f"--expected-category {expected_category} "
                '--reason "<ユーザーが承認した再開理由>"'
            )
        return {
            "next_action": "report-blocker",
            "summary": f"halted: {halt_reason}。blocker と次アクションをユーザーに報告する。{recovery_summary}",
            "command_hint": recovery_hint,
        }
    if data.get("passes") is True:
        return {
            "next_action": "report-complete",
            "summary": "mission は合格済み。最終報告 (成果物パス・検証結果・specialist summary) を出して終了する",
            "command_hint": "mission-state.py specialists summary",
        }
    if data.get("awaiting_user"):
        return {
            "next_action": "await-user",
            "summary": "ユーザー回答待ち (awaiting_user=true)。回答を得るまで不可逆操作に進まない",
            "command_hint": "",
        }
    if data.get("loop_active") is False:
        return {
            "next_action": "resume",
            "summary": "loop_active=false だが未合格・halt 理由なし。refresh-pid で再活性化してループを再開する",
            "command_hint": "mission-state.py refresh-pid",
        }
    phase = data.get("phase") or "planning"
    iteration = data.get("iteration", 1) or 1
    reviewer_count = data.get("reviewer_count", 2) or 2
    effective_reviewer_count = reviewer_count
    if iteration >= 2 and data.get("critic_has_new_scope") is False:
        effective_reviewer_count = min(reviewer_count, 2)
    pregate_warning = _pregate_verdict_warning(data.get("pregate"))

    def _planning_summary(summary: str) -> str:
        if not pregate_warning:
            return summary
        return f"{summary} {pregate_warning.removeprefix('WARNING: ')}"

    stagnation = data.get("stagnation_count", 0) or 0
    # 通常経路では push-score が phase=scoring へ遷移させるため stagnation>=3 と
    # phase=reviewing は共起しないが、手動 `set stagnation_count=N` は許可された操作。
    # 走行中のレビューを中断させないよう reviewing だけは phase 分岐を優先する。
    if stagnation >= 3 and phase != "reviewing":
        return {
            "next_action": "consider-halt",
            "summary": f"stagnation_count={stagnation} (3 連続でスコア停滞)。アプローチを変えても改善しない場合は mark-halt で停止し状況を報告する",
            "command_hint": 'mission-state.py mark-halt --reason "<停滞理由>"',
        }
    # #325: adaptive routing の next 駆動ゲート。init 引数経路 (#276) を通らず
    # 「init → set complexity=Simple」で確定したケースを補足する。portfolio-v1 で
    # Simple 3 tasks 全てが routing を素通りしてフルループが走った実測に基づく。
    if (
        phase == "planning"
        and iteration <= 1
        and data.get("complexity") == "Simple"
        and not data.get("review_tier_signals")
        and data.get("review_tier_source") != "user"
        and not data.get("issue_ref")
        and not data.get("force_mission")
        and (data.get("session_role") or "implementer") == "implementer"
        and not (data.get("score_history") or [])
    ):
        dispatch_fields = _goal_dispatch_route_fields(data)
        dispatch_guidance = _goal_dispatch_guidance(dispatch_fields)
        return {
            "next_action": "route-to-goal",
            "summary": (
                f"Simple + リスクシグナルなし: {dispatch_guidance}"
                "state を routed-goal で閉じ (pass-rate 対象外)、最終報告に routing を明記する。"
                "mission 機構が必要なら --force-mission で再 init (#325)"
            ),
            "command_hint": (
                "mission-state.py mark-halt --reason 'routed-to-goal (#325)' "
                f"--category routed-goal → {dispatch_guidance}"
            ),
            "details": {"complexity": "Simple", "route": "goal", **dispatch_fields},
        }
    if phase == "planning":
        lifecycle = derive_planning_lifecycle(data)
        if lifecycle["mode"] == "policy-v1":
            action = lifecycle.get("next_action")
            if action == "reconcile-provider-invocation":
                running = next(
                    record for record in data.get("specialist_invocations") or []
                    if isinstance(record, dict) and record.get("phase") == "planning"
                    and record.get("iteration") == iteration and record.get("status") == "running"
                )
                return {
                    "next_action": action,
                    "summary": "running planning provider must be reconciled before any new planning action",
                    "command_hint": f"mission-state.py specialists reconcile-invocation --invocation-id {running['invocation_id']} --status <completed|failed|abandoned-unknown> --evidence <ref> --expected-fencing-epoch <epoch>",
                }
            if action and action != "run-planner":
                hints = {
                    "prepare-planning-provider": "mission-state.py specialists prepare-invocation ...",
                    "await-planning-approval": "mission-state.py specialists verify-approval --preflight-id <id> --evidence-ref <ref> --approval-verifier <id>",
                    "invoke-planning-provider": "mission-state.py specialists invoke-prepared --provider <provider> --preflight-id <id> --iteration <i> --phase planning",
                    "import-planning-result": "mission-state.py specialists plan-import --input <result> --invocation-id <id>",
                    "promote-canonical-plan": "mission-state.py planning promote-provider-plan --invocation-id <id>",
                    "run-planner-with-evidence": "Skill: mission-planner (provider evidence is advisory only)",
                    "run-executor": "mission-state.py advance --phase executing --activity active:implementation",
                    "halt-required-planning-provider": "mission-state.py mark-halt --category required-planning-provider --reason <reason>",
                    "run-planner": "Skill: mission-planner",
                }
                return {
                    "next_action": action,
                    "summary": _planning_summary("policy v1 returns exactly one gated planning action"),
                    "command_hint": hints[action],
                    "details": {"planning_policy_version": 1, **({"degraded": True} if lifecycle.get("degraded") else {})},
                }
        core_adoption_required = (
            data.get("planning_policy_version") == 1
            and data.get("planning_strategy") in {None, "core"}
            and data.get("planning_provider_required") is not True
        )
        adoption_hint = (
            " → mission-state.py planning adopt-core --input <plan.json>"
            if core_adoption_required
            else ""
        )
        # #339: Standard iteration 1 は planner subagent を省略し orchestrator inline 計画。
        # portfolio-v4 実測: 時間比 (6.9-14.5x) > トークン比 (4.0-4.7x) の差分はターン数
        # (mission 19-31 turns vs goal 5) — subagent spin-up 1 回の削減がそのまま効く。
        # Complex / full tier / iteration>=2 は従来どおり mission-planner を使う。
        if (
            data.get("complexity") == "Standard"
            and iteration <= 1
            and (data.get("review_tier") or "standard") != "full"
        ):
            summary = _planning_summary(
                (
                    f"iteration {iteration} (Standard): mission-planner を起動せず、この turn 内で "
                    "bounded plan (steps + 依存関係 + 完了条件) を artifact に書く (#339)。"
                    "計画の成果物要件は subagent 経路と同一"
                )
            )
            return {
                "next_action": "plan-inline",
                "summary": summary,
                "command_hint": (
                    f"plan を artifact に記載{adoption_hint}"
                    " → mission-state.py advance --phase executing --activity active:implementation"
                ),
                "details": {"plan_mode": "inline"},
                "command_sequence": _happy_path_sequence(
                    "planning",
                    effective_reviewer_count,
                    plan_mode="inline",
                    adopt_core=core_adoption_required,
                ),
            }
        return {
            "next_action": "run-planner",
            "summary": _planning_summary(f"iteration {iteration}: mission-planner を起動して計画を立てる (完了後 set phase=executing)"),
            "command_hint": (
                f"Skill: mission-planner{adoption_hint}"
                " → mission-state.py advance --phase executing --activity active:implementation"
            ),
            "command_sequence": _happy_path_sequence(
                "planning",
                effective_reviewer_count,
                plan_mode="subagent",
                adopt_core=core_adoption_required,
            ),
        }
    if phase == "executing":
        return {
            "next_action": "run-executor",
            "summary": f"iteration {iteration}: mission-executor で計画を実行する (完了後 set phase=reviewing。10分超は progress update)",
            "command_hint": "Skill: mission-executor → mission-state.py advance --phase reviewing --activity reviewer-wait:review-response",
            "command_sequence": _happy_path_sequence("executing", effective_reviewer_count),
        }
    if phase == "reviewing":
        # #309 (F4): iter>=2 で critic_has_new_scope 未設定なら run-reviewers を返さない。
        # 実運用監査 (2026-08-01) で設定 0/115 件 — prose (SKILL.md #258) では実行されない
        # ため、guidance 層で機械的に強制する。安全側デフォルト (未設定=full) は維持しつつ、
        # 未設定のまま review へ進む経路を塞ぎ #240/#241 を発火可能にする。
        if iteration >= 2 and data.get("critic_has_new_scope") is None:
            return {
                "next_action": "record-critic-scope",
                "summary": (
                    f"iteration {iteration}: reviewer 起動前に critic の実行計画テーブルから "
                    "scope 判定を state へ記録する。全ステップの対応 finding が既存 finding id "
                    "のみなら false、new を含むなら true (#309)"
                ),
                "command_hint": "mission-state.py set critic_has_new_scope='false'  # または 'true'",
                "details": {"iteration": iteration},
            }
        # #241: bounded context — iteration >= 2 かつ新規 scope なしなら bounded mode
        context_mode = _expected_context_mode(data, iteration)
        return {
            "next_action": "run-reviewers",
            "summary": f"iteration {iteration}: mission-reviewer を {effective_reviewer_count} 名、単一メッセージで並列起動する (直列起動は規律違反。直列は Standard で約 2-3 分の無駄を実測 #338)",
            "command_hint": f"Skill: mission-reviewer x{effective_reviewer_count} (1 message)",
            "details": {"reviewer_count": effective_reviewer_count, "context_mode": context_mode, "parallel_spawn_required": True},
            "command_sequence": _happy_path_sequence("reviewing", effective_reviewer_count),
        }
    # phase == scoring / done / その他: 現 iteration の有効スコア有無で分岐
    history = data.get("score_history") or []
    scored_current = [
        h for h in history
        if isinstance(h, dict) and h.get("iteration") == iteration and _is_valid_composite(h.get("composite"))
    ]
    if scored_current:
        latest = scored_current[-1]
        # #187: score entry はあるが findings evidence がない (scoring-json 経路なのに
        # findings_evidence_path 欠落) 場合、mark-passes は _validate_findings_evidence_gate で
        # exit 2 になる。実運用で、この状態から Codex agent が --force に逃げた実害があったため、
        # next の時点で「force ではなく aggregate-reviews をやり直す」ことを明示する。
        missing_findings_evidence = (
            latest.get("score_source") == "scoring-json"
            and not latest.get("findings_evidence_path")
        )
        if missing_findings_evidence:
            # review 由来のレビュー結果: #189 の unclosed specialist 情報もここで併記する
            # (aggregate-reviews のリトライ待ちの間も next の details から欠落させない)。
            unclosed_during_retry = _unclosed_optional_specialist_skills(data)
            return {
                "next_action": "aggregate-reviews",
                "summary": (
                    f"iteration {iteration}: 直前の push-score に findings evidence "
                    "(findings_evidence_path) がありません。このまま mark-passes を呼んでも "
                    "exit 2 になります。--force は使わず aggregate-reviews からやり直してください。"
                ),
                "command_hint": (
                    _native_review_handoff_hint(
                        iteration, effective_reviewer_count, resubmit=True,
                    )
                    + " mission-scorer fallback を使った場合も、その mission-review/1 出力を Step 1 に渡す。"
                ),
                "details": {
                    "missing_findings_evidence": True,
                    **({"unclosed_specialists": unclosed_during_retry} if unclosed_during_retry else {}),
                },
            }
        unclosed = _unclosed_optional_specialist_skills(data)  # #189
        return {
            "next_action": "mark-passes",
            "summary": f"iteration {iteration} の採点は記録済み。mark-passes で threshold gate 判定する (reject なら mission-critic → 次 iteration)",
            "command_hint": "mission-state.py mark-passes",
            "details": {"unclosed_specialists": unclosed} if unclosed else {},
        }
    return {
        "next_action": "aggregate-reviews",
        "summary": (
            f"iteration {iteration}: reviewer の mission-review/1 JSON を review-import --stdin で"
            " state-owned evidence にし、review-finalize --input-ref で集計・記録する。"
            "--force は使わない。"
        ),
        "command_hint": _native_review_handoff_hint(
            iteration, effective_reviewer_count,
        ),
    }


def cmd_next(args):
    """ADR-002 Stage 3: 次の 1 手を JSON で返す (read-only・state 不在でも exit 0)."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print(json.dumps({
            "next_action": "init",
            "summary": "mission state がありません。init でミッションを登録してループを開始する",
            "command_hint": 'mission-state.py init "<ミッション記述>" --complexity <Simple|Standard|Complex|Critical>',
        }, ensure_ascii=False))
        return
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
    out = _derive_next_action(data)
    # #238 (S6): 時間予算の消費率を advisory として常に添付する。
    # exceeded 時のみ、spawn 系の高コスト手を consider-halt へ差し替え、
    # 成果物確定 + partial-done halt を促す (予算切れ全損の防止)。
    # aggregate-reviews / mark-passes 等の安価なローカル完結手・terminal・
    # await-user は override しない (誠実な終端を優先)。
    pressure = _budget_pressure(data, iso_now())
    out["budget_pressure"] = pressure
    if pressure and pressure["level"] == "exceeded" and out.get("next_action") in BUDGET_SPAWN_ACTIONS:
        out["budget_overridden_action"] = out["next_action"]
        out["next_action"] = "consider-halt"
        out["summary"] = (
            f"時間予算 {pressure['budget_minutes']} 分を超過 ({pressure['elapsed_minutes']} 分経過)。"
            " 新規 spawn を止め、現時点の成果物を確定して partial-done で終了する。"
        )
        out["command_hint"] = (
            'mission-state.py mark-halt --reason "時間予算超過: 完了分と未完了作業を明記" --category partial-done'
        )
    elif pressure and pressure["level"] == "warn":
        out["budget_warning"] = (
            f"時間予算の {pressure['pressure_pct']}% を消費。optional specialist / critic の"
            " 新規 spawn を控え、成果物の確定を優先する。"
        )
    out.setdefault("details", {})
    out.update({
        "phase": data.get("phase"),
        "iteration": data.get("iteration"),
        "session_id": data.get("session_id"),
        "loop_active": data.get("loop_active"),
        "passes": data.get("passes"),
        "stagnation_count": data.get("stagnation_count", 0) or 0,
    })
    print(json.dumps(out, ensure_ascii=False))


def _codex_hook_config_paths(explicit_path: str | None = None) -> list[Path]:
    """Return candidate Codex user hook config paths in deterministic order."""
    if explicit_path:
        return [Path(explicit_path).expanduser()]
    paths: list[Path] = []
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        paths.append(Path(codex_home).expanduser() / "hooks.json")
    paths.append(Path.home() / ".codex" / "hooks.json")
    out: list[Path] = []
    seen = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _hook_config_status(paths: list[Path]) -> dict:
    checked = []
    for path in paths:
        item = {"path": str(path), "exists": path.exists(), "configured": False}
        if not path.exists():
            checked.append(item)
            continue
        try:
            text = path.read_text(encoding="utf-8")
            json.loads(text)
        except json.JSONDecodeError as e:
            item["error"] = f"invalid json: {e}"
            checked.append(item)
            continue
        except OSError as e:
            item["error"] = str(e)
            checked.append(item)
            continue
        item["configured"] = "mission-stop-guard.sh" in text
        checked.append(item)
    return {
        "configured": any(item.get("configured") for item in checked),
        "checked": checked,
    }


def _version_tuple(value: str) -> tuple:
    """'1.2.0' -> (1, 2, 0). 非数値チャンクは 0 として比較する (壊れたディレクトリ名を無害化)."""
    parts = []
    for chunk in str(value).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _plugin_cache_roots() -> dict[str, Path]:
    """#186: plugin cache のバージョンディレクトリ親を返す (テスト用に env で override 可能)。

    MISSION_CLAUDE_HOME / CODEX_HOME (既存の codex hook 探索と同じ変数) を尊重する。
    """
    claude_home = os.environ.get("MISSION_CLAUDE_HOME")
    claude_root = Path(claude_home).expanduser() if claude_home else Path.home() / ".claude"
    codex_home = os.environ.get("CODEX_HOME")
    codex_root = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return {
        "claude-code": claude_root / "plugins" / "cache" / "mission-marketplace" / "mission",
        "codex": codex_root / "plugins" / "cache" / "mission-marketplace" / "mission",
    }


def _detect_version_skew() -> dict | None:
    """#186: インストール済み plugin cache が現在の MISSION_CLI_VERSION より古ければ警告データを返す。

    cache ディレクトリが存在しない、または全て現行以上のバージョンなら None (無警告)。
    実行中の mission-state.py が symlink/直接 checkout 経由 (plugin cache を介さない) の場合、
    このチェックは古い cache が「使われている」ことまでは検知できない — cache の存在自体を
    陳腐化の兆候として警告するに留まる (#186 スコープ: 検出であり自動修復ではない)。
    """
    current = _version_tuple(MISSION_CLI_VERSION)
    stale: dict[str, list[str]] = {}
    for label, cache_dir in _plugin_cache_roots().items():
        if not cache_dir.is_dir():
            continue
        try:
            older = sorted(
                p.name for p in cache_dir.iterdir()
                if p.is_dir() and _version_tuple(p.name) < current
            )
        except OSError:
            continue
        if older:
            stale[label] = older
    if not stale:
        return None
    return {"cli_version": MISSION_CLI_VERSION, "stale_caches": stale}


def cmd_codex_preflight(args):
    """Codex /mission startup health check.

    This intentionally does not auto-install hooks. Codex hook trust is a user-level
    security boundary, so the command reports state/guard readiness and leaves setup
    as an explicit opt-in action.
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    state_present = sf.exists()
    state_active = False
    state_snapshot = {}
    next_action = "init"
    next_summary = "mission state がありません。init を先に実行してください。"
    if state_present:
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
        state_snapshot = {
            "session_id": data.get("session_id"),
            "agent": data.get("agent"),
            "loop_active": data.get("loop_active"),
            "passes": data.get("passes"),
            "halt_reason": data.get("halt_reason") or "",
            "phase": data.get("phase"),
            "iteration": data.get("iteration"),
        }
        state_active = (
            data.get("loop_active") is True
            and data.get("passes") is not True
            and not (data.get("halt_reason") or "")
        )
        derived = _derive_next_action(data)
        next_action = derived.get("next_action") or "unknown"
        next_summary = derived.get("summary") or ""

    hook_status = _hook_config_status(_codex_hook_config_paths(getattr(args, "hook_config", None)))
    warnings: list[str] = []
    required_actions: list[str] = []
    if not state_present:
        required_actions.append(
            "Run `mission-state.py init ... --complexity <level>` and then "
            "`mission-state.py codex-preflight --json --strict` before any task setup, "
            "including worktree creation or implementation work, and before any final report."
        )
    elif not state_active:
        required_actions.append("Resolve the inactive, passed, or halted mission state before continuing.")
    if not hook_status["configured"]:
        warnings.append(
            "Codex Stop hook is not configured or was not found. Continue only with the state-driven fallback: call `mission-state.py next` at every phase boundary and before any final report."
        )
        if getattr(args, "require_stop_hook", False):
            required_actions.append("Configure and trust `mission-stop-guard.sh` in Codex hooks, or rerun without --require-stop-hook for skills-only fallback.")

    # #226 (A-4): MISSION_REQUIRE_SCORING_EVIDENCE=0 is a deprecated escape hatch that
    # bypasses the scoring-evidence gate (findings recomputation) via the legacy --items
    # push-score path. It must never be active for real work; --strict preflight rejects it
    # and reports the run as not ok.
    scoring_evidence_escape_hatch = os.environ.get("MISSION_REQUIRE_SCORING_EVIDENCE") == "0"
    if scoring_evidence_escape_hatch:
        required_actions.append(
            "Unset MISSION_REQUIRE_SCORING_EVIDENCE=0: this deprecated escape hatch bypasses "
            "the scoring-evidence gate and will be removed in the next minor release. "
            "Use `push-score --scoring-json` (aggregate-reviews output) instead."
        )

    version_skew = _detect_version_skew()  # #186
    if version_skew:
        warnings.append(
            "Installed plugin cache(s) are older than the running CLI version "
            f"({version_skew['cli_version']}): {version_skew['stale_caches']}. "
            "Old caches run stale SKILL.md instructions and gate logic; update the plugin "
            "install or clear the stale cache directory."
        )

    fallback_available = state_active and next_action not in {"init", "report-blocker", "report-complete"}
    result = {
        "ok": state_active and (hook_status["configured"] or (fallback_available and not getattr(args, "require_stop_hook", False))) and not (scoring_evidence_escape_hatch and getattr(args, "strict", False)),
        "state_guard": {
            "present": state_present,
            "active": state_active,
            "state_file": str(sf),
            **state_snapshot,
        },
        "codex_stop_hook": hook_status,
        "mechanical_guard": "stop-hook" if hook_status["configured"] else ("state-next-fallback" if fallback_available else "none"),
        "next_action": next_action,
        "next_summary": next_summary,
        "warnings": warnings,
        "required_actions": required_actions,
        "version_skew": version_skew,  # #186: None (no skew) or {"cli_version": ..., "stale_caches": {...}}
        # #187: Codex は Skill 並列不可・reviewer JSON の同一コンテキスト自演になりがちで、
        # aggregate-reviews が初回失敗すると --force に逃げやすい。scoring パイプラインの
        # 正規手順を preflight 時点で明示し、`next` の command_hint と合わせて force を回避する。
        "scoring_pipeline": (
            "Standard scoring path: for each reviewer, run "
            "`review-import --iteration <N> --stdin` and retain the returned "
            "review_evidence_ref.path; then run "
            "`review-finalize --iteration <N> --input-ref <review_evidence_ref.path>` "
            "with one --input-ref per reviewer; then run `mark-passes`. "
            "If reviewer JSON cannot be produced in this Codex context, use mission-scorer as a "
            "prose-to-JSON fallback converter, then feed its output through review-import and "
            "review-finalize the same way. Never fall back to `mark-passes --force` just because review import "
            "failed once; `mission-state.py next` will report a retry hint when the latest score "
            "entry is missing findings evidence."
        ),
    }
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("ok=" + str(result["ok"]).lower())
        print("mechanical_guard=" + result["mechanical_guard"])
        print("next_action=" + result["next_action"])
        for warning in warnings:
            print("WARNING: " + warning, file=sys.stderr)
        for action in required_actions:
            print("ACTION: " + action, file=sys.stderr)
    if required_actions:
        sys.exit(2 if getattr(args, "strict", False) or getattr(args, "require_stop_hook", False) else 0)


def _permission_preflight(cwd: Path) -> dict:
    """Return the Phase 0 write-probe result without interacting with stdout."""
    sf = resolve_state_file(cwd)
    if not sf.exists():
        return {
            "ok": False,
            "halt_recorded": False,
            "halt_category": "blocked-external",
            "terminal_outcome": "blocked_external",
            "error": "state-not-found",
            "probes": [],
        }
    try:
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text(encoding="utf-8"))
    except OSError:
        reason = (
            "Phase 0 permission preflight failed before task execution: "
            "state write unavailable"
        )
        return {
            "ok": False,
            "halt_recorded": False,
            "halt_category": "blocked-external",
            "terminal_outcome": "blocked_external",
            "halt_reason": reason,
            "probes": [
                {"target": "state", "ok": False, "error": "write-unavailable"}
            ],
        }
    assumptions_path = data.get("assumptions_path")
    if not assumptions_path:
        reason = (
            "Phase 0 permission preflight failed before task execution: "
            "assumptions path missing"
        )
        return {
            "ok": False,
            "halt_recorded": _record_permission_preflight_halt(cwd, sf, reason),
            "halt_category": "blocked-external",
            "terminal_outcome": "blocked_external",
            "halt_reason": reason,
            "error": "assumptions-path-missing",
            "probes": [],
        }

    try:
        assumptions_file = _validated_assumptions_probe_path(
            cwd, str(assumptions_path)
        )
    except (OSError, ValueError):
        reason = (
            "Phase 0 permission preflight failed before task execution: "
            "assumptions evidence path is invalid"
        )
        return {
            "ok": False,
            "halt_recorded": _record_permission_preflight_halt(cwd, sf, reason),
            "halt_category": "blocked-external",
            "terminal_outcome": "blocked_external",
            "halt_reason": reason,
            "probes": [
                {
                    "target": "assumptions",
                    "ok": False,
                    "error": "invalid-evidence-path",
                }
            ],
        }

    probes = []
    checks = (
        ("state", lambda: _probe_directory_write(sf.parent)),
        ("assumptions", lambda: _probe_file_write(assumptions_file)),
    )
    for target, probe in checks:
        try:
            probe()
        except OSError:
            probes.append(
                {"target": target, "ok": False, "error": "write-unavailable"}
            )
            reason = (
                "Phase 0 permission preflight failed before task execution: "
                f"{target} write unavailable"
            )
            halt_recorded = _record_permission_preflight_halt(cwd, sf, reason)
            return {
                "ok": False,
                "halt_recorded": halt_recorded,
                "halt_category": "blocked-external",
                "terminal_outcome": "blocked_external",
                "halt_reason": reason,
                "probes": probes,
            }
        probes.append({"target": target, "ok": True})

    return {
        "ok": True,
        "halt_recorded": False,
        "probes": probes,
    }


def cmd_permission_preflight(args):
    """Verify that Phase 0 can persist state and assumptions evidence."""
    result = _permission_preflight(Path.cwd())
    print(json.dumps(result, indent=2 if getattr(args, "json", False) else None))
    if not result["ok"]:
        sys.exit(2)


# Issue #2: set で変更禁止のフィールド (mission_id 整合性維持のため)
FROZEN_FIELDS = {
    "mission",  # 変更したいなら init を使う (mission_id が再計算される)
    "mission_id",
    "passes",
    "passes_forced",
    "force_reason",
    "score_history",
    "failure_ledger",
    "threshold",
    "schema_version",
    "session_role",
    "terminal_outcome",
    "artifact_applicability",
    "artifact",
    "artifact_path",
    "artifact_lint",
    "artifact_lint_identity",
    "artifact_lint_status",
    "project_root",
    "started_at",
    "created_at_session",
    "reactivation_history",  # 承認監査は reactivate の append-only 記録
}


def cmd_set(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        _reject_active_provider_mutation(data, "set")
        now = iso_now()
        explicit_keys = {kv.partition("=")[0] for kv in args.kvs}
        # Issue #222 (A-2/A-3): gate 判定に影響する state フィールドの条件付き set ガード。
        # reviewer_count は tier と同時の運用上書きだけを許し、halt の解除は承認監査付きの
        # dedicated reactivate command に限定する。
        if "reviewer_count" in explicit_keys and not ({"complexity", "review_tier"} & explicit_keys):
            _raise_guided_failure(
                "`reviewer_count` は単独 set 不可。"
                " 変更する場合は `complexity` または `review_tier` と同時に指定してください "
                "(A-2: agreement gate 無効化の防止)。",
                command="set",
                reason="reviewer-count",
                context=_guidance_context_for_state(data),
                outcome_kind="expected-gate",
            )
        if "halt_category" in explicit_keys:
            _raise_guided_failure(
                "`halt_category` は set で変更不可。"
                " 変更は mark-halt / refresh-pid / resume 経由でのみ行ってください "
                "(A-3: 無承認 reactivate の防止)。",
                command="set",
                reason="halt-category",
                context=_guidance_context_for_state(data),
                outcome_kind="expected-gate",
            )
        if "halt_reason" in explicit_keys:
            _raise_guided_failure(
                "`halt_reason` は set で変更不可。"
                " 明示 halt の解除は `reactivate --approved-by-user` を使用してください "
                "(A-3: 承認監査を伴わない再活性化の防止)。",
                command="set",
                reason="halt-reason",
                context=_guidance_context_for_state(data),
                outcome_kind="expected-gate",
            )
        if "loop_active" in explicit_keys and data.get("halt_reason"):
            loop_active_raw = next(
                (value for key, _, value in (kv.partition("=") for kv in args.kvs) if key == "loop_active"),
                None,
            )
            try:
                requested_loop_active = json.loads(loop_active_raw) if loop_active_raw is not None else None
            except json.JSONDecodeError:
                requested_loop_active = loop_active_raw
            if requested_loop_active is True:
                print(
                    "ERROR: halt中の `loop_active=true` は set で変更不可。"
                    " `reactivate --approved-by-user` を使用してください。",
                    file=sys.stderr,
                )
                sys.exit(2)
        for kv in args.kvs:
            if "=" not in kv:
                print(f"ERROR: key=value 形式で指定してください: {kv}", file=sys.stderr)
                sys.exit(1)
            key, _, value = kv.partition("=")
            # Issue #2: FROZEN_FIELDS を変更禁止 (mission_id 整合性維持)
            if key in FROZEN_FIELDS:
                print(
                    f"ERROR: `{key}` は set で変更不可。新しい mission は `init` を使用してください "
                    f"(mission_id が再計算されます)。",
                    file=sys.stderr,
                )
                sys.exit(2)
            # Issue #168: review_tier の検証と source 管理
            if key == "review_tier":
                if value not in TIER_REVIEWER_COUNT:
                    print(
                        f"ERROR: review_tier の値 '{value}' は無効です。"
                        f" 有効値: {list(TIER_REVIEWER_COUNT)}",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                # auto 導出値より低い tier を user 指定した場合は WARNING (拒否しない)
                _cur_mission = data.get("mission", "")
                _cur_cx = data.get("complexity")
                _cur_risk = (data.get("task_profile") or {}).get("risk")
                _derived_tier, _ = derive_review_tier(_cur_mission, _cur_cx, _cur_risk)
                _tier_order = {"light": 0, "standard": 1, "full": 2}
                if _tier_order.get(value, 0) < _tier_order.get(_derived_tier, 0):
                    print(
                        f"WARNING [#168]: review_tier='{value}' は auto 導出値 '{_derived_tier}' より低いです。"
                        f" ゲート意味論 (threshold/open_high/findings evidence/halt) は変わりません。",
                        file=sys.stderr,
                    )
                data["review_tier"] = value
                data["review_tier_source"] = "user"
                continue
            # 型推論: 数値 / bool / JSON
            try:
                parsed_value = json.loads(value)
            except json.JSONDecodeError:
                parsed_value = value
            if key == "phase":
                normalized_phase = _normalize_set_phase_value(str(parsed_value))
                try:
                    old_phase = data.get("phase")
                    current = data.get("activity_current")
                    if (
                        normalized_phase not in {"done", "halted"}
                        and old_phase != normalized_phase
                        and is_phase_default_activity(current, old_phase)
                    ):
                        end_activity_segment(data, now)
                    _transition_phase(data, normalized_phase, now)
                except ArtifactContractError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    sys.exit(2)
                if normalized_phase not in {"done", "halted"} and not data.get("activity_current"):
                    try:
                        start_phase_default_activity(data, now)
                    except ActivityTimingError:
                        pass
            else:
                data[key] = parsed_value
        # A-M1 (2026-06-10 / Issue #168 拡張): complexity 変更時の reviewer_count と review_tier 同期
        # - review_tier_source が "auto" (またはフィールド不在) の場合: tier を再導出して reviewer_count も同期
        # - review_tier_source が "user" の場合: tier を維持し、reviewer_count も tier 由来を維持
        # - reviewer_count を明示した場合はそちらが優先
        # (explicit_keys は関数冒頭で計算済み)
        if "complexity" in explicit_keys:
            tier_source = data.get("review_tier_source", "auto")
            if tier_source == "user":
                # user 指定の tier を維持: reviewer_count も tier 由来を維持 (complexity 変更に追随しない)
                if "reviewer_count" not in explicit_keys and data.get("review_tier") in TIER_REVIEWER_COUNT:
                    data["reviewer_count"] = TIER_REVIEWER_COUNT[data["review_tier"]]
            else:
                # auto: complexity 変更で tier を再導出
                cx = data.get("complexity")
                _mission = data.get("mission", "")
                _risk = (data.get("task_profile") or {}).get("risk")
                _new_decision = derive_review_tier_decision(_mission, cx, _risk)
                _new_tier = _new_decision["tier"]
                data["review_tier"] = _new_tier
                data["review_tier_source"] = "auto"
                data["review_tier_signals"] = _new_decision["signals"]
                data["review_tier_signal_details"] = _new_decision["signal_details"]
                if "reviewer_count" not in explicit_keys:
                    data["reviewer_count"] = TIER_REVIEWER_COUNT[_new_tier]
        elif "review_tier" in explicit_keys and "reviewer_count" not in explicit_keys:
            # review_tier だけ変更された場合: reviewer_count を tier から同期
            _tier = data.get("review_tier")
            if _tier in TIER_REVIEWER_COUNT:
                data["reviewer_count"] = TIER_REVIEWER_COUNT[_tier]
        # halt 理由がない中断 state の loop 再開時は aggregate へ戻す。
        # halt 済み state は上のガードで拒否し、reactivate / refresh-pid に限定する。
        if "loop_active" in explicit_keys and data.get("loop_active") is True:
            _add_to_aggregate(cwd, sf.stem)
        # #330: routing のコマンド層 hard 化。set complexity=Simple が routing 条件を
        # 満たす場合、コマンド自身が verdict を実行する (state を routed-goal で halt)。
        # #325 の next 駆動 gate は orchestrator が next を呼ばない経路に 1/3 しか
        # 効かなかった実測 (portfolio-v2) に基づく。init 経路 (#276/#304) と挙動統一。
        _routed_verdict = None
        if (
            "complexity" in explicit_keys
            and data.get("complexity") == "Simple"
            and data.get("loop_active") is True
            and not data.get("halt_reason")
            and (data.get("phase") or "planning") == "planning"
            and (data.get("iteration") or 1) <= 1
            and not data.get("review_tier_signals")
            and data.get("review_tier_source") != "user"
            and not data.get("issue_ref")
            and not data.get("force_mission")
            and (data.get("session_role") or "implementer") == "implementer"
            and not (data.get("score_history") or [])
        ):
            dispatch_fields = _goal_dispatch_route_fields(data)
            data["goal_dispatch_effective"] = dispatch_fields["goal_dispatch_effective"]
            data["goal_dispatch_host"] = dispatch_fields["goal_dispatch_host"]
            if dispatch_fields.get("goal_dispatch_fallback_reason"):
                data["goal_dispatch_fallback_reason"] = dispatch_fields["goal_dispatch_fallback_reason"]
            else:
                data.pop("goal_dispatch_fallback_reason", None)
            data["loop_active"] = False
            data["halt_reason"] = "routed-to-goal (#330: Simple + リスクシグナルなし)"
            data["halt_category"] = "routed-goal"
            _transition_phase(data, "halted", now)
            _write_terminal_outcome(data)
            _remove_from_aggregate(cwd, sf.stem)
            _routed_verdict = {
                "ok": True,
                "route": "goal",
                "complexity": "Simple",
                "reason": "Simple complexity with no irreversible/security signals (#330)",
                "guidance": _goal_dispatch_guidance(
                    dispatch_fields,
                    "state は routed-goal で halt 済み (mark-halt 不要)。mission ループを続けず、",
                ),
                **dispatch_fields,
            }
        _ensure_phase_timing(data, now)
        data["updated_at"] = now
        data = stamp_metadata(data, cwd)
        backup_state(sf)
        atomic_write_json(sf, data, administrative=bool(_routed_verdict))
    print(json.dumps(_routed_verdict or {"ok": True}, ensure_ascii=False, indent=2 if _routed_verdict else None))


# H2 (2026-06-10): スコア項目キーの正規形とエイリアス。実ログで表記揺れが混在し
# stats 横断集計・min_item 検証が壊れたため push-score 時に正規化する。
CANONICAL_SCORE_KEYS = {"mission_achievement", "accuracy", "completeness", "usability"}
REVIEW_SCORE_KEYS = ("mission_achievement", "accuracy", "completeness", "usability")
REVIEW_SEVERITIES = {"High", "Medium", "Low"}
# #353: provisional observation thresholds. Calibrate from reviewer_output_stats
# distributions before treating these values as stable guidance; this is WARN-only.
REVIEW_PROSE_BYTES_WARN = 20_000
REVIEW_PROSE_RATIO_WARN = 0.7
REVIEW_TEMPLATE_HEADING_RE = re.compile(
    r"^\s*#{2,3}\s+(?:"
    r"レビュー結果(?:\s*\(担当観点:.*\))?|"
    r"採点|強み\s*\(Good\)|改善点\s*\(Issues\)|"
    r"重大ブロッカー\s*\(あれば\)|担当観点に対する総評"
    r")\s*$"
)
REVIEW_JSON_FENCE_RE = re.compile(
    r"^```json[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
SCORE_KEY_ALIASES = {
    "usefulness": "usability",
    "practicality": "usability",
}

# `reviewer_consensus` used to be treated as an item.  It is now represented
# only by the independent review_agreement field.  New writers reject both the
# raw legacy name and its alias for every complexity; old state remains
# display-only and is classified separately by audit.
LEGACY_SCORE_ITEM_KEYS = {"reviewer_consensus", "reviewer_agreement"}


def normalize_score_items(items: dict):
    """エイリアスを正規キーへ変換する。返り値: (normalized, unknown, collisions).

    衝突規則 (B-H1, 2026-06-10): エイリアスの変換先が既に埋まっている場合は
    明示された正規キーの値が勝ち、エイリアス側は破棄して collisions に記録する
    (dict 順序依存のサイレント上書きを排除)。エイリアス同士の衝突は先勝ち。
    """
    normalized = {}
    unknown = []
    collisions = []
    # pass 1: 正規キー・未知キーを確定 (正規キーの明示指定が常に勝つ)
    for k, v in items.items():
        if k in CANONICAL_SCORE_KEYS or k not in SCORE_KEY_ALIASES:
            if k not in CANONICAL_SCORE_KEYS:
                unknown.append(k)
            normalized[k] = v
    # pass 2: エイリアスを変換 (衝突したら破棄して記録)
    for k, v in items.items():
        if k in SCORE_KEY_ALIASES:
            ck = SCORE_KEY_ALIASES[k]
            if ck in normalized:
                collisions.append((k, ck))
            else:
                normalized[ck] = v
    return normalized, unknown, collisions


def _scoring_archive_path(cwd: Path, iteration: int, data: dict, suffix: str = ".md") -> Path:
    archive_dir = state_dir(cwd) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    # H1 (2026-06-10): mission_id を含めて連続ランの上書き消失を防止
    gid = (data.get("mission_id") or "unknown")[:8]
    return archive_dir / f"iter-{iteration}-{gid}-scoring{suffix}"


def _scoring_metadata_header(data: dict, entry: dict, iteration: int) -> str:
    # #3 (2026-06-13): scoring md 単独で起動元を追えるようメタヘッダを前置 (HTML コメント=grep 可能)
    return (
        f"<!-- mission-meta: session_id={data.get('session_id')} "
        f"agent={data.get('agent') or 'unknown'} mission_id={data.get('mission_id')} "
        f"iteration={iteration} timestamp={entry['timestamp']} -->\n"
    )


def _archive_scoring_output(cwd: Path, scoring_output: str, iteration: int,
                            data: dict, entry: dict) -> str | None:
    """Scorer の md 出力を archive/iter-N-<mission8>-scoring.md に保存し起動元メタを前置する。

    返り値は保存先パス。ファイルが見つからなければ WARN を出して None を返す (後方互換)。
    """
    src = Path(scoring_output)
    if not (src.exists() and src.is_file()):
        print(f"WARNING: --scoring-output のファイルが見つかりません: {src}", file=sys.stderr)
        return None
    dst = _scoring_archive_path(cwd, iteration, data)
    meta = _scoring_metadata_header(data, entry, iteration)
    dst.write_text(meta + src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(dst)


def _validate_score_args(args) -> dict:
    """push-score の --items パース + エイリアス正規化 + 範囲検証。正規化済 items を返す (不正なら exit)。"""
    try:
        items = json.loads(args.items)
    except json.JSONDecodeError as e:
        print(f"ERROR: --items が不正な JSON です: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(items, dict):
        print("ERROR: --items は JSON オブジェクト (key->score) で指定してください。", file=sys.stderr)
        sys.exit(1)
    if LEGACY_SCORE_ITEM_KEYS & set(items):
        print(
            "ERROR: reviewer_consensus / reviewer_agreement は新規 score items では使用できません。"
            " 合意度は review_agreement の独立フィールドで記録し、items は4軸だけにしてください。",
            file=sys.stderr,
        )
        sys.exit(2)
    # H2: エイリアス正規化 + 未知キー WARN (reject はしない: 後方互換)
    items, unknown_keys, collisions = normalize_score_items(items)
    if collisions:
        for alias, ck in collisions:
            print(
                f"WARNING: エイリアス '{alias}' が既存キー '{ck}' と衝突したため破棄しました (明示値が優先)。",
                file=sys.stderr,
            )
    if unknown_keys:
        print(
            f"WARNING: 非正規のスコア項目キー {unknown_keys} を検出しました。"
            f" 正規キー: {sorted(CANONICAL_SCORE_KEYS)} (エイリアス: {SCORE_KEY_ALIASES})",
            file=sys.stderr,
        )
    # 改善3a: composite / min_item の有限値・範囲バリデーション。
    for label, val in (("composite", args.composite), ("min_item", args.min_item)):
        if not _finite_score(val):
            print(f"ERROR: --{label} {val} は bool ではない有限の {SCORE_MIN}〜{SCORE_MAX} 数値で指定してください。", file=sys.stderr)
            sys.exit(1)
    return items


def _numeric_item_values(items: dict) -> list:
    return [float(v) for v in items.values() if _finite_score(v)]


def _reject_normalized_scale(items: dict) -> None:
    """ADR-002 Stage 1: 0-1 正規化スケール混入の reject.

    実ログ (xai-cli cx-019efece, 2026-06-25) で composite 0.96 (= 4.8/5) が 0-5 範囲内として
    素通りした回帰。全 items が 1.0 以下なら 5 点スケールの採点ではないと判断して exit 2。
    正当に 1 項目だけ 1.0 以下になるケース (max > 1.0) は通す。
    """
    numeric = _numeric_item_values(items)
    if numeric and max(numeric) <= 1.0:
        print(
            "ERROR: すべての items スコアが 1.0 以下です。0-1 正規化スケールで採点した疑いがあります。"
            f" 5 点満点 ({SCORE_MIN}-{SCORE_MAX}) で採点し直してください。",
            file=sys.stderr,
        )
        sys.exit(2)


def _load_scoring_json(path_str: str):
    """ADR-002 Stage 1: --scoring-json の strict 読み込み。

    返り値: (items, notes, open_high, payload)。従来 --items 経路と異なり、
    未知キー・範囲外の item 値は WARN でなく reject する (exit 2)。
    """
    src = Path(path_str)
    if not (src.exists() and src.is_file()):
        print(f"ERROR: --scoring-json のファイルが見つかりません: {src}", file=sys.stderr)
        sys.exit(2)
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"ERROR: --scoring-json が不正な JSON です: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict) or not payload["items"]:
        print("ERROR: --scoring-json は {\"items\": {key->score}} を含む JSON オブジェクトで指定してください。", file=sys.stderr)
        sys.exit(2)
    items, unknown_keys, collisions = normalize_score_items(payload["items"])
    for alias, ck in collisions:
        print(
            f"WARNING: エイリアス '{alias}' が既存キー '{ck}' と衝突したため破棄しました (明示値が優先)。",
            file=sys.stderr,
        )
    if unknown_keys:
        print(
            f"ERROR: --scoring-json に非正規のスコア項目キー {unknown_keys} があります。"
            f" 正規キー: {sorted(CANONICAL_SCORE_KEYS)} (エイリアス: {SCORE_KEY_ALIASES})",
            file=sys.stderr,
        )
        sys.exit(2)
    if set(items) != CANONICAL_SCORE_KEYS:
        print(
            "ERROR: --scoring-json の items は4つの正規採点軸だけで指定してください。"
            f" 正規キー: {sorted(CANONICAL_SCORE_KEYS)}",
            file=sys.stderr,
        )
        sys.exit(2)
    for k, v in items.items():
        if not _finite_score(v):
            print(f"ERROR: --scoring-json の item '{k}'={v} は bool ではない有限の {SCORE_MIN}〜{SCORE_MAX} 数値で指定してください。", file=sys.stderr)
            sys.exit(2)
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        print("ERROR: --scoring-json の notes は文字列で指定してください。", file=sys.stderr)
        sys.exit(2)
    # open_high はキー欠如 (None) と明示 0 を区別する: 明示値は CLI --open-high より優先される
    open_high = payload.get("open_high")
    if open_high is not None and not _nonnegative_int(open_high):
        print("ERROR: --scoring-json の open_high は bool ではない 0 以上の整数で指定してください。", file=sys.stderr)
        sys.exit(2)
    findings_evidence_path = payload.get("findings_evidence_path")
    if findings_evidence_path is not None and not isinstance(findings_evidence_path, str):
        print("ERROR: --scoring-json の findings_evidence_path は文字列で指定してください。", file=sys.stderr)
        sys.exit(2)
    review_agreement = payload.get("review_agreement")
    if review_agreement is not None and not _finite_score(review_agreement):
        print("ERROR: --scoring-json の review_agreement は bool ではない有限の 0〜5 数値または null で指定してください。", file=sys.stderr)
        sys.exit(2)
    agreement_detail = payload.get("agreement_detail")
    if agreement_detail is not None and not isinstance(agreement_detail, dict):
        print("ERROR: --scoring-json の agreement_detail はオブジェクトで指定してください。", file=sys.stderr)
        sys.exit(2)
    return items, notes, open_high, payload


def _archive_scoring_json(
    cwd: Path, iteration: int, data: dict, entry: dict, payload: dict,
) -> _PublishedFile:
    """Archive scoring output under an immutable content-addressed name."""
    meta = {
        "session_id": data.get("session_id"),
        "agent": data.get("agent") or "unknown",
        "mission_id": data.get("mission_id"),
        "iteration": iteration,
        "timestamp": entry["timestamp"],
        "computed_composite": entry["composite"],
        "computed_min_item": entry["min_item"],
    }
    # This is the immutable object that binds what the scorer supplied to what
    # the state machine subsequently accepts.  Do not put the self-reference
    # (scoring_evidence_ref) in it: the digest names this exact byte sequence.
    out = {
        "schema": "mission-scoring-artifact/1",
        "_meta": meta,
        "binding": {
            "session_id": data.get("session_id"),
            "mission_id": data.get("mission_id"),
            "iteration": iteration,
            "items": entry["items"],
            "composite": entry["composite"],
            "min_item": entry["min_item"],
            "revision_scope": entry.get("revision_scope"),
            "review_generation": (entry.get("review_evidence_ref") or {}).get("generation"),
            "review_evidence_ref": entry.get("review_evidence_ref"),
        },
        "payload": payload,
    }
    # Keep the original top-level shape for historical archive consumers.  The
    # binding above, not these convenience fields, is authoritative.
    out.update(payload)
    content = (json.dumps(out, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    mission8 = str(data.get("mission_id") or "unknown")[:8]
    name = f"iter-{iteration}-{mission8}-scoring-{digest[:16]}.json"
    return _publish_review_archive_transaction(cwd, name, content)


_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ApprovalVerifier(Protocol):
    def __call__(self, request: dict) -> dict:
        ...


_APPROVAL_VERIFIERS: dict[str, ApprovalVerifier] = {}
_APPROVAL_VERIFIER_ENTRY_POINT_GROUP = "mission.approval_verifiers"
_APPROVAL_VERIFIER_REGISTRY_SCHEMA = "mission-approval-verifier-registry/2"
_APPROVAL_VERIFIER_REGISTRY_LIMIT = 64 * 1024
_APPROVAL_VERIFIER_TIMEOUT_SEC = 5
_APPROVAL_VERIFIER_TERMINATE_GRACE_SEC = 0.2
_EXECUTION_ISOLATOR_ENTRY_POINT_GROUP = "mission.execution_isolators"
_EXECUTION_ISOLATOR_REGISTRY_SCHEMA = "mission-execution-isolator-registry/1"
_EXECUTION_ISOLATOR_REQUIRED = frozenset({"filesystem-namespace", "readonly-mount", "env-reset", "network-policy"})


def register_approval_verifier(name: str, verifier: ApprovalVerifier) -> None:
    """Register a host-provided verifier. The portable CLI ships with none."""
    if not _APPROVAL_VERIFIER_NAME_RE.fullmatch(name) or not callable(verifier):
        raise ValueError("invalid approval verifier registration")
    _APPROVAL_VERIFIERS[name] = verifier


def _reject_duplicate_registry_key(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("approval verifier registry has duplicate keys")
        value[key] = item
    return value


def _read_approval_verifier_registry(path: Path) -> dict:
    """Read a bounded registry without following a file replacement or link."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("approval verifier registry cannot be read safely on this host")
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _APPROVAL_VERIFIER_REGISTRY_LIMIT:
                raise ValueError("approval verifier registry must be a bounded regular file")
            raw = os.read(fd, info.st_size + 1)
            if len(raw) != info.st_size or os.fstat(fd).st_size != info.st_size:
                raise ValueError("approval verifier registry changed while being read")
        finally:
            os.close(fd)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_registry_key)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("approval verifier registry is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "verifiers"} or value.get("schema") != _APPROVAL_VERIFIER_REGISTRY_SCHEMA:
        raise ValueError("approval verifier registry is invalid")
    verifiers = value.get("verifiers")
    if not isinstance(verifiers, list) or len(verifiers) > 64:
        raise ValueError("approval verifier registry is invalid")
    result = {}
    for item in verifiers:
        if not isinstance(item, dict) or set(item) != {"id", "entry_point", "distribution", "version", "source_digest"}:
            raise ValueError("approval verifier registry is invalid")
        identifier, entry_point = item.get("id"), item.get("entry_point")
        distribution, version, source_digest = item.get("distribution"), item.get("version"), item.get("source_digest")
        if (not isinstance(identifier, str) or not _APPROVAL_VERIFIER_NAME_RE.fullmatch(identifier)
                or not isinstance(entry_point, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", entry_point)
                or not isinstance(distribution, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", distribution)
                or not isinstance(version, str) or not version or len(version) > 128
                or not isinstance(source_digest, str) or not _SHA256_REF_RE.fullmatch(source_digest)
                or identifier in result):
            raise ValueError("approval verifier registry is invalid")
        result[identifier] = {"entry_point": entry_point, "distribution": distribution, "version": version, "source_digest": source_digest}
    return result


def _configured_execution_isolator(cwd: Path, isolator_name: str):
    """Resolve one host-only strict backend and pin its code and policy."""
    if not isinstance(isolator_name, str) or not _APPROVAL_VERIFIER_NAME_RE.fullmatch(isolator_name):
        raise ValueError("execution isolator is invalid")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    location = (Path(xdg) if xdg else Path.home() / ".config") / "mission" / "execution-isolators.json"
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("execution isolator registry cannot be read safely on this host")
    try:
        fd = os.open(os.fspath(location), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _APPROVAL_VERIFIER_REGISTRY_LIMIT:
                raise ValueError("execution isolator registry is invalid")
            raw = os.read(fd, info.st_size + 1)
            if len(raw) != info.st_size or os.fstat(fd).st_size != info.st_size:
                raise ValueError("execution isolator registry changed while being read")
        finally:
            os.close(fd)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_registry_key)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("execution isolator registry is invalid") from exc
    if not isinstance(value, dict) or set(value) != {"schema", "isolators"} or value.get("schema") != _EXECUTION_ISOLATOR_REGISTRY_SCHEMA:
        raise ValueError("execution isolator registry is invalid")
    configured = None
    for item in value.get("isolators", []):
        expected = {"id", "entry_point", "distribution", "version", "source_digest", "policy_digest", "enforced_capabilities"}
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("execution isolator registry is invalid")
        if item.get("id") == isolator_name:
            if configured is not None:
                raise ValueError("execution isolator registry has duplicate ids")
            configured = item
    if configured is None:
        return None
    if (not isinstance(configured.get("entry_point"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", configured["entry_point"])
            or not isinstance(configured.get("distribution"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", configured["distribution"])
            or not isinstance(configured.get("version"), str) or not configured["version"]
            or not isinstance(configured.get("source_digest"), str) or not _SHA256_REF_RE.fullmatch(configured["source_digest"])
            or not isinstance(configured.get("policy_digest"), str) or not _SHA256_REF_RE.fullmatch(configured["policy_digest"])
            or not isinstance(configured.get("enforced_capabilities"), list)
            or not _EXECUTION_ISOLATOR_REQUIRED.issubset(set(configured["enforced_capabilities"]))):
        raise ValueError("execution isolator registry is invalid")
    discovered = importlib.metadata.entry_points()
    candidates = (discovered.select(group=_EXECUTION_ISOLATOR_ENTRY_POINT_GROUP)
                  if hasattr(discovered, "select") else discovered.get(_EXECUTION_ISOLATOR_ENTRY_POINT_GROUP, ()))
    matches = [entry for entry in candidates if entry.name == configured["entry_point"]]
    if len(matches) != 1:
        raise ValueError("execution isolator entry point is not installed")
    entry = matches[0]; distribution = getattr(entry, "dist", None)
    if (distribution is None or str(distribution.metadata.get("Name") or "").lower() != configured["distribution"].lower()
            or str(distribution.version) != configured["version"]):
        raise ValueError("execution isolator distribution identity mismatch")
    module_name = getattr(entry, "module", "")
    module_spec = importlib.util.find_spec(module_name) if isinstance(module_name, str) else None
    origin = getattr(module_spec, "origin", None)
    if not isinstance(origin, str) or not origin:
        raise ValueError("execution isolator source is invalid")
    source = Path(origin).read_bytes()
    if "sha256:" + hashlib.sha256(source).hexdigest() != configured["source_digest"]:
        raise ValueError("execution isolator source digest mismatch")
    value = getattr(entry, "value", None)
    if not isinstance(value, str) or not value:
        raise ValueError("execution isolator entry point is invalid")
    attestation = {
        "schema": "execution-isolator/1", "backend_id": configured["id"], "version": configured["version"],
        "host_support": True, "policy_digest": configured["policy_digest"],
        "enforced_capabilities": sorted(configured["enforced_capabilities"]),
    }
    return {**configured, "module": module_name, "entry_point_value": value, "attestation": attestation}


def _run_strict_provider_backend(descriptor: dict, packet: bytes) -> dict:
    """Run only the host-pinned backend; no subprocess fallback exists."""
    entry_points = importlib.metadata.entry_points()
    candidates = (entry_points.select(group=_EXECUTION_ISOLATOR_ENTRY_POINT_GROUP)
                  if hasattr(entry_points, "select") else entry_points.get(_EXECUTION_ISOLATOR_ENTRY_POINT_GROUP, ()))
    matches = [entry for entry in candidates if entry.name == descriptor["entry_point"]]
    if len(matches) != 1 or getattr(matches[0], "value", None) != descriptor["entry_point_value"]:
        raise ProviderPreflightError("isolator-drift")
    backend = matches[0].load()
    result = dispatch_prepared_packet(
        {"isolation": "strict", "isolator": descriptor["attestation"], "ambient_scopes": []},
        descriptor["attestation"]["policy_digest"], packet,
        lambda _: (_ for _ in ()).throw(ProviderPreflightError("isolator-unavailable")),
        lambda _: (descriptor["attestation"], backend),
    )
    if not isinstance(result, dict) or type(result.get("returncode")) is not int:
        raise ProviderPreflightError("isolator-result-invalid")
    return result


def _configured_approval_entry_point(cwd: Path, verifier_name: str):
    """Return a pinned, non-executable verifier descriptor for a child to load."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    locations = [(Path(xdg) if xdg else Path.home() / ".config") / "mission" / "approval-verifiers.json"]
    configured = {}
    for location in locations:
        try:
            exists = location.exists()
        except OSError as exc:
            raise ValueError("approval verifier registry is invalid") from exc
        if not exists:
            continue
        for identifier, entry_point in _read_approval_verifier_registry(location).items():
            if identifier in configured:
                raise ValueError("approval verifier registry has duplicate verifier ids")
            configured[identifier] = entry_point
    configured_item = configured.get(verifier_name)
    if configured_item is None:
        return None
    discovered = importlib.metadata.entry_points()
    candidates = discovered.select(group=_APPROVAL_VERIFIER_ENTRY_POINT_GROUP) if hasattr(discovered, "select") else discovered.get(_APPROVAL_VERIFIER_ENTRY_POINT_GROUP, ())
    matches = [item for item in candidates if item.name == configured_item["entry_point"]]
    if len(matches) != 1:
        raise ValueError("approval verifier entry point is not installed")
    entry_point = matches[0]
    distribution = getattr(entry_point, "dist", None)
    if (distribution is None
            or str(distribution.metadata.get("Name") or "").lower() != configured_item["distribution"].lower()
            or str(distribution.version) != configured_item["version"]):
        raise ValueError("approval verifier distribution identity mismatch")
    module_name = getattr(entry_point, "module", "")
    if not isinstance(module_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module_name):
        raise ValueError("approval verifier entry point is invalid")
    module_spec = importlib.util.find_spec(module_name)
    origin = getattr(module_spec, "origin", None)
    if not isinstance(origin, str) or not origin or origin in {"built-in", "frozen"}:
        raise ValueError("approval verifier source is invalid")
    try:
        source = Path(origin).read_bytes()
    except OSError as exc:
        raise ValueError("approval verifier source is invalid") from exc
    if "sha256:" + hashlib.sha256(source).hexdigest() != configured_item["source_digest"]:
        raise ValueError("approval verifier source digest mismatch")
    # Never call EntryPoint.load() in the state-mutating parent.  Loading is
    # arbitrary provider code and belongs to the bounded child below.
    entry_point_value = getattr(entry_point, "value", None)
    if not isinstance(entry_point_value, str) or not entry_point_value:
        raise ValueError("approval verifier entry point is invalid")
    # Keep this immutable lookup result as a parent-side pin.  The child must
    # rediscover the same entry point before it calls load(), so metadata churn
    # cannot retarget a same-name provider during the handoff.
    return {**configured_item, "module": module_name, "entry_point_value": entry_point_value}


def _approval_verifier_child(verifier, request: dict, channel) -> None:
    try:
        # A verifier may create descendants; make this child their process
        # group leader so timeout cleanup has one bounded target.
        with contextlib.suppress(OSError):
            os.setsid()
        callback = verifier
        if isinstance(verifier, dict):
            discovered = importlib.metadata.entry_points()
            candidates = (discovered.select(group=_APPROVAL_VERIFIER_ENTRY_POINT_GROUP)
                          if hasattr(discovered, "select") else discovered.get(_APPROVAL_VERIFIER_ENTRY_POINT_GROUP, ()))
            matches = [item for item in candidates if item.name == verifier["entry_point"]]
            if len(matches) != 1:
                raise ValueError("approval verifier entry point is not installed")
            entry_point = matches[0]
            distribution = getattr(entry_point, "dist", None)
            if (distribution is None
                    or str(distribution.metadata.get("Name") or "").lower() != verifier["distribution"].lower()
                    or str(distribution.version) != verifier["version"]):
                raise ValueError("approval verifier distribution identity mismatch")
            module_name = getattr(entry_point, "module", "")
            if (module_name != verifier["module"]
                    or getattr(entry_point, "value", None) != verifier["entry_point_value"]):
                raise ValueError("approval verifier entry point changed after pinning")
            module_spec = importlib.util.find_spec(module_name)
            origin = getattr(module_spec, "origin", None)
            if not isinstance(origin, str) or "sha256:" + hashlib.sha256(Path(origin).read_bytes()).hexdigest() != verifier["source_digest"]:
                raise ValueError("approval verifier source digest mismatch")
            callback = entry_point.load()
        if not callable(callback):
            raise ValueError("approval verifier entry point is invalid")
        channel.send((True, callback(request)))
    except Exception:
        channel.send((False, None))
    finally:
        channel.close()


def _stop_approval_verifier_child(child) -> None:
    """Bound timeout cleanup even when provider code absorbs SIGTERM."""
    for signal_number, fallback in ((signal.SIGTERM, child.terminate), (signal.SIGKILL, child.kill)):
        if not child.is_alive():
            child.join()
            return
        with contextlib.suppress(OSError):
            os.killpg(child.pid, signal_number)
        if child.is_alive():
            with contextlib.suppress(OSError):
                fallback()
        child.join(_APPROVAL_VERIFIER_TERMINATE_GRACE_SEC)
    if not child.is_alive():
        child.join()


def _run_approval_verifier(verifier, request: dict) -> dict:
    """Execute verifier in a reaped child; timeouts cannot leave it running."""
    try:
        context = multiprocessing.get_context("fork")
    except ValueError as exc:
        raise ValueError("isolated approval verifier execution is unavailable on this host") from exc
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_approval_verifier_child, args=(verifier, request, sender))
    try:
        child.start()
        sender.close()
        child.join(_APPROVAL_VERIFIER_TIMEOUT_SEC)
        if child.is_alive():
            _stop_approval_verifier_child(child)
            raise ValueError("approval verifier timed out")
        if child.exitcode != 0 or not receiver.poll():
            raise ValueError("approval verifier rejected the evidence")
        success, result = receiver.recv()
        if not success or not isinstance(result, dict):
            raise ValueError("approval verifier rejected the evidence")
        return result
    finally:
        sender.close()
        receiver.close()
        if child.is_alive():
            _stop_approval_verifier_child(child)
        if not child.is_alive():
            child.close()


def verify_force_approval(request: dict, verifier_name: object, *, cwd: Path | None = None) -> dict:
    """Fail closed unless a registered callback returns a matching typed envelope."""
    if not isinstance(verifier_name, str) or not _APPROVAL_VERIFIER_NAME_RE.fullmatch(verifier_name):
        raise ValueError("approval verifier is invalid or not configured")
    verifier = _APPROVAL_VERIFIERS.get(verifier_name)
    descriptor = _configured_approval_entry_point(cwd, verifier_name) if verifier is None and cwd is not None else None
    if verifier is None and descriptor is None:
        raise ValueError("approval verifier is not configured")
    try:
        result = _run_approval_verifier(verifier if verifier is not None else descriptor, request)
    except Exception as exc:
        raise ValueError("approval verifier rejected the evidence") from exc
    try:
        envelope = {"request": request, "response": result, "receipt_ref": result.get("receipt_ref"), "consumed": True}
        validated = validate_recorded_envelope(envelope)
    except (AttributeError, ValueError) as exc:
        raise ValueError("approval verifier did not return a verified envelope") from exc
    if validated["response"]["verifier_id"] != verifier_name:
        raise ValueError("approval verifier did not return a verified envelope")
    return validated


def _force_envelope_replayed(cwd: Path, envelope: dict) -> bool:
    """Reject a request or receipt already consumed by any local session."""
    request_digest = envelope["request"]["request_digest"]
    receipt = envelope["receipt_ref"]
    sessions = state_dir(cwd) / "sessions"
    for candidate in sessions.glob("*.json"):
        try:
            other = json.loads(candidate.read_text(encoding="utf-8"))
            recorded = other.get("force_approval") if isinstance(other, dict) else None
            if not isinstance(recorded, dict):
                continue
            validated = validate_recorded_envelope(recorded)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            # A malformed record must never become a bypass; it is not a
            # reusable approved envelope, and audit will flag it separately.
            continue
        if (validated["request"]["request_digest"] == request_digest
                or validated["receipt_ref"] == receipt):
            return True
    return False


def _is_new_provenance_state(data: dict) -> bool:
    """Legacy terminal records are display-only; new writers do not consult this."""
    return isinstance(data.get("schema_version"), int) and data["schema_version"] >= 4


def _revision_scope_from_args(args) -> dict:
    base, head = getattr(args, "base_sha", None), getattr(args, "head_sha", None)
    if base is None and head is None:
        return {"kind": "not-applicable", "reason_code": "non-git"}
    if not (isinstance(base, str) and isinstance(head, str)
            and re.fullmatch(r"[0-9a-f]{40}", base) and re.fullmatch(r"[0-9a-f]{40}", head)):
        raise ValueError("git revision_scope requires exact 40-character --base-sha and --head-sha")
    return {"kind": "git", "base_sha": base, "head_sha": head}


def _validate_revision_scope(cwd: Path, scope: object) -> None:
    """Bind git scope to this checked-out project, never to a SHA-shaped claim."""
    if not isinstance(scope, dict):
        raise ValueError("revision_scope is invalid")
    git = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    is_git = git.returncode == 0
    if scope.get("kind") == "not-applicable":
        if scope != {"kind": "not-applicable", "reason_code": "non-git"} or is_git:
            raise ValueError("not-applicable revision_scope is allowed only for non-git projects")
        return
    if scope.get("kind") != "git":
        raise ValueError("revision_scope is invalid")
    if not is_git:
        raise ValueError("git revision_scope requires a git project")
    base, head = scope.get("base_sha"), scope.get("head_sha")
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
               for value in (base, head)):
        raise ValueError("git revision_scope must use exact SHAs")
    def checked(*command: str) -> str:
        result = subprocess.run(["git", "-C", str(cwd), *command], capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError("git revision_scope names an unknown commit")
        return result.stdout.strip()
    checked("cat-file", "-e", f"{base}^{{commit}}")
    checked("cat-file", "-e", f"{head}^{{commit}}")
    checked("merge-base", "--is-ancestor", base, head)
    if checked("rev-parse", "HEAD") != head:
        raise ValueError("git revision_scope head is not the current reviewed HEAD")


_REVIEW_LINEAGE_REF_FIELDS = ("review_group_id", "review_generation", "base_sha", "head_sha")


def _current_review_lineage(cwd: Path, data: dict, revision_scope: dict) -> dict | None:
    """Return the active review generation binding, or preserve pre-rollout state."""
    group = data.get("review_group_id")
    if group is None:
        return None
    generation = data.get("review_generation")
    base, head = data.get("base_sha"), data.get("head_sha")
    if (not isinstance(group, str) or not group or "\x00" in group
            or not isinstance(generation, int) or isinstance(generation, bool) or generation < 1
            or revision_scope.get("kind") != "git"
            or (base, head) != (revision_scope.get("base_sha"), revision_scope.get("head_sha"))):
        raise ValueError("review lineage state must bind a valid group, generation, and git revision")

    members = []
    try:
        for state_path in _iter_state_files(cwd):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("review_group_id") != group:
                continue
            member_generation = state.get("review_generation")
            if (not isinstance(member_generation, int) or isinstance(member_generation, bool)
                    or member_generation < 1):
                raise ValueError("review lineage group has an invalid generation")
            members.append(state)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("review lineage state is unreadable") from exc
    if not members:
        raise ValueError("review lineage group is missing")
    newest_generation = max(member["review_generation"] for member in members)
    newest = [member for member in members if member["review_generation"] == newest_generation]
    if len(newest) != 1:
        raise ValueError("review lineage group has no single active generation")
    current = newest[0]
    if (generation != newest_generation or current.get("session_id") != data.get("session_id")
            or current.get("passes") is not False or current.get("loop_active") is not True
            or current.get("terminal_outcome") is not None):
        raise ValueError("review lineage is not the current active generation")
    return {
        "review_group_id": group,
        "review_generation": generation,
        "base_sha": base,
        "head_sha": head,
    }


def _validate_review_lineage_ref(cwd: Path, data: dict, ref: dict, revision_scope: dict) -> None:
    """Bind an aggregate to the session's active review generation when present."""
    provided = [field in ref for field in _REVIEW_LINEAGE_REF_FIELDS]
    if any(provided) and not all(provided):
        raise ValueError("review lineage reference is incomplete")
    expected = _current_review_lineage(cwd, data, revision_scope)
    if expected is None:
        if any(provided):
            raise ValueError("review lineage reference is not allowed for a legacy session")
        return
    if not all(provided) or {field: ref[field] for field in _REVIEW_LINEAGE_REF_FIELDS} != expected:
        raise ValueError("review lineage reference does not bind the current generation")


def _validate_provenance(provenance: object, *, require: bool) -> dict | None:
    if provenance is None:
        if require:
            raise ValueError("structured score provenance is required")
        return None
    if not isinstance(provenance, dict):
        raise ValueError("score provenance must be an object")
    source = provenance.get("score_source")
    ref = provenance.get("review_evidence_ref")
    scope = provenance.get("revision_scope")
    if source not in {"scoring-json", "manual-import"}:
        raise ValueError("score provenance has invalid score_source")
    if source == "scoring-json":
        if not isinstance(ref, dict) or ref.get("kind") != "review-aggregate" or not isinstance(ref.get("path"), str) or not _SHA256_REF_RE.fullmatch(str(ref.get("digest") or "")) or not isinstance(ref.get("generation"), str) or not isinstance(ref.get("revision_scope"), dict):
            raise ValueError("score provenance has invalid review_evidence_ref")
        lineage_fields = [field in ref for field in _REVIEW_LINEAGE_REF_FIELDS]
        if any(lineage_fields) and (
                not all(lineage_fields)
                or not isinstance(ref.get("review_group_id"), str)
                or not ref["review_group_id"]
                or "\x00" in ref["review_group_id"]
                or not isinstance(ref.get("review_generation"), int)
                or isinstance(ref["review_generation"], bool)
                or ref["review_generation"] < 1
                or not all(isinstance(ref.get(field), str) and re.fullmatch(r"[0-9a-f]{40}", ref[field])
                           for field in ("base_sha", "head_sha"))):
            raise ValueError("score provenance has invalid review lineage reference")
        if not isinstance(scope, dict) or scope != ref["revision_scope"]:
            raise ValueError("score provenance revision_scope mismatch")
    else:
        # Manual imports intentionally do not reuse reviewer aggregates.  The
        # typed capture is the only supported host-file route.
        manual = provenance.get("manual_evidence_ref")
        if ref is not None:
            raise ValueError("manual-import must not contain review_evidence_ref")
        if (not isinstance(manual, dict) or manual.get("kind") != "manual-score"
                or not isinstance(manual.get("path"), str)
                or not _SHA256_REF_RE.fullmatch(str(manual.get("digest") or ""))
                or not isinstance(manual.get("generation"), str)
                or not isinstance(manual.get("revision_scope"), dict)):
            raise ValueError("score provenance has invalid manual_evidence_ref")
        if not isinstance(scope, dict) or scope != manual["revision_scope"]:
            raise ValueError("manual-import revision_scope mismatch")
    if not isinstance(provenance.get("scoring_evidence_ref"), dict) and require:
        # push-score adds this after archiving; raw aggregate output has none.
        pass
    return provenance


def _manual_score_binding(data: dict, entry: dict, payload: dict) -> dict:
    """The complete claim a manually supplied score is permitted to carry."""
    return {
        "session_id": data.get("session_id"), "mission_id": data.get("mission_id"),
        "iteration": entry.get("iteration"), "items": entry.get("items"),
        "composite": entry.get("composite"), "min_item": entry.get("min_item"),
        "review_agreement": entry.get("review_agreement"),
        "open_high": entry.get("open_high"), "revision_scope": payload.get("revision_scope"),
        "source_evidence_ref": payload.get("source_evidence_ref"),
    }


def _validate_manual_score_payload(payload: object, data: dict, entry: dict) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != "mission-manual-score/1":
        raise ValueError("manual score evidence has invalid schema")
    if "review_evidence_ref" in payload or payload.get("manual_evidence_ref") is not None:
        raise ValueError("manual score evidence must not use review aggregate references")
    required = {"schema", "session_id", "mission_id", "iteration", "items", "composite", "min_item",
                "review_agreement", "open_high", "revision_scope", "source_evidence_ref", "imported_at", "input_digest"}
    if set(payload) != required:
        raise ValueError("manual score evidence has invalid fields")
    if not isinstance(payload.get("imported_at"), str) or not payload["imported_at"].strip():
        raise ValueError("manual score evidence imported_at is invalid")
    items = payload.get("items")
    if not isinstance(items, dict) or set(items) != CANONICAL_SCORE_KEYS:
        raise ValueError("manual score items are invalid")
    for axis in REVIEW_SCORE_KEYS:
        if not _finite_score(items[axis]):
            raise ValueError(f"manual score item {axis} must be a finite in-range number")
    for label in ("composite", "min_item", "review_agreement"):
        if not _finite_score(payload.get(label)):
            raise ValueError(f"manual score {label} must be a finite in-range number")
    if not _nonnegative_int(payload.get("open_high")):
        raise ValueError("manual score open_high must be a non-negative integer")
    source = payload.get("source_evidence_ref")
    if (not isinstance(source, dict) or set(source) != {"kind", "ref", "digest"}
            or source.get("kind") != "manual-source-evidence"
            or not _SHA256_REF_RE.fullmatch(str(source.get("ref") or ""))
            or source.get("ref") != source.get("digest")):
        raise ValueError("manual score evidence source reference is invalid")
    claimed_digest = payload.get("input_digest")
    unsigned = {key: value for key, value in payload.items() if key != "input_digest"}
    if claimed_digest != provenance_digest(unsigned):
        raise ValueError("manual score evidence input digest mismatch")
    if _manual_score_binding(data, entry, payload) != {
        key: payload.get(key) for key in _manual_score_binding(data, entry, payload)
    }:
        raise ValueError("manual score evidence binding mismatch")


def _read_bounded_review_evidence(cwd: Path, path_text: object) -> bytes:
    """Read a state-local evidence file through one descriptor chain, without TOCTOU."""
    try:
        return read_score_provenance_evidence(cwd, path_text)
    except ValueError as exc:
        raise ValueError(str(exc).replace("score provenance evidence", "review evidence")) from exc


def _read_bounded_manual_input(path_text: object) -> bytes:
    """Safely capture one host-provided manual-score JSON file before archiving."""
    if not isinstance(path_text, str) or not path_text or "\x00" in path_text:
        raise ValueError("manual score input path is invalid")
    try:
        fd = os.open(path_text, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        try:
            initial = os.fstat(fd)
            if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or initial.st_size > 4 * 1024 * 1024:
                raise ValueError("manual score input must be a bounded regular non-symlink file")
            content = os.read(fd, initial.st_size)
            final = os.fstat(fd)
            current = os.stat(path_text, follow_symlinks=False)
            identity = lambda metadata: (
                metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
                metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
            )
            if (
                len(content) != initial.st_size
                or os.read(fd, 1)
                or identity(final) != identity(initial)
                or identity(current) != identity(initial)
            ):
                raise ValueError("manual score input changed while being read")
            return content
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("manual score input path is invalid") from exc


def cmd_manual_score_capture(args):
    """Capture a typed manual score into the only manual-import evidence route."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(2)
    try:
        content = _read_bounded_manual_input(args.input)
        payload = json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"ERROR: manual score input: {exc}", file=sys.stderr)
        sys.exit(2)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text(encoding="utf-8"))
        entry = {
            "iteration": payload.get("iteration"), "items": payload.get("items"),
            "composite": payload.get("composite"), "min_item": payload.get("min_item"),
            "review_agreement": payload.get("review_agreement"),
            "open_high": payload.get("open_high"),
        }
        try:
            _validate_manual_score_payload(payload, data, entry)
            _validate_revision_scope(cwd, payload["revision_scope"])
            if (not isinstance(entry["iteration"], int) or isinstance(entry["iteration"], bool)
                    or entry["iteration"] < 1 or entry["iteration"] != data.get("iteration")
                    or set(entry["items"] or {}) != CANONICAL_SCORE_KEYS):
                raise ValueError("manual score iteration or items are invalid")
            values = [float(entry["items"][axis]) for axis in REVIEW_SCORE_KEYS]
            if entry["composite"] != round(sum(values) / len(values), 2) or entry["min_item"] != round(min(values), 2):
                raise ValueError("manual score composite or min_item is not derived from four axes")
        except (TypeError, ValueError) as exc:
            print(f"ERROR: manual score input: {exc}", file=sys.stderr)
            sys.exit(2)
        digest = hashlib.sha256(content).hexdigest()
        mission8 = str(data.get("mission_id") or "unknown")[:8]
        archive = state_dir(cwd) / "archive" / f"iter-{entry['iteration']}-{mission8}-manual-{digest[:16]}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists() and _sha256_file(archive) != digest:
            print("ERROR: immutable manual score archive collision", file=sys.stderr)
            sys.exit(2)
        if not archive.exists():
            atomic_write_bytes(archive, content)
        ref = {
            "kind": "manual-score", "path": str(archive.relative_to(cwd)),
            "digest": "sha256:" + digest, "generation": digest[:16],
            "revision_scope": payload["revision_scope"],
        }
        scoring = {
            "items": entry["items"], "open_high": entry["open_high"],
            "review_agreement": entry["review_agreement"],
            "score_provenance": {"score_source": "manual-import", "manual_evidence_ref": ref,
                                  "revision_scope": payload["revision_scope"]},
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out, scoring)
    print(json.dumps({"ok": True, "scoring_json": str(out), "manual_evidence_ref": ref}, ensure_ascii=False))


def _revalidate_score_provenance(cwd: Path, entry: dict, data: dict, *, require_scoring_artifact: bool = True) -> None:
    """Re-check immutable evidence at the pass boundary; never trust a saved digest alone."""
    # A live pass is always a new decision.  Historical schema versions are
    # display compatibility only and must never relax its evidence boundary.
    provenance = _validate_provenance(entry.get("score_provenance"), require=True)
    if provenance is None:
        return
    is_manual = provenance["score_source"] == "manual-import"
    ref = provenance["manual_evidence_ref"] if is_manual else provenance["review_evidence_ref"]
    _validate_revision_scope(cwd, provenance["revision_scope"])
    if not is_manual:
        _validate_review_lineage_ref(cwd, data, ref, provenance["revision_scope"])
    content = _read_bounded_review_evidence(cwd, ref["path"])
    digest = hashlib.sha256(content).hexdigest()
    if "sha256:" + digest != ref["digest"]:
        raise ValueError("review evidence digest mismatch")
    if ref["generation"] != digest[:16]:
        raise ValueError("review evidence generation mismatch")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("review evidence must be valid UTF-8 JSON") from exc
    if is_manual:
        _validate_manual_score_payload(parsed, data, entry)
        score_ref = provenance.get("scoring_evidence_ref")
        if require_scoring_artifact:
            if not isinstance(score_ref, dict) or score_ref.get("kind") != "scoring-artifact" or not _SHA256_REF_RE.fullmatch(str(score_ref.get("digest") or "")):
                raise ValueError("scoring evidence reference is invalid")
            score_bytes = _read_bounded_review_evidence(cwd, score_ref.get("path"))
            if "sha256:" + hashlib.sha256(score_bytes).hexdigest() != score_ref["digest"]:
                raise ValueError("scoring evidence digest mismatch")
        return
    if not isinstance(parsed, dict) or parsed.get("schema") != "mission-review-aggregate/1":
        raise ValueError("review evidence has invalid schema")
    try:
        archive_iteration = parsed.get("iteration")
        expected_iteration = entry.get("iteration")
        if (isinstance(archive_iteration, bool) or not isinstance(archive_iteration, int)
                or archive_iteration < 1 or archive_iteration != expected_iteration):
            raise ValueError("review evidence iteration mismatch")
        derived = reduce_review_aggregate(parsed.get("inputs"), expected_iteration=expected_iteration)
    except ValueError as exc:
        raise ValueError(f"review evidence inputs are invalid: {exc}") from exc
    claim = parsed.get("score_claim")
    expected_claim = {
        "iteration": entry.get("iteration"), "items": entry.get("items"),
        "composite": entry.get("composite"), "min_item": entry.get("min_item"),
        "open_high": entry.get("open_high"), "review_agreement": entry.get("review_agreement"),
        "agreement_detail": entry.get("agreement_detail"),
    }
    derived_claim = {"iteration": expected_iteration, **derived}
    if claim != derived_claim:
        raise ValueError("review evidence score claim is absent or not derived from inputs")
    if expected_claim != derived_claim:
        raise ValueError("review evidence score claim mismatch")
    score_ref = provenance.get("scoring_evidence_ref")
    if require_scoring_artifact:
        if not isinstance(score_ref, dict) or score_ref.get("kind") != "scoring-artifact" or not _SHA256_REF_RE.fullmatch(str(score_ref.get("digest") or "")):
            raise ValueError("scoring evidence reference is invalid")
        score_bytes = _read_bounded_review_evidence(cwd, score_ref.get("path"))
        if "sha256:" + hashlib.sha256(score_bytes).hexdigest() != score_ref["digest"]:
            raise ValueError("scoring evidence digest mismatch")
        try:
            artifact = json.loads(score_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("scoring evidence must be valid UTF-8 JSON") from exc
        binding = artifact.get("binding") if isinstance(artifact, dict) else None
        expected = {
            "session_id": data.get("session_id"), "mission_id": data.get("mission_id"),
            "iteration": entry.get("iteration"), "items": entry.get("items"),
            "composite": entry.get("composite"), "min_item": entry.get("min_item"),
            "revision_scope": provenance["revision_scope"], "review_generation": ref["generation"],
            "review_evidence_ref": ref,
        }
        if artifact.get("schema") != "mission-scoring-artifact/1" or binding != expected:
            raise ValueError("scoring evidence binding mismatch")


def _resolve_recorded_path(cwd: Path, path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    return cwd / path


def _count_high_findings_in_evidence(cwd: Path, path_text: str) -> int:
    path = _resolve_recorded_path(cwd, path_text)
    if not (path.exists() and path.is_file()):
        print(f"ERROR: findings evidence file is missing: {path_text}", file=sys.stderr)
        sys.exit(2)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"ERROR: findings evidence JSON is invalid: {path_text}: {e}", file=sys.stderr)
        sys.exit(2)
    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        print("ERROR: findings evidence is missing inputs list", file=sys.stderr)
        sys.exit(2)
    high = 0
    for review in inputs:
        if not isinstance(review, dict):
            print("ERROR: findings evidence contains invalid review entry", file=sys.stderr)
            sys.exit(2)
        findings = review.get("findings") or []
        if not isinstance(findings, list):
            print("ERROR: findings evidence contains invalid findings list", file=sys.stderr)
            sys.exit(2)
        high += sum(1 for finding in findings if isinstance(finding, dict) and finding.get("severity") == "High")
    return high


def _validate_findings_evidence_gate(cwd: Path, latest: dict) -> None:
    source = latest.get("score_source")
    if source != "scoring-json":
        print(
            "WARNING: legacy score entry has no machine findings evidence; using stored open_high only.",
            file=sys.stderr,
        )
        return
    path_text = latest.get("findings_evidence_path")
    if not path_text:
        print(
            "ERROR: score_source=scoring-json なのに High findings evidence の findings_evidence_path がありません。"
            " aggregate-reviews の出力を push-score --scoring-json に渡してください。",
            file=sys.stderr,
        )
        sys.exit(2)
    evidence_high = _count_high_findings_in_evidence(cwd, path_text)
    open_high = latest.get("open_high")
    if open_high != evidence_high:
        print(
            f"ERROR: findings evidence の High 件数 ({evidence_high}) と score entry の open_high ({open_high}) が一致しません。",
            file=sys.stderr,
        )
        sys.exit(2)


def _max_agreement_delta(latest: dict) -> tuple[str | None, float | None]:
    detail = latest.get("agreement_detail")
    if not isinstance(detail, dict) or not detail:
        return None, None
    max_axis = None
    max_delta = None
    for axis, value in detail.items():
        if not isinstance(value, dict):
            continue
        delta = value.get("delta")
        if not isinstance(delta, (int, float)) or math.isnan(float(delta)):
            continue
        delta = float(delta)
        if max_delta is None or delta > max_delta:
            max_axis = str(axis)
            max_delta = delta
    return max_axis, max_delta


def _validate_review_agreement_gate(latest: dict) -> None:
    axis, delta = _max_agreement_delta(latest)
    if delta is None:
        return
    if delta > 1.5:
        print(
            f"ERROR: 低合意: 争点軸 {axis} の追加レビュー 1 名を実施して再集計してください (max-min={delta:.2f})",
            file=sys.stderr,
        )
        sys.exit(2)
    if delta > 1.0:
        print(
            f"WARNING: reviewer agreement is low on {axis} (max-min={delta:.2f}); consider one additional review.",
            file=sys.stderr,
        )


def _review_error(path: Path, message: str) -> None:
    print(f"ERROR: {path}: {message}", file=sys.stderr)
    sys.exit(2)


def _review_prose_bytes(text: str) -> int:
    """Count non-template prose lines outside the structured review JSON."""
    prose_lines = [
        line for line in text.splitlines()
        if line.strip() and not REVIEW_TEMPLATE_HEADING_RE.fullmatch(line)
    ]
    return len("\n".join(prose_lines).encode("utf-8"))


MAX_REVIEW_INPUT_BYTES = 4 * 1024 * 1024
COMMAND_OUTCOME_KINDS = frozenset({"ok", "expected-gate", "invalid-input", "external", "internal-error"})
COMMAND_OUTCOME_LIMIT = 128
_OUTCOME_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class CommandOutcomeInputError(ValueError):
    """Opaque command lineage input failed validation."""


class CommandOutcomeExit(SystemExit):
    """A legacy-compatible exit carrying the centralized outcome taxonomy."""

    def __init__(self, code: int, outcome_kind: str, *, guidance: list[str] | None = None):
        super().__init__(code)
        self.outcome_kind = outcome_kind
        self.guidance = guidance or None


def _provider_gate(reason_code: str) -> None:
    print(f"ERROR: provider-ineligible: {reason_code}", file=sys.stderr)
    error = CommandOutcomeExit(2, "expected-gate")
    error.provider_reason_code = reason_code
    raise error


def _guidance_context_for_state(data: dict | None = None, **extra) -> dict:
    context: dict = {}
    if isinstance(data, dict):
        for key in (
            "phase",
            "iteration",
            "reviewer_count",
            "session_id",
            "planning_strategy",
            "planning_provider_required",
        ):
            if key in data:
                context[key] = data.get(key)
        review_refs = data.get("review_evidence_refs")
        if isinstance(review_refs, list):
            for ref in reversed(review_refs):
                if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                    context.setdefault("latest_review_input_path", ref["path"])
                    context.setdefault("latest_review_input_ref", ref["path"])
                    break
        canonical_plan = data.get("canonical_plan")
        if isinstance(canonical_plan, dict) and isinstance(canonical_plan.get("path"), str):
            context.setdefault("canonical_plan_path", canonical_plan["path"])
    context.update({k: v for k, v in extra.items() if v is not None})
    return context


def _raise_guided_failure(
    message: str,
    *,
    command: str,
    reason: str,
    context: dict | None = None,
    outcome_kind: str = "invalid-input",
    exit_code: int = 2,
) -> None:
    guidance = build_guidance(command, reason, context or {})
    print(f"ERROR: {message}", file=sys.stderr)
    for line in guidance:
        print(line, file=sys.stderr)
    raise CommandOutcomeExit(exit_code, outcome_kind, guidance=guidance)


def _command_outcome(args: argparse.Namespace, command: str, outcome_kind: str) -> dict:
    """Build bounded, locator-free command lineage for state and JSON consumers."""
    if outcome_kind not in COMMAND_OUTCOME_KINDS:
        raise CommandOutcomeInputError("command outcome kind is invalid")
    provided_event = getattr(args, "event_id", None)
    if provided_event is not None and not _valid_command_outcome_identifier(provided_event):
        raise CommandOutcomeInputError("command event_id is invalid")
    event_id = provided_event or secrets.token_hex(16)
    provided_root = getattr(args, "root_event_id", None)
    if provided_root is not None and not _valid_command_outcome_identifier(provided_root):
        raise CommandOutcomeInputError("command root_event_id is invalid")
    root_event_id = provided_root or event_id
    attempt = getattr(args, "attempt", 1)
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise CommandOutcomeInputError("command attempt is invalid")
    outcome = {
        "event_id": event_id,
        "root_event_id": root_event_id,
        "attempt": attempt,
        "command": command,
        "outcome_kind": outcome_kind,
    }
    retry_of = getattr(args, "retry_of", None)
    if retry_of is not None and not _valid_command_outcome_identifier(retry_of):
        raise CommandOutcomeInputError("command retry_of is invalid")
    if retry_of is not None:
        outcome["retry_of"] = retry_of
    return outcome


def _append_command_outcome(data: dict, outcome: dict) -> None:
    """Keep command classification bounded; business writes call this under their lock."""
    append_command_outcome_state(data, outcome)


def _add_command_lineage_arguments(parser: argparse.ArgumentParser) -> None:
    """Expose retry lineage without accepting prompts, paths, or other raw input."""
    parser.add_argument("--event-id", default=None, help="opaque command event identifier")
    parser.add_argument("--root-event-id", default=None, help="opaque root event identifier")
    parser.add_argument("--attempt", type=int, default=1, help="positive retry attempt number")
    parser.add_argument("--retry-of", default=None, help="opaque prior event identifier")


def _record_command_outcome_only(cwd: Path, outcome: dict) -> None:
    """Persist a bounded failure classification without touching state bytes.

    This is a materialized telemetry view, not the lifecycle journal reserved
    for later work.  Its schema is deliberately small and contains only the
    opaque command lineage produced above.
    """
    session_token = hashlib.sha256(resolve_session_id().encode("utf-8")).hexdigest()[:16]
    try:
        append_command_outcome_sidecar(state_dir(cwd), session_token, outcome)
    except OutcomeStoreError:
        # The command remains rejected.  Do not recover by accepting a corrupt
        # sidecar or following a hostile path; readers surface this telemetry.
        return


def _emit_json_command_failure(args: argparse.Namespace, outcome: dict, guidance: list[str] | None = None) -> None:
    args.command_outcome_emitted = True
    if getattr(args, "json", False):
        payload = {"ok": False, "outcome_kind": outcome["outcome_kind"], "outcome": outcome}
        if guidance:
            payload["guidance"] = guidance
            for line in guidance:
                print(line, file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False))


def _nested_command_failure_kind(stdout: str, error: SystemExit) -> str:
    kind = getattr(error, "outcome_kind", None)
    if kind in COMMAND_OUTCOME_KINDS:
        return kind
    try:
        payload = json.loads(stdout)
        kind = payload.get("outcome_kind") if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        kind = None
    return kind if kind in COMMAND_OUTCOME_KINDS else "invalid-input"


def _emit_finalize_failure(
    args: argparse.Namespace, stdout: str, error: SystemExit, *, site: str = "aggregate"
) -> None:
    kind = _nested_command_failure_kind(stdout, error)
    failure = _command_outcome(args, "review-finalize", kind)
    guidance = getattr(error, "guidance", None) or None
    guidance_printed_at_site = guidance is not None
    state_data = {}
    state_file = resolve_state_file(Path.cwd())
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text())
        except (OSError, json.JSONDecodeError):
            state_data = {}
    context = _guidance_context_for_state(
        state_data,
        iteration=args.iteration,
        review_evidence_refs=[
            {"path": ref} for ref in (getattr(args, "input_refs", []) or [])
            if isinstance(ref, str)
        ],
    )
    if guidance is None:
        if site == "resubmit":
            guidance = build_guidance("review-finalize", "resubmit-reason-missing", context)
        elif not (getattr(args, "input", None) or getattr(args, "input_refs", None)):
            guidance = build_guidance("review-finalize", "missing-input-ref", context)
    if guidance:
        failure["guidance"] = True
        if not guidance_printed_at_site:
            for line in guidance:
                print(line, file=sys.stderr)
    _record_command_outcome_only(Path.cwd(), failure)
    args.command_outcome_emitted = True
    payload = {
        "ok": False,
        "outcome_kind": kind,
        "outcome": failure,
    }
    if guidance:
        payload["guidance"] = guidance
    print(json.dumps(payload, ensure_ascii=False))


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    """Return the stable identity fields used for hostile review input reads."""
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _read_strict_review_file(source: Path) -> bytes:
    """Read one bounded regular review input without following its final path."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(os.fspath(source), os.O_RDONLY | os.O_NONBLOCK | nofollow)
    except OSError as exc:
        raise ValueError("review input is unavailable") from exc
    try:
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or initial.st_size > MAX_REVIEW_INPUT_BYTES
        ):
            raise ValueError("review input must be a bounded regular non-linked file")
        chunks: list[bytes] = []
        remaining = initial.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise ValueError("review input changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1) or _stat_identity(os.fstat(fd)) != _stat_identity(initial):
            raise ValueError("review input changed while being read")
        try:
            named = os.lstat(source)
        except OSError as exc:
            raise ValueError("review input changed while being read") from exc
        if _stat_identity(named) != _stat_identity(initial):
            raise ValueError("review input changed while being read")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError("review input is unavailable") from exc
    finally:
        os.close(fd)


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_mode == second.st_mode
        and first.st_nlink == second.st_nlink
    )


def _stat_like_from_identity(identity: tuple[int, int, int, int, int, int, int]) -> SimpleNamespace:
    return SimpleNamespace(
        st_dev=identity[0],
        st_ino=identity[1],
        st_mode=identity[2],
        st_nlink=identity[3],
        st_size=identity[4],
        st_mtime_ns=identity[5],
        st_ctime_ns=identity[6],
    )


class _PublishStatLike(Protocol):
    st_dev: int
    st_ino: int
    st_mode: int
    st_nlink: int
    st_size: int
    st_mtime_ns: int
    st_ctime_ns: int


def _publish_directory_detail(
    expected: tuple[int, int, int],
    opened: os.stat_result,
    named: os.stat_result,
    *,
    reason: str,
) -> str:
    parts = [f"reason={reason}"]
    expected_dev, expected_ino, expected_mode = expected
    parts.extend((
        f"expected_dev={expected_dev}",
        f"expected_ino={expected_ino}",
        f"expected_mode={oct(expected_mode)}",
        f"opened_dev={opened.st_dev}",
        f"opened_ino={opened.st_ino}",
        f"opened_mode={oct(opened.st_mode)}",
        f"named_dev={named.st_dev}",
        f"named_ino={named.st_ino}",
        f"named_mode={oct(named.st_mode)}",
    ))
    return " ".join(parts)


def _publish_identity_detail(
    expected: _PublishStatLike,
    observed: _PublishStatLike,
    *,
    reason: str,
    expected_size: int | None = None,
) -> str:
    parts = [
        f"reason={reason}",
        f"expected_dev={expected.st_dev}",
        f"expected_ino={expected.st_ino}",
        f"expected_mode={oct(expected.st_mode)}",
        f"expected_nlink={expected.st_nlink}",
        f"observed_dev={observed.st_dev}",
        f"observed_ino={observed.st_ino}",
        f"observed_mode={oct(observed.st_mode)}",
        f"observed_nlink={observed.st_nlink}",
    ]
    if expected_size is not None:
        parts.append(f"expected_size={expected_size}")
        parts.append(f"observed_size={observed.st_size}")
    return " ".join(parts)


def _publish_first_mismatch_reason(
    expected: _PublishStatLike,
    observed: _PublishStatLike,
    fields: tuple[str, ...],
    *,
    default: str,
) -> str:
    for name in fields:
        if getattr(expected, name) != getattr(observed, name):
            return name[3:]
    return default


def _verify_review_archive_directory(directory_fd: int, archive_path: Path) -> None:
    try:
        opened = os.fstat(directory_fd)
        named = archive_path.lstat()
    except OSError as exc:
        raise ValueError("review archive directory changed") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or opened.st_dev != named.st_dev
        or opened.st_ino != named.st_ino
        or opened.st_mode != named.st_mode
    ):
        raise ValueError("review archive directory changed")


def _open_review_archive_directory(cwd: Path) -> tuple[int, Path]:
    root = state_dir(cwd)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(os.fspath(root), flags)
    except OSError as exc:
        raise ValueError("review state directory is unsafe") from exc
    archive_fd: int | None = None
    try:
        try:
            archive_fd = os.open("archive", flags, dir_fd=root_fd)
        except FileNotFoundError:
            try:
                os.mkdir("archive", 0o700, dir_fd=root_fd)
                archive_fd = os.open("archive", flags, dir_fd=root_fd)
            except OSError as exc:
                raise ValueError("review archive directory is unsafe") from exc
        opened = os.fstat(archive_fd)
        named = os.stat("archive", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(opened.st_mode) or not _same_inode(opened, named):
            os.close(archive_fd)
            raise ValueError("review archive directory changed")
        result = archive_fd
        archive_fd = None
        return result, root / "archive"
    except OSError as exc:
        raise ValueError("review archive directory is unsafe") from exc
    finally:
        if archive_fd is not None:
            os.close(archive_fd)
        os.close(root_fd)


def _read_review_archive_at(
    directory_fd: int, name: str,
) -> tuple[bytes, tuple[int, int, int, int, int, int, int]] | None:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("review archive evidence is unsafe") from exc
    try:
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or initial.st_size > MAX_REVIEW_INPUT_BYTES
        ):
            raise ValueError("review archive evidence is unsafe")
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _stat_identity(initial) != _stat_identity(named):
            raise ValueError("review archive evidence changed")
        chunks: list[bytes] = []
        remaining = initial.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise ValueError("review archive evidence changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ValueError("review archive evidence changed")
        after = os.fstat(fd)
        named_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _stat_identity(initial) != _stat_identity(after) or _stat_identity(after) != _stat_identity(named_after):
            raise ValueError("review archive evidence changed")
        return b"".join(chunks), _stat_identity(after)
    except OSError as exc:
        raise ValueError("review archive evidence is unsafe") from exc
    finally:
        os.close(fd)


class _PublishedFile(NamedTuple):
    path: Path
    created: bool
    directory_fd: int
    directory_identity: tuple[int, int, int]
    object_identity: tuple[int, int, int, int, int, int, int]
    previous_content: bytes | None = None


class PublishedRollbackRecoveryError(ValueError):
    """A rollback failed but left a content-verifiable recovery file."""

    def __init__(self, recovery_ref: dict):
        basename = recovery_ref.get("basename") if isinstance(recovery_ref, dict) else None
        digest = recovery_ref.get("digest") if isinstance(recovery_ref, dict) else None
        size = recovery_ref.get("size") if isinstance(recovery_ref, dict) else None
        if (
            not isinstance(recovery_ref, dict)
            or set(recovery_ref) != {"basename", "digest", "size"}
            or not isinstance(basename, str)
            or re.fullmatch(r"[A-Za-z0-9._-]{1,200}", basename) is None
            or not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ValueError("published file recovery reference is invalid")
        super().__init__("published file rollback requires recovery")
        self.recovery_ref = dict(recovery_ref)


class _PublishAttempt:
    """Tracks syscall outcome without confusing FileExists with our publish."""

    def __init__(self) -> None:
        self.attempted = False
        self.conflict = False
        self.completed = False

    def owns_named_target(
        self, directory_fd: int, name: str, temporary_stat: os.stat_result,
    ) -> bool:
        if self.completed:
            return True
        if not self.attempted or self.conflict:
            return False
        try:
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            return False
        return (
            named.st_dev == temporary_stat.st_dev
            and named.st_ino == temporary_stat.st_ino
            and named.st_mode == temporary_stat.st_mode
            and named.st_size == temporary_stat.st_size
        )


@contextlib.contextmanager
def _defer_publish_signals():
    """Close the syscall/ownership gap where pthread signal masks exist."""
    mask = getattr(signal, "pthread_sigmask", None)
    previous_mask = None
    if mask is not None:
        try:
            previous_mask = mask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        except (OSError, ValueError):
            previous_mask = None
    try:
        yield
    finally:
        if previous_mask is not None:
            mask(signal.SIG_SETMASK, previous_mask)


def _directory_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode


def _same_publish_target(first: Path, second: Path) -> bool:
    if first.name != second.name:
        return False
    try:
        first_parent = first.parent.resolve(strict=True).stat()
        second_parent = second.parent.resolve(strict=True).stat()
    except OSError:
        return False
    return _directory_identity(first_parent) == _directory_identity(second_parent)


def _verify_published_file(published: _PublishedFile) -> None:
    try:
        opened_parent = os.fstat(published.directory_fd)
        named_parent = published.path.parent.lstat()
        if (
            not stat.S_ISDIR(opened_parent.st_mode)
            or _directory_identity(opened_parent) != published.directory_identity
            or _directory_identity(named_parent) != published.directory_identity
        ):
            raise ValueError("published file directory changed")
        named = os.stat(
            published.path.name,
            dir_fd=published.directory_fd,
            follow_symlinks=False,
        )
        if _stat_identity(named) != published.object_identity:
            raise ValueError("published file changed")
    except OSError as exc:
        raise ValueError("published file changed") from exc


def _write_temp_at(directory_fd: int, name: str, content: bytes) -> tuple[str, os.stat_result]:
    temporary = ""
    fd: int | None = None
    try:
        for _attempt in range(32):
            temporary = f".{name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                continue
        if fd is None:
            raise ValueError("publish temporary file is unavailable")
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1 or stat.S_IMODE(initial.st_mode) != 0o600:
            raise ValueError("publish temporary file is unsafe")
        offset = 0
        while offset < len(content):
            written = os.write(fd, content[offset:])
            if written <= 0:
                raise ValueError("publish write failed")
            offset += written
        os.fsync(fd)
        current = os.fstat(fd)
        named = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if current.st_size != len(content) or not _same_inode(initial, current) or not _same_inode(current, named):
            raise ValueError("publish temporary file changed")
        result = temporary, current
        temporary = ""
        return result
    finally:
        if fd is not None:
            os.close(fd)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _verify_restore_content_at(
    directory_fd: int,
    name: str,
    expected_stat: os.stat_result,
    expected_content: bytes,
    *,
    allow_rename_ctime: bool,
) -> None:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError("published file restore changed") from exc
    try:
        initial = os.fstat(fd)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        initial_identity = _stat_identity(initial)
        expected_identity = _stat_identity(expected_stat)
        identity_matches = (
            initial_identity[:6] == expected_identity[:6]
            if allow_rename_ctime
            else initial_identity == expected_identity
        )
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or not identity_matches
            or initial_identity != _stat_identity(named)
            or initial.st_size != len(expected_content)
        ):
            raise ValueError("published file restore changed")
        chunks: list[bytes] = []
        remaining = len(expected_content)
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise ValueError("published file restore changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ValueError("published file restore changed")
        content = b"".join(chunks)
        after = os.fstat(fd)
        named_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            _stat_identity(initial) != _stat_identity(after)
            or _stat_identity(after) != _stat_identity(named_after)
            or content != expected_content
            or hashlib.sha256(content).digest() != hashlib.sha256(expected_content).digest()
        ):
            raise ValueError("published file restore changed")
    except OSError as exc:
        raise ValueError("published file restore changed") from exc
    finally:
        os.close(fd)


def _discard_rejected_restore(
    directory_fd: int, name: str, expected_stat: os.stat_result,
) -> None:
    try:
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _stat_identity(named)[:5] == _stat_identity(expected_stat)[:5]:
            os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)
    except OSError:
        pass


def _restore_current_after_rejected_restore(
    published: _PublishedFile,
    quarantine: str,
    rejected_stat: os.stat_result,
) -> bool:
    rejected = ""
    try:
        named = os.stat(
            published.path.name,
            dir_fd=published.directory_fd,
            follow_symlinks=False,
        )
        if _stat_identity(named)[:5] != _stat_identity(rejected_stat)[:5]:
            return False
        for _attempt in range(32):
            candidate = f".{published.path.name}.{secrets.token_hex(8)}.restore-rejected"
            try:
                os.stat(candidate, dir_fd=published.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                rejected = candidate
                break
        if not rejected:
            return False
        os.rename(
            published.path.name,
            rejected,
            src_dir_fd=published.directory_fd,
            dst_dir_fd=published.directory_fd,
        )
        moved = os.stat(rejected, dir_fd=published.directory_fd, follow_symlinks=False)
        if _stat_identity(moved)[:5] != _stat_identity(rejected_stat)[:5]:
            os.rename(
                rejected,
                published.path.name,
                src_dir_fd=published.directory_fd,
                dst_dir_fd=published.directory_fd,
            )
            rejected = ""
            return False
        os.rename(
            quarantine,
            published.path.name,
            src_dir_fd=published.directory_fd,
            dst_dir_fd=published.directory_fd,
        )
        restored = os.stat(
            published.path.name,
            dir_fd=published.directory_fd,
            follow_symlinks=False,
        )
        if _stat_identity(restored)[:6] != published.object_identity[:6]:
            return False
        os.unlink(rejected, dir_fd=published.directory_fd)
        rejected = ""
        os.fsync(published.directory_fd)
        return True
    except OSError:
        return False


def _publish_recovery_residue(
    published: _PublishedFile,
    temporary: str,
    temporary_stat: os.stat_result,
) -> dict:
    assert published.previous_content is not None
    digest = hashlib.sha256(published.previous_content).hexdigest()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", published.path.name)[:64] or "output"
    recovery_name = ""
    for attempt in range(32):
        candidate = (
            f".{safe_stem}.recovery-{digest[:16]}-"
            f"{temporary_stat.st_dev:x}-{temporary_stat.st_ino:x}-{attempt}.json"
        )
        try:
            os.link(
                temporary,
                candidate,
                src_dir_fd=published.directory_fd,
                dst_dir_fd=published.directory_fd,
                follow_symlinks=False,
            )
            recovery_name = candidate
            break
        except FileExistsError:
            continue
    if not recovery_name:
        raise ValueError("published file recovery name is unavailable")
    os.unlink(temporary, dir_fd=published.directory_fd)
    os.fsync(published.directory_fd)
    _verify_restore_content_at(
        published.directory_fd,
        recovery_name,
        temporary_stat,
        published.previous_content,
        allow_rename_ctime=True,
    )
    return {
        "basename": recovery_name,
        "digest": f"sha256:{digest}",
        "size": len(published.previous_content),
    }


def _rollback_published_file(published: _PublishedFile) -> None:
    quarantine = ""
    previous_temporary = ""
    previous_temporary_stat: os.stat_result | None = None
    try:
        _verify_published_file(published)
        if not published.created and published.previous_content is None:
            os.fsync(published.directory_fd)
            return
        if not published.created and published.previous_content is not None:
            previous_temporary, previous_temporary_stat = _write_temp_at(
                published.directory_fd,
                f"{published.path.name}.restore",
                published.previous_content,
            )
            os.fsync(published.directory_fd)
            _verify_published_file(published)
            try:
                _verify_restore_content_at(
                    published.directory_fd,
                    previous_temporary,
                    previous_temporary_stat,
                    published.previous_content,
                    allow_rename_ctime=False,
                )
            except ValueError:
                _discard_rejected_restore(
                    published.directory_fd, previous_temporary, previous_temporary_stat,
                )
                previous_temporary = ""
                raise
        for _attempt in range(32):
            candidate = f".{published.path.name}.{secrets.token_hex(8)}.rollback"
            try:
                os.stat(candidate, dir_fd=published.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                quarantine = candidate
                break
        if not quarantine:
            raise ValueError("published file rollback quarantine is unavailable")
        os.rename(
            published.path.name,
            quarantine,
            src_dir_fd=published.directory_fd,
            dst_dir_fd=published.directory_fd,
        )
        quarantined = os.stat(quarantine, dir_fd=published.directory_fd, follow_symlinks=False)
        # rename may update ctime on some filesystems; dev/inode/mode/link/size/mtime
        # remain the identity of the exact object that this transaction published.
        if _stat_identity(quarantined)[:6] != published.object_identity[:6]:
            try:
                os.stat(published.path.name, dir_fd=published.directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(
                    quarantine,
                    published.path.name,
                    src_dir_fd=published.directory_fd,
                    dst_dir_fd=published.directory_fd,
                )
                quarantine = ""
            raise ValueError("published file changed before rollback")
        if not published.created and published.previous_content is not None:
            assert previous_temporary_stat is not None
            os.replace(
                previous_temporary,
                published.path.name,
                src_dir_fd=published.directory_fd,
                dst_dir_fd=published.directory_fd,
            )
            previous_temporary = ""
            try:
                _verify_restore_content_at(
                    published.directory_fd,
                    published.path.name,
                    previous_temporary_stat,
                    published.previous_content,
                    allow_rename_ctime=True,
                )
            except ValueError:
                if _restore_current_after_rejected_restore(
                    published, quarantine, previous_temporary_stat,
                ):
                    quarantine = ""
                raise
            os.fsync(published.directory_fd)
        os.unlink(quarantine, dir_fd=published.directory_fd)
        quarantine = ""
        os.fsync(published.directory_fd)
    except Exception as exc:
        if previous_temporary and previous_temporary_stat is not None:
            recovery_ref = _publish_recovery_residue(
                published, previous_temporary, previous_temporary_stat,
            )
            previous_temporary = ""
            raise PublishedRollbackRecoveryError(recovery_ref) from exc
        if isinstance(exc, OSError):
            raise ValueError("published file rollback failed") from exc
        raise
    finally:
        # A fully written .restore.*.tmp is intentionally recoverable after a
        # failed rollback; deleting the only previous-content copy loses data.
        os.close(published.directory_fd)


def _close_published_file(published: _PublishedFile) -> None:
    os.close(published.directory_fd)


def _finish_published_file(published: _PublishedFile) -> _PublishedFile:
    """Last fallible boundary before ownership transfers to the caller."""
    _verify_published_file(published)
    return published


def _rollback_unreturned_publish(
    *,
    path: Path,
    directory_fd: int,
    directory_identity: tuple[int, int, int],
    temporary: str,
    temporary_stat: os.stat_result,
    created: bool,
    previous_content: bytes | None,
    published_by_this_call: bool,
) -> bool:
    """Rollback our temp inode when publish succeeded but no handle was returned."""
    if temporary:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            return False
    if not published_by_this_call:
        return False
    try:
        named = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    if (
        named.st_dev != temporary_stat.st_dev
        or named.st_ino != temporary_stat.st_ino
        or named.st_mode != temporary_stat.st_mode
    ):
        return False
    published = _PublishedFile(
        path,
        created,
        directory_fd,
        directory_identity,
        _stat_identity(named),
        previous_content,
    )
    try:
        _rollback_published_file(published)
    except PublishedRollbackRecoveryError:
        raise
    except ValueError as rollback_error:
        print(f"ERROR: unreturned publish rollback rejected: {rollback_error}", file=sys.stderr)
    return True


class _PublishedFilesTransaction:
    def __init__(self) -> None:
        self._published: list[_PublishedFile] = []

    def __enter__(self) -> _PublishedFilesTransaction:
        return self

    def add(self, published: _PublishedFile) -> _PublishedFile:
        self._published.append(published)
        return published

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        if exc_type is None:
            for published in self._published:
                _close_published_file(published)
            return False
        for published in reversed(self._published):
            try:
                _rollback_published_file(published)
            except PublishedRollbackRecoveryError:
                raise
            except ValueError as rollback_error:
                print(f"ERROR: published file rollback rejected: {rollback_error}", file=sys.stderr)
        return False


def _open_publish_directory(path: Path) -> tuple[int, tuple[int, int, int]]:
    path.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd: int | None = None
    try:
        directory_fd = os.open(os.fspath(path), flags)
        opened = os.fstat(directory_fd)
        named = path.lstat()
        identity = _directory_identity(opened)
        if not stat.S_ISDIR(opened.st_mode):
            raise ValueError(
                f"publish directory changed: {_publish_directory_detail(identity, opened, named, reason='not-a-dir')}",
            )
        if _directory_identity(named) != identity:
            raise ValueError(
                f"publish directory changed: {_publish_directory_detail(identity, opened, named, reason='pre-open')}",
            )
        result = directory_fd, identity
        directory_fd = None
        return result
    except OSError as exc:
        raise ValueError("publish directory is unsafe") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _publish_output_transaction(
    path: Path,
    content: bytes,
    *,
    forbidden_targets: tuple[tuple[tuple[int, int, int], str], ...] = (),
) -> _PublishedFile:
    if not path.name or path.name in {".", ".."}:
        raise ValueError("output filename is invalid")
    directory_path = path.parent.resolve()
    directory_fd, directory_identity = _open_publish_directory(directory_path)
    temporary = ""
    temporary_stat: os.stat_result | None = None
    created = False
    previous_content: bytes | None = None
    publish_attempt = _PublishAttempt()
    keep_directory_fd = False
    try:
        if (directory_identity, path.name) in forbidden_targets:
            raise ValueError("output target conflicts with an immutable archive")
        previous_entry = _read_review_archive_at(directory_fd, path.name)
        temporary, temporary_stat = _write_temp_at(directory_fd, path.name, content)
        opened_parent = os.fstat(directory_fd)
        named_parent = directory_path.lstat()
        opened_identity = _directory_identity(opened_parent)
        named_identity = _directory_identity(named_parent)
        if opened_identity != directory_identity or named_identity != directory_identity:
            reason = "directory-opened" if opened_identity != directory_identity else "directory-named"
            raise ValueError(
                f"publish directory changed: {_publish_directory_detail(directory_identity, opened_parent, named_parent, reason=reason)}",
            )
        named_temporary = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if _stat_identity(named_temporary) != _stat_identity(temporary_stat):
            reason = _publish_first_mismatch_reason(
                temporary_stat,
                named_temporary,
                ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"),
                default="identity",
            )
            raise ValueError(
                f"output temporary file changed: {_publish_identity_detail(temporary_stat, named_temporary, reason=reason, expected_size=temporary_stat.st_size)}",
            )
        if previous_entry is None:
            created = True
            try:
                publish_attempt.attempted = True
                with _defer_publish_signals():
                    try:
                        os.link(
                            temporary,
                            path.name,
                            src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        publish_attempt.conflict = True
                        raise
                    publish_attempt.completed = True
            except FileExistsError as exc:
                raise ValueError("output appeared during publish") from exc
            os.unlink(temporary, dir_fd=directory_fd)
            temporary = ""
        else:
            previous_content = previous_entry[0]
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            if _stat_identity(current) != previous_entry[1]:
                previous_identity = _stat_like_from_identity(previous_entry[1])
                reason = _publish_first_mismatch_reason(
                    previous_identity,
                    current,
                    ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"),
                    default="identity",
                )
                raise ValueError(
                    f"output changed during publish: {_publish_identity_detail(previous_identity, current, reason=reason, expected_size=previous_identity.st_size)}",
                )
            publish_attempt.attempted = True
            with _defer_publish_signals():
                os.replace(
                    temporary,
                    path.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                publish_attempt.completed = True
            temporary = ""
        published = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_inode(temporary_stat, published) or published.st_size != len(content):
            reason = _publish_first_mismatch_reason(
                temporary_stat,
                published,
                ("st_dev", "st_ino", "st_mode", "st_nlink"),
                default="size",
            )
            raise ValueError(
                f"output publish changed: {_publish_identity_detail(temporary_stat, published, reason=reason, expected_size=len(content))}",
            )
        os.fsync(directory_fd)
        result = _PublishedFile(
            directory_path / path.name,
            created,
            directory_fd,
            directory_identity,
            _stat_identity(published),
            previous_content,
        )
        result = _finish_published_file(result)
        keep_directory_fd = True
        return result
    except BaseException as exc:
        if temporary_stat is not None and _rollback_unreturned_publish(
            path=directory_path / path.name,
            directory_fd=directory_fd,
            directory_identity=directory_identity,
            temporary=temporary,
            temporary_stat=temporary_stat,
            created=created,
            previous_content=previous_content,
            published_by_this_call=publish_attempt.owns_named_target(
                directory_fd, path.name, temporary_stat,
            ),
        ):
            keep_directory_fd = True
            temporary = ""
        if isinstance(exc, OSError):
            raise ValueError("output publish failed") from exc
        raise
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if not keep_directory_fd:
            os.close(directory_fd)


def _publish_review_archive_transaction(cwd: Path, name: str, content: bytes) -> _PublishedFile:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("review archive filename is invalid")
    directory_fd, archive_path = _open_review_archive_directory(cwd)
    temporary = ""
    temporary_stat: os.stat_result | None = None
    publish_attempt = _PublishAttempt()
    keep_directory_fd = False
    try:
        _verify_review_archive_directory(directory_fd, archive_path)
        directory_identity = _directory_identity(os.fstat(directory_fd))
        existing_entry = _read_review_archive_at(directory_fd, name)
        if existing_entry is not None:
            existing, existing_identity = existing_entry
            if existing != content:
                raise ValueError("immutable review archive collision")
            _verify_review_archive_directory(directory_fd, archive_path)
            confirmed_entry = _read_review_archive_at(directory_fd, name)
            if confirmed_entry is None:
                raise ValueError("review archive evidence changed")
            confirmed, confirmed_identity = confirmed_entry
            if (
                confirmed != content
                or confirmed_identity != existing_identity
                or hashlib.sha256(confirmed).digest() != hashlib.sha256(content).digest()
            ):
                raise ValueError("review archive evidence changed")
            _verify_review_archive_directory(directory_fd, archive_path)
            result = _finish_published_file(_PublishedFile(
                archive_path / name, False, directory_fd, directory_identity,
                confirmed_identity,
            ))
            keep_directory_fd = True
            return result
        temporary, temporary_stat = _write_temp_at(directory_fd, name, content)
        _verify_review_archive_directory(directory_fd, archive_path)
        named_temporary = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if _stat_identity(named_temporary) != _stat_identity(temporary_stat):
            raise ValueError("review archive temporary file changed")
        try:
            publish_attempt.attempted = True
            with _defer_publish_signals():
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    publish_attempt.conflict = True
                    raise
                publish_attempt.completed = True
        except FileExistsError:
            os.unlink(temporary, dir_fd=directory_fd)
            temporary = ""
            concurrent_entry = _read_review_archive_at(directory_fd, name)
            if concurrent_entry is None or concurrent_entry[0] != content:
                raise ValueError("immutable review archive collision")
            _verify_review_archive_directory(directory_fd, archive_path)
            result = _finish_published_file(_PublishedFile(
                archive_path / name, False, directory_fd, directory_identity,
                concurrent_entry[1],
            ))
            keep_directory_fd = True
            return result
        os.unlink(temporary, dir_fd=directory_fd)
        temporary = ""
        published = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_inode(temporary_stat, published) or published.st_size != len(content):
            raise ValueError("review archive publish changed")
        _verify_review_archive_directory(directory_fd, archive_path)
        os.fsync(directory_fd)
        result = _finish_published_file(_PublishedFile(
            archive_path / name, True, directory_fd, directory_identity,
            _stat_identity(published),
        ))
        keep_directory_fd = True
        return result
    except BaseException as exc:
        if temporary_stat is not None and _rollback_unreturned_publish(
            path=archive_path / name,
            directory_fd=directory_fd,
            directory_identity=directory_identity,
            temporary=temporary,
            temporary_stat=temporary_stat,
            created=True,
            previous_content=None,
            published_by_this_call=publish_attempt.owns_named_target(
                directory_fd, name, temporary_stat,
            ),
        ):
            keep_directory_fd = True
            temporary = ""
        if isinstance(exc, OSError):
            raise ValueError("review archive publish failed") from exc
        raise
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if not keep_directory_fd:
            os.close(directory_fd)


def _publish_review_import_evidence(cwd: Path, name: str, content: bytes) -> Path:
    published = _publish_review_archive_transaction(cwd, name, content)
    try:
        _verify_published_file(published)
        return published.path
    finally:
        _close_published_file(published)


class _DuplicateReviewJsonKey(ValueError):
    pass


def _reject_duplicate_review_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateReviewJsonKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_strict_review_bytes(content: bytes, expected_iteration: int) -> dict:
    """Parse exactly one UTF-8 mission-review/1 document with no prose fallback."""
    if len(content) > MAX_REVIEW_INPUT_BYTES:
        raise ValueError("review input exceeds 4 MiB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("review input is invalid UTF-8") from exc
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_review_keys)
    except (_DuplicateReviewJsonKey, json.JSONDecodeError) as exc:
        raise ValueError("review input must be exactly one JSON document") from exc
    _validate_review_payload(payload, expected_iteration)
    return payload


def _validate_review_payload(payload: object, expected_iteration: int) -> None:
    """Validate the shared mission-review/1 contract without mutating state."""
    if (
        not isinstance(expected_iteration, int)
        or isinstance(expected_iteration, bool)
        or expected_iteration < 1
    ):
        raise ValueError("expected iteration must be a positive integer")
    if not isinstance(payload, dict):
        raise ValueError("review must be a JSON object")
    if payload.get("schema") != "mission-review/1":
        raise ValueError("schema must be mission-review/1")
    payload_iteration = payload.get("iteration")
    if (
        not isinstance(payload_iteration, int)
        or isinstance(payload_iteration, bool)
        or payload_iteration < 1
        or payload_iteration != expected_iteration
    ):
        raise ValueError(f"iteration must be {expected_iteration}")
    perspective = payload.get("perspective")
    if not valid_review_perspective(perspective):
        raise ValueError("perspective must be a non-empty trimmed string")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings must be a list")
    try:
        validate_review_learning(payload)
    except LearningContractError as exc:
        raise ValueError(str(exc)) from exc
    seen_ids = set()
    for idx, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            raise ValueError(f"finding {idx} must be an object")
        fid = finding.get("id")
        if not isinstance(fid, str) or not fid.startswith(f"{perspective}-"):
            raise ValueError(f"finding {idx} id must start with '{perspective}-'")
        if fid in seen_ids:
            raise ValueError(f"duplicate finding id: {fid}")
        seen_ids.add(fid)
        severity = finding.get("severity")
        if severity not in REVIEW_SEVERITIES:
            raise ValueError(f"finding {fid} severity must be one of {sorted(REVIEW_SEVERITIES)}")
        axis = finding.get("axis")
        if axis not in REVIEW_SCORE_KEYS:
            raise ValueError(f"finding {fid} axis must be one of {list(REVIEW_SCORE_KEYS)}")
        if severity in {"High", "Medium"} and not str(finding.get("evidence") or "").strip():
            raise ValueError(f"finding {fid} evidence is required for {severity}")
    if "scores" not in payload:
        raise ValueError("scores field is required; use null only for findings-only reviewers")
    scores = payload.get("scores")
    if scores is None:
        return
    if not isinstance(scores, dict) or set(scores) != set(REVIEW_SCORE_KEYS):
        raise ValueError(f"scores must contain exactly {list(REVIEW_SCORE_KEYS)}")
    for key, value in scores.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or math.isnan(float(value)) or not (SCORE_MIN <= float(value) <= SCORE_MAX):
            raise ValueError(f"score {key} must be a {SCORE_MIN}-{SCORE_MAX} number")
    values = [float(scores[key]) for key in REVIEW_SCORE_KEYS]
    if max(values) <= 1.0:
        raise ValueError("scores look like 0-1 normalized scale; use 0-5 scale")
    if len(set(values)) == 1 and not str(payload.get("same_score_note") or "").strip():
        raise ValueError("same_score_note is required when all four scores are equal")


def _extract_review_payload(src: Path) -> tuple[dict, dict]:
    """Extract one mission-review/1 payload and measure its external prose."""
    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        print(f"ERROR: reviewer input is invalid UTF-8: {src}: {error}", file=sys.stderr)
        sys.exit(2)

    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        candidates = []
        for match in REVIEW_JSON_FENCE_RE.finditer(text):
            json_text = match.group("body").strip()
            try:
                candidate = json.loads(json_text)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("schema") == "mission-review/1":
                candidates.append((candidate, json_text, match.span()))
        if len(candidates) != 1:
            print(
                f"ERROR: reviewer input must contain exactly one mission-review/1 JSON: {src}",
                file=sys.stderr,
            )
            sys.exit(2)
        payload, json_text, (start, end) = candidates[0]
        prose_text = text[:start] + text[end:]
    else:
        json_text = stripped
        prose_text = ""

    json_bytes = len(json_text.encode("utf-8"))
    prose_bytes = _review_prose_bytes(prose_text)
    denominator = json_bytes + prose_bytes
    metric = {
        "json_bytes": json_bytes,
        "prose_bytes": prose_bytes,
        "prose_ratio": round(prose_bytes / denominator, 6) if denominator else 0,
    }
    return payload, metric


def _load_review_json(path_str: str, expected_iteration: int) -> tuple[dict, dict]:
    src = Path(path_str)
    if not (src.exists() and src.is_file()):
        print(f"ERROR: reviewer input not found: {src}", file=sys.stderr)
        sys.exit(2)
    payload, metric = _extract_review_payload(src)
    try:
        _validate_review_payload(payload, expected_iteration)
    except ValueError as exc:
        _review_error(src, str(exc))
    return payload, metric


def _review_import_ref_is_valid(reference: object, expected_iteration: int) -> bool:
    return (
        isinstance(expected_iteration, int)
        and not isinstance(expected_iteration, bool)
        and expected_iteration >= 1
        and isinstance(reference, dict)
        and reference.get("kind") == "review-input"
        and isinstance(reference.get("path"), str)
        and isinstance(reference.get("digest"), str)
        and _SHA256_REF_RE.fullmatch(reference["digest"]) is not None
        and isinstance(reference.get("size"), int)
        and not isinstance(reference.get("size"), bool)
        and 0 <= reference["size"] <= MAX_REVIEW_INPUT_BYTES
        and isinstance(reference.get("iteration"), int)
        and not isinstance(reference.get("iteration"), bool)
        and reference["iteration"] >= 1
        and reference["iteration"] == expected_iteration
        and valid_review_perspective(reference.get("perspective"))
    )


def _load_imported_review(cwd: Path, state: dict, reference_path: str, expected_iteration: int) -> tuple[dict, dict, dict]:
    """Resolve a state-recorded immutable import; caller-provided paths add no authority."""
    records = state.get("review_evidence_refs")
    if not isinstance(records, list):
        raise ValueError("review import reference is unavailable")
    matches = [
        ref for ref in records
        if _review_import_ref_is_valid(ref, expected_iteration) and ref.get("path") == reference_path
    ]
    if len(matches) != 1:
        raise ValueError("review import reference is missing or ambiguous")
    reference = matches[0]
    try:
        content = _read_strict_review_file(cwd / reference["path"])
    except ValueError as exc:
        raise ValueError("review import evidence is unavailable") from exc
    if len(content) != reference["size"] or "sha256:" + hashlib.sha256(content).hexdigest() != reference["digest"]:
        raise ValueError("review import evidence integrity mismatch")
    payload = _parse_strict_review_bytes(content, expected_iteration)
    if payload["perspective"] != reference["perspective"]:
        raise ValueError("review import evidence perspective mismatch")
    metric = {"json_bytes": len(content), "prose_bytes": 0, "prose_ratio": 0}
    return payload, metric, reference


def _derive_failure_ledger(cwd: Path, score_history: object) -> dict:
    """Rebuild the materialized ledger only from immutable review aggregates."""
    if not isinstance(score_history, list):
        raise ValueError("score_history must be a list")
    observations: list[dict] = []
    for entry in score_history:
        if not isinstance(entry, dict):
            raise ValueError("score_history entry must be an object")
        reference = entry.get("review_evidence_ref")
        if reference is None:
            continue
        if (not isinstance(reference, dict) or reference.get("kind") != "review-aggregate"
                or not isinstance(reference.get("path"), str)
                or not isinstance(reference.get("digest"), str)
                or _SHA256_REF_RE.fullmatch(reference["digest"]) is None):
            raise ValueError("failure ledger review reference is invalid")
        content = _read_bounded_review_evidence(cwd, reference["path"])
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if digest != reference["digest"] or reference.get("generation") != digest[7:23]:
            raise ValueError("failure ledger review reference integrity mismatch")
        try:
            aggregate = json.loads(content.decode("utf-8"), object_pairs_hook=_reject_duplicate_review_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateReviewJsonKey) as exc:
            raise ValueError("failure ledger review aggregate is invalid") from exc
        iteration = entry.get("iteration")
        if (not isinstance(aggregate, dict) or aggregate.get("schema") != "mission-review-aggregate/1"
                or aggregate.get("iteration") != iteration or not isinstance(aggregate.get("inputs"), list)):
            raise ValueError("failure ledger review aggregate binding mismatch")
        for review in aggregate["inputs"]:
            observations.append({
                "iteration": iteration, "review": review,
                "review_aggregate_ref": {"kind": "review-aggregate", "digest": digest},
            })
    try:
        return reduce_failure_ledger(observations)
    except LearningContractError as exc:
        raise ValueError(f"failure ledger input is invalid: {exc}") from exc


def cmd_review_import(args):
    """Validate one untrusted review before atomically making it state-owned evidence."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    if args.iteration < 1:
        print("ERROR: --iteration は 1 以上で指定してください", file=sys.stderr)
        sys.exit(2)
    try:
        if args.input is not None:
            content = _read_strict_review_file(Path(args.input))
        else:
            content = sys.stdin.buffer.read(MAX_REVIEW_INPUT_BYTES + 1)
        review = _parse_strict_review_bytes(content, args.iteration)
    except ValueError as exc:
        outcome = _command_outcome(args, "review-import", "invalid-input")
        guidance = build_guidance("review-import", "schema-invalid", {"iteration": args.iteration})
        outcome["guidance"] = True
        _record_command_outcome_only(cwd, outcome)
        _emit_json_command_failure(args, outcome, guidance)
        print(f"ERROR: review import rejected: {exc}", file=sys.stderr)
        sys.exit(2)

    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    outcome = _command_outcome(args, "review-import", "ok")
    mission8 = "unknown"
    with StateLock(lock_file(cwd)), _PublishedFilesTransaction() as published_files:
        data = json.loads(sf.read_text())
        lease_decision = _enforce_session_lease_for_write(sf, data)
        mission8 = str(data.get("mission_id") or "unknown")[:8]
        archive_name = f"iter-{args.iteration}-{mission8}-review-input-{digest[7:23]}.json"
        try:
            evidence_publish = published_files.add(
                _publish_review_archive_transaction(cwd, archive_name, content)
            )
            destination = evidence_publish.path
        except ValueError as exc:
            failure = _command_outcome(args, "review-import", "invalid-input")
            guidance = build_guidance("review-import", "schema-invalid", {"iteration": args.iteration})
            failure["guidance"] = True
            _record_command_outcome_only(cwd, failure)
            _emit_json_command_failure(args, failure, guidance)
            print(f"ERROR: review import archive rejected: {exc}", file=sys.stderr)
            sys.exit(2)
        reference = {
            "kind": "review-input",
            "path": str(destination.relative_to(cwd)),
            "digest": digest,
            "size": len(content),
            "iteration": args.iteration,
            "perspective": review["perspective"],
        }
        previous = data.get("review_evidence_refs")
        records = previous if isinstance(previous, list) else []
        retained = [item for item in records if isinstance(item, dict) and item != reference]
        data["review_evidence_refs"] = (retained + [reference])[-128:]
        _append_command_outcome(data, outcome)
        data["updated_at"] = iso_now()
        backup_state(sf)
        _verify_published_file(evidence_publish)
        atomic_write_json(
            sf, stamp_metadata(data, cwd), lease_decision=lease_decision,
        )
    print(json.dumps({
        "ok": True,
        "outcome_kind": "ok",
        "outcome": outcome,
        "review_evidence_ref": reference,
    }, ensure_ascii=False))


def cmd_plan_import(args):
    """Validate one provider result and atomically publish only an inert plan candidate."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        _provider_gate("state-missing")
    if not re.fullmatch(r"inv_[0-9a-f]{32}", args.invocation_id):
        _provider_gate("invocation-id-invalid")
    try:
        raw = _read_strict_review_file(Path(args.input))
    except ValueError:
        _provider_gate("plan-input-unreadable")
    with StateLock(lock_file(cwd)), _PublishedFilesTransaction() as published_files:
        data = json.loads(sf.read_text(encoding="utf-8"))
        invocation = invocation_by_id(data, args.invocation_id)
        if data.get("planning_policy_version") == 1 and data.get("planning_strategy") == "provider-primary":
            _require_current_primary_planning_binding(data)
        if not isinstance(invocation, dict):
            _provider_gate("invocation-not-found")
        if (invocation.get("iteration") != data.get("iteration") or invocation.get("phase") != "planning"
                or invocation.get("status") != "completed" or invocation.get("lifecycle_state") != "terminal"):
            _provider_gate("invocation-not-current-completed-plan")
        provider = _find_provider(data, str(invocation.get("skill") or invocation.get("role") or ""))
        current = _require_current_provider_application(
            data, provider, requested_phase="planning", requested_iteration=data.get("iteration"),
            application_kind="result-import", selection_source=invocation.get("selection_source"),
            invocation_id=args.invocation_id, cwd=cwd, registry_args=args,
        )
        contract = current.get("result_contract") if isinstance(current.get("result_contract"), dict) else {}
        if not contract:
            _provider_gate("missing-structured-result-contract")
        pointers = data.get("provider_preflights") if isinstance(data.get("provider_preflights"), dict) else {}
        matches = [(key, value) for key, value in pointers.items() if isinstance(value, dict) and value.get("invocation_id") == args.invocation_id]
        if len(matches) != 1:
            _provider_gate("preflight-binding-missing")
        preflight_id, pointer = matches[0]
        if pointer.get("status") != "consumed" or pointer.get("consumed_invocation_id") != args.invocation_id:
            _provider_gate("preflight-not-consumed")
        artifact_path = pointer.get("artifact_path")
        receipt = pointer.get("receipt") if isinstance(pointer.get("receipt"), dict) else {}
        receipt_path, receipt_digest = receipt.get("artifact_path"), receipt.get("digest")
        try:
            for relative in (artifact_path, receipt_path):
                if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts: raise ValueError
            if not isinstance(receipt_digest, str) or _SHA256_REF_RE.fullmatch(receipt_digest) is None: raise ValueError
            packet_bytes = _read_strict_review_file(state_dir(cwd) / artifact_path)
            if "sha256:" + hashlib.sha256(packet_bytes).hexdigest() != pointer.get("outbound_packet_digest"): raise ValueError
            receipt_bytes = _read_strict_review_file(state_dir(cwd) / receipt_path)
            if "sha256:" + hashlib.sha256(receipt_bytes).hexdigest() != receipt_digest: raise ValueError
        except ValueError:
            _provider_gate("consumed-preflight-evidence-invalid")
        expected = {"invocation_id": args.invocation_id, "preflight_id": preflight_id,
                    "outbound_packet_digest": pointer.get("outbound_packet_digest"),
                    "selection_id": current.get("selection_id"),
                    "selection_source": current.get("eligibility_selection_source") or "automatic",
                    "iteration": data.get("iteration")}
        try:
            parsed = parse_provider_result(raw, expected_binding=expected, result_contract=contract, workspace=cwd)
        except PlanContractError as exc:
            _provider_gate(str(exc))
        digest = parsed["raw_result_digest"]
        metadata = {
            "authority": {"owner": "mission", "may_write_state": False, "may_decide_review": False, "may_decide_score": False, "may_decide_completion": False},
            "provenance": {"provider_id": current.get("provider_id"), "registry_entry_digest": current.get("registry_entry_digest"), "selection_id": expected["selection_id"], "selection_source": expected["selection_source"], "invocation_id": args.invocation_id, "iteration": expected["iteration"], "input_outbound_packet_digest": expected["outbound_packet_digest"], "raw_result_digest": digest},
            "capability_verification": {"selection_verified": True, "class_exact_match": True, "variant_exact_match": True},
        }
        candidate = {"schema": "mission-plan/1", **parsed["document"], "mission_metadata": metadata}
        canonical = canonical_plan_bytes(candidate)
        canonical_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
        mission8 = str(data.get("mission_id") or "unknown")[:8]
        raw_name = f"plan-result-{mission8}-{digest[7:23]}.json"
        raw_file = published_files.add(_publish_review_archive_transaction(cwd, raw_name, raw))
        candidate_path = state_dir(cwd) / "plans" / f"{canonical_digest[7:23]}.json"
        candidate_file = published_files.add(_publish_output_transaction(candidate_path, canonical))
        previous = (data.get("provider_plan_imports") or {}).get(args.invocation_id)
        generation = (previous.get("generation", 0) if isinstance(previous, dict) and previous.get("candidate_digest") != canonical_digest else 0)
        if not generation:
            generation = (previous.get("generation", 0) if isinstance(previous, dict) else 0) or 1
        else:
            generation += 1
        reference = {"raw_result_path": str(raw_file.path.relative_to(cwd)), "raw_result_digest": digest,
                     "candidate_path": str(candidate_file.path.relative_to(cwd)), "candidate_digest": canonical_digest,
                     "invocation_id": args.invocation_id, "preflight_id": preflight_id, "generation": generation}
        data.setdefault("provider_plan_imports", {})[args.invocation_id] = reference
        data["updated_at"] = iso_now()
        _verify_published_file(raw_file); _verify_published_file(candidate_file)
        backup_state(sf); atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "plan_import": reference}, indent=2 if args.json else None, ensure_ascii=False))


def _read_core_plan_input(source: Path) -> bytes:
    """Read one stable bounded regular plan document without following links."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(os.fspath(source), os.O_RDONLY | os.O_NONBLOCK | nofollow)
    except OSError as exc:
        raise PlanContractError("plan-input-unreadable") from exc
    try:
        initial = os.fstat(fd)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise PlanContractError("plan-input-not-regular")
        if initial.st_size > MAX_PLAN_RESULT_BYTES:
            raise PlanContractError("result-too-large")
        chunks: list[bytes] = []
        remaining = initial.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise PlanContractError("plan-input-changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1) or _stat_identity(os.fstat(fd)) != _stat_identity(initial):
            raise PlanContractError("plan-input-changed")
        try:
            named = os.lstat(source)
        except OSError as exc:
            raise PlanContractError("plan-input-changed") from exc
        if _stat_identity(named) != _stat_identity(initial):
            raise PlanContractError("plan-input-changed")
        return b"".join(chunks)
    except OSError as exc:
        raise PlanContractError("plan-input-unreadable") from exc
    finally:
        os.close(fd)


def cmd_planning_adopt_core(args):
    """Validate and publish one core-produced plan as canonical authority."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        _provider_gate("state-missing")
    try:
        raw = _read_core_plan_input(Path(args.input))
        document = _validate_document(_strict_plan_load(raw), workspace=cwd)
    except PlanContractError as exc:
        _provider_gate(str(exc))

    with StateLock(lock_file(cwd)), _PublishedFilesTransaction() as published_files:
        data = json.loads(sf.read_text(encoding="utf-8"))
        if data.get("planning_policy_version") != 1 or data.get("phase") != "planning":
            _provider_gate("planning-policy-not-active")
        if data.get("planning_strategy") not in {None, "core"}:
            _provider_gate("planning-strategy-not-core")
        if data.get("planning_provider_required") is True:
            _provider_gate("planning-provider-required")

        iteration = data.get("iteration")
        # `init` writes 0 and only `push-score` raises it, so the first plan of a
        # session is adopted at 0.  Reject bool, non-int, and negative values.
        if type(iteration) is not int or iteration < 0:
            _provider_gate("core-iteration-invalid")
        source_id = (
            f"core-{iteration}-{secrets.token_hex(6)}"
            if args.source_id is None
            else args.source_id
        )
        if not isinstance(source_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id) is None:
            _provider_gate("core-source-id-invalid")
        records = data.get("planning_source_records")
        records = records if isinstance(records, dict) else {}
        previous = records.get(f"core:{source_id}")
        if previous is None:
            generation = 1
        else:
            previous_generation = previous.get("generation") if isinstance(previous, dict) else None
            if type(previous_generation) is not int or previous_generation < 1:
                _provider_gate("core-source-generation-invalid")
            generation = previous_generation + 1
        source_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        metadata = {
            "authority": {
                "owner": "mission",
                "may_write_state": False,
                "may_decide_review": False,
                "may_decide_score": False,
                "may_decide_completion": False,
            },
            "provenance": {
                "source": "core",
                "source_id": source_id,
                "iteration": iteration,
            },
            "capability_verification": {
                "selection_verified": False,
                "class_exact_match": False,
                "variant_exact_match": False,
            },
        }
        # document 側の schema は candidate へ展開されるため、mission-plan/1 以外を
        # 持ち込まれると保存 plan の schema が乗っ取られる。値を検証して fail-closed にする。
        if "schema" in document and document["schema"] != "mission-plan/1":
            _provider_gate("core-plan-schema-invalid")
        candidate = {"schema": "mission-plan/1", **document, "mission_metadata": metadata}
        canonical = canonical_plan_bytes(candidate)
        canonical_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
        candidate_path = state_dir(cwd) / "plans" / f"{canonical_digest[7:23]}.json"
        candidate_file = published_files.add(_publish_output_transaction(candidate_path, canonical))
        plan = {
            "schema": "mission-plan/1",
            "path": str(candidate_file.path.relative_to(cwd)),
            "digest": canonical_digest,
            "source": "core",
            "source_id": source_id,
            "source_digest": source_digest,
            "selection_source": "core",
            "iteration": iteration,
            "generation": generation,
            "validated_at": iso_now(),
        }
        try:
            canonical_plan_identity(cwd, plan, reader=_read_strict_review_file)
        except (OSError, PlanningLifecycleError) as exc:
            _provider_gate(f"core-plan-candidate-invalid:{exc}")
        data["canonical_plan"] = plan
        records[f"core:{source_id}"] = {
            key: plan[key]
            for key in ("generation", "source", "source_id", "selection_source", "iteration")
        }
        data["planning_source_records"] = records
        data["updated_at"] = iso_now()
        _verify_published_file(candidate_file)
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "canonical_plan": plan}, indent=2 if args.json else None, ensure_ascii=False))


def cmd_planning_promote_provider_plan(args):
    """Promote only a #397-validated provider candidate to canonical authority."""
    cwd = Path.cwd(); sf = resolve_state_file(cwd)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text(encoding="utf-8"))
        if data.get("planning_policy_version") != 1 or data.get("phase") != "planning":
            _provider_gate("planning-policy-not-active")
        if data.get("planning_strategy") != "provider-primary":
            _provider_gate("planning-strategy-not-primary")
        primary = _require_current_primary_planning_binding(data)
        imports = data.get("provider_plan_imports") or {}
        record = imports.get(args.invocation_id) if isinstance(imports, dict) else None
        if not isinstance(record, dict):
            _provider_gate("provider-plan-import-missing")
        invocation = invocation_by_id(data, args.invocation_id)
        if (invocation.get("provider_id") != primary.get("provider_id")
                or invocation.get("selection_id") != primary.get("selection_id")):
            _provider_gate("planning-primary-invocation-provider-mismatch")
        if invocation.get("status") != "completed" or invocation.get("iteration") != data.get("iteration"):
            _provider_gate("provider-plan-invocation-not-current")
        candidate_path, candidate_digest = record.get("candidate_path"), record.get("candidate_digest")
        if not isinstance(candidate_path, str) or not isinstance(candidate_digest, str):
            _provider_gate("provider-plan-import-invalid")
        source_digest = record.get("raw_result_digest")
        plan = {"schema": "mission-plan/1", "path": candidate_path, "digest": candidate_digest,
                "source": "provider", "source_id": args.invocation_id, "source_digest": source_digest,
                "selection_source": invocation.get("selection_source") or "automatic",
                "iteration": data.get("iteration"), "generation": record.get("generation"), "validated_at": iso_now()}
        try:
            _raw, _steps = canonical_plan_identity(cwd, plan, reader=_read_strict_review_file)
        except (OSError, PlanningLifecycleError) as exc:
            _provider_gate(f"provider-plan-candidate-invalid:{exc}")
        data["canonical_plan"] = plan
        data.setdefault("planning_source_records", {})[f"provider:{args.invocation_id}"] = {
            key: plan[key] for key in ("generation", "source", "source_id", "selection_source", "iteration")
        }
        data["updated_at"] = iso_now(); backup_state(sf); atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "canonical_plan": plan}, ensure_ascii=False))


def cmd_planning_reselect(args):
    """Explicitly opt an active legacy planning session into fresh selection only."""
    cwd = Path.cwd(); sf = resolve_state_file(cwd)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text(encoding="utf-8"))
        if not data.get("loop_active") or data.get("halt_reason") or data.get("phase") != "planning":
            _provider_gate("legacy-reselection-requires-active-planning")
        if any(isinstance(r, dict) and r.get("status") == "running" for r in data.get("specialist_invocations") or []):
            _provider_gate("legacy-reselection-running-invocation")
        # Do not copy raw legacy candidate records.  Fresh recommendation is
        # deliberately a separate caller action after this bounded migration.
        data["planning_policy_version"] = 1
        data.pop("planning_strategy", None)
        data.pop("canonical_plan", None)
        data.pop("executor_handoff", None)
        data["specialists_candidates"] = []
        data["specialists_selected"] = []
        data["specialists_decision"] = _new_specialist_selection_checkpoint()
        # A legacy raw specialist record is intentionally not copied into a
        # public backup before it is discarded.
        data["updated_at"] = iso_now(); atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "planning_policy_version": 1, "next_action": "reselect-planning-provider"}, ensure_ascii=False))


def _cmd_executor_handoff(args, operation: str):
    cwd = Path.cwd(); sf = resolve_state_file(cwd)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text(encoding="utf-8"))
        handoff = data.get("executor_handoff")
        plan = data.get("canonical_plan")
        try:
            if not isinstance(handoff, dict) or not isinstance(plan, dict):
                raise PlanningLifecycleError("executor-handoff-missing")
            expected = _trusted_canonical_plan_binding(data, plan)
            _raw, steps = canonical_plan_identity(cwd, plan, expected=expected, reader=_read_strict_review_file)
            if handoff.get("plan_digest") != plan.get("digest") or handoff.get("plan_generation") != plan.get("generation") or handoff.get("step_ids") != steps:
                raise PlanningLifecycleError("executor-handoff-plan-drift")
            if operation == "begin":
                if handoff.get("status") != "prepared": raise PlanningLifecycleError("executor-handoff-not-prepared")
                handoff["status"] = "consuming"; handoff["begun_at"] = iso_now()
            elif operation == "verify":
                validate_handoff_step(data, args.step_id)
            elif operation == "record":
                validate_handoff_step(data, args.step_id)
                done = {d.get("step_id") for d in data.get("decisions") or [] if isinstance(d, dict) and d.get("handoff_id") == handoff.get("handoff_id")}
                if args.step_id in done: raise PlanningLifecycleError("executor-step-already-recorded")
                document = json.loads(_raw); step = next(s for s in document["steps"] if s["id"] == args.step_id)
                if any(dep not in done for dep in step.get("depends_on", [])): raise PlanningLifecycleError("executor-step-dependency-incomplete")
                data.setdefault("decisions", []).append({"handoff_id": handoff["handoff_id"], "plan_digest": plan["digest"], "plan_generation": plan["generation"], "plan_source": plan["source"], "source_id": plan["source_id"], "selection_source": plan["selection_source"], "iteration": plan["iteration"], "step_id": args.step_id, "result": args.result})
            else:
                if handoff.get("status") != "consuming": raise PlanningLifecycleError("executor-handoff-not-consuming")
                done = {d.get("step_id") for d in data.get("decisions") or [] if isinstance(d, dict) and d.get("handoff_id") == handoff.get("handoff_id")}
                if set(steps) != done: raise PlanningLifecycleError("executor-handoff-steps-incomplete")
                handoff["status"] = "consumed"; handoff["consumed_at"] = iso_now()
        except (OSError, ValueError, PlanningLifecycleError) as exc:
            # Identity mutation is terminal; a duplicate begin or invalid step
            # request is merely rejected and leaves a resumable handoff intact.
            if isinstance(handoff, dict) and operation in {"begin", "verify"} and str(exc).startswith("canonical-"):
                handoff["status"] = "rejected"; handoff["rejected_reason"] = str(exc)
                data["updated_at"] = iso_now(); backup_state(sf); atomic_write_json(sf, stamp_metadata(data, cwd))
            print(f"ERROR: executor handoff rejected: {exc}", file=sys.stderr); sys.exit(2)
        data["updated_at"] = iso_now(); backup_state(sf); atomic_write_json(sf, stamp_metadata(data, cwd))
    print(json.dumps({"ok": True, "operation": operation, "executor_handoff": handoff}, ensure_ascii=False))


def cmd_executor_handoff_begin(args): _cmd_executor_handoff(args, "begin")
def cmd_executor_handoff_verify(args): _cmd_executor_handoff(args, "verify")
def cmd_executor_handoff_record(args): _cmd_executor_handoff(args, "record")
def cmd_executor_handoff_complete(args): _cmd_executor_handoff(args, "complete")


def _emit_handoff_error(exc: Exception) -> None:
    print(f"ERROR: {exc}", file=sys.stderr)


def cmd_handoff_publish(args):
    cwd = Path.cwd()
    try:
        payload = load_handoff_payload(args.input)
        result = publish_evidence_handoff(cwd, args.topic, payload, producer_session=args.producer_session)
    except EvidenceHandoffError as exc:
        _emit_handoff_error(exc)
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False))


def cmd_handoff_await(args):
    cwd = Path.cwd()
    try:
        result = await_evidence_handoff(cwd, args.topic, after_seq=args.after_seq, timeout_sec=args.timeout_sec)
    except EvidenceHandoffTimeout:
        print(json.dumps({
            "status": "timeout",
            "topic": args.topic,
            "after_seq": args.after_seq,
            "timeout_sec": args.timeout_sec,
        }, ensure_ascii=False))
        sys.exit(3)
    except EvidenceHandoffError as exc:
        _emit_handoff_error(exc)
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False))


def cmd_handoff_verify(args):
    cwd = Path.cwd()
    try:
        result = verify_evidence_handoff(args.path, expect_digest=args.expect_digest, cwd=cwd)
    except EvidenceHandoffError as exc:
        _emit_handoff_error(exc)
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False))


def _cap_for_findings(findings: list[dict]) -> float | None:
    counts = {"High": 0, "Medium": 0, "Low": 0}
    for finding in findings:
        counts[finding["severity"]] += 1
    if counts["High"] >= 1:
        return 3.0
    if counts["Medium"] >= 3:
        return 3.5
    if 1 <= counts["Medium"] <= 2:
        return 4.0
    if counts["Low"] >= 4:
        return 4.3
    if 2 <= counts["Low"] <= 3:
        return 4.5
    if counts["Low"] == 1:
        return 4.7
    return None


def _apply_reviewer_caps(review: dict) -> tuple[dict, list[dict]]:
    scores = {key: float(review["scores"][key]) for key in REVIEW_SCORE_KEYS}
    cap_log = []
    for axis in REVIEW_SCORE_KEYS:
        axis_findings = [f for f in review.get("findings", []) if f.get("axis") == axis]
        cap = _cap_for_findings(axis_findings)
        if cap is not None and scores[axis] > cap:
            cap_log.append({"perspective": review["perspective"], "axis": axis, "original": scores[axis], "cap": cap})
            scores[axis] = cap
    return scores, cap_log


def _parse_reviewer_windows(specs: list[str], valid_perspectives: set[str]) -> list[dict]:
    """#282: '--reviewer-window P=<start>..<end>' 申告を検証して構造化する.

    観測専用の self-report (orchestrator が spawn/return 時刻を申告する)。
    review JSON の verbatim 契約には触れない。形式不正・未知 perspective・
    重複・end<start は strict に exit 2 で拒否する。
    """
    windows = []
    seen = set()
    for spec in specs:
        head, sep, times = spec.partition("=")
        start_raw, tsep, end_raw = times.partition("..")
        if not sep or not tsep or not head.strip() or not start_raw.strip() or not end_raw.strip():
            print(f"ERROR: --reviewer-window の形式が不正です: {spec!r} "
                  "(期待: '<perspective>=<start_iso>..<end_iso>')", file=sys.stderr)
            sys.exit(2)
        perspective, start_raw, end_raw = head.strip(), start_raw.strip(), end_raw.strip()
        if perspective not in valid_perspectives:
            print(f"ERROR: --reviewer-window の perspective {perspective!r} が "
                  f"--input の reviewer に存在しません", file=sys.stderr)
            sys.exit(2)
        if perspective in seen:
            print(f"ERROR: --reviewer-window の perspective {perspective!r} が重複しています", file=sys.stderr)
            sys.exit(2)
        seen.add(perspective)
        try:
            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        except ValueError:
            print(f"ERROR: --reviewer-window の時刻が ISO 8601 として解釈できません: {spec!r}", file=sys.stderr)
            sys.exit(2)
        # naive/aware 混在は比較で TypeError になるため、naive は UTC とみなして正規化する
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end < start:
            print(f"ERROR: --reviewer-window の end が start より前です: {spec!r}", file=sys.stderr)
            sys.exit(2)
        windows.append({
            "perspective": perspective,
            "started_at": start_raw,
            "ended_at": end_raw,
            "_start": start,
            "_end": end,
        })
    return windows


def _observe_parallel_execution(windows: list[dict]):
    """#282: 全ペアの時間帯が重なれば True、1 ペアでも disjoint なら False、判定不能は 'unknown'."""
    if len(windows) < 2:
        return "unknown"
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            a, b = windows[i], windows[j]
            if not (a["_start"] < b["_end"] and b["_start"] < a["_end"]):
                return False
    return True


def _consensus_score(max_delta: float) -> float:
    if max_delta <= 0.5:
        return 5.0
    if max_delta <= 1.0:
        return 4.0
    if max_delta <= 1.5:
        return 3.0
    if max_delta <= 2.0:
        return 2.0
    return 1.0


_ARTIFACT_HEADING_RE = re.compile(r"^ {0,3}(#{1,3})(?:[ \t]+(.*?))?[ \t]*$")
_ARTIFACT_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ARTIFACT_STUB_RE = re.compile(
    r"(?:"
    r"recorded below(?:\s+once\s+.+\s+completes)?|"
    r"once\s+.+\s+completes|"
    r"will be (?:recorded|populated|filled)|"
    r"to be (?:recorded|determined)|"
    r"TBD|"
    r"後で記録|"
    r"完了後に記録|"
    r"review-finalize\s*(?:後|完了後)(?:に記録)?"
    r")[.!。]?[ \t]*",
    re.IGNORECASE,
)
_ARTIFACT_LINT_MAX_BYTES = 4 * 1024 * 1024


def _read_regular_artifact_utf8(path: Path) -> str:
    """Read a bounded regular file from one descriptor without blocking on FIFOs."""
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"artifact path is not a regular file: {path}")
        chunks = []
        remaining = _ARTIFACT_LINT_MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _ARTIFACT_LINT_MAX_BYTES:
            raise OSError(
                "artifact exceeds lint size limit "
                f"({_ARTIFACT_LINT_MAX_BYTES} bytes): {path}"
            )
        return payload.decode("utf-8")
    finally:
        os.close(fd)


def lint_artifact_completeness(artifact_text: str) -> list[dict]:
    """Detect empty H1-H3 sections and forward-reference-only stubs."""
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    fence_char: str | None = None
    fence_length = 0
    for line in artifact_text.splitlines():
        fence_match = _ARTIFACT_FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            suffix = fence_match.group(2)
            if fence_char is None:
                # CommonMark: a backtick fence info string containing a backtick
                # is not a valid opener. Tilde-fence info has no such restriction.
                if marker[0] != "`" or "`" not in suffix:
                    fence_char = marker[0]
                    fence_length = len(marker)
            elif (
                marker[0] == fence_char
                and len(marker) >= fence_length
                and not suffix.strip()
            ):
                fence_char = None
                fence_length = 0
        heading_match = None if fence_char is not None else _ARTIFACT_HEADING_RE.match(line)
        if heading_match:
            raw_heading = (heading_match.group(2) or "").strip()
            # An optional ATX closing sequence is recognized only when separated
            # from the title by whitespace. Thus `Score###` remains literal text.
            heading = (
                ""
                if re.fullmatch(r"#+", raw_heading)
                else re.sub(r"[ \t]+#+[ \t]*$", "", raw_heading).strip()
            )
            current = (heading, [])
            sections.append(current)
        elif current is not None:
            current[1].append(line)

    findings = []
    for heading, body_lines in sections:
        content_lines = [line.strip() for line in body_lines if line.strip()]
        if not content_lines:
            findings.append({
                "heading": heading,
                "kind": "empty-section",
                "excerpt": "",
            })
            continue
        if all(_ARTIFACT_STUB_RE.fullmatch(line) for line in content_lines):
            findings.append({
                "heading": heading,
                "kind": "stub-forward-reference",
                "excerpt": "\n".join(content_lines),
            })
    return findings


def _lint_state_artifact(cwd: Path, data: dict) -> tuple[list[dict], str]:
    """Lint the canonical artifact identity, with a read-only legacy fallback."""
    if data.get("artifact_applicability") == "not-applicable":
        return [], "skipped"
    artifact_path, canonical = artifact_path_from_state(data)
    if not artifact_path:
        return (
            ([], "missing")
            if data.get("artifact_applicability") in {"producing", "pending"}
            else ([], "skipped")
        )
    if not canonical:
        try:
            path = Path(artifact_path).expanduser()
            if not path.is_absolute():
                path = cwd / path
            path = path.resolve()
            try:
                path.relative_to(cwd.resolve())
            except UnicodeError:
                raise
            except ValueError:
                print(
                    "WARN #351: artifact lint skipped: "
                    f"path outside project root: {artifact_path}",
                    file=sys.stderr,
                )
                return [], "skipped"
            artifact_text = _read_regular_artifact_utf8(path)
        except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
            print(f"WARN #351: artifact lint skipped: {exc}", file=sys.stderr)
            return [], "skipped"
        findings = lint_artifact_completeness(artifact_text)
        return findings, "findings" if findings else "clean"
    try:
        if data.get("artifact_applicability") == "producing":
            _, payload = validate_artifact_identity(data, cwd)
        else:
            _, payload = capture_artifact_identity(
                cwd,
                artifact_path,
                str(data.get("session_id") or "legacy"),
                canonical=canonical,
            )
        try:
            artifact_text = payload.decode("utf-8")
        except UnicodeError as exc:
            if data.get("artifact_applicability") == "producing":
                raise ArtifactContractError("artifact is not valid UTF-8") from exc
            raise
    except ArtifactContractError:
        if data.get("artifact_applicability") == "producing":
            raise
        print("WARN #351: artifact lint skipped: invalid artifact contract", file=sys.stderr)
        return [], "skipped"
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
        print(f"WARN #351: artifact lint skipped: {exc}", file=sys.stderr)
        return [], "skipped"
    findings = lint_artifact_completeness(artifact_text)
    return findings, "findings" if findings else "clean"


def cmd_aggregate_reviews(args):
    """Aggregate mission-review/1 reviewer JSON into push-score compatible scoring JSON."""
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    if args.iteration < 1:
        print("ERROR: --iteration は 1 以上で指定してください", file=sys.stderr)
        sys.exit(2)
    outcome = _command_outcome(args, "aggregate-reviews", "ok")
    try:
        revision_scope = _revision_scope_from_args(args)
        _validate_revision_scope(cwd, revision_scope)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    # #326: critic scope 記録の hard gate。#309 の guidance 層は next を呼ばない
    # orchestrator に bypass される実測 (disc-v3) があるため、集計側で fail-closed に
    # 強制する。escape hatch は作らない (#240 の合意偽装防止と同思想)。
    if args.iteration >= 2:
        try:
            _gate_state = json.loads(sf.read_text())
        except (OSError, json.JSONDecodeError):
            _gate_state = {}
        if _gate_state.get("critic_has_new_scope") is None:
            print(
                "ERROR: iteration >= 2 の集計には critic_has_new_scope の記録が必要です (#326)。"
                " critic の実行計画テーブルから判定し、"
                "`mission-state.py set critic_has_new_scope='false'` (全ステップが既存 finding id のみ)"
                " または `'true'` (new を含む) を実行してから再集計してください。",
                file=sys.stderr,
            )
            raise CommandOutcomeExit(2, "expected-gate")
    input_paths = getattr(args, "input", None) or []
    input_refs = getattr(args, "input_refs", None) or []
    if not input_paths and not input_refs:
        print("ERROR: --input または --input-ref を少なくとも 1 件指定してください", file=sys.stderr)
        sys.exit(2)
    loaded_reviews = [_load_review_json(path, args.iteration) for path in input_paths]
    imported_refs: list[dict] = []
    if input_refs:
        try:
            source_state = json.loads(sf.read_text())
            for reference_path in input_refs:
                review, metric, reference = _load_imported_review(
                    cwd, source_state, reference_path, args.iteration
                )
                loaded_reviews.append((review, metric))
                imported_refs.append(reference)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: review import reference rejected: {exc}", file=sys.stderr)
            sys.exit(2)
    reviews = [review for review, _metric in loaded_reviews]
    reviewer_output_metrics = [
        {"perspective": review["perspective"], **metric}
        for review, metric in loaded_reviews
    ]
    for metric in reviewer_output_metrics:
        if (
            metric["prose_bytes"] > REVIEW_PROSE_BYTES_WARN
            or metric["prose_ratio"] > REVIEW_PROSE_RATIO_WARN
        ):
            print(
                "WARN #353: reviewer output exceeds bounded template guidance "
                f"(perspective={metric['perspective']}, "
                f"prose_bytes={metric['prose_bytes']}, "
                f"prose_ratio={metric['prose_ratio']:.3f})",
                file=sys.stderr,
            )

    min_reviewers = getattr(args, "min_reviewers", None)
    if min_reviewers is not None and len(reviews) < min_reviewers:
        state_reviewer_count = None
        try:
            state_reviewer_count = json.loads(sf.read_text()).get("reviewer_count")
        except (OSError, json.JSONDecodeError):
            state_reviewer_count = None
        shortage_guidance = build_guidance(
            "review-finalize",
            "min-reviewers",
            {
                "iteration": args.iteration,
                "reviewer_count": state_reviewer_count,
                "latest_review_input_ref": input_refs[0] if input_refs else None,
            },
        )
        print(
            f"ERROR: reviewer 数不足 (期待 {min_reviewers} 名, 実際 {len(reviews)} 名)。"
            " reviewer を追加してやり直してください。",
            file=sys.stderr,
        )
        for line in shortage_guidance:
            print(line, file=sys.stderr)
        raise CommandOutcomeExit(2, "expected-gate", guidance=shortage_guidance)

    scoring_reviews = [r for r in reviews if r.get("scores") is not None]
    if not scoring_reviews:
        print("ERROR: 採点対象 reviewer がありません (scores:null の検証専任のみ)", file=sys.stderr)
        sys.exit(2)

    adjusted_scores = []
    cap_log = []
    excluded = []
    for review in scoring_reviews:
        values = [float(review["scores"][key]) for key in REVIEW_SCORE_KEYS]
        same_score_note = str(review.get("same_score_note") or "")
        if len(set(values)) == 1 and ("全体印象" in same_score_note or "overall impression" in same_score_note.lower()):
            excluded.append({"perspective": review["perspective"], "reason": "same-score overall-impression note"})
            continue
        adjusted, caps = _apply_reviewer_caps(review)
        adjusted_scores.append({"perspective": review["perspective"], "scores": adjusted})
        cap_log.extend(caps)
    if not adjusted_scores:
        print("ERROR: 全採点 reviewer が除外されました (Reviewer 独立性に疑念)", file=sys.stderr)
        raise CommandOutcomeExit(2, "expected-gate")

    axis_values = {
        axis: [entry["scores"][axis] for entry in adjusted_scores]
        for axis in REVIEW_SCORE_KEYS
    }
    items = {
        axis: round(sum(values) / len(values), 2)
        for axis, values in axis_values.items()
    }
    agreement_detail = {
        axis: {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "delta": round(max(values) - min(values), 2),
        }
        for axis, values in axis_values.items()
    }
    review_agreement = None
    if len(adjusted_scores) >= 2:
        max_delta = max(detail["delta"] for detail in agreement_detail.values())
        review_agreement = _consensus_score(max_delta)
    open_high = sum(
        1
        for review in reviews
        for finding in review.get("findings", [])
        if finding.get("severity") == "High"
    )
    # The archive claim is authored by the same pure reducer that later
    # validates the untrusted archive.  Keep the surrounding observability
    # fields, but do not let this writer become a second scoring authority.
    try:
        derived_score = reduce_review_aggregate(reviews, expected_iteration=args.iteration)
    except ValueError as exc:
        print(f"ERROR: review aggregate inputs are invalid: {exc}", file=sys.stderr)
        sys.exit(2)
    items = derived_score["items"]
    open_high = derived_score["open_high"]
    review_agreement = derived_score["review_agreement"]
    agreement_detail = derived_score["agreement_detail"]

    # #282/#350: reviewer 並列実行の観測。2 名以上では全 reviewer の
    # self-report を fail-closed で要求し、実行形態そのものは gate しない。
    valid_perspectives = {review["perspective"] for review in reviews}
    reviewer_windows = _parse_reviewer_windows(
        getattr(args, "reviewer_windows", []) or [], valid_perspectives
    )
    if len(reviews) >= 2:
        reported_perspectives = {window["perspective"] for window in reviewer_windows}
        missing_perspectives = sorted(valid_perspectives - reported_perspectives)
        if missing_perspectives:
            gate_outcome = _command_outcome(args, "aggregate-reviews", "expected-gate")
            if getattr(args, "record_outcome", True):
                _record_command_outcome_only(cwd, gate_outcome)
            _emit_json_command_failure(args, gate_outcome)
            print(
                "ERROR: reviewer window の報告が不足しています。"
                f"不足 perspective: {', '.join(missing_perspectives)}。"
                "報告書式: --reviewer-window <perspective>=<start>..<end>。"
                "#350: 並列実行の検証可能性のため必須",
                file=sys.stderr,
            )
            raise CommandOutcomeExit(2, "expected-gate")
    parallel_execution = _observe_parallel_execution(reviewer_windows)
    reviewer_windows_public = [
        {k: v for k, v in window.items() if not k.startswith("_")}
        for window in reviewer_windows
    ]
    if parallel_execution is False:
        print(
            "WARN: reviewer が直列実行されています (実行時間帯の重なりなし)。"
            "Claude Code では Reviewer を単一メッセージで並列起動してください (#282)。"
            "この warn は観測のみで集計・gate には影響しません。",
            file=sys.stderr,
        )

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        try:
            review_lineage = _current_review_lineage(cwd, data, revision_scope)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        try:
            validate_artifact_state_consistency(data, require_resolved=True)
            artifact_lint, artifact_lint_status = _lint_state_artifact(cwd, data)
        except ArtifactContractError as exc:
            invalidate_artifact_lint_observation(data)
            data["artifact_lint_status"] = "invalid"
            data["updated_at"] = iso_now()
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(data, cwd))
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        if artifact_lint_status not in {"clean", "findings"}:
            data.pop("artifact_lint", None)
        else:
            data["artifact_lint"] = artifact_lint
        data["artifact_lint_status"] = artifact_lint_status
        identity_snapshot = canonical_artifact_identity_snapshot(data)
        if artifact_lint_status in {"clean", "findings"} and identity_snapshot:
            data["artifact_lint_identity"] = identity_snapshot
        else:
            data.pop("artifact_lint_identity", None)
        for finding in artifact_lint:
            print(
                "WARN #351: artifact lint: "
                f"{finding['kind']} at {finding['heading']}",
                file=sys.stderr,
            )
        # #338: 観測結果を state へ永続化し stats で横断集計可能にする (gate 不変)
        data["last_parallel_execution"] = parallel_execution
        if data.get("phase") == "reviewing":
            now = iso_now()
            if isinstance(data.get("activity_current"), dict):
                end_activity_segment(data, now)
            _transition_phase(data, "scoring", now)
            record_activity_event(data, "review-aggregate", now)
            data["updated_at"] = now
        prior_metrics = [
            record for record in data.get("reviewer_output_records", [])
            if isinstance(record, dict) and record.get("iteration") != args.iteration
        ]
        data["reviewer_output_records"] = prior_metrics + [
            {"iteration": args.iteration, **metric}
            for metric in reviewer_output_metrics
        ]
        if getattr(args, "record_outcome", True):
            _append_command_outcome(data, outcome)
        context_mode_expected = _expected_context_mode(data, args.iteration)
        context_manifest_generated = _context_manifest_generated(data, args.iteration)
        if context_mode_expected == "bounded" and not context_manifest_generated:
            print(
                "WARN #352: bounded context expected but no manifest generated",
                file=sys.stderr,
            )
        mission8 = (data.get("mission_id") or "unknown")[:8]
        evidence = {
            "schema": "mission-review-aggregate/1",
            "iteration": args.iteration,
            "inputs": reviews,
            "input_refs": imported_refs,
            "scoring_perspectives": [entry["perspective"] for entry in adjusted_scores],
            "excluded": excluded,
            "cap_log": cap_log,
            "agreement_detail": agreement_detail,
            "open_high": open_high,
            "reviewer_windows": reviewer_windows_public,
            "parallel_execution": parallel_execution,
            "artifact_lint": artifact_lint,
            "artifact_lint_status": artifact_lint_status,
            "context_mode_expected": context_mode_expected,
            "context_manifest_generated": context_manifest_generated,
            "reviewer_output_metrics": reviewer_output_metrics,
            # This is the authoritative, deterministic derivation from the
            # archived review inputs. push-score and mark-passes compare every
            # decision value to it; a digest alone is not semantic binding.
            "score_claim": {
                "iteration": args.iteration, **derived_score,
            },
        }
        evidence_content = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        evidence_digest = "sha256:" + hashlib.sha256(evidence_content).hexdigest()
        evidence_path = state_dir(cwd) / "archive" / f"iter-{args.iteration}-{mission8}-reviews-{evidence_digest[7:23]}.json"
        evidence_ref_path = str(evidence_path.relative_to(cwd))

        out_path = Path(args.out) if args.out else Path("/tmp") / f"mission-scorer-iter-{args.iteration}-{mission8}.json"
        if _same_publish_target(out_path, evidence_path):
            raise CommandOutcomeExit(2, "invalid-input")
        payload = {
            "items": items,
            "notes": f"aggregate-reviews: {len(adjusted_scores)} scoring reviewer(s), {len(reviews) - len(scoring_reviews)} findings-only reviewer(s)",
            "open_high": open_high,
            "findings_evidence_path": evidence_ref_path,
            "review_agreement": review_agreement,
            "agreement_detail": agreement_detail,
            "score_provenance": {
                "score_source": "scoring-json",
                "review_evidence_ref": {
                    "kind": "review-aggregate", "path": evidence_ref_path,
                    "digest": evidence_digest,
                    "generation": evidence_digest[7:23],
                    "revision_scope": revision_scope,
                    **(review_lineage or {}),
                },
                "revision_scope": revision_scope,
            },
        }
        payload_content = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        archive_publish: _PublishedFile | None = None
        out_publish: _PublishedFile | None = None
        try:
            archive_publish = _publish_review_archive_transaction(
                cwd, evidence_path.name, evidence_content,
            )
            if archive_publish.path != evidence_path:
                raise ValueError("review aggregate archive path mismatch")
            out_publish = _publish_output_transaction(
                out_path,
                payload_content,
                forbidden_targets=((archive_publish.directory_identity, archive_publish.path.name),),
            )
            _verify_published_file(archive_publish)
            _verify_published_file(out_publish)
            atomic_write_json(sf, data)
        except BaseException as exc:
            recovery_error: PublishedRollbackRecoveryError | None = None
            if out_publish is not None:
                try:
                    _rollback_published_file(out_publish)
                except PublishedRollbackRecoveryError as rollback_error:
                    recovery_error = rollback_error
                except ValueError as rollback_error:
                    print(f"ERROR: aggregate output rollback rejected: {rollback_error}", file=sys.stderr)
                out_publish = None
            if archive_publish is not None:
                try:
                    _rollback_published_file(archive_publish)
                except PublishedRollbackRecoveryError as rollback_error:
                    if recovery_error is None:
                        recovery_error = rollback_error
                except ValueError as rollback_error:
                    print(f"ERROR: aggregate archive rollback rejected: {rollback_error}", file=sys.stderr)
                archive_publish = None
            if recovery_error is not None:
                raise recovery_error from exc
            if isinstance(exc, ValueError):
                print(f"ERROR: aggregate output rejected: {exc}", file=sys.stderr)
                raise CommandOutcomeExit(2, "invalid-input") from exc
            raise
        finally:
            if out_publish is not None:
                _close_published_file(out_publish)
            if archive_publish is not None:
                _close_published_file(archive_publish)

    result = {
        "ok": True,
        "outcome_kind": "ok",
        "outcome": outcome,
        "out": str(out_path),
        "findings_evidence_path": str(evidence_path),
        "open_high": open_high,
        "items": items,
        "review_agreement": review_agreement,
        "parallel_execution": parallel_execution,
        "artifact_lint": artifact_lint,
        "artifact_lint_status": artifact_lint_status,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(str(out_path))


def _reject_on_score_item_mismatch(args, items: dict) -> None:
    """Reject (exit 2) when self-reported scores INFLATE above the item scores (#122).

    Only the legacy --items path supplies self-reported composite/min_item; the
    --scoring-json path recomputes both from items, so this cannot fire there.
    The gate uses the stored self-reported values, so over-reporting is the
    bypass to close ("全項目 3.0 でも min_item 4.0 と申告すれば合格"). It was
    previously only a WARNING and is now a hard error. Under-reporting is left
    permitted because it is conservative (it can only make the gate stricter).
    """
    numeric_values = [float(v) for v in items.values() if isinstance(v, (int, float)) and not math.isnan(float(v))]
    if not numeric_values:
        return
    item_mean = sum(numeric_values) / len(numeric_values)
    item_min = min(numeric_values)
    errors = []
    if args.composite - item_mean > 0.1:
        errors.append(f"composite={args.composite} > items mean={item_mean:.2f}")
    if args.min_item - item_min > 0.1:
        errors.append(f"min_item={args.min_item} > items min={item_min:.2f}")
    if errors:
        print(
            "ERROR: 自己申告スコアが items 明細より上振れしています (許容 0.1 超): "
            + "; ".join(errors)
            + "。composite/min_item を items から算出した値に下げるか、--scoring-json を使ってください。",
            file=sys.stderr,
        )
        sys.exit(2)


def _validate_consensus_policy(data: dict, items: dict) -> None:
    """Retained call site: new score items are always exactly the four axes."""
    if set(items) != CANONICAL_SCORE_KEYS:
        print("ERROR: 新規 score items は4つの正規採点軸だけで指定してください。", file=sys.stderr)
        sys.exit(2)


def _require_score_resubmit_reason(data: dict, iteration: int, reason: str | None) -> None:
    """Reject a duplicate score before any aggregate or archive side effect."""
    already_scored = any(
        isinstance(entry, dict) and entry.get("iteration") == iteration
        for entry in data.get("score_history", [])
    )
    if already_scored and not reason:
        print(
            f"ERROR: iteration {iteration} は既に採点済みです。"
            ' 同一 iteration を再 push する場合は --resubmit-reason "<理由>" を指定してください (#122)。',
            file=sys.stderr,
        )
        raise CommandOutcomeExit(2, "expected-gate")


def cmd_context_manifest(args):
    """#241: bounded context manifest を生成する.

    reviewer fork に渡す evidence manifest: mission goal, iteration,
    prior findings を state から抽出し JSON で出力する。
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。", file=sys.stderr)
        sys.exit(1)
    data = json.loads(sf.read_text())
    iteration = args.iteration if args.iteration is not None else data.get("iteration", 1)
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
        print("ERROR: --iteration は 1 以上で指定してください", file=sys.stderr)
        sys.exit(2)
    history = data.get("score_history") or []
    prior_findings = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        for f in entry.get("findings_summary", []):
            if isinstance(f, dict):
                prior_findings.append(f)
    manifest = {
        "schema": "mission-context-manifest/1",
        "iteration": iteration,
        "mission_goal": data.get("mission", ""),
        "mission_id": data.get("mission_id", ""),
        "assumptions_path": data.get("assumptions_path", ""),
        "prior_findings": prior_findings,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    digest = "sha256:" + hashlib.sha256(out.read_bytes()).hexdigest()
    generated_at = iso_now()
    with StateLock(lock_file(cwd)):
        current = json.loads(sf.read_text())
        context_manifests = current.get("context_manifests")
        if not isinstance(context_manifests, dict):
            context_manifests = {}
        context_manifests[str(iteration)] = {
            "path": str(out),
            "digest": digest,
            "generated_at": generated_at,
        }
        current["context_manifests"] = context_manifests
        atomic_write_json(sf, current)
    print(json.dumps({
        "ok": True,
        "path": str(out),
        "digest": digest,
        "findings_count": len(prior_findings),
    }, ensure_ascii=False))


def cmd_push_score(args):
    """Phase 5 scoring JSON 生成後、orchestrator が呼ぶ score_history append.

    標準フローでは aggregate-reviews が scoring JSON を生成し、
    orchestrator (mission/SKILL.md Phase 5 直後) がそのパスを渡してこのコマンドを呼ぶ。
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    if not _nonnegative_int(args.open_high):
        print("ERROR: --open-high は bool ではない 0 以上の整数で指定してください", file=sys.stderr)
        sys.exit(2)
    if args.iteration < 1:
        print("ERROR: --iteration は 1 以上で指定してください", file=sys.stderr)
        sys.exit(2)
    scoring_payload = None
    if args.scoring_json:
        # ADR-002 Stage 1 (G-1): items を scoring JSON ファイルから読み、
        # composite/min_item を CLI 側で再計算する (orchestrator の転記レイヤを排除)。
        if args.items is not None or args.composite is not None or args.min_item is not None:
            print(
                "ERROR: --scoring-json と --items/--composite/--min-item は併用できません "
                "(composite/min_item は items から CLI が再計算します)。",
                file=sys.stderr,
            )
            sys.exit(2)
        items, json_notes, json_open_high, scoring_payload = _load_scoring_json(args.scoring_json)
        score_axis_values = [float(items[axis]) for axis in REVIEW_SCORE_KEYS]
        args.composite = round(sum(score_axis_values) / len(score_axis_values), 2)
        args.min_item = round(min(score_axis_values), 2)
        # scoring JSON が authoritative: JSON に open_high があれば CLI --open-high より優先
        if json_open_high is not None:
            args.open_high = json_open_high
        if json_notes and not args.notes:
            args.notes = json_notes
    else:
        if args.items is None or args.composite is None or args.min_item is None:
            print(
                "ERROR: --scoring-json を使わない場合は --items/--composite/--min-item が必須です。",
                file=sys.stderr,
            )
            sys.exit(2)
        # G-2: scoring evidence なしの push-score は default reject。
        # DEPRECATED (#226/A-4): MISSION_REQUIRE_SCORING_EVIDENCE=0 は scoring-evidence gate を
        # バイパスする移行期 escape hatch。次のマイナーリリースで削除予定。codex-preflight --strict は
        # この env を検出して exit 2 にする。新規利用は禁止。
        if not args.scoring_output:
            if os.environ.get("MISSION_REQUIRE_SCORING_EVIDENCE") == "0":
                print(
                    "DEPRECATED ESCAPE HATCH: scoring evidence なしの push-score を許可しました "
                    "(MISSION_REQUIRE_SCORING_EVIDENCE=0)。この env は次のマイナーリリースで削除予定です。"
                    " --scoring-json (推奨) または --scoring-output へ移行してください。",
                    file=sys.stderr,
                )
            else:
                print(
                    "ERROR: scoring evidence が必須です。"
                    " --scoring-json (推奨) または --scoring-output を指定してください。",
                    file=sys.stderr,
                )
                sys.exit(2)
        print(
            "DeprecationWarning: push-score の --items 経路は将来のマイナーリリースで削除予定です。"
            " --scoring-json を使用してください (#122)。",
            file=sys.stderr,
        )
        items = _validate_score_args(args)
        _reject_on_score_item_mismatch(args, items)
    _reject_normalized_scale(items)

    with StateLock(lock_file(cwd)), _PublishedFilesTransaction() as published_files:
        data = json.loads(sf.read_text())
        _reject_active_provider_mutation(data, "push-score")
        lease_decision = _enforce_session_lease_for_write(sf, data)
        _validate_consensus_policy(data, items)
        try:
            provenance = _validate_provenance(
                scoring_payload.get("score_provenance") if scoring_payload else None,
                # A push is always a new score write, even if an attacker
                # deleted/downgraded schema_version from an active state.
                require=True,
            )
        except ValueError as exc:
            print(f"ERROR: provenance: {exc}", file=sys.stderr)
            sys.exit(2)
        if provenance is not None:
            try:
                _revalidate_score_provenance(cwd, {
                    "iteration": args.iteration, "items": items,
                    "composite": args.composite, "min_item": args.min_item,
                    "open_high": args.open_high,
                    "review_agreement": scoring_payload.get("review_agreement") if scoring_payload else None,
                    "agreement_detail": scoring_payload.get("agreement_detail") if scoring_payload else None,
                    "score_provenance": provenance,
                }, data,
                                              require_scoring_artifact=False)
            except ValueError as exc:
                print(f"ERROR: provenance: {exc}", file=sys.stderr)
                sys.exit(2)
        now = iso_now()
        # #122: 同一 iteration の再 push は gate 迂回の温床 (低スコア push 後に
        # 高スコアで上書き)。再 push には差し替え理由を必須化する。旧 entry は履歴として残す。
        resubmit_reason = getattr(args, "resubmit_reason", None)
        _require_score_resubmit_reason(data, args.iteration, resubmit_reason)
        entry = {
            "iteration": args.iteration,
            "composite": args.composite,
            "min_item": args.min_item,
            "items": items,
            "timestamp": now,
        }
        if resubmit_reason:
            entry["resubmit_reason"] = resubmit_reason
        if args.notes:
            entry["notes"] = args.notes
        # Issue #3: open_high を保存 (mark-passes gate で参照)
        entry["open_high"] = getattr(args, "open_high", 0)
        if args.scoring_json:
            if scoring_payload.get("findings_evidence_path") is not None:
                entry["findings_evidence_path"] = scoring_payload["findings_evidence_path"]
            if "review_agreement" in scoring_payload:
                review_agreement = scoring_payload["review_agreement"]
                entry["review_agreement"] = None if review_agreement is None else float(review_agreement)
            if scoring_payload.get("agreement_detail") is not None:
                entry["agreement_detail"] = scoring_payload["agreement_detail"]
            # archive を state 書き込みより先に行う (crash 時に state が実在しない
            # scoring_evidence_path を指す dangling reference を防ぐ。他 archive 系と同順序)
            entry["score_source"] = provenance["score_source"] if provenance else "scoring-json"
            if provenance is not None:
                entry["score_provenance"] = provenance
                if provenance["score_source"] == "manual-import":
                    entry["manual_evidence_ref"] = provenance["manual_evidence_ref"]
                else:
                    entry["review_evidence_ref"] = provenance["review_evidence_ref"]
                entry["revision_scope"] = provenance["revision_scope"]
            scoring_publish = published_files.add(
                _archive_scoring_json(cwd, args.iteration, data, entry, scoring_payload)
            )
            scoring_json_archived_to = str(scoring_publish.path)
            artifact_state_path = str(scoring_publish.path.relative_to(cwd))
            entry["scoring_evidence_path"] = artifact_state_path if provenance is not None else scoring_json_archived_to
            artifact_bytes = _read_bounded_review_evidence(cwd, artifact_state_path)
            if provenance is not None:
                provenance["scoring_evidence_ref"] = {
                    "kind": "scoring-artifact",
                    "path": artifact_state_path,
                    "digest": "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
                }
        data.setdefault("score_history", []).append(entry)
        data["failure_ledger"] = _derive_failure_ledger(cwd, data["score_history"])
        # 改善2: top-level iteration を同期 (orchestrator の set 取りこぼしで
        # iteration と score_history 長が不整合になる問題への対処)。
        data["iteration"] = args.iteration
        _transition_phase(data, "scoring", now)  # M4 (2026-06-10): phase 自動更新
        # Q11: stagnation_count 自動更新。
        # append 後の score_history から前エントリの composite を取得し改善幅を判定。
        # 初回 (前エントリなし) は 0 にリセット。改善幅 >= 0.1 も 0 にリセット。
        # 改善幅 < 0.1 は +1 する (後方互換: data.get で既存 state にも対応)。
        history = data["score_history"]
        if len(history) >= 2:
            prev_composite = history[-2].get("composite")
            cur_composite = entry["composite"]
            if _is_valid_composite(prev_composite) and 0 <= (cur_composite - prev_composite) < 0.1:
                data["stagnation_count"] = data.get("stagnation_count", 0) + 1
            else:
                data["stagnation_count"] = 0
        else:
            data["stagnation_count"] = 0
        transaction_outcome = getattr(args, "transaction_outcome", None)
        if transaction_outcome is not None:
            _append_command_outcome(data, transaction_outcome)
        data["updated_at"] = now
        # A successful provenance-bearing score is the only migration path.
        data["schema_version"] = SCHEMA_VERSION
        data = stamp_metadata(data, cwd)
        backup_state(sf)
        if args.scoring_json:
            _verify_published_file(scoring_publish)
        atomic_write_json(sf, data, lease_decision=lease_decision)

    if args.scoring_json:
        archived_to = scoring_json_archived_to  # StateLock 内で archive 済み (dangling path 防止)
    elif args.scoring_output:
        archived_to = _archive_scoring_output(cwd, args.scoring_output, args.iteration, data, entry)
    else:
        archived_to = None

    result = {"ok": True, "appended": entry}
    if archived_to:
        result["archived_to"] = archived_to
    print(json.dumps(result, ensure_ascii=False))


def cmd_review_finalize(args):
    """#283: aggregate-reviews → push-score を 1 コマンドで実行する (Phase 5 transactional).

    既存の cmd_aggregate_reviews / cmd_push_score をそのまま内部呼び出しし、
    validator (min-reviewers / strict review 検証 / findings gate / #122 再 push 保護) を複製しない。
    集計が exit 非0 なら push-score には到達せず、score_history は不変 (atomic)。
    """
    outcome = _command_outcome(args, "review-finalize", "ok")
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if sf.exists():
        try:
            with StateLock(lock_file(cwd)):
                _require_score_resubmit_reason(
                    json.loads(sf.read_text()), args.iteration, args.resubmit_reason,
                )
        except SystemExit as error:
            _emit_finalize_failure(args, "", error, site="resubmit")
            raise error
    agg_args = argparse.Namespace(
        iteration=args.iteration,
        input=args.input,
        input_refs=getattr(args, "input_refs", []) or [],
        out=args.out,
        json=True,
        min_reviewers=args.min_reviewers,
        reviewer_windows=args.reviewer_windows,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        record_outcome=False,
    )
    agg_stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(agg_stdout):
            cmd_aggregate_reviews(agg_args)
    except SystemExit as error:
        _emit_finalize_failure(args, agg_stdout.getvalue(), error)
        raise error
    agg_result = json.loads(agg_stdout.getvalue())

    push_args = argparse.Namespace(
        iteration=args.iteration,
        composite=None,
        min_item=None,
        items=None,
        scoring_json=agg_result["out"],
        notes=args.notes,
        scoring_output=None,
        open_high=0,
        resubmit_reason=args.resubmit_reason,
        record_outcome=False,
        transaction_outcome=outcome,
    )
    push_stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(push_stdout):
            cmd_push_score(push_args)
    except SystemExit as error:
        _emit_finalize_failure(args, push_stdout.getvalue(), error, site="push")
        raise error
    push_result = json.loads(push_stdout.getvalue())

    print(json.dumps({
        "ok": True,
        "outcome_kind": "ok",
        "outcome": outcome,
        "aggregate": agg_result,
        "push": push_result,
    }, ensure_ascii=False, indent=2))


def cmd_closeout(args):
    """#283: mark-passes → next を 1 コマンドで実行する (Phase 6 transactional).

    標準経路専用で --force は受け付けない (override は mark-passes を直接使う)。
    gate 未達なら mark-passes の exit code を保ち、next 相当の guidance を
    JSON で返す。state は mark-passes が exit 前に書き込まないため不変。
    """
    mp_args = argparse.Namespace(force=False, reason=None, approved_by_user=False)
    mp_stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(mp_stdout):
            cmd_mark_passes(mp_args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        # 防衛的分岐: 現在 cmd_mark_passes は sys.exit(0) を発しない (成功時は return) ため
        # code == 0 には通常到達しない。到達した場合のみ成功経路へ継続する。
        if code != 0:
            next_stdout = io.StringIO()
            with contextlib.redirect_stdout(next_stdout):
                cmd_next(argparse.Namespace())
            print(json.dumps({
                "ok": False,
                "closeout": "mark-passes-gate-failed",
                "next": json.loads(next_stdout.getvalue()),
            }, ensure_ascii=False, indent=2))
            sys.exit(code)

    next_stdout = io.StringIO()
    with contextlib.redirect_stdout(next_stdout):
        cmd_next(argparse.Namespace())
    print(json.dumps({
        "ok": True,
        "mark_passes": json.loads(mp_stdout.getvalue()),
        "next": json.loads(next_stdout.getvalue()),
    }, ensure_ascii=False, indent=2))


def _unclosed_optional_specialist_skills(data: dict) -> list[str]:
    """#189: `specialists_selected` に明示選定された specialist で、invocation 終端ログ
    (skipped/unavailable/failed/completed 等、どのステータスでもよい) が一件もないものを検出する。

    `explicitly_selected_specialist_skills` (specialists_selected のみ) を使う点が重要:
    `selected_specialist_skills` (共有関数。specialists_phase_plan の providers も含む) を
    使うと、phase_plan にしか登場しない specialist を誤って「未クローズ」と WARN する
    偽陽性になる (mission-audit.py の specialist_invocation_gap_skills と同じ理由で除外)。

    非 --force 経路では required specialist は cmd_mark_passes の
    accounting_required/result_required gate がこのコードに到達する前に exit 2 で止めるため、
    ここに残るのは常に optional。ただし --force はこれらの gate を丸ごと skip するため、
    --force 経路では required specialist も unclosed になり得る — 呼び出し側 (cmd_mark_passes)
    は --force 時にこの WARN 自体を出さないことで「optional のため」という文言の誤りを避ける。
    hard gate ではなく WARN (mark-passes 自体は成功させる) — optional specialist の
    graceful degradation を維持しつつ、クローズアウト漏れを可視化する (#189)。
    """
    selected = _accounting_selected_specialist_skills(data)
    terminal = _accounting_terminal_invoked_specialist_skills(data)
    return sorted(selected - terminal)


def cmd_mark_passes(args):
    """合格マーク。score_history の最新 entry を threshold gate で検証する.

    - score_history が空 -> exit 2 (採点未実施)
    - composite < threshold -> exit 2
    - min_item < MIN_ITEM_THRESHOLD (3.5) -> exit 2 (採点した items のいずれかが閾値未満)
    - すべて通過なら passes=true, loop_active=false を書き込み
    - --force --reason "<理由>" --approved-by-user は人手 override (バリデーション skip + force_reason 保存)
      (#185: --approved-by-user はユーザーの明示承認宣言。orchestrator が自律的に付けてはならない)
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state file が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    force = bool(getattr(args, "force", False))
    reason = getattr(args, "reason", None)
    approved_by_user = bool(getattr(args, "approved_by_user", False))
    specialist_waiver = (getattr(args, "specialist_waiver", None) or "").strip()
    approval_ref = getattr(args, "approval_evidence_ref", None)
    approved_actor = getattr(args, "approved_actor", None)
    approved_at = getattr(args, "approved_at", None)
    reason_code = getattr(args, "reason_code", None)
    approval_verifier = getattr(args, "approval_verifier", None)

    if force and not reason:
        print("ERROR: --force を指定する場合は --reason \"<理由>\" が必須です。", file=sys.stderr)
        sys.exit(2)
    if force and not approved_by_user:
        print(
            "ERROR: --force を指定する場合は --approved-by-user も必須です (#185)。"
            " これはユーザーが明示的に override を承認したことの宣言であり、"
            " orchestrator が自律的に付けてはならないフラグです。"
            " ユーザーから明示的な override 指示があった場合のみ指定してください。",
            file=sys.stderr,
        )
        sys.exit(2)

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        if force:
            try:
                latest_scope = next((entry.get("revision_scope") for entry in reversed(data.get("score_history", []))
                                     if isinstance(entry, dict) and isinstance(entry.get("revision_scope"), dict)),
                                    {"kind": "not-applicable", "reason_code": "non-git"})
                # Bind the approval to the exact terminal state that this
                # invocation will persist.  The shared projection excludes
                # force_approval and timestamps, so it can be reproduced by
                # the post-write assertion and historical audit.
                terminal_object = dict(data)
                terminal_object.update({"passes": True, "loop_active": False, "passes_forced": True})
                _write_terminal_outcome(terminal_object)
                request = build_approval_request(
                    session_id=data.get("session_id"), mission_id=data.get("mission_id"),
                    revision_scope=latest_scope, terminal_object_digest=terminal_state_digest(terminal_object),
                    approval_evidence_ref=approval_ref, approved_actor=approved_actor, approved_at=approved_at,
                    reason_code=reason_code, event_nonce=secrets.token_hex(32),
                )
                verification = verify_force_approval(request, approval_verifier, cwd=cwd)
                if _force_envelope_replayed(cwd, verification):
                    raise ValueError("approval request or receipt was already consumed")
                validate_receipt_binding(cwd, verification)
                if data.get("force_approval"):
                    raise ValueError("approval envelope was already consumed")
            except ValueError as exc:
                print(f"ERROR: force approval: {exc}", file=sys.stderr)
                sys.exit(2)
        try:
            validate_artifact_state_consistency(data, require_resolved=True)
        except ArtifactContractError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        now = iso_now()
        threshold = data.get("threshold", DEFAULT_THRESHOLD)
        history = data.get("score_history", [])

        if not force:
            # 改善3b: composite を持つ直近エントリで判定 (末尾に進捗ノート等の
            # composite 欠損エントリが混入していても gate を壊さない)。
            # Select the newest declared score before validating its immutable
            # provenance.  Filtering malformed values first would misreport a
            # forged manual score as "not scored" and skip its typed boundary.
            scored = [h for h in history if isinstance(h, dict) and "composite" in h]
            if not scored:
                print("ERROR: 採点未実施。`push-score` を先に呼んでください。", file=sys.stderr)
                sys.exit(2)
            latest = scored[-1]
            try:
                _revalidate_score_provenance(cwd, latest, data)
            except ValueError as exc:
                print(f"ERROR: provenance: {exc}", file=sys.stderr)
                sys.exit(2)
            # Findings evidence must be reconciled before applying the High
            # gate. A High finding caps its axis at 3.0, so evaluating the
            # generic score gates first would make this dedicated safety gate
            # unreachable for otherwise valid, reducer-derived evidence.
            _validate_findings_evidence_gate(cwd, latest)
            # Issue #3: unresolved High findings always prevent a pass.
            open_high = latest.get("open_high") or 0
            if open_high > 0:
                print(
                    f"ERROR: 未解決 High が {open_high} 件あるため合格にできません。High 指摘を全て解消してから再採点してください。",
                    file=sys.stderr,
                )
                sys.exit(2)
            composite = latest.get("composite")
            min_item = latest.get("min_item")
            if composite is None or composite < threshold:
                print(
                    f"ERROR: composite {composite} < threshold {threshold} のため合格にできません。Critic を起動し次イテレーションへ進んでください。",
                    file=sys.stderr,
                )
                sys.exit(2)
            if min_item is None or min_item < MIN_ITEM_THRESHOLD:
                print(
                    f"ERROR: min_item {min_item} < {MIN_ITEM_THRESHOLD} のため合格にできません (採点した items のいずれかが {MIN_ITEM_THRESHOLD} 未満)。Critic を起動し次イテレーションへ進んでください。",
                    file=sys.stderr,
                )
                sys.exit(2)
            # Issue #126: reviewer agreement は composite から独立した gate として扱う。
            _validate_review_agreement_gate(latest)
            artifact_error = _artifact_gate_error(data, cwd)
            if artifact_error:
                print(f"ERROR: {artifact_error}", file=sys.stderr)
                sys.exit(2)
            if _specialist_selection_checkpoint_expected(data) and not _has_specialist_selection_checkpoint(data):
                print(
                    "ERROR: specialist selection checkpoint missing before pass: "
                    "record task_profile.primary and specialists_decision.policy, "
                    "including fallback/degraded policy when no external specialist is used.",
                    file=sys.stderr,
                )
                sys.exit(2)
            checkpoint_error = _specialist_selection_checkpoint_error(data)
            if checkpoint_error:
                print(f"ERROR: {checkpoint_error}", file=sys.stderr)
                sys.exit(2)
            decision = data.get("specialists_decision")
            if isinstance(decision, dict) and decision.get("decision") == "selected":
                invocation_gaps = selected_without_terminal_invocations(data)
                if invocation_gaps and not specialist_waiver:
                    skills = ", ".join(item["skill"] for item in invocation_gaps)
                    print(
                        "ERROR: terminal specialist invocation missing before pass: "
                        f"{skills}. Record a terminal result or pass --specialist-waiver <reason>.",
                        file=sys.stderr,
                    )
                    sys.exit(2)
            specialist_report = candidate_accounting_report(data)
            if specialist_report.get("accounting_required"):
                skills = ", ".join(c["skill"] for c in specialist_report.get("required_unaccounted_candidates", []))
                print(
                    "ERROR: specialist accounting required before pass: "
                    f"{skills}. Record used/skipped/unavailable/failed evidence or use user-approved --force.",
                    file=sys.stderr,
                )
                sys.exit(2)
            if specialist_report.get("result_required"):
                skills = ", ".join(c["skill"] for c in specialist_report.get("result_required_unmet_candidates", []))
                print(
                    "ERROR: required specialist result evidence missing before pass: "
                    f"{skills}. Required providers must produce completed/inline-applied/skill-tool-applied evidence or use user-approved --force.",
                    file=sys.stderr,
                )
                sys.exit(2)
        else:
            print(
                f"WARNING: --force によりバリデーションを skip して passes=true を書き込みます。reason={reason!r}",
                file=sys.stderr,
            )

        if data.get("artifact_applicability") == "producing":
            artifact = _artifact_state(data)
            identity_present = any(
                key in artifact for key in ("path", "digest", "size", "producer_run_id")
            )
            coverage = _artifact_profile_coverage(cwd, data)
            if identity_present:
                try:
                    validate_artifact_identity(data, cwd)
                except ArtifactContractError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    sys.exit(2)
                if (
                    coverage.get("gate_active")
                    and (
                        data.get("artifact_lint_status") not in {"clean", "findings"}
                        or not artifact_lint_observation_matches(data)
                    )
                ):
                    print(
                        "ERROR: artifact lint observation is missing for a profile with an active coverage gate",
                        file=sys.stderr,
                    )
                    sys.exit(2)
            elif coverage.get("gate_active"):
                print("ERROR: artifact path is missing", file=sys.stderr)
                sys.exit(2)
            else:
                print(
                    "WARN: artifact coverage gate is not active for this profile; "
                    "recording a terminal missing observation",
                    file=sys.stderr,
                )

        data["passes"] = True
        data["loop_active"] = False
        data["passes_forced"] = force  # 改善1: force-pass を機械可読に記録 (stats で集計)
        _transition_phase(data, "done", now)  # M4 (2026-06-10): phase 自動更新
        _write_terminal_outcome(data)
        data["updated_at"] = now
        if force:
            data["force_reason"] = reason
            data["force_approved_by_user"] = approved_by_user  # #185
            data["force_approval"] = verification
            if verification["request"]["terminal_object_digest"] != terminal_state_digest(data):
                print("ERROR: force approval terminal state binding changed before write", file=sys.stderr)
                sys.exit(2)
            data["force_approval"]["consumed"] = True
        elif specialist_waiver:
            data["specialist_waiver"] = {
                "reason": specialist_waiver,
                "selection_id": _current_selection_id(data),
                "recorded_at": now,
            }
        backup_state(sf)
        atomic_write_json(sf, data)
        # #11: aggregate 更新も同じ StateLock 内で行う (lock 外だと並列 mark で lost update)
        _remove_from_aggregate(cwd, resolve_session_id())
        # #189: --force は accounting_required/result_required gate ごと skip するため、
        # unclosed に required specialist が混入し得る。「optional のため」という文言が
        # 誤りになるので --force 経路ではこの WARN 自体を出さない。
        unclosed = [] if force else _unclosed_optional_specialist_skills(data)
    if unclosed:
        print(
            "WARNING [#189]: selected specialist に invocation 終端ログがありません: "
            f"{', '.join(unclosed)}。"
            " `mission-state.py specialists log-invocation --status skipped --reason \"<理由>\"` 等で"
            " クローズアウトしてください (optional specialist のため mark-passes は成功させています)。",
            file=sys.stderr,
        )
    output = {"ok": True, "passes": True, "forced": force}
    if force:
        output["force_approved_by_user"] = approved_by_user
    print(json.dumps(output))


def cmd_supersede_reviews(args):
    """Terminalize older review generations without deleting their raw records."""
    cwd = Path.cwd()
    group = args.group
    if not isinstance(group, str) or not group or "\x00" in group:
        print("ERROR: review group is invalid", file=sys.stderr)
        sys.exit(2)

    def capture(path):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or path.parent != session_dir(cwd):
            raise ValueError("review state path is unsafe")
        payload = path.read_bytes()
        return payload, (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
                         metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)

    def unchanged(path, identity, payload):
        current_payload, current_identity = capture(path)
        return current_identity == identity and current_payload == payload

    with StateLock(lock_file(cwd)):
        members = []
        try:
            for state_path in _iter_state_files(cwd):
                payload, identity = capture(state_path)
                state = json.loads(payload)
                if state.get("review_group_id") != group:
                    continue
                generation = state.get("review_generation")
                if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
                    raise ValueError("review group has an invalid generation")
                members.append((generation, state_path, state, payload, identity))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            sys.exit(2)
        if not members:
            print("ERROR: review group was not found", file=sys.stderr)
            sys.exit(2)
        current_generation = max(item[0] for item in members)
        current = [item for item in members if item[0] == current_generation]
        if len(current) != 1:
            print("ERROR: review group has no single current generation", file=sys.stderr)
            sys.exit(2)
        targets = [item for item in members if item[0] < current_generation]
        if not all(unchanged(path, identity, payload) for _, path, _, payload, identity in members):
            print("ERROR: review group changed during supersede preflight", file=sys.stderr)
            sys.exit(2)
        now = iso_now()
        superseded = []
        originals = [(path, payload) for _, path, _, payload, _ in targets]
        try:
            for generation, state_path, state, payload, identity in targets:
                if not unchanged(state_path, identity, payload):
                    raise ValueError("review state changed during supersede")
                state.update({"passes": False, "loop_active": False,
                              "halt_reason": "superseded by a replacement run", "halt_category": "stale"})
                _transition_phase(state, "halted", now, terminal_trusted_boundary=True)
                _write_terminal_outcome(state)
                state["updated_at"] = now
                path_key = str(state_path.resolve())
                _SUPERSEDE_TERMINAL_PATHS.add(path_key)
                try:
                    atomic_write_json(state_path, state)
                finally:
                    _SUPERSEDE_TERMINAL_PATHS.discard(path_key)
                superseded.append(state.get("session_id"))
            _, current_path, current_state, current_payload, current_identity = current[0]
            if not unchanged(current_path, current_identity, current_payload):
                raise ValueError("current review state changed during supersede")
            current_state["supersedes"] = superseded
            current_state["updated_at"] = now
            atomic_write_json(current_path, current_state)
        except (OSError, ValueError, CommandOutcomeExit):
            for path, payload in originals:
                _atomic_write(path, lambda handle, content=payload: handle.write(content.decode("utf-8")))
            print("ERROR: supersede transaction was rolled back", file=sys.stderr)
            sys.exit(2)
    print(json.dumps({"ok": True, "group": group, "current_generation": current_generation, "superseded": superseded}))


def cmd_mark_halt(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state file が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    category = _normalize_halt_category(getattr(args, "category", None))
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        _reject_active_provider_mutation(data, "mark-halt")
        now = iso_now()
        if category == "awaiting-approval":
            record_activity_event(data, "awaiting-approval", now)
        data["halt_reason"] = args.reason
        data["halt_category"] = category  # #190
        data["loop_active"] = False
        if category == "routed-goal":
            dispatch_fields = _goal_dispatch_route_fields(data)
            data["goal_dispatch_effective"] = dispatch_fields["goal_dispatch_effective"]
            data["goal_dispatch_host"] = dispatch_fields["goal_dispatch_host"]
            if dispatch_fields.get("goal_dispatch_fallback_reason"):
                data["goal_dispatch_fallback_reason"] = dispatch_fields["goal_dispatch_fallback_reason"]
            else:
                data.pop("goal_dispatch_fallback_reason", None)
        _transition_phase(
            data,
            "halted",
            now,
            terminal_trusted_boundary=category == "stale",
        )  # M4 (2026-06-10): phase 自動更新
        _write_terminal_outcome(data)
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, data)
        # #11: aggregate 更新も同じ StateLock 内で行う (lock 外だと並列 halt で lost update)
        _remove_from_aggregate(cwd, resolve_session_id())
    print(json.dumps({"ok": True, "halt_reason": args.reason, "halt_category": category}))


def cmd_reactivate(args):
    """Reactivate a manually halted mission only with explicit user approval."""
    if not args.approved_by_user:
        print(
            "ERROR: reactivate には --approved-by-user が必須です。",
            file=sys.stderr,
        )
        sys.exit(2)
    approved_reason = sanitize_activity_detail(args.reason)
    if not approved_reason:
        print("ERROR: reactivate の --reason は空にできません。", file=sys.stderr)
        sys.exit(2)

    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state file が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        previous_halt_reason = data.get("halt_reason") or ""
        raw_halt_category = data.get("halt_category")
        previous_halt_category = raw_halt_category if raw_halt_category not in (None, "") else "unknown"
        expected_halt_category = _halt_category_for_confirmation(raw_halt_category)
        previous_phase = data.get("phase") or "unknown"
        if data.get("passes") is True:
            print("ERROR: 合格済み mission は reactivate できません。", file=sys.stderr)
            sys.exit(2)
        if data.get("loop_active") is not False or not previous_halt_reason:
            print("ERROR: reactivate 対象の停止中 mission ではありません。", file=sys.stderr)
            sys.exit(2)
        legacy_stale = _is_legacy_stale_halt(raw_halt_category, previous_halt_reason)
        if expected_halt_category == "stale" or legacy_stale:
            print(
                "ERROR: stale/orphan halt は reactivate ではなく resume を使用してください。",
                file=sys.stderr,
            )
            sys.exit(2)
        if args.expected_category != expected_halt_category:
            print(
                "ERROR: --expected-category が現在の halt_category と一致しません: "
                f"expected={args.expected_category!r} actual={previous_halt_category!r} "
                f"normalized={expected_halt_category!r}",
                file=sys.stderr,
            )
            sys.exit(2)

        now = iso_now()
        audit_entry = {
            "timestamp": now,
            "previous_halt_reason": previous_halt_reason,
            "previous_halt_category": previous_halt_category,
            "previous_phase": previous_phase,
            "approved_reason": approved_reason,
            "approved_by_user": True,
            "target_phase": args.phase,
        }
        history = data.get("reactivation_history")
        if history is not None and not isinstance(history, list):
            print("ERROR: reactivation_history が不正なため再活性化できません。", file=sys.stderr)
            sys.exit(2)
        close_activity_for_terminal(data, now, trusted_boundary=True)
        data["halt_reason"] = ""
        data.pop("halt_category", None)
        data.pop("terminal_outcome", None)
        data.pop("resume_target_phase", None)
        data["loop_active"] = True
        data["phase"] = args.phase
        data["phase_started_at"] = now
        start_activity_segment(
            data,
            "active",
            "resumed-implementation",
            now,
            detail=approved_reason,
            resume=True,
        )
        data.setdefault("reactivation_history", []).append(audit_entry)
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
        _add_to_aggregate(cwd, sf.stem)
    print(json.dumps({"ok": True, "reactivated": True, "audit": audit_entry}, ensure_ascii=False))


def _pid_is_agent(pid: int) -> bool:
    """PID 再利用対策: pid が alive かつ comm がエージェント CLI (claude/codex) であることを確認.

    テスト用: MISSION_FORCE_PID_IS_AGENT=1 が設定されている場合は常に True を返し、
    project_root 不存在チェックのみを切り分けて検証できるようにする。
    注意: この関数を呼ぶ全箇所 (cleanup-stale / refresh-pid 等) に影響するため、本番では設定しないこと。
    """
    # テスト専用バイパス: subprocess テストで _pid_is_agent=True を固定したい場合
    if os.environ.get("MISSION_FORCE_PID_IS_AGENT") == "1":
        return True
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False
    try:
        r = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
        comm = (r.stdout or "").strip()
        return _comm_is_agent(comm)
    except subprocess.TimeoutExpired:
        # 高負荷で ps が応答しない場合は保守的に alive 扱い (誤 halt 防止。
        # cleanup-stale --execute から呼ばれるため False は不可逆な halt に直結する)
        return True
    except Exception:
        return False


def cmd_refresh_pid(args):
    """R1: resume 後に state.pid を現セッションの agent CLI PID に更新.

    既存 pid が alive かつ agent CLI プロセスの場合は --force なしでは拒否。
    dead OR alive だが agent CLI プロセスでない (= PID 再利用) 場合は自動継承。
    --reactivate (デフォルト true) で halt_reason を解除し loop_active=true に復帰。
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。", file=sys.stderr)
        sys.exit(1)
    new_pid = find_agent_pid()
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        now = iso_now()
        current = data.get("activity_current")
        if not (isinstance(current, dict) and current.get("started_at") == now):
            close_activity_for_resume(data, now)
        old_pid = data.get("pid")
        if (
            not _lease_fields_present(data)
            and old_pid
            and isinstance(old_pid, int)
            and old_pid != new_pid
        ):
            # PID 再利用対策: comm が agent CLI でなければ別プロセス → 安全に継承可
            if _pid_is_agent(old_pid) and not args.force:
                print(
                    f"ERROR: 既存の owner pid={old_pid} が agent CLI プロセスとして alive です。"
                    f" 別セッションが現役の可能性があるため拒否しました。"
                    f" 強制継承するには --force を指定してください。",
                    file=sys.stderr,
                )
                sys.exit(2)
        data["pid"] = new_pid
        # halt 解除 + ループ再アクティベート (resume → orphan halt フローからの復帰用)
        prev_halt = data.get("halt_reason", "")
        prev_category = data.get("halt_category")
        prev_loop = data.get("loop_active", False)
        legacy_reactivatable_halt = _is_legacy_stale_halt(prev_category, prev_halt)
        was_reactivatable_halt = prev_category == "stale" or legacy_reactivatable_halt
        target_phase = data.get("resume_target_phase")
        phase_can_reactivate = data.get("phase") != "halted" or target_phase in {
            "planning",
            "executing",
            "reviewing",
            "scoring",
        }
        reactivated = (
            was_reactivatable_halt
            and not getattr(args, "no_reactivate", False)
            and phase_can_reactivate
        )
        restored_phase = False
        if reactivated:
            if data.get("phase") == "halted":
                data["phase"] = target_phase
                data["phase_started_at"] = now
                data.pop("resume_target_phase", None)
                restored_phase = True
            data["halt_reason"] = ""
            data.pop("halt_category", None)
            data.pop("terminal_outcome", None)
            data["loop_active"] = True
            _add_to_aggregate(cwd, sf.stem)  # F-4: 再活性化分を active_sessions へ戻す
        if not restored_phase:
            _resume_phase_timing(data, now)
        if data.get("loop_active") is not False and not data.get("activity_current"):
            start_phase_default_activity(data, now)
        data["updated_at"] = now
        backup_state(sf)
        with _lease_write_reason(getattr(args, "lease_reason", None)):
            atomic_write_json(sf, data)
    print(json.dumps({
        "ok": True,
        "old_pid": old_pid,
        "new_pid": new_pid,
        "reactivated": reactivated,
        "prev_halt_reason": prev_halt,
        "prev_loop_active": prev_loop,
    }))


def _capture_command_output(fn, ns) -> tuple[int, str]:
    """Run a cmd_* function in-process, capturing its stdout JSON and exit code.

    Used by `resume` to compose existing subcommands without duplicating their
    logic. stderr is left untouched (errors surface to the user naturally). A
    SystemExit is caught so one step's exit does not abort the whole sequence.

    Note: redirect_stdout mutates process-global sys.stdout, so this is not
    thread-safe. `resume` runs the steps sequentially in a single thread, so
    this is fine; do not call it from concurrent threads.
    """
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            fn(ns)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return code, buf.getvalue()


def cmd_resume(args):
    """#123: compaction/resume 復帰を 1 コマンドに統合する.

    順序は固定 (refresh-pid → cleanup-empty → cleanup-stale → next)。refresh-pid を
    先に実行することで、自 state の pid が現 agent CLI に更新されてから cleanup-stale が
    走り、自分の (復帰直後は旧 dead pid の) state を誤って orphan halt しない。返り値は
    `next` の出力に resume サマリを添えたもの。
    """
    cwd = Path.cwd()
    dry_run = bool(getattr(args, "dry_run", False))
    resume = {
        "pid_refreshed": False,
        "reactivated": False,
        "cleaned_empty": False,
        "halted_stale": 0,
        "dry_run": dry_run,
        "version_skew": _detect_version_skew(),  # #186: None (no skew) or skew details
    }
    sf = resolve_state_file(cwd)

    # 1. refresh-pid を最優先 (cleanup-stale より必ず先)。state 不在時は skip。
    if sf.exists():
        code, out = _capture_command_output(
            cmd_refresh_pid,
            argparse.Namespace(
                force=bool(getattr(args, "force", False)),
                no_reactivate=False,
                lease_reason="resume",
            ),
        )
        if code not in (0, None):
            # foreign live owner 等 (refresh-pid が stderr に理由を出して exit 済)。
            sys.exit(code)
        resume["pid_refreshed"] = True
        try:
            resume["reactivated"] = bool(json.loads(out).get("reactivated"))
        except (ValueError, AttributeError):
            pass

    # 2. cleanup-empty (空 .mission-state/ を rmdir)。
    _, ce_out = _capture_command_output(cmd_cleanup_empty, argparse.Namespace(path=str(cwd)))
    try:
        resume["cleaned_empty"] = json.loads(ce_out).get("action") == "removed"
    except ValueError:
        pass

    # 3. cleanup-stale --root cwd (dry-run 指定時は --execute しない)。
    _, cs_out = _capture_command_output(
        cmd_cleanup_stale,
        argparse.Namespace(root=str(cwd), execute=not dry_run),
    )
    try:
        resume["halted_stale"] = len(json.loads(cs_out).get("halted", []))
    except ValueError:
        pass

    # 4. next (state から次の 1 手を決定論導出)。
    _, next_out = _capture_command_output(cmd_next, argparse.Namespace())
    try:
        out_obj = json.loads(next_out)
    except ValueError:
        out_obj = {"next_action": "init", "summary": "state を判定できませんでした"}
    out_obj["resume"] = resume
    print(json.dumps(out_obj, ensure_ascii=False))


def cmd_update_project_root(args):
    """P2-1: project_root を正しいパスに更新する (陳腐化救済用).

    project_root が不存在になった state (ディレクトリ移動・rename 等で発生) は
    cleanup-stale に孤児扱いされ続ける。このコマンドで正しいパスに更新することで
    rescue できる (実例: cc-48c91727, project_root=/dev/ccbattle 不存在)。
    state.json が存在するディレクトリの cwd で実行すること。
    legacy state.json も sessions/<sid>.json も両方対応する。
    """
    cwd = Path.cwd()
    # sessions/<sid>.json を優先、なければ legacy state.json にフォールバック
    sf = resolve_state_file(cwd)
    if not sf.exists():
        legacy = state_dir(cwd) / "state.json"
        if legacy.exists():
            sf = legacy
        else:
            print("ERROR: state.json が見つかりません。", file=sys.stderr)
            sys.exit(1)
    new_root = str(Path(args.path).resolve())
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        old_root = data.get("project_root", "")
        data["project_root"] = new_root
        data["updated_at"] = iso_now()
        backup_state(sf)
        atomic_write_json(sf, data)
    print(json.dumps({"ok": True, "old_project_root": old_root, "new_project_root": new_root}))


def cmd_cleanup_empty(args):
    """A-3: 空 .mission-state/ ディレクトリを rmdir."""
    target = Path(args.path).resolve() / ".mission-state"
    if not target.exists():
        print(json.dumps({"ok": True, "action": "nothing", "path": str(target)}))
        return
    contents = list(target.iterdir())
    if not contents:
        target.rmdir()
        print(json.dumps({"ok": True, "action": "removed", "path": str(target)}))
    else:
        print(json.dumps({"ok": True, "action": "skipped", "path": str(target), "contents": [c.name for c in contents]}))


def _terminalize_state_file(
    sf: Path,
    proj: Path,
    *,
    reason: str,
    category: str,
    set_terminal_phase: bool,
    expected_pid: Any = None,
    require_missing_root: bool = False,
    require_stale_no_score: bool = False,
    require_dead_pid: bool = False,
    require_expired_lease: bool = False,
) -> bool:
    """Re-read and revalidate under lock before a bulk terminal write."""
    with StateLock(lock_file(proj)):
        latest = json.loads(sf.read_text())
        if not latest.get("loop_active") or latest.get("passes") or latest.get("halt_reason"):
            return False
        if expected_pid is not None and latest.get("pid") != expected_pid:
            return False
        if require_missing_root:
            stored_root = latest.get("project_root", "")
            if not stored_root or Path(stored_root).exists():
                return False
        if require_stale_no_score:
            age_sec = _state_age_since_update_sec(latest)
            if (
                latest.get("score_history")
                or latest.get("awaiting_user")
                or age_sec is not None and age_sec < _stale_active_seconds()
            ):
                return False
        if require_dead_pid:
            try:
                if _pid_is_agent(int(latest.get("pid") or 0)):
                    return False
            except (TypeError, ValueError):
                pass
        if require_expired_lease and not _expired_lease_without_heartbeat(latest)[0]:
            return False
        now = iso_now()
        sampled = _parse_iso_datetime(now)
        updated = _parse_iso_datetime(latest.get("updated_at"))
        if sampled and updated and sampled < updated:
            now = str(latest["updated_at"])
        latest["halt_reason"] = reason
        latest["halt_category"] = category
        latest["loop_active"] = False
        if set_terminal_phase:
            _transition_phase(
                latest,
                "halted",
                now,
                terminal_trusted_boundary=category == "stale",
            )
        else:
            if category == "stale":
                _accrue_phase_for_terminal_control(
                    latest,
                    latest.get("phase"),
                    now,
                    trusted_boundary=True,
                )
                latest["phase_started_at"] = now
            close_activity_for_terminal(
                latest,
                now,
                trusted_boundary=category == "stale",
            )
        _write_terminal_outcome(latest)
        latest["updated_at"] = now
        _validate_specialist_public_state(latest)
        backup_state(sf)
        # Publish the janitor CAS directly on every terminalize path: the janitor
        # is not a normal writer and must neither impersonate the owner token nor
        # acquire a fresh lease onto a legacy state it is halting (which would
        # also emit a misleading lease carrier for the dead session).
        _atomic_write(
            sf, lambda f: json.dump(latest, f, indent=2, ensure_ascii=False)
        )
        if sf.parent.name == "sessions":
            _remove_from_aggregate(proj, sf.stem)
        return True


def _expired_lease_without_heartbeat(data: dict) -> tuple[bool, str]:
    """Return eligibility and reason for lease-based stale cleanup."""
    expires = parse_iso_datetime(str(data.get("lease_expires_at") or ""))
    if expires is None:
        return False, "lease-expiry-invalid"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    expires = expires.astimezone(timezone.utc)
    if _lease_now() < expires:
        return False, "lease-unexpired"
    heartbeat = parse_iso_datetime(
        data.get("last_activity_at") or data.get("updated_at")
    )
    if heartbeat is not None:
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        heartbeat = heartbeat.astimezone(timezone.utc)
        heartbeat_age = (_lease_now() - heartbeat).total_seconds()
        if heartbeat > expires and heartbeat_age < _lease_ttl_seconds():
            return False, "lease-expired-activity-heartbeat-present"
    return True, "expired-session-lease"


def cmd_cleanup_stale(args):
    """C-4: dead-PID の active state.json を orphan として halt (要 --execute).

    SAFETY: デフォルトは dry-run。--execute を明示しないと halt しない。
    """
    if getattr(args, "root", None):
        search_roots = [Path(args.root)]
    else:
        search_roots = _default_search_roots()
    results = {"halted": [], "would_halt": [], "skipped": [], "errors": [], "warnings": [], "dry_run": not args.execute}
    _pid_sessions: dict[int, list[str]] = {}  # #314: 重複 PID 検出
    for root in search_roots:
        if not root.exists():
            continue
        for sf in _iter_state_files(root):
            try:
                data = json.loads(sf.read_text())
                if not data.get("loop_active"):
                    continue
                if data.get("passes") or data.get("halt_reason"):
                    continue
                if _lease_fields_present(data):
                    lease_stale, lease_reason = _expired_lease_without_heartbeat(data)
                    if not lease_stale:
                        results["skipped"].append({
                            "path": str(sf),
                            "reason": lease_reason,
                            "owner_session_id": data.get("owner_session_id"),
                            "lease_expires_at": data.get("lease_expires_at"),
                        })
                        continue
                    proj = _project_root_of(sf)
                    if args.execute:
                        halted = _terminalize_state_file(
                            sf,
                            proj,
                            reason=(
                                "stale: session lease expired without activity heartbeat "
                                "(cleanup-stale)"
                            ),
                            category="stale",
                            set_terminal_phase=True,
                            require_expired_lease=True,
                        )
                        if halted:
                            results["halted"].append({
                                "path": str(sf),
                                "reason": lease_reason,
                                "owner_session_id": data.get("owner_session_id"),
                            })
                    else:
                        results["would_halt"].append({
                            "path": str(sf),
                            "reason": lease_reason,
                            "owner_session_id": data.get("owner_session_id"),
                            "mission": (data.get("mission") or "")[:80],
                        })
                    continue
                pid = data.get("pid")
                if not pid:
                    results["skipped"].append({"path": str(sf), "reason": "no pid"})
                    continue
                try:
                    _pid_sessions.setdefault(int(pid), []).append(sf.stem)  # #314
                except (TypeError, ValueError):
                    pass
                # alive check: PID が生きていて かつ agent CLI プロセスである場合のみ skip。
                # raw os.kill(pid,0) だけだと PID が別プロセスに再利用された orphan を
                # 「alive」と誤判定して永久放置する (P3-4a, 2026-06-10 検査で発見)
                try:
                    if _pid_is_agent(int(pid)):
                        # P2-1(b): alive agent でも project_root が恒久不在なら孤児扱い。
                        # 「alive なので skip」の保護は一時的なマウント外れ等の保護のためだが、
                        # project_root パスそのものが存在しない場合は「恒久不在」として扱う。
                        # update-project-root コマンドで正しいパスに更新することで救済可能。
                        stored_root = data.get("project_root", "")
                        if stored_root and not Path(stored_root).exists():
                            halt_reason = (
                                f"orphan: project_root not found ({stored_root})"
                                " / update-project-root で救済可能"
                            )
                            proj = _project_root_of(sf)
                            if args.execute:
                                halted = _terminalize_state_file(
                                    sf, proj, reason=halt_reason, category="stale",
                                    set_terminal_phase=False,
                                    expected_pid=pid, require_missing_root=True,
                                )
                                if halted:
                                    results["halted"].append({"path": str(sf), "pid": pid})
                            else:
                                results["would_halt"].append({"path": str(sf), "pid": pid, "mission": (data.get("mission") or "")[:80]})
                        else:
                            age_sec = _state_age_since_update_sec(data)
                            stale_threshold = _stale_active_seconds()
                            # #314: checker 系 role は設計上 score を書かないため、
                            # live-pid no-score 判定から除外する (shared-PID false-stale の主因)。
                            # dead PID になれば従来どおり orphan 経路で回収される。
                            _role = data.get("session_role") or "implementer"
                            if _role != "implementer" and not data.get("score_history"):
                                results["skipped"].append({
                                    "path": str(sf),
                                    "reason": "checker-role-no-score-by-design",
                                    "pid": pid,
                                    "session_role": _role,
                                    "age_sec": age_sec,
                                })
                            elif not data.get("score_history") and (age_sec is None or age_sec >= stale_threshold):
                                halt_reason = (
                                    "stale: active no-score checkpoint exceeded "
                                    f"{stale_threshold}s with live agent pid {pid} (cleanup-stale)"
                                )
                                proj = _project_root_of(sf)
                                if args.execute:
                                    halted = _terminalize_state_file(
                                        sf, proj, reason=halt_reason, category="stale",
                                        set_terminal_phase=True,
                                        expected_pid=pid, require_stale_no_score=True,
                                    )
                                    if halted:
                                        results["halted"].append({"path": str(sf), "pid": pid, "reason": "stale-active-no-score", "age_sec": age_sec})
                                else:
                                    results["would_halt"].append({
                                        "path": str(sf),
                                        "pid": pid,
                                        "reason": "stale-active-no-score",
                                        "age_sec": age_sec,
                                        "mission": (data.get("mission") or "")[:80],
                                    })
                            else:
                                results["skipped"].append({"path": str(sf), "reason": f"pid {pid} alive (agent)", "age_sec": age_sec})
                    else:
                        # #239: pid_source=fallback の場合は PID 消滅を即 orphan 扱いにせず
                        # age + heartbeat/updated_at の複合条件で判定 (false stale 止血)
                        pid_source = data.get("pid_source", "agent")
                        if pid_source == "fallback":
                            age_sec = _state_age_since_update_sec(data)
                            stale_threshold = _stale_active_seconds()
                            if age_sec is not None and age_sec < stale_threshold:
                                results["skipped"].append({
                                    "path": str(sf),
                                    "reason": "fallback-pid-unobserved",
                                    "pid": pid,
                                    "age_sec": age_sec,
                                })
                            else:
                                proj = _project_root_of(sf)
                                halt_reason = (
                                    f"stale: fallback pid {pid} dead, age {age_sec}s >= "
                                    f"{stale_threshold}s threshold (cleanup-stale)"
                                )
                                if args.execute:
                                    halted = _terminalize_state_file(
                                        sf, proj, reason=halt_reason,
                                        category="stale", set_terminal_phase=True,
                                        expected_pid=pid,
                                    )
                                    if halted:
                                        results["halted"].append({"path": str(sf), "pid": pid, "reason": "fallback-stale"})
                                else:
                                    results["would_halt"].append({"path": str(sf), "pid": pid, "reason": "fallback-stale", "mission": (data.get("mission") or "")[:80]})
                        else:
                            proj = _project_root_of(sf)
                            if args.execute:
                                halted = _terminalize_state_file(
                                    sf, proj,
                                    reason=f"orphan: pid {pid} dead or reused (cleanup-stale)",
                                    category="stale", set_terminal_phase=False,
                                    expected_pid=pid, require_dead_pid=True,
                                )
                                if halted:
                                    results["halted"].append({"path": str(sf), "pid": pid, "reason": "orphan-dead-or-reused"})
                            else:
                                results["would_halt"].append({"path": str(sf), "pid": pid, "mission": (data.get("mission") or "")[:80]})
                except Exception as e:
                    results["errors"].append({"path": str(sf), "error": str(e)})
            except Exception as e:
                results["errors"].append({"path": str(sf), "error": str(e)})
    # #314: 同一 PID を複数 active session が共有している場合の可観測性 warning
    for _pid, _sids in sorted(_pid_sessions.items()):
        if len(_sids) > 1:
            results["warnings"].append({
                "kind": "duplicate-pid",
                "pid": _pid,
                "sessions": _sids,
                "note": "複数 session が同一 PID を共有 (親プロセス管理下の並列 mission)。"
                        " stale 判定は last_activity_at ベースで行われる (#310/#314)",
            })
    print(json.dumps(results, indent=2, ensure_ascii=False))


def cmd_list(args):
    """C-4: 全プロジェクトの active state.json 一覧."""
    search_roots = _default_search_roots()
    results = []
    for root in search_roots:
        if not root.exists():
            continue
        for sf in _iter_state_files(root):
            try:
                data = json.loads(sf.read_text())
                project_root = data.get("project_root") or str(_project_root_of(sf))
                results.append({
                    "project_root": project_root,
                    "loop_active": data.get("loop_active"),
                    "passes": data.get("passes"),
                    "halt_reason": data.get("halt_reason"),
                    "iteration": data.get("iteration"),
                    "pid": data.get("pid"),
                    "session_id": data.get("session_id"),
                    "agent": data.get("agent"),
                    "mission_id": data.get("mission_id"),
                    "mission": (data.get("mission") or "")[:80],
                    "updated_at": data.get("updated_at"),
                })
            except Exception as e:
                results.append({"path": str(sf), "error": str(e)})
    print(json.dumps(results, indent=2, ensure_ascii=False))


def _lane_report_wall_clock_sec(state: dict) -> float:
    started = parse_iso_datetime(state.get("started_at"))
    updated = parse_iso_datetime(state.get("updated_at"))
    if not started or not updated:
        return 0.0
    try:
        seconds = (updated - started).total_seconds()
    except TypeError:
        return 0.0
    return seconds if seconds >= 0 else 0.0


def _lane_report_session_role(state: dict) -> str:
    role = state.get("session_role")
    return role if isinstance(role, str) and role else "implementer"


def _lane_report_root_run_id(state: dict):
    root_run_id = state.get("root_run_id")
    return root_run_id if isinstance(root_run_id, str) and root_run_id else None


def _lane_report_session_entry(
    state: dict,
    *,
    slo_minutes: int | None,
) -> dict:
    summary = summarize_activity_states([state])
    role = _lane_report_session_role(state)
    role_summary = summary.get("role_summaries", {}).get(role, {})
    wait_totals = role_summary.get("wait_totals_sec") if isinstance(role_summary, dict) else {}
    wait_totals = wait_totals if isinstance(wait_totals, dict) else {}
    wait_total_sec = 0.0
    normalized_wait_totals: dict[str, float] = {}
    for kind in sorted(WAIT_KINDS):
        seconds = wait_totals.get(kind)
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
            wait_total_sec += float(seconds)
            normalized_wait_totals[kind] = float(seconds)
    observed_active_sec = 0.0
    if isinstance(role_summary, dict):
        active = role_summary.get("observed_active_sec")
        if isinstance(active, (int, float)) and not isinstance(active, bool) and active >= 0:
            observed_active_sec = float(active)
    entry = {
        "session_id": state.get("session_id"),
        "session_role": role,
        "phase": state.get("phase"),
        "wall_clock_sec": _lane_report_wall_clock_sec(state),
        "observed_active_sec": observed_active_sec,
        "wait_totals_sec": normalized_wait_totals,
        "unobserved_gap_sec": float(summary.get("unobserved_gap_sec") or 0.0),
        "wait_total_sec": wait_total_sec,
    }
    if slo_minutes is not None:
        terminal = state.get("phase") in {"done", "halted"} or state.get("loop_active") is False
        entry["slo_breached"] = bool(
            role == "implementer"
            and terminal
            and entry["wall_clock_sec"] > float(slo_minutes) * 60.0
        )
    return entry


def _lane_report_group_entry(root_run_id, sessions: list[dict]) -> dict:
    implementer_wait_sec = 0.0
    non_implementer_active_sec = 0.0
    for session in sessions:
        role = session["session_role"]
        if role == "implementer":
            implementer_wait_sec += float(session["wait_total_sec"])
        else:
            non_implementer_active_sec += float(session["observed_active_sec"])
    return {
        "root_run_id": root_run_id,
        "sessions": sessions,
        "rendezvous_loss_sec": max(0.0, implementer_wait_sec - non_implementer_active_sec),
    }


def _lane_positive_minutes(value: str) -> int:
    minutes = int(value)
    if minutes <= 0:
        raise argparse.ArgumentTypeError("--slo-minutes must be a positive integer")
    return minutes


def cmd_lane_report(args):
    """Read-only lane duration report across current search roots."""
    search_roots = _default_search_roots()
    states: list[dict] = []
    for root in search_roots:
        if not root.exists():
            continue
        for sf in _iter_state_files(root):
            if sf.parent.name != "sessions":
                continue
            try:
                data = json.loads(sf.read_text())
            except Exception:
                continue
            if not _is_mission_state_record(data):
                continue
            states.append(data)
    if not states:
        print("ERROR: lane-report requires at least one mission state", file=sys.stderr)
        sys.exit(1)
    overall = summarize_activity_states(states)
    entries = [
        _lane_report_session_entry(
            state,
            slo_minutes=getattr(args, "slo_minutes", None),
        )
        for state in sorted(states, key=lambda item: str(item.get("session_id") or ""))
    ]
    groups: dict[object, list[dict]] = {}
    for state, entry in zip(sorted(states, key=lambda item: str(item.get("session_id") or "")), entries):
        groups.setdefault(_lane_report_root_run_id(state), []).append(entry)
    grouped_entries = [
        _lane_report_group_entry(
            root_run_id,
            sorted(sessions, key=lambda item: str(item.get("session_id") or "")),
        )
        for root_run_id, sessions in sorted(
            groups.items(),
            key=lambda item: (item[0] is not None, str(item[0]) if item[0] is not None else ""),
        )
    ]
    report = {
        "sessions": entries,
        "role_summaries": overall.get("role_summaries", {}),
        "groups": grouped_entries,
        # 集約意味論: 全 implementer の待機合算 - 従属レーンの実働合算 (下限0)
        "rendezvous_loss_sec": sum(group["rendezvous_loss_sec"] for group in grouped_entries),
    }
    if getattr(args, "slo_minutes", None) is not None:
        report["slo_minutes"] = args.slo_minutes
    print(json.dumps(report, indent=2 if getattr(args, "json", False) else 0, ensure_ascii=False))


def cmd_halt(args):
    """C-4: active state.json に halt_reason を立てて停止."""
    if args.all:
        # 候補1: --root 指定時はその root のみ走査 (テスト分離・スコープ指定)。未指定は従来通り全 root
        search_roots = [Path(args.root)] if getattr(args, "root", None) else _default_search_roots()
        category = _normalize_halt_category(getattr(args, "category", None))
        halted = []
        for root in search_roots:
            if not root.exists():
                continue
            for sf in _iter_state_files(root):
                try:
                    data = json.loads(sf.read_text())
                    if data.get("loop_active") and not data.get("passes") and not data.get("halt_reason"):
                        proj = _project_root_of(sf)
                        changed = _terminalize_state_file(
                            sf, proj, reason=args.reason, category=category,
                            set_terminal_phase=True,
                        )
                        if changed:
                            halted.append(str(proj))
                except Exception as e:
                    print(f"WARN: skip {sf}: {e}", file=sys.stderr)
        print(json.dumps({"ok": True, "halted": halted, "halt_category": category}))
    else:
        if getattr(args, "root", None):
            print("WARN: --root は --all と併用時のみ有効です (無視されました)", file=sys.stderr)
        cmd_mark_halt(args)


def _parse_date_to_iso_prefix(s: str | None) -> str | None:
    """YYYY-MM-DD を返す (そのまま prefix 比較に使う)."""
    if not s:
        return None
    # 簡易 validate
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        print(f"ERROR: --since/--until は YYYY-MM-DD 形式: {s}", file=sys.stderr)
        sys.exit(1)
    return s[:10]


def _matches_period(state: dict, since: str | None, until: str | None) -> bool:
    updated = (state.get("updated_at") or "")[:10]
    if not updated:
        return True  # 日時不明は除外しない (warn は将来)
    if since and updated < since:
        return False
    if until and updated > until:
        return False
    return True


def _median(xs: list) -> float | None:
    """外れ値に頑健な中央値。空なら None."""
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _nearest_rank_percentile(values: list[int], percentile: float) -> int | None:
    """Return an observed integer using the nearest-rank percentile method."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _reviewer_output_stats(states: list[dict]) -> dict:
    """Aggregate valid per-reviewer output observations across session states."""
    records = []
    for state in states:
        state_records = state.get("reviewer_output_records", [])
        if not isinstance(state_records, list):
            continue
        for record in state_records:
            if not isinstance(record, dict):
                continue
            prose_bytes = record.get("prose_bytes")
            prose_ratio = record.get("prose_ratio")
            if (
                not isinstance(prose_bytes, int)
                or isinstance(prose_bytes, bool)
                or prose_bytes < 0
                or not isinstance(prose_ratio, (int, float))
                or isinstance(prose_ratio, bool)
                or not 0 <= float(prose_ratio) <= 1
            ):
                continue
            records.append((prose_bytes, float(prose_ratio)))
    prose_values = [prose_bytes for prose_bytes, _ratio in records]
    return {
        "records": len(records),
        "oversize_warns": sum(
            1 for prose_bytes, prose_ratio in records
            if prose_bytes > REVIEW_PROSE_BYTES_WARN or prose_ratio > REVIEW_PROSE_RATIO_WARN
        ),
        "prose_bytes_p50": _nearest_rank_percentile(prose_values, 0.5),
        "prose_bytes_p90": _nearest_rank_percentile(prose_values, 0.9),
    }


def _collect_states(root: Path) -> list[dict]:
    """root 配下を再帰的にスキャンして state を収集 (現役 + archive、stats 用)。

    glob パターンは _iter_state_files に集約 (重複していた 3 つの glob を統合)。
    """
    states = []
    for sf in _iter_state_files(root, include_archive=True):
        try:
            state = json.loads(sf.read_text())
        except Exception:
            continue
        if not _is_mission_state_record(state):
            continue
        state["_mission_source_path"] = str(sf)
        states.append(state)
    return states


def _discover_project_roots(root: Path) -> list[Path]:
    """Return project roots that contain a .mission-state directory under root."""
    discovered: list[Path] = []
    seen: set[str] = set()
    root = Path(root)
    if root.name == ".mission-state":
        candidate = root.parent
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            discovered.append(candidate)
        return discovered
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        if os.path.basename(dirpath) != ".mission-state":
            continue
        dirnames[:] = []
        candidate = Path(dirpath).parent
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            discovered.append(candidate)
    return discovered


def _collect_learning_brief_states(roots: list[Path]) -> list[dict]:
    """Collect readable session states plus archived terminal generations."""
    states: list[dict] = []
    project_roots: list[Path] = []
    seen_projects: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        states.extend(_collect_states(root))
        for project_root in _discover_project_roots(root):
            key = str(project_root)
            if key in seen_projects:
                continue
            seen_projects.add(key)
            project_roots.append(project_root)
    for project_root in sorted(project_roots, key=lambda path: str(path)):
        state_root = project_root / ".mission-state"
        try:
            compaction = read_state_archive_compaction(state_root)
        except ValueError:
            continue
        if compaction is None:
            continue
        for record in compaction.records:
            canonical_path = record.get("canonical_path")
            if not isinstance(canonical_path, str) or not canonical_path:
                continue
            try:
                canonical_bytes = read_state_archive_file_bytes(project_root, canonical_path)
                canonical_state = json.loads(canonical_bytes.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                continue
            if not _is_mission_state_record(canonical_state):
                continue
            canonical_state["_mission_source_path"] = str(project_root / canonical_path)
            states.append(canonical_state)
    deduped, _duplicate_state_group_count = _dedupe_states(states)
    return deduped


def _is_mission_state_record(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(state.get("mission") and state.get("mission_id") and state.get("session_id"))


def _dedupe_states(states: list[dict]) -> tuple[list[dict], int]:
    """Audit と同じ identity/rank で同一 session の代表 state を選ぶ."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for state in states:
        key = state_identity(
            state,
            source_path=str(state.get("_mission_source_path") or ""),
        )
        groups.setdefault(key, []).append(state)
    deduped = [
        min(
            group,
            key=lambda state: state_dedupe_rank(
                state, str(state.get("_mission_source_path") or "")
            ),
        )
        for group in groups.values()
    ]
    return deduped, sum(1 for group in groups.values() if len(group) > 1)


def _is_valid_composite(c) -> bool:
    """composite が採点値として有効か (数値・bool除外・NaN除外)."""
    return _finite_score(c)


def _latest_composite(history: list) -> float | None:
    """score_history から有効な composite を持つ直近エントリの composite を返す.

    末尾に進捗ノート (composite 欠損) が混入していても直近の採点値を拾う。
    """
    for entry in reversed(history):
        c = entry.get("composite")
        if _is_valid_composite(c):
            return c
    return None


def _build_agent_summary(states: list[dict], classes: list[str] | None = None) -> dict:
    """agent 別 (claude-code/codex/cli/unknown) に total/pass/halt/incomplete を集計する。

    classes (各 state の _classify 結果) を渡すと再計算を避ける (_aggregate と共有)。
    """
    if classes is None:
        classes = [_classify(s) for s in states]
    by_agent: dict = {}
    for s, cls in zip(states, classes):
        ag = s.get("agent") or "unknown"
        b = by_agent.setdefault(ag, {"total": 0, "pass": 0, "halt": 0, "incomplete": 0, "abandoned": 0})
        b["total"] += 1
        b[cls] += 1
    return by_agent


def _build_breakdown(states: list[dict], classes: list[str], keyfn) -> dict:
    """任意キー (project/complexity) 別に total/pass/halt/incomplete/abandoned を集計する."""
    out: dict = {}
    for s, cls in zip(states, classes):
        k = keyfn(s) or "unknown"
        b = out.setdefault(k, {"total": 0, "pass": 0, "halt": 0, "incomplete": 0, "abandoned": 0})
        b["total"] += 1
        b[cls] = b.get(cls, 0) + 1
    return out


def _build_halt_category_breakdown(states: list[dict], classes: list[str]) -> dict:
    """#190: halt したセッションを halt_category 別に集計する (completed 風の自由文 halt と
    障害 halt を区別可能にする)。halt_category 未記録の historical state は 'unknown' に落ちる。"""
    out: dict = {}
    for s, cls in zip(states, classes):
        if cls != "halt":
            continue
        if "halt_category" not in s or s.get("halt_category") == "":
            cat = "unknown"
        elif isinstance(s.get("halt_category"), str):
            cat = s["halt_category"]
        else:
            cat = json.dumps(
                s.get("halt_category"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        out[cat] = out.get(cat, 0) + 1
    return dict(sorted(out.items()))


def _build_iteration_by_key(states: list[dict], keyfn) -> dict:
    """任意キー別に iteration ヒストグラムをネストして返す。

    バケット規則は iteration_histogram と同じ:
      iteration 0-3 → そのまま文字列、4 以上 → "4+"、非整数 → "unknown"
    """
    out: dict = {}
    for s in states:
        k = keyfn(s) or "unknown"
        it = s.get("iteration", 0)
        if isinstance(it, int) and it <= 3:
            bucket = str(it)
        elif isinstance(it, int):
            bucket = "4+"
        else:
            bucket = "unknown"
        tier_hist = out.setdefault(k, {})
        tier_hist[bucket] = tier_hist.get(bucket, 0) + 1
    return out


def _phase_duration_totals(states: list[dict]) -> dict:
    totals: dict = {}
    for state in states:
        durations = state.get("phase_durations_sec")
        if not isinstance(durations, dict):
            continue
        for phase, sec in durations.items():
            if not isinstance(phase, str):
                continue
            value = _finite_nonnegative_phase_seconds(sec)
            if value is None:
                continue
            updated = _finite_nonnegative_phase_seconds(totals.get(phase, 0.0) + value)
            if updated is not None:
                totals[phase] = updated
    return dict(sorted(totals.items()))


def _artifact_lint_counts(states: list[dict]) -> dict:
    counts = {
        "empty_section": 0,
        "stub_forward_reference": 0,
        "clean": 0,
    }
    for state in states:
        lint = state.get("artifact_lint")
        if not isinstance(lint, list):
            continue
        if not lint:
            counts["clean"] += 1
            continue
        for finding in lint:
            if not isinstance(finding, dict):
                continue
            if finding.get("kind") == "empty-section":
                counts["empty_section"] += 1
            elif finding.get("kind") == "stub-forward-reference":
                counts["stub_forward_reference"] += 1
    return counts


def _score_provenance_counts(states: list[dict]) -> dict[str, int]:
    counts = {"verified": 0, "legacy-unverifiable": 0, "invalid": 0}
    for state in states:
        terminal = not bool(state.get("loop_active"))
        source_path = state.get("_mission_source_path")
        project_root = project_root_from_state_path(source_path)
        reader = None
        if isinstance(source_path, str):
            source = Path(source_path)
            parts = source.parts
            if ".mission-state" in parts:
                index = parts.index(".mission-state")
                if index + 2 < len(parts) and parts[index + 1] == "archive" and parts[index + 2].startswith("worktree-"):
                    validation = validate_worktree_archive_bundle(Path(*parts[:index + 3]))
                    persisted_state = {key: value for key, value in state.items() if key != "_mission_source_path"}
                    if validation.status == "valid" and validation.state == persisted_state:
                        reader = validated_archive_evidence_reader(validation)
        for entry in state.get("score_history") or []:
            counts[classify_score_provenance(
                entry, terminal=terminal, project_root=project_root, state=state,
                evidence_reader=reader,
            )] += 1
    return counts


def _command_outcome_counts(states: list[dict]) -> dict[str, int]:
    """Summarize state and failure-sidecar outcomes without trusting paths."""
    sessions: list[tuple[list[dict], int, int]] = []
    for state in states:
        if state.get("_mission_snapshot_record") is True:
            observation = validate_command_outcome_observation(
                state.get("_command_outcome_observation")
            )
            if observation is None:
                observation = observe_state_command_outcomes(state)
            sessions.append((
                observation["records"], observation["invalid_records"],
                observation["corrupt_sidecars"],
            ))
            continue
        source = state.get("_mission_source_path")
        if not isinstance(source, str):
            # Snapshots contain no live sidecars but may carry valid state data.
            raw = state.get("command_outcomes") or []
            if isinstance(raw, list):
                sessions.append((
                    [item for item in raw if isinstance(item, dict)],
                    sum(not isinstance(item, dict) for item in raw), 0,
                ))
            else:
                sessions.append(([], 1, 0))
            continue
        source_path = Path(source)
        root = source_path.parent.parent if source_path.parent.name == "sessions" else source_path.parent
        sid = state.get("session_id")
        if not isinstance(sid, str) or not sid:
            sessions.append(([], 1, 0))
            continue
        token = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16]
        found, bad, damaged = iter_command_outcome_records(state, root, token)
        sessions.append((found, bad, damaged))
    return summarize_command_outcome_sessions(sessions)


def _aggregate(
    states: list[dict], duplicate_state_group_count: int = 0,
    *, observation_now: datetime | None = None,
) -> dict:
    n = len(states)
    pass_rate_summary = summarize_pass_rate_population(
        states,
        now=observation_now,
        stale_after_sec=_stale_active_seconds(),
    )
    if n == 0:
        return {
            "total_sessions": 0, "pass_count": 0, "halt_count": 0,
            "duplicate_state_group_count": duplicate_state_group_count,
            "incomplete_count": 0, "abandoned_count": 0,
            "active_count": 0, "active_no_score_count": 0, "stale_count": 0,
            "raw_pass_rate_numerator": 0, "raw_pass_rate_denominator": 0,
            "raw_pass_rate": None,
            "completed_pass_rate_numerator": 0, "completed_pass_rate_denominator": 0,
            "completed_pass_rate": None,
            "terminal_outcome_counts": {name: 0 for name in TERMINAL_OUTCOMES},
            "terminal_count": 0, "non_terminal_count": 0,
            "role_counts": {name: 0 for name in SESSION_ROLES},
            "implementer_pass_rate_numerator": 0,
            "implementer_pass_rate_denominator": 0,
            "implementer_pass_rate": None,
            "evidence_completion_rate_numerator": 0,
            "evidence_completion_rate_denominator": 0,
            "evidence_completion_rate": None,
            # Deprecated compatibility aliases: stats historically reported raw quality.
            "pass_rate_numerator": 0, "pass_rate_denominator": 0, "pass_rate": None,
            "forced_pass_count": 0, "forced_pass_rate": None,
            "ungated_pass_count": 0, "ungated_pass_rate": None,
            "avg_iterations": None, "avg_final_composite": None,
            "avg_session_duration_sec": None,
            "median_session_duration_sec": None,
            "phase_duration_totals_sec": {},
            "phase_duration_avg_sec": {},
            "by_agent": {},
            "by_project": {}, "by_complexity": {}, "iteration_histogram": {},
            "by_review_tier": {}, "iteration_by_review_tier": {},
            "by_cli_version": {},
            "by_halt_category": {},
            "parallel_review_counts": {"true": 0, "false": 0, "unknown": 0},
            "artifact_lint_counts": _artifact_lint_counts([]),
            "artifact_coverage": summarize_artifact_coverage([]),
            "bounded_context_counts": {
                "expected_bounded": 0,
                "manifest_generated": 0,
                "fallback_full": 0,
            },
            "reviewer_output_stats": _reviewer_output_stats([]),
            "score_provenance_counts": _score_provenance_counts([]),
            "command_outcome_counts": summarize_command_outcomes([]),
            "activity_timing": summarize_activity_states([]),
            "planning_provider_kpis": reduce_planning_provider_kpis([], population_kind="observed"),
            "failure_ledger_counts": failure_ledger_counts([]),
            "iteration_recovery": reduce_iteration_recovery([]),
        }
    # _classify を 1 回だけ評価 (旧実装は pass/halt/incomplete で 3N 回呼んでいた)
    classes = [_classify(s) for s in states]
    pass_count = classes.count("pass")
    halt_count = pass_rate_summary["halt_count"]
    incomplete_count = pass_rate_summary["incomplete_count"]
    abandoned_count = pass_rate_summary["abandoned_count"]
    # 改善1: force-pass (品質ゲート未通過の合格) を集計し可視化する
    forced_pass_count = sum(1 for s in states if s.get("passes") and s.get("passes_forced"))
    # 採点エントリ無しで合格 = 品質ゲート未通過 (set 直叩き or 旧版)。
    # force-pass (理由記録あり) は除外し、無記録バイパスのみ数える。
    ungated_pass_count = sum(
        1 for s in states
        if s.get("passes")
        and _latest_composite(s.get("score_history", [])) is None
        and not s.get("passes_forced")
        and not s.get("force_reason")  # 旧版 force-pass (passes_forced 未記録) も除外
    )
    # #338: reviewer 並列実行の観測集計 (last_parallel_execution 記録済み session のみ)
    parallel_review_counts = {"true": 0, "false": 0, "unknown": 0}
    for s in states:
        lpe = s.get("last_parallel_execution")
        if lpe is True:
            parallel_review_counts["true"] += 1
        elif lpe is False:
            parallel_review_counts["false"] += 1
        elif lpe == "unknown":
            parallel_review_counts["unknown"] += 1
    bounded_context_counts = {
        "expected_bounded": 0,
        "manifest_generated": 0,
        "fallback_full": 0,
    }
    for state in states:
        iteration = state.get("iteration", 1)
        expected_bounded = (
            isinstance(iteration, int)
            and _expected_context_mode(state, iteration) == "bounded"
        )
        generated = _context_manifest_generated(state, iteration)
        if expected_bounded:
            bounded_context_counts["expected_bounded"] += 1
        if generated:
            bounded_context_counts["manifest_generated"] += 1
        if expected_bounded and not generated:
            bounded_context_counts["fallback_full"] += 1
    iterations = [s.get("iteration", 0) for s in states]
    # 改善3b: composite を持つ直近エントリを final とする (末尾の進捗ノート混入に耐える)
    finals = [c for c in (_latest_composite(s.get("score_history", [])) for s in states) if c is not None]
    durations = [d for d in (_duration_sec(s) for s in states) if d is not None and d >= 0]
    # #2 (2026-06-13): agent 別の成績内訳 (起動元ごとの PASS 率可視化)。classes を共有して再計算回避。
    by_agent = _build_agent_summary(states, classes)
    # #6 (2026-06-15): project/complexity 別内訳と iteration ヒストグラム
    by_project = _build_breakdown(states, classes, lambda s: os.path.basename((s.get("project_root") or "unknown").rstrip("/")) or "unknown")
    by_complexity = _build_breakdown(states, classes, lambda s: s.get("complexity") or "Unknown")
    # #180: review_tier 別内訳 (旧 state で review_tier フィールドなし → "unknown")
    by_review_tier = _build_breakdown(states, classes, lambda s: s.get("review_tier") or "unknown")
    iteration_by_review_tier = _build_iteration_by_key(states, lambda s: s.get("review_tier") or "unknown")
    # #186: cli_version 別内訳 (旧 state で cli_version フィールドなし → "unknown")
    by_cli_version = _build_breakdown(states, classes, lambda s: s.get("cli_version") or "unknown")
    by_halt_category = _build_halt_category_breakdown(states, classes)  # #190
    phase_totals = _phase_duration_totals(states)
    activity_timing = summarize_activity_states(states)
    iteration_histogram: dict = {}
    for _it in iterations:
        _k = str(_it) if isinstance(_it, int) and _it <= 3 else ("4+" if isinstance(_it, int) else "unknown")
        iteration_histogram[_k] = iteration_histogram.get(_k, 0) + 1
    return {
        "total_sessions": n,
        "duplicate_state_group_count": duplicate_state_group_count,
        "pass_count": pass_count,
        "halt_count": halt_count,
        "incomplete_count": incomplete_count,
        "abandoned_count": abandoned_count,
        "active_count": pass_rate_summary["active_count"],
        "active_no_score_count": pass_rate_summary["active_no_score_count"],
        "stale_count": pass_rate_summary["stale_count"],
        "raw_pass_rate_numerator": pass_rate_summary["raw_pass_rate_numerator"],
        "raw_pass_rate_denominator": pass_rate_summary["raw_pass_rate_denominator"],
        "raw_pass_rate": pass_rate_summary["raw_pass_rate"],
        "completed_pass_rate_numerator": pass_rate_summary["completed_pass_rate_numerator"],
        "completed_pass_rate_denominator": pass_rate_summary["completed_pass_rate_denominator"],
        "completed_pass_rate": pass_rate_summary["completed_pass_rate"],
        "terminal_outcome_counts": pass_rate_summary["terminal_outcome_counts"],
        "terminal_count": pass_rate_summary["terminal_count"],
        "non_terminal_count": pass_rate_summary["non_terminal_count"],
        "role_counts": pass_rate_summary["role_counts"],
        "implementer_pass_rate_numerator": pass_rate_summary["implementer_pass_rate_numerator"],
        "implementer_pass_rate_denominator": pass_rate_summary["implementer_pass_rate_denominator"],
        "implementer_pass_rate": pass_rate_summary["implementer_pass_rate"],
        "evidence_completion_rate_numerator": pass_rate_summary["evidence_completion_rate_numerator"],
        "evidence_completion_rate_denominator": pass_rate_summary["evidence_completion_rate_denominator"],
        "evidence_completion_rate": pass_rate_summary["evidence_completion_rate"],
        # Deprecated compatibility aliases: stats historically reported raw quality.
        "pass_rate_numerator": pass_rate_summary["raw_pass_rate_numerator"],
        "pass_rate_denominator": pass_rate_summary["raw_pass_rate_denominator"],
        "pass_rate": pass_rate_summary["raw_pass_rate"],
        "forced_pass_count": forced_pass_count,
        "parallel_review_counts": parallel_review_counts,
        "artifact_lint_counts": _artifact_lint_counts(states),
        "artifact_coverage": summarize_artifact_coverage(states),
        "bounded_context_counts": bounded_context_counts,
        "reviewer_output_stats": _reviewer_output_stats(states),
        "score_provenance_counts": _score_provenance_counts(states),
        "command_outcome_counts": _command_outcome_counts(states),
        "forced_pass_rate": forced_pass_count / pass_count if pass_count else None,
        "ungated_pass_count": ungated_pass_count,
        "ungated_pass_rate": ungated_pass_count / pass_count if pass_count else None,
        "avg_iterations": sum(iterations) / n,
        "avg_final_composite": sum(finals) / len(finals) if finals else None,
        "avg_session_duration_sec": sum(durations) / len(durations) if durations else None,
        # median は放置/resume 跨ぎの外れ値に頑健 (avg は max 8000min 級の忘れ session で歪む)
        "median_session_duration_sec": _median(durations),
        "phase_duration_totals_sec": phase_totals,
        "phase_duration_avg_sec": {phase: total / n for phase, total in phase_totals.items()},
        "by_agent": by_agent,
        "by_project": by_project,
        "by_complexity": by_complexity,
        "iteration_histogram": iteration_histogram,
        "by_review_tier": by_review_tier,
        "iteration_by_review_tier": iteration_by_review_tier,
        "by_cli_version": by_cli_version,
        "by_halt_category": by_halt_category,
        "activity_timing": activity_timing,
        "planning_provider_kpis": reduce_planning_provider_kpis(states, population_kind="observed"),
        "failure_ledger_counts": failure_ledger_counts(states),
        "iteration_recovery": reduce_iteration_recovery(states),
    }


def _pct_detail(rate) -> str:
    """合格に対する比率を " / NN% of PASS" 形式で返す (None なら空文字)."""
    return f" / {rate*100:.0f}% of PASS" if rate is not None else ""


def _ratio_detail(stats: dict, prefix: str) -> str:
    numerator = stats[f"{prefix}_numerator"]
    denominator = stats[f"{prefix}_denominator"]
    rate = stats[prefix]
    percentage = f" ({rate * 100:.1f}%)" if rate is not None else " (-)"
    return f"{numerator}/{denominator}{percentage}"


def _rate_detail(stats: dict, prefix: str) -> str:
    return _ratio_detail(stats, f"{prefix}_pass_rate")


def _format_text(stats: dict, since: str | None, until: str | None) -> str:
    period = f"{since or '(all)'} ~ {until or '(now)'}"
    roots = ", ".join(stats.get("roots") or ["(none)"])
    n = stats["total_sessions"]
    fc = stats["avg_final_composite"]
    sd = stats["avg_session_duration_sec"]
    md = stats.get("median_session_duration_sec")
    lines = [
        f"=== /mission stats ({period}) ===",
        f"roots:                    {roots}",
        f"total_sessions:           {n}",
        f"duplicate_state_groups:   {stats.get('duplicate_state_group_count', 0)}",
        f"raw_pass_rate:            {_rate_detail(stats, 'raw')}",
        f"completed_pass_rate:      {_rate_detail(stats, 'completed')}",
        f"implementer_pass_rate:    {_rate_detail(stats, 'implementer')}",
        f"evidence_completion_rate: {_ratio_detail(stats, 'evidence_completion_rate')}",
        f"  PASS:                   {stats['pass_count']}",
        f"    (forced:              {stats['forced_pass_count']}{_pct_detail(stats.get('forced_pass_rate'))})",
        f"    (ungated:             {stats['ungated_pass_count']}{_pct_detail(stats.get('ungated_pass_rate'))})",
        f"  active:                 {stats['active_count']}",
        f"  active-no-score:        {stats['active_no_score_count']}",
        f"  stale:                  {stats['stale_count']}",
        f"  HALT:                   {stats['halt_count']}",
    ]
    by_halt_category = stats.get("by_halt_category") or {}
    if by_halt_category:
        lines.append("    (by category)")
        for cat, cnt in by_halt_category.items():
            lines.append(f"      {cat:<18} {cnt}")
    lines.append("terminal_outcomes:")
    for outcome, count in (stats.get("terminal_outcome_counts") or {}).items():
        lines.append(f"  {outcome:<20} {count}")
    lines.append(f"  {'non_terminal':<20} {stats.get('non_terminal_count', 0)}")
    artifact_coverage = stats.get("artifact_coverage") or {}
    artifact_counts = artifact_coverage.get("counts") or {}
    coverage_value = artifact_coverage.get("coverage")
    coverage_text = f"{coverage_value * 100:.1f}%" if coverage_value is not None else "-"
    lines.extend([
        "artifact_coverage:",
        f"  eligible {artifact_counts.get('eligible', 0)} / observed {artifact_counts.get('observed', 0)} / "
        f"missing {artifact_counts.get('missing', 0)} / invalid {artifact_counts.get('invalid', 0)}",
        f"  clean {artifact_counts.get('clean', 0)} / findings {artifact_counts.get('findings', 0)} / "
        f"skipped {artifact_counts.get('skipped', 0)}",
        f"  coverage {coverage_text} / gate_active {str(artifact_coverage.get('gate_active', False)).lower()} / "
        f"counts_conserved {str(artifact_coverage.get('counts_conserved', False)).lower()}",
    ])
    lines += [
        "score_provenance:       verified {verified} / legacy-unverifiable {legacy} / invalid {invalid}".format(
            verified=(stats.get("score_provenance_counts") or {}).get("verified", 0),
            legacy=(stats.get("score_provenance_counts") or {}).get("legacy-unverifiable", 0),
            invalid=(stats.get("score_provenance_counts") or {}).get("invalid", 0),
        ),
        f"  incomplete:             {stats['incomplete_count']}",
        f"  abandoned:              {stats['abandoned_count']}",
        f"avg_iterations:           {stats['avg_iterations']:.2f}" if stats['avg_iterations'] is not None else "avg_iterations: -",
        f"avg_final_composite:      {fc:.2f}" if fc is not None else "avg_final_composite: -",
        f"avg_session_duration:     {sd/60:.1f} min ({sd:.0f}s)" if sd is not None else "avg_session_duration: -",
        f"median_session_duration:  {md/60:.1f} min ({md:.0f}s)" if md is not None else "median_session_duration: -",
    ]
    phase_totals = stats.get("phase_duration_totals_sec") or {}
    if phase_totals:
        lines.append("phase_duration_totals:")
        for phase, sec in sorted(phase_totals.items()):
            # #188: 過去の無検証 set phase= (typo 等) で混入した不正キーを明示する。
            invalid_note = "" if phase in VALID_PHASES else " (invalid: 過去の無検証 set で混入)"
            lines.append(f"  {phase:<14} {sec/60:.1f} min ({sec:.0f}s){invalid_note}")
    activity = stats.get("activity_timing") or {}
    if activity:
        coverage = activity.get("coverage_ratio")
        coverage_text = f"{coverage * 100:.1f}%" if coverage is not None else "-"
        lines.extend([
            "activity_timing:",
            f"  observed:       {activity.get('observed_total_sec', 0.0):.0f}s",
            f"  unclassified:   {activity.get('unclassified_sec', 0.0):.0f}s",
            f"  coverage:       {coverage_text}",
            f"  unobserved gap: {activity.get('unobserved_gap_sec', 0.0):.0f}s",
            f"  totals consistent: {str(activity.get('totals_consistent', False)).lower()}",
            f"  segments:       closed {activity.get('closed_segment_count', 0)} / "
            f"open {activity.get('open_segment_count', 0)} / invalid {activity.get('invalid_segment_count', 0)}",
        ])
        for kind, sec in sorted((activity.get("activity_duration_totals_sec") or {}).items()):
            lines.append(f"  kind {kind:<18} {sec:.0f}s")
        for kind, reasons in sorted((activity.get("wait_reason_totals_sec") or {}).items()):
            for reason, sec in sorted(reasons.items()):
                lines.append(f"  wait {kind}/{reason:<18} {sec:.0f}s")
        for task, values in sorted((activity.get("task_duration_percentiles_sec") or {}).items()):
            lines.append(
                f"  task {task:<16} p50 {values.get('p50')}s / p90 {values.get('p90')}s "
                f"(n={values.get('count', 0)})"
            )
        for phase, values in sorted((activity.get("phase_duration_percentiles_sec") or {}).items()):
            lines.append(
                f"  phase {phase:<14} p50 {values.get('p50')}s / p90 {values.get('p90')}s "
                f"(n={values.get('count', 0)})"
            )
    by_agent = stats.get("by_agent") or {}
    if by_agent:
        lines.append("by_agent:")
        for ag, b in sorted(by_agent.items()):
            lines.append(
                f"  {ag:<14} {b['total']} (PASS {b['pass']} / HALT {b['halt']} / incomplete {b['incomplete']})"
            )
    for label, key in (("by_project", "by_project"), ("by_complexity", "by_complexity"), ("by_review_tier", "by_review_tier"), ("by_cli_version", "by_cli_version")):
        bd = stats.get(key) or {}
        if bd:
            lines.append(f"{label}:")
            for k, b in sorted(bd.items()):
                lines.append(
                    f"  {k:<22} {b['total']} (PASS {b['pass']} / HALT {b['halt']} / incomplete {b['incomplete']} / abandoned {b['abandoned']})"
                )
    hist = stats.get("iteration_histogram") or {}
    if hist:
        lines.append("iteration_histogram:")
        for k in sorted(hist.keys()):
            lines.append(f"  iter {k:<6} {hist[k]}")
    ibrt = stats.get("iteration_by_review_tier") or {}
    if ibrt:
        lines.append("iteration_by_review_tier:")
        for tier in sorted(ibrt.keys()):
            tier_hist = ibrt[tier]
            bucket_str = "  ".join(f"iter {bk}: {bv}" for bk, bv in sorted(tier_hist.items()))
            lines.append(f"  {tier:<14} {bucket_str}")
    return "\n".join(lines)


def cmd_stats(args):
    """全プロジェクトの /mission セッションを横断集計 (read-only)。

    --root 省略時は _default_search_roots() (MISSION_SEARCH_ROOTS、未設定なら cwd) のみをスキャンする。
    --root は複数回指定でき、scripts/mission-audit.py と同じく各 root を集約する。
    Path.home() 全体の rglob (86 秒) を避ける設計 (list/cleanup と統一)。
    """
    requested_roots = [Path(root) for root in args.root] if args.root else None
    roots = requested_roots or _default_search_roots()
    since = _parse_date_to_iso_prefix(args.since)
    until = _parse_date_to_iso_prefix(args.until)
    all_states = []
    observation_now = None
    if args.snapshot:
        try:
            document, roots, observation_now = consume_snapshot_document(
                Path(args.snapshot), requested_roots=requested_roots
            )
        except SnapshotError as error:
            print(f"ERROR: invalid state snapshot: {error}", file=sys.stderr)
            raise SystemExit(2)
        for item in document["records"]:
            state = dict(item["state"])
            state["_mission_source_path"] = str(item["path"])
            state["_mission_snapshot_record"] = True
            if "command_outcome_observation" in item:
                state["_command_outcome_observation"] = item["command_outcome_observation"]
            all_states.append(state)
    else:
        for r in roots:
            if r.exists():
                all_states.extend(_collect_states(r))
    filtered = [s for s in all_states if _matches_period(s, since, until)]
    deduped, duplicate_state_group_count = _dedupe_states(filtered)
    stats = _aggregate(
        deduped, duplicate_state_group_count, observation_now=observation_now
    )
    stats["roots"] = [str(root) for root in roots]
    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print(_format_text(stats, since, until))


def cmd_learning_brief(args):
    """Read-only failure-ledger learning brief across session and archive state roots."""
    requested_roots = [Path(root) for root in args.root] if args.root else None
    roots = requested_roots or _learning_brief_default_roots(Path.cwd())
    try:
        states = _collect_learning_brief_states(roots)
        brief = summarize_learning_brief(
            states,
            weak_phase=getattr(args, "weak_phase", None),
            limit=getattr(args, "limit", 10),
        )
    except LearningContractError as exc:
        print(f"ERROR: learning brief: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if args.json:
        print(json.dumps(brief, indent=2, ensure_ascii=False))
        return
    lines = [
        "recurrence={recurrence} sessions={sessions} weak_phase={weak_phase} general_fix_rule={general_fix_rule}".format(
            **rule,
        )
        for rule in brief["rules"]
    ]
    if lines:
        print("\n".join(lines))


# ---------------------------------------------------------------------------
# Issue #301: resolve-archive
# ---------------------------------------------------------------------------

_VALID_RESOLUTION_STATUSES = frozenset({"resolved", "superseded", "closed"})


def _validate_resolve_archive_path(raw_path: str, cwd: Path) -> Path:
    """resolve-archive の対象ファイルパスを検証して解決した絶対パスを返す。

    拒否条件:
    - .mission-state/ の外 (project root 境界違反 / path escape)
    - パス上の symlink
    - archive/worktree-*/generations/ 以下 (immutable bundle)
    - 通常ファイルでない
    """
    # 絶対パスに解決 (.. を正規化するが symlink はまだ展開しない)
    raw = Path(raw_path)
    candidate = (cwd / raw) if not raw.is_absolute() else raw
    candidate = Path(os.path.normpath(str(candidate)))

    # cwd/.mission-state/ 内であることを確認 (normpath で .. を除去済み)
    state_root = cwd / ".mission-state"
    try:
        candidate.relative_to(state_root)
    except ValueError:
        print(
            f"ERROR: target path must be within <project-root>/.mission-state/; "
            f"got: {candidate}",
            file=sys.stderr,
        )
        sys.exit(2)

    # パス上の各コンポーネントで symlink を検出
    try:
        check = cwd
        for part in candidate.relative_to(cwd).parts:
            check = check / part
            if check.is_symlink():
                print(
                    f"ERROR: symlink detected in path component: {check}; "
                    "resolve-archive does not follow symlinks",
                    file=sys.stderr,
                )
                sys.exit(2)
    except (OSError, ValueError) as exc:
        print(f"ERROR: path validation failed: {exc}", file=sys.stderr)
        sys.exit(2)

    if not candidate.is_file():
        print(f"ERROR: target is not a regular file: {candidate}", file=sys.stderr)
        sys.exit(2)

    # archive/worktree-*/generations/ 以下は immutable bundle — 変更禁止
    try:
        archive_root = state_root / "archive"
        rel_to_archive = candidate.relative_to(archive_root)
        parts = rel_to_archive.parts
        if (
            len(parts) >= 3
            and parts[0].startswith("worktree-")
            and parts[1] == "generations"
        ):
            print(
                "ERROR: target is inside an immutable worktree archive generation "
                f"({parts[0]}/generations/{parts[2]}/...); "
                "resolve-archive cannot modify generation-frozen records",
                file=sys.stderr,
            )
            sys.exit(2)
    except ValueError:
        pass  # not under archive/ — acceptable

    return candidate


def _validate_resolve_archive_record(
    data: dict,
    cwd: Path,
    *,
    allow_active_snapshot: bool = False,
) -> None:
    """resolve-archive の対象 record を検証する。問題があれば sys.exit(2)。

    拒否条件:
    - valid mission state record でない
    - loop_active=true (active session) — allow_active_snapshot=True のとき archive/ 配下のみ skip
    - passes=true (completed session)
    - halt_reason が空 (non-halt terminal)
    - PID が生存中 (belt-and-suspenders active check)
    - project_root が cwd または cwd の配下パスと不一致 (別 project / #318)
    """
    if not _is_mission_state_record(data):
        print(
            "ERROR: target is not a valid mission state record "
            "(missing mission / mission_id / session_id)",
            file=sys.stderr,
        )
        sys.exit(2)

    if data.get("loop_active") is True and not allow_active_snapshot:
        print(
            "ERROR: target record is active (loop_active=true); "
            "cannot annotate an active session with resolve-archive",
            file=sys.stderr,
        )
        sys.exit(2)

    if data.get("passes") is True:
        print(
            "ERROR: target record has passes=true; "
            "resolve-archive only operates on terminal halted records (passes=false)",
            file=sys.stderr,
        )
        sys.exit(2)

    halt_reason = str(data.get("halt_reason") or "").strip()
    # #318: allow_active_snapshot=True は archive/ 配下の frozen snapshot 用 opt-in。
    # mid-flight snapshot は halt_reason が空のまま保存されるため、このチェックも緩和する。
    if not halt_reason and not allow_active_snapshot:
        print(
            "ERROR: target record has no halt_reason; "
            "it is not a terminal halted record",
            file=sys.stderr,
        )
        sys.exit(2)

    # Belt-and-suspenders: PID 生存チェック
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            print(
                f"ERROR: PID {pid} is still alive; "
                "target record may still be active",
                file=sys.stderr,
            )
            sys.exit(2)
        except ProcessLookupError:
            pass  # プロセス不存在 = OK
        except PermissionError:
            pass  # 別プロセス (OS がアクセス拒否) = not our session

    # project_root チェック: cwd と一致、または cwd 配下のパス (#318: worktree 由来 record 対応)
    record_root_raw = str(data.get("project_root") or "").strip()
    if record_root_raw:
        try:
            resolved_record_root = Path(record_root_raw).resolve()
            resolved_cwd = cwd.resolve()
            # Path.is_relative_to は Python 3.9+。文字列 prefix 比較ではなく Path API を使う
            # (文字列比較では /foo/bar-baz が /foo/bar の配下と誤判定する)
            is_same = resolved_record_root == resolved_cwd
            is_subdir = False
            try:
                resolved_record_root.relative_to(resolved_cwd)
                is_subdir = True
            except ValueError:
                pass
            if not (is_same or is_subdir):
                print(
                    f"ERROR: target record belongs to project {record_root_raw!r}, "
                    f"not the current directory {cwd!r}; "
                    "resolve-archive must be run from the record's project root",
                    file=sys.stderr,
                )
                sys.exit(2)
        except (OSError, ValueError) as exc:
            print(
                f"ERROR: could not validate project_root: {exc}",
                file=sys.stderr,
            )
            sys.exit(2)


def _state_archive_reference(cwd: Path, path: Path) -> str:
    try:
        relative = path.relative_to(cwd)
    except ValueError as exc:
        raise WorktreeArchiveError("state archive path is outside the project") from exc
    value = relative.as_posix()
    if not value.startswith(".mission-state/"):
        raise WorktreeArchiveError("state archive path is outside .mission-state")
    return value


def _reject_duplicate_state_archive_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("state archive manifest contains duplicate keys")
        document[key] = value
    return document


def _publish_state_archive_compaction(
    cwd: Path,
    target: Path,
    canonical: Path,
    target_data: dict,
    retain_generations: int,
) -> str:
    """Publish one immutable materialized-state index without deleting lineage."""
    if target == canonical:
        raise WorktreeArchiveError("canonical and superseded paths must differ")
    canonical_ref = _state_archive_reference(cwd, canonical)
    target_ref = _state_archive_reference(cwd, target)
    try:
        canonical_bytes = read_state_archive_file_bytes(cwd, canonical_ref)
        canonical_data = json.loads(canonical_bytes.decode("utf-8"))
        target_bytes = read_state_archive_file_bytes(cwd, target_ref)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorktreeArchiveError("canonical state is unreadable") from exc
    if not _is_mission_state_record(canonical_data):
        raise WorktreeArchiveError("canonical state is not a mission state record")
    if (
        canonical_data.get("session_id") != target_data.get("session_id")
        or canonical_data.get("mission_id") != target_data.get("mission_id")
    ):
        raise WorktreeArchiveError("canonical state identity does not match the superseded record")

    state_root = cwd / ".mission-state"
    try:
        current = read_state_archive_compaction(state_root, verify_superseded=True)
    except ValueError as exc:
        raise WorktreeArchiveError("existing state archive compaction is invalid") from exc
    records = copy.deepcopy(list(current.records)) if current else []
    canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
    target_digest = hashlib.sha256(target_bytes).hexdigest()
    identity = (str(target_data["mission_id"]), str(target_data["session_id"]))
    matches = [
        record for record in records
        if (record["mission_id"], record["session_id"]) == identity
    ]
    if len(matches) > 1:
        raise WorktreeArchiveError("state archive canonical identity is ambiguous")
    if matches:
        record = matches[0]
        if record["canonical_path"] != canonical_ref:
            raise WorktreeArchiveError("state archive canonical path changed")
        record["canonical_sha256"] = canonical_digest
    else:
        record = {
            "canonical_path": canonical_ref,
            "canonical_sha256": canonical_digest,
            "mission_id": identity[0],
            "session_id": identity[1],
            "superseded": [],
        }
        records.append(record)
    superseded = [item for item in record["superseded"] if item["path"] != target_ref]
    superseded.append({"path": target_ref, "sha256": target_digest})
    record["superseded"] = sorted(superseded, key=lambda item: item["path"])
    records.sort(key=lambda item: (item["mission_id"], item["session_id"], item["canonical_path"]))

    if current and records == list(current.records) and retain_generations == current.retention_generations:
        return current.generation
    core = {
        "schema": STATE_ARCHIVE_GENERATION_SCHEMA,
        "previous_generation": current.generation if current else None,
        "retention_policy": {
            "retain_generations": retain_generations,
            "physical_deletion": "forbidden",
        },
        "records": records,
    }
    generation = state_archive_content_digest(core)
    manifest = {
        **core,
        "created_at": iso_now(),
        "content_digest": generation,
    }
    compaction = _ensure_regular_directory_path(
        cwd, (".mission-state", "archive", "compaction")
    )
    compaction.mkdir(parents=True, exist_ok=True)
    generations = _ensure_regular_directory_path(compaction, ("generations",))
    generations.mkdir(parents=True, exist_ok=True)
    generation_root = generations / generation
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    if generation_root.exists() or generation_root.is_symlink():
        if generation_root.is_symlink() or not generation_root.is_dir():
            raise WorktreeArchiveError("state archive generation is not a regular directory")
        existing_ref = (
            Path(".mission-state") / "archive" / "compaction" / "generations"
            / generation / "manifest.json"
        ).as_posix()
        try:
            existing_bytes = read_state_archive_file_bytes(cwd, existing_ref)
            existing_document = json.loads(
                existing_bytes.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_state_archive_keys,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise WorktreeArchiveError("state archive generation collision") from exc
        expected_fields = {
            "schema", "created_at", "previous_generation", "retention_policy",
            "records", "content_digest",
        }
        existing_core = {
            "schema": existing_document.get("schema"),
            "previous_generation": existing_document.get("previous_generation"),
            "retention_policy": existing_document.get("retention_policy"),
            "records": existing_document.get("records"),
        } if isinstance(existing_document, dict) else None
        if (
            not isinstance(existing_document, dict)
            or set(existing_document) != expected_fields
            or existing_core != core
            or existing_document.get("content_digest") != generation
            or state_archive_content_digest(existing_document) != generation
            or not isinstance(existing_document.get("created_at"), str)
            or not existing_document["created_at"]
        ):
            raise WorktreeArchiveError("state archive generation collision")
        manifest = existing_document
        manifest_bytes = existing_bytes
    else:
        staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=generations))
        try:
            atomic_write_json(staging / "manifest.json", manifest, administrative=True)
            os.replace(staging, generation_root)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    try:
        if (
            read_state_archive_file_bytes(cwd, canonical_ref) != canonical_bytes
            or read_state_archive_file_bytes(cwd, target_ref) != target_bytes
        ):
            raise WorktreeArchiveError("state archive source changed before publication")
    except ValueError as exc:
        raise WorktreeArchiveError("state archive source changed before publication") from exc
    atomic_write_json(
        compaction / "current.json",
        {
            "schema": STATE_ARCHIVE_POINTER_SCHEMA,
            "generation": generation,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
        administrative=True,
    )
    return generation


def cmd_resolve_archive(args):
    """#301: terminal halted record に resolution metadata を atomic に追記する。"""
    cwd = Path.cwd().resolve()

    # パスの検証と解決
    target = _validate_resolve_archive_path(args.path, cwd)
    canonical: Path | None = None
    if args.canonical_path is not None:
        if args.status != "superseded":
            print("ERROR: --canonical-path requires --status superseded", file=sys.stderr)
            sys.exit(2)
        if args.retention_generations < 1:
            print("ERROR: --retention-generations must be positive", file=sys.stderr)
            sys.exit(2)
        canonical = _validate_resolve_archive_path(args.canonical_path, cwd)

    # lock ファイルは対象の .mission-state/ 直下
    # (state_root = 対象ファイルから .mission-state を探す)
    target_parts = target.parts
    if ".mission-state" not in target_parts:
        print("ERROR: cannot locate .mission-state in target path", file=sys.stderr)
        sys.exit(2)
    mission_state_idx = target_parts.index(".mission-state")
    target_state_root = Path(*target_parts[: mission_state_idx + 1])
    lock = target_state_root / ".state.lock"

    # #318: --frozen-snapshot フラグの事前検証 (lock 外で行う)
    frozen_snapshot = getattr(args, "frozen_snapshot", False)
    if frozen_snapshot:
        # archive/ 配下のみ有効（sessions/ は従来の active 拒否を維持）
        archive_root = target_state_root / "archive"
        try:
            target.relative_to(archive_root)
        except ValueError:
            print(
                "ERROR: --frozen-snapshot は archive/ 配下のファイルにのみ適用できます; "
                f"sessions/ 配下の active record は従来どおり拒否されます: {target}",
                file=sys.stderr,
            )
            sys.exit(2)

    archive_generation: str | None = None
    with StateLock(lock):
        original_target = target.read_bytes()
        data = json.loads(original_target.decode("utf-8"))

        # #318: --frozen-snapshot フラグが指定された場合、live session の terminal 性を確認する
        if frozen_snapshot:
            session_id = str(data.get("session_id") or "").strip()
            if session_id:
                live_path = target_state_root / "sessions" / f"{session_id}.json"
                if live_path.exists():
                    try:
                        live_data = json.loads(live_path.read_text(encoding="utf-8"))
                        if live_data.get("loop_active") is True:
                            print(
                                f"ERROR: live session {session_id!r} は loop_active=true のまま稼働中です; "
                                "--frozen-snapshot であっても active な live session がある間は resolution を付与できません",
                                file=sys.stderr,
                            )
                            sys.exit(2)
                    except (OSError, json.JSONDecodeError) as exc:
                        print(
                            f"ERROR: live session ファイルの読み取りに失敗しました: {exc}",
                            file=sys.stderr,
                        )
                        sys.exit(2)

        # record の検証
        _validate_resolve_archive_record(data, cwd, allow_active_snapshot=frozen_snapshot)

        now = iso_now()

        # 既存の resolution を history へ append (audit trail 保持)
        if data.get("resolution_status"):
            prev = {"resolution_status": data["resolution_status"]}
            if data.get("resolution_decided_at"):
                prev["resolution_decided_at"] = data["resolution_decided_at"]
            if data.get("resolution_owner_issue"):
                prev["resolution_owner_issue"] = data["resolution_owner_issue"]
            if data.get("resolution_evidence_url"):
                prev["resolution_evidence_url"] = data["resolution_evidence_url"]
            if data.get("resolution_note"):
                prev["resolution_note"] = data["resolution_note"]
            data.setdefault("resolution_history", []).append(prev)

        # resolution metadata を設定し、v3 の明示 outcome だけを同じ transition 内で整合させる。
        # outcome を持たない legacy record には追加しない。
        data["resolution_status"] = args.status
        if "terminal_outcome" in data:
            _write_terminal_outcome(data)
        data["resolution_decided_at"] = now
        if args.owner_issue is not None:
            data["resolution_owner_issue"] = args.owner_issue
        if args.evidence_url is not None:
            data["resolution_evidence_url"] = args.evidence_url
        if args.note is not None:
            data["resolution_note"] = args.note

        # #310: resolution 付与は管理系 janitor 書き込みのため last_activity_at を刻まない
        atomic_write_json(target, data, administrative=True)
        if canonical is not None:
            try:
                archive_generation = _publish_state_archive_compaction(
                    cwd,
                    target,
                    canonical,
                    data,
                    args.retention_generations,
                )
            except (OSError, ValueError, WorktreeArchiveError) as exc:
                atomic_write_bytes(target, original_target)
                print(f"ERROR: state archive compaction failed: {exc}", file=sys.stderr)
                sys.exit(2)

    result: dict = {
        "ok": True,
        "path": str(target),
        "resolution_status": args.status,
        "resolution_decided_at": now,
    }
    if archive_generation is not None:
        result["archive_generation"] = archive_generation
    if args.owner_issue is not None:
        result["resolution_owner_issue"] = args.owner_issue
    if args.evidence_url is not None:
        result["resolution_evidence_url"] = args.evidence_url
    if args.note is not None:
        result["resolution_note"] = args.note

    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))


def _build_parser():
    parser = argparse.ArgumentParser(description="/mission skill state manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="新規ミッションで state.json を初期化")
    p_init.add_argument("--complexity", choices=["Simple", "Standard", "Complex", "Critical"], default=None,
                        help="Phase 1 の複雑度判定結果。指定すると reviewer_count も自動設定 (Simple:1/Standard:2/Complex:3/Critical:3)。未指定は Unknown のまま WARN")
    p_init.add_argument("mission", help="ミッション記述")
    p_init.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_init.add_argument("--max-iter", type=int, default=None, help=f"最大反復回数。未指定={DEFAULT_MAX_ITER} / 0=上限なし(stagnation停止)")
    p_init.add_argument("--budget-minutes", default=None,
                        help="#238: 時間予算 (分・正の有限数)。next が budget_pressure を返し、80%%で warn、100%%超で spawn 系を consider-halt へ差し替える")
    p_init.add_argument("--issue-ref", default=None, dest="issue_ref",
                        help="関連 issue の参照。裸番号 `42` / `#42` / `host:owner/repo#42` / `https://.../issues/42` を受理し、"
                             "#295 で Issue 番号へ正規化して比較する (形式差でも同一 Issue の active session があれば WARN)。"
                             "下流の受理形式に合わせる場合は裸番号 `42` を推奨")
    p_init.add_argument("--files", default=None,
                        help="予定変更ファイルのカンマ区切り project-root 相対パス。同一 active session と重複する場合 WARN")
    p_init.add_argument(
        "--artifact-applicability",
        choices=["producing", "not-applicable", "pending"],
        default="pending",
        help="成果物契約。profile 未確定時は pending、成果物を生成する実行は producing、対象外は not-applicable",
    )
    p_init.add_argument("--role", choices=["implementer", "checker", "planning", "analyze", "release"],
                        default="implementer", dest="session_role",
                        help="#311: session の役割。checker 系は iter=0 での証拠提出が正規出口となり、"
                             "pass-rate は implementer 限定指標で別計上される")
    p_init.add_argument("--force-mission", action="store_true", dest="force_mission",
                        help="#276: Simple タスクでも goal へルーティングせず mission ループを強制する")
    p_init.add_argument("--goal-dispatch", choices=sorted(GOAL_DISPATCH_MODES), default=None,
                        dest="goal_dispatch",
                        help="#355: Simple routing 後の完遂手段。inline または実行ホストの host-native goal 機構")
    p_init.add_argument("--review-tier", choices=list(TIER_REVIEWER_COUNT), default=None,
                        dest="review_tier",
                        help="レビュー深度 (light/standard/full)。未指定は complexity・ミッション記述から auto 導出 (Issue #168)")
    p_init.add_argument("--host-run-id", default=None)
    p_init.add_argument("--root-run-id", default=None)
    p_init.add_argument("--parent-run-id", default=None)
    p_init.add_argument("--child-run-id", default=None)
    p_init.add_argument("--logical-group-id", default=None)
    p_init.add_argument("--review-group-id", default=None)
    p_init.add_argument("--review-perspective", default=None)
    p_init.add_argument("--base-sha", default=None)
    p_init.add_argument("--head-sha", default=None)
    p_init.set_defaults(func=cmd_init)

    p_parallel_init = sub.add_parser("parallel-init", help="create a versioned parallel child manifest")
    p_parallel_init.add_argument("--group-id", required=True)
    p_parallel_init.add_argument("--issue-ref", action="append", default=[])
    p_parallel_init.set_defaults(func=cmd_parallel_init)
    p_parallel_status = sub.add_parser("parallel-status", help="summarize planned parallel children")
    p_parallel_status.add_argument("--group-id", required=True)
    p_parallel_status.set_defaults(func=cmd_parallel_status)
    p_parallel_closeout = sub.add_parser("parallel-closeout", help="terminalize a complete parallel group")
    p_parallel_closeout.add_argument("--group-id", required=True)
    p_parallel_closeout.set_defaults(func=cmd_parallel_closeout)

    p_pregate = sub.add_parser("pregate", help="pre-gate evaluation cache sidecar")
    p_pregate_sub = p_pregate.add_subparsers(dest="pregate_cmd", required=True)
    p_pregate_record = p_pregate_sub.add_parser("record", help="record a pregate evaluation")
    p_pregate_record.add_argument("--issue-ref", required=True)
    p_pregate_record.add_argument("--input", required=True, help="evaluation JSON file path or - for stdin")
    p_pregate_record.set_defaults(func=cmd_pregate)
    p_pregate_check = p_pregate_sub.add_parser("check", help="lookup a pregate evaluation")
    p_pregate_check.add_argument("--issue-ref", required=True)
    p_pregate_check.add_argument("--subject-digest", required=True)
    p_pregate_check.add_argument("--json", action="store_true")
    p_pregate_check.set_defaults(func=cmd_pregate)
    p_pregate_digest = p_pregate_sub.add_parser("digest", help="compute a canonical pregate subject digest")
    p_pregate_digest.add_argument("--input", required=True, help="snapshot JSON file path or - for stdin")
    p_pregate_digest.set_defaults(func=cmd_pregate)

    p_queue = sub.add_parser("queue", help="merge queue sidecar")
    p_queue_sub = p_queue.add_subparsers(dest="queue_cmd", required=True)
    p_queue_enqueue = p_queue_sub.add_parser("enqueue", help="enqueue one merge candidate")
    p_queue_enqueue.add_argument("--issue-ref", required=True)
    p_queue_enqueue.add_argument("--pr-ref", required=True)
    p_queue_enqueue.add_argument("--head-sha", default=None)
    p_queue_enqueue.add_argument("--base-sha", default=None)
    p_queue_enqueue.add_argument("--from-state", action="store_true")
    p_queue_enqueue.add_argument("--depends-on", default=None)
    p_queue_enqueue.add_argument("--session", default=None)
    p_queue_enqueue.set_defaults(func=cmd_queue)
    p_queue_status = p_queue_sub.add_parser("status", help="list queue entries in enqueue order")
    p_queue_status.add_argument("--json", action="store_true")
    p_queue_status.set_defaults(func=cmd_queue)
    p_queue_next = p_queue_sub.add_parser("next", help="return the next merge candidate")
    p_queue_next.add_argument("--json", action="store_true")
    p_queue_next.set_defaults(func=cmd_queue)
    p_queue_verify = p_queue_sub.add_parser("verify", help="validate a candidate against the live base sha")
    p_queue_verify.add_argument("--queue-id", required=True)
    p_queue_verify.add_argument("--current-base-sha", required=True)
    p_queue_verify.set_defaults(func=cmd_queue)
    p_queue_mark = p_queue_sub.add_parser("mark", help="transition one queue entry")
    p_queue_mark.add_argument("--queue-id", required=True)
    p_queue_mark.add_argument("--status", required=True, choices=["merged", "invalidated", "superseded"])
    p_queue_mark.add_argument("--reason", default=None)
    p_queue_mark.set_defaults(func=cmd_queue)

    p_stop_guard = sub.add_parser(
        "stop-guard-observe",
        help="record one digest-based stop-hook block observation",
    )
    p_stop_guard.add_argument("--session-id", required=True)
    p_stop_guard.add_argument("--digest", required=True)
    p_stop_guard.add_argument("--now-epoch", type=int, required=True)
    p_stop_guard.add_argument("--ttl-seconds", type=int, required=True)
    p_stop_guard.set_defaults(func=cmd_stop_guard_observe)

    p_next = sub.add_parser("next", help="ADR-002 Stage 3: state から次の 1 手を JSON で返す (read-only。Codex/compaction 復帰時の進行ガイド)")
    p_next.set_defaults(func=cmd_next)

    p_codex = sub.add_parser("codex-preflight", help="Codex /mission 起動時の state/hook guard readiness を診断")
    p_codex.add_argument("--json", action="store_true", help="診断結果を JSON で出力")
    p_codex.add_argument("--strict", action="store_true", help="active state が無い場合など required_actions があれば exit 2")
    p_codex.add_argument("--require-stop-hook", action="store_true", dest="require_stop_hook",
                         help="Codex Stop hook 未設定を required action として exit 2 にする")
    p_codex.add_argument("--hook-config", default=None,
                         help="Codex hooks.json の明示パス。未指定なら $CODEX_HOME/hooks.json と ~/.codex/hooks.json を確認")
    p_codex.set_defaults(func=cmd_codex_preflight)

    p_permission = sub.add_parser(
        "permission-preflight",
        help="Phase 0 の state/assumptions 書き込み可否を実書き込みで検証",
    )
    p_permission.add_argument("--json", action="store_true", help="診断結果を JSON で出力")
    p_permission.set_defaults(func=cmd_permission_preflight)

    p_get = sub.add_parser("get", help="state.json の値取得")
    p_get.add_argument("--field", default=None)
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set", help="state.json のフィールド更新 (key=value 複数可)")
    p_set.add_argument("kvs", nargs="+")
    p_set.add_argument("--json", action="store_true", help="失敗時に機械可読 outcome を出力")
    _add_command_lineage_arguments(p_set)
    p_set.set_defaults(func=cmd_set, command_outcome_tracking=True)

    p_pass = sub.add_parser("mark-passes", help="threshold gate を満たすとき passes=true, loop_active=false (--force には --reason --approved-by-user --approval-evidence-ref --approved-actor --approved-at --reason-code --approval-verifier が全て必須)")
    p_pass.add_argument("--force", action="store_true",
                        help="threshold gate を skip して強制的に passes=true を書き込む (--reason と --approved-by-user が必須)")
    p_pass.add_argument("--reason", default=None,
                        help="--force の理由 (state.force_reason に記録される)")
    p_pass.add_argument("--approved-by-user", action="store_true", dest="approved_by_user",
                        help="#185: --force と併用必須。ユーザーが明示的に override を承認したことの宣言 "
                             "(orchestrator が自律的に付けてはならない — ユーザーの明示指示があった場合のみ)")
    p_pass.add_argument("--specialist-waiver", default=None,
                        help="selected checkpoint に terminal invocation がない場合の明示理由")
    p_pass.add_argument("--approval-evidence-ref", default=None,
                        help="verified approval record digest (sha256:<64 hex>)")
    p_pass.add_argument("--approved-actor", default=None,
                        help="approval role only; role:<opaque>, never an identity")
    p_pass.add_argument("--approved-at", default=None, help="timezone-aware approval timestamp")
    p_pass.add_argument("--reason-code", default=None,
                        choices=sorted(_PROVENANCE_REASON_CODES))
    p_pass.add_argument("--approval-verifier", default=None,
                        help="configured approval verifier provider (neutral-test is test-only)")
    p_pass.set_defaults(func=cmd_mark_passes)

    p_score = sub.add_parser("push-score", help="score_history に採点結果を append (orchestrator が Phase 5 直後に呼ぶ)")
    p_score.add_argument("--iteration", type=int, required=True)
    p_score.add_argument("--composite", type=float, default=None,
                         help="自己申告 composite (従来経路)。--scoring-json 使用時は指定不可 (CLI が items から再計算する)")
    p_score.add_argument("--min-item", type=float, default=None, dest="min_item",
                         help="自己申告 min_item (従来経路)。--scoring-json 使用時は指定不可")
    p_score.add_argument("--items", default=None, help=f'JSON 形式 (例: {{"mission_achievement": {DEFAULT_THRESHOLD}, "accuracy": {MIN_ITEM_THRESHOLD}, ...}})。--scoring-json 使用時は指定不可')
    p_score.add_argument("--scoring-json", default=None, dest="scoring_json",
                         help="aggregate-reviews などが生成した構造化 JSON 出力パス (ADR-002 Stage 1)。{\"items\": {...}, \"notes\"?, \"open_high\"?} を読み、"
                              "composite/min_item を CLI 側で再計算し、evidence として archive に保存する。転記レイヤを排除する推奨経路")
    p_score.add_argument("--notes", default=None)
    p_score.add_argument("--scoring-output", default=None,
                         help="legacy scorer Markdown 出力ファイルパス。指定すると .mission-state/archive/iter-N-scoring.md にコピー保存される (移行互換)")
    p_score.add_argument("--open-high", type=int, default=0, dest="open_high",
                         help="未解決の High 指摘件数 (mark-passes の gate で使用)。--scoring-json に open_high があればそちらを優先")
    p_score.add_argument("--resubmit-reason", default=None, dest="resubmit_reason",
                         help="同一 iteration を再 push する際に必須 (#122)。理由を score_history entry の resubmit_reason に記録する")
    _add_command_lineage_arguments(p_score)
    p_score.set_defaults(func=cmd_push_score, command_outcome_tracking=True, json=True)

    p_manual_score = sub.add_parser(
        "manual-score-capture",
        help="typed mission-manual-score/1 を安全に archive し、manual-import 用 scoring JSON を生成",
    )
    p_manual_score.add_argument("--input", required=True, help="host user が用意した mission-manual-score/1 JSON")
    p_manual_score.add_argument("--out", required=True, help="push-score --scoring-json に渡す出力 JSON")
    p_manual_score.set_defaults(func=cmd_manual_score_capture)

    p_import = sub.add_parser(
        "review-import",
        help="strict mission-review/1 を検証して state-local immutable evidence に取り込む",
    )
    p_import.add_argument("--iteration", type=int, required=True)
    p_import.add_argument("--json", action="store_true", help="失敗時に機械可読 outcome を出力")
    import_source = p_import.add_mutually_exclusive_group(required=True)
    import_source.add_argument("--input", default=None, help="review JSON の regular file")
    import_source.add_argument("--stdin", action="store_true", help="stdin から review JSON を読む")
    _add_command_lineage_arguments(p_import)
    p_import.set_defaults(
        func=cmd_review_import, command_outcome_tracking=True, json=True,
    )

    p_agg = sub.add_parser("aggregate-reviews", help="#119: mission-review/1 JSON を決定論集計して push-score 互換 scoring JSON を生成")
    p_agg.add_argument("--iteration", type=int, required=True)
    p_agg.add_argument("--input", action="append", default=[],
                       help="reviewer が出力した legacy mission-review/1 JSON。複数指定可")
    p_agg.add_argument("--input-ref", action="append", default=[], dest="input_refs",
                       help="review-import が返した state-local review evidence path。複数指定可")
    p_agg.add_argument("--out", default=None,
                       help="出力する push-score 互換 scoring JSON パス。未指定なら /tmp/mission-scorer-iter-N-<mission8>.json")
    p_agg.add_argument("--json", action="store_true", help="結果を JSON で出力")
    p_agg.add_argument("--min-reviewers", type=int, default=None, dest="min_reviewers",
                       help="#240: 最低 reviewer 数。不足なら exit 2 (合意偽装防止)")
    p_agg.add_argument("--reviewer-window", action="append", default=[], dest="reviewer_windows",
                       help="#282: reviewer 実行時間帯 '<perspective>=<start>..<end>' (ISO 8601)。"
                            "reviewer 2 名以上では全 perspective 分が必須 (不足は exit 2)。"
                            "実行時間帯の重なりは evidence に記録 (#350)")
    p_agg.add_argument("--base-sha", default=None, help="exact reviewed git base SHA (requires --head-sha)")
    p_agg.add_argument("--head-sha", default=None, help="exact reviewed git head SHA (requires --base-sha)")
    _add_command_lineage_arguments(p_agg)
    p_agg.set_defaults(func=cmd_aggregate_reviews, command_outcome_tracking=True)

    p_rf = sub.add_parser("review-finalize",
                          help="#283: aggregate-reviews → push-score を 1 コマンドで実行 (Phase 5 transactional)")
    p_rf.add_argument("--iteration", type=int, required=True)
    p_rf.add_argument("--input", action="append", default=[],
                      help="reviewer が出力した legacy mission-review/1 JSON。複数指定可")
    p_rf.add_argument("--input-ref", action="append", default=[], dest="input_refs",
                      help="review-import が返した state-local review evidence path。複数指定可")
    p_rf.add_argument("--out", default=None,
                      help="scoring JSON の出力パス。未指定なら /tmp/mission-scorer-iter-N-<mission8>.json")
    p_rf.add_argument("--min-reviewers", type=int, default=None, dest="min_reviewers",
                      help="#240: 最低 reviewer 数。不足なら exit 2 (score は push されない)")
    p_rf.add_argument("--reviewer-window", action="append", default=[], dest="reviewer_windows",
                      help="#282: reviewer 実行時間帯 '<perspective>=<start>..<end>'。"
                           "reviewer 2 名以上では全 perspective 分が必須 (不足は exit 2、score は push されない) (#350)")
    p_rf.add_argument("--notes", default=None)
    p_rf.add_argument("--resubmit-reason", default=None, dest="resubmit_reason",
                      help="#122: 同一 iteration の再 push 理由")
    p_rf.add_argument("--base-sha", default=None, help="exact reviewed git base SHA")
    p_rf.add_argument("--head-sha", default=None, help="exact reviewed git head SHA")
    _add_command_lineage_arguments(p_rf)
    p_rf.set_defaults(func=cmd_review_finalize)

    p_closeout = sub.add_parser("closeout",
                                help="#283: mark-passes → next を 1 コマンドで実行 (Phase 6 transactional)。"
                                     "gate 未達なら exit 2 + next guidance。--force 非対応 (override は mark-passes 直接)")
    p_closeout.set_defaults(func=cmd_closeout)

    p_manifest = sub.add_parser("context-manifest",
                                help="#241: bounded context manifest を生成 (reviewer fork 向け)")
    p_manifest.add_argument("--iteration", type=int, default=None,
                            help="対象 iteration (省略時: state の現在 iteration)")
    p_manifest.add_argument("--out", required=True,
                            help="出力 manifest JSON パス")
    p_manifest.set_defaults(func=cmd_context_manifest)

    p_halt = sub.add_parser("mark-halt", help="halt_reason を立てて停止")
    p_halt.add_argument("--reason", required=True)
    p_halt.add_argument("--category", default=None,
                         help=f"#190: halt の種別。有効値: {sorted(HALT_CATEGORIES)}。省略/不正値は 'other' + WARN"
                              " (argparse choices は使わない: _normalize_halt_category が WARN+fallback で検証する)")
    p_halt.set_defaults(func=cmd_mark_halt)

    p_supersede = sub.add_parser("supersede-reviews", help="review groupの旧generationをstale_supersededへ終端化")
    p_supersede.add_argument("--group", required=True)
    p_supersede.set_defaults(func=cmd_supersede_reviews)

    p_reactivate = sub.add_parser(
        "reactivate",
        help="明示的なユーザー承認を監査記録して手動 halt を再活性化",
    )
    p_reactivate.add_argument("--approved-by-user", action="store_true")
    p_reactivate.add_argument("--reason", required=True)
    p_reactivate.add_argument("--expected-category", required=True)
    p_reactivate.add_argument(
        "--phase",
        choices=sorted(VALID_PHASES - {"done", "halted"}),
        default="planning",
    )
    p_reactivate.set_defaults(func=cmd_reactivate)

    p_refresh = sub.add_parser("refresh-pid", help="R1: resume 後に state.pid を現 agent CLI PID に更新 + orphan halt を解除")
    p_refresh.add_argument("--force", action="store_true", help="既存 pid が alive な agent CLI プロセスでも強制継承")
    p_refresh.add_argument("--no-reactivate", action="store_true", help="orphan halt の解除を行わない (純粋に pid だけ更新)")
    p_refresh.set_defaults(func=cmd_refresh_pid)

    p_resume = sub.add_parser("resume", help="#123: 復帰処理を統合実行 (refresh-pid → cleanup-empty → cleanup-stale → next)")
    p_resume.add_argument("--force", action="store_true", help="refresh-pid に渡す: 既存 alive agent pid でも強制継承")
    p_resume.add_argument("--dry-run", action="store_true", dest="dry_run", help="cleanup-stale を dry-run にする (halt しない)。refresh-pid/next は通常実行")
    p_resume.add_argument("--json", action="store_true", help="出力は常に JSON (互換用の no-op フラグ)")
    p_resume.set_defaults(func=cmd_resume)

    p_uproot = sub.add_parser("update-project-root", help="P2-1: project_root を正しいパスに更新 (ディレクトリ移動・rename 後の rescue 用)")
    p_uproot.add_argument("--path", required=True, help="新しい project_root パス")
    p_uproot.set_defaults(func=cmd_update_project_root)

    p_archive = sub.add_parser(
        "archive-worktree",
        help="#212: 完了した worktree の state/evidence を main checkout 側へ整合性付きで保存",
    )
    p_archive.add_argument(
        "--destination-root",
        required=True,
        dest="destination_root",
        help="保存先の project root (通常は main checkout)",
    )
    p_archive.add_argument("--dry-run", action="store_true", help="検証のみ行い、bundle を作成しない")
    p_archive.add_argument("--json", action="store_true", help="結果を JSON 形式で出力")
    p_archive.set_defaults(func=cmd_archive_worktree)

    p_clean = sub.add_parser("cleanup-empty", help="空 .mission-state/ ディレクトリを rmdir")
    p_clean.add_argument("path", help="プロジェクトルートパス")
    p_clean.set_defaults(func=cmd_cleanup_empty)

    p_clean2 = sub.add_parser("cleanup-stale", help="C-4: dead-PID の active state.json を検出 (--execute で halt 実行)")
    p_clean2.add_argument("--execute", action="store_true", help="実際に halt 実行 (デフォルトは dry-run)")
    p_clean2.add_argument("--root", default=None, help="探索ルート (デフォルト: MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_clean2.set_defaults(func=cmd_cleanup_stale)

    p_list = sub.add_parser("list", help="全プロジェクトの active state.json 一覧")
    p_list.set_defaults(func=cmd_list)

    p_lane = sub.add_parser(
        "lane-report",
        help="read-only lane duration report across current search roots",
    )
    p_lane.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_lane.add_argument("--slo-minutes", type=_lane_positive_minutes, default=None, dest="slo_minutes")
    p_lane.set_defaults(func=cmd_lane_report)

    p_halt2 = sub.add_parser("halt", help="state.json を halt させる (--all で全プロジェクト)")
    p_halt2.add_argument("--reason", required=True)
    p_halt2.add_argument("--category", default=None,
                         help=f"#190: halt の種別。有効値: {sorted(HALT_CATEGORIES)}。省略/不正値は 'other' + WARN")
    p_halt2.add_argument("--all", action="store_true")
    p_halt2.add_argument("--root", default=None, help="--all と併用時のみ有効。指定 root 配下のみ halt (省略時は MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_halt2.set_defaults(func=cmd_halt)

    p_stats = sub.add_parser("stats", help="全プロジェクトの /mission セッションを横断集計 (read-only)")
    p_stats.add_argument("--root", action="append", default=None, help="スキャン対象ルート。複数回指定可 (デフォルト: MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_stats.add_argument("--since", default=None, help="期間下限 (YYYY-MM-DD, updated_at で比較)")
    p_stats.add_argument("--until", default=None, help="期間上限 (YYYY-MM-DD, updated_at で比較)")
    p_stats.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_stats.add_argument("--snapshot", default=None, help="audit --snapshot-out で明示作成したsnapshotを利用 (invalid時はfail closed)")
    p_stats.set_defaults(func=cmd_stats)

    p_learning = sub.add_parser("learning", help="failure-ledger learning brief and related read-only helpers")
    learning_sub = p_learning.add_subparsers(dest="learning_cmd", required=True)
    p_brief = learning_sub.add_parser("brief", help="failure_ledger を横断集計して learning brief を出力")
    p_brief.add_argument("--root", action="append", default=None,
                         help="スキャン対象ルート。複数回指定可 (デフォルト: MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_brief.add_argument("--weak-phase", default=None, choices=sorted(WEAK_PHASES),
                         help="学習対象の weak_phase で絞り込む")
    p_brief.add_argument("--limit", type=int, default=10, help="出力する rule の上限 (default: 10)")
    p_brief.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_brief.set_defaults(func=cmd_learning_brief)

    p_advance = sub.add_parser(
        "advance",
        help="#237: phase 遷移と activity 切替を 1 lock で atomic に行う (phase だけ進んで activity が空の state を作らない)",
    )
    p_advance.add_argument("--phase", required=True,
                           help=f"遷移先 phase。terminal (done/halted) は mark-passes / mark-halt 専用。有効値: {sorted(VALID_PHASES - {'done', 'halted'})}")
    p_advance.add_argument("--activity", default=None,
                           help="任意の <kind>:<reason> override。省略時は遷移先 phase の既定値")
    p_advance.add_argument("--detail", default=None,
                           help="任意の補足。control 文字除去・160文字へ正規化")
    p_advance.add_argument("--at", default=None,
                           help="ISO timestamp。省略時は現在 UTC")
    p_advance.add_argument(
        "--artifact-applicability",
        choices=["producing", "not-applicable"],
        default=None,
        help="executing で確定した portable artifact contract",
    )
    p_advance.add_argument(
        "--artifact-path",
        default=None,
        help="producing 時の repository-relative artifact path",
    )
    p_advance.add_argument(
        "--producer-run-id",
        default=None,
        help="artifact bytes を生成した executor run identifier",
    )
    p_advance.add_argument("--json", action="store_true", help="失敗時に機械可読 outcome を出力")
    _add_command_lineage_arguments(p_advance)
    p_advance.set_defaults(func=cmd_advance, command_outcome_tracking=True)

    p_activity = sub.add_parser("activity", help="#211: phase 内の active/wait/idle segment を記録")
    activity_sub = p_activity.add_subparsers(dest="activity_cmd", required=True)
    p_activity_start = activity_sub.add_parser("start", help="activity segment を開始し、開いている前 segment を閉じる")
    p_activity_start.add_argument("--kind", required=True, choices=sorted(ACTIVITY_KINDS))
    p_activity_start.add_argument("--reason", required=True,
                                  help="kind ごとの明示 reason enum")
    p_activity_start.add_argument("--detail", default=None,
                                  help="任意の補足。control 文字除去・160文字へ正規化")
    p_activity_start.add_argument("--at", default=None,
                                  help="ISO timestamp。省略時は現在 UTC")
    p_activity_start.add_argument("--resume", action="store_true",
                                  help="crash/resume: 旧 open は最終観測 updated_at までだけ確定")
    p_activity_start.set_defaults(func=cmd_activity_start)
    p_activity_end = activity_sub.add_parser("end", help="現在の activity segment を閉じる")
    p_activity_end.add_argument("--at", default=None,
                                help="ISO timestamp。省略時は現在 UTC")
    p_activity_end.set_defaults(func=cmd_activity_end)

    p_progress = sub.add_parser("progress", help="long-running mission の progress checkpoint を記録/取得")
    progress_sub = p_progress.add_subparsers(dest="progress_cmd", required=True)
    p_progress_update = progress_sub.add_parser("update", help="progress checkpoint を state と archive に記録")
    p_progress_update.add_argument("--kind", default="batch", choices=["batch"], help="progress 種別")
    p_progress_update.add_argument("--total", type=int, required=True)
    p_progress_update.add_argument("--completed", type=int, required=True)
    p_progress_update.add_argument("--batch-size", type=int, default=None, dest="batch_size")
    p_progress_update.add_argument("--last-unit", default=None, dest="last_unit")
    p_progress_update.add_argument("--artifact", default=None)
    p_progress_update.add_argument("--iteration", type=int, default=None)
    p_progress_update.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_progress_update.set_defaults(func=cmd_progress_update)
    p_progress_get = progress_sub.add_parser("get", help="progress checkpoint を表示")
    p_progress_get.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_progress_get.set_defaults(func=cmd_progress_get)
    p_progress_clear = progress_sub.add_parser("clear", help="progress checkpoint を削除")
    p_progress_clear.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_progress_clear.set_defaults(func=cmd_progress_clear)

    p_artifact = sub.add_parser("artifact", help="local mission artifact を作成・更新・export/publish 証跡化")
    artifact_sub = p_artifact.add_subparsers(dest="artifact_cmd", required=True)
    p_artifact_init = artifact_sub.add_parser("init", help="canonical local artifact を初期化")
    p_artifact_init.add_argument("--format", default="markdown", choices=["markdown"])
    p_artifact_init.add_argument("--title", default=None)
    p_artifact_init.add_argument("--required-for-pass", action="store_true",
                                 help="mark-passes 前に rendered artifact を必須にする")
    p_artifact_init.add_argument("--redaction-status", default="unchecked", choices=sorted(ARTIFACT_REDACTION_STATUSES))
    p_artifact_init.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_artifact_init.set_defaults(func=cmd_artifact_init)

    p_artifact_append = artifact_sub.add_parser("append", help="artifact section に evidence block を追記")
    p_artifact_append.add_argument("--section", required=True)
    p_artifact_append.add_argument("--text", default=None)
    p_artifact_append.add_argument("--file", default=None)
    p_artifact_append.add_argument("--label", default=None)
    p_artifact_append.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_artifact_append.set_defaults(func=cmd_artifact_append)

    p_artifact_render = artifact_sub.add_parser("render", help="state と blocks から canonical Markdown を再生成")
    p_artifact_render.add_argument("--redaction-status", default=None, choices=sorted(ARTIFACT_REDACTION_STATUSES))
    p_artifact_render.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_artifact_render.set_defaults(func=cmd_artifact_render)

    p_artifact_export = artifact_sub.add_parser("export", help="reviewed artifact を project 内の durable path に export")
    p_artifact_export.add_argument("--to", required=True)
    p_artifact_export.add_argument("--redaction-status", required=True, choices=sorted(ARTIFACT_REDACTION_STATUSES - {"unchecked"}))
    p_artifact_export.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_artifact_export.set_defaults(func=cmd_artifact_export)

    p_artifact_publish = artifact_sub.add_parser("publish", help="remote/local publish intent と approval evidence を記録")
    p_artifact_publish.add_argument("--provider", required=True, choices=sorted(ARTIFACT_PUBLISH_PROVIDERS))
    p_artifact_publish.add_argument("--destination", default=None)
    p_artifact_publish.add_argument("--require-confirm", action="store_true")
    p_artifact_publish.add_argument("--approval-text", default=None)
    p_artifact_publish.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_artifact_publish.set_defaults(func=cmd_artifact_publish)

    p_spec = sub.add_parser("specialists", help="specialist skill の discovery / recommend / state 記録")
    spec_sub = p_spec.add_subparsers(dest="specialists_cmd", required=True)
    p_rec = spec_sub.add_parser("recommend", help="task_profile から specialist 候補を dry-run 推薦")
    p_rec.add_argument("--task", required=True, help="分類対象のミッション文またはタスク説明")
    p_rec.add_argument("--files", default=None, help="関連ファイルのカンマ区切り project-root 相対パス")
    p_rec.add_argument("--registry", action="append", default=None,
                       help="project/user specialist registry (JSON または限定 YAML)。複数指定可")
    p_rec.add_argument("--skills-dir", default=None, help="追加 skill root のカンマ区切り")
    p_rec.add_argument("--no-default-skill-roots", action="store_true",
                       help="~/.codex/skills、~/.claude/skills、user registry を discovery しない (テスト/隔離用)")
    p_rec.add_argument("--installed-skills", default=None, help="テスト/手動指定用の installed skill 名カンマ区切り")
    p_rec.add_argument("--first-use", default=None, help="初回確認扱いにする skill 名カンマ区切り")
    p_rec.add_argument("--consent-file", default=None,
                       help="first-use provider consent allowlist JSON (default: ~/.config/mission/provider-consent.json)")
    p_rec.add_argument("--complexity", default=None, choices=["Simple", "Standard", "Complex", "Critical", "Unknown"],
                       help="auto_use.min_complexity 判定用の mission complexity")
    p_rec.add_argument("--record-state", action="store_true", help="現在の mission state に推薦結果を記録")
    p_rec.add_argument("--user-specified", default=None, dest="user_specified",
                       help="Issue #100: ミッション本文でユーザーが名指ししたスキル (comma 区切り)。"
                            "実質 confirmed-user として扱い、high-risk でも ask-user へ倒さず selected に記録する "
                            "(first-use consent が必要な provider は名指しでも確認を維持)")
    p_rec.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_rec.set_defaults(func=cmd_specialists)

    p_consent = spec_sub.add_parser("consent", help="command/skill provider の first-use consent を記録")
    p_consent.add_argument("--provider", required=True)
    p_consent.add_argument("--consent-file", default=None,
                           help="consent allowlist JSON (default: ~/.config/mission/provider-consent.json)")
    p_consent.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_consent.set_defaults(func=cmd_specialists_consent)

    p_account = spec_sub.add_parser("accounting", help="available candidate の未処理 decision trail を確認")
    p_account.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_account.set_defaults(func=cmd_specialists_accounting)

    p_summary = spec_sub.add_parser("summary", help="final report 用の specialist usage summary を出力")
    p_summary.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_summary.set_defaults(func=cmd_specialists_summary)

    p_log = spec_sub.add_parser("log-invocation", help="specialist skill の実呼び出し/inline/skip 証跡を記録")
    p_log.add_argument("--invocation-id", default=None,
                       help="既存 selected/started invocation を同一 ID のまま terminal へ遷移")
    p_log.add_argument("--iteration", type=int, required=True)
    p_log.add_argument("--phase", required=True,
                       choices=["planning", "execution", "review", "scoring", "critic"])
    p_log.add_argument("--role", required=True)
    p_log.add_argument("--skill", required=True)
    p_log.add_argument("--mode", required=True, choices=sorted(SPECIALIST_INVOCATION_MODES))
    p_log.add_argument("--status", required=True, choices=sorted(SPECIALIST_INVOCATION_STATUSES))
    p_log.add_argument("--started-at", default=None, dest="started_at")
    p_log.add_argument("--completed-at", default=None, dest="completed_at")
    p_log.add_argument("--reason", default=None, help="skip/unavailable/failed 等の判断理由")
    p_log.add_argument("--notes", default=None)
    p_log.add_argument("--selection-source", default=None, choices=sorted(SPECIALIST_SELECTION_SOURCES),
                       help="明示選択された specialist の selection metadata も同時に記録する")
    p_log.add_argument("--bounded-purpose", default=None,
                       help="broad/bounded orchestrator specialist を限定用途で使った目的")
    p_log.add_argument("--evidence-output", default=None,
                       help="specialist 出力 Markdown。指定時 archive/iter-N-<mission8>-specialist-<skill>.md に保存")
    p_log.add_argument("--registry", action="append", default=None,
                       help="external explicit registry の application 時再供給。複数指定可")
    p_log.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_log.set_defaults(func=cmd_log_specialist_invocation, command_outcome_tracking=True)

    def add_provider_invoke_arguments(command_parser):
        command_parser.add_argument("--provider", required=True, help="state 内の role / skill / command")
        command_parser.add_argument("--iteration", type=int, required=True)
        command_parser.add_argument("--phase", required=True,
                                    choices=["planning", "execution", "review", "scoring", "critic"])
        command_parser.add_argument("--input-file", default=None, help="provider stdin packet に含める入力ファイル")
        command_parser.add_argument("--preflight-id", default=None,
                                    help="prepare-invocationで生成したper-invocation preflight ID")
        command_parser.add_argument("--execution-isolator", default=None,
                                    help="prepare時と一致するhost-only strict isolator ID")
        command_parser.add_argument("--registry", action="append", default=None,
                                    help="external explicit registry の application 時再供給。複数指定可")
        command_parser.add_argument("--selection-source", default=None, choices=sorted(SPECIALIST_SELECTION_SOURCES),
                                    help="ask-user 後に command provider を適用する場合の confirmed selection metadata")
        command_parser.add_argument("--timeout", type=int, default=None,
                                    help="command timeout seconds (default: provider timeout, then 120)")
        command_parser.add_argument("--json", action="store_true", help="JSON 形式で出力")
        _add_command_lineage_arguments(command_parser)
        command_parser.set_defaults(func=cmd_invoke_command_provider, command_outcome_tracking=True)

    p_cmd = spec_sub.add_parser("invoke-command", help="legacy command provider entrypoint; prepared flowはinvoke-preparedを使う")
    add_provider_invoke_arguments(p_cmd)
    p_invoke_prepared = spec_sub.add_parser(
        "invoke-prepared", help="verified per-invocation preflightをsingle-use receiptで実行する"
    )
    add_provider_invoke_arguments(p_invoke_prepared)

    p_prepare = spec_sub.add_parser(
        "prepare-invocation", help="command provider のexact outbound packetを副作用なしでprepareする"
    )
    p_prepare.add_argument("--provider", required=True)
    p_prepare.add_argument("--iteration", type=int, required=True)
    p_prepare.add_argument("--phase", required=True,
                           choices=["planning", "execution", "review", "scoring", "critic"])
    p_prepare.add_argument("--input-file", required=True)
    p_prepare.add_argument("--registry", action="append", default=None)
    p_prepare.add_argument("--selection-source", default=None, choices=sorted(SPECIALIST_SELECTION_SOURCES))
    p_prepare.add_argument("--execution-isolator", default=None,
                           help="host-only execution-isolator/1 ID")
    p_prepare.add_argument("--json", action="store_true")
    p_prepare.set_defaults(func=cmd_prepare_provider_invocation, command_outcome_tracking=True)

    p_plan_import = spec_sub.add_parser(
        "plan-import", help="strict provider plan resultを検証してinert canonical candidateへ取り込む"
    )
    p_plan_import.add_argument("--input", required=True, help="providerが返したmission-provider-result/1 regular file")
    p_plan_import.add_argument("--invocation-id", required=True)
    p_plan_import.add_argument("--registry", action="append", default=None)
    p_plan_import.add_argument("--json", action="store_true")
    p_plan_import.set_defaults(func=cmd_plan_import, command_outcome_tracking=True)

    p_planning = sub.add_parser("planning", help="policy v1 planning lifecycle transitions")
    planning_sub = p_planning.add_subparsers(dest="planning_cmd", required=True)
    p_adopt_core = planning_sub.add_parser("adopt-core", help="validate and adopt one core planning document")
    p_adopt_core.add_argument("--input", required=True, help="canonical mission-plan/1 document regular file")
    p_adopt_core.add_argument("--source-id", default=None, help="bounded source generation identifier")
    p_adopt_core.add_argument("--json", action="store_true")
    p_adopt_core.set_defaults(func=cmd_planning_adopt_core, command_outcome_tracking=True)
    p_promote = planning_sub.add_parser("promote-provider-plan", help="promote one validated primary provider candidate")
    p_promote.add_argument("--invocation-id", required=True)
    p_promote.set_defaults(func=cmd_planning_promote_provider_plan, command_outcome_tracking=True)
    p_reselect = planning_sub.add_parser("reselect", help="explicitly migrate active legacy planning state without raw copy")
    p_reselect.set_defaults(func=cmd_planning_reselect, command_outcome_tracking=True)

    p_handoff = sub.add_parser("executor-handoff", help="consume a prepared canonical executor handoff")
    handoff_sub = p_handoff.add_subparsers(dest="executor_handoff_cmd", required=True)
    p_begin = handoff_sub.add_parser("begin", help="atomically begin one prepared handoff")
    p_begin.set_defaults(func=cmd_executor_handoff_begin, command_outcome_tracking=True)
    p_verify_step = handoff_sub.add_parser("verify-step", help="revalidate plan before a step")
    p_verify_step.add_argument("--step-id", required=True)
    p_verify_step.set_defaults(func=cmd_executor_handoff_verify, command_outcome_tracking=True)
    p_record_step = handoff_sub.add_parser("record-step", help="record one verified completed step")
    p_record_step.add_argument("--step-id", required=True)
    p_record_step.add_argument("--result", required=True, choices=["ok", "partial", "failed"])
    p_record_step.set_defaults(func=cmd_executor_handoff_record, command_outcome_tracking=True)
    p_complete = handoff_sub.add_parser("complete", help="consume handoff after all canonical steps")
    p_complete.set_defaults(func=cmd_executor_handoff_complete, command_outcome_tracking=True)

    p_handoff = sub.add_parser("handoff", help="local evidence handoff sidecar contract")
    handoff_cli = p_handoff.add_subparsers(dest="handoff_cmd", required=True)
    p_handoff_publish = handoff_cli.add_parser("publish", help="atomic write one evidence handoff envelope")
    p_handoff_publish.add_argument("--topic", required=True)
    p_handoff_publish.add_argument("--input", required=True, help="payload JSON regular file or '-' for stdin")
    p_handoff_publish.add_argument("--producer-session", default=None)
    p_handoff_publish.set_defaults(func=cmd_handoff_publish)
    p_handoff_await = handoff_cli.add_parser("await", help="block until one newer evidence handoff exists")
    p_handoff_await.add_argument("--topic", required=True)
    p_handoff_await.add_argument("--after-seq", type=int, default=0)
    p_handoff_await.add_argument("--timeout-sec", type=int, default=600)
    p_handoff_await.set_defaults(func=cmd_handoff_await)
    p_handoff_verify = handoff_cli.add_parser("verify", help="recompute and compare one handoff digest")
    p_handoff_verify.add_argument("--path", required=True)
    p_handoff_verify.add_argument("--expect-digest", default=None)
    p_handoff_verify.set_defaults(func=cmd_handoff_verify)

    p_verify_approval = spec_sub.add_parser(
        "verify-approval", help="host-trusted verifierのevidenceからper-invocation receiptを生成する"
    )
    p_verify_approval.add_argument("--preflight-id", required=True)
    p_verify_approval.add_argument("--evidence-ref", required=True)
    p_verify_approval.add_argument("--approval-verifier", required=True)
    p_verify_approval.add_argument("--json", action="store_true")
    p_verify_approval.set_defaults(func=cmd_verify_provider_approval, command_outcome_tracking=True)

    p_reconcile = spec_sub.add_parser(
        "reconcile-invocation",
        help="orphaned running provider invocationをfenced evidenceでterminal化",
    )
    p_reconcile.add_argument("--invocation-id", required=True)
    p_reconcile.add_argument(
        "--status", required=True, choices=["completed", "failed", "abandoned-unknown"]
    )
    p_reconcile.add_argument("--evidence", required=True)
    p_reconcile.add_argument("--expected-fencing-epoch", required=True, type=int)
    p_reconcile.add_argument("--json", action="store_true")
    p_reconcile.set_defaults(func=cmd_reconcile_provider_invocation, command_outcome_tracking=True)

    p_resolve = sub.add_parser(
        "resolve-archive",
        help="#301: terminal halted record に監査可能な resolution metadata を追記する",
    )
    p_resolve.add_argument(
        "--path", required=True,
        help="対象 state ファイルのパス (.mission-state/ 以下の相対パスまたは絶対パス)",
    )
    p_resolve.add_argument(
        "--status", required=True,
        choices=sorted(_VALID_RESOLUTION_STATUSES),
        help="解消区分: resolved / superseded / closed",
    )
    p_resolve.add_argument("--owner-issue", default=None, dest="owner_issue",
                           help="起票元 Issue 参照 (例: 301)")
    p_resolve.add_argument("--evidence-url", default=None, dest="evidence_url",
                           help="解消証跡 URL (PR / commit / コメント等)")
    p_resolve.add_argument("--note", default=None,
                           help="自由記述の解消メモ")
    p_resolve.add_argument(
        "--canonical-path",
        default=None,
        help="superseded record に対応する materialized canonical state path",
    )
    p_resolve.add_argument(
        "--retention-generations",
        type=int,
        default=3,
        help="generation manifest の materialized retention policy (物理削除は行わない)",
    )
    p_resolve.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_resolve.add_argument(
        "--frozen-snapshot",
        action="store_true",
        dest="frozen_snapshot",
        default=False,
        help=(
            "#318: archive/ 配下の frozen snapshot（loop_active=true のまま保存された mid-flight record）に "
            "対して resolution を付与できる opt-in フラグ。"
            "対応する live session（sessions/<session_id>.json）が存在しないか terminal であることを検証したうえで許可する。"
            "live session が loop_active=true の場合は拒否（保守側）。"
            "sessions/ 配下への適用は引き続き拒否。"
        ),
    )
    p_resolve.set_defaults(func=cmd_resolve_archive)

    return parser


def main():
    args = _build_parser().parse_args()
    try:
        args.func(args)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
        if (
            code
            and getattr(args, "command_outcome_tracking", False)
            and not getattr(args, "command_outcome_emitted", False)
        ):
            try:
                kind = getattr(error, "outcome_kind", "invalid-input")
                if kind not in COMMAND_OUTCOME_KINDS:
                    kind = "invalid-input"
                outcome = _command_outcome(
                    args, str(getattr(args, "cmd", "unknown")),
                    kind,
                )
                guidance = getattr(error, "guidance", None)
                if guidance:
                    outcome["guidance"] = True
                _record_command_outcome_only(Path.cwd(), outcome)
                if getattr(args, "json", False):
                    envelope = {"ok": False, "outcome_kind": outcome["outcome_kind"], "outcome": outcome}
                    if guidance:
                        envelope["guidance"] = guidance
                    provider_reason = getattr(error, "provider_reason_code", None)
                    if provider_reason:
                        envelope["error"] = {
                            "code": "provider-ineligible",
                            "reason_code": provider_reason,
                        }
                    print(json.dumps(envelope, ensure_ascii=False))
            except Exception:
                pass
        raise
    except LeaseRejectedError as error:
        outcome = _command_outcome(args, str(getattr(args, "cmd", "unknown")), "expected-gate")
        guidance = getattr(error, "guidance", None)
        if guidance:
            outcome["guidance"] = True
        _record_command_outcome_only(Path.cwd(), outcome)
        if getattr(args, "json", False):
            envelope = {"ok": False, "outcome_kind": "expected-gate", "outcome": outcome}
            if guidance:
                envelope["guidance"] = guidance
            print(json.dumps(envelope, ensure_ascii=False))
        raise SystemExit(2)
    except CommandOutcomeInputError:
        print(json.dumps({"ok": False, "outcome_kind": "invalid-input"}, ensure_ascii=False))
        raise SystemExit(2)
    except SpecialistPublicContractError as error:
        print(json.dumps({
            "ok": False,
            "reason_code": "unsafe-legacy-specialist-record",
            "field_path": error.field_path,
        }, ensure_ascii=False))
        raise SystemExit(2)
    except SpecialistEvidenceInputError as error:
        print(json.dumps({
            "ok": False,
            "reason_code": error.reason_code,
            "field_path": error.field_path,
        }, ensure_ascii=False))
        raise SystemExit(2)
    except PublishedRollbackRecoveryError as error:
        envelope = {
            "ok": False,
            "outcome_kind": "internal-error",
            "recovery_ref": error.recovery_ref,
        }
        try:
            outcome = _command_outcome(
                args, str(getattr(args, "cmd", "unknown")), "internal-error",
            )
            _record_command_outcome_only(Path.cwd(), outcome)
            envelope["outcome"] = outcome
        except Exception:
            pass
        print(json.dumps(envelope, ensure_ascii=False))
        raise SystemExit(1)
    except Exception:
        # Never serialize exception text or a traceback: CLI input and provider
        # output may be sensitive.  The typed outcome is the machine contract.
        try:
            outcome = _command_outcome(args, str(getattr(args, "cmd", "unknown")), "internal-error")
            _record_command_outcome_only(Path.cwd(), outcome)
            print(json.dumps({"ok": False, "outcome_kind": "internal-error", "outcome": outcome}, ensure_ascii=False))
        except Exception:
            print(json.dumps({"ok": False, "outcome_kind": "internal-error"}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
