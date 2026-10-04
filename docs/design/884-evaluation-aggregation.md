# 全割当から検出・修復・改悪と予算内完遂を集計する設計と事前登録

決定案: 主結果は「予算内に受入可能な成果を残せない率」とし、worker から分離した決定的な外部 evaluator だけで判定する。確認用 cohort は **mission の開発者が作っていない外部の公開 benchmark** から作る（owner 決定 2026-10-04）。**独立単位は upstream の project（code の系譜を共有しない別 repository）** とし、各単位から観測前に 1 task だけを選ぶ。選択は「候補 pool を集める前に commit した seed」と task ごとの key で決め、commit 済みの成果物だけから再現できる。**推定対象は「選んだ K task における、1 run あたり失敗確率の task 平均」** とし、片側 Clopper-Pearson がこの量について run の独立性だけを前提に保守的であることを根拠に判定する（task の母集団への一般化は主張しない。§3.2）。成功基準は verified-complex の上限 ÷ native Goal の下限 ≤ 0.1。各 record は計画した arm の具体的な構成と照合し、照合できない record は仮説に不利な側へ数える。**T に達した run は、凍結した候補を評価する品質の結果**であり、そのために I2a で probe が識別項目をすべての経路（成功・T 到達・例外）で記録するよう直す（§2.3）。段階は smoke → pilot（公開 benchmark の pilot 単位 12 件）→ 検出力計算 → 確認実験の順で、各段の有料実行は owner の承認を要する。H の 12 件は開発・診断専用で、確認判定にも K の計画にも使わない。

