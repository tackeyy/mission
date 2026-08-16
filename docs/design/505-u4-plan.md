# U4（#505）実装計画 — 参照安全な generation garbage collection

## 1. 目的とスコープ

ADR-005 §7 の mark / quarantine / purge 三段プロトコルで `collect(policy) -> GCReport` を実装する。dry-run を既定とし、破壊操作は explicit flag を必須にする。**参照されている generation を絶対に消さないこと**が本体。

やらないこと:

- production v5 routing（P1 まで禁止）
- operation tombstone のコンパクション（別設計）
- `Finding.status=resolved` の導入
- v4→v5 の物理マイグレーション
- worktree archive の GC（別集約）
- `objects/` の孤立オブジェクト削除（下記 3.5 で U4 非スコープと決定）

## 2. 前提（実装開始の gate）

この worktree の base は K2 までで、**U3（crash recovery, PR #527）が入っていない**。U3 が提供する `RecoveryReport` の型と open journal からの generation 参照方法に U4 は依存するため、**U3 merge → rebase → 型確認**を終えるまで exact contract を確定しない。

## 3. 変更対象ファイル

| ファイル | 種別 |
|---|---|
| `skills/mission/lib/mission_persistence/gc.py` | 新規（`RetentionPolicy` / `GCReport` / `collect()`） |
| `skills/mission/lib/mission_persistence/fenced_commit.py` | `collect()` の公開 |
| `skills/mission/tests/test_issue505_gc.py` | 新規 |
| `plugins/mission/skills/mission/lib/mission_persistence/gc.py` | ミラー（D1 の再帰 inventory が自動検出） |

## 4. exact contract として確定する項目

### 4.1 retention root

| root | 参照元 | 取得方法 |
|---|---|---|
| current session head | `sessions/<sid>.json` の `state_generation` | 全 session を strict read |
| prior safety generation | current head の 1 世代前 | commit record の `base.generation` を辿る |
| archive pointer | worktree archive の manifest | 読み取り API を確定（別集約） |
| open recovery record | `transactions/prepared/<txid>.json` | U3 の `RecoveryReport` から取得（**U3 merge 後に確定**） |

### 4.2 retention policy 定数

| 定数 | 導出方針 | 暫定 |
|---|---|---|
| `GRACE_SECONDS` | 実 commit コーパスの最大 commit 間隔の 10 倍を下限 | 3600（要計測） |
| `PURGE_GRACE_SECONDS` | 同上 | 86400（要計測） |
| `PRIOR_SAFETY_COUNT` | ADR-005 §7「current plus one prior」 | 1 固定 |

値は creation ではなく**観測から導出**し、測定根拠をコードコメントに残す（先例: `502-u1-blob-limits.md`）。

### 4.3 削除候補の判定（AND 条件）

```
candidate := retention root に含まれない
             AND GRACE_SECONDS 経過
             AND strict validated（regular file / link count 1 / symlink でない）
             AND 全 open transaction manifest に不在
             AND StateLock 下で root を再読しても依然 unreferenced
```

非自明なフィルタ条件には**意図コメントを必ず添える**（learning brief）。

### 4.4 スキャン完全性

OSError / permission denied / 想定外エントリは即 fail-closed。途中失敗時は何も削除しない。

### 4.5 削除順序と objects の扱い

1. quarantine: `generations/<hex>.json` → `transactions/quarantine/<hex>.json` へ atomic rename（StateLock 下で root 再読の後）
2. purge: quarantine 内で `PURGE_GRACE_SECONDS` 経過かつ bytes 不変を確認してから unlink

**`objects/` は U4 で削除しない。** generation manifest の purge のみを対象とし、孤立オブジェクトの回収は別設計とする。

### 4.6 operation tombstone

`operations/` は GC root でも GC 対象でもない（ADR-005 §11 の恒久 idempotency authority）。**`collect()` は `operations/` を traverse しない。** これにより #528 の O(N) スキャン問題に GC が干渉しない。

## 5. 実装ステップ

