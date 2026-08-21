# tail-metrics-reconciliation — mission arm (rep1)

## Mission

`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
に書かれた番号付き主張のすべてを、
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`
の生表（fixture 内で "source of truth" と明記）から再計算して事実確認する。誤りには
訂正値と算術を付し、正しい主張は正しいと明示的に確認する。

- Task id: `tail-metrics-reconciliation` / category: analysis
- Arm: mission (profile: full) / complexity: Complex / `--max-iter 2`
- 制約: commit / push / パッケージ導入 / ネットワークなし。`benchmarks/mission-vs-goal/`
  配下で参照したのは上記 2 fixture と本成果物のみ（ベンチマークのメタデータ・
  採点設定・解答キーは未参照）。ベンチマーク上の優劣は一切主張しない。

## Plan

`.mission-state/plans/64d64569eb6d33fb.json`（canonical plan, digest
`sha256:64d64569eb6d33fb…`）として採用したステップ:

| step | 内容 | 完了条件 |
|---|---|---|
| s1 | 2 fixture を読み、13 週 × 6 列を転記 | 全セル転記 |
| s2 | 7 key を算術で再計算 | 各 key に式と結果 |
| s3 | 各主張を correct / incorrect 判定、訂正値を付す | 7 主張すべてに判定 |
| s4 | confirmed findings と rejected candidates を分離 | 却下理由が付く |
| s5 | 指定 8 見出し + findings table 1 個で成果物を書く | artifact 書き込み |
| s6 | verification record → reviewer 2 名 → review-finalize → closeout | closeout exit 0 |

## Execution

### 生表（weekly-metrics.md, 転記）

| Week | Signups | Active users (EOW) | p95 latency (ms) | Support tickets | Infra cost (USD) |
|---:|---:|---:|---:|---:|---:|
| 1 | 290 | 8200 | 620 | 210 | 1400 |
| 2 | 310 | 8310 | 600 | 205 | 1420 |
| 3 | 325 | 8420 | 570 | 198 | 1380 |
| 4 | 301 | 8500 | 545 | 190 | 1450 |
| 5 | 340 | 8610 | 520 | 186 | 1500 |
| 6 | 355 | 8730 | 490 | 180 | 1480 |
| 7 | 410 | 8900 | 455 | 175 | 1620 |
| 8 | 298 | 8990 | 380 | 170 | 1440 |
| 9 | 362 | 9080 | 410 | 165 | 1460 |
| 10 | 330 | 9170 | 395 | 160 | 1430 |
| 11 | 342 | 9260 | 370 | 155 | 1410 |
| 12 | 276 | 9340 | 350 | 152 | 1450 |
| 13 | 278 | 9430 | 330 | 149 | 1410 |

表外 Notes（引用）:
「the week-7 signup and cost spike coincides with the paid campaign that ran that
week. Uptime for the quarter was 99.95% (status page export).」

### 再計算（claim ごと）

**Claim 1 — total_signups**（主張「Total signups for the quarter reached 4,127.」）

```
290 +310 =  600      600 +325 =  925      925 +301 = 1226
1226 +340 = 1566    1566 +355 = 1921    1921 +410 = 2331
2331 +298 = 2629    2629 +362 = 2991    2991 +330 = 3321
3321 +342 = 3663    3663 +276 = 3939    3939 +278 = 4217
```
実測合計 = **4,217**（13 週）。主張 4,127 との差 = 90。→ **incorrect**。
訂正値: 4,217。

**Claim 2 — active_user_growth_pct**（主張「grew from 8,200 to 9,430, a 15% increase.」）

- 起点: Week 1 の Active users (EOW) = `8200`、終点: Week 13 = `9430`（両方とも表の値と一致）。
- `(9430 − 8200) / 8200 = 1230 / 8200 = 0.15 = 15.0%`（丸めなしで厳密に 15%）。
→ **correct**。

**Claim 3a — p95_improvement_factor**（主張「p95 latency improved 3x over the quarter」）

