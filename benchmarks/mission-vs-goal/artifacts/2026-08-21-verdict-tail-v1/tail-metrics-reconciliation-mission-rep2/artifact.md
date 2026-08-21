# tail-metrics-reconciliation — mission arm (rep2)

## Mission

`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md` の
numbered claim を全件、raw table である
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`
に対して再計算し、各 claim を correct / incorrect に判定して、incorrect には算術付きの修正値を与える。
correct な claim は flag せず、verified-claims 節で明示的に correct と確認する。

- Task id: `tail-metrics-reconciliation` / category: analysis
- Arm: mission / profile: full / complexity: Complex / `--max-iter 2`
- Mission state: `.mission-state/sessions/cc-d87fa167-6acb-4757-8f26-92b442ad59df.json`
  (mission_id `acf49b42a89509fd`)
- 本成果物はこの 1 タスクの完遂のみを目的とし、arm 間の優劣は主張しない（比較は未計測）。

## Plan

canonical plan (`mission-plan/1`) を `planning adopt-core` で state に登録済み
（validated_at `2026-08-21T03:04:46Z`, operation id `plan-adopt-iter1-001`）。

| step | 内容 | 依存 | 完了条件 |
|---|---|---|---|
| s1 | fixture 2 件を読み、13 週 × 5 指標を確定する | — | 全行転記済み・claim 7 件列挙済み |
| s2 | 7 項目を算術で再計算する | s1 | 各項目に算術式と再計算値、incorrect には修正値 |
| s3 | confirmed findings と rejected candidates を分離する | s2 | 両区分が存在し、rejected に「疑わしく見えた理由」がある |
| s4 | machine-checkable findings table を作る | s2 | header 一致・verdict は drift / no-finding のみ・指定 key 文字列 |
| s5 | 8 見出しで成果物を書き出す | s3, s4 | 指定パスに artifact が存在し 8 見出しを持つ |

Out of scope: commit / push / network、fixture の編集、benchmark metadata
（task 定義・scoring 設定・answer key）の閲覧。

## Execution

### 使用した raw data（`weekly-metrics.md` からの逐語転記）

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

Notes 行（逐語）: 「the week-7 signup and cost spike coincides with the paid campaign that
ran that week. Uptime for the quarter was 99.95% (status page export).」

### claim 別の再計算

**Claim 1 — total_signups**（原文: 「Total signups for the quarter reached 4,127.」）

```
290+310+325+301+340+355+410+298+362+330+342+276+278
= 600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663, 3939, 4217
合計 = 4,217
```

判定: **incorrect**。修正値 **4,217**（主張の 4,127 との差 −90。数字の並びから
transposition が疑われるが、原因は本成果物では未計測で、事実として値が一致しないことのみを主張する）。

**Claim 2 — active_user_growth_pct**（原文: 「Active users grew from 8,200 to 9,430, a 15% increase.」）

```
始点 = week 1 の 8200 / 終点 = week 13 の 9430
増分 = 9430 − 8200 = 1230
1230 / 8200 = 0.15 = 15.0%
```

判定: **correct**。始点・終点・伸び率のいずれも table と一致する（丸めではなく厳密に 15.0%）。

**Claim 3a — p95_improvement_factor**（原文: 「p95 latency improved 3x over the quarter」）

```
620 / 330 = 1.8787... ≈ 1.88x
（改善率で見ても (620−330)/620 = 46.8% で、3x = 66.7% 減には届かない）
```

判定: **incorrect**。修正値 **約 1.88x**（620 ms → 330 ms）。

**Claim 3b — p95_improved_every_week**（原文: 「and improved every single week」）

```
週次差分（負が改善）:
w1→w2 −20, w2→w3 −30, w3→w4 −25, w4→w5 −25, w5→w6 −30, w6→w7 −35,
w7→w8 −75, w8→w9 +30 ← 悪化, w9→w10 −15, w10→w11 −25, w11→w12 −20, w12→w13 −20
```

判定: **incorrect**。week 8 の `380` から week 9 の `410` へ +30 ms 悪化しており、
12 回の週次遷移のうち 11 回が改善、1 回が悪化。修正値: **「1 週を除き改善（week 8 → week 9 で 380 → 410 と悪化）」**。

**Claim 4 — support_ticket_reduction_pct**（原文: 「Support tickets are down 42% quarter over quarter.」）

```
210 (week 1) → 149 (week 13)
削減 = 210 − 149 = 61
61 / 210 = 0.29047... ≈ 29.0%
```

判定: **incorrect**。修正値 **約 29.0%**（四半期内の week 1 → week 13 ベース）。
なお fixture には前四半期の値が無いため、文字どおりの「quarter over quarter」（前四半期との比較）は
このデータからは計算不能であり、未計測である。42% はどちらの読み方でも table から導けない。

**Claim 5 — avg_weekly_infra_cost_usd**（原文: 「Average weekly infra cost was held at about USD 1,300.」）

```
1400+1420+1380+1450+1500+1480+1620+1440+1460+1430+1410+1450+1410 = 18,850
18,850 / 13 = 1,450.0
```

判定: **incorrect**。修正値 **USD 1,450**。13 週中 1 週も 1,300 を下回っておらず
（最小は week 3 の `1380`）、「about USD 1,300」は範囲としても成立しない。

**Claim 6 — quarterly_uptime_pct**（原文: 「Quarterly uptime was 99.95%.」）

判定: **correct**。fixture の Notes 行「Uptime for the quarter was 99.95% (status page export).」
と逐語一致する。週次テーブルに uptime 列は無く、Notes 行が唯一の根拠である。

**Claim 7 — week-7 spike の説明**（items 一覧には対応キーが無いため findings table には含めない）

判定: **correct**。week 7 は signups `410`（13 週の最大値）、infra cost `1620`（同じく最大値）で
実際にスパイクしており、Notes 行の「the week-7 signup and cost spike coincides with the paid
campaign that ran that week」と整合する。ただし「campaign が原因である」という因果自体は
fixture の記述に依拠したもので、本成果物では独立に検証していない（未計測）。

### Confirmed findings（実在する欠陥）

| # | claim | fixture 逐語 | 再計算値 |
|---|---|---|---|
| F1 | 1 | `Total signups for the quarter reached 4,127.` | 4,217 |
| F2 | 3 前半 | `p95 latency improved 3x over the quarter` | ≈1.88x (620/330) |
| F3 | 3 後半 | `improved every single week` | week 8 `380` → week 9 `410` で悪化 |
| F4 | 4 | `Support tickets are down 42% quarter over quarter.` | ≈29.0% (61/210) |
| F5 | 5 | `Average weekly infra cost was held at about USD 1,300.` | USD 1,450 (18,850/13) |

### Rejected candidates（疑わしく見えたが finding ではないもの）

| 候補 | なぜ疑わしく見えたか | なぜ finding ではないか |
|---|---|---|
| Claim 2 の「15%」 | 概算値らしい丸い数字で、他の丸い数字（3x, 42%, 1,300）が軒並み誤っていたため同類に見えた | 1230/8200 = 0.15 で**厳密に** 15.0%。丸めですらない。table の 8200 / 9430 とも逐語一致 |
| Claim 2 の始点 8,200 | 「Active users (EOW)」= 週末値なので、四半期開始時点の値としては week 1 の EOW が始点として妥当か疑問だった | claim 自身が「grew from 8,200 to 9,430」と table 上の 2 値を名指ししており、両方 table に存在する。始点の解釈を変える根拠が fixture 内に無い |
| Claim 6 の 99.95% | 週次テーブルに uptime 列が無く、検証不能＝未裏付けに見えた | fixture の Notes 行が同じ 99.95% を「status page export」として明示している。source of truth 内に一致する記載があるため drift ではない |
| Claim 7 の week-7 spike | 「原因は campaign」という因果主張は table だけでは検証できない | スパイクの事実（410 と 1620 がいずれも 13 週の最大値）は table と一致し、因果の記述も Notes 行と一致する。因果の独立検証は未実施だが、fixture 内に矛盾は無い |
| week 12 の signups `276` の落ち込み | 13 週で最小値であり、異常値＝データ誤りに見えた | summary のどの claim もこの週を個別に主張していない。table は source of truth なので値そのものは検証対象外 |

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15 | 15 | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88 | 3 | drift |
| quarterly-summary.md | p95_improved_every_week | false | true | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0 | 42 | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | 1300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95 | no-finding |

### Verified claims（correct として確認したもの — flag しない）

- **Claim 2**（active user growth 15%）: correct。(9430 − 8200) / 8200 = 15.0%。
- **Claim 6**（quarterly uptime 99.95%）: correct。fixture Notes 行と逐語一致。
- **Claim 7**（week-7 spike は paid campaign によるもの）: correct。week 7 の signups `410` と
  infra cost `1620` はいずれも 13 週の最大値で、Notes 行の記述と整合する（因果自体は fixture 依拠）。

## Review

- Reviewer 2 名を単一メッセージで並列起動（Complex / シグナルなし → reviewer_count 2）。
  観点: (A) 算術の正確性と証拠引用、(B) 契約遵守（8 見出し・findings table 形式・
  confirmed/rejected 分離・over-flagging の有無）。
- 起動直前/全返却の時刻は `aggregate-reviews --reviewer-window` として state に記録した。
- reviewer の raw JSON は `.mission-state/archive/` に保存済み。ここでは逐語再掲しない（出力圧縮規律 #280）。
- 独立検証（`verification record`）: 13 週分の合計・平均・成長率・倍率・削減率・p95 単調性を
  Python で実行して再計算し、本文の値と一致することを機械確認した（意見ではなく実行結果）。

### Review 結果と、本文に反映した修正

- Reviewer A（算術）: 7 項目すべてを独立に再計算し、算術・verdict ともに一致。false positive
  （compliant なのに drift）も false negative（欠陥なのに no-finding）も検出されず、指摘 0 件。
- Reviewer B（契約遵守）: Medium 1 件・Low 1 件。いずれも iteration 1 内でインライン修正した。
  - **B1 (Medium)**: 指定 8 見出し以外に `## 修正履歴` が存在し level-2 見出しが 9 個になっていた
    → 当該節を削除し、見出しを指定の 8 個ちょうどに戻した。
  - **B2 (Low)**: Score 節の全セルが「tool-computed」で、成果物単体では採点内容が確認できなかった
    → reviewer が返した軸別 raw スコアを Score 節に明示した（gate 判定値はツール算出を正とする点は維持）。
