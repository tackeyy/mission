# mission — 自律ミッション達成オーケストレータ

<p align="center">
  <img src="docs/assets/hero.png" alt="mission — 品質ゲート付き自律ミッションループ" width="760">
</p>


[English](README.md) | **日本語**

`mission` は、Claude Code / Codex 向けの OSS loop engineering プラグインです。
計画、レビュー、採点、state gate が「本当に完了した」と判断するまで、agentic work を進め続けます。

計画、実行、ピアレビュー、スコアリングを合格閾値に達するまで反復し、Stop hook が早期終了を抑止します。

> Prompt engineering は「AI に何を頼むか」。
> Loop engineering は「AI が完了まで進み続ける仕組みをどう設計するか」。

`mission` が解く問いは「どんな prompt を書くか」ではなく、
**AI が品質ゲートを通る前に成功宣言して止まるのをどう防ぐか**です。

## Loop Engineering

`mission` は、複数ステップの agent work を品質ゲート付きで回す loop です。

```text
plan -> execute -> review -> score -> iterate
```

recurring agent system、workflow、skills、plugins、sub-agent が実務の leverage になり始める一方で、
loop には「いつ止まってよいか」を決める仕組みが必要です。`mission` は `.mission-state`、
reviewer/scorer phase、threshold-based pass/fail state で、その completion gate を提供します。

public launch positioning、GitHub topics、`/goal` / `ralph-loop` / Superpowers との比較は
[`docs/LOOP_ENGINEERING.md`](docs/LOOP_ENGINEERING.md) を参照してください。

## 特徴

- メインオーケストレータ: `skills/mission`
- 5 つのサブスキル: planner / executor / reviewer / critic / scorer
- `.mission-state` セッションを扱う state 管理 CLI
- Claude Code / Codex の複数セッション分離
- 未達ミッションの早期終了を防ぐ Stop hook
- ドメイン別 evidence provider を選ぶ任意 specialist registry と beginner presets（[設計](skills/mission/refs/specialist-registry.md)）
- state routing、scoring gate、hook 挙動を検証する Python テスト

## 競合ポジショニング

`mission` は **品質ゲート付きの自律ミッション達成オーケストレータ** として位置づけます。
完全なソフトウェア開発方法論、PR レビュー bot、単純な prompt replay loop を目指すものではありません。
中核価値はより狭く、複数ステップのミッションについて、state、レビュー、スコアリングのゲートが
「止まってよい」と判断するまで進め続けることです。

