# mission 実行ログ全量 × ソースコード突き合わせレビュー — 2026-07-02

## 0. 位置づけと調査範囲

本レビューは、既存の分析資産と観点を重複させず、**実行ログの全量観測から逆算してアーキテクチャの妥当性を評価**する。

| 既存資産 | 観点 | 本レビューとの関係 |
|---|---|---|
| `docs/audit-2026-07-02.md` | 静的解析 + 実証（ゲートバイパス可能性、コードバグ F-1〜F-18） | 前提として引用。重複指摘はしない |
| `output/mission-log-audit-20260628/` | ログ横断監査（cutoff 6/26） | 本レビューで cutoff 6/28 に更新して再実行 |
| `docs/ontology-redesign-analysis.md`（6/17） | ゼロベース再設計の思想分析 | ログ証拠で各提案の必要性を検証 |
| `docs/adr/002-typed-mission-state-objects.md`（#101） | typed state objects の段階導入 | ログ証拠で不足点を指摘 |

調査対象:

- **定量**: `scripts/mission-audit.py --current-since 2026-06-28` を再実行（roots: `~/dev`, `~/workspace`, `~/.codex/worktrees`）→ **426 sessions**（pass 403 / halt 19 / incomplete 2 / abandoned 2、pass rate 94.8%）
- **質的深掘り**: 10 プロジェクトの `.mission-state`（sessions 計 172、archive 計 545、~8.7MB）を並列サブエージェント 2 体で精読
- **ソース**: worktree（main 相当、1.0.5 + #101）を並列サブエージェント 1 体で精読

---

## 1. エグゼクティブサマリー

**結論: 個別バグの修正フェーズは終わりに近い。ログが示す残存問題はすべて「LLM の散文指示遵守に品質を依存する」という単一のアーキテクチャ前提に収斂しており、ADR-002 の方向（typed objects + CLI ゲート化）は正しいが、それだけでは 3 つの穴（採点の独立性・identity 層・Codex-first）が残る。**

ログが実証した最重要ファクト:

1. **品質ループはほぼ「1周の儀式」になっている** — iteration≥2 到達は精読 100 sessions 中 10 件（10%）。全 220 スコアエントリの 96.8% が閾値 4.0 以上、71.4% が 4.4〜4.7 に密集。改善ループが起動するのは初回スコア 3.9 以下の場合のみ（実績 2.7%）。
2. **レビュー本文なしの自動生成スコアの方が高い** — `generated=true`（push-score フォールバック生成、本文なし）22 件の平均 4.54 > 本文ありの 4.41。エビデンスとスコアが逆相関気味という、自己採点インフレの直接証拠。
3. **specialist pipeline は名目化** — 呼び出し記録 192 件中 74% が `skipped`、100 sessions 中 82 件で `specialists_selected=[]`。最多 mode は `fallback-core`（93 件）＝orchestrator 自身が review を兼務。Maker-Checker の独立性が実態として存在しない。
4. **state 書き込みに 2 ルートが並存** — CLI 経由（archive に session_id 付き正規配置）と LLM 直書き（`.mission-state/` ルートに散乱、命名 3 系統、session_id 紐付けなし）。散乱ファイルは 3 プロジェクトで 42 件 + social-foundry で 34 件。
5. **実行の 84% は Codex**（codex 357 / claude-code 67 / unknown 2）なのに、ループ強制の要である Stop hook は Claude Code 前提。
6. **修正 → ガード追加 → それでも漏れる、のループが監査自体を肥大させている** — audit.json のトップレベルキーは 80 超、finding タイプ 12 種。cutoff 6/28 以降（#78〜#101 適用後）でも candidate-only 11 件、selection checkpoint 欠落継続、low-score-pass 20 件、slow 21 件が新規発生。

---

## 2. ログが実証した構造問題（既存監査に上乗せする新規 finding）

### L-1. スコア分布の崩壊（High）

