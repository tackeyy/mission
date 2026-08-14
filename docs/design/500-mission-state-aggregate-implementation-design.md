# Issue #500: versioned `MissionState` aggregate / v1-v4 decoder 実装設計

Status: **実装設計確定**  
独立レビュー: **条件付き Go**（実装着手前に閉じる Medium 2 件を本文書へ反映済み）  
対象: Wave 3 / K1  
基準: local `origin/main` `19e3e09dec576d19bedd3db95bf0a5e660a56d33`
（2026-08-14、#498 / #499 取り込み後）

## 1. 結論

K1 は **1 Issue / 1 PR** で実装する。公開 API の decoder 入力は `bytes`
だけとし、dict を受け取る公開 API は作らない。JSON の構文・重複 key・有限数・
サイズを byte 境界で検証してから、missing/v1-v4 または v5 の schema decoder に
渡す。ファイル種別・link・identity は persistence adapter が検証し、pure kernel
には path や filesystem を持ち込まない。

ドメイン型は `@dataclass(frozen=True)` と `str, Enum`、閉じた `Union` で表す。
`TypedDict` と `NamedTuple` は採用しない。全 nested collection も tuple または frozen
JSON 値に変換し、`frozen=True` が浅い不変性に留まらないようにする。

K1 は v5 の **state-generation document を検証・正規化・canonical encode する
in-memory codec** を持つ。一方、head / commit / prepare、filesystem publish、CLI route、
新規 session の v5 化は作らない。この区別により、v5 の閉じた語彙を K1 で固定しつつ、
ADR-005 の「K1 では v5 writer を有効化しない」を守る。

## 2. スコープ

### 2.1 K1 で行うこと

- versioned `MissionState` の immutable read model
- missing/v1-v4 state document の read-only normalization
- v5 state-generation document の strict decode / canonical encode
- legacy review evidence document の pure decoder
- bounded strict JSON codec と strict file snapshot reader
- v1-v4 の unknown/unowned field を失わない frozen passthrough
- #483 の schema-version 判定と `mission_common.derive_terminal_outcome` の再利用
- canonical / plugin mirror の同時追加と Python 3.9 gate
- 新コードを production command から到達不能に保つテスト

### 2.2 K1 で行わないこと

- `mission-state.py` の command routing を typed kernel に切り替えること
- v4 state を v5 へ物理変換すること
- v5 head / commit / prepare record、CAS、recovery、GC
- state decoder が review / score evidence path を開くこと
- Finding を resolve する command / transition
- `accepted-risk` / `not-reproducible` の実装
- pass gate、provider authority、lease admission の意味変更

## 3. 現行コードとの照合結果

### 3.1 確認した事実

以下は基準 SHA のコードから確認した事実であり、提案ではない。

| 対象 | 現行位置 | 確認結果 |
|---|---:|---|
| schema/version | `skills/mission/bin/mission-state.py:243-270` | `SCHEMA_VERSION = 4`。missing を許容し、bool と非 int、1 未満、4 超を拒否する `_validate_schema_version` と、`Path.read_text()` + `json.loads()` の `_load_state_json` がある |
| lease | 同 `:564-908` | lease は nested object ではなく root の `owner_session_id`, `lease_id`, `fencing_epoch`, `lease_expires_at` と `lease_history`。4 field の一部だけが non-empty の場合は mutation admission で拒否する |
| phase | 同 `:1786-1817` | enum 型ではなく `VALID_PHASES` set。alias は `set phase=...` 入力の正規化にだけ使われる |
| initial state | 同 `:6953-7051` | `cmd_init` の初期 dict は `6978-7051`。plan / handoff / review ref / lease は初期 dict 内にはない |
| terminal outcome | `skills/mission/lib/mission_common.py:42-52,74-129` | 9 語彙と、control field からの導出・explicit outcome の整合性検査は既に単一関数にある。active は `None` |
| #483 golden | `skills/mission/tests/test_issue483_schema_compat_matrix.py:101-195,528-615` | missing/v1-v4 の最小 state と `get/next/set/stats` の結果、future/non-int version の拒否を固定する |
| plan | `mission-state.py:12372-12383,12425-12428` | core/provider とも path, digest, source identity, selection source, iteration, generation, validated time を持つ。core は source bytes digest も保持する |
| handoff | 同 `:8326-8333,12465-12503` | `prepared -> consuming -> consumed`、canonical plan identity drift 時の `rejected` が実装済み |
| review refs | 同 `:12014-12156,13118-13128` | state は review input / aggregate の immutable reference を保持する。review 本文や Finding 本文を root state に埋め込まない |
| score | 同 `:10255-10301,13416-13460` | legacy score と、review/manual evidence・revision scope・scoring artifact に結び付く現行 score が共存する |
| strict input read | 同 `:10836-10879,11863-11889` | review input には bounded regular single-link read と duplicate-key/UTF-8/単一 JSON 検証がある。ただし state loader には使われていない |
| Python 3.9 gate | `test_issue99_py39_compat.py:17-75`, `test_python_module_inventory.py:61-102` | recursive inventory は Python 3.9 grammar で parse し、canonical/plugin 両 root から import する。`match` は reject fixture。実機 `/usr/bin/python3` は 3.9.6 |

`skills/mission/bin/mission-state.py` には `_load_state_json` を通らない直接
`json.loads(sf.read_text(...))` も残る。例として `advance` は `:8301-8303`、
review aggregation は `:13018-13020`、planning lifecycle は `:12184-12186` と
`:12311-12313` に直接 read がある。したがって #483 は「全 command 共通の strict
reader」ではない。

### 3.2 移行計画 K1 節への指摘

