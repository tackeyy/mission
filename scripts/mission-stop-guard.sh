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

# mission-state.py の起動には Python のインタプリタ起動と import が含まれ、
# cold start では数秒かかる。実測で 5 秒を超えることがあり、その場合 guard は
# 判定材料を得られないまま block へ倒れて Stop のたびに止まり続ける。
# 上限は「遅いだけの正常な起動」を殺さず、「本当に固まった呼び出し」は
# 切れる幅にする。環境変数で上書きできるようにし、計測結果に応じて
# 調整できる余地を残す。
# D3 (#742): the host bounds the whole hook at 10 seconds, so the guard's own limit
# sits inside that budget. Only positive integers are honoured; anything else falls
# back to the default rather than being passed to `timeout` as a bad argument.
MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"
case "$MISSION_STATE_TIMEOUT" in
  ''|*[!0-9]*) MISSION_STATE_TIMEOUT=8 ;;
  *) [ "$MISSION_STATE_TIMEOUT" -gt 0 ] || MISSION_STATE_TIMEOUT=8 ;;
esac
# The command applies the same limit to itself (D2), so it has to see the value.
export MISSION_STATE_TIMEOUT

# D2 (#742): `stop-verdict` bounds itself (see mission_application/guard_timeout.py),
# so no branch here is unbounded. `timeout` / `perl` are kept as a second, outer limit
# for the case where the interpreter never reaches its own alarm (e.g. it hangs before
# the handler is installed). Their absence is no longer a hole.
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
