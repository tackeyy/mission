# #644 transition.new_state 射影 writer 設計

**第 2 版（Fable 5 異系統レビュー 1 巡反映）**

| 指摘 | 第 2 版での対応 |
|---|---|
| H1 | §4.3 で `_executing` → `_callback_depth` の対象を `LegacyV4Repository` / `V5CompatibilityRepository` の両方と明記。 |
| M1 | §2.4 に supersede-reviews production 経路の実コード行と、synthetic state が gate-only である確認結果を追記。 |
| M2 | §5.1 のデフォルトを PR1（golden + projector）/ PR2（typed payload + atomic cutover）の二分割へ反転。 |
| M3 | §4.5 に `stamp_metadata` の全 field と条件、59 leaf との実効書込み非重複を行番号付きで追記。 |
| L1 | §3.4 の supersede-reviews に `activity_anomaly_counts` を含む activity payload を追記。 |
| L2 | §6.1 条件 2 に corpus-backed 比較の clock 同一性前提を追記。 |
| L3 | §5.2 に `LegacyCommandExecutionResult` の戻り値を使う application caller を追記。 |

## 1. 目的・結論

Issue #644（批2-a-4）では、kernel が完成させた `transition.new_state` を
`project_legacy_document()` で legacy document に射影し、その結果だけを repository が保存する。
application から repository へ渡している `mutation` / `finalize` callback は削除する。

実測結論は次のとおりである。

- 基点 `e949bc810838dadd1abea16f72cae37c6f99e2aa` で 8 経路を実行した結果、
  **8/8 経路、合計 59 leaf で現行保存 document と `new_state` 射影が不一致**だった。
- 現行保存 document は、既存 golden fixture の契約に 8/8 経路で一致した。したがって比較元は有効であり、
  差分は現行 writer の揺れではない。
- 差分の主因は、(A) timing / activity / audit 等を kernel command が受け取らず
  `new_state.extensions` / `legacy_passthrough` を完成できないこと、(B) 射影が新規
  `halt_category` / `terminal_outcome` を挿入せず、空の `lease_history` を勝手に追加すること、
  (C) supersede-reviews の比較 driver が synthetic decision state を transition の入力にしていること、の三つである。
- `SetExtensionFields(fields=...)` が書く `custom_note` は現行保存値と一致した。
  generic extension が `extensions` と `legacy_passthrough` の両方に保存される現方式は維持できる。
- command には application が生成した変更 effect ではなく、deep-frozen の
  **互換観測 payload** を渡す。kernel reducer が command ごとの閉じた許可集合を検証し、
  canonical control と互換 field を合わせた `new_state` を一度だけ生成する。
- #622 の裁定に従い、process-global issued-transition registry は本 Issue では削除しない。
  callback / claims の `before` 依存は除去できるが、他経路を含む入力 provenance の保証までは
  #644 のスコープでは一本化できない。

### スコープ

対象は次の accepted transition 8 経路である。

1. mark-pass
2. advance
3. mark-halt
4. reactivate
5. resume-stale（refresh-pid の reactivate branch）
6. set
7. permission observation
8. supersede-reviews

terminal / 復号不能 document の emergency halt のように、#632 で gate-only と裁定済みの branch は
transition 射影へ昇格しない。これらは application が transaction 内で互換 document を事前構築して
`save()` する縮退経路として残すが、repository に callback は渡さない。

## 2. 実測方法と結果

### 2.1 方法

`skills/mission/tests/test_issue632_transition_is_the_writer.py` の 8 個の `_path_*` driver を
変更せず実行し、各 driver が記録した現行保存 dict と次を比較した。

```python
json.loads(project_legacy_document(decision.transition.new_state))
```

比較スクリプトは `/tmp/issue644_projection_diff.py` に置き、repository へは追加していない。
比較は top-level key、再帰的な leaf value、canonical JSON bytes の三段階で行った。
実行時刻は 2026-08-23T13:33Z 前後であり、corpus-backed の mark-pass / advance に含まれる
環境由来時間はこの実行値である。

同時に既存 fixture
`skills/mission/tests/fixtures/issue632_main_saved_documents.json` との比較も行った。
mark-pass / advance は既存テストと同じ環境由来 field 除外を適用し、残り 6 経路は全 key/value を比較した。
結果は 8/8 経路で golden 契約に一致した。

確認した既存テスト:

```text
16 passed in 17.67s
```

対象は次の二つである。

- `test_saved_document_matches_main_on_every_transition_path`（8 case）
- `test_saved_document_matches_decided_claims_for_every_transition_path`（8 case）

### 2.2 集計

