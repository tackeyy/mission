# portfolio-std-metrics — mission arm (rep3)

## Mission

Reconcile the June 2026 revenue between two fixture documents, identify the numeric discrepancy, quote both values, and state the mechanical cause using the derivation notes. The data ledger is the source of truth.

- Task id: `portfolio-std-metrics` / category: analytics / complexity: Standard / arm: mission (profile: full)
- Mission state: `.mission-state/sessions/cc-6fcb982a-94ec-40ef-9dcd-fba288efb51b.json` (mission_id `205372c0f97dc1e5`)
- Routing: `init --complexity Standard` は mission ループを返した（`route: "goal"` verdict なし）。goal 契約へのルーティングは発生していない。

## Plan

Inline bounded plan (iteration 1, Standard, #339 — `next` が `plan-inline` を返したため mission-planner subagent は起動しない):

| Step | 内容 | 依存 | 完了条件 |
|---|---|---|---|
| 1 | 指定 fixture 2 件のみを Read（`data-ledger.md`, `finance-report.md`） | なし | 両ファイルの revenue 値と derivation note を取得 |
| 2 | 数値突合: ledger（source of truth）と finance report の June revenue を比較 | 1 | 乖離額と機械的原因を derivation note から特定 |
| 3 | 本 artifact に必須 8 見出しで証拠付き記載 | 2 | validator 要件（両値の引用 + 機械的原因）を充足 |
| 4 | reviewer 2 名を単一メッセージで並列起動し scored review を 1 iteration 実施 | 3 | `review-finalize` exit 0 |
| 5 | `closeout`（mark-passes → next）で gate 判定 | 4 | exit 0 かつ `next_action=report-complete` |

触る範囲: 本 artifact と `.mission-state/` のみ。触らない範囲: `benchmarks/mission-vs-goal/` 配下の fixture 以外すべて、コード、git 履歴。

## Execution

Step 1-2 実施済み。読んだファイルは指定 fixture 2 件のみ:

- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`

### 突合結果（確定した finding）

**June 2026 revenue の乖離: 45,930 (ledger, net) vs 48,210 (finance report, gross) — 差額 2,280 (JPY thousands)。**

1. **Data ledger（source of truth）**: 「Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280」— 正しい June revenue は **45,930**（JPY thousands）。
2. **Finance report**: 「Revenue for June: 48,210 (JPY thousands).」— 報告値は **48,210**（JPY thousands）。
3. **機械的原因（derivation notes より）**: ledger の導出規則は「revenue counts settled orders net of refunds」（返金控除後の settled orders を計上）。一方 finance report は自ら「The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag.」と記載している。つまり finance report のクエリは **settled テーブルに残る返金済みオーダーを refund flag でフィルタしていない**ため、返金分 2,280 を含む gross 値 48,210 を報告した。45,930 = 48,210 − 2,280 で差額は返金額に厳密に一致する。

### 棄却した候補（rejected candidates）

- **桁区切りフォーマット差**: ledger の Footnote F-1「some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference.」— フォーマットのみでデータ差ではないため、乖離原因として棄却。
- **USD 換算差**: finance report は「USD reporting: see the board deck for the converted figure.」と USD 値を記載しておらず、ledger の USD 295.0（45,930 / 155.7）との換算比較は本乖離の原因ではない。JPY thousands 同士の直接比較で乖離が確定するため棄却。
- **DAU / conversion 系の指標**: ledger には Peak DAU 11,987 や conversion 2.6% / 3.4% もあるが、finance report に対応する数値記載がなく revenue reconciliation の対象外。棄却。

## Review

Scored review iteration 1 実施済み（Standard = reviewer 2 名、単一メッセージ並列起動）。

- Reviewer A（正確性・証拠観点）: score 5.0 / High 0 / Medium 0 / Low 1（表引用に列見出しを併記すると監査が容易、という文体指摘）
- Reviewer B（validator 充足・完全性観点）: score 5.0 / High 0 / Medium 0 / Low 1（差額 2,280 の一致検算の明示を推奨 → Execution 節に検算式を記載済み）
- 集計: `review-finalize --iteration 1 --min-reviewers 2` exit 0。`parallel_execution` WARN なし。レビュー生データは `.mission-state/` 配下に保存（Evidence 参照）。

## Score

- composite_score: 5.0（threshold 4.0、min item 5.0 ≥ 3.5）
- max_agreement_delta: 0.0（≤ 1.5）
- open_high: 0 / findings evidence: 記録済み
- passes gate: すべて充足（`closeout` exit 0、`passes=true`）

## Stop Decision

Iteration 1 で threshold 到達かつ `open_high == 0` のため early-stop 原則に従い pass で停止。`closeout` が exit 0 で `next_action=report-complete` を返したことを確認してから完了と判定した。max-iter 2 のうち 1 iteration で完了。

## Evidence

| 主張 | 証拠（fixture からの逐語引用） |
|---|---|
| Ledger の正値は 45,930 | data-ledger.md: 「Net revenue (JPY thousands) \| 45,930 \| settled 48,210 minus refunded 2,280」 |
| Finance report の報告値は 48,210 | finance-report.md: 「Revenue for June: 48,210 (JPY thousands).」 |
| 機械的原因 = refund flag 未フィルタ | finance-report.md: 「the June query does not filter on the refund flag」 |
| Ledger の導出規則 = 返金控除後 | data-ledger.md: 「revenue counts settled orders net of refunds」 |
| 差額 2,280 は返金額と一致 | data-ledger.md: 「settled 48,210 minus refunded 2,280」→ 48,210 − 45,930 = 2,280 |
| フォーマット差は原因でない | data-ledger.md Footnote F-1: 「This is formatting only, not a data difference.」 |

- Mission state: `.mission-state/sessions/cc-6fcb982a-94ec-40ef-9dcd-fba288efb51b.json`（score_history / findings evidence を含む）
- Review 生データ: `.mission-state/` 配下の scoring JSON（`review-finalize` 入力）に全量保存
- 未計測事項: 実行時間・トークン消費は本 run では計測していない（unmeasured）。ベンチマーク優劣の主張はしない。

## Assumptions

- ネットワーク禁止のため `mission-local-authoring-sync.sh` は実行せず、リポジトリ root の `scripts/mission-state.py` を state CLI の正とした。
- specialists recommend は未実行: fixture 読解のみで完結し、`benchmarks/mission-vs-goal/` 配下の探索がベンチマーク規則で禁止されているため。
- 通貨単位は fixture 記載どおり JPY thousands。USD 換算は乖離判定に不要のため実施せず。
- 詳細は `.mission-state/sessions/cc-6fcb982a-94ec-40ef-9dcd-fba288efb51b-assumptions.md` に記録。
