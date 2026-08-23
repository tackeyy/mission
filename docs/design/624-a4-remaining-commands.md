# #624 A4 残り 6 command の実測精査と kernel 移行設計

## 1. 結論

基準は `origin/main` と一致する `14549975f40be5d559dfdfde2741d417deb1ff2d` である。
Issue 本文の静的分類を実装・既存回帰テスト・実 CLI テストから再計測した結果、裁定は次のとおり。
本書は依頼時に示された #633 の確定方針、すなわち「kernel command が typed 入力を受け、
repository は `transition.new_state` の射影を保存し、effect は kernel 発行に限る」を設計制約として採用する。

| 裁定 | command | 理由 |
|---|---|---|
| **昇格** | `executor-handoff begin` | `executor_handoff.status / begun_at` と top-level `updated_at` を session state へ書く |
| **昇格** | `executor-handoff verify-step` | 成功時も top-level `updated_at` を session state へ書き、canonical drift 時は handoff を `rejected` にする場合がある |
| **昇格** | `executor-handoff record-step` | lineage 付き step record を top-level `decisions` へ append し、`updated_at` を書く |
| **昇格** | `executor-handoff complete` | `executor_handoff.status / consumed_at` と `updated_at` を書く |
| **昇格** | `specialists recommend --record-state` | specialist の候補・選択・判断・advisory phase plan・planning binding を session state へ記録する |
| **降格** | `specialists consent` | session state を読まず書かず、user/config の provider-consent aggregate だけを書く |

したがって、**session kernel 対象は 5 command、分離 aggregate へ降格するものは 1 command** である。
`specialists recommend` の flag なしは query として read-only だが、同じ CLI command に明示的な
`--record-state` mutation mode があるため command 全体を対象外にはしない。

6 command はいずれも `phase / passes / loop_active / halt_reason / halt_category /
terminal_outcome / score_history` を書かない。これは read-only を意味しない。handoff 4 command と
state-recording recommend は A4 固有 state を書くため、ADR-006 の「全 session mutation が
`decide()` を通り `transition.new_state` を保存する」対象である
（`docs/adr/006-kernel-reducer-adjudication.md:80-103`）。一方、独立 lifecycle の sidecar を
`MissionState` に強制しない境界は ADR-005 が明記している
（`docs/adr/005-typed-mission-kernel-and-unit-of-work.md:104-108`）。

## 2. スコープと非スコープ

### スコープ

- parser の `set_defaults` から 6 command の実装関数を確定する。
- session state と分離 aggregate の書き込みを区別する。
- handoff の plan / generation / digest / source / selection / iteration / step dependency
  lineage と、現行の許可順序を保つ。
- session writer 5 command を typed command と reducer に移し、repository が
  `transition.new_state` を射影・保存する。
- `specialists consent` に session no-write AST guard を置く。
- source と plugin の production file を byte-identical に保つ。

### 非スコープ

- specialist に review / score / pass/fail / final-report authority を与えること。
- provider の install、dispatch、credential、外部送信を追加すること。
- `verify-step` を durable approval receipt に変更すること。
- 現行より厳しい `begin -> verify-step -> record-step` の必須順序を新設すること。
- #619 で精査済みの別 9 command の再分類。本書は依頼された残り 6 command だけを扱う。

provider は evidence provider に限定され、state・score・pass/fail・final report は `mission` が所有する
（`AGENTS.md:25-29`）。auto-selection は routing の選択であって自動 install / invoke ではなく、
最終 completion gate は core のままである
（`docs/adr/001-specialist-auto-selection.md:23-40`）。

## 3. 実測方法

1. `_build_parser` の各 `set_defaults(func=...)` を起点に handler を確定した
   （`skills/mission/bin/mission-state.py:19056-19085`,
   `skills/mission/bin/mission-state.py:19184-19196`）。
2. handler の repository transaction、dict mutation、save を追った
   （`skills/mission/bin/mission-state.py:4534-4710`,
   `skills/mission/bin/mission-state.py:4721-4738`,
   `skills/mission/bin/mission-state.py:14237-14363`）。
3. handoff の pure decision と plan file 再検証を追い、status / dependency / duplicate / lineage
   guard を確定した
   （`skills/mission/lib/mission_application/planning.py:272-408`,
   `skills/mission/lib/planning_lifecycle.py:66-97`）。
4. v4/v5 real-process、operation replay、stale fence、provider selection の既存テストを実行した。
   対象 4 file は 101 tests が成功した。既存テストが固定する主要挙動は
   `skills/mission/tests/test_issue550_c2_stage_b_batch1.py:139-245`,
   `skills/mission/tests/test_issue550_c2_stage_b_batch1.py:248-322`,
   `skills/mission/tests/test_specialist_selection.py:219-244` にある。
