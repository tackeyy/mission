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
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py cleanup-empty <path>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py list   # 全プロジェクト active 一覧 (C-4)
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py halt --reason <text> [--all]
"""

# Issue #99: PEP 604 union 注釈 (X | None) を Python 3.9 (macOS Xcode CLT の python3) でも
# パース可能にする。これが無いとモジュール読み込み時点で TypeError になり全コマンドが全滅する。
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from mission_common import (  # noqa: E402
    PREPARATION_ONLY_MARKERS,
    SPECIALIST_SELECTION_CHECKPOINT_REQUIRED_AT,
    classify_state as _classify,
    duration_sec as _duration_sec,
    parse_iso_datetime,
)
from specialist_accounting import (  # noqa: E402
    candidate_accounting_report,
    explicitly_selected_specialist_skills as _accounting_selected_specialist_skills,
    terminal_invoked_specialist_skills as _accounting_terminal_invoked_specialist_skills,
)

SCHEMA_VERSION = 2  # v1: 旧 schema (project_root/pid なし), v2: A-1/A-2/B-3 追加

# #186: 実行中の mission-state.py のバージョン。.claude-plugin/plugin.json 等の manifest と
# 一致させる (release 時に手動 bump。test_doc_consistency.py::test_release_version_paths_are_in_sync
# が manifest 間の一致は既に保証しているため、ここでは manifest との一致のみ追加で固定する)。
# 実行時に manifest ファイルを読みに行かない設計: plugin cache 配布・symlink 配布・単一ファイル
# 実行のいずれでも `.claude-plugin/plugin.json` への相対パスが安定しないため。
MISSION_CLI_VERSION = "1.2.0"

# Tier5: スコア/反復のマジックナンバーを単一定義 (散在防止・閾値変更を1箇所に集約)
DEFAULT_THRESHOLD = 4.0     # 合格 composite 閾値 (init --threshold 未指定時 / mark-passes fallback)
MIN_ITEM_THRESHOLD = 3.5    # 各項目スコアの足切り (これ未満は mark-passes が reject)
DEFAULT_MAX_ITER = 3        # init --max-iter 未指定時の最大反復回数 (0=上限なし)
SCORE_MIN, SCORE_MAX = 0.0, 5.0  # composite/min_item の許容範囲

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

DEFAULT_COMMAND_RESULT_CONTRACTS = {
    "oracle-reviewer": {
        "min_non_template_chars": 200,
        "forbidden_markers": list(PREPARATION_ONLY_MARKERS),
    },
}

SPECIALIST_SELECTION_CHECKPOINT_COMPLEXITIES = {"Standard", "Complex", "Critical"}
DEFAULT_STALE_ACTIVE_SECONDS = 3 * 60 * 60


def iso_now() -> str:
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
                        yield from sorted(sub.glob("*.json"))
                        worktree_sessions = sub / "sessions"
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


def _project_root_of(sf: Path) -> Path:
    """state ファイルパスからプロジェクトルートを導く (legacy/sessions 両対応)。
    sessions/<sid>.json は sf.parent.parent が .mission-state になり狂うため .mission-state を基準にする。"""
    parts = sf.parts
    if ".mission-state" in parts:
        i = parts.index(".mission-state")
        if i > 0:
            return Path(*parts[:i])
    return sf.parent.parent


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
    return f"pid-{find_agent_pid()}"  # fallback (env なし環境)


def resolve_agent() -> str:
    """state を起動したエージェント種別を判定 (ログでの起動元識別用)。
    session_id とは独立に起動元 env を見るため、MISSION_SESSION_ID 明示時も正しく記録される。"""
    if os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return "claude-code"
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "cli"


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


def atomic_write_json(path: Path, data: dict) -> None:
    """Phase B-2: fsync + os.replace で完全な前 or 後状態を保証."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def backup_state(path: Path) -> None:
    """A-4: 更新前に .bak をコピー生成."""
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())


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


def find_agent_pid() -> int:
    """親プロセスツリーを遡って claude / codex (エージェント CLI) プロセスを見つける。

    Bash 経由で実行された場合、os.getppid() は bash を返してしまうため、
    プロセスツリーを最大 8 階層遡って agent CLI プロセスを特定する。
    見つからなければ os.getppid() (= 直接の親) を fallback で返す。
    """
    pid = os.getppid()
    for _ in range(8):
        if pid <= 1:
            break
        try:
            r = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
            comm = r.stdout.strip()
            if _comm_is_agent(comm):
                return pid
            r2 = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
            pid = int(r2.stdout.strip() or 0)
        except Exception:
            break
    return os.getppid()  # fallback


def stamp_metadata(data: dict, cwd: Path) -> dict:
    """A-1/A-2/B-3: project_root / pid / hostname / session_id / agent を保証."""
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("project_root", str(cwd.resolve()))
    # #12: setdefault は RHS を eager 評価するため、既存キーでも find_agent_pid (ps subprocess)
    # が走り StateLock を最大 8x2s 占有して lock timeout を誘発する。存在チェックで遅延させる。
    if "pid" not in data:
        data["pid"] = find_agent_pid()  # agent CLI プロセスの PID (プロセスツリー遡及で正確に取得)
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
        data.get("heartbeat_at") or data.get("last_progress_at") or data.get("updated_at")
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


def _ensure_phase_timing(data: dict, now: str | None = None) -> None:
    """phase 別所要時間の計測フィールドを後方互換で初期化する."""
    now = now or iso_now()
    if not isinstance(data.get("phase_durations_sec"), dict):
        data["phase_durations_sec"] = {}
    if not data.get("phase_started_at"):
        data["phase_started_at"] = data.get("started_at") or now


def _transition_phase(data: dict, new_phase: str, now: str | None = None) -> None:
    """phase を変更し、旧 phase の経過秒数を phase_durations_sec に加算する."""
    now = now or iso_now()
    _ensure_phase_timing(data, now)
    old_phase = data.get("phase")
    if old_phase and old_phase != new_phase:
        started = _parse_iso_datetime(data.get("phase_started_at"))
        ended = _parse_iso_datetime(now)
        if started and ended:
            elapsed = (ended - started).total_seconds()
            if elapsed >= 0:
                durations = data.setdefault("phase_durations_sec", {})
                durations[old_phase] = float(durations.get(old_phase, 0)) + elapsed
        data["phase_started_at"] = now
    data["phase"] = new_phase


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


# M7 (2026-06-10): SKILL.md Phase 1 の複雑度→Reviewer 数マッピング
COMPLEXITY_REVIEWER_COUNT = {"Simple": 1, "Standard": 2, "Complex": 3, "Critical": 3}

# Issue #168: review_tier による適応的レビュー深度
# tier → reviewer_count のマッピング (COMPLEXITY_REVIEWER_COUNT と同値になる設計)
TIER_REVIEWER_COUNT = {"light": 1, "standard": 2, "full": 3}
# complexity → review_tier のベースマッピング
REVIEW_TIER_BASE = {"Simple": "light", "Standard": "standard", "Complex": "full", "Critical": "full"}

