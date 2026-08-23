# 設計書: artifact 系 5 command の kernel 経由化と公開 effect 契約（#633）

> 調査基点: `ba5a87c`（依頼指定）。本書は調査・設計のみであり、実装を含まない。
> 実装着手は #632 と #644 の merge 後とする。

## 結論

実測の結果、対象 5 command は **5/5 を昇格**する。降格対象はない。

- 5 command はすべて parser から専用 `cmd_artifact_*` へ接続され、すべて
  `_run_evidence_decision` を通る（`skills/mission/bin/mission-state.py:19018-19054`,
  `skills/mission/bin/mission-state.py:7605-7758`）。
- 完了隣接 field（`phase` / `passes` / `loop_active` / `halt_reason` /
  `halt_category` / `terminal_outcome` / `score_history`）は変更しない。一方で、全 command が
  `artifact` または artifact lint / applicability / timestamp を変更するため read-only ではない
  （`skills/mission/lib/mission_application/artifact.py:156-348`）。
- `append` だけは公開 effect を持たないが、artifact block と state を書くため降格できない
  （`skills/mission/lib/mission_application/artifact.py:199-238`）。
- `init` / `render` / `export` / `publish` は content-bound な `EvidenceEffect` を返す。
  `export` は canonical artifact と export 先の 2 effect、ほかは canonical artifact の 1 effectである
  （`skills/mission/lib/mission_application/artifact.py:194-196`,
  `skills/mission/lib/mission_application/artifact.py:260-266`,
  `skills/mission/lib/mission_application/artifact.py:289-302`,
  `skills/mission/lib/mission_application/artifact.py:340-347`）。

したがって #619 / #620 型の「read-only へ降格して AST no-write guard だけを置く」処理は
本 Issue には適用しない。kernel は completion authority を新たに得ず、artifact aggregate と
その公開 effect だけを authority とする。

## 前提とスコープ

ADR-006 は Batch 2 の到達点を「全 mutation が `decide()` を通り、
`transition.new_state` を永続化する」と定め、Batch 3 の先頭を evidence command としている
（`docs/adr/006-kernel-reducer-adjudication.md:80-100`）。#632 の後続設計は #644 について、
kernel command が互換 payload を typed input として受け、kernel が `new_state` を完成させ、
repository が `project_legacy_document(new_state)` を保存すると定めている
（`docs/design/632-post-claims-finalizer.md:407-425`。調査時点では #632 worktree の
`05565d9` に存在し、main への merge 待ち）。本書はこの到達点を前提にする。

現行の artifact 経路は `select_legacy_repository` を使い、v5 head を明示拒否する
（`skills/mission/bin/mission-state.py:501-518`,
`skills/mission/lib/mission_persistence/repository_binding.py:178-204`）。また、A3 抽出時の
明示スコープも schema-v5 activation を除外している（`docs/design/508-a3-plan.md:10-17`）。
このため #633 では次を境界とする。

- v1-v4 の wire / CLI / artifact Markdown / consent-only publish を保つ。
- artifact 5 command の v5 activation や v5 schema への typed artifact aggregate 追加は行わない。
- v4 では `MissionState.legacy_passthrough` を保存 carrier としつつ、意味論は専用 typed command と
  reducer が検証・決定する。`project_legacy_document` は passthrough を起点に typed field を射影するため、
  reducer が完成させた passthrough をそのまま保存できる
  （`skills/mission/lib/mission_kernel/codec_v4.py:851-940`）。
- 将来 v5 でも artifact command を有効化する場合は、`MissionState` の dedicated artifact aggregate と
  codec v5 の閉じた schema を別 Issue で設計する。現行 v5 envelope は top-level field を閉じ、
  artifact field を持たない（`skills/mission/lib/mission_kernel/codec_v5.py:75-87`,
  `skills/mission/lib/mission_kernel/codec_v5.py:540-582`）。

## 実測方法

1. `_build_parser` の各 `set_defaults(func=...)` から CLI 実装関数を確定した
   （`skills/mission/bin/mission-state.py:19018-19054`）。
2. 各 CLI 関数から application decision と `_run_evidence_decision` の呼出しを追った
   （`skills/mission/bin/mission-state.py:7605-7758`）。