5. source/plugin の現行 production files は `mission-state.py`、planning application、kernel command、
   transition の 4 組で byte-identical である。plugin 配下には tests tree がないため、test は source 側だけに置く。

## 4. 6 command の実測表

### 4.1 session state / sidecar の区別

| command | parser -> 実装関数 | session state に実際に書く field | 完了隣接 7 field | 分離 aggregate / effect | 裁定 |
|---|---|---|---|---|---|
| `executor-handoff begin` | `cmd_executor_handoff_begin` -> `_cmd_executor_handoff(..., "begin")`（`skills/mission/bin/mission-state.py:19184-19187`, `skills/mission/bin/mission-state.py:14366`） | 成功: `executor_handoff.status=consuming`, `executor_handoff.begun_at`, `updated_at`（`skills/mission/lib/mission_application/planning.py:371-376`, `skills/mission/bin/mission-state.py:14348-14362`）。canonical drift: `executor_handoff.status=rejected`, `rejected_reason`, `updated_at`（`skills/mission/bin/mission-state.py:14352-14360`） | 書き込みなし | session effect なし。失敗時だけ command-outcome telemetry sidecar の対象 | **昇格** |
| `executor-handoff verify-step` | `cmd_executor_handoff_verify` -> `_cmd_executor_handoff(..., "verify")`（`skills/mission/bin/mission-state.py:19188-19190`, `skills/mission/bin/mission-state.py:14367`） | 成功: `updated_at` のみ。handoff 自体は同値（`skills/mission/lib/mission_application/planning.py:377-382`, `skills/mission/bin/mission-state.py:14348-14362`）。canonical drift: begin と同じ rejected 3 field | 書き込みなし | session effect なし。失敗時だけ command-outcome telemetry sidecar の対象 | **昇格**。`updated_at` write を消さない限り read-only ではない |
| `executor-handoff record-step` | `cmd_executor_handoff_record` -> `_cmd_executor_handoff(..., "record")`（`skills/mission/bin/mission-state.py:19191-19194`, `skills/mission/bin/mission-state.py:14368`） | `decisions` に `{handoff_id, plan_digest, plan_generation, plan_source, source_id, selection_source, iteration, step_id, result}` を 1 件 append、`updated_at`（`skills/mission/lib/mission_application/planning.py:383-401`, `skills/mission/bin/mission-state.py:14348-14362`） | 書き込みなし | session effect なし。失敗時だけ command-outcome telemetry sidecar の対象 | **昇格** |
| `executor-handoff complete` | `cmd_executor_handoff_complete` -> `_cmd_executor_handoff(..., "complete")`（`skills/mission/bin/mission-state.py:19195-19196`, `skills/mission/bin/mission-state.py:14369`） | `executor_handoff.status=consumed`, `executor_handoff.consumed_at`, `updated_at`（`skills/mission/lib/mission_application/planning.py:402-408`, `skills/mission/bin/mission-state.py:14348-14362`） | 書き込みなし | session effect なし。失敗時だけ command-outcome telemetry sidecar の対象 | **昇格** |
| `specialists recommend` | `cmd_specialists`（`skills/mission/bin/mission-state.py:19056-19078`） | flag なし: なし。`--record-state`: `task_profile`, `specialists_candidates`, `specialists_selected`, `specialists_unavailable`, `specialists_ineligible`, `specialist_registry_projection`, `specialists_decision`, `specialists_phase_plan`, conditional `planning_strategy`, conditional `planning_contract_digest`, set/pop `planning_provider_binding`, `specialists_mode`, `updated_at`（`skills/mission/bin/mission-state.py:4626-4710`） | `passes` は iteration 2 以降の selection context として読むだけ（`skills/mission/bin/mission-state.py:4577-4585`）。7 field の書き込みなし | session effect なし。parser は command-outcome tracking を有効化していない | **昇格**は `--record-state` mode のみ。dry-run は query |
| `specialists consent` | `cmd_specialists_consent`（`skills/mission/bin/mission-state.py:19080-19085`） | **なし**。`resolve_state_file` / repository / session save を呼ばない（`skills/mission/bin/mission-state.py:4721-4738`） | 読み書きなし | `provider-consent.json` の `providers[provider].granted_at` だけを atomic write。既定先は user config（`skills/mission/bin/mission-state.py:4179-4190`, `skills/mission/bin/mission-state.py:4726-4736`） | **降格**。provider-consent aggregate owner |

表の「session state に実際に書く field」は **command が所有する semantic field** である。これとは別に、
session writer 共通の repository envelope が次を更新し得る。

