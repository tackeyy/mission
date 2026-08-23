# Issue #626: thin adapter 抽出と allowlist 静的ガード設計

## 1. 結論

ADR-006 の thin adapter 定義は変更しない。`mission-state.py` に残してよい責務は、
argparse 配線、parsed arguments から typed request への機械的変換、application use case
呼び出し、結果の表示と exit code 変換だけである
（`docs/adr/006-kernel-reducer-adjudication.md:116-124`）。

完了判定には、次の **baseline ratchet + 新規コード strict** を採用する。

1. parser が `set_defaults(func=...)` で公開する handler、`main` dispatcher、そこから
   参照される adapter 内 helper を AST call/reference graph で走査する。
2. 既存の allowlist 外ノードは、行番号ではなく「関数名 × rule id × 件数」の baseline
   と完全一致させる。baseline にない新規 handler/helper は違反ゼロを要求する。
3. PR の base SHA にある baseline と比較し、関数・rule id の追加と件数増加を拒否する。
   削除または件数減少だけを許可する。
4. 最初の実装 PR で `_derive_next_action` の判断本体を application 層へ移し、その
   baseline を実際に削除する。合成違反 fixture でも各 rule の検出力を証明する。

新規 handler だけを strict にする案は採らない。現 parser は 69 個の一意な handler を
公開し、`main` はその `args.func(args)` を動的 dispatch している
（`skills/mission/bin/mission-state.py:18560-19283`,
`skills/mission/bin/mission-state.py:19286-19387`）。既存 handler を無期限に免除すると、
今後の変更の大半に対して何も強制できないためである。

本設計はコード変更を含まない。実装時も既存の期待値は変更せず、既存テストを無修正で
green に保つ。

## 2. スコープと非目標

### 2.1 スコープ

- `skills/mission/bin/mission-state.py` の再計測と span 上位 15 の責務分類
- `_build_parser` の分割先と callback 注入方式
- legacy `_derive_next_action` の application 層への物理移動
- `_aggregate` を中心とする stats 集計の projection 層への物理移動
- adapter allowlist の AST 静的ガード、baseline、CI ratchet、合成 fixture
- source/plugin mirror と Python 3.9 配布契約

正典 CLI は `skills/mission/bin/mission-state.py` であり、root の
`scripts/mission-state.py` は `runpy` で正典へ委譲する wrapper である
（`scripts/mission-state.py:1-23`）。配布 mirror は canonical と byte-for-byte 同期する
既存 gate がある（`skills/mission/tests/test_plugins_in_sync.py:89-105`,
`skills/mission/tests/test_plugins_in_sync.py:311-320`）。新しい library module も recursive
inventory が自動発見し、mirror の存在・同一性・importability を検査する
（`skills/mission/lib/mission_python_inventory.py:33-76`,
`skills/mission/lib/mission_python_inventory.py:79-99`）。

### 2.2 非目標

- `mission_kernel.guidance.derive_next` への authority switch は行わない。現行設計は
  legacy `_derive_next_action` を parity 完了まで authority として保持する
  （`docs/design/501-k2-guidance-authority-decision.md:326-364`,
  `docs/design/501-k2-guidance-authority-decision.md:478-490`）。本 Issue では同じロジックを
  application 層へ移すだけである。
- stats の指標定義、分母、閾値、JSON/text schema は変更しない。
- kernel 化、stop hook の `GuardDecision` 化、各 sidecar protocol の意味論は変更しない。
- 行数や最大 span を thin 判定に使わない。`_build_parser` は 724 行でも責務自体は
  allowlist 内であり、逆に 3 行の threshold 比較でも allowlist 外なら違反とする。

## 3. 現在の実測

### 3.1 計測条件

- 基準: HEAD `ba5a87c1e97ef72d45375dcefb52fccb31958ab7`
- 対象: `skills/mission/bin/mission-state.py:1-19387`
- 総行数: `len(source.splitlines())`
- 関数数: module top-level の `ast.FunctionDef` / `ast.AsyncFunctionDef` のみ。nested
  function と class method は Issue #626 の旧値と母集団が異なるため含めない。
- 分岐数: `ast.walk(tree)` 中の `If`, `For`, `AsyncFor`, `While`, `Try`, `TryStar`,
  `Match`, `IfExp`, `BoolOp` の node 数。`BoolOp` は expression 一個を一分岐として数え、
  operand 数には展開しない。
- span: `end_lineno - lineno + 1`。module top-level 関数だけを対象にする。

再現用の最小スクリプトは次のとおりである。

```python
import ast
from pathlib import Path

path = Path("skills/mission/bin/mission-state.py")
source = path.read_text(encoding="utf-8")
tree = ast.parse(source)
functions = [
    node for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
]
branch_types = tuple(
    node_type for node_type in (
        ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
        getattr(ast, "TryStar", None), ast.Match, ast.IfExp, ast.BoolOp,
    )
    if node_type is not None
)
print(len(source.splitlines()))
print(len(functions))
print(sum(isinstance(node, branch_types) for node in ast.walk(tree)))
for node in sorted(functions, key=lambda item: (-item.end_lineno + item.lineno, item.lineno))[:15]:
    print(node.name, node.lineno, node.end_lineno, node.end_lineno - node.lineno + 1)
```

