# Issue 502 U1: VerifiedBlobSet 集約上限の測定根拠と提案

## 結論

この文書は、U1 の `VerifiedBlobSet` に対する集約上限の**提案**を記録する。本変更では提案値を U1 tests/implementation へ入れたが、owner review で採否が確定するまでは確定仕様でも API 互換性約束でもない。
提案値は次のとおりである。

| 定数候補 | 提案値 | 対象 |
| --- | ---: | --- |
| `MAX_BLOB_COUNT` | 64 | 1 回の capture が返す `VerifiedBlobSet` の binding 件数 |
| `MAX_TOTAL_BLOB_BYTES` | `16 * 1024 * 1024` B (16 MiB) | 同 set 内の `binding.size` の合計 |

ここでいう総 bytes は capture 済みの immutable bytes の合計であり、manifest bytes は別に数える。state、個々の source/blob、manifest は既存の `STATE_LIMIT = 4 * 1024 * 1024` B に従う。

## 正典との整合

- `docs/design/502-U1.md` は、bounded source input を immutable `VerifiedBlobSet` として capture し、stage が digest/size を再検証することを要求する。
- ADR-005 §4 は、effect descriptor と capture 済み bytes の一対一対応、source の後続変更から stage を切り離すことを定める。ADR-005 §5 は、private stage が public write より前であることを定める。
- 移行計画 §12 は、v5 head/commit manifest の具体的な byte limit を K1/U1/U2 のテストで実装前に固定するとしている。

したがって、ここでの集約上限は個別ファイル上限を置き換えない。次のすべてを満たす必要がある。

1. 各 `BlobSource.limit` は `0 <= limit <= STATE_LIMIT`。
2. capture 済みの各 blob は source limit と `STATE_LIMIT` の両方を超えない。
3. state bytes と manifest bytes はそれぞれ `STATE_LIMIT` を超えない。
4. `VerifiedBlobSet` は件数が `MAX_BLOB_COUNT` 以下、`binding.size` 合計が `MAX_TOTAL_BLOB_BYTES` 以下である。

## 一次計測

### 計測方法

計測は既存の `generate_cli_state_corpus` を用い、隔離した一時 root で production CLI の state を生成した。呼出し側は `PYTHONPATH` と repository の primary checkout にある `.venv-ci/bin/python` を使用した。task worktree 直下には `.venv-ci` を置かない。fixture 内部は `sys.executable` で production の `mission-state.py` を subprocess 実行する。このため fixture の手製 state ではなく、現行 writer が on-disk に出力した state snapshot を測っている。

worktree から観測表を再集計する匿名化済みの inline 形は次のとおりである。corpus が on-disk JSON から取得した snapshot を production writer と同じ `json.dump(..., indent=2, ensure_ascii=False)` の bytes に戻し、実装の `stage_generation` に渡す。従って、出力行には表の `state B`、`VBS count`、`VBS B`、`public object count`、`object B`、`manifest B` がこの順で得られる。manifest overhead の比較条件を揃えるため、各 1-blob case の binding label は validator を通る固定値を使う。

```sh
PYTHONPATH=skills/mission/lib:skills/mission/tests \
  ../../.venv-ci/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mission_state_fixture_corpus import generate_cli_state_corpus
from mission_persistence.local_uow import (
    BlobBinding,
    VerifiedBlob,
    VerifiedBlobSet,
    stage_generation,
)


def writer_bytes(state):
    return json.dumps(state, indent=2, ensure_ascii=False).encode("utf-8")


def measure(root, name, state, source):
    state_content = writer_bytes(state)
    if source is None:
        blobs = VerifiedBlobSet(())
    else:
        content = source.read_bytes()
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        binding = BlobBinding(
            blob_id="measurement",
            kind="evidence",
            relative_path="snapshot-data.json",
            digest=digest,
            size=len(content),
        )
        blobs = VerifiedBlobSet((VerifiedBlob(binding, content),))

    staged = stage_generation(
        root / "measurement" / name,
        state_bytes=state_content,
        effects=tuple(blob.binding for blob in blobs.blobs),
        blobs=blobs,
    )
    vbs_bytes = sum(blob.binding.size for blob in blobs.blobs)
    return (
        len(state_content),
        len(blobs.blobs),
        vbs_bytes,
        1 + len(blobs.blobs),
        len(state_content) + vbs_bytes,
        len(staged.manifest_bytes),
    )

with TemporaryDirectory() as temporary_root:
    root = Path(temporary_root).resolve()
    corpus = generate_cli_state_corpus(root)
    rows = (
        ("init", "init", corpus["lease_acquired"], None),
        (
            "handoff prepared + plan",
            "handoff",
            corpus["handoff_prepared"],
            root / "handoff" / "plan-input.json",
        ),
        ("handoff consumed", "consumed", corpus["handoff_consumed"], None),
        (
            "second review import",
            "review",
            corpus["review_input"],
            root / "reviews" / "operability.json",
        ),
        (
            "aggregate / push-score",
            "aggregate",
            corpus["review_aggregate_and_bound_score"],
            root / "reviews" / "scoring.json",
        ),
        (
            "manual / push-score",
            "manual",
            corpus["manual_import_bound_score"],
            root / "manual-score" / "manual-scoring.json",
        ),
    )
    for label, name, state, source in rows:
        values = measure(root, name, state, source)
        print("| " + label + " | " + " | ".join(f"{value:,}" for value in values) + " |")
PY
```

