# mission skill をオントロジー駆動アーキテクチャ思想でゼロベース再設計する場合の設計変更とメリット — 検査レポート

- 作成: 2026-06-16（iter1）/ 改訂: 2026-06-17（iter2: ゼロベース理想形章 + 接地エージェント前面化 / iter3: 一般概念語化）
- 対象: `/Users/<user>/dev/mission/`（/mission 自律オーケストレーター）一式
- 目的: 「設計をゼロベースで見直し、オントロジー駆動アーキテクチャの思想で再設計するとどんな設計になり、どんなメリット/コストがあるか」を**検査・提案**する（実装はしない）
- スタンス: ここで用いる設計概念（オントロジー / Object・Property・Link / Action Type / Function / lineage / 接地 grounding 等）は、ドメイン駆動設計（DDD）・CQRS・capability-based security・イベントソーシング・データ系譜管理といった**一般的に確立した設計パターン**に基づく（付録 B）。特定製品の内部実装には依拠しない
- 一次根拠: 現状の記述はすべて実コード（`skills/mission/bin/mission-state.py` 全 1255 行、各 SKILL.md、`claude-hooks/hooks.json`）を read して確認済み

---

## 1. エグゼクティブサマリー

結論を 4 点。

1. **mission は「ゼロから作り直しても 8 割は今の形に戻る」** — atomic write（`fsync`+`os.replace`）+ `fcntl.flock` のローカル整合性、`mission-state.py` 経由のみ・JSON 直書き禁止という API 規律、Reviewer 分離による Maker-Checker は、オントロジー駆動で設計し直しても**外さない正解**。ゼロベース見直しの価値は「捨てる」ことではなく、**今は暗黙慣習・散文・空配列で放置されている部分を、オントロジー駆動の設計言語で一級概念へ昇格させる**ことにある。

2. **借用すべき思想は 4 つ** — (a) **構造化オントロジー**（`Finding`/`Score`/`Review`/`Decision` を独立オブジェクト化し関係を Link 型で持つ）、(b) **Action Types**（型付き・事前条件付き・監査付きの状態遷移）、(c) **型付きオントロジー SDK 的な I/O**（サブスキル間を自然言語でなく schema 付き構造化データで受け渡す）、(d) **オントロジー接地（grounding）思想**（LLM がオントロジー文脈を読み、許可された Action からのみ次手を選ぶ）。この 4 つが現状の未解決課題（Q2/Q7/Q10/Q11/S3、ハルシネーション、lineage 欠落、指示肥大）を**構造的に**塞ぐ。

3. **接地（grounding）は mission にとって「補助」ではなく「核心」** — オントロジー接地型 LLM エージェントの基本原則は「**LLM は tool を直接実行しない。tool 使用を要求するだけで、実行は権限内で検証付き Action として行われる**」（capability-based security / hexagonal architecture の発想）。これは mission が既に持つ「LLM（orchestrator）は state を直接書けない。`mission-state.py` の Action を要求し、`mark-passes` の gate が検証する」という規律と**完全に同型**。mission は本質的に LLM オーケストレーターであり、**接地思想を受け入れるコストが最も低い種類のソフトウェア**である。前回 iter1 が接地を「補助に留める」と保守的に扱ったのは過小評価で、本レポートでは核心に位置づける。

4. **過剰なのは重量級データプラットフォームの「インフラ実装」** — 分散ストレージ・Spark パイプライン・常駐 ontology service・SAML/分類ラベルベースのマルチテナント権限基盤。mission は**単一マシン・ローカル JSON・単一ユーザー**で動くツールであり、これらをフルポートすると現状の単純さという強みを壊す純損失になる。**借用するのは「概念・規律」であって「インフラ」ではない**（4 章で仕分け）。

> 一言でいえば: **重量級プラットフォームを建てるのではなく、オントロジー・Action・接地の「規律」を mission に注入する。ゼロから設計しても、整合性基盤は今のまま残る。**

---

## 2. 現状アーキテクチャの評価（実コードベース）

### 2.1 評価すべき強み（壊してはいけない現状）

| 現状の仕組み | コード根拠 | オントロジー駆動での対応 | 評価 |
|---|---|---|---|
| `mission-state.py` が state の単一真実源、JSON 直書き禁止 | 全 cmd が `resolve_state_file`→`StateLock`→`atomic_write_json` を通る | Action 経由でのみ writeback する規律 | **API-first の萌芽。維持** |
| `mark-passes` の threshold gate（composite≥threshold ∧ min_item≥3.5 ∧ open_high=0） | `cmd_mark_passes` の gate L651-680（関数定義は L625） | Action の validation / precondition | **Action gate の萌芽。他遷移へ横展開する余地** |
| Reviewer を別ロールに分離（maker と checker の分離） | `mission-reviewer/SKILL.md`（`context: fork`） | Action approval workflow | **Maker-Checker の萌芽。構造化の余地** |
| atomic write（`fsync`+`os.replace`）+ `fcntl.flock` | `atomic_write_json` L243-250 / `StateLock` L212-240 | トランザクション整合性 | **ローカルでは十分。ゼロベースでも維持** |
| `sessions/<sid>.json` による multi-session 分離 | `resolve_session_id` L174 / `cmd_init` L364 | オブジェクトインスタンスの分離 | **既に正しい。維持** |
| スコアキーのエイリアス正規化 | `normalize_score_items` L491-515 | 弱い型強制の萌芽 | **入口でのみ機能。維持しつつ拡張** |

