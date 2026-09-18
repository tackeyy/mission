# #788: export の宛先を、名前ではなく開いたディレクトリで決める

Refs #764（PR #787）。異系統レビュー round 5 の High を切り出したもの。

## 問題

#764 で `artifact export` の宛先が repository（`.mission-state/`）を指す場合に、CLI の入口で
拒否するようにした。入口はファイルシステムの identity（`st_dev`, `st_ino`）で判定するので、
大文字小文字の違いや、その時点で存在する symlink では回避できない。

**残っているのは、検査と書き込みの間の時間差である。** v4 の publisher は書き込み時に宛先を
解決し直す（`_publish_evidence_effects` → `_resolve_evidence_output_path`。後者は
`Path.resolve()` で symlink を追う）。検査の後・解決の前に `docs` を `.mission-state` への
symlink へ差し替えると、`.mission-state/` 配下へ書ける。

v5 には書き込み側の `_refuse_repository_alias` があるので v5 では止まる。**露出するのは v4 経路だけ。**

## 脅威モデル（ここを先に決める）

**守るのは「利用者が意図しない宛先へ書いてしまうこと」**である。具体的には次の 2 つ。

1. **その時点で存在する symlink**（`docs -> .mission-state` のような置き換え）を、解決が黙って追うこと
2. repository が**別の名前で開けること**（大文字小文字を区別しないファイルシステム、別名の symlink）

**この設計が保証すること**は 1 つだけである。

> **書き込みは、project root から symlink を 1 つも追わずに開いたディレクトリへ行われ、
> その各段は、開いた時点で repository ではなかった。**

**保証しないこと**と、その理由。**「攻撃者は直接書けるはずだ」という理由は使わない**
（`docs` を差し替える権限と `.mission-state/` へ書く権限は別で、publisher が別の主体として
動く場合には confused deputy になる。round 2 の指摘）。

| 保証しないもの | なぜ閉じられないか |
|---|---|
| **開いたあとに、そのディレクトリが repository の中へ移動される** | fd は inode を指し続ける。移動を検出するには、書き込みの瞬間まで祖先関係を固定する必要があり、`openat(fd, "..")` は bind mount で mount point 側へ抜けるため、祖先関係そのものを確定できない |
| **repository の部分木が別の場所に bind mount されている** | mount 先のディレクトリは自分の identity を持ち、repository root の identity とは一致しない。**identity の比較では原理的に見分けられない** |
| Windows | `openat` / `O_NOFOLLOW` の挙動が違う。**現在の publisher も同じ前提に立っている**（`_open_publish_directory`） |

**v5 側（`_refuse_repository_alias`）も同じ限界を持つ。** この設計は v4 を v5 と同じ水準へ揃えるもので、
両方の水準を上げるものではない。**上の 2 つを閉じるなら、両方の層をまとめて変える別の作業になる。**

## 決定

### D1. 宛先を project root の fd から 1 段ずつ、symlink を追わずに開く

`Path.resolve()` をやめる。project root を fd で開き、宛先の各部品を
`os.open(part, O_NOFOLLOW | O_DIRECTORY, dir_fd=前段の fd)` で辿る。

- **部品が symlink なら `ELOOP` になる。これを拒否として扱う**（追わない。追ってよいなら
  そもそも判定の意味が無い）
- 各段で `fstat` の identity を `.mission-state` の identity と比べ、一致したら拒否する。
  これで**別名で repository を開く経路が閉じる**（大文字小文字・別名 symlink の両方）
- **最後の部品（ファイル名）は開かない。** 既存の `_publish_output_transaction` が、
  親ディレクトリの fd と名前で書く形をすでに取っている
- pin 後は再検査しない。fd が inode を保持するため、同一 fd の再検査は意味を持たない
- **段数の上限は置かない。** 辿る段数は宛先のパスの部品数で決まり、無限にならない
- 途中のディレクトリが存在しなければ作る（`mkdir` を `dir_fd` 付きで行い、作った直後に同じ fd から
  開き直す）。**作る前に、その名前が既存の symlink でないことを O_NOFOLLOW の失敗で確かめる**

**「祖先を辿って repository の中かを見る」方式は採らない。** `openat(fd, "..")` は bind mount の
mount point 側へ抜けるため、repository の部分木が別の場所に mount されている場合に root へ到達しない。
**辿り方を工夫しても、この方式では「repository の中か」を決められない。**

### D2. どの effect にこの解決を使うか

**repository の外へ公開する effect だけ**が対象である。artifact（`.mission-state/artifacts/...`）と
progress（`.mission-state/archive/...`）は repository の中へ書くのが正しい。