| 経路 | 現行 key 数 | 射影 key 数 | top-level 差分 field | leaf 差分数 | canonical bytes |
|---|---:|---:|---|---:|---|
| mark-pass | 78 | 77 | `activity_current`, `activity_rollup`, `activity_segments`, `lease_history`, `passes_forced`, `phase_durations_sec`, `phase_started_at`, `terminal_outcome`, `updated_at` | 12 | 不一致 |
| advance | 76 | 77 | `activity_current`, `activity_rollup`, `activity_segments`, `lease_history`, `phase_durations_sec`, `phase_started_at`, `updated_at` | 14 | 不一致 |
| mark-halt | 12 | 9 | `halt_category`, `phase_started_at`, `terminal_outcome`, `updated_at` | 4 | 不一致 |
| reactivate | 14 | 9 | `activity_current`, `activity_rollup`, `activity_segments`, `phase_started_at`, `reactivation_history`, `updated_at` | 6 | 不一致 |
| resume-stale | 14 | 11 | `activity_current`, `activity_rollup`, `activity_segments`, `phase_started_at`, `pid`, `resume_target_phase`, `updated_at` | 7 | 不一致 |
| set | 10 | 10 | `updated_at` | 1 | 不一致 |
| permission observation | 11 | 9 | `halt_category`, `terminal_outcome`, `updated_at` | 3 | 不一致 |
| supersede-reviews | 18 | 6 | `activity_anomaly_counts`, `activity_current`, `halt_category`, `iteration`, `mission`, `phase_durations_sec`, `phase_started_at`, `resume_target_phase`, `review_generation`, `review_group_id`, `terminal_outcome`, `updated_at` | 12 | 不一致 |

### 2.3 leaf 差分全量

表中の `<absent>` は JSON field が存在しないことを示す。裁定は次の記号を使う。

- **A-payload**: kernel が知らない互換 field。typed 互換 payload に載せる。
- **A-input**: transition の入力 state が synthetic で、実 document の情報を失っている。実 state 入力へ直す。
- **B-projector**: `project_legacy_document()` の射影欠陥を修正する。

#### mark-pass

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.activity_current` | `null` | `{"iteration":1,"kind":"active","origin":"phase-default","phase":"scoring","reason":"planning","started_at":"2026-08-23T13:33:07Z"}` | A-payload |
| `$.activity_rollup.activity_duration_totals_sec.active` | `126185214.0` | `1.0` | A-payload |
| `$.activity_rollup.closed_segment_count` | `2` | `1` | A-payload |
| `$.activity_rollup.observed_total_sec` | `126185214.0` | `1.0` | A-payload |
| `$.activity_rollup.phase_activity_duration_totals_sec.scoring` | `{"active":126185213.0}` | `<absent>` | A-payload |
| `$.activity_segments[1]` | `{"duration_sec":126185213.0,"ended_at":"2030-08-23T01:00:00Z","iteration":1,"kind":"active","phase":"scoring","reason":"planning","started_at":"2026-08-23T13:33:07Z"}` | `<absent>` | A-payload |
| `$.lease_history` | `<absent>` | `[]` | B-projector |
| `$.passes_forced` | `false` | `<absent>` | A-payload |
| `$.phase_durations_sec.scoring` | `126185213.0` | `<absent>` | A-payload |
| `$.phase_started_at` | `2030-08-23T01:00:00Z` | `2026-08-23T13:33:07Z` | A-payload |
| `$.terminal_outcome` | `"completed_pass"` | `<absent>` | B-projector |
| `$.updated_at` | `2030-08-23T01:00:00Z` | `2026-08-23T13:33:07Z` | A-payload |

#### advance

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.activity_current.iteration` | `2` | `1` | A-payload |
| `$.activity_current.origin` | `<absent>` | `"phase-default"` | A-payload |
| `$.activity_current.phase` | `"reviewing"` | `"scoring"` | A-payload |
| `$.activity_current.reason` | `"review"` | `"planning"` | A-payload |
| `$.activity_current.started_at` | `2030-08-23T01:00:00Z` | `2026-08-23T13:33:07Z` | A-payload |
| `$.activity_rollup.activity_duration_totals_sec.active` | `126185214.0` | `1.0` | A-payload |
| `$.activity_rollup.closed_segment_count` | `2` | `1` | A-payload |
| `$.activity_rollup.observed_total_sec` | `126185214.0` | `1.0` | A-payload |
| `$.activity_rollup.phase_activity_duration_totals_sec.scoring` | `{"active":126185213.0}` | `<absent>` | A-payload |
| `$.activity_segments[1]` | `{"duration_sec":126185213.0,"ended_at":"2030-08-23T01:00:00Z","iteration":1,"kind":"active","phase":"scoring","reason":"planning","started_at":"2026-08-23T13:33:07Z"}` | `<absent>` | A-payload |
| `$.lease_history` | `<absent>` | `[]` | B-projector |
| `$.phase_durations_sec.executing` | `126185213.0` | `<absent>` | A-payload |
| `$.phase_started_at` | `2030-08-23T01:00:00Z` | `2026-08-23T13:33:07Z` | A-payload |
| `$.updated_at` | `2030-08-23T01:00:00Z` | `2026-08-23T13:33:07Z` | A-payload |

