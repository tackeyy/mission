#!/usr/bin/env bash
# mission-stop-guard.sh — Stop hook for /mission skill (v4)
#
# 目的: /mission skill が context compaction やモデルの早期完了判断で停止しないよう、
#       state.json で loop_active=true なら未達成中は decision:block を返してループ継続を強制する。
#
# v4 改修 (2026-05-24, Issue #4 のみ — Issue #3 は revert 済):
#   NEW: state.updated_at と現在時刻の乖離が 1 時間超なら feedback に警告追加
#        → 古い state による紛らわしいメッセージを防ぐ

# v3 改修 (2026-05-24):
#   NEW: state.pid が現在の agent CLI プロセス PID と異なる場合は exit 0
#        → 同一プロジェクトで別目的セッションが起動したとき、巻き込まれない
#
# v2 既存:
#   A-1: state.project_root と current CWD を照合し、不一致なら exit 0 (越境発火防止)
#   A-2: state.pid が生きていなければ halt_reason: "orphan: pid <N> dead" を自動設定して exit 0
#   CWD 取得: プロセスツリーを遡って agent CLI を見つけ、その cwd を採用 (最優先)
#
# 解除条件:
#   - passes: true
#   - halt_reason != ""
#   - loop_active: false
#   - sessions/ に自セッション (HOOK_SID 一致) の未達 state がない
#   - stop_hook_active: true
#   - project_root != current cwd (越境発火防止)
#   - HOOK_SID 不一致 (別セッションの state)

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MISSION_STATE_PY="${MISSION_STATE_PY:-$SCRIPT_DIR/../skills/mission/bin/mission-state.py}"

INPUT="$(cat)"

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

STOP_HOOK_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  exit 0
fi

# === Agent CLI プロセス PID と CWD を取得 (プロセスツリー遡り) ===
# 戻り値: $AGENT_PID と $CWD をセット
_mission_pid_cwd() {
  local pid="$1"
  local cwd=""
  if [ -e "/proc/$pid/cwd" ]; then
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || echo "")
    if [ -n "$cwd" ]; then
      printf '%s' "$cwd"
      return 0
    fi
  fi
  if command -v timeout >/dev/null 2>&1; then
    cwd=$(timeout 3 lsof -p "$pid" 2>/dev/null | awk '$4=="cwd"{print $NF; exit}' || echo "")
  elif command -v perl >/dev/null 2>&1; then
    cwd=$(perl -e 'alarm shift; exec @ARGV' 3 lsof -p "$pid" 2>/dev/null | awk '$4=="cwd"{print $NF; exit}' || echo "")
  else
    cwd=$(lsof -p "$pid" 2>/dev/null | awk '$4=="cwd"{print $NF; exit}' || echo "")
  fi
  [ -n "$cwd" ] && printf '%s' "$cwd"
}

find_agent_proc() {
  local pid="$PPID"
  local i=0
  AGENT_PID=""
  CWD=""
  while [ "$i" -lt 6 ] && [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ]; do
    local comm
    comm=$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' \n' || echo "")
    # basename 一致 (claude/codex)。判定ロジックの正しさは mission-state.py の
    # tests/test_agent_pid.py::test_comm_is_agent で代理検証 (偽陽性 notcodex 等を除外)。
    case "$comm" in
      claude|claude.exe|codex|codex.exe|*/claude|*/claude.exe|*/codex|*/codex.exe)
        AGENT_PID="$pid"
        CWD=$(_mission_pid_cwd "$pid" || true)
        return 0
        ;;
    esac
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' \n' || echo "")
    i=$((i + 1))
  done
  return 1
}

# === env override (テスト用) ===
if [ -n "${MISSION_HOOK_CWD:-}" ]; then
  CWD="${MISSION_HOOK_CWD}"
  AGENT_PID="${MISSION_HOOK_AGENT_PID:-${MISSION_HOOK_CLAUDE_PID:-}}"
else
  find_agent_proc || true
  # Fallback: hook input .cwd
  if [ -z "${CWD:-}" ]; then
    CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
  fi
  # Last resort: $PWD
  [ -z "${CWD:-}" ] && CWD="$PWD"
fi

SESSIONS_DIR="$CWD/.mission-state/sessions"

