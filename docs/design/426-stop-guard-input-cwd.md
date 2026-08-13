# Design: stop-guard の CWD 解決を hook input 優先へ変更（Issue #426）

## 問題（正診断は Issue #426 コメント参照）

`scripts/mission-stop-guard.sh` の `find_agent_proc()` はプロセス祖先の claude/codex の実 cwd を、hook input JSON の `.cwd` より優先して `SESSIONS_DIR` を決める。このため agent CLI 配下で `make test` を実行すると、テストが input で渡す tmp_path が無視され、stop-guard 系 14 件（`test_stop_guard_dedupe.py` 13 件 + `test_stop_hook.py::test_hook_lsof_timeout_falls_back_to_input_cwd`）が誤失敗する。CI（ubuntu、agent 祖先なし）では input fallback に落ちるため green。

実運用でも、Stop hook の input `.cwd` は常にセッションの作業ディレクトリを指す一次情報であり、祖先プロセスの cwd はヒューリスティック（agent が別ディレクトリで起動されたケースで誤る）。

## 変更内容

`scripts/mission-stop-guard.sh` の CWD 解決順序を次に変更する:

```
1. MISSION_HOOK_CWD 環境変数（テスト・明示 override、現行どおり最優先）
2. hook input JSON の .cwd（非空 かつ ディレクトリとして存在する場合）← 新規に昇格
3. find_agent_proc() による祖先 agent プロセスの実 cwd（fallback へ降格）
4. $PWD（最終 fallback、現行どおり）
```

- `AGENT_PID` の発見ロジック（`find_agent_proc` の pid 探索）は**変更しない**。CWD の採用源のみ変更する（AGENT_PID は heartbeat/pid 判定で引き続き使用）
- `_mission_pid_cwd()` 自体（lsof/timeout 経路）は変更しない
- input `.cwd` が存在しないディレクトリを指す場合は従来どおり祖先 cwd → $PWD へ落ちる（fail-safe）

## スコープ

やること:

1. `scripts/mission-stop-guard.sh` の CWD 解決ブロックの並び替え（上記）
2. `plugins/mission/scripts/mission-stop-guard.sh` ミラー同期
3. shellcheck green
4. 回帰確認: `pytest -q skills/mission/tests/test_stop_guard_dedupe.py skills/mission/tests/test_stop_hook.py` が**このマシン（agent CLI 配下）で全緑になること**（これ自体が受け入れテスト。現状 14 件失敗 → 修正後 0 件になるはず）

やらないこと:

- `_mission_pid_cwd` の実装変更（perl/python3/timeout fallback は現状維持）
- mission-state.py 側の変更
- 新規テストファイルの追加（既存 14 件が回帰テストとして機能する。必要なら input cwd 優先を直接検証する小テストを 1 件だけ追加可）

## 受け入れ条件

1. agent CLI 配下での `pytest -q skills/mission/tests/test_stop_guard_dedupe.py skills/mission/tests/test_stop_hook.py` が全緑
2. `MISSION_HOOK_CWD` override の既存挙動が不変（既存テストで担保）
3. input `.cwd` 欠落・空・不存在ディレクトリ時に祖先 cwd → $PWD へ fail-safe
4. shellcheck green・plugins ミラー同期
5. `make test` 全体で新規失敗ゼロ
