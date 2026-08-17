# A1（#506）実装計画 — lifecycle use case を v4 互換 repository 上へ抽出

## 1. 目的とスコープ

`mission-state.py` の lifecycle コマンド 12 本を、**薄い CLI アダプタ / application use case / v4 互換 repository** の 3 層へ分離する。`decide()` を経由してカーネル判定を行い、既存 v1-v4 セッションの JSON shape と lease 挙動を保持する。

やらないこと（後続へ残す）:

| 残す作業 | 担当 |
|---|---|
| `cmd_next` の `_derive_next_action` 切替 | P1 |
| v5 `RecoverableUnitOfWork` への切替 | P1 / Stage 5 |
| review / score / pass の抽出 | A2 |
| artifact / progress / context の抽出 | A3 |
| specialists / planning / handoff の抽出 | A4 |
| permission-preflight / stop-guard-observe の抽出 | A5 |
| v4→v5 の物理マイグレーション | 非スコープ |

## 2. 抽出対象の lifecycle use case

| use case | 現行関数 | 行 |
|---|---|---|
| init | `cmd_init` | 6959 |
| activity_start | `cmd_activity_start` | 8138 |
| activity_end | `cmd_activity_end` | 8164 |
| advance | `cmd_advance` | 8245 |
| set（allowlist） | `cmd_set` | 9331 |
| mark_halt | `cmd_mark_halt` | 13960 |
| reactivate | `cmd_reactivate` | 14003 |
| refresh_pid | `cmd_refresh_pid` | 14121 |
| resume | `cmd_resume` | 14226 |
| update_project_root | `cmd_update_project_root` | 14292 |
| cleanup_stale | `cmd_cleanup_stale` | 14444 |
| halt（bulk） | `cmd_halt` | 14804 |

`resume` / `cleanup_stale` / `halt` は coordinator であり CLI 層に残す。per-session の mutation は `mark_halt` / `refresh_pid` の use case を再利用する。

## 3. 変更対象ファイル

新規: `lib/mission_application/{__init__,ports,lifecycle}.py`、`lib/mission_persistence/legacy_v4.py`、`tests/test_lifecycle_usecases.py`、`tests/test_command_inventory.py`
変更: `bin/mission-state.py`（12 コマンドのアダプタ化）
ミラー: `plugins/mission/skills/mission/lib/` 配下の対応ファイル

## 4. exact contract として決める項目

| 項目 | 決めること |
|---|---|
| `MissionRepository.load()` の戻り値 | typed `MissionState` か v4 raw dict か（A1 は raw で足りるか） |
| `save()` のシグネチャ | `(state, *, backup)` か typed mutation result か |
| `decide()` の呼び出し責務 | use case が直接呼ぶか repository がラップするか |
| aggregate index 更新の失敗扱い | use case でエラー報告しつつ session authority は変えない |
| `cmd_set` の allowlist 境界 | dedicated command へ権限委譲する field の確定 |
| `resume` coordinator の内包度 | CLI 層に留める（判断が揺れたら報告してから実装） |
| use case への `GuidanceFacts` 引数 | 持たせるか否か（guidance 選択のみで accept/reject には使わない） |

## 5. 実装ステップ

```
1. TDD Red（テスト先書き・実 CLI 出力から fixture 生成）
2. ports.py（MissionRepository / MissionClock / MissionIdentity の protocol）
3. legacy_v4.py（v4 互換 repository。StateLock / atomic_write_json / backup_state を内包）
4. lifecycle.py（use case。decide() 経由）
5. mission-state.py のアダプタ書き換え（12 コマンド）
6. inventory テスト（registry owner の重複・未登録検出）
7. plugins ミラー同期と D1 gate 確認
```

`cmd_advance` の lock 外 `json.loads()` bypass path は、アダプタが渡す raw dict で代替し、use case 内では lock 後の `repo.load()` 結果のみを権威とする（移行計画 §10.5 で A1 の担当）。

## 6. TDD Red 一覧

| # | 検証 | legacy 一致の確認方法 |
|---|---|---|
| T1 | advance 前後の state bytes | 実 CLI 出力を golden にして diff |
| T2 | done/halted への advance が bytes 無変更 | exit 2 + bytes 不変を fixture に |
| T3 | mark_halt の halt_reason / category / phase / terminal_outcome | 同上 |
| T4 | `approved_by_user=False` の reactivate が reject | exit 2 + stderr |
| T5 | passes=True の state への reactivate が reject | 同上 |
| T6 | activity start/end の `activity_current` と `activity_log` | 段階的に fixture 化 |
| T7 | init が作る state の keys と schema | golden |
| T8 | aggregate 更新失敗時に session state が変わらない | aggregate を破損させて実行 |
| T9 | A1 ルートに同一コマンドの重複所有者がいない | parser 全サブコマンド vs route 定義 |
| T10 | A1 コマンドが全て use case にマップ済み | 同上 |
| T11 | policy v1 で plan あり/なしの偶数ペアによる executing advance 境界 | plan なしは `missing-canonical-plan` |
| T12 | `cmd_set` で dedicated field が reject される | 実 CLI で確認後に use case テストへ |

