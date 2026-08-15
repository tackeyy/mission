# Issue #501 K2: guidance authority の設計判断

## 1. 結論

**案 2 を採用する。** `derive_next` は `MissionState` だけでなく、closed typed な
`GuidanceFacts` を受け取る。ただし、caller が任意に組み立てた mapping を渡す API は
認めない。version-aware reader が同じ authoritative snapshot から
`MissionState` と `GuidanceFacts` を一度に生成し、同じ provenance へ束縛した
`Snapshot` だけを application 層へ返す。

概念上の API は次とする。

```python
@dataclass(frozen=True)
class Snapshot:
    state: MissionState
    guidance: GuidanceFacts
    provenance: SnapshotProvenance

def derive_next(state: MissionState, guidance: GuidanceFacts) -> GuidanceRecipe: ...
def decide(state: MissionState, command: Command) -> Decision: ...
```

application 層は `Snapshot` を分解して `derive_next` を呼ぶ。
`state` と `guidance` の provenance 不一致は呼び出し前または関数入口で拒否し、
guidance を返さない。`GuidanceFacts` は **guidance の選択だけ**に使い、command の
accept/reject、reducer、event、effect、pass、score、review、lease、terminal outcome の
authority には使わない。

`AdmittedSnapshot` という名前は ADR-005 §5 の write 側 UnitOfWork admission 専用として
維持する。K2 の read 側概念を既存 `MissionRepository.read() -> Snapshot` に統合し、
write 側 `AdmittedSnapshot` は `base: Snapshot` に pending acquire/renew/verify/takeover
decision と target lease/fence 値を加えた包含関係とする。これにより、永続化済み read model と
未 commit の lease decision を型名だけで区別でき、P1 が二つの同名型を統合する余地を残さない。

この判断により、単一 transition table は次の二つの predicate を同じ named rule に持つ。

- `command_guard(state, command)`: `decide` が使う。`GuidanceFacts` を参照しない。
- `guidance_guard(state, guidance)`: `derive_next` が使う。rule の command template または
  external observation + follow-up command を選ぶ。

local command を提示する rule は、materialize した command が同じ state に対する
`decide` で accept されることを property test で証明する。したがって二つの predicate を
置いても、独立した decision tree は作らない。

## 2. 判断の状態とスコープ

- 判断状態: **確定**。実装と既存 ADR / 設計文書の改訂はレビュー後の別作業。
- 証拠基準: `origin/main` の
  `118ab24526a5001458e53921dcafaa53103f0146`。
- 現 worktree の K2 branch は基準 SHA に設計 Issue 文書 1 commit だけを加えた状態で、
  本文書が参照する production code、ADR-005、#483 / #485 / #500 設計は基準 SHA と一致する。
- 本文書は design-only であり、production code、codec、ADR、test、state/evidence file を
  変更しない。

## 3. 確認した事実

以下は基準 SHA のコードまたは既存設計から確認した事実であり、提案ではない。

1. `MissionState` は `identity`, `control`, `plan`, `handoff`, `reviews`, `findings`,
   `scores`, `lease`, `extensions`, `legacy_passthrough` を持つ。
2. 依頼で列挙された 24 field のうち 11 field は K1 の typed model に既にある。
   `session_id` は `identity`、9 個の control field は `control`、`score_history` は
   `scores` に canonicalize される。
3. AST で `_derive_next_action` 本体の `data.get(...)` / `data[...]` を数えると直接参照は
   23 field である。依頼にある `session_id` は `_derive_next_action` 内ではなく、直後の
   `cmd_next` が response envelope に付加する。production の `next` 経路全体としては、
   依頼の 24 field 全てを読む。
4. `_derive_next_action` は `derive_planning_lifecycle`,
   `_goal_dispatch_route_fields`, `_unclosed_optional_specialist_skills` 等も呼ぶ。
   これらは 24 field 以外に `canonical_plan`, `planning_provider_binding`,
   `specialists_selected`, `provider_plan_imports`, goal dispatch field、実行 host 等を読む。
