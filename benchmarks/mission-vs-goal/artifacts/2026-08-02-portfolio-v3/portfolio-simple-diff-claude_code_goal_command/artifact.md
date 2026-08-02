# Goal

`benchmarks/mission-vs-goal/fixtures/portfolio/exporter-lists.md` を読み、List A（deployed）に存在するが List B（documented）に存在しないエクスポーターを特定する。

# Result

List A（deployed）にあり List B（documented）に存在しない未文書化のエクスポーターは **`gamma-exporter`** の1件。

# Evidence

fixture `benchmarks/mission-vs-goal/fixtures/portfolio/exporter-lists.md` の内容（該当行をそのまま引用）:

List A (deployed):
- `alpha-exporter`
- `beta-exporter`
- `gamma-exporter`
- `delta-exporter`
- `epsilon-exporter`

List B (documented):
- `alpha-exporter`
- `beta-exporter`
- `delta-exporter`
- `epsilon-exporter`

比較結果:
- List A の5件中、List B に同名の記載があるもの: `alpha-exporter`, `beta-exporter`, `delta-exporter`, `epsilon-exporter`（4件、確認済み）
- List A にあり List B に記載がないもの: `gamma-exporter`（1件、確認済み）
- List B にのみ存在し List A にないもの: なし（確認済み。逆方向の差分は本タスクの対象外）

# Assumptions

- fixture 内の項目名（例: `gamma-exporter`）はそのまま識別子として扱い、表記揺れ（大文字小文字・別名）は無いものとして比較した。表記揺れの有無自体は未確認。
- fixture ファイルは指示された1ファイルのみを読み、他のベンチマークメタデータ（タスク定義・採点設定・答えキー）は参照していない。

# Stop Condition

以下をすべて満たした時点で完了とする:
- 本アーティファクトが `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-simple-diff-claude_code_goal_command.md` に存在する（本ファイル自体が証跡）。
- Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含む（本ファイルに全て含まれる）。
- 未文書化のエクスポーター（`gamma-exporter`）を明示している（Result・Evidence 節に記載済み）。