### 2.2 体系整理した弱み（audit-2026-06-15 由来 + 実コード確認）

| ID | 弱み | コード上の確証 | 根因（設計観点） |
|---|---|---|---|
| **Q7** | `decisions[]` が全 run で空 → Phase 6 判定の監査証跡なし | `cmd_init` L344 で `[]` 初期化、**decisions に自動 append する専用コードが皆無**（`cmd_set` 経由の手動書き込みは可能だが運用されていない） | 監査ログが「副作用の任意書き込み」扱いで、Action の本質的出力になっていない |
| **Q11** | `stagnation_count` の自動増分が未実装 | `cmd_init` L343 で `0` 設定のみ、**増分ロジックがどこにもない** | 派生値（derived property）の自動算出機構がない |
| **Q2/Q10** | `reviewer_consensus` を scorer が分散ベースで算出せず run ごとに別物の値 | `push-score` は items を**そのまま保存**（`cmd_push_score` L575-622）。分散検証なし | スコアが「入力された値」で、`Finding[]` から導出される関数になっていない |
| **S3** | `issue_ref` フィールドがなく cross-session の重複作業を検出不可 | `cmd_init` の initial dict に issue 識別子なし。同一性キーは `mission_id`(文字列ハッシュ) のみ L317-318 | オブジェクトの同一性キーが mission 文字列ハッシュに限定 |
| **S1** | Reviewer 並列起動が soft instruction のみで直列化（7/7 run 違反） | hook は Stop のみ（`hooks.json`）。reviewer 起動を強制する機構なし | 実行制御の問題（後述: これは型化では解けない・射程外） |
| 横断 | 型スキーマ不在（全フィールドが暗黙慣習）、状態遷移が散文（FSM 不在）、`Finding` がテキストで lineage 不完全 | `schema_version=2` は単なる整数 L38。型定義/検証ファイルは存在しない。`phase` は各 cmd が手で set | データモデルが「1 枚の平坦な JSON」に閉じ、状態機械が散文 SKILL.md に散在 |

---

## 3. 設計次元別分析

3.0 でゼロベース理想形（制約を外した到達点）を先に描き、3.1〜3.9 で各次元を「現状→オントロジー駆動思想→具体的設計変更→メリット→コスト/リスク」で分解する。

### 3.0 【ゼロベース理想形】制約を外して今ゼロから設計するなら

「現状コードへの後方互換」「実装コスト」をいったん無視し、オントロジー駆動思想で mission を**ゼロから設計する**と、4 本柱のアーキテクチャに収束する。重要なのは、**4 本柱のどれも現状コードに萌芽があり、ゼロベースでも整合性基盤（atomic write/lock）は外さない**点である（＝夢物語でなく現状の自然な一般化）。

```
┌──────────────────────────────────────────────────────────┐
│  接地ループ (orchestrator = 接地エージェント)              │  ← 第4柱
│   現在の Object グラフを読む → 取りうる Action を導出 → 選ぶ │
├──────────────────────────────────────────────────────────┤
│  Action Engine: 全状態遷移 = 型付き Action               │  ← 第2柱
│   precondition / validation / 自動 Audit 生成            │
├──────────────────────────────────────────────────────────┤
│  Typed I/O 層 (型付き SDK 相当): サブスキル間は schema 契約 │  ← 第3柱
│   Reviewer→Finding[] / Scorer は Finding[] の純粋関数     │
├──────────────────────────────────────────────────────────┤
│  Ontology Store: Mission/Iteration/Finding/Score/Decision │  ← 第1柱
│   Object + Property + Link（atomic write/fcntl は不変）   │
└──────────────────────────────────────────────────────────┘
```

| 柱 | 理想形 | 現状の萌芽（連続性） | ゼロベースでも残すもの |
|---|---|---|---|
| **第1柱 Ontology Store** | `Mission`/`Iteration`/`Plan`/`Execution`/`Review`/`Finding`/`Score`/`Assumption`/`Decision` を Object 化し、関係を Link 型で保持 | 単一 state.json に平坦同居 | atomic write + `fcntl.flock`（整合性モデルは正解なので不変） |
| **第2柱 Action Engine** | 全状態遷移を precondition/validation/audit 付き Action として定義 | `mark-passes` の threshold gate だけが Action gate | gate のロジック自体（composite/min_item/open_high）は流用 |
| **第3柱 Typed I/O** | サブスキル I/O を JSON schema 契約で縛り、scorer は `Score=f(Finding[])` の純粋関数 | 完全に自然言語 Markdown、scorer が再解釈 | rubric の採点基準（数値マッピング）は schema の値域定義に転写 |
| **第4柱 接地ループ** | orchestrator が Object グラフを読み、許可された Action から次手を導出 | 散文 SKILL.md + Compact Instructions の手続き記述 | 「許可された Action だけが世界に作用する」規律（既にある） |

**ゼロベース設計の核心的転換**: 現状は「**state.json という 1 枚の紙に、手続き（SKILL.md）が書き込む**」モデル。理想形は「**型付きオブジェクトグラフがあり、許可された Action だけがそれを変え、orchestrator はグラフを読んで次の Action を選ぶ**」モデル。後者は **state を読めば次手が自明**になるため、compaction 復元の堅牢性・指示肥大の軽減・移植性（Codex 等）がすべて副産物として得られる。