3. application decision の deepcopy 後の代入・削除と effect 列を確認した。入力 state は
   `_state` で deepcopy されるため、decision は入力を直接変更しない
   （`skills/mission/lib/mission_application/artifact.py:77-80`）。
4. 完了隣接 7 field と lint field を持つ合成 state に 5 decision を順番に適用し、top-level diff と
   effect 列を取得した。結果は下表と実装行が一致し、完了隣接 7 field は全段で不変だった。
5. lease 検証、effect publication、state save の実順序と既存テストの監視対象を突合した。

## 5 command の実測表

「書き込む field」は top-level と `artifact.*` の両方を明記する。`updated_at` は state と
artifact 内の双方を区別する。

| command | parser → 実装 → decision | 実際に書く / 削除する field | effect | 判定 |
|---|---|---|---|---|
| `artifact init` | `set_defaults` → `cmd_artifact_init` → `artifact_init`（`skills/mission/bin/mission-state.py:19020-19027`, `skills/mission/bin/mission-state.py:7605-7634`, `skills/mission/lib/mission_application/artifact.py:156-196`） | `artifact` を全置換: `status=draft`, `format`, `title`, `path`, `exports=[]`, `publish_events=[]`, `redaction_status`, `required_for_pass`, `blocks=[]`, `created_at`, `updated_at`, `digest`, `size`, `producer_run_id`; top-level `artifact_applicability=producing`, `updated_at`; `artifact_lint` / `artifact_lint_status` / `artifact_lint_identity` を削除（`skills/mission/lib/mission_application/artifact.py:178-195`, `skills/mission/lib/mission_application/artifact.py:139-153`） | canonical artifact 1 件 | **昇格** |
| `artifact append` | `set_defaults` → `cmd_artifact_append` → `artifact_append`（`skills/mission/bin/mission-state.py:19029-19035`, `skills/mission/bin/mission-state.py:7641-7666`, `skills/mission/lib/mission_application/artifact.py:199-238`） | `artifact.blocks` に `{section, content, timestamp, source?, label?}` を append; `artifact.status=draft`; `artifact.digest` / `artifact.size` を削除; `artifact.updated_at`, top-level `updated_at`; lint 3 field を削除（`skills/mission/lib/mission_application/artifact.py:215-235`） | なし | **昇格**（state writer） |
| `artifact render` | `set_defaults` → `cmd_artifact_render` → `artifact_render`（`skills/mission/bin/mission-state.py:19037-19040`, `skills/mission/bin/mission-state.py:7669-7691`, `skills/mission/lib/mission_application/artifact.py:241-266`） | optional `artifact.redaction_status`; `artifact.status=rendered`, `last_rendered_at`, `updated_at`, `path`, `digest`, `size`, `producer_run_id`; top-level `artifact`, `artifact_applicability=producing`, `updated_at`; lint 3 field を削除（`skills/mission/lib/mission_application/artifact.py:248-265`, `skills/mission/lib/mission_application/artifact.py:139-153`） | canonical artifact 1 件 | **昇格** |
| `artifact export` | `set_defaults` → `cmd_artifact_export` → `artifact_export`（`skills/mission/bin/mission-state.py:19042-19046`, `skills/mission/bin/mission-state.py:7694-7721`, `skills/mission/lib/mission_application/artifact.py:269-302`） | `artifact.redaction_status`; `status=exported`, `last_rendered_at`, `updated_at`, identity 4 field; `artifact.exports` に `{path, timestamp, redaction_status}` を append; top-level `artifact`, `artifact_applicability=producing`, `updated_at`; lint 3 field を削除（`skills/mission/lib/mission_application/artifact.py:277-301`, `skills/mission/lib/mission_application/artifact.py:139-153`） | canonical artifact と export 先の 2 件。同じ content（`skills/mission/tests/test_issue508_a3_application.py:139-146`） | **昇格** |
| `artifact publish` | `set_defaults` → `cmd_artifact_publish` → `artifact_publish`（`skills/mission/bin/mission-state.py:19048-19054`, `skills/mission/bin/mission-state.py:7724-7758`, `skills/mission/lib/mission_application/artifact.py:305-348`） | `artifact.publish_events` に provider / timestamp / approval / status / optional destination / `artifact_path` を append; `artifact.status`, `updated_at`, identity 4 field; top-level `artifact`, `artifact_applicability=producing`, `updated_at`; lint 3 field を削除（`skills/mission/lib/mission_application/artifact.py:324-347`, `skills/mission/lib/mission_application/artifact.py:139-153`） | canonical artifact 1 件。remote send は行わない（`skills/mission/tests/test_issue508_a3_application.py:147-154`） | **昇格** |

