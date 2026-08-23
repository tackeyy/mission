# 設計書: 批2-a-3 第二段 — post-claims finalizer と terminal outcome の一本化（#632）

> 第 7 版（2026-08-24・**最終判定 GO**）。Codex Sol high の異系統レビュー 7 巡を反映した。
> 1 巡目 NO-GO（High 4）→ 2 巡目 NO-GO（High 2 / Medium 3）→ 3 巡目 NO-GO（新規 High 1 / Medium 1）。
> 3 巡目で H1 / H2 / M2 / M3 / L2、4 巡目で permission 戻り値 authority と `_SetFieldsPlan` 契約が解消。
> 5 版で synthetic before の乖離（4 巡目 High）と Low 3 件を解消。
> 6 版で 5 巡目 High（gate-only と削除の矛盾）を解消。7 版では **削除可否を branch 単位から
> field 単位の claim 判定へ**（6 巡目 Medium）改め、`phase` writer を timing 理由で残すことと
> `halt_category` の同値ケースを明記した。
> 3 版では claims lifecycle の所有・消費規則（2 巡目 H1）、第三段の authority 設計（同 H2）、
> goal-route の単回評価（同 M1）、Issue / PR 粒度（同 M2）、role matrix（同 M3）、行番号（L2）を修正した。
> 前提は main `ba5a87c`（第一段 PR #643 merge 済み）。

## #632 の残条件・Issue 分割・本 PR の位置づけ（1 巡目 H1 / 2 巡目 H1・M2）

ADR-006 は Batch 2 の完了状態を「`transition.new_state` を保存し mutation callbacks を除去する」と規定し
（`docs/adr/006-kernel-reducer-adjudication.md:92`）、child issue ごとに 1 PR とする（同 `:85`）。
第一段（PR #643）は設計レビューの High 指摘で当初計画より縮小して merge されたため、
**#632 は既に「1 child issue = 1 PR」から外れている**。ここで粒度を戻す。

| 残条件 | 扱い |
|---|---|
| post-claims finalizer の導入と `mark_pass` / `mark_halt` の dict 代入削除 | **本 PR（#632 を close）** |
| `derive_terminal_outcome` と kernel `_halt_outcome` の一本化 | **本 PR** |
| `cmd_permission_preflight` 経路の `RuntimeError` 構造化 | **本 PR** |
| `mutation` callback の最終削除 | **新規 child issue「批2-a-4」へ移管**（Batch 2 内・#621 / #614 にぶら下げる） |

- 本 PR は #632 の完了条件のうち上 3 件を満たし、**4 件目を新 child issue へ明示移管**して #632 を close する
  （移管理由・移管先・ADR 条件が生きていることを #632 / #621 / #614 に記録する）
- Batch 3 へは送らない。批2-a-4 は Batch 2 内の直後 PR として実施する
- 批2-a-4 の技術方針は末尾「批2-a-4 の設計方針」に記す（2 巡目 H2 への回答）

## 実測（一次確認・main `ba5a87c`）

### transition を送付している **8 経路**（第一段の property テストが列挙する集合と一致）

| # | 経路 | 送付箇所 | terminal_outcome の claim | compat writer の terminal_outcome 書き込み |
|---|---|---|---|---|
| 1 | mark-pass | `review.py:514` | None→`completed_pass` | `write_terminal_outcome` |
| 2 | advance | `lifecycle.py:532` | 変更なし | なし |
| 3 | mark-halt（実 state かつ set_terminal_phase） | `lifecycle.py:664` | None→outcome | `derive_terminal_outcome` |
| 4 | reactivate | `lifecycle.py:815` | 値→None | `pop` |
| 5 | resume-stale（refresh-pid） | `lifecycle.py:918` | 値→None（synthetic before） | `pop` |
| 6 | set（SetExtensionFields） | `lifecycle.py:1260` | 変更なし | goal-route 経路のみ derive（後述 H3） |
| 7 | permission observation | `runtime_guard.py:445` | None→`blocked_external` | 固定文字列 |
| 8 | supersede-reviews | `bin/mission-state.py:16158` | None→`stale_superseded` | `_write_terminal_outcome` |

