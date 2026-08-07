# portfolio-cx-config — mission arm (rep2)

Task id: `portfolio-cx-config` / Category: configuration / Arm: mission (profile: full) / Date: 2026-08-07

## Mission

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify as compliant, undocumented divergence (violation), or documented override (approval reference cited; rejected as a non-finding). Sources are exactly the five named fixtures; missing rows count as audit failures.

- Mission state: `.mission-state/sessions/cc-055b7f31-4590-46fd-871d-83fdaade8c57.json` (mission_id `ec3244abb23c27f3`, complexity Complex, max-iter 3, threshold 4.0)
- Routing: `init` は route verdict を返さず mission ループ継続（Complex のため adaptive routing 対象外）。

## Plan

mission-planner (iteration 1, forked Skill invocation) が策定。要旨:

1. `platform/platform-defaults.md` を読み、9 canonical constants を確定。
2. 4 サービスの `config.md` を読み、各 9 行の値と Note 列を確認（計 36 セル）。
3. 各セルを Compliant / Violation / Documented override に分類。override は `PLAT-<id>` 承認参照の有無で判定（override protocol: "Overrides without an approval reference are treated as violations"）。
4. 本 artifact を必須 8 見出しで作成。検証: セル数 27+6+3=36、violation 全件に fixture 引用、override 全件に `PLAT-<id>`。

## Execution

- Orchestrator が fixture 5 件を単一メッセージで並列 Read（一次情報）。
- mission-planner は forked Skill として実行し、36 セルの分類表を返却。orchestrator の一次読解と全セル一致を確認。
- Artifact の Write は orchestrator inline で実施（分類は planner 出力と orchestrator 一次読解の二重確認済み。この実施形態は state の specialist 証跡と Assumptions に記録）。
- ネットワーク・commit・push・パッケージ導入なし。編集対象は本 artifact と `.mission-state/` のみ。

### Canonical defaults (PLAT-CONFIG v4)

| Constant | Canonical value |
|---|---|
| CONNECT_TIMEOUT_MS | 4000 |
| REQUEST_RETRY_MAX | 5 |
| SESSION_TTL_SEC | 3600 |
| DB_POOL_SIZE | 64 |
| BATCH_WINDOW_MS | 500 |
| TLS_MIN_VERSION | TLSv1.2 |
| CACHE_TTL_SEC | 300 |
| IDEMPOTENCY_WINDOW_SEC | 600 |
| LOG_RETENTION_DAYS | 30 |

### Full per-service compliance table (36 rows = 9 constants × 4 services)

| Service | Constant | Canonical | Actual | Verdict |
|---|---|---|---|---|
| auth | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| auth | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| auth | SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented divergence) |
| auth | DB_POOL_SIZE | 64 | 64 | Compliant |
| auth | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (undocumented divergence) |
| auth | CACHE_TTL_SEC | 300 | 300 | Compliant |
| auth | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| auth | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) — rejected as non-finding |
| billing | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| billing | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| billing | DB_POOL_SIZE | 64 | 64 | Compliant |
| billing | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| billing | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| billing | CACHE_TTL_SEC | 300 | 300 | Compliant |
| billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence) |
| billing | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| search | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| search | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| search | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| search | DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence) |
| search | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| search | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| search | CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) — rejected as non-finding |
| search | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| search | LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence) |
| notify | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| notify | REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) — rejected as non-finding |
| notify | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| notify | DB_POOL_SIZE | 64 | 64 | Compliant |
| notify | BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence) |
| notify | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| notify | CACHE_TTL_SEC | 300 | 300 | Compliant |
| notify | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| notify | LOG_RETENTION_DAYS | 30 | 30 | Compliant |

Totals: Compliant 27 / Violations 6 / Documented overrides (rejected) 3 = 36 cells. No missing rows.

### Violations (confirmed findings, with quoted evidence)

1. **auth `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`. Fixture row: `| SESSION_TTL_SEC | 7200 | |` (Note column empty; no `PLAT-<id>`). Operational note admits the drift: "session length was extended during the 2026-04 login incident and the change was kept afterwards." No approval reference → violation.
2. **auth `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`. Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. "legacy SDK compat" is a descriptive reason without a `PLAT-<id>` approval reference; canonical rationale states "TLSv1.1 is end-of-life". Per override protocol this is a violation.
3. **billing `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`. Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (Note empty). Operational note: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03." No approval reference → violation.
4. **search `DB_POOL_SIZE`** — canonical `64`, actual `128`. Fixture row: `| DB_POOL_SIZE | 128 | |` (Note empty). Operational note: "the pool was doubled during a 2026-05 load test and never reverted." No approval reference → violation.
5. **search `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`. Fixture row: `| LOG_RETENTION_DAYS | 45 | |` (Note empty). Operational note: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." No approval reference → violation (canonical rationale: "Data-minimization policy DM-9").
6. **notify `BATCH_WINDOW_MS`** — canonical `500`, actual `250`. Fixture row: `| BATCH_WINDOW_MS | 250 | |` (Note empty). Operational note: "the batch window was halved to reduce push latency during the 2026-06 campaign." No approval reference → violation.

### Rejected candidates (documented overrides — non-findings)