| 分類 | K1 節の問題 | 確定する解決 |
|---|---|---|
| 食い違い | `mission-state.py:241-270`, `:560-908`, `:6953-7075` は現在の main とずれている | 本文書 §3.1 の行番号に更新する。基準 SHA も併記する |
| 食い違い | 「lease dict」と読めるが現行 lease は root に flatten されている | v1-v4 decoder は flatten された 4 field + history を読む。v5 wire だけ nested `lease` object にする |
| 食い違い | 「phase enum」とあるが現行は set と CLI alias map | persisted canonical 6 語彙だけを `Phase` enum にする。alias は decode しない |
| 食い違い | #483 の loader が strict JSON/file validation まで提供するように読める | #483 から再利用するのは version policy と golden expectation。byte/file strictness は K1 の共通 primitive として追加する |
| 食い違い | state decoder だけで legacy Finding 本文を取得できるように読める | state にあるのは evidence ref。state decode と、caller が取得・digest 検証済みの review evidence decode を分離する |
| 未定義 | decoder 入力が bytes か dict か未定義 | 公開入力は `bytes` のみ。mapping decoder は module-private とする |
| 未定義 | duplicate key、invalid UTF-8、trailing data を dict 入力でどう検出するか不明 | dict 化前の common JSON byte codec で検出する |
| 未定義 | oversize の閾値と境界値がない | state-generation document は missing/v1-v5 とも最大 4 MiB。`len == limit` は許可、`limit + 1` は拒否する |
| 未定義 | unknown key の対象 version が不明 | missing/v1-v4 は frozen passthrough、v5 の closed object は拒否。v5 `extensions` だけを非権威の open surface とする |
| 未定義 | legacy passthrough の粒度・aliasing・write 時の優先順位がない | parse 済み legacy document 全体を deep-frozen snapshot として保持。typed field が authority、passthrough は将来の v4 projection 専用とする |
| 未定義 | complete lease の型、時刻、history の条件がない | §7.4 の exact contract を適用する。bool/float を int として受けない |
| 未定義 | v5 の unknown variant の範囲がない | phase、terminal outcome、plan kind、handoff status、review kind、Finding severity/status、score source、revision scope kind、lease kind を閉じる |
| 未定義 | `resolved` の prior identity/evidence/time の wire shape がない | §8 の exact fields と invariants に固定する |
| 未定義 | round-trip が object equality か byte equality か不明 | `decode(encode(model)) == model` と `encode(decode(bytes)) == canonical bytes` の両方を要求する |
| 未定義 | 「source bytes を変えない」が decoder の純粋性か file 非更新か不明 | bytes hash/identity の不変と、file bytes/stat identity の不変を別々に test する |
| 矛盾 | title/Expected は v1-v4 decoder・v5 writer なしだが TDD Red は v5 round-trip と v5 variant validation を要求する | v5 **codec** は K1、v5 **persistence writer/route** は U1/U2 以降、と定義する |
| 矛盾 | K1 の file race attack と U1 の staging race attack が重なる | K1 は read-only snapshot reader、U1 は staging/publish/write race。共通 reader を U1 が再利用する |
| 矛盾 | v1-v4 unknown field passthrough と「unknown key reject」が無条件に並ぶ | reject は v5 closed objects に限定する |
| 矛盾 | v5 `resolved` を decode できる一方で migration command は生成してはならない | read-only `ResolvedFinding` 型と codec は置くが、command/transition/factory/CLI import を置かない。静的 route test で固定する |
| 後続との緊張 | K2 が immutable state を前提にする一方、dict passthrough が mutable だと transition から変更できる | deep-frozen JSON とし、`legacy_passthrough` を v4 projection module 以外から参照しない dependency test を置く |
| 後続との緊張 | U1/U2 が head/commit codec まで K1 に期待すると scope が膨張する | K1 の v5 対象は state-generation document だけ。head/commit/prepare の schema と上限は U2 で決める |
| 後続との緊張 | R1 の「versioned reader」と K1 の file reader を同じ責務にすると、K1 が未実装の head/commit lineageを扱うことになる | K1 は generic `strict_reader`。R1 の authoritative `reader.py` がこれを使い、U2 の head/commit/generationを解決する |
| 後続との緊張 | A2 は Finding本文と evidence再検証を必要とし、A4 は plan/handoffを state-only で必要とする | Findingはverified evidence bundleからmaterializeし、Plan/Handoffはstate decodeだけでmaterializeする。両者を同じ暗黙I/Oにしない |

## 4. モジュール構成

canonical と plugin mirror に同じ相対 path を置く。

```text
skills/mission/lib/
  mission_kernel/
    __init__.py          public read-model/decode API の限定 export。v5 encoder は export しない
    model.py             frozen domain types、closed Enum/Union、aggregate invariants
    json_codec.py        bytes <-> immutable JSON、duplicate/UTF-8/finite/size/canonical encoding
    versions.py          missing/v1-v5 の version classification。max_reader_version を引数化
    codec_v4.py          missing/v1-v4 normalization、legacy review evidence normalization
    codec_v5.py          closed v5 state-generation decode / canonical encode
  mission_persistence/
    __init__.py          K1 では strict snapshot read だけを export
    strict_reader.py     no-follow regular single-link bounded stable-identity read

plugins/mission/skills/mission/lib/
  （上記と byte-for-byte 同じ相対 path）
```

責務境界は次のとおり。

```text
Path
  -> mission_persistence.strict_reader.read_stable_bytes()
  -> bytes
  -> mission_kernel.json_codec.decode_json_object()
  -> immutable JSON object
  -> versions + codec_v4 / codec_v5
  -> frozen MissionState
```

- `strict_reader` は JSON や schema を知らない。
- `json_codec` は Mission の field を知らない。
- `codec_v4` / `codec_v5` は path を開かない。
- `model` は JSON、filesystem、clock、environment、process を import しない。
- `mission_kernel/__init__.py` は `encode_v5_state` を re-export しない。encoder は
  `mission_kernel.codec_v5` 内に留め、package root の production-reachable API にしない。
