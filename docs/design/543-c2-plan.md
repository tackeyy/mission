# C2（#543）実装計画 — 全 mutating command の owner 整理と operation_id 配線

## 1. 目的とスコープ

#542（C1-core）で新規 init が v5 session を作るようになった。しかし **A1 管理外の約 16 コマンドは `_select_repository_for_cli` を通らず `StateLock` + `atomic_write_json(sf, ...)` で session state を直接書く**。これらが v5 session に当たると fenced 構造を破壊しうる。

C2 は #513 の残 Acceptance を達成する:
- 全 mutating command に単一 owner を宣言
- repository 外の直接 session write を無くす（LegacyV4Repository / v5 UnitOfWork のみが書く）
- 全 mutating v5 invocation に caller-stable operation ID を配線
- 静的 inventory を拡張し、unowned mutating command と repository 外の直接 write を検出

## 2. 対象コマンド（調査で確定）

### C2 で repository 経由にする（session state を直接書いている）

| コマンド | cmd_ 関数 | 現状の危険度 |
|---|---|---|
| specialists recommend | `cmd_specialists`（`_specialist_finalize_selection`） | format_guard なし・v5 で corrupt しうる |
| specialists verify-approval | `cmd_verify_provider_approval` | lease あり |
| specialists prepare-invocation | `cmd_prepare_provider_invocation` | lease あり |
| specialists invoke-prepared 系 | `cmd_invoke_command_provider` | dispatch_state 複数箇所 |
| specialists reconcile | `cmd_reconcile_provider_invocation` | lease あり |
| manual-score-capture | `cmd_manual_score_capture` | lease あり |
| plan-import | `cmd_plan_import` | lease あり |
| planning adopt-core | `cmd_planning_adopt_core` | lease あり |
| planning promote-provider-plan | `cmd_planning_promote_provider_plan` | lease あり |
| **planning reselect** | `cmd_planning_reselect` | **lease すら無い・最優先** |
| executor-handoff begin/verify/record/complete | `_cmd_executor_handoff` | lease あり |
| **supersede-reviews** | `cmd_supersede_reviews` | **review session ファイル群を直接書く・最危険** |

### 対象外（session state を書かない。manifest/sidecar のみ）

`parallel-init` / `parallel-closeout` / `archive-worktree` / `specialists-consent` / `context-manifest` / `pregate record` / `queue enqueue-mark`。これらは `_ParallelGroupStore` や XDG config、aggregate.json 等を対象とし **session state の format に依存しない**ため C2 では触らない。

## 3. 設計方針

### 3.1 repository 経由への移行

各対象コマンドを、C1-core が確立した **`_select_repository_for_cli`（v4/v5 を format-pin して選ぶ）→ repository.transaction() → load/execute/save** のパターンへ寄せる。

ただし対象コマンドは A1 の lifecycle use case（`run_initialize` 等）と違い、**それぞれ固有のドメインロジック**（specialist 選定・plan 検証・provider dispatch）を持つ。use case 層を新設せず、**既存のドメイン検証は保ったまま、state の read/write だけを repository seam に通す**のが現実的。

- read: `repository.load()`
- write: `repository.save(state, ...)`（transaction 内）
- format_guard が v5 head を検出したら、C1-core と同じく fenced 経路で処理

### 3.2 段階分割（規模が大きいため）

16 コマンド一括は #542 iter を繰り返した規模と同等以上。**危険度順に 2 段階**で進める:

- **Stage A（本 Issue で必須）**: `supersede-reviews` と `planning-reselect`（破壊リスク最大）+ owner レジストリと静的 inventory テストの骨格。これで「repository 外の直接 write を検出する仕組み」が入る
- **Stage B（本 Issue で可能なら、無理なら follow-up）**: 残りの specialists/provider/plan 系。数が多く各々ドメインロジックが重い

