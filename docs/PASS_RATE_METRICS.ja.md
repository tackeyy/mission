# Pass rate 指標

`mission-state.py stats` と `mission-audit.py` は同じ排他的な session health 分類を使い、全 session と完了 session の品質を別々に表示します。

## Rate

| field | 分子 | 分母 |
| --- | --- | --- |
| `raw_pass_rate` | pass session | 選択された全 session |
| `completed_pass_rate` | pass session | `pass + halt + abandoned + stale` |

両 rate は `_numerator` と `_denominator` を明示します。分母が 0 の場合は JSON `null` とし、`NaN` や infinity は出力しません。

実行中の fresh session は completed 分母だけから除外します。stale な実行中 session は未合格の完了 health debt として分母に含めるため、除外によって completed population が健全に見えることはありません。current session を暗黙には除外しません。明示した root・期間の対象外か、session identity に基づく重複排除の場合だけ除外します。

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
