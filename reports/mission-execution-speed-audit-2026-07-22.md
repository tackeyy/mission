# Mission 実行速度監査 2026-07-22

## 結論

品質ゲートを弱めずに速くする余地はある。ただし、現時点で最も大きな問題は「遅い処理」そのものより、実行時間の分類欠損、差分修正でもfull reviewへ戻るorchestration、fork時に継承される大きな履歴である。bounded checkerの入れ子運用は有力な追加仮説だが、親子lineageを取得するまで効果量を断定しない。

厳密な改善後 cohort では、Mission state 28件のうち pass は11件、halt は15件、incomplete は2件だった。pass の最終 composite score は平均4.90、forced pass・ungated pass・P0 finding は0件であり、完了した実行の品質ゲートは維持されている。一方、activity が記録された state は14/28件、phase時間に対する activity coverage は9.96%に留まり、149,370秒が active / wait / idle のいずれにも分類されていない。この状態で「実行時間のX%を削減できる」と断定するのは不可能である。

優先順位は次の通り。

1. unknown時間をactiveへ塗り替えないactivity計測契約を先に確立する。
2. iteration 2以降の限定差分では、review agreementを計算できる独立reviewer 2名をstate-drivenに強制する。
3. bounded reviewer / checkerへ渡す履歴をevidence manifestで限定し、欠落時はfull-historyへfail-safeする。
4. reviewer待機を1回のevent-driven fan-inへまとめ、30秒pollと不要なorchestration turnを減らす。
5. false staleを生むPID ownershipを、fencing付きsession leaseへ独立して置き換える。
6. 親子lineageを計測したうえで、bounded checker / reviewerのevidence-provider pilotを行う。

この6件は threshold、open High、review agreement、required evidence、TDD、exact-head acceptanceを変更しない。

## 監査範囲

### 基準時点

- 改善群: `2ae61e9..9a17f73`（2026-07-21）
- transition cohort: `d84bacb`（review tierの否定文較正、2026-07-21 03:43:29 JST）以降
- strict post-improvement cohort: `9a17f73`（昨日の改善群の最終merge、2026-07-21 18:40:46 JST）以降
- 観測終端: 2026-07-22 14:02:57 JST（この監査自身を母集団から除外）
- `790d85d` は元ログを変えない監査分類側の後続修正として扱った

### 参照したログ

- `/Users/<user>/dev`、`/Users/<user>/workspace`、agent worktree、設定repository以下の current / archived Mission state
- immutable worktree archive generation と scoring / review evidence
- 2026-07-21および2026-07-22の strict cohort に開始した raw agent rollout 38ファイル
- Mission state 走査候補510件から strict cutoffで絞り込み、identityとarchive lineageで重複排除した28件

raw state、raw rollout、実home path、個人メモ内容はこの公開可能レポートへ収録していない。

### 完全性の制約

再利用snapshotは走査中の別session更新を検出し、2回ともfail-closedした。最初のdirect auditは29件、その後の固定期間censusは28件となった。これはcutoff以前に更新されたactive stateが、その後in-place更新されてcutoff外へ移動したためである。最終集計は後続censusの28件を採用し、初回29件との差を欠落として隠していない。

また、`mission-state.py stats` は日時を日単位へ丸めるため、厳密な時刻cutoffの独立照合には使えなかった。日単位statsは83件、strict auditは28件であり、同じ母集団を表す数値ではない。

### 固定census manifest

28件を取得した時点の集計は、audit revision `790d85d`、cutoff `2026-07-21T18:40:46+09:00`、観測終端 `2026-07-22T14:02:57+09:00`、探索root `/Users/<user>/dev`、`/Users/<user>/workspace`、`/Users/<user>/.codex/worktrees`で固定した。設定repositoryの補助探索を加えた再集計でも対象件数は28件だった。入力510件、期間filter後28件、identity dedupe後28件、duplicate group 0、invalid archive 0である。

下表は`(project_root, session_id, mission_id)`のSHA-256先頭12桁と、公開可能な集計フィールドのrow digestである。raw identityやMission本文は収録しない。manifest全体のdigestは`1a82411b7fb3b5a0affe289d8df76c2736d86365f4562e41be61fa6585b0060c`。

