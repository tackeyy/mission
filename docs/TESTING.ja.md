# テストガイド

**日本語** | [English](TESTING.md)

このリポジトリでは、state manager と Stop hook を中心にテストします。最重要の不変条件は、score history が threshold gate を満たさない限り、orchestrator が mission を完了扱いにできないことです。

## テストコマンド

no-install smoke、CI と同一の全テスト、slow operational を分けて実行します。

```bash
make test-smoke
make test
make test-e2e
```

full / E2E tier は pinned された `.github/requirements-ci.txt` から
`.venv-ci` を作ります。各 target は exact Git tree SHA と実行 test manifest を
`mission-test-report/1` JSON で出力します。

CI はこのテスト群が CPU バウンドであるため、複数の runner へ分割して実行します。
CI の 1 シャードをローカルで再現するには次を実行します。

```bash
make test-shard SHARD_INDEX=1 SHARD_TOTAL=6
```

分割は `scripts/ci_shard_targets.py` が行います。決定的・網羅的・排他的で、
追跡下のテストファイルは必ずどれか 1 つのシャードに属します。シャードが 1 件も
選ばなかった場合は非 0 で終了するため、分割の破綻はテスト減少ではなくビルド失敗
として表面化します。

特定ファイル:

```bash
.venv-ci/bin/python -m pytest -q skills/mission/tests/test_mark_passes_threshold.py
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