### 3.2 集計結果

| 項目 | 現在値 | 一次証拠 |
|---|---:|---|
| 物理行 | **19,387** | `skills/mission/bin/mission-state.py:1-19387` |
| top-level 関数 | **525** | 同ファイルを上記 AST 条件で計測 |
| 分岐 node | **3,468** | 同ファイルを上記 AST 条件で計測 |

分岐の内訳は `If=1,624`, `Try=367`, `IfExp=271`, `For=218`, `While=17`,
`BoolOp=971` で、`AsyncFor/TryStar/Match=0`、合計 3,468 である。

### 3.3 span 上位 15 と allowlist 分類

「該当」は関数全体が ADR allowlist 内にある場合だけを指す。表示を一部含んでいても、
判定、検証、集計、永続化、I/O orchestration を同じ関数が持つなら「非該当」とする。

| 順位 | 関数 | span | ADR allowlist | 現在持っている allowlist 外責務と証拠 |
|---:|---|---:|---|---|
| 1 | `_build_parser` | 724 (`18560-19283`) | **該当** | `add_parser/add_argument/set_defaults` による argparse 配線。末尾も parser を返すだけ（`skills/mission/bin/mission-state.py:18560-18582`, `skills/mission/bin/mission-state.py:19237-19283`） |
| 2 | `cmd_invoke_command_provider` | 487 (`5936-6422`) | 非該当 | provider 選択・preflight・timeout 決定、外部 process 実行、reserve/dispatch/result の永続化を所有する（`skills/mission/bin/mission-state.py:5985-6059`, `skills/mission/bin/mission-state.py:6116-6339`, `skills/mission/bin/mission-state.py:6361-6418`） |
| 3 | `cmd_aggregate_reviews` | 440 (`14687-15126`) | 非該当 | critic/min-reviewer gate、score 集約、artifact lint、evidence/output transaction と rollback を所有する（`skills/mission/bin/mission-state.py:14709-14824`, `skills/mission/bin/mission-state.py:14949-15031`, `skills/mission/bin/mission-state.py:15063-15108`） |
| 4 | `_initialize_legacy_v4` | 383 (`8033-8415`) | 非該当 | 初期 state schema と default policy を構築し、旧 state/archive/aggregate を移行・公開する（`skills/mission/bin/mission-state.py:8058-8132`, `skills/mission/bin/mission-state.py:8270-8347`, `skills/mission/bin/mission-state.py:8381-8400`） |
| 5 | `_supersede_reviews_locked` | 292 (`15912-16203`) | 非該当 | review generation を選別し、全 fence admission、kernel decision、複数 repository の順序付き publish/rollback を行う（`skills/mission/bin/mission-state.py:15939-15990`, `skills/mission/bin/mission-state.py:16004-16091`, `skills/mission/bin/mission-state.py:16104-16202`） |
| 6 | `_derive_next_action` | 290 (`9929-10218`) | 非該当 | terminal、halt recovery、stagnation、routing、planning/review/scoring の次手を決定する application policy そのもの（`skills/mission/bin/mission-state.py:9929-10036`, `skills/mission/bin/mission-state.py:10037-10162`, `skills/mission/bin/mission-state.py:10163-10218`） |
| 7 | `_discover_specialist_registry_candidates` | 276 (`3430-3705`) | 非該当 | registry I/O、precedence、invalid barrier、effective projection digest を導出する（`skills/mission/bin/mission-state.py:3430-3515`, `skills/mission/bin/mission-state.py:3517-3648`, `skills/mission/bin/mission-state.py:3650-3705`） |
| 8 | `_collect_worktree_archive_specs` | 241 (`6624-6864`) | 非該当 | archive 可否、identity/iteration、evidence allowlist、lineage/path safety を検証して archive spec を構築する（`skills/mission/bin/mission-state.py:6624-6666`, `skills/mission/bin/mission-state.py:6708-6755`, `skills/mission/bin/mission-state.py:6797-6864`） |
| 9 | `_aggregate` | 205 (`17619-17823`) | 非該当 | pass-rate、quality debt、context、activity、各 breakdown/率を集約する query projection（`skills/mission/bin/mission-state.py:17619-17683`, `skills/mission/bin/mission-state.py:17684-17756`, `skills/mission/bin/mission-state.py:17757-17823`） |
| 10 | `cmd_cleanup_stale` | 204 (`16766-16969`) | 非該当 | lease/PID/root/age/role を比較し、dry-run と terminalization 対象を判断する janitor policy（`skills/mission/bin/mission-state.py:16766-16838`, `skills/mission/bin/mission-state.py:16839-16918`, `skills/mission/bin/mission-state.py:16919-16969`） |
| 11 | `cmd_push_score` | 201 (`15221-15421`) | 非該当 | score input/provenance gate、composite 再計算、score history/failure ledger/stagnation mutation を所有する（`skills/mission/bin/mission-state.py:15221-15291`, `skills/mission/bin/mission-state.py:15300-15382`, `skills/mission/bin/mission-state.py:15383-15409`） |
| 12 | `cmd_specialists` | 185 (`4534-4718`) | 非該当 | task profile、candidate ranking/selection、phase plan、任意の state 記録を所有する（`skills/mission/bin/mission-state.py:4534-4609`, `skills/mission/bin/mission-state.py:4610-4710`） |
| 13 | `_terminalize_state_file` | 179 (`16562-16740`) | 非該当 | stale precondition の再検証、repository format 選択、janitor takeover、mark-halt use case の service 構築を所有する（`skills/mission/bin/mission-state.py:16562-16609`, `skills/mission/bin/mission-state.py:16610-16704`, `skills/mission/bin/mission-state.py:16705-16740`） |
| 14 | `cmd_log_specialist_invocation` | 177 (`7843-8019`) | 非該当 | threshold/required reason/selection/provider gate、invocation transition、evidence publish と state mutation を所有する（`skills/mission/bin/mission-state.py:7843-7898`, `skills/mission/bin/mission-state.py:7902-7988`, `skills/mission/bin/mission-state.py:7990-8019`） |
| 15 | `_legacy_lifecycle_repository` | 152 (`9439-9590`) | 非該当 | backup/CAS、lease admission、state/aggregate write、v4/v5 repository 選択を組み立てる persistence logic（`skills/mission/bin/mission-state.py:9439-9503`, `skills/mission/bin/mission-state.py:9505-9555`, `skills/mission/bin/mission-state.py:9557-9590`） |