5. `cmd_next` は `_derive_next_action` の後に clock と `budget_minutes` / `started_at` から
   budget pressure を導出し、一部 spawn guidance を `consider-halt` に置き換える。
   よって 24 field を `MissionState` に足すだけでは production `next` の完全な pure parity
   には到達しない。
6. K1 設計と ADR-005 は、`legacy_passthrough` を v4 compatibility projection 専用とし、
   typed authority として扱わないこと、`extensions` も kernel decision authority に
   しないことを明記している。
7. K1 の v5 codec は top-level exact key set を
   `schema_version, identity, control, plan, handoff, reviews, findings, scores, lease, extensions`
   に固定している。K1 は v5 production writer / CLI route / migration command を追加していない。
8. #483 の compatibility contract は missing/v1-v4 を読み、v4 reader が v5 を future schema
   として fail-closed に拒否する。v1-v4 の物理 rewrite は行わない。
9. ADR-005 §1 は environment、clock、provider 等から得た値を adapter/application/port が
   validation し、typed fact として pure kernel へ渡す境界を既に認めている。一方、§3 は
   `derive_next` の追加 input、provenance、authority の範囲を定義していない。

主な一次証拠 anchor は次のとおりである。

| 証拠 | 基準 SHA 上の位置 |
|---|---|
| K1 aggregate shape | `skills/mission/lib/mission_kernel/model.py:441` |
| v1-v4 paired decode の起点候補 | `skills/mission/lib/mission_kernel/codec_v4.py:695` |
| v5 exact top-level / control set | `skills/mission/lib/mission_kernel/codec_v5.py:73-98` |
| v5 encode と passthrough 禁止 | `skills/mission/lib/mission_kernel/codec_v5.py:744-755` |
| legacy `next` decision tree | `skills/mission/bin/mission-state.py:8579-8858` |
| response enrichment / budget override | `skills/mission/bin/mission-state.py:8861-8911`, `:8208-8242` |
| transitive planning reads | `skills/mission/lib/planning_lifecycle.py:20-69` |
| passthrough / extensions の非権威契約 | `docs/design/500-mission-state-aggregate-implementation-design.md:221-229` |
| v5 production writer / CLI route 不在 | `docs/reports/issue-500-k1-implementation.md:70-72` |

## 4. 24 field の割り当て

### 4.1 凡例

- `State`: K1 の既存 `MissionState` field から読む。重複コピーを作らない。
- `GF`: 採用案の closed typed `GuidanceFacts` に置く。
- `GA`: 案 1 を採る場合の `MissionState.guidance_authority` 内の候補位置。
- `legacy_passthrough`: **24 field のうち、これだけで authority を満たす field は 0 個**。
  v1-v4 bytes の lossless 保持には使えても、K2 の guard/source には使えない。

### 4.2 field-by-field table

