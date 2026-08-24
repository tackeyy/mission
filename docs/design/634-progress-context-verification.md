# 設計書: progress / context-manifest / verification record の kernel 経由化（#634）

> 調査基点: `1454997`（`origin/main` と一致）。本書は調査・設計のみであり、実装を含まない。
> 実装着手は #633 の effect / 写像 API と #644 の transition writer 契約が main に入った後とする。
> #633 自身も #632 / #644 後を着手条件にしている（`docs/design/633-artifact-kernel-commands.md:3-4`）。

## 結論

対象は parser の `set_defaults` から、`progress update`、`progress clear`、
`context-manifest`、`verification record` の 4 command と確定した
（`skills/mission/bin/mission-state.py:18828-18846`,
`skills/mission/bin/mission-state.py:18999-19016`）。実測の結果、**4/4 を昇格**し、降格は 0 件とする。

- `progress update` は session state の `progress` と `updated_at` を書き、progress archive を 1 件公開する
  （`skills/mission/lib/mission_application/artifact.py:369-410`）。
- `progress clear` は session state から `progress` を削除し、`updated_at` を書く。公開 effect はない
  （`skills/mission/lib/mission_application/artifact.py:413-417`）。
- `context-manifest` は session state の `context_manifests[iteration]` を書き、manifest JSON を 1 件公開する。
  top-level `updated_at` は書かない（`skills/mission/lib/mission_application/artifact.py:420-465`,
  `skills/mission/bin/mission-state.py:15202-15214`）。
- `verification record` は session state の `verification_history` に 1 件 append し、`updated_at` を書く。
  公開 effect はない（`skills/mission/bin/mission-state.py:15517-15542`）。
- 4 command はいずれも `phase` / `passes` / `loop_active` / `halt_reason` / `halt_category` /
  `terminal_outcome` / `score_history` を変更しない。`context-manifest` だけが `score_history` を manifest の
  入力として読む（`skills/mission/lib/mission_application/artifact.py:437-461`）。

したがって「read-only command へ降格し no-write AST guard だけを置く」対象はない。一方で、4 command に
completion authority は与えない。各 reducer は evidence observation の専用 field だけを所有し、完了隣接
7 field の同値を property test で固定する。

## 前提とスコープ

ADR-006 の Batch 3 は evidence command を残り family の先頭に置き、全 mutation が `decide()` を通り
`transition.new_state` が保存される到達点を要求する
（`docs/adr/006-kernel-reducer-adjudication.md:80-100`,
`docs/adr/006-kernel-reducer-adjudication.md:126-142`）。本書は #633 で確定した次の契約をそのまま前提にする。

1. bytes は kernel command に入れず、kind / target / digest / size の immutable claim だけを入れる
   （`docs/design/633-artifact-kernel-commands.md:173-192`）。
2. preparer と reducer は 1 個の pure projection helper を共有し、application 側に第 2 reducer を残さない
   （`docs/design/633-artifact-kernel-commands.md:263-279`）。
3. lease admission、claim と実 effect の照合、公開、公開物検証、
   `project_legacy_document(transition.new_state)` の保存、という順序を守る
   （`docs/design/633-artifact-kernel-commands.md:292-325`）。
4. 正規な v1-v4 state の wire / CLI / evidence bytes を維持し、v5 schema activation は行わない。現行 v5 schema は
   closed top-level で、対象 aggregate を持たない
   （`docs/design/633-artifact-kernel-commands.md:40-55`,
   `skills/mission/lib/mission_kernel/codec_v5.py:75-87`,
   `skills/mission/lib/mission_kernel/codec_v5.py:539-582`）。

スコープは 4 command の typed command、pure reducer、effect binding、CLI adapter、正規な v1-v4 persistence parity、
source/plugin mirror に限定する。#592 の測定式、pass gate、review tier、v5 model / codec は変更しない。

### legacy decode parity の明示裁定

`context_manifest()` の現行 pure helper は `score_history` の非 mapping entry を読み飛ばすが、kernel の v4 decoder は
全 entry を `_object()` に通すため、同じ非 mapping entry を `MissionState` 生成前に拒否する
（`skills/mission/lib/mission_application/artifact.py:437-447`,
`skills/mission/lib/mission_kernel/codec_v4.py:545-552`）。また、projection は typed score の payload を object として
再構成する設計である（`skills/mission/lib/mission_kernel/codec_v4.py:825-848`）。したがって「decoder を先に通しながら
非 mapping entry を読み飛ばす」という両立不能な parity は要求しない。

#634 では、**正規な v1-v4 document は byte / state parity、decoder が既に拒否する malformed legacy document は
effect / state write 前の fail-closed rejection** と裁定する。任意 JSON scalar を lossless に保持できる score model / codec へ
広げる変更は、context command の authority 移行ではなく legacy codec migration なので本 Issue に含めない。この例外は
既存 helper の単体互換性を削除する根拠にはせず、helper test では skip 挙動を維持し、kernel 経路 test では decoder rejection と
publisher 未呼出しを固定する。