### 3.4 #626 が直接抽出する優先リスト

span 順の全件表と、並行 Issue との ownership は分ける。A1/A2/A3/A4/A5/C1 の command
family は既存 registry で閉じている
（`skills/mission/lib/mission_application/command_owners.py:10-107`）。#626 は他 family の
kernel 化を横取りせず、次の三つを直接担当する。

| #626 内優先 | 対象 | 理由 |
|---:|---|---|
| 1 | `_derive_next_action` | 290 行の application policy。allowlist 外ロジックを実際に減らせるため、ガード導入と同じ PR で最初の ratchet 減少を証明する |
| 2 | `_aggregate` | 205 行の query projection。`cmd_stats` の collection/dedupe/render と集計を分離する |
| 3 | `_build_parser` | 724 行だが allowlist 内。family 単位 module へ分割して編集衝突と単一巨大関数を解消する。command family の並行作業後に行う |

`cmd_next` には `_derive_next_action` の後にも clock/budget による override が残る
（`skills/mission/bin/mission-state.py:10221-10271`）。`cmd_stats` にも root/snapshot の読取、
filter、dedupe が残る（`skills/mission/bin/mission-state.py:17969-18026`）。したがって三関数を
移しただけで handler 全体を「thin」と宣言せず、残余は ratchet baseline に残して後続抽出を
強制する。

## 4. allowlist 静的ガード

### 4.1 走査範囲

canonical source だけを分析し、plugin mirror は既存 byte equality gate に任せる。

1. `skills/mission/bin/mission-state.py`
2. 抽出後の `skills/mission/lib/mission_adapter/**/*.py`
3. root は parser の `set_defaults(func=<Name>)` から解決した handler、命名上の
   `cmd_*` / `_cmd_*`、および `main`
4. 各 root から、同じ adapter scope 内の top-level function を `ast.Name` の load で
   参照する edge を再帰探索する。直接 call だけでなく、service callback として渡す
   function object も edge に含める。
5. dynamic attribute call、文字列からの handler lookup、`getattr(module, name)` は解決不能
   call として違反にする。新しいロジックを helper 名変更や callback 化で隠せないようにする。

現 `main` は dispatch 後にも outcome tracking と typed error envelope を構築している
（`skills/mission/bin/mission-state.py:19286-19383`）。ここも adapter root として走査し、
generic error-to-output mapping だけを許す。

### 4.2 正の allowlist

#### A. argparse 配線

許すもの:

- `argparse.ArgumentParser`
- `add_subparsers`, `add_parser`, `add_argument`, `add_mutually_exclusive_group`
- `set_defaults`
- help/default/choices/dest/action/type を表す literal collection と文字列整形
- parser/helper の代入と parser return

許さないもの:

- state、clock、environment、filesystem を読む call
- `If/IfExp/Match/For/While/Compare/BoolOp` による command 有効性や default の動的判断
- application/persistence function の呼び出し

現 `_build_parser` は parser API と handler binding から成り、終端も parser return である
（`skills/mission/bin/mission-state.py:18560-18582`,
`skills/mission/bin/mission-state.py:19120-19145`,
`skills/mission/bin/mission-state.py:19281-19283`）。