### `derive_terminal_outcome`（`mission_common.py:74-129`）と kernel `_halt_outcome`（`transitions.py:205`）の差分

kernel は `(category, session_role)` の純写像。legacy 側は加えて:

- **(A)** `str(resolution_status).strip().lower() == "superseded"`、または
  `str(halt_reason).strip().lower()` が `"superseded by a replacement run"` /
  `"superseded by replacement run"` → **category に関わらず** `stale_superseded`
- **(B)** `category not in HALT_CATEGORIES` かつ reason が `orphan:` / `stale:` 始まり → `stale_superseded`
- **(C)** `halt_category` が str でない → `failed`
- **(D)** 明示 `terminal_outcome` が derived と不一致 → `failed`

transition 送付経路では **(B)(C) は到達不能**（`HaltCategory` 型を通った有効値のみ）、**(D)** は mutate が `pop` 済み。
**(A) が唯一の実差分**であり、`resolution_status` は `MissionControl` にも
`_mark_halt_decision_state` の synthetic view にも存在しないため kernel からは見えない。

実測した反例（現行コード）:

```
resolution_status="superseded", category="blocked-external"
  kernel  = blocked_external
  legacy  = stale_superseded
```

`resolution_status` は generic set の frozen / dedicated field ではない（`commands.py:67`）ため、
**active document に set で設定できる = 実到達可能**。

### `RuntimeError` の所在

`runtime_guard.py:430` の `raise RuntimeError("permission-halt-rejected: " + code)`。
`_record_permission_probe_observation`（`mission-state.py:10873`）は `(OSError, ValueError)` しか捕捉せず、
`_permission_preflight` を貫通して **traceback で終了する**。
`_permission_preflight` は `cmd_permission_preflight`（`:10910`）だけでなく **`cmd_init`（`:8401`）からも**
呼ばれる（L1）。

## 設計

### 1. terminal outcome 導出の一本化（H3 / M1）

`mission_common` に 2 つの純関数を置き、legacy 入口と kernel の双方がそれを呼ぶ。

```python
def is_supersede_marked(resolution_status: object, halt_reason: object) -> bool:
    """(A) の先行規則。現行の str()+strip()+lower() をそのまま移植する。"""
    status = str(resolution_status or "").strip().lower()
    reason = str(halt_reason or "").strip().lower()
    return status == "superseded" or reason in _SUPERSEDE_REASONS

def terminal_outcome_for_halt(category: str, session_role: str, *, superseded: bool) -> str:
    """halt の terminal outcome を決める唯一の写像。"""
```

- `_derive_control_terminal_outcome` は (B)(C)(D) の判定を残したまま、(A) と category 写像を
  上記 2 関数へ委譲する。**legacy 入口の挙動は 1 bit も変えない**（M1: 大小文字・前後空白を含めて同値）
- kernel `_mark_halt` は `_halt_outcome` を廃し、
  `terminal_outcome_for_halt(command.category.value, control.session_role.value, superseded=command.superseded)`
  を `TerminalOutcome` へ写す
- `MarkHalt` へ **`superseded: bool = False`** を追加。`_mark_halt` の先頭で
  `if type(command.superseded) is not bool: raise _Rejected("invalid-supersede-marker")`（M3。
  `MarkPass.force` の既存 guard と同形）

**`MarkHalt` の全構築点で同一 helper から値を与える**（H3）:

| 構築点 | superseded の入力 |
|---|---|
| `mark_halt`（`lifecycle.py:586`） | `is_supersede_marked(state.get("resolution_status"), request.reason)` |
| `monotonic_halt_decision`（`lifecycle.py:60` → `:77`） | `is_supersede_marked(raw_state.get("resolution_status"), reason)` |
| `route_simple_to_goal`（`lifecycle.py:1130`） | `is_supersede_marked(proposed.get("resolution_status"), <固定 reason>)` |

