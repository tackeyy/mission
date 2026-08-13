# Design: ローカル evidence handoff（Issue #422）

## 目的

implementer mission と checker（`init --role checker|planning` の従属 mission）間の evidence 受け渡しを、GitHub Issue コメントのポーリング（`gh issue view --json comments` + 30秒固定 wait ループ）からローカルファイルの単一 blocking wait に置き換える。**正規記録（canonical record）は従来どおり GitHub コメント**とし、ランデブー経路だけをローカル化する。

実測背景: checker の壁時間 11.3 分に対し実 active 時間 152 秒。差分約 9 分がポーリングランデブーの損失（2026-08-13 実運用ログ）。

## スコープ

やること:

- `skills/mission/lib/evidence_handoff.py` 新規モジュール（ロジックは全てここ）
- `mission-state.py` に `handoff` サブコマンド群を登録（`publish` / `await` / `verify`。登録とディスパッチの最小行のみ）
- `refs/codex-setup.md` に「checker ランデブー」節を追加
- `SKILL.md` の checker 段落（`init --role` の段落）に 1 行参照を追加
- テスト `skills/mission/tests/test_evidence_handoff.py` 新規

やらないこと:

- 既存 state schema・lease・gate 意味論の変更（handoff は state 外の sidecar であり、mutating state command ではない）
- GitHub コメント投稿の代行（投稿は従来どおり checker 自身が行う）
- handoff ファイルの自動 prune（v1 では対象外。`.mission-state/handoff/` は archive 対象外の一時領域として文書化）
- エージェントランタイム固有 API（wait_agent 等）への依存

## インターフェース定義

### ディレクトリ / スキーマ

- 置き場所: `<project>/.mission-state/handoff/<topic>/<seq>-<digest8>.json`
- `<topic>`: slug（`[a-z0-9][a-z0-9-]*`、最大 64 文字）。推奨命名は `issue-<N>-<purpose>`（例: `issue-979-size-planning`）
- envelope（`mission-evidence-handoff/1`）:

```json
{
  "schema": "mission-evidence-handoff/1",
  "topic": "issue-979-size-planning",
  "seq": 1,
  "created_at": "<ISO8601 UTC>",
  "producer_session": "<session id または空文字>",
  "payload_digest": "sha256:<payload の正規化 JSON の hex>",
  "payload": { "任意の JSON": "checker が提出する evidence 本体" }
}
```

### CLI

```
mission-state.py handoff publish --topic <slug> --input <payload.json> [--producer-session <sid>]
  # payload.json を envelope に包み atomic write（tmp + rename）。stdout に {path, seq, payload_digest} の JSON。exit 0
  # --input - で stdin 読み込みを許可
mission-state.py handoff await --topic <slug> [--after-seq N] [--timeout-sec N]
  # seq > N の最初の envelope が現れるまで内部 1 秒間隔で待機（呼び出し側は 1 回の blocking call）。
  # 発見: stdout に envelope 全体 + path の JSON、exit 0。timeout: exit 3（gate の exit 2 と区別）。既定 timeout-sec=600
mission-state.py handoff verify --path <file> [--expect-digest sha256:<hex>]
  # envelope の payload_digest 再計算一致で exit 0 / 不一致 exit 2。--expect-digest 指定時はその値との一致も要求
```

設計上の制約:

- `publish` は lease を要求しない（state mutation ではない）。ただし `.mission-state` が存在しない場合は exit 2（mission 文脈外での誤用防止）
- `seq` は topic ディレクトリ内の既存最大 + 1。tmp ファイル（`.tmp-` prefix）は列挙から除外し、部分書き込みが `await` に見えないことを保証する
- `payload_digest` は `json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` の sha256。GitHub コメント側に同じ digest を記載することで、ローカル evidence と正規記録の一致を機械検証できる
- timeout の exit 3 は「異常ではない待機満了」。呼び出し側（orchestrator）は再 await または fallback（従来の gh ポーリング）を選べる

### 運用フロー（refs/codex-setup.md に記載する内容）

- checker: evidence 確定 → `handoff publish` → 返却された `payload_digest` を本文に含めて GitHub コメント投稿（正規記録）→ `mark-halt --category evidence-submitted`
- implementer: checker spawn 後は `handoff await --topic <slug> --timeout-sec 600` を 1 回実行して待つ。30 秒刻みの wait ループ・`gh issue view --json comments` ポーリング・`list_agents` 巡回を行わない
- 受領後に GitHub コメントの digest と `handoff verify` で一致確認してから利用する

## 受け入れ条件（検証可能形式）

1. `publish` → `await` → `verify` の往復がテストで通る（同一プロセス内 / 別プロセス想定の両方）
2. `await --after-seq` が過去 seq をスキップし、新しい evidence のみ返す
3. timeout 時に exit 3 / stdout に timeout を示す JSON
4. `.tmp-` 部分書き込みファイルが `await` に拾われない
5. digest 不一致で `verify` が exit 2
6. topic slug バリデーション（不正 slug は exit 2、path traversal 不可）
7. `.mission-state` 不在時の `publish`/`await` は exit 2
8. refs/codex-setup.md・SKILL.md の該当節が更新されている
9. `make test`（pytest skills/mission）が全緑

## テストリスト（test_evidence_handoff.py）

- test_publish_creates_envelope_and_digest
- test_publish_stdin_input
- test_await_returns_new_evidence_after_seq
- test_await_timeout_exit_code_3
- test_await_ignores_tmp_partial_files
- test_verify_digest_match_and_mismatch
- test_topic_slug_validation_rejects_traversal
- test_publish_requires_mission_state_dir
- test_seq_increments_per_topic

## 実装メモ

- 既存 lib モジュール（例: `command_outcomes.py`, `state_snapshot.py`）の書式・docstring・型注釈の慣習に合わせる
- mission-state.py 側の追加は「subparser 登録 + lib 呼び出しディスパッチ」の最小差分にとどめる（#423 が同ファイルのエラー経路を並行改修中のため、競合面を増やさない）
- ドキュメント内のパス例は anonymize 規約（`/Users/<user>/`）に従う（test_artifact_hygiene.py が walk する）