`_publish_evidence_effects` は effect ごとに publication path を持つので、
`canonical_generated_path` と `resolve_internal_*`（#764 で `skills/mission/lib/` にある）で分類し、

- repository 内の規則に当たる → 従来どおりの経路
- 当たらない（projection） → D1 の解決を使う

### D3. 入口の事前検査は残す

#764 で入れた入口の identity 判定は残す。**早く落ちるほうが利用者に分かりやすい**からで、
唯一の防壁としては扱わない。入口の `OSError` の扱いも変えない（存在しないディレクトリへの正常な
export を落とさないため）。**fail-closed にするのは D1 の側**で、そこでは実際に開いた結果を見ている。

### D4. 置き場

解決のロジックは `skills/mission/lib/` へ置く（adapter に分岐を足すと ratchet が拒否する）。
`mission-state.py` からは、project root と宛先を渡して fd を受け取るだけにする。

## やらないこと

- v5 経路（`_refuse_repository_alias` が既に書き込み側で識別する）
- 脅威モデルで対象外とした 3 つ
- `_resolve_evidence_output_path` を使う他の呼び出し元（evidence の projection 以外）。
  **この Issue では export の経路だけを変える**

## テスト

**hook の位置が要点である。** `_open_publish_directory` が返った後に名前を差し替えても、
fd は既に元のディレクトリを pin しており、既存の identity 検査が拒否する。**それでは新しい検査を
外しても通る。** 差し替えは**解決と open より前**に置き、新しい経路が実際に symlink を開こうとする
状態を作る。

1. **`docs` が repository の外の別ディレクトリ（`elsewhere/`）への symlink のとき、export が拒否され、
   `elsewhere/` にファイルが作られない。** `O_NOFOLLOW` を外すと `elsewhere/` へ書かれてしまうので、
   **このテストが `O_NOFOLLOW` を load-bearing にする**（`.mission-state` を指す symlink では、
   identity の比較が先に拒否してしまい、`O_NOFOLLOW` を外しても落ちない）
2. `docs` が `.mission-state` への symlink のとき、export が拒否され、`.mission-state/` 配下に
   ファイルが作られない（identity の比較が担う側）
3. 入口の検査を通した**後**に `docs` を symlink（repository の外・repository の中の両方）へ
   差し替えても拒否される。差し替えは `_resolve_evidence_output_path` を呼ぶ直前の hook で行う
4. 途中の部品が symlink のとき（`docs/out -> ../elsewhere` と `docs/out -> ../.mission-state`）も拒否される
5. repository の外への通常の export が通る: 既存の `docs/`、これから作る深い階層
   （`a/b/c/d/e/f.md`）、ファイル名だけ（`out.md`）
6. progress と artifact の in-root 公開は従来どおり通る（D2 の対象外側）
7. **`docs/../.mission-state/out.md` が拒否される。** `..` を含む綴りは、どの環境でも
   repository と同じディレクトリを開く。**identity の比較を外すと通ってしまう**（D1 の層は名前を
   比較しないため）ので、**このテストが identity の比較を環境非依存で load-bearing にする**
7b. 大文字小文字を区別しないファイルシステムでは `.MISSION-STATE/...` が拒否される
   （区別する環境では skip）。**7 の補強であって、代わりではない**
8. 変異と、落ちるテストの対応（**実測した結果を書く。設計時の見込みではない**）

   | 変異 | 落ちるテスト |
   |---|---|
   | `O_NOFOLLOW` を外す | 3 件 |
   | 書かれた形（`.` `..` 空）の検査を外す | 2 件 |
   | 経路の分類を反転する | 2 件 |
   | **repository の identity の比較を外す** | **1 件** |
   | **repository の identity を各段で取り直さない** | **1 件** |
   | **拒否経路で fd を解放しない** | **1 件** |
   | repository 名（casefold）の拒否を外す | **0 件**（identity の比較が同じ入力を拒否する） |

   **casefold の拒否だけは単独で固定できない。** 綴りの違いで開いた先は repository そのものなので、
   identity の比較が必ず拒否する。**名前の検査は、identity より前に・分かりやすい理由で落とすために
   残す多重防御**であって、これが唯一の防壁になる入力は無い。

   **「identity の比較だけが拒否する状況は構成できない」と書いていたのは誤りだった**（round 2 の指摘）。
   repository を pin したあとに repository 自身を親の名前へ rename すると、部品の綴りは普通の名前のまま
   identity だけが一致する。テストはこの形で固定している。