## 実測方法

1. `_build_parser` の各 `set_defaults(func=...)` から command 名と実装関数を確定した
   （`skills/mission/bin/mission-state.py:18828-18846`,
   `skills/mission/bin/mission-state.py:18999-19016`）。
2. CLI から application decision または inline state mutation まで追跡した
   （`skills/mission/bin/mission-state.py:7768-7840`,
   `skills/mission/bin/mission-state.py:15181-15218`,
   `skills/mission/bin/mission-state.py:15493-15542`）。
3. completion 隣接 7 field をすべて持つ合成 v4 state に各 decision を適用し、top-level diff と effect kind を
   取得した。結果は `progress update={progress, updated_at}/effect=progress`、
   `progress clear={progress absence, updated_at}/effect=0`、
   `context-manifest={context_manifests}/effect=context-manifest`、
   `verification record={verification_history, updated_at}/effect=0` で、7 field の差分は全件空だった。
   この結果は各代入箇所と既存 application test に一致する
   （`skills/mission/lib/mission_application/artifact.py:369-465`,
   `skills/mission/bin/mission-state.py:15523-15541`,
   `skills/mission/tests/test_issue508_a3_application.py:317-347`）。
4. state 書込みとは別に、effect の公開先、lease-first、rollback、consumer 側の検証を追った
   （`skills/mission/bin/mission-state.py:7516-7602`,
   `skills/mission/lib/mission_persistence/legacy_v4.py:347-383`,
   `skills/mission/tests/test_issue508_a3_application.py:172-243`）。
5. `verification_history` の writer と #592 の consumer / tests / 事前登録条件を突合した
   （`skills/mission/bin/mission-state.py:15462-15542`,
   `skills/mission/lib/mission_gate_outcome.py:200-250`,
   `docs/PRE_REGISTRATION.md:71-81`）。

## 4 command の実測表

「書込み先」は session state、分離 aggregate、公開 evidence を区別する。progress archive と manifest JSON は
rollback 対象の file effect であり、独立した administrative aggregate ではない。現行 repository は effect を
検証・公開した同じ transaction 内で state を保存する
（`skills/mission/lib/mission_persistence/legacy_v4.py:347-383`）。

| command | parser → 実装 → decision | session state に書く / 削除する field | 分離 aggregate / 公開 effect | 完了隣接 7 field | 裁定 |
|---|---|---|---|---|---|
| `progress update` | `set_defaults` → `cmd_progress_update` → `progress_update`（`skills/mission/bin/mission-state.py:19001-19010`, `skills/mission/bin/mission-state.py:7768-7805`, `skills/mission/lib/mission_application/artifact.py:369-410`） | `progress` を全置換: `kind=batch`, `total`, `completed`, `remaining`, `batch_size`, `last_unit`, `artifact_path`, `updated_at`, `evidence_path`; top-level `updated_at`（`skills/mission/lib/mission_application/artifact.py:396-410`） | 分離 aggregate なし。`.mission-state/archive/iter-{iteration}-{mission_id[:8]}-progress.md` に `progress` effect 1 件（`skills/mission/bin/mission-state.py:7761-7765`, `skills/mission/lib/mission_application/artifact.py:351-366`, `skills/mission/lib/mission_application/artifact.py:407-410`） | 言及・変更なし。既存 CLI test も `loop_active=True` / `passes=False` の維持を確認（`skills/mission/tests/test_progress_checkpoints.py:17-40`） | **昇格** |
| `progress clear` | `set_defaults` → `cmd_progress_clear` → `progress_clear`（`skills/mission/bin/mission-state.py:19014-19016`, `skills/mission/bin/mission-state.py:7824-7840`, `skills/mission/lib/mission_application/artifact.py:413-417`） | `progress` を存在すれば削除、top-level `updated_at`（`skills/mission/lib/mission_application/artifact.py:413-417`） | 分離 aggregate / effect ともになし（`skills/mission/lib/mission_application/artifact.py:417`） | 言及・変更なし（`skills/mission/lib/mission_application/artifact.py:413-417`） | **昇格** |
| `context-manifest` | `set_defaults` → `cmd_context_manifest` → `context_manifest`（`skills/mission/bin/mission-state.py:18828-18834`, `skills/mission/bin/mission-state.py:15181-15218`, `skills/mission/lib/mission_application/artifact.py:420-465`） | 既存 mapping を保持して `context_manifests[str(iteration)]={path,digest,generated_at}` を upsert。top-level `updated_at` は不変（`skills/mission/lib/mission_application/artifact.py:457-465`, `skills/mission/bin/mission-state.py:15212-15214`） | 分離 aggregate なし。`--out` の path に `context-manifest` effect 1 件。JSON は schema / iteration / mission goal / mission id / assumptions path / prior findings（`skills/mission/lib/mission_application/artifact.py:437-465`） | `score_history` を読み、mapping の finding だけを manifest に copy するが変更しない。他 6 field は言及なし（`skills/mission/lib/mission_application/artifact.py:437-455`） | **昇格** |
| `verification record` | `set_defaults` → `cmd_verification_record`（inline mutation）（`skills/mission/bin/mission-state.py:18836-18846`, `skills/mission/bin/mission-state.py:15493-15542`） | `verification_history` を list として取得し `{iteration,status,checks,failed_count,recorded_at}` を 1 件 append、top-level `updated_at=recorded_at`。非 list の既存値は空 list として置換（`skills/mission/bin/mission-state.py:15523-15541`） | 分離 aggregate / effect ともになし（`skills/mission/bin/mission-state.py:15530-15542`） | command 本体は 7 field を変更しない。失敗 verification でも command は停止させず gate に委ねる（`skills/mission/bin/mission-state.py:15493-15501`, `skills/mission/tests/test_issue594_verification.py:62-64`） | **昇格** |

