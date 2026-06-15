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
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py init <mission> [--threshold X] [--max-iter N]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py get [--field key]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py set key=value [key=value ...]
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-passes
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-halt --reason <text>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py cleanup-empty <path>
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py list   # 全プロジェクト active 一覧 (C-4)
  python3 ${MISSION_PLUGIN_ROOT}/skills/mission/bin/mission-state.py halt --reason <text> [--all]
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2  # v1: 旧 schema (project_root/pid なし), v2: A-1/A-2/B-3 追加

# Tier5: スコア/反復のマジックナンバーを単一定義 (散在防止・閾値変更を1箇所に集約)
DEFAULT_THRESHOLD = 4.0     # 合格 composite 閾値 (init --threshold 未指定時 / mark-passes fallback)
MIN_ITEM_THRESHOLD = 3.5    # 各項目スコアの足切り (これ未満は mark-passes が reject)
DEFAULT_MAX_ITER = 3        # init --max-iter 未指定時の最大反復回数 (0=上限なし)
SCORE_MIN, SCORE_MAX = 0.0, 5.0  # composite/min_item の許容範囲


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
    return data


def mission_id(mission: str) -> str:
    return hashlib.sha256(mission.encode("utf-8")).hexdigest()[:16]


# M7 (2026-06-10): SKILL.md Phase 1 の複雑度→Reviewer 数マッピング
COMPLEXITY_REVIEWER_COUNT = {"Simple": 1, "Standard": 2, "Complex": 3, "Critical": 3}



def cmd_init(args):
    cwd = Path.cwd()
    state_dir(cwd).mkdir(parents=True, exist_ok=True)

    initial = {
        "mission": args.mission,
        "mission_id": mission_id(args.mission),
        "subtasks": [],
        "complexity": "Unknown",
        "reviewer_count": 2,
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
        "started_at": iso_now(),
        "updated_at": iso_now(),
    }
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
            except Exception:
                existing_agg = {}  # F-6: 壊れた aggregate は空扱いで復旧 (init を落とさない)
        backup_state(sf_target)
        atomic_write_json(sf_target, initial)
        existing_agg.setdefault("active_sessions", [])
        if sid not in existing_agg["active_sessions"]:
            existing_agg["active_sessions"].append(sid)
        existing_agg["updated_at"] = iso_now()
        atomic_write_json(agg, existing_agg)
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


