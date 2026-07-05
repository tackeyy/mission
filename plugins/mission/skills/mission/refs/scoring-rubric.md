# Scoring Rubric — /mission の5項目×5点評価基準

合格条件: **平均スコア ≥ threshold（デフォルト4.0）** かつ **全項目 ≥ 3.5（足切り）**

---

## 絶対評価原則 (2026-05-26 追加)

**5点 = 改善余地ゼロ・残存 Issue ゼロ**。Reviewer が `Issues` セクションに 1 件でも (Low でも) 記載した時点で、その項目に対応するスコアは 5 にならない。

| 残存 Issue (該当項目分) | 該当項目の最大スコア |
|---|---|
| High 1 件以上 | 3 (足切り近辺) |
| Medium 1-2 件 | 4 (合格圏内・要改善) |
| Medium 3 件以上 | 3.5 |
| Low 1 件 | 4.7 |
| Low 2-3 件 | 4.5 |
| Low 4 件以上 | 4.3 |
| 残存なし | 5.0 |

「前イテレーションの指摘解消確認」は採点軸ではなく **絶対評価の補助情報**。Iter N-1 で指摘した点が解消されても、Iter N で新たな Low が見つかれば該当項目は 5 にならない。

**Maker-Checker バイアス警告**: orchestrator が executor と reviewer を同一セッションから spawn する場合、reviewer は「指示された修正の確認」モードに陥りやすく、絶対評価が甘くなる。`aggregate-reviews` は reviewer findings 件数とスコアを突合し、ペナルティ上限を適用する。

---

## 1. ミッション達成度 (Mission Achievement)

「ユーザーが指定したミッションに、どれだけ近づいたか」

| 点 | 判定 |
|---|---|
| 5 | ミッションが完全に達成され、追加の宿題が残っていない |
| 4 | ミッションはほぼ達成。軽微な未完事項のみ |
| 3 | 主要な部分は達成。重要な未完事項が1-2個ある |
| 2 | 部分的にしか達成できていない |
| 1 | ミッションから乖離している |

### 先送り減点 (2026-05-26 追加)

未完項目を「依頼書発行」「別タスク化」「next iter で対応」等で mission の scope 内に残したまま採点する場合、その件数で Mission Achievement の上限を制限する。

| scope 内に残った未完項目 | Mission Achievement 上限 |
|---|---|
| 0 件 (全件本セッション内で完遂) | 5 |
| 1-2 件 | 4 |
| 3-5 件 | 3 |
| 6 件以上 | 2 |

**「先送り」の定義**: 元の mission 文に含まれる項目で、本セッション内で具体的成果物 (ファイル・コード・データ) として形にならず、TODO / 依頼書 / 別 issue として書き残した項目。

**「scope 外」の定義** (減点対象外): 元の mission 文に含まれない発見事項を別タスクへ切り出した場合。元 mission 文と照合して判定する。

### 反面教師ケース (2026-05-25 19:28 セッション 5f182cd8)

- mission: 「品質チェック + 未充足項目の追加リサーチ + マスター v08.27 反映」
- 結果: 品質チェック 6 ゲート完走、即時修正 3 件 → **残 6 件は依頼書 v04 (R37-R40) へ集約**
- 当時の採点: Mission Achievement **4.5** で composite 4.52 一発合格
- 本ルール適用時: 6 件先送り → Mission Achievement 上限 **2** → composite ~3.5 → threshold 4.0 未達で **iter2 強制**、実際に R37-R40 を完走させてから合格すべきだった

## 2. 正確性 (Correctness)

「事実誤認・論理破綻・バグがないか」

| 点 | 判定 |
|---|---|
| 5 | 事実誤認・論理破綻・バグなし。テストやログで検証済み |
| 4 | 軽微な不確実性はあるが、致命的な誤りはない |
| 3 | 1-2個の誤りまたは未検証箇所がある |
| 2 | 複数の誤りまたは未検証の不安要素がある |
| 1 | 重大な誤り、または検証不能 |

## 3. 完成度 (Completeness)

「抜け漏れ・未完タスクの有無」

| 点 | 判定 |
|---|---|
| 5 | 全サブタスクが完了。エッジケース・テスト・ドキュメントも揃う |
| 4 | 主要なサブタスク完了。エッジケース1-2個が未対応 |
| 3 | 主要部分は完了。一部のサブタスクが未着手 |
| 2 | サブタスクの半分以上が未完 |
| 1 | ほぼ未着手 |

エラーパス、性能劣化、セキュリティ境界、運用手順の抜け漏れも完成度で評価する。Critical/Complex の未解決 High は採点軸を増やさず、`aggregate-reviews` が reviewer findings から `open_high` と `findings_evidence_path` を生成し、`mark-passes` が evidence の High 件数と `open_high` を再照合して不合格にする。

## 4. 実用性 (Usability)

「ユーザーが即座に使える状態か」

