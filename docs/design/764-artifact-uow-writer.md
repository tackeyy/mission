# #764: artifact の出力を UoW の唯一の writer 経路へ移す（#747 3b）

親 Issue: #747 / 先行: #763（PR #765、merge commit `1708410`）

## 問題

artifact の 4 コマンド（`initialize-artifact` / `render-artifact` / `export-artifact` /
`record-artifact-publication`）は、公開ファイルをいまも旧 publisher で書いている。

- 4 コマンドとも `PUBLICATION_PATH_FIELD_BY_COMMAND_TYPE`
  （`skills/mission/lib/mission_application/evidence_publication.py`）に載っていない。そのため
  `_publication_claims`（`skills/mission/lib/mission_persistence/evidence_order.py`）が空を返し、
  blob 集合も空になる
- blob が空で effect があると、`execute_evidence_transition_effects` の `elif effects:` 分岐
  （`skills/mission/lib/mission_persistence/legacy_v4.py` の 1498 行付近）に入る。ここでは
  `self.execute`（commit）より**前に** publish が走る
- この経路は、現状テストで固定されている
  （`skills/mission/tests/test_issue711_uow_sole_writer.py`
  `test_the_legacy_publisher_still_runs_for_a_path_less_command`）

progress（#763）で閉じたのと同じ窓である。commit 前に書くため、crash すると
state に載らない公開ファイルが残る。

## 決定

### D1. repository 内の宛先規則を、artifact 用に 1 つ足す

`mission_kernel/projection_path.py` に `resolve_internal_artifact_path` を追加する。
受理するのは、次の 4 部品の完全一致だけとする。

```
.mission-state / artifacts / <segment> / mission-artifact.md
```

`<segment>` の条件は **`_sanitize_sid`（`skills/mission/bin/mission-state.py`）の値域と一致させる**。

