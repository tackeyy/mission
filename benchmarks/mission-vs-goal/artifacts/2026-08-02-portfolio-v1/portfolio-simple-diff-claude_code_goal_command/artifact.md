# Portfolio Simple Diff — claude_code_goal_command

## Goal

`benchmarks/mission-vs-goal/fixtures/portfolio/exporter-lists.md` に記載された List A（deployed）と List B（documented）を突き合わせ、List A には存在し List B には存在しない exporter を特定する。

## Result

List A（deployed）に存在し List B（documented）に存在しない exporter は **`gamma-exporter`** の1件のみ。

## Evidence

フィクスチャ（`benchmarks/mission-vs-goal/fixtures/portfolio/exporter-lists.md`）の内容:

- List A (deployed):
  - `alpha-exporter`
  - `beta-exporter`
  - `gamma-exporter`
  - `delta-exporter`
  - `epsilon-exporter`
- List B (documented):
  - `alpha-exporter`
  - `beta-exporter`
  - `delta-exporter`
  - `epsilon-exporter`

突き合わせ結果:

| exporter | List A | List B | 判定 |
|---|---|---|---|
| `alpha-exporter` | あり | あり | 確認済み（差分なし） |
| `beta-exporter` | あり | あり | 確認済み（差分なし） |
| `gamma-exporter` | あり | **なし** | **未文書化（差分あり）** |
| `delta-exporter` | あり | あり | 確認済み（差分なし） |
| `epsilon-exporter` | あり | あり | 確認済み（差分なし） |

### 確認済みの発見（Confirmed findings）

- `gamma-exporter` — List A の3行目に記載（`- gamma-exporter`）、List B には対応する行が存在しない。List A にのみ存在する未文書化の exporter。

### 却下した候補（Rejected candidates）

- なし。List A の5件中4件（`alpha-exporter`, `beta-exporter`, `delta-exporter`, `epsilon-exporter`）は List B にも記載があり、差分候補として検討する余地がない（両リストに完全一致で存在）。

## Assumptions

- フィクスチャファイルの内容は本タスク実行時点（2026-08-02）のものであり、以降の変更は反映されない。
- exporter 名の一致判定は文字列の完全一致（大文字小文字・空白の差異なし）で行った。フィクスチャ上に表記揺れ（別名・エイリアス）は見当たらなかった。
- List A と List B 以外に「デプロイ済みだが記載されていない」exporter が存在するかどうかは、本フィクスチャの範囲外であり未確認（unmeasured）。

## Stop Condition

以下をすべて満たした時点で本タスクは完了とする:

- 本アーティファクトが `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-simple-diff-claude_code_goal_command.md` に作成されていること（本ファイル）。
- Goal / Result / Evidence / Assumptions / Stop Condition の5見出しがすべて含まれていること。
- List A にのみ存在する未文書化 exporter（`gamma-exporter`）が名指しで明記されていること。

上記3条件を満たしたため、本タスクは完了。
