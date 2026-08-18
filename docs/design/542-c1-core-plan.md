# C1-core（#542）実装計画 — ジェネシス commit API と cmd_init の v5 cutover

## 1. 目的とスコープ

新規 `init` が **schema v5 の session を作る**ようにする。既存 v1-v4 session は v4 writer のまま。

**このリリースが mission 本番の挙動を変える唯一の変更**であり、rollback は init default を戻すことに限定する。

やらないこと（→ #543 / 将来）:

| 残す作業 | 担当 |
|---|---|
| A1 管理外コマンドの完全な owner 整理 | #543 (C2) |
| init 以外への operation_id 配線 | #543 (C2) |
| v4 writer の削除・既存 session の物理マイグレーション | 将来 |

## 2. 実装ブロッカーと解法（調査で確定・本計画で決める）

### 2.1 ジェネシス commit の公開 API が無い

現状:
- `stage()` は `admitted.base is None` で `transition-binding-mismatch`（fenced_commit.py:1788）
- `execute()` は `admitted.base is None` で `initial-state-required`（:1867）
- `_stage_persistence(admitted, *, state_bytes, effects)` は private だが genesis を扱える（:2378）

**解法**: `LocalFencedRepository` に **`initialize(request, *, state_bytes) -> CommitResult`** を新設する。

- `begin(request)` で genesis `AdmittedSnapshot`（base=None）を得る
- `base is not None` なら `FencedCommitError("session-already-initialized", ...)` で拒否（**再 init を防ぐ**）
- `_stage_persistence` で staging → `commit()` で公開
- 既存の `stage()` / `execute()` の genesis 拒否は**そのまま維持**する（genesis 経路を 1 本に固定し、通常経路から誤って genesis を作らせない）

### 2.2 `schema_origin=V5` の genesis MissionState を作れない

`encode_v5_state(state, guidance)` は `schema_origin is SchemaOrigin.V5` の state しか受けない。

**解法**: **v4 の state bytes を genesis の初期 payload とする**。

理由: `_seed_repository`（既存テストヘルパ）が実際にこの方式を採っており、`stage()` 内でも `schema_origin` が V5 でなければ `project_legacy_document` で投影する既存経路がある。v5 の「head/commit/generation で包む」という本質は**コンテナ側**にあり、初期 payload が v4 shape でも v5 session として成立する。

つまり **v5 session = v5 コンテナ（head → commit → generation → object）+ 検証済み state payload**。schema_version フィールドを 5 に変える作業は本 Issue の範囲外（そこを変えると v4 reader 互換の議論が別途必要になる）。

**この判断は PR 本文に明記し、レビューで妥当性を問う。**

### 2.3 init 用 operation_id の発番方法が未定義

**解法**: `init` の operation_id は **session_id と初期 state の intent から決定的に導出**する。

- ランダム UUID にすると、crash 後の retry で別 operation とみなされ二重 init になりうる
- 決定的キーなら、同一 session_id への再 init 試行が operation tombstone に当たり、**元の CommitResult が返る**（冪等）
- 具体形は実装時に確定（`session_id` + canonical な初期 command の digest）

### 2.4 genesis 用 guidance が未定義

**解法**: genesis では **guidance を空（または既定値）とする**。guidance は「前 snapshot からの継続情報」であり genesis には前がない。既定値の具体形は `decode_snapshot` の戻り型に合わせて実装時に確定する。

## 3. cmd_init の切替

```
cmd_init
  ├─ 既存 state ファイルあり → 現行どおり（re-init 拒否 or 既存経路）
  └─ 新規
       └─ v5 経路: LocalFencedRepository.initialize(request, state_bytes=<v4 shape の初期 state>)
```

- `select_legacy_repository` の `v5_factory` を `reject_v5` スタブから**実 v5 repository** へ差し替える
- **rollback**: init default を v4 に戻す 1 箇所の変更で v4 に戻せる構造にし、その手順を docs に書く。既に作られた v5 session は v5 のまま（rollback しても壊れない）

## 4. フルライフサイクル成立の範囲

新規 v5 session で **init → set → advance → activity → mark-halt / review-import → review-finalize → mark-passes → closeout** が回ること。

A1 の 12 コマンドは既に repository 経由なので、`FormatPinnedRepositorySelector` が v5 を選べば動くはず。**動かないコマンドが出たら、そのコマンドを本 Issue の範囲に含める**（フルライフサイクルが回らない cutover は無意味なため）。範囲に含めた/含めなかったコマンドを最終報告で区別する。

## 5. TDD Red 一覧

| # | 検証 |
|---|---|
| T1 | 新規 `init` が v5 session（`mission-head/1` + commits/generations/objects）を作る |
| T2 | 既存 v1-v4 session は v4 writer のまま（upgrade しない） |
| T3 | 新規 v5 session で **フルライフサイクルが完走**する |
| T4 | 同一 session_id への再 init が `session-already-initialized` で拒否される |
| T5 | genesis commit 後の crash + 同一 operation ID retry が**元の結果**を返す |
| T6 | 異なる intent + 同一 operation ID は `operation-intent-collision` |
| T7 | `stage()` / `execute()` は依然 genesis を拒否する（genesis 経路が 1 本） |
| T8 | dual-write が無い（1 session につき 1 writer）／per-invocation の writer 切替が無い |
| T9 | v4 reader が v5 head を fail-closed（#483 契約の維持） |
| T10 | rollback（init default を v4 へ）で新規は v4 に戻り、既存 v5 は読める |
| T11 | 混在 root（v4 session と v5 session が同居）で stats/audit/list/next が正しく動く |
| T12 | D1 distribution / Python 3.9 gate が新規モジュールを覆う |

## 6. 受け入れ条件

- [ ] 新規 init が v5、既存 v1-v4 は v4 のまま
- [ ] 新規 v5 session でフルライフサイクル完走（T3）
- [ ] no dual-write / no per-invocation writer switch（T8）
- [ ] rollback が init default に限定され文書化されている（T10）
- [ ] 安全 gate が同等以上（fenced lease / strict validation / content-addressed evidence / 機械的 pass gate）
- [ ] crash + 同一 operation ID retry が元結果を返す（T5/T6）
- [ ] T1-T12 が Green、フルスイート green

## 7. リスクと対策

| リスク | 対策 |
|---|---|
| **本番挙動の不可逆変更** | rollback を init default 1 箇所に限定。既存 v5 session が rollback 後も読めることを T10 で固定 |
| フルライフサイクルが途中で止まる | T3 を最優先で Red にする。止まったコマンドは範囲に含める |
| genesis 経路が複数できて整合が崩れる | `initialize()` 1 本に固定し、`stage()`/`execute()` の genesis 拒否は維持（T7） |
| 二重 init | operation_id を決定的にし、`session-already-initialized` で拒否（T4/T5） |
| v4 互換の破壊 | T2/T9/T11 で v4 session と混在 root を固定 |
| 差分肥大でレビュー不能 | A1 外コマンドの owner 整理は #543 へ委ねる。範囲を最終報告で明示 |