`monotonic_halt_decision` は synthetic view を作るが、**superseded は raw_state から読む**
（synthetic view は decode 安全性のための最小化であり、outcome 判定の入力を落としてよい理由はない）。

### 2. `terminal_outcome` を claimable にする（H2 / H3 / M2）

`_CLAIMABLE_CONTROL_FIELDS` に `"terminal_outcome"` を追加する。8 経路すべてで
「compat writer の値 == claim 値」または「compat writer が書かない」を新規テストで固定する。

**goal-route（経路 6）の扱い（1 巡目 H3 / 2 巡目 M1 / 3 巡目 Medium）**: 現状は `MarkHalt` を
gate にしか使わず、保存時には `SetExtensionFields` の transition を渡すため `terminal_outcome` が
claims の外に落ちる。これを **批3-c へ送らず本 PR で閉じる**。

`SetFieldsServices` は任意の Callable を注入する契約（`lifecycle.py:264`）なので、**同じ計算を
2 回走らせない**。ただし「service ごとに 1 回」という契約は現行挙動と両立しない — 現行は
`request.kvs` を順に処理し、`review_tier` が重複すればその回数だけ `derive_review_tier` を呼ぶ
（`lifecycle.py:1180-1219`）。したがって契約は次のとおりとする。

> **plan の生成は 1 回だけ。`mutate` は service を一切呼ばない。**
> service の呼び出し列（回数・順序・引数）は現行と **完全に同一**であり、重複 key の挙動も変えない。

```python
@dataclass(frozen=True)
class _SetFieldsPlan:
    document: dict                      # shadow へ現行 mutate 全体を適用した結果
    warnings: tuple[str, ...]
    route: Optional[_GoalRoutePlan]     # routed でなければ None（decision / dispatch / verdict を含む）
```

- `_plan_set_fields(state, request, services)` が `copy.deepcopy(state)` の shadow に対して
  **現行 mutate の本体をそのまま**実行する（kv ループ・`review_tier` 検証・`derive_review_tier*`・
  reviewer_count 導出・`route_simple_to_goal`・`ensure_phase_timing`・`updated_at` まで含む）。
  `ensure_phase_timing` も shadow 側で呼ぶ（3 巡目 Medium の指摘どおり mutate に残さない）
- `mutate(proposed)` は `proposed.clear(); proposed.update(plan.document)` のみ。**例外を出さない**
  （`refresh_pid` の mutate が既に同じ idiom を使っている — `lifecycle.py:895`）
- routed のとき `execute` へ渡す transition は **goal-route の `MarkHalt` transition**。
  `SetExtensionFields` の transition は control 差分ゼロであることをテストで固定し、
  置換で失われる claims が無いことを示す
- **例外送出順序**: kv 形式エラー・`review-tier-invalid`・service 例外はいずれも
  execute の**前**へ移るが、execute は mutation 前に I/O を行わないため観測可能な差は無い。
  service 呼び出し列と warning tuple と `SetFieldsResult` の完全一致を、
  **重複 key / `complexity`・`review_tier` の順序違い**を含めてテストで固定する
- `aggregate_action`（routed → `"remove"`）と `administrative=True` は現状のまま維持する

**M2（absence と明示 None の区別）**: `after=None` の claim は
**「field が存在しないこと」** として扱う。`_apply_transition_claims` の適用は現状どおり `pop` だが、
**再検証（下記 3）では `field not in document` を要求する**（`document[field] is None` は違反）。
legacy 導出では両者の意味が異なる（absent → active、明示 None → `failed`）ため。

### 2b. synthetic decision state 経路の扱い（4 巡目 High）

`_mark_halt_decision_state` は raw document にかかわらず
`phase=<active>` / `loop_active=True` / `passes=False` / `halt_reason=""` /
`session_role="implementer"` / `terminal_outcome` 不在 の synthetic view を作る（`lifecycle.py:344`）。
claims の `before` はこの **synthetic 値**であり、実 document の値とは限らない。