- v4/v5 共通の stamp は、未設定時だけ `schema_version`, `project_root`, `pid`, `pid_source`, `hostname`,
  `session_id`, `agent`, `created_at_session`, `cli_version` を補完する
  （`skills/mission/bin/mission-state.py:1918-1934`, `skills/mission/bin/mission-state.py:9505-9507`,
  `skills/mission/bin/mission-state.py:9575-9578`）。
- v4 の非 administrative save は `last_activity_at` を毎回更新する
  （`skills/mission/bin/mission-state.py:1680-1707`）。lease-free state の acquire、same-owner の renew、
  expiry 後の takeover は `owner_session_id`, `lease_id`, `fencing_epoch`, `lease_expires_at`,
  conditional `lease_history` を repository admission として更新する
  （`skills/mission/bin/mission-state.py:1155-1234`）。
- v5 も admission 時に同じ lease target を typed state へ射影し、renew/takeover を commit precondition に束縛する
  （`skills/mission/lib/mission_persistence/legacy_v4.py:479-502`,
  `skills/mission/lib/mission_persistence/fenced_commit.py:1120-1193`,
  `skills/mission/lib/mission_persistence/fenced_commit.py:3977-4025`）。head/generation、operation journal、
  transaction files は v5 repository の物理 aggregate であり、command reducer の field claim ではない。

したがって exact-diff test は、(a) reducer 単体では transition が claim する semantic field、(b) repository
統合では semantic projection に加えて上記 envelope / physical commit metadata、に期待値を分ける。
`specialists consent` は session repository を経由しないため、この共通更新も一切生じない。

`command_outcome_tracking=True` の handoff 4 command は、CLI rejection 時に
`.mission-state/telemetry/command-outcomes/<session-token>.json` へ bounded record を append し得る
（`skills/mission/bin/mission-state.py:19296-19315`,
`skills/mission/lib/command_outcomes.py:63-99`,
`skills/mission/lib/command_outcomes.py:349-370`）。これは ADR-005 が明記する別 aggregate であり、
handoff reducer の effect / state claim に含めない。成功 command の authoritative record は session state / operation
journal 側であり、この failure sidecar を成功証拠として読まない
（`skills/mission/lib/command_outcomes.py:1-6`）。
通常の guard rejection は session no-change だが、§5.4 の canonical drift は begin/verify の handoff を
`rejected` にする session transition と sidecar failure record の両方を生じ得る。

### 4.2 recommend / consent の authority 裁定

`recommend --record-state` は「provider が state を決める」command ではない。core が registry・installed
availability・consent・task context から routing を選び、その**選択 checkpoint**を audit 可能に記録する。
記録要件自体が ADR-001 に列挙されている
（`docs/adr/001-specialist-auto-selection.md:81-91`）。したがって selection state の writer ではあるが、
review / score / completion authority ではない。

`consent` は first-use 自動選択を許す user-scoped input であり、session の pass 判定ではない。
実際、consent 後に recommend の policy が first-use から auto へ変わるだけである
（`skills/mission/tests/test_specialist_selection.py:384-430`）。この aggregate を MissionState kernel へ入れると、
異なる lifecycle と保存先を不必要に結合するため採らない。

## 5. 現行 handoff lineage 契約

### 5.1 plan bytes と state-owned lineage の束縛

handoff mutation の前に、adapter は次を毎回行う。

1. current session の `canonical_plan` と `executor_handoff` を repository lock 内で読む
   （`skills/mission/bin/mission-state.py:14277-14290`）。
2. provider plan なら `provider_plan_imports` と該当 invocation、core plan なら
   `planning_source_records` から state-owned expected binding を作る
   （`skills/mission/bin/mission-state.py:9808-9832`）。
3. plan file を strict reader で再読込し、path、sha256 digest、canonical JSON、schema、non-empty unique
   step IDs、generation / source / source_id / selection_source / iteration を照合する
   （`skills/mission/lib/planning_lifecycle.py:66-97`）。
4. handoff の `plan_path / plan_digest / plan_generation / plan_source / source_id /
   selection_source / iteration / step_ids` を、上記 plan facts と完全一致させる
   （`skills/mission/lib/mission_application/planning.py:272-315`）。
5. plan document の `depends_on` を step ごとに取り出し、handoff decision に渡す
   （`skills/mission/bin/mission-state.py:14329-14346`）。

このため digest だけの一致では足りず、世代・producer source・selection・iteration・ordered step set が
同時に束縛される。

### 5.2 step decision の束縛

current handoff と同じ `handoff_id` の既存 `decisions` だけを対象にし、各 record の key set、
`plan_digest / generation / source / source_id / selection_source / iteration / step_id / result` を
再検証する。重複 step は拒否する
（`skills/mission/lib/mission_application/planning.py:347-369`）。新しい record も同じ 9 field だけで作る
（`skills/mission/lib/mission_application/planning.py:394-401`）。