#### B. typed request 変換

許すもの:

- `args.<field>` / `getattr(args, <literal>, <literal>)` の読取
- `str`, `int`, `float`, `bool`, `Path`, `tuple`, `list` の機械的変換
- `mission_application.*` から import した `*Request`, `*Observation`, `*Services`
  dataclass/protocol 実装の constructor
- local name への単純代入

許さないもの:

- raw state document の `.get`, subscript、`update`, `setdefault`, `pop`
- typed request に入れる前の比較、閾値適用、日時演算、default fallback の業務判断
- dict/list/set comprehension での選別・集計
- mutable dict を「request」と称して application へ渡すこと

#### C. use case 呼び出し

許すもの:

- import origin が `mission_application.*` または `mission_projection.*` の公開 callable
- repository/clock/provider 等を application port の実装として constructor へ渡すこと
- use case result の renderer への引き渡し

許さないもの:

- `mission_persistence.*`, filesystem, subprocess, datetime を handler から直接呼ぶこと
- adapter-local business helper、未解決 dynamic call、複数 use case 結果の adapter 内合成
- result field を比較して別 use case を選ぶこと

#### D. 表示と exit

許すもの:

- `print`, `json.dumps`, `sys.stdout.write`, `sys.stderr.write`
- `sys.exit` / `raise SystemExit`
- `mission_adapter.rendering` の renderer
- application の named failure を catch し、renderer と exit code へ一対一変換する限定 `try`

限定 `try` は `else/finally` なし、body は一回の use case 呼び出し、handler body は表示と
exit だけにする。retry、fallback、state mutation は許さない。text renderer 内の iteration は、
projection が返した既成 `lines` の出力だけを許し、field 比較や再集計は許さない。

### 4.3 違反 rule

AST node は行番号付き診断を返すが、baseline identity には行番号を含めない。

| rule id | 検出対象 | 具体例 |
|---|---|---|
| `control.branch` | `If`, `IfExp`, `Match` | phase/status/結果で処理を選ぶ |
| `control.loop` | `For`, `AsyncFor`, `While` | state/review/session を走査する |
| `logic.compare` | `Compare` | score、phase、status、時刻、件数の比較 |
| `logic.boolean` | `BoolOp` | 複数 field を組み合わせた gate |
| `logic.arithmetic` | `BinOp`, 業務値に対する `UnaryOp` | score 平均、elapsed、閾値差分 |
| `logic.comprehension` | list/set/dict/generator comprehension | filter、dedupe、count、projection |
| `logic.threshold-literal` | handler 内の数値 literal | `3`, `0.1`, `80.0`, `1_000_000`。`sys.exit(0/1/2)` と parser declaration だけ除外 |
| `state.raw-access` | state/data/document の subscript、`.get` | `data.get("phase")`, `state["passes"]` |
| `state.mutation` | subscript assign、`update/setdefault/pop/append/extend` | state/decision dict の組立・更新 |
| `logic.business-container` | handler 内の raw dict/set literal | dict の業務判定、score/result envelope の adapter 内合成 |
| `time.policy` | `datetime`, `timedelta`, `time`, `math` の演算/call | stale/budget/lease の判定 |
| `io.direct` | Path read/write/exists、`os`, `subprocess`, repository method | adapter が I/O orchestration を持つ |
| `call.non-allowlisted` | origin が allowlist 外、または dynamic で解決不能な call | local business helper への退避 |
| `dispatch.dynamic` | 文字列/globals/getattr による handler 解決 | call graph から処理を隠す |

この rule 群は、現コードで実在する違反形を直接捉える。例として、`cmd_push_score` は
score 平均と停滞幅を計算する（`skills/mission/bin/mission-state.py:15249-15257`,
`skills/mission/bin/mission-state.py:15391-15400`）、`cmd_cleanup_stale` は age/threshold/PID を
比較する（`skills/mission/bin/mission-state.py:16874-16919`）、`cmd_log_specialist_invocation`
は `0..1_000_000` threshold と status/reason gate を持つ
（`skills/mission/bin/mission-state.py:7847-7868`）。

### 4.4 baseline 表現

`skills/mission/tests/fixtures/thin-adapter-baseline.jsonl` を追加し、一関数一行の JSONL とする。

```json
{"path":"skills/mission/bin/mission-state.py","function":"cmd_example","rules":{"control.branch":3,"logic.compare":2}}
```

規則:

- key は `(repo-relative path, top-level function name)`。行番号と source digest は含めない。
- `rules` は rule id ごとの **現在の検出件数そのもの**。余裕枠を持たせない。
- 検出ゼロの関数は baseline に置かない。
- current scan と current baseline は完全一致させる。検出が減ったのに baseline を残すことも
  失敗にし、将来の再増加に使える headroom を作らない。