## 完了隣接 field の裁定

| field | `progress update` | `progress clear` | `context-manifest` | `verification record` |
|---|---|---|---|---|
| `phase` | 不変 | 不変 | 不変 | 不変 |
| `passes` | 不変 | 不変 | 不変 | 不変。#592 consumer が後で読むだけ |
| `loop_active` | 不変 | 不変 | 不変 | 不変 |
| `halt_reason` | 不変 | 不変 | 不変 | 不変 |
| `halt_category` | 不変 | 不変 | 不変 | 不変 |
| `terminal_outcome` | 不変 | 不変 | 不変 | 不変 |
| `score_history` | 不変 | 不変 | **読みのみ** | 不変 |

progress / context の pure decision が変更する field は application 実装に閉じており、既存 test も phase / score の
不変を固定する（`skills/mission/lib/mission_application/artifact.py:369-465`,
`skills/mission/tests/test_issue508_a3_application.py:317-347`）。verification record は
`verification_history` / `updated_at` だけを代入する
（`skills/mission/bin/mission-state.py:15523-15541`）。#592 の false-negative 集計が `passes` を読むのは
別の read-only consumer であり、writer に completion authority があることを意味しない
（`skills/mission/lib/mission_gate_outcome.py:217-250`,
`scripts/mission-audit.py:4048-4053`）。

## #592 verification-backed completion gate との境界

### 衝突の有無

**writer の配線は衝突領域だが、contract の意味変更は不要である。** #634 は
`verification_history` を direct dict mutation から kernel reducer へ移すため同じデータを触る。一方、#592 の
測定器は state の append 済み record を読むだけなので、下記 contract を完全保存すれば測定を継続できる
（`skills/mission/bin/mission-state.py:15523-15541`,
`skills/mission/lib/mission_gate_outcome.py:200-250`）。

| 保存する contract | 一次証拠 | #634 の扱い |
|---|---|---|
| input は JSON object。`checks` 省略は空 list、非 list は拒否。各 check の `ok` は明示 bool 必須 | `skills/mission/bin/mission-state.py:15462-15490`; `skills/mission/tests/test_issue594_verification.py:67-86` | adapter/application normalization を parity 固定。kernel 化に便乗して schema を厳格化しない |
| check name は非空文字列なら保持、それ以外は `check-{index}`。detail は文字列だけ保持し、それ以外は `None` | `skills/mission/bin/mission-state.py:15475-15490` | normalized typed `VerificationCheck` に同値写像 |
| status は checks なし=`not-run`、1 件以上の failed あり=`failed`、それ以外=`passed` | `skills/mission/bin/mission-state.py:15508-15515`; `skills/mission/tests/test_issue594_verification.py:45-71` | reducer が checks から再導出。caller 申告の status / failed_count は受けない |
| history は append-only。同 iteration の再記録も置換せず、後の record が後ろに来る | `skills/mission/bin/mission-state.py:15535-15540`; `skills/mission/tests/test_issue594_verification.py:89-94` | dedupe / sort / overwrite / compaction をしない |
| entry は `iteration,status,checks,failed_count,recorded_at`、同じ timestamp を top-level `updated_at` に使う | `skills/mission/bin/mission-state.py:15522-15540` | field 名・型・timestamp cardinality を変えない |
| verification failure 自体は command failure や halt にしない | `skills/mission/bin/mission-state.py:15499-15501`; `skills/mission/tests/test_issue594_verification.py:62-64` | reducer は failed entry を通常 accepted transition として返す |
| verification は pass gate の式を変えない | `skills/mission/tests/test_issue594_verification.py:116-126`; `skills/mission/SKILL.md:110` | `passes` / score / findings / agreement の reducer と tests に触らない |
| FN 集計は history 中で最後に現れる `passed|failed` を使い、`not-run` だけなら測定不能。`passes=True && latest.status=failed` を FN と数える | `skills/mission/lib/mission_gate_outcome.py:200-250`; `skills/mission/tests/test_issue594_verification.py:150-192` | iteration 結合、latest の定義、母数を #634 で「改善」しない |
| audit は `summarize_states` と `false_negative_summary` を read-only に接続する | `scripts/mission-audit.py:4048-4053` | `scripts/mission-audit.py` / `mission_gate_outcome.py` は変更対象外 |
| gate 精度の公表条件は classifiable 20、FN は measured かつ verification 20 | `docs/PRE_REGISTRATION.md:71-81` | 閾値・母集団・公表条件を変更しない |

