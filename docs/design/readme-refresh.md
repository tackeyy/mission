# README 鮮度更新（v2.4.0 世代合わせ）

## 目的

README.md / README.ja.md が v2.0 相当のまま止まっており、v2.1〜v2.4 で追加した主要機能が 1 件も反映されていない。加えてテスト件数・環境変数表・Usage フラグが実装と乖離している。README だけを読む利用者に対して正しい現状を提示する。

## スコープ

対象は `README.md` と `README.ja.md` の 2 ファイルのみ。

**やらないこと**（このタスクで触ってはならない）:

- 「451 件の scored 本番ミッション / 95%」の数値更新（再計測は CASE_STUDIES.md の方法論固定が必要。別タスク）
- Competitive Positioning の research snapshot 日付（2026-06-15）と競合比較表の内容更新（別タスク）
- `## Verified Behavior` / `## E2E 検証済み事項` 節（親側で実測値を反映するため触らない）
- CHANGELOG・plugin manifest・skills 配下・docs 配下の他ファイル
- 既存の文体・見出し構成の変更、節の並べ替え

## 変更内容

### 1. Features / 特徴 に v2.1〜v2.4 の機能を追加

既存の箇条書きと同じ粒度（1 項目 1〜2 行、必要なら refs へのリンク）で以下を追加する。EN は `## Features`、JA は `## 特徴`。

- Pre-gate 評価キャッシュ: `pregate record` / `pregate check` により、同一 Issue・同一 subject digest の再評価を省略し evidence を引用する
- Evidence handoff: `handoff publish` / `await` / `verify` によるセッション間の証跡受け渡し
- Merge queue: `queue enqueue` / `status` / `next` / `verify` / `mark` により、同一 state root の並列 mission が base 不一致のまま merge するのを防ぐ
- Lane report と SLO 判定: `lane-report --slo-minutes` により所要時間を SLO と突合し、待機種別ごとの内訳を出力する
- Failure ledger と learning brief: reviewer の `general_fix_rule` を蓄積し、`learning brief` で再発回数降順に取り出して planner / executor へ注入する（reviewer へは注入せず採点の独立性を保つ）
- 反復回復の計測: `stats` / audit の `iteration_recovery` により、ゲート reject を経た run の初回→最終スコア差・反復回数・指摘解消率を集計する
- 実装委譲（任意）: registry に implementation provider が登録済みなら実装ステップの diff 生成を headless coding agent へ委譲できる。検証・レビュー・pass 判定は core が保持する（[design](skills/mission/refs/implementation-delegation.md)）

### 2. Testing / テスト の数値と実行形態を更新

- 履歴スナップショットの行を `2026-08-14: 3020 passed` に更新する（EN: `Historical local verification snapshot:` の行 / JA: 対応行）
- `make test` / `make test-e2e` が pytest-xdist による並列実行（`-n auto --dist loadfile`）である旨を 1 行追記する
- CI に docs 変更のみを対象とする fast path（`scripts/ci_changed_scopes.js` が対象スコープを判定）がある旨を 1 行追記する

### 3. Configuration / 設定 の環境変数表を実装と一致させる

現在の表は 3 変数のみ。`skills/mission/bin/mission-state.py` が実際に参照する変数を追加する。既存表と同じ 3 列（変数 / 既定 / 用途）を維持すること。追加対象と用途は実装を読んで記述する:

- `MISSION_LEASE_ID`（fenced session lease のトークン。mutating command に必須）
- `MISSION_LEASE_TTL_SECONDS`
- `MISSION_SESSION_ID`
- `MISSION_STALE_ACTIVE_SECONDS`
- `MISSION_SKILL_ROOTS`
- `MISSION_REQUIRE_SCORING_EVIDENCE`

`MISSION_CLAUDE_HOME` / `MISSION_FORCE_PID_IS_AGENT` / `MISSION_STATE_NOW` はテスト・内部用途のため表には載せない。既定値は実装のフォールバック値を確認して記載し、推測で書かない。

### 4. Usage / 使い方 のフラグを補う

`/mission <mission description> [--max-iter N] [--threshold X] [--skip-preflight]` の行に `--budget-minutes N` / `--goal-dispatch <inline|host-native>` / `--force-mission` を加える。各フラグの意味は `skills/mission/SKILL.md` の記述と一致させること。

### 5. Repository Layout / 構成 に不足パスを追加

`docs/`（設計・運用ドキュメント）、`benchmarks/`（mission vs goal のパイロット計測）、`scripts/ci_changed_scopes.js`（CI の変更スコープ判定）を既存表の書式で追加する。

### 6. EN と JA の同期

両ファイルに同内容を反映する。JA は既存の日本語表現・敬体に合わせる。JA 側に対応節が無い場合は EN の構成に合わせて追加せず、既存の JA 見出し（`## 特徴` / `## テスト` / `## 設定` / `## 使い方` / `## 構成`）配下に収める。

## 受け入れ条件

- [ ] EN / JA 双方の Features に上記 7 機能が漏れなく記載されている
- [ ] テスト件数が `3020 passed`、日付が `2026-08-14` になっている
- [ ] 環境変数表に上記 6 変数が追加され、既定値が実装と一致している（推測値でない）
- [ ] Usage に 3 フラグが追加されている
- [ ] Repository Layout に 3 パスが追加されている
- [ ] スコープ外ファイルに差分が無い（`git status` で README 2 ファイルのみ）
- [ ] `Verified Behavior` / `E2E 検証済み事項` 節に差分が無い
- [ ] `git diff --check` が通る

## テスト

README のみの変更のためコードテストは不要。以下を確認する:

- `git diff --stat` が README.md / README.ja.md の 2 ファイルのみであること
- 記載した環境変数・フラグ・サブコマンドが実装（`skills/mission/bin/mission-state.py`、`skills/mission/SKILL.md`）に実在すること