- 母集団 220 スコアエントリ（エージェント精読分）: mean 4.51、**4.4〜4.7 帯に 71.4%**、閾値 4.0 未満は 3.2%。
- audit.py 全量でも `avg_final_composite 4.51`、current low-score-pass（4.3 未満で pass）は 20 件のみ。
- `docs/audit-2026-07-02.md` §1.1 が指摘した「iteration 1 で 4.x を自己付与 → early-stop で確定」の構造バイアスを、**分布として実証**した形。
- iter≥2 で **スコアが 1 点も動かない pass** が複数（[4.2→4.2], [4.3→4.3], [4.5→4.5], [4.6→4.6]。iter2 の notes が iter1 と完全同文の例も）。stagnation_count は増分されるが、閾値超えなので pass する。「改善ループ」が改善を測定できていない。
- 対照的に、**ループが機能した例も 5 件確認**（3.40→4.65、3.62→4.48 等、High 指摘→修正→再検証の実チェーン）。つまりループ機構自体は有効で、**問題は「初回採点が甘いとループが起動しない」ことに集中**している。

### L-2. iteration 意味論の崩壊（High）

- social-foundry Epic #463: 5 iterations でスコアが 4.44→4.38→4.28→4.30→4.22 と**微減しながら** pass。iteration が「品質改善のリトライ」ではなく「子 issue の進捗カウンタ」として流用されている。
- この流用が起きると score_history・stagnation・early-stop の全セマンティクスが壊れるが、現行スキーマはこれを表現も禁止もできない。

### L-3. specialist accounting の「会計は揃ったが実態がない」問題（High）

- 6/28 以降の修正で `unresolved_confirm` 0 件、`blank_invocation` 0 件など**帳簿は綺麗になった**。しかし中身は: 192 invocations 中 `completed` は 8 件（4%）。`inline-applied` 40 件、`skipped` 143 件。
- つまり #64/#69/#70/#71/#78/#81 で強化してきた specialist 会計は「skip した証跡を残す」方向に収束し、**外部の独立レビューが実際に走る率はほぼ変わっていない**。会計の完備と品質の独立性は別問題であることをログが示した。

### L-4. 成果物書き込みの二重ルート（Med-High）

- 正規ルート: `push-score --scoring-output` → `archive/iter-N-<mid8>-scoring.md`（`<!-- mission-meta: session_id=... -->` ヘッダー付き）。
- 非正規ルート: LLM が `.mission-state/` ルートへ直接 Write。42 件（mission 7 / navibot 11 / workspace 24）+ social-foundry 34 件（issue-comment-*.md 20 件、issue-bodies/ 等）。命名は `iter-1-<slug>-scoring.md` / `mission-scorer-iter-1-<slug>.md` 等 3 系統が乱立し、session_id 紐付けなし。同一内容が root と archive に重複する例もある（workspace `iter-1-06251ffb-scoring.md`）。
- `.mission-state/` が「gitに出ない便利なスクラッチパッド」として学習されており、**state ディレクトリの意味論（監査可能な実行記録）が侵食**されている。audit/stats はルート散乱ファイルを一切拾わない。
- `aggregate.json` は 3 プロジェクトとも `{"active_sessions": [], "updated_at": ...}` のみの空骸で、設計意図（集計）を果たしていない。

### L-5. project identity の断片化（Med-High）

- audit の by_project に `followers-x-profile-links`、`thread-mode-ux`（実体は social-foundry の `.worktrees/` 配下）や `ecstatic-chebyshev-b577df`（自動生成 worktree 名）が独立プロジェクトとして多数並ぶ。
- 1 worktree = 1 project 扱いのため、(a) プロジェクト横断の集計が断片化、(b) cross-session の重複検出（ADR-002 の issue_ref が狙う機能）が worktree を跨ぐと成立しない、(c) worktree 削除で state ごと消える既知問題（P3-2）と併せ、**「mission の識別子体系に canonical project の概念がない」**ことが根因。

