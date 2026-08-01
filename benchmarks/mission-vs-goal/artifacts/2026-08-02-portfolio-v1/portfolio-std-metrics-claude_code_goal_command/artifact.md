# Goal

June 2026 の revenue（売上）について、`data-ledger.md`（source of truth）と `finance-report.md` の間の数値差異を特定し、両方の値を引用したうえで、derivation notes に基づく機械的な原因を述べる。

# Result

- Confirmed: 2つの文書間で June revenue の数値に差異がある。
- Data ledger（source of truth）の値: **45,930**（JPY thousands）
- Finance report の値: **48,210**（JPY thousands）
- 差異: 48,210 − 45,930 = **2,280**（JPY thousands）
- 機械的な原因: finance report の revenue クエリは refund flag でフィルタしておらず、返金済み（refunded）注文が settled table に残ったまま集計に含まれているため。

# Evidence

- data-ledger.md より（該当行を引用）:
  > | Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |

- data-ledger.md 冒頭の derivation rule（該当行を引用）:
  > Derivation rules: revenue counts settled orders net of refunds; ...

- finance-report.md より（該当行を引用）:
  > Gross performance was strong. Revenue for June: 48,210 (JPY thousands).

- finance-report.md の機械的原因を裏付ける記述（該当行を引用）:
  > The revenue query sums all settled orders in June. Refunded orders remain in
  > the settled table with a refund flag; the June query does not filter on the
  > refund flag.

以上から、data ledger の derivation note「settled 48,210 minus refunded 2,280」と finance report の「the June query does not filter on the refund flag」が整合しており、finance report の 48,210 は data ledger の settled 総額（refund 控除前）と一致する。つまり finance report は refund 控除前の gross settled 額を revenue として提示しており、これが 45,930 との差異（2,280）の機械的原因である。

# Assumptions

- 両フィクスチャファイル（data-ledger.md, finance-report.md）は本タスクで指定された通りの内容であり、それ以外の情報源（ベンチマークのタスク定義・採点設定・正解キー等）は参照していない（指示により意図的に未参照）。
- USD 換算値・DAU・conversion 等、本タスクの検証対象外の指標は本 artifact では扱っていない（未計測・対象外）。
- data-ledger.md が source of truth であるという前提はタスク指示に基づくものであり、それ自体の正しさは検証していない（指示を事実として採用）。

# Stop Condition

本 artifact が `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-std-metrics-claude_code_goal_command.md` に作成され、Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含み、data ledger の値（45,930）と finance report の値（48,210）を両方引用し、derivation notes に基づく機械的原因（refund flag でのフィルタ漏れ）を記載した時点で、本タスクは完了とする。
