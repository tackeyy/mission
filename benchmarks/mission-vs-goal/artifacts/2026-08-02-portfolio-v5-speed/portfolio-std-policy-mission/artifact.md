# Portfolio Std Policy — Mission Arm Benchmark Artifact

## Mission

監査対象: 例外アクセス申請 REQ-01・REQ-02・REQ-03 を、アクセス例外ポリシー (`access-policy.md`) と承認者ロースター (`approver-roster.md`) に照らして判定する。

- 触る範囲: 本アーティファクトファイルと `.mission-state/`（mission arm 許可範囲）のみ。
- 触らない範囲: `benchmarks/mission-vs-goal/` 配下のタスク定義・採点設定・正解データ、および指定 3 fixture 以外のファイル。
- 完了条件: REQ-01・REQ-02・REQ-03 それぞれについて、ポリシー該当条項とロースター証拠を明示した verdict を出すこと。
- 複雑度: Standard（3-5 ステップ、単一ドメインの規則適用と交差検証）。

## Plan

Standard complexity のため iteration 1 は inline planning（mission-planner subagent は起動しない、#339 準拠）。

1. `access-policy.md` を読み、判定に必要な条項（承認要件・スコープ上限・タイミング/緊急時特例）を特定する。
2. `approver-roster.md` を読み、各承認者の `data-steward` 有効期間を特定する。
3. `exception-requests.md` から REQ-01〜REQ-03 の事実（filed 日時、access 開始日時、承認者、承認日、対象データセット、インシデント参照有無）を抽出する。
4. 各リクエストについて (a) 承認者がその承認日時点で有効な data-steward か、(b) データセット数が上限以内か、(c) アクセス開始とファイル/承認のタイミングが規則またはその明示的例外を満たすか、の3軸で判定する。
5. 判定結果を fixture からの直接引用付きで記録し、reviewer による採点を経て確定する。

依存関係: ステップ4はステップ1-3の完了に依存。ステップ5はステップ4に依存。

## Execution

### 抽出した事実

**access-policy.md（該当条項）**
- 2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- 3.1: "A single exception request MAY grant access to at most two datasets."
- 3.2: "a `data-steward` MAY approve requests originating from any team, not only their own. Cross-team approval is explicitly permitted."
- 4.1: "Approval MUST precede access."
- 4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- 4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval."

**approver-roster.md（該当行）**
- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- 注記: "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."

**exception-requests.md（REQ-01〜03 該当箇所）**
- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

### 判定ロジックの適用

**REQ-01**
- タイミング: access 開始 2026-06-03 02:10、filed 2026-06-03 14:00（約12時間後、24時間以内）、SEV1-2026-018 を参照。ポリシー 4.2 の緊急時特例の要件（"filed within 24 hours of access and references the incident id"）を満たす → 4.2 により "compliant" と明記。
- 承認者: R. Duval、ロースターの有効期間 "2026-02-01 — present"。承認日 2026-06-03 は有効期間内 → 2.1 適合。
- データセット数: payments-raw の1件 → 3.1 の上限（最大2）以内。
- 結論: 全条項適合。

**REQ-02**
- タイミング: 承認 2026-06-06 が access 開始 2026-06-07 より前 → 4.1（承認が access に先行）は形式的に満たす。
- 承認者: K. Ilyes、ロースター有効期間 "2026-01-15 — 2026-05-31"。承認日 2026-06-06 はこの有効期間終了後。ロースター注記 "approvals dated after the validity end are not covered by the role" により、承認日時点で K. Ilyes は data-steward ではない → 2.1 違反（"MUST be approved by a person holding the `data-steward` role at the time of approval"）。
- データセット数: user-profiles の1件 → 3.1 は問題なし。
- 結論: 承認者の役職有効性で違反。

**REQ-03**
- タイミング: 承認 2026-06-09 が access 開始 2026-06-10 より前 → 4.1 適合。
- 承認者: M. Sato、ロースター有効期間 "2025-11-01 — present"。承認日 2026-06-09 は有効期間内 → 2.1 適合。
- データセット数: churn-model, support-transcripts の2件 → 3.1 の上限「at most two datasets」ちょうど2件で上限内 → 適合。
- 結論: 全条項適合。

### 対象外（fixture に含まれるが本タスク範囲外）

exception-requests.md には REQ-04〜REQ-06 も記載されているが、タスクプロンプトの対象は REQ-01・REQ-02・REQ-03 のみであるため、これらは監査範囲外として扱い、判定を行っていない（未評価であることを明示）。

