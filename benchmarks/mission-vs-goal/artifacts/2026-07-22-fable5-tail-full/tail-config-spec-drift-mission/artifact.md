# tail-config-spec-drift — Mission Arm Artifact (2026-07-22, profile: full)

## Mission

`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md` を正典（canonical contract）として、`impl-alpha.md` / `impl-beta.md` / `runbook.md` の設定ドリフトを監査する。spec と矛盾する箇所は key と両値を引用して confirmed drift として列挙し、単位・集計換算で整合する見かけ上の矛盾は換算式付きで明示的に棄却する。

- Task id: `tail-config-spec-drift` / Category: configuration / Arm: mission / Mission profile: full
- Mission state: `.mission-state/sessions/cc-f8a3209c-58f8-4e43-b3a9-9a224b98d6ac.json`（mission_id: `dc1a20f9b824e8de`、complexity: Complex、threshold: 4.0、max_iter: 3、review_tier: full）

## Plan

Iteration 1 計画（mission-planner 指示を SKILL.md から読み込みインライン適用。fork 呼び出しがエラーになったための fallback、証跡は Assumptions 参照）:

| # | アクション | 入力 | 出力 | 完了条件 | 依存 | 並列可 |
|---|---|---|---|---|---|---|
| 1 | fixture 4 ファイル読了 | spec/impl-alpha/impl-beta/runbook | 生値の把握 | 4 ファイル全行読了 | - | - |
| 2 | spec の 10 key を軸に全ファイル突合 | #1 | ドリフト候補リスト | 全 key × 全ファイルを走査済み | 1 | - |
| 3 | 換算判定（ticks→s、per-replica→aggregate、大文字小文字、key 分割） | #2 | confirmed / rejected の分離 | 各候補に判定根拠が付く | 2 | - |
| 4 | artifact 起草（8 見出し + validator 3 要件） | #3 | 本ファイル | validator 要件を満たす | 3 | - |
| 5 | Reviewer 3 名並列レビュー（Complex/full tier） | #4 | mission-review/1 JSON ×3 | 3 名分の JSON 取得 | 4 | 3並列 |
| 6 | aggregate-reviews → push-score → gate 判定 | #5 | score_history / passes | mark-passes exit 0 または critic 継続 | 5 | - |

リスクと対策: (a) answer key 汚染 → `benchmarks/mission-vs-goal/` 配下は fixture 4 + 本 artifact 以外に一切アクセスしない（reviewer にも同制約を明示）。(b) 見かけ上の矛盾の誤判定 → 換算式を必ず数値で示す。(c) network 禁止 → local authoring sync をスキップ（Assumptions #1）。

## Execution

spec の 10 key を軸に、3 ファイルを全行突合した。結果: **confirmed drift 7 件、rejected candidates 5 件**。

### Confirmed drift table