**ただしゼロベース設計でも「やらない」もの**（4 章で詳述）: 分散ストレージ、常駐 ontology service、Spark、SAML/purpose-based access。理由はインフラ規模のミスマッチ。**ゼロから作っても mission はローカル単機ツールであることは変わらない**。

> このゼロベース理想形は「一気に作る対象」ではなく「方位磁針」である。5 章で、ここへ至る現実的な 4 層ロードマップを引く。

### 3.1 データモデルのオントロジー化（第1柱）

- **現状**: 単一 `state.json` に全てが平坦に同居。`score_history[]` / `decisions[]` は埋め込み配列、`Finding`（指摘）は Reviewer 出力 Markdown の中にしか存在せず、オブジェクトとして取り出せない。
- **オントロジー駆動の対応**: Ontology = **Object Types + Properties + Link Types** による意味論レイヤー。統治された型付き双方向グラフとして、データ要素（Objects/Properties）と操作要素（Actions/Functions/Policies）を統合する。
- **設計変更**: state を Object Type に分解する。**物理設計は「単一 `state.json` 内の型付きコレクション」を採用し、ファイル分割（`objects/<type>/<id>.json`）は行わない**（理由: atomic write + `fcntl.flock` の単一ファイル整合性モデルを維持するため。§4 の over-normalization 批判と整合）。具体的には `state.json` に `findings: []` / `scores: []` / `decisions: []` 等の型付きコレクションを追加し、各要素が `id` と link を持つ。
  - Object Type: `Mission` / `Iteration` / `Plan`(`PlanStep`) / `Execution` / `Review` / `Finding` / `Score` / `Assumption` / `Decision`
  - Link 例: `Mission –has→ Iteration`、`Iteration –has→ {Plan, Execution, Review[], Score, Decision}`、`Review –raises→ Finding`、`Finding –resolved_by→ Execution`
- **メリット**:
  - `Finding → 修正(Execution) → 再検証(Review)` の解決チェーンが**型で**追跡可能になり、iter2+ の差分レビュー（「前 iter の High/Medium が解消したか」）の根拠が機械化される。
  - `decisions[]` が独立 `Decision` オブジェクトになり **Q7** が構造解決。
  - `score_history` が埋め込み配列でなく `Score –derived_from→ Review[]` の関係を持てる。
- **コスト/リスク**: `mission-state.py` の大幅改修、ファイル/オブジェクト数の増加、移行コスト。**over-normalization** すると atomic write/lock の単純さを失う。→ 段階適用（まず `Finding`/`Score`/`Decision` の 3 つだけ独立化）が妥当。

### 3.2 Action Types 化（第2柱・状態操作の型付け）

- **現状**: サブコマンドは Action 風だが、本格的な precondition gate は `mark-passes` の gate（L651-680・関数定義は L625）だけ。他遷移（`set`/`push-score`/`mark-halt`）の guard は弱い。`phase` は各 cmd が手で文字列 set するだけで FSM ではない。
- **オントロジー駆動の対応**: Action Type = **入力パラメータ型 + preconditions + validations + 状態遷移 + 権限 + 監査ログ生成**を 1 単位にまとめた「許可された状態遷移」。Action が create/modify/delete を担い、function 連動 Action として metadata・permissions を付与できる。
- **設計変更**: 全状態遷移を Action として再定義し precondition を付与する。
  - `submit-plan`: `complexity != Unknown` を必須（P3-5 のインライン判定と差分レビュー設計が機能する前提を型で保証）
  - `record-finding`: `severity ∈ {High,Medium,Low}` ∧ `file`/`line` 必須
  - `resolve-finding`: 対応する `Execution` への link を**必須**にする（lineage を強制）
  - `push-score`: items 正規キー・範囲チェックに加え、**iter≥2 で `reviewer_consensus` の自己申告を reject**（**Q10**。現状 `push-score` は値をそのまま保存している）
  - `mark-passes`: 既存 gate を維持
  - **全 Action が `Audit` オブジェクトを自動生成**（**Q7** を「任意の副作用」から「Action の本質的出力」へ昇格）
- **メリット**: gate の一貫性（mark-passes 以外も検証される）、監査証跡が必ず残る、状態遷移が「許可された Action の集合」として明示化され散文 SKILL.md への依存が減る。
- **コスト/リスク**: 各 Action の precondition 設計コスト。過剰 gate は機動性を下げる（early-stop の柔軟さを殺さないバランスが必要）。

### 3.3 型付き I/O（第3柱・型付きオントロジー SDK 的インターフェース）