# 不可逆系キーワード (英語) — 小文字化して部分一致
# Issue #174 で 505 mission 遡及分析に基づき較正: push / merge を除外 (標準 dev フロー誤発火)
_IRREVERSIBLE_KEYWORDS_EN = (
    "deploy", "release", "migration", "drop", "delete",
    "publish", "production",
)
# 不可逆系キーワード (日本語) — そのまま部分一致
# Issue #174 で 505 mission 遡及分析に基づき較正: 単体「削除」を除外し複合語に置換 (可逆なコード変更への誤発火)
_IRREVERSIBLE_KEYWORDS_JA = ("本番", "リリース", "マイグレーション", "データ削除", "レコード削除", "物理削除", "公開", "決済")
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
    base_tier = REVIEW_TIER_BASE.get(complexity or "", "standard")
    signals: list[str] = []

    # エスカレータ: task_profile.risk=high
    if task_profile_risk == "high":
        signals.append("task_profile.risk=high")

    # エスカレータ: 不可逆系英語キーワード (小文字化して部分一致)
    lowered = mission_text.lower()
    for kw in _IRREVERSIBLE_KEYWORDS_EN:
        if kw in lowered:
            signals.append(f"irreversible-keyword:{kw}")

    # エスカレータ: 不可逆系日本語キーワード (そのまま部分一致)
    for kw in _IRREVERSIBLE_KEYWORDS_JA:
        if kw in mission_text:
            signals.append(f"irreversible-keyword:{kw}")

    # エスカレータ: セキュリティ系英語キーワード (小文字化して部分一致)
    for kw in _SECURITY_KEYWORDS_EN:
        if kw in lowered:
            signals.append(f"security-keyword:{kw}")

    # エスカレータ: セキュリティ系日本語キーワード (そのまま部分一致)
    for kw in _SECURITY_KEYWORDS_JA:
        if kw in mission_text:
            signals.append(f"security-keyword:{kw}")

    # エスカレータ該当 → "full" に昇格 (降格はしない)
    if signals and base_tier != "full":
        tier = "full"
    else:
        tier = base_tier

    return tier, signals


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


def _load_specialist_registry(path: str | None) -> list[dict]:
    """Load the small registry subset used by specialist selection.

    JSON is accepted directly. YAML support intentionally covers only the documented
    `specialists: - key: value` shape to avoid adding a runtime dependency.
    """
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    txt = p.read_text(encoding="utf-8")
    try:
        data = json.loads(txt)
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


def _registry_arg_paths(value) -> list[Path]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    paths: list[Path] = []
    for item in values:
        paths.extend(Path(p).expanduser() for p in _split_csv(item))
    return paths


def _load_registry_candidates(path: Path, source: str) -> list[dict]:
    candidates = []
    for item in _load_specialist_registry(str(path)):
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate.setdefault("source", source)
        candidates.append(candidate)
    return candidates


def _discover_specialist_registry_candidates(args) -> list[dict]:
    """Discover registry candidates in deterministic precedence order.

    Explicit CLI registries have the highest precedence, then project, user, and
    skill/plugin manifests. Built-in presets are appended later by the ranker so
    registry-level `enabled: false` entries can suppress lower-precedence defaults.
    """
    candidates: list[dict] = []
    for path in _registry_arg_paths(getattr(args, "registry", None)):
        candidates.extend(_load_registry_candidates(path, f"registry:{path}"))

    project_registry = Path.cwd() / ".mission" / "specialists.yml"
    candidates.extend(_load_registry_candidates(project_registry, "project:.mission/specialists.yml"))

    if not getattr(args, "no_default_skill_roots", False):
        user_registry = Path.home() / ".config" / "mission" / "specialists.yml"
        candidates.extend(_load_registry_candidates(user_registry, "user:~/.config/mission/specialists.yml"))

    for root in _skill_roots(args):
        if not root.is_dir():
            continue
        for manifest in sorted(root.glob("*/mission-specialist.yml")):
            candidates.extend(_load_registry_candidates(manifest, f"skill-manifest:{manifest}"))
    return candidates


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
        candidate.get("skill")
        or candidate.get("role")
        or candidate.get("name")
        or candidate.get("command")
        or ""
    )


def _disable_keys(candidate: dict) -> set[str]:
    return {str(v) for v in (
        candidate.get("role"),
        candidate.get("skill"),
        candidate.get("name"),
        candidate.get("command"),
    ) if v}


def _enabled_registry_candidates(registry_candidates: list[dict]) -> list[dict]:
    disabled: set[str] = set()
    enabled_keys: set[str] = set()
    enabled: list[dict] = []
    for raw in [*registry_candidates, *BUILTIN_SPECIALIST_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        keys = _disable_keys(raw)
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


def _complexity_at_least(current: str | None, minimum: str | None) -> bool:
    if not minimum:
        return True
    order = {"Simple": 1, "Standard": 2, "Complex": 3, "Critical": 4}
    if not current:
        return False
    return order.get(str(current), 0) >= order.get(str(minimum), 0)


def _command_is_available(command: str | None) -> bool:
    if not command:
        return False
    if os.sep in command:
        path = Path(command).expanduser()
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def _default_result_contract_for(skill: str | None, role: str | None = None) -> dict:
    keys = {str(skill or ""), str(role or "")} - {""}
    for key in keys:
        if key in DEFAULT_COMMAND_RESULT_CONTRACTS:
            return dict(DEFAULT_COMMAND_RESULT_CONTRACTS[key])
    return {}


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
    phases = candidate.get("phases") or ["planning", "review"]
    if isinstance(phases, str):
        phases = [phases]
    args = _as_list(candidate.get("args"))
    env = _string_map(candidate.get("env"))
    auto_use = candidate.get("auto_use") if isinstance(candidate.get("auto_use"), dict) else {}
    risk = candidate.get("risk") if isinstance(candidate.get("risk"), dict) else {}
    explicit_result_contract = candidate.get("result_contract") if isinstance(candidate.get("result_contract"), dict) else {}
    result_contract = _merge_result_contract(_default_result_contract_for(skill, role), explicit_result_contract)
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
        "risk": risk,
        "result_contract": result_contract,
        "bounded_use": bounded_use,
        "bounded_purpose_required": bool(candidate.get("bounded_purpose_required", bounded_use)),
        "install_hint": bool(candidate.get("install_hint", True)),
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
                               complexity: str | None = None, consented: set[str] | None = None) -> list[dict]:
    first_use = first_use or set()
    consented = consented or set()
    profiles = [task_profile.get("primary"), *(task_profile.get("secondary") or [])]
    ranked = []
    for raw in _enabled_registry_candidates(registry_candidates):
        c = _normalize_candidate(raw, raw.get("source", "registry") if isinstance(raw, dict) else "registry")
        skill = c.get("skill")
        if not skill:
            continue
        if not _complexity_at_least(complexity, c.get("auto_use", {}).get("min_complexity")):
            continue
        overlap = [p for p in c.get("task_profiles", []) if p in profiles]
        if not overlap:
            continue
        if c.get("kind") == "command":
            installed_info = {"available": True} if _command_is_available(c.get("command")) else None
        else:
            installed_info = installed.get(skill)
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
            "reason": f"{', '.join(overlap)} profile match",
        })
    ranked.sort(key=lambda c: (-c["score"], _candidate_source_rank(c), c["skill"]))
    return ranked


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
    if task_profile.get("confidence", 0) < 0.5 or top.get("score", 0) < 0.45:
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
            {**c, "status": "selected", "selection_source": "user-specified"}
            for c in candidates
            if str(c.get("skill") or "") in names and c.get("installed")
        ]
        return selected, unavailable
    if decision.get("policy") == "auto" and candidates:
        return [{**candidates[0], "status": "selected"}], unavailable
    return [], unavailable


def build_phase_plan(candidates: list[dict], complexity: str | None = None) -> list[dict]:
    """Return a bounded advisory provider plan grouped by mission phase."""
    if not candidates:
        return []
    max_per_phase = 1 if complexity in {None, "Simple", "Standard"} else 2
    plan: list[dict] = []
    seen_skills: set[str] = set()
    for phase, preferred_roles in PHASE_ROLE_ORDER.items():
        phase_candidates = [
            c for c in candidates
            if c.get("installed")
            and phase in (c.get("phases") or [])
            and str(c.get("skill") or "") not in seen_skills
        ]
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


