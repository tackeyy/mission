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
# #754: 呼び出し側が渡した元の値を、正規化する前にそのまま退避する。Python 側が
# 「1 や 2 を頼まれて 8 に倒した」ことを出力に載せるための材料で、hook はここでも
# 値を読まず判断もしない（文字列を運ぶだけ）。外部から同名の変数が来ていても上書きする。
MISSION_STATE_TIMEOUT_RAW="${MISSION_STATE_TIMEOUT-}"
MISSION_STATE_TIMEOUT="${MISSION_STATE_TIMEOUT:-8}"
# 1 と 2 は受理しない（#754）。総予算から予約 2 秒を引いた実行枠が 0 以下になり、
# 常に block を生むため。既定の 8 へ倒し、退避した元の値で観測可能にする。
case "$MISSION_STATE_TIMEOUT" in
  3|4|5|6|7|8) ;;
  *) MISSION_STATE_TIMEOUT=8 ;;
esac
export MISSION_STATE_TIMEOUT_RAW
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

# 1 回で終わる。以前はここに dispatch の `case` block があり、判定が返す命令を
# hook が `mission-state.py` の別 subcommand で実行し、その receipt を渡して
# 判定を取り直す、というループを回していた。1 回の判定に最大 7 プロセスかかり、
# それらが 8 秒の予算を共有するため、負荷が上がると予算が起動時間で尽きて
# `guard-budget-exhausted` になった（1 セッションで 5 回観測している）。
#
# 命令の適用は `stop-verdict` の中（同一プロセス）へ移した（#779）。
# したがって hook が持つ副作用は無く、**`stop-verdict` 以外の subcommand を
# 呼ばないこと**が hook 側の契約になる。`analyze_guard_shell` はそれを
# allowlist ではなく「`stop-verdict` 以外は全部だめ」として検査する。
if ! SHELL_TEXT=$(printf '%s' "$GUARD_DECISION" | jq -er '.shell_text'); then
  printf '%s\n' '{"decision":"block","reason":"mission Stop guard decision is invalid","outcome_kind":"expected-gate"}'
  exit 0
fi
printf '%s' "$SHELL_TEXT"
