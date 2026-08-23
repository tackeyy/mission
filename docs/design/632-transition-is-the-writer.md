# 設計書: 批2-a-3 第一段 — pass / advance を transition 適用経路へ載せる（#632）

> 本設計は Codex Sol high の異系統レビューを 3 巡反映した第 3 版（2026-08-24・最終判定 GO）。
> 初版の「`mark_pass` の `passes` / `loop_active` 代入を削除する」方針は **High 指摘により撤回**した
> （理由は「スコープ外」節を参照）。2 巡目で advance の例外 deferral を全 `Exception` へ拡大し、
> 3 巡目でテスト 5 件を追加した。

## 前提条件（Red 開始条件）

- **#631（PR #642）が merge 済みで、その head を含む最新 `main` から worktree を作ること。**
  本設計は `transition_control_claim_bounds`（kernel）と claims 適用版 `_apply_transition_claims`
  （persistence）に依存する。両者は #631 で初めて導入される。

## 目的

ADR-006 批 2-a-3 のうち、**kernel transition を「書き手」とする構造を pass と advance へ広げる**。
批 2-a-1（#630・claims 検証）→ 批 2-a-2（#631・claims 適用）で完成した仕組みを、まだ
`repository.execute` を通っていない `mark_pass` と、decide が事後照合になっている `advance` に適用する。

## スコープ

### やること

1. **`mark_pass` を `repository.execute(state, mutate, decision.transition)` 経由にする**（`review.py`）
   - 現状は `repository.execute` を一切通らず、`copy.deepcopy(data)` に直接書いて `repository.save` する。
     kernel command でありながら transition の適用も検証も効かない唯一の経路であり、これを閉じる
   - decide は既に実 state（`_kernel_state_for_pass`）で行われているため、**decision の作り方は変更しない**
   - `with repository.transaction():` 内の評価順序（force approval 検証 → artifact gate → score evidence →
     specialist gate → decide）を変更しない
   - **mutate closure の中身は現状の代入をすべてそのまま維持する**（下記「やらないこと」1 を参照）。
     本 PR の変更は「`copy.deepcopy(data)` + 直接書き込み」を「`repository.execute(state, mutate, transition)`」
     に置き換えることに限る

2. **`advance` の decide を execute より前に計算し、accept 時のみ transition を送付する**（`lifecycle.py`）
   - **例外の発生順序を現状と 1 つも変えない**ため、以下の deferral パターンで実装する:

     ```python
     candidate = None
     deferred_error = None
     if is_phase_change:
         try:
             candidate = _advance_decision(state, request, prepared_handoff)
         except Exception as error:          # noqa: BLE001 - 送出順序の保存が目的
             # LifecycleFailure だけでなく、_typed_state の MissionStateDecodeError や
             # Phase(request.phase) の ValueError も従来は execute の後に送出されていた。
             # 例外型を絞ると順序が変わるため、ここでは全 Exception を遅延する
             deferred_error = error
     proposed = repository.execute(
         state,
         mutate,
         candidate.transition if (candidate is not None and candidate.accepted) else None,
     )
     if is_phase_change:
         if deferred_error is not None:
             raise deferred_error
         # 既存の if candidate.accepted / elif ... raise ブロックをそのまま維持
     ```

   - これにより「mutate 内の artifact 検証エラー」が従来どおり先に送出され、handoff decode エラー・
     typed decode エラー・不正 phase 値の `ValueError` もすべて従来位置で送出される。**`is_phase_change` が False の場合は従来どおり decide を呼ばず、
     transition も送らない**
   - rejection を意図的に無視して legacy mutation を保存する既存経路（旧 policy の planning→executing、
     skip-ahead、reviewing→scoring 等）も**そのまま維持する**。それらの経路では `decision is None` /
     execute への transition 引数 `None` / 保存 bytes 不変を新規テストで固定する

3. **property テストと互換テストの追加**（新規テストファイル）

### やらないこと（本 PR のスコープ外・後続で扱う）