def cmd_specialists(args):
    task = getattr(args, "task", "") or ""
    files = _split_csv(getattr(args, "files", None))
    installed = _discover_installed_skills(args)
    registry_candidates = _discover_specialist_registry_candidates(args)
    task_profile = classify_task_profile(task, files)
    candidates = rank_specialist_candidates(
        task_profile,
        registry_candidates,
        installed,
        set(_split_csv(getattr(args, "first_use", None))),
        getattr(args, "complexity", None),
        _load_provider_consent(getattr(args, "consent_file", None)),
    )
    decision = decide_specialists(task_profile, candidates,
                                  _split_csv(getattr(args, "user_specified", None)))
    selected, unavailable = _selection_from_decision(candidates, decision)
    phase_plan = build_phase_plan(candidates, getattr(args, "complexity", None))
    result = {
        "ok": True,
        "task_profile": task_profile,
        "installed_skills": sorted(installed.keys()),
        "specialists_candidates": candidates,
        "specialists_selected": selected,
        "specialists_unavailable": unavailable,
        "specialists_decision": decision,
        "specialists_phase_plan": phase_plan,
    }

    if getattr(args, "record_state", False):
        cwd = Path.cwd()
        sf = resolve_state_file(cwd)
        if not sf.exists():
            print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
            sys.exit(1)
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
            data["task_profile"] = task_profile
            data["specialists_candidates"] = candidates
            data["specialists_selected"] = selected
            data["specialists_unavailable"] = unavailable
            data["specialists_decision"] = decision
            data["specialists_phase_plan"] = phase_plan
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


def _archive_specialist_evidence(cwd: Path, evidence_output: str, iteration: int,
                                 data: dict, entry: dict) -> str | None:
    """Specialist evidence を archive/iter-N-<mission8>-specialist-<skill>.md に保存する."""
    src = Path(evidence_output)
    if not (src.exists() and src.is_file()):
        print(f"WARNING: --evidence-output のファイルが見つかりません: {src}", file=sys.stderr)
        return None
    return _archive_specialist_text(cwd, src.read_text(encoding="utf-8"), iteration, data, entry)


def _archive_specialist_text(cwd: Path, text: str, iteration: int, data: dict, entry: dict) -> str:
    archive_dir = state_dir(cwd) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    gid = (data.get("mission_id") or "unknown")[:8]
    skill_slug = _slug_for_filename(entry.get("skill") or "unknown")
    dst = archive_dir / f"iter-{iteration}-{gid}-specialist-{skill_slug}.md"
    meta = (
        f"<!-- mission-specialist-meta: session_id={data.get('session_id')} "
        f"agent={data.get('agent') or 'unknown'} mission_id={data.get('mission_id')} "
        f"iteration={iteration} phase={entry.get('phase')} role={entry.get('role')} "
        f"skill={entry.get('skill')} mode={entry.get('mode')} status={entry.get('status')} "
        f"timestamp={entry['timestamp']} -->\n"
    )
    dst.write_text(meta + text, encoding="utf-8")
    return str(dst)


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
    return redacted


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
    contract = _merge_result_contract(
        _default_result_contract_for(provider.get("skill"), provider.get("role")),
        explicit_contract,
    )
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
    if min_chars and non_template_len < min_chars:
        return "prepared", f"command provider evidence below result_contract.min_non_template_chars ({non_template_len} < {min_chars})"
    return "completed", None


def _provider_timeout(provider: dict, override: int | None) -> int:
    if override is not None:
        return override
    try:
        return int(provider.get("timeout") or 120)
    except (TypeError, ValueError):
        return 120


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
    selected.append(selected_entry)
    return selected_entry


def _command_provider_packet(data: dict, provider: dict, args) -> str:
    body = ""
    if getattr(args, "input_file", None):
        body = Path(args.input_file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    packet = {
        "mission": data.get("mission"),
        "mission_id": data.get("mission_id"),
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


def cmd_invoke_command_provider(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    data = json.loads(sf.read_text())
    provider = _find_provider(data, args.provider)
    if not provider:
        print(f"ERROR: provider not found in mission state: {args.provider}", file=sys.stderr)
        sys.exit(2)
    if provider.get("kind") != "command":
        print(f"ERROR: provider is not kind=command: {args.provider}", file=sys.stderr)
        sys.exit(2)
    if _confirmed_selection_required(data, provider.get("skill") or provider.get("role"), "completed") and not args.selection_source:
        print(
            "ERROR: specialists_decision requested user confirmation; pass --selection-source confirmed-user "
            "when invoking an applied command provider after confirmation.",
            file=sys.stderr,
        )
        sys.exit(2)
    _reject_unbounded_orchestrator_execution(data, provider.get("skill") or provider.get("role"), args.phase)

    now = iso_now()
    entry = {
        "iteration": args.iteration,
        "phase": args.phase,
        "role": provider.get("role"),
        "skill": provider.get("skill") or provider.get("role"),
        "mode": "command-provider",
        "status": "started",
        "timestamp": now,
        "started_at": now,
        "provider_kind": "command",
        "command": provider.get("command"),
    }
    command = provider.get("command")
    if not _command_is_available(command):
        entry.update({
            "status": "unavailable",
            "completed_at": iso_now(),
            "reason": f"command provider is not available: {command}",
        })
        with StateLock(lock_file(cwd)):
            data = json.loads(sf.read_text())
            data.setdefault("specialist_invocations", []).append(entry)
            data["updated_at"] = entry["completed_at"]
            backup_state(sf)
            atomic_write_json(sf, stamp_metadata(data, cwd))
        print(json.dumps({"ok": False, "entry": entry}, indent=2 if args.json else None, ensure_ascii=False))
        return

    argv = [command, *[str(a) for a in provider.get("args") or []]]
    packet = _command_provider_packet(data, provider, args)
    command_env = os.environ.copy()
    command_env.update(_string_map(provider.get("env")))
    timeout = _provider_timeout(provider, args.timeout)
    try:
        completed = subprocess.run(
            argv,
            input=packet,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=command_env,
        )
        exit_code = completed.returncode
        stdout = _redact_provider_output(completed.stdout or "")
        stderr = _redact_provider_output(completed.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        exit_code = None
        stdout = ""
        stderr = _redact_provider_output(str(exc))

    status, reason = _classify_command_provider_result(provider, exit_code, stdout, stderr)
    completed_at = iso_now()
    entry.update({
        "status": status,
        "completed_at": completed_at,
        "exit_code": exit_code,
        "timeout": timeout,
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
        selected_entry = None
        if status in APPLIED_SPECIALIST_INVOCATION_STATUSES and args.selection_source:
            entry["selection_source"] = args.selection_source
            selected_entry = _add_selected_specialist_metadata(
                data,
                entry,
                args.selection_source,
                completed_at,
                provider,
                reason,
            )
        archived_to = _archive_specialist_text(cwd, evidence, args.iteration, data, entry)
        entry["evidence_path"] = _state_relative_path(cwd, archived_to)
        data.setdefault("specialist_invocations", []).append(entry)
        data["updated_at"] = completed_at
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
    result = {"ok": status == "completed", "entry": entry}
    if selected_entry:
        result["selected_entry"] = selected_entry
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False))


def _state_relative_path(cwd: Path, path_text: str) -> str:
    path = Path(path_text)
    try:
        rel = path.resolve().relative_to(cwd.resolve())
        return str(rel)
    except ValueError:
        return str(path)


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
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, stamp_metadata(data, cwd))
        _write_artifact(cwd, data, artifact)
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
        artifact["updated_at"] = now
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
    if args.iteration < 0:
        print("ERROR: --iteration は 0 以上で指定してください", file=sys.stderr)
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
        if _confirmed_selection_required(data, skill, args.status) and not getattr(args, "selection_source", None):
            print(
                "ERROR: specialists_decision requested user confirmation; pass --selection-source confirmed-user "
                "when recording applied specialist evidence after confirmation.",
                file=sys.stderr,
            )
            sys.exit(2)
        _reject_unbounded_orchestrator_execution(data, skill, args.phase)
        if _bounded_purpose_required(data, skill, args.phase, args.status) and not getattr(args, "bounded_purpose", None):
            print(
                f"ERROR: bounded orchestrator specialist requires --bounded-purpose for applied evidence: {skill}",
                file=sys.stderr,
            )
            sys.exit(2)
        now = iso_now()
        entry = {
            "iteration": args.iteration,
            "phase": args.phase,
            "role": role,
            "skill": skill,
            "mode": args.mode,
            "status": args.status,
            "timestamp": now,
        }
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

        data = stamp_metadata(data, cwd)
        selected_entry = None
        if getattr(args, "selection_source", None):
            selected_entry = _add_selected_specialist_metadata(
                data,
                entry,
                args.selection_source,
                now,
                reason=reason or notes,
            )
        archived_to = None
        if args.evidence_output:
            archived_to = _archive_specialist_evidence(cwd, args.evidence_output, args.iteration, data, entry)
            if archived_to:
                entry["evidence_path"] = _state_relative_path(cwd, archived_to)

        data.setdefault("specialist_invocations", []).append(entry)
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, data)

    result = {"ok": True, "entry": entry}
    if selected_entry:
        result["selected_entry"] = selected_entry
    if archived_to:
        result["archived_to"] = archived_to
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))



