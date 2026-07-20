# 再利用可能な state snapshot

`mission-audit.py` は、明示的に作成した短時間有効・read-only の state
snapshot を、後続の audit と複数の `stats` 期間で再利用できます。

```bash
python3 scripts/mission-audit.py \
  --root /path/to/projects \
  --snapshot-out /tmp/mission-state.snapshot.json \
  --snapshot-ttl-sec 300 \
  --json

python3 scripts/mission-audit.py \
  --snapshot-in /tmp/mission-state.snapshot.json \
  --since 2026-07-01 \
  --json

python3 skills/mission/bin/mission-state.py stats \
  --snapshot /tmp/mission-state.snapshot.json \
  --since 2026-07-01 \
  --json
```

snapshot は明示指定時だけ使います。snapshot option を省略した場合は従来どおり直接探索します。
出力先は全 scan root の外に置きます。snapshot 作成自身による discovery metadata の変化を
防ぐためです。コマンドが snapshot を Git や他の artifact store へ自動追加することはありません。

## 正確性の契約

snapshot は期間 filter・dedupe 前の全 parsed record を保持します。各 audit / stats は
自身の期間 filter を適用してから dedupe するため、期間外の高 rank record が期間内 record を
隠しません。ordered root multiset、record identity/index、record/discovery count、
schema・CLI・record・discovery・dedupe contract version、invalid archive inventory、
content digest も保存します。

`observed_at` は時間依存の health 分類を全 consumer で固定します。`created_at` は
`ttl_seconds` と組み合わせる wall-clock の cache age です。production capture では通常、
両 timestamp は近接しますが、決定的な audit clock では意図的に異なる場合があります。
どちらも timezone 必須です。

capture は audit の堅牢な archive discovery と manifest semantic validation を使います。
走査した各 directory と全 `.mission-state` fileについて、path/type/device/inode/mode/
size/mtime/ctime metadata を記録します。root 外の scoring / specialist evidence 候補は、
まだ存在しない path も含めて別に記録します。atomic write 前に metadata inventory を再計算し、
capture 中の drift を拒否します。snapshot には完全な inventory を重複保存せず、inventory の
count / digest、再現に必要な root 外候補 path、各 record の source entry を保存します。

consume は metadata-only rewalk 1回と root 外 evidence の `lstat` だけを行います。
state/evidence content の再read・再hash・再parseは行いません。metadata が完全一致した後だけ、
capture時のarchive semantic validation結果を再利用します。state、directory、pointer、manifest、
evidence、legacy candidate、generation の変更は snapshot を stale にします。

snapshot は同一directory内の一意なtemporary file、mode `0600`、file `fsync`、atomic replace、
directory `fsync` で保存します。consumer は symlink、非regular file、group/world-readable file、
期限切れ・未来時刻、root/version/count/index/digest不一致、stale discoveryを拒否します。
invalid snapshotからlive scanへのsilent fallbackはありません。

snapshot は owner が管理する local trusted artifact であり、認証済み交換形式ではありません。
mode `0600`、content digest、semantic self-consistency、live metadata freshness は、事故または
一部 field の改変を検出します。認証鍵がないため、悪意ある owner が関連 field をすべて書き換え、
digest を再計算する攻撃までは防げません。信頼できない利用者や transport から受け取った
snapshot は使用しないでください。

## 性能の範囲

削減対象は、snapshot consumer における state/evidence byte read、content hash、JSON parse、
archive semantic validation の重複です。freshness のためmetadata rewalk 1回は残します。
filter-before-dedupeが正確性要件なので、期間filterとgroup構築もconsumerごとに残します。
性能効果は代表fixtureのbenchmark結果だけで判断し、filesystem traversal全廃とは表現しません。

最終 local benchmark は、80 project、660 state variant、640 evidence file、3,200 unrelated
file の synthetic fixture を warm APFS 上で使用しました。2回 warmup 後、14回の AB/BA
counterbalance run で、direct audit + direct stats 3期間と、snapshot-out audit + snapshot
stats 3期間を比較しました。全 run で4つの JSON output は一致しました。direct median は
0.4515秒（MAD 0.0028秒）、snapshot median は0.7039秒（MAD 0.0050秒）で、snapshot は
1.56倍遅い結果でした。snapshot size は1,223,615 bytes、discovery entry は3,081件です。

したがって、この release では end-to-end の速度改善を主張しません。実測上の価値は、live
drift を拒否した再現可能な複数期間分析です。一方、regression counter により consumer が
candidate load、state/evidence content の read/hash/parse、archive semantic validation を
省略することは確認済みです。この benchmark は synthetic かつ warm-cache のため、cold disk や
他 filesystem の挙動までは示しません。将来の速度改善候補は、freshness contract を弱めず、
1回だけ validate して全 requested window を出力する single-process batch command です。