# 自セッションの session_id を env から算出 (AGENT_PID プロセス遡及に依存しない owner 照合用)。
# mission-state.py の resolve_session_id と同一順: MISSION_SESSION_ID > cc-CLAUDE_CODE_SESSION_ID > cx-CODEX_THREAD_ID
# サニタイズ (mission-state.py _sanitize_sid と整合: / と \\ を _ に置換)
_mission_sanitize_sid() {
  # py _sanitize_sid と整合: / \ を _ に置換 → 前後空白除去 → 先頭ドット除去 → 空なら default
  local v="${1//\//_}"; v="${v//\\/_}"
  v="$(printf '%s' "$v" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  while [ "${v#.}" != "$v" ]; do v="${v#.}"; done
  [ -z "$v" ] && v="default"
  printf '%s' "$v"
}

_mission_halt_session() {
  local sf="$1"
  local reason="$2"
  local sid root
  sid=$(basename "$sf" .json)
  root=$(jq -r '.project_root // empty' "$sf" 2>/dev/null || echo "")
  [ -z "$root" ] && root="$CWD"
  (
    cd "$root" 2>/dev/null || exit 1
    MISSION_SESSION_ID="$sid" python3 "$MISSION_STATE_PY" mark-halt \
      --reason "$reason" --category stale >/dev/null
  )
}
HOOK_SID=""
if [ -n "${MISSION_SESSION_ID:-}" ]; then
  HOOK_SID="$(_mission_sanitize_sid "${MISSION_SESSION_ID}")"
elif [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  HOOK_SID="cc-$(_mission_sanitize_sid "${CLAUDE_CODE_SESSION_ID}")"
elif [ -n "${CODEX_THREAD_ID:-}" ]; then
  HOOK_SID="cx-$(_mission_sanitize_sid "${CODEX_THREAD_ID}")"
fi

# === C-2/C-3: sessions/ ディレクトリ優先 (multi-session 対応) ===
if [ -d "$SESSIONS_DIR" ]; then
  HAS_ACTIVE=false
  if [ -n "$HOOK_SID" ] && [ -f "$SESSIONS_DIR/$HOOK_SID.json" ]; then
    set -- "$SESSIONS_DIR/$HOOK_SID.json"
  else
    set -- "$SESSIONS_DIR"/*.json
  fi
  for sf in "$@"; do
    [ -f "$sf" ] || continue
    s_loop=$(jq -r '.loop_active // false' "$sf" 2>/dev/null || echo "false")
    [ "$s_loop" != "true" ] && continue
    s_passes=$(jq -r '.passes // false' "$sf" 2>/dev/null || echo "false")
    s_halt=$(jq -r '.halt_reason // empty' "$sf" 2>/dev/null || echo "")
    [ "$s_passes" = "true" ] && continue
    [ -n "$s_halt" ] && continue

    # project_root 照合
    s_root=$(jq -r '.project_root // empty' "$sf" 2>/dev/null || echo "")
    if [ -n "$s_root" ]; then
      CWD_REAL=$(cd "$CWD" 2>/dev/null && pwd -P || echo "$CWD")
      ROOT_REAL=$(cd "$s_root" 2>/dev/null && pwd -P || echo "$s_root")
      [ "$CWD_REAL" != "$ROOT_REAL" ] && continue
    fi

    # owner 照合: session env があれば sid (ファイル名) で照合 (AGENT_PID 遡及非依存で確実)。
    # env が無い環境のみ従来の pid 照合に fallback。
    sf_sid=$(basename "$sf" .json)
    s_pid=$(jq -r '.pid // empty' "$sf" 2>/dev/null || echo "")
    if [ -n "$HOOK_SID" ]; then
      [ "$sf_sid" != "$HOOK_SID" ] && continue   # 自分の session でない
    elif [ -n "$s_pid" ] && [ "$s_pid" != "null" ] && [ -n "${AGENT_PID:-}" ]; then
      if [ "$s_pid" != "$AGENT_PID" ]; then
        continue
      fi
    fi

    # PID alive 照合 (env なし pid fallback 時のみ)。HOOK_SID 一致時は自セッション確定のため
    # スキップ — resume/compaction で PID が変わっても自分の state を block できる (M-1)。
    if [ -z "$HOOK_SID" ] && [ -n "$s_pid" ] && [ "$s_pid" != "null" ] && [ "$s_pid" -gt 0 ] 2>/dev/null; then
      if ! kill -0 "$s_pid" 2>/dev/null; then
        _mission_halt_session "$sf" "orphan: pid $s_pid dead" || true
        continue
      fi
    fi

    HAS_ACTIVE=true
    SESSION_FILE_TO_BLOCK="$sf"
    break
  done

  if [ "$HAS_ACTIVE" = "true" ]; then
    ITER=$(jq -r '.iteration // 0' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "0")
    LAST_SCORE=$(jq -r '.score_history[-1].composite // "n/a"' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "n/a")
    THRESHOLD=$(jq -r '.threshold // 4.0' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "4.0")
    MISSION=$(jq -r '.mission // ""' "$SESSION_FILE_TO_BLOCK" 2>/dev/null | head -c 200)
    # Issue #1 / F-5 (v4): updated_at が古ければ WARN 前置 or auto-halt。
    # MISSION_STALE_HALT_SECONDS (既定 10800=3h) 超: 自セッションを halt して exit 0 (block しない)
    # 1h < DIFF <= halt_seconds: 従来通り WARN 前置 + block
    STALE=""
    UPDATED_AT=$(jq -r '.updated_at // empty' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "")
    if [ -n "$UPDATED_AT" ]; then
      # BSD (macOS): date -j -f ... / GNU (Linux): date -u -d ... へフォールバック。-u: Z=UTC として解釈し JST 誤判定を防止
      U_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" -u "$UPDATED_AT" +%s 2>/dev/null || date -u -d "$UPDATED_AT" +%s 2>/dev/null || echo "")
      if [ -n "$U_EPOCH" ]; then
        DIFF=$(( $(date +%s) - U_EPOCH ))
        STALE_HALT_SEC="${MISSION_STALE_HALT_SECONDS:-10800}"
        case "$STALE_HALT_SEC" in ''|*[!0-9]*) STALE_HALT_SEC=10800 ;; esac
        [ "$STALE_HALT_SEC" -lt 300 ] && STALE_HALT_SEC=10800
        if [ "$DIFF" -gt "$STALE_HALT_SEC" ] 2>/dev/null; then
          AWAITING_USER=$(jq -r '.awaiting_user // false' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "false")
          if [ "$AWAITING_USER" = "true" ]; then
            STALE="[WARN: state が $(( DIFF / 60 ))分 未更新だが awaiting_user=true のため stale auto-halt を保留] "
          else
          # 3h (または MISSION_STALE_HALT_SECONDS) 超: state CLI の lock/terminal helper で halt
          STALE_MINS=$(( DIFF / 60 ))
          STALE_HALT_REASON="stale: auto-halted after ${STALE_MINS}m idle"
          if ! _mission_halt_session "$SESSION_FILE_TO_BLOCK" "$STALE_HALT_REASON"; then
            printf '{"decision":"block","reason":"stale auto-halt の書き込みに失敗。手動で cleanup-stale を実行してください"}
'
            exit 0
          fi
          # halt 済みなので block せず通す
          exit 0
          fi
        elif [ "$DIFF" -gt 3600 ]; then
          # 1h < DIFF <= halt_seconds: 従来通り WARN 前置
          STALE="[WARN: state が $(( DIFF / 60 ))分 未更新。stuck/放置の可能性 — cleanup-stale を検討] "
        fi
      fi
    fi
    # P1-2: planning 滞留(push-score 未実行) bd12 型の早期検出。
    # 検出条件: loop_active=true かつ score_history 空(=push-score未実行) かつ
    #           iteration が閾値(MISSION_PLANNING_WARN_ITERATIONS)超。
    # haltせず警告をfeedbackに注入するのみ。偽陽性(正常なplanning初期)は閾値で制御。
    # デフォルト閾値=3: iteration>=3 かつ score_history 空を異常とみなす根拠 —
    # 正常run では iter1 実行後に push-score が呼ばれ score_history に1件以上入るため。
    # テスト容易性のため環境変数 MISSION_PLANNING_WARN_ITERATIONS で override 可能。
    PUSH_SCORE_WARN=""
    SCORE_HISTORY_LEN=$(jq -r '.score_history | length' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "0")
    PHASE=$(jq -r '.phase // "unknown"' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "unknown")
    PLANNING_WARN_ITER="${MISSION_PLANNING_WARN_ITERATIONS:-3}"
    case "$PLANNING_WARN_ITER" in ''|*[!0-9]*) PLANNING_WARN_ITER=3 ;; esac
    [ "$PLANNING_WARN_ITER" -lt 1 ] && PLANNING_WARN_ITER=3
    if [ "$SCORE_HISTORY_LEN" -eq 0 ] 2>/dev/null && [ "$ITER" -ge "$PLANNING_WARN_ITER" ] 2>/dev/null; then
      PUSH_SCORE_WARN="[WARN: push-score 未実行の疑い (iter=$ITER, score_history 空, phase=$PHASE)。mission-state.py get で state を確認し、push-score 未実行なら push-score を実行してください] "
    fi
    REASON="${STALE}${PUSH_SCORE_WARN}/mission skill アクティブ・未達 (multi-session: iter=$ITER, last_score=$LAST_SCORE, threshold=$THRESHOLD)。 state.json の passes=true か halt_reason を立てるまでループを継続。 ミッション: $MISSION"
    jq -n --arg r "$REASON" '{decision:"block", reason:$r}'
    exit 0
  fi
fi

# sessions/ に自セッションの未達 state が無ければ block しない (legacy fallback は撤廃)
exit 0