各実 state は legacy writer の `json.dumps(state, indent=2, ensure_ascii=False)` が出す UTF-8 bytes と on-disk bytes の一致を確認してから集計した。これは任意の canonical JSON へ再整形した値ではなく、writer が実際に保存する表現を基準にするためである。`manifest B` は schema を手で近似せず、production の `stage_generation` が返した `manifest_bytes` を直接数える。

以下の bytes は**上記コマンドを最初に実行した測定 run の観測値**であり、普遍的な固定値ではない。`VBS B` は `binding.size` の合計、`object B` は state と各 blob がすべて異なる content である場合の bytes 合計である。generation manifest は `public object count` と `object B` から除外し、別の immutable generation file/record として `manifest B` に示す。

| snapshot | state B | VBS count | VBS B | public object count | object B | manifest B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| init | 2,874 | 0 | 0 | 1 | 2,874 | 238 |
| handoff prepared + plan | 4,406 | 1 | 790 | 2 | 5,196 | 501 |
| handoff consumed | 5,230 | 0 | 0 | 1 | 5,230 | 238 |
| second review import | 3,952 | 1 | 225 | 2 | 4,177 | 501 |
| aggregate / push-score | 7,469 | 1 | 1,259 | 2 | 8,728 | 502 |
| manual / push-score | 5,143 | 1 | 706 | 2 | 5,849 | 501 |

この測定 run の全 25 snapshot では、`provider-plan` が最大 state 14,671 B だった。state と一部 evidence には project root の絶対 path、PID、hostname 等の環境依存値が入り得るため、byte 長は環境と run に依存する。実際に最終検証 run では subprocess PID の桁数差により各 `state B` と `object B` が表より 1 B 小さくなったが、要求する再現対象の `VBS B` と `manifest B` は全行で表と一致した。上のコマンドは、その環境での全列を同じ集計方法で出力する。

### 測定範囲と限界

この corpus は現行 production CLI writer の状態表現を測るものだが、U1 の production route はまだ接続されていない。従って、これは live U2 commit の計測ではない。将来の 1 commit に相当する U1 の stage/publication を見積もるための state と public object 形状の測定であり、lease、fence、CAS、commit/head record、実際の recovery root の容量を実測したものではない。

また、表の object count は content がすべて異なる場合の上限形状である。同一 digest の content-addressed object が再利用されれば、実 object 数と bytes はこれより小さくなり得る。

## 提案の根拠

### 件数

提案根拠は corpus の byte 長の exact な固定値ではなく、観測した桁感と既存上限に対する比である。観測上の最大 `VerifiedBlobSet` 件数は 1 であり、同一 commit に対して累積参照した public object は state を含めて最大 2 だった。64 件は観測最大の 64 倍以上であり、複数 evidence を一度に扱う余地を残しつつ、無制限の descriptor 増加を拒否する。

`relative_path` validator は Python 文字数を 4,096 以下に制限し、安全な相対 POSIX path かを検査するが、JSON で escape される制御文字を除外していない。`ensure_ascii=False` でも U+0000 などは `\u0000` の 6 B に展開されるため、1 文字 4 B の Unicode scalar だけを最悪ケースにはできない。

実 schema と validator で、`blob_id` と `kind` を各 128 文字、`relative_path` を 4,096 文字、digest/object/size を各 field の最大長として直接 serialize した。blob なしの基底 manifest は、state size が `STATE_LIMIT` の 4,194,304 B の場合に 241 B である。4-byte UTF-8 scalar のみなら entry は 16,870 B、64 件全体は次の 1,079,984 B になる。

```text
241 + 64 * 16,870 + 63 = 1,079,984 B < 4,194,304 B
```

validator が許す U+0000 を 4,096 文字使うと entry は 25,062 B となり、これが JSON encoding 上の最悪ケースである。64 件全体は次の 1,604,272 B になる。