#### mark-halt

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.halt_category` | `"blocked-external"` | `<absent>` | B-projector |
| `$.phase_started_at` | `"2030-08-23T01:00:00Z"` | `<absent>` | A-payload |
| `$.terminal_outcome` | `"blocked_external"` | `<absent>` | B-projector |
| `$.updated_at` | `"2030-08-23T01:00:00Z"` | `"2030-08-23T00:00:00Z"` | A-payload |

#### reactivate

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.activity_current` | `{"detail":"unblocked by the provider","iteration":2,"kind":"active","phase":"planning","reason":"resumed-implementation","started_at":"2030-08-23T01:00:00Z"}` | `<absent>` | A-payload |
| `$.activity_rollup` | `{"activity_duration_totals_sec":{},"closed_segment_count":0,"observed_total_sec":0.0,"phase_activity_duration_totals_sec":{},"wait_reason_totals_sec":{}}` | `<absent>` | A-payload |
| `$.activity_segments` | `[]` | `<absent>` | A-payload |
| `$.phase_started_at` | `"2030-08-23T01:00:00Z"` | `<absent>` | A-payload |
| `$.reactivation_history` | `[{"approved_by_user":true,"approved_reason":"unblocked by the provider","previous_halt_category":"blocked-external","previous_halt_reason":"blocked externally","previous_phase":"halted","target_phase":"planning","timestamp":"2030-08-23T01:00:00Z"}]` | `<absent>` | A-payload |
| `$.updated_at` | `"2030-08-23T01:00:00Z"` | `"2030-08-23T00:00:00Z"` | A-payload |

#### resume-stale

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.activity_current` | `{"iteration":2,"kind":"active","origin":"phase-default","phase":"planning","reason":"planning","started_at":"2030-08-23T01:00:00Z"}` | `<absent>` | A-payload |
| `$.activity_rollup` | `{"activity_duration_totals_sec":{},"closed_segment_count":0,"observed_total_sec":0.0,"phase_activity_duration_totals_sec":{},"wait_reason_totals_sec":{}}` | `<absent>` | A-payload |
| `$.activity_segments` | `[]` | `<absent>` | A-payload |
| `$.phase_started_at` | `"2030-08-23T01:00:00Z"` | `<absent>` | A-payload |
| `$.pid` | `424243` | `424242` | A-payload |
| `$.resume_target_phase` | `<absent>` | `"planning"` | A-payload |
| `$.updated_at` | `"2030-08-23T01:00:00Z"` | `"2030-08-23T00:00:00Z"` | A-payload |

#### set

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.updated_at` | `"2030-08-23T01:00:00Z"` | `"2030-08-23T00:00:00Z"` | A-payload |

`custom_note="kept"` は両 document で一致した。`SetExtensionFields` が generic field を
`extensions` と `legacy_passthrough` の両方へ反映する現実装は、分類 (c) の確認済み経路である。

