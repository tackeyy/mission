# 全割当から検出・修復・改悪と予算内完遂を集計する設計と事前登録

決定案: 主結果は「予算内に受入可能な成果を残せない率」を、全割当（未起動・timeout・blocked・unsupported を分母に残す ITT）について、worker から分離した決定的な外部 evaluator だけで判定する。成功基準は、確認用 held-out cohort で verified-complex の率が native Goal の率の 1/10 以下であることを、事前に固定した片側区間で示すこととする。段階は smoke → pilot（H の 12 件）→ 検出力計算 → 確認実験の順で、各段の有料実行は owner の費用承認を要する。H の 12 件は開発・診断専用で、確認判定には使わない。

対象: [Issue 884: 検出・修復・改悪と予算内完遂を全割当から集計する](https://github.com/tackeyy/mission/issues/884)（親: [Issue 876: 実検証と反例修復で複雑タスクの品質を改善する](https://github.com/tackeyy/mission/issues/876)）。本書は設計と事前登録のみ。実装、テスト追加、benchmark・有料モデルの実行、Issue 起票・本文変更、Git 操作による公開は行っていない。

照合 head: `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`（開始時の worktree HEAD とローカル `origin/main` が一致）。以下の「現状」はこの固定 head のソースを指し、「決定」は追加予定の契約、「凍結」は事前登録として観測前に固定する項目を指す。引用 [S*] / [R*] / [D*] は末尾の出典へ結ぶ。2026-10-04 JST 時点で `gh pr list --state open --limit 100` は 0 件、#884 の `closedByPullRequestsReferences` も 0 件だった（best-effort 照合であり、着手の排他ではない）。

**本書で計算した数値は、実測ではなく事前登録用の見込みである。** 費用は既存 summary の API 相当額からの外挿、検出力は二項モデルの数値計算で、いずれも品質改善の証拠ではない。

## 1. 採用する境界と既存状態

決定: I は「実行」と「判定」と「集計」を分ける。実行は G の adapter、判定は H 形式の外部 evaluator、集計は I が担い、Mission の内部状態（`passes`、score、finding の解決状態）は正解として使わない。E の lineage/event は「どの段で何が起きたか」の帰属にだけ使い、正誤は常に外部 evaluator の結果で決める。

| 照合した現状 | I の選択 |
|---|---|
| G の結果 schema `native-goal-benchmark-result/1` は `outcome` を `completed/failed/blocked/unsupported/not_started`、`fidelity` を `verified/unverified/not_applicable` に閉じ、verified には一致した manifest を要求する。[S01] | I の入力 record として使う。G の `outcome=completed` は host の lifecycle 観測であって品質の合格ではないので、主結果には使わない |
| `preserve_assignment_outcomes` は計画した全割当を出力し、record の無い割当を `not_started`/`missing_assignment_record` で補い、計画外 record と重複を拒否する。[S02] | 全割当の会計の基礎にする。I はこの関数を再実装せず呼ぶ（同じ判定を 2 通りに持たない） |
| Codex の Goal arm は `thread/goal/set/get` の同一性と terminal status を観測して初めて verified になり、`budgetLimited/usageLimited` は verified blocked、turn 上限は `assignment_turn_limit`。`tokensUsed` を記録する。[S03][S05] | 確認実験の host は Codex に限る（§2）。Goal arm の token 使用量は G の observation から取る |
| CC の Goal arm は `/goal` 送信を観測しても lifecycle を読み戻せず、常に `fidelity=unverified`, `outcome=failed`, `goal_lifecycle_unobserved` になる。[S04] | CC host は確認実験から外す。lifecycle を観測できる手段が出るまで unsupported として報告する |
| Codex の Mission arm は固定 package の skill を入力し、`.mission-state/sessions/cx-<thread>.json` の `passes`/`halt_reason` を読む。`budget_enforcement` は `unavailable`。[S05][S06] | Mission の `passes` は「Mission が完了を主張したか」の記録にだけ使う。予算は全 arm 共通の wall-clock 上限で揃える（§3）。Mission 側の token 使用量の取得経路は **UNKNOWN**（smoke で確認） |
| `RpcProcess` は起動時刻から一つの deadline を持ち、`--timeout-seconds` が run 全体の wall-clock 上限になる。[S07] | 予算の共通単位にする |
| package は `git archive <commit> plugins/mission skills/mission` から作り、manifest は commit・source tree・package の digest と条件を束縛する。[S08] | baseline と verified-complex の package をこの経路で固定 SHA から作る |
| G の record には USD 費用・run の所要時間・介入回数の欄がない（observation にあるのは Codex Goal の `tokensUsed` のみ）。[S05][S09] | I1 で費用・時間・介入の欄を集計 schema 側に追加し、取れないものは null と理由で残す。0 で埋めない |
| H の evaluator は候補を fresh な領域へ複製して別 process で実行し、3 ケースを evaluator 所有の期待値と比較する。候補の自己申告は使わず、0 ケース・不正出力・候補変化は非 pass、timeout は blocked。[S10] | 主結果の判定器はこれと同じ契約（決定的・fail-closed・候補 digest 束縛）を満たすものに限る |
| `evaluate_assignment` は固定 commit の生成器と照合し、worker 群（`fixture_group="worker"`）の雛形 digest と一致した割当だけを評価する。[S11] | 現状は欠陥入りの starter から始める割当しか評価できない。正常な control から始める割当（改悪 u の測定、§4）は I1 で `fixture_group` を割当に持たせて評価する（evaluator の判定規則は変えない） |
| H は 6 family × 2 task の 12 件で、各 task に worker（欠陥入り）・reference・control（正常）がある。[S12][S13][S14] | 開発・診断用。H の README は「開発用 cohort であり 10 倍や一般的な品質の証拠ではない」と明記している。[S15] **確認判定に使わない** |
| H の export は生成した一時 repo から G の positive allowlist export を呼ぶ。[S16] | G の probe と H の評価を割当単位でつなぐ実装は現状どこにも無い。I が結合する（§7）。結合が実際に通るかは **UNKNOWN**（smoke で確認） |
| E は `mission-repair-event/1` で before/after 候補・receipt・比較可否を供給し、全割当の分母・正誤・不要変更・費用の集計は I が担うと決めている。E4 より前の期間は event が無く、未測定として扱うことを I へ要求している。[D01][D02][D03] | 段の帰属（検出・修復・改悪）は E の event を event ID で重複排除して割当と join する。event が無い割当の d/r/u は「未測定」で、0 とは書かない |

**未 merge 依存**: E（#880 の E1〜E4）、F（#881）、J（#885）は固定 head に存在しない。本書はそれらの設計文書の契約を参照するだけで、実装を前提にしない。E の event schema と J の profile の実体は、I の各 PR の着手時に最新 main で再照合する。

## 2. 比較する arm

凍結: 確認実験の比較は **同じ host（Codex）・同じ model ID・同じ effort・同じ permission profile・同じ wall-clock 上限**で行う。arm 間で違うのは実行方式と package だけとする。

| arm | 中身 | 位置付け |
|---|---|---|
| `native_goal` | Codex native Goal（G の `codex_native_goal`）。Mission package を与えない | 主比較の基準 |
| `mission_baseline` | Mission の固定版 package。候補 SHA `ed1d1c2723c55c08f06a82d6e397b164323b5b59` | 副次（#876 の改善の帰属） |
| `mission_verified_complex` | J（#885）merge 後の verified-complex profile。package SHA は確認実験の事前登録時に凍結 | 主比較の対象 |
| `ablation_gate_only` | 受入条件の検証 gate まで（反例探索・修復なし） | 参考のみ。確認判定に使わない |
| `ablation_gate_fresh_review` | gate と独立した反例探索まで（修復なし） | 参考のみ。確認判定に使わない |

**baseline に `ed1d1c27` を使う根拠（照合済み）**: このコミットは固定 head の祖先で、#876 の最初の実装 PR である [#886 feat(quality): 受入条件の契約を型付き状態へ追加する](https://github.com/tackeyy/mission/pull/886) の merge commit `d83f7e0c` の親そのものである（`git log -1 --format=%P d83f7e0c` が `ed1d1c27…` を返した）。`ed1d1c27..d25a66c6` の 14 commit はすべて #876 系の作業で、最古が #886。初期調査の提案書もこの SHA を調査の起点にしている。[R01] したがって「#876 の作業が入る直前の main」であり、改善の前後比較として公平な固定点になる。

baseline の限界: この版の Mission は verified-complex profile を持たないため、通常の profile で動く。G の adapter が読む state の場所・形式をこの版が満たすかは **UNKNOWN** で、smoke で確認する。満たさない場合、baseline の割当は `mission_state_unobserved` で failed と記録されるが、これは Mission の品質ではなく観測の失敗なので、主比較には使わない（baseline は副次 arm）。

**ablation の作り方**: J の profile に「gate のみ」「gate + 反例探索」を選ぶ切替が入る場合だけ ablation を実施する。途中の commit（例: C の merge 時点）から package を作る方法は、同時に入った他の変更と区別できないので採らない。J に切替が無い場合、ablation は実施せず「未実施」と報告する（**UNKNOWN**: J の設計で切替を持つかは未確定。§8 の決定事項）。

ablation は「同じ初回成果」から始める診断比較で行う（Issue 884 の「同じ初回成果による診断比較」）。ある割当で得た初回候補を凍結し、それを starter として各 variant に検出・修復だけをさせる。end-to-end 比較とは別 report に出し、母集団を合算しない。

## 3. 主要評価項目と成功基準（事前登録）

### 3.1 主要評価項目

凍結: **主結果 F_arm = 予算内に受入可能な成果を残せなかった割当の数 ÷ 計画した割当の数**（arm ごと）。

- 分母は計画した全割当。`not_started`・`unsupported`・`blocked`・timeout・予算超過・record 欠落・evaluator が評価できなかった候補（候補の不正・変化）を、すべて分母に残し「残せなかった」に数える。
- 「受入可能」の判定は外部 evaluator の `status == "passed"` だけ（全ケース pass、ケース数が期待どおり、候補 digest が凍結 envelope と一致）。[S10]
- 「予算内」は wall-clock 上限 T 以内に存在した候補を、上限時点で凍結して評価したもの。上限を超えた run は、その時点の候補を評価する（途中成果でも受入可能なら成功、未完でも途中成果は捨てない）。Mission の `passes`、score、finding の解決状態、Goal の `complete` status は使わない。
- 正しい halt が要求される課題では、その halt を受入条件として evaluator が採点する（Issue 884 本文）。確認用 cohort の作成時に該当課題を明示する。
- evaluator 自身の基盤障害（`evaluator_process_unavailable` など、候補に起因しないもの）だけは、凍結した同じ候補を再評価してよい。evaluator は決定的なので worker を再実行しない。元の評価記録は残す。

**worker の再実行規則（凍結）**: 再実行してよいのは、turn 開始前の基盤障害で `not_started` になった割当だけ（host の起動失敗、app-server の接続不能）。両 arm に同じ規則を適用し、1 割当につき 1 回まで、元の record は保持する。主解析は再実行後の結果を使い、元の結果を「失敗」として数える感度解析を併記する。turn が始まった後の失敗は再実行しない。

### 3.2 成功基準と区間の方法

凍結:

- 解析単位は **task**。確認実験では各 (task, arm) を **1 回だけ**実行する。同じ task の反復は独立な観測として数えない。
- 片側の総有意水準 0.05 を、2 つの片側 Clopper-Pearson 限界へ Bonferroni で分ける（単一 host なので各 0.025）。
  - `U_vc` = verified-complex の失敗数 x_vc / n に対する片側 97.5% 上限
  - `L_goal` = native Goal の失敗数 x_goal / n に対する片側 97.5% 下限
  - `RR_upper = U_vc / L_goal`
- **成功（`achieved`）**: `RR_upper ≤ 0.1`、かつ下記の guard を満たす。
- guard: 正常な control から始める割当（§4）で、verified-complex の失敗率の点推定が native Goal 以下であること。満たさない場合、主結果の判定に関わらず `achieved` にせず、`achieved_with_regression_guard_failed` として両方の数値を報告する。
- 2 host で行う場合は各限界を 0.0125 とする（初期調査の例示計算と同じ配分）。[R02] host ごとに判定し、合算しない。

この方法は初期調査の `statistical-examples.json` と同じ手順（周辺の正確な二項限界 + Bonferroni）である。[R02] 対応のある設計（同じ task を両 arm で実行）を活かさない分だけ保守的になる。task family 内の相関は主解析では扱わず、family 別の内訳と、pilot で推定した design effect（DEFF）で n と x を割った感度解析を併記する（DEFF は pilot 終了時に凍結し、確認実験の結果を見て変えない）。感度解析の結果は判定を変えないが、必ず報告する。

### 3.3 「未達」の報告の仕方（凍結）

| 判定 | 条件 | 報告する文 |
|---|---|---|
| `achieved` | 全割当を解析し、`RR_upper ≤ 0.1` と guard を満たす | 「この cohort・host・model・予算の下で、予算内に受入可能な成果を残せない率は標準 Goal の 1/10 以下だった（`RR_upper`）」。条件を必ず併記し、一般化しない |
| `not_achieved` | 全割当を解析し、`RR_upper > 0.1` | 「標準 Goal の 1/10 以下は示されなかった」。観測比 `(x_vc/n)/(x_goal/n)`、`U_vc`、`L_goal`、`RR_upper`、x と n を全部出す。改善量と失敗例も公開する |
| `not_comparable` | `x_goal = 0`（`L_goal = 0`） | 「標準 Goal が失敗しなかったため比を定義できない」。cohort が弁別しなかったことを記録する |
| `inconclusive_incomplete` | 費用上限・基盤障害の停止規則で計画の全割当を実行できなかった | 実行できなかった割当を `not_started` として数えた数値を出し、「判定不能」と書く。途中結果で `achieved` を宣言しない |
| `achieved_with_regression_guard_failed` | `RR_upper ≤ 0.1` だが guard 不成立 | 主結果と改悪の両方を出し、「1/10 以下」の表現を単独で使わない |

別の指標・別の閾値・別の部分集合へ判定を移すことはしない。判定基準の変更は、観測前に限り、理由と日付を事前登録の文書へ記録して行う。観測後の変更は事前登録の無効化として扱い、report に明記する（既存の事前登録の規律と同じ）。[R03]

## 4. 副次評価項目

すべて分子・分母・null の理由を持つ。分母 0 は `null`（理由 `zero_denominator`）とし、0 や 1 で埋めない。

| 項目 | 定義（外部 evaluator の判定で数える） | 取れない場合 |
|---|---|---|
| 段の候補 | S0 = 初回実装の候補、S2 = 修復後の候補、S3 = 最終候補。E の event の before/after candidate ref から復元し、各段を外部 evaluator で評価する | event が無い・候補 ref が absent なら、その割当の段は `unmeasured` |
| d（検出率） | S0 が不合格の割当のうち、最終より前に Mission が「同じ割当について失敗を観測した finding」（E の `reverification-observed` failed または D の finding 導入）を持つ割当の割合 | 分母 0 → null。native Goal は段が観測できないので `not_applicable` |
| 誤検出 | S0 が合格の割当のうち、finding を導入した割合（報告のみ） | 同上 |
| r（修復率） | S0 不合格かつ検出した割当のうち、S3 が合格になった割合 | 同上 |
| u（改悪率） | S0 が合格の割当のうち、S3 が不合格になった割合。加えて control から始めた割当（正常な starter）で S3 が不合格になった割合を別に出す | control の割当は native Goal にも適用でき、u の arm 間比較はこちらを使う |
| 全割当の完遂 | evaluator 合格の割合を arm・family 別に | — |
| 未完了の理由 | `not_started`・`unsupported`・`blocked`・timeout・予算・evaluator 判定の理由コード別件数 | 理由が無い record は `reason_missing` として数える |
| 費用 | API 相当額（host が報告する値）と、実際の請求額を別欄に持つ。全 child・retry・再実行を含む | Codex の token→USD 換算は **UNKNOWN**。請求額は定額プランでは 0 円でなくプランの rate limit 消費であり、金額としては **UNKNOWN**。いずれも null と理由 |
| 時間 | run ごとの wall 時間、run の総和（総実行時間）、段階全体の経過時間を別欄に | 並列実行の経過時間を run の総和と混同しない |
| 介入 | 人の介入回数。harness が無人で回したことを記録できた場合だけ 0 | 記録できなければ **UNKNOWN** |
| task family 別 | 主結果と全副次項目を family 別に | 部分集団での主張はしない |

段の帰属（失敗した割当を一つの分類へ）: `infrastructure`（not_started/unsupported）→ `budget`（timeout・budget 系の理由）→ `detect`（S0 不合格・未検出）→ `repair`（検出・未修復）→ `regression`（S0 合格→S3 不合格）→ `false_completion`（Mission が完了を主張したが S3 不合格）→ `unmeasured`（段が観測できない）。上から順に最初に当たるものに帰属させ、規則を report に載せる。

## 5. 確認用 held-out cohort

凍結:

1. **作成者**: 実装（E・F・I・J の branch、Mission の内部、H の evaluator 実装の詳細）を見ていない独立した agent が作る。作成者には H の形式（要求・欠陥入り starter・公開 smoke・3 件以上の外部評価ケース・reference 修復・正常な control）と中立な命名規約だけを渡す。H の 12 task は再利用しない。
2. **規模の下限**: 6 family 以上 × 各 2 task 以上。これは下限であり、確認実験の n は §6 の検出力計算で決まる（30% の Goal 失敗率でも約 180 task が要る見込み。§6.3）。必要な task 数が作成可能な数を超える場合は owner へ上げる。
3. **構成**: family ごとに欠陥入りの starter から始める task と、正常な control から始める task（guard と u 用）を、作成前に決めた比率で含める（推奨 2:1。§8 の決定事項）。正しい halt が要求される task は明示する。
4. **evaluator の分離**: evaluator・ケース・reference は worker の export に含めない（G の positive allowlist export を使う）。[S16] evaluator は worker と別の process・別の場所で実行する。Mission の package に cohort の内容が含まれないことを、package の digest 対象ファイルと cohort の file 一覧の照合で確かめる。
5. **凍結**: cohort の bundle（生成器・catalog・要求文・ケース・reference・control）を正規化した tar の SHA-256 を digest とし、family 名・task 数・構成比・作成日・作成者の種別とともに **事前登録の文書として commit してから**、いかなる run（開発 run・smoke・pilot を含む）もこの cohort に触れない。bundle 本体は確認実験が終わるまで公開 repo の外（owner が管理する場所。§8）に置き、終了後に repo へ公開して digest を照合できるようにする。
6. **使用回数**: 確認実験で一度だけ使う。確認実験の後に cohort を開発に使った場合、それ以降の結果は確認結果として扱わない。

## 6. 段階的実行・費用見積り・停止規則

### 6.1 費用の算定式

```
C_stage = Σ_arm N_arm × ĉ_arm × (1 + ρ)
```

- `N_arm`: その段で計画した割当数。`ρ`: §3.1 の規則で許す再実行の上限比率（凍結値 0.1）。evaluator は決定的なローカル実行なので費用に含めない。
- `ĉ_goal = 0.9477`、`ĉ_baseline = 5.9447`（USD、API 相当額の 1 run 平均）。出典は保存 summary の `cost_usd_mean`（Goal 15 records 合計 14.2157、Mission 15 records 合計 89.1708）。[R04][R05]
- **この値の限界**: CC host・`tail` cohort（5 task × 3 反復）・開始 commit `068dc405` の値で、Codex host・複雑 task の値ではない。Mission は 15 件中 6 件が `max_budget_usd` で打ち切られており、`ĉ_baseline` は打ち切り込みの値（打ち切りの無い費用はこれ以上でありうる）。summary 自身が「runtime が報告する API 相当の推定で請求額ではない」と書いている。[R06][R07]
- **`ĉ_vc`（verified-complex）は smoke まで UNKNOWN。** 以下では仮定として baseline の 1 倍・2 倍・3 倍を並べるが、これは見積りの幅を示すための仮定であって予測ではない。

### 6.2 段ごとの計画と見積り（USD、API 相当、ρ を含まない値。ρ=0.1 の上限は ×1.1）

| 段 | 割当 | Goal | baseline | verified-complex（仮定 1× / 2× / 3×） | 合計（1× / 2× / 3×） |
|---|---|---:|---:|---:|---:|
| smoke | 2 task × 3 arm × 1 | 1.90 | 11.89 | 11.89 / 23.78 / 35.67 | 25.67 / 37.56 / 49.45 |
| pilot | H 12 task × 2 反復 × 3 arm | 22.74 | 142.67 | 142.67 / 285.35 / 428.02 | 308.09 / 450.76 / 593.44 |
| 確認（例: n=180） | 180 task × 2 arm × 1（baseline は任意） | 170.59 | （任意 1,070.05） | 1,070.05 / 2,140.09 / 3,210.14 | 1,240.63 / 2,310.68 / 3,380.72（baseline 除く） |
| 確認（例: n=560） | 560 task × 2 arm × 1 | 530.71 | — | 3,329.03 / 6,658.06 / 9,987.10 | 3,859.74 / 7,188.78 / 10,517.81 |

smoke の目的は harness の検証と `ĉ_vc`・Codex 上の実費用・所要時間の実測で、smoke の結果は品質の主張に使わない。pilot の目的は H 上の native Goal 失敗率と task 内相関（DEFF）と `ĉ_vc` の測定で、品質の主張に使わない（H は開発用）。

### 6.3 検出力計算と確認実験の n（凍結した手順）

pilot 後、確認実験を始める前に、次の手順で n を決めて事前登録の文書に commit する。

1. 計画用の Goal 失敗率 `p_goal` = pilot の native Goal 失敗率の片側 80% Clopper-Pearson **下限**（楽観を避ける）。
2. verified-complex の真の失敗率を 0 と 0.01 の 2 通りで仮定する。
3. §3.2 の判定方法で、検出力 0.8 を満たす最小の n（task 数）を二項分布の厳密計算で求める。`p_vc = 0` の値を下限、`p_vc = 0.01` の値を参考として報告し、確認実験の n は owner が費用とあわせて選ぶ（選んだ後は変えない）。
4. n × §6.1 の費用が承認上限を超える、または n が held-out cohort の task 数を超える場合は、確認実験に進まず owner へ上げる。

本書の作成時に同じ方法で計算した見込み（独立な task を仮定、片側 0.025 ×2、検出力 ≥0.8。実測ではない）:

| Goal 失敗率（仮定） | 目標（1/10） | verified-complex の真の率（仮定） | 必要な task 数 / arm |
|---|---|---|---|
| 30% | ≤3% | 0 | 約 180（170 で検出力 0.72、180 で 0.86） |
| 30% | ≤3% | 1% | 約 620（600 で 0.80、625 で 0.82） |
| 20% | ≤2% | 0 | 約 275（270 で 0.80、280 で 0.87） |
| 10% | ≤1% | 0 | 約 560（540 で 0.74、560 で 0.82） |
| 10% | ≤1% | 0.5% | 2,000 を超える（2,000 で 0.44） |

**Goal の失敗率が 10% 程度なら、1/10 を示すには数百〜数千の独立な task と、上表で数千 USD 相当の実行が要る。** この場合は確認実験に進まず owner へ上げる。参考として、100 件中 0 件の失敗でも片側 97.5% 上限は約 3.6% で、真の失敗率が 0 とは言えない（初期調査の片側 95% では約 2.95%）。[R02][R08]

### 6.4 停止規則と費用上限（観測前に凍結）

| 段 | 進む条件 | 止める条件 | 費用上限（承認対象） |
|---|---|---|---|
| smoke | 6 割当すべてに record があり、Goal arm が `fidelity=verified`、Mission 2 arm の state を観測でき、evaluator が全候補をケース数 3 で評価し、費用・時間の欄が値か理由付き null で埋まる | いずれかを満たさない。harness を直して smoke をやり直す（smoke の結果は主張に使わない） | 承認額。推奨は 3× 仮定の合計 49.45 × 1.1 ≈ 55 USD 相当 |
| pilot | 72 割当を全件記録し、`ĉ_vc` と DEFF を得る | ① native Goal の失敗が 24 割当中 2 以下（H で弁別しない。確認に要る n が非現実的になる見込み）→ owner へ上げる ② Mission 2 arm の `not_started`/`unsupported` の合計が 10% を超える → harness 停止 ③ 累計の API 相当額が承認額に達した → 停止し、残りを `not_started` として報告 | 承認額。3× 仮定で 593.44 × 1.1 ≈ 653 USD 相当 |
| 確認 | §6.3 で決めた n を全件実行 | 成功による早期停止はしない（中間解析を行わない）。基盤障害で `not_started` が最初の 20% の割当の 10% を超えたら一時停止し、owner へ上げる。費用上限に達したら停止して `inconclusive_incomplete` | §6.3 の n と `ĉ_vc` の実測から算出し、owner が承認する |

費用は Codex の Mission arm で run ごとに強制できない（`budget_enforcement=unavailable`）。[S05] したがって上限は「割当の実行を順に進め、各 run の後に累計を確かめて止める」形で守る。run ごとの強制は共通の wall-clock 上限 T だけで、T は smoke の実測を見て pilot 前に凍結する（全 arm 同じ値）。

## 7. 集計の実装と PR 分割

### 7.1 report schema（決定）

`mission-benchmark-aggregate/1`（JSON）。Markdown は JSON からだけ描画する。

- `inputs`: 割当計画の digest、record 群の digest、evaluator（生成器 bytes・catalog）の digest、cohort digest（確認実験のみ）、事前登録文書の commit SHA、各 arm の package SHA と digest、host・model・effort・permission・T。
- `assignments[]`: 計画した全割当。`assignment_id`、task、family、`fixture_group`（worker/control）、arm、run の `outcome`/`reason`/`fidelity`、評価の `status`/`reason`/`case_count`/候補 digest、段ごとの評価（S0/S2/S3。無ければ `unmeasured` と理由）、帰属（§4）、費用・時間・介入（値または null と理由）。
- `arms{}`: 主結果（x, n, F, 片側限界）、副次項目（分子・分母・null 理由）、理由コード別の件数、family 別の内訳。
- `primary_judgement`: §3.3 の判定値と、判定に使った数値。`claims_allowed`: 判定値から機械的に決まる許可文だけ（自由記述を置かない）。
- `report_kind`: `end_to_end` / `diagnostic_ablation` / `development`（H）/ `confirmatory`。kind が違う report を一つの母集団に合算しない。

### 7.2 全割当の会計（決定）

- 計画 → record の結合は G の `preserve_assignment_outcomes` を呼ぶ。[S02] 計画外 record・重複・不一致は集計全体を拒否する（理由付き終了）。
- 評価が無い割当は「評価欠落」として失敗に数え、件数を別に出す。評価の重複は拒否する。
- E の event は event ID で重複排除して割当に join する。join できない event は件数を出して拒否理由に残す（黙って捨てない）。
- 計画・record・評価・event のどれかが読めない場合は report を出さずに理由付きで失敗する。一部だけで report を出さない。

### 7.3 段の帰属と ablation runner（決定）

- 段の候補（S0/S2/S3）は E の event の候補 ref から凍結済みの成果物を取り出し、H 形式の evaluator で評価する。event の無い期間（E4 より前）や absent の ref は `unmeasured`。[D02]
- ablation runner は、ある end-to-end 割当の S0 を starter として凍結し、variant ごとに新しい割当（`diagnostic_ablation`）を作って G の adapter で実行し、同じ evaluator で評価する。variant 間で starter・T・host・model は同じにする。
- end-to-end の runner は、計画に従って H 形式の task を生成し（`materialize_task`）[S17]、G の probe を割当ごとに実行し、凍結した候補を `evaluate_assignment` で評価する。worker の雛形照合には別に作った無変更の export を使う（G の candidate は worker が書き換えた後の木なので、雛形照合に使えない）。[S11][S09]
- runner は割当を順に実行し、§6.4 の停止規則と累計費用を各 run の後に確かめる。並列化は既定にしない（費用上限の判定が遅れるため）。

### 7.4 PR 分割と見積り

1 PR = 1 子の方針に従い、#884 を 2 PR に分ける。見積りは未実測で、配布 mirror と生成物を除いた reviewed lines（追加+削除）。repo の閾値は 600 行で説明、1,400 行で分割必須。[S18] ×1.6 は依頼で指定された較正係数で、repo 内の出典は見つからなかった（**UNKNOWN**）。

| PR | 内容 | raw 見積り | ×1.6 | 閉じ方 |
|---|---|---:|---:|---|
| I1: 集計と report | 割当計画の型、record・評価・E event の結合、全割当の会計、主結果と副次項目、§3 の区間計算、帰属、`fixture_group` を持つ評価呼出し、JSON schema と Markdown 描画、偽の割当欠落・重複・分母 0・event 欠落・evaluator 欠落・family 反復を Red にするテスト | 650〜820 | 1,040〜1,312 | `Refs #884` |
| I2: runner と ablation | 計画に従う end-to-end runner（H 形式の task 生成 → G probe → 凍結 → 評価）、停止規則と累計費用の確認、同じ初回成果からの ablation variant、offline の契約テストと H での診断手順 | 560〜760 | 896〜1,216 | `Closes #884` |

どちらも 600 行を超える見込みなので、各 PR 本文に「分割しない理由」を書く（I1 は会計と判定が同じ型の producer/consumer で、片方だけ main に入ると分母の無い判定ができてしまう。I2 は runner の停止規則と実行が同じ関心事）。実 diff で再計測し、1,400 行を超えたら再分割する。

TDD・既存の CI 経路（`make test-shard` → Quality）・配布 mirror・artifact hygiene は既存の規律に従う。判定・validator を変える PR なので、異系統レビューの前に正常・拒否あわせて 50 入力以上の独立探索を行い、件数と発見数を PR 本文に記録する（Issue 884 本文）。通常 CI に有料のモデル実行を追加しない。

## 8. owner の決定事項・対象外・主張してはならないこと

### 8.1 owner の決定が要る事項

1. **段ごとの費用承認**: smoke・pilot・確認の各段で有料実行の額を承認する。承認が無い段は実行しない。確認実験の額は pilot の後に §6.3 で算出して改めて承認を求める。
2. **host の範囲**: 確認実験を Codex のみとするか。CC は Goal の lifecycle を観測できず、現状では unsupported（§1）。
3. **baseline arm を pilot・確認に含めるか**: 含めると pilot で約 143 USD 相当、確認で n × 5.94 USD 相当が加わる。主判定には不要で、#876 の改善の帰属にだけ使う。
4. **held-out cohort の bundle の保管場所と作成者**: 公開 repo の外で owner が管理する場所、作成に使う agent と、作成者が見てよい資料の範囲。
5. **cohort の構成比**（欠陥入り : control、推奨 2:1）と、正しい halt を要求する task を含めるか。
6. **ablation の可否**: J の profile に gate のみ / gate + 反例探索の切替を入れるか。入れない場合 ablation は未実施と報告する。
7. **I1 の着手時期**: Issue 884 は E・F・G・H の merge 後を条件にしている。E4 の event schema が確定してから着手するのが推奨。先に着手する場合、段の項目は全件 `unmeasured` とし、E4 後に結合を追加する。
8. **必要な task 数が非現実的な場合の扱い**: pilot の結果 Goal の失敗率が低く n が数百を超えた場合、cohort を大きくするか、確認実験を行わず「未検証」のままとするか。閾値（1/10）を後から変えることは選択肢に含めない。

### 8.2 対象外

- 確認実験・pilot・smoke の自動起動、上限の無い有料実行、10 倍に届くまで実行を続けること。
- 通常 CI への有料モデル実行の追加。
- G・H の判定規則（Goal の観測、evaluator の合否）の変更。I は呼ぶだけ。
- 過去の結果ファイルの書き換え・再採点による置換。
- CC host の lifecycle 観測手段の開発（別 Issue）。
- held-out cohort そのものの作成（独立した作成者が行う。I の PR に含めない）。

### 8.3 主張してはならないこと

- 確認実験で `achieved` を得るまで、「10 倍」「1/10」「品質が N 倍」と書かない。H の 12 件・smoke・pilot・ablation の結果は、どれほど良くても改善の証拠として書かない。
- `not_achieved`・`not_comparable`・`inconclusive_incomplete` を「同等」「有意差なし＝同じ品質」と書かない。
- Mission の `passes`、score、finding の解決数、修復件数を品質の指標として書かない。
- API 相当額を請求額として書かない。定額プランでの実行は金額ではなく rate limit を消費する。
- run の総和時間と経過時間、wall 時間と総実行時間を混同しない。
- 確認結果を、対象 cohort・host・model・予算以外へ一般化しない。

## 9. 事前登録として凍結する項目

確認実験の前に、本書の該当節と追記（pilot 後の n・DEFF・T・`ĉ_vc`・cohort digest・package SHA）を commit し、その commit SHA を report の `inputs` に記録する。

1. 主結果の定義と分母（§3.1）、受入可能の判定器、予算の単位（共通の wall-clock 上限 T）。
2. 再実行規則（turn 開始前の基盤障害のみ、1 回、両 arm 対称、元 record 保持、ρ = 0.1）。
3. 解析単位（task、確認実験では 1 run / task / arm）、区間の方法（片側 Clopper-Pearson ×2、各 0.025、Bonferroni、`RR_upper ≤ 0.1`）、guard（control 割当の失敗率）。
4. 判定値とその報告文（§3.3）。
5. arm の定義と baseline SHA `ed1d1c2723c55c08f06a82d6e397b164323b5b59`、verified-complex の package SHA（J merge 後に凍結）。
6. held-out cohort の作成規則・下限・構成比・digest・使用回数（§5）。
7. n の決め方（§6.3）、停止規則と各段の費用上限（§6.4）。
8. 副次項目の定義と帰属の順序（§4）。

## 固定 head の出典

リンクは全て `d25a66c6abed33a4c0fbd1036e22bdf49da3c606` に固定。各ラベルの `file:line` はこの head で読んだ範囲。

[S01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_result.schema.json#L1-L36 "native_goal_result.schema.json:1-36 — arm/outcome/fidelity の閉じた列挙、verified の manifest 要求"
[S02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L101-L127 "native_goal_benchmark.py:101-127 — preserve_assignment_outcomes（全割当、欠落は not_started、計画外と重複を拒否）"
[S03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L40-L85 "native_goal_benchmark.py:40-85 — observe_codex_goal（同一性・terminal status・tokensUsed）"
[S04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L88-L98 "native_goal_benchmark.py:88-98 — observe_claude_goal は完了を確立しない"
[S05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L151-L232 "run_native_goal_probe.py:151-232 — probe_codex（Mission arm 165-198、budget_enforcement 226-229）"
[S06]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L296-L317 "run_native_goal_probe.py:296-317 — _fresh_mission_state（.mission-state/sessions/cx-<thread>.json）"
[S07]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L33-L37 "run_native_goal_probe.py:33-37 — RpcProcess の単一 deadline"
[S08]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L191-L229 "native_goal_benchmark.py:191-229 — immutable_manifest と create_immutable_package"
[S09]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L336-L428 "run_native_goal_probe.py:336-428 — main（record の欄、arm label 370、candidate の置き場 386-394、failure reason 397-425）"
[S10]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L333-L392 "complex_fixture_benchmark.py:333-392 — evaluate_candidate（fresh process、非 pass の保持）"
[S11]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L401-L434 "complex_fixture_benchmark.py:401-434 — evaluate_assignment（fixture_group=worker 固定 411、雛形照合 419-420）"
[S12]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/generate_complex_fixtures.py#L21-L34 "generate_complex_fixtures.py:21-34 — 12 task と 6 family"
[S13]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/generate_complex_fixtures.py#L306-L313 "generate_complex_fixtures.py:306-313 — task_template の worker/reference/control"
[S14]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L125-L139 "complex_fixture_benchmark.py:125-139 — load_catalog は 12 件を要求"
[S15]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex-fixtures/README.md#L1-L13 "complex-fixtures/README.md:1-13 — 開発用 cohort、10 倍の証拠ではない（3, 12-13）"
[S16]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L453-L483 "complex_fixture_benchmark.py:453-483 — export_worker_fixtures が G の positive allowlist export を呼ぶ"
[S17]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L78-L122 "complex_fixture_benchmark.py:78-122 — materialize_task（固定 commit の生成器から一時 repo）"
[S18]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/AGENTS.md#L83-L126 "AGENTS.md:83-126 — reviewed area の測り方、600/1,400 の閾値"
[R01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/proposal.ja.md#L1-L11 "proposal.ja.md:1-11 — 調査ソース ed1d1c27、効果は仮説"
[R02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/statistical-examples.json#L1-L60 "statistical-examples.json:1-60 — 正確な二項限界 + Bonferroni の例示（実験結果ではない）"
[R03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/PRE_REGISTRATION.md#L96-L98 "docs/PRE_REGISTRATION.md:96-98 — 事前登録した基準の変更の規律"
[R04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L15-L36 "2026-08-21-verdict-tail-v1-summary.json:15-36 — Goal arm cost_usd_mean 0.9477、total 14.2157、records 15"
[R05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L48-L73 "2026-08-21-verdict-tail-v1-summary.json:48-73 — Mission arm budget_blocked 6、cost_usd_mean 5.9447、total 89.1708、records 15"
[R06]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L186-L192 "2026-08-21-verdict-tail-v1-summary.json:186-192 — 制約: budget 打ち切り、API 相当額は請求額ではない"
[R07]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L216-L233 "2026-08-21-verdict-tail-v1-summary.json:216-233 — 30 records、3 反復、tail cohort、starting_commit 068dc405"
[R08]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/proposal.ja.md#L184-L194 "proposal.ja.md:184-194 — pilot の規模、相関、片側上限、公開表現の条件"
[D01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L126-L145 "docs/design/880-repair-lineage.md:126-145 — E の比較履歴と mission-repair-event/1、集計は I（未 merge 実装依存）"
[D02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L53-L53 "docs/design/880-repair-lineage.md:53 — E4 より前は event が無く未測定として扱う要求"
[D03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L222-L226 "docs/design/880-repair-lineage.md:222-226 — orchestrator の決定: I との event 契約と証拠保持"
