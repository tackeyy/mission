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

## review_agreement (独立フィールド)

「複数レビュアーのスコア分散がどれだけ小さいか」。これは `items` ではなく score entry の独立フィールドであり、composite には含めない。

| 状況 (4軸の max-min 最大値) | review_agreement |
|---|---|
| ≤ 0.5 | 5 |
| ≤ 1.0 | 4 |
| ≤ 1.5 | 3 |
| ≤ 2.0 | 2 |
| > 2.0 | 1 |

`aggregate-reviews` は `agreement_detail` に軸別 min/max/delta を保存し、Reviewer 1名の場合は `review_agreement: null` にする。

`mark-passes` の gate:
- max delta > 1.5: exit 2。争点軸の追加レビュー 1 名を実施して再集計する。
- max delta > 1.0: WARN のみ。
- 旧 `reviewer_consensus` 入り score entry は履歴として読むが、新規 `aggregate-reviews` 出力の `items` には含めない。

---

## 算出フロー

1. 各 Reviewer が項目1-4を独立に採点し、`mission-review/1` JSON に findings を含める
2. `aggregate-reviews` が項目5を算出（分散ベース）。Reviewer 1名のみの場合は項目5を省略
3. 各項目の最終スコア = reviewer スコア平均（High/Medium/Low findings による rubric cap 適用後）
4. composite_score = mean(4軸 items: mission_achievement / accuracy / completeness / usability)
5. 判定:
   - `findings_evidence_path` が存在し、evidence 内の High 件数が `open_high` と一致することを `mark-passes` が再照合
   - `agreement_detail` の max delta が 1.5 以下であることを `mark-passes` が確認
   - `composite_score >= threshold` AND `min(採点した items) >= 3.5` AND `open_high == 0` → 合格
   - それ以外 → 不合格 → Critic で改善案

## 改善優先順位（Critic への入力）

- 最も低い項目から優先的に改善
- 足切り（3.5未満）の項目は最優先
- 改善見込みが大きい順（current vs 5点 のギャップ）