### 5.3 現行の順序は「strict verify receipt」ではない

現行の formal state machine は次である。

```text
prepared --begin--> consuming --complete(all steps recorded)--> consumed
    |                    |
    +--verify-step-------+   status は変えない
    +--record-step-------+   prepared からも許可
```

- `begin` は `prepared` のみを `consuming` にする
  （`skills/mission/lib/mission_application/planning.py:371-376`）。
- `verify-step` は `prepared` または `consuming` を許し、step membership を確認するだけで durable
  verification record を残さない
  （`skills/mission/lib/mission_application/planning.py:377-382`）。
- `record-step` も `prepared` または `consuming` を許す。dependency が全て既存 decision にあり、
  同じ step が未記録なら append できる
  （`skills/mission/lib/mission_application/planning.py:383-401`）。
- `complete` だけは `consuming` を必須にし、handoff の全 step が decision に存在することを要求する
  （`skills/mission/lib/mission_application/planning.py:402-408`）。

したがって、通常運用の `begin -> verify-step -> record-step ... -> complete` は安全な利用手順だが、
**verify-step が record-step の前に実行済みであることを state は証明しない**。kernel 化で verification receipt
や strict verify-before-record を新設すると現行契約の意味変更になる。今回の reducer は record-step 自身が
同じ canonical plan observation と dependency を再検証する現行意味を保つ。

### 5.4 rejection と replay

canonical identity error は `begin` / `verify-step` に限り handoff を `rejected` にして理由を保存する。
`record-step` / `complete` の同じ error は session state を変えず拒否する
（`skills/mission/bin/mission-state.py:14352-14360`）。v5 の各 command は caller-stable operation ID を要求し、
同じ ID / intent は head generation を増やさず replay、同じ ID / 別 intent は拒否される
（`skills/mission/tests/test_issue550_c2_stage_b_batch1.py:248-322`）。stale lease も mutation 前に拒否される
（`skills/mission/tests/test_issue550_c2_stage_b_batch1.py:413-430`）。

現行 application の closed-wire check は `rejected` に `rejected_reason` だけを許す
（`skills/mission/lib/mission_application/planning.py:121-135`）一方、typed codec/model は consuming 後の
rejection に備えて optional `begun_at` を許している
（`skills/mission/lib/mission_kernel/codec_v4.py:370-380`,
`skills/mission/lib/mission_kernel/model.py:264-275`）。kernel reducer では後者へ一本化し、consuming handoff の
canonical drift でも既存 `begun_at` を失わない。これは新しい authority の追加ではなく、既に persistence
codec が表現できる rejected variant への統一である。

## 6. 提案する typed state projection

### 6.1 最小 A4 projection

current `MissionState` は plan と handoff を closed union として持つ一方、step `decisions` と specialist
selection/invocation は typed field を持たず、legacy document 側に残っている
（`skills/mission/lib/mission_kernel/model.py:470-483`,
`skills/mission/lib/mission_kernel/codec_v4.py:666-686`）。handoff reducer が prior decisions を command 引数から
信じると、caller が「dependency 完了済み」を偽造できる。`legacy_passthrough` を reducer authority にする案も、
K2 の境界と合わない
（`docs/design/500-mission-state-aggregate-implementation-design.md:555-564`）。

このため `MissionState` に、A4 全体を再モデル化せず今回必要な最小 closed projection を追加する。

```python
@dataclass(frozen=True)
class ExecutorStepDecision:
    handoff_id: str
    plan_digest: str
    plan_generation: int
    plan_source: str
    source_id: str
    selection_source: str
    iteration: int
    step_id: str
    result: str

@dataclass(frozen=True)
class SpecialistSelectionProjection:
    task_profile: FrozenJsonObject | None
    candidates: tuple[FrozenJsonObject, ...]
    selected: tuple[FrozenJsonObject, ...]
    unavailable: tuple[FrozenJsonObject, ...]
    ineligible: tuple[FrozenJsonObject, ...]
    registry_projection: FrozenJsonObject | None
    decision: FrozenJsonObject | None
    phase_plan: tuple[FrozenJsonObject, ...]
    mode: str | None
    active_provider_invocation_ids: tuple[str, ...]
    planning_policy_version: int | None
    planning_strategy: str | None
    planning_contract_digest: str | None
    planning_provider_binding: FrozenJsonObject | None

@dataclass(frozen=True)
class A4Projection:
    current_handoff_decisions: tuple[ExecutorStepDecision, ...]
    specialist_selection: SpecialistSelectionProjection
```