```text
241 + 64 * 25,062 + 63 = 1,604,272 B < 4,194,304 B
```

ここで 63 B は概算余白ではなく、64 entry 間に入る 63 個の comma そのものである。NUL case でも `STATE_LIMIT` に対して 2,590,032 B の余裕が残る。この計算は各 field の独立最大を同時に置く保守的上限であり、64 blob の `size` をすべて 4 MiB とする形は 16 MiB の aggregate limit よりさらに悲観的である。

再計算には、実 validator へ 64 binding を通し、production と同じ JSON option で実 schema 全体を encode する次のコマンドを用いた。

```sh
PYTHONPATH=skills/mission/lib \
  ../../.venv-ci/bin/python - <<'PY'
import json

from mission_persistence.local_uow import BlobBinding, _validate_binding
from mission_persistence.strict_reader import STATE_LIMIT


def encode(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


state_digest = "f" * 64
base = {
    "schema": "mission-generation/1",
    "state": {
        "digest": "sha256:" + state_digest,
        "size": STATE_LIMIT,
        "object": "objects/" + state_digest + ".blob",
    },
    "blobs": [],
}


def records(relative_path):
    result = []
    for index in range(64):
        binding = BlobBinding(
            blob_id=f"{index:02d}" + "a" * 126,
            kind="b" * 128,
            relative_path=relative_path,
            digest="sha256:" + "c" * 64,
            size=STATE_LIMIT,
        )
        _validate_binding(binding)
        result.append(
            {
                "blob_id": binding.blob_id,
                "kind": binding.kind,
                "relative_path": binding.relative_path,
                "digest": binding.digest,
                "size": binding.size,
                "object": "objects/" + "c" * 64 + ".blob",
            }
        )
    return result


print("base", len(encode(base)))
for label, relative_path in (("four-byte", "\U0001f600" * 4096), ("nul", "\x00" * 4096)):
    entries = records(relative_path)
    print(label, len(encode(entries[0])), len(encode({**base, "blobs": entries})))
PY
```

出力は `base 241`、`four-byte 16870 1079984`、`nul 25062 1604272` である。最終 schema でも encoded manifest を実測し、`STATE_LIMIT` で reject する必要がある。

### 総 bytes と一時領域

16 MiB は最大 4 MiB blob を 4 本まで許す。U1 の stage では、state 上限 4 MiB と blob 集約上限 16 MiB を合わせ、stage object 総量を最大 20 MiB に制約できる。capture 済み bytes と staged bytes を同時に保持する局面では、概ね 40 MiB 級となる。この値は性能目標ではなく、resource exhaustion を fail closed にする安全上限である。

この提案は corpus が示す state の桁感より十分大きく、同時に既存の個別上限と矛盾しない。正確には、各 blob/state と manifest はそれぞれ `STATE_LIMIT` 以下、blob の合計は別の aggregate limit である 16 MiB 以下となる。64 件を NUL 最大長で encode した保守的 manifest も 1,604,272 B で `STATE_LIMIT` 未満である。個別 blob を 4 MiB 以下にしても件数無制限なら合計は無制限になるため、per-source/blob/state/manifest の上限だけでは集約 resource の防御にならない。

## テストで固定した提案契約

移行計画 §12 の「tests before implementation」は、未確定値をコード先行で既成事実化しないための境界である、と解釈する。この解釈は本計画からの推論である。本変更では、U1/U2 の担当境界に合わせて次の契約を Red から開始し、現在は implementation と Green を完了している。値そのものは owner review による採否確定まで提案として扱う。

- capture: 65 件目を read 前に reject し、source bytes を読まない。
- capture: 各 read の前に残り total-byte budget を判定し、累積で上限を超える source を read しない。
- stage: forged `VerifiedBlobSet` を受けても、directory の作成や file write より前に件数・総 bytes を再検証して reject する。
- boundary: 件数 64 と総 bytes 16 MiB を accept し、それぞれ 1 増加または 1 B 超過を reject する。
- manifest: encoded manifest が `STATE_LIMIT` を超える場合、private stage から public publication へ進めない。

上記の capture と stage の二重検証は、正規 capture 経路だけに依存せず、構築済み object を直接渡す経路も同じ resource contract に従わせるためである。

## 採否時の確認事項

この提案を確定する前に、U1/U2 の実 manifest schema、descriptor 最大長、duplicate-content の object accounting、target platform のメモリ余力を確認する。いずれかが上記の 25,062 B/entry 測定または 40 MiB 級の一時保持と矛盾する場合、値を再計測してこの提案を更新する。
