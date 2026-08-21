"""#587: 構造化 findings 照合による採点。

正規表現の共起では推論の有無を測れない。実測された限界:

  - 語を並べただけの文字列が満点 1.00 を取る
  - 正しい言い換え・日本語の正解が 0 点になる
  - 「棄却した」と「主張した」を区別できない

本方式は散文への正規表現マッチを**完全に廃止**し、成果物が出力する機械可読な
findings 表と正解キーを厳密照合する。値と識別子は表記が一意なので、
言い回し・語順・言語に依存しない。verdict が明示フィールドなので極性も判定できる。
"""
from __future__ import annotations

import re

#: 認める verdict。曖昧な値を「発見」とも「棄却」とも解釈しない。
VERDICT_FINDING = "drift"
VERDICT_NO_FINDING = "no-finding"
VALID_VERDICTS = (VERDICT_FINDING, VERDICT_NO_FINDING)

REQUIRED_COLUMNS = ("location", "key", "expected", "actual", "verdict")

_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


class FindingsFormatError(ValueError):
    """findings 表が無い / 壊れている。**採点不能であり 0 点ではない。**

    「表が無い」と「表はあるが 1 件も報告していない」は意味が違う。
    前者を 0 点として扱うと、書式を守らないだけの成果物と、探して
    何も見つけられなかった成果物が区別できなくなる。
    """


def _split_row(line):
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return cells if cells else None


def parse_findings_block(text):
    """散文の中から findings 表を 1 つ取り出して行の list を返す。"""
    if not isinstance(text, str):
        raise FindingsFormatError("artifact text must be a string")

    lines = text.splitlines()
    header_index = None
    for index, line in enumerate(lines):
        cells = _split_row(line)
        if cells is None:
            continue
        normalized = [cell.lower() for cell in cells]
        if all(column in normalized for column in REQUIRED_COLUMNS):
            header_index = index
            columns = normalized
            break
    if header_index is None:
        raise FindingsFormatError(
            "no findings table found; expected a markdown table with columns "
            + ", ".join(REQUIRED_COLUMNS)
        )

    rows = []
    for line in lines[header_index + 1:]:
        if _SEPARATOR.match(line):
            continue
        cells = _split_row(line)
        if cells is None:
            if rows or line.strip() == "":
                if line.strip() == "":
                    continue
                break
            continue
        if len(cells) != len(columns):
            raise FindingsFormatError(
                f"findings row has {len(cells)} cells, expected {len(columns)}"
            )
        row = dict(zip(columns, cells))
        verdict = row.get("verdict", "").lower()
        if verdict not in VALID_VERDICTS:
            raise FindingsFormatError(
                f"unknown verdict {row.get('verdict')!r}; expected one of {VALID_VERDICTS}"
            )
        row["verdict"] = verdict
        rows.append({column: row[column] for column in REQUIRED_COLUMNS})
    return rows


def _normalize_key(text):
    """key を比較用に正規化する。

    `Total Signups` と `total_signups` を別物と扱わない。実 run で、実質的に
    完全正解の artifact が識別子の書き方だけで 0 点になった。正しい答えを
    書式の理由で落とさない。
    """
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _normalize_value(text):
    """値を比較用に正規化する。

    桁区切り (`4,127` と `4127`)・前後の空白・大文字小文字だけの違いで
    不一致にしない。**値そのものが違えば一致させない**（正規化するのは
    表記であって数値ではない）。
    """
    lowered = str(text).strip().lower()
    without_separators = re.sub(r"(?<=\d),(?=\d{3}\b)", "", lowered)
    return re.sub(r"\s+", " ", without_separators).strip()


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers(text):
    return _NUMBER.findall(_normalize_value(text))


def _values_match(reported, truth):
    """報告値が正解値と**実質的に**一致するか。

    実 run で、7 項目すべてを正しく判定した artifact が recall 0.2 になった。
    `42%` と `42`、`about USD 1,300` と `1300` を別物と扱っていたためである。
    **測っているのは判定であって表記ではない。**

    - 両側に数値があれば数値で比較する (単位・記号・桁区切りを無視)
    - 数値が無ければ正規化した文字列の包含で比較する
    - 値そのものが違えば一致させない
    """
    reported_n, truth_n = _numbers(reported), _numbers(truth)
    if truth_n:
        return bool(reported_n) and truth_n[0] in reported_n
    a, b = _normalize_value(reported).strip('"\''), _normalize_value(truth).strip('"\'')
    if not a or not b:
        return a == b
    return a in b or b in a


def _identity(location, key):
    return f"{_normalize_key(location)}:{_normalize_key(key)}"


def score_findings(text, answer_key):
    """findings 表を正解キーと照合して recall / precision / F1 を返す。

    - **recall**: 正解の defect のうち、location/key/actual まで一致して
      `drift` と報告できたものの割合
    - **precision**: `drift` と報告した行のうち、正解の defect だったものの割合。
      decoy を `drift` と報告すると下がる
    - decoy を `no-finding` と明示するのは**正しい挙動なので減点しない**
    """
    rows = parse_findings_block(text)

    defects = {
        _identity(d["location"], d["key"]): _normalize_value(d.get("actual", ""))
        for d in (answer_key.get("defects") or [])
    }
    # 報告用に元の表記を保持する (正規化した識別子は人が読めないため)。
    display = {
        _identity(d["location"], d["key"]): f"{d['location']}:{d['key']}"
        for d in (answer_key.get("defects") or [])
    }
    decoys = {
        _identity(d["location"], d["key"])
        for d in (answer_key.get("decoys") or [])
    }

    claimed = {}
    rejected = set()
    for row in rows:
        identity = _identity(row["location"], row["key"])
        display.setdefault(identity, f"{row['location']}:{row['key']}")
        if row["verdict"] == VERDICT_FINDING:
            claimed[identity] = _normalize_value(row["actual"])
        else:
            rejected.add(identity)

    found = {
        identity for identity, actual in claimed.items()
        if identity in defects and _values_match(actual, defects[identity])
    }
    false_positives = sorted(
        display.get(identity, identity) for identity in claimed if identity not in found
    )

    recall = len(found) / len(defects) if defects else None
    precision = (len(found) / len(claimed)) if claimed else (None if not defects else 0.0)
    if recall is None or precision is None:
        f1 = None
    elif recall + precision == 0:
        f1 = 0.0
    else:
        f1 = 2 * recall * precision / (recall + precision)

    return {
        "rows_reported": len(rows),
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "found": sorted(display.get(identity, identity) for identity in found),
        "false_positives": false_positives,
        "decoys_correctly_rejected": len(rejected & decoys),
        "scoring_method": "structured_findings_exact_match_v1",
    }