- 空でない
- `/` と `\` を含まない
- `.` で始まらない
- 前後に空白が無い

**生成規則（`_sanitize_sid`）と受理規則を食い違わせない。** #763 では、生成された名前を受理規則が
拒否する形を作りかけた。値域より狭く受理すると、既存の artifact がまた書けなくなる。
**値域が一致していることはテストで固定する**（`_sanitize_sid` に任意の session id を通した結果を、
受理規則がすべて受け入れる）。

`init` は導入時（`ed98b0e`）から常にこの形でパスを導出しており、利用者は指定できない。
state を手で書き換えない限り、render / record-publication が保存済みの `artifact.path` から
読むパスもこの形になる。

**規則に合わない保存済みパスは拒否する**（`publication-path-inside-root`。書き込みの前に失敗させる）。
**旧 publisher へは戻さない。** 戻すと窓がまた開く。拒否されるのは手で書き換えた state だけで、
その扱いは「影響」節に書く。

`canonical_generated_path` は、progress の規則・artifact の規則・projection 規則の 3 つを
順に当てる。**progress と artifact の規則は重ならない**（`archive/` と `artifacts/`）。

### D2. 認可の単位を「コマンド」から「コマンドと effect フィールドの組」へ変える

現行の `INTERNAL_DESTINATION_COMMAND_TYPES` は**コマンド単位**である。
`export-artifact` は effect を 2 つ持つ。`artifact_effect` は repository 内へ書くが、
`export_effect` は書いてはならない。コマンド単位では、この 2 つを区別できない。

次の表に置き換える。

| コマンド | effect フィールド | 書いてよい repository 内の規則 |
|---|---|---|
| `update-progress` | `effect` | progress |
| `initialize-artifact` | `effect` | artifact |
| `render-artifact` | `effect` | artifact |
| `record-artifact-publication` | `effect` | artifact |
| `export-artifact` | `artifact_effect` | artifact |
| `export-artifact` | `export_effect` | **なし（projection のみ）** |

**規則まで表に持たせる。** 「repository 内に書いてよい」だけだと、progress のコマンドが
artifact のパスへ書けてしまう。

`authorize_generated_destinations` は `(command_type, field)` を受け取り、パスが当たった規則が
表の規則と一致しなければ `publication-destination-unauthorized` を返す。
**表に無い組は、repository 内のどの規則にも書けない**（fail-closed）。

`_publication_claims` は `(field, claim)` の組を返すように変え、フィールド名が認可まで届くようにする。

### D3. claim のパス読み出し表に 4 コマンドを足す

`PUBLICATION_PATH_FIELD_BY_COMMAND_TYPE` に、4 コマンドを `"target"` で追加する。
artifact の claim（`ArtifactEffectClaim`）は、`target` が公開パスそのものである。
`publication_path` キーは足さない。足すと kernel command の schema が変わり、
記録済みの operation identity がすべて動く（progress で同じ判断をしている）。

### D4. 公開バイト列から今回の時刻を外す

`_render_artifact_markdown`（`skills/mission/bin/mission-state.py`）が出す
`- updated_at: ...` の行を削除する。

- kernel は 4 コマンドとも `artifact["updated_at"] = at` を設定し、prepare は途中の文書から
  描画する。そのため今回の `now` が公開バイト列に入る
- 同じ operation identity の retry は時刻が違うので、バイト列が変わる。blob の digest と、
  prepare で記録した digest が一致しなくなる
- progress は #765 で同じ理由から `updated_at` を外した
  （`test_issue747_3a_progress_is_time_independent.py`）

**state 側の `artifact.updated_at` は残す。** 外すのは描画だけとする。

block の `timestamp=` は、commit 済みの append 履歴の値であって今回の時刻ではないので、残す。

**`updated_at` 行を消すと、既存の artifact は次の render で 1 行短くなる。** #593 の
`artifact_digest`（iteration ごとの digest で、変化の有無を判定する）には、
この変更の直後に 1 回だけ「変化あり」が出る。gate の判定には使っていない（同テストの docstring）。

### D5. export の宛先

**`--to` を `.mission-state/` の中へ向けた export は拒否する。**

- 現行では、`_resolve_evidence_output_path` が「親ディレクトリが cwd の中にあること」しか
  見ていないので通る
- UoW では、`export_effect` は projection 規則でしか書けない（D2）。repository 内のパスは
  `publication-path-inside-root` になる
- **書き込みの前に拒否する。** artifact ファイルも state も変化しないことをテストで固定する
- export 先を state の中に置く用途は、文書（`docs/MISSION_ARTIFACTS.md` とその日本語版）にも
  テストにも無い（`--to` を使う例はすべて `docs/` 配下）

**`--to` に artifact 自身のパスを指定した場合**は、`validate_effects` が
`effect-target-duplicated` で拒否する（`test_issue633_artifact_kernel.py`
`test_export_effects_bind_in_order_and_duplicate_targets_close_before_publish`）。
v5 の経路でも、公開より前に同じ拒否が起きることをテストで固定する
（`local_uow.py` の blob ID 重複検査に頼らない）。

### D6. 旧 publisher への到達を塞ぐ

`elif effects:` 分岐のコード自体は残す（progress と同じ形）。**effect を持つ全コマンドが
blob を持つので、この分岐には到達しない**。

- `test_the_legacy_publisher_still_runs_for_a_path_less_command` を反転させる。
  4 コマンドそれぞれで spy が一度も呼ばれないことを固定する
- `EFFECT_FIELDS_BY_COMMAND_TYPE` に載っているのに `PUBLICATION_PATH_FIELD_BY_COMMAND_TYPE`
  に無いコマンドが 0 件であることも固定する。**今後コマンドを足したとき、表を 1 つ忘れると
  黙って旧 publisher へ落ちる**形を検出するため

## 受け入れ条件との対応

| #764 の受け入れ条件 | 対応 |
|---|---|
| UoW だけが書くことを、旧 publisher に到達しないことで固定 | D6 |
| 公開バイト列が semantic time に依存しない | D4 |
| 既存の blob ID が 1 つも動かない | blob ID はパスだけから導出する（`derive_blob_id`）。progress と projection の canonical 形は変えない。artifact は今回初めて blob を持つので、既存 ID が存在しない |
| v4 経路の observable contract を崩さない | v4 は `update-progress` と同じ扱い。CLI の JSON 出力と exit code は D5 の拒否を除いて変えない |
| skills → plugins のミラー | lib と bin を byte-identical に |

## 影響

| 変わるもの | 誰に | 扱い |
|---|---|---|
| canonical Markdown から `updated_at` 行が消える | artifact を読む人 | D4。state には残る |
| `--to .mission-state/...` の export が拒否される | その使い方をしている利用者（文書とテストには無い） | D5。エラー文で projection 規則を案内する |
| 手で書き換えて規則に合わなくなった `artifact.path` の render / publish が拒否される | state を手で編集した利用者 | D1。書き込み前に失敗し、state もファイルも変えない |

## 分割

**1 PR で出す。** D2（認可の単位）は export を含む 4 コマンドすべての前提で、export だけ
別 PR に分けると、中間状態では「コマンド単位の認可に export を足せない」ため
export が旧 publisher に残る。その状態は #764 の受け入れ条件を満たさない。

人がレビューする行数は実装 300〜450 行、テスト 500〜700 行と見積もる。
**1,000 行を超えたら、PR 本文に理由を書き、owner の承認を取る**（`pr-size-and-scope.md` 層 2）。

## やらないこと

- `verification` 経路。`RecordVerification` は `EFFECT_FIELDS_BY_COMMAND_TYPE` に無く、
  公開ファイルを持たない（`transitions.py` で Transition の effect は空）。窓が無いので対象外。
  #747 に記録して閉じる
- `ExecutionRequest` 入口での internal 宛先の認可（#747 の持ち越し 1）
- `elif effects:` 分岐のコード削除（到達しないことを固定するだけにする）

## テスト

1. `resolve_internal_artifact_path` の受理と拒否。`_sanitize_sid` の値域との一致を
   property 的に固定（任意の文字列 → `_sanitize_sid` → 受理）
2. `(command, field)` 認可表: 4 コマンドの `effect` と export の `artifact_effect` は artifact 規則だけを受理し、
   export の `export_effect` と progress のコマンドは artifact のパスを拒否する
3. 4 コマンドで旧 publisher の spy が呼ばれない（D6）。表の網羅性の検査
4. 同じ operation identity の retry が同じバイト列を出す（D4）
5. `--to .mission-state/...` と `--to <artifact 自身>` が、書き込み前に拒否される（D5）
6. 既存の progress / projection の blob ID が変わらない（固定値）
7. 変異: 認可表から `export_effect` の除外を外す、`updated_at` 行を戻す、表から 1 コマンドを外す。
   それぞれで上のテストが落ちること