| identity hash | started UTC | updated UTC | result | tier | complexity | iter | row digest |
|---|---|---|---|---|---|---:|---|
| `29ba391d369e` | 2026-07-21T08:42:51Z | 2026-07-21T09:42:26Z | pass | full | Critical | 2 | `a331e12f4389` |
| `7fa6ef2b80bb` | 2026-07-21T08:44:02Z | 2026-07-21T09:44:50Z | pass | full | Complex | 1 | `db98093993a0` |
| `16e1ead64908` | 2026-07-21T09:46:39Z | 2026-07-21T09:51:30Z | pass | full | Complex | 1 | `d846f6626629` |
| `7aabefe02fc4` | 2026-07-21T09:50:32Z | 2026-07-21T12:21:42Z | pass | full | Complex | 2 | `847c1e819519` |
| `134eda9b184f` | 2026-07-21T09:47:51Z | 2026-07-22T01:58:30Z | pass | standard | Standard | 1 | `0a820df610da` |
| `f489c87f0e14` | 2026-07-22T01:59:23Z | 2026-07-22T02:00:38Z | halt | full | Complex | 0 | `d4ec19d114a4` |
| `34c85e0b8fb3` | 2026-07-22T01:57:15Z | 2026-07-22T02:06:37Z | pass | light | Simple | 1 | `ffb1f6f46353` |
| `fb5cd94896a3` | 2026-07-21T09:44:42Z | 2026-07-22T02:12:48Z | halt | full | Complex | 0 | `204a5733f284` |
| `931e3a7d0e5a` | 2026-07-22T01:59:26Z | 2026-07-22T02:13:16Z | halt | full | Complex | 0 | `c2fd240b725b` |
| `99563ab521e2` | 2026-07-22T02:13:35Z | 2026-07-22T02:17:32Z | halt | full | Complex | 0 | `03426d9bd91b` |
| `33bca8e4ea1b` | 2026-07-21T09:45:54Z | 2026-07-22T02:17:48Z | halt | full | Complex | 0 | `b490973bd487` |
| `e6a5515e3f9b` | 2026-07-22T02:22:34Z | 2026-07-22T02:23:58Z | halt | full | Complex | 0 | `06ae878aa4a1` |
| `1f86a5314696` | 2026-07-21T09:42:00Z | 2026-07-22T02:30:17Z | pass | full | Critical | 1 | `bca803feb442` |
| `a693e33aa5af` | 2026-07-22T02:25:51Z | 2026-07-22T02:30:28Z | halt | full | Critical | 0 | `17f01f50e1e4` |
| `ccbdfd83fa06` | 2026-07-22T04:24:30Z | 2026-07-22T04:24:36Z | halt | full | Complex | 0 | `6ea1bd91c2e0` |
| `c14153508509` | 2026-07-22T04:22:24Z | 2026-07-22T04:34:52Z | halt | full | Critical | 0 | `4215fd3a120b` |
| `10c84eeda5c9` | 2026-07-22T04:36:44Z | 2026-07-22T04:41:12Z | halt | full | Complex | 0 | `33e97e46a85f` |
| `231ff7e9f104` | 2026-07-22T04:22:35Z | 2026-07-22T04:41:30Z | pass | full | Complex | 1 | `a73227b72078` |
| `160e27e6e562` | 2026-07-21T07:03:36Z | 2026-07-22T04:42:47Z | halt | full | Critical | 2 | `76120227c91f` |
| `9afe55a19afb` | 2026-07-22T04:43:40Z | 2026-07-22T04:46:54Z | halt | full | Complex | 0 | `e6f9538c392d` |
| `b0cc2a9e25eb` | 2026-07-22T02:12:48Z | 2026-07-22T04:48:22Z | halt | full | Complex | 0 | `d3c723cf2281` |
| `d683b4ada764` | 2026-07-22T04:43:49Z | 2026-07-22T04:48:22Z | halt | full | Complex | 0 | `297ac3f7eca5` |
| `d21ee17be5e0` | 2026-07-22T04:33:48Z | 2026-07-22T04:49:57Z | pass | light | Critical | 1 | `b0c55ad72f94` |
| `6c8bdc6857b5` | 2026-07-22T02:29:26Z | 2026-07-22T04:52:24Z | pass | full | Critical | 1 | `387ed0ef5afa` |
| `5c96d44f2c7f` | 2026-07-22T04:54:12Z | 2026-07-22T04:54:12Z | incomplete | standard | Standard | 0 | `a6d5a4420bbd` |
| `f26a46ee6159` | 2026-07-22T01:56:46Z | 2026-07-22T05:01:48Z | pass | full | Complex | 3 | `f117c310804b` |
| `d2a6a0bc9c32` | 2026-07-22T04:53:21Z | 2026-07-22T05:02:22Z | halt | full | Critical | 0 | `1f018eab794f` |
| `8ca53be42a22` | 2026-07-22T04:35:13Z | 2026-07-22T05:02:47Z | incomplete | full | Critical | 0 | `542ef1fd4a4a` |