### L-6. スキーマバージョンの形骸化（Med）

- 全 sessions が `schema_version: 2` のまま、実際には `task_profile` / `specialists_mode` / `phase_durations_sec` / `specialist_invocations` 等の追加で**少なくとも 2 世代のフィールドセットが混在**（精読 100 件中 40 件が旧世代）。
- 採点 items も標準 5 項目から外れた session が 13 件（14%）: `reviewer_consensus` 欠落 4 件、独自項目への全面置換（`{publication, docs_updated, ...}` 等）。`normalize_score_items` はエイリアス変換のみで、**未知キーは WARN 素通り**（audit-2026-07-02 §1.3 と同根）。監査ツールが世代とバリアントを都度吸収するため、audit.py が 1,581 行に肥大している。
- **スケール異常の実例（深掘り確定）**: xai-cli の `cx-019efece`（Codex、`archive/worktree-codex-xai-media-extensions/`）が、iter1 で独自項目名 + **0-1 正規化スコア（composite 0.96 = 4.8/5）** を push、iter2 で項目名のみ修正（まだ 0.96）、iter3 で「5-point scale」と明記して 4.8 を push。3 回の push は 39 秒間の連続実行で、**スコア形式の修正に iteration を 3 消費**した。0.96 は 0-5 の範囲内のため `_validate_score_args` を素通り。なおミッション成果自体は xai-cli main に PR #29（`dbb9288 feat(media): support webp and mov uploads`）としてマージ済みで、実害はスコア統計の汚染に限られる。対策は ADR-002 Stage 1 の scale-anomaly reject（全 items ≤ 1.0 なら exit 2）として吸収。

### L-7. phase 計測が実態を反映していない（Med）

- `phase_durations_sec` を持つ session で planning n=75（mean 525s、max 4126s）に対し **executing n=9、reviewing n=7、scoring n=64**。実行フェーズがほぼ計上されず、経過時間の大半が planning に張り付く（audit の coarse-phase-attribution 14 件と整合）。
- 「planning が遅い」という監査所見は計測アーチファクトの可能性が高く、速度最適化の判断材料として使えない。
- orphan session（PID 死亡）は `updated_at` が cleanup 時刻で上書きされ、「21 時間 planning に滞留」なのか「即死して 21 時間後に回収」なのか区別不能（token-battle 1256 分、workspace 1997 分の例）。

### L-8. 複雑度に対する儀式コストの逆転（Med）

- zeimu-ai: `git rebase && push` 相当の作業（4 分）に planning/review/scoring の全儀式 + スコア 4.95。wedgeai 投稿分析（6 分）: reviewer 3 名招集、specialist 3 名全 skip。
- Simple 複雑度で reviewer_count=1 への縮小は効いているが、**「mission を使わない」という選択肢がフローに存在しない**。median 548s vs mean 2789s のロングテール分布は、軽タスクへの過剰適用と重タスクの混在を示す。

### L-9. Codex 主戦場とガード設計の乖離（High）

- 実行の 84% が Codex。Stop hook（ループ強制の唯一の機械ガード）は Claude Code hook 機構前提で、Codex では opt-in + compaction で指示が消えると二重喪失（audit-2026-07-02 §6.6）。
- missing scoring evidence の current 5 件はすべて `cx-*`（Codex）session。**品質ガードの実効性が agent 種別で非対称**という運用リスクがログで裏付けられた。

---

## 3. ソースとの突き合わせ: 根因の同定