| # | File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|---|
| 1 | impl-alpha.md | `request_timeout_ms` / `requestTimeoutMs` | `3000` | `27000` | spec: 「`request_timeout_ms` \| 3000」/ alpha: 「`requestTimeoutMs   = 27000`」。両者とも単位は ms であり換算余地なし（27000 ms = 27 s ≠ 3 s） |
| 2 | impl-alpha.md | `queue_max_depth` / `MAX_QUEUE_DEPTH` | `10000` | `1250` | spec: 「`queue_max_depth` \| 10000」/ alpha: 「`MAX_QUEUE_DEPTH    = 1250`」。1250 に掛けて 10000 になる shard/worker 数などの根拠は alpha にも spec にも記載がない（replica 2 でも 1250×2=2500≠10000） |
| 3 | impl-alpha.md | `enable_legacy_auth` / `enableLegacyAuth` | `false`（「Must stay false; scheduled for removal.」） | `true` | alpha: 「`enableLegacyAuth   = true`」および注記「The legacy auth flag was toggled during the March incident bridge and has not been revisited since.」。boolean の真逆で換算余地なし |
| 4 | impl-beta.md | `retry_backoff` / `RETRY_BACKOFF_STRATEGY` | `exponential, base 250ms` | `constant-interval`（base は `RETRY_BACKOFF_BASE_MS=250` で spec と整合。乖離は戦略のみ） | spec: 「`retry_backoff` \| exponential, base 250ms」/ beta: 「`RETRY_BACKOFF_STRATEGY=constant-interval`」および「`RETRY_BACKOFF_BASE_MS=250`」。beta の注記どおり retry library enum には `exponential` が存在する（「(`constant-interval`, `exponential`, `decorrelated-jitter`)」）ため、命名規約差ではなく戦略そのものの相違 |
| 5 | impl-beta.md | `health_check_interval_s` / `HEALTH_CHECK_INTERVAL_SECONDS` | `15` | `75` | spec: 「`health_check_interval_s` \| 15」/ beta: 「`HEALTH_CHECK_INTERVAL_SECONDS=75`」。key 名が SECONDS を明示しており tick 換算は適用不可。75 s ≠ 15 s |
| 6 | runbook.md | `max_retries` | `3`（「Applies to idempotent requests only.」） | `6` | runbook Retry guidance: 「the gateway will retry idempotent requests up to 6 times before shedding」。spec と同じ idempotent requests を対象に 6 回と記述しており、集計・単位の差ではない |
| 7 | runbook.md | `tls_min_version` | `1.3`（「Hard floor for all listeners.」） | `1.2`（rotation 時に一時的に設定せよと指示） | runbook TLS: 「set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window」。spec は例外のない hard floor を宣言しており、一時的でも 1.2 への引き下げ指示は矛盾 |

### Rejected candidates（見かけ上の矛盾だが整合するもの）

| # | File | Key | 疑わしく見えた理由 | 棄却根拠（換算・理由） |
|---|---|---|---|---|
| R1 | impl-beta.md | `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s` = `90` | 数値が 5400 vs 90 で大きく乖離 | 単位換算で一致: beta 注記「the scheduler runs at 60 ticks per second」より **5400 ticks ÷ 60 ticks/s = 90 s** = spec の 90 s |
| R2 | runbook.md | 「the two replicas hold 64 pooled connections in total」 vs spec `db_pool_size_per_replica` = `32` | 64 ≠ 32 に見える | 集計換算で一致: spec Notes「Two replicas run in production.」より **32 per replica × 2 replicas = 64 total**。runbook は aggregate 表記 |
| R3 | impl-alpha.md | `retryBackoff = exponential` + `retryBackoffBaseMs = 250` vs spec `retry_backoff` = `exponential, base 250ms` | spec の複合値 1 key が alpha では 2 key に分割されており、機械的な key 突合では不一致に見える | 意味的に一致: strategy = exponential、base = 250 ms でともに spec と同値。key の分割は表現差であり値の矛盾ではない |
| R4 | runbook.md | 「Run all services at INFO verbosity」 vs spec `log_level` = `info` | 表記が `INFO` vs `info` | ログレベル名の大文字小文字は同一レベルの表記差。値として同じ `info` レベルを指す |
| R5 | runbook.md | 「DEBUG is allowed only on a single canary replica for up to one hour」 vs spec `log_level` = `info` | production で DEBUG を許す記述が default と矛盾に見える | spec の Notes は「Production default.」であり、恒久設定の既定値を規定するもの。単一 canary replica・最長 1 時間の一時的 DEBUG は default 値の変更ではなく、spec の禁止条項も存在しない |

### Violated spec constraints（明示ステートメント）

spec の 10 制約のうち、以下の **7 件（key ベースでは 7 制約）** が violation を受けている:

1. `request_timeout_ms = 3000` — impl-alpha が 27000 で違反
2. `queue_max_depth = 10000` — impl-alpha が 1250 で違反
3. `enable_legacy_auth = false`（Must stay false） — impl-alpha が true で違反
4. `retry_backoff = exponential, base 250ms` — impl-beta が constant-interval で違反（base 250ms 部分は両実装とも準拠）
5. `health_check_interval_s = 15` — impl-beta が 75 で違反
6. `max_retries = 3` — runbook が「up to 6 times」で違反
7. `tls_min_version = 1.3`（Hard floor for all listeners） — runbook が rotation 時 1.2 への引き下げを指示して違反