| # | flat field | 現行 `next` での役割 | K1 の既存 typed 位置 | 案 1 の所属 | 採用する案 2 の所属 |
|---:|---|---|---|---|---|
| 1 | `awaiting_user` | 回答待ちを最優先し action を止める | なし | `GA.routing.awaiting_user` | `GF.routing.awaiting_user` |
| 2 | `complexity` | Simple routing、Standard inline planning | なし | `GA.routing.complexity` | `GF.routing.complexity` |
| 3 | `critic_has_new_scope` | iter 2+ の reviewer 数、scope 記録、context mode | なし | `GA.review.critic_has_new_scope` | `GF.review.critic_has_new_scope` |
| 4 | `force_mission` | Simple mission の goal routing override | なし | `GA.routing.force_mission` | `GF.routing.force_mission` |
| 5 | `halt_category` | stale resume と manual reactivate を分離 | `control.halt_category` | 既存 `control` | `State.control.halt_category` |
| 6 | `halt_reason` | blocker guidance と terminal 導出 | `control.halt_reason` | 既存 `control` | `State.control.halt_reason` |
| 7 | `issue_ref` | Simple routing 抑止、pregate/issue identity の結合 | なし | `identity.issue_ref` を追加 | `GF.routing.issue_ref` |
| 8 | `iteration` | phase recipe、current score、diff review 判定 | `control.iteration` | 既存 `control` | `State.control.iteration` |
| 9 | `loop_active` | inactive session の resume 判定 | `control.loop_active` | 既存 `control` | `State.control.loop_active` |
| 10 | `passes` | completed guidance | `control.passes` | 既存 `control` | `State.control.passes` |
| 11 | `phase` | planning/executing/reviewing/scoring recipe | `control.phase` | 既存 `control` | `State.control.phase` |
| 12 | `planning_policy_version` | policy-v1 lifecycle の有効化 | なし | `GA.planning.policy_version` | `GF.planning.policy_version` |
| 13 | `planning_provider_required` | core adoption 禁止、required failure halt | なし | `GA.planning.provider_required` | `GF.planning.provider_required` |
| 14 | `planning_strategy` | core / primary / advisory planning 分岐 | なし | `GA.planning.strategy` | `GF.planning.strategy` |
| 15 | `pregate` | non-accepted verdict の planning warning | なし | `GA.advisories.pregate` | `GF.advisories.pregate` |
| 16 | `review_tier_signals` | Simple routing eligibility の risk signal | なし | `GA.review.tier_signals` | `GF.review.tier_signals` |
| 17 | `review_tier_source` | user override 時の Simple routing 抑止 | なし | `GA.review.tier_source` | `GF.review.tier_source` |
| 18 | `review_tier` | Standard inline planning から full を除外 | なし | `GA.review.tier` | `GF.review.tier` |
| 19 | `reviewer_count` | reviewer recipe と `--min-reviewers` | `control.reviewer_count` | 既存 `control` | `State.control.reviewer_count` |
| 20 | `score_history` | current score、findings evidence retry、pass guidance | `scores` | 既存 `scores` | `State.scores` |
| 21 | `session_id` | `cmd_next` response identity | `identity.session_id` | 既存 `identity` | `State.identity.session_id` |
| 22 | `session_role` | Simple routing を implementer に限定 | `control.session_role` | 既存 `control` | `State.control.session_role` |
| 23 | `specialist_invocations` | planning lifecycle と unclosed specialist の表示 | なし | `GA.providers.invocations` | `GF.providers.invocations` |
| 24 | `stagnation_count` | 3 回停滞時の consider-halt | `control.stagnation_count` | 既存 `control` | `State.control.stagnation_count` |

採用案の内訳は `MissionState` 11 field、`GuidanceFacts` 13 field、
`legacy_passthrough` only 0 field である。

`score_history -> scores` は単なる key rename ではない。K1 decoder が legacy score と
provenance-bearing score を closed union へ変換した結果を使う。同様に
`specialist_invocations` は raw list をそのまま渡さず、少なくとも status、phase、iteration、
invocation identity、required/selection/evidence binding を持つ closed invocation variant とする。

`terminal_outcome` は 24 field 表へ追加する stored authority ではなく、transition table が
typed control state から読む **computed property** とする。v5 wire が materialized
`control.terminal_outcome` を保持する場合も、`derive_terminal_outcome` と同様に control field との
一致を decoder/invariant で検証し、raw stored value 単独では rule eligibility を決めない。

`issue_ref -> GF.routing` は K2 の query selection に限る暫定配置である。A4 で planning provider
dispatch の command guard、plan/handoff identity、または effect binding に必要と判明した場合は、
command-owned typed identity か verified observation へ昇格し、`GuidanceFacts` を `decide` に渡して
再利用しない。分類が閉じるまでは A4 の production switch を許可しない。

## 5. 二案の比較