### 本 Issue で触らない範囲

- `skills/mission/lib/mission_gate_outcome.py` の FN/FP 分類と `scripts/mission-audit.py` の集計配線。
  現行は測定器として独立している（`skills/mission/lib/mission_gate_outcome.py:13-14`,
  `scripts/mission-audit.py:4048-4053`）。
- `docs/PRE_REGISTRATION.md` の数値条件（`docs/PRE_REGISTRATION.md:71-81`）。
- `skills/mission/SKILL.md` の verification 実行順、payload、gate 不変の説明
  （`skills/mission/SKILL.md:108-112`）。
- verification による review tier の出し分け、pass/halt の追加、history の pruning / migration / backfill。
  現行 contract は「記録して gate の入力を増やすが gate の式は変えない」ことを固定している
  （`skills/mission/bin/mission-state.py:15493-15501`,
  `skills/mission/tests/test_issue594_verification.py:116-126`）。
- payload の `schema` 値の新規強制、iteration の正数化、latest record の iteration 別集計。現行 normalizer は
  payload object / checks / explicit bool だけを検証し、CLI parser は iteration を単なる required int として受ける
  （`skills/mission/bin/mission-state.py:15462-15490`,
  `skills/mission/bin/mission-state.py:18841-18846`）。測定中の #592 と同じ PR で wire contract を狭めない。

## command dataclass と effect claim

`commands.py` の frozen dataclass / closed `Command` union / canonical encoder へ 4 command と
`VerificationCheck` を追加する。encoder は nested dataclass と tuple を既に canonical JSON へ変換できる
（`skills/mission/lib/mission_kernel/commands.py:14-64`,
`skills/mission/lib/mission_kernel/commands.py:131-185`）。

```python
@dataclass(frozen=True)
class VerificationCheck:
    name: str
    ok: bool
    detail: Optional[str]


@dataclass(frozen=True)
class ProgressEffectClaim:
    kind: str
    target: str
    digest: str
    size: int


@dataclass(frozen=True)
class ContextManifestEffectClaim:
    kind: str
    target: str
    publication_path: str
    digest: str
    size: int


@dataclass(frozen=True)
class UpdateProgress:
    at: str
    total: int
    completed: int
    batch_size: Optional[int]
    last_unit: Optional[str]
    artifact_path: Optional[str]
    iteration: int
    effect: ProgressEffectClaim


@dataclass(frozen=True)
class ClearProgress:
    at: str


@dataclass(frozen=True)
class GenerateContextManifest:
    at: str
    iteration: int
    effect: ContextManifestEffectClaim


@dataclass(frozen=True)
class RecordVerification:
    at: str
    iteration: int
    checks: tuple[VerificationCheck, ...]
```

iteration validation も wire parity を優先する。`UpdateProgress` は現行どおり 0 以上、
`GenerateContextManifest` は 1 以上を要求する一方、`RecordVerification` は `type(value) is int` だけを要求し、
0 / 負数を新たに拒否しない。verification parser は required `type=int` までしか課しておらず、writer に正数 gate はない
（`skills/mission/lib/mission_application/artifact.py:383-388`,
`skills/mission/lib/mission_application/artifact.py:428-431`,
`skills/mission/bin/mission-state.py:15523-15529`,
`skills/mission/bin/mission-state.py:18841-18846`）。この非対称を正す変更は #634 の migration scope 外とする。

#633 の `ArtifactEffectClaim` は artifact family 固有であり、同設計も repository に artifact 専用 executor を追加し、
progress / context の既存 executor は変更しないと明記している
（`docs/design/633-artifact-kernel-commands.md:294-300`）。よって #634 は型を流用したことにせず、同じ immutable
descriptor pattern に従う `ProgressEffectClaim` と `ContextManifestEffectClaim` を evidence family の意味型として追加する。
bytes は `EvidenceEffect` に残し、missing / extra / reorder / kind / target / digest / size mismatch を公開前に拒否する
（`docs/design/633-artifact-kernel-commands.md:175-192`）。

`UpdateProgress.effect` の型は上記 `ProgressEffectClaim` とし、その `target` は state の
`progress.evidence_path` と同値にする。context は現行で state に `str(--out)`、effect target に basename、publisher の
`path_overrides` に実 path という 3 箇所の値を渡している
（`skills/mission/bin/mission-state.py:15197-15214`,
`skills/mission/bin/mission-state.py:7543-7559`,
`skills/mission/lib/mission_application/artifact.py:432-460`）。これを
`ContextManifestEffectClaim(target=basename, publication_path=str(--out), ...)` に一度だけ束縛し、reducer が state record の
`path` を `publication_path` から作り、publisher も同じ claim だけから destination を決める。executor は
`Path(publication_path).name == target` と実 bytes の identity を公開前に検証し、caller の別 `path_overrides` は受け取らない。

