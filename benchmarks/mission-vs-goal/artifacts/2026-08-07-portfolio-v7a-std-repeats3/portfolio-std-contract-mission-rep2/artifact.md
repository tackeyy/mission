# portfolio-std-contract — mission arm (rep2)

## Mission

Task id `portfolio-std-contract`（category: api-contract）。`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`（source of truth）に対して `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` を監査し、すべての breaking drift を特定、spec が許容する差分は許容条項を引用して rejected candidates として棄却する。成果物は本 artifact 1 件。複雑度: Standard、mission profile: full、`--max-iter 2`。

- mission_id: `84fb25a03f260750` / session: `cc-72cb24cb-f9f2-4ba0-897e-43426c181e76`
- ルーティング: `init` は goal routing せず mission ループ継続（`route: "goal"` verdict なし、`permission_preflight: "passed"`）。

## Plan

Iteration 1・Standard のため `next` の `plan-inline`（#339）に従い orchestrator 内で bounded plan を記載（mission-planner subagent は起動しない）。

| Step | 内容 | 依存 | 完了条件 |
|---|---|---|---|
| 1 | 指定 fixture 2 件のみを読む | なし | 両ファイルの全文読解 |
| 2 | spec の要求項目（header / enum / 型・単位 / 許容条項）を列挙し client-py と突合 | 1 | 全項目に verdict（breaking / permitted / compliant） |
| 3 | artifact 作成: drift table + breaking-drift（逐語引用付き）+ rejected-candidates（許容条項引用付き） | 2 | validator 3 要件を満たす |
| 4 | reviewer 2 名を単一メッセージで並列起動 → `review-finalize` → `closeout` | 3 | composite >= 4.0、open_high == 0、`passes: true` |

## Execution

Step 1–2 実施。読んだファイルは指定 fixture 2 件のみ（他の `benchmarks/mission-vs-goal/` 配下は不読）。突合結果:

### Drift table（全照合項目）

| # | 項目 | Spec（source of truth） | client-py の挙動 | 判定 |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header | 「`Idempotency-Key` is REQUIRED on every POST /v2/transfers request.」 | 「fires the request without an `Idempotency-Key` header」（bulk endpoint のみ生成、single path 未更新） | **Breaking (B1)** |
| 2 | `status` enum 綴り | 「one of: `pending`, `settled`, `cancelled`, `failed`」（British spelling `cancelled`） | American spelling `canceled` を exact string equality で照合 | **Breaking (B2)** |
| 3 | `X-Sig` header | 「Every request MUST carry the `X-Sig` header」 | 「Sends the `X-Sig` header exactly as specified.」 | Compliant（非 drift, R1） |
| 4 | `expires_at` の単位 | 「integer / epoch_ms (milliseconds since epoch, UTC)」 | 「Parses `expires_at` as epoch milliseconds.」 | Compliant（非 drift, R2） |
| 5 | `X-Trace-Id` 追加 header | 拡張条項（section 7）が `X-*` 追加を許容 | 「Sends an `X-Trace-Id` header on every request」 | Permitted（rejected candidate, R3） |
| 6 | POST の retry 方針 | 「clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header」 | 「Never retries POSTs.」 | Compliant（rejected candidate, R4） |

### Breaking drifts（確定 finding・逐語証拠付き）

**B1: POST /v2/transfers で必須 header `Idempotency-Key` を送らない**
- Spec: 「`Idempotency-Key` is REQUIRED on every POST /v2/transfers request.」（api-spec.md, POST /v2/transfers 節）
- Client: 「POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated.」（client-py.md）
- 影響: REQUIRED header の欠落は retry の有無にかかわらず every request への要求違反。契約違反として breaking。

**B2: `status` enum の綴り不一致 — wire 値 `cancelled` をクライアントが `canceled` で照合**
- Spec: 「one of: `pending`, `settled`, `cancelled`, `failed`」「The `status` enum uses British spelling `cancelled`.」（api-spec.md, GET /v2/transfers/{id} 節）
- Client: 「maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value.」（client-py.md）
- 影響: exact string equality のため wire 値 `cancelled` は client 側テーブルの `canceled` に一致せず、cancelled 状態の transfer がマッピング不能になる。breaking。

### Rejected candidates（非 finding・許容/適合条項の引用付き）

**R1: `X-Sig` header** — 非 drift。Spec の Authentication 節「Every request MUST carry the `X-Sig` header」に対し client は「Sends the `X-Sig` header exactly as specified.」で適合。なお header 名の casing は「Header names are matched case-insensitively per RFC 9110; clients MAY send any casing.」により casing 差も許容される。