#### permission observation

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.halt_category` | `"blocked-external"` | `<absent>` | B-projector |
| `$.terminal_outcome` | `"blocked_external"` | `<absent>` | B-projector |
| `$.updated_at` | `"2030-08-23T01:00:00Z"` | `"2030-08-23T00:00:00Z"` | A-payload |

この driver は `transition_phase=None` の最小 branch なので timing field が増えていない。
production の injected transition branch で許可される `activity_*`, `phase_durations_sec`,
`phase_started_at`, `resume_target_phase` も typed payload の対象に含める。

#### supersede-reviews

| JSON path | 現行保存値 | 現行 `new_state` 射影値 | 裁定 |
|---|---|---|---|
| `$.activity_anomaly_counts` | `{"invalid-phase-terminal":1}` | `<absent>` | A-payload |
| `$.activity_current` | `null` | `<absent>` | A-payload |
| `$.halt_category` | `"stale"` | `<absent>` | B-projector |
| `$.iteration` | `1` | `<absent>` | A-input |
| `$.mission` | `"issue632 property mission"` | `<absent>` | A-input |
| `$.phase_durations_sec` | `{}` | `<absent>` | A-payload |
| `$.phase_started_at` | `"2030-08-23T01:00:00Z"` | `<absent>` | A-payload |
| `$.resume_target_phase` | `"executing"` | `<absent>` | A-payload |
| `$.review_generation` | `1` | `<absent>` | A-input |
| `$.review_group_id` | `"issue632"` | `<absent>` | A-input |
| `$.terminal_outcome` | `"stale_superseded"` | `<absent>` | B-projector |
| `$.updated_at` | `"2030-08-23T00:00:00Z"` | `<absent>` | A-input |

この driver は `_mark_halt_decision_state()` の最小 synthetic state から作った transition を
そのまま保存比較へ使うため、identity / review lineage / passthrough を失う。production code は
decode 可能かつ active な superseded document では `real_terminalizable_state(state)` を使い、
terminal / 復号不能 document だけを gate-only に落としている。実装時は driver も同じ分岐へ合わせ、
accepted transition を synthetic state から永続化しない。

### 2.4 field 単位の最終裁定

| field 群 | 分類 | 裁定 |
|---|---|---|
| `phase_started_at`, `phase_durations_sec`, `resume_target_phase` | A-payload | transition 前に既存 timing reducer を shadow に一度だけ適用し、delta を immutable payload にする。`phase` 自体は payload で受けず canonical reducer が所有する。 |
| `activity_current`, `activity_segments`, `activity_rollup`, `activity_anomaly_counts`, activity gap/event fields | A-payload | 既存 activity reducer の結果を payload にする。permission observation は現行 `_PERMISSION_TRANSITION_FIELDS` と shape validator を kernel 側の command validator に移す。 |
| `updated_at` | A-payload | 全 command に `at` を持たせ、kernel が legacy projection field を更新する。application が時刻を再採取しない。 |
| `passes_forced`, `force_reason`, `force_approved_by_user`, `force_approval` | A-payload | `MarkPass` 専用 payload。approval envelope は deep-freeze し、kernel が terminal binding を照合して `consumed=true` の最終値だけを new_state に置く。 |
| `specialist_waiver`, `early_stop_evaluation` | A-payload | gate の入力には使わない observation として `MarkPass` payload に載せる。観測失敗の既存 record-and-continue 意味論を維持する。 |
| 生の `halt_reason` | A-payload（専用 scalar） | `MarkHalt.reason` は trim 済み semantic reason、`legacy_reason` は現行保存文字列とする。`legacy_reason.strip() == reason` を kernel が検証し、transition を送る branch では canonical control も legacy 値を保持する。空 reason は従来どおり gate-only。 |
| `reactivation_history` | A-payload | `Reactivate` 専用 audit payload。previous fields は入力 state と一致することを kernel が検証し、append 後配列を new_state に保持する。 |
| `pid` | A-payload | `ResumeStale` に exact integer `new_pid` を追加する。bool / 0 以下を拒否する。 |
| `goal_dispatch_*` | A-payload | `MarkHalt` の routed-goal branch 専用 payload。routing は application で一度だけ評価し、kernel は closed key set と JSON shape を検証する。 |
| `supersedes` | 分類 (c) | current generation branch は `SetExtensionFields` の generic field として既に `extensions` / `legacy_passthrough` に残る。追加で同 command の `at` により `updated_at` を揃える。 |
| その他 extension kv | 分類 (c) | `SetExtensionFields.fields` の既存二重格納を維持し、projector が legacy_passthrough から同値を返すことを golden で固定する。 |
| 新規 `halt_category`, `terminal_outcome` | B-projector | 値が non-None なら元 document の key 有無にかかわらず挿入し、None なら削除する。typed control が authority である。 |
| 空 `lease_history` | B-projector | legacy passthrough に元 key があるか history が非空の場合だけ出力する。元 key がなく空 tuple の場合は追加しない。 |
| supersede の `mission`, `iteration`, review lineage | A-input | decode 可能な active document では実 typed state から decide する。synthetic decision は gate-only に限定し、transition を repository へ渡さない。 |

supersede-reviews の production 経路は、transaction 内で `repository.load()` した document を診断し
（`skills/mission/bin/mission-state.py:16161-16177`）、active の場合に
`real_terminalizable_state(state)` を呼ぶ（同 `:16185-16189`）。その実 state を `decide()` の入力にし、
accepted transition を repository へ渡す（同 `:16190-16206`）。terminal / 復号不能の場合だけ
`_mark_halt_decision_state(state)` の synthetic state で monotonic gate を評価するが、
`transition = None` に固定される（同 `:16190-16201`）。後続は load 済みの実 document を mutation / save
しており（同 `:16219-16248`）、synthetic state 自体を永続化する production 経路はない。
したがって supersede-reviews の identity / review lineage 差分は production バグではなく、比較 driver 固有の
A-input とする裁定を維持する。

## 3. typed 互換 payload と kernel reducer

### 3.1 採用案

command ごとに多数の可変 dict field を直接追加するのではなく、共通の immutable envelope を使う。

```python
@dataclass(frozen=True)
class CompatibilityPayload:
    upserts: FrozenJsonObject
    removals: tuple[str, ...] = ()
```

各 command dataclass に default-empty の `compatibility: CompatibilityPayload` を追加する。
ただし生 `halt_reason`、`pid`、`at` のように command 意味論または検証規則を持つ値は envelope に隠さず、
それぞれ `legacy_reason`, `new_pid`, `at` の専用 field とする。

採用理由:

- nested activity / timing / approval / audit document を deep immutable にできる。
- `SetExtensionFields` と同じ strict JSON codec を再利用でき、command encode の決定性を維持できる。
- command class ごとの allowlist と validator を kernel 内に置ける。
- application が任意の完成 document を渡す方式と違い、canonical control / identity / lease を上書きできない。

### 3.2 却下案

| 案 | 却下理由 |
|---|---|
| application が完成 legacy document または任意 patch を渡す | application が writer のままで、kernel は追認者になる。ADR-006 の目的を満たさない。 |
| command dataclass に全 nested field を個別展開する | activity / rollup / approval の schema を command layerへ重複定義し、既存 validator と二重化する。 |
| compatibility payload を `EvidenceEffect` として発行する | state 変更を外部 effect と誤分類する。ADR-005 の effect 発行主体と、V5 stage が `BlobBinding` だけを許す契約に反する。 |
| payload を `legacy_passthrough` だけへ反映する | v5 canonical state では `legacy_passthrough=None` なので互換 field が失われる。 |

### 3.3 reducer 共通処理

kernel に private helper を一つ置く。

```text
apply_compatibility(state, command_type, payload)
  1. FrozenJson / removals の型、duplicate、交差を検証
  2. command ごとの closed allowlist を検証
  3. control / identity / lease / evidence authority field を拒否
  4. extensions に upsert / remove
  5. legacy_passthrough がある場合は同じ upsert / remove
  6. canonical reducer の control 更新と合わせて new_state を返す
