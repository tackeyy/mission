# R1（#512）実装計画 — authoritative state consumer を versioned reader へ移行

## 1. 目的とスコープ

`sessions/<sid>.json` を各所でバラバラに解釈している現状を、**単一の version-aware Python reader** へ集約する。reader は missing/v1-v4 を直接読み、v5 は `head -> commit -> immutable generation` を strict lineage validation の後にのみ解決する。

Stop hook は authoritative field を shell/JQ で読まず、**Python が出した verdict を受け取って表示するだけ**にする。

やらないこと（後続へ残す）:

| 残す作業 | 担当 |
|---|---|
| 新規 session の v5 切替 | C1 |
| v4 writer の削除・物理マイグレーション | 将来 |
| `cmd_*` の writer 所有権整理 | C1 |

## 2. 現状（調査で確定した実態）

### 2.1 stop-guard.sh の jq 直読み

| 行 | 読んでいる authoritative field |
|---|---|
| 235 / 237 / 238 | `loop_active` / `passes` / `halt_reason` |
| 253-257 | `pid` / `owner_session_id` / `lease_id` / `fencing_epoch` / `lease_expires_at` |
| 298-301 | `iteration` / `score_history[-1].composite` / `threshold` / `mission` |
| 311 | `awaiting_user` |
| 357-369 | `score_history | length` / `phase` / `session_id` / `issue_ref` |
| 378-394 | 並列セッション走査で上記を再度 jq で読む |

**既に確立している委譲パターン**: `_mission_state_freshness()`（172-190 行）が `mission-state.py freshness --state-file <sf>` を `timeout 5` 付きで呼び、JSON verdict を受け取る。判定不能時は stale auto-halt せず通常 block へ戻す fail-safe。**R1 はこのパターンを全 verdict へ拡張する。**

### 2.2 Python 側の散在

- `_load_state_json`（351-354）は `json.loads` + `_validate_schema_version` の薄いラッパで、`strict_reader` / `json_codec` を通さない
- `cmd_freshness`（9330）と `cmd_list`（14862）はそのヘルパすら使わず生 `json.loads`
- `mission-state.py` 内に生 `json.loads(sf.read_text())` が **30 箇所以上**散在
- `mission-audit.py:815` / `worktree_archive.py:854` も生 `json.loads`

### 2.3 既存の部品（再利用する）

| 部品 | 責務 |
|---|---|
| `strict_reader.read_stable_bytes` | lstat → O_NOFOLLOW open → fstat 照合の TOCTOU 耐性読み取り |
| `json_codec.decode_json_object` | UTF-8 decode → 重複 key 拒否 → NaN/Inf 拒否 → FrozenJsonObject |
| `versions.read_schema_version` | schema_version の型・範囲検証 |
| `fenced_commit.parse_head` | v5 head bytes → `HeadRecord`（commit / generation / session_id / state_generation）。P1 で公開化済み |

`state_snapshot.py` は discovery + seal のキャッシュ層であり reader ではない。**置き換え対象ではなく、reader の利用者になる。**

## 3. 設計

### 3.1 新規モジュール `mission_persistence/authoritative_reader.py`

```
read_authoritative_snapshot(session_path, *, expected_session_id=None) -> AuthoritativeSnapshot
```

- `read_stable_bytes` → `decode_json_object` → format 判別
- `schema` が `mission-head/1` → `parse_head` で `HeadRecord` を得て commit → generation を strict lineage validation の後に解決
- それ以外 → `read_schema_version` で missing/v1-v4 を検証して直接読む
- **返すのは verified typed snapshot**。生 dict を返さない

`AuthoritativeSnapshot` は少なくとも `loop_active` / `passes` / `halt_reason` / `phase` / `iteration` / `session_id` / `issue_ref` / `lease`（owner/id/epoch/expires）/ `pid` / `updated_at` / `score_history` を typed に持つ。

### 3.2 Stop verdict コマンド

`mission-state.py stop-verdict --state-file <sf> --json` を新設し、Stop hook が必要とする判定（block / skip / warn とその理由・表示用メッセージ素材）を **Python 側で確定**して返す。stop-guard.sh は verdict と表示文字列を受け取るだけにする。

`freshness` の既存契約は壊さない（別コマンドとして残す）。

### 3.3 fail-closed 規律

**「読めない v5 session を inactive として扱わない」**が最重要。malformed/missing head・commit・generation、digest/size/generation drift、重複 key、非有限数、symlink/FIFO/hard link、future schema はすべて fail-closed とし、**ファイル名や空 state へフォールバックしない**。

Stop hook 側は verdict 取得に失敗した場合、**skip ではなく block 側へ倒す**（現行の freshness fail-safe と同じ思想）。

### 3.4 静的 guard

`loop_active` / `passes` / `halt_reason` / lease 系 / freshness 系を、shell/JQ または consumer ローカルで解釈することを禁止する静的検査を追加する。

- shell: `scripts/*.sh` に対し、上記フィールド名を含む `jq` 呼び出しを検出して失敗させる
- Python: reader 以外のモジュールが上記フィールドを生 dict から直接 `.get()` することを AST で検出

## 4. exact contract として確定する項目

