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

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
MISSION_STATE_PY="${MISSION_STATE_PY:-$SCRIPT_DIR/../skills/mission/bin/mission-state.py}"

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"decision":"block","reason":"mission Stop guard requires jq; state verdict is unavailable","outcome_kind":"expected-gate"}'
  exit 0
fi

INPUT="$(cat)"

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
  # CWD は hook input .cwd を一次情報として優先し、祖先 agent の実 cwd は fallback に降格 (#426)。
  # find_agent_proc は本来 CWD 取得のために呼ぶ（AGENT_PID は副作用）。AGENT_PID が実際に
  # 必要なのは env sid (MISSION_SESSION_ID / CLAUDE_CODE_SESSION_ID / CODEX_THREAD_ID) が
  # 全欠落した pid fallback 照合のみ。そこで「INPUT_CWD が有効かつ env sid あり」の場合に
  # 限り find_agent_proc をスキップし、slow lsof で hook が固まる経路 (#94) を避ける。
  # INPUT_CWD が無効な場合は CWD 取得のため env sid の有無に関わらず従来どおり探索する。
  INPUT_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
  _HAS_ENV_SID=false
  if [ -n "${MISSION_SESSION_ID:-}" ] || [ -n "${CLAUDE_CODE_SESSION_ID:-}" ] || [ -n "${CODEX_THREAD_ID:-}" ]; then
    _HAS_ENV_SID=true
  fi
  if [ -n "$INPUT_CWD" ] && [ -d "$INPUT_CWD" ]; then
    if [ "$_HAS_ENV_SID" != "true" ]; then
      find_agent_proc || true
    fi
    # find_agent_proc は CWD も設定するため、input .cwd を最終値として再固定する
    CWD="$INPUT_CWD"
  else
    find_agent_proc || true
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

_mission_sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 -r | awk '{print $1}'
  else
    return 1
  fi
}

_mission_halt_session() {
  local sf="$1"
  local reason="$2"
  local sid root
  sid=$(basename "$sf" .json)
  root="$CWD"
  (
    cd "$root" 2>/dev/null || exit 1
    MISSION_SESSION_ID="$sid" python3 "$MISSION_STATE_PY" mark-halt \
      --reason "$reason" --category stale >/dev/null
  )
}

_mission_cleanup_expired_lease() {
  local sf="$1"
  local root output
  root="$CWD"
  output=$(
    cd "$root" 2>/dev/null || exit 1
    python3 "$MISSION_STATE_PY" cleanup-stale --root "$root" --execute
  ) || return 1
  printf '%s' "$output" | jq -e --arg target "$sf" \
    'any(.halted[]?; .path == $target)' >/dev/null 2>&1
}

_mission_state_freshness() {
  local sf="$1"
  local root output
  root="$CWD"
  if command -v timeout >/dev/null 2>&1; then
    output=$(
      cd "$root" 2>/dev/null || exit 1
      timeout 5 python3 "$MISSION_STATE_PY" freshness --state-file "$sf"
    ) || return 1
  elif command -v perl >/dev/null 2>&1; then
    output=$(
      cd "$root" 2>/dev/null || exit 1
      perl -e 'alarm shift; exec @ARGV' 5 python3 "$MISSION_STATE_PY" freshness --state-file "$sf"
    ) || return 1
  else
    output=$(
      cd "$root" 2>/dev/null || exit 1
      python3 "$MISSION_STATE_PY" freshness --state-file "$sf"
    ) || return 1
  fi
  printf '%s' "$output" | jq -e '
    .ok == true and
    (.verdict == "fresh" or .verdict == "warn" or .verdict == "stale")
  ' >/dev/null 2>&1 || return 1
  printf '%s' "$output"
}

_mission_stop_verdict() {
  local sf="$1"
  local output
  local -a verdict_args
  verdict_args=(stop-verdict --state-file "$sf" --json --cwd "$CWD" \
    --planning-warn-iterations "$PLANNING_WARN_ITER")
  if [ -n "$HOOK_SID" ]; then
    verdict_args+=(--hook-session-id "$HOOK_SID")
  fi
  if [ -n "${AGENT_PID:-}" ]; then
    verdict_args+=(--hook-pid "$AGENT_PID")
  fi
  if [ "$HOOK_SID_FROM_PID" = "true" ]; then
    verdict_args+=(--hook-session-id-from-pid)
  fi
  if command -v timeout >/dev/null 2>&1; then
    output=$(cd "$CWD" 2>/dev/null && timeout 5 python3 "$MISSION_STATE_PY" "${verdict_args[@]}") || return 1
  elif command -v perl >/dev/null 2>&1; then
    output=$(cd "$CWD" 2>/dev/null && perl -e 'alarm shift; exec @ARGV' 5 python3 "$MISSION_STATE_PY" "${verdict_args[@]}") || return 1
  else
    output=$(cd "$CWD" 2>/dev/null && python3 "$MISSION_STATE_PY" "${verdict_args[@]}") || return 1
  fi
  printf '%s' "$output" | jq -e '
    .schema == "mission-stop-verdict/1" and
    (.decision == "block" or .decision == "skip" or .decision == "warn")
  ' >/dev/null 2>&1 || return 1
  printf '%s' "$output"
}
HOOK_SID=""
HOOK_SID_FROM_PID=false
if [ -n "${MISSION_SESSION_ID:-}" ]; then
  HOOK_SID="$(_mission_sanitize_sid "${MISSION_SESSION_ID}")"
