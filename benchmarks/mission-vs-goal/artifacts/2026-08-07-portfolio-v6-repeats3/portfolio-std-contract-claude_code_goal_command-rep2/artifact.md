# portfolio-std-contract — client-py 契約ドリフト監査 (arm: claude_code_goal_command, rep2)

## Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-std-contract-claude_code_goal_command-rep2.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` を正（source of truth）として `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` を監査し、すべての破壊的ドリフトを特定し、仕様が許容する差分は許容条項を引用して非所見として棄却する。

## Result

- **確定所見（破壊的ドリフト）: 2 件**
  1. POST /v2/transfers で必須の `Idempotency-Key` ヘッダーを送信していない。
  2. status enum のマッピングがワイヤ値 `cancelled`（英国綴り）に一致しない米国綴り `canceled` を厳密一致で照合しており、`cancelled` が未処理になる。
- **棄却候補（仕様許容・非所見）: 4 件**（`X-Sig` 送信、POST 非リトライ、`expires_at` の epoch_ms 解釈、`X-Trace-Id` 拡張ヘッダー）
- 監査は 2 つの fixture の静的読解のみで実施。実挙動・実トラフィックは未測定（unmeasured）。

### Drift table

| # | 項目 | client-py の挙動 | spec の要求 | 判定 |
|---|---|---|---|---|
| 1 | `Idempotency-Key` ヘッダー | POST /v2/transfers（単発）で送信しない | `Idempotency-Key` is REQUIRED on every POST /v2/transfers request | **破壊的ドリフト** |
| 2 | `status` enum の綴り | 内部マッピングが `canceled`（米国綴り）を厳密文字列一致で照合 | enum は `pending`, `settled`, `cancelled`, `failed`（英国綴り `cancelled`） | **破壊的ドリフト** |
| 3 | `X-Sig` ヘッダー | 仕様どおり送信 | 全リクエストに `X-Sig` 必須 | 適合（非所見） |
| 4 | POST のリトライ | 一切リトライしない | Idempotency-Key なしのリトライを禁止（リトライ自体は義務でない） | 適合（非所見） |
| 5 | `expires_at` の解釈 | epoch ミリ秒として解析 | integer / epoch_ms | 適合（非所見） |
| 6 | `X-Trace-Id` ヘッダー | 全リクエストに送信 | 未定義ヘッダーだが Extension clause (section 7) が明示許容 | 仕様許容（棄却候補） |

## Breaking drifts（確定所見・引用証拠付き）

### 1. POST /v2/transfers で必須ヘッダー `Idempotency-Key` の欠落

- spec（api-spec.md, POST /v2/transfers）:
  > `Idempotency-Key` is REQUIRED on every POST /v2/transfers request.
- client-py（client-py.md）:
  > POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated.
- 判定: 全リクエストで REQUIRED と明記されたヘッダーを単発 transfer パスで送っていないため、契約違反（破壊的ドリフト）。

### 2. `status` enum の綴りドリフト（`cancelled` vs `canceled`）

- spec（api-spec.md, GET /v2/transfers/{id}）:
  > | status | enum | one of: `pending`, `settled`, `cancelled`, `failed` |
  >
  > The `status` enum uses British spelling `cancelled`.
- client-py（client-py.md）:
  > Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value.
- 判定: マッピングは「exact string equality against the wire value」で照合するため、ワイヤ値 `cancelled` はクライアント側の `canceled` に一致せず、cancelled 状態の transfer が未処理となる。破壊的ドリフト。

## Rejected candidates（仕様許容・非所見、許容条項の引用付き）

1. **`X-Sig` ヘッダー送信** — client-py は「Sends the `X-Sig` header exactly as specified.」であり、spec の「Every request MUST carry the `X-Sig` header」（Authentication 節）を満たす。適合のため非所見。
2. **POST を一切リトライしない** — client-py は「Never retries POSTs.」。spec は「clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header」と、キーなしリトライを禁止しているだけで、リトライしないこと自体は義務違反ではない。非所見。
3. **`expires_at` を epoch ミリ秒として解析** — client-py は「Parses `expires_at` as epoch milliseconds.」。spec は「expires_at | integer | epoch_ms (milliseconds since epoch, UTC)」であり一致。spec が警告する「treating it as seconds」には該当しない。非所見。
4. **`X-Trace-Id` 拡張ヘッダーの送信** — client-py は「Sends an `X-Trace-Id` header on every request for distributed tracing.」。spec の Extension clause (section 7) が「Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). ... Sending an extension header is never a contract violation.」と明示的に許容しているため、非所見として棄却。

補足: ヘッダー名の大文字小文字については、spec の Authentication 節が「Header names are matched case-insensitively per RFC 9110; clients MAY send any casing.」と許容しており、client-py 側にも casing 逸脱の記載がないため候補にも挙がらない。

## Evidence

- 読んだ fixture は指示された 2 ファイルのみ:
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- 上記 Breaking drifts / Rejected candidates 節の各引用は、両 fixture からの逐語引用（ヘッダー名 `X-Sig` / `Idempotency-Key` / `X-Trace-Id`、フィールド名 `id` / `status` / `expires_at`、enum 値 `pending` / `settled` / `cancelled` / `canceled` / `failed` を含む）。
- 実 API・実クライアントコードの実行検証は行っていない（fixture は実装ノート形式のため）。ランタイム挙動は unmeasured。

## Assumptions

- api-spec.md がタスク指示どおり source of truth であり、client-py.md の実装ノートが実装挙動を正確に記述していると仮定する。
- 「breaking drift」を「spec の MUST / REQUIRED / enum 定義に反し、相互運用を壊す差分」と解釈した。
- fixture に記載のない挙動（タイムアウト、エラーハンドリング等）は監査対象外とした。

## Stop Condition

本アーティファクトが指定パス `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-std-contract-claude_code_goal_command-rep2.md` に存在し、Goal / Result / Evidence / Assumptions / Stop Condition の全見出し、drift table、引用証拠付き breaking-drift 節、rejected-candidates 節を含んだ時点で完了。commit / push / ネットワークアクセスは行わない。