raw rolloutは日別に2026-07-21が5件、2026-07-22が33件。filename開始時刻とrecord timestampの両方へ同じcutoffを適用した。

## 実測結果

### Mission state

| 指標 | strict cohort |
|---|---:|
| state | 28 |
| 改善後に開始 | 25 |
| 境界跨ぎ | 3 |
| pass / halt / incomplete | 11 / 15 / 2 |
| full / standard / light | 24 / 2 / 2 |
| Complex / Critical / Standard / Simple | 16 / 9 / 2 / 1 |
| pass平均 composite | 4.90 |
| forced / ungated / P0 | 0 / 0 / 0 |
| activityあり / なし | 14 / 14 |
| activity coverage | 9.96% |
| 未分類phase時間 | 149,370秒 |
| slow session（300秒超） | 18 |

passしたfull tier 8件のphase中央値は2,391.5秒、light tier 2件は727.5秒だった。ただしtask難度と役割が異なる少数標本なので、light化による因果効果としては扱わない。light 2件のscore平均は4.81、full 8件は4.92で、いずれもgateを通過した。

halt 15件の内訳は stale 5、partial-done 4、other 4、awaiting-approval 1、blocked-external 1だった。stale 5件のうち複数は0秒または短時間のchecker stateであり、実作業失敗ではなくownership判定・terminalizationの問題を含む。

参考として、最初の速度関連改善 `d84bacb` 以降を含むtransition cohortは87件（pass 49 / halt 35 / incomplete 3）、pass平均composite 4.81、activityあり51件、coverage 19.92%だった。改善実装中のstateを含むため主評価には使わないが、strict cohortでもactivity採用が十分に定着していないことを確認する補助線になる。

### Phase分布

全28件の記録済みphase時間は次の通り。

| phase | 秒 | 構成比 |
|---|---:|---:|
| executing | 86,318 | 52.1% |
| halted | 51,944 | 31.4% |
| planning | 20,001 | 12.1% |
| reviewing | 6,526 | 3.9% |
| scoring | 771 | 0.5% |

`halted`のほぼ全量は長期の外部承認待ち1件であり、性能劣化として削る対象ではない。`executing`も単一の長時間stateに強く支配され、そのstateはactivity未記録だった。よって平均やp90をそのまま最適化目標にしない。

### Raw rollout / tool call

strict cohort に開始したraw rollout 38ファイルから、観測終端までのcall IDを重複排除した。

| 指標 | 値 |
|---|---:|
| rolloutファイル | 38 |
| paired tool call | 578（dependency bootstrapを除外） |
| output欠落 | 0 |
| paired tool時間 | 約1,218秒 |
| wait系call | 35回 / 約506秒 |
| Mission state call | 79回 / 約49秒 |
| Mission audit call | 18回 / 約56秒 |
| repository hosting参照 | 44回 / 約143秒 |
| test関連call | 34回 / 約70秒 |