調査時点: 2026-06-15。調査対象は、ローカルの Claude Code official marketplace cache、
ローカルの Codex `openai-curated` plugin cache、Anthropic
[`claude-plugins-official`](https://github.com/anthropics/claude-plugins-official)
repository、[Claude Code `/goal` docs](https://code.claude.com/docs/ja/goal)、
[OpenAI Codex plugin docs](https://developers.openai.com/codex/plugins/build)、
公開 competitor README です。

| Product / plugin | Surface | 関係 | 重なる領域 | `mission` との差分 |
|---|---|---|---|---|
| [`/goal`](https://code.claude.com/docs/ja/goal) | Claude Code | 公式の最重要直接競合 | completion condition を設定し、各ターン後に評価して条件達成まで継続 | `/goal` はセッションスコープの軽量な継続条件で、評価器は会話上に示された証拠を見て達成可否を判断します。`mission` は複数サブスキル、永続 `.mission-state`、score history、review/critic loop、threshold gate を持つ、より構造化されたミッション完遂 layer です。 |
| [`ralph-loop`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/ralph-loop) | Claude Code | Claude Code 上の最も近い直接競合 | Stop hook で完了まで iteration を継続 | `ralph-loop` は prompt を completion promise または max iteration まで再投入します。`mission` は計画、実行、ピアレビュー、スコアリング、critic feedback、永続 session state、threshold gate で完了判断します。 |
| [`Superpowers`](https://github.com/obra/superpowers) | Claude Code, Codex, other agents | cross-agent の最有力競合 | planning、TDD、debugging、review、delivery workflow | Superpowers は広い開発方法論です。`mission` は docs、research、release prep、feature 以外の作業も含む任意ミッションの完遂 loop に絞り、明示的な scoring と state gate を持ちます。 |
| [`feature-dev`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/feature-dev) | Claude Code | 隣接 workflow 競合 | discovery、architecture、implementation、quality review | feature-dev は新機能開発に最適化されています。`mission` は feature development 形状に限定せず、任意の project outcome を扱います。 |
| [`code-review`](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-review) / `pr-review-toolkit` | Claude Code | 隣接 quality 競合 | multi-agent review、confidence scoring、test / quality review | これらは PR や code change のレビューが主目的です。`mission` は review を 1 phase として使い、その後の修正と再採点まで loop します。 |
| `github`, `coderabbit`, `circleci`, `codex-security`, `plugin-eval` | Codex | 専門領域の隣接 plugin | PR、review、CI、security、plugin evaluation | `mission` の中で使える下流 tool です。top-level の mission state machine、iteration ごとの score history、Stop hook completion guard は提供しません。 |

意図する打ち出しは以下です。

- **`ralph-loop` と比べて**: prompt 再投入ループに plan / 実行 / レビュー / 採点を加えて構造化している
- **Claude `/goal` と比べて**: 公式の軽量 completion condition より重いが、state、レビュー、採点、改善 loop を含む
- **Superpowers と比べて**: 完全な開発方法論より狭く軽量
- **review / CI plugin と比べて**: review、test、release を phase として呼び出し、ミッション全体の完了可否を判断する orchestrator

`mission` が最も向いているのは、**早く止まりすぎることが主なリスク** の作業です。
曖昧な複数ステップ作業、iteration 間の品質劣化、compaction/resume をまたぐ作業、
あるいは agent が「なぜ今止まってよいのか」を監査可能に説明する必要がある作業に使います。

| 選ぶもの | 向いている場面 |
|---|---|
| `mission` | 複数ステップの outcome に監査可能な completion gate が必要。特に iteration、compaction、research/docs/code 混在作業をまたぐ場合。 |
| Claude Code `/goal` | Claude Code 標準機能として、単一セッション内で検証可能な終了条件まで軽量に走らせたい場合。 |
| `ralph-loop` | Claude Code で、prompt を literal な completion promise が出るまで再投入する loop がほしい場合。 |
| `Superpowers` | brainstorming、planning、TDD、debugging、review、branch delivery まで含む広い coding-agent 方法論がほしい場合。 |
| review / CI / security plugin | workflow の一部分だけを専門的に検査し、全体の完了判断は別の orchestrator または人間が行う場合。 |

## 構成

| パス | 役割 |
|---|---|
| `skills/mission/` | オーケストレータ本体、state CLI、参照ドキュメント、テスト |
| `skills/mission-planner/` | 計画立案サブスキル |
| `skills/mission-executor/` | 実行サブスキル |
| `skills/mission-reviewer/` | ピアレビューサブスキル |
| `skills/mission-critic/` | 改善案立案サブスキル |
| `skills/mission-scorer/` | 5 項目スコアリングサブスキル |
| `scripts/mission-stop-guard.sh` | ループ継続を強制する Stop hook |
| `claude-hooks/hooks.json` | Claude Code 用 Stop hook 宣言 |
| `.claude-plugin/` | `plugin.json` / `marketplace.json` |
| `.codex-plugin/` | Codex plugin metadata |
| `.agents/plugins/` | Codex local marketplace metadata |
| `plugins/mission/` | Codex marketplace plugin wrapper |

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

plugin 配布用に、この repo には `.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json` も含めています。Codex marketplace install は、Codex が marketplace entry の `source.path` として `plugins/` 配下の plugin folder を期待するため、`plugins/mission/` wrapper を使います。Codex plugin package は default では skills-only です。Stop hook は Codex の hook trust と path resolution が Claude Code と異なるため、opt-in 手順に分離しています。詳細は
[`skills/mission/refs/codex-setup.md`](skills/mission/refs/codex-setup.md) と [`docs/DISTRIBUTION.ja.md`](docs/DISTRIBUTION.ja.md) を参照してください。

`codex plugin add mission@mission-marketplace` 後は、`MISSION_PLUGIN_ROOT` を install cache path に設定し、現行 model-visible command text 互換のため `CLAUDE_PLUGIN_ROOT` も同じ値にします。

```bash
export MISSION_PLUGIN_ROOT="${CODEX_HOME:-$HOME/.codex}/plugins/cache/mission-marketplace/mission/1.0.5"
export CLAUDE_PLUGIN_ROOT="$MISSION_PLUGIN_ROOT"
```

marketplace 提出前は [`docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md`](docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md) を確認してください。

## 使い方

```text
/mission <ミッション記述> [--max-iter N] [--threshold X] [--skip-preflight]
```

orchestrator は仮置き、ミッション分解、実行、レビュー、採点を行い、合格または中断条件成立まで反復します。
詳細な運用プロトコルは [`skills/mission/SKILL.md`](skills/mission/SKILL.md) を参照してください。

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

`MISSION_SEARCH_ROOTS` は OS の path separator で複数指定できます。macOS/Linux では `~/workspace:~/dev` のように指定します。

## テスト

```bash
cd skills/mission
python3 -m pytest -q
```

現在のローカル検証結果:

```text
327 passed
```

詳細は [`docs/TESTING.md`](docs/TESTING.md) を参照してください。

## E2E 検証済み事項

2026-06-14、Claude Code 2.1.177、隔離 `CLAUDE_CONFIG_DIR` で正式 install し、以下を確認しました。

- 6 スキル + Stop hook が登録される
- install 時に `${CLAUDE_PLUGIN_ROOT}` が実パスに解決する
- `mission-state.py` が `.mission-state/sessions/*.json` を生成できる
- `mission-reviewer` などの非修飾サブスキル名が実行時に解決される
- Python テストが通る

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

MIT。詳細は [LICENSE](LICENSE) を参照してください。