- record は path/function 順、rule id は辞書順に canonicalize し、重複 key、未知 rule、負数、
  bool-as-int、余分 field を拒否する。
- source から消えた function の baseline record は削除必須。rename した違反 function は
  「旧 record 削除 + 新 record 追加」となり、後述の base 比較が新規追加を拒否する。

### 4.5 baseline を減少方向に固定する CI

within-tree test だけでは、実装と baseline を同時に増やせてしまう。そこで PR の base SHA
にある baseline と current baseline を別に比較する。

1. `.github/workflows/ci.yml` の既存 `shell` job の checkout だけを full history にする。
   test shard 6 本の checkout は変更しない（現 job 構成は
   `.github/workflows/ci.yml:39-70`）。
2. `github.event.pull_request.base.sha` を argv ではなく env で
   `scripts/check-thin-adapter-ratchet.py` へ渡す。
3. script は `git show <base_sha>:<baseline-path>` を読み、current と比較する。
4. current の function key は base の subset、各 rule id も subset、各 count は
   `current <= base` を必須にする。新規 function/rule/count 増加は exit 1。
5. その後 current source scan と current baseline の **完全一致**を検査する。
6. `merge_group` では current source/baseline 一致を必須とし、base comparison は PR で既に
   通った baseline を前提にする。Quality は shell/test の明示 success を集約する既存構造を
  維持する（`.github/workflows/ci.yml:114-138`）。

この二重条件により、baseline を古い高い値のまま残すことも、新しい違反を baseline に追加する
こともできない。既存関数で同種 node を同数の別ロジックへ置き換える意味差までは AST count で
証明できないため、これは「業務意味論の証明」ではなく「adapter 構造予算の単調減少」ガードで
ある。既存 behavior test と独立 review は引き続き必要である。

### 4.6 合成違反 fixture

`skills/mission/tests/test_issue626_thin_adapter_guard.py` に、production source とは独立した
source string fixture を置く。少なくとも次を一つの composite offender と個別最小 fixture の
両方で検査する。

```python
def cmd_offender(args, state):
    deadline = datetime.now() + timedelta(minutes=30)
    if state.get("phase") == "reviewing" and args.score >= 4.0:
        chosen = {item["id"]: item for item in state["reviews"] if item["open"]}
        for item in chosen.values():
            state.setdefault("accepted", []).append(item)
    print(deadline)
```

期待する rule は `time.policy`, `control.branch`, `logic.compare`, `logic.boolean`,
`logic.threshold-literal`, `state.raw-access`, `logic.comprehension`, `control.loop`,
`state.mutation`, `logic.business-container` である。

あわせて次を固定する。

- `Request(value=str(args.value)) -> run_use_case(request) -> print(json.dumps(result)) ->
  sys.exit(0)` は違反ゼロ。
- `cmd_clean` から呼ぶ新規 local helper に比較を置いた fixture も検出する。
- parser fixture の `add_parser/add_argument/set_defaults` は通るが、parser helper が state を
  読む fixture は落ちる。
- detector が対象 node を一つずつ落とした mutation fixture で、対応 rule が消えることを
  確認する。検査器が常に空配列を返す退行を防ぐ。既存の no-write guard も production null
  だけを信用せず合成違反を持つ（`skills/mission/tests/test_issue619_a4_no_completion_writes.py:140-174`,
  `skills/mission/tests/test_issue620_kernel_a5_c1.py:194-208`）。
- ratchet comparator は synthetic base/current で「削除・減少は通る」「追加・増加・rename・
  未知 rule は落ちる」を検査する。

## 5. 抽出方針

### 5.1 `_derive_next_action` を application 層へ移す

#### 配置

- canonical: `skills/mission/lib/mission_application/next_action.py`
- mirror: `plugins/mission/skills/mission/lib/mission_application/next_action.py`

application package は既に typed request/use case の配置先であり、lifecycle module も
request dataclass と use case を持つ（`skills/mission/lib/mission_application/lifecycle.py:1-36`,
`skills/mission/lib/mission_application/lifecycle.py:39-77`）。

#### API

```python
@dataclass(frozen=True)
class NextActionRequest:
    document: Mapping[str, object]
    authoritative: NextStateView

@dataclass(frozen=True)
class NextActionServices:
    pregate_warning: Callable[[object], str | None]
    goal_dispatch_fields: Callable[[Mapping[str, object]], Mapping[str, object]]
    goal_dispatch_guidance: Callable[[Mapping[str, object]], str]
    expected_context_mode: Callable[[Mapping[str, object], int], str]
    valid_composite: Callable[[object], bool]

def derive_next_action(
    request: NextActionRequest,
    services: NextActionServices,
) -> dict[str, object]: ...
```

`NextStateView` は application module 内の `Protocol` とし、
`mission_persistence.authoritative_reader.AuthoritativeSnapshot` を import しない。adapter が現在の
authoritative reader から得た snapshot を structural typing で渡す。application -> persistence
の逆向き import を作らない。

