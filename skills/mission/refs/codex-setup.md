# Codex CLI での mission セットアップ

mission スキルは Claude Code と Codex の両方で動作する。本書は Codex 固有の導入、パス解決、Stop hook の扱いをまとめる。

## 対応範囲

| 項目 | Codex 対応 |
|---|---|
| skill 実行 | 対応。`~/.codex/skills` symlink または plugin install |
| state 管理 | 対応。`CODEX_THREAD_ID` を `cx-...` session として記録 |
| agent PID owner 判定 | 対応。`claude` / `codex` の両 CLI process を認識 |
| 指示ベースの ReAct loop | 対応 |
| Stop hook 強制 | opt-in。Codex の hook trust と path 解決を明示設定する |

## 推奨: まず skills-only で使う

Codex では、まず Stop hook なしの skills-only 構成で使う。短〜中規模の mission では、Compact Instructions と state 確認による指示ベースの loop で動作する。

長時間 mission や compaction を跨ぐ mission で「未達なのに停止する」ことを機械的に防ぎたい場合だけ、後述の Stop hook を opt-in で設定する。

**進行の正は `mission-state.py next`** (ADR-002 Stage 3): Codex には Stop hook のループ強制が無いため、各 iteration の区切り・compaction 復帰直後・「次に何をするか迷った」時に必ず `next` を呼び、返った `next_action` に従う。これにより Compact Instructions が compaction で消失しても、state さえ読めればループ継続手順が復元できる (二重失敗対策)。

## Local authoring: symlink install

```bash
MISSION_REPO="$HOME/dev/mission"

for s in mission mission-planner mission-executor mission-reviewer mission-critic mission-scorer; do
  ln -sfn "$MISSION_REPO/skills/$s" "$HOME/.codex/skills/$s"
done

# agent-neutral root。shell rc に永続化する。
export MISSION_PLUGIN_ROOT="$MISSION_REPO"

# 現行 SKILL.md / refs には Claude Code 公式変数名が残るため互換用にも設定する。
# 将来、全 command text を MISSION_PLUGIN_ROOT に寄せたら不要にできる。
export CLAUDE_PLUGIN_ROOT="$MISSION_REPO"
```

確認:

```bash
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" --help
```

## Plugin install 用 metadata

この repository には Codex 用 metadata も含める。

| Path | 用途 |
|---|---|
| `.codex-plugin/plugin.json` | Codex plugin manifest |
| `.agents/plugins/marketplace.json` | repo-local Codex marketplace entry |
| `plugins/mission/` | Codex marketplace install 用 plugin wrapper |

Codex marketplace install は `codex plugin marketplace add /path/to/mission` → `codex plugin add mission@mission-marketplace` で検証する。Codex CLI は marketplace entry の `source.path` として repo root そのものではなく plugin folder を期待するため、entry は `./plugins/mission` を指す。`skills/` または `scripts/` を変えたら `scripts/sync-codex-plugin-wrapper.sh` で wrapper を同期する。

Install 後、Codex には Claude Code の `${CLAUDE_PLUGIN_ROOT}` 相当の plugin-root env がないため、shell rc で root を明示する。

```bash
export MISSION_PLUGIN_ROOT="${CODEX_HOME:-$HOME/.codex}/plugins/cache/mission-marketplace/mission/1.0.6"
export CLAUDE_PLUGIN_ROOT="$MISSION_PLUGIN_ROOT"
```

`codex plugin add --json` の `installedPath` が上記と異なる場合は、その `installedPath` を `MISSION_PLUGIN_ROOT` に使う。

Codex の plugin package は default では skills-only にしている。理由は、Codex は plugin-bundled `hooks/hooks.json` も読み込める一方で、Claude Code の `${CLAUDE_PLUGIN_ROOT}` に相当する plugin-root 変数が Codex manual では確認できないため。Claude Code 専用 hook config は `claude-hooks/hooks.json` に移し、Codex が default hook として誤読しないようにしている。

`/hooks` で `failed to parse plugin hooks config ... unknown field 'description', expected 'hooks'` のようなエラーが出る場合、表示された path の plugin に Claude Code 用 hook metadata が混入している。`mission` の Codex package は CI で `hooks` manifest と `hooks/hooks.json` の混入を禁止しているため、path が `mission` 以外ならその plugin を Codex で無効化するか、Codex 互換の `{"hooks": ...}` だけの config に直す。

## Codex でのパス解決

Claude Code の plugin hook / MCP config では `${CLAUDE_PLUGIN_ROOT}` が公式に document されている。一方、Codex manual では plugin root 変数は確認できない。

そのため Codex では以下の方針にする。

- local authoring: `MISSION_PLUGIN_ROOT` を使う
- 現行 skill command text との互換: `CLAUDE_PLUGIN_ROOT` も同じ値で export する
- Stop hook: plugin-bundled hook ではなく、絶対パス指定の user hook として opt-in 設定する

## Stop hook を Codex で有効化する

Codex hooks は有効だが、non-managed command hook は実行前に review / trust が必要。`/hooks` で確認・承認する。

### User hook 例

`~/.codex/hooks.json` に以下を追加する。`/ABS/PATH/TO/mission` は実 clone 先に置き換える。

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/ABS/PATH/TO/mission/scripts/mission-stop-guard.sh\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

設定後:

1. Codex を再起動する
2. `/hooks` を開く
3. `mission-stop-guard.sh` の hook を確認して trust する
4. 小さな `/mission` 相当の run で `.mission-state/sessions/*.json` と Stop hook block を確認する

### Hook logic の直接確認

未達 state がある project root で以下を実行する。

```bash
MISSION_HOOK_CWD="$(pwd)" CODEX_THREAD_ID="<thread-id>" \
  bash "$MISSION_PLUGIN_ROOT/scripts/mission-stop-guard.sh" <<'JSON'
{"stop_hook_active": false}
JSON
```

未達かつ session owner が一致すれば `{"decision":"block",...}` が返る。

## Compaction 時の注意

Stop hook 無しでも基本動作するが、compaction 後に model が Compact Instructions を見落とすと、未達 (`loop_active:true`) のまま停止しうる。

対策:

```bash
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" get
```

`passes != true` かつ `halt_reason` が空なら、`refresh-pid` 後に続行する。

```bash
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" refresh-pid
```

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-06-13 | 初版作成。PID owner 判定の Codex 対応 + Stop hook 有効化手順 |
| 2026-06-14 | OSS plugin 化に伴い、Codex での path 解決と hook trust 注意を追記 |
| 2026-06-15 | Codex plugin metadata (`.codex-plugin/`, `.agents/plugins/`) を追加。Codex default package は skills-only、Stop hook は user hook opt-in に整理 |