- Week 1 p95 = `620` ms、Week 13 p95 = `330` ms。
- `620 / 330 = 1.8787…` → **約 1.88x**（改善幅は `620 − 330 = 290` ms、= 46.8% 低下）。
- 3x であれば Week 13 は `620 / 3 ≈ 206.7` ms でなければならないが、表の最小値は
  Week 13 の `330` ms。→ **incorrect**。訂正値: 約 1.88x（≒1.9x）。

**Claim 3b — p95_improved_every_week**（主張「and improved every single week.」）

- 週次差分を全 12 区間で確認: 620→600→570→545→520→490→455→380→**410**→395→370→350→330。
- Week 8 = `380` ms から Week 9 = `410` ms へ **+30 ms 悪化**（唯一の反転）。
  他の 11 区間は単調に改善。→ **incorrect**。
  訂正値: 「12 区間中 11 区間で改善。Week 8→9 のみ 380 ms → 410 ms と悪化」。

**Claim 4 — support_ticket_reduction_pct**（主張「Support tickets are down 42% quarter over quarter.」）

- 表の Support tickets: Week 1 = `210`、Week 13 = `149`。
- `(210 − 149) / 210 = 61 / 210 = 0.29047… = 29.0%`。
- 参考（合計ベース）: Q3 合計 = 2,195 件。前四半期の値は fixture に存在しないため、
  文字どおりの「quarter over quarter」（前四半期との比較）は **未計測 (unmeasured)**。
  表から算出できるのは四半期内の Week 1 → Week 13 減少率 29.0% であり、
  どちらの読み方でも 42% を支持する数値は表に存在しない。→ **incorrect**。
  訂正値: 29.0%（Week 1 → Week 13）。

**Claim 5 — avg_weekly_infra_cost_usd**（主張「Average weekly infra cost was held at about USD 1,300.」）

```
1400+1420+1380+1450+1500+1480+1620+1440+1460+1430+1410+1450+1410 = 18,850
18,850 / 13 = 1,450.0
```
実測平均 = **USD 1,450.0 / 週**。最小値でも Week 3 の `1380` で、1,300 を下回る週は
13 週中 0 週。→ **incorrect**。訂正値: USD 1,450（週平均）。

**Claim 6 — quarterly_uptime_pct**（主張「Quarterly uptime was 99.95%.」）

- 表に uptime 列は存在しない。表外 Notes に
  「Uptime for the quarter was 99.95% (status page export).」と明記され、値が一致。
- source of truth 内の唯一の uptime 記載と完全一致するため → **correct**。
  ただし週次行からの独立再計算は不可能（uptime の週次データは fixture に無く **未計測**）。

**Claim 7 — week-7 spike の帰属**（主張「The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week.」）

- Week 7 の signups = `410`（13 週の最大値。次点は Week 9 の `362`）、
  infra cost = `1620`（13 週の最大値。次点は Week 5 の `1500`）。両列とも Week 7 が
  実際にピークであることを確認。
- Notes が「the week-7 signup and cost spike coincides with the paid campaign that
  ran that week」と述べており、主張の内容と一致 → **correct**。
  なお Notes の語は "coincides"（同時発生）であり、因果を厳密に立証するデータ
  （campaign 支出内訳・対照群）は fixture に無く **未計測**。ピークの所在と
  campaign の存在という主張の事実部分は表と Notes で裏付けられる。

## Review

- Validator 対応:
  - 7 主張すべてを再計算付きで検証（Claim 3 は 2 つの下位主張に分解し、両方を検証）。
  - incorrect な主張（1 / 3a / 3b / 4 / 5）すべてに訂正値と算術を明記。
  - correct な主張（2 / 6 / 7）は下記 verified-claims 節で明示的に confirm。
- Reviewer: mission-reviewer 2 名を単一メッセージで並列起動（Complex / full tier）。
  レビュー JSON は `.mission-state/archive/` に保存し、`review-finalize` で集計。
  レビュー生データの逐語転記はしない（証跡はアーカイブに全量保存済み）。