現状これが露見しないのは、compat writer が claimed field に必ず **after 値**を書いているためである。
本 PR で代入を削除すると、`_matches(current, before)` 側の許容が効かず、
**既に terminal な document で `transition-divergence` になる**（例: `terminal_outcome="completed_pass"` の
document に permission failure → before `None` / after `blocked_external` のどちらとも不一致）。
`_permission_preflight` に active-state 制限は無く（`mission-state.py:10769`）、到達可能な正規入力である。

**裁定**: synthetic view を claims の根拠に使わない。批2-a-2（#631）が `mark_halt` に導入した
「**decode でき active なら実 state で decide、そうでなければ synthetic + gate-only（transition 非送付）**」
構造を、synthetic を使う残り 2 経路へも広げる。

| 経路 | 変更 |
|---|---|
| permission observation（`runtime_guard.py:445`） | 実 state が decode でき active（`mark_halt` と同一述語）なら実 state で decide し transition を送付。そうでなければ synthetic + gate-only（compat writer が従来どおり書く＝**同一入力に対する現行 main の保存結果と全 key/value 一致（JSON の key 順も含む）**） |
| supersede-reviews（`mission-state.py:16158`） | 同上（`role == "superseded"` の terminalization のみ対象） |

- 述語は `mark_halt` の実 state 判定を共通ヘルパー
  `real_terminalizable_state(document) -> MissionState | None` として切り出し、3 経路で共有する
  （phase が done/halted でない・`passes is False`・`halt_reason` 空・`terminal_outcome is None`）
- 実 state を使うことで `session_role` も実値になる。対象 category（`stale` / `blocked-external`）は
  role 非依存のため導出値は変わらない（45 組テストで固定）
- **既に terminal / 復号不能な document に対する保存結果は現行 main と byte-identical**
  （gate-only 経路で従来の compat 書き込みが残る。「入力 document が不変」という意味ではない）。
  「完了済み mission に permission failure を上書きすると復号上 `failed` になる」という
  現行の自己矛盾は本 PR のスコープ外とし、別 Issue として起票する

### 3. post-claims finalizer と claims の lifecycle（1 巡目 H4 / 2 巡目 H1）

`execute` を拡張する:

```python
def execute(self, state, mutation=None, transition=None, finalize=None): ...
```

順序は **deepcopy → mutation → claims 適用 → finalize → claims 再検証**。

- `finalize` は `transition is None` のとき指定不可（`FencedCommitError("request-invalid", ...)`）
- 再検証は「claimed field が claim 値と一致（`after=None` は **field 不在**）」のみを見る。逸脱は
  `FencedCommitError("transition-divergence", "finalizer diverges ... on <field>")`

**これだけでは不足する**。V5 の `save` は `self._prepare_state(copy.deepcopy(state))` を適用してから
bytes 化する（`legacy_v4.py:532`）ため、再検証後・serialize 前に claimed field を書き換えられる。
よって claims を「**transaction × execute が返した特定 document**」へ束縛し、保存直前に再検証する。

#### 所有・消費規則（fail-closed）

```python
@dataclass
class _PendingDecision:
    document: dict          # execute が返した dict そのもの（identity で照合）
    claims: dict            # {field: expected}  expected は値または _CLAIM_ABSENT
```

| 事象 | 規則 |
|---|---|
| `execute(..., transition=T)` 成功 | 再検証成功後に `_pending.append(record)`。**例外時は append しない** |
| `execute(..., transition=None)` | 何もしない（pending は変更しない） |
| `save(document)`・`_pending` が空 | 従来どおり（typed request 経路 / `execute_effects` 経路） |
| `save(document)`・`_pending` が非空 | `record.document is document` の record を探す。**無ければ** `transition-divergence`（"save target is not a decided document"）で fail-closed |
| 上記で record が見つかった | **最終 document**（V5 は `_prepare_state` 適用後・`json.dumps` 直前 / V4 は `_write_state` 直前）で再検証。**record は破棄しない**（同一 document の 2 回目 save も検証される） |
| V5 の replay 早期 return | **検証を早期 return より前**に行う（replay でも未検証 save を作らない） |
| `transaction()` の終了（正常・例外いずれも） | `finally` で `_pending` を**必ず空にする**（transaction 跨ぎの漏れを断つ） |