- M6 に従い、Medium 指摘のインライン修正は自己検証で合格とせず、差分 Reviewer 1 名の再確認を経た。
- 以下は reviewer 指摘の前に本文へ織り込み済みの明示事項:
  - `support_ticket_reduction_pct` の「quarter over quarter」は前四半期データが無く文字どおりには
    計算不能である旨（未計測であることを明示）。
  - Claim 7 の因果関係は fixture 記述に依拠しており独立検証していない旨。
  - `total_signups` の差分について「transposition が原因」と断定せず、原因は未計測と明記。

## Score

Reviewer が返した `mission-review/1` の raw スコア（`.mission-state/archive/` に全量保存）:

| 軸 | Reviewer A（算術） | Reviewer B（契約遵守） |
|---|---:|---:|
| mission_achievement | 5.0 | 4.5 |
| accuracy | 5.0 | 4.5 |
| completeness | 5.0 | 3.5 |
| usability | 4.8 | 4.5 |

- High 指摘: 0 件。Medium: 1 件（B1）、Low: 1 件（B2）。いずれも iteration 1 内でインライン修正済み。
- 軸ごとの reviewer 間乖離は最大 1.5（completeness: 5.0 vs 3.5）で agreement gate の上限に一致。
- composite score / `open_high` / `max_agreement_delta` / `findings_evidence_path` の確定値は
  `review-finalize` が算出し `.mission-state/` に記録したものを正とする（上表は reviewer 提出値であり、
  gate 判定はツール側の算出値で行う）。