**R2: `expires_at` を epoch milliseconds で解釈** — 非 drift。Spec は「expires_at | integer | epoch_ms (milliseconds since epoch, UTC)」「The `expires_at` field is always epoch_ms」と定義し、client は「Parses `expires_at` as epoch milliseconds.」で一致。秒解釈なら breaking だったが該当しない。

**R3: `X-Trace-Id` header の送信** — spec が明示許容する差分。許容条項は Extension clause (section 7): 「Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation.」`X-Trace-Id` は `X-*` 拡張 header に該当。

**R4: 「Never retries POSTs.」** — 非 drift。Spec は「clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header」と retry を条件付き禁止しており、retry しないこと自体は適合挙動。Idempotency-Key 欠落（B1）とは独立の論点として棄却。

## Review

Iteration 1: reviewer 2 名（観点 A: accuracy-evidence、観点 B: completeness-validator）を単一メッセージで並列起動（window 2026-08-07T06:41:20Z..06:46:30Z、`parallel_execution: true` を aggregate が確認）。指摘: High 0 / Medium 0 / Low 1（Low: R1/R2/R4 は「spec 許容差分」ではなく「適合挙動」であり rejected-candidates 節への分類境界が曖昧、という分類上の指摘。引用要件は全件充足と判定）。レビュー生 JSON は `.mission-state/review-iter1-{accuracy,completeness}.json`、集計 evidence は `.mission-state/archive/iter-1-84fb25a0-reviews.json`。

スキーマ適合の往復: reviewer JSON が mission-review/1 validator に 4 回拒否され修正した（scores 4 軸キー・same_score_note は Reviewer A 本人が再出力、finding id は Reviewer B 本人が追記、severity 大文字化 "low"→"Low" と axis="completeness" 付与のみ orchestrator が機械的正規化。判定・スコア値の変更なし）。

## Score

`review-finalize`（aggregate-reviews → push-score）による tool-computed 値（iteration 1、timestamp 2026-08-07T06:49:18Z）:

- composite_score: **4.96**（threshold 4.0 以上）
- items: mission_achievement 5.0 / accuracy 5.0 / completeness 4.85 / usability 5.0（min_item 4.85 >= 3.5）
- open_high: **0**
- review_agreement: 5.0（全軸 delta 0.0 <= 1.5）
- findings_evidence_path: `.mission-state/archive/iter-1-84fb25a0-reviews.json`

## Stop Decision

Iteration 1 で全 gate 達成（composite 4.96 >= 4.0、open_high == 0、agreement delta 0.0 <= 1.5、min_item 4.85 >= 3.5、findings evidence 保存済み）。early-stop 条件（threshold 到達かつ open_high == 0、composite が 4.3 超）により pass。`closeout`（mark-passes → next）の exit 0 と `passes: true` を確認して停止。iteration 2 は不要（`--max-iter 2` の範囲内で 1 iteration 完了）。Low 1 件（分類境界の指摘）は open_high に影響せず、artifact 本文で「適合条項」引用により対処済みのため残置。

## Evidence

- 読んだ fixture（指定 2 件のみ）: `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`, `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- B1/B2/R1–R4 の逐語引用は上記 Execution 節（すべて fixture 本文からの exact quote）。
- Mission state: `.mission-state/sessions/cc-72cb24cb-f9f2-4ba0-897e-43426c181e76.json`（mission_id `84fb25a03f260750`）。score / passes は `mission-state.py` の `review-finalize` / `closeout` 出力を正とし手計算していない。
- Review 生データ・scoring JSON: `.mission-state/` 配下に保存（本 artifact へは逐語再掲しない、#280 出力圧縮規律）。
- closeout 結果（tool 出力）: `mark_passes: {"ok": true, "passes": true, "forced": false}` / `next_action: "report-complete"` / `phase: "done"` / `loop_active: false`（2026-08-07 実行）。
- Specialists（`specialists summary --json`）: selected / used / degraded / unselected-manual いずれも空。task_profile.primary は `documentation`（`specialists recommend --record-state` で記録、外部 specialist 不使用の degraded/fallback 方針も state に記録済み）。
- 未計測事項: 本 run は実 HTTP 通信・実コード実行を行っていない（fixture は実装ノートのテキスト）。B1/B2 の実挙動（サーバー応答・例外発生）は未計測であり、fixture 記述からの契約照合のみが根拠。ベンチマーク間の優劣は主張しない。

## Assumptions

- Spec を source of truth、client-py.md の実装ノートを client の事実挙動として扱う。
- ネットワーク禁止のため mission local authoring sync（git fetch 伴う）はスキップし、repo 内 `scripts/mission-state.py` を使用。
- 「Never retries POSTs」は適合挙動であり drift ではない（B1 とは独立に棄却）。
- 詳細: `.mission-state/sessions/cc-72cb24cb-f9f2-4ba0-897e-43426c181e76-assumptions.md`