| ログ上の症状 | ソース上の根因 |
|---|---|
| スコアインフレ / 本文なし高スコア | scorer は `context: fork` で state に書けず、**orchestrator がテキストから数値を転記**して `push-score` に渡す（mission-scorer/SKILL.md L184-191）。転記値と scorer 出力の一致検証なし。客観信号（テスト実行結果）は採点の必須根拠ではない |
| 空回り iteration / 意味論流用 | iteration の意味は SKILL.md の散文にのみ存在。`push-score` は iteration 番号の意味を検証しない |
| specialist skip 74% | 会計（log-invocation）は CLI 強制だが、**呼ぶかどうかは散文指示**。skip 証跡を残せばゲートを通る設計のため、最小抵抗経路が skip に収束 |
| ルート散乱 42+34 件 | scoring/レビュー成果物の書き込み先を強制する API がない。`--scoring-output` は任意で、省略時は本文なし `generated=true` を自動生成（皮肉にも正規ルートの方が証拠価値が低い） |
| identity 断片化 | `resolve_state_file` が cwd 起点。mission_id はミッション文字列ハッシュのみで、canonical repo / issue への参照が一級概念でない |
| schema 形骸化 | `schema_version` は整数リテラルのみ（型定義・検証ファイル不在）。フィールド追加時にバージョンを上げる規律も検証もない |
| phase 計測歪み | phase 遷移は各 cmd が任意に set する文字列で、FSM でない。executor が長時間走っても state 更新がなければ全時間が直前 phase に計上される |
| Codex 非対称 | ループ強制が Claude Code の Stop hook という**ハーネス固有機構**に結合している |

ソース精読での追加所見（audit-2026-07-02 未掲載分）:

- `mission-state.py` は **3,631 行・30 サブコマンド**まで肥大。LLM が呼ぶ CLI としては引数面が広すぎ、`push-score` は実質 6 引数 + JSON 文字列。
- `test_plugins_in_sync.py` の同期検証対象は **8 ファイルペアのみ**。`mission-planner/SKILL.md` や `mission-critic/SKILL.md` は対象外で、`plugins/` ミラーの drift を検知できない（現時点 drift ゼロは確認済み。ただし #101 の diff が示す通り全変更が2箇所コミットされ続けている）。
- Stop hook は dead-PID halt を `jq | mv` で直接書き込み、`StateLock`（fcntl.flock）を経由しない（コメントで意図的許容と明記。aggregate との不整合が §2.6 の残存経路）。
- specialist registry の手書き YAML パーサー（~65 行）は 1 段ネストまでしか対応せず、registry 仕様（`result_contract.forbidden_markers` 等）を完全に読めない。

---

## 4. ゼロベース・アーキテクチャ評価

### 4.1 現行アーキテクチャの実効性の検証結果

現行は 3 層で品質を守る設計になっている。ログによる各層の実効性判定:

| 層 | 設計上の役割 | ログでの実効性 |
|---|---|---|
| 散文指示（SKILL.md 420 行 + refs 1,720 行） | ループ手順・レビュー規律・specialist 活用 | **低**。reviewer 並列 0/7 遵守（既知）、specialist 実呼び出し 4%、scoring-output 省略・ルート直書き多発 |
| CLI ゲート（mark-passes / push-score） | 閾値未達の pass を機械拒否 | **中**。手続き強制は機能（ungated pass 0）。ただし自己申告値のみ検証（audit-2026-07-02 §1.2/1.3）で、値の真正性は守れない |
| Stop hook | ループ途中終了の防止 | **中**（CC のみ）。「終了を防ぐ」は機能するが「正しく進める」は守備範囲外。Codex 84% には効かない |

**ゼロベースで見直すべき核心はこの表そのもの**: 品質の実体（採点の妥当性・レビューの独立性・証跡の完全性）が、3 層のうち最も弱い「散文指示」層に載っている。

### 4.2 既存の再設計方針（ADR-002 / ontology-redesign）の検証

ログ証拠に照らすと、ADR-002 の方向（単一 JSON 内に typed objects を段階導入、CLI ゲート先行）は**正しい**。ontology-redesign-analysis の「散文の規律を型と Action の規律へ移す」という診断は、本レビューの L-1〜L-9 でほぼ全面的に裏付けられた。ただし、ログは既存方針がカバーしない 3 つの穴を示している:

**穴 1: typed objects でも「転記レイヤ」が残る限りスコアの真正性は守れない。**
Finding/Score をオブジェクト化しても、その値を orchestrator が scorer のテキスト出力から読み取って CLI 引数に書く構造が残るなら、捏造・転記ミス・インフレは通過する。必要なのはデータモデルではなく**評価情報の経路変更**: reviewer/scorer サブスキルが構造化出力（JSON）を直接ファイルに書き、orchestrator は「そのファイルパスを push-score に渡すだけ」にする。CLI がファイルから items を読んで composite を自己計算すれば、orchestrator の自由記述余地が消える（audit-2026-07-02 F-2 の warning 案より一段強い）。

**穴 2: identity 層の設計が存在しない。**
ADR-002 の issue_ref は session 単位の重複検出用で、(a) canonical project（worktree 群の親）、(b) mission 系列（Epic の子 issue 連続処理のような「1 mission ≠ 1 iteration 系列」）を表現できない。L-2（iteration 流用）と L-5（worktree 断片化）は同じ欠落の 2 症状。`Mission` に `canonical_root`（git common dir 由来）と `series_ref` を持たせ、audit/stats は canonical_root で集計する再設計が要る。

**穴 3: ガードの実行基盤が Claude Code 固有のまま。**
利用実態（Codex 84%）を踏まえると、「Stop hook が最後の砦」という設計自体を見直すべき。ハーネス非依存のループ強制は CLI 側にしか置けない: 例えば `mission-state.py` に heartbeat / `next` コマンドを設け、**「state を読めば次にやるべき 1 手が返る」**（ontology-redesign の接地ループの最小実装）を正とし、Stop hook は CC での追加保険に格下げする。これは compaction 復元・Codex 対応・SKILL.md 圧縮を同時に解決する。

### 4.3 ゼロベースで変えるもの / 残すもの

**残すもの（ログが有効性を裏付けたもの）**: 単一 session JSON + flock + atomic write、sessions/<sid> 分離、mark-passes の手続きゲート（ungated pass 0 の実績）、iteration ループ機構そのもの（起動しさえすれば +0.7〜+1.25 の実改善）、archive による証跡永続化。

**変えるもの（優先順）**:

1. **採点を「自己申告の数値」から「客観信号 + 構造化出力」へ**
   - reviewer に最低 1 つの機械検証（テスト/型/lint/再現コマンド）実行を必須化し、実行ログを Finding の evidence として構造化保存。未実行なら accuracy/completeness に上限（audit F-6 と同方向、ただし「散文で義務化」でなく**評価 JSON のスキーマ必須フィールド**にする）。
   - scorer 出力を JSON ファイル化し、composite/min_item は CLI が items から再計算。閾値も「スコア ≥4.0」単独でなく「open High findings = 0 かつ 必須 evidence 充足」を主、スコアを従に再定義する。スコアインフレ（L-1）はスコアを主ゲートにする限り消えない。
2. **接地ループの最小実装（`next` コマンド）** — 4.2 穴 3 の通り。SKILL.md の Compact Instructions・手順記述の大半を CLI 出力へ移し、Codex/CC 共通の進行保証にする。
3. **identity 層** — canonical_root / issue_ref / series_ref の 3 識別子。iteration は品質リトライ専用に戻し、連続タスクは series で表現。
4. **成果物 API 化** — scoring/レビュー/中間成果物の書き込みを `mission-state.py artifact put` 系に一本化し、`.mission-state/` ルート直書きは Stop hook / audit で検出して fail にする。`--scoring-output` 省略時の `generated=true` 自動生成は「本文なし高スコア」の温床なので**廃止し、evidence なし push-score を reject**。
5. **複雑度に応じた儀式の脱着** — Simple 判定時は planning/scoring を省く「lite パス」を正式化（reviewer 1 名 + 完了条件チェックのみ）。mission の価値が出るのは Complex/Critical であることがログで明確（L-8）。
6. **監査ディメンジョンの整理** — finding 12 種は typed Finding/Decision 導入後に「スキーマ違反」「ゲート違反」「運用逸脱」の 3 分類へ集約し、audit.py の肥大（1,581 行）を止める。

