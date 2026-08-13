# Design: pytest-xdist並列実行（Issue #446）

Issue 本文をそのまま設計とする:

Related #420（CI 実測分析 2026-08-13: 直近20 run 中央値 900 秒、単一 Issue クリティカルパスの支配項）

# 目的

pytest スイート（約2,900件）が単一プロセス直列実行で CI 12〜15 分かかっている。テストは tmp_path + MISSION_* env 隔離済みで並列化に適した構造のため、pytest-xdist で 4〜5 分へ短縮する。

# スコープ（codex 委譲可能な粒度）

1. `.github/requirements-ci.txt` に `pytest-xdist` を追加（バージョン固定）
2. `Makefile` の `test` ターゲットを `-n auto --dist loadfile` に変更（`--dist loadfile` はファイル単位分配で flock/カウンター系テストのファイル内順序を保持）
3. ローカルで全緑と所要時間を実測し、直列比を PR に記録
4. 並列で落ちるテストが出た場合は該当テストに `xdist_group` を付与して同一 worker へ固定（テスト本体の意味は変えない）
5. `test_actions_cost_guard.py` が Makefile/ci.yml の文字列を固定している場合は同時更新

# 受け入れ条件

- [ ] `make test` が並列実行になり、ローカル実測でスイート時間が半分以下
- [ ] 全テスト緑（flaky 化ゼロ。3回連続実行で安定）
- [ ] cost guard 更新込みで CI green


## 補足実装メモ
- pytest-xdist はバージョン固定で requirements-ci.txt へ追加（pytest 9.1.1 と互換の最新安定版を選ぶ）
- Makefile の test / test-e2e 両ターゲットを '-n auto --dist loadfile' 化。test-smoke は変更不要
- 検証は .venv-ci/bin/pip install pytest-xdist 後に3回連続で全緑・所要時間を計測し最終メッセージに記載
- ネットワーク: pip install は .venv-ci に対して既に許可されている環境（sandbox内で失敗する場合は最終メッセージで申告し、依存追加はファイル編集のみ行う）