### 完了隣接 field の実測結論

5 decision はいずれも state 全体を deepcopy した後、上表の artifact 固有 field だけを変更する
（`skills/mission/lib/mission_application/artifact.py:156-348`）。Markdown renderer は
`score_history` / `threshold` を score gate 表示の入力として読むだけである
（`skills/mission/bin/mission-state.py:7413-7423`）。したがって次の 7 field は **読み取りまたは passthrough のみ**で、
artifact command の claim にはしない。

`phase` / `passes` / `loop_active` / `halt_reason` / `halt_category` /
`terminal_outcome` / `score_history`

ただし「完了隣接 field を書かない」ことは command 全体が read-only であることを意味しない。
5 command は artifact 固有 state を書くため、全件が kernel transition の対象である。

## 現行 `_run_evidence_decision` と共通化の起点

5/5 が `_run_evidence_decision` を通る。共通化に再利用できるのは次の 3 点である。

1. **lease admission を伴う repository factory**:
   `_legacy_evidence_repository.read_state` が state 読込直後に
   `_enforce_session_lease_for_write` を呼び、取得した lease decision を state write へ渡す
   （`skills/mission/bin/mission-state.py:7516-7539`）。
2. **rollback-capable effect publisher**:
   `_publish_evidence_effects` が全 effect を `_PublishedFilesTransaction` に載せ、各 publish 後に
   object identity を検証する（`skills/mission/bin/mission-state.py:7543-7561`,
   `skills/mission/bin/mission-state.py:12786-12804`,
   `skills/mission/bin/mission-state.py:13187-13210`）。
3. **effect の閉包検証**:
   `EvidenceEffect` は kind / relative target / immutable bytes / digest / size を束縛し、
   `validate_effects` は各 effect の再検証と target 重複拒否を行う
   （`skills/mission/lib/mission_application/artifact.py:37-54`,
   `skills/mission/lib/mission_application/artifact.py:83-115`,
   `skills/mission/lib/mission_persistence/legacy_v4.py:347-356`）。

一方、現行 `_run_evidence_decision` は application の `EvidenceDecision.state` をそのまま保存し、
`bind_published` が公開後に state を変更できるため、#644 後の「kernel が完成させた
`transition.new_state` を保存する」契約にはそのまま使えない
（`skills/mission/bin/mission-state.py:7582-7602`,
`skills/mission/lib/mission_persistence/legacy_v4.py:358-383`,
`skills/mission/bin/mission-state.py:7564-7579`）。progress / context も同 helper の consumer なので、
#633 で既存 helper 全体を置換してスコープを広げない
（`skills/mission/bin/mission-state.py:7768-7805`,
`skills/mission/bin/mission-state.py:7824-7840`,
`skills/mission/bin/mission-state.py:15181-15218`）。artifact 専用の typed 実行入口を追加し、
publisher / lease adapter だけを共有する。

## lease-first 契約の現状

### 一次証拠

現行順序は次である。

```text
StateLock
  -> repository.load()
     -> state read
     -> fenced lease validation
  -> application decision / render
  -> validate_effects
  -> effect transaction を開始して公開
  -> artifact identity bind
  -> state save
```

- lease 検証は `read_state` 内で実行される
  （`skills/mission/bin/mission-state.py:7520-7527`）。
- `execute_effects` は `load()` の後に初めて decision と effect transaction を開始する
  （`skills/mission/lib/mission_persistence/legacy_v4.py:366-382`）。
- repository 単体テストは rejected load のとき effect context が一度も開かれず、呼出列が
  `lock-enter -> load -> lock-exit` だけであることを固定する
  （`skills/mission/tests/test_issue508_a3_application.py:172-201`）。
- CLI 回帰は foreign lease で init が file を作らず、render / export / publish が既存 file と state を
  変更しないことを固定する（`skills/mission/tests/test_artifact_cli.py:73-149`,
  `skills/mission/tests/test_artifact_cli.py:509-535`）。