## pure projection と transition table

`mission_kernel/evidence.py` に pure helper を 1 組だけ置き、preparer と reducer が共有する。

- `project_progress_update(state, command_without_claim) -> (progress, bytes)`:
  bounds / optional text / relative archive path を検証し、現行 Markdown bytes を生成する。現行 validation と bytes は
  `skills/mission/lib/mission_application/artifact.py:351-407` にある。
- `project_context_manifest(state, iteration, publication_path, at) -> (record, bytes)`:
  `score_history` / findings を現行どおり検証・copy し、canonical JSON bytes と record を生成する
  （`skills/mission/lib/mission_application/artifact.py:420-465`）。
- `project_verification_entry(at, iteration, checks) -> entry`:
  status / failed_count を checks から再導出し、caller が outcome を偽造できないようにする。現行導出は
  `skills/mission/bin/mission-state.py:15508-15529` にある。

| rule ID | command | reducer の決定 | event | state claim |
|---|---|---|---|---|
| `progress-update` | `UpdateProgress` | pure helper で progress / bytes identity を再構成し effect claim を検証。`progress` を全置換 | `progress-checkpoint-updated` | `progress` 全体、`updated_at` |
| `progress-clear` | `ClearProgress` | `progress` を存在すれば削除し `updated_at=at`。progress 不在でも accepted | `progress-checkpoint-cleared` | `progress` absence、`updated_at` |
| `context-manifest-generate` | `GenerateContextManifest` | pure helper で manifest bytes / digest を再構成し、既存 mapping を保持して iteration record を upsert | `context-manifest-recorded` | `context_manifests[str(iteration)]`; top-level `updated_at` は claim しない |
| `verification-record` | `RecordVerification` | normalized checks から status / failed_count を再導出し history 末尾へ 1 件 append | `verification-recorded` | `verification_history` append、`updated_at` |

state claim は reducer が完成させた `transition.new_state` の所有 field を表す。legacy v1-v4 では
`MissionState.legacy_passthrough` の該当 top-level field を pure helper の結果で置換し、repository は
`project_legacy_document(transition.new_state)` を保存する。codec v4 は passthrough を保存 carrier としつつ
typed control / score / lease を再射影する
（`skills/mission/lib/mission_kernel/model.py:470-483`,
`skills/mission/lib/mission_kernel/codec_v4.py:666-702`,
`skills/mission/lib/mission_kernel/codec_v4.py:851-940`）。

`_CLAIMABLE_CONTROL_FIELDS` 型の legacy control claims は増やさない。4 command は completion control を
所有せず、#633 後は transition 全体が保存値だからである
（`docs/design/633-artifact-kernel-commands.md:247-261`）。代わりに generic `set` が専用 command を迂回できないよう、
`progress` / `context_manifests` / `verification_history` を `GENERIC_SET_DEDICATED_FIELDS` に追加する。
generic reducer はこの閉集合への request を `dedicated-field` で拒否する
（`skills/mission/lib/mission_kernel/commands.py:97-128`,
`skills/mission/lib/mission_kernel/transitions.py:399-435`）。

## application / persistence / 公開順序

application 境界は `PreparedEvidenceOperation(command, effects, result)` を返す。context の実公開先は
`command.effect.publication_path` に閉じるため、prepared operation に別の path override carrier は持たせない。progress / context は
admitted current state から immutable bytes と typed command を準備し、clear / verification は空 effect tuple と
typed command を準備する。既存 `EvidenceDecision.state` を 4 command の保存値として使わず、CLI inline mutation も
残さない。現行の `EvidenceDecision.state` 保存と verification inline mutation はそれぞれ
`skills/mission/bin/mission-state.py:7582-7602` と
`skills/mission/bin/mission-state.py:15530-15541` にある。

effectful 2 command の必須順序は #633 と同じにする。

```text
lock
  -> load + fenced lease admission
  -> prepare progress/context bytes + typed command
  -> decode current MissionState
  -> decide(current, command)
  -> validate EvidenceEffect
  -> validate and bind transition effect claim
  -> begin effect transaction
     -> publish effect
     -> verify path / digest / size
     -> save project_legacy_document(transition.new_state)
  -> commit / close
```

現行 `_run_evidence_decision` も load 後に effect transaction を開始し、save failure では transaction が公開物を
rollback する（`skills/mission/lib/mission_persistence/legacy_v4.py:358-383`,
`skills/mission/tests/test_issue508_a3_application.py:172-243`）。ただし #633 の確定 API は artifact 専用であり、progress / context
への直接再利用を約束していない（`docs/design/633-artifact-kernel-commands.md:294-300`）。#634 は #633 merge 後に、そこで確立した
transaction core を `execute_evidence_transition_effects` という共通内部契約へ抽出し、artifact executor と本 Issue の
progress/context executor の双方から使う。#633 の `ArtifactEffectClaim` と artifact reducer の契約は変更せず、publisher / rollback
実装だけを重複させない。この一般化は #634 の明示的な persistence 変更であり、存在を前提にしない。

