# Mission 実行速度分析 2026-08-07 (portfolio-v5-speed 追跡)

Refs #349

## 結論

速度改善メカニズム (adaptive routing #276/#325/#330、diff-review #240/#309/#326、bounded context #241、tier 較正 #266、transactional command #283、並列レビュー検証 #338、ターン圧縮 #339) はほぼ実装済みである。残る問題は次の 4 系統に集約され、いずれも品質ゲート (threshold 4.0 / min_item 3.5 / open_high=0 / agreement / 必須 evidence) を変えずに削減できる。

1. **guidance 層バイパス**: orchestrator が prose 指示に従わず機構が発火しない (4 回目の実測)
2. **レビューフェーズが最大コスト項**: reviewer subagent の出力が orchestrator を上回る
3. **executor の成果物スタブが iteration 2 を誘発**: 予防可能な約 9 分 / 22 turn
4. **ベンチ計測規律**: repeats=1 で効果判定しており、自らの検証契約 (N>=3 計測検証 / N>=10 本採用) に反する

## v4 → v5-speed の「遅くなった」の再解釈

同一 5 タスク (Standard 3 + Complex 2) 比較で mission 平均は 6.49 分 → 10.37 分に見えるが、大半は測定アーチファクトである。

| task | v4 | v5-speed | 差の実体 |
|---|---:|---:|---|
| std-contract | 9.27 min (iter=1) | 18.27 min (iter=2) | ゲート再作業発火 (成果物スタブ起因、下記) |
| std-metrics | 4.83 min | 6.02 min | n=1 分散内 |
| std-policy | 10.16 min | 4.84 min | 唯一の並列発火 record。約 1/2 に短縮 |
| cx-config | 5.20 min | 8.33 min | n=1 分散内 |
| cx-ledger | 2.99 min (iter=0) | 14.38 min (iter=1) | v4 はレビュー未実行の evidence-submitted halt (過小測定)。v5 が初の真正フルループ (#341 効果) |

v4 の cx-ledger は reviewer モデルの modelUsage が存在せず (`mission_passes=false`)、レビューパイプラインを一度も起動していない。14.38 − 2.99 = 11.4 分の差は「遅くなった」のではなく v4 が測っていなかったレビューフェーズそのものである。

## 実測ボトルネック (v5-speed トランスクリプト)

### 1. guidance 層バイパス (最優先)

- #339 command_sequence: 不発。turns 26-53 で削減なし。orchestrator が `next` を参照しない既知経路 (portfolio-v2 / disc-v3 / portfolio-v1 に続く 4 回目)
- #338 並列レビュー: 機械観測 (reviewer-window) ベースの発火は 5 record 中 1。発火した std-policy は同 task 比約 1/2 の時間。成果物の自己申告 (「並列実行」) と機械観測が乖離しており、自己申告は信頼できない
- 対応: #309→#326 の前例 (prose → next 層 → aggregation 層 hard gate) に従う段階 gate 化 → #350

### 2. レビューフェーズのコスト

- reviewer subagent (レビュー用モデル) の出力トークンが orchestrator を上回る: std-contract 37,993 vs 28,839、cx-ledger 41,136 vs 12,580
- reviewer 1 invocation あたり cache-creation 179k-250k tokens は fresh の大量 context 投入を示唆。#241 bounded context の不発の可能性 (速度監査 F10 の本丸) → #352
- #281 の出力有界化は prose のみで無検証 → #353

### 3. iteration 2 の予防 (std-contract)

- iter1 失敗の原因は分析品質ではなく成果物の構造不備: Score/Stop Decision を「review-finalize 後に記録」という forward-reference スタブのまま残し、Step 3 見出しの本文が空。High finding (VCC-1) + completeness 軸 agreement delta 1.70 でゲート不通過
- iter2 は差分再レビューのみ (分析の再実行なし) で composite 4.46→4.56、open_high 1→0
- 機械 lint で予防可能 → #351

### 4. 計測規律

- v5-speed は repeats=1。fail-first 設計タスクの iter=2 と n=1 分散が平均を支配しており、この母集団で「#338/#339 は効果なし」と結論するのは unsupported (report.ja.md の注意書きどおり)
- 2026-08-07 に同一 5 タスク・repeats=3 の portfolio-v6-repeats3 を起動済み。以後の速度効果判定はこの分散を基準線とする
- 速度監査 (reports/mission-execution-speed-audit-2026-07-22.md) の未完項目: F1 fenced session lease (P0) → #354、F6 incremental snapshot、F4 evidence-provider pilot、F2 activity coverage 95% の受入実測

## Codex goal mode との関係 (2026-08-07 ファクトチェック)

- Codex CLI にも goal mode が実装済み (2026-05-21 GA、thread state として goal を保持)。ローカル codex-cli 0.146.0 にも thread_goals ストアが存在することを確認
- mission の adaptive routing は「/goal コマンドの起動」ではなく goal 契約 5 見出しでの inline 完遂であり、ホストのコマンドに依存しない (SKILL.md)。CC / Codex で挙動は同一
- Simple タスクのディスパッチ先を設定で選べる provider 設計 (`goal_dispatch: inline | host-native`) を #355 として起票。既定は inline (現行挙動・後方互換)、host-native はホスト機構欠如時に inline へ fail-safe。cross-host 起動は既定では提供しない (OSS ポータビリティ原則)

## 対応 Issue

親 #349 / 子: #350 (reviewer-window gate 化)、#351 (成果物スタブ lint)、#352 (bounded context 発火実測)、#353 (reviewer 出力検証)、#354 (fenced session lease)、#355 (goal dispatch provider)

## 一次データ

- `benchmarks/mission-vs-goal/results/2026-08-02-portfolio-v5-speed.jsonl` / `-summary.json`
- `benchmarks/mission-vs-goal/artifacts/2026-08-02-portfolio-v5-speed/` (各 record の claude-result.json / artifact.md)
- `benchmarks/mission-vs-goal/report.ja.md` portfolio-v5-speed 節
- `reports/mission-execution-speed-audit-2026-07-22.md`

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-07 | 初版作成 (v5-speed トランスクリプト分析 + 速度監査照合 + Issue #349-#355 起票) |