- state save 失敗時の全 effect rollback は repository 単体と実 adapter の双方で固定されている
  （`skills/mission/tests/test_issue508_a3_application.py:204-270`）。

### 既存テストの検出力に関する発見

`test_artifact_cli.py` の `*_does_not_publish_before_foreign_lease_rejection` 4 件は
`_write_artifact` を monkeypatch する（`skills/mission/tests/test_artifact_cli.py:62-70`,
`skills/mission/tests/test_artifact_cli.py:152-290`）。しかし現在の 5 command は
`_publish_evidence_effects` から `_publish_output_transaction` を直接呼び、`_write_artifact` を通らない
（`skills/mission/bin/mission-state.py:7543-7559`,
`skills/mission/bin/mission-state.py:7427-7444`）。したがって、この 4 件だけでは現在の publisher が
呼ばれなかったことを検出できない。

lease-first 自体は上記 repository 呼出順と実装順序で一次確認できるが、#633 では既存テストを削除せず、
実経路の `_publish_output_transaction` または effect transaction を監視する 4 ケースを追加する。

## 提案する kernel command と transition table

### effect claim

bytes を kernel command に入れない。kernel には次の immutable descriptor だけを渡す。

```python
@dataclass(frozen=True)
class ArtifactEffectClaim:
    kind: str
    target: str
    digest: str       # sha256:<64 hex>
    size: int
```

実 bytes は引き続き `EvidenceEffect` が保持する。`ArtifactEffectClaim` と
`EvidenceEffect` の kind / target / digest / size が完全一致しなければ、公開前に reject する。
これは ADR-005 の「kernel effect は logical kind と digest / size / reference identity を持ち、bytes は
`VerifiedBlobSet` 側に置く」という境界と同じである
（`docs/adr/005-typed-mission-kernel-and-unit-of-work.md:231-242`）。

### command dataclass

`mission_kernel.commands.Command` union と canonical encoder に、次の frozen dataclass 5 個を追加する。
命名は CLI 名と authority の意味が一対一になるものとする。

```python
@dataclass(frozen=True)
class InitializeArtifact:
    at: str
    path: str
    format: str
    title: str
    redaction_status: str
    required_for_pass: bool
    effect: ArtifactEffectClaim

@dataclass(frozen=True)
class AppendArtifactBlock:
    at: str
    section: str
    content: str
    source: Optional[str]
    label: Optional[str]

@dataclass(frozen=True)
class RenderArtifact:
    at: str
    redaction_status: Optional[str]
    effect: ArtifactEffectClaim

@dataclass(frozen=True)
class ExportArtifact:
    at: str
    destination: str
    redaction_status: str
    artifact_effect: ArtifactEffectClaim
    export_effect: ArtifactEffectClaim

@dataclass(frozen=True)
class RecordArtifactPublication:
    at: str
    provider: str
    destination: Optional[str]
    approval_text: str
    confirmed: bool
    effect: ArtifactEffectClaim
```

`RecordArtifactPublication` は remote dispatch command ではない。現行 publish は consent と intent を
artifact state に記録するだけである（`skills/mission/bin/mission-state.py:7733-7738`,
`skills/mission/tests/test_issue508_a3_application.py:147-154`）。`confirmed is True` と非空 approval を
kernel reducer でも検証し、CLI の事前検証だけに authority を残さない。

### transition table

| rule ID | command | reducer の決定 | event | state claim |
|---|---|---|---|---|
| `artifact-initialize` | `InitializeArtifact` | 現行 init validation 後に artifact aggregate を初期形へ全置換し、effect identity を bind | `artifact-initialized` | `artifact` 全体、`artifact_applicability`, lint 3 field の absence, `updated_at` |
| `artifact-append-block` | `AppendArtifactBlock` | artifact / blocks を検証し、block を 1 件だけ append。status を draft に戻し digest / size を外す | `artifact-block-appended` | `artifact.blocks`, `artifact.status`, digest / size absence, `artifact.updated_at`, lint 3 field absence, `updated_at` |
| `artifact-render` | `RenderArtifact` | redaction を検証し rendered 状態と effect identity を bind | `artifact-rendered` | `artifact.status`, optional redaction, `last_rendered_at`, identity 4 field, `artifact.updated_at`, `artifact_applicability`, lint 3 field absence, `updated_at` |
| `artifact-export` | `ExportArtifact` | checked 系 redaction と 2 effect の同一 content identity を検証し exports を 1 件 append | `artifact-exported` | render と同じ field + `artifact.exports` append + status exported |
| `artifact-record-publication` | `RecordArtifactPublication` | redaction / provider / consent を検証し publish event を 1 件 append。destination 有無から current status を導出 | `artifact-publication-recorded` | render と同じ field + `artifact.publish_events` append + status publish-prepared / published |