- **現状**: サブスキル（planner/executor/reviewer/scorer/critic）間は**完全に自然言語 Markdown**で受け渡し（`mission-reviewer/SKILL.md` の出力は Markdown テーブル）。scorer の算出ロジック自体は rubric §5 で定式化されている（`mission-scorer/SKILL.md` Step 1-2 で max-min を計算する指示がある）が、**入力が構造化 JSON でなく自然言語 Markdown のため、scorer が Reviewer の項目値を読み取る段階でパース誤り・表記揺れが発生しうる**。つまり問題は「算出式の不在」ではなく「入力が型付きでないこと」にある。これが **Q2/Q10**（consensus が run ごとに別物の値になる）とハルシネーションの温床。
- **オントロジー駆動の対応**: 型付きオントロジー SDK = オントロジーを**型安全な API として公開**し、Objects/Actions/Queries を型付きエンドポイントとして扱う思想。Action types は Ontology から自動生成され、入力パラメータ型を持つ。
- **設計変更**: サブスキルの出力を構造化スキーマで縛る（Claude Code の Workflow `schema` option、あるいは各 SKILL に JSON 出力契約を持たせる）。
  - Reviewer は `Finding[]`（`{severity, file, line, claim, evidence}`）を**構造化して返す**
  - scorer は `Finding[]` を読み、`reviewer_consensus` を `Score = f(Finding[])` の**純粋関数**として機械算出する（rubric §5 の分散定義を関数化）
  - **変更対象ファイル（3 点連携）**: `skills/mission-reviewer/SKILL.md`（出力契約を `Finding[]` スキーマへ変更）／ `skills/mission-scorer/SKILL.md`（`Finding[]` から consensus を関数算出する手順へ変更）／ `skills/mission/SKILL.md`（orchestrator が受け取る際のパース部）。Workflow `schema` option を使う場合は **CC の対応バージョン確認が必要（要一次確認）**
- **メリット**: テキスト再解釈の誤差・ハルシネーションを排除、`reviewer_consensus` の自動化（**Q2/Q10**）、型不整合を I/O 層で即 reject。
- **コスト/リスク**: サブスキル出力の自由度が下がる（定性コメントの表現力とのトレードオフ）、スキーマ設計・保守コスト。

### 3.4 Branch・Scenario（worktree との対応）

- **現状**: worktree は git 機能を借用しているだけで、mission の state は worktree 削除と共に消える（**P3-2** の実害事故あり）。退避は手作業コピー（SKILL.md に手順記載）。
- **オントロジー駆動の対応**: データバージョニングの **Branch**（データの git 的バージョニング）と **Scenario**（本番 commit 前の what-if 仮想分岐）。
- **設計変更**: worktree を mission オントロジーの **Branch** として一級化し、`Mission` オブジェクトが branch 識別子を property として持つ。main への merge 時に lineage ごと state を統合（手作業退避を不要に）。Critic の「代替アプローチを 3 回試す」を **Scenario** として並行評価し、スコアで勝者を選ぶ。
- **メリット**: **P3-2** の構造解決、複数アプローチの並行試行が型化される。
- **コスト/リスク**: branch state のマージ戦略設計が必要。Scenario 比較はローカル単機には重い可能性 → **優先度は低め**（効果を実測してから）。

### 3.5 Governance・権限（分類ラベル / Action approval）

- **現状**: Trigger 1（不可逆操作の確認）は**手続き**（散文の指示）。Maker-Checker は Reviewer 分離で実現。
- **オントロジー駆動の対応**: **データ分類ラベル**、purpose-based access control、**Action approval workflow**。接地型 LLM では Action 実行が「呼び出しユーザーの権限」内に厳格に制限される。
- **設計変更**: 不可逆 Action（deploy / force-push / DB migration / 外部送信）に **分類ラベル**（操作分類）を付け、承認 gate を型で強制する。M6（インライン修正の自己検証禁止）を**「差分 Reviewer への link が無ければ `mark-passes` を reject」という precondition** に変換する。
- **メリット**: 確認が「手続き」から「型システムが強制する gate」へ昇格、Maker-Checker が構造化。
- **コスト/リスク**: 単一ユーザー・ローカルツールに本格的な権限基盤は**過剰**。→ 分類ラベルは軽量版（操作分類の enum 1 つ）に留めるのが妥当。

### 3.6 【前面化】接地エージェント化 — orchestrator を接地エージェントとして再設計（第4柱）

**本次元を接地（grounding）の核心と位置づける**。理由を先に: mission は本質的に「LLM が、許可された道具を使って、状態を達成へ進めるループ」であり、これは接地エージェントの定義そのものだからである。

- **現状**: orchestrator は非常に長い散文 SKILL.md（mission/SKILL.md 420 行 + refs 群）。compaction 後の復元は「Compact Instructions」という人手で書いた手続きに依存。次手は「state を読んで人間が書いたルールで分岐」する。
- **接地エージェントの基本原則**:
  - 接地ロジックは「data / logic / action」の 3 カテゴリのオントロジー駆動 tool を LLM に提供する。
  - **設計原則: 「LLM は tool を直接実行しない。LLM は tool 使用を要求するだけで、実行は権限内で検証付き Action として行われる。Action 経由でのみ Ontology を編集する」**（capability-based security / hexagonal architecture の発想。LLM の出力を「能力（capability）の要求」として扱い、副作用は型付き境界の内側でのみ起こす）。
  - LLM を双方向グラフに接地（ground）し、統治された意味論レイヤー経由でのみ作用させる。
- **mission との同型性（接地前面化の論拠）**:

  | 接地エージェントの原則 | mission に既にある同型物 | ゼロベースで一般化すると |
  |---|---|---|
  | LLM は tool を直接実行できず、要求するだけ | orchestrator(LLM) は state を直接書けず `mission-state.py` の Action を呼ぶ | 全状態遷移を Action 化（3.2）すれば完全一致 |
  | tool 実行は権限内で検証される | `mark-passes` の threshold gate が検証 | 全 Action に precondition（3.2/3.5） |
  | LLM はオントロジー文脈に接地される | orchestrator は state.json を読んで判断 | state がグラフ（3.1）なら接地が型で効く |
  | Action だけが Ontology を変える | JSON 直書き禁止規律 | そのまま writeback 規律 |