effectless 2 command は同じ typed execution から blob/effect 0 件で `decide()` し、
`transition.new_state` を保存する。verification input file / stdin の読み取りと JSON decode は state mutation ではないため
lock 前の adapter input capture として維持できるが、state の load / decision / save は transaction 内に閉じる。
現行 input capture と transaction の境界は `skills/mission/bin/mission-state.py:15503-15508` と
`skills/mission/bin/mission-state.py:15530-15541` にある。

## 降格 command と no-write AST guard

### 今回の判定

降格は **0 件**である。4 command はすべて session state の専用 field を実際に変更するため、production test として
「command は no-write」と宣言する AST guard は置かない
（`skills/mission/lib/mission_application/artifact.py:408-417`,
`skills/mission/lib/mission_application/artifact.py:458-465`,
`skills/mission/bin/mission-state.py:15535-15541`）。

代わりに次を固定する。

- reducer property test: 4 command の前後で completion 隣接 7 field が完全一致する。
- thin-adapter AST test: `cmd_progress_update` / `cmd_progress_clear` / `cmd_context_manifest` /
  `cmd_verification_record` に direct state assignment、`dict.update/setdefault/pop`、legacy
  `EvidenceDecision.state` 保存が残らない。
- dedicated authority test: generic `set` から `progress` / `context_manifests` /
  `verification_history` を書けない。

実装着手時の再計測で read-only へ変わっていた command だけは #619 / #620 型 guard へ切り替える。guard は
`Assign` / `AnnAssign` / `AugAssign` / `del`、`update` / `setdefault` / `pop`、state writer / transition helper の
間接 call を検出し、少なくとも次の合成違反 fixture で検出力を証明する。

```python
def offender(data):
    data["loop_active"] = False
    data.update({"verification_history": []})
```

既存 guard は合成 `loop_active` assignment が非空 violation になることを固定している
（`skills/mission/tests/test_issue620_kernel_a5_c1.py:138-208`）。ただし本書の実測結果のままなら、この条件分岐は
発動せず guard も追加しない。

## TDD テストリスト

Red を先に作り、次の behavior group ごとに Green にする。

1. `Command` union / canonical encoder が 4 command と nested `VerificationCheck` を受け、各 type が一意になる。
2. transition table に 4 rule が各 1 件だけ存在し、unknown / duplicate / ambiguous dispatch を拒否する。
   現行 table builder は duplicate rule / command を拒否する
   （`skills/mission/lib/mission_kernel/transitions.py:91-145`）。
3. 4 reducer が実測表どおりの state diff / event を返し、input `MissionState` は不変。
4. completion 隣接 7 field を多様化した property test で、4 reducer の前後値が完全一致する。
5. `progress update`: bounds、bool / wrong type、iteration、batch size、optional text、unsafe target を現行 code と同値に
   reject。progress 全置換、remaining、archive path、Markdown bytes、effect 1 件を golden 固定
   （`skills/mission/lib/mission_application/artifact.py:351-410`）。
6. `progress clear`: progress 有無の両方で accepted、effect 0、`progress` absence、`updated_at` 更新を固定
   （`skills/mission/lib/mission_application/artifact.py:413-417`）。
7. `context-manifest`: iteration 1 未満、非 list score history、非 list findings を reject。正規な mapping entry 内の
   非 mapping finding は現行どおり skip。legacy helper 単体では非 mapping history entry の skip を維持する一方、kernel 実経路では
   canonical decoder が同 entry を effect / state write 前に reject し、publisher 未呼出しを固定する。manifest schema /
   iteration / mission fields / prior findings / JSON bytes / digest は正規な state で golden 固定
   （`skills/mission/lib/mission_application/artifact.py:420-465`）。
8. context record は既存 iteration records を保持し、同じ iteration だけ upsertする。consumer が path / digest /
   generated_at と実 file を検証して bounded observation に使う現行 contract を維持
   （`skills/mission/tests/test_issue352_bounded_context_observability.py:80-120`,
   `skills/mission/tests/test_issue352_bounded_context_observability.py:168-228`）。
9. progress / context の effect claim と実 bytes の missing / extra / reorder / kind / target / digest / size mismatch、
   context の `Path(publication_path).name != target` を合成し、publisher が一度も呼ばれないことを示す。publisher が
   destination を claim 以外の引数から差し替えられないことも API test で固定する。
10. progress / context の foreign lease で effect transaction / publisher が一度も開かれず、state と output file が
    実行前 byte と一致する。既存 repository の load-before-effect test を 2 command の実経路へ拡張する
    （`skills/mission/tests/test_issue508_a3_application.py:172-201`）。
