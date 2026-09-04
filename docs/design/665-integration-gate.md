# 統合退行ゲート（#665）: merge 経路を 1 本にし、統合ツリーで全スイートを通す

## 解く問題

同一ソースを触る 2 本の PR が、**それぞれ単独では CI 緑なのに、統合すると壊れる**。
mission では 2026-08-24 に実際に発生した。

| PR | 内容 | 単独 CI |
|---|---|---|
| #660 | `specialists recommend --record-state` を kernel command 化（#624） | 緑 |
| #662 | first-use confirmation 待ちの checkpoint を terminal にできるようにする（#659） | 緑 |

#662 を merge した後に #660 が base 統合したところ、**#662 のテストが #660 の退行 2 件を検出した**。

1. `awaiting-confirmation` の候補 checkpoint が kernel 経由で記録不能になっていた。
   reducer の `allow_pending` 緩和対象が `pending-evaluation` のみで、first-use confirmation
   待ちの候補が `specialist-selection-invalid` で拒否されていた。**first-use confirmation
   gate 自体が機能しなくなる欠陥**。
2. `specialists decline` が exit 0 なのに checkpoint が変わらなかった。`specialists_decision`
   が typed A4 projection 側の権威になったため、legacy document だけを書く reducer の結果が
   projection に上書きされていた。

**merge 順が逆なら両方 green のまま main に入っていた。** 検出できたのは偶然である。

## 実測で否定した案

異系統レビュー（Sol high）3 巡と自前の実測で、素朴な案はいずれも成立しなかった。

| 案 | 却下理由（実測） |
|---|---|
| **A. open PR 同士をクロステスト** | `git merge-tree ac328fa 73681cc` は 3 ファイルで実コンフリクトを出す。案 A はコンフリクトを skip 扱いにするため**唯一の実例が素通りする**。加えて blocking にすると両 PR の required check が赤になり「先に merge して解決」自体ができずデッドロックする |
| **B. 一律 `strict: true`** | 2026-08-19 に owner 承認のうえ測定に基づいて解除した設定。直近 merged 25 件中 10 件が base 統合を必要とし、base 統合コミット 17 個がそれぞれ CI 1 run と独立 Checker 再取得を発生させていた |
| **C. 定義単位の交差で選択的 strike** | AST で実計算した結果、**本番コードの定義交差はゼロ**。共有 7 ファイルの交差は `test_issue501_k2_parity.py::test_decide_and_guidance_share_one_named_transition_table` の 1 件のみで、`transitions.py` は #660 が `_record_specialist_recommendation`、#662 が `_decline_specialist_selection` と別物。2 件の退行は「同じ定義を触った」からではなく**別々の定義どうしの相互作用**で起きた |
| **D. 変更テストの和集合だけ実行** | 「変更されていないが影響を受ける既存テスト」を取りこぼす。和集合を安全と判断する根拠がない |
| **CI 側での判定（案 A / C 共通）** | `pull_request` workflow は main の `push` では起動しない。CI job が成功した後に main が動いてもその成功は更新されず、**必ず陳腐化する** |
| **merge queue** | ユーザー所有 repo では ruleset API が `merge_queue` を 422 で拒否する |

補足: 「main が実装だけ変えてテストを変えなかった場合は検出できない」という懸念は、実測では
**全 337 PR 中 0 件**（直近 30 / 100 でも 0 件）であり、履歴上支配的ではなかった。

## 採用案: 共通 `gate-and-merge` 経路

`strict: true` の確実性を、**merge の瞬間に 1 回だけ**払う。かつ merge の入口を 1 本に統一する。

### 流れ

```
gate-and-merge <pr>:
  1. repo 単位の lease を取得する（処理全体で保持）
  2. origin/main を fetch し、base sha を記録する
  3. scratch worktree で PR head に origin/main を統合する
     → コンフリクト → fail「手動統合してから再実行せよ」
  4. 統合ツリーで全スイートを実行する（docs-only 変更は既存 fast path）
     → 赤 → fail
  5. origin/main を再 fetch し、手順 2 の base sha と一致するか照合する
     → 動いていた → fail（手順 2 からやり直す）
  6. PR head sha を照合し、`gh pr merge --match-head-commit <sha>` で merge する
  7. merge 結果を read-back で確認する
```