**やらないもの（ontology-redesign §4 の再確認）**: 分散ストレージ、常駐サービス、ファイル分割正規化、フル権限基盤。単機ローカルの単純さは維持。

---

## 5. 優先度付き改善バックログ（新規分のみ。F-1〜F-18 は audit-2026-07-02 参照）

| P | ID | 改善 | 根拠 | 規模 |
|---|---|---|---|---|
| P0 | G-1 | scorer/reviewer 出力の JSON ファイル化 + push-score の CLI 側 composite 再計算（転記レイヤ排除） | L-1, §3 | 中 |
| P0 | G-2 | `generated=true` フォールバック廃止 → evidence なし push-score を reject | L-1（本文なし 22 件が平均 4.54） | 小 |
| P1 | G-3 | `mission-state.py next`（state から次の 1 手を返す）+ Codex はこれを進行の正とする | L-9, 4.2 穴 3 | 中 |
| P1 | G-4 | pass 判定の主ゲートを「open High = 0 + 必須 evidence 充足」に変更、composite は従 | L-1, L-2 | 中 |
| P1 | G-5 | canonical_root（git common dir）で project identity を正規化、audit/stats を集計替え | L-5 | 中 |
| P1 | G-6 | `.mission-state/` ルート直書き検出（audit finding 化 + cleanup で隔離）、成果物 API 一本化 | L-4 | 中 |
| P2 | G-7 | iteration/series の分離（Epic 連続処理は series_ref） | L-2 | 中 |
| P2 | G-8 | Simple 用 lite パス（planning/scoring 省略）を正式化 | L-8 | 小 |
| P2 | G-9 | schema_version の実質運用（フィールド追加時に increment + CLI 検証、5 項目スキーマの reject） | L-6 | 小 |
| P2 | G-10 | phase 遷移の FSM 化 + executor 実行中 heartbeat（orphan の滞留時間を測定可能に） | L-7 | 中 |
| P3 | G-11 | plugins/ 同期テストを全ファイル網羅に拡張（8 ペア → skills/ 全体 hash） | §3 | 小 |
| P3 | G-12 | specialist registry の手書き YAML パーサー廃止 → JSON 一本化 | §3 | 小 |

推奨着手順: **G-2（小・即効）→ G-1 → G-4**（この 3 つでスコアインフレの構造原因を塞ぐ）→ G-3/G-5（アーキ転換の入口）。G-1/G-3/G-4 は ADR-002 の実装ステップとして統合可能であり、別 ADR を起こすより ADR-002 を改訂して「転記レイヤ排除」「next コマンド」「pass 条件の再定義」を Stage に追加するのが整合的。

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-07-02 | 初版作成。実行ログ全量（426 sessions / 10 プロジェクト .mission-state）× ソース（1.0.5 + #101）突き合わせ。既存監査（audit-2026-07-02 / 6-28 ログ監査 / ontology-redesign / ADR-002）に上乗せする新規 finding L-1〜L-9 とゼロベース評価、改善バックログ G-1〜G-12 を提示 |
| 2026-07-02 | xai-cli スコアスケール異常を深掘り確定（L-6 に追記）: `cx-019efece` が 0-1 正規化 composite 0.96 を 2 回 push、iteration 3 消費、バリデーション素通り。成果物は xai-cli main に PR #29 でマージ済みと照合。G-1/G-3/G-4 を ADR-002 の Staged Extensions として統合（別 ADR は起こさない） |