| 項目 | 決めること |
|---|---|
| `AuthoritativeSnapshot` のフィールド集合 | 上記 3.1 の一覧で足りるか。consumer ごとの追加要求 |
| v5 lineage validation の厳密度 | head → commit → generation の各段で何を検証するか（U3/U4 の既存検証と重複させない） |
| `stop-verdict` の出力スキーマ | `mission-stop-verdict/1` として versioned にする |
| 移行範囲 | 30 箇所超の `json.loads` を全部変えるか、authoritative field を読む箇所に限るか |
| 静的 guard の allowlist | reader 自身と、authoritative でない読み取り（表示専用等）の扱い |

**移行範囲の暫定判断**: 全 `json.loads` の置換は差分が巨大になりレビュー不能になる。**authoritative field（loop_active / passes / halt_reason / lease / freshness）を解釈する箇所に限定**し、それ以外は C1 の writer 所有権整理に委ねる。この判断は PR 本文に明記する。

## 4.1 既存テスト資産（調査で確定・再利用する）

| 資産 | 場所 | R1 での使い方 |
|---|---|---|
| Stop hook テスト | `tests/test_stop_hook.py` | `subprocess.run(["bash", HOOK])` で実 shell を起動し stdin に JSON、stdout の JSON を検証する方式。**T1/T2/T10 はこの方式で書く**（in-process モック不可） |
| Stop guard dedupe テスト | `tests/test_stop_guard_dedupe.py` | 同方式。counter sidecar の fail-safe 系が既にあるので退行させない |
| audit テスト | `tests/test_mission_audit.py` | `subprocess.run([sys.executable, MISSION_AUDIT_PY])` |
| v5 fixture | `tests/test_issue511_p1_repository_binding.py:109` の `_seed_repository(tmp_path) -> (repository, root, lease_id)` | v4/v5 混在テストは `@pytest.mark.parametrize("repository_kind", ["legacy-v4", "v5"])` と組み合わせる既存パターンに倣う |
| conftest フィクスチャ | `tests/conftest.py` | `run_cli` / `state_dir` / `read_state` / `_isolate_session_env`(autouse) を使う。env 非依存が既に担保されている |
| AST guard の先例 | `test_issue511_p1_repository_binding.py:488`、`test_issue510_a5_application.py:530` 等 11 件 | §3.4 の静的 guard は **bin/scripts を AST 走査する既存パターン**（`test_production_entrypoints_do_not_import_u2_private_persistence_seam`）と同じ構造で書く |

**ミラー**: `plugins/mission/scripts/mission-stop-guard.sh` に byte 一致のミラーがあり `test_plugins_in_sync.py:268` の `test_stop_guard_in_sync` が検証している。**shell を変更したら必ず cp 同期する。**

## 5. 実装ステップ

```
1. TDD Red（全テストを先に書いて落ちることを確認）
2. authoritative_reader.py（v1-v4 直読み + v5 lineage 解決）
3. stop-verdict コマンド
4. stop-guard.sh の jq 置換（verdict 受け取りのみに）
5. mission-audit.py / worktree_archive.py / CLI query の移行
6. 静的 guard（shell + AST）
7. plugins ミラー同期と D1 gate 確認
```

## 6. TDD Red 一覧

| # | 検証 |
|---|---|
| T1 | active な v5 session が v4 と**同一に** Stop を block する |
| T2 | pass / halt / evidence-complete の verdict が現行の区別を保つ |
| T3 | v4/v5 混在 root で audit / archive / snapshot / list / stats / freshness / next が等価な結果を返す |
| T4 | malformed head で fail-closed（ファイル名・空 state へフォールバックしない） |
| T5 | commit / generation の欠落で fail-closed |
| T6 | digest / size / generation drift で fail-closed |
| T7 | 重複 key / 非有限数の JSON で fail-closed |
| T8 | symlink / FIFO / hard link で fail-closed |
| T9 | future schema で fail-closed（#483 契約の維持） |
| T10 | **読めない v5 session を inactive 扱いしない**（Stop が block 側へ倒れる） |
| T11 | 静的 guard が shell/JQ による authoritative field 解釈を拒否する |
| T12 | 静的 guard が consumer ローカルの直接解釈を拒否する |
| T13 | old-v4-reader × new-v5-writer の互換 |
| T14 | new-reader × old-v4-writer の互換 |

## 7. 受け入れ条件

- [ ] 対象 consumer が authoritative reader または Python verdict を使う
- [ ] Stop hook が読めない v5 session を inactive として扱えない（T10）
- [ ] audit / archive / snapshot が strict evidence lineage と現行 v4 出力を保つ
- [ ] T1-T14 が Green
- [ ] D1 distribution / script mirror / compatibility gate が全変更を覆う
- [ ] フルスイート green

## 8. リスクと対策

| リスク | 対策 |
|---|---|
| 差分が巨大化してレビュー不能 | 移行範囲を authoritative field に限定（§4）。範囲判断を PR 本文へ明記 |
| Stop hook の退行で無限ループ or ループ停止 | T1/T2/T10 を先に Red にする。verdict 取得失敗は block 側へ倒す |
| v5 lineage 検証が U3/U4 と重複・矛盾 | 既存 API（`parse_head` 等）を再利用し、検証ロジックを新規実装しない |
| shell 側の fail-safe を壊す | `_mission_state_freshness` の既存 fail-safe 構造を踏襲する |
| 静的 guard が過検出で開発を阻害 | allowlist を明示し、表示専用の読み取りは対象外と定義する |
| plugins ミラー漏れで D1 gate 失敗 | Step 7 を独立コミットにし `plugins/` の diff をゼロにしてから PR |
