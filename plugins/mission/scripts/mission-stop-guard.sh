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

# 上限は `stop-verdict` 自身が掛ける（mission_application/guard_timeout.py）。
# hook 側は値を解釈しない: #615 が hook を judgment-free と定めており、
# `MISSION_STATE_TIMEOUT` の検証と既定は判断であって dispatch ではない。
#
# 以前はここで `timeout` / `perl` を使い、どちらも無ければ**上限なしで実行**
# していた。その環境では hook が返らないと Stop が永久に止まる（#742 D2）。
# いまは呼び出し先が自分に上限を掛けるため、外部コマンドの有無は穴にならない。
#
# さらに外側の上限はホスト側の hook timeout が持つ。#742 D3 で「ホスト側の値を
# 契約とし、guard の上限はその内側に置く」と決めているため、ここで二重に
# 掛ける必要はない。
_mission_state_bounded() {
  python3 "$MISSION_STATE_PY" "$@"
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
