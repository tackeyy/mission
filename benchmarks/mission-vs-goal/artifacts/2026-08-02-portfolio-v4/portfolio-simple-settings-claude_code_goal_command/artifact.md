# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v4/portfolio-simple-settings-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Read exactly `benchmarks/mission-vs-goal/fixtures/portfolio/settings-registry.md`. The reference usage notes must only reference constants that exist in the registry table. Check every referenced constant and report each reference that points at a constant not present in the registry, quoting the exact name.

# Result

3 件のダングリング参照（レジストリに存在しない定数への参照）を確認した。

## 確認済み（Confirmed）— ダングリング参照

1. `CFG_B027_LIMIT_MS` — Legacy importer が参照。レジストリには `CFG_B027_TTL`（値 289）は存在するが、`CFG_B027_LIMIT_MS` という名前の行は存在しない。
2. `CFG_Q084_TTLX` — cache warmer が参照。レジストリには `CFG_Q084_...` で始まる定数自体が存在しない（該当インデックス 084 の定数は `CFG_G084_TIMEOUT`、値 688、のみ）。
3. `CFG_ZZ999_RATE` — rate governor が参照。レジストリにこの名前・接頭辞（`ZZ999`）の定数は存在しない。

## 却下（Rejected）— レジストリに実在すると確認できた参照

以下は Reference Usage Notes に列挙されているが、レジストリ表に完全一致する行が存在するため、ダングリングではないと判定した。

- `CFG_A000_TIMEOUT`（stage 0）— レジストリに存在（値 100）
- `CFG_I008_SIZE`（stage 1）— レジストリに存在（値 156）
- `CFG_Q016_RATE`（stage 2）— レジストリに存在（値 170）
- `CFG_Y024_TIMEOUT`（stage 3）— レジストリに存在（値 268）
- `CFG_G032_SIZE`（stage 4）— レジストリに存在（値 324）
- `CFG_W048_TIMEOUT`（stage 6）— レジストリに存在（値 436）
- `CFG_E056_SIZE`（stage 7）— レジストリに存在（値 492）
- `CFG_M064_RATE`（stage 8）— レジストリに存在（値 548）
- `CFG_U072_TIMEOUT`（stage 9）— レジストリに存在（値 604）
- `CFG_C080_SIZE`（stage 10）— レジストリに存在（値 660）
- `CFG_K088_RATE`（stage 11）— レジストリに存在（値 716）
- `CFG_S096_TIMEOUT`（stage 12）— レジストリに存在（値 772）
- `CFG_A104_SIZE`（stage 13）— レジストリに存在（値 828）
- `CFG_I112_RATE`（stage 14）— レジストリに存在（値 884）

# Evidence

フィクスチャファイル `benchmarks/mission-vs-goal/fixtures/portfolio/settings-registry.md` を全文読み込み、レジストリ表（1行目〜124行目、`CFG_A000_TIMEOUT` 〜 `CFG_P119_DEPTH` まで全120行）と Reference Usage Notes セクション（126〜147行目）を突合した。

- レジストリ表の全定数名を目視で一覧化し、各参照を1件ずつ照合した。
- `CFG_B027_LIMIT_MS` の照合根拠: レジストリ32行目は `| CFG_B027_TTL | 289 |` であり、`CFG_B027_LIMIT_MS` という文字列は表中のどこにも出現しない。
- `CFG_Q084_TTLX` の照合根拠: レジストリの連番パターン（`_A000_` 〜 `_P119_` の4文字ローテーション: TIMEOUT/LIMIT/SIZE/TTL/RATE/DEPTH）から該当インデックス084は89行目 `| CFG_G084_TIMEOUT | 688 |` のみ。`CFG_Q084_TTLX` という文字列は表中に出現しない。
- `CFG_ZZ999_RATE` の照合根拠: レジストリ全120行を確認したが `ZZ999` という接頭辞・番号を持つ行は存在しない。

# Assumptions

- レジストリ表内で定数名の重複や表記揺れ（大文字小文字違いなど）はないものと仮定した（目視確認の範囲では該当なし）。
- 「存在する」の判定基準は、参照文字列とレジストリ表の `Constant` 列の値が完全一致することとした（部分一致・接頭辞一致は不一致として扱った）。
- パフォーマンス・自動化ツールは使用せず、目視+手動照合で全120行×17参照を確認した。見落としのリスクは非ゼロだが、複数回読み直して確認した。

# Stop Condition

本タスクは完了した。artifact は指定パス `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v4/portfolio-simple-settings-claude_code_goal_command.md` に作成済みで、Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含む。ダングリング参照 `CFG_B027_LIMIT_MS`、`CFG_Q084_TTLX`、`CFG_ZZ999_RATE` の3件をすべて報告し、確認済み参照14件との切り分けも明記した。これ以上の追加作業は不要。