- record を list で持つのは、1 transaction 内で `execute` が複数回呼ばれても
  「保存対象 document と claims の対応」を失わないため。対応の無い save は fail-closed
- `_pending` は repository instance のフィールドとして持ち、`transaction()` の
  `finally` で破棄する（V5 が既に `_admitted` / `_replayed` を破棄している場所と同じ）

### 4. dict 代入の削除 — **field 単位の claim 判定**（5・6 巡目 High/Medium）

claim は「decision の before と after が異なる field」だけに生じる（`transitions.py:591`）。
したがって **branch 単位の `claims_applied: bool` では不十分**であり、削除可否は
**その decision で実際に claim された field かどうか**で決める。

```python
claimed = (
    set(transition_control_claim_bounds(decision.transition))
    if <この経路で transition を送る条件> else set()
)

def mutate(proposed: dict) -> None:
    ...
    if "halt_category" not in claimed:
        proposed["halt_category"] = request.category      # claim が無いときだけ compat writer が書く
    ...
```

これにより gate-only 分岐（`claimed` が空）でも、claim が生じなかった field（例: 既に同値の
`halt_category`）でも、**同一入力に対する現行 main の保存結果と全 key/value 一致（JSON の key 順も含む）** になる。

#### 各経路の実 claim 集合（実測）

| 経路 | transition を送る条件 | 実 claim 集合（本 PR 適用後） |
|---|---|---|
| mark_pass | 常に（実 state decide） | `phase` / `passes` / `loop_active` / `terminal_outcome` |
| mark_halt | `real_terminalizable_state(...) is not None and request.set_terminal_phase` | `phase` / `loop_active` / `terminal_outcome` ＋ `halt_category`（**既存が同値なら生じない**） |
| permission observation | `real_terminalizable_state(...) is not None` | `phase` / `loop_active` / `terminal_outcome` ＋ `halt_category`（同上） |
| supersede-reviews（`role == "superseded"`） | 同上 | `phase` / `loop_active` / `terminal_outcome` ＋ `halt_category`（同上） |
| advance / reactivate / resume-stale / set | 現状どおり | 本 PR では writer を削除しない（`terminal_outcome` の claim 検証のみ追加） |

#### 本 PR で削除する直接 writer / 残す writer

| 経路 | 削除する（`claimed` 判定つき） | **残す**（理由つき） |
|---|---|---|
| mark_pass | `passes` / `loop_active` / `write_terminal_outcome` | `services.transition_phase(...)` — **`phase` は claim だが timing（`phase_started_at` / duration）計算のため compat writer を残す**（claim 値と同値を書くので検証が通る）。`passes_forced` / force_* / waiver / early_stop / `updated_at` |
| mark_halt | `loop_active` / `halt_category` / terminal_outcome 導出ブロック | `transition_phase` / `terminalize_without_phase`（timing）/ 生 `halt_reason` / activity / goal_dispatch_* / `updated_at` |
| permission observation | `halt_category` / `loop_active` / `terminal_outcome` | `_closed_permission_transition`（timing）/ 生 `halt_reason` / `updated_at` |
| supersede-reviews | `loop_active` / `halt_category` / `_write_terminal_outcome` | **`passes = False`**（`MarkHalt` は `passes` を変更しないため claim にならない）/ `_transition_phase`（timing）/ 生 `halt_reason` / `updated_at` |

- `mark_pass` の force 経路の `validate_force_terminal` と `force_approval["consumed"] = True` は
  **finalize へ移す**（`terminal_state_digest` は passes / loop_active / terminal_outcome を含む）
- `MarkPassServices.write_terminal_outcome` の **service field は既存 constructor / API 互換のため残す**
  （`mission-state.py:15704 / 16153 / 18498` はグローバル helper `_write_terminal_outcome` の caller であり
  service field の caller ではない）