paired callはdependency bootstrapを含める集計では579件になる。waitの直接callは31回・約495秒で、wait primitiveを内包するwrapper 4件を同じ分類へ加えると35回・約506秒になる。本レポートの表は後者を使用した。これらは並行処理を含む累積tool時間であり、wall-clockのcritical pathではない。その多くは子処理やreviewerの実行中に待っている時間なので、506秒すべてを削減可能時間とはみなさない。改善対象は、30秒timeout後に再pollするorchestration turnと、複数待機先を個別に確認するfan-in方式である。

pass平均4.90は「passした11件が現行gateを通った」ことを示すが、改善前cohortに対する品質非劣化を単独で証明するものではない。非劣化判定は後述のpaired benchmarkで行う。

### Raw rollout / model context

forkへ継承されたhistoryと、最初の`trigger_turn`以後に新規発生したeventを分離した。`token_count`の累積値変化をmodel turnのproxyとして使う。

| 指標 | 値 |
|---|---:|
| root / fork rollout | 9 / 29 |
| 新規model turn proxy | 778 |
| input token累計 | 86,025,256 |
| cached input比率 | 95.1% |
| 1 turn input p50 / p90 / max | 104,085 / 205,907 / 243,710 |
| context window | 258,400 |
| window 50%超 / 75%超 | 285 / 107 turn |
| forkへ複製されたhistory | 9,050 record / 15.5 MB |
| 複製history内のtoken event | 5,338 |

入力の95.1%はcache済みなので、token量をそのままlatencyやcostへ換算しない。ただし29/38 rolloutがforkで、36.6%の新規turnがwindowの50%を、13.8%が75%を超えた。継承historyはrollout走査量も増やすため、bounded context projectionは独立benchmarkに値する。

## Findings

### F1: PID ownershipが短命runnerをownerとして記録し、false staleを作る

- 重大度: High
- 根拠: strict cohortのhalt 15件中stale 5件。この監査自身でも`refresh-pid`直後の`resume`が新PIDをdead/reusedと判定し、2回自己haltした。
- 原因: agent CLIを親process treeで発見できない実行環境では、`find_agent_pid()`が短命shell/runnerのPIDへfallbackする。次のcommandではそのPIDが消えている。
- 影響: state再作成、resumeやり直し、halt分類ノイズ、active sessionの誤停止。
- 品質リスク: 高。設計を誤るとsplit-brain、ABA、foreign session takeoverを起こすため、activity変更とは別Issue / PRで扱う。

推奨: PID単独ではなく、owner session ID・ランダムlease ID・単調増加fencing epochをStateLock内CASで更新する。unexpired foreign leaseは必ず拒否し、expired takeover後は旧epochの書込みも拒否する。PIDはownershipの管理元ではなくliveness補助として残し、継承証跡を保存する。clock rollback、PID再利用、旧owner復帰、同時renewを攻撃testに含める。cohortのstale 5件すべてが同じ原因とは未確認なので、削減効果はこの監査で再現したfalse staleに限定して主張する。

### F2: activity観測がopt-inのため、速度改善の根拠が作れない

- 重大度: High
- 根拠: activityあり14/28、coverage 9.96%、未分類149,370秒。
- 影響: active work、reviewer wait、approval wait、idle、crash gapを分離できず、長時間stateを性能問題と待機問題に分類できない。
- 品質リスク: 低。計測のみでpass/fail gateは変えない。

推奨: `next`はread-onlyのまま維持し、書込みは明示的な`advance --phase <phase> --activity <kind:reason>`へ限定する。active segmentは最終heartbeat / progress時刻で上限を設け、lease失効後をactiveではなくunknown / unobservedへ送る。phaseだけ進んでactivityが空の状態は作れないようにしつつ、crash / compaction / 放置時間をactiveへ塗り替えない。coverageだけでなく、raw tool eventとの分類一致率と「lease失効後のactive秒数0」を受入条件にする。findingはpass gateと分離する。

### F3: iteration 2以降の限定差分でもfull reviewer数のままである