| 評価軸 | 案 1: `MissionState.guidance_authority` | 案 2: bound `GuidanceFacts` | 判断 |
|---|---|---|---|
| 単一 input | 最も単純。pair mismatch がない | provenance binding が必要 | 案 1 が有利 |
| domain aggregate の凝集 | routing、warning、provider projection、runtime observation まで aggregate に入る | query projection を domain state から分離できる | 案 2 が有利 |
| pure kernel | persisted fact だけなら pure | immutable typed input なので pure | 同等。ただし案 2 は外部 fact を自然に扱える |
| production `next` parity | 24 field は収容できるが host/clock/budget で別 input が結局必要 | state、policy、sidecar、host/clock observation を一つの admitted query input にできる | 案 2 が有利 |
| authority の安全性 | aggregate validation だけで閉じやすい | bare dict なら危険。bound `Snapshot` 契約で閉じる必要 | 条件付きで同等 |
| v5 schema への影響 | `MissionState` と v5 wire の双方が恒常的に肥大化 | v5 wire には closed `guidance` が必要だが、domain model は増やさない | 案 2 が有利 |
| pregate 等の別 aggregate | aggregate 内への複製または参照が必要 | verified projection として扱える | 案 2 が有利 |
| policy の将来変更 | guidance tuning ごとに aggregate/schema 変更が起きやすい | versioned `GuidanceFacts` 内で閉じて変更できる | 案 2 が有利 |
| 実装量 | 小さい | paired decoder、provenance、mismatch test が増える | 案 1 が有利 |

案 1 は短期実装が単純だが、`MissionState` を「mission の command authority」から
「`next` が現在表示するために必要な全 query input」へ変えてしまう。特に pregate は
ADR-005 が別 aggregate と明記し、host と clock は persisted state ではない。結局
`MissionState` 以外の typed input が必要になるため、案 1 の単一 input という利点は
production parity では成立しない。

## 6. 採用案の契約

### 6.1 `GuidanceFacts` の形

`GuidanceFacts` は flat dict にせず、少なくとも次の closed object に分ける。

```text
GuidanceFacts
├── routing: RoutingFacts
│   └── awaiting_user, complexity, force_mission, issue_ref
├── planning: PlanningGuidanceFacts
│   └── policy_version, provider_required, strategy
├── review: ReviewGuidanceFacts
│   └── critic_has_new_scope, tier, tier_source, tier_signals
├── advisories: AdvisoryFacts
│   └── pregate
├── providers: ProviderGuidanceFacts
│   └── invocations
└── provenance: GuidanceProvenance
```

`MissionState` に既にある 11 field は `GuidanceFacts` に複製しない。rule は typed accessor
経由で `state` または `guidance` のどちらか一方だけから読む。

### 6.2 provenance: 誰が組み立てるか

1. authoritative reader / repository adapter が state bytes を strict reader で一度だけ読む。
2. version dispatcher が同じ parsed immutable document から `MissionState` と
   `GuidanceFacts` を生成する。
3. v1-v4 では current flat field を field-explicit に decode する。
   `legacy_passthrough.thaw()` を K2 から呼ばない。
4. v5 では head -> commit -> generation の lineage 検証後、同じ generation document の
   closed `guidance` object を decode する。
5. reader が `schema_origin`, `session_id`, document digest、および v5 では generation /
   commit identity を `SnapshotProvenance` に記録する。
6. provider、CLI renderer、transition rule、`extensions`、任意 sidecar は
   `GuidanceFacts` を直接生成できない。

`GuidanceFacts` の constructor は module-private とし、public surface は
`decode_snapshot(bytes)`、将来の R1 reader、test fixture factory に限定する。
test factory が作った provenance は production adapter から受理しない。

pregate cache 自体は ADR-005 §1 の separate aggregate のままであり、`Snapshot` の provenance
binding 対象に live cache や sidecar 全体を含めない。K2 が `GuidanceFacts.advisories.pregate` に
取り込むのは、同じ session document / v5 generation に保存済みで、cache record の
`issue_ref`、subject digest、verdict、gate identity、評価時刻を指す validation 済み closed
projection だけである。したがって provenance binding はこの **pregate projection の bytes を
含むが、live separate aggregate は含まない**。paired decoder は query 時に pregate cache を
再読込・join しない。
将来 live cache の再評価結果を使う場合は、separate aggregate の protocol で検証した typed
observation を application 層が新しい state commit に取り込んでから、次の `Snapshot` を読む。

### 6.3 validation

- `awaiting_user`, `force_mission`, `planning_provider_required` は bool/optional bool の
  exact semantics を持ち、integer を bool として受けない。