- 生 `halt_reason` は claims 外のまま（kernel `_reason()` が前後空白・空文字を拒否するため
  raw を渡すと invariant が緩む）。この除外は **恒久**とする

#### 4b. `record_permission_observation` の戻り値（3 巡目 High）

現状 `PermissionObservationResult(..., terminal_outcome="blocked_external")`
（`runtime_guard.py:447-455`）は固定値であり、supersede-marked document では kernel claim と食い違う。

- `PermissionObservationResult.terminal_outcome` / `.halt_category` は
  **保存された document の値**から生成する（claims 適用結果、gate-only 分岐では compat writer の結果）
- `_record_permission_probe_observation`（`mission-state.py:10873`）は bool ではなく
  `(halt_recorded, terminal_outcome)` を返し、`_permission_preflight` の 5 箇所の JSON
  （`:10739 / :10763 / :10787 / :10814 / :10852`）と init fallback（`:1814 / :1838`）は
  **記録できた場合は保存値**、記録できなかった場合は従来どおり `"blocked_external"` を出す。
  `_record_permission_preflight_halt`（`:1789`）の戻り値は **明示 unpack** する
  （`bool((False, None))` が truthy になる罠を避ける。4 巡目 Low）

> **意図した挙動変更（要記録）**: supersede-marked かつ active な document では、現行は
> `terminal_outcome="blocked_external"` を書くが `derive_terminal_outcome` は (A) により
> `stale_superseded` を導出するため、保存 document は明示値と導出値が矛盾し (D) で `failed` に復号される。
> 一本化後は `stale_superseded` が書かれ矛盾が解消する。通常 state は `blocked_external` のまま。

### 5. `RuntimeError` の構造化（L1）

- `runtime_guard` に `class PermissionHaltRejected(RuntimeError)`（`.code` 付き）を追加。
  `RuntimeError` 系を維持するのは `_record_permission_probe_observation` の
  `except (OSError, ValueError): return False` に **吸収させない**ため
- 構造化は **`_permission_preflight` の共有呼び出し面**で行う。`cmd_permission_preflight` と
  `cmd_init` の双方から到達するため、共通ヘルパー
  `_exit_internal_invariant(code, detail)` を導入して両入口に置く
  （`ERROR: internal-invariant: <code>: <detail>` / `sys.exit(2)`。他 command と同形）
- 対象は `PermissionHaltRejected` と
  `FencedCommitError(code in {"transition-divergence", "transition-unsealed"})`

## 受け入れ条件

1. `execute(..., finalize=...)` が claims 適用後に finalize を呼び、finalize が claimed field を
   書き換えたら `transition-divergence` で fail-closed
2. **V5 の `_prepare_state` が claimed field を書き換えたら serialize 前に fail-closed**（1 巡目 H4）
2b. claims の lifecycle が守られる: ①execute した document 以外の save は fail-closed
   ②同一 document の 2 回目 save も検証される ③transaction 終了で pending は必ず破棄される
   ④V5 replay でも検証が先行する ⑤execute / finalize が例外を出したら pending は残らない（2 巡目 H1）
3. `after=None` の claim は保存 document に **field が存在しない**ことを要求する（M2）
4. `mark_pass` の保存 document が変更前と **全 key/value 一致**（force / 非 force / waiver / early-stop）
5. force pass の `validate_force_terminal` が claims 適用後の document で突合し成功する。
   **claims 前の document では digest が不一致になる**ことを検出力として示す
6. `mark_halt` の claim 経路と gate-only 経路の保存 document が変更前と一致
   （**HaltCategory 9 種 × SessionRole 5 種 = 45 組**。M4 / 2 巡目 M3）
7. (A) の supersede 先行規則が **`mark_halt` / `monotonic_halt_decision` / `route_simple_to_goal` の
   3 構築点すべてで** kernel と legacy が一致する
8. **8 経路すべて**で claims == 保存値（property テスト。第一段の 8 経路列挙と同集合）
9. goal-route が `MarkHalt` transition を execute へ送り、`terminal_outcome` が claims で書かれる。
   **service 呼び出し列は現行と完全一致し（plan 生成時のみ・mutate では呼ばない）**、`administrative=True` /
   `aggregate_action="remove"` / 保存 document 全 key 一致が維持される（2 巡目 M1）