ここでいう state claim は `transition.new_state` が最終値として所有する field である。
`_CLAIMABLE_CONTROL_FIELDS` を artifact field まで拡張しない。あれは Batch 2 の compatibility bridge であり、
#644 後は repository が `new_state` 全体を射影・保存するためである
（`skills/mission/lib/mission_kernel/transitions.py:580-624`,
`docs/adr/006-kernel-reducer-adjudication.md:92-100`）。完了隣接 7 field は全 reducer で input と同値を保つ。

### render preparation と自己参照の回避

現行 Markdown は artifact の title / path / status / redaction / updated_at /
required-for-pass / blocks と state の mission / score summary / assumptions を使い、artifact digest / size /
producer identity / exports / publish event 本文は描画しない
（`skills/mission/bin/mission-state.py:7357-7410`）。したがって effectful command は lease admission 後、
同じ lock 内で次の順序にできる。

1. admitted current state と semantic args から、identity 未 bind の provisional artifact を pure helper で作る。
2. provisional artifact を現行 renderer へ渡して bytes を確定する。
3. `EvidenceEffect` と `ArtifactEffectClaim` を作る。
4. typed command を `decide()` へ渡す。reducer は同じ pure helper で delta を再構成し、claim の
   digest / size / target を最終 artifact identity に bind して `new_state` を完成させる。

pure helper は kernel package に 1 個だけ置き、preparer と reducer が共有する。application 側に第 2 の
artifact state reducer を残さない。application は `PreparedArtifactOperation(command, effects, result)` を返し、
現行 `EvidenceDecision.state` は artifact 5 command では使わない。

## 公開 effect の実行契約

### 既存部品の判定

| 部品 | 判定 | 理由 |
|---|---|---|
| `EvidenceEffect` | **再利用可** | immutable bytes と target / digest / size を既に閉じている（`skills/mission/lib/mission_application/artifact.py:37-45`, `skills/mission/lib/mission_application/artifact.py:83-115`） |
| `LegacyV4Repository.validate_effects` | **再利用可** | tuple、effect integrity、duplicate target を公開前に検証する（`skills/mission/lib/mission_persistence/legacy_v4.py:347-356`） |
| `LegacyV4Repository.execute_effects` | **そのままでは不可** | kernel `decide()` を呼ばず `EvidenceDecision.state` を保存し、公開後 state mutation callback を許す（`skills/mission/lib/mission_persistence/legacy_v4.py:358-383`） |
| `bind_transition_effects` | **拡張して再利用** | sealed transition に immutable effect を付ける機構はあるが、現在は opaque tuple を無検証で置換するだけで、command の effect claim との一致を確認しない（`skills/mission/lib/mission_kernel/transitions.py:627-637`） |

### 必要な追加

1. `PreparedArtifactOperation`（typed command / `tuple[EvidenceEffect, ...]` / CLI result）を application 境界に置く。
2. artifact reducer の transition には `ArtifactEffectClaim` を入れる。
3. `bind_transition_effects` は、transition に claim がある場合、実 effect の kind / target / digest / size と
   件数・順序が完全一致することを検証してから bound transition を返す。missing / extra / reorder /
   duplicate / digest mismatch は `invalid-transition-effect-binding` で reject する。
4. repository に artifact 専用 `execute_transition_effects(prepare, effect_transaction, verify_published)` を追加する。
   progress / context の既存 `execute_effects` は変更しない。
5. `bind_published` による state mutation は廃止する。公開後は effect と実 file の path / digest / size を
   **検証するだけ**の callback とし、不一致時は transaction rollback 後、state を保存しない。

### 必須順序

