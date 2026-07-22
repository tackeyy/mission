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

**進行の正は `mission-state.py next`** (ADR-002 Stage 3): Codex には Stop hook のループ強制が無いため、各 iteration の区切り・「次に何をするか迷った」時に必ず `next` を呼び、返った `next_action` に従う。これにより Compact Instructions が compaction で消失しても、state さえ読めればループ継続手順が復元できる (二重失敗対策)。

**compaction 復帰直後は `mission-state.py resume` (#123)**: refresh-pid → cleanup-empty → cleanup-stale → next を正しい順序で 1 コマンドに統合したもの。復帰直後の旧 PID で自 state を誤って halt しないよう refresh-pid を先に走らせる。返り値の `next_action` に従う (`next` を別途呼ぶ必要はない)。

`resume` が手動 halt (`awaiting-approval` / `blocked-external` 等) を返した場合は自動解除しない。対象操作と state 再活性化をユーザーが明示承認した後、返された `command_hint` の `reactivate --approved-by-user --expected-category ... --reason ...` を使う。汎用 `set loop_active=true halt_reason=''` は承認監査を残さないため使用しない。

**開始時の正は `mission-state.py init` + strict preflight** (Issue #108 / #144): Codex では、read-only の repository 確認を除く task setup、worktree 作成、実装より前に必ず mission state を作り、直後に strict preflight を実行する。

local authoring では、そのさらに前に `SKILL.md` の「Local authoring source bootstrap」に従って `mission-local-authoring-sync.sh` を実行する。成功した fetch が観測した `origin/main` と `HEAD` が一致した場合だけ続行し、同期後の `SKILL.md` を disk から読み直す。network 不通や checkout 保護条件の不一致では古い版へ fallback しない。versioned plugin install はこの source bootstrap の対象外。

```bash
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" init \
  "<mission>" --complexity <Simple|Standard|Complex|Critical>
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" codex-preflight --json --strict
```

source bootstrap を完了して更新後の `SKILL.md` を読み直し、`init` と strict preflight を実行する。strict preflight の exit 0 を確認するまで、対象 repository の task setup（fetch / pull / switch / worktree 作成など）や実装へ進まない。exit 2 の場合は JSON の `next_action` と `required_actions` に従い、`init` または inactive state を解決してから同じコマンドを再実行する。通常の `codex-preflight --json` は診断互換のためexit 0を維持するため、開始ゲートには使わない。

preflight は次を検出する。

- `state_guard.active=false`: `init` 未実行または非terminalのinactive stateでは、task setup・実装・final報告を禁止する。passed / halted は terminal stateのため、strict preflightではなく後述の`next`でfinal可否を判定する。
- `codex_stop_hook.configured=false`: Codex Stop hook が未設定。skills-only fallback として継続できるが、各 phase 境界と final 直前に `mission-state.py next` を呼ぶ。
- `mechanical_guard=none`: state も hook も効かない。`init` または hook 設定を先に直す。

Stop hook自体も必須運用にしたい環境では、開始時の`--strict`に加えて`--require-stop-hook`を指定する。

```bash
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" codex-preflight --json --strict --require-stop-hook
```

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
bash "$MISSION_PLUGIN_ROOT/scripts/mission-local-authoring-sync.sh"
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
export MISSION_PLUGIN_ROOT="${CODEX_HOME:-$HOME/.codex}/plugins/cache/mission-marketplace/mission/2.0.0"
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
5. `mission-state.py codex-preflight --json --require-stop-hook` が exit 0 になることを確認する

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
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" codex-preflight --json
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" next
```

`state_guard.active=true` かつ `passes != true` / `halt_reason` 空なら、`next_action` に従って続行する。PID が古い場合は `refresh-pid` 後に続行する。

final の直前は `mission-state.py next` を呼ぶ。`next_action=report-complete`（`passes=true`）なら完了報告、`next_action=report-blocker`（明示的な `halt_reason` あり）なら未完了報告を返す。それ以外の action では final を返さず、指示されたphaseを継続する。terminal stateはactiveではないため、`mark-passes` / `mark-halt` 後のfinal判定にstrict preflightを再利用しない。

```bash
python3 "$MISSION_PLUGIN_ROOT/skills/mission/bin/mission-state.py" refresh-pid
```

## aggregate-reviews が回らない時のトラブルシュート (#187)

Codex は (a) Skill tool の単一メッセージ並列起動ができない (b) reviewer 役をロール切替で同一コンテキストに適用しがちで reviewer JSON の書き出しが自演になりやすい、という制約がある。この制約下で `aggregate-reviews` の初回試行が失敗すると、`mark-passes --force` に逃げてしまう実害が確認されている (実運用ログ監査 #185/#187 参照)。**`--force` はユーザーが明示指示した場合のみ**。aggregate-reviews が失敗しても force には進まず、以下の手順でやり直す。

### 正規経路 (毎 iteration)

```
reviewer が mission-review/1 JSON を出力
  -> mission-state.py aggregate-reviews --iteration N --input <file...> --out <path>
  -> mission-state.py push-score --iteration N --scoring-json <path>
  -> mission-state.py mark-passes
```

### 初回失敗時のリトライ手順

1. `mission-state.py next` を呼ぶ。直前の score entry が `score_source=scoring-json` なのに `findings_evidence_path` が無い場合、`next_action=aggregate-reviews` と共に「force を使わずやり直す」旨の `command_hint` が返る。
2. reviewer 役が Codex の同一コンテキストで `mission-review/1` 形式の JSON を直接出力できない場合は、**mission-scorer を散文→JSON 変換の fallback として使う** (reviewer の散文レビューを scorer に渡し、`mission-review/1` 形式へ整形させる)。
3. 変換された JSON を `aggregate-reviews` に渡し直す。得られた `--out` パスを `push-score --scoring-json` へ渡す。同一 iteration への 2 回目の push なので `--resubmit-reason` が必須 (#122)。
4. それでも `aggregate-reviews` の入力が用意できない場合は、`mark-halt --reason "<理由>" --category blocked-external` で正直に中断する。**未達を force で覆い隠さない**。

### codex-preflight での事前確認

`codex-preflight --json` の `scoring_pipeline` フィールドに上記の正規経路が要約されている。Codex セッション開始時に一度読んでおくと、初回失敗時に force へ逃げるリスクを減らせる。

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-06-13 | 初版作成。PID owner 判定の Codex 対応 + Stop hook 有効化手順 |
| 2026-06-14 | OSS plugin 化に伴い、Codex での path 解決と hook trust 注意を追記 |
| 2026-06-15 | Codex plugin metadata (`.codex-plugin/`, `.agents/plugins/`) を追加。Codex default package は skills-only、Stop hook は user hook opt-in に整理 |
| 2026-07-03 | `codex-preflight` を追加し、state 未初期化 / Stop hook 未設定 / `next` fallback の診断手順を明文化 |
| 2026-07-11 | #187: aggregate-reviews が回らない時のトラブルシュート節を追加。`next` の findings evidence 欠落検出と `codex-preflight` の `scoring_pipeline` フィールドを併記し、`--force` へ逃げない手順を明記 |
| 2026-07-21 | #144: Codex開始時の `--strict` を必須化し、task setup / worktree / implementation 前のexit 0確認と、final前の`next` terminal gateを明記 |