現関数から、専用の pure helper
`_is_legacy_stale_halt`, `_halt_category_for_confirmation`, `_happy_path_sequence`,
`_native_review_handoff_hint`, `_unclosed_optional_specialist_skills` は同 module へ移す。
複数 consumer がある `_pregate_verdict_warning`, `_goal_dispatch_route_fields`,
`_goal_dispatch_guidance`, `_expected_context_mode`, `_is_valid_composite` は第一 PR では
`NextActionServices` で注入する。これらの既存 consumer は
`skills/mission/bin/mission-state.py:8205-8229`,
`skills/mission/bin/mission-state.py:14999-15000`,
`skills/mission/bin/mission-state.py:15391-15398`,
`skills/mission/bin/mission-state.py:17719-17731` にあり、一度に移すと別 capability へ広がるためである。

`mission-state.py` の `_derive_next_action` 名は既存 direct test 互換の thin facade として残し、
snapshot 補完、typed request 生成、use case 呼び出しだけにする。現在の K2 parity test はこの
公開名を direct call して全 corpus と比較している
（`skills/mission/tests/test_issue501_k2_parity.py:57-64`,
`skills/mission/tests/test_issue501_k2_parity.py:81-102`）。facade の stdout/return shape は変えない。

#### 挙動不変条件

- legacy `_derive_next_action` の全 return dict を key/value とも exact match
- `route-goal` の host-dependent output と legacy-required 扱いを維持
- `cmd_next` 後段の budget pressure/override の順序と envelope を維持
- malformed-but-readable v1-v4 の exception/return behavior を維持
- kernel `derive_next` への production selection authority 切替はしない

### 5.2 stats 集計を projection 層へ移す

#### 配置

- canonical: `skills/mission/lib/mission_projection/__init__.py`
- canonical: `skills/mission/lib/mission_projection/stats.py`
- 同じ相対 path の plugin mirror

既存にも planning provider KPI を「pure reduction」として独立させた precedent があり
（`skills/mission/lib/planning_provider_metrics.py:1-23`,
`skills/mission/lib/planning_provider_metrics.py:120-190`）、`_aggregate` はその reducer を既に
合成している（`skills/mission/bin/mission-state.py:17619-17629`,
`skills/mission/bin/mission-state.py:17819-17822`）。

#### 境界

- projection へ移す: `_aggregate` 本体、median/percentile、breakdown、histogram、artifact lint
  counts、reviewer output stats、phase totals など、入力済み observation の純粋集計。
- application/query collection に残す: root discovery、snapshot read、period filter、dedupe、
  archive/sidecar evidence read。
- adapter に残す: `StatsRequest` 生成、stats use case 呼び出し、JSON/text renderer 呼び出し、exit。

現 `_score_provenance_counts` は archive bundle/evidence reader を開き
（`skills/mission/bin/mission-state.py:17552-17576`）、`_command_outcome_counts` は sidecar record を
読む（`skills/mission/bin/mission-state.py:17580-17616`）。これらは pure projection にそのまま
移さない。application/query 側で `ScoreProvenanceObservation` と
`CommandOutcomeObservation` を作り、projection は observation を集計する。現
`_context_manifest_generated` も path を読み digest/schema を検証する
（`skills/mission/bin/mission-state.py:9879-9926`）ため、同様に boolean observation として渡す。

第一段階では `mission-state.py._aggregate` 名を thin compatibility facade として残す。既存 test
はこの関数を direct call して project/complexity/iteration と tier breakdown を固定している
（`skills/mission/tests/test_issue6_stats_breakdown.py:21-69`,
`skills/mission/tests/test_issue180_stats_by_tier.py:50-95`）。facade から typed projection input を
生成し、新 reducer の result をそのまま返す。text renderer `_format_text` は表示責務なので、
別 PR で `mission_adapter/rendering.py` へ移しても output は変更しない。

#### 挙動不変条件

- JSON object の key set、数値、`None`、順序に依存する text output を exact match
- empty states、legacy field 欠落、invalid/non-finite、snapshot/direct、sidecar/archive を維持
- `stats --json` と text の exit/stdout/stderr を維持
- existing stats tests を変更しない

### 5.3 `_build_parser` を family 単位へ分割する

#### 配置と循環 import 回避

- canonical: `skills/mission/lib/mission_adapter/__init__.py`
- canonical: `skills/mission/lib/mission_adapter/parser.py`
- 同じ相対 path の plugin mirror

`parser.py` は `mission-state.py` を import しない。代わりに、`mission-state.py` の thin
`_build_parser` が明示的な `Mapping[str, Callable]` を `build_parser(handlers)` へ渡す。
parser module は必要 handler key の完全一致を検査し、各 `set_defaults` では mapping から得た
callable を設定する。この方向なら hyphen を含む script module の import も、application から
adapter への逆 import も生じない。

`parser.py` 内は ownership と同じ family で分ける。