- scoring evidence: `.mission-state/archive/iter-1-acf49b42-scoring-a4befccd7d3bc6b9.json`
  / review aggregate: `.mission-state/archive/iter-1-acf49b42-reviews-8cf0c228affbf996.json`
  （reviewed base/head sha = `068dc40517caa7b85e50e618b2603ebec81cc1c2`）。
- `mark-passes` の結果: `{"ok": true, "passes": true, "forced": false}`（force override は未使用）。
  同時に completeness 軸の agreement が上限ちょうど（max−min = 1.50）である WARNING が出たが、
  gate 閾値 1.5 以内のため reject にはなっていない。
gate 式: `findings_evidence_path exists AND evidence_high_count == open_high AND
max_agreement_delta <= 1.5 AND composite_score >= 4.0 AND min(scored_items) >= 3.5 AND open_high == 0`。

## Stop Decision

- 停止条件: 成果物が指定パスに書かれ、scored review iteration を 1 回完了した時点。
- **結果: iteration 1 で gate 通過。`passes: true` / `loop_active: false` / `halt_reason` 空 /
  `next_action: report-complete`。High 指摘 0 件のため early-stop し、`--max-iter 2` の 2 回目は未実施。**
- iteration 1 で threshold 到達かつ `open_high == 0` の場合は early-stop（`--max-iter 2` 未使用のまま
  `closeout` へ）。未達の場合は critic → iteration 2 へ継続し、2 回目で未達なら
  `mark-halt --category partial-done` とする。