```

`phase`, `passes`, `loop_active`, `halt_category`, `terminal_outcome`, identity、lease field は
compatibility envelope から拒否する。これらは command の dedicated scalar と canonical reducer が所有する。
`halt_reason` だけは raw legacy contract のため `MarkHalt.legacy_reason` という専用入力で扱い、generic envelope には入れない。

application は既存互換 reducer を transaction 内の private shadow に一度だけ適用し、before/after delta を
command ごとの allowlist で閉じてから freeze する。これは外部 effect の宣言ではなく command の観測入力であり、
kernel は値・許可 field・input state との整合を検証した後に初めて state へ反映する。

repository は application が組み立てた `MissionState` を受け取らない。直前の `load()` で固定した source から
command ごとの pure decision-input builder を呼ぶ。builder は一般的な「補正 dict」ではなく、現行互換に必要な
次の narrow normalization だけを型で扱う。

- mark-pass: verified score index の元 entry が source と一致する場合だけ authoritative observation を束縛する。
- advance: prepared handoff の legacy iteration 表現を canonical plan iteration へ decision 用に正規化する。
- reactivate: pre-K2 の absent / malformed category を、明示された expected `unknown` と一致する場合だけ
  conservative `other` view にする。
- resume-stale: legacy stale/orphan marker と resume target を検証した場合だけ halted stale view にする。
- emergency halt: terminal / 復号不能 source は transition を作らず gate-only にする。

builder は source document digest を保持し、`new_state.legacy_passthrough` の起点が直前に load した document で
あることを repository が照合する。これにより synthetic view から identity / lineage を永続化する事故と、
application が別 state の transition を差し込む事故を同時に防ぐ。

### 3.4 command 別入力

| 経路 | command | 追加する typed 入力 | kernel での検証・反映 |
|---|---|---|---|
| mark-pass | `MarkPass` | `at`, verified score index/evidence observations、pass compatibility payload、force terminal binding | source score entry と observation を束縛して gate 判定後に timing/activity、`passes_forced`、force fields、waiver、early-stop observation を反映。force 時は approved digest と完成 new_state の terminal binding を純粋関数で照合してから transition を返す。 |
| advance | `AdvancePhase` | `at`, activity/timing/artifact compatibility payload | target phase は canonical reducer、artifact/handoff/timing/activity は closed payload。従来の validation 順序を shadow 構築時に維持する。 |
| mark-halt | `MarkHalt` | `at`, `legacy_reason`, halt compatibility payload | category/outcome/control は canonical reducer。raw reason、timing/activity、goal dispatch は専用検証後に反映。 |
| reactivate | `Reactivate` | `at`, reactivation audit、activity/timing payload | audit の previous values を input state と照合し、terminal field removal と history append を一つの new_state にする。 |
| resume-stale | `ResumeStale` | `at`, `new_pid`, activity/timing payload | pid 型、target と `resume_target_phase`、stale state を照合し、terminal field removalを含める。 |
| set | `SetExtensionFields` | `at`, derived compatibility payload | explicit kv は既存 `fields`、`updated_at` と review-tier/goal-route 由来の互換 field は payload。goal-route の場合は `MarkHalt` command を使う。 |
| permission observation | `MarkHalt` | `observed_at`, permission timing payload | `_PERMISSION_TRANSITION_FIELDS` の closed subset と shape を kernel で検証。probe は application の evidence であり state payload には入れない。 |
| supersede-reviews | `MarkHalt` / `SetExtensionFields` | `at`, terminal timing payload、`activity_anomaly_counts` を含む activity payload / `supersedes` | superseded active state は実 state で `MarkHalt`。current generation は `SetExtensionFields({supersedes})`。terminal/undecodable は gate-only。 |

### 3.5 kernel の純粋性と #622

payload の生成に I/O は入れない。host callback、clock、activity reducer、approval verifier、goal route の評価は
application adapter が先に完了し、kernel へは immutable value だけを渡す。kernel reducer は
`(MissionState, Command) -> Transition` の値変換だけを行う。

#622 は issued-transition registry と decide-replay が非包含であると裁定した。registry は
入力 provenance と legacy claims の before を、decide-replay は出力 drift を担当する。
#644 の 8 経路は repository が transaction 内で保持した exact loaded state と command から
`decide()` する形へ移し、caller-supplied transition を受けない。これにより claims before 依存も消える。
ただし public `LocalFencedRepository.stage()` 等の他 production surface が caller-supplied transition を
受けるため、registry の削除条件は repository 全体ではまだ全充足しない。本 Issue では次を守る。

- `_ISSUED_TRANSITIONS`, `is_sealed_transition`, exact input binding を削除しない。
- `transition_control_claim_bounds()` / `_apply_transition_claims()` は、8 経路と legacy repository からの
  参照がゼロになった時点で削除する。
- registry の最終削除は、全 production 経路が repository 内 decide へ移ったことを別途実測してから行う。

## 4. repository 契約と #632 境界の簡素化

### 4.1 新しい契約

`LegacyMissionRepository.execute` の legacy overload から `state`, `mutation`, `transition`, `finalize` を外し、
transaction 内で直前に `load()` した exact state と typed command から repository 自身が decide / project / write
する一段 API にする。application が transition を持ち込めないため、#622 が示した state / command
すり替えを API 上表現できない。

```python
@overload
def execute(
    self,
    command: Command,
    *,
    backup: bool = True,
    administrative: bool = False,
    aggregate_action: str | None = None,
) -> LegacyCommandExecutionResult: ...