- `_add_lifecycle_commands`
- `_add_review_commands`
- `_add_evidence_commands`
- `_add_specialist_planning_commands`
- `_add_runtime_guard_commands`
- `_add_query_commands`
- `_add_separate_aggregate_commands`

family の管理元は command owner registry
（`skills/mission/lib/mission_application/command_owners.py:10-107`）とし、parser 独自の業務分類を
増やさない。handler mapping は argparse wiring として guard の parser allowlist に含める。

#### 挙動不変条件

- command/subcommand path、option strings、dest、required、nargs、action、type、choices、default、
  handler、`command_outcome_tracking` を normalized parser tree で exact match
- `--help` stdout と invalid argument の exit/stderr を characterization fixture で維持
- root wrapper と plugin mirror の起動を維持
- Python 3.9 parse/help/next gate を維持。既存 test は module inventory の全 `.py` を 3.9 grammar
  で parse する（`skills/mission/tests/test_issue99_py39_compat.py:15-49`）

## 6. TDD テストリスト

既存 test の期待値を書き換えて green にしない。以下を Red -> Green の順で追加する。

### 6.1 PR 1: guard + `_derive_next_action`

1. **Red: 合成 offender** — §4.6 の composite fixture が全 expected rule id を返す。
2. **Red: positive allowlist** — typed request -> use case -> render -> exit fixture はゼロ違反。
3. **Red: transitive helper** — clean handler から呼ぶ local offender を call/reference graph が検出。
4. **Red: baseline schema** — duplicate/unknown/negative/bool/extra field を拒否。
5. **Red: ratchet comparator** — add/increase/rename を拒否し、delete/decrease だけ受理。
6. **Red: actual source** — current scan と generated baseline が exact match。新規違反を一つ
   合成した source では baseline miss。
7. **Red: next application contract** — current legacy corpus の pre-move output を fixture にし、
   new use case/facade の output が exact match。
8. **Green: move** — `_derive_next_action` と専用 helper を application module へ移し、facade 化。
9. **Green: ratchet proof** — `_derive_next_action` の baseline record が削除され、base comparison が
   減少として通る。guard だけを入れて全件 baseline に逃がす状態を許さない。
10. **Regression** — `test_issue501_k2_parity.py`, `test_adr002_next_command.py`,
    `test_issue542_c1_core.py` の mixed v4/v5 `next` を無修正で通す。mixed root の stats/list/next/audit
    contract は `skills/mission/tests/test_issue542_c1_core.py:944-970` にある。

### 6.2 PR 2: stats projection

1. **Red: pure projection parity** — empty/single/mixed/legacy/invalid observation の old/new result exact match。
2. **Red: no I/O import** — `mission_projection.stats` が `pathlib`, `os`, `subprocess`,
   `mission_persistence`, `mission_adapter` を import しない AST guard。
3. **Red: observation boundary** — provenance/sidecar/context manifest の I/O failure は collection 側で
   現行と同じ count/exit へ変換され、projection は file path を開かない。
4. **Green: move** — `_aggregate` と pure helper を projection module へ移し、compatibility facade 化。
5. **Regression** — `test_stats.py`, `test_issue6_stats_breakdown.py`,
   `test_issue180_stats_by_tier.py`, `test_issue210_state_snapshot.py`,
   `test_issue352_bounded_context_observability.py`, provenance/command outcome tests を無修正で通す。

### 6.3 PR 3: parser 分割

1. **Red: normalized parser tree snapshot** — current `_build_parser` から全 parser action を再帰採取。
2. **Red: handler set exactness** — missing/extra handler mapping を fail closed。
3. **Red: parser allowlist** — declarative parser fixture は通り、state/clock/filesystem 分岐を混ぜた
   parser helper は落ちる。
4. **Green: family split** — parser module へ移し、thin facade から mapping を注入。
5. **Regression** — full CLI suite、root wrapper、plugin mirror、Python 3.9 help/next を無修正で通す。

## 7. 受け入れ条件

### 7.1 guard 導入 PR

- [ ] actual source scan と baseline が完全一致する
- [ ] PR base 比較で baseline function/rule/count の追加・増加が不可能
- [ ] baseline の余裕枠がなく、検出減少時は同じ PR で baseline も必ず減る
- [ ] 新規 handler/helper は baseline なしで strict zero
- [ ] synthetic offender が全 rule を検出し、positive fixture は通る
- [ ] transitive helper と dynamic dispatch escape を検出する
- [ ] `_derive_next_action` の application 抽出により baseline が実際に減る
- [ ] existing next/parity tests は期待値無変更で green
- [ ] canonical/plugin mirror は byte-identical、Python 3.9 compatible

### 7.2 各抽出 PR 共通