- `complexity`, planning strategy、review tier/source、invocation status は closed enum。
- `critic_has_new_scope` は `None | False | True` の tri-state を維持する。
- count/iteration は bool でない bounded integer。
- `issue_ref`、signal、reason、invocation identity は length bound と canonical form を持つ。
- pregate は warning に必要な closed projection だけを持ち、pass/transition gate にしない。
- invocation は status ごとの required field と lineage binding を検証し、unknown status /
  partial identity を fail-closed にする。
- v1-v4 の missing default は current behavior の characterization fixture で固定する。
  malformed legacy valueを新しい都合で黙って default にしない。現行が受理する値を closed
  type へ正確に表せない場合は、明示 `legacy-unknown` variant を設けるか production switch を
  止める。
- v5 closed object は missing/unknown key、unknown variant、型違反を fail-closed にする。
- `state.provenance != guidance.provenance`、session mismatch、generation/digest mismatch は
  guidance なしの typed rejection とし、片方への fallback をしない。

### 6.4 authority

kernel が `GuidanceFacts` を信頼してよい根拠は「値がもっともらしい」ことではなく、
authoritative reader が同じ persisted `Snapshot` から生成したことに限定する。

`GuidanceFacts` が許されること:

- primary guidance rule の eligibility / rank / typed continuation の選択
- warning、context mode、reviewer recipe、外部 observation 要求の表示
- command template の非権威な parameter 候補の生成

`GuidanceFacts` が許されないこと:

- command rejection を accept に変える
- state reducer、event、effect を生成する
- pass、score、finding、review、provider result、lease、terminal outcome を確定する
- `extensions`、raw provider output、未検証 sidecar、`legacy_passthrough` を authority に昇格する

現行で missing 13 field の一部が mutating command の gate にも使われている場合、その
command を A1-A5 で抽出するときは command-owned typed state または verified observation に
昇格させる。`GuidanceFacts` を `decide` へ渡して近道にしてはならない。

### 6.5 ADR-005 の pure kernel 原則との整合

pure とは「引数が一つ」であることではなく、同じ immutable typed input に対して同じ出力を
返し、I/O、clock、environment、process、provider、mutable global state を読まないことである。
adapter が host/clock/sidecar を読んで validation 済み fact に変換し、kernel がその値だけを
読む形は ADR-005 §1 の dependency direction と一致する。

clock や host を `MissionState` に保存して pure に見せかける案は採らない。runtime observation
は observation time/source を持つ typed fact とし、再実行時は adapter が新しい snapshot を
作る。

## 7. v4 / v5 wire contract への影響

### 7.1 v1-v4

- 既存 flat JSON、schema version、writer、bytes を変更しない。
- paired decoder が既知 13 field を explicit に読み、同じ bytes から `GuidanceFacts` を作る。
- unknown field は従来どおり `legacy_passthrough` に保存するが、guidance authority にはしない。
- #483 の missing/v1-v4 read と「v4 reader は v5 を拒否」は変更しない。
- production `next` は shadow parity が成立するまで legacy `_derive_next_action` を authority とする。

### 7.2 v5

案 2 でも v5 wire 変更は必要である。`GuidanceFacts` を non-authoritative `extensions` に
入れることはできないため、v5 state-generation top-level exact set に required closed
`guidance` object を追加する。これは `MissionState` field ではなく、同じ generation に束縛された
query projection である。

```text
schema_version, identity, control, plan, handoff,
reviews, findings, scores, lease, guidance, extensions
```

別 file/sidecar にすると state と guidance の atomicity、CAS、recovery、GC が増えるため採らない。
同じ canonical generation document に置き、head/commit/generation の一つの lineage で保護する。

K1 は v5 production writer / CLI route を作っていないため、v5 activation 前に draft v5 contract
を改訂する。schema number は 5 のままとする。ただし実装着手前の repository scan で production
または配布済みの v5 state bytes が一件でも確認された場合、この前提は崩れるため同じ version を
再定義せず v6 を設計する。

### 7.3 案 1 を採った場合の wire impact（比較のため）