違反を受けていない spec 制約: `idle_timeout_s = 90`（beta は tick 換算で準拠）、`log_level = info`（全ファイル準拠。alpha にも `logLevel = info` が存在）、`db_pool_size_per_replica = 32`（alpha/beta 準拠、runbook は aggregate で整合）。なお impl-alpha は excerpt であり、`health_check_interval_s` と `idle_timeout_s` の 2 key は alpha に記載がない。記載なしは「矛盾の証拠」ではないため drift に数えず、未検証項目として下記 not-checked 表に列挙する。

### Not-checked items（fixture 上で検証不能だった組合せ）

| File | Key | 理由 |
|---|---|---|
| impl-alpha.md | `health_check_interval_s` | excerpt に該当 key の記載なし（欠落 ≠ 矛盾。実効値は未計測） |
| impl-alpha.md | `idle_timeout_s` | excerpt に該当 key の記載なし（同上） |
| runbook.md | `request_timeout_ms` / `queue_max_depth` / `enable_legacy_auth` / `idle_timeout_s` / `db_pool_size_per_replica`（per-replica 値以外の言及） | runbook に対応する数値・記述が存在しないため比較対象なし |

## Review

### Iteration 1 — reviewer 3 名並列レビュー（実施済み）

mission-reviewer サブスキル契約に基づき、独立サブエージェント 3 名を単一メッセージで並列起動した（A-2 指摘への回答: inline fallback は planner/executor のみで、reviewer は実際に並列 spawn した。証跡は `.mission-state/archive/iter-1-dc1a20f9-reviews.json`）。観点C は選定 specialist `sc-document-reviewer` のレンズを evidence provider として併用した。

| Reviewer | 観点 | 達成度 | 正確性 | 完成度 | 実用性 | High | Medium | Low |
|---|---|---|---|---|---|---|---|---|
| A | ミッション達成度 | 4 | 5 | 3 | 4 | 1 (A-1) | 1 (A-2) | 0 |
| B | 正確性 | 5 | 5 | 4 | 4 | 0 | 0 | 2 (B-1, B-2) |
| C | 実用性 + 文書品質 | 3 | 4 | 3 | 3 | 1 (C-1) | 1 (C-2) | 2 (C-3, C-4) |

3 名とも confirmed drift 7 件・rejected 5 件を fixture 原文と独立突合し、**偽陽性・偽陰性ゼロ**（A: 「false positive・false negativeの検出なし」、B: 「偽陽性・偽陰性ともにゼロ」）を確認。指摘は監査内容ではなく artifact の完結性・表現に集中した:

- **A-1 / C-1 / B-2**: Review・Score・Stop Decision 節がプレースホルダー → 本節および以下 2 節の記入により解消
- **A-2**: reviewer 並列レビュー未実行との指摘 → 実際には 3 名並列実行済み。本節に証跡を明記して解消
- **B-1 / C-3**: alpha 欠落 key 説明文への `log_level` 誤混入 → Execution 節の該当文を修正済み
- **C-2**: Finding 4 の Actual value に base 250ms の整合が不可視 → 表の Actual value / Quoted evidence 列に追記済み
- **C-4**: not-checked scope の構造化がない → Execution 節に「Not-checked items」表を追加済み

### Iteration 2 — 差分レビュー（M6: Medium 以上のインライン修正の再確認）

上記修正は orchestrator によるインライン修正のため、自己検証のみで合格とせず、検証担当 reviewer 1 名（perspective: verify）による差分再確認を実施した。結果は下記 Score の iteration 2 行のとおり。

## Score

`mission-state.py aggregate-reviews` → `push-score --scoring-json` による機械集計（手計算なし）。threshold 4.0 / min item gate 3.5 / open_high 0 必須。

