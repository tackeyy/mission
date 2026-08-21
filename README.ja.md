# mission

<p align="center">
  <img src="docs/assets/hero.png" alt="mission — quality-gated autonomous mission loop" width="760">
</p>

[English](README.md) | **日本語**

`mission` は Claude Code と Codex 向けの OSS ループエンジニアリングプラグインである。
記録された計画・レビュー証拠・集計スコア・state ゲートが「本当に完了した」と示すまで、
エージェントの作業を継続させる。

> プロンプトエンジニアリングはエージェントに何をするかを伝える。
> ループエンジニアリングはエージェントが本当に終わるまでどう働き続けるかを定義する。

**解決するのは「早すぎる完了宣言」であって、より良いプロンプトの書き方ではない。**

---

## `mission` を使う場面

- 単発のパスが「一見終わっているもの」を出しうる複数ステップの作業。網羅の黙殺、
  明細と一致しない要約、本文で約束したのに書かれていない節、など。
- 人間の承認なしに実行してはならない不可逆な本番操作。
- セッションやコンテキストのリセットをまたぐ作業で、再開可能かつ監査可能な state が要るもの。
- **なぜ停止してよいかの証拠**自体が成果物の一部である環境。

## `mission` を使うべきでない場面

- **平均的な品質向上を期待している場合。** 品質優位は実証されていない。実測コストは
  goal ベースラインに対し **時間 5.4 倍・想定支出 4.9 倍**。ゲートを素通りする 95% に
  似た作業なら、単発の丁寧な 1 パスが同じ成果物をより速く出す。
- **タスクが単純で自己完結している場合。** `mission` 自身がそうした作業をホストの
  goal 契約へルーティングし、mission state すら作らない。
