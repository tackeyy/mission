親 Issue: #473（Wave 1 / 依存順 5 番目・P1）

# 概要

CLI が名乗るバージョンと配布バージョンが 4 マイナー分ずれている（CLI `2.0.0` / 配布 `2.4.0`）。stale plugin cache の検出はこの CLI 定数を基準に比較するため、**2.1.0〜2.4.0 の古いキャッシュを「新しい」と誤判定して警告を出さない**（false negative）。

原因は明確で、`docs/VERSIONING.md` の version bump 手順に `MISSION_CLI_VERSION` が列挙されていないこと。手順に無いので毎回更新されない。

# 一次証拠（現在の main）

| 対象 | 値 |
|---|---|
| `skills/mission/bin/mission-state.py` L301 `MISSION_CLI_VERSION` | `"2.0.0"` |
| `.claude-plugin/plugin.json` | `"2.4.0"` |
| `.codex-plugin/plugin.json` | `"2.4.0"` |
| `plugins/mission/.codex-plugin/plugin.json` | `"2.4.0"` |

`MISSION_CLI_VERSION` の用途:

- L1491 `data.setdefault("cli_version", MISSION_CLI_VERSION)`（init 時に state へ記録）
- L8886 / L8902 `_detect_version_skew()` の比較基準。`codex-preflight` の JSON `version_skew.cli_version` に出る
- L14063 `resume` の JSON `resume.version_skew`
- L15148 `stats` の `by_cli_version` 集計キー

検出ロジック `_detect_version_skew()`（L8878）と `_version_tuple()`（L8852）は、キャッシュディレクトリ名を `MISSION_CLI_VERSION` と数値比較し、**小さければ stale** と判定する。plugin.json は参照しない。`2.0.0 < 2.4.0` の逆転により、2.1.0〜2.4.0 のキャッシュは stale 判定されない。

`docs/VERSIONING.md` L55–L59 の更新対象一覧: CHANGELOG（EN/JA）、plugin.json 3 種、README と Codex setup docs の install パス。**`MISSION_CLI_VERSION` は含まれていない**。

# 変更内容

1. version の**単一管理元**を決め、`MISSION_CLI_VERSION` がそこから外れないようにする。取りうる方針は 2 つで、実装者はどちらかを選び PR 本文に理由を書くこと
   - (a) `MISSION_CLI_VERSION` を現行配布と同じ `2.4.0` に更新し、**CI で plugin.json 3 種との一致を検証するテストを追加**する（実装は小さく、ドリフトは機械的に止まる）
   - (b) `MISSION_CLI_VERSION` を plugin.json から実行時に読み込む（管理元が 1 つになるが、配布形態ごとの読み込み経路とフォールバックが必要になる）
   - **推奨は (a)**。mission は配布物が 3 種ありパッケージ境界も異なるため、実行時読み込みは失敗経路が増える
2. `docs/VERSIONING.md`（および `docs/VERSIONING.ja.md`）の更新対象一覧に `MISSION_CLI_VERSION` を追加する
3. version が一致していることを検証するテストを追加し、次のリリースで再びずれないようにする

## やらないこと

- バージョン番号そのものの bump（2.5.0 へ上げる作業ではない。**現行 2.4.0 に揃えるだけ**）
- CHANGELOG への新エントリ追加やリリース作業
- `_detect_version_skew()` の比較アルゴリズム自体の変更
- `stats` の `by_cli_version` 集計仕様の変更

# 受け入れ条件

- [ ] `MISSION_CLI_VERSION` と plugin.json 3 種の version が一致している
- [ ] 一致をテストが機械的に検証し、片方だけ変更すると必ず落ちる
- [ ] `docs/VERSIONING.md` / `.ja.md` の手順に `MISSION_CLI_VERSION`（またはその単一管理元）が明記されている
- [ ] `2.1.0` / `2.3.0` のキャッシュディレクトリが stale として検出される
- [ ] plugins ミラー一致・既存テスト全緑

# テストリスト

1. `MISSION_CLI_VERSION` == `.claude-plugin/plugin.json` の version == `.codex-plugin/plugin.json` == `plugins/mission/.codex-plugin/plugin.json`
2. キャッシュディレクトリ名が `2.1.0` / `2.3.0` の状況で `codex-preflight` が stale を報告する（現状は報告しない = Red になるはず）
3. キャッシュが現行 version と同じなら stale と報告しない
4. `init` が記録する `cli_version` が新しい値になる

既存の `skills/mission/tests/test_issue186_cli_version.py` は L16–L19 で `MISSION_CLI_VERSION` を動的に読み込む作りのため、**値を変えてもこのテストは壊れない**（＝現状ではドリフトを検出できない）。本 Issue で追加する一致テストがその穴を塞ぐ。

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。再現テストが Red になることを先に確認する
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- CI Quality と同じセット（`test_artifact_hygiene` `test_vendor_fingerprint` `test_plugins_in_sync` `test_codex_wrapper_sync` `test_actions_cost_guard` `test_doc_consistency`）をローカルで回してから push する