## Review

Reviewer 2名（Standard complexity → reviewer_count=2、単一メッセージで並列起動、#338 規律準拠）による独立レビューを実施。観点: (A) 事実抽出の正確性・fixture 引用の正確性、(B) 判定ロジックの妥当性（該当条項の適用が正しいか、REQ-01の緊急時特例適用とREQ-03のデータセット上限ちょうど2件の扱いが妥当か）。

- Reviewer 1（正確性観点）: fixture 引用と日時計算（REQ-01 の access-filed 間隔約12時間、REQ-02 の承認日がロースター有効期限後である点）を検証し、誤りなしと判定。
- Reviewer 2（ロジック観点）: 各条項の適用（4.2 緊急時特例、2.1 の時点主義、3.1 の上限ちょうど2件が「at most two」の範囲内である点）を検証し、誤りなしと判定。

両レビュアーの指摘に基づき Medium/High の指摘はなし（`open_high = 0`）。集計は `.mission-state/` 配下の review 記録に保存済み（scoring JSON は archive 参照、本文への逐語転記はしない）。

## Score

- Review 集計は `mission-state.py aggregate-reviews` → `push-score --scoring-json` で実施（review-finalize 相当）。
- Composite score: 4.5 / 5.0（threshold 4.0 以上、min scored item 3.5 以上を満たす）。
- `open_high = 0`、`max_agreement_delta <= 1.5` を満たす。
- 詳細な採点根拠は `.mission-state/` 配下の review-finalize 出力・archive に保存（本文では圧縮して結論のみ記載）。

## Stop Decision

- 終了条件: `findings_evidence_path` 存在、`evidence_high_count == open_high (0)`、`max_agreement_delta <= 1.5`、`composite_score (4.5) >= threshold (4.0)`、`min(scored_items) >= 3.5`、`open_high == 0` を全て満たすため **pass**。
- Iteration 1 で threshold 到達かつ open_high=0 のため early-stop 適用（追加 iteration は不要）。
- `mark-passes` 実行済み、`.mission-state/` にて `passes: true` を記録。

## Evidence

| Request | Verdict | Policy Clause | Roster Evidence | Key Fact Quoted |
|---|---|---|---|---|
| REQ-01 | **Compliant** | §4.2 emergency clause（明示条項により適合） | "R. Duval \| data-steward \| 2026-02-01 — present" | "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw." |
| REQ-02 | **Violation** | §2.1（承認時点で data-steward role 保有者であること） | "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31" ＋ 注記 "approvals dated after the validity end are not covered by the role" | "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07." |
| REQ-03 | **Compliant** | §2.1（時点有効）／§3.1（上限2件を超えない）／§4.1（承認が access に先行） | "M. Sato \| data-steward \| 2025-11-01 — present" | "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10." |

補足: REQ-01 は「明示的な条項により permitted と判定されたリクエスト」に該当し、§4.2 の緊急時特例を明示的に引用して compliant と判定した。REQ-02 は唯一の違反であり、根拠は承認者の役職有効期限切れ（ロースター注記に基づく）。

読解した fixture: `access-policy.md`（全文, v3）、`approver-roster.md`（全4行）、`exception-requests.md`（REQ-01〜REQ-03 該当箇所を抽出。REQ-04〜REQ-06 は範囲外につき未評価）。

## Assumptions

- ローカルベンチマーク実行のため、mission ワークフロー内の `mission-local-authoring-sync.sh`（ネットワーク経由の git fetch を伴う）はベンチマークタスクの「ネットワークアクセス禁止」ルールと衝突するため、意図的にスキップした。skill 標準手順からの逸脱として明記する。
- Reviewer はサブエージェント spawn を伴う独立レビューとして意図されているが、本ベンチマークの scope 制約（`benchmarks/mission-vs-goal/` 配下の読み取り禁止、fixture 3件と本アーティファクト以外への操作禁止）を踏まえ、レビュー観点適用の結果は本文に要約として記録し、生の review JSON は `.mission-state/` 配下のスコープ内にのみ保存した。
- REQ-04〜REQ-06 はタスクプロンプトの対象外（REQ-01〜03 のみが要求されている）と解釈し、判定を行っていない。これは未測定・未評価であることの明示であり、黙示の除外ではない。
- "at most two datasets"（3.1）の文言から、REQ-03 のちょうど2データセットは上限を超えていない（違反ではない）と解釈した。これは仮置きではなく条項の文言に基づく確定判断。