11. progress / context の effect publish、公開後 identity 検証、state save の各 failure で output を rollback し state を
    不変にする。既存 repository rollback 契約を維持する
    （`skills/mission/tests/test_issue508_a3_application.py:204-270`）。
12. verification adapter: malformed JSON、non-object、non-list checks、explicit bool 欠落を reject。name/detail normalization と
    passed / failed / not-run を固定（`skills/mission/tests/test_issue594_verification.py:45-86`）。kernel の `decide()` へ直接、
    list など tuple 以外の `checks`、`VerificationCheck` 以外の要素、`ok=1`、非文字列/空の `name`、文字列/`None` 以外の
    `detail`、非文字列/空の `at` を渡し、transition / effect なしで reject する。CLI normalizer が作る正規値は全件受理する。
13. verification は append-only、同 iteration の後勝ち、failed でも exit 0、entry 5 field、
    `updated_at == recorded_at` を固定（`skills/mission/tests/test_issue594_verification.py:45-94`,
    `skills/mission/bin/mission-state.py:15522-15542`）。
14. #592 protection suite: `test_gate_semantics_unchanged_by_verification_record` と false-negative 5 cases を一件も変更せず
    Green にする（`skills/mission/tests/test_issue594_verification.py:116-192`）。加えて consumer 実経路で
    `failed -> not-run` は最後の classifiable `failed` を維持し、`failed(iteration=1) -> passed(iteration=2)` は global append order の
    latest `passed` を選んで false negative にしないことを固定する。
15. payload schema / iteration の現在の acceptance surface を golden parity で固定する。unknown / missing schema と
    verification iteration 0 / 負数は現行 CLI と同じく受理し、bool / non-int の typed command は拒否する。#634 が
    暗黙の contract tightening を起こさない
    （`skills/mission/bin/mission-state.py:15462-15490`,
    `skills/mission/bin/mission-state.py:18841-18846`）。
16. `progress` / `context_manifests` / `verification_history` が generic `set` の dedicated field となり、各専用 command だけが
   変更できる。kernel/application/CLI の field 集合 drift test を維持する
    （`skills/mission/tests/test_issue617_kernel_a1_lifecycle.py:195-211`）。
17. kernel 化後の 4 `cmd_*` に direct state mutation と `EvidenceDecision.state` 保存がない。
18. current v5 head への既存非 activation / rejection parity を維持し、model / codec v5 に evidence aggregate を足さない
    （`skills/mission/lib/mission_kernel/codec_v5.py:75-87`,
    `skills/mission/lib/mission_kernel/codec_v5.py:539-582`）。
19. source/plugin の変更 production file が byte-identical。新規 Python module の mirror 欠落と byte drift は recursive
    inventory test で検出する（`skills/mission/tests/test_python_module_inventory.py:23-58`）。
20. focused suite、full suite、artifact hygiene、vendor fingerprint を通す。

## 受け入れ条件

- [ ] 実装着手時に parser → implementation → state/effect diff を再計測し、4/4 昇格と差がない。
- [ ] 4 command が専用 frozen dataclass と transition table を通る。
- [ ] application / CLI に state-producing `EvidenceDecision.state` の保存または verification inline mutation が残らない。
- [ ] repository が `project_legacy_document(transition.new_state)` だけを state 保存対象にする。
- [ ] effect cardinality が progress update=1、progress clear=0、context-manifest=1、verification record=0 で現行一致する。
- [ ] progress / context の claim と実 bytes / publish target が一対一に閉じ、lease admission 前に file / temp / backup を
      作らない。
- [ ] effect または state save failure で output file と state が all-or-none rollback される。
- [ ] progress / context / verification の CLI JSON、state document、progress Markdown、context JSON が正規な v1-v4 state で
      golden parity。非 mapping `score_history` entry は canonical decoder が公開・保存前に fail-closed reject する。
- [ ] completion 隣接 7 field は 4 command すべてで不変。
- [ ] `progress` / `context_manifests` / `verification_history` は generic `set` から書けない。
- [ ] #592 の `test_issue594_verification.py` が無変更で Green。status、append order、not-run、FN 母集団、pass gate を変えない。
- [ ] `mission_gate_outcome.py`、`scripts/mission-audit.py`、`docs/PRE_REGISTRATION.md`、`SKILL.md` は変更しない。
- [ ] v5 schema/model を変更せず、既存非 activation / rejection parity を維持する。
- [ ] `skills/mission/**` と `plugins/mission/skills/mission/**` の対応 production file が byte-identical。
- [ ] focused suite、full suite、artifact hygiene、vendor fingerprint がすべて Green。

## 変更対象ファイル

実装時の対象は次に限定する。source 変更は対応する plugin mirror へ同一内容を反映する。