`queue` を使う構成では `queue verify` の後に本コマンドへ委譲する。使わない構成では直接呼ぶ。
**いずれの経路でも merge の実行主体は本コマンドだけ**にする。

### 判断と根拠

| 論点 | 決定 | 根拠 |
|---|---|---|
| 実行タイミング | **merge 直前**（CI 側ではない） | CI 側の成功は main 移動で陳腐化し、`pull_request` は main の push で再実行されない |
| 実行するテスト | **全スイート**（docs-only のみ fast path） | 変更テストの和集合は「変更されていない依存テスト」を取りこぼす。実例の 2 件は main 側の新規テストが検出したが、それを一般化する根拠がない |
| コンフリクト時 | **fail**（skip にしない） | 案 A の最大の欠陥がここだった。実例はコンフリクトする |
| fetch 失敗・解析失敗 | **fail-closed** | 「取得できなかった」を「安全」に倒さない |
| scope 判定 helper の不在 | **scope を full とする**（#697） | scope の判定を諦めて対象リポジトリの `make test` へ委ねる。**実行されるテストが増える保証はない**（何が走るかは対象の Makefile 次第で、0 件もありうる。#722）。helper が存在して壊れている場合は上の行どおり fail-closed を維持する |
| 入口 | **共通コマンド 1 本**。queue も同じ入口へ委譲 | 独立スクリプトを併置すると迂回経路が残る |
| 直列化 | **repo 単位の lease を処理全体で保持** | 同一 base から 2 件が並行実行されたとき、先行だけが merge し後続は手順 5 で止まる |
| branch protection | **変更しない** | merge 側のゲートなので保護設定に触らない |
| CI 側 | **変更しない** | 非 blocking の早期警告も置かない。陳腐化した情報を増やさない |

### 保証範囲（誇張しない）

本ゲートが保証するのは次の 1 点に限る。

> **最終 fetch で確認した base / head の組に対して全スイートを通し、既知のエージェント merge 経路を直列化する。**

保証しないもの:

- **scope helper を持たないリポジトリで「全スイートを通した」こと。** helper 不在時は
  scope を full とし、実行方法を対象リポジトリの `make test` へ委ねる（#697）。
  **その `make test` が実際にテストを実行したかをゲートは確認していない**（終了コードしか
  見ていない）。テストが 0 件でも `suite_exit=0` になり、保証が空洞のまま merge が通る。
  **この経路の閉じ方は #722 で設計中**であり、それまでは helper を持つリポジトリでのみ
  上の保証が成り立つ

- **git 設定による解決先の書き換えを完全には閉じない。** `url.<base>.insteadOf` は git の
  解決先を透過的に書き換えるため、検証済み URL を渡すだけでは行き先を固定しきれない（#701）。
  ゲートは **`gh` が報告する `baseRefOid` と、git が解決した base sha の一致を要求**して
  この書き換えを検出する。`gh` は検証済み identity で API を叩き git の書き換え規則の影響を
  受けないため、両者は独立した観測になる。**不一致は再 fetch で切り分ける**: git 自身の
  観測が動いていれば base の移動として既存の `base-moved` を返し、git が動いていない
  のに API と食い違う場合だけ書き換えとして扱う。これにより、**正当な base 移動が
  step 3 で検出されるようになる**（従来は手順 4 の全スイートを走らせたあと step 5 で
  検出していた）。**理由コードは変わらないが、検出される手順は早くなる。**
  比較できない観測（欠落・非 sha）は `base-observation-unusable` として停止し、
  「検査が走らなかった」と「検査が走って食い違った」を区別する。
  **ただし同一 sha を持つミラーへの書き換えは
  検出しない**（内容は sha で content-addressed に同一なので実害はない）。また
  `remote.origin.proxy` / `uploadpack` / `tagOpt` 等の remote 固有設定は URL 直指定では
  継承されない。現 checkout は `url` / `fetch` のみのため影響しないが、非標準認証や proxy を
  使う環境では互換性リスクが残る