- application/use-case は K1 では追加しない。

`mission-state.py` の `_validate_schema_version` は K1 実装時に `versions.py` の
`read_schema_version(document, max_reader_version=4)` を呼ぶ薄い compatibility wrapper
へ変える。これにより #483 の v4 reader contract はそのまま残り、K1 decoder は同じ
primitive を `max_reader_version=5` で使う。v4 CLI が v5 を受理する変更はしない。

既存 `_read_strict_review_file` と JSON pair hook は `strict_reader` / `json_codec` に
委譲する薄い wrapper へ変え、既存 review tests を characterization gate とする。
semantic な `mission-review/1` validation は既存位置に残す。K1 が同じ file/JSON
safety contract を二重実装することは認めない。

## 5. 型表現

### 5.1 選択

- state object / record / variant: `@dataclass(frozen=True)`
- scalar vocabulary: `class X(str, Enum)`
- variant: `typing.Union[AbsentPlan, CorePlan, ProviderPlan]` のような closed union
- list: tuple
- map/set: `FrozenJsonObject` または tuple pair
- optional: `Optional[T]` または `T | None`（全 module に future annotations）

`dataclass` は field 名、default、invariant を表現でき、runtime decoder の戻り値として
検査可能である。`TypedDict` は runtime object が mutable dict のままで、unknown key と
deep immutability を保証しない。`NamedTuple` は variant ごとの optional field と将来の
field 追加が位置依存になり、schema evolution に不向きである。

`frozen=True` だけでは nested dict/list が mutable なので、JSON passthrough は次の
recursive value に変換する。

```python
JsonScalar = Union[None, bool, int, float, str]
FrozenJsonValue = Union[JsonScalar, "FrozenJsonObject", tuple["FrozenJsonValue", ...]]

@dataclass(frozen=True)
class FrozenJsonObject:
    items: tuple[tuple[str, FrozenJsonValue], ...]
```

float は decode 時に有限値だけを許す。object の pair order は保持するが、v5 canonical
encode は key sort を行う。legacy passthrough を thaw する関数は `codec_v4` 内部だけに
置く。

### 5.2 closed scalar types

- `Phase`: `planning|executing|reviewing|scoring|done|halted`
- `TerminalOutcome`: `completed_pass|completed_evidence|blocked_external|awaiting_approval|stale_superseded|failed|incomplete|user_aborted|routed_elsewhere`
- `PlanSource`: `core|provider`
- `HandoffStatus`: `prepared|consuming|consumed|rejected`
- `ReviewKind`: `review-input|review-aggregate`
- `FindingSeverity`: `High|Medium|Low`
- `FindingStatus`: `open|resolved`
- `ScoreSource`: `legacy-unverified|scoring-json|manual-import`
- `RevisionScopeKind`: `git|not-applicable`
- `LeaseKind`: in-memory `legacy-absent|fenced`。v5 wire は `fenced` のみ
- `SessionRole`: `implementer|checker|planning|analyze|release`
- `HaltCategory`: `blocked-external|awaiting-approval|partial-done|evidence-submitted|routed-goal|stagnation|user-abort|stale|other`

Python 3.9 には `enum.StrEnum` がないため `class Phase(str, Enum)` を使う。

### 5.3 aggregate shape

`MissionState` は少なくとも次を field とする。

| field | type | 備考 |
|---|---|---|
| `schema_origin` | `SchemaOrigin` | `missing|v1|v2|v3|v4|v5`。canonical state 自体の version は常に model version 1 |
| `identity` | `MissionIdentity` | mission/session identity。legacy は field absence を保持し、v5 は全 field を要求する |
| `control` | `MissionControl` | phase, iteration/max_iter, loop_active, passes, halt fields, role, threshold, reviewer/stagnation fields |
| `terminal_outcome` | `TerminalOutcome | None` | active は `None` |
| `plan` | closed `Plan` union | absent/core/provider |
| `handoff` | closed `Handoff` union | absent/4 status variants |
| `reviews` | `tuple[ReviewRef, ...]` | state-owned refs。evidence 本文は含めない |
| `findings` | `FindingCollection` | v5 は materialized。legacy state-only decode は `LegacyFindingsUnloaded(review_refs)` |
| `scores` | `tuple[Score, ...]` | `LegacyScore` と provenance-bearing score を区別 |
| `lease` | `LegacyAbsentLease | FencedLease` | complete record のみ |
| `extensions` | `FrozenJsonObject` | v5 の明示 open surface。kernel decision の authority にしない |
| `legacy_passthrough` | `LegacyPassthrough | None` | missing/v1-v4 の deep-frozen original document。v5 は必ず `None` |

`LegacyFindingsUnloaded` は Finding が「0件」だとは主張しない。state file だけでは review
evidence の本文を取得できない事実を型で表す。caller が strict reader と digest/size
binding を検証した evidence bytes を渡した場合だけ、`decode_legacy_review_evidence`
が `MaterializedFindings` を返す。`open_high` から Finding を合成してはならない。

K2 は `legacy_passthrough` や unloaded evidence を guard の authority に使ってはならない。
不足する verified observation は application port から明示的に command へ渡す。

### 5.4 Identity / Control の exact contract

`MissionIdentity` の model field は `mission`, `mission_id`, `session_id` とする。

- missing/v1-v4: field がなければ `None`。present なら string だけを許し、値を変更しない。
- v5 wire: `identity` の exact key set は `mission, mission_id, session_id`。すべて trimmed
  non-empty string、`mission_id` / `session_id` は最大128文字、`mission` は最大64 KiB。
- `project_root`, pid/host、run correlation は K1 の typed authority にせず legacy
  passthroughに残す。後続で型に昇格するまでは kernel decisionに使わない。

`MissionControl` の v5 wire exact key set:

```text
phase, terminal_outcome, iteration, max_iter, threshold,
reviewer_count, stagnation_count, loop_active, passes,
halt_reason, halt_category, session_role
```

- `phase`: closed `Phase`
- `terminal_outcome`: closed `TerminalOutcome` または null
- `iteration`, `stagnation_count`: bool でない non-negative int
- `max_iter`: null または bool でない positive int
- `threshold`: bool でない finite number、0..5
- `reviewer_count`: bool でない positive int
- `loop_active`, `passes`: exact bool
- `halt_reason`: string。active/pass は empty、halted は non-empty
- `halt_category`: null または closed `HaltCategory`
- `session_role`: closed `SessionRole`

missing/v1-v4 の absent default は、現行 readerの観測意味に合わせて phase は §7.2、
iteration は1、max_iter/thresholdは `None`、reviewer_countは2、stagnation_countは0、
loop_active/passesはFalse、halt_reasonはempty、halt_categoryはNone、session_roleは
implementer とする。present field の型不正は canonical authority にせず黙ってcoerce
せず、decodeを fail-closedにする。

v5 では control projectionを `mission_common.derive_terminal_outcome` に渡し、戻り値と
`terminal_outcome` が exact 一致すること、phaseが terminal/nonterminal controlと矛盾
しないことを aggregate invariant とする。

legacy passthrough の「lossless」は unknown fieldの名前・型・値・array順を失わないという
semantic losslessを意味する。空白、indent、object key順を含む元fileのbyte-for-byte再現は
意味しない。元bytesはdecode中に変更せず、将来のv4 compatibility writerは現行と同様に
JSONを再serializeする。

## 6. decoder / codec API

public API は次に固定する。実際の naming はこのまま使用し、同義 API を増やさない。

```python
def decode_mission_state(source: bytes) -> MissionState: ...
def decode_legacy_review_evidence(source: bytes, reference: ReviewRef) -> MaterializedFindings: ...
def encode_v5_state(state: MissionState) -> bytes: ...
def project_legacy_document(state: MissionState) -> bytes: ...
```

ここで public API は各 owner module が互換性を保つ API を指す。package root の export
surfaceと同義ではなく、`encode_v5_state` の ownerは `codec_v5` のままとし、§4 のとおり
`mission_kernel` package rootからはexportしない。

- `decode_mission_state` は common strict JSON parse 後、schema missing/1..4 を
  `codec_v4`、5 を `codec_v5` へ dispatch する。6 以上は future version error。
- mapping を受ける helper は `_decode_*_object` として private にする。
- `encode_v5_state` は `schema_origin == v5` かつ `legacy_passthrough is None` の
  invariant-valid model だけを canonical bytes にする。file を開かず publish しない。
- `project_legacy_document` は `legacy_passthrough` を thaw し、typed-owned field だけを
  model から再投影する pure function。K1 では file writer から呼ばない。
- error は `MissionStateDecodeError(code, json_path, detail)` と
  `StrictReadError(code, detail)` に正規化する。test は安定した `code` と `json_path`
  を assert し、全文 message を public contract にしない。

JSON canonical encoding は UTF-8、`ensure_ascii=False`, `sort_keys=True`,
`separators=(",", ":")`, `allow_nan=False`、末尾 newline なしとする。integer と finite
float の Python 標準 JSON 表現を使う。v5 の timestamp は UTC seconds precision の
`YYYY-MM-DDTHH:MM:SSZ` だけを受ける。

## 7. missing/v1-v4 normalization

### 7.1 version 共通

1. missing は `SchemaOrigin.MISSING`、1..4 は対応する origin とする。
2. bool、string、float、null、0 以下、5 以上を version として受けない。
3. unknown root/nested field は拒否せず、original immutable document 全体を
   `legacy_passthrough` に保持する。
4. decoder は source bytes、path、state/evidence file を書き換えない。
5. legacy field を canonical authority に昇格するときだけ field-specific validation を行う。
6. unknown closed union value（phase、plan source、handoff status）は fail-closed。
   Finding status だけは §7.5 の例外を適用する。

### 7.2 phase / terminal

- valid phase はそのまま `Phase` へ変換する。CLI alias (`execution`, `review`, `plan`,
  `score`) は persisted state の語彙ではないため拒否する。
- phase が missing/null/empty の legacy state は、current next の default と terminal
  control を明文化して次へ正規化する。
  - active/nonterminal: `planning`
  - `completed_pass|completed_evidence`: `done`
  - その他の terminal outcome: `halted`
- terminal outcome は `mission_common.derive_terminal_outcome` を直接呼んで得る。
  9 語彙の判定ロジックを codec に複製しない。
- v5 decoder は nested `control` をそのまま渡さず、次の exact projection dict だけを
  `derive_terminal_outcome` へ渡す。表にない `phase`, `iteration`, `max_iter`, `threshold`,
  `reviewer_count`, `stagnation_count` は projection に含めない。

| projection key | 供給元 | presence rule |
|---|---|---|
| `passes` | `control.passes` | 常に含める |
| `loop_active` | `control.loop_active` | 常に含める |
| `halt_reason` | `control.halt_reason` | 常に含める |
| `halt_category` | `control.halt_category` | non-null のときだけ含め、null は省略する。現行関数は key-present + non-string を `failed` にするため null を渡さない |
| `session_role` | `control.session_role` | 常に含める |
| `terminal_outcome` | `control.terminal_outcome` | non-null のときだけ含め、null は省略する。省略が active/no-explicit-outcome を表す |
| `resolution_status` | 供給元なし | v5 wire に存在しない legacy hint なので常に省略する。null、empty string、既定値を注入しない |

projection の key set は、常時4 keyに non-null の
`halt_category` / `terminal_outcome` を加えたものだけとする。
- v1-v4 の explicit outcome unknown/contradiction は現行どおり canonical `failed`。
  passthrough には元値を残す。v5 は unknown/contradiction 自体を reject する。

