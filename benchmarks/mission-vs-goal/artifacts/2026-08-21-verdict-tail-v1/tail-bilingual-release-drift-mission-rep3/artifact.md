# tail-bilingual-release-drift — mission arm (rep3)

- Task id: `tail-bilingual-release-drift`
- Task category: documentation
- Arm: mission / Mission profile: full / Complexity: Complex
- Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
- Compared copy: `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`

## Mission

英語ドラフトを唯一の source of truth として、日本語訳を主張単位 (claim by claim) で突き合わせ、日本語側が (a) 英語の証拠より強い、(b) 数値が異なる、(c) 安全に関わる記述を欠落させている、箇所をすべて特定する。言い換えただけで内容が等価な主張は non-finding として理由付きで棄却する。

対象は上記 2 つの fixture ファイルと本成果物 1 ファイルのみ。`benchmarks/mission-vs-goal/` 配下のその他 (タスク定義・採点設定・正解キー) は参照していない。本成果物はベンチマークの優劣を主張しない。

## Plan

採択済み計画 (`.mission-state/plan-iter1.json`, `planning adopt-core` generation 1 で検証済み) の 4 ステップ。

| step | action | depends_on | 完了条件 |
|---|---|---|---|
| S1 | read | — | EN 全 7 主張 (見出し文 + 6 箇条)、JA 全 6 主張 (見出し文 + 5 箇条) を漏れなく列挙 |
| S2 | analyze | S1 | 各判定に EN/JA の逐語引用を付ける。言い換え等価は rejected candidate へ |
| S3 | write | S2 | 必須 8 見出し + findings テーブル 1 個 (verdict は `drift` / `no-finding` のみ) |
| S4 | decide | S3 | reviewer 2 名以上を `review-finalize` で集計し `closeout` が exit 0 |

範囲外 (触らない): fixture の編集、benchmark metadata の参照、commit / push / install / network。

## Execution

両 fixture を全文読み、行番号付きで主張を抽出した。EN は内容行 13 行、JA は内容行 9 行 (いずれも末尾に改行を持つ)。以下が突き合わせの一次データ (逐語引用)。

### 主張単位パリティ表