### Verified claims（correct と確認したもの）

| claim | 主張 | 確認根拠 |
|---|---|---|
| 2 | active users 8,200 → 9,430, 15% 増 | `8200` / `9430` が表の Week 1 / Week 13 と一致し、1230/8200 = 厳密に 15.0% |
| 6 | quarterly uptime 99.95% | Notes の「Uptime for the quarter was 99.95% (status page export).」と一致 |
| 7 | week-7 spike は paid campaign による | Week 7 が signups `410` / cost `1620` で両列の最大値、Notes の campaign 記述と一致 |

### Confirmed findings（実在する欠陥）

| # | key | 誤り | 訂正値 | fixture 引用 |
|---|---|---|---|---|
| F1 | total_signups | 合計が 90 少ない | 4,217 | 主張「reached 4,127」 vs 表の 13 週合計 |
| F2 | p95_improvement_factor | 改善倍率の過大主張 | 約 1.88x | 「improved 3x」 vs `620` → `330` |
| F3 | p95_improved_every_week | 単調改善ではない | 12 区間中 11 区間のみ改善 | 「improved every single week」 vs Week 8 `380` → Week 9 `410` |
| F4 | support_ticket_reduction_pct | 減少率の過大主張 | 29.0% | 「down 42%」 vs `210` → `149` |
| F5 | avg_weekly_infra_cost_usd | 週平均の過小主張 | USD 1,450 | 「about USD 1,300」 vs 合計 18,850 / 13 |

### Rejected candidates（疑わしく見えたが finding ではないもの）

| 候補 | なぜ疑わしく見えたか | なぜ finding でないか |
|---|---|---|
| Claim 2 の「15%」 | 端数のない丸い数字は概算・丸め誤差の典型的サイン | `1230 / 8200` が厳密に 0.15。丸めが介在せず一致するため drift ではない |
| Claim 6 の uptime | 表に uptime 列が無く、週次行から再計算できない（値の出所が表外） | source of truth の Notes 行に `99.95%` が明記され、主張と完全一致。fixture 内に矛盾する uptime 値は存在しない |
| Claim 7 の campaign 帰属 | Week 8 で signups が `298` へ急落しており「campaign の効果」に見せかけた事後説明を疑った | Week 7 が両列とも 13 週の最大値であることを表で確認でき、Notes が同週の paid campaign を明記している。主張の事実部分は裏付けられる（因果の厳密検証は未計測だが、主張は "explained by" の域を超えていない） |
| Claim 4 の "quarter over quarter" 表現 | 前四半期データが無いため「比較対象そのものが存在しない」別種の欠陥に見えた | 数値誤り（42% vs 29.0%）として F4 に統合済み。同一箇所を二重に finding 化しない |
| Week 12–13 の signups 減少（`276` / `278`） | 四半期末の急落は集計漏れ・打ち切りを疑わせる | summary に該当する主張が無く、表内で矛盾もない。fact-check 対象の claim ではない |

## Score

Gate 値は `mission-state.py review-finalize` / `closeout` が算出した tool-computed 値のみを記載する（本節の値は finalize 実行後に転記した実測値であり、事前の見込み値ではない）。

### レビュアー別スコア（in-artifact 内訳・F-B-1 対応）

| 軸 | Reviewer A（算術・事実正確性） | Reviewer B（契約網羅性・証拠規律） | 軸差 |
|---|---:|---:|---:|
| mission_achievement | 5.0 | 4.0 | 1.0 |
| accuracy | 5.0 | 5.0 | 0.0 |
| completeness | 5.0 | 4.0 | 1.0 |
| usability | 4.0 | 4.0 | 0.0 |

最大軸差は 1.0（mission_achievement と completeness）で、gate 条件 `max_agreement_delta <= 1.5` を満たす。

### tool-computed gate 値（iteration 1）

| 項目 | 値 |
|---|---|
| iteration | 1 |
| composite_score | 4.5 |
| min(scored_items) | 4.0 |
| open_high | 0 |
| max_agreement_delta | 1.0 |
| threshold | 4.0 |
| passes | true |