1. **`mark_pass` の dict 代入（`passes` / `loop_active`）の削除**
   - **理由（Sol High 指摘）**: `execute` は mutation を完了した**後**に claims を適用する。一方
     `services.write_terminal_outcome(proposed)` は mutation 内で走り、`derive_terminal_outcome` は
     `passes` / `loop_active` から outcome を導出する。代入を削除すると、outcome 導出時点では旧値のままで
     `None` が返り、`ValueError("terminal transition did not produce a terminal outcome")` で
     **保存前に落ちる**。force 経路の `validate_force_terminal`（承認 digest の突合）も同じ理由で不一致になる
   - 削除には「claims 適用後に terminal outcome 生成と force digest 検証を行う post-claims finalizer」の
     導入が必要であり、これは `derive_terminal_outcome` と kernel `_halt_outcome` の一本化
     （terminal_outcome の claims 化）と同時に設計すべき。**次段の独立 PR とする**
   - 本 PR では代入を残すことで、claims 適用は「writer が書いた決定後の値と一致する」経路を通り、
     等価性が機械検証される（#631 の `_apply_transition_claims` の許容 2 状態のうち片方）

2. **`mutation` callback の最終削除** — claims 外の互換書き込み（timing / activity / handoff / artifact /
   `passes_forced` / raw halt_reason）が残るため時期尚早（Sol Low 指摘も同旨）

3. **`derive_terminal_outcome` と kernel `_halt_outcome` の一本化**、`_ISSUED_TRANSITIONS` 廃止（#622）

4. **批 1 追加 command の置換** — #618〜#620 は既に「decide 先行 gate + transition 送付」で実装済みのため
   置換対象は存在しない（この事実を PR 本文に記録する）

5. **advance の複合不正入力（artifact 不正 ＋ kernel 不正 の同時発生）における reason 優先順位** —
   上記 deferral パターンにより現状と同一になる想定だが、**契約としては規定しない**。単一不正条件の
   reason / message は従来どおり不変であることをテストで固定する

> **本 PR は #632 を完了扱いにしない。** 残る完了条件（上記 1〜4）は PR 本文に列挙し、#632 は open のまま
> 次段へ引き継ぐ。

## 変更対象ファイル

| ファイル | 変更内容 |
|---|---|
| `skills/mission/lib/mission_application/review.py` | `mark_pass` を `repository.execute` 経由化（mutate 内容は不変） |
| `skills/mission/lib/mission_application/lifecycle.py` | `advance` の decide 前倒し（deferral パターン）と transition 送付 |
| `skills/mission/tests/test_issue632_transition_is_the_writer.py` | 新規（下記テストリスト） |
| `plugins/mission/skills/mission/lib/mission_application/review.py` | ミラー（byte-identical） |
| `plugins/mission/skills/mission/lib/mission_application/lifecycle.py` | ミラー（byte-identical） |

## インターフェース定義

**変更しない。** `mark_pass` / `advance` の引数・戻り値 dataclass、`MarkPassServices` / `AdvanceServices` の
構成、CLI の出力 JSON・exit code はすべて現状のまま。

`repository.execute(state, mutation, transition=None)` の既存契約（#630 / #631）を利用する:
- claims は mutation 完了後に persistence 層が適用する
- writer が claimed field に「決定後の値」または「未接触（決定前の値）」以外を書いたら
  `FencedCommitError("transition-divergence")`

## 受け入れ条件（検証可能）

1. `mark_pass` の永続化が `repository.execute` を経由し、第 3 引数へ `decision.transition` が渡る
2. `mark_pass` 成功後に保存される document の `passes` / `loop_active` / `phase` が
   `True` / `False` / `"done"` であり、**変更前と byte 単位で同一**（golden 比較または全 key 比較）
3. **force pass（`--force`）経路が成功し、`force_approval.consumed` / `validate_force_terminal` の
   digest 突合が従来どおり成立する**
4. `advance` は decide が accept したときのみ transition を execute へ送付する。
   **decide を呼ばない経路（同一 phase）と、rejection を無視して成功する既存経路（旧 policy
   planning→executing / skip-ahead planning→reviewing / reviewing→scoring）で、
   `decision is None`・transition 引数 `None`・保存 bytes 不変が維持される**
5. 単一不正条件における `ReviewFailure.reason` / `LifecycleFailure.reason` とメッセージが従来どおり
6. **V5 経路**: 実 `V5CompatibilityRepository` で mark_pass を通し、commit された head が claims と一致し、
   `save(..., aggregate_action="remove")` が維持され、remove callback が commit 後に 1 回だけ呼ばれること。
   mutation / claims 失敗時は commit も aggregate 更新も起きないこと
7. **V4 経路**: 保存後に aggregate remove が行われる既存順序が維持されること
8. 既存テストが全て green（特に `test_review_usecases.py` / `test_issue237_advance.py` /
   `test_lifecycle_usecases.py` / `test_issue501_k2_parity.py` / `test_issue542_c1_core.py`）