- 重大度: High
- 根拠: 3 iterationを要した実装runで、iteration 3のaggregate evidenceにA/B/Cの3 reviewerが記録されていた。iteration 3は過去findingの差分修正確認で、新規scopeは記録されていない。review phaseは1,155秒だった。
- 現行品質境界: review agreementは独立scoreが2件以上ないと計算できない。reviewer 1名化は品質ゲート不変と両立しない。
- 影響: full 3名から独立diff reviewer 2名へ限定できる場合、対象iterationのreviewer model workとslot使用は人数差の上限として最大1/3削減余地がある。これは実測cost / wall-clock改善率ではない。
- 品質リスク: 低〜中。既存規約どおりに限定し、High/Medium finding・exact HEAD・全test gateは維持する。

推奨: critic planからfinding IDと`new`有無をstateへ構造化し、`new`なし・固定base HEAD・前回findingのみの全条件を機械確認して`next`がrequired reviewer count 2を返す。2名は最終candidate HEADを独立reviewし、agreementを再計算する。HEAD drift、新規scope、未確認finding、agreement不足があれば即full 3名へ戻す。Medium以上のインライン修正後も最終HEADに対する独立scoreを2件揃え、aggregate時に期待数と実数を監査する。

### F4: bounded checkerが独立したfull Missionとして起動される

- 重大度: Medium
- 根拠: 28件中24件がfull tier。Issue Size Checker、Planning Checker、semantic review、post-merge audit等の名称を含むstateがあり、checker/review/audit語を含むstateは19件だった。ただし、全19件の親子lineageと重複phase / scoringは記録されておらず、二重loopを断定できない。
- 影響: 子タスクにもrootと重なるlifecycleが存在する可能性があり、子handoffがhaltとして混ざる。削減効果は未測定の仮説である。
- 品質リスク: 中。子の独立性を消してはならない。

推奨: 先に`parent_session_id`、`role=root|evidence-provider`、fixed HEADを記録し、親子lineageが確認できたstateだけで重複phase / tool call / score処理を集計する。その後、Issue Size Checker 1種類で`evidence-provider` roleをpilotする。子はbounded purpose、finding、score、provenanceを`mission-review/1`として返し、rootだけがaggregate、push-score、mark-passesを所有する。子の独立contextとMaker-Checker境界は維持する。

### F5: reviewer / subprocess待機がpoll turnを増やす

- 重大度: Medium
- 根拠: wait系35 call、paired tool時間約506秒。30秒のwait / wait_agent / write_stdin timeoutが繰り返されていた。
- 影響: 待機対象が未完了のたびにagent turn、token、状態確認が増える。実処理時間そのものは短縮しない。
- 品質リスク: 低。

推奨: 複数reviewerを1回のbounded event-driven fan-inで待ち、最初のattention/completionでwakeする。timeoutはhang検知のsafety netとして残し、poll間隔短縮は行わない。独立reviewerは最初に全員spawnし、完了順に次工程へ渡すrolling windowを使う。

### F6: audit snapshotが並行更新1件で全体失敗する

- 重大度: Medium
- 根拠: この監査でsnapshotが2回fail-closedし、audit / statsの同一母集団比較ができなかった。raw rolloutではMission audit 18回、約56秒。
- 影響: 全root再scan、集計差、改善レポート作成時間の増加。
- 品質リスク: 中。stale snapshotを黙って使う変更は不可。

推奨: record単位のimmutable copyに加え、path manifest、directory membership、identity groupを二重collectする。変更されたpath、そのidentity group、新規作成・削除・archive移動を再検証し、安定epochを得られなければchanged-path一覧付きでincompleteとしてfail-closedする。auditとstatsは同じsnapshot IDを消費し、statsの期間parserもauditと同じISO UTC parserへ統一する。

### F7: exact-head evidence再利用は効果量未計測の仮説である

- 重大度: Low
- 根拠: repository hosting参照44回・約143秒、git状態参照28回・約14秒、test関連34回・約70秒。ただしstable request keyによるunique / repeated分離は未実施。
- 影響: 同一HEAD、同一object、同一test結果の反復があればagent turnを減らせるが、再利用可能件数と秒数は未測定。
- 品質リスク: 中。mutable stateの古いcacheは誤判定を生む。