- **「merge の瞬間まで fresh」ではない。** `gh pr merge --match-head-commit` は head sha のみを
  固定し、base sha の compare-and-swap を提供しない。手順 5 と 6 の間に main が動く窓は
  ローカルスクリプトでは閉じられない。サーバー側で原子的に保証するには `strict: true` か
  merge queue が必要で、いずれも本 repo では採れない
- **GitHub UI からの直接 merge は迂回できる。** 本ゲートはエージェント merge 経路に対する強制であり、
  人手の UI merge は規律で担保する。実測では直近 30 件のうち 29 件が `gh pr merge` 経路
  （残り 1 件は経路未確認）で、現状の運用実態では致命的でない
- **直列化は同一ホスト内に限る。** lease は `fcntl.flock` によるファイルロックであり、
  同じマシン上のプロセス間でしか効かない。複数ホストから同時に `gate-and-merge` を
  呼んだ場合は直列化されない。
- **3 本以上の同時干渉**は扱わない。main は直列なので順に 1 本ずつ検出される

### exact-head / refreeze 規律との整合

既存規律（base 移動時は accepted 無効・refreeze・fresh review 再取得）は**変更しない**。
本ゲートは規律を置き換えるのではなく、その前段で「統合しても壊れないこと」を機械確認する層である。

統合テスト済みの tree を fresh review の代替として認めるかは**別の判断**であり、本設計では
提案しない。認めるなら owner 承認付きで例外として明文化する必要がある。

## 受け入れ条件

計測器の検出力を先に実証する。実装が通っても、以下が示せなければ信じない。

1. **実例の再現**: `ac328fa`（#660 統合前 head）と `73681cc`（#662 の main 上 squash）を入力に、
   手順 3 が「コンフリクトで fail」を返すこと
2. **コンフリクトしない統合退行の検出**: 実履歴では該当例を特定できなかったため、
   **人工 fixture** で「clean merge するが統合後にテストが赤になる」組を作り、手順 4 が
   落とすことを示す（selector と実行経路の証明）
3. **誤検出しない証明**: 同一ファイルの別関数しか触っていない組（例 #639 の
   `cmd_resolve_archive` 対 `_supersede_reviews_locked`）で、統合が成功しテストも緑になること
4. **race**: 同じ base から 2 件を並行実行したとき、先行だけが merge し、後続は手順 5 で
   停止すること
5. **fail-closed**: fetch 失敗・コンフリクト・テスト赤・base 移動のいずれでも非 0 終了すること
6. **可観測性**: 実行したテスト範囲と、手順 2 / 5 の base sha がログに出ること
   （無音の打ち切りを作らない）

## 追記（#721）: 変更集合 digest の検証を加える

### 何を変えたか

`gate-and-merge` は、レビュー時の変更集合と、実際に merge する変更集合が同じであることを
機械確認するようになった。`--reviewed-changeset-digest` に digest を渡し、統合ツリー上で
観測した `git diff <merge-base>...<head>` のパッチ本文 sha256 と比較する。

**digest を渡した場合、書式不正・算出不能・不一致のいずれでも非 0 終了する。**

**digest は任意引数である。** 渡さなければ緩和が適用されないだけで、gate の既存要求
（base 不動・head 不動・統合ツリーでの全スイート）はそのまま残る。参照実装
(company-os `verify-exact-head.mjs`) と同じ意味論で、「渡さないと止まる」ではなく
「渡さないと厳しい側に倒れる」。

**必須にしてはならない。** 全 merge に digest を課すと、実装者は「引数を埋める最も
自然な方法」を選ぶ。それは実行時に `git diff` して sha256 を取ることであり、gate が
観測する値と同じ計算から出るため**常に一致する。悪意なく、検証が空回りする実装が
既定になる。**

したがって **digest は Checker の報告から転記する。gate の実行時に計算して渡しては
ならない。** producer が事前に値を得るときは
`mission-state.py changeset-digest --base-sha <sha> --head-sha <sha>` を使い、
レビュー依頼の時点で Checker へ渡す。手元で `git diff | shasum` を組み立てると、
ローカルの diff 設定次第で別の値になり、恒常的な不一致になる。

### 上の「exact-head / refreeze 規律との整合」との関係

本文は「既存規律（base 移動時は accepted 無効・refreeze・fresh review 再取得）は変更しない」
と書いた。**本追記はその規律を緩めるものではなく、規律が既に定めている条件を満たすための
実装である。**