1. **billing `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`. Fixture row: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`. Approval reference **PLAT-482** cited in-config → documented override, rejected as non-finding.
2. **search `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Fixture row: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`. Approval reference **PLAT-511** → documented override, rejected as non-finding.
3. **notify `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Fixture row: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`. Approval reference **PLAT-390** → documented override, rejected as non-finding.

## Review

- Reviewer 2 名（Complex, 独立並列 spawn、単一メッセージ発行）。観点: A=正確性・網羅性、B=validator 適合・証拠品質。
- 結果は `review-finalize`（aggregate-reviews → push-score）で集計。生 JSON は `.mission-state/` 配下に保存し、本節には gate 値のみ記載（#280 出力圧縮規律）。
- 実施記録: Reviewer A（accuracy-completeness）= findings 0 件・4軸オール 5.0（same_score_note 記載）。Reviewer B（validator-evidence）= Low 1 件（compliance table に canonical Rationale 列がなく、compliant 行の traceability が部分的）。High / Medium は両名とも 0 件。
- reviewer window: 2026-08-07T14:16:44Z 開始の単一メッセージ並列 spawn（両観点とも同一 window で `review-finalize` に報告済み）。初回返却 JSON がスキーマ不一致（`criteria_scores`）だったため、両 reviewer に正スキーマ（`scores` 4軸）で再発行させた（スコアの orchestrator 転記はしていない）。
- 生 JSON: `.mission-state/reviews/iter1-accuracy.json` / `iter1-validator.json`、集計 archive: `.mission-state/archive/iter-1-ec3244ab-{reviews,scoring}.json`。

## Score

`push-score`（review-finalize 経由）が記録した iteration 1 実値（`score_history[0]`、timestamp 2026-08-07T14:20:38Z）:

- composite: **4.9**（threshold 4.0 以上）
- items: mission_achievement 4.9 / accuracy 4.95 / completeness 4.85 / usability 4.9（min 4.85 >= 3.5）
- open_high: **0** / findings evidence: `.mission-state/archive/iter-1-ec3244ab-reviews.json`
- agreement: max delta 0.3（completeness 4.7 vs 5.0）<= 1.5

## Stop Decision

- Gate 全達成（composite 4.9 >= 4.0、open_high 0、min_item 4.85 >= 3.5、max_agreement_delta 0.3 <= 1.5、findings evidence 記録済み）。early-stop 続行条件（composite 4.0–4.3 帯 + Medium 3件以上）に非該当のため iteration 1 で停止。
- Low 1 件（Rationale 列）は本節更新と同時に Evidence 節へ canonical rationale の対応を追記して解消（High/Medium ではないため再レビュー gate 対象外、M6 非該当）。
- `closeout`（mark-passes → next）exit 0・`next_action=report-complete` を確認して終了（実行証跡は state の `passes` / `decisions`）。max-iter 3 のうち 1 iteration で完了。

## Evidence

- Fixtures read (exactly the five named): `platform/platform-defaults.md`, `auth/config.md`, `billing/config.md`, `search/config.md`, `notify/config.md`（すべて `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/` 配下）。
- すべての violation / override 判定は上記の逐語引用（該当テーブル行と operational note）を根拠とする。判定規則の根拠は platform-defaults.md の逐語: "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`)." および "Overrides without an approval reference are treated as violations."
- Mission state evidence: session `cc-055b7f31-4590-46fd-871d-83fdaade8c57` / `iterations[0]` の scoring 記録 / assumptions: `.mission-state/sessions/cc-055b7f31-4590-46fd-871d-83fdaade8c57-assumptions.md` / review 生 JSON: `.mission-state/reviews/iter1-*.json`。
- Canonical rationale の対応（reviewer B Low 指摘への反映。すべて platform-defaults.md の Rationale 列逐語）: CONNECT_TIMEOUT_MS "Upstream LB kills idle connects at 5s" / REQUEST_RETRY_MAX "Backoff budget fits the 30s request SLA" / SESSION_TTL_SEC "Security review SR-2026-02" / DB_POOL_SIZE "Sized for the shared PgBouncer tier" / BATCH_WINDOW_MS "Downstream consumer throughput contract" / TLS_MIN_VERSION "Security baseline; TLSv1.1 is end-of-life" / CACHE_TTL_SEC "Balance of freshness and origin load" / IDEMPOTENCY_WINDOW_SEC "Duplicate-suppression window for retries" / LOG_RETENTION_DAYS "Data-minimization policy DM-9"。
- Unmeasured: 各サービスの実行時実効値、PLAT-482/511/390 の承認チケット実在性、SDK deprecation ticket の状態。本 audit は fixture 記載内容のみを対象とし、これらは未測定と明記する。
- Closeout 経緯: 初回 `closeout` は specialist selection checkpoint 未記録の gate で `mark-passes-gate-failed`（fail-closed 動作を確認）。`specialists recommend --record-state` 実行（decision: policy=fallback, action=continue-core — documentation-provider 未インストールのため core 続行）後の再実行で `ok: true / next_action: report-complete / passes: true / loop_active: false` を確認。
- 本 run はベンチマーク優劣を主張しない。artifact 完成のみを成果とする。

## Assumptions

- フィクスチャ 5 ファイルを audit の唯一の真実とする（実行時状態・外部チケットシステムは検証しない = unmeasured）。
- `PLAT-<id>` 形式の承認参照がない divergence は、記述的理由（"legacy SDK compat" 等）があっても violation（override protocol の逐語規定による）。
- benchmarks/mission-vs-goal/ 配下は指定 fixture 5 件と本出力ファイル以外アクセスしない。commit / push / network なし。
- MISSION_PLUGIN_ROOT はローカル authoring 構成だが network 禁止のため authoring sync をスキップし、repo 直下 `scripts/mission-state.py` を正とした。
- 複雑度はタスク指示により Complex 固定（reviewer 2 名、mission profile: full）。
- Executor 工程は orchestrator inline（planner 出力と orchestrator 一次読解の二重確認で代替）。実施形態は本節と state に記録済み。