`codec_v4` は current handoff ID に一致する `decisions` を closed projection へ decode し、同じ ID の malformed
record を拒否する。別 handoff ID の legacy record は current reducer の判断対象外なので passthrough のまま保つ。
specialist projection は既存 public/lifecycle validator と同じ closed contractで decode する。
v5 exact top-level を増やさず、`extensions` 内で新たに kernel-owned として予約する A4 keys から同じ
projection を decode / encode する。
これにより generic extension 全体を authority にせず、今回の reducer が必要な field だけを typed owner にする。

より単純に prior `decisions` と `active_provider_invocation_ids` を command へ渡す案は、state-derived guard を
caller assertion に落とすため却下する。逆に provider lifecycle 全体を新 top-level aggregate にする案は
6 command の必要範囲を超える。最小 projection は dependency/order と active-invocation fence を kernel 内で
判断でき、wire compatibility も維持する。

## 7. command dataclass と verified observation

### 7.1 canonical plan observation

filesystem I/O は kernel に入れない。adapter/application が strict read した結果だけを immutable fact にする。
ADR-005 も host I/O は adapter が検証し typed fact として kernel へ渡す境界を定めている
（`docs/adr/005-typed-mission-kernel-and-unit-of-work.md:81-102`）。

```python
@dataclass(frozen=True)
class CanonicalPlanObservation:
    path: str
    digest: str
    generation: int
    source: str
    source_id: str
    selection_source: str
    iteration: int
    ordered_step_ids: tuple[str, ...]
    dependencies: tuple[tuple[str, tuple[str, ...]], ...]
```

observation は admitted state の `Plan` と `Handoff.plan` に reducer が再束縛する。adapter が verified と称しても、
state lineage と一致しなければ reject する。

### 7.2 command

```python
@dataclass(frozen=True)
class BeginExecutorHandoff:
    at: str
    plan: CanonicalPlanObservation

@dataclass(frozen=True)
class VerifyExecutorStep:
    at: str
    step_id: str
    plan: CanonicalPlanObservation

@dataclass(frozen=True)
class RecordExecutorStep:
    at: str
    step_id: str
    result: str
    plan: CanonicalPlanObservation

@dataclass(frozen=True)
class CompleteExecutorHandoff:
    at: str
    plan: CanonicalPlanObservation

@dataclass(frozen=True)
class RejectExecutorHandoff:
    at: str
    attempted_operation: Literal["begin", "verify-step"]
    reason_code: CanonicalPlanRejectionCode

@dataclass(frozen=True)
class RecordSpecialistRecommendation:
    at: str
    expected_complexity: str | None
    expected_iteration: int
    projection: SpecialistRecommendationProjection
```

`RejectExecutorHandoff` は public parser command にしない。begin / verify の strict canonical observation 作成が
closed `canonical-*` reason で失敗した場合だけ application use case が発行する。free-form exception text を
command に入れず、現行の stable reason set を enum 化する。

`RecordSpecialistRecommendation` は selection projection だけを受け、provider stdout、review finding、score、
pass、terminal outcome を入力に持たない。`SpecialistRecommendationProjection` は existing public sanitizer が
許可する candidate / decision / phase-plan field の closed value である。`--record-state` がない場合はこの
command を発行せず query result だけを返す。

## 8. transition table

| rule ID | command | allowed state / guard | reducer | event | state claim |
|---|---|---|---|---|---|
| `executor-handoff-begin` | `BeginExecutorHandoff` | handoff=`prepared`; plan observation が current plan/handoff と完全一致 | `ConsumingHandoff(..., begun_at=at)` | `executor-handoff-begun` | `executor_handoff.status`, `begun_at`, `updated_at` |
| `executor-handoff-verify-step` | `VerifyExecutorStep` | handoff=`prepared|consuming`; observation 一致; step member | handoff/decisions は同値、`updated_at=at` | `executor-step-revalidated` | `updated_at` のみ |
| `executor-handoff-record-step` | `RecordExecutorStep` | handoff=`prepared|consuming`; observation 一致; step member; result enum; no duplicate; 全 dependency が typed current decisions に存在 | closed decision 1 件 append。handoff は同値 | `executor-step-recorded` | current handoff の `decisions` append、`updated_at` |
| `executor-handoff-complete` | `CompleteExecutorHandoff` | handoff=`consuming`; observation 一致; ordered step set と recorded set が一致 | `ConsumedHandoff(..., consumed_at=at)` | `executor-handoff-consumed` | `executor_handoff.status`, `consumed_at`, `updated_at` |
| `executor-handoff-reject-canonical-drift` | `RejectExecutorHandoff` | non-absent handoff; attempted operation は begin/verify; closed canonical reason | `RejectedHandoff`。既存 `begun_at` があれば保持 | `executor-handoff-rejected` | `executor_handoff.status`, `rejected_reason`, optional `begun_at`, `updated_at` |
| `specialists-record-recommendation` | `RecordSpecialistRecommendation` | current complexity/iteration が expected と一致; active provider invocation が 0; projection が public/lifecycle contract に適合; selection ID が decision/candidate/selected/unavailable で一致 | selection fields を全置換。planning selected なら provider strategy/binding を set。該当なしで policy v1 なら core に戻し binding だけを削除し、現行どおり既存 `planning_contract_digest` は保持。その他は planning fields を保持 | `specialist-recommendation-recorded` | §4.1 の recommend fields、`updated_at` |