**判断**: まず Stage A を確実に入れ、Stage B は実装者が「1 PR で安全に収まるか」を判断する。収まらなければ Stage B を follow-up Issue に切り出し、その場合も**静的 inventory テストは「未対応コマンドを明示的に allowlist する」形**にして、対応済みと未対応を機械的に区別できるようにする（silent gap を作らない）。

### 3.3 operation_id 配線

C1-core で init が `operation_id = "init:" + digest` を持つようになった。C2 の各 mutating command も、v5 経路で **caller-stable な operation_id**（コマンド種別 + 対象の canonical digest）を渡し、operation-result lookup を効かせる。決定的キーにして crash 後 retry で二重実行を防ぐ。

### 3.4 静的 inventory テスト

`test_command_inventory.py` を拡張:
- `test_all_mutating_commands_have_declared_owner`: `_build_parser` の全 mutating subcommand を抽出し、owner レジストリ（A1 / C1 / C2 / 明示 allowlist）に必ず属することを要求
- C2 対応済みコマンドが `FORBIDDEN_LEGACY_CALLS`（atomic_write_json / StateLock 直呼び等）を含まないことを AST で検証
- **未対応コマンドは allowlist に列挙**し、その数が減っていくことを可視化（silent gap 禁止）

## 4. exact contract として確定する項目

| 項目 | 決めること |
|---|---|
| Stage A/B の切り分け | 1 PR に収まる範囲。収まらなければ Stage B を follow-up へ |
| ドメインロジックの扱い | use case 層を新設するか、既存関数の read/write だけ seam に通すか |
| operation_id の粒度 | コマンドごとの canonical intent の作り方 |
| supersede-reviews の多ファイル操作 | review group の各 session を repository 経由にする方法 |
| allowlist の形式 | 未対応コマンドをどう明示するか |

## 5. TDD Red 一覧

| # | 検証 |
|---|---|
| T1 | 全 mutating command が owner レジストリに属する（未宣言があれば Red） |
| T2 | C2 対応コマンドが atomic_write_json/StateLock を直接呼ばない（AST） |
| T3 | supersede-reviews が v5 review session を fenced 経路で正しく更新する |
| T4 | planning-reselect が v5 session で lease/fencing を尊重する（現状 lease すら無い） |
| T5 | 各 C2 コマンドが v5 session で動く（フルライフサイクルに組み込めるもの） |
| T6 | v5 session への直接 write が起きない（fenced 構造が保たれる） |
| T7 | operation_id retry が元結果を返す（配線したコマンド） |
| T8 | 未対応コマンドが allowlist に明示され、silent gap が無い |
| T9 | 既存の v4 session に対する各コマンドの挙動が不変 |

## 6. 受け入れ条件

- [ ] 全 mutating parser command に単一 owner（未宣言がゼロ、または allowlist に明示）
- [ ] repository 外の直接 session write が対応済みコマンドで無い
- [ ] supersede-reviews / planning-reselect が v5 で fenced 構造を壊さない
- [ ] operation_id が配線され retry が冪等（対応コマンド）
- [ ] 静的 inventory が unowned command と直接 write を検出
- [ ] 既存 v4 挙動が不変
- [ ] フルスイート green

## 7. リスクと対策

| リスク | 対策 |
|---|---|
| 16 コマンド一括で #542 の反復地獄 | 危険度順に Stage A/B。Stage B は follow-up 可 |
| **本番挙動の変更をテストがすり抜ける**（#542 の教訓） | 各コマンドを **実 CLI で v5 session に対して叩く**テストを必須にする。同一プロセス完結・時刻固定で経路を潰さない |
| supersede-reviews の多ファイル破壊 | v5 review session に対する実 CLI テストで fenced 構造の保全を確認 |
| ドメインロジックの退行 | 既存の specialist/plan/provider テストを維持。read/write seam の差し替えに限定 |
| silent gap | 未対応コマンドを allowlist で明示し、静的テストで数を可視化 |