Compatibility risk: `PHASE_ALIASES` は現行 CLI の `set` / `advance` 入力を canonical phase
へ正規化するが、現時点の保証は #483 golden fixture に限られ、全履歴の persisted stateを
証明しない。過去の手編集、旧実装、または未把握の write path が aliasを保存していた
可能性は未確認である。
K1 は alias を黙って canonicalize せず reject する方針を維持し、実装前に §12.2 の
tracked corpus / writer characterization を追加確認する。alias の実在が確認された場合は、
実装に進む前に migration policy を再決定する。

### 7.3 Plan / Handoff / Review / Score

Plan:

- `canonical_plan` missing/null は `AbsentPlan`。
- present は `schema`, `path`, `digest`, `source`, `source_id`, `source_digest`,
  `selection_source`, `iteration`, `generation`, `validated_at` を保持する。
- source は `core|provider` のみ。iteration/generation は bool でない non-negative /
  positive int、digest は `sha256:` + 64 lowercase hex。
- partial present record は reject。unknown legacy plan field は passthrough に残す。

Handoff:

- `executor_handoff` missing/null は `AbsentHandoff`。
- present は schema/handoff id と plan path/digest/generation/source/source id/
  selection source/iteration/ordered step ids を必須とし、canonical plan と完全一致させる。
- `consuming` は `begun_at`、`consumed` は `begun_at` と `consumed_at`、`rejected` は
  `rejected_reason` を要求する。`rejected.begun_at` は optional とする。

Review:

- `review_evidence_refs[]` は `ReviewInputRef` へ順序を保って変換する。
- score provenance 内の `review_evidence_ref` は `ReviewAggregateRef` として score と
  同じ occurrence に保持する。勝手に dedupe しない。
- legacy `ReviewInputRef` は path, digest, size, iteration, perspective を必須とする。
- legacy `ReviewAggregateRef` は現行に存在する path, digest, generation, revision scopeを
  必須とし、review group/generation/base/head の4 fieldは all-or-none。現行に存在しない
  size/iteration/perspectiveを発明しない。
- model の path field名は `relative_path` に統一し、codec_v4だけが legacy `path` と対応付ける。
- v5 の review ref は kind, relative_path, digest, size, iterationを必須とし、inputは
  perspective、aggregateは generation/revision scopeとoptional all-or-none lineageを要求する。
- decoder は path を開かず、digest が正しい内容を指すとは主張しない。

Score:

- missing/empty `score_history` は empty tuple、最新値は `NoScore`。
- complete provenance がない v1-v4 entry は `LegacyScore(authoritative=False)`。
  #483 の v4 minimal fixtureもここに入る。
- complete shape がある entry は `BoundScore(authoritative=False)`。reference/binding を
  型として保持するが、evidence bytes の再検証前に `verified` と呼ばない。
- items, composite, min_item は bool でない finite number、agreementはnullまたは同じ
  finite number、open_high は bool でない non-negative int。revision scope と
  force-approval lineage を欠落させない。
- v5 score は complete provenance と scoring evidence ref を必須とし、legacy score
  variant を wire 上で拒否する。

### 7.4 Lease

v1-v4 の 4 root lease field がすべて missing/null/empty の場合だけ
`LegacyAbsentLease` とする。1 つでも non-empty なら 4 field 全部を要求する。

`FencedLease` の contract:

- `owner_session_id`, `lease_id`: trimmed non-empty string
- `fencing_epoch`: `type(value) is int` かつ 1 以上
- `lease_expires_at`: timezone-aware ISO-8601、canonical model では UTC に正規化
- `lease_history`: missing は empty tuple、present は list
- history item: owner/session token、positive epoch、non-empty reason、aware `at`
- history epoch は strictly increasing、current epoch より小さい
- history lease id は重複せず current lease id と異なる

partial、型不正、invalid time、history contradiction は decode error とする。expiry を
現在時刻と比較しない。decoder は clock を持たず、expired/takeover は後続 use case の
admission decision である。

### 7.5 legacy Finding

legacy Finding は state 本体ではなく `mission-review/1` evidence 内にある。
`decode_legacy_review_evidence` は caller が binding 済み `ReviewRef` と bytes を渡した時に
だけ実行する。

- current fields（id, iteration, perspective/reviewer, severity, axis, summary/claim,
  evidence, recommendation と learning fields）を frozen payload として保持する。
- status missing、`open`、`resolved`、任意 string、`accepted-risk`,
  `not-reproducible` のいずれも canonical status は **`open`**。
- legacy の文字列 evidence や status は resolution authority にしない。
- Finding identity は `(review_ref.digest, finding_id)` とし、score count から identity を
  発明しない。

これは ADR-002 の「`accepted-risk` / `not-reproducible` は未実装」という注記を守る。

## 8. v5 state-generation contract

v5 top-level key は次の exact set とする。

```text
schema_version, identity, control, plan, handoff,
reviews, findings, scores, lease, extensions
```

- `schema_version` は exact int `5`。
- `plan` は `{kind:"absent"}` または core/provider の required fields。
- `handoff` は `{kind:"absent"}` または §7.3 の closed record。
- `reviews`, `findings`, `scores` は array。absence は空 arrayで、架空の status を置かない。
- `lease` は complete `{kind:"fenced", ...}`。v5 wire に `legacy-absent` は書かない。
- `extensions` は namespaced key と frozen JSON value の map。kernel transition、pass gate、
  lease、provider authority はこの値を読まない。
- すべての closed object は required/optional key set を宣言し、未知 key を拒否する。

### 8.1 Finding status

v5 Finding 共通 required fields:

```text
id, generation, iteration, reviewer, severity, axis,
summary, recommendation, evidence_ref, status
```