- commit / push / network / package install は一切行わない。編集したのは本成果物と
  `.mission-state/` のみ。

## Evidence

| 主張 | 証拠 |
|---|---|
| total signups = 4,217 | `weekly-metrics.md` Signups 列 13 値の総和（本文に累積和を明示） |
| growth = 15.0% | `8200`（week 1）, `9430`（week 13）; 1230/8200 |
| p95 factor ≈ 1.88x | `620`（week 1）, `330`（week 13）; 620/330 |
| p95 に悪化週あり | week 8 `380` → week 9 `410` |
| ticket reduction ≈ 29.0% | `210`（week 1）, `149`（week 13）; 61/210 |
| avg infra cost = 1,450 | Infra cost 列 13 値の総和 18,850 / 13 |
| uptime 99.95% | Notes 行「Uptime for the quarter was 99.95% (status page export).」 |
| week-7 spike の実在 | `410`（Signups 最大）, `1620`（Infra cost 最大） |
| 再計算の機械確認 | Python による総和/平均/比率/単調性チェックを実行し本文値と一致（`verification record`） |
| mission state | `.mission-state/sessions/cc-d87fa167-6acb-4757-8f26-92b442ad59df.json`, `.mission-state/archive/` |

未計測: 前四半期の support ticket 実績、campaign と week-7 spike の因果、
summary の誤り 5 件がどのような経緯で生じたか、他 arm との比較。

## Assumptions

| id | 仮置き | 検証方法 / 根拠 |
|---|---|---|
| a1 | `weekly-metrics.md` が唯一の source of truth で、その Notes 行も一次情報として扱う | 見出し「Q3 raw table (source of truth)」および Notes 行を逐語確認 |
| a2 | Claim 3 は 2 主張を含むため、findings では `p95_improvement_factor` と `p95_improved_every_week` の 2 行に分ける | prompt の items 一覧に両キーが別項目として存在する |
| a3 | support ticket の「quarter over quarter」は前四半期データが無いため week 1 → week 13 の四半期内変化として再計算する | fixture に前四半期の行・列が存在しない（未計測である旨を本文に明記） |
| a4 | active users の始点/終点は claim が名指しする `8200` / `9430`（week 1 / week 13 の EOW）とする | claim 本文が両値を明示しており、いずれも table に存在する |
| a5 | Claim 7 は items 一覧に対応キーが無いため findings table には行を作らず、prose の verified-claims でのみ扱う | prompt の items 一覧が 7 キーちょうどで、うち 2 つが Claim 3 由来 |