| # | 主張 | 英語 (source of truth) | 日本語 | 判定 | 分類 |
|---|---|---|---|---|---|
| C1 | リリース段階 | `We are announcing the public beta of our task runner.` (en:3) | `タスクランナーの正式版を発表します。` (ja:3) | 乖離 | stage drift |
| C2 | レイテンシ改善 | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` (en:5-6) | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` (ja:5) | 乖離 | overclaim (数値限定子の強化 + 安全側 caveat の欠落) |
| C3 | 中断からの再開 | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` (en:7-8) | `実行が中断しても、必ず自動で復旧します。` (ja:6) | 乖離 | overclaim |
| C4 | 自動テスト件数 | `The release is verified by 402 automated tests.` (en:9) | `本リリースは 500以上の自動テストで検証されています。` (ja:7) | 乖離 | numeric drift |
| C5 | 手動承認 | `Irreversible actions require manual approval before they run.` (en:10) | (対応文なし) | 乖離 | omission (safety-relevant) |
| C6 | CLI ワークフロー互換 | `It works with existing CLI workflows without changes.` (en:11) | `既存の CLI ワークフローにそのまま組み込めます。` (ja:8) | 等価 | — (rejected candidate) |
| C7 | テレメトリー | `No telemetry is collected.` (en:12) | `テレメトリーは収集しません。` (ja:9) | 等価 | — (rejected candidate) |

### 確定した findings (confirmed)

**F1 — stage drift: public beta → 正式版**
EN: `the public beta of our task runner`。JA: `タスクランナーの正式版を発表します`。`public beta` は一般提供前の段階を指すが、`正式版` は GA (一般提供) を意味する。日本語側は英語の証拠にない成熟度を主張しており、期待値・サポート水準・安定性の読み取りを変える。分類: stage drift。

**F2 — overclaim: `by 18%` → `18%以上` かつ caveat 欠落**
EN: `median latency improved by 18%` に続けて `This is a single controlled measurement, not a general performance claim.`。JA: `中央値レイテンシを 18%以上改善しました。` の 1 文のみ。
2 点の乖離がある。(i) `by 18%` は点推定だが `18%以上` は下限としての保証を意味し、英語より強い。(ii) 「単一の統制下の計測であって一般的な性能主張ではない」という限定文が日本語に一切存在せず、単一パイロットの結果が一般的な性能主張として読める。分類: overclaim (数値限定子の強化を伴う)。

**F3 — overclaim: `not guaranteed` → `必ず`**
EN: `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.`。JA: `実行が中断しても、必ず自動で復旧します。`。英語が明示的に否定している「あらゆる障害モードでの保証」を、日本語は `必ず` で肯定している。加えて英語の `can be resumed from persisted state` (再開が可能) に対し日本語は `自動で` 復旧すると述べ、操作の自動性まで強めている。信頼性に関する最も強い方向の反転であり、安全性に関わる。分類: overclaim。

**F4 — numeric drift: 402 → 500以上**
EN: `The release is verified by 402 automated tests.`。JA: `本リリースは 500以上の自動テストで検証されています。`。実数 402 に対し `500以上` は数値が異なるうえ、実際の件数を約 24% 上回る下限を主張している。分類: numeric drift。

**F5 — omission: 手動承認の要件が日本語に存在しない**
EN: `Irreversible actions require manual approval before they run.` (en:10)。JA には対応する文がない。日本語 fixture 全文 (ja:1-9) に `承認` `手動` `不可逆` のいずれの語も出現しない。不可逆操作の実行前に人手の承認が要る、という安全上の前提条件が読者に伝わらないため、safety-relevant な欠落である。分類: omission。

### 棄却した候補 (rejected candidates)

以下は「怪しく見えたが、証拠上は乖離ではない」ものである。

**R1 — `without changes` → `そのまま組み込めます` (cli_workflow_claim)**
怪しく見えた理由: 英語が `works with ... without changes` (変更なしで動作する) と述べるのに対し、日本語は `そのまま組み込めます` と、能動的な組み込み・統合が可能であるかのような語を使っており、語彙が 1 対 1 対応していない。
棄却理由: `そのまま` が `without changes` を正確に担っており、主張の強さは同じである。日本語側に `すべての` `あらゆる` といった全称限定子も、英語にない保証語も追加されていない。動詞の選択の違いは訳文としての言い換えであり、主張の内容・強度・条件のいずれも変えていない。よって non-finding。

**R2 — `No telemetry is collected.` → `テレメトリーは収集しません。` (telemetry_claim)**
怪しく見えた理由: 英語は受動態 (誰が収集しないかを明示しない) だが、日本語は主体を省略した能動形であり、他の箇所で日本語が強化されているパターンから、ここでも範囲が広がっている可能性を疑った。
棄却理由: 主張内容は「テレメトリーを収集しない」で完全に一致し、日本語側に `一切` `いかなる場合も` のような英語にない強調は付いていない。日本語の主語省略は英語の受動態と同じく主体を特定しない表現であり、範囲の拡大にあたらない。よって non-finding。

**R3 — 見出しの表記差 `draft (English, source of truth)` → `ドラフト（日本語訳・レビュー前）`**
怪しく見えた理由: 見出しの括弧内が一致していない。
棄却理由: いずれも当該ファイル自身の位置づけを記述したメタ情報であり、製品に関する主張ではない。日本語側の `レビュー前` は英語の `draft` と整合し、むしろ弱い方向である。製品主張のパリティ対象外として棄却。ただし本行は findings テーブルの採点対象 key ではないため、テーブルには含めていない。

### Findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| announcement.ja.md | automated_test_count | `402 automated tests` (実数 402) | `500以上の自動テストで検証されています` | drift |
| announcement.ja.md | cli_workflow_claim | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` (等価) | no-finding |
| announcement.ja.md | latency_improvement_claim | `median latency improved by 18%` + `This is a single controlled measurement, not a general performance claim.` | `中央値レイテンシを 18%以上改善しました。` (下限化 + caveat なし) | drift |
| announcement.ja.md | manual_approval_sentence_omitted | `Irreversible actions require manual approval before they run.` に対応する文が存在すること | 対応文なし (ja:1-9 に `承認`/`手動`/`不可逆` の語が 0 件) | drift |
| announcement.ja.md | resume_guarantee_claim | `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | drift |
| announcement.ja.md | telemetry_claim | `No telemetry is collected.` | `テレメトリーは収集しません。` (等価) | no-finding |
| announcement.ja.md | release_stage_claim | `the public beta of our task runner` | `タスクランナーの正式版を発表します。` | drift |

## Review

`.mission-state/` に記録された mission ループの reviewer 集計結果に基づく。

reviewer 3 名を単一メッセージで並列起動し、各自の `mission-review/1` JSON を `review-import` で保存した。逐語再掲はせず要旨のみ記す (レビュー生データは `.mission-state/archive/` に全量保存済み)。

| 観点 | 指摘 id / 重要度 | 要旨 | 対応 |
|---|---|---|---|
| A: 正確性・証拠 | A-1 / Low | 「JA は 9 行」が末尾改行を含む実ファイル行数と 1 ずれる | 修正済み (「内容行 9 行」と明記) |
| B: 完全性・validator 適合 | B-1 / Medium | findings テーブルの 7 行目 `release_stage_claim` は指定 6 key の外であり、機械照合で無視される恐れがある | 据え置き。理由を Assumptions A3 に明記 |
| B: 完全性・validator 適合 | B-2 / Low | Score 節が composite_score の実値を持たずポインタのみ | 表に「種別」列を追加し、実測ポインタ／閾値を区別 |
| C: リスク・規律遵守 | C-1 / Low | `max_agreement_delta` が実測値か閾値か判別できない表記 | 同上。閾値と明示 |
| C: リスク・規律遵守 | C-2 / Low | `open_high: 0` だけポインタなしで断言され非対称 | 同上。根拠 (`closeout` exit 0) を併記 |

観点 A は「5 件の drift 判定はすべて逐語引用で裏付けられ、false drift はゼロ」と結論した。観点 B は英語側 7 主張すべてが adjudicate されており見落としはないと結論した。観点 C は run rules (成果物 1 ファイル / 新規コミットなし / benchmark 優位性の主張なし / 未計測の明示 / 仮定の反証可能性) をすべて充足と結論した。High 指摘は 0 件。

検証 (verification) として実行した事実確認:

| check | 結果 | 根拠 |
|---|---|---|
| 必須 8 見出しの存在 | ok | 本ファイルの `## Mission` / `## Plan` / `## Execution` / `## Review` / `## Score` / `## Stop Decision` / `## Evidence` / `## Assumptions` |
| findings テーブルがちょうど 1 個 | ok | `\| location \| key \| expected \| actual \| verdict \|` ヘッダーは本ファイル中 1 箇所のみ |
| 指定 6 key の全出現 | ok | automated_test_count / cli_workflow_claim / latency_improvement_claim / manual_approval_sentence_omitted / resume_guarantee_claim / telemetry_claim |
| verdict 値が `drift` / `no-finding` のみ | ok | 7 行すべて該当 |
| 引用の逐語一致 | ok | EN 13 行・JA 9 行を全文読み、引用文字列を原文と照合 |