- `generation`: bool でない positive int
- `evidence_ref`: kind/relative_path/digest/size を持つ content-addressed immutable reference
- `status == "open"`: resolution fields を含めてはならない
- `status == "resolved"`: 次の 3 field をすべて要求する
  - `prior_identity`: exact `{id, generation}`。id は同一、prior generation は current より小さい
  - `resolution_evidence_ref`: exact `{kind, relative_path, digest, size}`、kind は `finding-resolution`
  - `resolved_at`: canonical UTC timestamp
- 3 field の partial presence は reject
- `accepted-risk`, `not-reproducible` とその他の status は reject

model は `OpenFinding` と `ResolvedFinding` の frozen dataclass union とする。K1 に
`ResolveFinding` command、resolver factory、transition は置かない。`codec_v5` は既存の
resolved documentを検証して読めるが、production command は v5 を emit できない。

### 8.2 「v5 writer なし」の機械的担保

- `encode_v5_state` は path、repository、atomic write を import しない pure serializer。
- `mission_kernel/__init__.py` の `__all__` と package attribute に `encode_v5_state` を置かない。
  canonical / plugin package を fresh interpreter で import する import graph test は
  `from mission_kernel import encode_v5_state` が `ImportError` となり、package root から
  到達不能であることを assert する。
- `skills/mission/bin/*.py`, `scripts/*.py` は `codec_v5` / `encode_v5_state` /
  `ResolvedFinding` を import しない。
- `_build_parser` の command inventory に v5 init/migrate/resolve route を追加しない。
- `mission-migrate.py` の execute fixture は v1-v4 bytesを compatibility layoutへ移すだけで、
  `schema_version: 5` を出さないことを test する。legacy arbitrary status は v4 の
  passthrough として残り得るが、v5 resolved authorityにはならない。

## 9. 拒否条件の層別表

| 条件 | 拒否層 | error code / 補足 |
|---|---|---|
| future version | `versions` | `unsupported-schema-version`; decoder max は 5、legacy CLI wrapper max は 4 |
| non-int version | `versions` | `schema-version-type` |
| bool version | `versions` | `schema-version-type`; int subclass として通さない |
| partial lease | `codec_v4` / `codec_v5` | `partial-lease` |
| unknown v5 variant | `codec_v5` | `unknown-variant` + JSON path |
| v5 Finding status が open/resolved 以外 | `codec_v5` | `finding-status-invalid` |
| duplicate key | `json_codec` | `duplicate-json-key`; nested object も対象 |
| invalid UTF-8 | `json_codec` | `invalid-utf8` |
| `NaN` / `Infinity` | `json_codec` | `non-finite-number`; parse constant と recursive finite check の両方 |
| trailing data | `json_codec` | `trailing-json-data`; whitespace だけは許可 |
| oversize | `strict_reader` と `json_codec` | `record-too-large`; bytes API 直呼びでも逃がさない |
| root non-object | `json_codec` | `root-not-object`; JSON path は `$` |
| unknown key | `codec_v5` | `unknown-key`; missing/v1-v4 は reject せず passthrough |
| symlink | `strict_reader` | `not-regular-single-link`; `O_NOFOLLOW` 必須、利用不可platformは fail-closed |
| FIFO | `strict_reader` | `not-regular-single-link`; `O_NONBLOCK` 後に `fstat` |
| hard link | `strict_reader` | `not-regular-single-link`; `st_nlink == 1` 必須 |
| identity swap | `strict_reader` | `identity-changed`; initial/final `fstat` と final `lstat` の identity/size一致 |

file identity tuple は現行 strict review read と同じ
`(st_dev, st_ino, st_mode, st_nlink, st_size, st_mtime_ns, st_ctime_ns)` とする。
`os.read` は initial size ちょうどを読み、short read、追加 byte、read 後 identity/size drift、
最終 pathname identity driftをすべて拒否する。

`source bytesを変更しない` は次の二重保証にする。

1. `decode_mission_state(source)` 前後で input bytes の equality と SHA-256 が同一。
2. `read_stable_bytes(path)` + decode 前後で path の bytes と identity が同一で、temp/backup/
   evidence file が新規作成されていない。

## 10. 既存実装との単一ソース関係

### 10.1 #483

- version acceptance は `versions.read_schema_version` を唯一の primitive とする。
- 現行 `_validate_schema_version` は max=4 wrapper として名前・exception behavior を維持する。
- K1 decoder は max=5 で同じ primitive を使う。
- #483 golden expected object は変更しない。fixture state だけを shared fixture module へ
  移す場合も expectation は同じ test に残す。

### 10.2 `mission_common.derive_terminal_outcome`

- v1-v4 canonical outcome は必ず現行関数から導出する。
- codec に terminal decision tree をコピーしない。
- `TerminalOutcome` enum への conversion は関数の戻り値に対してだけ行う。
- v5 は同じ関数へ §7.2 の exact control projectionだけを渡し、projection helper が
  keyを追加・default注入しないことを unit testで固定する。explicit valueとの不一致は
  codec errorにする。
- K1 後も `mission_common` が計算ロジックの管理元。K2 が transition tableへ移す時に、
  parityを保ったまま管理元を一度だけ切り替える。

### 10.3 strict JSON/file primitive

現行 review/plan/provenance に散在する duplicate-key、finite-number、stable file read の
意味を新共通 primitive へ寄せる。ただし semantic validator は各 document owner に残す。
K1 の refactor で既存 command の出力、exit code、publication order、lease check orderを
変えてはならない。

### 10.4 後続 Issue への接続

- K2: `MissionState` とclosed variantsだけを受け取り、legacy passthroughを読まない。
- U1: `strict_reader` とcanonical JSON bytesを再利用するが、staging/publishは別責務。
- U2: K1のstate-generation codecをpayload ownerとして使い、head/commit/prepare codecと
  record別limitを追加する。`codec_v5.py` にhead/commit責務を逆流させない。