ここでの claim は compatibility bridge の control claim listを広げる意味ではない。#633 と同じく、
`transition.new_state` が最終値を所有し、その projection を repository が保存するという field ownership である。
既存 `_CLAIMABLE_CONTROL_FIELDS` は completion-adjacent compatibility bridge である
（`skills/mission/lib/mission_kernel/transitions.py:580-624`）。今回の 7 completion field は全 reducer で
input と同値を必須にする。

### 8.1 transaction 順序

effect は全 command で空 tuple とする。#633 が定める typed preparation / transition executor と同じ入口を使い、
次の順序にする。

```text
lock
  -> load + lease / fence / generation admission
  -> strict canonical plan observation または recommendation projection を prepare
  -> typed command を構築
  -> decide(admitted_state, command)
  -> stage transition.new_state（blobs/effects は空）
  -> exact generation / head precondition で commit
```

handoff/recommend 専用の第 2 persistence protocol は作らない。v4 compatibility と v5 head の現行
operation identity / replay contract を共通 executor に保持する。current repository は v5 mutation に
caller operation ID を要求する
（`skills/mission/bin/mission-state.py:9416-9436`）。

## 9. 降格 command の no-write AST guard

### 9.1 `specialists consent`

`test_issue619_a4_no_completion_writes.py` は direct subscript assignment、`update`、`setdefault`、
completion helper call を検出し、合成違反 fixture で guard 自体の検出力を固定している
（`skills/mission/tests/test_issue619_a4_no_completion_writes.py:37-122`,
`skills/mission/tests/test_issue619_a4_no_completion_writes.py:147-174`）。consent は正当に sidecar を書くため、
generic write 全禁止にはせず **session authority への到達だけ**を禁止する。

新 guard `find_session_write_violations(entrypoint, module)` は local helper call を再帰的に辿り、次を検出する。

- session resolver/repository: `resolve_state_file`, `session_file`, `_activity_state_file`,
  `_legacy_lifecycle_repository`, `LocalFencedRepository`, `decode_mission_state`, `decide`。
- session writer/transition: repository `save/execute/stage/commit`、session state writer helper、
  completion helper。
- session authority key: completion 7 field に加え `executor_handoff`, `decisions`, `task_profile`,
  `specialists_*`, `specialist_registry_projection`, `planning_*`, `updated_at` への代入・update・setdefault・pop・del。
- literal `.mission-state/sessions` path または同 path を組み立てる call。

許可するのは `_default_consent_file` / explicit `--consent-file` の resolve、parent mkdir、既存 consent JSON read、
`providers[provider].granted_at` の更新、同じ path への `atomic_write_json` だけである。command inventory でも
consent は `NON_SESSION_DIRECT_CALL_FUNCTIONS` に分類済みである
（`skills/mission/lib/mission_application/command_owners.py:145-150`）。

合成違反 fixture は少なくとも次を入れ、検出が non-empty になることを先に Red にする。

```python
def offender(args):
    state_file = resolve_state_file(Path.cwd())
    repository = _legacy_lifecycle_repository(Path.cwd(), state_file, stamp=True)
    with repository.transaction():
        data = repository.load()
        data["specialists_selected"] = []
        data["passes"] = True
        repository.save(data)
```

併せて正常 fixture は consent sidecar の `providers` update と `atomic_write_json(consent_path, data)` を許し、
session no-write と「一切書かない」を混同しない。

### 9.2 recommend dry-run

同一 handler に `--record-state` branch があるため AST で関数全体を no-write と宣言しない。behavior test で
flag なし実行の前後に session bytes/head generation と `.mission-state` descendants が不変であることを固定する。
`--record-state` 実行では逆に transition event と §4.1 の exact state diff を要求する。

## 10. TDD テストリスト

### Red: parser / inventory

- [ ] parser の 6 `set_defaults` が本書の handler と一致する。
- [ ] state-recording recommend と handoff 4 command は kernel command inventory に入り、consent は
  separate aggregate inventory のままになる。