- `skills/mission/lib/mission_kernel/commands.py`
- `skills/mission/lib/mission_kernel/transitions.py`
- `skills/mission/lib/mission_kernel/evidence.py`（progress / context / verification pure projection。新規）
- `skills/mission/lib/mission_application/evidence.py`（prepared operation / verification normalization。新規）
- `skills/mission/lib/mission_application/artifact.py`（既存 progress / context compatibility seam の委譲先変更）
- `skills/mission/lib/mission_persistence/legacy_v4.py`（#633 の artifact 専用 transaction core を evidence family からも使える
  共通内部契約へ抽出）
- `skills/mission/bin/mission-state.py`
- `skills/mission/tests/test_issue634_progress_context_verification.py`（新規）
- `skills/mission/tests/test_issue508_a3_application.py`
- `skills/mission/tests/test_progress_checkpoints.py`
- `skills/mission/tests/test_issue241_bounded_context.py`
- `skills/mission/tests/test_issue352_bounded_context_observability.py`
- `skills/mission/tests/test_issue594_verification.py`（削除・意味変更なし。必要なら parity assertion の追加だけ）
- `skills/mission/tests/test_issue617_kernel_a1_lifecycle.py`
- `plugins/mission/skills/mission/lib/mission_kernel/commands.py`
- `plugins/mission/skills/mission/lib/mission_kernel/transitions.py`
- `plugins/mission/skills/mission/lib/mission_kernel/evidence.py`（新規）
- `plugins/mission/skills/mission/lib/mission_application/evidence.py`（新規）
- `plugins/mission/skills/mission/lib/mission_application/artifact.py`
- `plugins/mission/skills/mission/lib/mission_persistence/legacy_v4.py`（source と同一変更）
- `plugins/mission/skills/mission/bin/mission-state.py`

`skills/mission/lib/mission_gate_outcome.py`、`scripts/mission-audit.py`、`docs/PRE_REGISTRATION.md`、
`skills/mission/SKILL.md`、`mission_kernel/model.py`、codec v5 は変更しない。#592 の測定 schema / consumer の変更、
v5 evidence aggregate、または上記 transaction core 抽出を越える persistence redesign が必要と判明した場合は、
#634 に黙って含めず別 Issue / 設計判断へ切り出す。

## 親 Issue #614 の表の更新案

設計完了時点では、親 Issue の「批 3 対象」直下へ次を追記する。

> **批3-a-2 実測結果（#634、2026-08-23 設計）**: A3 残り 4 command は
> `progress update` が progress state + archive effect、`progress clear` が progress removal、
> `context-manifest` が context_manifests state + manifest effect、`verification record` が
> verification_history append を行うため **4/4 昇格・降格 0**。完了隣接 7 field は全件不変で、
> context-manifest の score_history は読みのみ。verification writer は #592 の測定入力なので、
> status / append order / not-run / FN 集計 / pass gate を変更せず kernel reducer へ写像する。

上記の command 対応は parser と writer の実測、#592 境界は writer と consumer の突合に基づく
（`skills/mission/bin/mission-state.py:15493-15542`,
`skills/mission/bin/mission-state.py:18828-18846`,
`skills/mission/bin/mission-state.py:18999-19016`,
`skills/mission/lib/mission_application/artifact.py:369-465`,
`skills/mission/lib/mission_gate_outcome.py:200-250`）。

実装 merge 後は子 Issue checklist を次へ更新する。

> - [x] #634 批3-a-2: progress / context-manifest / verification record — PR #<N> で完了
>   （4/4 kernel transition 化、#592 measurement contract 不変、source/plugin mirror）

## リスクと出口戦略

| リスク | 制御 | 出口戦略 |
|---|---|---|
| #634 が verification record を「正しくし直す」過程で #592 の母集団を変える | schema acceptance、append order、status、latest/not-run、gate 不変を golden と既存 test で固定 | semantic change は #592 の測定レビューとは分離して別 Issue とする |
| context の state path と effect publication path が別々に drift する | `ContextManifestEffectClaim.publication_path` を state record と publisher の唯一の source にし、basename と target を公開前検証 | mismatch は effect 0 / state 0 で reject |
| progress/context preparer と reducer が別計算になり digest が drift する | kernel の pure projection helper を共有 | application の state-producing legacy decision を compatibility wrapper まで縮退し、実 adapter から外す |
| clear / verification が effect 0 のため旧 direct save に戻る | 4 command 共通の typed execution test と thin-adapter AST test | empty `VerifiedBlobSet` / empty effect tuple も同じ repository entrypoint だけを使う |
| generic `set` が専用 evidence field を迂回する | 3 field を kernel-owned dedicated set に追加し drift test | field ownership の拡張は command と同じ PR でのみ許可 |
| v5 schema を暗黙変更する | v1-v4 passthrough projection と v5 rejection parity を固定 | dedicated v5 evidence aggregate は別 Issue で migration として設計 |
| #633 API が artifact 専用なのに generic と誤認する | #633 / #644 merge を着手 gate とし、#634 が transaction core の共通内部契約化を明示してから両 family で再利用 | API が確定設計と異なる場合は実装前に本書だけを更新し、二重 publisher を作らない |
