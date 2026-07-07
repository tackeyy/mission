# tail-bilingual-release-drift — Mission Artifact

Arm: **mission** · Task id: `tail-bilingual-release-drift` · Task category: documentation
Mission complexity (as instructed): **Complex** · `mission_id`: `16e471245177ee61` · `session_id`: `cc-8f50b239-2e5e-4703-a5ce-1c99fdb58c27`

---

## Mission

Compare `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md` (source of truth)
against `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md` (Japanese draft)
claim by claim. Find every place where the Japanese copy is stronger than, numerically different from, or
missing a safety-relevant statement of the English evidence, quoting both sides. Reworded-but-equivalent
claims must be rejected as non-findings with reasoning.

Validator (as given in the task prompt): the artifact must include a claim-by-claim parity table quoting
English and Japanese, classify each divergence as one of `overclaim` / `numeric drift` / `stage drift` /
`omission`, and include a rejected-candidates section for reworded-but-equivalent claims.

Constraints applied during this run: no commit/push/network/package-install; edits scoped to this artifact
file and `.mission-state/`; only the two named fixture files were read under `benchmarks/mission-vs-goal/`
(no other file in that tree was opened, listed, or grepped).

State machinery used: `scripts/mission-state.py init/set/resume/next/aggregate-reviews/push-score` (this
repository's own mission tooling — this benchmark run exercises the tool on itself). `max_iter` set to `2`
per the task instruction (`--max-iter 2`), overriding the skill default of `3`.

---

## Plan

Phase 2 (`mission-state.py next` → `run-planner`) called for the `mission-planner` sub-skill. Invoking it via
the `Skill` tool (`Skill(skill="mission-planner", ...)`) returned only a short `Execute skill: mission-planner`
marker with no plan content — the sub-skill's context fork did not produce visible output in this environment.
This is recorded as a real operational limitation, not omitted (see Evidence and Assumptions). Per the
degradation pattern the `/mission` skill itself documents for Codex ("該当 skill 指示を同一コンテキストで
適用"), the orchestrator read `skills/mission-planner/SKILL.md` directly and produced the plan below by
following its documented role/output format in-context.

### 全体方針

EN の導入文＋6 bullet と JA の導入文＋5 bullet を突合し、各ペアを overclaim / numeric drift / stage drift /
omission / non-finding (reworded-equivalent) に分類する。安全性に直結する2箇所（復旧保証の文言、不可逆操作の
承認要件）を最優先で精査する。

### ステップ

| # | アクション | 入力 | 出力 | 完了条件 | 依存 | 並列可 |
|---|---|---|---|---|---|---|
| 1 | EN/JA の claim 単位（導入文＋各 bullet）を突合表にする | 読了済み fixture 全文 | 未分類の対応表 | 全 EN claim（7件）にJA対応の有無が判定済み | - | - |
| 2 | 各ペアを4分類 + non-finding に分類 | ステップ1 | 分類済み finding 候補 | 各行に分類ラベルと逐語引用の根拠がある | 1 | - |
| 3 | 言い換え等価候補を rejected-candidates として却下理由付きで分離 | ステップ2 | rejected-candidates 草稿 | 却下理由が「意味内容が一致」と明記されている | 2 | 4と並列 |
| 4 | 安全性関連 finding（復旧保証・不可逆操作承認）を最優先で再精査 | ステップ2 | 確定 finding（安全性クリティカル） | EN の hedge 文言の欠落/強化が明示されている | 2 | 3と並列 |
| 5 | 8見出し構成で成果物ドラフトを作成 | ステップ2-4 | 本ファイルの Execution 節 | 全8見出みが存在し parity table が EN/JA 引用+分類を含む | 2,3,4 | - |
| 6 | Reviewer 3名（観点A/B/C）+ 観点D による検証、独立ブラインド突合を追加 | ステップ5 | mission-review/1 JSON ×3 + 独立 Agent 検証 | 各 reviewer が fixture 原文を突合して採点 | 5 | - |
| 7 | aggregate-reviews → push-score → 合否判定 | ステップ6 | score_history 更新 | mission-state.py のみで判定、手計算なし | 6 | - |

### リスク・対策

- リスク1: 「18%以上」のような助詞レベルの数値ドリフトを見逃す → 対策: EN/JA を逐語で並べ、数量修飾語（以上/以下等）を独立に確認する
- リスク2: 言い換え等価表現を誤って finding にする（false positive） → 対策: 「安全性・数値・stage に実質的影響があるか」で判定し、影響しないものは rejected-candidates へ
- リスク3: 安全性関連の欠落（不可逆操作の承認要件）の見落とし → 対策: EN/JA の bullet 数を数え、対応が取れない bullet を機械的に洗い出す
- リスク4（実際に発生）: Skill tool 経由の sub-skill fork が期待通り動作しない → 対策: SKILL.md を直接読み、指示に従ってインライン実行にフォールバック。加えて独立性を補うため Agent tool でブラインド突合を1件追加した

### 検証方法

- fixture からの直接引用と成果物内の引用の文字列一致
- 8見出しの存在確認（validator 要件）
- Reviewer 間の `review_agreement` / `max_agreement_delta` が閾値以内か
- 独立ブラインド Agent 検証との finding 一致・不一致の確認

---

## Execution

### 方法論

- EN bullet 内に「数値主張」と「限定注記（scope-limiting caveat）」など複数の独立した claim が含まれる場合、
  parity table では行を分割する（例: 行1と行1bはどちらも EN bullet1 由来）。これは finding の水増しではなく、
  claim 単位比較の粒度をそろえるための分割である。
- EN の bullet 数（6）と JA の bullet 数（5）を数え、対応の取れない bullet を機械的に検出した（4番目の
  bullet が JA に存在しない）。
- 分類は仕事上の便宜として `overclaim` / `numeric drift` / `stage drift` / `omission` の4種のうち
  最も当てはまる1つを主分類とし、二次的な性質（例: 数値ドリフトが結果的に誇張＝overclaim的性質も持つ）は
  説明欄に併記した。
- Severity（High/Medium）ラベルは validator が要求する分類ではなく、分析上の付加情報である（Assumptions参照）。

### Claim-by-claim parity table

| # | EN (source of truth) | JA (draft) | 分類 | 説明・安全性への影響 |
|---|---|---|---|---|
| 0 | "We are announcing the public beta of our task runner." | "タスクランナーの正式版を発表します。" | **stage drift** | EN は明示的に "public beta"（公開ベータ）と述べているが、JA の「正式版」は日本語で一般に「official / GA release」を意味し、ベータより高い成熟度・サポート水準を読者に示唆する。安全性関連（読者のリスク評価・利用判断に直結）。Severity: High |
| 1 | "In one internal pilot, median latency improved by 18%." | "社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。" | **numeric drift** | EN は「18%」という単一の確定値。JA は「18%以上」＝下限保証のニュアンスへ変化しており、単一計測の実測値があたかも最低保証ラインであるかのように読める。Severity: Medium |
| 1b | "This is a single controlled measurement, not a general performance claim." | （対応文なし） | **omission** | 「単一の管理された計測であり、一般的な性能主張ではない」という限定注記がJAに一切存在しない。行1のnumeric driftと組み合わさり、一般化された性能保証であるかのように読める複合的リスクを生む。加えて EN 冒頭の "In **one** internal pilot" という単数の強調も JA "社内パイロットにおいて" では訳出されておらず、一般化リスクを補強している。Severity: High |
| 2 | "Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode." | "実行が中断しても、必ず自動で復旧します。" | **overclaim** | EN は「テストシナリオでは成功したが、全ての障害モードで保証されない」と明記する hedge 文。JA は「必ず自動で復旧します」＝無条件・自動的な復旧保証に書き換えられており、安全上の非保証表現が完全に消えている。さらに EN が述べていない「自動で（automatically）」という性質まで追加されている。Severity: High（本件は最も安全性への影響が大きい finding の一つ） |
| 3 | "The release is verified by 402 automated tests." | "本リリースは 500以上の自動テストで検証されています。" | **numeric drift** | 402（確定値）→ 500以上（実数より大きい下限付き値）。検証範囲を実態より誇張して伝えている。Severity: Medium |
| 4 | "Irreversible actions require manual approval before they run." | （bullet自体が対応なし） | **omission** | 不可逆操作の実行前に手動承認が必要という安全機構の記述が JA 草稿に一切存在しない。EN 6 bullet 中この1文だけ JA に対応する bullet が無い（JA は 5 bullet のみ）。Severity: High（本件は最も安全性への影響が大きい finding の一つ） |
| 5 | "It works with existing CLI workflows without changes." | "既存の CLI ワークフローにそのまま組み込めます。" | non-finding（rejected candidates 参照） | — |
| 6 | "No telemetry is collected." | "テレメトリーは収集しません。" | non-finding（rejected candidates 参照） | — |

**確定 finding 件数: 6件**（stage drift ×1、numeric drift ×2、omission ×2、overclaim ×1 — validator が要求する4分類を全てカバー）。

### Rejected candidates（言い換えだが等価、non-finding）

| # | EN | JA | 却下理由 |
|---|---|---|---|
| R1 | "It works with existing CLI workflows without changes." | "既存の CLI ワークフローにそのまま組み込めます。" | 「変更なしで動く」と「そのまま組み込める」は同一の主張（既存 CLI ワークフローに変更を要さず統合できる）。数値・stage・安全性のいずれにも実質的な差がなく、文体差のみの言い換え。 |
| R2 | "No telemetry is collected." | "テレメトリーは収集しません。" | 直訳に近い等価表現。主張内容・強度に差がない。 |
| R3（比較対象外、参考） | "# Release announcement — draft (English, source of truth)" | "# リリース告知 — ドラフト（日本語訳・レビュー前）" | これは製品に関する claim ではなく、ドキュメント自体のメタ情報（ドラフト状態・翻訳レビュー前であることの明記）。claim-by-claim 比較の対象外として除外した。むしろ JA 側が自ら「レビュー前」と明記している点は、本タスクで検出した6件のドリフトが「未レビューの翻訳ドラフトに実際に存在する」という前提と整合する。 |

---

## Review

### 実施方法（実際に起きたこと・限界の開示）

`/mission` 標準フローでは Phase 4 の Reviewer 呼び出しは `Skill(skill="mission-reviewer", ...)` を観点別に
複数回、単一メッセージ内で並列実行する設計である。本セッションで実際に試したところ、`mission-planner` 同様
`mission-reviewer` の Skill tool 呼び出しも context fork が実行内容を返さず、`Execute skill: ...` という
短いマーカーのみが返った。これは既知の制約として Plan 節と Evidence 節に記録済み。

このため、以下の2段構成で検証の独立性を補った:

1. **Orchestrator によるインライン3観点採点**（`skills/mission-reviewer/SKILL.md` の採点ルール・JSON契約
   に厳密に従い、観点A/B/C それぞれ独立に4軸採点＋Issues抽出を実施。JSON は
   `.mission-state/reviews/iter1-{a,b,c}.json` に保存し、`mission-state.py aggregate-reviews` で
   決定論的に集計）。
2. **独立ブラインド検証（Agent tool）**: 上記のドラフトを一切見せず、同じ2つの fixture ファイルのみを
   渡して独立にゼロから claim-by-claim 比較をやり直させた（maker-checker 分離）。結果は本節末尾に記載。

#### 観点A（ミッション達成度）— 採点

| 項目 | スコア | 根拠 |
|---|---|---|
| ミッション達成度 | 4.5/5 | EN 7 claim 全件をJAと突合し、6件のconfirmed finding（4分類全カバー）+ rejected-candidates 3件を提示。8見出しも全て存在し、validator の3要件を満たす。 |
| 正確性 | 4.5/5 | 引用は Read tool 出力との照合のみ（機械的 diff ログなし）。 |
| 完成度 | 4.0/5 | severity ラベルが validator 非要求の追加情報である点、Skill fork 不具合の記録が必要な点を Issue 化。 |
| 実用性 | 4.5/5 | 翻訳レビュー担当者への差し戻しリストとして即使用可能。 |

Issues: A-1 (Low, completeness) — severity ラベルは analyst 追加情報。A-2 (Low, accuracy) — 機械的diffログ未取得。

#### 観点B（正確性・論理整合性）— 採点

| 項目 | スコア | 根拠 |
|---|---|---|
| ミッション達成度 | 4.5/5 | 分類ロジック（stage drift/numeric drift/overclaim/omission の割当）が EN-source-of-truth 原則と整合。 |
| 正確性 | 5.0/5 | parity table の全 EN/JA 引用を fixture 原文と1字ずつ再照合し、誤引用・誤分類なし。 |
| 完成度 | 4.5/5 | 複合 claim（行1/1b）の分割方針が表の前に明示されていない点をIssue化。 |
| 実用性 | 4.5/5 | 分類根拠が明確で追加調査なしに利用可能。 |

Issues: B-1 (Low, completeness) — 複合claimの分割方針の事前説明不足。

#### 観点C（実用性・抜け漏れ）— 採点

| 項目 | スコア | 根拠 |
|---|---|---|
| ミッション達成度 | 4.5/5 | bullet数の突合（EN6/JA5）による機械的完全性チェックが有効に機能。 |
| 正確性 | 4.5/5 | 独立再確認でも既存6findingを超える見落としは検出されず。 |
| 完成度 | 4.5/5 | EN "one" の単数強調の欠落が1bの補強証拠として言及されていない点をIssue化。 |
| 実用性 | 5.0/5 | 追加修正なしで翻訳差し戻しチェックリストとして利用可能。 |

Issues: C-1 (Low, completeness) — "one" の欠落を1bの補強根拠として明記すべき。

#### 観点D（計画指示明瞭度、採点対象外）

| # | 種別 | 内容 | 推奨対応（Planner向け） |
|---|---|---|---|
| 1 | 不明瞭点 | Skill tool 経由の sub-skill fork（`user-invocable: false` + `context: fork`）が短いマーカーのみ返す挙動が `/mission` 本体ドキュメントに明記されていない | `refs/react-loop-details.md` にフォールバック手順（SKILL.md直読 + インライン実行）を明記する |
| 2 | 裁量補完 | 上記の代替として Codex 向けの degradation パターン（同一コンテキストでの skill 指示適用）を Claude Code 環境にも準用した | 次回は Claude Code 側の既知挙動として明文化 |
| 3 | 再試行 | 該当なし（同一呼び出しの単純リトライはせず、1回の観測結果で方針転換） | - |

### 独立ブラインド検証（Agent tool、maker-checker）

Agent tool（`general-purpose`、read-only、上記ドラフトは一切見せず fixture 2ファイルのパスのみ渡した）で
独立にゼロから claim-by-claim 比較をやり直させた。結果（agentId: 内部管理のため非開示、`duration_ms=56725`,
`tool_uses=2`）:

| Claim (agent番号) | Agent の分類 | 本ドラフトの対応行 | 一致状況 |
|---|---|---|---|
| 0 (導入文 beta/正式版) | stage drift | 行0 (stage drift) | **完全一致** |
| 1 (18% latency) | overclaim（18%以上への強化＋caveat欠落を一体で評価） | 行1 (numeric drift) + 行1b (omission) | **事実は一致、分類粒度が異なる**（下記参照） |
| 2 (resumption) | overclaim | 行2 (overclaim) | **完全一致**（"most extreme inversion" と明記、本ドラフトの Severity: High 判定と整合） |
| 3 (402→500以上) | numeric drift | 行3 (numeric drift) | **完全一致** |
| 4 (irreversible actions) | omission（"the only explicit safety mechanism ... entirely absent"） | 行4 (omission) | **完全一致**（agent はこれを "most critical" と独立に判定。本ドラフトも Severity: High） |
| 5 (CLI workflows) | no drift (equivalent) | Rejected candidates R1 | **完全一致** |
| 6 (telemetry) | no drift (equivalent) | Rejected candidates R2 | **完全一致** |

**discrepancy の解消**: agent は EN bullet1（18%数値＋"single controlled measurement" caveat）を単一の
`overclaim` として報告した一方、本ドラフトは数値変化（行1: numeric drift）と caveat 消失（行1b: omission）を
別行に分割している。これは事実認識の相違ではなく、複合 claim の集計単位の違いである（agent の報告文中でも
"Two separate issues coexist" と明記した上で1つにまとめている）。本ドラフトは validator が「classify each
divergence」と divergence 単位での分類を求めていること、また numeric drift と omission は是正すべきアクション
が異なる（前者は数値修正、後者は文の追加）ため、実務上は分離した方が翻訳レビュー担当者にとって actionable
であるという理由で分割を維持する。どちらの粒度でも **安全性への実質的懸念（EN原文にある反証・限定注記が
JAで失われている事実）は完全に一致**しており、finding の見落とし・誤検出は独立検証で確認されなかった。

**結論**: 独立ブラインド検証は本ドラフトの6件の finding のうち5件で分類・対象が完全一致、残り1件は分類粒度の
差のみ（事実は一致）。新規の見落としは検出されず、rejected candidates（R1/R2相当）も完全一致した。iteration 1
のまま `mark-passes` を実行してよいと判断する。

---

## Score

`mission-state.py aggregate-reviews` → `push-score` の実行結果（`.mission-state/reviews/iter1-scoring.json`
および `.mission-state/archive/iter-1-16e47124-scoring.json` に保存、手計算なし）:

| 指標 | 値 |
|---|---|
| iteration | 1 |
| mission_achievement | 4.5 |
| accuracy | 4.67 |
| completeness | 4.33 |
| usability | 4.67 |
| composite | **4.54** |
| min_item | 4.33 |
| open_high | 0 |
| review_agreement | 5.0 |
| agreement_detail (delta) | mission_achievement 0.0 / accuracy 0.5 / completeness 0.5 / usability 0.5 |
| threshold | 4.0 |

---

## Stop Decision

終了判定（`/mission` 本体の合否式）:

```
passes = findings_evidence_path exists            → true (.mission-state/archive/iter-1-16e47124-reviews.json)
  AND evidence_high_count == open_high             → true (0 == 0)
  AND max_agreement_delta <= 1.5                   → true (max delta 0.5)
  AND composite_score >= threshold                 → true (4.54 >= 4.0)
  AND min(scored_items) >= 3.5                     → true (4.33 >= 3.5, 全個別軸スコアも最低4.0)
  AND open_high == 0                               → true
```

全ゲートを満たすため、iteration 1 で `mark-passes` を実行する条件が揃っている。本ラン固有の追加ゲートとして
設定していた maker-checker 独立検証（Agent tool のブラインド突合）は完了し、6件の finding のうち5件が
完全一致、残り1件（EN bullet1の複合 claim）は分類粒度の違いのみで事実認識は一致、rejected candidates も
完全一致という結果を得た（Review節参照）。新規の見落としは検出されなかったため、iteration 1 のまま
`mark-passes` を実行する。

---

## Evidence

- Fixture (EN, quoted verbatim above): `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
- Fixture (JA, quoted verbatim above): `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`
- Mission state init: `mission_id=16e471245177ee61`, `session_id=cc-8f50b239-2e5e-4703-a5ce-1c99fdb58c27`,
  `complexity=Complex`, `reviewer_count=3`, `max_iter=2` (state file:
  `.mission-state/sessions/cc-8f50b239-2e5e-4703-a5ce-1c99fdb58c27.json`)
- Assumptions log: `.mission-state/sessions/cc-8f50b239-2e5e-4703-a5ce-1c99fdb58c27-assumptions.md`
- Specialists recommend output: `sc-document-reviewer` selected (`task_profile.primary=documentation`,
  score 0.552); `sc-report-writer` / `sc-competitor-intel` / `sc-market-researcher` candidates but not
  selected; `documentation-provider` unavailable (not installed). No specialist was invoked as a judge —
  the registry's own doc-review specialist was not additionally invoked beyond the mission-reviewer rubric,
  since this task's evidence requirement (verbatim bilingual quote matching) is directly verifiable without
  an external specialist opinion; this decision is recorded here rather than silently dropped.
- Reviewer JSONs (mission-review/1): `.mission-state/reviews/iter1-a.json`, `iter1-b.json`, `iter1-c.json`
- Aggregated scoring JSON: `.mission-state/reviews/iter1-scoring.json`
- Archived findings evidence: `.mission-state/archive/iter-1-16e47124-reviews.json`
- Archived scoring evidence: `.mission-state/archive/iter-1-16e47124-scoring.json`
- Known operational limitation (disclosed, not hidden): `Skill(skill="mission-planner", ...)` and the
  equivalent `mission-reviewer` invocation returned only a short `Execute skill: <name>` marker with no
  forked-context content in this environment; the orchestrator fell back to reading each sub-skill's
  `SKILL.md` directly and following its documented role/output contract in-line, and added one independent
  blind `Agent` tool check to preserve maker-checker separation for the scoring-relevant review.

---

## Assumptions

See full log in `.mission-state/sessions/cc-8f50b239-2e5e-4703-a5ce-1c99fdb58c27-assumptions.md`. Summary of
load-bearing assumptions for this artifact:

1. **Direction of comparison**: only EN→JA divergence counts as a finding (EN is source of truth per task
   prompt); a JA statement being *weaker* than EN in a non-safety-relevant way was not sought out as a
   separate finding category, since the task only asked for JA being stronger/numerically different/stage-
   drifted/missing relative to EN.
2. **Severity labels (High/Medium) are an analyst addition**, not a validator-required classification. The
   validator only requires the 4-way type classification (overclaim/numeric drift/stage drift/omission);
   severity is supplementary context for prioritizing translation fixes, flagged as such per Reviewer A's
   Issue A-1.
3. **Document header/subtitle lines are out of scope** for claim-by-claim comparison — they describe the
   document's own draft/review status, not a product claim (see Rejected candidates R3).
4. **Compound-bullet splitting convention**: where one EN bullet contains two distinct claims (a number and
   a scope-limiting caveat), the parity table splits it into two rows (e.g. 1 / 1b) rather than merging them,
   so each claim can be independently verified against its own JA counterpart (or lack thereof).
5. **`mission_id`/`session_id` and `max_iter=2`** were set via `mission-state.py init` / `set` at the start of
   this run and are unchanged for the duration of this artifact.
6. **Specialist non-invocation is a recorded decision, not an omission**: `sc-document-reviewer` was
   registry-selected but not invoked as an additional judge; per this project's own `AGENTS.md`/`CLAUDE.md`
   guardrail ("keep external specialists as evidence providers, not final judges"), and because the task's
   evidence bar (verbatim quote matching) does not require external specialist input, this was a scoped
   decision rather than a silent gap.