@overload
def execute(self, request: ExecutionRequest) -> RepositoryExecutionResult: ...
```

`LegacyCommandExecutionResult` は `decision` と、実際に保存した projection の defensive copy を返す。
legacy command overload は次を一回の呼び出しで行う。

1. active transaction 内で `load()` が一度だけ成功していることを検証する。
2. v4 はその exact loaded bytes と command を pure decision-input builder へ渡し、v5 compatibility は
   admitted state と同じ command を使う。
3. command に含まれる verified observation と compatibility payload を検証して `decide()` する。
4. reject なら write / aggregate action なしで closed rejection を返す。
5. accepted なら `project_legacy_document(transition.new_state)` を生成する。
6. v4 は backup / write、v5 compatibility は stage / commit する。
7. aggregate action を実行し、decision と実保存 projection を返す。

`save(document, ...)` は、transition を持たない Batch 3 未移行 command と、#632 で gate-only と裁定した
terminal / 復号不能 emergency branch のために残す。これらの application reducer は callback として渡さず、
transaction 内で shadow document を完成してから `save()` する。

### 4.2 `_pending` と claims

現行は `execute()` と `save()` が分離しているため、`_PendingDecision` が document identity と claims を保持する。
新契約では decide、transition の射影、write が同じ `execute()` 内で完了するため、次を削除できる。

- `_PendingDecision`
- `LegacyV4Repository._pending`
- `V5CompatibilityRepository._pending`
- `_apply_transition_claims()`
- `_verify_transition_claims()`
- transaction exit 時の pending clear
- finalizer divergence / save-target identity の claims 専用分岐

write 前後に caller が transition や mutable dict を差し替える窓がなくなるため、pending receipt への置換も不要である。

### 4.3 `_callback_guard`

`_callback_guard` 自体は削除しない。repository へ注入される次の callable / context manager は引き続き
外部 trust boundary だからである。

- `_format_guard`, `_clock`, `_read_state`, `_backup_state`, `_write_state`
- `_add_to_aggregate`, `_remove_from_aggregate`, `_lock`, `_effect_transaction`
- v5 の `_prepare_state` を廃止するまでの移行期間に限る metadata producer
- `execute_effects()` の evidence decision / publication callbacks

一方、`mutation` / `finalize` の実行を囲む branch と、それらからの再入を検証するテストは削除する。
guard は「repository に注入された I/O callback から repository entry point へ再入させない」という
単一責務に縮小し、`LegacyV4Repository` と `V5CompatibilityRepository` の両クラスで
`_executing` を `_callback_depth` のような実態を表す名前へ変更する。

### 4.4 finalizer と force pass

`finalize` callback は削除する。force pass は次へ置き換える。

1. application が現行と同じ terminal binding を作り、approval verifier を実行する。
2. verification envelope、expected digest、score/findings evidence 検証結果、`consumed=true` の保存値を
   deep-freeze して `MarkPass` に渡す。application が typed state を差し替えて authority を作らない。
3. kernel は pass gate を評価し、完成した new_state から既存
   `mission-force-terminal-state/1` と同じ subset を純粋に計算する。
4. expected digest と不一致なら transition を返さず reject する。
5. 一致時だけ `force_approval.consumed=true` を含む new_state を返す。

これにより「claims 適用後に digest を照合する」という #632 の保証は、
「完成 new_state を返す前に kernel が照合する」というより強い境界へ移る。
pass gate の threshold / open_high / findings evidence / agreement / artifact / specialist / force approval の
論理積は変更しない。

### 4.5 v5 compatibility の metadata

現行 `_prepare_state=stamp_metadata` は decision 後、save 直前に document を変更する。
射影を唯一の writer にするとこの後置き変更は許可できない。stamp が変更する field を transaction 内で先に採取し、
command compatibility payload へ含める。`_prepare_state` は identity 関数へ固定した後に constructor 引数ごと削除する。

実コード上、`stamp_metadata` が欠損時に書く候補 field は次の 9 個である
（`skills/mission/bin/mission-state.py:1944-1960`）。

| field | 現行の書込み条件 |
|---|---|
| `schema_version` | key 欠損時に `SCHEMA_VERSION` を `setdefault` |
| `project_root` | key 欠損時に解決済み cwd を `setdefault` |
| `pid` | key 欠損時だけ `find_agent_pid()` の結果を書込む |
| `pid_source` | `pid` 欠損 branch で `pid` と同時に書込む |
| `hostname` | key 欠損時に hostname を `setdefault` |
| `session_id` | key 欠損時だけ `resolve_session_id()` の結果を書込む |
| `agent` | key 欠損時だけ `resolve_agent()` の結果を書込む |
| `created_at_session` | key 欠損時に `iso_now()` を `setdefault` |
| `cli_version` | key 欠損時に `MISSION_CLI_VERSION` を `setdefault` |

v5 repository にはこの関数が `_prepare_state` として注入されている
（同 `:9608-9611`）。59 leaf の A-payload / B-projector field と候補キーを比較すると、
B-projector との交差はなく、名前上の交差は A-payload の `pid` 1 個だけである。ただし
ResumeStale の `pid` は source に存在し、command の `new_pid` 反映後も key が存在するため、
`stamp_metadata` の欠損時 branch は実行されない。残る 8 field は59 leafの A-payload / B-projector と
名前上も交差しない。したがって projection を対象とする実効書込み集合では重複せず、metadata payload は
既存 key を上書きしない set-if-absent 契約、ResumeStale の `pid` は専用 `new_pid` の authority として分離する。

v5 path は `transition.new_state` から生成した bytes を直接 stage し、application が作った別 dict を stage しない。
v4/v5 の両 repository で同じ transition と projection を使うことを回帰テストで固定する。

## 5. 実装・PR 計画

### 5.1 PR 分割の裁定

**デフォルトは次の二分割とする。** #632 第二段は今回より小さい変更でも異系統レビューに 8 巡を要したため、
projector の独立欠陥を先に閉じ、typed payload と repository 契約変更のレビュー面積を分離する。

1. **PR1: golden + projector 修正**
   - 8 経路の `current saved == checked-in golden` と
     `current saved == project(new_state)` の failing comparison を追加する。
   - projector の `halt_category` / `terminal_outcome` / empty `lease_history` を修正する。
   - callback / repository 契約は維持したまま、projector 単独の golden を green にする。
2. **PR2: typed payload + atomic cutover**
   - `CompatibilityPayload` と command ごとの validator / reducer を追加する。
   - 8 経路すべてを repository 内 decide の一段 `execute(command, ...)` へ一括切替する。
   - mutation / finalizer / claims / pending を削除し、non-transition / gate-only caller を callback なしの
     `save(document)` へ機械的に移す。
   - source/plugin mirror と full regression を通す。

port signature、`_pending`、v5 compatibility は全経路で共有されるため、PR2 の内部を経路単位には分割しない。
経路別 PR にすると中間 PR が旧 callback と新射影の二重契約を持ち、claims と pending を削除できないためである。

レビュー進捗が良好で、PR1 の projector 修正と PR2 の atomic cutover を同一 head で扱う方が明確だと
Checker と合意できた場合に限り、1 PR 統合を最適化として選べる。その場合も上記二境界を論理 commit で維持する。

### 5.2 変更対象

正典側:

- `skills/mission/lib/mission_kernel/commands.py`
- `skills/mission/lib/mission_kernel/transitions.py`
- `skills/mission/lib/mission_kernel/codec_v4.py`
- `skills/mission/lib/mission_application/ports.py`
- `skills/mission/lib/mission_application/review.py`
- `skills/mission/lib/mission_application/lifecycle.py`
- `skills/mission/lib/mission_application/runtime_guard.py`
- `skills/mission/lib/mission_persistence/legacy_v4.py`
- `skills/mission/bin/mission-state.py`
- `skills/mission/tests/test_issue644_projection_is_the_writer.py`（新規）
- 必要な既存 regression tests / fixtures

`LegacyCommandExecutionResult` への戻り値変更に伴い、8 経路で legacy overload の返り値を現在
`proposed` dict として使用している caller も変更対象に含める。具体的には
`lifecycle.py:592,731,881,984,1347`、`review.py:518-524`、`runtime_guard.py:477-485` の各使用箇所で、
保存 projection は result の defensive copy、decision は result の typed field から取得する。
Batch 3 未移行の `lifecycle.py:326,342,1016` はこの result の caller にはせず、§4.1 のとおり完成 document を
`save(document)` する経路へ移す。

配布 mirror は同じ相対 path を `plugins/mission/skills/mission/**` に同期する。
設計時点で上記 9 production file はすべて source/plugin byte-identical である。

## 6. TDD テストリストと受け入れ条件

### 6.1 Red: golden / projector

1. 8 driver それぞれで現行保存 document が checked-in golden と一致する。
2. 8 driver それぞれで `project_legacy_document(decision.transition.new_state)` が同じ golden と
   全 key/value 一致する。corpus-backed 2 経路の環境由来 field は同一実行内の current-vs-projection を比較し、
   fixture placeholder へは逃がさない。この比較では command の `at` 値と、現行 mutation 内で呼ばれる clock が
   同一結果を返すよう固定し、clock の差を projection 差分として数えない。
3. canonical JSON bytes も一致する。
4. 59 leaf のうち一つを改変した合成 fixture が必ず失敗する。
5. 元 document に key がなくても non-None の `halt_category` / `terminal_outcome` を挿入する。
6. Reactivate / ResumeStale の None 値は key 削除になる。
7. 元 key なし・empty lease history は `lease_history: []` を追加しない。
8. 元 key あり、または history 非空なら lease history を保存する。

### 6.2 Red: typed payload

9. `CompatibilityPayload` は nested list/dict を deep-freeze し、encode/decode が決定的である。
10. duplicate key、upsert/remove 交差、unknown field、non-finite number を拒否する。
11. `phase`, `passes`, `loop_active`, `halt_category`, `terminal_outcome`, identity、lease の generic upsert を拒否する。
12. command ごとの allowlist を別 command の payload で使えない。
13. `MarkHalt.legacy_reason.strip() == reason`、空 raw reason の transition 禁止を固定する。
14. `ResumeStale.new_pid` の bool / 0 / 負数を拒否する。
15. reactivation audit の previous values が input state と不一致なら拒否する。
16. permission timing payload は現行 closed field set と shape 以外を拒否する。
17. `SetExtensionFields` の explicit kv、`supersedes`、`custom_note` が extensions / legacy passthrough の両方に残る。

### 6.3 Red: 8 経路の意味論

18. mark-pass normal / force / specialist waiver / early-stop success / observation failure の保存値が現行 golden と一致する。
19. force approval の terminal digest が完成 new_state と一致し、`consumed=true` になる。
20. force digest 不一致では write / aggregate remove が一度も走らない。
21. pass gate の score-required / authoritative score / open-high / threshold / min-item / agreement / artifact /
    specialist / terminal-state rejection code と write 回数が不変である。
22. advance の phase timing、activity close/start、artifact applicability、handoff、validation 順序が不変である。
23. halt category 9 種 × session role 5 種の terminal outcome と全保存 document が不変である。
24. raw halt reason、routed-goal dispatch、`set_terminal_phase=False` の gate-only behavior が不変である。
25. reactivate の承認、category 照合、audit append、activity restart、aggregate add が不変である。
26. resume-stale の pid、resume target removal、activity/timing、aggregate add が不変である。
27. set の generic kv、review-tier derivation、goal route の単回評価、warning 順序が不変である。
28. permission observation の active real-state transition と terminal / undecodable gate-only が不変である。
29. supersede-reviews の active real-state transition、current `supersedes` index、terminal / undecodable gate-only、
    rollback 順序が不変である。

### 6.4 Red: repository / #632 / #622

30. `LegacyMissionRepository.execute` の legacy overload に `mutation` / `finalize` parameter がない。
31. production AST に `repository.execute(state, mutation, ...)` が 0 件である。
32. command execute は exact loaded state から decide / projection / write を一度だけ行い、caller が
    transition または別 document を差し込めない。
33. write 失敗時に aggregate action は走らず、aggregate 失敗時の既存 `AggregateIndexError` 契約を維持する。
34. `_PendingDecision`, `_pending`, `_apply_transition_claims`, `_verify_transition_claims` が存在しない。
35. callback guard inventory は残る injected I/O / effect callback を全件列挙し、そこからの
    `execute` / `save` / `execute_effects` 再入を拒否する。
36. mutation / finalizer 再入テストは削除し、残存 callback の合成再入テストへ置き換える。
37. 8 経路の repository API が caller-supplied transition を受けないことを signature / AST で固定する。
38. 残存する public transition surface の sealed transition / exact input provenance registry test は維持し、
    forged transition と異なる input 由来 transition の拒否能力を弱めない。

### 6.5 v4/v5・配布・全体 gate

39. 同じ command/input で v4 と v5 compatibility の保存 projection が一致する。
40. v5 の metadata stamp は command payload に含まれ、decision 後の `_prepare_state` drift がない。
41. v5 replay、lease admission、single commit、aggregate once の回帰が green である。
42. missing/v1-v4 decode→command→project の互換 corpus が green である。
43. schema v5 encode/decode、unknown key、extension round-trip が green である。
44. source/plugin の recursive inventory と対象 file の byte identity が green である。
45. artifact hygiene / neutral vocabulary scan が green である。
46. repository full suite が green である。

### 6.6 Issue #644 の受け入れ条件への対応

| Issue 条件 | 本設計の完了判定 |
|---|---|
| 8 経路 golden comparison | §2 の実測を Red fixture として固定し、実装後は 8/8 object / canonical bytes 一致。 |
| 差分 field の個別裁定 | §2.3 の 59 leaf と §2.4 の field 群裁定に未裁定がない。 |
| callback 削除と port 更新 | §4.1 の signature、§4.2 の claims/pending 削除、AST 0 件。 |
| pass gate 不変・v4/v5 回帰 | §6.3 の gate matrix と §6.5 の両形式テストが green。 |
| source/plugin mirror | recursive inventory と対象 file byte identity が green。 |

実装開始 gate は、追加する 8 経路 comparison が現行 main で期待どおり Red になり、
本書の 59 leaf と一致することである。差分ゼロを確認するまでは mutation callback を削除しない。