| Iteration | Composite | 達成度 | 正確性 | 完成度 | 実用性 | open_high | agreement delta | 判定 |
|---|---|---|---|---|---|---|---|---|
| 1 | 3.89 | 4.00 | 4.57 | 3.33 | 3.67 | 2 | 2.0 | 未達（composite < 4.0、min_item 3.33 < 3.5、open_high 2、delta 2.0 > 1.5） |
| 2 | 4.75 | 5.0 | 5.0 | 4.0 | 5.0 | 0 | 0.0（単一 verify reviewer） | 合格（composite 4.75 ≥ 4.0、min_item 4.0 ≥ 3.5、open_high 0、mark-passes exit 0） |

Iteration 1 の未達要因はプレースホルダー節（A-1/C-1）に起因する完成度・実用性の減点であり、監査内容（drift 分類）自体への High/Medium 指摘はC-2（表現の不完全性）のみだった。

## Stop Decision

- **pass で停止**（iteration 2 / max_iter 3 の範囲内）。全 gate 通過: composite 4.75 ≥ 4.0、min_item 4.0 ≥ 3.5、open_high 0、findings evidence `.mission-state/archive/iter-2-dc1a20f9-reviews.json` 存在。`mark-passes` exit 0（`{"ok": true, "passes": true, "forced": false}`）、直後の `next` は `next_action=report-complete` / `loop_active=false` を返した。
- 残存 Low 1 件（verify-1: archive の独立検証不能）は fixture 監査精度に影響せず、対応任意と判定されたため停止を妨げない。
- 本節と Score 表 iteration 2 行の数値は、差分レビュー完了後に `push-score` 出力（score_history）から転記した機械集計値。差分レビュー時点ではこの 2 箇所は「転記待ち」であることを reviewer に明示して評価させた（採点値の先書き・捏造を防ぐ手順）。
- ベンチマーク規則により commit / push / network は不使用。編集対象は本 artifact と `.mission-state/`（および scratchpad の中間 JSON）のみ。

## Evidence

- 読み取った fixture（これ以外の `benchmarks/mission-vs-goal/` 配下ファイルには一切アクセスしていない）:
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`（canonical、10 key）
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md`（9 設定行 + deployment notes）
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md`（11 設定行 + tick/enum 注記）
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md`（Retry/TLS/Logging/DB/Health の 5 節）
- 各 confirmed finding の引用は上表 Quoted evidence 列に記載（すべて fixture 原文からの逐語引用）
- Mission state 証跡: `.mission-state/sessions/cc-f8a3209c-58f8-4e43-b3a9-9a224b98d6ac.json`（init → specialists recommend → phase=executing の遷移を記録）
- 未計測事項: 実際のデプロイ環境・実測値は本タスクの範囲外（fixture のテキストのみが根拠）。impl-alpha excerpt に health/idle 系 key が無い理由は fixture からは判定不能（未計測・未記載）

## Assumptions

`.mission-state/sessions/cc-f8a3209c-58f8-4e43-b3a9-9a224b98d6ac-assumptions.md` に記録した仮置きの要約:

1. ベンチマーク規則（network 禁止）を上位指示として local authoring sync をスキップし、repo root の `scripts/mission-state.py` を state oracle に使用
2. 複雑度 Complex はタスク指示の明記に従う（review_tier=full、reviewer 3 名）
3. `benchmarks/mission-vs-goal/` 配下は fixture 4 + 本 artifact のみアクセス（answer key 汚染防止、reviewer にも同制約を伝達)
4. 単位・集計換算で一致すれば rejected、換算しても不一致なら confirmed（換算式は本文に明記）
5. commit/push はしない。成果物は本 artifact と `.mission-state/` のみ
6. mission-planner / mission-executor の Skill fork 呼び出しがエラーになったため、SKILL.md を disk から読み込み同一コンテキストで適用（codex-inline 相当。specialist_invocations に記録）