- A2: strict readerで取得しreference digest/sizeを検証済みのreview bytesだけを
  `decode_legacy_review_evidence` へ渡す。legacy statusは常にopenとしてpass gateへ渡す。
- A4: K1のPlan/Handoff unionをそのままcommand guardへ使い、provider resultをauthorityへ
  昇格させない。
- R1: `mission_persistence/reader.py` を新設し、missing/v1-v4 direct documentまたは
  verified v5 head -> commit -> generationをK1 decoderへ渡す。
- C1: max=4 compatibility readerとmax=5 authoritative readerを使い分け、新規initを
  切り替えるまでK1 codecをproduction writerへ接続しない。

### 10.5 `json.loads()` bypass path の後続 Issue への引き継ぎ

K1 は common strict primitiveを追加するが、production command routeの切替は行わない。
基準 SHA で確認済みの次の bypass path は、各 command familyをrepository portへ移す後続
Issueが担当候補となる。ownershipは
`docs/design/485-typed-kernel-migration-plan.md` §2.3 の command-surface routingに従い、
別の横断 cleanup Issueは起票しない。

| 現行位置 | command / bypass | 担当候補 | 判断理由 |
|---|---|---|---|
| `skills/mission/bin/mission-state.py:8301-8303` | `cmd_advance` が lock内で `json.loads(sf.read_text())` | A1 | lifecycle command と state mutation repositoryのownerがA1 |
| 同 `:13018-13020` | `cmd_aggregate_reviews` が lock内で直接stateを読む | A2 | review aggregation / score / pass authority boundaryのownerがA2 |
| 同 `:12184-12186` | `cmd_plan_import` が lock内で直接stateを読む | A4 | plan/provider evidence command familyのownerがA4 |

同じ plan family の `cmd_planning_adopt_core`（同 `:12311-12313`）も §3.1 で確認済みの
同種 bypass であり、A4 が併せて引き取る。各Issueでは common strict readerを単に呼ぶ
だけでなく、既存の lock、lease check、publication order、exception behaviorを保つ
characterization testを先に置く。GitHub Issueの起票は本設計更新のscope外とする。

## 11. Python 3.9 制約

全追加 `.py` は先頭に `from __future__ import annotations` を置く。次を許可する。

- `dataclasses.dataclass(frozen=True)`
- `class X(str, Enum)`
- `dict[str, T]`, `tuple[T, ...]`
- future annotations 下の `X | None`

次を禁止する。

- `match` / `case`
- `type Alias = ...`
- `dataclass(slots=True)` / `kw_only=True`
- `enum.StrEnum`
- `typing.Self`、標準 library 3.10+ API

D1 の `ast.parse(..., feature_version=(3, 9))`、canonical/plugin isolated import、実機
Python 3.9 import testを新 package 全体へ適用する。runtime annotationを評価する必要が
ある箇所は `typing.get_type_hints` を import 時に呼ばない。

## 12. TDD テスト計画

### 12.1 fixture の管理

新規 shared fixture builder を
`skills/mission/tests/mission_state_fixture_corpus.py` に置く。production code からは
import しない。

- #483 の `V1_STATE`, `V2_STATE`, `V3_STATE`, `V4_STATE`,
  `MISSING_SCHEMA_STATE` を shared builder へ移し、既存 test と K1 test が同じ object を使う。
- expected golden result は #483 test に残し、K1 側でコピーしない。
- current plan/handoff/review/score/lease/terminal corpus は下表の既存 test helper / writer
  から作る。static JSON を推測で作らず、integration fixture は current CLI で state を生成し、
  unit fixture はその exact field set を shared builder で再現する。

### 12.2 Issue 本文の TDD Red と assertion

| TDD Red | fixture source | 主な assertion |
|---|---|---|
| missing/v1-v4 golden decode | `test_issue483_schema_compat_matrix.py:101-195` shared fixtures | `schema_origin`, phase/control, score variant、terminal outcome、legacy passthrough、source bytes不変 |
| core/provider Plan | `test_issue465_core_canonical_plan.py::test_core_adoption_records_canonical_plan_and_exact_source_binding`; `test_planning_provider_lifecycle.py::test_provider_import_promote_advance_and_handoff_preserves_identity` | source/path/digest/source_digest/selection/iteration/generation/time が exact、core/provider union が正しい |
| 4 Handoff status | `test_issue465_core_canonical_plan.py::test_adopted_core_plan_advances_with_ordered_executor_handoff`; lifecycle の begin/complete/drift tests | prepared/consuming/consumed/rejected 全 variant、ordered step IDs、plan binding、variant-specific time/reason |
| Review input ref | `test_review_import.py::_review_bytes` と `cmd_review_import` success fixture | kind/path/digest/size/iteration/perspective、順序、no dereference |
| Review aggregate ref | `test_issue119_aggregate_reviews.py::_review` + aggregate + push; correlation lineage tests | digest/generation/revision scope、group/generation/base/head all-or-none、score との同一 binding |
| Finding / status normalization | 上記 review corpusに status missing/arbitrary/open/resolved/ADR-002未実装2語を追加 | legacy は全て OpenFinding、元 status/payload は frozen legacy payload、resolved authority field は生成されない |
| Score | #483 v4 entry、`test_push_score.py`、`test_score_provenance.py` の scoring-json/manual/force lineage fixtures | legacy と bound score を区別、全 scalar/items/open_high、review/manual/scoring refs、revision/force lineage を保持 |
| Lease | `test_issue354_session_lease.py::_lease_state`, legacy acquire, takeover/history fixtures | all-absent、complete、history を decode。4 field各 partial、bool/float epoch、invalid time/historyを拒否 |
| Terminal | `test_terminal_outcome.py:71-177,266-333` と #483 expected | K1 outcome が `mission_common` と全 case一致。active `None`、legacy conflict `failed` |
| v5 terminal projection | full v5 control fixture + projection helper spy | §7.2 の exact key setと供給元を固定。null `halt_category` / `terminal_outcome` と wireにない `resolution_status` は省略し、defaultを注入しない。explicit一致/不一致を検証 |
| CLI phase alias persistence risk | tracked state/golden corpus、`test_issue188_phase_enum.py`、`test_issue237_advance.py` の全4 alias | tracked fixture / writerにaliasがpersistされていないことを検索し、`set` / `advance` 後のdisk値がcanonicalであることをcharacterizeする。missing/v1-v4の各alias decodeは安定した拒否code + source bytes不変。alias実在時は実装前にmigration policyを再設計 |
| unknown legacy passthrough | 各 corpusへ unknown root/nested object/listを注入 | decode成功、deep mutation不能、`project_legacy_document` で unknown値をlossless保持、typed fieldが勝つ |
| source bytes不変 | 上記全 corpusのcanonical/pretty JSON bytes | equality + SHA-256、file bytes/stat identity、作成fileなし |
| v5 open round-trip | full `MissionState` model fixture | `decode(encode(x)) == x`、re-encode byte equality、canonical key order/finite number |
| v5 resolved round-trip | prior identity/evidence/timeを持つ read-only fixture | ResolvedFindingへdecode、3 required field保持、re-encode byte equality |
| no resolved producer | parser/import graph と `mission-migrate.py` current fixture | fresh interpreterで `from mission_kernel import encode_v5_state` は `ImportError`、package rootにencoder exportなし、production entrypointに v5 encoder/ResolvedFinding importなし、command inventory不変、migrate outputはv5でない |

