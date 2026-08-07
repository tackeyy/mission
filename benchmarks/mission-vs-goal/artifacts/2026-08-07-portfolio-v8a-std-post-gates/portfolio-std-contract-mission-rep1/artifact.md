# portfolio-std-contract — mission arm (rep1)

Task id: `portfolio-std-contract` / Category: `api-contract` / Arm: mission (profile: full) / Complexity: Standard

## Mission

client-py 実装を API 仕様 `api-spec.md`（source of truth）と突合し、**すべての breaking drift** を証拠付きで特定し、**仕様が許容する差分は許容条項を引用して非指摘（rejected candidates）として棄却**する監査 artifact を作成する。読む fixture は次の 2 ファイルのみ:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`

Validator 要件: drift table / breaking-drift section（引用証拠付き）/ rejected-candidates section。

## Plan

Standard iteration 1 のため inline plan（#339: mission-planner subagent 非起動）。

| # | ステップ | 依存 | 完了条件 |
|---|---|---|---|
| 1 | `mission-state.py init`（Standard, lease 取得） | — | init exit 0, route されない |
| 2 | 指定 fixture 2 ファイルを Read（並列） | 1 | 両ファイル全文取得 |
| 3 | spec の各条項 × client-py の各挙動を突合し、drift 候補を breaking / spec-permitted に分類 | 2 | 全候補（6 件）が分類済み・各判定に引用証拠 |
| 4 | artifact を validator 要件（drift table / breaking / rejected）＋ mission 見出し 8 種で作成 | 3 | 本ファイルが全見出しを含む |
| 5 | reviewer 2 名を単一メッセージ並列 spawn → `review-finalize` → `closeout` | 4 | composite >= 4.0, open_high == 0, `passes: true` |

## Execution

fixture 2 ファイルを読了し、client-py の記述 6 項目を spec と突合した。

### Drift table（全候補の分類）

| # | client-py の挙動（引用） | spec の該当条項（引用） | 判定 |
|---|---|---|---|
| D1 | POST /v2/transfers: "fires the request without an `Idempotency-Key` header" | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **Breaking** |
| D2 | "American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." | status enum は "one of: `pending`, `settled`, `cancelled`, `failed`"（"The `status` enum uses British spelling `cancelled`."） | **Breaking** |
| R1 | "Sends the `X-Sig` header exactly as specified." | Authentication: "Every request MUST carry the `X-Sig` header" | Rejected（準拠） |
| R2 | "Never retries POSTs." | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Rejected（準拠） |
| R3 | "Parses `expires_at` as epoch milliseconds." | "`expires_at` … epoch_ms (milliseconds since epoch, UTC)" / "always epoch_ms" | Rejected（準拠） |
| R4 | "Sends an `X-Trace-Id` header on every request for distributed tracing." | Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." | Rejected（許容） |

### Breaking drifts（引用証拠付き）

**D1 — `Idempotency-Key` ヘッダー欠落（POST /v2/transfers）**

- spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."（api-spec.md, POST /v2/transfers 節）
- client-py: "fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."（client-py.md）
- 影響: REQUIRED ヘッダーの欠落は全リクエストで契約違反。single transfer path のみ未対応で、bulk endpoint 用の生成は POST /v2/transfers を満たさない。

**D2 — status enum 値の綴りドリフト: `canceled` vs `cancelled`**

- spec: status は "one of: `pending`, `settled`, `cancelled`, `failed`"、かつ "The `status` enum uses British spelling `cancelled`."（api-spec.md, GET /v2/transfers/{id} 節）
- client-py: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."（client-py.md）
- 影響: wire 値 `cancelled` は client の `canceled` と exact string equality で不一致となり、cancelled 状態の transfer がマッピング不能（unknown state）になる。`pending` / `settled` / `failed` は両綴り一致のため影響なし。

### Rejected candidates（許容条項の引用付き）

- **R1 `X-Sig`**: "Sends the `X-Sig` header exactly as specified." — spec の "Every request MUST carry the `X-Sig` header" を満たす。準拠であり非指摘。なお spec は "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." のため casing 差も違反にならない。
- **R2 リトライなし**: "Never retries POSTs." — spec は "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header"。リトライしないことは MUST NOT を満たす保守的挙動で違反ではない（D1 の欠落とは独立の論点）。
- **R3 `expires_at` の epoch_ms 解釈**: "Parses `expires_at` as epoch milliseconds." — spec の "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)" と一致。"treating it as seconds shifts expiry by three orders of magnitude" の罠に該当しない。
- **R4 `X-Trace-Id` 送信**: Extension clause (section 7) が明示許容: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."（api-spec.md）— tracing header はまさに例示されたケース。

## Review

Iteration 1 で独立 reviewer 2 名を単一メッセージで並列起動（reviewer window A=2026-08-07T09:19:20Z..09:20:24Z / B=09:19:20Z..09:20:48Z、`parallel_execution: true` を aggregate が確認）。`mission-review/1` JSON 2 件を `review-finalize` で strict 検証・集計。

- Reviewer A（観点: 正確性・網羅性）: mission_achievement 5.0 / accuracy 5.0 / completeness 5.0 / usability 4.9 — findings 0 件
- Reviewer B（観点: 証拠品質・validator 準拠）: mission_achievement 5.0 / accuracy 4.8 / completeness 5.0 / usability 4.8 — findings: Low 1 件（B-1: drift table R4 の spec 引用が "…" 省略 → 本 artifact で全文引用に修正済み）
- High: 0 / Medium: 0 / Low: 1（修正反映済み。Low のため差分再レビュー不要 — M6 は Medium 以上が対象）

## Score

review-finalize（`push-score --scoring-json`）の tool-computed 実測値（2026-08-07T09:22:08Z 記録）:

- iteration: 1 / composite_score: **4.92** / threshold: 4.0 / min_item: 4.8 (>= 3.5)
- items: mission_achievement 5.0 / accuracy 4.9 / completeness 5.0 / usability 4.8 / review_agreement 5.0
- max_agreement_delta: 0.2 (<= 1.5) / open_high: 0 / findings_evidence_path: `.mission-state/archive/iter-1-7baf6690-reviews.json`

## Stop Decision

early-stop 条件成立（iteration 1 で composite 4.92 >= threshold 4.0 かつ open_high == 0、Medium 3 件以上の続行条件に非該当）。`closeout`（mark-passes → next）exit 0・`passes: true` を確認して停止。max-iter 2 のうち 1 iteration で完了。

## Evidence

- 読了 fixture: `api-spec.md`（33 行）・`client-py.md`（13 行）の 2 ファイルのみ。他の `benchmarks/mission-vs-goal/` 配下は不読（ベンチ規約遵守）。
- 全 6 候補の判定は上記 Drift table に fixture 逐語引用付きで記載（D1/D2 = breaking、R1–R4 = rejected）。
- mission state: `.mission-state/sessions/cc-502dd22b-67b2-499d-8f65-08b83dfdd9bf.json`（mission_id `7baf6690fc264ddc`、lease fenced、init の permission_preflight: passed）。
- review 生データ: `.mission-state/reviews/iter1-A.json` / `iter1-B.json`（verbatim 保存）、集計 archive: `.mission-state/archive/iter-1-7baf6690-reviews.json` / `iter-1-7baf6690-scoring.json`。
- closeout 実測: `mark-passes` → `{"passes": true, "forced": false}`、`next` → `next_action: report-complete` / `loop_active: false` / `phase: done`（2026-08-07 実行、exit 0）。
- specialist checkpoint: `specialists recommend --record-state` 記録済み（task_profile.primary: backend / risk: medium）。外部 specialist は未使用（ベンチ制約下の監査タスクのため orchestrator + reviewer 2 名で完結、accounting_required: false を CLI が確認）。
- 未計測事項: 実 HTTP トラフィックでの再現・client-py の実コードは fixture に含まれず未検証（notes 記載の挙動を実装挙動と仮定 — Assumptions 参照）。ベンチマーク優劣は本 artifact の主張範囲外。

## Assumptions

- client-py.md は実装の自己申告 notes であり、記載挙動＝実装挙動と仮定（コード実体は fixture に非含有）。
- 「Never retries POSTs」は D1 と独立に評価し、spec の MUST NOT retry 条件を満たす準拠挙動と判定。
- ネットワーク禁止のベンチ制約により mission local authoring sync は非実行。repo 内 `scripts/mission-state.py` を正典 CLI として使用。
- 詳細は `.mission-state/sessions/cc-502dd22b-67b2-499d-8f65-08b83dfdd9bf-assumptions.md`。