`~/.claude/rules/git-workflow.md` の「変更集合の不変性による代替」は、**機械検証を備える
repo に限り** base 移動後の accepted 維持を認め、備えない repo には base 不動を要求する。
この repo は digest 比較を持たなかったため後段に該当し、変更ファイルが重ならない PR でも
base が動くたびに fresh review の取り直しが必要だった（#709 で 3 回）。

したがって本追記が加えるのは判断の緩和ではなく、**判断を人間の目視から機械検証へ移すこと**
である。目視で「重なっていないから同じ」と判断する運用は、同規約が明示的に禁じている。

### 三点差分である理由（二点にしてはならない）

digest の範囲は `base...head`（三点）であり、merge-base からの差分を指す。base が進んでも
head の分岐点が動かない限り同じ変更集合を指すため、digest は安定する。**これが「base が
動いても accepted を維持できる」根拠そのもの**で、二点差分に変えると base 移動のたびに
digest が動き、代替が成立しない。

### digest を PR 内のファイルへ書かない

digest 値を設計文書・Handover など PR 内のファイルへ記録すると、その記述自体が変更集合に
含まれ、digest が変わる（自己言及）。記録先は Checker の報告コメントか、merge 直前に
verifier へ渡す引数とする。

**この自己言及を「digest の計算対象から特定パスを除外する」ことで解いてはならない。**
除外を認めると、そのパスへ変更を寄せることで検証をすり抜けられる。`pr-size-and-scope.md` が
「allowlist 自体の変更は security 関心事として扱う」としているのと同じ構図で、検証を弱める
変更が検証の対象外に置かれる形になる。

### 保証しないもの

- **レビューの質は保証しない。** digest が示すのは「レビュー時と同じ変更集合である」ことだけで、
  そのレビューが妥当だったかは別問題である
- **統合結果の同一性は保証しない。** base が動けば統合後のツリーは変わる。統合しても壊れないことは
  本ゲートの手順 4（統合ツリーでの全スイート）が別途担保する

## 追記（#727）: digest の出所を申告として記録する

### 何を変えたか

`--claimed-digest-source` を追加した。値は `checker-comment`（Checker の報告から転記した）か
`argv-manual`（人手で指定した）のいずれかで、**`--reviewed-changeset-digest` と双方向で対に
なる**。digest だけ・申告だけの指定はいずれも手順 1 で停止する。gate の出力 JSON には
`claimed_digest_source` が入り、申告が無かった merge では `null` になる。

### 記録するのは出所ではなく「出所の申告」である

**gate は digest の出所を観測できない。** 観測できるのは値だけで、その値が Checker の報告から
転記されたのか、producer が merge 時に `git diff | sha256sum` で作ったのかは区別がつかない。
`checker-comment` と申告しながら自算した値を渡すことは可能で、**gate はそれを検出しない。**

したがってこの引数は強制ではなく**事後監査のための記録**である。merge を止める力は持たない。

### なぜ機械強制を採らなかったか

強制するには Checker を別 credential（別 GitHub App）にして、その投稿だけを受理する
required check を置く必要がある。**論理的に不可能ではないが、採らない。**

| 前提 | 実測（2026-09-02） |
|---|---|
| PR コメントの著者で Checker を識別できる | できない。producer と Checker は同一アカウント |
| `gate-and-merge` を通らないと merge できない | できる。GitHub UI からの直接 merge は迂回できる |

継続コスト（credential の rotate ごとに人間の承認が要る）に対して、守る対象の影響範囲が
repo 内に閉じているためである。**影響範囲が広がればこの判断は変わる。**

### 引数名について

`--digest-source` にしない。gate が記録できるのは出所ではなく出所の申告であり、
`digest_source` と名付けると、記録が出所の証跡だと誤認される。

### 保証しないもの

- **producer の自算を防がない。** 記録は事後監査のためで、merge を止めない
- **producer がコメントも作れば、後続の照合でも検出できない**
- **UI merge 経路は一切カバーしない**
- **照合と集計は本追記の範囲外。** gate の結果を state へ相関保存する経路（#733）が
  前提になる