対象: [Issue 884: 検出・修復・改悪と予算内完遂を全割当から集計する](https://github.com/tackeyy/mission/issues/884)（親: [Issue 876: 実検証と反例修復で複雑タスクの品質を改善する](https://github.com/tackeyy/mission/issues/876)）。本書は設計と事前登録のみ。実装、テスト追加、benchmark・有料モデルの実行、Issue 起票・本文変更、Git 操作による公開は行っていない。

照合 head: `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`（開始時の worktree HEAD とローカル `origin/main` が一致）。設計レビュー 1 巡目の反映時に、作業 branch の HEAD `aa604792` と `d25a66c6` の間で `benchmarks/`・`reports/`・`docs/PRE_REGISTRATION.md`・`AGENTS.md` に差分が無いこと（`git diff --stat` が空）を確かめ、同じ行番号で引用している。設計レビュー 2 巡目の反映時にも、作業 branch の HEAD `69bfd8d9` と `d25a66c6` の間で同じ範囲の `git diff --stat` が空であることを確かめた。以下の「現状」はこの固定 head のソースを指し、「決定」は追加予定の契約、「凍結」は事前登録として観測前に固定する項目を指す。引用 [S*] / [R*] / [D*] は末尾の出典へ結ぶ。2026-10-04 JST 時点で `gh pr list --state open --limit 100` は 0 件、#884 の `closedByPullRequestsReferences` も 0 件だった（best-effort 照合であり、着手の排他ではない）。

**本書で計算した数値は、実測ではなく事前登録用の見込みである。** 費用は既存 summary の API 相当額からの外挿、検出力は二項モデルの数値計算で、いずれも品質改善の証拠ではない。

## 1. 採用する境界と既存状態

決定: I は「実行」と「判定」と「集計」を分ける。実行は G の adapter、判定は H 形式の外部 evaluator、集計は I が担い、Mission の内部状態（`passes`、score、finding の解決状態）は正解として使わない。E の lineage/event は「どの段で何が起きたか」の帰属にだけ使い、正誤は常に外部 evaluator の結果で決める。

| 照合した現状 | I の選択 |
|---|---|
| G の結果 schema `native-goal-benchmark-result/1` は `arm` を `codex_native_goal/claude_code_native_goal/mission` の 3 値、`outcome` を `completed/failed/blocked/unsupported/not_started`、`fidelity` を `verified/unverified/not_applicable` に閉じ、verified には `config_matches=true` と、package・provider version・conditions・worker export を持つ manifest を要求する。[S01] | I の入力 record として使う。G の `outcome=completed` は host の lifecycle 観測であって品質の合格ではないので、主結果には使わない |
| `preserve_assignment_outcomes` は計画した全割当を出力し、record の無い割当を `not_started`/`missing_assignment_record` で補い、計画外 record と重複を拒否する。計画の照合は `(assignment_id, task_id, arm)` で、arm は record の label と比べる（:117）。[S02] | 全割当の会計の基礎にする。I はこの関数を再実装せず呼ぶ。ただし label は Mission の 2 版を区別しないので、計画した arm 名との照合は I が manifest で別に行う（§2.2） |
| Codex の Goal arm は `thread/goal/set/get` の同一性と terminal status を観測して初めて verified になり、`budgetLimited/usageLimited` は verified blocked。`tokensUsed` を記録する。[S03] | 確認実験の host は Codex に限る（§2）。Goal arm の token 使用量は G の observation から取る |
| Codex の Goal arm は `turn/start` を最大 `max_turns` 回繰り返し、`complete/budgetLimited/usageLimited` で抜ける。上限まで active のままなら `assignment_turn_limit`（verified blocked）になる。`--max-turns` の既定は 2、`--timeout-seconds` の既定は 30 である。[S19][S21] | 既定の 2 turn では Goal arm が T より先に止まりうる。turn 上限は arm の定義として事前登録し、T より先に効かない値にする（§2.1） |
| Codex の Mission arm は skill を 1 回入力して `turn/start` を 1 回だけ送り、その turn の完了を待つ（turn を重ねない）。[S05] | Mission は 1 turn 内の自身のループで止まる。arm の終了条件の違いは arm の定義として事前登録する（§2.1） |
| CC の Goal arm は `/goal` 送信を観測しても lifecycle を読み戻せず、常に `fidelity=unverified`, `outcome=failed`, `goal_lifecycle_unobserved` になる。[S04] | CC host は確認実験から外す。lifecycle を観測できる手段が出るまで unsupported として報告する |
| Codex の Mission arm は固定 package の skill を入力し、`.mission-state/sessions/cx-<thread>.json` の `passes`/`halt_reason` を読む。`budget_enforcement` は `unavailable`。[S05][S06] | Mission の `passes` は「Mission が完了を主張したか」の記録にだけ使う（偽完了、§4）。Mission 側の token 使用量の取得経路は **UNKNOWN**（smoke で探すが、進行の条件にしない。§6.4） |
| 実行構成の照合は `thread/start` の応答から読んだ model・reasoningEffort・activePermissionProfile と要求値の一致（`config_matches`）だけで、turn ごとの読み戻しは無い。Mission arm は不一致なら `execution_config_mismatch` で failed、Goal arm は verified を unverified に落とす。[S05][S19] | record ごとの構成照合に使う（§2.2）。turn ごとに構成が保たれたかは **UNKNOWN** であり、照合の範囲を `thread/start` 時点と明記する |
| `RpcProcess` は起動時刻から一つの deadline を持ち、deadline を過ぎた `request` は `TimeoutError` を送出し、`wait_for_event` は False を返す。[S07][S20] | T の共通単位にする。ただし現状では、Goal arm が T に達すると `wait_for_event` が False を返した直後の `thread/goal/get`（:211）が deadline 後の request として `TimeoutError` を送出し（:74）、例外が `main` へ抜けて record は `adapter_execution_failed` になる。このとき `provider_version`（:405）・`observed_config`・`config_matches`・`package_delivery` が record に残らない（:410-414）。stdout の EOF（app-server の異常終了）でも同じ `TimeoutError` になり、deadline と区別できない（:98-101）。候補の digest はその後も取られる（:415-419）。[S22][S29] I2a で直す（§2.3） |
| record の `arm` は Codex Goal なら `codex_native_goal`、Mission なら版を問わず `mission`。manifest の `conditions` に host・arm・model・effort・permission・T・token 予算・turn 上限が入り、`mission_source_commit` と package の SHA-256 も残る。[S22][S08] | Mission の 2 版は label ではなく `mission_source_commit` と package digest で区別する（§2.2） |
| package は `git archive <commit> plugins/mission skills/mission` から作り、manifest は commit・source tree・package の digest と条件を束縛する。[S08] | baseline と verified-complex の package をこの経路で固定 SHA から作る |
| G の record には USD 費用・run の所要時間・介入回数の欄がない（observation にあるのは Codex Goal の `tokensUsed` のみ）。[S05][S22] | run の wall 時間は runner（I3）が subprocess の前後で測る。費用・介入は集計 schema 側に欄を持ち、取れないものは null と理由で残す。0 で埋めない |
| H の evaluator は候補を fresh な領域へ複製して別 process で実行し、ケースを evaluator 所有の期待値と比較する。候補の自己申告は使わず、0 ケース・不正出力・候補変化は非 pass、timeout は blocked。`root` 引数は本体で参照されず、判定は渡された entry の `checks` と候補だけで決まる。各ケースは、候補の `service.py` を読み込んで `execute(scenario)` を呼ぶ固定 runner で実行される。[S10][S26][S28] | H の割当の判定器はこの関数を使う（判定規則は変えない）。**公開 benchmark の task は「候補の `service.py` の `execute`」という形を持たないので、この関数では評価できない。** I2c が同じ原則（fresh な複製・別 process・evaluator 所有の期待値・評価前後の digest 照合・非 pass の保持）で公開 benchmark 用の evaluator adapter を作る（§5.1） |
| `evaluate_assignment` は固定 commit の生成器と照合し、worker 群（`fixture_group="worker"`）の雛形 digest と一致した割当だけを評価する。[S11] | H の worker 割当（開発・診断）にだけ使う。H の control 割当は確認判定にも guard にも使わないので、評価 adapter は作らない |
| `_fixed_generator` は repo 内の生成器が宣言 commit と一致することを要求し、`materialize_task` は生成器の catalog に無い task を `unknown fixture task` で拒否する。`load_catalog` は 12 件ちょうどを要求し、`export_worker_fixtures` は固定の catalog root 以外を拒否する。[S24][S14][S16] | 公開 benchmark の bundle にはこれらを使わない。H の固定 catalog の検査を流用しない（§5.2） |
| H は 6 family × 2 task の 12 件で、各 task に worker（欠陥入り）・reference・control（正常）がある。[S12][S13][S14] | 開発・診断用。H の README は「開発用 cohort であり 10 倍や一般的な品質の証拠ではない」と明記している。[S15] **確認判定にも K の計画にも使わない**。H は mission の開発者が作った課題で、公開 benchmark の課題と失敗率が同じとみなす根拠が無いため |
| H の export は生成した一時 repo から G の positive allowlist export を呼ぶ。[S16] | G の probe と H の評価を割当単位でつなぐ実装は現状どこにも無い。I が結合する（§7）。結合が実際に通るかは **UNKNOWN**（smoke で確認） |
| E は `mission-repair-event/1` で before/after 候補・receipt・比較可否を供給し、全割当の分母・正誤・不要変更・費用の集計は I が担うと決めている。E4 より前の期間は event が無く、未測定として扱うことを I へ要求している。[D01][D02][D03] | 段の帰属（検出・修復・改悪）は E の event を event ID で重複排除して割当と join する。event が無い割当の d/r/u は「未測定」で、0 とは書かない |

**未 merge 依存**: E（#880 の E1〜E4）、F（#881）、J（#885）は固定 head に存在しない。本書はそれらの設計文書の契約を参照するだけで、実装を前提にしない。E の event schema と J の profile の実体は、I の各 PR の着手時に最新 main で再照合する。

## 2. 比較する arm

凍結: 確認実験の比較は **同じ host（Codex）・同じ Codex version・同じ model ID・同じ effort・同じ permission profile・同じ wall-clock 上限 T**で行う。arm 間で違うのは実行方式・package・下記の終了条件だけとする。

| arm | 中身 | 位置付け |
|---|---|---|
| `native_goal` | Codex native Goal（G の `codex_native_goal`）。Mission package を与えない | 主比較の基準 |
| `mission_baseline` | Mission の固定版 package。候補 SHA `ed1d1c2723c55c08f06a82d6e397b164323b5b59` | 副次（#876 の改善の帰属） |
| `mission_verified_complex` | J（#885）merge 後の verified-complex profile。package SHA は確認実験の事前登録時に凍結 | 主比較の対象 |
| `ablation_gate_only` | 受入条件の検証 gate まで（反例探索・修復なし） | 参考のみ。確認判定に使わない |
| `ablation_gate_fresh_review` | gate と独立した反例探索まで（修復なし） | 参考のみ。確認判定に使わない |

**baseline に `ed1d1c27` を使う根拠（照合済み）**: このコミットは固定 head の祖先で、#876 の最初の実装 PR である [#886 feat(quality): 受入条件の契約を型付き状態へ追加する](https://github.com/tackeyy/mission/pull/886) の merge commit `d83f7e0c` の親そのものである（`git log -1 --format=%P d83f7e0c` が `ed1d1c27…` を返した）。`ed1d1c27..d25a66c6` の 14 commit はすべて #876 系の作業で、最古が #886。初期調査の提案書もこの SHA を調査の起点にしている。[R01] したがって「#876 の作業が入る直前の main」であり、改善の前後比較として公平な固定点になる。

baseline の限界: この版の Mission は verified-complex profile を持たないため、通常の profile で動く。G の adapter が読む state の場所・形式をこの版が満たすかは **UNKNOWN** で、smoke で確認する。満たさない場合も主結果は外部 evaluator で決まるので評価はできるが、偽完了（§4）は `unmeasured` になる。

**ablation の作り方**: J の profile に「gate のみ」「gate + 反例探索」を選ぶ切替が入る場合だけ ablation を実施する。途中の commit（例: C の merge 時点）から package を作る方法は、同時に入った他の変更と区別できないので採らない。J に切替が無い場合、ablation は実施せず「未実施」と報告する（**UNKNOWN**: J の設計で切替を持つかは未確定。§8 の決定事項）。

ablation は「同じ初回成果」から始める診断比較で行う（Issue 884 の「同じ初回成果による診断比較」）。ある割当で得た初回候補を凍結し、それを starter として各 variant に検出・修復だけをさせる。end-to-end 比較とは別 report に出し、母集団を合算しない。

### 2.1 終了条件と turn 上限（凍結）

- **Goal arm**: `thread/goal/set` の後、goal status が `complete`・`budgetLimited`・`usageLimited` のいずれかになるか、T に達するまで turn を重ねる。turn 上限 `M` は「T より先に効かない値」として smoke 後に凍結し（目安は smoke で観測した 1 turn の最短 wall 時間で T を割った値の 10 倍以上）、`--max-turns M` で渡す。token 予算（`tokenBudget`）は渡さない（Mission arm に同等の強制が無いため）。[S19][S21]
- **Mission arm（2 版とも）**: skill を 1 回入力した 1 turn の完了か、T に達するまで。[S05]
- **turn 上限が先に効いた run**（Goal arm の `assignment_turn_limit`）は arm の定義に反した run として §3.1 の「非品質」に分類する。主判定では仮説に不利な側（Goal では成功）に数え、件数を別に報告する。pilot で 1 件でも出たら M を上げて smoke からやり直す。
- 各 arm が自分の停止信号（Goal の `complete`、Mission の `passes`/`halt_reason`）で T より前に止まるのは arm の挙動であり、そのまま扱う。

### 2.2 計画した arm と具体的な構成の対応・record ごとの照合（凍結）

割当計画は各割当に `planned_arm` と、それに対応する **arm 仕様**を持つ。arm 仕様は事前登録の文書に固定し、I1 は record ごとに次を照合する。

| 照合項目 | 期待値 | 出典 |
|---|---|---|
| record の `arm` label | `native_goal` → `codex_native_goal`、Mission 2 版 → `mission` | [S22] |
| `manifest.conditions.host` / `.arm` | `codex` / `goal` または `mission` | [S22] |
| `manifest.conditions` の `model_id`・`effort`・`permissions`・`timeout_seconds`・`max_turns`・`token_budget`・`max_budget_usd` | 事前登録値（`token_budget` と `max_budget_usd` は null、`max_turns` は Goal arm が M） | [S22] |
| `manifest.mission_source_commit` と `manifest.package.sha256` | Mission arm は版ごとの SHA と、事前登録時に同じ機械で `create_immutable_package` から算出した digest。Goal arm は事前登録した固定値（package は作られるが skill として入力されない） | [S08][S22][S05] |
| `package_delivery` | Mission arm は `skill_input`、Goal arm は null | [S05] |
| `config_matches` と `observed_config` | true。`observed_config` が record に無い場合は照合不能 | [S05][S19] |
| `manifest.provider_version` | 事前登録した Codex version と一致 | [S22] |
| `manifest.task_snapshot.matches` と `manifest.worker_export` | true、`initial_sha256` が割当の starter と一致 | [S01][S22] |

- 2 つの Mission 版は label が同じなので、**`mission_source_commit` と package digest の組で planned arm へ対応付ける**。組がどちらの版の仕様とも一致しない record は「構成不一致」とする。
- **照合に一つでも失敗した record（照合項目の欠落を含む）は、評価結果が passed でも成功に数えない。** §3.1 の「非品質」に分類し、主判定では verified-complex 側では失敗、native Goal 側では成功として数える（仮説に不利な側へ倒す）。件数は arm・理由別に必ず報告する。
- `git archive` の出力 bytes が git version を超えて同一かは **UNKNOWN** なので、期待 digest は実行と同じ機械・同じ git で算出し、smoke で全 record の一致を確かめる。
- 上表の項目は、run の結果（成功・T 到達・例外）に関わらず全 record に揃っていなければならない。現状の probe はそうなっていない（T 到達と例外の経路で 4 項目が落ちる）。I2a の変更と、変更が入るまでの扱いは §2.3。
- G の結果 schema は `provider_version` などを `fidelity=verified` の record にだけ要求し（schema :16-17、:24）、`package_delivery`・`observed_config` は定義していない（`additionalProperties: true`）。[S01] したがって schema を通ることは照合項目が揃っていることを意味しない。I1 は上表の項目を schema とは別に、全 record について必須として検査する。

### 2.3 T に達した run と識別項目の記録（決定・I2a）

**決定: T に達することは arm の終了条件の一つであり、品質の結果として扱う。** T に達した run は §3.1 の規則で凍結した候補を評価し、`success` か `quality_failure` に決める。`non_quality` にしない。これを可能にするため、I2a で probe を次のように直す。

**現状の経路ごとの欠落（固定 head で確認）**:

| 照合項目 | 記録する箇所 | 成功経路 | T 到達（Goal） | T 到達（Mission） | 例外経路 |
|---|---|---|---|---|---|
| `arm` label | `main` :370（probe の前） | あり | あり | あり | あり |
| `manifest.conditions`・`mission_source_commit`・`package.sha256`・`task_snapshot` | `main` :378-386（probe の前） | あり | あり | あり | あり（`package_prepare_failed` :421-425 を除く） |
| `worker_export.initial_sha256` | `main` :389-395（probe の前） | あり | あり | あり | あり |
| `worker_export.candidate_sha256` | `main` :416（probe の後、例外でも実行） | あり | あり | あり | あり（digest 不能は `candidate_snapshot_invalid` :417-419） |
| `observed_config`・`config_matches` | probe の返り値 :191、:226-229 | あり | **無い**（:211 の例外で返り値が作られない） | あり（:193） | **無い**（:414） |
| `package_delivery` | probe の返り値 :191、:226-229 | あり | **無い** | あり | **無い**。Mission の skill 未観測の早期 return（:183）にも無い |
| `manifest.provider_version` | `main` :405（probe が正常に返った後だけ） | あり | **無い** | あり | **無い**。`_codex_version` 自体の失敗（:147）は、終わった run を `adapter_execution_failed` に変える |
| deadline と異常終了の区別 | — | — | 無い（EOF も deadline も `TimeoutError` :74、:98-101） | 無い（`wait_for_event` が False :88） | 無い |

[S05][S22][S29][S30][S31]

**I2a の変更（G の判定規則は変えない。記録の追加と例外の型付けに限る）**:

1. **`provider_version` を run の前に取る。** `main` は probe を呼ぶ前に `_codex_version()` を呼んで `manifest.provider_version` に記録し、probe の後にもう一度取って `provider_version_after` に記録する。前で取れなければ turn を始めずに record を書く（`provider_version_unavailable`、turn 開始前の基盤障害）。前後が一致しない record は §2.2 の照合で不一致とする。
2. **識別項目を、確立した時点で probe の外の入れ物へ書く。** `main` が渡す dict に、probe は `observed_config`・`config_matches`（`thread/start` の直後）、`package_delivery`（Mission は skill を観測した直後に `skill_input`、Goal は開始時に null）、`turn_start_sent`（`turn/start` を送るたびに加算。応答を受け取る前に加算する）を書く。例外で probe が抜けても `main` はこの入れ物を record へ写す。返り値の値と入れ物の値が食い違う場合は照合不一致とする。
3. **deadline を型で区別する。** `RpcProcess` は deadline を過ぎたことで request が終わった場合だけ専用の例外（`TimeoutError` の派生型）を送出し、`wait_for_event` が False を返した理由（deadline か EOF か）を記録する。EOF は別の例外とする。probe は deadline の例外を捕まえて、その時点の Goal の観測（最後の `thread/goal/get` があればそれ）と `deadline_reached: true` を返す。`outcome`/`fidelity` は既存の語彙のまま（`failed`/`unverified`）とし、`reason` に `assignment_deadline_reached` を使う。Mission arm の `turn_completion_unobserved`（:193）にも `deadline_reached` を付ける。
4. **候補の digest は従来どおり `main` が app-server の終了後に取る**（:415-419）。T 到達でも例外でも同じ。

**I1 の分類（§3.1 と一致させる）**:

| record の状態 | 分類 |
|---|---|
| 識別項目が揃い照合が通る、かつ arm 自身の停止（Goal の terminal status、Mission の turn 完了）または `deadline_reached: true` | 候補を評価して `success` / `quality_failure` |
| `turn_start_sent == 0` で終わった（host 起動失敗・`provider_version_unavailable`・`thread/start` の失敗など） | turn 開始前の基盤障害。`non_quality`（再実行の対象。§3.1） |
| `turn_start_sent ≥ 1` で、deadline でも arm の停止でもない例外・EOF で終わった | `non_quality`（`adapter_failed_after_turn_start`）。Goal 側では早く止まった run を失敗に数えると仮説に有利になるため、品質の結果にしない |
| 識別項目のいずれかが欠落 | `non_quality`（照合不能） |

I2a が merge されるまでに得た record は、T 到達の Goal record が照合不能になるので、確認実験にも pilot にも使わない（smoke も I2a 後に行う）。

## 3. 主要評価項目と成功基準（事前登録）

### 3.1 主要評価項目

凍結: 各割当の結果を次の 3 値のどれか一つに決める（arm ごと、計画した全割当が分母）。

| 結果 | 条件 |
|---|---|
| `success` | §2.2 の構成照合がすべて通り、凍結した候補（下記）が有効で、外部 evaluator の `status == "passed"`（全ケース pass、ケース数が期待どおり、候補 digest が凍結 envelope と一致）[S10] |
| `quality_failure` | 構成照合が通り、候補が有効で、evaluator が passed 以外を返した。run が arm 自身の停止（`mission_halted`・`goal_budget_limited` などを含む）または T 到達（`assignment_deadline_reached`、`deadline_reached: true` の `turn_completion_unobserved`。§2.3）で終わり、候補を評価した結果が passed でなければここに入る |
| `non_quality` | 閉じた列挙: `not_started`（record 欠落を含む）、`unsupported`、`package_prepare_failed`、`provider_version_unavailable`、`task_snapshot_dirty`/`task_snapshot_mismatch`、`package_skill_unobserved`、構成照合の不一致・欠落（§2.2、§2.3）、`adapter_failed_after_turn_start`（deadline でも arm の停止でもない例外・EOF。§2.3）、`assignment_turn_limit`（§2.1）、`goal_usage_limited`（アカウントの利用上限）、`candidate_snapshot_invalid`、凍結後の候補 digest 不一致、再評価後も残る evaluator の基盤障害（`evaluator_process_unavailable` と、公開 benchmark の評価環境の起動失敗。§5.1） |

- **主結果 F_arm**（主判定に使う値）= 失敗数 ÷ 計画した割当数。失敗数は、verified-complex では `quality_failure + non_quality`、native Goal では `quality_failure` だけとする（Goal の `non_quality` は成功に数える）。どの経路でも仮説（verified-complex が少ない）に有利な方向へ数えない。
- 対称な ITT（両 arm とも `non_quality` を失敗）と、`non_quality` を除いた値を参考として併記する。判定には使わない。
- **予算時点の候補（単一の規則）**: 候補は、run が終わった時点（arm 自身の停止か T のうち早い方）で probe が app-server を終了させた後に digest を取った worker の木とする（:415-419 の `candidate_sha256`）。[S22] I はこの digest と、評価直前に取った digest が一致した場合だけ評価する（H は `freeze_candidate`、公開 benchmark は I2c の adapter が同じ照合を行う）。[S27] 結果は上表の規則だけで決め、run の `outcome`（T 到達・blocked を含む）は、`non_quality` の列挙に当たらない限り結果を変えない。途中成果でも passed なら `success`、完了を主張していても passed でなければ `quality_failure` である。
- Mission の `passes`、score、finding の解決状態、Goal の `complete` status は結果に使わない（偽完了の集計にだけ使う。§4）。
- 正しい halt が要求される課題では、その halt を受入条件として evaluator が採点する（Issue 884 本文）。確認用 cohort の作成時に該当課題を明示する。
- evaluator 自身の基盤障害（`evaluator_process_unavailable` など、候補に起因しないもの）だけは、凍結した同じ候補を 1 回だけ再評価してよい。evaluator は決定的なので worker を再実行しない。元の評価記録は残す。

**worker の再実行規則（凍結）**: 再実行してよいのは、turn 開始前の基盤障害（`turn_start_sent == 0` で終わった割当。host の起動失敗、app-server の接続不能、`provider_version_unavailable`。§2.3）だけ。両 arm に同じ規則を適用し、1 割当につき 1 回まで、元の record は attempt として保持する（再実行は別の attempt ID で記録し、計画との照合には最後の attempt を使う）。**再実行の総数は各段・各 arm で計画割当数の 10%（切り上げ）を上限とする。** 上限に達した後の基盤障害は再実行せず `non_quality` のまま残す。主解析は再実行後の結果を使い、元の結果を `non_quality` として数える感度解析を併記する。turn が始まった後の失敗は再実行しない。

### 3.2 解析単位・推定対象・成功基準・区間の方法

凍結:

- **独立単位は upstream の project** とする。単位は「code の系譜を共有しない repository の集まり」で、fork・mirror・vendoring で系譜がつながる repository は一つの単位にまとめる（判定の機械検査は §5.1 の G1〜G3）。各単位から観測前に 1 task だけを主 task として選び、同じ単位から 2 件以上を主解析に入れない。初期調査の提案も「主解析は独立 task-family 単位」としている。[R08]
- **主 task の選択**は §5.0 の手順で、候補 pool を集める前に commit した seed と、task ごとの key `SHA-256(seed ‖ "primary" ‖ unit_id ‖ task_id)` で決める。単位の中で key が最小の task を主 task とし、key が同じ場合は `task_id` の UTF-8 bytes の辞書順で小さい方を採る（task_id は pool 内で一意であることを pool の凍結時に検査する）。選択は commit 済みの seed と pool manifest だけから再計算でき、判定の前に再計算して commit 済みの選択一覧と一致することを確かめる（§3.2.1 の検査 3）。
- 確認実験では各 (task, arm) を **1 回だけ**実行する。同じ task の反復は独立な観測として数えない。
- **推定対象（estimand）**: arm ごとに、選んだ K 件の主 task i について、その arm の 1 run が §3.1 の意味で失敗する確率を `p_arm,i` とし（確率は model の sampling・実行環境の揺らぎなど run ごとの偶然について取る）、`p̄_arm = (1/K) Σ_i p_arm,i` を対象とする。**K 件の task を固定した条件付きの量であり、task の母集団（benchmark 全体・他の課題）の失敗率ではない。**
- 片側の総有意水準 0.05 を、2 つの片側 Clopper-Pearson 限界へ Bonferroni で分ける（単一 host なので各 0.025）。
  - `U_vc` = verified-complex の主 task の失敗数 x_vc / K に対する片側 97.5% 上限
  - `L_goal` = native Goal の主 task の失敗数 x_goal / K に対する片側 97.5% 下限
  - `RR_upper = U_vc / L_goal`
- **成功（`achieved`）**: `RR_upper ≤ 0.1`、かつ下記の guard と §3.2.1 の独立性検査をすべて満たす。
- guard: 正常な control から始める割当（§4、§5.1）で、verified-complex の失敗率の点推定が native Goal 以下であること。満たさない場合、主結果の判定に関わらず `achieved` にせず、`achieved_with_regression_guard_failed` として両方の数値を報告する。
- 2 host で行う場合は各限界を 0.0125 とする（初期調査の例示計算と同じ配分）。[R02] host ごとに判定し、合算しない。

**この方法が主張する水準で有効である理由**:

1. **前提（A1）は run の独立性だけである。** 選んだ K task を固定したとき、異なる task の run の偶然が互いに独立であれば、arm ごとの失敗数 X は確率 `p_arm,1..K` の独立な Bernoulli の和（Poisson 二項分布）になる。task 同士が同じ失敗原因（同じ言語・同じ build 系・同じ model の苦手）を共有して `p_arm,i` が似ていても、それは各 `p_arm,i` の値に入るだけで、X の分布の形（独立和）を壊さない。
2. **Poisson 二項分布に対して、片側 Clopper-Pearson は `p̄` について保守的である。** Hoeffding（1956, "On the distribution of the number of successes in independent trials", Ann. Math. Statist. 27(3)）の定理は、平均が同じ二項分布 Bin(K, p̄) と比べて、独立 Bernoulli の和の下側 tail が `c ≤ Kp̄ − 1` の範囲で、上側 tail が `c ≥ Kp̄ + 1` の範囲で重くならないことを示す（**原文は本書では照合していない。文献依拠**）。片側 97.5% 限界が p̄ を外す事象は「X がこの範囲の端にあるとき」に限られるので、外す確率は 0.025 以下になる。本書作成時の数値確認（証明ではない）: K = 20・50・100・180・275・560 と p̄ = 1/400 刻みの全点で、外す事象が上記の範囲に入ることを確かめた（範囲外 0 件）。また K = 50・180 で不均一な `p_arm,i` を無作為に 300 組作り、外す確率の厳密値を計算した最大値は上限側 0.0123、下限側 0.0113 で、いずれも 0.025 以下だった。I1 はこの 2 つの検査を契約テストとして持つ。
3. **比への変換は Bonferroni だけを使う。** `U_vc ≥ p̄_vc` と `L_goal ≤ p̄_goal` は同時に確率 0.95 以上で成り立つ（2 arm 間の独立は要らない）。このとき `p̄_vc / p̄_goal ≤ RR_upper` である。

**残る相関と、それを主張の側で扱うこと（凍結）**:

- **task 間の相関（共通の失敗原因・benchmark の作り方・作成者の作風）は、条件付きの推定対象の妥当性を壊さないが、一般化を制限する。** 同じ benchmark の task は、同じ収集手順・同じ言語圏・共通の依存 library・似た issue の書き方を共有しうる。これらを機械で除くことはできない（**UNKNOWN**）。したがって主張は「選んだ K 件の task について」に限り、benchmark 全体・他の課題・実利用への一般化をしない（§3.3、§8.3）。単位を別 project に限るのは、名目の K を近い重複で水増しせず、主張の対象を「K 個の別 project の課題」として正しく書けるようにするためである。
- **run 間の相関（A1 の違反）は、妥当性そのものを壊す。** 例えば provider 側の一時的な品質低下・障害が、近い時刻に走った複数の run をまとめて失敗させる場合である。構造的に次で抑える: 各 run は別の worktree・別の app-server process・別の thread で始め、run 間で状態を持ち越さない（§3.2.1 の検査 4）。全割当の実行順を seed から決めた無作為な順に固定し、arm を交互に混ぜる（§7.3）。これで時間で偏った障害は両 arm に同じ確率でかかる。**provider 側の時間変動を完全に除くことはできない（UNKNOWN）**ので、`achieved` の報告文は「run が互いに独立という前提の下で」を必ず含める（§3.3）。実行順に沿った失敗の偏りは診断として報告する（判定は変えない）。

**反例での確認**: 6 project × 30 task で project 内の結果が全く同じ場合（Goal 60/180、Mission 0/180）、180 task を独立単位として数えると `RR_upper ≈ 0.077` で `achieved` になってしまい、「180 個の別 project の課題で」という主張が成り立たない。本書の方法では 1 project 1 task なので K = 6、x_goal = 2、x_vc = 0 となり、`U_vc ≈ 0.459`、`L_goal ≈ 0.043`、`RR_upper ≈ 10.6` で `not_achieved` になる（本書作成時の数値計算）。

#### 3.2.1 独立性の検査（凍結。どれか一つでも通らなければ `achieved` にしない）

判定の前に次をすべて機械で確かめ、一つでも通らなければ判定値を `invalid_cohort`（§3.3）とする。検査 4・5 は I1 が record から行う。検査 1〜3 は I2c の関数で再計算した結果を I1 が入力として要求し、入力が無ければ `invalid_cohort` にする（I2c の merge 前は常にこうなる。§7.4）。`achieved` へ進む経路は、これらがすべて通った場合だけである。

1. **単位の一意性**: 主 task の `unit_id` がすべて異なる。
2. **系譜の検査の記録**: commit 済みの pool manifest に、全単位の組について §5.1 の G1〜G3 の検査結果が「系譜の共有なし」として記録され、その記録の digest が事前登録の文書と一致する。
3. **選択の再現**: commit 済みの seed（§5.0 の手順で得た値）と pool manifest から、主 task・control・pilot・実行順を再計算し、commit 済みの一覧と完全に一致する。
4. **run の分離**: 主解析に入る record の `run_id`・worker export の場所・thread ID が arm をまたいで重複しない。同じ割当の attempt が複数ある場合、主解析に入るのは §3.1 の規則で決まる 1 件だけである。
5. **実行順**: record の開始時刻の順が、commit 済みの実行順と一致する（再実行は元の位置の直後に置いたものとして扱う）。

**task を単位とする集計は参考に限る**: pilot・H・副次の task 単位の集計は出してよいが、判定値・`claims_allowed` の計算には一切使わない（§7.1）。

### 3.3 「未達」の報告の仕方（凍結）

| 判定 | 条件 | 報告する文 |
|---|---|---|
| `achieved` | 全割当を解析し、§3.2.1 の検査がすべて通り、主 task K 件で `RR_upper ≤ 0.1` と guard を満たす | 「公開 benchmark〈名称・版〉から事前登録の手順で選んだ K 個の別 project の課題について、run が互いに独立という前提の下で、予算内に受入可能な成果を残せない 1 run あたりの確率の課題平均は、標準 Goal の 1/10 以下だった（`RR_upper`、片側 95%）」。host・model・予算を必ず併記し、他の課題・benchmark 全体へ一般化しない |
| `not_achieved` | 全割当を解析し、`RR_upper > 0.1` | 「標準 Goal の 1/10 以下は示されなかった」。観測比 `(x_vc/K)/(x_goal/K)`、`U_vc`、`L_goal`、`RR_upper`、x と K、`non_quality` の件数を全部出す。改善量と失敗例も公開する |
| `not_comparable` | `x_goal = 0`（`L_goal = 0`） | 「標準 Goal が失敗しなかったため比を定義できない」。cohort が弁別しなかったことを記録する |
| `inconclusive_incomplete` | 実行量の上限・基盤障害の停止規則で計画の全割当を実行できなかった | 実行できなかった割当を `non_quality` として §3.1 の規則で数えた数値を出し、「判定不能」と書く。途中結果で `achieved` を宣言しない |
| `invalid_cohort` | §3.2.1 の検査のどれかが通らない | 「独立性の前提を確かめられなかったため判定しない」。通らなかった検査と数値を出す。数値がどうであれ `achieved` の文を使わない |
| `achieved_with_regression_guard_failed` | `RR_upper ≤ 0.1` だが guard 不成立 | 主結果と改悪の両方を出し、「1/10 以下」の表現を単独で使わない |

判定の優先順は `invalid_cohort` → `inconclusive_incomplete` → `not_comparable` → `achieved_with_regression_guard_failed` / `achieved` / `not_achieved` とし、上から最初に当たるものを採る。

別の指標・別の閾値・別の部分集合（task 単位の集計を含む）へ判定を移すことはしない。判定基準の変更は、観測前に限り、理由と日付を事前登録の文書へ記録して行う。観測後の変更は事前登録の無効化として扱い、report に明記する（既存の事前登録の規律と同じ）。[R03]

## 4. 副次評価項目

すべて分子・分母・null の理由を持つ。分母 0 は `null`（理由 `zero_denominator`）とし、0 や 1 で埋めない。副次項目は確認 cohort の全割当（control を含む）で出してよいが、判定には使わない。内訳は、確認 cohort では benchmark が持つ分類（言語・repository など。I2b の調査で決める）別、H では family 別に併記する。

| 項目 | 定義（外部 evaluator の判定で数える） | 取れない場合 |
|---|---|---|
| 段の候補 | S0 = 初回実装の候補、S2 = 修復後の候補、S3 = 最終候補（§3.1 の予算時点の候補）。E の event の before/after candidate ref から復元し、各段を外部 evaluator で評価する | event が無い・候補 ref が absent なら、その割当の段は `unmeasured` |
| d（検出率） | S0 が不合格の割当のうち、最終より前に Mission が「同じ割当について失敗を観測した finding」（E の `reverification-observed` failed または D の finding 導入）を持つ割当の割合 | 分母 0 → null。native Goal は段が観測できないので `not_applicable` |
| 誤検出 | S0 が合格の割当のうち、finding を導入した割合（報告のみ） | 同上 |
| r（修復率） | S0 不合格かつ検出した割当のうち、S3 が合格になった割合 | 同上 |
| u（改悪率） | S0 が合格の割当のうち、S3 が不合格になった割合。加えて control から始めた割当（正常な starter）で S3 が不合格になった割合を別に出す | control の割当は native Goal にも適用でき、u の arm 間比較はこちらを使う |
| **偽完了** | arm が完了を主張した割当（Mission: 観測した state の `passes == true`。Goal: verified の `goal_status == "complete"`）のうち、S3 が `success` でない割当。分子を (a) 完了を主張した割合で割った率と、(b) 計画した全割当で割った率の両方で出す | Mission の state が観測できない割当（`mission_state_unobserved` など）は `unmeasured` として件数を出し、分母から除いたことを明記する。段の帰属とは独立に数える |
| 全割当の完遂 | `success` の割合を arm・上記の分類別に | — |
| 未完了の理由 | `quality_failure` と `non_quality` を run の `reason` と evaluator の `reason` の組で件数化 | 理由が無い record は `reason_missing` として数える |
| 実行量 | run の wall 時間（runner が測定。必須）、Goal arm の `tokensUsed`、Mission arm の token 使用量（取得経路は **UNKNOWN**）。再実行・全 child を含む | 取れない値は null と理由 |
| 費用 | API 相当額の見積り（§6.1 の式で wall 時間ではなく割当数から外挿したもの）と、実際の請求額を別欄に持つ | Codex の token→USD 換算は **UNKNOWN**。請求額は定額プランでは金額ではなくプランの rate limit 消費であり、金額としては **UNKNOWN**。いずれも null と理由 |
| 時間 | run ごとの wall 時間、run の総和（総実行時間）、段階全体の経過時間を別欄に | 並列実行の経過時間を run の総和と混同しない |
| 介入 | 人の介入回数。harness が無人で回したことを記録できた場合だけ 0 | 記録できなければ **UNKNOWN** |
| 分類別 | 主結果と全副次項目を上記の分類別に | 部分集団での主張はしない |

段の帰属（失敗した割当を一つの分類へ）: `non_quality` → `budget`（T で止まり S3 不合格）→ `detect`（S0 不合格・未検出）→ `repair`（検出・未修復）→ `regression`（S0 合格→S3 不合格）→ `unmeasured`（段が観測できない）。上から順に最初に当たるものに帰属させ、規則を report に載せる。**偽完了は帰属の分類ではなく、上表の独立した旗として全割当に付け、帰属の結果に関わらず集計する。**

## 5. 確認用 held-out cohort（公開 benchmark から作る）

**owner 決定（2026-10-04）**: 確認用 cohort は、mission の開発者が作っていない、互いに独立な task を多数持つ外部の公開 benchmark から作る。自作の family は使わない。使う benchmark・license・複雑タスクへの適合は I2b で調べて owner へ報告し、owner が選ぶ。以下はどの benchmark を選んでも適用する選定基準と手順である。候補の benchmark の規模・license・評価環境は本書では確かめていない（**UNKNOWN**）。

### 5.0 選定の手順と順序（凍結）

順序を守らなかった場合、その選択は無効とし、新しい seed で手順 1 からやり直す（やり直した事実と理由を事前登録の文書に記録する）。

1. **commit A（pool を集める前）**: 本節の基準と閾値、seed の取り方、key の式、verified-complex と baseline の package SHA と digest を事前登録の文書に commit する。seed は次のどちらかで、owner が選ぶ（§8.1）。
   - (a) **公開 randomness beacon**: 公開予定時刻が commit B の予定より後の round 番号 R を commit A に書き、seed = round R の値とする。beacon の候補（例: drand）の利用可否・取得と検証の方法は **UNKNOWN**（I2b で調べる）。
   - (b) **commit-reveal**: owner が 32 bytes の乱数 r を作り、`SHA-256(r)` だけを commit A に書く。r は commit B の後に公開し、seed = r とする。I1 は `SHA-256(r)` と照合する。
2. **pool を集める**: benchmark の全 task に下記の基準（Lic・Det・Cx・Con）を機械で当て、通った task を系譜の検査（G1〜G3）で単位にまとめる。評価環境での検査の実行（Det）は evaluator だけを動かし、worker（model）は動かさない。
3. **commit B（seed が分かる前）**: pool manifest（全 task の `task_id`・`unit_id`・基準ごとの判定と証拠の digest・系譜の検査結果）を正規化 JSON にし、その digest を commit する。commit B の時刻が seed の公開（(a) round R の公開時刻、(b) r の公開）より前であることを記録する。後になった場合は手順 1 からやり直す。
4. **seed の確定と選択（commit C）**: seed から次を決め、一覧を commit する。key は `SHA-256(seed ‖ 用途 ‖ 識別子…)` とし、`‖` は各要素を「4 bytes big-endian の長さ + UTF-8 bytes」で連結する（区切りとの衝突を避けるため）。
   - **pilot 単位**: `SHA-256(seed ‖ "pilot" ‖ unit_id)` が小さい 12 単位。確認には使わない。
   - **確認の単位の順位**: 残りの単位を `SHA-256(seed ‖ "unit" ‖ unit_id)` の昇順に並べる。確認で使うのは常にこの順位の先頭 K 単位で、K は §6.3 で後から決める（順位を先に commit するので、K の選び方で単位を選べない）。
   - **主 task**: 各単位の中で `SHA-256(seed ‖ "primary" ‖ unit_id ‖ task_id)` が最小の task（§3.2）。
   - **control**: 確認の先頭 K 単位のうち `SHA-256(seed ‖ "control" ‖ unit_id)` が小さい ⌈K/2⌉ 単位（K 確定後に計算して commit する）。
   - **実行順**: `SHA-256(seed ‖ "order" ‖ assignment_id)` の昇順（§7.3）。
   - key が同じ場合は、識別子（`unit_id`、`task_id`、`assignment_id`）の UTF-8 bytes の辞書順で小さい方を先にする。識別子は pool 内で一意であることを commit B の前に検査する。
5. **commit C より前に、pool のどの task でも worker を動かさない。** smoke と開発の worker run は H だけで行う（smoke で pilot 単位を使うのは commit C の後）。
6. **package を動かさない**: commit A の後に verified-complex または baseline の package を変えた場合、選択は無効になる。新しい seed で手順 1 からやり直し、すでに worker の結果を見た task（pilot・smoke）は新しい pool から除く。

### 5.1 選定基準（凍結。閾値は commit A の前に owner が確定する）

| 基準 | 内容 | 判定の方法 |
|---|---|---|
| **Lic（license）** | benchmark のデータの license と、各 upstream repository の license の両方が、複製・実行と、bundle に内容を含める場合はその保存を許す | benchmark の license は I2b が調べて報告する（**UNKNOWN**）。repository の license を機械で特定できないもの・許可が確認できないものは除く |
| **Det（決定的な外部評価）** | task ごとに evaluator 所有の検査（修正前に失敗し修正後に通るべき検査と、前後とも通るべき検査）と、固定された評価環境（container image の digest など）がある | commit B の前に、評価環境で starter と reference をそれぞれ 3 回評価し、starter は「失敗すべき検査」を 1 件以上失敗して「通るべき検査」を全部通し、reference は全部通し、検査ごとの結果が 3 回とも同じ task だけを残す。評価は network を遮断して行う。遮断できない benchmark の扱いは I2b の報告を受けて owner が決める（**UNKNOWN**） |
| **Cx（複雑さ）** | #876 の「複雑タスク」に当たる | 下記 Cx1〜Cx4 をすべて満たす |
| **Con（汚染）** | 課題と解答が実装の文脈に入っていない | 下記 Con1〜Con2 |

**Cx の操作的定義**: #876 の本文に「複雑タスク」の操作的な定義は無い（2026-10-04 JST に `gh issue view 876` の本文で確認）。初期調査の提案は、開発用の複雑タスクの対象を「別プロジェクトの複数モジュール変更、互換性、再実行、集計整合等」とし、Mission 自身を直す課題に偏らせないとしている。[R09] 本書はこれを機械で判定できる形に置き換える。

- Cx1: reference の変更が、test 以外の source file 2 件以上にわたる（test の判定: path に `test`・`tests`・`spec` のディレクトリを含む、または file 名が `test_*`・`*_test.*`・`*.test.*`・`*.spec.*`）。「複数モジュール変更」に対応する。
- Cx2: 評価の検査が 3 件以上（H の「外部評価 3 ケース以上」と同じ下限）で、そのうち「修正前に失敗し修正後に通る」検査が 1 件以上。H の下限は [Issue 883: 改善と改悪を判別できる複雑タスク12件を追加する](https://github.com/tackeyy/mission/issues/883) の本文（「外部評価3ケース」「36ケース」）による。
- Cx3: reference の test 以外の変更行（追加 + 削除）が 10 行以上（推奨値。owner が確定）。
- Cx4: 課題文が upstream の実際の issue など自然な依頼に由来し、Mission や特定の agent を狙って作られていない（benchmark の作り方の記述で判定し、I2b が報告する）。
- 「互換性・再実行・集計整合」などの種類は機械で判定できないので基準にしない。種類別の主張もしない。

**Con（汚染）の検査**: 対象範囲は、commit A の時点の mission repository の全 tracked file と、verified-complex・baseline の package の中身。

- Con1: pool の `task_id` と upstream の repository 名（`owner/repo` の形）が、対象範囲に文字列として現れない。
- Con2: 各 reference の test 以外の追加行（空白を正規化し 30 文字以上のもの）が、対象範囲に連続 3 行以上一致して現れない。
- 当たった task は pool から除き、件数を記録する。
- **model の学習データへの混入は確かめられない（UNKNOWN）。** 両 arm は同じ model を使うので arm 間の比較は同じ条件になるが、失敗率の絶対値と一般化には影響しうる。主張に含めない。

**系譜の検査（単位を作る。迷う場合はまとめる側＝単位を減らす側へ倒す）**:

- G1: 正規化した repository の識別子（host・owner・repo を小文字にしたもの）が同じ task は同じ単位。
- G2: 2 つの repository の base commit から辿れる root commit が一つでも共通なら同じ単位（fork・履歴の取り込み）。
- G3: 2 つの base tree で、空白を正規化した 4 行以上の file の SHA-256 の集合を比べ、小さい方の集合の 20% 以上が共通なら同じ単位（vendoring・コピー）。
- まとめた単位の `unit_id` は、含まれる識別子のうち辞書順で最小のもの。全単位の組を比べる計算量と clone の容量は **UNKNOWN**（I2b で見積もる）。

**その他の凍結事項**:

1. **規模**: pool の単位数が「pilot 12 + §6.3 で必要な K」に足りない場合、確認実験に進まず owner へ上げる。**1 project 1 task の制約の下で約 180〜560 の単位を持つ公開 benchmark があるかは UNKNOWN** で、I2b の主要な調査項目である。
2. **control**: 選んだ ⌈K/2⌉ 単位の主 task について、reference を適用済みの状態を starter とし、同じ課題文・同じ検査で評価する割当を置く（欠陥入り : control = 2:1。§8.1）。壊さずに終われば合格。reference 適用後の状態を starter にできるかは benchmark によるので **UNKNOWN**（I2b が確認）。できない場合は guard の作り方を owner へ上げる。
3. **halt**: reference の修正を持つ task だけを対象にするので、正しい halt を正解とする task は含まれない。
4. **evaluator の分離**: 検査・reference・評価環境の定義は worker の export にも package にも含めない。evaluator は worker と別の process・別の場所で実行する。
5. **保管**: pool manifest・選択一覧・evaluator 用データは digest を commit する。中身を公開 repo に置けるかは license による（Lic。§8.1）。
6. **使用回数**: 確認実験で一度だけ使う。確認実験の後に cohort の task を開発に使った場合、それ以降の結果は確認結果として扱わない。

### 5.2 公開 benchmark の bundle と evaluator（決定・I2c）

H の入口は repo 内の固定生成器と 12 件の固定 catalog を前提にし、H の evaluator は候補の `service.py` の `execute` を呼ぶ形に限られる。[S24][S14][S16][S28] I2c は公開 benchmark 用に別の入口と evaluator adapter を持ち、H の検査も判定規則も変えない。

- **bundle はデータだけで構成する**: 閉じた schema の pool manifest と、task ごとの `task_id`・`unit_id`・upstream の識別子・base commit・評価環境の識別子・課題文・検査の一覧・license・基準ごとの判定と証拠の digest。reference と test 用の差分は evaluator だけが読む場所に置く。benchmark 由来の code は harness の process で実行せず、評価環境の中だけで実行する。
- **digest を最初に検証する**: 読み込みの最初に、bundle 全体を正規化した tar の SHA-256 を計算し、事前登録の文書にある digest と一致しなければ何も読まずに拒否する。一致後に閉じた schema で検証し、未知の key・重複 ID・空の検査一覧を拒否する。
- **割当の束縛**: H の `generator_digest`/`catalog_digest` の代わりに `bundle_digest` と task の starter digest を割当と候補 envelope に持たせる。starter（base commit の upstream repository）を一時 repo へ書き出し、G の `create_worker_export` で worker 用の export を作る。評価前に worker の初期 digest（`worker_export.initial_sha256`）が bundle の starter と一致することを照合する。[S22] G の positive allowlist（repo 相対 path の列挙、`allowlist_count ≥ 1`）で upstream repository 全体を過不足なく export できるかは **UNKNOWN**（I2c が確認）。[S01]
- **evaluator adapter**: 候補を fresh な領域へ複製し、test 用の差分を evaluator 側で適用し、固定の評価環境で network を遮断して検査を実行する。評価の前後で候補の digest を照合し、検査ごとの結果を残し、全検査が通り検査の件数が manifest と一致した場合だけ `passed` とする。検査 0 件・不正な出力・候補の変化は非 pass、timeout は blocked、評価環境の起動失敗は evaluator の基盤障害（1 回だけ再評価。§3.1）。出力の語彙は H の `evaluate_candidate` と同じにする。[S10]
- **H の 12 件**は従来どおり `materialize_task`・`evaluate_assignment` を使う。公開 benchmark と H の割当は report の `report_kind` で分け、同じ母集団に入れない。

## 6. 段階的実行・実行量の上限・停止規則

### 6.1 実行量と費用の見積り

**強制する上限は実行量で表す。** Codex の Mission arm には run ごとの費用の強制が無く（`budget_enforcement=unavailable`）、Codex の USD 換算も UNKNOWN なので、累計 USD を run ごとに測って止めることはできない。[S05] 代わりに次の 2 つを事前登録の上限とし、runner が各 run の後に確かめる。

- **run 数の上限** `R_stage = Σ_arm N_arm + 再実行上限（各 arm で ⌈0.1 × N_arm⌉）`。`N_arm` はその段で計画した割当数。
- **wall 時間の総和の上限** `H_stage = R_stage × (T + g)`。g は package 準備・終了処理の余裕で、smoke の実測から T とともに凍結する。各 run は deadline で T に縛られるので、この上限は run 数から決まる。

USD は owner の判断材料としての見積りで、上限の判定には使わない。

```
C_stage（見積り） = Σ_arm N_arm × ĉ_arm × (1 + ρ)
```

- `ρ = 0.1` は §3.1 の再実行上限そのもの（上限なので見積りの最大側）。evaluator は決定的なローカル実行なので費用に含めない。
- `ĉ_goal = 0.9477`、`ĉ_baseline = 5.9447`（USD、API 相当額の 1 run 平均）。出典は保存 summary の `cost_usd_mean`（Goal 15 records 合計 14.2157、Mission 15 records 合計 89.1708）。[R04][R05]
- **この値の限界**: CC host・`tail` cohort（5 task × 3 反復）・開始 commit `068dc405` の値で、Codex host・複雑 task の値ではない。Mission は 15 件中 6 件が `max_budget_usd` で打ち切られており、`ĉ_baseline` は打ち切り込みの値（打ち切りの無い費用はこれ以上でありうる）。summary 自身が「runtime が報告する API 相当の推定で請求額ではない」と書いている。[R06][R07]
- **`ĉ_vc`（verified-complex）は UNKNOWN。** Codex での費用の測定経路が smoke で見つからなければ、確認実験まで UNKNOWN のままである。以下では仮定として baseline の 1 倍・2 倍・3 倍を並べるが、これは見積りの幅を示すための仮定であって予測ではない。

### 6.2 段ごとの計画と見積り（USD、API 相当、ρ を含まない値。ρ=0.1 の上限は ×1.1）

確認実験の割当は arm ごとに「K 件の主 task + ⌈K/2⌉ 件の control」。

| 段 | 割当 | Goal | baseline | verified-complex（仮定 1× / 2× / 3×） | 合計（1× / 2× / 3×） |
|---|---|---:|---:|---:|---:|
| smoke | 2 task（H 1 件 + pilot 単位 1 件）× 3 arm × 1 | 1.90 | 11.89 | 11.89 / 23.78 / 35.67 | 25.67 / 37.56 / 49.45 |
| pilot | pilot 単位 12 件（各 1 task）× 2 反復 × 3 arm | 22.74 | 142.67 | 142.67 / 285.35 / 428.02 | 308.09 / 450.76 / 593.44 |
| 確認（K=180） | 270 task × 2 arm × 1（baseline は任意） | 255.88 | （任意 1,605.07） | 1,605.07 / 3,210.14 / 4,815.21 | 1,860.95 / 3,466.02 / 5,071.09（baseline 除く） |
| 確認（K=275） | 413 task × 2 arm × 1 | 391.40 | — | 2,455.16 / 4,910.32 / 7,365.48 | 2,846.56 / 5,301.72 / 7,756.88 |
| 確認（K=560） | 840 task × 2 arm × 1 | 796.07 | — | 4,993.55 / 9,987.10 / 14,980.64 | 5,789.62 / 10,783.16 / 15,776.71 |
| 確認（K=620） | 930 task × 2 arm × 1 | 881.36 | — | 5,528.57 / 11,057.14 / 16,585.71 | 6,409.93 / 11,938.50 / 17,467.07 |

確認実験の run 数の上限 `R` は、K=180 で 2 arm × (270 + 27) = 594、K=560 で 2 × (840 + 84) = 1,848 になる（baseline を含めない場合）。

smoke の目的は harness の検証、構成照合の確認、T・M・g を決めるための wall 時間の実測で、smoke の結果は品質の主張に使わない。pilot の目的は、公開 benchmark の pilot 単位（§5.0 で確認から除いた 12 単位）での native Goal 失敗率と `non_quality` の率、公開 benchmark の evaluator（§5.2）が通ること、turn 上限が効かないことの確認で、品質の主張に使わない。pilot は H を使わない（H は mission の開発者が作った課題で、公開 benchmark の失敗率の計画値にならないため）。smoke と pilot は I2a・I2c の merge 後に行う。

### 6.3 検出力計算と確認実験の K（凍結した手順）

pilot 後、確認実験を始める前に、次の手順で K を決めて事前登録の文書に commit する。

1. 計画用の Goal 失敗率: pilot の各単位の Goal 失敗割合（1 task × 2 反復の平均）を 12 単位で平均した点推定 `p̂_goal` と、その半分 `p̂_goal / 2`（悲観側）の 2 値を使う。12 単位しか無いので、単位ごとの信頼限界は計画に使えるほど狭くならない。
2. verified-complex の真の失敗率を 0 と 0.01 の 2 通りで仮定する。
3. §3.2 の判定方法で、検出力 0.8 を満たす最小の K（単位数）を二項分布の厳密計算で求める（全 task の失敗確率が等しいと置いた計画用の近似。task ごとに確率が違う場合の検出力はこれと異なりうる）。確認実験の K は owner が費用とあわせて選ぶが、`p̂_goal`・`p_vc = 0` の値を下回る K は選べない（選んだ後は変えない）。
4. K × §6.1 の実行量・見積りが承認上限を超える、または K が pool の単位数から pilot の 12 を引いた数を超える場合は、確認実験に進まず owner へ上げる。

本書の作成時に同じ方法で計算した見込み（task ごとの失敗確率が等しい二項モデル、片側 0.025 ×2、検出力 ≥0.8。実測ではない）:

| Goal 失敗率（仮定） | 目標（1/10） | verified-complex の真の率（仮定） | 必要な単位数 K |
|---|---|---|---|
| 30% | ≤3% | 0 | 約 180（170 で検出力 0.72、180 で 0.86） |
| 30% | ≤3% | 1% | 約 620（600 で 0.80、625 で 0.82） |
| 20% | ≤2% | 0 | 約 275（270 で 0.80、280 で 0.87） |
| 10% | ≤1% | 0 | 約 560（540 で 0.74、560 で 0.82） |
| 10% | ≤1% | 0.5% | 2,000 を超える（2,000 で 0.44） |

**1 project 1 task にすると、必要な独立単位の数はそのまま別 project の数になる。** Goal の失敗率が 30% でも約 180、10% なら約 560 の別 project の課題と、上表で数千〜1 万 USD 相当の実行が要る。この数の別 project を持ち、§5.1 の基準を満たす公開 benchmark があるかは **UNKNOWN**（I2b の主要な調査項目）で、無い場合は確認実験に進まず owner へ上げる（§8.1）。参考として、100 件中 0 件の失敗でも片側 97.5% 上限は約 3.6% で、真の失敗率が 0 とは言えない（初期調査の片側 95% では約 2.95%）。[R02][R08]

### 6.4 停止規則と実行量の上限（観測前に凍結）

| 段 | 進む条件 | 止める条件 | 上限（承認対象） |
|---|---|---|---|
| smoke | 6 割当すべてに record があり、Goal arm が `fidelity=verified`、全 record が §2.2 の構成照合を通り、Mission 2 arm の state を観測でき、evaluator が全候補を manifest どおりの検査件数で評価し、**全 run の wall 時間が記録される**。加えて、T を意図的に短くした追加の 1 割当（Goal arm、H の task）で、T 到達の record が §2.3 の識別項目をすべて持ち、候補が評価されることを確かめる（この割当は run 数の上限に含める）。token・USD は値か理由付き null で埋まればよい（取れないことは進行を止めない） | いずれかを満たさない。harness を直して smoke をやり直す（smoke の結果は主張に使わない） | run 数 7 + 再実行 3、wall 時間の総和 10 × (T_smoke + g)。見積りは 3× 仮定で (49.45 + 追加の Goal 1 run 0.95) × 1.1 ≈ 55 USD 相当 |
| pilot | 72 割当を全件記録し、`p̂_goal` と各 arm の `non_quality` 率と wall 時間の分布を得る | ① native Goal の失敗が 24 割当中 2 以下（この benchmark で弁別しない。確認に要る K が非現実的になる見込み）→ owner へ上げる ② いずれかの arm で `non_quality` が 10% を超える → harness 停止 ③ `assignment_turn_limit` が 1 件でも出る → M を上げて smoke から ④ run 数か wall 時間の総和が上限に達した → 停止し、残りを `not_started` として報告 | run 数 72 + 再実行 9、wall 時間の総和 81 × (T + g)。見積りは 3× 仮定で 593.44 × 1.1 ≈ 653 USD 相当 |
| 確認 | §6.3 で決めた K の全割当を実行 | 成功による早期停止はしない（中間解析を行わない）。最初の 20% の割当でいずれかの arm の `non_quality` が 10% を超えたら一時停止し、owner へ上げる。run 数か wall 時間の総和が上限に達したら停止して `inconclusive_incomplete` | `R`（§6.2）と `R × (T + g)`。owner が K と T とあわせて承認する |

T・M・g は smoke の実測を見て pilot 前に凍結する（T は全 arm 同じ値）。run 数・wall 時間の上限は「割当の実行を順に進め、各 run の後に累計を確かめて止める」形で守る。

## 7. 集計の実装と PR 分割

### 7.1 report schema（決定）

`mission-benchmark-aggregate/1`（JSON）。Markdown は JSON からだけ描画する。

- `inputs`: 割当計画の digest、record 群の digest、evaluator の digest（H では生成器 bytes・catalog、公開 benchmark では bundle digest と evaluator adapter の識別）、事前登録文書の commit SHA（§5.0 の commit A・B・C を含む）、seed の取り方と値（beacon の round 番号または commit-reveal の digest）、pool manifest の digest、選択一覧の digest、§3.2.1 の検査結果、各 arm の仕様（§2.2 の照合項目すべて）、host・Codex version・model・effort・permission・T・M・g。
- `assignments[]`: 計画した全割当。`assignment_id`、attempt の一覧、task、`unit_id`（H では family）、`fixture_group`（worker/control）、`primary`（主 task か）、実行順の位置、planned arm、構成照合の結果（項目ごとの一致・不一致・欠落）、run の `outcome`/`reason`/`fidelity`・`deadline_reached`・`turn_start_sent`、候補の digest（probe と評価直前）、評価の `status`/`reason`/`case_count`、§3.1 の結果（`success`/`quality_failure`/`non_quality` と理由）、段ごとの評価（S0/S2/S3。無ければ `unmeasured` と理由）、帰属、偽完了の旗、実行量・費用・時間・介入（値または null と理由）。
- `arms{}`: 主結果（主 task の x, K, F, 片側限界）、参考の対称 ITT と除外版、副次項目（分子・分母・null 理由）、偽完了、理由コード別の件数、分類別の内訳（§4）、task 単位の参考集計（`reference_only: true`）。
- `primary_judgement`: §3.3 の判定値と、判定に使った数値。判定は主 task（1 単位 1 件）の値と §3.2.1 の検査結果だけから計算し、task 単位の集計を入力に取らない。`claims_allowed`: 判定値から機械的に決まる許可文だけ（自由記述を置かない）。
- `report_kind`: `end_to_end` / `diagnostic_ablation` / `development`（H）/ `confirmatory`。kind が違う report を一つの母集団に合算しない。`confirmatory` 以外の kind では `primary_judgement` を出さず、`claims_allowed` を空にする。

### 7.2 全割当の会計（決定）

- 計画 → record の結合は G の `preserve_assignment_outcomes` を呼ぶ。[S02] 計画の arm 欄には record の label を渡し、planned arm は I が別に持って §2.2 で照合する。計画外 record・重複・不一致は集計全体を拒否する（理由付き終了）。
- 評価が無い割当は「評価欠落」として `quality_failure` ではなく原因に応じて分類し（候補はあるが評価が無い場合は集計を拒否する。候補が無い場合は `non_quality`）、件数を別に出す。評価の重複は拒否する。
- E の event は event ID で重複排除して割当に join する。join できない event は件数を出して拒否理由に残す（黙って捨てない）。
- 計画・record・評価・event のどれかが読めない場合は report を出さずに理由付きで失敗する。一部だけで report を出さない。

### 7.3 段の帰属と runner（決定）

- 段の候補（S0/S2/S3）は E の event の候補 ref から凍結済みの成果物を取り出し、その割当と同じ evaluator（H または §5.2）で評価する。event の無い期間（E4 より前）や absent の ref は `unmeasured`。[D02]
- ablation runner は、ある end-to-end 割当の S0 を starter として凍結し、variant ごとに新しい割当（`diagnostic_ablation`）を作って G の adapter で実行し、同じ evaluator で評価する。variant 間で starter・T・M・host・model は同じにする。
- end-to-end の runner は、計画に従って task を一時 repo へ書き出し（H は `materialize_task`、公開 benchmark は §5.2 の入口）[S17]、G の probe を割当ごとに §2.1 の終了条件で実行し、run の wall 時間を測り、凍結した候補を評価する。worker の雛形照合には別に作った無変更の export を使う（G の candidate は worker が書き換えた後の木なので、雛形照合に使えない）。[S11][S22]
- runner は割当を commit 済みの実行順（§5.0。arm が無作為に交互に混ざる）で 1 件ずつ実行し、§6.4 の停止規則と run 数・wall 時間の累計を各 run の後に確かめる。並列化はしない（上限の判定が遅れ、§3.2.1 の検査 5 が成り立たなくなるため）。再実行は元の割当の直後に行う。

### 7.4 PR 分割と見積り

1 PR = 1 一次関心事の方針に従い、#884 を 5 PR に分ける。設計レビュー 2 巡目の反映で、公開 benchmark の調査・選定・evaluator と probe の識別項目の記録が加わり、旧 I2 が分割必須の閾値を超える見込みになったため、旧 I2 を I2a・I2b・I2c に分けた。見積りは未実測で、配布 mirror と生成物を除いた reviewed lines（追加+削除）。repo の閾値は 600 行で説明、1,400 行で分割必須。[S18] ×1.6 は依頼で指定された較正係数で、repo 内の出典は見つからなかった（**UNKNOWN**）。

| PR | 内容 | raw 見積り | ×1.6 | 閉じ方 |
|---|---|---:|---:|---|
| I1: 集計と判定 | 割当計画の型（planned arm・primary・attempt・unit_id）、record・評価・E event の結合、全割当の会計、§3.1 の 3 値分類と仮説に不利な側への計数、主 task の区間計算と判定、§3.2.1 の検査 4・5（run の分離・実行順）、検査 1〜3 の証拠を入力の型として要求し無ければ `invalid_cohort` にすること（fail-closed）、偽完了、帰属、JSON schema と Markdown 描画。偽の割当欠落・重複・分母 0・event 欠落・evaluator 欠落・同じ単位の重複で `achieved` を出さないこと（§3.2 の反例）・task 単位の値が判定へ入らないこと・Poisson 二項での片側限界の保守性（§3.2 の数値確認 2 種）を Red にするテスト | 690〜870 | 1,104〜1,392 | `Refs #884` |
| I2a: probe の識別項目と record の照合 | §2.3 の probe の変更（`provider_version` の前後取得、識別項目の入れ物、`turn_start_sent`、deadline の型付けと `assignment_deadline_reached`）と、§2.2 の record ごとの構成照合（Mission 2 版の区別を含む）。T 到達・例外・EOF・skill 未観測の各経路で識別項目が揃うこと、deadline と EOF が区別されることを Red にするテスト | 400〜520 | 640〜832 | `Refs #884` |
| I2b: 公開 benchmark の調査報告 | 文書のみ。候補 benchmark ごとに、license（データと upstream）、§5.1 の基準 Lic・Det・Cx・Con を満たす task 数と別 project の単位数（G1〜G3 適用後）、評価環境と network 遮断の可否、control を作れるか、G の export で足りるか、seed の取り方（beacon の可否）、clone の容量と計算量を実測または **UNKNOWN** で報告し、owner の選択を仰ぐ | 150〜250 | 240〜400 | `Refs #884` |
| I2c: 選定・bundle・evaluator | §5.0 の key と選択（pilot・順位・主 task・control・実行順）の計算と再計算、基準 Lic・Det・Cx・Con と系譜検査 G1〜G3 の機械判定、pool manifest、§5.2 の bundle の入口（digest 検証・閉じた schema・束縛）と公開 benchmark 用の evaluator adapter、§3.2.1 の検査 1〜3 の証拠の生成。seed と pool の順序違反・key の衝突・系譜の共有・bundle digest 不一致・未知 key・benchmark 由来 code の非実行を Red にするテスト | 640〜820 | 1,024〜1,312 | `Refs #884` |
| I3: runner と ablation | 計画に従う end-to-end runner（task の書き出し → G probe（§2.1 の M と T）→ wall 時間の測定 → 凍結 → 評価）、commit 済みの実行順での実行、run 数・wall 時間の上限と停止規則、再実行の上限、同じ初回成果からの ablation variant、offline の契約テストと H での診断手順 | 600〜780 | 960〜1,248 | `Closes #884` |

合計は raw 2,480〜3,240、×1.6 で 3,968〜5,184（2 巡目時点の 3 PR は raw 1,620〜2,100、×1.6 で 2,592〜3,360）。

依存: I1 を先に merge する。I2a は I1 の型を使う。I2b は他に依存せず先行でき、その報告への owner の選択が I2c の着手条件である。I2c は I1・I2b に依存する。I3 は I1・I2a・I2c に依存する。I1・I2c・I3 は 600 行を超える見込みなので、PR 本文に「分割しない理由」を書く（I1 は会計と判定が同じ型の producer/consumer で、片方だけ main に入ると分母の無い判定ができてしまう。I2c は選定と bundle の束縛が同じ「どの task を受け入れるか」の関心事。I3 は runner の停止規則と実行が同じ関心事）。I1 は上限が 1,400 行に近いので、実 diff が超えたら Markdown 描画を別 PR に切り出す。他も実 diff で再計測し、1,400 行を超えたら再分割する。

TDD・既存の CI 経路（`make test-shard` → Quality）・配布 mirror・artifact hygiene は既存の規律に従う。判定・validator を変える PR（I1・I2a・I2c）なので、異系統レビューの前に正常・拒否あわせて 50 入力以上の独立探索を行い、件数と発見数を PR 本文に記録する（Issue 884 本文）。通常 CI に有料のモデル実行を追加しない。

## 8. owner の決定事項・対象外・主張してはならないこと

### 8.1 owner の決定が要る事項

1. **段ごとの実行量の承認**: smoke・pilot・確認の各段で、run 数の上限と wall 時間の総和の上限（§6.1）を承認する。USD は見積りとして併記するが、上限の判定には使わない。承認が無い段は実行しない。確認実験の上限は pilot の後に §6.3 で算出して改めて承認を求める。
2. **host の範囲**: 確認実験を Codex のみとするか。CC は Goal の lifecycle を観測できず、現状では unsupported（§1）。
3. **baseline arm を pilot・確認に含めるか**: 含めると pilot で約 143 USD 相当、確認で (K + ⌈K/2⌉) × 5.94 USD 相当が加わる。主判定には不要で、#876 の改善の帰属にだけ使う。
4. **公開 benchmark の選択（I2b の報告の後）**: 確認用 cohort を外部の公開 benchmark から作ることは決定済み（2026-10-04）。I2b の報告を見て、使う benchmark と版、bundle と pool manifest の保管場所（license が公開 repo への保存を許すか）を決める。
5. **選定基準の閾値と seed の取り方（commit A の前）**: Cx1〜Cx3 の閾値（推奨: source file 2 件以上・検査 3 件以上・変更 10 行以上）、Con2 の閾値、G3 の閾値（推奨 20%）、cohort の構成比（欠陥入り : control、推奨 2:1）、seed を公開 beacon（推奨。可否は I2b）と commit-reveal のどちらで取るか。
6. **ablation の可否**: J の profile に gate のみ / gate + 反例探索の切替を入れるか。入れない場合 ablation は未実施と報告する。
7. **I1 の着手時期**: Issue 884 は E・F・G・H の merge 後を条件にしている。E4 の event schema が確定してから着手するのが推奨。先に着手する場合、段の項目は全件 `unmeasured` とし、E4 後に結合を追加する。
8. **必要な単位数が足りない場合の扱い**: §5.1 の基準を満たす別 project が 180〜560 以上ある公開 benchmark が無い場合、複数の benchmark を合わせるか（系譜検査は benchmark をまたいで行う）、確認実験を行わず「未検証」のままとするか。1 単位から複数 task を主解析に入れること（§3.2 の推定対象は条件付きなので統計的には成り立つが、「K 個の別 project」という主張が成り立たなくなる）、task 単位の解析へ戻すこと、閾値（1/10）を後から変えることは、owner が観測前に事前登録を改めない限り選ばない。
9. **T と Codex version の固定**: smoke の実測後に T・M・g と、確認実験の間に使う Codex version を固定すること（version が途中で変わった record は `non_quality` になる）。

### 8.2 対象外

- 確認実験・pilot・smoke の自動起動、上限の無い有料実行、10 倍に届くまで実行を続けること。
- 通常 CI への有料モデル実行の追加。
- G・H の判定規則（Goal の観測、H の evaluator の合否）の変更。I は呼ぶだけ。G の probe への変更は §2.3 の 4 点（`provider_version` の前後取得、識別項目の入れ物と `turn_start_sent`、deadline の型付けと `assignment_deadline_reached`）に限り、Goal の terminal status の解釈は変えない。
- 過去の結果ファイルの書き換え・再採点による置換。
- CC host の lifecycle 観測手段の開発（別 Issue）。
- 選定 tool の実行（pool の組成・seed の確定・選択）と bundle の作成そのもの。I2c は tool を作るだけで、実行は §5.0 の手順に沿って commit A〜C として別に行う。
- 公開 benchmark の課題・解答の変更や、benchmark 側の検査の差し替え。

### 8.3 主張してはならないこと

- 確認実験で `achieved` を得るまで、「10 倍」「1/10」「品質が N 倍」と書かない。H の 12 件・smoke・pilot・ablation・task 単位の参考集計の結果は、どれほど良くても改善の証拠として書かない。
- `not_achieved`・`not_comparable`・`inconclusive_incomplete` を「同等」「有意差なし＝同じ品質」と書かない。
- Mission の `passes`、score、finding の解決数、修復件数を品質の指標として書かない。
- API 相当額を請求額として書かない。定額プランでの実行は金額ではなく rate limit を消費する。wall 時間を費用として書かない。
- run の総和時間と経過時間、wall 時間と総実行時間を混同しない。
- 確認結果を、選んだ K 件の task・host・model・予算以外へ一般化しない。「benchmark 全体で」「複雑タスク一般で」と書かない。`achieved` の文から「run が互いに独立という前提の下で」を落とさない。

## 9. 事前登録として凍結する項目

確認実験の前に、本書の該当節と追記（§5.0 の commit A〜C、pilot 後の K・T・M・g・Codex version・各 arm の仕様と package digest・bundle digest・主 task・control・実行順の一覧）を commit し、その commit SHA を report の `inputs` に記録する。

1. 主結果の 3 値分類（`success`/`quality_failure`/`non_quality` と `non_quality` の閉じた列挙）、仮説に不利な側への計数、予算時点の候補の規則（§3.1）、受入可能の判定器、予算の単位（共通の wall-clock 上限 T）。
2. 再実行規則（turn 開始前の基盤障害（`turn_start_sent == 0`）のみ、1 割当 1 回、各段・各 arm で計画割当数の 10% が上限、両 arm 対称、元 record を attempt として保持）。
3. 解析単位（upstream の project、単位ごとに seed から選んだ主 task 1 件、確認実験では 1 run / task / arm）、推定対象（選んだ K task の 1 run あたり失敗確率の平均）、独立性の検査（§3.2.1）、区間の方法（片側 Clopper-Pearson ×2、各 0.025、Bonferroni、`RR_upper ≤ 0.1`）、guard（control 割当の失敗率）（§3.2）。
4. 判定値・優先順・報告文（§3.3）。
5. arm の定義（§2）、終了条件と turn 上限 M（§2.1）、arm 仕様と record ごとの照合項目・不一致時の扱い（§2.2）、T 到達を品質の結果とする扱いと識別項目の記録・経路ごとの分類（§2.3）、baseline SHA `ed1d1c2723c55c08f06a82d6e397b164323b5b59`、verified-complex の package SHA（J merge 後に凍結）。
6. 公開 benchmark からの選定の手順と順序（§5.0）、選定基準 Lic・Det・Cx・Con と系譜検査 G1〜G3 の閾値（§5.1）、規模・構成比・使用回数（§5.1）、bundle と evaluator の受け入れ境界（§5.2）。
7. K の決め方（§6.3）、停止規則と各段の run 数・wall 時間の上限（§6.1、§6.4）。
8. 副次項目の定義、偽完了の独立集計、帰属の順序（§4）。

## 固定 head の出典

リンクは全て `d25a66c6abed33a4c0fbd1036e22bdf49da3c606` に固定。各ラベルの `file:line` はこの head で読んだ範囲。

[S01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_result.schema.json#L1-L36 "native_goal_result.schema.json:1-36 — arm/outcome/fidelity の閉じた列挙、verified の config_matches・manifest 要求"
[S02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L101-L127 "native_goal_benchmark.py:101-127 — preserve_assignment_outcomes（全割当、欠落は not_started、計画外と重複を拒否、label の照合 117）"
[S03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L40-L85 "native_goal_benchmark.py:40-85 — observe_codex_goal（同一性・terminal status・tokensUsed）"
[S04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L88-L98 "native_goal_benchmark.py:88-98 — observe_claude_goal は完了を確立しない"
[S05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L151-L232 "run_native_goal_probe.py:151-232 — probe_codex（config_matches 162、Mission arm は turn/start 1 回 185-198、budget_enforcement と package_delivery 226-229）"
[S06]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L296-L317 "run_native_goal_probe.py:296-317 — _fresh_mission_state（.mission-state/sessions/cx-<thread>.json）"
[S07]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L33-L37 "run_native_goal_probe.py:33-37 — RpcProcess の単一 deadline"
[S08]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/native_goal_benchmark.py#L191-L229 "native_goal_benchmark.py:191-229 — immutable_manifest（package sha256・conditions 197-203）と create_immutable_package"
[S10]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L333-L392 "complex_fixture_benchmark.py:333-392 — evaluate_candidate（fresh process、非 pass の保持、root 引数は本体で未参照）"
[S11]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L401-L434 "complex_fixture_benchmark.py:401-434 — evaluate_assignment（fixture_group=worker 固定 411、雛形照合 419-420）"
[S12]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/generate_complex_fixtures.py#L21-L34 "generate_complex_fixtures.py:21-34 — 12 task と 6 family"
[S13]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/generate_complex_fixtures.py#L306-L313 "generate_complex_fixtures.py:306-313 — task_template の worker/reference/control"
[S14]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L125-L139 "complex_fixture_benchmark.py:125-139 — load_catalog は 12 件ちょうどを要求"
[S15]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex-fixtures/README.md#L1-L13 "complex-fixtures/README.md:1-13 — 開発用 cohort、10 倍の証拠ではない（3, 12-13）"
[S16]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L453-L483 "complex_fixture_benchmark.py:453-483 — export_worker_fixtures（固定 catalog root 以外を拒否 460-463、G の positive allowlist export を呼ぶ）"
[S17]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L78-L122 "complex_fixture_benchmark.py:78-122 — materialize_task（固定 commit の生成器から一時 repo）"
[S18]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/AGENTS.md#L83-L126 "AGENTS.md:83-126 — reviewed area の測り方、600/1,400 の閾値"
[S19]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L199-L222 "run_native_goal_probe.py:199-222 — Goal arm の turn ループ 204-217、assignment_turn_limit 219-220、構成不一致で unverified 221-222"
[S20]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L55-L88 "run_native_goal_probe.py:55-88 — deadline 後の request は TimeoutError（74）、wait_for_event は False（76-88）"
[S21]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L352-L355 "run_native_goal_probe.py:352-355 — --timeout-seconds 既定 30、--max-turns 既定 2"
[S22]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L370-L420 "run_native_goal_probe.py:370-420 — arm label 370、manifest の conditions・mission_source_commit 378-385、worker_export 389-395、provider_version 405、例外経路は observed_config を持たない 410-414、候補 digest 415-419、record の書き出し 420"
[S24]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L47-L87 "complex_fixture_benchmark.py:47-87 — 生成器 bytes の exec 47-52、_fixed_generator の commit 照合 55-65、materialize_task の unknown fixture task 拒否 78-87"
[S26]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L200-L217 "complex_fixture_benchmark.py:200-217 — _entry_error（checks の形）と _entry_metadata"
[S27]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L395-L398 "complex_fixture_benchmark.py:395-398 — freeze_candidate（envelope の key と candidate digest）"
[S28]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/complex_fixture_benchmark.py#L142-L150 "complex_fixture_benchmark.py:142-150 — evaluator の固定 runner（候補の service.py を読み込み execute(scenario) を呼ぶ）"
[S29]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L89-L122 "run_native_goal_probe.py:89-122 — _next_message は select の timeout と stdout の EOF の両方で None を返す（98-101）、close は app-server を terminate/kill する（115-122）"
[S30]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L181-L193 "run_native_goal_probe.py:181-193 — skill 未観測の早期 return は package_delivery を持たない（183）、Mission arm の base（191）と turn_completion_unobserved（193）"
[S31]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L144-L148 "run_native_goal_probe.py:144-148 — _codex_version（失敗は provider_version_unavailable の RuntimeError）"
[R01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/proposal.ja.md#L1-L11 "proposal.ja.md:1-11 — 調査ソース ed1d1c27、効果は仮説"
[R02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/statistical-examples.json#L1-L60 "statistical-examples.json:1-60 — 正確な二項限界 + Bonferroni の例示（実験結果ではない）"
[R03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/PRE_REGISTRATION.md#L96-L98 "docs/PRE_REGISTRATION.md:96-98 — 事前登録した基準の変更の規律"
[R04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L15-L36 "2026-08-21-verdict-tail-v1-summary.json:15-36 — Goal arm cost_usd_mean 0.9477、total 14.2157、records 15"
[R05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L48-L73 "2026-08-21-verdict-tail-v1-summary.json:48-73 — Mission arm budget_blocked 6、cost_usd_mean 5.9447、total 89.1708、records 15"
[R06]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L186-L192 "2026-08-21-verdict-tail-v1-summary.json:186-192 — 制約: budget 打ち切り、API 相当額は請求額ではない"
[R07]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/results/2026-08-21-verdict-tail-v1-summary.json#L216-L233 "2026-08-21-verdict-tail-v1-summary.json:216-233 — 30 records、3 反復、tail cohort、starting_commit 068dc405"
[R08]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/proposal.ja.md#L184-L194 "proposal.ja.md:184-194 — pilot の規模、family 単位の主解析と相関（188-192）、片側上限、公開表現の条件"
[R09]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/reports/benchmark-quality-20261003/proposal.ja.md#L111-L111 "proposal.ja.md:111 — 開発用の複雑タスクの対象（別プロジェクトの複数モジュール変更、互換性、再実行、集計整合等）"
[D01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L126-L145 "docs/design/880-repair-lineage.md:126-145 — E の比較履歴と mission-repair-event/1、集計は I（未 merge 実装依存）"
[D02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L53-L53 "docs/design/880-repair-lineage.md:53 — E4 より前は event が無く未測定として扱う要求"
[D03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L222-L226 "docs/design/880-repair-lineage.md:222-226 — orchestrator の決定: I との event 契約と証拠保持"