```text
lock
  -> load + lease admission
  -> prepare immutable bytes / typed command
  -> decode current MissionState
  -> decide(current, command)
  -> validate EvidenceEffect
  -> validate and bind transition effect claims
  -> begin effect transaction
     -> publish all effects
     -> verify every published path / digest / size
     -> save project_legacy_document(transition.new_state)
  -> commit / close
```

`load + lease admission` より前に directory 作成、temp file 作成、replace/link、backup 作成を行わない。
この順序は現行 repository の「load が effect context より先」という契約を維持する
（`skills/mission/lib/mission_persistence/legacy_v4.py:366-382`）。`export` の 2 effect または state save の
いずれかが失敗した場合は全 file を元に戻す。現行 transaction は逆順 rollback を実装している
（`skills/mission/bin/mission-state.py:13187-13210`）。

## 降格 command と AST guard

### 今回の判定

降格は **0 件**である。よって #633 の production test として「5 command は no-write」という
AST guard は置かない。artifact state writer を read-only と宣言する誤った guard になるためである。

代わりに、kernel reducer の property test で完了隣接 7 field が input と同値であることを固定する。
CLI adapter については、kernel 化後に direct dict write / `EvidenceDecision.state` 保存が残らないことを
thin-adapter test で固定する。

### 再精査で降格が発生した場合の guard 仕様

実装着手時の再計測で command が本当に read-only へ変わっていた場合だけ、#619 / #620 と同型の guard を
その command に適用する。既存 guard は subscript assignment と authority helper call を検出し、
合成 direct write で検出力を実証している
（`skills/mission/tests/test_issue620_kernel_a5_c1.py:138-208`）。適用時は次まで含める。

- 禁止 key: 完了隣接 7 field に加え、降格理由となった artifact 固有 field。
- 検出構文: `Assign` / `AnnAssign` / `AugAssign` / `del`、`update` / `setdefault` / `pop`、
  state writer / transition helper の間接 call。
- 合成違反 fixture 1: `data["loop_active"] = False` を追加し guard が検出する。
- 合成違反 fixture 2: `data.update({"artifact": {}})` を追加し間接 writer を検出する。

この条件分岐は実装時の推測による降格を許すものではない。parser から実体を再追跡し、state diff が
空である一次証拠が出た場合だけ設計変更する。

## TDD テストリスト

Red を先に作り、各まとまりを Green にする。

1. `Command` union / canonical encoder が artifact 5 dataclass を受け、各 type が一意になる。
2. transition table に 5 rule が各 1 件だけ存在し、unknown / ambiguous dispatch が起きない。
3. 各 reducer が上の実測表どおりの state diff と event を返す。入力 state は不変。
4. 完了隣接 7 field を多様化した property test で、5 reducer の前後値が完全一致する。
5. init の全置換、append の 1 block append・digest/size invalidation、render の identity refresh、
   export の 1 export append、publish の 1 event append を個別に固定する。
6. invalid section / malformed artifact / invalid redaction / unsafe target / invalid consent / invalid provider /
   wrong field type が transition と effect publication を作らず reject される。
7. effect claim と `EvidenceEffect` の missing / extra / reorder / kind / target / digest / size mismatch を
   合成し、publisher が一度も呼ばれないことを示す。
8. `export` の 2 effect は content digest / size が同一で、target だけが異なる。duplicate target は
   公開前に reject される。
9. foreign lease の init / render / export / publish で、実経路の `_publish_output_transaction` または
   effect context が一度も呼ばれない。artifact file / export file / state は実行前 byte と一致する。
10. foreign lease の append は effect なしでも state byte が不変で exit 2 になる。
11. 1 個目または 2 個目の effect publish、公開後 identity 検証、state save の各 fault point で
    全公開 file が rollback され、state が不変になる。
12. 現行 5 command の CLI JSON、state document、canonical Markdown、export bytes を golden 比較する。
13. publish は consent-only のまま、remote dispatch / send の result や effect を生成しない。
14. current v5 head に対する既存 format rejection を維持し、#633 が暗黙 activation を起こさない。
15. kernel 化後の `cmd_artifact_*` に direct state write と `EvidenceDecision.state` 保存がない。
16. source / plugin の production file が byte-identical である。

## 受け入れ条件

- [ ] 実装着手時にも parser → 実装 → reducer の再計測を行い、5/5 昇格判定と差がない。差があれば
  実装前に本書の実測表を一次証拠つきで更新する。