- **PR レビュー bot・開発方法論・プロンプト再実行ループが欲しい場合。**
  それらは他ツールの方が適している（[代替](#代替) を参照）。

---

## 動作原理

```text
plan -> execute -> verify -> review -> aggregate score -> iterate
```

計画を記録し、実行し、**実行された検証結果**を記録し、構造化レビュー出力
（`mission-review/1`）を収集し、`aggregate-reviews` で 4 軸スコアへ集計し、
`push-score --scoring-json` で記録し、`mark-passes` が state を受理するか
halt 条件が発火するまで繰り返す。
Stop hook は、アクティブなミッションがゲート未達の間セッションの終了を阻止する。

pass ゲートは明示的である。

```text
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= threshold
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

単純・低リスクかつ `--issue-ref` を伴わない作業では、`init` がホストの goal 契約への
ルーティング判定を返し、state を作らない。複雑度・リスクシグナル・`--issue-ref` が
これを決め、`--force-mission` で上書きできる。

---

## 証拠が示すこと

このセクションは **あなたが再現できるもの** と **メンテナ自身のリポジトリでの運用証拠** を
分けて示す。限界も併記する。

### 再現可能: goal ベースラインとのペア比較ベンチマーク

手順と生データ: [`benchmarks/mission-vs-goal/README.ja.md`](benchmarks/mission-vs-goal/README.ja.md)

| 測定 | 結果 | 読み方 |
|---|---|---|
| 完了率 | goal 94.5% / mission 96.6% | ほぼ同等。優位性を主張できる N ではない |
| 完了宣言したが validator 未達 | 0/120 goal, 0/114 mission | **ベースラインが失敗し `mission` が救った事例は記録上ゼロ** |
| 実時間 | mission **5.4 倍** | 実コスト。クリーン条件での測定 |
| 想定支出 | mission **4.9 倍** | 相対比較のみ。サブスクリプション実行では従量課金は発生せず、消費するのはプランのレート制限 |
| 2 回目の反復が起きた割合 | mission run の 5.6% | レビューループが成果物を変えるのは少数の run に限られる |
| 品質 | **有効に測定できていない** | 下記参照 |

**品質について正直な記述は「同点」ではなく「測れていない」である。**
ベンチマークの品質 marker 採点は構造的に壊れている。正しい語を並べただけの文字列が
満点 1.00 を取り、正しい言い換えは — そして日本語で書かれた正解は — 0 点になる。
3 つの独立レビューが、正規表現の共起では推論の有無を測れないと結論した。
**現在の marker スコアを、どちらの方向にも証拠として引用してはならない。**
置き換えはリポジトリの open issue で追跡している。

### 運用: 本番 451 ミッション

匿名化した事例: [`docs/CASE_STUDIES.md`](docs/CASE_STUDIES.md)
**これらはメンテナの非公開リポジトリ由来であり、本リポジトリから再現できない。**
その限界を明示した上で開示している。

| 測定 | 値 | 読み方 |
|---|---:|---|
| iteration 1 でゲート通過 | 427 / 451 (95%) | 大半の作業でループは素通り。レビューコストだけかかり何も変わらない |
| iteration 1 でゲート未達 | 24 (5%) | ゲートが刺さるテール |
| 複数 iteration のミッション | 44 | |
| — composite が改善 | 27 | 例: 2.80 → 4.20、0.96 → 4.80 |
| — composite が不変 | 15 | **正直な負の結果**: コストだけかかり測定可能な改善なし |
| 人間承認のため halt | 7 | 本番 DB マイグレーション、セキュリティ監査の公開、本番 API 上限変更 |

**平均より分布が重要である。** ゲートは平均を押し上げることではなく、少数のテールで
コストに見合う。

### 本プロジェクトが主張しないこと

- `mission` が平均的により高品質な成果物を作ること
- goal ベースラインでは完遂できない作業を `mission` が完遂できること。該当する記録は存在しない
- 5% というテール率があなたのワークロードにも当てはまること
- 現在のベンチマーク品質数値に意味があること

これらは、差を実際に検出できる採点方式に対して**事前登録した基準**を満たした場合にのみ主張する。
基準はデータ収集より前に [`docs/PRE_REGISTRATION.md`](docs/PRE_REGISTRATION.md) で固定しており、
結果を見てから調整できない。**同ファイルが merge された後に取得したデータのみを判定に使う。**

### コスト制御

素通りする 95% のレビュー負荷を下げるため、`mission` は init 時に complexity と
ミッション記述から `review_tier`（light / standard / full）を導出する。light tier は
reviewer を 3 名ではなく 1 名にし、specialist を `required: true` のものへ限定する。
**ゲート意味論（threshold・open High findings・agreement delta・halt 条件）は
tier によらず不変である。**
コスト削減の効果は本番ではまだ計測されていません。

### 検証済み事項

各主張の鮮度が読み手に分かるよう、日付付きのスナップショットで示す。

- 2026-08-14: 3244 passed — その時点でのフルスイート。現在の件数は `make test` で確認する
- Artifact support は明示的な opt-in の公開証跡を伴うローカル Markdown artifact として
  実装されている（[`docs/MISSION_ARTIFACTS.ja.md`](docs/MISSION_ARTIFACTS.ja.md)）

---

## セキュリティ

### 実装済み

- **fail-closed な Stop hook** — アクティブなミッションがゲート未達の間セッションを終了できない
- **TTL 付き lease / fencing** — 変更系コマンドは有効な lease を要求し、古い書き込みと並行書き込みを拒否する
- **不可逆操作の halt ゲート** — 不可逆操作は人間の明示承認まで halt する。理由は verbatim で state に記録される
- **typed force-pass 承認** — ゲートの迂回には typed かつ content-addressed な承認が必要。レビュー集計証拠を force-pass の根拠に流用できない
- **provenance binding** — レビュー証拠とスコアは sha256 で content-addressed され、スコアを別の証拠へ差し替えられない
- **監査証跡** — `scripts/mission-audit.py` が force-pass・specialist provenance・lease のリスクを記録済み state 全体から分類する（JSON / Markdown）
- **permission preflight** — 必要権限を init 時に検査し、作業開始前に報告する

### 既知のギャップ

以下は開いているギャップであり、予定された機能ではない。重要なワークフローへ採用する前に
評価すること。[`SECURITY.md`](SECURITY.md) も参照。

- **アーカイブされたレビュー JSON に改ざん検知の署名がない。** provenance binding は
  証拠とスコアの結び付きを守るが、アーカイブ後のファイルシステムレベルの改ざんは検出できない
- **外部 specialist provider の identity binding がない。** 呼び出しは記録されるが、
  実行した provider の identity が出力へ暗号学的に束縛されていない
- **blast-radius の制限がない。** ミッションが変更できるファイル数や書き込めるパスに
  強制的な上限がない
- **reviewer に検証の実行義務がない。** 検証ツールは reviewer に与えられているが、
  読むだけのレビューも成立する。実行された検証結果は別途記録されるようになったので、
  この点は測定できる

### 安全側への倒れ方

| 条件 | 挙動 |
|---|---|
| lease が古い / 期限切れ | 変更系コマンドを拒否し、state を書かない |
| 採点証拠の欠落 | `push-score` が提出を拒否する |
| ゲート未達のスコア | Stop hook がセッション終了を阻止し、ループを継続する |
| 不可逆操作の検出 | halt が発火し理由を記録。人間が再開するまで作業は未完了のまま |
| goal-dispatch 設定が不明 | ルーティングゲートを変えずに inline へ fail-safe |
| local-authoring sync 時にオフライン / remote 不在 | 古い内容へ fallback せず、ローカル作業も書き換えずに停止する |
| typed 承認なしの force-pass | 拒否する |

---

## 証拠の再現手順

以下はすべてリポジトリルートで実行する。

```bash
# 1. CI と同一のフルテストスイート
make test

# 2. goal ベースラインとのペア比較ベンチマーク
#    注意: --max-budget-usd は請求上限ではなく、推定値に対する打ち切り閾値である。
#    サブスクリプション実行では消費するのはプランのレート制限。
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --starting-commit "$(git rev-parse HEAD)" \
  --tasks-file benchmarks/mission-vs-goal/tasks.tail.json \
  --run-id "$(date +%Y-%m-%d)-your-run" \
  --model-id <your-model-id> \
  --limit-tasks 5 \
  --repeats 3 \
  --stop-on-blocked

# 3. 自分で生成した mission state を監査する
python3 scripts/mission-audit.py --root <path-to-your-project> --json
```

比較目的の結論には `--repeats 3` 以上が要る。実測されたタスク別分散は 0.51〜1.97 倍に達し、
それ未満の差はノイズと区別できない。品質の結論を支えられない run では、ランナーが警告を出し
summary にその旨を記録する。

---

## 代替

| 選ぶもの | 場面 |
|---|---|
| `mission` | 監査可能な完了ゲート、不可逆操作のガバナンス、再開可能な state が要り、主なリスクが「早すぎる停止」である場合 |
| Claude Code `/goal` | 単一セッション内の軽量な run-until 条件。state machine もレビューループもない |
| `ralph-loop` | 完了宣言が出るまで 1 つのプロンプトを再実行する。より単純でスコアリングなし |
| Superpowers | ブレインストーミング・TDD・デバッグ・デリバリーを含む広範な開発方法論 |
| レビュー / CI プラグイン | ワークフローの一部を検査する専門ツール。全体の完了判定は別が担う |

詳細な比較: [`docs/LOOP_ENGINEERING.md`](docs/LOOP_ENGINEERING.md)

---

## インストール

clone 先を `MISSION_REPO` として指定します。

```bash
MISSION_REPO="$HOME/dev/mission"
git clone https://github.com/tackeyy/mission.git "$MISSION_REPO"
```

### Claude Code

ローカル marketplace 経由でインストールします。

```text
/plugin marketplace add ~/dev/mission
/plugin install mission@mission-marketplace
```

別の場所に clone した場合は `~/dev/mission` を `$MISSION_REPO` のパスに置き換えてください。`/plugin marketplace add` はパスをそのまま受け取り、シェル変数を展開しないため、clone 先と一致させる必要があります。

`/plugin install` は `.claude-plugin/plugin.json` から `claude-hooks/hooks.json` を読み、ループ継続を強制する Stop hook も有効化します。

実運用では `/plugin install` を推奨します。2026-06-14 の単回検証では、development mode の
plugin loading だと SKILL.md 本文の `${CLAUDE_PLUGIN_ROOT}` がモデル提示時に展開されず、
orchestrator が `mission-state.py` に到達できませんでした。

`~/.claude/skills/mission/` に同名スキルがある環境では名前が衝突します。先に旧スキルを退避または削除してください。

### Codex

local authoring では、skill 群を `~/.codex/skills/` に symlink し、plugin root を設定します。

```bash
MISSION_REPO="$HOME/dev/mission"
for s in mission mission-planner mission-executor mission-reviewer mission-critic mission-scorer; do
  ln -sfn "$MISSION_REPO/skills/$s" "$HOME/.codex/skills/$s"
done
export MISSION_PLUGIN_ROOT="$MISSION_REPO"
export CLAUDE_PLUGIN_ROOT="$MISSION_REPO"  # 現行 skill command text との互換用
```

local authoring の各実行では、mission state の初期化前に
`scripts/mission-local-authoring-sync.sh` を実行します。この guard は
`origin/main` を取得し、clean な `main` だけを fast-forward で更新して
`HEAD == origin/main` を検証した後、更新済み `SKILL.md` の読み直しを要求します。
dirty、`main` 以外、detached、ahead/diverged、remote branch 欠落、offline の場合は、
古い checkout への fallback や local work の書き換えを行わず停止します。

plugin 配布用に、この repo には `.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json` も含めています。Codex marketplace install は、Codex が marketplace entry の `source.path` として `plugins/` 配下の plugin folder を期待するため、`plugins/mission/` wrapper を使います。Codex plugin package は default では skills-only です。Stop hook は Codex の hook trust と path resolution が Claude Code と異なるため、opt-in 手順に分離しています。詳細は
[`skills/mission/refs/codex-setup.md`](skills/mission/refs/codex-setup.md) と [`docs/DISTRIBUTION.ja.md`](docs/DISTRIBUTION.ja.md) を参照してください。

`codex plugin add mission@mission-marketplace` 後は、`MISSION_PLUGIN_ROOT` を install cache path に設定し、現行 model-visible command text 互換のため `CLAUDE_PLUGIN_ROOT` も同じ値にします。

```bash
export MISSION_PLUGIN_ROOT="${CODEX_HOME:-$HOME/.codex}/plugins/cache/mission-marketplace/mission/2.8.0"
export CLAUDE_PLUGIN_ROOT="$MISSION_PLUGIN_ROOT"
```

marketplace 提出前は [`docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md`](docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md) を確認してください。

## 使い方

```text
/mission <ミッション記述> [--max-iter N] [--skip-preflight] [--threshold X] [--budget-minutes N] [--goal-dispatch <inline|host-native>] [--force-mission]
```

orchestrator は仮置き、ミッション分解、実行、reviewer JSON 収集、`aggregate-reviews`、`push-score --scoring-json` 記録を行い、`mark-passes` が state を受理するか中断条件が成立するまで反復します。ユーザーが明示的に供給した手動採点は、先に `manual-score-capture` で typed・content-addressed な入力として固定します。review aggregate の evidence を流用しません。
詳細な運用プロトコルは [`skills/mission/SKILL.md`](skills/mission/SKILL.md)、`stats` / audit の raw・completed 品質 schema は [`docs/PASS_RATE_METRICS.ja.md`](docs/PASS_RATE_METRICS.ja.md)、明示的に再利用する state snapshot は [`docs/STATE_SNAPSHOTS.ja.md`](docs/STATE_SNAPSHOTS.ja.md) を参照してください。
`--goal-dispatch` は Simple routing 後の完遂先を inline / host-native から選び、`--force-mission` は Simple なら goal に逃がす場面でも mission ループを維持します。

## 動作環境

- macOS / Linux
- Python 3.9 以上
- Stop hook 用の `jq`
- Claude Code または Codex

`skills/mission/bin/mission-state.py` は Unix 専用の `fcntl` に依存するため、Windows は非対応です。

Stop hook の stale-state 警告は macOS では BSD `date`、Linux では GNU `date` でタイムスタンプを解釈するため両 OS で動作します。
両方の解釈に失敗した場合のみ警告を無効化しますが、ループ継続強制の本機能は常に動作します。

## 設定

| 環境変数 | 既定 | 用途 |
|---|---|---|
| `MISSION_PLUGIN_ROOT` | 未設定 | Codex/local install で使う agent-neutral な plugin root |
| `CLAUDE_PLUGIN_ROOT` | 未設定 | 既存の model-visible command text と Claude Code hook path 互換用 |
| `MISSION_SEARCH_ROOTS` | 現在のディレクトリ | `list` / `cleanup-stale` / `stats` / `halt --all` の検索対象 |
| `MISSION_LEASE_ID` | 未設定 | mutating command 用の明示 fencing token。lease-free な legacy state は最初の write で取得できる |
| `MISSION_LEASE_TTL_SECONDS` | `900` | mutating command の lease TTL（秒）。`supersede-reviews` は旧 review session の lease 失効が前提で、live な lease が残る間は `lease-rejected` で終了し何も書き込まない |
| `MISSION_OPERATION_ID` | 未設定 | v5 の `planning reselect` / `supersede-reviews` に必須の caller-stable retry ID。同一 retry だけ再利用し、新規 invocation では新しい ID を渡す |
| `MISSION_SESSION_ID` | 未設定 | 明示 session ID。`CLAUDE_CODE_SESSION_ID` → `CODEX_THREAD_ID` → pid にフォールバックする |
| `MISSION_STALE_ACTIVE_SECONDS` | `10800` | active state の stale 判定しきい値（秒） |
| `MISSION_SKILL_ROOTS` | 未設定 | 既定の `~/.codex/skills` / `~/.claude/skills` より前に探索する追加 skill root |
| `MISSION_REQUIRE_SCORING_EVIDENCE` | 未設定 | `push-score` の scoring-evidence gate。`0` で deprecated escape hatch を使う |

`MISSION_SEARCH_ROOTS` は OS の path separator で複数指定できます。macOS/Linux では `~/workspace:~/dev` のように指定します。

## テスト

```bash
make test-smoke   # 構文・import チェック。インストール不要
make test         # CI と同一のフルスイート
make test-shard   # CI シャード 1 本
```

## 構成

| パス | 役割 |
|---|---|
| `skills/mission/` | オーケストレータ本体、state CLI、参照ドキュメント、テスト |
| `skills/mission-planner/` | 計画立案サブスキル |
| `skills/mission-executor/` | 実行サブスキル |
| `skills/mission-reviewer/` | ピアレビューサブスキル |
| `skills/mission-critic/` | 改善案立案サブスキル |
| `skills/mission-scorer/` | reviewer output を JSON 化するフォールバック変換器 |
| `docs/` | 設計・運用ドキュメント |
| `benchmarks/` | mission vs goal のパイロット計測 |
| `scripts/mission-local-authoring-sync.sh` | Git-backed local authoring を最新 mainへ揃える fail-closed bootstrap |
| `scripts/ci_changed_scopes.js` | CI の変更スコープ判定 |
| `scripts/mission-stop-guard.sh` | ループ継続を強制する Stop hook |
| `claude-hooks/hooks.json` | Claude Code 用 Stop hook 宣言 |
| `.claude-plugin/` | `plugin.json` / `marketplace.json` |
| `.codex-plugin/` | Codex plugin metadata |
| `.agents/plugins/` | Codex local marketplace metadata |
| `plugins/mission/` | Codex marketplace plugin wrapper |

## ドキュメント

| パス | 内容 |
|---|---|
| [`skills/mission/SKILL.md`](skills/mission/SKILL.md) | 実行プロトコル |
| [`docs/LOOP_ENGINEERING.md`](docs/LOOP_ENGINEERING.md) | ポジショニングと比較 |
| [`docs/CASE_STUDIES.ja.md`](docs/CASE_STUDIES.ja.md) | 運用証拠（独立再現は不可） |
| [`benchmarks/mission-vs-goal/`](benchmarks/mission-vs-goal/) | 再現可能なベンチマーク手順と生データ |
| [`docs/MISSION_ARTIFACTS.ja.md`](docs/MISSION_ARTIFACTS.ja.md) | ローカルファーストな artifact 契約 |
| [`docs/PASS_RATE_METRICS.ja.md`](docs/PASS_RATE_METRICS.ja.md) | pass rate と監査指標のスキーマ |
| [`docs/DISTRIBUTION.ja.md`](docs/DISTRIBUTION.ja.md) | 配布とパッケージング |
| [`SECURITY.md`](SECURITY.md) | セキュリティポリシーと報告 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | コントリビューションガイド |

## コントリビューション

Issue や Pull Request を歓迎します。作業前に [CONTRIBUTING.ja.md](CONTRIBUTING.ja.md)、
[docs/TESTING.ja.md](docs/TESTING.ja.md)、[SECURITY.ja.md](SECURITY.ja.md) を確認してください。

コード、ドキュメント、テスト、Issue 起票、アイデア、レビュー、フィードバックを
`mission` への貢献として扱います。

### Contributors

<!-- CONTRIBUTORS-START -->
<a href="https://github.com/tackeyy"><img src="https://github.com/tackeyy.png" width="40" height="40" alt="@tackeyy"></a>
<a href="https://github.com/shurijoc"><img src="https://github.com/shurijoc.png" width="40" height="40" alt="@shurijoc"></a>
<!-- CONTRIBUTORS-END -->

## ライセンス

MIT。[`LICENSE`](LICENSE) を参照。