| 点 | 判定 |
|---|---|
| 5 | そのまま本番投入可能。ドキュメント・実行手順含む |
| 4 | 微調整なしで使える。説明補足があれば理想 |
| 3 | 使えるが、ユーザー側で手直しが必要 |
| 2 | 使うには大幅な追加作業が必要 |
| 1 | 現状では使用不能 |

## 5. レビュアー合意度 (Reviewer Consensus)

「複数レビュアーのスコア分散がどれだけ小さいか」

| 点 | 判定 |
|---|---|
| 5 | 全レビュアーが各項目で ±0.5 以内に収束 |
| 4 | 大半の項目で ±1.0 以内 |
| 3 | 一部の項目で意見が分かれる（±1.5） |
| 2 | 多くの項目で意見が分かれる |
| 1 | 評価が大きく分裂、何が正解か判断不能 |

Reviewer 1名のみの場合（Simple複雑度を含む）は、`reviewer_consensus` を採点 items から**省略**する。自己一貫性チェックは品質確認として notes に記録してよいが、consensus は複数 Reviewer 間の合意度を測る指標であり、1名では検証できない。

consensus 算出では、同一イテレーション内でインライン修正前に出た古い採点値を除外し、修正後成果物を読んだ Reviewer の値だけを使う。scoring-output には reviewer ごとの max-min 差分表と、除外した古い値があればその理由を記録する。

### 差分レビュー周回 (iter 2 以降・検証 1 名) の合意度 — 据置禁止 (M5, 2026-06-10)

- 前 iter の consensus 値をそのままコピーする「据置」は**禁止**（絶対評価原則 P3-3 に違反。2026-06-10 のログ分析で「前iter据置」の実例を確認）
- 検証 Reviewer 1 名の周回では items から consensus を**省略**する（composite / min_item は残り 4 項目で算出し、その旨を notes に明記）
- 前 iter の consensus 値と同値になるような再算出・コピーは行わない

### M5 consensus 規律 — 差分レビュー(検証1名)周回での高得点禁止 (Issue #4, 2026-06-15)

差分レビュー周回（iter 2 以降、検証 Reviewer 1 名のみ）では、**reviewer_consensus に高得点（4.5 以上）を付けてはならない**。
1 名固定で consensus 5.0 を付けるのは**違反**。理由: consensus は複数 Reviewer 間の合意度を測る指標であり、
1 名では合意を検証できない。

| 状況 | 正しい処置 |
|---|---|
| 差分レビュー 1 名 | `--items` から `reviewer_consensus` を**省略**する（composite/min_item は残り4項目で算出し notes に明記） |
| Simple 複雑度・通常 1 名レビュー | `--items` から `reviewer_consensus` を**省略**する（自己一貫性チェックは notes に明記可） |
| 前 iter 値のコピーペーストだけ | 禁止（据置禁止ルールに加えて、上記禁止も適用） |

#### 違反例（絶対に行わない）

```bash
# NG: 差分レビュー1名で consensus=5.0 を付けている
python3 ... push-score --iteration 2 --composite 4.76 --min-item 4.5 \
    --items '{"mission_achievement":4.8,"accuracy":4.9,"completeness":4.7,"usability":4.6,"reviewer_consensus":5.0}' \
    --notes "iter2 差分検証1名"
#                                                                       ^^^^^^^^^^^^^^^^^^^^^ 違反: 1名で5.0は不正
```

#### 正例（consensus 省略）

```bash
# OK: consensus を省略し、4項目で算出する
python3 ... push-score --iteration 2 --composite 4.75 --min-item 4.6 \
    --items '{"mission_achievement":4.8,"accuracy":4.9,"completeness":4.7,"usability":4.6}' \
    --notes "iter2 差分検証1名: consensus 省略・composite/min_item は4項目で算出"
```

省略時の push-score 具体例 (C-M1)。`--items` は 4 キー、`--min-item` はその 4 項目の最小値を渡す
(mark-passes の gate は「採点した items」基準なので 4 項目でも整合する):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py push-score --iteration 2 --composite 4.13 --min-item 3.9 \
    --items '{"mission_achievement":4.5,"accuracy":3.9,"completeness":4.1,"usability":4.0}' \
    --notes "iter2 差分検証1名: consensus 省略・composite/min_item は4項目で算出"
```

---

## 算出フロー

1. 各 Reviewer が項目1-4を独立に採点し、`mission-review/1` JSON に findings を含める
2. `aggregate-reviews` が項目5を算出（分散ベース）。Reviewer 1名のみの場合は項目5を省略
3. 各項目の最終スコア = reviewer スコア平均（High/Medium/Low findings による rubric cap 適用後）
4. composite_score = mean(採点した items)
5. 判定:
   - `findings_evidence_path` が存在し、evidence 内の High 件数が `open_high` と一致することを `mark-passes` が再照合
   - `composite_score >= threshold` AND `min(採点した items) >= 3.5` AND `open_high == 0` → 合格
   - それ以外 → 不合格 → Critic で改善案

## 改善優先順位（Critic への入力）

- 最も低い項目から優先的に改善
- 足切り（3.5未満）の項目は最優先
- 改善見込みが大きい順（current vs 5点 のギャップ）