## Score

`review-finalize` (= `aggregate-reviews` → `push-score`) が算出した値のみを記載する。手計算での pass 判定は行っていない。

| 項目 | 種別 | 値 |
|---|---|---|
| composite_score | 実測 | 4.33 (`review-finalize` が算出) |
| 軸別スコア | 実測 | mission_achievement 4.33 / accuracy 4.33 / completeness 4.33 / usability 4.33 |
| threshold | 閾値 | 4.0 |
| min(scored_items) | 実測 | 4.33 (>= 3.5) |
| open_high | 実測 | 0 |
| max_agreement_delta | 閾値 | 1.5 (実測値は scoring evidence に記録。gate 充足) |
| findings_evidence_path | 実測 (ポインタ) | `.mission-state/archive/iter-1-561de73d-scoring-b02609218b0ab6af.json` |
| passes | 実測 | true (`closeout` = `mark-passes` → `next` が exit 0、`next_action=report-complete`) |

reviewer 3 名 (観点 A 正確性 / B 完全性 / C リスク) の 5 段階採点は `review-import` で `mission-review/1` として保存し、`review-finalize` が集計した。本表に数値を手で転記していないのは、手計算の pass 判定を禁じる mission 規律に従うため。

## Stop Decision

- iteration 1 で scored review (reviewer 3 名 → `review-import` → `review-finalize` → `mark-passes`) を完了し、`closeout` が exit 0 / `next_action=report-complete` を返した時点で停止。composite 4.33 >= threshold 4.0、open_high 0。
- 外部 specialist は使用していない。ベンチマーク規約でネットワークアクセスが禁止のため、`specialists` 選定は `decision=unavailable` (terminal) として記録し、core reviewer 3 名で degraded 実行した。
- `--max-iter 2` に対し使用 iteration は 1。
- 未達 gate は無い。`halt_reason` は空。
- 追加の iteration を回さない理由: validator 要件 (パリティ表・分類・rejected candidates・findings テーブル) がすべて満たされ、open_high が 0 のため。