案 1 なら `MissionState.guidance_authority` は in-memory だけに置けない。v5 writer が復元可能で
なければ command 後に guidance が失われるため、v5 top-level に required closed object として
含める必要がある。`extensions` は authority 不可であり、非永続 property も replay/parity を壊す。

その場合も #483 の v4 decoder は v5 を拒否し続け、v1-v4 writer は flat bytes を維持できる。
ただし K1 の `MissionState` equality、v5 exact key set、codec round-trip、legacy projection、fixture
corpus を全て更新する必要があり、policy/advisory の変更まで aggregate schema change になる。

## 8. 単一 transition table と parity gate

K2 の transition-table parity gate が比較する対象は、clock-dependent な application wrapper を
適用する前の legacy `_derive_next_action` 出力と、新しい `derive_next` の normalized guidance
出力である。`cmd_next` が後段で付加する response envelope と、`iso_now()`、`budget_minutes`、
`started_at` に依存する budget pressure / `consider-halt` override は K2 の equivalence class に
含めない。K2 の production switch は `cmd_next` 内の selection authority だけを差し替え、budget
override は application 層の既存 contract として維持し、別の integration regression test で
出力順序と override 条件の非回帰を確認する。clock を transition table の state class に偽装して
取り込まない。

table definition は named rule ごとに次を持つ。

```text
rule_id
command_type
command_guard(state, command)
reducer(state, command)
events / effects
primary_guidance_eligible
guidance_guard(state, guidance)
rank / stable_tie_break
command_factory or external_observation + follow_up_command
continuation_edges
```

K2 の production switch 前に次を満たす。

1. 既存 `next_action` fixture の各 case が一つの named rule に対応する。
2. 同一 `Snapshot` から legacy/new normalized guidance が exact match する。
3. local command は `decide` で accept され、各 continuation は直前の resulting state で accept される。
4. external step は observation 不在で follow-up が reject され、named verified observation だけを
   加えた後に accept される。
5. pass guidance は同じ state に対する pass command が accept される場合だけ出る。
6. forged/mismatched/stale guidance provenance は no-guidance rejection になる。
7. duplicate rule、missing primary guidance、equal-rank tie、unknown typed variant は table build/test を
   fail させる。
8. 24 field だけでなく §3 の transitive/runtime dependency inventory が空になるまで、legacy
   `_derive_next_action` と `cmd_next` override を削除しない。
9. ADR-005 §3 の `derive_next(state, guidance_facts)` signature、trust contract、property suite の
   改訂が Accepted になっている。改訂未 accept のまま K2 production switch を行わない。

## 9. 後続 Issue への影響

| Issue | 変更点 | 変えない安全境界 |
|---|---|---|
| A1 lifecycle use cases | `MissionRepository.read` は read 側 `Snapshot` を返す。lifecycle command は `state` だけで `decide` し、`next` だけが guidance も使う。generic `set` で guidance provenance を作れないようにする | lease、terminal/reactivation/stale recovery の分離 |
| A2 review/score/pass | `critic_has_new_scope` と review policy は guidance projectionとして読めるが、aggregate/finalize command の gate は command-owned typed state/verified observationへ昇格する。pass rule は guidance を読まない | ADR-003 gate、findings provenance、open High、force approval、pass authority |
| A3 artifact/progress/context | context mode と warning は guidance を使える。artifact/progress/context effect は従来どおり validated blob/state binding だけを使う | publication ordering、content identity、pass 非干渉 |
| A4 plan/handoff/provider | 最大影響。provider lifecycle の authoritative recordから closed invocation projectionを作る。provider outputやrendererが `GuidanceFacts` を生成してはならない | provider は evidence providerのみ。plan/handoff identity と dispatch/reconcile safety |
| A5 runtime guard observations | `awaiting_user` 等は validated observation writerから読み取る query factにする。runtime observationから phase/pass/halt/leaseを書けない | Python verdict authority、unknown/denied の fail-closed |
| P1 repository selection | v4/v5 共通 port は同一 load で state+guidance+provenanceを返す。v5 は同じ generation/CAS、v4 は同じ file bytesに束縛する | format pin、single writer、fence/CAS、no dual-write |
| R1 reader migration | version-aware authoritative reader が read 側 `Snapshot` の唯一の production factoryになる。state-only consumerは `.state`、`next` は pairを使う | unreadable v5 を inactive/empty に fallbackしない |
| C1 v5 cutover | cutover gate に required `guidance` object、24 field coverage、transitive/runtime inventory、全 `next` branch の v5 E2E を追加する | old v4 sessionはv4のまま、v4 readerはv5を拒否、rollbackはinit defaultのみ |

