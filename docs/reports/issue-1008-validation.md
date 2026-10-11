# 登録前のhost観測・IPC基盤の検証

対象:[観測・IPC基盤Issue1008](https://github.com/tackeyy/mission/issues/1008)。
親:[実host adapterのIssue897](https://github.com/tackeyy/mission/issues/897)。
状態:model-free検証中。実hostの観測・登録・起動はしていない。

## 範囲と信頼境界

`skills/mission/lib/fresh_review_observation.py`は、レビュー済み静的manifest、
信頼済みhostのsource列挙、native event bridgeからの閉じた投影を検査する。
validator自身がhostの全source列挙や任意hook実装の無入力性を証明するものではない。
callerやモデルのsource一覧・fresh宣言から実host証拠を作らない。
実bridgeとSDK適合は[後続Issue1010](https://github.com/tackeyy/mission/issues/1010)で扱う。

`skills/mission/lib/fresh_review_ipc.py`は、operationへ束縛された短命capability、
閉じたframe、専用socket FDの複製と回収を検査する。capabilityは親identityの出所ではない。
FDを既存`spawn_deadline_exec`へ渡す場合だけ継承を許容し、新spawn入口は追加しない。
guardianとownerの実運用は[後続Issue1009](https://github.com/tackeyy/mission/issues/1009)で扱う。

既存5method/5field registry、pin、callback transport、予算writerを変更していない。
既定のprovider・adapter登録・SDK依存は追加していない。

## RedとGreen

- 観測module欠落のRedから、policy安定読取と未列挙source拒否を追加。
- hook契約欠落のRedから、推移的入力の閉包・循環拒否・固定出力digest照合を追加。
- 動的source・未閉包入力が通るRedから、閉じたmanifest検査を追加。
- identity結合欠落のRedから、query owner、startupとinitの一致、重複・交差のsticky拒否を追加。
- IPC欠落・改ざんのRedから、閉じたfield、boot/operation/request、nonceとcapability検査を追加。
- 宣言長・frame数の上限が通るRedから、2MiB/frame、16MiB/operation、256frame上限を追加。
- capability期限契約欠落のRedから、最大300秒のmonotonic期限と途中期限切れ拒否を追加。
- FD helper欠落のRedから、接続済みlocal stream socketの複製・例外時回収を追加。

## テストの検出価値

`test_issue1008_host_observation.py`は、policy変更・実symlink・欠落source、
動的source種別・固定出力から変わった親要約、hook出力未観測、native eventの欠落・重複・交差を検出する。
fixtureの投影を検査しており、native SDKの実イベント型・fresh contextを実証していない。

`test_issue1008_host_ipc.py`は、改ざん・再送・duplicate key・部分frame・過大入力・
期限切れによる誤dispatchと、owner生存pipeの漏出によるEOF停止の妨害を検出する。
大きな入力はbytes上限の実境界を一度検査する。実processはFD継承の1ケースだけで、
残りは同期的なparser/FD検査。実processがなければbootstrapを越えたFD保持/閉鎖は検出できない。
寿命・watchdog故障の全組合せをここへ複製しない。

keeperは`test_issue896_adapter_registry.py`の登録・source pin、
`test_issue909_fresh_review_receipts.py`のreceipt契約、
`test_issue917_fresh_review_bounds.py`の共通上限、
`test_issue912_fresh_review_dispatch.py`のfence・cancel未確認拒否。
既存シナリオの削除はない。

CIは`.github/workflows/ci.yml`の`make test-shard`から`skills/mission`を選択し、
`scripts/ci_shard_targets.py`で追跡済みtest fileを分割する。新規2ファイルはcommit後に対象となる。
CIの実行結果はまだ取得していない。

## ローカル検証

- 既存registry baseline:121件成功、2.04秒。
- 新規2ファイル、writer/spawn/module inventories、mirror同期:630件成功、11.20秒。
- spawn inventoryが新しい`os.O_ACCMODE`定数参照を未分類として拒否したため、
  非spawn定数として明示分類。既存SAFE列挙の回帰検査がこの分類も通る。

独立探索・新規セッションレビュー・独立Checker・required CI/exact-headは別のgateとして残る。
model-free成功を親Issue897の実host受入へ昇格させない。

独立context探索は固定checkpoint1の観測module
`sha256:19f5da6006e724de0c2aac564953056be1ac693e00df5109e288a4815fd91922`を対象に89入力を検査し、
accept10・blocked79・想定外例外0・期待との差0。
最初の探索ではstable readerをstubに置換していたため、実`mission_persistence.strict_reader`依存でも
同じ固定snapshotを独立再実行し、同じ結果を確認した。
現候補全体・IPC/FD・実hostの保証には読み替えない。

独立IPC/FD探索はcheckpoint2の104入力でaccept17・blocked87・想定外例外0・期待との差1。
実cleanup/strict reader依存による独立再実行でも、成功EOF後に別nonceのframeが通る反例を再現した。
同nonce再送のkeeperではreplay拒否に隠れるため、別nonceのEOF後再入力を検査するテストを追加。
Redの`DID NOT RAISE`を確認し、成功EOFもchannelを終端化する修正後、IPC24件成功・0.20秒。
再探索は修正済みcheckpoint3を対象とし、旧snapshotの結果を現候補へ流用しない。
実依存でcheckpoint3の104入力を独立再実行し、accept16・blocked88・想定外例外0・期待との差0。
修正対象moduleは`sha256:bd52a04b64c16adb996db8233cb38d462a983856052815599c0c63be165192b6`。