### 12.3 adversarial matrix

`test_issue500_json_codec.py`:

- root/nested duplicate key
- invalid UTF-8
- `NaN`, `Infinity`, `-Infinity`, `1e999`
- JSON 後の prose / 2個目 document
- 4 MiB exact / 4 MiB + 1
- root non-object
- schema bool/string/float/null/0/6

`test_issue500_codec_v5.py`:

- top-level と各 closed nested object に unknown key を1つずつ追加
- 全 closed union の unknown variant
- Finding `accepted-risk`, `not-reproducible`, arbitrary status
- resolved required 3 field の各単独欠落・partial combination
- prior id mismatch、generation >= current、invalid evidence kind、naive/noncanonical time
- open に resolution field が付く case
- legacy-absent leaseを v5 wire に置く case
- legacy score sourceを v5 wire に置く case

`test_issue500_strict_reader.py`:

- regular single-link success
- symlink / FIFO / hard link
- oversize before open/read
- short read / append / truncate
- read 中 `fstat` change
- same-size final pathname swap (`lstat` identity change)
- platform に `O_NOFOLLOW` がない simulation の fail-closed

全拒否 case で Transition/effectは存在せず、source state/evidence bytesとdirectory listingが
不変であることを assert する。

### 12.4 regression / CI

最低 gate:

```text
/Users/<user>/dev/mission/.venv-ci/bin/python -m pytest -q -n auto --dist loadfile skills/mission
```

加えて K1 実装 PR では D1 recursive inventory、plugin sync、artifact hygiene、vendor
fingerprint、#483、terminal outcome、review import、score provenance、lease の targeted
testsを同一 HEAD で通す。

## 13. 実装順序（TDD）

1. shared corpus と新 test filesだけを追加し、missing imports / missing typesで Red を確認。
2. `json_codec` と `strict_reader` を最小実装し adversarial byte/file testsを Green。
3. `versions` を追加し、#483 wrapperを単一ソースへ移して #483 全緑。
4. frozen model と v4 decoderを実装し corpus / terminal / passthroughを Green。
5. v5 closed decoder、Finding status、canonical encoderを実装し round-tripを Green。
6. existing strict review wrapperを common primitiveへ寄せ、既存 review testsで behavior parity。
7. canonical/plugin mirror、Python 3.9/import、full suiteを実行。

各段階で production command route は増やさない。

## 14. PR 分割判断

**分割しない。K1 を 1 PR に収める。**

理由:

- model、legacy normalization、v5 closed vocabulary、strict byte boundary は相互に1つの
  decoder contractを構成し、どれかだけでは downstream が依存できない。
- runtime routeを変えないため、application/UoW変更を含む PR より risk が限定される。
- common primitive と model/codec は commitを分ければ review可能だが、merge gateは同じ
  exact HEAD で評価すべきである。
- PRを分けると「partial K1」が一時的に dependencyを満たしたように見え、K2/U1 の着手
  順を誤らせる。

PR 内では `tests -> common primitives -> v4 -> v5 -> mirror` の論理 commitに分ける。
K2/U1以下はK1 PR mergeまで待つ。

## 15. 確定事項と未解決事項

### 15.1 この設計で確定した事項

- public decoder は bytes-only
- frozen dataclass + Enum + closed Union
- 4 MiB state-generation upper bound
- v1-v4 unknown field passthrough / v5 unknown key reject
- legacy state decode と legacy review evidence decode の分離
- legacy Finding status は常に open、v5 は open/resolved のみ
- resolved の prior identity/evidence/time exact contract
- v5 codec と persistence writer/route の分離
- #483 version primitive と `mission_common` terminal logic の単一ソース化
- file rejectionを persistence layer、JSON rejectionを byte codec、schema rejectionを
  version/domain layerへ配置
- K1 は1 PR

### 15.2 未解決事項

K1 実装開始を妨げる未解決仕様はない。次は意図的な後続 scope であり、K1 で決めない。

- v5 head/commit/prepare record の exact schema と各 size limit（U2）
- immutable generation publish と collision protocol（U1）
- `ResolveFinding` の evidence producer / authorization / transition（別 ADR/Issue）
- v4 compatibility writer の production routing（A1以降）
- v5 new-session cutover（Stage 7）