- [ ] stdout/stderr/exit code、JSON/text schema、state bytes、write/publish ordering を変更しない
- [ ] 既存 test file の期待値を変更しない。新規 characterization/architecture test の追加だけ可
- [ ] application は adapter/persistence を import せず、projection は I/O を行わない
- [ ] guard baseline は対象 function について削除または減少し、他 function を増やさない
- [ ] source/plugin mirror と recursive inventory gate が green
- [ ] full local test と独立 Checker が green/accepted

### 7.3 Issue #626 の close 判定

この task の指示どおり、全抽出完了ではなく **allowlist 外ロジックを増やせず、減少しか
許さない静的ガードが CI に入り、少なくとも一つの business extraction で実際の減少を証明した
時点**を close gate とする。第一候補は `_derive_next_action` である。

未実施の `_build_parser` 分割または stats projection が残る場合は、unchecked のまま暗黙に
close せず、独立 child Issue へ移して #626 本文に disposition と link を記録する。ガードが
残余を baseline として固定し、後続 PR で減少方向にしか変更できないことが条件である。

## 8. 変更対象ファイル

### PR 1: guard + next application extraction

- `skills/mission/bin/mission-state.py`
- `skills/mission/lib/mission_application/next_action.py`（新規）
- `plugins/mission/skills/mission/bin/mission-state.py`
- `plugins/mission/skills/mission/lib/mission_application/next_action.py`（新規）
- `scripts/check-thin-adapter-ratchet.py`（新規）
- `skills/mission/tests/fixtures/thin-adapter-baseline.jsonl`（新規）
- `skills/mission/tests/test_issue626_thin_adapter_guard.py`（新規）
- `skills/mission/tests/test_issue626_next_action_application.py`（新規）
- `.github/workflows/ci.yml`

### PR 2: stats projection

- `skills/mission/bin/mission-state.py`
- `skills/mission/lib/mission_projection/__init__.py`（新規）
- `skills/mission/lib/mission_projection/stats.py`（新規）
- 対応する plugin mirror 3 ファイル
- `skills/mission/tests/test_issue626_stats_projection.py`（新規）
- `skills/mission/tests/fixtures/thin-adapter-baseline.jsonl`（減少のみ）

### PR 3: parser 分割

- `skills/mission/bin/mission-state.py`
- `skills/mission/lib/mission_adapter/__init__.py`（新規）
- `skills/mission/lib/mission_adapter/parser.py`（新規）
- 対応する plugin mirror 3 ファイル
- `skills/mission/tests/test_issue626_parser_contract.py`（新規）
- `skills/mission/tests/fixtures/thin-adapter-baseline.jsonl`（parser が allowlist 内なら増加なし）

## 9. PR 分割と並行 Issue との衝突回避

| 順序 | PR | 主な source 範囲 | 衝突回避 |
|---:|---|---|---|
| 1 | guard + `_derive_next_action` | imports、`9929-10218`、CI/test/baseline | #615 の主対象 `cmd_stop_verdict` は `10340-10453` で直接範囲が分かれる。#624 の A4 と #633/#634 の A3 は ownership も別（`skills/mission/lib/mission_application/command_owners.py:37-71`, `skills/mission/lib/mission_application/command_owners.py:74-87`） |
| 2 | stats projection | stats helper 群 `17201-18026` | R1.query `stats` に限定し、A3/A4/C1 の mutation/kernel 変更を含めない |
| 3 | parser family split | `18560-19283` | parser は全 command registration を物理移動するため最も衝突しやすい。#633/#634/#624/#625 の parser option/binding 変更が落ち着いた後に実施する |

各 PR は一つの root function extraction を主単位にする。移動対象だけが使う private helper は同じ
PR に含めてよいが、別 command family の意味変更は含めない。guard baseline は一関数一行 JSONL
なので、並行 PR が異なる function の count を減らす場合は行単位で merge できる。base が移動した
場合は rebase/merge 後に baseline を再生成するのではなく、current scan の減少分だけを更新し、
base comparison と full test を再実行する。

## 10. 設計上のリスクと対策

| リスク | 対策 |
|---|---|
| baseline が既存違反の免罪符になる | current=baseline 完全一致、base からの増加禁止、新規 strict、第一 PR で実減少 |
| helper へロジックを移して逃げる | direct call だけでなく function reference graph を再帰走査。dynamic dispatch は違反 |
| application/projection へ移した後に循環 import | application は `Protocol` と service injection、parser は handler mapping injection、projection は observation input。いずれも CLI script を import しない |
| stats projection に I/O が混入 | provenance/sidecar/context manifest は application/query observation とし、projection の forbidden-import test を置く |
| parser 分割が並行 kernel PR と大衝突 | parser PR を最後にし、family 単位 helper へ一度だけ物理移動 |
| guard 自体が検出不能へ退行 | rule ごとの synthetic negative、positive、mutation fixture、ratchet comparator fixture |
| line move だけで baseline churn | identity から line/source digest を除外し、関数名 × rule id × count に限定 |
| 同種 node を同数の別ロジックへ置換 | static guard の限界として明記し、既存 behavior parity と独立 review を acceptance に残す |