依存順は維持できるが、K2 の最初に `GuidanceFacts` / read 側 `Snapshot` / paired codec の
contract test を置く。これが通るまで transition table 実装へ進まない。A1-A5 で command
authority へ昇格した field は、query builder の source を順次 legacy flat projection から
typed owner へ差し替える。

## 10. ADR / 設計文書の改訂要否

**ADR-005 の改訂が必要である。** 現行 §1 は typed fact の受け渡しを許しているが、§3 の
`derive_next` input と trust contract が未定義で、現行 §2 の v5 exact wire に `guidance` がない。

レビュー後の別作業で、少なくとも次を改訂する。K2 実装計画ではこの改訂を独立した backlog
ticket として起票・K2 から link し、ADR-005 §3 の改訂が review を経て Accepted になることを
§8 の production switch gate にする。ticket が未起票、未 review、未 accept のいずれかなら、
paired codec や shadow parity の準備はできても production authority は切り替えない。

### ADR-005 §1 Boundary and dependency direction

- read/query input に `Snapshot(MissionState, GuidanceFacts, provenance)` を追加する。
- application/reader だけが bound pair を生成できることを明記する。
- `GuidanceFacts` は query selection authority であり、transition/completion authority ではないと
  明記する。

### ADR-005 §2 Versioned typed aggregate

- `MissionState` aggregate 自体は拡張しない。
- v5 state-generation exact top-level に required closed `guidance` を追加する。
- missing/v1-v4 は同一 immutable documentから paired decodeし、physical rewriteしない。
- `legacy_passthrough` / `extensions` は GuidanceFacts source にできないことを明記する。

### ADR-005 §3 One transition table

- signature を `derive_next(state, guidance_facts)` と明記する。
- 同じ rule object 内の `command_guard` と `guidance_guard` の責務差を明記する。
- local command の `decide` acceptance、provenance mismatch rejection、runtime observation
  fixture を property suite に追加する。

### ADR-005 §4 Effect model / repository port

- `MissionRepository.read()` result を read 側 `Snapshot` とし、effect staging/commit input は従来どおり
  transition と verified blob に限定する。
- GuidanceFacts から effect を直接生成できないことを明記する。

### ADR-005 §5 UnitOfWork protocol

- 現行 write 側 `AdmittedSnapshot` の名称と意味を維持し、K2 の read 側では使用しない。
- `Snapshot` を persisted read result として、canonical `MissionState`、closed `GuidanceFacts`、
  head generation/digest を含む `SnapshotProvenance` の組と定義する。
- `RecoverableUnitOfWork.begin(request) -> AdmittedSnapshot` は `base: Snapshot`、pending
  acquire/renew/verify/takeover decision、pending target lease/fence を反映した admitted state を
  包含する。`stage(admitted, ...)` はこの write-side admission だけを受ける。
- `MissionRepository.read() -> Snapshot` は pending lease decision を生成せず、`derive_next` の read
  path で使う。`begin()` が返す `AdmittedSnapshot` やその pending lease/fence を guidance authority
  として再利用しない。

この **read 側を `Snapshot` に統合し、write 側 `AdmittedSnapshot` がそれを包含する**案を選ぶ。
既存 §5 の admission/CAS 語彙を壊さず、repository port の既存 `read() -> Snapshot` と K2 paired
decode を一つの結果型にでき、未 commit lease decision が通常の guidance read に混入しないためである。

### `docs/design/485-typed-kernel-migration-plan.md`

