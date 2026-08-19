# tail-bilingual-release-drift — claude_code_goal_command

- Task id: `tail-bilingual-release-drift`
- Task category: documentation
- Arm: `claude_code_goal_command`
- Sources compared (read in full, 13 lines EN / 9 lines JA):
  - `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md` (source of truth)
  - `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`

## Goal

英語ドラフトを唯一の正とし、日本語版を claim 単位で突合する。日本語側が (a) 英語より強い主張になっている、(b) 数値が異なる、(c) 安全性に関わる記述を欠落させている箇所をすべて特定し、両言語の原文を引用して分類する。表現が変わっているだけで意味が等価な claim は「非 finding」として理由付きで棄却する。

## Result

英語 7 claim・日本語 5 claim を突合し、**確定 finding 6 件**（overclaim 3 / numeric drift 2 / stage drift 1 / omission 2 ※1 件は overclaim と omission の複合）を検出した。等価な言い換えは **2 件を棄却**した。

分類別の内訳:

| 分類 | 件数 | 該当 finding |
|---|---|---|
| stage drift | 1 | F1 |
| numeric drift | 2 | F2, F4 |
| overclaim | 3 | F2, F3, F4 |
| omission（安全性に関わる記述の欠落） | 2 | F3（caveat 欠落）, F5（承認要件の全欠落） |
| omission（測定条件の限定文の欠落） | 1 | F6 |

安全性の観点で最も重い divergence は **F5**（"Irreversible actions require manual approval before they run." が日本語版に一切存在しない）と **F3**（復旧の非保証 caveat が「必ず」に反転）である。

### Claim-by-claim parity table

| # | EN claim（原文引用） | JA 対応箇所（原文引用） | 判定 | 分類 |
|---|---|---|---|---|
| C0 | `# Release announcement — draft (English, source of truth)` | `# リリース告知 — ドラフト（日本語訳・レビュー前）` | 非 finding | 棄却 R1 |
| C1 | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | **finding F1** | stage drift |
| C2a | `In one internal pilot, median latency improved by 18%.` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` | **finding F2** | numeric drift + overclaim |
| C2b | `This is a single controlled measurement, not a general performance claim.` | （対応表現なし） | **finding F6** | omission |
| C3a | `Interrupted runs can be resumed from persisted state.` | `実行が中断しても、必ず自動で復旧します。` | **finding F3** | overclaim |
| C3b | `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | （対応表現なし） | **finding F3**（同一 finding の omission 側） | omission |
| C4 | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | **finding F4** | numeric drift + overclaim |
| C5 | `Irreversible actions require manual approval before they run.` | （行そのものが存在しない） | **finding F5** | omission（安全性） |
| C6 | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | 非 finding | 棄却 R2 |
| C7 | `No telemetry is collected.` | `テレメトリーは収集しません。` | 非 finding | 等価 |

## Evidence

### 確定 finding

#### F1 — stage drift: public beta → 正式版（GA）

- EN: `We are announcing the public beta of our task runner.`
- JA: `タスクランナーの正式版を発表します。`
- 根拠: 英語の識別子は `public beta`。日本語の `正式版` は一般に GA（一般提供）を指し、beta より後段のリリース段階を表す。段階を 1 つ進めて提示しており、読者の導入判断（本番投入可否）に直結する。

#### F2 — numeric drift + overclaim: `18%` → `18%以上`

- EN: `median latency improved by 18%`
- JA: `中央値レイテンシを 18%以上改善しました`
- 根拠: 英語は点推定 `18%` を 1 件の計測結果として述べる。日本語の `18%以上` は下限保証（18% 以上が常に得られる）を意味し、単一計測から導けない主張へ強化されている。数値そのもの（18）は一致するが、量化の意味が異なるため numeric drift かつ overclaim として扱う。

#### F3 — overclaim + 安全性 caveat の omission: 「必ず自動で復旧」

- EN: `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.`
- JA: `実行が中断しても、必ず自動で復旧します。`
- 根拠: 英語は能力表現 `can be resumed` かつ明示的に `it is not guaranteed under every failure mode`（全障害モードで保証されない）と否定している。日本語は `必ず` で保証へ反転し、さらに `自動で` を追加している（英語には自動復旧を述べる語がなく、`from persisted state` という復旧元の限定のみ）。非保証の caveat 一文は日本語版に対応表現がない。強化と安全性記述の欠落が同一行で同時に起きているため 1 finding として扱い、分類は overclaim と omission の複合とする。

#### F4 — numeric drift + overclaim: `402` → `500以上`

- EN: `The release is verified by 402 automated tests.`
- JA: `本リリースは 500以上の自動テストで検証されています。`
- 根拠: 英語の値は `402`、日本語は `500以上`。実数が 402 → 500 へ増加しているうえ、`以上` により下限主張になっている。検証量の水増しであり、単純な訳語差では説明できない。

#### F5 — omission（安全性）: 不可逆操作の手動承認要件が丸ごと欠落