elif [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  HOOK_SID="cc-$(_mission_sanitize_sid "${CLAUDE_CODE_SESSION_ID}")"
elif [ -n "${CODEX_THREAD_ID:-}" ]; then
  HOOK_SID="cx-$(_mission_sanitize_sid "${CODEX_THREAD_ID}")"
elif [ -n "${AGENT_PID:-}" ]; then
  # mission-state.py resolve_session_id() の env-less fallback と同じ owner SID。
  HOOK_SID="pid-$(_mission_sanitize_sid "${AGENT_PID}")"
  HOOK_SID_FROM_PID=true
fi

# === C-2/C-3: sessions/ ディレクトリ優先 (multi-session 対応) ===
PLANNING_WARN_ITER="${MISSION_PLANNING_WARN_ITERATIONS:-3}"
case "$PLANNING_WARN_ITER" in ''|*[!0-9]*) PLANNING_WARN_ITER=3 ;; esac
[ "$PLANNING_WARN_ITER" -lt 1 ] && PLANNING_WARN_ITER=3
if [ -d "$SESSIONS_DIR" ]; then
  HAS_ACTIVE=false
  EXACT_SESSION_FILE=""
  EXACT_SESSION_SEEN=false
  if [ -n "$HOOK_SID" ] && { [ -e "$SESSIONS_DIR/$HOOK_SID.json" ] || [ -L "$SESSIONS_DIR/$HOOK_SID.json" ]; }; then
    EXACT_SESSION_FILE="$SESSIONS_DIR/$HOOK_SID.json"
    if [ "$HOOK_SID_FROM_PID" = "true" ]; then
      # exact fenced state を最優先し、不適格/terminal の場合だけ legacy PID stateへ降下。
      set -- "$EXACT_SESSION_FILE" "$SESSIONS_DIR"/*.json
    else
      set -- "$EXACT_SESSION_FILE"
    fi
  else
    set -- "$SESSIONS_DIR"/*.json
  fi
  for sf in "$@"; do
    [ -e "$sf" ] || [ -L "$sf" ] || continue
    if [ -n "$EXACT_SESSION_FILE" ] && [ "$sf" = "$EXACT_SESSION_FILE" ]; then
      [ "$EXACT_SESSION_SEEN" = "true" ] && continue
      EXACT_SESSION_SEEN=true
    fi
    if ! STATE_VERDICT=$(_mission_stop_verdict "$sf"); then
      jq -n --arg r "authoritative session state を検証できないため安全側で停止: $sf" \
        '{decision:"block", reason:$r, outcome_kind:"expected-gate"}'
      exit 0
    fi
    STATE_DECISION=$(printf '%s' "$STATE_VERDICT" | jq -r '.decision')
    if [ "$STATE_DECISION" != "block" ] && [ "$STATE_DECISION" != "warn" ]; then
      ORPHAN_PID=$(printf '%s' "$STATE_VERDICT" | jq -r '.orphan_pid // empty')
      if [ -n "$ORPHAN_PID" ]; then
        _mission_halt_session "$sf" "orphan: pid $ORPHAN_PID dead" || true
      fi
      continue
    fi

    HAS_ACTIVE=true
    SESSION_FILE_TO_BLOCK="$sf"
    SESSION_VERDICT="$STATE_VERDICT"
    SESSION_LEASE_PRESENT=$(printf '%s' "$STATE_VERDICT" | jq -r '.lease_present')
    SESSION_LEASE_UNEXPIRED=$(printf '%s' "$STATE_VERDICT" | jq -r '.lease_unexpired')
    break
  done

  if [ "$HAS_ACTIVE" = "true" ]; then
    # Issue #1 / F-5 (v4): freshness verdict は Python の read-only 判定へ集約する。
    # 判定不能時は stale auto-halt を行わず、通常の block 継続へ戻す。
    STALE=""
    FRESHNESS=""
    if FRESHNESS=$(_mission_state_freshness "$SESSION_FILE_TO_BLOCK"); then
      FRESHNESS_VERDICT=$(printf '%s' "$FRESHNESS" | jq -r '.verdict // empty' 2>/dev/null || echo "")
      FRESHNESS_AGE_SEC=$(printf '%s' "$FRESHNESS" | jq -r 'if .age_sec == null then empty else .age_sec end' 2>/dev/null || echo "")
      case "$FRESHNESS_VERDICT" in
        stale)
          AWAITING_USER=$(printf '%s' "$SESSION_VERDICT" | jq -r '.awaiting_user')
          if [ "${SESSION_LEASE_UNEXPIRED:-false}" = "true" ]; then
            STALE="[WARN: state が $(( FRESHNESS_AGE_SEC / 60 ))分 未更新だが session lease は有効なため stale auto-halt を保留] "
          elif [ "$AWAITING_USER" = "true" ]; then
            STALE="[WARN: state が $(( FRESHNESS_AGE_SEC / 60 ))分 未更新だが awaiting_user=true のため stale auto-halt を保留] "
          else
            STALE_MINS=$(( FRESHNESS_AGE_SEC / 60 ))
            STALE_HALT_REASON="stale: auto-halted after ${STALE_MINS}m idle"
            if [ "${SESSION_LEASE_PRESENT:-false}" = "true" ]; then
              if _mission_cleanup_expired_lease "$SESSION_FILE_TO_BLOCK"; then
                HALT_OK=0
              else
                HALT_OK=$?
              fi
            else
              if _mission_halt_session "$SESSION_FILE_TO_BLOCK" "$STALE_HALT_REASON"; then
                HALT_OK=0
              else
                HALT_OK=$?
              fi
            fi
            if [ "$HALT_OK" -ne 0 ]; then
              printf '{"decision":"block","reason":"stale auto-halt の書き込みに失敗。手動で cleanup-stale を実行してください"}
'
              exit 0
            fi
            # halt 済みなので block せず通す
            exit 0
          fi
          ;;
        warn)
          STALE="[WARN: state が $(( FRESHNESS_AGE_SEC / 60 ))分 未更新。stuck/放置の可能性 — cleanup-stale を検討] "
          ;;
        fresh)
          :
          ;;
      esac
    fi
    PUSH_SCORE_WARN=$(printf '%s' "$SESSION_VERDICT" | jq -r '.planning_warning')
    SESSION_SID=$(printf '%s' "$SESSION_VERDICT" | jq -r '.session_id')
    PENDING_DIGEST=$(printf '%s' "$SESSION_VERDICT" | jq -r '.pending_digest')
    DISPLAY_REASON=$(printf '%s' "$SESSION_VERDICT" | jq -r '.display_reason')
    STOP_GUARD_OBSERVATION=""
    STOP_GUARD_NOW="${MISSION_STOP_GUARD_NOW_EPOCH:-$(date +%s)}"
    STOP_GUARD_TTL="${MISSION_STOP_GUARD_HEARTBEAT_SECONDS:-600}"
    case "$STOP_GUARD_NOW" in ''|*[!0-9]*) STOP_GUARD_NOW=$(date +%s) ;; esac
    case "$STOP_GUARD_TTL" in ''|*[!0-9]*) STOP_GUARD_TTL=600 ;; esac
    [ "$STOP_GUARD_TTL" -lt 1 ] 2>/dev/null && STOP_GUARD_TTL=600
    if [ -n "$PENDING_DIGEST" ]; then
      STOP_GUARD_ATTEMPT=0
      while [ "$STOP_GUARD_ATTEMPT" -lt 3 ]; do
        if STOP_GUARD_OBSERVATION=$(
          cd "$CWD" 2>/dev/null || exit 1
          python3 "$MISSION_STATE_PY" stop-guard-observe \
            --session-id "$SESSION_SID" --digest "$PENDING_DIGEST" \
            --now-epoch "$STOP_GUARD_NOW" --ttl-seconds "$STOP_GUARD_TTL" 2>/dev/null
        ); then
          break
        fi
        STOP_GUARD_OBSERVATION=""
        STOP_GUARD_ATTEMPT=$((STOP_GUARD_ATTEMPT + 1))
        sleep 0.05
      done
    fi
    STOP_GUARD_MODE=$(printf '%s' "$STOP_GUARD_OBSERVATION" | jq -r '.mode // "detail"' 2>/dev/null || echo "detail")
    if [ "$STOP_GUARD_MODE" = "heartbeat" ]; then
      REASON="${STALE}${PUSH_SCORE_WARN}/mission heartbeat (blocker=unfinished-mission, next=python3 scripts/mission-state.py next)"
    else
      REASON="${STALE}${PUSH_SCORE_WARN}${DISPLAY_REASON}"
    fi
    jq -n --arg r "$REASON" '{decision:"block", reason:$r, outcome_kind:"expected-gate"}'
    exit 0
  fi
fi

# sessions/ に自セッションの未達 state が無ければ block しない (legacy fallback は撤廃)
exit 0