- **設計変更**: orchestrator を「**現在の Object グラフを読む → 取りうる Action を導出する → 選ぶ**」の接地ループに寄せる。state がグラフなら次の Action がデータから自明になり、SKILL.md の長大な手続き記述への依存が減る。compaction 復元は「state を読めば次手が決まる」ため Compact Instructions の大半が不要になる。
- **メリット**:
  - **compaction 復元の堅牢化**（state を読めば次手が自明。現状の手書き手続き依存を解消）
  - **指示肥大の軽減**（420 行 SKILL.md + refs の手続き記述が、Action の precondition と Object グラフへ移譲される）
  - **移植性向上**（Codex 等への移植は「同じ Action セット + 同じグラフ」を渡すだけ。散文の再現でなくなる）
  - mission は既に LLM オーケストレーターなので、**接地思想の受容コストが最も低い**（新しい実行モデルの導入でなく、既存規律の一般化）
- **コスト/リスク**: 「Action をデータから完全導出」は、現状の決定論的手続きより LLM の選択誤りで**信頼性が下がる**局面がある。→ **全面置換ではなく、(1) state グラフ化 → (2) Action 化 → (3) 次手導出は「決定論的ルールを主、LLM 導出を従」** の順で段階導入する。決定論で書ける分岐（合格/不合格/halt）は決定論のまま、曖昧な局面（例: Critic の改善アプローチ選択、Reviewer への観点割り当て、early-stop 続行可否のグレーゾーン判断）のみ LLM 導出に委ねるハイブリッドが現実的。

### 3.7 Lineage・Provenance（決定追跡）

- **現状**: `mission_id`/`session_id`/`timestamp` は持つ（`stamp_metadata` L300-314）が、`Finding → 修正`の対応と `decisions[]` が空で lineage が不完全。
- **オントロジー駆動の対応**: データ系譜 **lineage**（全変換の来歴。どのデータからどの結果が出たか）。
- **設計変更**: 3.1/3.2 と連動。`Finding –resolved_by→ Execution`、`Score –derived_from→ Review[]`、`Decision`（Phase 6 判定）を Action 監査として記録。`Mission` に `issue_ref` property を足し、cross-session の同一 issue を検出（**S3**）。
- **メリット**: 「なぜ合格したか」「どの指摘がどの修正で解消したか」が完全に追跡可能、**S3** の重複作業検出。
- **コスト/リスク**: link 管理のオーバーヘッド（軽微）。

### 3.8 監査・自己診断（データ品質期待値・自動 check）

- **現状**: `archive/` に scoring.md を永続化（`_archive_scoring_output` L518-540）、`.bak` を 1 世代保持（`backup_state` L253-257）。一方 `stagnation_count` は増分されず常に 0。
- **オントロジー駆動の対応**: 自動ヘルス check / データ品質期待値（data quality **expectation**） / build check（データ品質の自動検証）。
- **設計変更**: 各 Action が `Audit` を生成（3.2 と統合）、`stagnation_count` を `push-score` の**派生 check として自動算出**（**Q11**）、score 改善幅に expectation check（「3 連続で改善幅 < 0.1 なら停滞フラグ」を機械判定）。
- **メリット**: **Q11** 解消、判定の自己診断が自動化。
- **コスト/リスク**: 軽微。**High 優先**で取り込める。

### 3.9 hook・実行制御（オントロジー駆動の射程外を補完する層）

- **現状**: hook は **Stop hook 1 本のみ**（`claude-hooks/hooks.json`）。`mission-stop-guard.sh` が `loop_active`/`passes`/`halt_reason` と PID owner を見てループ継続を強制する。一方、Reviewer 並列起動の強制（S1）や不可逆操作の型による阻止は hook では行っていない（散文の指示頼み）。
- **オントロジー駆動との関係**: Action の precondition/approval は「**データの型**」の話、hook は「**エージェント実行のスケジューリング・割り込み**」の話で、層が異なる。オントロジー駆動（Ontology/Action）は前者を強化するが後者は守備範囲外。
- **設計変更**: (a) Stop hook は維持（ループ強制は有効に機能）。(b) Reviewer 並列化（S1）は PreToolUse hook で「reviewer Skill の単独メッセージ起動」を警告/ブロックする案がある（**ただし Claude Code の PreToolUse が `Skill` ツール呼び出しをトリガー/ブロックできるか未検証**・要一次確認、Codex には効かない）。(c) 不可逆操作は hook でなく Action の precondition（3.5 の分類ラベル）で止めるのが本筋。
- **メリット**: 型化（Action）で解けない実行制御の課題を hook が補完し、役割分担が明確になる。
- **コスト/リスク**: PreToolUse の Skill 制御可否が未検証。Codex 非対応。→ **hook は「オントロジー駆動の射程外」を埋める別レイヤー**として位置づけ、Action 化と混同しない。

### 3.10 オントロジー駆動アーキテクチャ採用メリットの統合俯瞰

各次元のメリットを 1 枚に集約する（「設計し直した時のメリットを分析せよ」というミッションへの直接回答）。