- EN: `Irreversible actions require manual approval before they run.`
- JA: 対応する箇所なし（日本語版の箇条書きは 4 項目のみで、承認・approval・不可逆に相当する語は 1 つも出現しない）。
- 根拠: 英語版の 6 番目の箇条書きが日本語版に存在しない。これは製品の安全ガード（実行前の人手承認）の説明であり、欠落すると読者は不可逆操作が無確認で走ると誤解しうる。本タスクで最も安全性影響が大きい divergence。

#### F6 — omission: 計測条件の限定文が欠落

- EN: `This is a single controlled measurement, not a general performance claim.`
- JA: 対応する箇所なし（`社内パイロットにおいて` は計測の場を示すのみで、`single controlled measurement` や `not a general performance claim` に相当する限定を含まない）。
- 根拠: 英語は性能主張の一般化を明示的に禁じている。日本語ではこの限定が消え、F2 の `18%以上` と組み合わさって一般的性能保証と読める状態になっている。

### 棄却した候補（reworded but equivalent / 非 finding）

#### R1 — 見出しの副題差: `(English, source of truth)` vs `（日本語訳・レビュー前）`

- EN: `# Release announcement — draft (English, source of truth)`
- JA: `# リリース告知 — ドラフト（日本語訳・レビュー前）`
- 疑わしく見えた理由: 括弧内の文言が一対一対応しておらず、日本語側にだけ `レビュー前` という語が増えている。
- 棄却理由: 両者とも `draft` / `ドラフト` を保持しており、副題は各ファイルの役割メタ情報（どちらが原文か・翻訳の状態）を述べたもので、製品に関する claim ではない。日本語側の追加語 `レビュー前` は主張を強めるものではなく、むしろ確度を弱める方向であり、overclaim・numeric drift・safety omission のいずれにも該当しない。

#### R2 — `without changes` vs `そのまま組み込めます`

- EN: `It works with existing CLI workflows without changes.`
- JA: `既存の CLI ワークフローにそのまま組み込めます。`
- 疑わしく見えた理由: `works with`（動作する）が `組み込めます`（組み込める）という別動詞に置き換わっており、字面上は対応が崩れている。また `そのまま` が `without changes` より緩い表現に見える。
- 棄却理由: `そのまま` は「変更なしで」の定訳範囲であり `without changes` と同じ限定を保持している。動詞の差も、既存ワークフローとの互換性という同一の主張を自然な日本語に言い換えたもので、対象範囲・強度・数値のいずれも変わっていない。よって reworded but equivalent として非 finding。

（参考・言い換えのみで棄却するまでもない完全一致: C7 `No telemetry is collected.` / `テレメトリーは収集しません。` は主張・強度ともに一致。）

### 未測定・未検証の事項

- 実際のテスト件数（`402` と `500以上` のどちらが実測か）は fixture 外の情報であり、**未測定**。本レポートは「英語が source of truth」という前提のみに基づき、日本語側を drift と判定している。
- レイテンシ改善率の再現性、復旧成功率、承認フローの実装有無はいずれも **未測定**（fixture に記載がなく、コード・計測結果を参照していない）。
- 日本語版の drift が意図的か翻訳事故かの原因究明は **未実施**（fixture に履歴情報がない）。

## Assumptions

1. 英語ファイルが唯一の正であり、日本語側の差分は常に日本語側の問題として扱う（タスク指示に明記された前提）。
2. 比較対象は本文の主張のみとし、Markdown の記法差・改行位置（英語の 5-6 行目・7-8 行目は 1 箇条書きの折り返し）は差分として数えない。
3. `正式版` は GA を指す一般的な用法として解釈した。組織内で `正式版` を beta の別名として定義している場合、F1 の重大度は下がる（この社内定義の有無は fixture 外のため未確認）。
4. 「安全性に関わる記述」とは、実行時のリスク・保証範囲・人手承認に関する記述（EN の approval 行、非保証 caveat、計測の一般化禁止文）を指すものとした。
5. 本レポートはベンチマークの優劣に関する主張を一切含まない。指示どおり、この 1 タスクの成果物のみを作成した。
6. 指示に従い、`benchmarks/mission-vs-goal/` 配下は本プロンプトが名指しした 2 つの fixture と本出力ファイルのみを操作した。他のベンチマークメタデータは開いていない。

## Stop Condition

以下をすべて満たした時点で本タスクを終了する（現時点で全項目が満たされている）:

- [x] 成果物が `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280-r2/tail-bilingual-release-drift-claude_code_goal_command.md` に 1 ファイルのみ存在する。
- [x] 見出し Goal / Result / Evidence / Assumptions / Stop Condition を含む。
- [x] 英語・日本語の原文を引用した claim-by-claim parity table を含む（C0–C7 の 10 行）。
- [x] 各 divergence を overclaim / numeric drift / stage drift / omission に分類している。
- [x] 言い換えだけで等価な claim を棄却する rejected-candidates セクションを、棄却理由付きで含む（R1, R2）。
- [x] 確定 finding と棄却候補を明確に分離している。
- [x] commit / push / パッケージ導入 / ネットワークアクセスを行っていない。