# Issue #2: set で変更禁止のフィールド (mission_id 整合性維持のため)
FROZEN_FIELDS = {
    "mission",  # 変更したいなら init を使う (mission_id が再計算される)
    "mission_id",
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
            # 型推論: 数値 / bool / JSON
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                data[key] = value
        # A-M1 (2026-06-10): complexity 変更時は reviewer_count を自動同期
        # (明示指定があればそちらが優先。片方だけ更新される矛盾 state を防ぐ)
        explicit_keys = {kv.partition("=")[0] for kv in args.kvs}
        if "complexity" in explicit_keys and "reviewer_count" not in explicit_keys:
            cx = data.get("complexity")
            if cx in COMPLEXITY_REVIEWER_COUNT:
                data["reviewer_count"] = COMPLEXITY_REVIEWER_COUNT[cx]
        # F-4: set loop_active=true での手動再活性化(gotchas §2)も aggregate へ戻す
        if "loop_active" in explicit_keys and data.get("loop_active") is True:
            _add_to_aggregate(cwd, sf.stem)
        data["updated_at"] = iso_now()
        data = stamp_metadata(data, cwd)
        backup_state(sf)
        atomic_write_json(sf, data)
    print(json.dumps({"ok": True}))


# H2 (2026-06-10): スコア項目キーの正規形とエイリアス。実ログで表記揺れが混在し
# stats 横断集計・min_item 検証が壊れたため push-score 時に正規化する。
CANONICAL_SCORE_KEYS = {"mission_achievement", "accuracy", "completeness", "usability", "reviewer_consensus"}
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


def _archive_scoring_output(cwd: Path, scoring_output: str, iteration: int,
                            data: dict, entry: dict) -> str | None:
    """Scorer の md 出力を archive/iter-N-<mission8>-scoring.md に保存し起動元メタを前置する。

    返り値は保存先パス。ファイルが見つからなければ WARN を出して None を返す (後方互換)。
    """
    src = Path(scoring_output)
    if not (src.exists() and src.is_file()):
        print(f"WARNING: --scoring-output のファイルが見つかりません: {src}", file=sys.stderr)
        return None
    archive_dir = state_dir(cwd) / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    # H1 (2026-06-10): mission_id を含めて連続ランの上書き消失を防止
    gid = (data.get("mission_id") or "unknown")[:8]
    dst = archive_dir / f"iter-{iteration}-{gid}-scoring.md"
    # #3 (2026-06-13): scoring md 単独で起動元を追えるようメタヘッダを前置 (HTML コメント=grep 可能)
    meta = (
        f"<!-- mission-meta: session_id={data.get('session_id')} "
        f"agent={data.get('agent') or 'unknown'} mission_id={data.get('mission_id')} "
        f"iteration={iteration} timestamp={entry['timestamp']} -->\n"
    )
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


def cmd_push_score(args):
    """Phase 5 scorer 完了後、orchestrator が呼ぶ score_history append.

    scorer 自身は context: fork で state.json に書き込めないため、
    orchestrator (mission/SKILL.md Phase 5 直後) が採点結果を渡してこのコマンドを呼ぶ。
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state.json が見つかりません。先に `init` してください。", file=sys.stderr)
        sys.exit(1)
    items = _validate_score_args(args)

    entry = {
        "iteration": args.iteration,
        "composite": args.composite,
        "min_item": args.min_item,
        "items": items,
        "timestamp": iso_now(),
    }
    if args.notes:
        entry["notes"] = args.notes

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        data.setdefault("score_history", []).append(entry)
        # 改善2: top-level iteration を同期 (orchestrator の set 取りこぼしで
        # iteration と score_history 長が不整合になる問題への対処)。
        data["iteration"] = args.iteration
        data["phase"] = "scoring"  # M4 (2026-06-10): phase 自動更新
        data["updated_at"] = iso_now()
        data = stamp_metadata(data, cwd)
        backup_state(sf)
        atomic_write_json(sf, data)

    archived_to = None
    if args.scoring_output:
        archived_to = _archive_scoring_output(cwd, args.scoring_output, args.iteration, data, entry)

    result = {"ok": True, "appended": entry}
    if archived_to:
        result["archived_to"] = archived_to
    print(json.dumps(result, ensure_ascii=False))


def cmd_mark_passes(args):
    """合格マーク。score_history の最新 entry を threshold gate で検証する.

    - score_history が空 -> exit 2 (採点未実施)
    - composite < threshold -> exit 2
    - min_item < MIN_ITEM_THRESHOLD (3.5) -> exit 2 (採点した items のいずれかが閾値未満)
    - すべて通過なら passes=true, loop_active=false を書き込み
    - --force --reason "<理由>" は人手 override (バリデーション skip + force_reason 保存)
    """
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state file が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    force = bool(getattr(args, "force", False))
    reason = getattr(args, "reason", None)

    if force and not reason:
        print("ERROR: --force を指定する場合は --reason \"<理由>\" が必須です。", file=sys.stderr)
        sys.exit(2)

    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
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
        else:
            print(
                f"WARNING: --force によりバリデーションを skip して passes=true を書き込みます。reason={reason!r}",
                file=sys.stderr,
            )

        data["passes"] = True
        data["loop_active"] = False
        data["passes_forced"] = force  # 改善1: force-pass を機械可読に記録 (stats で集計)
        data["phase"] = "done"  # M4 (2026-06-10): phase 自動更新
        data["updated_at"] = iso_now()
        if force:
            data["force_reason"] = reason
        backup_state(sf)
        atomic_write_json(sf, data)
        # #11: aggregate 更新も同じ StateLock 内で行う (lock 外だと並列 mark で lost update)
        _remove_from_aggregate(cwd, resolve_session_id())
    print(json.dumps({"ok": True, "passes": True, "forced": force}))


def cmd_mark_halt(args):
    cwd = Path.cwd()
    sf = resolve_state_file(cwd)
    if not sf.exists():
        print("ERROR: state file が見つかりません。先に init してください。", file=sys.stderr)
        sys.exit(1)
    with StateLock(lock_file(cwd)):
        data = json.loads(sf.read_text())
        data["halt_reason"] = args.reason
        data["loop_active"] = False
        data["phase"] = "halted"  # M4 (2026-06-10): phase 自動更新
        data["updated_at"] = iso_now()
        backup_state(sf)
        atomic_write_json(sf, data)
        # #11: aggregate 更新も同じ StateLock 内で行う (lock 外だと並列 halt で lost update)
        _remove_from_aggregate(cwd, resolve_session_id())
    print(json.dumps({"ok": True, "halt_reason": args.reason}))


def _pid_is_agent(pid: int) -> bool:
    """PID 再利用対策: pid が alive かつ comm がエージェント CLI (claude/codex) であることを確認."""
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
        was_orphan_halt = isinstance(prev_halt, str) and prev_halt.startswith("orphan:")
        if was_orphan_halt and not getattr(args, "no_reactivate", False):
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
        "reactivated": was_orphan_halt and not getattr(args, "no_reactivate", False),
        "prev_halt_reason": prev_halt,
        "prev_loop_active": prev_loop,
    }))


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
                        results["skipped"].append({"path": str(sf), "reason": f"pid {pid} alive (agent)"})
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
                            data["halt_reason"] = args.reason
                            data["loop_active"] = False
                            data["phase"] = "halted"  # M4
                            data["updated_at"] = iso_now()
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


def _classify(state: dict) -> str:
    """PASS / HALT / abandoned / incomplete に分類.

    abandoned = loop を抜けた (loop_active=false) が pass でも halt でもない
    (set 直叩き等の異常終了)。incomplete = loop_active=true の進行中/放置。
    """
    if state.get("passes") is True:
        return "pass"
    if state.get("halt_reason"):
        return "halt"
    if not state.get("loop_active"):
        return "abandoned"
    return "incomplete"


def _median(xs: list) -> float | None:
    """外れ値に頑健な中央値。空なら None."""
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _duration_sec(state: dict) -> float | None:
    started = state.get("started_at")
    updated = state.get("updated_at")
    if not started or not updated:
        return None
    try:
        t1 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t2 = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        return (t2 - t1).total_seconds()
    except Exception:
        return None


def _collect_states(root: Path) -> list[dict]:
    """root 配下を再帰的にスキャンして state を収集 (現役 + archive、stats 用)。

    glob パターンは _iter_state_files に集約 (重複していた 3 つの glob を統合)。
    """
    states = []
    for sf in _iter_state_files(root, include_archive=True):
        try:
            states.append(json.loads(sf.read_text()))
        except Exception:
            continue
    return states


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


def _aggregate(states: list[dict]) -> dict:
    n = len(states)
    if n == 0:
        return {
            "total_sessions": 0, "pass_count": 0, "halt_count": 0,
            "incomplete_count": 0, "abandoned_count": 0, "pass_rate": None,
            "forced_pass_count": 0, "forced_pass_rate": None,
            "ungated_pass_count": 0, "ungated_pass_rate": None,
            "avg_iterations": None, "avg_final_composite": None,
            "avg_session_duration_sec": None,
            "median_session_duration_sec": None,
            "by_agent": {},
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
    return {
        "total_sessions": n,
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
        "by_agent": by_agent,
    }


def _pct_detail(rate) -> str:
    """合格に対する比率を " / NN% of PASS" 形式で返す (None なら空文字)."""
    return f" / {rate*100:.0f}% of PASS" if rate is not None else ""


def _format_text(stats: dict, since: str | None, until: str | None) -> str:
    period = f"{since or '(all)'} ~ {until or '(now)'}"
    n = stats["total_sessions"]
    if n == 0:
        return f"=== /mission stats ({period}) ===\ntotal_sessions: 0\n(no sessions in this period)"
    pr = stats["pass_rate"]
    fc = stats["avg_final_composite"]
    sd = stats["avg_session_duration_sec"]
    md = stats.get("median_session_duration_sec")
    lines = [
        f"=== /mission stats ({period}) ===",
        f"total_sessions:           {n}",
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
    by_agent = stats.get("by_agent") or {}
    if by_agent:
        lines.append("by_agent:")
        for ag, b in sorted(by_agent.items()):
            lines.append(
                f"  {ag:<14} {b['total']} (PASS {b['pass']} / HALT {b['halt']} / incomplete {b['incomplete']})"
            )
    return "\n".join(lines)


def cmd_stats(args):
    """全プロジェクトの /mission セッションを横断集計 (read-only)。

    --root 省略時は _default_search_roots() (MISSION_SEARCH_ROOTS、未設定なら cwd) のみをスキャンする。
    Path.home() 全体の rglob (86 秒) を避ける設計 (list/cleanup と統一)。
    """
    roots = [Path(args.root)] if args.root else _default_search_roots()
    since = _parse_date_to_iso_prefix(args.since)
    until = _parse_date_to_iso_prefix(args.until)
    all_states = []
    for r in roots:
        if r.exists():
            all_states.extend(_collect_states(r))
    filtered = [s for s in all_states if _matches_period(s, since, until)]
    stats = _aggregate(filtered)
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
    p_init.set_defaults(func=cmd_init)

    p_get = sub.add_parser("get", help="state.json の値取得")
    p_get.add_argument("--field", default=None)
    p_get.set_defaults(func=cmd_get)

    p_set = sub.add_parser("set", help="state.json のフィールド更新 (key=value 複数可)")
    p_set.add_argument("kvs", nargs="+")
    p_set.set_defaults(func=cmd_set)

    p_pass = sub.add_parser("mark-passes", help="threshold gate を満たすとき passes=true, loop_active=false (--force --reason で override 可)")
    p_pass.add_argument("--force", action="store_true",
                        help="threshold gate を skip して強制的に passes=true を書き込む (--reason 必須)")
    p_pass.add_argument("--reason", default=None,
                        help="--force の理由 (state.force_reason に記録される)")
    p_pass.set_defaults(func=cmd_mark_passes)

    p_score = sub.add_parser("push-score", help="score_history に採点結果を append (orchestrator が Phase 5 直後に呼ぶ)")
    p_score.add_argument("--iteration", type=int, required=True)
    p_score.add_argument("--composite", type=float, required=True)
    p_score.add_argument("--min-item", type=float, required=True, dest="min_item")
    p_score.add_argument("--items", required=True, help=f'JSON 形式 (例: {{"mission_achievement": {DEFAULT_THRESHOLD}, "accuracy": {MIN_ITEM_THRESHOLD}, ...}})')
    p_score.add_argument("--notes", default=None)
    p_score.add_argument("--scoring-output", default=None,
                         help="Scorer の Markdown 出力ファイルパス。指定すると .mission-state/archive/iter-N-scoring.md にコピー保存される (案 1: ログ充実化)")
    p_score.set_defaults(func=cmd_push_score)

    p_halt = sub.add_parser("mark-halt", help="halt_reason を立てて停止")
    p_halt.add_argument("--reason", required=True)
    p_halt.set_defaults(func=cmd_mark_halt)

    p_refresh = sub.add_parser("refresh-pid", help="R1: resume 後に state.pid を現 agent CLI PID に更新 + orphan halt を解除")
    p_refresh.add_argument("--force", action="store_true", help="既存 pid が alive な agent CLI プロセスでも強制継承")
    p_refresh.add_argument("--no-reactivate", action="store_true", help="orphan halt の解除を行わない (純粋に pid だけ更新)")
    p_refresh.set_defaults(func=cmd_refresh_pid)

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
    p_stats.add_argument("--root", default=None, help="スキャン対象ルート (デフォルト: MISSION_SEARCH_ROOTS、未設定なら cwd)")
    p_stats.add_argument("--since", default=None, help="期間下限 (YYYY-MM-DD, updated_at で比較)")
    p_stats.add_argument("--until", default=None, help="期間上限 (YYYY-MM-DD, updated_at で比較)")
    p_stats.add_argument("--json", action="store_true", help="JSON 形式で出力")
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main():
    args = _build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