def cmd_init(args):
    cwd = Path.cwd()
    state_dir(cwd).mkdir(parents=True, exist_ok=True)
    planned_files = _parse_files_arg(getattr(args, "files", None))
    now = iso_now()

    initial = {
        "mission": args.mission,
        "mission_id": mission_id(args.mission),
        "subtasks": [],
        "complexity": "Unknown",
        "reviewer_count": 2,
        "task_profile": {},
        "specialists_mode": "auto",
        "specialists_candidates": [],
        "specialists_selected": [],
        "specialists_unavailable": [],
        "specialists_decision": {},
        "specialist_invocations": [],
        # M-audit-2 (2026-06-11): 未指定は 3 (98 セッション実測で iter>3 の ROI 低下)。
        # 0 は「上限なし (stagnation 停止モード)」として None を保持する。
        "max_iter": (DEFAULT_MAX_ITER if args.max_iter is None else (None if args.max_iter == 0 else args.max_iter)),
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
        # S3: issue_ref (未指定 None)
        "issue_ref": getattr(args, "issue_ref", None),
        # S3-files: 同一 project の file-set overlap WARN 用 (未指定は空 list)
        "planned_files": planned_files,
    }
    # S3: 同プロジェクト内の active session で同一 issue_ref があれば WARN (reject しない)
    _issue_ref = getattr(args, "issue_ref", None)
    _cur_sid = resolve_session_id()
    if _issue_ref:
        for sf_other in _iter_state_files(cwd):
            try:
                other = json.loads(sf_other.read_text())
            except Exception:
                continue
            # 同一セッションの resume では自分自身の旧 state を誤検出しないよう sid 除外
            if (
                other.get("loop_active")
                and other.get("issue_ref") == _issue_ref
                and other.get("session_id") != _cur_sid
            ):
                print(
                    f"WARNING [S3]: issue_ref='{_issue_ref}' を持つ active session が既に存在します"
                    f" (session_id={other.get('session_id', '?')})。重複作業の可能性を確認してください。",
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
    else:
        # auto 導出: mission 記述と complexity、task_profile の risk を使用
        # (init 時点では task_profile は空 dict のため risk は参照しない)
        _auto_tier, _auto_signals = derive_review_tier(
            args.mission,
            initial.get("complexity"),
        )
        initial["review_tier"] = _auto_tier
        initial["review_tier_source"] = "auto"
        initial["review_tier_signals"] = _auto_signals
    # reviewer_count は review_tier から設定 (COMPLEXITY_REVIEWER_COUNT と同値になる設計)
    initial["reviewer_count"] = TIER_REVIEWER_COUNT[initial["review_tier"]]
    initial = stamp_metadata(initial, cwd)

    # multi-session 完全統一 (2026-06-13): 常に sessions/<sid>.json に書く。
    # 各セッションは独立 sid を持つため奪い合いは起きない (同一 sid 再 init は本人の上書き=resume)。
    sid = initial["session_id"]
    initial["assumptions_path"] = f".mission-state/sessions/{sid}-assumptions.md"
    sdir = session_dir(cwd)
    sdir.mkdir(parents=True, exist_ok=True)
    sf_target = session_file(cwd, sid)
    agg = aggregate_file(cwd)
    with StateLock(lock_file(cwd)):
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
                existing_mid = existing_data.get("mission_id", "")
                new_mid = initial.get("mission_id", "")
                if existing_mid and new_mid and existing_mid != new_mid:
                    archive_dir = state_dir(cwd) / "archive"
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    old_mid8 = existing_mid[:8] if len(existing_mid) >= 8 else existing_mid
                    archive_dest = archive_dir / f"state-{sid}-{old_mid8}.json"
                    shutil.copy2(sf_target, archive_dest)
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
        backup_state(sf_target)
        atomic_write_json(sf_target, initial)
        existing_agg.setdefault("active_sessions", [])
        if sid not in existing_agg["active_sessions"]:
            existing_agg["active_sessions"].append(sid)
        existing_agg["updated_at"] = iso_now()
        atomic_write_json(agg, existing_agg)
    # Issue #5: assumptions_path の実ファイルを空テンプレで作成する
    assumptions_file = cwd / initial["assumptions_path"]
    try:
        assumptions_file.parent.mkdir(parents=True, exist_ok=True)
        if not assumptions_file.exists():
            assumptions_file.write_text("# Assumption Registry\n")
    except OSError as e:
        print(f"WARNING: assumptions_path ファイル作成に失敗: {e}", file=sys.stderr)
    print(json.dumps({"ok": True, "mode": "multi-session", "session_file": str(sf_target), "session_id": sid, "mission_id": initial["mission_id"]}))



def cmd_get(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print(json.dumps({"ok": False, "error": "state.json not found"}))
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
    if args.field:
        print(json.dumps(data.get(args.field)))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def _derive_next_action(data: dict) -> dict:
    """ADR-002 Stage 3 (G-3): state から次の 1 手を決定論的に導出する。

    ハーネス非依存の進行ガイド。Stop hook が使えない環境 (Codex 等) や
    compaction 後の復元で、散文指示に依存せず「state を読めば次手が自明」にする。
    分岐は SKILL.md の Phase 0-7 と同じ決定木を機械化したもの。
    """
    halt_reason = data.get("halt_reason") or ""
    if halt_reason:
        return {
            "next_action": "report-blocker",
            "summary": f"halted: {halt_reason}。blocker と次アクションをユーザーに報告する。再開する場合は refresh-pid",
            "command_hint": "mission-state.py refresh-pid",
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
    mid8 = (data.get("mission_id") or "unknown")[:8]
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
    if phase == "planning":
        return {
            "next_action": "run-planner",
            "summary": f"iteration {iteration}: mission-planner を起動して計画を立てる (完了後 set phase=executing)",
            "command_hint": "Skill: mission-planner → mission-state.py set phase='\"executing\"'",
        }
    if phase == "executing":
        return {
            "next_action": "run-executor",
            "summary": f"iteration {iteration}: mission-executor で計画を実行する (完了後 set phase=reviewing。10分超は progress update)",
            "command_hint": "Skill: mission-executor → mission-state.py set phase='\"reviewing\"'",
        }
    if phase == "reviewing":
        return {
            "next_action": "run-reviewers",
            "summary": f"iteration {iteration}: mission-reviewer を {reviewer_count} 名、単一メッセージで並列起動する (直列起動は規律違反)",
            "command_hint": f"Skill: mission-reviewer x{reviewer_count} (1 message)",
            "details": {"reviewer_count": reviewer_count},
        }
    # phase == scoring / done / その他: 現 iteration の有効スコア有無で分岐
    history = data.get("score_history") or []
    scored_current = [
        h for h in history
        if isinstance(h, dict) and h.get("iteration") == iteration and _is_valid_composite(h.get("composite"))
    ]
    if scored_current:
        unclosed = _unclosed_optional_specialist_skills(data)  # #189
        return {
            "next_action": "mark-passes",
            "summary": f"iteration {iteration} の採点は記録済み。mark-passes で threshold gate 判定する (reject なら mission-critic → 次 iteration)",
            "command_hint": "mission-state.py mark-passes",
            "details": {"unclosed_specialists": unclosed} if unclosed else {},
        }
    return {
        "next_action": "aggregate-reviews",
        "summary": f"iteration {iteration}: reviewer の mission-review/1 JSON を aggregate-reviews で集計し、push-score --scoring-json で記録する",
        "command_hint": f"mission-state.py aggregate-reviews --iteration {iteration} --input /tmp/mission-reviewer-iter-{iteration}-{mid8}-a.json --out /tmp/mission-scorer-iter-{iteration}-{mid8}.json && mission-state.py push-score --iteration {iteration} --scoring-json /tmp/mission-scorer-iter-{iteration}-{mid8}.json",
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
        required_actions.append("Run `mission-state.py init ... --complexity <level>` before creating worktrees or doing implementation work.")
    elif not state_active:
        required_actions.append("Resolve the inactive, passed, or halted mission state before continuing.")
    if not hook_status["configured"]:
        warnings.append(
            "Codex Stop hook is not configured or was not found. Continue only with the state-driven fallback: call `mission-state.py next` at every phase boundary and before any final report."
        )
        if getattr(args, "require_stop_hook", False):
            required_actions.append("Configure and trust `mission-stop-guard.sh` in Codex hooks, or rerun without --require-stop-hook for skills-only fallback.")

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
        "ok": state_active and (hook_status["configured"] or (fallback_available and not getattr(args, "require_stop_hook", False))),
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


# Issue #2: set で変更禁止のフィールド (mission_id 整合性維持のため)
FROZEN_FIELDS = {
    "mission",  # 変更したいなら init を使う (mission_id が再計算される)
    "mission_id",
    "passes",
    "passes_forced",
    "force_reason",
    "score_history",
    "threshold",
    "schema_version",
    "project_root",
    "started_at",
    "created_at_session",
}


def cmd_set(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        now = iso_now()
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
                _transition_phase(data, normalized_phase, now)
            else:
                data[key] = parsed_value
        # A-M1 (2026-06-10 / Issue #168 拡張): complexity 変更時の reviewer_count と review_tier 同期
        # - review_tier_source が "auto" (またはフィールド不在) の場合: tier を再導出して reviewer_count も同期
        # - review_tier_source が "user" の場合: tier を維持し、reviewer_count も tier 由来を維持
        # - reviewer_count を明示した場合はそちらが優先
        explicit_keys = {kv.partition("=")[0] for kv in args.kvs}
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
                _new_tier, _new_signals = derive_review_tier(_mission, cx, _risk)
                data["review_tier"] = _new_tier
                data["review_tier_source"] = "auto"
                data["review_tier_signals"] = _new_signals
                if "reviewer_count" not in explicit_keys:
                    data["reviewer_count"] = TIER_REVIEWER_COUNT[_new_tier]
        elif "review_tier" in explicit_keys and "reviewer_count" not in explicit_keys:
            # review_tier だけ変更された場合: reviewer_count を tier から同期
            _tier = data.get("review_tier")
            if _tier in TIER_REVIEWER_COUNT:
                data["reviewer_count"] = TIER_REVIEWER_COUNT[_tier]
        # F-4: set loop_active=true での手動再活性化(gotchas §2)も aggregate へ戻す
        if "loop_active" in explicit_keys and data.get("loop_active") is True:
            _add_to_aggregate(cwd, sf.stem)
        _ensure_phase_timing(data, now)
        data["updated_at"] = now
        data = stamp_metadata(data, cwd)
        backup_state(sf)
        atomic_write_json(sf, data)
    print(json.dumps({"ok": True}))


# H2 (2026-06-10): スコア項目キーの正規形とエイリアス。実ログで表記揺れが混在し
# stats 横断集計・min_item 検証が壊れたため push-score 時に正規化する。
CANONICAL_SCORE_KEYS = {"mission_achievement", "accuracy", "completeness", "usability", "reviewer_consensus"}
REVIEW_SCORE_KEYS = ("mission_achievement", "accuracy", "completeness", "usability")
REVIEW_SEVERITIES = {"High", "Medium", "Low"}
SCORE_KEY_ALIASES = {
    "usefulness": "usability",
    "practicality": "usability",
    "reviewer_agreement": "reviewer_consensus",
}


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
    # 改善3a: composite / min_item の範囲バリデーション (NaN・範囲外を弾く)。
    for label, val in (("composite", args.composite), ("min_item", args.min_item)):
        if math.isnan(val) or not (SCORE_MIN <= val <= SCORE_MAX):
            print(f"ERROR: --{label} {val} は {SCORE_MIN}〜{SCORE_MAX} の範囲で指定してください。", file=sys.stderr)
            sys.exit(1)
    return items


def _numeric_item_values(items: dict) -> list:
    return [float(v) for v in items.values() if isinstance(v, (int, float)) and not math.isnan(float(v))]


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
    for k, v in items.items():
        if not isinstance(v, (int, float)) or math.isnan(float(v)) or not (SCORE_MIN <= float(v) <= SCORE_MAX):
            print(f"ERROR: --scoring-json の item '{k}'={v} は {SCORE_MIN}〜{SCORE_MAX} の数値で指定してください。", file=sys.stderr)
            sys.exit(2)
    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, str):
        print("ERROR: --scoring-json の notes は文字列で指定してください。", file=sys.stderr)
        sys.exit(2)
    # open_high はキー欠如 (None) と明示 0 を区別する: 明示値は CLI --open-high より優先される
    open_high = payload.get("open_high")
    if open_high is not None and (not isinstance(open_high, int) or open_high < 0):
        print("ERROR: --scoring-json の open_high は 0 以上の整数で指定してください。", file=sys.stderr)
        sys.exit(2)
    findings_evidence_path = payload.get("findings_evidence_path")
    if findings_evidence_path is not None and not isinstance(findings_evidence_path, str):
        print("ERROR: --scoring-json の findings_evidence_path は文字列で指定してください。", file=sys.stderr)
        sys.exit(2)
    review_agreement = payload.get("review_agreement")
    if review_agreement is not None and (
        not isinstance(review_agreement, (int, float))
        or math.isnan(float(review_agreement))
        or not (SCORE_MIN <= float(review_agreement) <= SCORE_MAX)
    ):
        print("ERROR: --scoring-json の review_agreement は 0〜5 の数値または null で指定してください。", file=sys.stderr)
        sys.exit(2)
    agreement_detail = payload.get("agreement_detail")
    if agreement_detail is not None and not isinstance(agreement_detail, dict):
        print("ERROR: --scoring-json の agreement_detail はオブジェクトで指定してください。", file=sys.stderr)
        sys.exit(2)
    return items, notes, open_high, payload


def _archive_scoring_json(cwd: Path, iteration: int, data: dict, entry: dict, payload: dict) -> str:
    """--scoring-json の payload を _meta 付きで archive/iter-N-<mid8>-scoring.json に保存する."""
    dst = _scoring_archive_path(cwd, iteration, data, suffix=".json")
    meta = {
        "session_id": data.get("session_id"),
        "agent": data.get("agent") or "unknown",
        "mission_id": data.get("mission_id"),
        "iteration": iteration,
        "timestamp": entry["timestamp"],
        "computed_composite": entry["composite"],
        "computed_min_item": entry["min_item"],
    }
    out = {"_meta": meta}
    out.update(payload)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(dst)


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


def _load_review_json(path_str: str, expected_iteration: int) -> dict:
    src = Path(path_str)
    if not (src.exists() and src.is_file()):
        print(f"ERROR: reviewer input not found: {src}", file=sys.stderr)
        sys.exit(2)
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"ERROR: reviewer input is invalid JSON: {src}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(payload, dict):
        _review_error(src, "review must be a JSON object")
    if payload.get("schema") != "mission-review/1":
        _review_error(src, "schema must be mission-review/1")
    if payload.get("iteration") != expected_iteration:
        _review_error(src, f"iteration must be {expected_iteration}")
    perspective = payload.get("perspective")
    if not isinstance(perspective, str) or not perspective.strip():
        _review_error(src, "perspective must be a non-empty string")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        _review_error(src, "findings must be a list")
    seen_ids = set()
    for idx, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            _review_error(src, f"finding {idx} must be an object")
        fid = finding.get("id")
        if not isinstance(fid, str) or not fid.startswith(f"{perspective}-"):
            _review_error(src, f"finding {idx} id must start with '{perspective}-'")
        if fid in seen_ids:
            _review_error(src, f"duplicate finding id: {fid}")
        seen_ids.add(fid)
        severity = finding.get("severity")
        if severity not in REVIEW_SEVERITIES:
            _review_error(src, f"finding {fid} severity must be one of {sorted(REVIEW_SEVERITIES)}")
        axis = finding.get("axis")
        if axis not in REVIEW_SCORE_KEYS:
            _review_error(src, f"finding {fid} axis must be one of {list(REVIEW_SCORE_KEYS)}")
        if severity in {"High", "Medium"} and not str(finding.get("evidence") or "").strip():
            _review_error(src, f"finding {fid} evidence is required for {severity}")
    if "scores" not in payload:
        _review_error(src, "scores field is required; use null only for findings-only reviewers")
    scores = payload.get("scores")
    if scores is None:
        return payload
    if not isinstance(scores, dict) or set(scores) != set(REVIEW_SCORE_KEYS):
        _review_error(src, f"scores must contain exactly {list(REVIEW_SCORE_KEYS)}")
    for key, value in scores.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or math.isnan(float(value)) or not (SCORE_MIN <= float(value) <= SCORE_MAX):
            _review_error(src, f"score {key} must be a {SCORE_MIN}-{SCORE_MAX} number")
    values = [float(scores[key]) for key in REVIEW_SCORE_KEYS]
    if max(values) <= 1.0:
        _review_error(src, "scores look like 0-1 normalized scale; use 0-5 scale")
    if len(set(values)) == 1 and not str(payload.get("same_score_note") or "").strip():
        _review_error(src, "same_score_note is required when all four scores are equal")
    return payload


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
    reviews = [_load_review_json(path, args.iteration) for path in args.input]
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
        sys.exit(2)

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

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        mission8 = (data.get("mission_id") or "unknown")[:8]
        evidence_path = state_dir(cwd) / "archive" / f"iter-{args.iteration}-{mission8}-reviews.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "schema": "mission-review-aggregate/1",
            "iteration": args.iteration,
            "inputs": reviews,
            "scoring_perspectives": [entry["perspective"] for entry in adjusted_scores],
            "excluded": excluded,
            "cap_log": cap_log,
            "agreement_detail": agreement_detail,
            "open_high": open_high,
        }
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        out_path = Path(args.out) if args.out else Path("/tmp") / f"mission-scorer-iter-{args.iteration}-{mission8}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": items,
            "notes": f"aggregate-reviews: {len(adjusted_scores)} scoring reviewer(s), {len(reviews) - len(scoring_reviews)} findings-only reviewer(s)",
            "open_high": open_high,
            "findings_evidence_path": str(evidence_path),
            "review_agreement": review_agreement,
            "agreement_detail": agreement_detail,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = {
        "ok": True,
        "out": str(out_path),
        "findings_evidence_path": str(evidence_path),
        "open_high": open_high,
        "items": items,
        "review_agreement": review_agreement,
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
    """Issue #10: Simple/Reviewer 1名では reviewer_consensus を採点 items から省略する."""
    if "reviewer_consensus" not in items:
        return
    if data.get("complexity") == "Simple" and data.get("reviewer_count") == 1:
        print(
            "ERROR: Simple 複雑度かつ Reviewer 1名では reviewer_consensus を --items から省略してください "
            "(Issue #10: consensus は複数 Reviewer 間の合意度であり、1名では検証できません)。"
            " composite/min_item は残り4項目で算出し、notes に consensus 省略を明記してください。",
            file=sys.stderr,
        )
        sys.exit(2)


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
    if args.open_high < 0:
        print("ERROR: --open-high は 0 以上で指定してください", file=sys.stderr)
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
        args.composite = round(sum(_numeric_item_values(items)) / len(items), 2)
        args.min_item = round(min(_numeric_item_values(items)), 2)
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
        # MISSION_REQUIRE_SCORING_EVIDENCE=0 は移行期の一時 escape hatch として残す。
        if not args.scoring_output:
            if os.environ.get("MISSION_REQUIRE_SCORING_EVIDENCE") == "0":
                print(
                    "TEMPORARY ESCAPE HATCH: scoring evidence なしの push-score を許可しました "
                    "(MISSION_REQUIRE_SCORING_EVIDENCE=0)。"
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

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        now = iso_now()
        _validate_consensus_policy(data, items)
        # #122: 同一 iteration の再 push は gate 迂回の温床 (低スコア push 後に
        # 高スコアで上書き)。再 push には差し替え理由を必須化する。旧 entry は履歴として残す。
        resubmit_reason = getattr(args, "resubmit_reason", None)
        already_scored = any(
            h.get("iteration") == args.iteration for h in data.get("score_history", [])
        )
        if already_scored and not resubmit_reason:
            print(
                f"ERROR: iteration {args.iteration} は既に採点済みです。"
                ' 同一 iteration を再 push する場合は --resubmit-reason "<理由>" を指定してください (#122)。',
                file=sys.stderr,
            )
            sys.exit(2)
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
            entry["score_source"] = "scoring-json"
            scoring_json_archived_to = _archive_scoring_json(cwd, args.iteration, data, entry, scoring_payload)
            entry["scoring_evidence_path"] = scoring_json_archived_to
        data.setdefault("score_history", []).append(entry)
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
        data["updated_at"] = now
        data = stamp_metadata(data, cwd)
        backup_state(sf)
        atomic_write_json(sf, data)

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
        now = iso_now()
        threshold = data.get("threshold", DEFAULT_THRESHOLD)
        history = data.get("score_history", [])

        if not force:
            # 改善3b: composite を持つ直近エントリで判定 (末尾に進捗ノート等の
            # composite 欠損エントリが混入していても gate を壊さない)。
            scored = [h for h in history if _is_valid_composite(h.get("composite"))]
            if not scored:
                print("ERROR: 採点未実施。`push-score` を先に呼んでください。", file=sys.stderr)
                sys.exit(2)
            latest = scored[-1]
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
            # Issue #121: scoring-json entry は aggregate-reviews が保存した findings evidence と open_high を再照合する。
            _validate_findings_evidence_gate(cwd, latest)
            # Issue #126: reviewer agreement は composite から独立した gate として扱う。
            _validate_review_agreement_gate(latest)
            # Issue #3: 未解決 High が残っている場合は合格にできない (後方互換: open_high 欠如 → 0 扱い → 通過)
            open_high = latest.get("open_high") or 0
            if open_high > 0:
                print(
                    f"ERROR: 未解決 High が {open_high} 件あるため合格にできません。High 指摘を全て解消してから再採点してください。",
                    file=sys.stderr,
                )
                sys.exit(2)
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

        data["passes"] = True
        data["loop_active"] = False
        data["passes_forced"] = force  # 改善1: force-pass を機械可読に記録 (stats で集計)
        _transition_phase(data, "done", now)  # M4 (2026-06-10): phase 自動更新
        data["updated_at"] = now
        if force:
            data["force_reason"] = reason
            data["force_approved_by_user"] = approved_by_user  # #185
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


def cmd_mark_halt(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state file が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        now = iso_now()
        data["halt_reason"] = args.reason
        data["loop_active"] = False
        _transition_phase(data, "halted", now)  # M4 (2026-06-10): phase 自動更新
        data["updated_at"] = now
        backup_state(sf)
        atomic_write_json(sf, data)
        # #11: aggregate 更新も同じ StateLock 内で行う (lock 外だと並列 halt で lost update)
        _remove_from_aggregate(cwd, resolve_session_id())
    print(json.dumps({"ok": True, "halt_reason": args.reason}))


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
        old_pid = data.get("pid")
        if old_pid and isinstance(old_pid, int) and old_pid != new_pid:
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
        prev_loop = data.get("loop_active", False)
        was_reactivatable_halt = isinstance(prev_halt, str) and (
            prev_halt.startswith("orphan:") or prev_halt.startswith("stale:")
        )
        if was_reactivatable_halt and not getattr(args, "no_reactivate", False):
            data["halt_reason"] = ""
            data["loop_active"] = True
            _add_to_aggregate(cwd, sf.stem)  # F-4: 再活性化分を active_sessions へ戻す
        data["updated_at"] = iso_now()
        backup_state(sf)
        atomic_write_json(sf, data)
    print(json.dumps({
        "ok": True,
        "old_pid": old_pid,
        "new_pid": new_pid,
        "reactivated": was_reactivatable_halt and not getattr(args, "no_reactivate", False),
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
            argparse.Namespace(force=bool(getattr(args, "force", False)), no_reactivate=False),
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


def cmd_cleanup_stale(args):
    """C-4: dead-PID の active state.json を orphan として halt (要 --execute).

    SAFETY: デフォルトは dry-run。--execute を明示しないと halt しない。
    """
    if getattr(args, "root", None):
        search_roots = [Path(args.root)]
    else:
        search_roots = _default_search_roots()
    results = {"halted": [], "would_halt": [], "skipped": [], "errors": [], "dry_run": not args.execute}
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
                pid = data.get("pid")
                if not pid:
                    results["skipped"].append({"path": str(sf), "reason": "no pid"})
                    continue
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
                                with StateLock(lock_file(proj)):
                                    data["halt_reason"] = halt_reason
                                    data["loop_active"] = False
                                    data["updated_at"] = iso_now()
                                    backup_state(sf)
                                    atomic_write_json(sf, data)
                                    if sf.parent.name == "sessions":
                                        _remove_from_aggregate(proj, sf.stem)
                                results["halted"].append({"path": str(sf), "pid": pid})
                            else:
                                results["would_halt"].append({"path": str(sf), "pid": pid, "mission": (data.get("mission") or "")[:80]})
                        else:
                            age_sec = _state_age_since_update_sec(data)
                            stale_threshold = _stale_active_seconds()
                            if not data.get("score_history") and (age_sec is None or age_sec >= stale_threshold):
                                halt_reason = (
                                    "stale: active no-score checkpoint exceeded "
                                    f"{stale_threshold}s with live agent pid {pid} (cleanup-stale)"
                                )
                                proj = _project_root_of(sf)
                                if args.execute:
                                    with StateLock(lock_file(proj)):
                                        now = iso_now()
                                        data["halt_reason"] = halt_reason
                                        data["loop_active"] = False
                                        _transition_phase(data, "halted", now)
                                        data["updated_at"] = now
                                        backup_state(sf)
                                        atomic_write_json(sf, data)
                                        if sf.parent.name == "sessions":
                                            _remove_from_aggregate(proj, sf.stem)
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
                        proj = _project_root_of(sf)
                        if args.execute:
                            with StateLock(lock_file(proj)):
                                data["halt_reason"] = f"orphan: pid {pid} dead or reused (cleanup-stale)"
                                data["loop_active"] = False
                                data["updated_at"] = iso_now()
                                backup_state(sf)
                                atomic_write_json(sf, data)
                                # H-1: aggregate 更新も StateLock 内で (並列 cleanup/init と lost update 防止)
                                if sf.parent.name == "sessions":
                                    _remove_from_aggregate(proj, sf.stem)
                            results["halted"].append({"path": str(sf), "pid": pid})
                        else:
                            results["would_halt"].append({"path": str(sf), "pid": pid, "mission": (data.get("mission") or "")[:80]})
                except Exception as e:
                    results["errors"].append({"path": str(sf), "error": str(e)})
            except Exception as e:
                results["errors"].append({"path": str(sf), "error": str(e)})
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


def cmd_halt(args):
    """C-4: active state.json に halt_reason を立てて停止."""
    if args.all:
        # 候補1: --root 指定時はその root のみ走査 (テスト分離・スコープ指定)。未指定は従来通り全 root
        search_roots = [Path(args.root)] if getattr(args, "root", None) else _default_search_roots()
        halted = []
        for root in search_roots:
            if not root.exists():
                continue
            for sf in _iter_state_files(root):
                try:
                    data = json.loads(sf.read_text())
                    if data.get("loop_active") and not data.get("passes") and not data.get("halt_reason"):
                        proj = _project_root_of(sf)
                        with StateLock(lock_file(proj)):
                            now = iso_now()
                            data["halt_reason"] = args.reason
                            data["loop_active"] = False
                            _transition_phase(data, "halted", now)  # M4
                            data["updated_at"] = now
                            backup_state(sf)
                            atomic_write_json(sf, data)
                            # H-1: aggregate 更新も StateLock 内で (並列 halt/init と lost update 防止)
                            if sf.parent.name == "sessions":
                                _remove_from_aggregate(proj, sf.stem)
                        halted.append(str(proj))
                except Exception as e:
                    print(f"WARN: skip {sf}: {e}", file=sys.stderr)
        print(json.dumps({"ok": True, "halted": halted}))
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
        states.append(state)
    return states


def _is_mission_state_record(state: object) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(state.get("mission") and state.get("mission_id") and state.get("session_id"))


def _state_identity(state: dict) -> tuple | None:
    """active/archive の同一 state 二重集計を避けるための安定キー."""
    project_root = state.get("project_root")
    session_id = state.get("session_id")
    mission = state.get("mission_id")
    started_at = state.get("started_at")
    if not (project_root and session_id and mission and started_at):
        return None
    return (project_root, session_id, mission, started_at)


def _dedupe_states(states: list[dict]) -> tuple[list[dict], int]:
    """同一 state を 1 件に正規化し、重複していた identity グループ数を返す."""
    seen = set()
    duplicate_groups = set()
    deduped = []
    for state in states:
        key = _state_identity(state)
        if key is None:
            deduped.append(state)
            continue
        if key in seen:
            duplicate_groups.add(key)
            continue
        seen.add(key)
        deduped.append(state)
    return deduped, len(duplicate_groups)


def _is_valid_composite(c) -> bool:
    """composite が採点値として有効か (数値・bool除外・NaN除外)."""
    return isinstance(c, (int, float)) and not isinstance(c, bool) and not math.isnan(c)


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
            if isinstance(sec, (int, float)) and not isinstance(sec, bool) and not math.isnan(sec) and sec >= 0:
                totals[phase] = totals.get(phase, 0.0) + float(sec)
    return dict(sorted(totals.items()))


def _aggregate(states: list[dict], duplicate_state_group_count: int = 0) -> dict:
    n = len(states)
    if n == 0:
        return {
            "total_sessions": 0, "pass_count": 0, "halt_count": 0,
            "duplicate_state_group_count": duplicate_state_group_count,
            "incomplete_count": 0, "abandoned_count": 0, "pass_rate": None,
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
        }
    # _classify を 1 回だけ評価 (旧実装は pass/halt/incomplete で 3N 回呼んでいた)
    classes = [_classify(s) for s in states]
    pass_count = classes.count("pass")
    halt_count = classes.count("halt")
    incomplete_count = classes.count("incomplete")
    abandoned_count = classes.count("abandoned")
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
    phase_totals = _phase_duration_totals(states)
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
        "pass_rate": pass_count / n,
        "forced_pass_count": forced_pass_count,
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
    }


def _pct_detail(rate) -> str:
    """合格に対する比率を " / NN% of PASS" 形式で返す (None なら空文字)."""
    return f" / {rate*100:.0f}% of PASS" if rate is not None else ""


def _format_text(stats: dict, since: str | None, until: str | None) -> str:
    period = f"{since or '(all)'} ~ {until or '(now)'}"
    roots = ", ".join(stats.get("roots") or ["(none)"])
    n = stats["total_sessions"]
    if n == 0:
        return f"=== /mission stats ({period}) ===\nroots: {roots}\ntotal_sessions: 0\n(no sessions in this period)"
    pr = stats["pass_rate"]
    fc = stats["avg_final_composite"]
    sd = stats["avg_session_duration_sec"]
    md = stats.get("median_session_duration_sec")
    lines = [
        f"=== /mission stats ({period}) ===",
        f"roots:                    {roots}",
        f"total_sessions:           {n}",
        f"duplicate_state_groups:   {stats.get('duplicate_state_group_count', 0)}",
        f"  PASS:                   {stats['pass_count']} ({pr*100:.1f}%)" if pr is not None else f"  PASS: {stats['pass_count']}",
        f"    (forced:              {stats['forced_pass_count']}{_pct_detail(stats.get('forced_pass_rate'))})",
        f"    (ungated:             {stats['ungated_pass_count']}{_pct_detail(stats.get('ungated_pass_rate'))})",
        f"  HALT:                   {stats['halt_count']}",
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
    roots = [Path(root) for root in args.root] if args.root else _default_search_roots()
    since = _parse_date_to_iso_prefix(args.since)
    until = _parse_date_to_iso_prefix(args.until)
    all_states = []
    for r in roots:
        if r.exists():
            all_states.extend(_collect_states(r))
    filtered = [s for s in all_states if _matches_period(s, since, until)]
    deduped, duplicate_state_group_count = _dedupe_states(filtered)
    stats = _aggregate(deduped, duplicate_state_group_count)
    stats["roots"] = [str(root) for root in roots]
    if args.json:
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print(_format_text(stats, since, until))


def _build_parser():
    parser = argparse.ArgumentParser(description="/mission skill state manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="新規ミッションで state.json を初期化")
    p_init.add_argument("--complexity", choices=["Simple", "Standard", "Complex", "Critical"], default=None,
                        help="Phase 1 の複雑度判定結果。指定すると reviewer_count も自動設定 (Simple:1/Standard:2/Complex:3/Critical:3)。未指定は Unknown のまま WARN")
    p_init.add_argument("mission", help="ミッション記述")
    p_init.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p_init.add_argument("--max-iter", type=int, default=None, help=f"最大反復回数。未指定={DEFAULT_MAX_ITER} / 0=上限なし(stagnation停止)")
    p_init.add_argument("--issue-ref", default=None, dest="issue_ref",
                        help="関連 issue の参照 (例: github:owner/repo#42)。同一 issue_ref の active session が存在する場合 WARN")
    p_init.add_argument("--files", default=None,
                        help="予定変更ファイルのカンマ区切り project-root 相対パス。同一 active session と重複する場合 WARN")
    p_init.add_argument("--review-tier", choices=list(TIER_REVIEWER_COUNT), default=None,
                        dest="review_tier",
                        help="レビュー深度 (light/standard/full)。未指定は complexity・ミッション記述から auto 導出 (Issue #168)")
    p_init.set_defaults(func=cmd_init)

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

    p_get = sub.add_parser("get", help="state.json の値取得")
    p_get.add_argument("--field", default=None)
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set", help="state.json のフィールド更新 (key=value 複数可)")
    p_set.add_argument("kvs", nargs="+")
    p_set.set_defaults(func=cmd_set)

    p_pass = sub.add_parser("mark-passes", help="threshold gate を満たすとき passes=true, loop_active=false (--force --reason --approved-by-user で override 可)")
    p_pass.add_argument("--force", action="store_true",
                        help="threshold gate を skip して強制的に passes=true を書き込む (--reason と --approved-by-user が必須)")
    p_pass.add_argument("--reason", default=None,
                        help="--force の理由 (state.force_reason に記録される)")
    p_pass.add_argument("--approved-by-user", action="store_true", dest="approved_by_user",
                        help="#185: --force と併用必須。ユーザーが明示的に override を承認したことの宣言 "
                             "(orchestrator が自律的に付けてはならない — ユーザーの明示指示があった場合のみ)")
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
    p_score.set_defaults(func=cmd_push_score)

    p_agg = sub.add_parser("aggregate-reviews", help="#119: mission-review/1 JSON を決定論集計して push-score 互換 scoring JSON を生成")
    p_agg.add_argument("--iteration", type=int, required=True)
    p_agg.add_argument("--input", action="append", required=True,
                       help="reviewer が出力した mission-review/1 JSON。複数指定可")
    p_agg.add_argument("--out", default=None,
                       help="出力する push-score 互換 scoring JSON パス。未指定なら /tmp/mission-scorer-iter-N-<mission8>.json")
    p_agg.add_argument("--json", action="store_true", help="結果を JSON で出力")
    p_agg.set_defaults(func=cmd_aggregate_reviews)

    p_halt = sub.add_parser("mark-halt", help="halt_reason を立てて停止")
    p_halt.add_argument("--reason", required=True)
    p_halt.set_defaults(func=cmd_mark_halt)

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

    p_clean = sub.add_parser("cleanup-empty", help="空 .mission-state/ ディレクトリを rmdir")
    p_clean.add_argument("path", help="プロジェクトルートパス")
    p_clean.set_defaults(func=cmd_cleanup_empty)

    p_clean2 = sub.add_parser("cleanup-stale", help="C-4: dead-PID の active state.json を検出 (--execute で halt 実行)")
    p_clean2.add_argument("--execute", action="store_true", help="実際に halt 実行 (デフォルトは dry-run)")
    p_clean2.add_argument("--root", default=None, help="探索ルート (デフォルト: MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_clean2.set_defaults(func=cmd_cleanup_stale)

    p_list = sub.add_parser("list", help="全プロジェクトの active state.json 一覧")
    p_list.set_defaults(func=cmd_list)

    p_halt2 = sub.add_parser("halt", help="state.json を halt させる (--all で全プロジェクト)")
    p_halt2.add_argument("--reason", required=True)
    p_halt2.add_argument("--all", action="store_true")
    p_halt2.add_argument("--root", default=None, help="--all と併用時のみ有効。指定 root 配下のみ halt (省略時は MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_halt2.set_defaults(func=cmd_halt)

    p_stats = sub.add_parser("stats", help="全プロジェクトの /mission セッションを横断集計 (read-only)")
    p_stats.add_argument("--root", action="append", default=None, help="スキャン対象ルート。複数回指定可 (デフォルト: MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_stats.add_argument("--since", default=None, help="期間下限 (YYYY-MM-DD, updated_at で比較)")
    p_stats.add_argument("--until", default=None, help="期間上限 (YYYY-MM-DD, updated_at で比較)")
    p_stats.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_stats.set_defaults(func=cmd_stats)

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
    p_rec.add_argument("--complexity", default=None, choices=["Simple", "Standard", "Complex", "Critical"],
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
    p_log.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_log.set_defaults(func=cmd_log_specialist_invocation)

    p_cmd = spec_sub.add_parser("invoke-command", help="kind=command provider を argv/stdin/stdout で実行して証跡を記録")
    p_cmd.add_argument("--provider", required=True, help="state 内の role / skill / command")
    p_cmd.add_argument("--iteration", type=int, required=True)
    p_cmd.add_argument("--phase", required=True,
                       choices=["planning", "execution", "review", "scoring", "critic"])
    p_cmd.add_argument("--input-file", default=None, help="provider stdin packet に含める入力ファイル")
    p_cmd.add_argument("--selection-source", default=None, choices=sorted(SPECIALIST_SELECTION_SOURCES),
                       help="ask-user 後に command provider を適用する場合の confirmed selection metadata")
    p_cmd.add_argument("--timeout", type=int, default=None,
                       help="command timeout seconds (default: provider timeout, then 120)")
    p_cmd.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_cmd.set_defaults(func=cmd_invoke_command_provider)

    return parser


def main():
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