| 次元 | 採用で得られる主要メリット | 塞ぐ課題 | 区分 |
|---|---|---|---|
| 3.1 Ontology 化 | `Finding`/`Score`/`Decision` が追跡可能なグラフになる | Q7, lineage | 品質 |
| 3.2 Action 化 | 全状態遷移に gate・監査が必ず付く | Q7, Q10 | 品質 |
| 3.3 型付き I/O | scorer の再パース誤差を排除・consensus 自動化 | Q2, Q10, ハルシネーション | 品質 |
| 3.5 Governance | 不可逆操作・Maker-Checker が型強制になる | M6 | 品質/安全 |
| 3.6 接地（grounding） | state を読めば次手が自明 → compaction 堅牢・指示肥大解消・移植性 | 指示肥大 | 運用 |
| 3.7 Lineage | 「なぜ合格したか」を完全追跡・cross-session 重複検出 | S3 | 運用 |
| 3.8 派生 check | stagnation 自動算出・自己診断 | Q11 | 品質/堅牢 |
| 3.9 hook（補完） | 実行制御課題を別レイヤーで補完 | S1（射程外） | 実行制御 |

**一文での総括**: オントロジー駆動アーキテクチャを採用する最大のメリットは、現在「**散文の SKILL.md が人手で守らせている規律**」を「**型システムと Action が機械的に強制する規律**」へ移すことにある。これにより (1) ハルシネーション・甘採点・証跡欠落といった**品質課題**が構造的に塞がれ、(2) compaction 復元・指示肥大・移植性という**運用課題**が state グラフの自明性で解決し、(3) しかも整合性基盤（atomic write/`fcntl.flock`）と単機ローカル性は一切犠牲にしない。一方、Reviewer 並列化（S1）のような**実行制御の課題は型化の射程外**であり hook で別途補完する — この線引き自体が、思想を過大評価しないための重要なメリット（適用範囲の明確化）である。

---

## 4. オーバーエンジニアリング批判（重量級プラットフォーム フルポート不要論）

オントロジー駆動の「思想」は有用だが、**重量級データプラットフォームの「実装」をフルポートするのは過剰**。理由を 4 点。

1. **規模のミスマッチ**: mission は単一マシン・ローカル JSON・単一ユーザー。重量級プラットフォームは分散データ基盤（Spark / Object Storage / ontology service）。インフラ層を持ち込む必然性がゼロ。
2. **権限基盤の過剰**: 分類ラベル/SAML/purpose-based access は組織のマルチテナント前提。単一開発者の運用では「操作分類の enum 1 つ」で目的を達する。
3. **正規化のやり過ぎリスク**: 9 個の Object Type をファイル分割でフル正規化すると、現状の単一 `state.json` の atomic write + `fcntl` ロックという**単純で堅牢な整合性モデル**を壊し、race と複雑性を増やしうる。
4. **エージェント導出の信頼性低下**: 接地エージェント的な「Action をデータから完全導出」は、mission の現状の**決定論的手続き**より、LLM の選択誤りで不安定になる局面がある（3.6 のハイブリッド戦略で緩和）。

**接地前面化（3.6）と本批判は矛盾しない**: 借用対象は「**LLM がオントロジー文脈で許可された Action を選ぶという接地の思想**」であって、「重量級プラットフォームのインフラ（分散ストレージ・Spark・常駐 service）」ではない。前者は mission に既に萌芽があり受容コストが低い。後者は規模ミスマッチで不要。両者は別物である。

→ **結論**: mission に効くのは**概念**（型付き Action、structured I/O、lineage link、派生 check、接地 grounding）であって、重量級プラットフォームの**実装**ではない。下表で仕分ける。

| 借用すべき思想 | 借用不要なインフラ |
|---|---|
| Action Type の precondition/validation/audit | Spark パイプライン・分散ストレージ |
| Object/Link による Finding/Score の構造化 | ontology service（常駐サーバ） |
| 型付き SDK 的な I/O（schema 契約） | SDK のコード生成基盤そのもの |
| **接地（grounding）= Action 経由のみ作用** | **マネージド LLM ホスティング・推論基盤** |
| lineage link（Finding→Execution） | フル data lineage グラフ DB |
| 派生 property の自動 check（stagnation） | 大規模 health monitoring |
| 分類ラベル（操作分類 enum） | SAML/purpose-based access 基盤 |

---

## 5. 優先度マトリクスと適用ロードマップ（4 層）

ゼロベース理想形（3.0）を最上位に置き、そこへ至る**今日 → 短期 → 中期 → 理想形**の 4 層に整理する。即効性（低コストで既存課題に直結）× 実装コストで分類。

| 次元/変更 | 即効性 | 実装コスト | 層 | 塞ぐ課題 |
|---|---|---|---|---|
| 3.2 `push-score` の reviewer_consensus reject（iter≥2 自己申告弾き） | 高 | 低 | **今日（即着手）** | Q10 |
| 3.8 stagnation 自動算出（push-score で composite 差を計算） | 高 | 低 | **今日（即着手）** | Q11 |
| 3.7 `Mission.issue_ref` 追加（init 引数 + 重複警告） | 中 | 低 | **今日（即着手）** | S3 |
| 3.3 Reviewer→`Finding[]` の structured I/O | 高 | 中 | **短期** | ハルシネーション, Q2/Q10 |
| 3.3 scorer の `consensus=f(Finding[])` 機械算出 | 高 | 中 | **短期** | Q2 |
| 3.2 `resolve-finding` の Execution link 必須 | 高 | 中 | **短期** | lineage |
| 3.2/3.7 全 Action の Audit/Decision 自動生成 | 高 | 中 | **短期** | Q7 |
| 3.1 `Finding`/`Score`/`Decision` 独立オブジェクト化 | 中 | 中〜高 | **中期** | lineage 基盤 |
| 3.5 分類ラベル（操作分類 enum）+ M6 の link 強制 | 中 | 中 | **中期** | Maker-Checker 構造化 |
| 3.6 接地ループ化（次手導出はハイブリッド） | 中 | 高 | **理想形** | 指示肥大・compaction 堅牢化 |
| 3.4 worktree の Branch/Scenario 一級化 | 低 | 高 | **理想形** | P3-2 |
| 重量級プラットフォームのインフラ移植・SAML 権限基盤 | — | — | **WON'T** | — |

