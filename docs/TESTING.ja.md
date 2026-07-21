# テストガイド

**日本語** | [English](TESTING.md)

このリポジトリでは、state manager と Stop hook を中心にテストします。最重要の不変条件は、score history が threshold gate を満たさない限り、orchestrator が mission を完了扱いにできないことです。

## テストコマンド

Python テスト全体:

```bash
cd skills/mission
python3 -m pytest -q
```

特定ファイル:

```bash
cd skills/mission
python3 -m pytest -q tests/test_mark_passes_threshold.py
```

shell lint:

```bash
shellcheck scripts/mission-stop-guard.sh scripts/sync-codex-plugin-wrapper.sh scripts/mission-local-authoring-sync.sh
```

## テスト構成

| Path | 検証内容 |
|---|---|
| `skills/mission/tests/test_mark_passes_threshold.py` | passing gate と force override |
| `skills/mission/tests/test_push_score.py` | score normalization と score history 書き込み |
| `skills/mission/tests/test_session_routing.py` | session file routing |
| `skills/mission/tests/test_session_lifecycle.py` | state lifecycle transition |
| `skills/mission/tests/test_stop_hook.py` | Stop hook blocking behavior |
| `skills/mission/tests/test_cleanup_stale.py` | stale / orphan state cleanup |
| `skills/mission/tests/test_local_authoring_sync.py` | local authoring の最新 remote main bootstrap と fail-closed checkout 保護 |
| `skills/mission/tests/test_doc_consistency.py` | ドキュメントと command の整合性 |

## テストを追加すべき変更

以下を変更する場合はテストを追加または更新してください。

- `mission-state.py` の command、schema field、session routing
- Stop hook の owner check または blocking condition
- scoring item normalization または threshold logic
- Claude Code / Codex の multi-session behavior
- command、path、必須 field を記載するドキュメント

## ローカル E2E チェック

### Claude Code

Claude Code plugin install 挙動を確認するときは、既存設定を汚さないように隔離した Claude config directory を使います。

```bash
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
```

そのうえで local marketplace 経由で plugin をインストールします。

```text
/plugin marketplace add /absolute/path/to/mission
/plugin install mission@mission-marketplace
```

確認項目:

- `claude plugin details mission` に 6 skills が表示される
- Stop hook が登録される
- 最小 `/mission` 実行で `.mission-state/sessions/*.json` が生成される
- `mission-reviewer` などの subskill が namespace prefix なしで解決される

`.mission-state/` 出力は commit しないでください。

### Codex

Codex marketplace install 挙動を確認するときは、隔離した `CODEX_HOME` を使います。

```bash
export CODEX_HOME="$(mktemp -d)"
codex plugin marketplace add /absolute/path/to/mission
codex plugin list
codex plugin add mission@mission-marketplace
```

確認項目:

- `codex plugin list` に `mission@mission-marketplace` が installed / enabled として表示される
- install cache に `.codex-plugin/plugin.json`、`skills/mission/SKILL.md`、`scripts/mission-stop-guard.sh` が入っている
- install cache に `.git`、`.mission-state`、`.pytest_cache` が入っていない

`skills/` または `scripts/` を変更した場合、この確認前に `scripts/sync-codex-plugin-wrapper.sh` を実行してください。