10. `cmd_permission_preflight` と `cmd_init` が kernel invariant 違反で traceback を出さず
    `ERROR: internal-invariant: ...` / exit 2 を返す
11. AST ガード: 上表 4 closure について、**「削除する」列の field への書き込みが
    `if "<field>" not in claimed:` ガードの下以外に存在しない**こと。「残す」列の field は対象外
    （closure ごとに許容 field を宣言する）。検出対象は添字代入だけでなく
    **`update` / `setdefault` / `pop` / `del` / `_write_terminal_outcome` 等の間接 writer** も含め、
    各操作が対応するガード配下かを構造的に判定する。合成違反（無条件書き込みを 1 つ足す）で
    検出力を実証する（間接 writer 版の合成違反も 1 件用意する）
12. pass gate 意味論（threshold / open_high / findings evidence / agreement / halt）不変
13. `skills/mission/**` と `plugins/mission/skills/mission/**` が byte-identical
14. full suite green
15. permission observation / supersede-reviews が既に terminal / 復号不能な document では gate-only へ落ち、
    **同一入力に対する現行 main の保存結果と全 key/value 一致（JSON の key 順も含む）** であること（4・5 巡目 High）。
    active かつ `passes` 欠落の document、terminal outcome 全種、復号不能 document を含める
16. **#632 を close する前に child issue「批2-a-4」を実際に起票し**、#621 / #614 へ
    「批2-a-4 merge まで Batch 2 は未完了・Batch 3 は開始不可」を記録してあること（3 巡目の必須ゲート）

## テストリスト（TDD・新規 `skills/mission/tests/test_issue632_post_claims_finalizer.py`）

1. `test_execute_calls_finalize_after_claims`
2. `test_finalize_cannot_overwrite_claimed_fields`（合成違反）
3. `test_finalize_cannot_reintroduce_a_removed_field`（`terminal_outcome=None` 再追加。M2）
4. `test_finalize_requires_a_transition`
5. `test_prepare_state_cannot_change_claimed_fields_before_serialization`（V5・合成違反。H4）
6. `test_execute_without_save_does_not_leak_claims_into_the_next_transaction`
6b. `test_saving_a_document_other_than_the_executed_one_is_rejected`
6c. `test_second_save_of_the_same_document_is_verified_again`
6d. `test_failed_finalize_leaves_no_pending_claims`
6e. `test_v5_replay_save_verifies_before_returning`
7. `test_mark_pass_saved_document_is_unchanged`（golden 全 key 比較 × 4 形）
8. `test_force_validation_runs_against_the_post_claims_document`（検出力: claims 前だと不一致）
9. `test_mark_halt_saved_document_is_unchanged_for_every_category`（9 category × 5 role = 45 組）
10. `test_mark_halt_gate_only_paths_still_write_compatibility_fields`
11. `test_supersede_marker_is_propagated_from_every_markhalt_construction_site`
    （`mark_halt` / `monotonic_halt_decision` / `route_simple_to_goal`）
12. `test_supersede_marker_matches_legacy_string_normalization`（`" Superseded "` / `"SUPERSEDED"`。M1）
13. `test_kernel_and_legacy_derivations_agree_for_every_category_and_role`（9×5 = 45 組の網羅）
14. `test_markhalt_rejects_a_non_bool_supersede_marker`（M3）
15. `test_saved_document_matches_the_decided_claims_on_every_transition_path`（**8 経路** property。
    terminal_outcome が変化しない advance / 非 route set では claim 自体が生じないことも固定する）
16. `test_third_value_injection_is_rejected_on_every_path`（各経路で第三値注入 → divergence）
16b. `test_halt_category_claim_is_absent_when_the_document_already_matches`
    （`halt_category` が ①不在 ②異値 ③**同値** の 3 ケースで、claim 集合と保存 document が
    現行 main と一致すること。mark_halt / permission / supersede の 3 経路）