レビュー指摘の反映状況（M6: Medium 以上はインライン自己承認しない）:

| finding | 重要度 | 対応 |
|---|---|---|
| B/F-B-1 | Medium | 本節にレビュアー別スコア内訳を追加し、Score 値の導出を artifact 内で追跡可能にした。Reviewer B が差分再確認 |
| B/F-B-2 | Low | Assumptions a6 に 7 key の一覧と claim 7 を table 対象外とした理由を明記 |
| A/I-01 | Low | Claim 1 の累積和を addend 付きに展開（Execution 節） |

## Stop Decision

- `passes = true`（`findings_evidence_path` 有り / `open_high == 0` /
  `max_agreement_delta <= 1.5` / `composite_score >= 4.0` / `min(scored_items) >= 3.5`）。
- iteration 1 で threshold 到達かつ open_high なしのため early-stop 条件を満たし、
  iteration 2 へは進まない（`--max-iter 2` の上限は未消費）。
- `closeout`（`mark-passes` → `next`）が exit 0 を返し、`next_action=report-complete`
  を確認した時点で終了。

## Evidence

| 主張 | 証拠 |
|---|---|
| 表の全数値 | `weekly-metrics.md` L5–L17（13 行）を Execution 節に全量転記 |
| signups 合計 4,217 | 13 項の逐次加算を Execution 節に明示 |
| infra cost 平均 1,450 | 合計 18,850 と除算 18,850/13 を明示 |
| p95 反転 | `| 8 | ... | 380 | ...` と `| 9 | ... | 410 | ...` の隣接 2 行 |
| uptime / campaign | `weekly-metrics.md` 末尾 Notes の原文引用 |
| mission state | `.mission-state/sessions/cc-cb99816d-af7a-4b33-80eb-a6844152f220.json` |
| canonical plan | `.mission-state/plans/64d64569eb6d33fb.json` |
| verification record | `mission-state.py verification record --iteration 1`（算術の再実行結果） |
| review 生データ / scoring | `.mission-state/archive/` 配下（review-import が保存した JSON） |
| ベンチマーク優劣 | 本成果物では一切測定・主張していない（未計測） |

## Assumptions

| id | 仮置き | 検証状況 |
|---|---|---|
| a1 | `weekly-metrics.md` の表と末尾 Notes が唯一の source of truth | fixture 見出しに "(source of truth)" と明記され確認済み |
| a2 | 四半期 = Week 1..13 の 13 週 | 表の行数 13 で確認済み |
| a3 | 「growth」「improvement」は四半期の始点(Week 1)と終点(Week 13)の比較 | claim 2 が明示する `8,200 → 9,430` が Week 1/Week 13 の値と一致することで裏付け。claim 4 も同じ規約を適用した |
| a4 | claim 5 の「about USD 1,300」は算術平均に対する主張 | 平均 1,450 との乖離は 11.5% で、13 週中 1,300 未満の週が 0 のため、どの丸め規約でも支持されない |
| a5 | 「quarter over quarter」の前四半期データは fixture に存在しない | 未計測。四半期内 Week 1→13 で代替評価した旨を Claim 4 に明記 |
| a6 | findings table は adjudication 対象として与えられた次の 7 key ちょうどで構成する: `active_user_growth_pct` / `avg_weekly_infra_cost_usd` / `p95_improved_every_week` / `p95_improvement_factor` / `quarterly_uptime_pct` / `support_ticket_reduction_pct` / `total_signups` | 7 key を上記に転記済み。claim 7（week-7 spike の campaign 帰属）はこの key 一覧に対応する metric key を持たない因果主張のため table には行を作らず、verified-claims 節と Execution 節で散文検証した（未評価ではない） |

## Findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15.0 | 15 | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88 | 3 | drift |
| quarterly-summary.md | p95_improved_every_week | false | true | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0 | 42 | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | 1300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95 | no-finding |