- [ ] source/plugin parser・command inventory が byte-identical。

### Red: exact state diff と completion boundary

- [ ] reducer 単体では 4 handoff operation の成功前後 semantic diff が §4.1 と完全一致し、repository
  統合では §4.1 直後に列挙した metadata / lease / activity envelope 以外の差分がない。
- [ ] verify-step 成功は handoff/decisions を変えず `updated_at` だけを変える。
- [ ] recommend dry-run は session bytes/head generation を変えない。
- [ ] recommend `--record-state` は列挙した selection/planning fields だけを変える。
- [ ] 5 reducer 全てで `phase / passes / loop_active / halt_reason / halt_category /
  terminal_outcome / score_history` が input と同値。
- [ ] provider candidate / decision に completion 7 field、review result、score を混入させても reducer が reject する。

### Red: handoff lineage / order / malformed

- [ ] path、digest、generation、source、source_id、selection_source、iteration、ordered step IDs の各 1-field drift を
  個別に reject し state を勝手に進めない。
- [ ] plan bytes mutation、non-canonical JSON、duplicate/empty step IDs、unknown dependency、bool-as-int generation/
  iteration を fail-closed にする。
- [ ] `record-step` は dependency 未完了・duplicate step・unknown step・unknown result を reject する。
- [ ] 現行 parity として `prepared` からの record、record 後の verify を許す。
- [ ] complete は `consuming` かつ exact all-step set のみ許し、incomplete / duplicate / forged decision を reject する。
- [ ] canonical drift は begin/verify だけ `RejectedHandoff` transition を発行し、record/complete は state no-change rejection。
- [ ] rejected handoff は optional prior `begun_at` を保持し、codec/model/application の wire contract を一本化する。

### Red: recommendation / provider boundary

- [ ] complexity / iteration TOCTOU mismatch と active provider invocation を kernel guard で reject する。
- [ ] selection ID が decision / candidates / selected / unavailable で一つに束縛される。
- [ ] planning provider 選択時の strategy / contract digest / binding、policy-v1 fallback 時の core / binding removal、
  legacy policy 時の planning fields preserve が現行一致する。
- [ ] recommendation は provider を dispatch/install せず、state/effect に provider stdout を含めない。
- [ ] existing `mark-passes` specialist gate、review、score、terminal outcome の挙動が不変。

### Red: persistence / replay / guard

- [ ] v1-v4 wire/CLI JSON と v5 head operation replay を維持する。
- [ ] v5 は missing operation ID、stale fence、head generation race を mutation 前に reject する。
- [ ] 同じ operation ID / same intent は generation を増やさず同じ committed result を返し、same ID / different intent は reject する。
- [ ] consent no-session-write AST guard が production handler の transitive call graph を通す。
- [ ] 合成違反 fixture が session resolver、specialist field write、completion write、repository save を検出する。
- [ ] consent の正常 sidecar write fixture を誤検出しない。

### Green / Refactor gates

- [ ] `test_issue509_a4_application.py`, `test_planning_provider_lifecycle.py`,
  `test_issue550_c2_stage_b_batch1.py`, `test_issue550_c2_stage_b_batch2.py`, specialist suites が Green。
- [ ] focused suite 後に full suite、artifact hygiene、vendor fingerprint が Green。
- [ ] source/plugin 対応 production files が全て `cmp` で一致する。
- [ ] CLI handler から handoff/recommend の direct dict mutation と direct `repository.save(data)` が消え、
  typed preparation -> `decide` -> transition persistence だけになる。

## 11. 受け入れ条件

- [ ] 実装対象は handoff 4 + recommend record-state の 5 session transitions と確定し、consent は対象外である。
- [ ] repository が保存する semantic state は `transition.new_state` の projection と byte-for-byte 等価である。
- [ ] 5 command の transition effects は空であり、kernel 外 effect 発行がない。
- [ ] handoff の plan lineage、step membership、dependency、duplicate、complete gate、canonical drift rejection、
  operation replay、lease/fence が現行より弱くならない。
- [ ] strict verify-before-record や durable verify receipt を暗黙に追加せず、現行の許可順序を維持する。
- [ ] recommend は selection/routing checkpoint だけを所有し、review / score / pass / terminal authority を持たない。
- [ ] consent は provider-consent aggregate の protocol だけを使い、session state に到達しないことを AST guard と
  合成違反 fixture で証明する。
- [ ] dry-run recommend は state no-change、record-state recommend は exact diff を持つ。
- [ ] retained v1-v4 compatibility、v5 exact-head replay、CLI output/exit code、source/plugin mirror が Green。