9. `skills/mission/**` と `plugins/mission/skills/mission/**` が byte-identical（`cmp` で確認）
10. **pass gate 意味論（threshold / open_high / findings evidence / agreement / halt）が不変**。
    `_mark_pass` reducer と `mark_pass` の validate 系サービス呼び出し順を変更しない

## テストリスト（TDD: この順で Red → Green）

新規ファイル `skills/mission/tests/test_issue632_transition_is_the_writer.py`:

1. `test_mark_pass_persists_through_repository_execute` — 記録 repository で `execute` が呼ばれ、
   第 3 引数が `result.decision.transition` と同一オブジェクトであること
2. `test_mark_pass_saved_document_is_unchanged` — 変更前後で保存 document の全 key/value が一致すること
   （`passes=True` / `loop_active=False` / `phase="done"` / `terminal_outcome="completed_pass"` を含む）
3. `test_mark_pass_force_path_preserves_approval_binding` — force pass が成功し、
   `force_approval["consumed"] is True`、`validate_force_terminal` が呼ばれて例外を出さないこと
4. `test_mark_pass_gate_rejections_are_unchanged` — 以下の各ケースで従来と同じ `ReviewFailure.reason` と
   メッセージが返り、**保存が一切行われない**こと:
   `score-required` / `terminal-state` / `authoritative-score-required` / `invalid-open-high` /
   `open-high-findings` / `composite-below-threshold` / `minimum-item-below-threshold` /
   `review-agreement-too-low` / `artifact-gate-unsatisfied` / force approval 検証失敗
   （**実物の `_write_terminal_outcome` / `derive_terminal_outcome` を使うこと**。stub では High 級の
   順序問題を検出できない）
5. `test_advance_sends_the_accepted_transition_to_execute` — kernel が accept する 2 経路
   （policy v1 の planning→executing、および executing→reviewing）で transition が送付されること
6. `test_advance_compatibility_success_paths_send_no_transition` — 同一 phase / 旧 policy の
   planning→executing / skip-ahead planning→reviewing / reviewing→scoring で、成功・`decision is None`・
   execute の transition 引数が `None`・保存 bytes 不変であること
7. `test_advance_rejection_paths_are_unchanged` — terminal phase 指定 / handoff 不整合 /
   artifact applicability pending 等で `LifecycleFailure.reason` が従来どおりであること
7b. `test_advance_defers_non_lifecycle_decision_errors_until_after_mutation` — decision 計算が
   `LifecycleFailure` 以外の例外（typed decode 失敗 / 不正 phase 値）を投げるケースで、
   mutate 内の例外が競合するときは **mutate 側が先に送出される**こと（deferral の実効性）
7c. `test_mark_pass_validate_services_are_called_in_the_recorded_order` — validate 系サービス
   （force approval → artifact gate → score evidence → specialist gate）の呼び出し順を spy で記録し、
   従来順序と一致すること
8. `test_mark_pass_on_v4_repository_removes_from_aggregate_after_save` — V4 経路で保存後に
   aggregate remove が 1 回呼ばれる既存順序が維持されること
8b. `test_mark_pass_on_v5_repository_commits_claims_and_aggregate_once` — 実 `V5CompatibilityRepository`
   で commit された head が claims と一致し、aggregate remove が commit 後 1 回だけ呼ばれること。
   claims 違反時は commit も aggregate 更新も起きないこと
9. `test_saved_document_matches_decided_claims_for_every_transition_path` — property テスト。
   mark-pass / mark-halt（実 state）/ advance / reactivate / resume-stale / **supersede-reviews** /
   **permission-preflight** / **set_fields（SetExtensionFields）** の各経路について、`transition_control_claims(decision.transition)` の
   全 field が保存 document と一致すること

## 実装上の注意

- `mark_pass` の変更は最小に保つ。`proposed = copy.deepcopy(data)` 以降の代入群を `mutate(proposed)`
  closure へ移し、`proposed = repository.execute(data, mutate, decision.transition)` に置き換えるだけ。
  **closure 内の順序を並べ替えない**（`validate_force_terminal` の呼び出し位置も含む）
- `repository.save(proposed, aggregate_action="remove")` の呼び出しは現状のまま維持する
- `advance` は上記 deferral パターンを厳密に守る。`is_phase_change` が False のときは
  `candidate` を計算しない
- Python 3.9 互換で書く（この repo は 3.9 構文ゲートがある）
- `git commit` は実行しない（コミットは CC 側が分割案に沿って作成する）