推奨: 先に`(kind, object ID, head OID, updatedAt, command, environment fingerprint)`をstable keyとして記録し、unique / repeated callと再利用可能秒数を計測する。効果が確認できた対象だけcontent-addressed Evidence Bundleへ移す。再利用対象は客観証跡に限定し、reviewerの結論・severity・scoreは共有しない。working tree、HEAD、CI run、外部state、TTLのいずれかが変わればinvalidateし、merge直前のexact-head / CI再確認は省略しない。

### F8: state commandが細粒度で、1境界に複数callを要する

- 重大度: Low〜Medium
- 根拠: Mission state call 79回・約49秒。この監査でも`set phase`、`activity start`、`next`を連続実行した。
- 影響: lock取得、process起動、JSON read/write、agent turnが増える。単体latencyは小さいが全runで積み上がる。
- 品質リスク: 低〜中。atomic command内で既存validatorをそのまま呼ぶ必要がある。

推奨: `advance`、`review-finalize`、`closeout`等のtransactional commandを追加する。内部では既存のphase validator、evidence gate、agreement gate、specialist accountingを順番に実行し、途中失敗時はstateを進めない。

### F9: performance監査がgeneral taskとして分類された

- 重大度: Low
- 根拠: 「全実行ログ」「実行速度」「改善案」を含むこのtaskがtask profile `general`、specialist候補0件となり、performance reviewerを手動選定した。
- 影響: 適切な観点の選定漏れと追加turn。
- 品質リスク: 低。

推奨: task profileへlatency、duration、p50/p90、execution log、speed audit等の中立signalを追加し、単語一致だけでなくmetric/output要求を組み合わせてconfidenceを上げる。

### F10: full-history forkがcontextとrollout走査量を増やす

- 重大度: High
- 根拠: 38 rollout中29件がfork。継承historyを分離した新規778 turn proxyでもinput p50 104,085、p90 205,907。285 turnがcontext windowの50%超、107 turnが75%超だった。forkへ9,050 record、15.5 MBが複製されていた。
- 影響: 長いcontextがmodel critical pathへ与える実測latencyは未取得だが、各turnのinput処理とraw rollout走査量を増やす。bounded taskへ無関係な親履歴を渡すと、判断ノイズも増える。
- 品質リスク: 高。必要な規約、過去finding、固定HEAD、証跡を落とすとreview品質が下がる。

推奨: reviewer / checkerのforkに`bounded-evidence` modeを追加し、Mission goal、bounded purpose、適用規約、固定base / candidate HEAD、対象artifact、客観証跡manifest、前iteration finding IDだけをcontent manifestで渡す。依存関係が不明、manifest検証失敗、reviewerが不足を申告した場合はfull-historyへfail-safeする。`context_manifest_digest`、除外category、fallback理由をstateへ残し、同じ欠陥注入fixtureをfull-history / bounded双方で検出できる場合だけ採用する。

## 優先ロードマップ

| 優先 | 改善 | 期待効果 | 実測確度 | 工数 | 品質ガード |
|---|---|---|---|---|---|
| P0 | accurate phase/activity | unknownを守りながら計測可能時間を増やす | 高 | 中 | heartbeat上限、lease失効後unobserved |
| P0 | fenced session lease設計 | false staleと再開やり直しを削減 | 中〜高 | 中〜大 | CAS、epoch、foreign owner拒否、攻撃test |
| P1 | diff-reviewer count enforcement | iter2+のmodel work/slot占有を最大1/3削減 | 中〜高 | 小〜中 | 独立2名、agreement再計算、`new`時full |
| P1 | bounded context projection | forkのinputとrollout走査量を削減 | 中 | 中 | manifest検証、欠落時full-history |
| P1 | event-driven reviewer fan-in | poll turnと待機管理を削減 | 中 | 中 | hang timeout・partial failure保持 |
| P2 | incremental stable snapshot | audit再scanと集計差を削減 | 高 | 中 | changed path再検証、安定しなければfail |
| P2 | evidence-provider pilot | lineage確認済みのnested lifecycleを削減 | 低〜中 | 中〜大 | 独立context、root aggregate、Maker-Checker |
| P3 | exact-head repeat instrumentation | 再利用可能な重複を先に計測 | 未計測 | 小〜中 | stable key、final recheck |
| P2 | state transactional commands | process/lock/turn overheadを削減 | 高 | 中 | existing validator再利用、atomic rollback |
| P3 | task profile signal改善 | specialist選定漏れを削減 | 高 | 小 | false-positive fixture |