**ロードマップ解説**:
- **今日（`mission-state.py` への純増分・整合性モデルを壊さない・最大 ROI）**: Q10/Q11/S3 を低コストで塞ぐ。型導入もオブジェクト化も不要、既存 cmd への数十行追加で済む。
- **短期**: structured I/O（3.3）で reviewer→scorer を型化し、ハルシネーションと Q2 を構造解決。Action 化（3.2）で gate を横展開し Q7 を解決。
- **中期**: `Finding`/`Score`/`Decision` を独立オブジェクト化し lineage link を張る。差分レビューの根拠が機械化される。
- **理想形（3.0）**: 接地ループ化と Branch/Scenario。**実測効果を見てから着手**（投機的に作らない）。ここまで来ると compaction 復元と指示肥大が構造解決する。

### 5.1 「今日（即着手）」3 項目の具体的変更箇所

「最大 ROI・整合性モデルを壊さない」と主張する以上、実装者が辿れる粒度まで落とす（本レポートは実装はしないが、変更先ファイル・関数・最小差分の方針までは分析として示す）。3 件とも `skills/mission/bin/mission-state.py` への増分のみで、型導入・オブジェクト化は不要。

| # | 課題 | 変更先ファイル / 関数 | 追加する処理（最小差分の方針） |
|---|---|---|---|
| 1 | Q10（consensus 自己申告 reject） | `mission-state.py` の `cmd_push_score`（L575-622）/ `_validate_score_args`（L543-572） | 正規化済 `items` 取得後・`entry` 構築前に `if args.iteration >= 2 and "reviewer_consensus" in items:` で警告して `sys.exit(2)`（差分レビュー周回での自己申告を機械 reject。rubric M5 §119 と整合） |
| 2 | Q11（stagnation 自動算出） | `mission-state.py` の `cmd_push_score`（L575-622）StateLock 内 | `score_history` へ append 後、前 iter の composite との差を計算し `if 0 <= (prev - cur の改善幅) < 0.1: data["stagnation_count"] += 1` else `0` にリセット（保存先は既存 `stagnation_count` フィールド L343） |
| 3 | S3（issue_ref で重複検出） | `mission-state.py` の `cmd_init`（initial dict L330-351）+ `_build_parser` の `p_init`（L1177-1183） | `--issue-ref` 引数を追加し initial dict に `"issue_ref"` を格納。init 時に `_iter_state_files` で同一 `issue_ref` の active state を走査し、あれば WARN（S3 の「init 時警告」の前提が満たされる） |

**いずれも `StateLock` + `atomic_write_json` の既存トランザクション内で完結**するため、`fcntl.flock` ベースの整合性モデルを変えずに済む。これが「今日着手・最大 ROI」の技術的根拠である。

---

## 6. 既存課題（Q2/Q7/Q10/Q11/S1/S3）への対応マッピング

| 課題 | 対応次元 | 解決の型 |
|---|---|---|
| **Q2**（consensus 分散算出が未実装・run 毎に別値） | 3.3 | `reviewer_consensus = f(Finding[])` を純粋関数化（rubric §5 の分散定義をコードに落とす）。scorer の目視集計を排除 |
| **Q7**（decisions 空＝判定証跡なし） | 3.2 / 3.7 | 全 Action が `Audit` を自動生成、Phase 6 判定を `Decision` オブジェクト化 |
| **Q10**（reviewer_consensus 自己申告 M5 違反） | 3.2 / 3.3 | `push-score` precondition で iter≥2 の自己申告を reject ＋ `Score = f(Finding[])` の機械算出 |
| **Q11**（stagnation 未実装） | 3.8 | `push-score` の派生 check で改善幅を自動判定 |
| **S3**（issue_ref 無し） | 3.1 / 3.7 | `Mission.issue_ref` property ＋ cross-session lineage |
| **S1**（Reviewer 直列化 7/7 違反） | — | **型化では解決しない。オントロジー駆動の射程外**（下記） |

> **S1 についての率直な評価（効能の誇張を避けるため明記）**: Reviewer 並列の直列化は「データの型」ではなく「**エージェント実行のスケジューリング**」の問題であり、オントロジー化や Action 化では直らない。structured I/O にしても「1 メッセージで N 名同時起動」を保証するのは hook/harness 側の責務である。audit の対策（planner 出力チェックリストへの機械埋め込み・PreToolUse hook での強制）が正道で、本再設計のスコープ外。**オントロジー駆動で全課題が解けるわけではない**ことを正直に記す。

---