## Evidence

すべての確定 finding は 2 つの fixture の逐語引用に基づく。

| id | fixture / 行 | 逐語引用 |
|---|---|---|
| E1 | announcement.en.md:3 | `We are announcing the public beta of our task runner.` |
| E2 | announcement.ja.md:3 | `タスクランナーの正式版を発表します。` |
| E3 | announcement.en.md:5-6 | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` |
| E4 | announcement.ja.md:5 | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` |
| E5 | announcement.en.md:7-8 | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` |
| E6 | announcement.ja.md:6 | `実行が中断しても、必ず自動で復旧します。` |
| E7 | announcement.en.md:9 | `The release is verified by 402 automated tests.` |
| E8 | announcement.ja.md:7 | `本リリースは 500以上の自動テストで検証されています。` |
| E9 | announcement.en.md:10 | `Irreversible actions require manual approval before they run.` |
| E10 | announcement.ja.md (内容行 1-9 の全体) | 対応文なし。`承認` / `手動` / `不可逆` の語がいずれも出現しない (grep 一致 0 件) |
| E11 | announcement.en.md:11 / announcement.ja.md:8 | `It works with existing CLI workflows without changes.` / `既存の CLI ワークフローにそのまま組み込めます。` |
| E12 | announcement.en.md:12 / announcement.ja.md:9 | `No telemetry is collected.` / `テレメトリーは収集しません。` |

mission 実行の証跡:

| 項目 | 値 |
|---|---|
| routing | goal へルーティングされず (init が `route: "goal"` を返さなかった) ため mission ループを implementer role で実行 |
| session state | `.mission-state/sessions/cc-99bbedd6-21f3-4dc7-a662-7500745098d8.json` |
| mission_id | `561de73d4f86d2aa` |
| 採択計画 | `.mission-state/plan-iter1.json` (`planning adopt-core` generation 1, validated) |
| review 生データ | `.mission-state/archive/` (逐語再掲はしない — 出力圧縮規律) |

未計測 (unmeasured) と明示する事項:

- 本タスクの正解キーとの一致率は未計測。答え合わせに必要な benchmark metadata は参照禁止のため確認していない。
- 他の arm や他の rep との比較は未計測。本成果物はベンチマークの優劣を主張しない。
- fixture 以外の実プロダクト (テスト件数 402 の実在性、レイテンシ計測の再現) は未検証。判定は fixture の記述同士の突き合わせに閉じている。

## Assumptions

| id | 仮置き | 検証方法 / 状態 |
|---|---|---|
| A1 | network / commit / push / install が禁止のため、mission の local authoring sync (`mission-local-authoring-sync.sh`) は実行せず、repo 同梱の `scripts/mission-state.py` のみを使う | 全 state 操作が exit 0 で完了。ベンチマークの「ネットワーク禁止」規則を skill の bootstrap 手順より優先した |
| A2 | EN の限定文 (`This is a single controlled measurement, not a general performance claim.` / `it is not guaranteed under every failure mode.`) は safety-relevant statement とみなす | いずれも読者のリスク認識を下げる方向の情報であり、欠落・反転を drift として扱った |
| A3 | 採点対象として指定された 6 key に加え、`release_stage_claim` 行を 1 行追加した | 指示は「評価した項目ごとに 1 行」であり行数上限を定めていないこと、および validator が `stage drift` の分類を要求していることを根拠に追加を維持した。指定 6 key はすべて指定文字列どおりに 1 行ずつ存在するため、機械照合は成立する。追加行が照合対象外として無視される可能性は残るが、`public beta` → `正式版` は証拠上まぎれもない drift であり、誤った drift 主張にはあたらない。レビューで本行の削除を推奨する Medium 指摘 (観点 B) を受けたが、上記理由により据え置き、判断根拠をここに残す |
| A4 | `18%以上` は英語の `by 18%` に対する強化 (下限としての主張) であり、単なる訳し崩れではない | `以上` は日本語で明確に「その値を下回らない」を意味するため、点推定より強い主張と判定した |
| A5 | 見出し行のメタ情報差 (`source of truth` vs `レビュー前`) は製品主張ではないため findings テーブルの対象外 | R3 として rejected candidates に理由を記載 |

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-21 | 初版作成 (mission arm rep3, iteration 1) |