## 7. 受け入れ条件

- [ ] 12 コマンドの本体からアダプタ以外の論理が消えている（`atomic_write_json` / `StateLock` / lock 外 `json.loads` が CLI 関数本体に無い）
- [ ] T1-T12 が Green
- [ ] 既存 lifecycle テスト（`test_issue237_advance` / `test_session_lifecycle` / `test_reactivate` / `test_resume` / `test_issue190_halt_category`）が全 Green
- [ ] `issue483_corpus()` の各 v1-v4 バリアントで init / advance / halt 後も schema v4 の shape を保持
- [ ] D1 の再帰 sync と Python 3.9 gate が Green
- [ ] `cmd_next` が引き続き legacy `_derive_next_action` を呼ぶ
- [ ] `lifecycle.py` に `os` / `subprocess` / `sys.stdout` が現れない（I/O 非依存）

## 8. リスクと対策

| リスク | 対策 |
|---|---|
| フィクスチャ手書きで実データを decode できない（K1 再発） | `_run_cli` で必ず実 CLI を叩いて生成。手書き fixture 禁止。init→advance→halt→reactivate の全段を corpus に含める |
| テストが緩く use case と legacy が独立でも Green（K2 再発） | T1-T8 は bytes レベルの diff を assert。`result_bytes == legacy_bytes` を省略しない |
| lock 外 read がアダプタに残る | Step 4 で use case シグネチャを確定し、preview 用引数を明示。lock 外 read を use case に持ち込まない |
| aggregate 更新が use case に漏れて authority 境界が曖昧化 | `save()` に更新コールバックを渡す設計とし、use case は aggregate に直接触らない |
| `cmd_set` の allowlist 境界が不明確 | T12 を先に Red にし、exact allowlist を PR 本文に記載 |
| ミラー漏れで D1 gate が落ちる | Step 7 を独立コミットにし、`plugins/` の diff をゼロにしてから PR |
| `resume` coordinator の引き上げ範囲の判断ミス | coordinator は CLI 層に留める。揺れたら実装前に報告 |

---

## 付録: 2 本目の planner による具体化

計画確定後、独立した 2 本目の planner が同じ Issue を計画した。canonical plan の objective / steps / acceptance は変更しないが、以下の具体化を実装時の指針として取り込む。

### `cmd_advance` の lock 外 read の実体

`mission-state.py` 8258–8262 の `state_preview = json.loads(sf.read_text())` は **error context 生成のための pre-lock read**。lock 内で改めて read しているため二重読み込みになっている。

置き換え方針: pre-lock read を削除し、error context は lock 内 snapshot から生成する。state 不在時は None context で代替する。lock 順序を変える場合は race を作らないよう lock 内 read に一本化する。

### `cmd_set` の narrowing

`phase` / `pid` / `loop_active` / halt / lease / activity / lifecycle timing fieldは専用commandだけが所有し、generic `set` はbytes不変の`expected-gate`で拒否する。`complexity`、review tier、iteration、bounded orchestration observation、extension propertyの既存利用は維持する。

既存v1-v4 stateにtyped decoderが受理できないlegacy fieldが残っていても、単調な安全操作である`mark-halt`は最小control projectionでkernel判断を行う。partial leaseのterminal writeはStateLock下でのみ許可し、raw legacy fieldをdecision viewからauthoritative stateへ投影し直さない。

### 層の責務境界

- `LegacyV4Repository.execute()` は **I/O を行わない**。stdout 出力と `sys.exit` は adapter 側のみ
- aggregate index の失敗は `AggregateIndexError` として上げ、use case が報告して続行する。**session state の write は aggregate failure と独立に完了している**必要がある
- `resume` coordinator は use case を直接呼ばず、現行の adapter 経由パターン（`_capture_command_output`）を維持する

### 非スコープの追加

`cmd_cleanup_empty` の抽出は C1 の担当とする。

### AST による受け入れ検証

散文の確認ではなく機械検証にする。

- `cmd_*` 関数内に `json.loads` / `_transition_phase` / `_write_terminal_outcome` / `_add_to_aggregate` の直接呼び出しが **0 件**
- `cmd_advance` スコープ内に lock 外の `sf.read_text()` が **0 件**
- `lifecycle.py` に `os` / `subprocess` / `sys.stdout` が現れない