```
1. U3 merge → rebase → RecoveryReport の型確認
2. exact contract 確定（interface と定数値）
3. TDD Red（全テストを先に書いて落ちることを確認）
4. mark / quarantine 実装
5. purge 実装
6. fenced_commit への collect() 統合（dry-run 既定）
7. plugins ミラー同期と Python 3.9 gate
```

## 6. TDD Red 一覧

### 参照保護（本体）

| # | 検証 |
|---|---|
| R1 | 最新 generation は dry-run でも候補にならない |
| R2 | 1 世代前（prior safety）も候補にならない |
| R3 | archive pointer が指す generation は候補にならない |
| R4 | open recovery record が参照する generation は候補にならない |
| R5 | multi-session で全 session の head が保護される |
| R6 | `operations/` は collect 後も byte-identical |

### 回収

| # | 検証 |
|---|---|
| C1 | grace 経過した unreferenced generation が quarantine される |
| C2 | C1 後に同一 operation ID を retry すると tombstone から結果が返る |
| C3 | 同一 operation ID + 異なる intent は `operation-intent-collision` |
| C4 | grace 未経過は quarantine されない |
| C5 | quarantine 後に bytes が変わったものは purge されない |

### fail-closed

| # | 検証 |
|---|---|
| S1 | scan と quarantine の間に head が動いたら削除されない |
| S2 | symlink があると collect 全体が abort |
| S3 | digest 不一致で abort |
| S4 | スキャン中の OSError で abort |
| S5 | archive pointer が読めないと fail-closed |
| S6 | quarantine 中断後の再実行が冪等 |
| S7 | purge 中断後の再実行が冪等 |

### dry-run

| # | 検証 |
|---|---|
| D1 | 既定は dry-run でファイルが消えない |
| D2 | explicit flag なしに破壊操作が拒否される |
| D3 | 破壊モードで quarantine した generation ID が報告される |

### 不変性・境界値

| # | 検証 |
|---|---|
| M1 | collect 後も MissionState が byte-identical |
| M2 | U3 の recovery スイートを GC interleaving ありで全 pass |
| B1 | `grace-1`（保持）と `grace+1`（候補）の偶数ペア |
| B2 | `purge_grace-1`（保持）と `purge_grace+1`（削除）の偶数ペア |
| B3 | 1 世代前（保持）と 2 世代前（候補）の偶数ペア |

## 7. 受け入れ条件

- [ ] collect 前後で MissionState が byte-identical（M1）
- [ ] 破壊モードは exact generation ID を報告し explicit flag 必須（D2 / D3）
- [ ] U3 の recovery スイートが GC interleaving で全 pass（M2）
- [ ] D1 の再帰 distribution gate と Python 3.9 gate が `gc.py` を自動検出（A4）
- [ ] tombstone が collect 後も byte-identical（R6）
- [ ] operation ID retry が tombstone から正確な結果を返す（C2 / C3）
- [ ] 参照されている generation は dry-run を含め物理削除されない（R1-R5）
- [ ] symlink / digest 不一致 / 不完全スキャン / 曖昧 root で fail-closed（S2-S5）

## 8. リスクと対策

| リスク | 対策 |
|---|---|
| U3 未 merge で `RecoveryReport` 型が未確定 | Step 1 完了まで contract を固定しない。interface は型のみ先行定義 |
| root 判定漏れで参照中のものを消す | R1-R5 に加え、archive pointer が読めない場合・open recovery の gen ref が nil の場合という **fail-open しうる経路**を adversarial に検証 |
| scan と quarantine の間の head 移動 | StateLock 下で root を再読してから rename。S1 で fault injection 検証 |
| tombstone の肥大化（#528） | `collect()` は `operations/` を traverse しない。S6 で byte-identical を assert |
| quarantine ディレクトリが既存 layout に無い | `_ensure_layout()` に追加。U3 の layout 変更と競合しないか確認 |
| 中断した purge が変化済みエントリを削除 | purge 前に bytes を再読して差分を検出、unchanged 以外は skip して GCReport に記録 |