## 付録 A: 用語対応表（オントロジー駆動アーキテクチャの概念 ↔ mission 内用語）

| オントロジー駆動の概念 | mission での対応 |
|---|---|
| Object Type | `Mission` / `Iteration` / `Finding` / `Score` / `Decision` 等 |
| Property | state.json の各フィールド |
| Link Type | オブジェクト間参照（`Review –raises→ Finding` 等） |
| Action Type | `mission-state.py` のサブコマンド（init/push-score/mark-passes 等） |
| Action validation / precondition | `mark-passes` の threshold gate（既存）＋各遷移へ横展開 |
| function 連動 Action | precondition/権限付きの状態遷移（3.2 の `resolve-finding` 等） |
| 型付きオントロジー SDK（型安全 API 公開） | サブスキル間の structured I/O（Workflow `schema`・※CC 対応バージョン要確認） |
| Function（純粋計算） | `reviewer_consensus = f(Finding[])` の機械算出 |
| Object Query | state グラフからの導出読み取り（次手導出の入力） |
| 接地ロジック（data/logic/action tool で LLM 関数） | orchestrator のサブスキル群（planner/executor/reviewer/scorer/critic） |
| 接地エージェント（grounding ループ） | orchestrator 本体（グラフを読み Action を選ぶ ReAct ループ） |
| Action 適用（LLM は要求のみ・実行は権限内） | orchestrator は state 直書き不可・`mission-state.py` Action 経由のみ |
| Branch | worktree |
| Scenario（what-if） | Critic の複数アプローチ試行 |
| 分類ラベル | 不可逆操作の分類 enum |
| Action approval workflow | Maker-Checker（Reviewer 分離）/ Trigger 1 |
| Lineage / Provenance | `Finding → Execution → Review` 解決チェーン |
| 自己診断 check / 品質期待値（expectation） | stagnation・score 改善幅の派生 check |

## 付録 B: 留意点（正確性の担保）と参考設計パターン

- 本レポートのオントロジー駆動概念は、**ドメイン駆動設計（DDD）の domain model + ER**、**CQRS の command + 状態遷移**、**capability-based security / hexagonal architecture（副作用を型付き境界の内側に閉じる）**、**イベントソーシング / 監査ログ**、**データ系譜（lineage）管理**、**有限状態機械（FSM）** といった**一般に確立した設計パターン**に基づく。特定製品の内部実装やバージョン固有の挙動には依拠していない。
- 記述は「特定の製品ではこうである」という断定ではなく、「オントロジー駆動／接地の◯◯思想を借用すると mission ではこう設計できる」という**変換の提案**として読むべきである。
- mission 現状の記述（state スキーマ・threshold gate・stagnation/decisions の未実装・サブスキル自然言語 I/O 等）は、すべて実コード（`mission-state.py`・各 SKILL.md・`hooks.json`）を read して確認した一次情報である。
- 最大の価値は、mission が既に持つ萌芽（API-first 規律・threshold gate・Maker-Checker・JSON 直書き禁止）を**全 Action/全オブジェクト/接地ループへ一般化する設計言語**を、これらの設計パターンが提供する点にある。重量級プラットフォームのインフラ移植ではない。

**参考設計パターン（一般文献）**: ドメイン駆動設計（DDD） / CQRS / capability-based security / hexagonal architecture（ports and adapters） / イベントソーシング / データ系譜（data lineage）管理 / 有限状態機械（FSM） / データ品質期待値（data quality expectations）。

---

## 修正履歴
| 日時 | 内容 |
|------|------|
| 2026-06-16 | 初版作成（/mission Iteration 1）。8 次元分析・オーバーエンジニアリング批判・優先度マトリクス・課題マッピング・用語対応表 |
| 2026-06-17 | iter2 ゼロベース改訂: §3.0「ゼロベース理想形（4 本柱）」新章を追加、§3.6 を「補助」から「核心（orchestrator=接地エージェント）」へ全面書き換え（接地の設計原則と同型性表を追加）、§1 サマリーを 4 点（ゼロベース + 接地核心）に再構成、§5 ロードマップを 4 層（今日/短期/中期/理想形）化、§6 に Q2 を正式追加・S1 の射程外論拠を強化、付録 A に接地ロジック/Function/Object Query/Action 適用を追記、付録 B に参照範囲の留意を明記、現状記述に実コード行番号根拠を付与 |
| 2026-06-17 | iter2 Reviewer 3名指摘反映: §5.1「今日3項目の変更先ファイル/関数」追加（実用性 High 解消）、§3.10「採用メリット統合俯瞰表」+ §3.9「hook・実行制御」新節（Medium/Low）、§3.3 scorer 記述を「算出式不在でなく入力が型付きでない」へ精緻化 + structured I/O 変更対象3ファイル明記、§3.1 物理設計を単一 state コレクションに確定、行番号 L575-622 補正、Q7 decisions 表現補正（cmd_set 手動可）、§3.6 トーン調整 + ハイブリッド例示追加 |
| 2026-06-17 | iter3 一般概念語化: 設計概念を一般的な設計パターン名（オントロジー駆動 / 接地 grounding / 型付きオントロジー SDK / 分類ラベル / DDD・CQRS・capability-based security 等）で統一し、ファイルを `ontology-redesign-analysis.md` にリネーム。ontology/Object/Action/Link/Function/lineage/grounding/Branch/Scenario 等の一般概念語は維持 |
