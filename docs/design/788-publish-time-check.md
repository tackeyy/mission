# #788: export の宛先検査を、書き込み時に開いた親ディレクトリに対して行う

Refs #764（PR #787）。異系統レビュー round 5 の High を切り出したもの。

## 問題

#764 で `artifact export` の宛先が repository（`.mission-state/`）を指す場合に、CLI の入口で
拒否するようにした。入口はファイルシステムの identity（`st_dev`, `st_ino`）で判定するので、
大文字小文字の違いや symlink の別名では回避できない。

**残っているのは、検査と書き込みの間の時間差である。** v4 の publisher は書き込み時に宛先を
解決し直す（`_publish_evidence_effects` が `_resolve_evidence_output_path(cwd, publication_path)` を
呼ぶ）。したがって次のどちらかで `.mission-state/` 配下へ書ける。

- 検査の時点で存在しなかった `docs/` を、書き込みまでの間に `.mission-state` への symlink にする
- 検査の時点だけ `stat` が失敗し、書き込み時には成功する（入口は `OSError` を「repository ではない」と読む）

v5 には書き込み側の `_refuse_repository_alias` があるので v5 では止まる。**露出するのは v4 経路だけ。**

## 決定

### D1. 判定を、書き込みに使う fd に対して行う

`_publish_output_transaction`（`skills/mission/bin/mission-state.py`）は、書き込む親ディレクトリを
`_open_publish_directory` で開き、**その fd の identity を pin して**、書き込みの前後で
「開いたものが名前どおりか」を確かめている。**時間差を閉じる場所はここしかない**（名前で解決し直す
どの検査も、解決のあとに入れ替えられる）。

**`_publish_output_transaction` に「repository の中へ書いてはならない」という要求を渡し、
開いた fd から祖先を辿って確かめる。**

- 祖先を辿るのは `openat(fd, "..")` による fd の連鎖で行う。**パス文字列へ戻さない**
  （戻した時点で名前による解決が再び入り、時間差が復活する）
- 各段で `fstat` の identity を repository root の identity と比べる
- 同じ identity に達したら拒否する（`publication-path-invalid` と同じ扱いで exit 2）
- ファイルシステムの根（`..` が自分自身と同じ identity）に達したら、repository の外だと確定する
- **辿る段数に上限を置く**（例: 64）。上限に達したら**拒否する**（fail-closed）

### D2. 誰がこの要求を渡すか

**export の projection 側の effect だけ**が対象である。artifact 側（`.mission-state/artifacts/...`）と
progress（`.mission-state/archive/...`）は、repository の中へ書くのが正しい。

`_publish_evidence_effects` は effect ごとに publication path を持っているので、
**その path が repository の中を指す規則に当たるかどうかで、要求を渡すかを決める**
（`canonical_generated_path` と `resolve_internal_*` は #764 で lib にある）。

- 規則に当たる（progress / artifact）→ 従来どおり。repository の中へ書く
- 当たらない（projection）→ **repository の中へ書いてはならない**という要求を渡す

### D3. 入口の事前検査は残す

#764 で入れた入口の identity 判定は残す。**早く落ちるほうが利用者にとって分かりやすい**からで、
唯一の防壁としては扱わない。

**入口の `OSError` の扱いは変えない。** そこで fail-closed にすると、存在しないディレクトリへの
正常な export（`docs/` をこれから作る場合）が落ちる。**fail-closed にする場所は D1 の書き込み側**で、
そこでは fd を開いた後なので「開けなかった」と「repository だった」を取り違えない。

### D4. ratchet

判定のロジックは `skills/mission/lib/` へ置く（adapter に分岐を足すと ratchet が拒否する）。
`mission-state.py` からは、開いた fd を渡して呼ぶだけにする。

## やらないこと

- v5 経路（`_refuse_repository_alias` が既に書き込み側で識別する）
- repository の外にある別の禁止領域（そういう要求は無い）
- Windows 対応（`O_DIRECTORY` / `openat` の挙動が違う。**現在の publisher も同じ前提に立っている**）

## テスト

1. **時間差そのもの**: 入口の検査を通したあと、書き込みの直前に親を `.mission-state` への symlink へ
   差し替える。`.mission-state/` 配下にファイルが作られないこと。差し替えは
   `_open_publish_directory` を包んで、開いた直後に実行する（テスト側の hook）
2. 祖先の途中が repository である場合（`.mission-state/archive/` の下）も拒否されること
3. repository の外への通常の export（`docs/`・深い階層・これから作るディレクトリ）は通ること
4. 祖先を辿る段数が上限に達したら拒否されること
5. progress と artifact の in-root 公開は従来どおり通ること（要求を渡さない側）
6. 変異: 祖先の走査を 1 段で打ち切る / 上限到達を許可に倒す / D2 の判定を反転する。
   それぞれで上のテストが落ちること