- [ ] artifact 5 command が専用 typed command と transition table を通り、repository は
  `project_legacy_document(transition.new_state)` だけを state 保存対象にする。
- [ ] application / CLI / post-publish callback に state mutation が残らない。
- [ ] effectful 4 command は lease admission 前に public file / temp file / directory / backup を一切作らない。
- [ ] `append` を含む 5 command が foreign lease を拒否し state を変更しない。
- [ ] effect claim と実 bytes が kind / target / digest / size で一対一に閉じる。
- [ ] init=1、append=0、render=1、export=2、publish=1 の effect cardinality と target が現行一致する。
- [ ] 完了隣接 7 field は全 command で不変であり、artifact command に completion authority を追加しない。
- [ ] publish は既存どおり consent / intent の記録だけで、外部送信を行わない。
- [ ] **lease-first 不変の既存テストが 1 件も落ちないこと**。加えて、現在の実 publisher を監視する
  新規 4 ケースと append の foreign-lease ケースが Green になる。
- [ ] multi-effect / published-identity / state-save failure の rollback テストが Green になる。
- [ ] v1-v4 state / CLI JSON / Markdown / export bytes の golden parity が Green になる。
- [ ] v5 format rejection parity が Green になり、schema-v5 activation を持ち込まない。
- [ ] `skills/mission/**` と `plugins/mission/skills/mission/**` の対応 production file が byte-identical。
- [ ] focused suite、full suite、artifact hygiene、vendor fingerprint がすべて Green。

## 変更対象ファイル

実装時の対象は次に限定する。source 変更は対応する plugin mirror へ同一内容を反映する。

- `skills/mission/lib/mission_kernel/commands.py`
- `skills/mission/lib/mission_kernel/transitions.py`
- `skills/mission/lib/mission_kernel/artifact.py`（artifact projection / validation / effect claim。新規）
- `skills/mission/lib/mission_application/artifact.py`
- `skills/mission/lib/mission_persistence/legacy_v4.py`
- `skills/mission/bin/mission-state.py`
- `skills/mission/tests/test_issue633_artifact_kernel.py`（新規）
- `skills/mission/tests/test_artifact_cli.py`
- `skills/mission/tests/test_issue508_a3_application.py`
- `skills/mission/tests/test_artifact_wiring.py`
- `plugins/mission/skills/mission/lib/mission_kernel/commands.py`
- `plugins/mission/skills/mission/lib/mission_kernel/transitions.py`
- `plugins/mission/skills/mission/lib/mission_kernel/artifact.py`（新規）
- `plugins/mission/skills/mission/lib/mission_application/artifact.py`
- `plugins/mission/skills/mission/lib/mission_persistence/legacy_v4.py`
- `plugins/mission/skills/mission/bin/mission-state.py`

`mission_kernel/model.py` と codec v5 は変更しない。実装時に artifact aggregate の dedicated model が
必要と判明した場合は、それは本書の v5 非 activation 境界を超えるため、#633 に黙って追加せず
別 Issue / 設計判断とする。

## リスクと出口戦略

| リスク | 制御 | 出口戦略 |
|---|---|---|
| renderer と reducer が別々に artifact delta を計算して drift する | kernel の pure projection helper を preparer / reducer で共有し、golden state / Markdown 比較を置く | helper の input を command dataclass に閉じ、application の state-producing decision を削除する |
| effect claim は正しいが公開 file が差し替わる | transaction 内で object identity に加えて path / digest / size を再検証してから state save | 不一致は全 effect rollback。state は保存しない |
| `bind_transition_effects` の強化が既存 effect consumer を壊す | 既存「claim なし transition への binding」回帰と、artifact claim ありの strict binding を分けてテストする | generic claim model への全面移行は別 Issue にし、#633 は artifact claim だけを追加する |
| progress / context まで同時に kernel 化して scope が膨らむ | `_run_evidence_decision` の既存 consumer を維持し、artifact 専用 typed executor を追加する | 後続 family Issue で同じ executor を段階的に採用する |
| v5 schema を暗黙変更する | 現行 legacy-only selector と rejection parity を固定する | dedicated artifact aggregate / codec v5 は別 Issue で明示 migration として扱う |