Pは緊急度を示し、後述の番号順は依存関係・変更リスクを含む実施順を示す。高リスクのleaseはP0でも、計測と低リスクのorchestration変更を先に行う。

## 検証契約

改善は単発の自己評価で採用しない。代表cohortを固定してpaired benchmarkを行う。

### Cohort

1. Simple read-only audit
2. Standard bounded checker
3. Complex implementation、iteration 1 pass
4. Complex implementation、iteration 2差分修正
5. external approval waitを含むCritical task

各taskをbaseline / candidateでpilotは最低3回実行する。exact commit OID、agent / tool revision、model、input、review tier、concurrency上限、host / dependency / environment fingerprint、random seedを固定し、cache cold / warmを分離する。各cohortで1回のwarm-upを採用判定から除外し、AB / BAを交互化または事前seedでrandomizeする。Critical approval taskは外部作用のない決定論的fixtureを使う。

N=3ではmedian・range・tool countだけを見て計測系を検証し、candidateを採用しない。本採用は各cohort最低10回、できれば20回で事前登録した判定を行う。主対象cohortはpaired wall-clock中央値を10%以上改善し、非対象cohortとp90は5%を超えて悪化しないことを速度条件とする。cohortごとのpaired差と95% bootstrap区間も記録し、pooled平均だけでは採用しない。

### 速度指標

- wall-clock median / p90
- observed active time
- reviewer-wait / approval-wait / external-wait
- unclassified timeとcoverage
- agent turn数、tool call数、wait poll数
- reviewer数、test実行数、外部状態参照数
- input / cached / output / reasoning token、context window使用率
- fork history record数・byte数、bounded-context fallback数

### 品質非劣化条件

- `passes=true`
- compositeとmin itemはcohort単位でbaselineを下回らない（品質の非劣化幅0）
- `open_high=0`
- required review / specialist / scoring evidenceが全件存在
- forced / ungated pass 0
- current P0/P1増加なし
- exact-head test / CI / artifact hygieneがgreen
- halt/approval safety gateの見逃し0
- 同一の欠陥注入fixtureをbaselineとcandidateの全runで検出

速度が改善しても上記を1つでも満たさなければ不採用とする。

## 採用しない案

- 全taskを一律lightにする
- thresholdやmin itemを下げる
- reviewer agreement、open High、scoring evidenceを省略する
- test / preflight / exact-head確認を減らす
- `mark-passes --force`を自動利用する
- wait 506秒をそのまま「削減可能時間」とみなす
- halt時間を性能問題として削る

## 次の実装順

1. unknown時間を守るactivity精度契約を定義し、明示的atomic `advance`を実装する。
2. diff-reviewer count監査を追加し、独立2名とagreement再計算をstate-drivenにpilotする。
3. bounded context manifestを実装し、checker 1種類でfull-history fallback付きpilotを行う。
4. reviewer fan-inを1回のevent-driven待機へまとめる。
5. fencing付きsession leaseを独立Issue / PRで実装する。
6. 1週間または100 stateでcoverageと分類一致率を観測する。coverage 95%以上に加え、lease失効後のactive秒数0を必須とする。
7. snapshotのmanifest / identity二重collectを実装する。
8. 親子lineageを取得してからbounded checker 1種類でevidence-provider pilotを行う。
9. stable request keyで証跡反復を計測し、paired benchmarkで品質非劣化を確認できた対象だけEvidence Bundleへ展開する。

現時点では、速度削減率を宣言するよりも、この順序で「何がactive workで、何が待機・重複・誤haltか」を機械的に分離する方が正しい。