- Stage 1/2 と K2 に paired codec、GuidanceFacts、provenance parity gate を追加する。
- A1-A5、P1、R1、C1 を §9 の影響に合わせて更新する。
- dependency diagram は変更不要だが、K2 内の最初の gate を明示する。

### `docs/design/500-mission-state-aggregate-implementation-design.md`

- v5 exact top-level set と decode API の記述を更新する。
- 「K2 は passthrough を authority に使わない」は維持し、paired decoder の field-explicit
  legacy projection を例外ではなく正規経路として追記する。
- K1 実装済み codec の amendment test scope を記載する。

ADR-002 と ADR-003 の decision 自体は改訂不要である。ADR-003 の review tier provenance と
pass gate 非干渉を、GuidanceFacts validation fixture として再利用する。

## 11. 却下する実装形

次は現行の安全境界を弱めるため採用しない。

- `derive_next(state, dict)` として CLI/provider が任意 mapping を渡す。
- `legacy_passthrough` または `extensions` を直接読む。
- state と guidance を別 read/別 lock/別 generation から組み合わせる。
- missing/corrupt guidance を empty/default にして処理を続ける。
- provider output に tier、pass、score、terminal、lease、command acceptance を決めさせる。
- pregate warning を pass/transition gateへ昇格する。
- `GuidanceFacts` を reducer/effectへ渡して command guardを迂回する。
- v5 guidance を独立 sidecar に置き、state commit と別 lifecycle にする。

## 12. 確認した事実と提案の境界

| 区分 | 内容 |
|---|---|
| 確認済み | 基準 SHA、K1 model/codec、23 direct field + `cmd_next.session_id`、helper/runtime の追加依存、legacy passthrough/extensions の非権威性、v5 production writer/route 不在、#483 contract |
| 本文書で確定した提案 | 案 2、read 側 `Snapshot`、write 側 `AdmittedSnapshot` との包含関係、closed `GuidanceFacts`、同一 snapshot provenance、guidance-only authority、v5 required `guidance` object、ADR-005 改訂方針 |
| 後続実装で証明すること | legacy/new exact parity、全 branch の command executability、malformed corpus、transitive/runtime dependency inventory、v4/v5 repository equivalence |

## 13. 未解決事項

設計選択自体に未決はない。実装前後に閉じる必要がある evidence task は次のとおり。

1. 依頼の 24 field 外にある transitive dependency を AST/call-graph inventory と fixture で
   全件固定する。少なくとも planning binding/selection/import、goal dispatch、budget/clock/host、
   optional specialist accounting が対象である。暫定分類として、planning
   binding/selection/import と optional specialist accounting は `GuidanceFacts.providers`、
   persisted routing input の
   `goal_dispatch_requested` と `goal_dispatch_source` は guidance selection 専用の
   `GuidanceFacts.routing`、`detect_host()` の結果は observation time/source を持つ A5 runtime
   observation、`goal_dispatch_effective` は両者からの computed property とする。
   `goal_dispatch_resolution_fallback_reason` を含む全 field の owner と provenance を
   inventory/fixture で確定するまでは、
   これらを読む legacy `_derive_next_action` を authority のまま維持する。
2. v1-v4 の malformed-but-currently-readable value を characterization し、closed variant で
   parity を表せない case がないか確認する。表せなければ production switch を止める。
3. v5 bytes が production/配布物に存在しないことを実装開始時に再確認する。存在する場合は
   v5 exact set を再定義せず v6 にする。
4. K1 の v5 codec round-trip / exact-key test が `_TOP_LEVEL` への required `guidance` 追加後も
   全て green であり、encode -> decode -> canonical encode と unknown/missing-key rejection が
   guidance を含む形で維持されることを確認する。
5. A1-A5 で `critic_has_new_scope`、planning policy、provider invocation 等を command-owned
   typed state/observationへ昇格する具体的 command schema は各 Issue で確定する。
6. base drift 後は基準 SHA を更新し、field inventory と ADR amendment を再照合する。

これらは案 2 を再選択するための論点ではなく、K2 production switch と後続 cutover の
acceptance evidence である。
