# Pass rate 指標

`mission-state.py stats` と `mission-audit.py` は同じ排他的な session health / terminal outcome reducer を使い、全 session、完了 session、implementer 品質、証跡提出完了を role 混在なしで表示します。

## Rate

| field | 分子 | 分母 |
| --- | --- | --- |
| `raw_pass_rate` | pass session | 選択された全 session |
| `completed_pass_rate` | pass session | `pass + halt + abandoned + stale` |
| `implementer_pass_rate` | `completed_pass` の implementer record | `completed_pass`、`failed`、`incomplete` の implementer record |
| `evidence_completion_rate` | `completed_evidence` の checker/planning/analyze record | `completed_evidence`、`failed`、`incomplete` の checker/planning/analyze record |

両 rate は `_numerator` と `_denominator` を明示します。分母が 0 の場合は JSON `null` とし、`NaN` や infinity は出力しません。

実行中の fresh session は completed 分母だけから除外します。stale な実行中 session は未合格の完了 health debt として分母に含めるため、除外によって completed population が健全に見えることはありません。current session を暗黙には除外しません。明示した root・期間の対象外か、session identity に基づく重複排除の場合だけ除外します。

`release` record は `role_counts` と `terminal_outcome_counts` には残りますが、role 別 rate の両分母から除外します。`mission-audit.py` の `actionable_pass_rate*` は `implementer_pass_rate*` の互換 alias として残り、`low-pass-rate` もこの role-aware population を使います。

## Terminal outcome

schema v3 の terminal writer は次のいずれかを保存します。

`completed_pass`、`completed_evidence`、`blocked_external`、`awaiting_approval`、`stale_superseded`、`failed`、`incomplete`、`user_aborted`、`routed_elsewhere`。

`evidence-submitted` は checker、planning、analyze role だけ `completed_evidence` へ写像し、implementer と release role では `incomplete` へ写像します。`partial-done` も `incomplete`、`routed-goal` は非比較 outcome の `routed_elsewhere` です。active record は terminal outcome を持ちません。明示 outcome が `passes`、`loop_active`、`halt_reason`、role、halt category と矛盾する場合は `failed` に fail-closed します。

schema v1/v2 の legacy record は読み取り時に導出し、audit/stats のためだけに物理 rewrite しません。`terminal_count` は `terminal_outcome_counts` の合計と一致し、active state は `non_terminal_count` として conservation 合計の外に記録します。

## 排他的な health count

- `active_count`: finite な scoring checkpoint がある fresh live session。
- `active_no_score_count`: finite な scoring checkpoint がない fresh live session。
- `stale_count`: progress timestamp が欠落・不正・未来、または stale threshold 超過の live session。
- `halt_count`: halt 済みの終端 session。
- `abandoned_count`: pass・halt の証跡がない inactive session。

`incomplete_count` は互換用として `active_count + active_no_score_count + stale_count` を表します。orphan cleanup 後に halt 済みの record は `halt_count` と completed 分母に残ります。

## 互換 alias

`pass_rate` は command ごとに従来の意味が異なるため deprecated です。

- `mission-state.py stats` の `pass_rate`、`pass_rate_numerator`、`pass_rate_denominator` は raw field の alias です。
- `mission-audit.py` の同名 field は completed field の alias です。

新しい consumer は `raw_pass_rate*` または `completed_pass_rate*` を明示的に選択してください。
品質 consumer は `implementer_pass_rate*`、証跡 workflow consumer は `evidence_completion_rate*` を使用してください。