## 12. 実装時の変更対象ファイル

### source

- `skills/mission/bin/mission-state.py`
- `skills/mission/lib/mission_application/planning.py`
- `skills/mission/lib/mission_application/command_owners.py`
- `skills/mission/lib/mission_kernel/commands.py`
- `skills/mission/lib/mission_kernel/transitions.py`
- `skills/mission/lib/mission_kernel/model.py`
- `skills/mission/lib/mission_kernel/codec_v4.py`
- `skills/mission/lib/mission_kernel/codec_v5.py`
- `skills/mission/lib/mission_kernel/a4.py`（closed A4 projection / validation。新規）
- `skills/mission/lib/mission_persistence/legacy_v4.py`（#633 の typed executor 再利用で変更不要なら除外）
- `skills/mission/lib/provider_public_contract.py` / `skills/mission/lib/specialist_lifecycle.py`
  （closed validator の管理元を `a4.py` へ寄せる場合だけ）

### tests

- `skills/mission/tests/test_issue624_a4_remaining_kernel.py`（新規）
- `skills/mission/tests/test_issue619_a4_no_completion_writes.py`
- `skills/mission/tests/test_issue509_a4_application.py`
- `skills/mission/tests/test_planning_provider_lifecycle.py`
- `skills/mission/tests/test_issue550_c2_stage_b_batch1.py`
- `skills/mission/tests/test_issue550_c2_stage_b_batch2.py`
- `skills/mission/tests/test_specialist_selection.py`
- `skills/mission/tests/test_specialist_checkpoint.py`
- `skills/mission/tests/test_command_inventory.py`

### plugin mirror

上記 source production file のうち `skills/mission/**` に対応するものを
`plugins/mission/skills/mission/**` に byte-identical で反映する。tests は現行 distribution layout に存在しないため
plugin へ複製しない。新規 `mission_kernel/a4.py` も production code なので mirror 必須である。

## 13. 親 Issue #614 の更新案

このターンでは GitHub Issue を変更しない。実装 PR の検証完了後、#614 の批3-b 行と実測注記を次の内容へ更新する。

```markdown
> **批3-b 実測結果（#624 / PR #<N>、<date>）**:
> A4 残り 6 command を parser から実測。executor-handoff 4 command は
> handoff/decisions/updated_at の session writer、specialists recommend は
> `--record-state` 時だけ selection/planning checkpoint の session writerだったため、
> 5 command を typed kernel transition 化した。完了隣接 7 field は全件不変。
> specialists consent は user-scoped provider-consent aggregate だけを書くため MissionState
> kernel 対象外へ降格し、transitive no-session-write AST guard と合成違反 fixture で固定した。
> recommend dry-run は state no-change。provider は evidence provider のままで review / score /
> pass/fail authority を持たない。
```

親の「批3対象」表は `A4 残り 6` を次の 2 行へ分ける。

| owner group | command | 実測結果 |
|---|---|---|
| A4.specialist-planning | executor-handoff begin / verify-step / record-step / complete、specialists recommend `--record-state` | session writer 5、kernel 化 |
| A4.specialist-planning | specialists consent | provider-consent aggregate のみ、MissionState 対象外 |

#619 から引き継いだ 9 command の既存注記と no-completion-write guard は削除せず、本書の 6 command 裁定と
混ぜない。#624 の check を完了にするのは、実装・mirror・full test・独立 review 完了後である。

## 14. リスク、代替案、出口戦略

| リスク | 制御 | 出口戦略 |
|---|---|---|
| prior decisions を command fact として信じ dependency を偽造できる | current handoff decisions を A4 typed projection として state から読む | projection 導入が独立 review で大きすぎると判定された場合も、caller fact 案には戻さず typed decisions の先行小 PR に分離する |
| verify-step を read-only と誤認し現行 updated_at/replay が変わる | exact state diff と v4/v5 replay test | updated_at 廃止は別 Issue の明示的 behavior change とする |
| kernel 化を機に strict verify-before-record を導入する | prepared からの record と record 後 verify の parity test | durable verification が必要なら新 schema / receipt を別 Issue で設計する |
| recommend projection から provider authority が漏れる | closed field set、completion 7 field invariant、provider-output rejection | full specialist aggregate 化は別 capability に分離し、#624 は selection/routing のみ保持する |
| consent sidecar を session kernel に混ぜる | ADR-005 separate aggregate 裁定と no-session-write AST guard | consent protocol の durability 強化が必要なら user-config aggregate owner の別 Issue にする |
| #633 と別 executor を作り persistence が二重化する | #633 の typed preparation / transition executor を再利用 | #633 の最終 API と差が出たら #624 の adapter だけ合わせ、repository protocol を増やさない |
