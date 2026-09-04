#!/usr/bin/env bash
# mission-stop-guard.sh — judgment-free Stop-hook adapter for /mission.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
MISSION_STATE_PY="${MISSION_STATE_PY:-$SCRIPT_DIR/../skills/mission/bin/mission-state.py}"

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' '{"decision":"block","reason":"mission Stop guard requires jq; state verdict is unavailable","outcome_kind":"expected-gate"}'
  exit 0
fi

INPUT=$(cat)

# 上限は二重に掛ける。どちらか一方では穴が残る。
#
# 外側（ここ）: #714 は `MISSION_STATE_PY` を差し替えた hang でも hook が有限時間で
# block を返すことを要求する。差し替え先は guard 側の上限を持たないため、
# **外側の上限が無いとこの契約を満たせない**。
#
# 内側（stop-verdict 自身・mission_application/guard_timeout.py）: 以前はここで
# `timeout` / `perl` の**どちらも無ければ上限なしで実行**していた。その環境では
# hook が返らないと Stop が永久に止まる（#742 D2）。呼び出し先が自分に上限を
# 掛けるようになったため、下の `else` はもう無制限ではない。
#
# 受理する値を literal で列挙する。#615 が hook の数値比較（-gt 等）を policy 判断
# として拒否するため範囲では書けず、また `resolve_guard_timeout` と**同じ入力に同じ
# 答えを返す**必要がある。範囲と clamp で書くと両側の解釈がずれる（`9` を shell が
# 通して Python が 8 に丸める、`01` を Python だけが 1 と読む、など）。
#
# 上限が 8 なのはホスト側の hook timeout が 10 秒だからで、それを超える値を受けると
# ホストが先に切って guard の block が出ない。
MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"
case "$MISSION_STATE_TIMEOUT" in
  1|2|3|4|5|6|7|8) ;;
  *) MISSION_STATE_TIMEOUT=8 ;;
esac
# 内側の上限が同じ値を見られるようにする。
export MISSION_STATE_TIMEOUT

_mission_state_bounded() {
  if command -v timeout >/dev/null 2>&1; then
    timeout "$MISSION_STATE_TIMEOUT" python3 "$MISSION_STATE_PY" "$@"
  elif command -v perl >/dev/null 2>&1; then
    perl -e 'alarm shift; exec @ARGV' "$MISSION_STATE_TIMEOUT" python3 "$MISSION_STATE_PY" "$@"
  else
    python3 "$MISSION_STATE_PY" "$@"
  fi
}

if ! GUARD_DECISION=$(printf '%s' "$INPUT" | _mission_state_bounded stop-verdict --hook-input - --json); then
  printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is unavailable","outcome_kind":"expected-gate"}'
  exit 0
fi

while :; do
  if ! COMMAND_KIND=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.kind'); then
    printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is invalid","outcome_kind":"expected-gate"}'
    exit 0
  fi

  COMMAND_STDOUT=""
  COMMAND_EXIT_CODE=""

  # GUARD_DECISION_DISPATCH_BEGIN
  case "$COMMAND_KIND" in
    none)
      if ! SHELL_TEXT=$(printf '%s' "$GUARD_DECISION" | jq -er '.shell_text'); then
        printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is invalid","outcome_kind":"expected-gate"}'
        exit 0
      fi
      printf '%s' "$SHELL_TEXT"
      exit 0
      ;;
    mark-halt)
      if ! COMMAND_CWD=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.cwd') ||
         ! COMMAND_SESSION_ID=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.session_id') ||
         ! COMMAND_REASON=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.reason') ||
         ! COMMAND_CATEGORY=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.category'); then
        printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is invalid","outcome_kind":"expected-gate"}'
        exit 0
      fi
      set +e
      COMMAND_STDOUT=$(
        cd "$COMMAND_CWD" 2>/dev/null &&
        MISSION_SESSION_ID="$COMMAND_SESSION_ID" python3 "$MISSION_STATE_PY" mark-halt \
          --reason "$COMMAND_REASON" --category "$COMMAND_CATEGORY"
      )
      COMMAND_EXIT_CODE=$?
      set -e
      ;;
    cleanup-stale)
      if ! COMMAND_ROOT=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.root'); then
        printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is invalid","outcome_kind":"expected-gate"}'
        exit 0
      fi
      set +e
      COMMAND_STDOUT=$(
        cd "$COMMAND_ROOT" 2>/dev/null &&
        python3 "$MISSION_STATE_PY" cleanup-stale --root "$COMMAND_ROOT" --execute
      )
      COMMAND_EXIT_CODE=$?
      set -e
      ;;
    stop-guard-observe)
      if ! COMMAND_CWD=$(printf '%s' "$GUARD_DECISION" | jq -er '.continuation.project_root') ||
         ! COMMAND_SESSION_ID=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.session_id') ||
         ! COMMAND_DIGEST=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.digest') ||
         ! COMMAND_NOW=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.now_epoch') ||
         ! COMMAND_TTL=$(printf '%s' "$GUARD_DECISION" | jq -er '.command.ttl_seconds'); then
        printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is invalid","outcome_kind":"expected-gate"}'
        exit 0
      fi
      set +e
      COMMAND_STDOUT=$(
        cd "$COMMAND_CWD" 2>/dev/null &&
        python3 "$MISSION_STATE_PY" stop-guard-observe \
          --session-id "$COMMAND_SESSION_ID" --digest "$COMMAND_DIGEST" \
          --now-epoch "$COMMAND_NOW" --ttl-seconds "$COMMAND_TTL" 2>/dev/null
      )
      COMMAND_EXIT_CODE=$?
      set -e
      ;;
    *)
      printf '%s\n' '{"decision":"block","reason":"mission Stop guard command is not allowed","outcome_kind":"expected-gate"}'
      exit 0
      ;;
  esac
  # GUARD_DECISION_DISPATCH_END

  if ! NEXT_GUARD_DECISION=$(
    printf '%s' "$INPUT" |
      _mission_state_bounded stop-verdict --hook-input - --json \
        --prior-decision-fd 3 --receipt-stdout-fd 4 \
        --receipt-kind "$COMMAND_KIND" --receipt-exit-code "$COMMAND_EXIT_CODE" \
        3< <(printf '%s' "$GUARD_DECISION") \
        4< <(printf '%s' "$COMMAND_STDOUT")
  ); then
    printf '%s\n' '{"decision":"block","reason":"mission Stop guard receipt is unavailable","outcome_kind":"expected-gate"}'
    exit 0
  fi
  GUARD_DECISION="$NEXT_GUARD_DECISION"
done
