# Design: learning briefコマンドと注入規律（Issue #457）

Issue 本文をそのまま設計とする:

Related #420（SLO の残変数 = iter1 合格率）/ Refs #418（failure ledger 基盤）

# 問題

#418 の failure ledger は reviewer の `general_fix_rule` を state に蓄積するが、**蓄積のみで planner / executor へ届く注入点がない**。結果、同種の指摘が別 mission で再生産される（2026-08-13 の実運用 2 run で 29 件・ユニーク 18 種の general_fix_rule を確認。iter1 が gate reject された run では、指摘 9 件の大半が「既知の作法」だった）。

iter1 合格率が上がれば mission 完了時間は品質ループ 1 周分（実測 20 分前後）短縮され、10〜15 分 SLO の実測達成に直結する。

# 提案（codex 委譲可能な粒度）

1. **read-only サブコマンド `learning brief [--weak-phase <phase>] [--limit N] [--json]` を追加**: 同一 state root の全 session（sessions/ + archive の terminal state）から `failure_ledger` エントリを集計し、再発回数降順で正規化済み general_fix_rule を最大 N 件（既定 10）出力する。lease 不要・state mutation なし。rule 本文は state 内に既存保持のため新たな情報露出はない（stats が出力しないのは集計コンパクト性の意図であり、brief は明示要求時のみ出力）
2. **SKILL.md の Phase 2-3 に注入規律を追記**: planner（inline 含む）は plan 作成前に `learning brief --weak-phase planning` を、executor への委譲 brief には `learning brief --weak-phase execution` の出力を含める（0件なら省略）。reviewer への注入は行わない（採点の独立性維持）
3. `refs/state-management.md` の failure ledger 節へ brief の仕様を追記
4. テスト: 複数 session 集計・weak_phase フィルタ・再発降順・limit・空 ledger・archive generation からの読み取り・plugins ミラー同期

# 受け入れ条件

- [ ] `learning brief` が複数 session 横断で再発降順の rule を返す
- [ ] SKILL.md / refs に注入規律が明文化され、reviewer 独立性への非注入が明記される
- [ ] gate 意味論不変（brief は観測・guidance のみ）
- [ ] 既存テスト全緑・ミラー同期


## 補足実装メモ
- 集計元: sessions/*.json の failure_ledger（mission-failure-ledger/1）+ archive の terminal state（generation 経由の読み取りは #391 の材料化 canonical 記録を使う。読めない generation は skip し fail-safe）
- 出力形式（--json）: {"schema": "mission-learning-brief/1", "rules": [{"general_fix_rule": str, "weak_phase": str, "recurrence": int, "sessions": int}]}。非 json は 1 行 1 rule
- weak_phase enum は review_learning.py の定義（understanding|planning|execution|formatting）に従う
- lane-report / cmd_list の走査パターンを踏襲し read-only・lease 非取得
- SKILL.md 追記は Phase 2-6 の planner/executor 段落に各1文（context 規律 #285 に反しない簡潔さ）
- conftest run_cli 使用・plugins ミラー同期・SYNC_PAIRS 確認