16c. `test_phase_writer_remains_for_timing_and_agrees_with_the_claim`
    （4 経路をパラメータ化。`phase` に加えて `phase_started_at` / duration / activity closure /
    `resume_target_phase` が現行 main と一致すること）
17. `test_goal_route_sends_its_markhalt_transition`
17b. `test_goal_route_specific_services_are_called_once_per_plan`
    （`goal_dispatch_fields` / `goal_dispatch_guidance` のみ。撤回した「全 service 1 回」契約ではない）
17c. `test_goal_route_preserves_administrative_flag_and_aggregate_action`
17d. `test_set_fields_error_precedence_is_unchanged`（kv 形式 / review-tier-invalid / service 例外）
17e. `test_set_fields_service_call_sequence_is_unchanged_for_duplicate_keys`
    （`review_tier` 重複・`complexity` と `review_tier` の順序違いで、呼び出し列・warning tuple・
    保存 document・`SetFieldsResult` が現行と完全一致）
18. `test_set_extension_fields_transition_claims_no_control_change`（置換で失うものが無いことの根拠）
19. `test_permission_observation_saved_document_is_unchanged`（通常 state）
19b. `test_permission_observation_on_supersede_marked_state_returns_the_persisted_outcome`
    （保存値 == claim == 戻り値 == CLI JSON。現行の自己矛盾 document が解消することも示す）
19c. `test_permission_observation_result_is_derived_from_the_saved_document`
19d. `test_permission_observation_on_a_terminal_document_falls_back_to_gate_only`
    （`completed_pass` / 各 terminal outcome で保存 bytes が変更前と一致し divergence が起きない）
19e. `test_record_permission_preflight_halt_unpacks_the_tuple_explicitly`
    （`(False, None)` を truthy 扱いしない。4 巡目 Low）
20b. `test_supersede_reviews_on_a_terminal_document_falls_back_to_gate_only`
20. `test_supersede_reviews_saved_document_is_unchanged`
21. `test_permission_preflight_and_init_report_internal_invariant_without_traceback`
22. AST ガード（合成違反 fixture つき）

## 批2-a-4 の設計方針（新 child issue・本 PR では実装しない・2 巡目 H2 への回答）

2 巡目 H2 のとおり、**application が state 変更 effect を宣言する方式は採らない**
（ADR-005 では effect の発行主体は kernel であり、既存の `validate_effects` は `EvidenceEffect` 専用、
V5 の stage は `BlobBinding` 以外を拒否する — `fenced_commit.py:2106`）。

代わりに **`transition.new_state` の射影を保存する**（ADR-006 Batch 2 の文言そのもの）。

- 現在 compat writer が書いている互換 field（phase timing / activity segment / 生 halt_reason /
  `passes_forced` / `force_*` / `specialist_waiver` / `early_stop_evaluation` /
  `reactivation_history` / `goal_dispatch_*` / `pid` / `supersedes` / extension kv / `updated_at`）は、
  すべて `MissionState.extensions` または `legacy_passthrough` の射影範囲にある
  （`project_legacy_document` が legacy document を再構成できる）
- したがって批2-a-4 は「**kernel command が互換 payload を typed 入力として受け取り、
  kernel が `new_state` を完成させ、repository は `project_legacy_document(new_state)` を保存する**」
  へ移行する。writer は kernel 側の 1 箇所になり、`mutation` callback は削除できる
- 最大のリスクは **射影が現行 document と byte-identical にならないこと**。批2-a-4 は
  8 経路それぞれで golden document 比較を先に置き、差分が出た field を個別に裁定してから移行する
  （差分ゼロを確認するまで callback を外さない）

## 変更対象ファイル

`mission_common.py` / `mission_kernel/commands.py` / `mission_kernel/transitions.py` /
`mission_persistence/legacy_v4.py` / `mission_application/ports.py` /
`mission_application/review.py` / `mission_application/lifecycle.py` /
`mission_application/runtime_guard.py` / `bin/mission-state.py` ＋ plugins ミラー
