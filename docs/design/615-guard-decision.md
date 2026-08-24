# #615 GuardDecision と Stop hook 判断移管 設計書

## 0. 結論

`scripts/mission-stop-guard.sh` は現在、Python verdict の表示アダプターではない。session 候補の順序付けと選択、stale 時の保留条件、発火コマンド、発火後の成否、heartbeat/detail の表示選択を shell が決めている（`scripts/mission-stop-guard.sh:228-272`, `scripts/mission-stop-guard.sh:275-321`, `scripts/mission-stop-guard.sh:327-355`）。これは ADR-006 が記録した乖離そのものである（`docs/adr/006-kernel-reducer-adjudication.md:28-32`）。

本設計では、application 層の `GuardDecision` を唯一の判断 authority にする。shell に残すのは次の 4 点だけとする。

1. hook input をそのまま Python へ渡す。
2. Python が返した typed decision を `jq` でデコードする。
3. `none` / `mark-halt` / `cleanup-stale`（`execute=True` 固定）/ `stop-guard-observe` の閉じた `case` で発火する。
4. コマンド実行結果を計算せず Python へ返し、最終的に Python が用意した `shell_text` を表示する。

この境界は「判断は Python、発火は shell、コマンド一覧は閉じる」という ADR-006 の決定をそのまま実装可能な形にしたものである（`docs/adr/006-kernel-reducer-adjudication.md:60-78`）。hook 機構と freshness の式は変えない。freshness は現在の timestamp 優先順、`> 3600` の warn、`> MISSION_STALE_HALT_SECONDS` の stale をそのまま application decision へ移す（`skills/mission/bin/mission-state.py:10274-10289`, `skills/mission/bin/mission-state.py:10471-10499`, `skills/mission/lib/mission_common.py:169-201`）。

## 1. 調査範囲と実測ベースライン

- 対象基点: `ba5a87c`（依頼指定）。調査開始時の worktree は `investigate/615`、変更なし。
- `scripts/mission-stop-guard.sh` は 361 行であり、本調査では `1-361` を全行確認した。
- 既存回帰テストの実測:
  - `python3 -m pytest -q skills/mission/tests/test_stop_hook.py skills/mission/tests/test_stop_guard_dedupe.py skills/mission/tests/test_issue377_parallel_mission.py -k 'stop_guard or hook'` → `57 passed, 6 deselected`。対象契約は `skills/mission/tests/test_stop_hook.py:41-770`, `skills/mission/tests/test_stop_guard_dedupe.py:58-259`, `skills/mission/tests/test_issue377_parallel_mission.py:220-389`。
  - R1 / mirror の選択実行 → `12 passed, 99 deselected`。対象契約は `skills/mission/tests/test_issue512_r1_authoritative_reader.py:656-724`, `skills/mission/tests/test_issue512_r1_authoritative_reader.py:977-984`, `skills/mission/tests/test_issue512_r1_authoritative_reader.py:1152-1184`, `skills/mission/tests/test_issue512_r1_authoritative_reader.py:1749-1759`, `skills/mission/tests/test_plugins_in_sync.py:268-278`, `skills/mission/tests/test_plugins_in_sync.py:629-635`。
  - application runtime guard の選択実行 → `7 passed, 22 deselected`。対象契約は `skills/mission/tests/test_issue510_a5_application.py:102-186`, `skills/mission/tests/test_issue510_a5_application.py:575-589`。
- `test_stop_guard_dedupe.py` は依頼で負荷依存 flaky と明示されているため、実装時も単独実行と suite 内実行の双方を記録する。ただし本調査では単独系 57 件が一度で通った。

## 2. 現行 shell hook の全判断

### 2.1 hook context と session ID の作成

| 処理 | 現行挙動 | 一次証拠 |
|---|---|---|
| 再入防止 | stdin JSON の `stop_hook_active` を `jq` で読み、`true` なら即終了する | `scripts/mission-stop-guard.sh:39-44` |
| CWD 明示 override | `MISSION_HOOK_CWD` があれば最優先し、test 用 PID override も読む | `scripts/mission-stop-guard.sh:91-95` |
| input CWD | input `.cwd` が存在する directory なら祖先 process の CWD より優先する | `scripts/mission-stop-guard.sh:96-115`; `docs/design/426-stop-guard-input-cwd.md:11-22` |
| process fallback | 最大 6 世代の祖先を走査し、`claude` / `codex` の PID と CWD を得る | `scripts/mission-stop-guard.sh:46-89` |
| 最終 CWD | 上記で得られなければ `$PWD` | `scripts/mission-stop-guard.sh:116-118` |
| session directory | `<CWD>/.mission-state/sessions` | `scripts/mission-stop-guard.sh:120` |
| hook SID | `MISSION_SESSION_ID` → `cc-${CLAUDE_CODE_SESSION_ID}` → `cx-${CODEX_THREAD_ID}` → `pid-${AGENT_PID}` の順 | `scripts/mission-stop-guard.sh:214-226` |
| SID sanitize | `/` と `\` を `_`、前後空白と先頭 `.` を除き、空なら `default` | `scripts/mission-stop-guard.sh:122-132` |

このうち CWD/PID の取得は host observation であり、selection の結論ではない。一方、SID precedence、sanitize、`stop_hook_active` の扱いは候補集合と終了可否を変えるため、#615 では Python adapter が facts を作り、application が選択理由を返す経路へ移す。

### 2.2 session 候補の走査と 1 件の選択

現行アルゴリズムは次のとおりである。

1. `sessions/` がなければ何も表示せず終了する（`scripts/mission-stop-guard.sh:232`, `scripts/mission-stop-guard.sh:358-361`）。
2. `HOOK_SID.json` が存在する場合:
   - env SID 由来なら、その 1 ファイルだけを候補にする（`scripts/mission-stop-guard.sh:236-243`）。
   - PID fallback 由来なら、exact file を先頭に置き、その後に `sessions/*.json` を置く。exact の重複は loop 内で除く（`scripts/mission-stop-guard.sh:238-251`）。
3. exact file がなければ `sessions/*.json` を全候補にする。script 内に明示的な `sort` や `.group.json` 除外はない（`scripts/mission-stop-guard.sh:244-247`）。
4. 候補ごとに `_mission_stop_verdict` を呼ぶ。失敗・契約不正は、その時点で fail-closed の block を表示して終了する（`scripts/mission-stop-guard.sh:186-213`, `scripts/mission-stop-guard.sh:247-257`）。
5. `decision` が `block` / `warn` でなければ、`orphan_pid` がある場合だけ `mark-halt` を試み、次候補へ進む（`scripts/mission-stop-guard.sh:258-265`）。
6. 最初の `block` / `warn` 候補を `SESSION_FILE_TO_BLOCK` として選び、以後の候補は見ずに `break` する（`scripts/mission-stop-guard.sh:267-272`）。

Python の個別 verdict は、project root、terminal 状態、owner を次の順に判定する。

| 個別候補の判定 | Python の現行条件 | 一次証拠 |
|---|---|---|
| project 越境 | state の `project_root` と hook CWD が不一致なら `skip` | `skills/mission/bin/mission-state.py:10346-10350` |
| pass | `passes=true` なら `skip` | `skills/mission/bin/mission-state.py:10351-10353` |
| evidence terminal | `halt_category=evidence-submitted` なら `skip` | `skills/mission/bin/mission-state.py:10354-10356` |
| halt | `halt_reason` があれば `skip` | `skills/mission/bin/mission-state.py:10357-10359` |
| inactive | `loop_active=false` なら `skip` | `skills/mission/bin/mission-state.py:10360-10362` |
| active unfinished | 上記以外は `block` | `skills/mission/bin/mission-state.py:10363-10365` |
| exact fenced owner | filename SID が hook SID と同じでも lease owner が違えば `skip` | `skills/mission/bin/mission-state.py:10367-10372` |
| legacy PID fallback | PID 由来 SID かつ exact でない lease-less state は state PID と hook PID を照合 | `skills/mission/bin/mission-state.py:10373-10375` |
| その他の owner 不一致 | `skip` | `skills/mission/bin/mission-state.py:10376-10377` |

したがって、現状は「候補順の決定」と「最初の eligible 候補を採用」が shell、「各候補の eligibility」が Python という二重所有である。

### 2.3 orphan / freshness / awaiting-user / lease の分岐

| 分岐 | 入力・比較 | 現行の結果 | 一次証拠 |
|---|---|---|---|
| orphan 候補 | 個別 verdict が active、hook SID なし、lease なし、PID が正整数で `os.kill(pid, 0)` が失敗 | Python は `orphan_pid` を返して `skip/orphan-pid`、shell は `mark-halt` を試し次候補へ進む | `skills/mission/bin/mission-state.py:10379-10391`; `scripts/mission-stop-guard.sh:258-264` |
| lease timestamp | lease があり、parse できる expiry に対して `expiry > now` | Python が `lease_unexpired=true` | `skills/mission/bin/mission-state.py:10393-10400` |
| freshness source | 最初に存在する `heartbeat_at` → `last_progress_at` → `last_activity_at` → `updated_at`。最初の present 値が不正なら後続へ落とさず age 不明 | Python `freshness` が field と age を返す | `skills/mission/bin/mission-state.py:10471-10482`; `skills/mission/lib/mission_common.py:169-201` |
| stale | `age_sec > _stale_halt_seconds()`。既定 10800 秒、環境値は整数かつ 300 秒以上だけ採用 | Python `freshness.verdict=stale` | `skills/mission/bin/mission-state.py:10274-10289`; `skills/mission/bin/mission-state.py:10482-10493` |
| warn | stale でなく `age_sec > 3600` | Python `freshness.verdict=warn` | `skills/mission/bin/mission-state.py:10487-10493` |
| age 不明 | timestamp なし、不正、未来、非有限 | `fresh` とし auto-halt しない | `skills/mission/lib/mission_common.py:178-200`; `skills/mission/bin/mission-state.py:10482-10485` |
| freshness CLI 失敗 | timeout、非 0、不正 JSON、`ok!=true`、未知 verdict | shell は auto-halt せず通常 block へ戻す | `scripts/mission-stop-guard.sh:159-184`, `scripts/mission-stop-guard.sh:275-282`; `skills/mission/tests/test_stop_hook.py:522-576` |
| stale + unexpired lease | shell が `lease_unexpired` を先に見る | auto-halt を保留し warning 付き block | `scripts/mission-stop-guard.sh:283-288` |
| stale + awaiting user | unexpired lease でなく `awaiting_user=true` | auto-halt を保留し warning 付き block | `scripts/mission-stop-guard.sh:285-290` |
| stale + expired/invalid lease | 上記保留なし、`lease_present=true` | `cleanup-stale --root <CWD> --execute` を発火 | `scripts/mission-stop-guard.sh:291-298` |
| stale + lease なし | 上記保留なし | `mark-halt --reason "stale: auto-halted after <minutes>m idle" --category stale` を発火 | `scripts/mission-stop-guard.sh:291-305` |
| auto-halt 成功 | command 成功 | block を出さず終了 | `scripts/mission-stop-guard.sh:306-312` |
| auto-halt 失敗 | command 非 0、または cleanup 結果に対象 path なし | 固定エラーで block | `scripts/mission-stop-guard.sh:151-157`, `scripts/mission-stop-guard.sh:306-310` |
| warn | Python verdict `warn` | age 分数の warning を前置して block | `scripts/mission-stop-guard.sh:315-317` |
| fresh | Python verdict `fresh` | freshness prefix なしで block | `scripts/mission-stop-guard.sh:318-321` |

注意点として、`awaiting_user` は Python が bool を返すだけで、stale 時に auto-halt を保留する判断と、lease を先に優先する判断は shell にある（`skills/mission/bin/mission-state.py:10441-10444`, `scripts/mission-stop-guard.sh:283-290`）。また `cleanup-stale` 自身は lease expiry と期限後 heartbeat を再検証し、eligible でなければ対象を halt しない。具体的には `now < expires` を unexpired とし、expiry 後の heartbeat があり、その heartbeat age が lease TTL 未満なら cleanup を保留する（`skills/mission/bin/mission-state.py:16743-16763`）。lease TTL は既定 15 分で、正整数の `MISSION_LEASE_TTL_SECONDS` だけを採用する（`skills/mission/bin/mission-state.py:842-843`, `skills/mission/bin/mission-state.py:1110-1116`）。実際の halt / skip は `skills/mission/bin/mission-state.py:16791-16838`、shell 側の対象確認は `scripts/mission-stop-guard.sh:151-157` にある。

### 2.4 発火する mission command と引数

| command | 発火箇所 | 現行引数 | 発火条件 |
|---|---|---|---|
| `stop-verdict` | `_mission_stop_verdict` | `--state-file <sf> --json --cwd <CWD> --planning-warn-iterations <N>`、必要に応じ `--hook-session-id <SID>` / `--hook-pid <PID>` / `--hook-session-id-from-pid` | 各 session 候補 | `scripts/mission-stop-guard.sh:186-207` |
| `freshness` | `_mission_state_freshness` | `--state-file <selected-sf>` | active 候補を 1 件選んだ後 | `scripts/mission-stop-guard.sh:159-183`, `scripts/mission-stop-guard.sh:275-280` |
| `mark-halt` | `_mission_halt_session` | env `MISSION_SESSION_ID=<basename(sf)>`; `mark-halt --reason <reason> --category stale` | orphan、または stale + lease なし | `scripts/mission-stop-guard.sh:134-145`, `scripts/mission-stop-guard.sh:260-263`, `scripts/mission-stop-guard.sh:291-305` |
| `cleanup-stale` | `_mission_cleanup_expired_lease` | `cleanup-stale --root <CWD> --execute` | stale + lease present + unexpired でない + awaiting-user でない | `scripts/mission-stop-guard.sh:147-157`, `scripts/mission-stop-guard.sh:286-298` |
| `stop-guard-observe` | active block 出力前 | `--session-id <SESSION_SID> --digest <PENDING_DIGEST> --now-epoch <epoch> --ttl-seconds <ttl>` | pending digest が非空。最大 3 回試行 | `scripts/mission-stop-guard.sh:327-347` |

`stop-guard-observe` の `now` は環境値または `date +%s`、TTL は環境値または 600、無効値は 600 に戻し、TTL は 1 以上に clamp する。これらの正規化と retry 回数も shell の policy 判断である（`scripts/mission-stop-guard.sh:328-346`）。観測結果の `mode=heartbeat` なら compact text、それ以外は detail text を shell が選ぶ（`scripts/mission-stop-guard.sh:349-355`）。

### 2.5 jq 使用箇所の全分類

| 行 | filter / 用途 | 現行分類 | #615 後 |
|---|---|---|---|
| `34-37` | `command -v jq` と jq 不在時の固定 block | dependency / fail-closed | 維持 |
| `41-44` | input `.stop_hook_active` | host input decode + 終了判断 | raw input を Python へ渡し削除 |
| `102` | input `.cwd` | host input decode | Python adapter へ移し削除 |
| `155-156` | cleanup 結果 `any(.halted[]?; .path == $target)` | command 成否判断 | typed receipt の Python 判定へ移し削除 |
| `179-182` | freshness の `ok` と enum 検証 | Python verdict の契約 decode | `freshness` 独立呼び出し自体を削除 |
| `208-211` | stop verdict schema / decision enum 検証 | Python verdict の契約 decode | GuardDecision decode として維持 |
| `254-255` | `jq -n` で fail-closed JSON を生成 | shell rendering | 固定 JSON、または Python の `shell_text` に置換 |
| `258` | `.decision` | Python verdict decode。その後 shell が候補を選ぶ | 候補選択を Python へ移し削除 |
| `260` | `.orphan_pid` | Python verdict decode。その後 shell が command を選ぶ | typed command args decode のみへ置換 |
| `270-271` | `.lease_present`, `.lease_unexpired` | Python verdict decode。その後 shell が stale action を選ぶ | evidence は shell が読まず削除 |
| `281-282` | freshness `.verdict`, `.age_sec` | Python verdict decode。その後 shell が分数計算・分岐 | 削除 |
| `285` | `.awaiting_user` | Python verdict decode。その後 shell が保留判断 | 削除 |
| `323-326` | `.planning_warning`, `.session_id`, `.pending_digest`, `.display_reason` | Python verdict decode。その後 shell が message / observe args を組成 | typed command と `shell_text` decode へ置換 |
| `349` | observation `.mode` | Python result decode。その後 shell が detail/heartbeat を判断 | command receipt を Python へ戻し削除 |
| `355` | `jq -n` で最終 hook JSON を生成 | shell rendering | Python serializer の `shell_text` をそのまま表示 |

既存 T11 は、shell が authoritative field を session file から jq で読む経路を、改行 filter、`cat | jq`、filter 変数の合成違反でも検出する（`skills/mission/tests/test_issue512_r1_authoritative_reader.py:1064-1149`, `skills/mission/tests/test_issue512_r1_authoritative_reader.py:1152-1184`）。#615 はこのテストを維持し、さらに「jq の入力元は Python が返した GuardDecision だけ」という allowlist を追加する。

## 3. 現行 `cmd_stop_verdict` の全 field

### 3.1 success JSON

`cmd_stop_verdict` は typed object ではなく、その場で dict を組み立てて `json.dumps` している（`skills/mission/bin/mission-state.py:10340-10445`）。success path の field は次の 12 個である。

| field | 型 / 値 | 現行の生成元 | 一次証拠 |
|---|---|---|---|
| `schema` | string、固定 `mission-stop-verdict/1` | serializer 内 literal | `skills/mission/bin/mission-state.py:10432-10434` |
| `decision` | string、実生成は `block` / `skip` | terminal / owner / orphan 判定 | `skills/mission/bin/mission-state.py:10348-10391`, `skills/mission/bin/mission-state.py:10434` |
| `reason` | string | `project-root-mismatch`, `passes-true`, `evidence-submitted`, `halt-reason`, `inactive`, `active-unfinished`, `lease-owner-mismatch`, `pid-owner-mismatch`, `session-owner-mismatch`, `orphan-pid` | `skills/mission/bin/mission-state.py:10348-10391`, `skills/mission/bin/mission-state.py:10435` |
| `outcome_kind` | string | `completed-pass`, `halted`, `completed-evidence`、その他 `expected-gate` | `skills/mission/bin/mission-state.py:10427-10436` |
| `display_reason` | string | session label、未達一覧、iteration、last score、threshold、mission 先頭 200 字 | `skills/mission/bin/mission-state.py:10405-10426`, `skills/mission/bin/mission-state.py:10437` |
| `planning_warning` | string | score history 空かつ `iteration >= planning_warn_iterations` | `skills/mission/bin/mission-state.py:10408-10414`, `skills/mission/bin/mission-state.py:10438` |
| `session_id` | string | snapshot session ID、なければ filename stem | `skills/mission/bin/mission-state.py:10405-10407`, `skills/mission/bin/mission-state.py:10439` |
| `pending_digest` | string |同 root の active unfinished session facts の canonical JSONL SHA-256 | `skills/mission/bin/mission-state.py:10292-10337`, `skills/mission/bin/mission-state.py:10440` |
| `lease_present` | bool | typed lease の有無 | `skills/mission/bin/mission-state.py:10367`, `skills/mission/bin/mission-state.py:10441` |
| `lease_unexpired` | bool | parse 済み expiry と現在 UTC の `>` 比較 | `skills/mission/bin/mission-state.py:10393-10400`, `skills/mission/bin/mission-state.py:10442` |
| `awaiting_user` | bool | authoritative snapshot | `skills/mission/bin/mission-state.py:10443`; `skills/mission/lib/mission_persistence/authoritative_reader.py:320-323` |
| `orphan_pid` | int または null | env-less lease-less dead PID 判定 | `skills/mission/bin/mission-state.py:10379-10391`, `skills/mission/bin/mission-state.py:10444` |

shell の schema validator は `warn` も許可するが、現行 `cmd_stop_verdict` 内に `decision="warn"` を代入する分岐はない（`scripts/mission-stop-guard.sh:208-211`, `skills/mission/bin/mission-state.py:10348-10391`）。

### 3.2 error JSON

例外時は stderr に次の 4 field を出し、exit 2 になる（`skills/mission/bin/mission-state.py:10446-10453`）。

| field | 値 |
|---|---|
| `schema` | `mission-stop-verdict/1` |
| `decision` | `block` |
| `reason` | `authoritative-state-unreadable` |
| `error` | 例外文字列 |

hook は non-zero を受けると error JSON 自体を使わず、対象 path を含む固定 block を生成する（`scripts/mission-stop-guard.sh:253-256`）。

### 3.3 Python / shell の責務対応表

| hook が必要とする判断 | Python に既にある | shell にしかない / shell が最終決定 | #615 の移管先 |
|---|---|---|---|
| session ID precedence / sanitize | `mission-state.py` 側にも同等 resolver はあるが、hook は shell 実装を使う | env precedence、sanitize、候補順、最初の block/warn 選択 | Guard runtime facts + `select_guard_session()` |
| candidate eligibility | project root、terminal、owner、legacy PID | 候補 loop と early break | `select_guard_session()` |
| orphan | dead PID の検出、`orphan_pid` | `mark-halt` を選び、失敗を無視して次候補へ進む | `OrphanFinding` + `MarkHaltCommand` + receipt resolver |
| freshness | timestamp source、age、warn/stale threshold | freshness CLI の呼出成否、warning 文、minutes 計算、auto-halt 可否 | `FreshnessEvidence` + `StaleFinding` |
| awaiting user | authoritative bool | stale auto-halt 保留。unexpired lease を先に優先 | `AwaitingUserFinding` と明示 priority |
| lease | typed lease、expiry の `>` 比較 | stale 時の保留 / cleanup 選択、cleanup 結果の対象 path 検証 | `LeaseEvidence` + `LeaseExpiredFinding` + receipt resolver |
| planning warning | warning text の生成 | env threshold の parse / clamp | `GuardPolicy.planning_warn_iterations` と application rendering |
| 未達 digest | Python が全候補を集計 | observe の now / TTL / retry、mode による表示選択 | `StopGuardObserveCommand` + receipt resolver |
| 最終 text | detail / planning text は Python | stale prefix、heartbeat/detail、JSON 組成 | `HookReply` + CLI serializer `shell_text` |
| 次 command | なし | `mark-halt` / `cleanup-stale` / `stop-guard-observe` / none の選択 | closed `GuardCommand` union |

## 4. 既存テストが固定している契約

| テスト群 | 固定済みの主な契約 | 一次証拠 |
|---|---|---|
| `test_stop_hook.py` owner | own session block、foreign/terminal skip、Codex prefix、SID sanitize、PID fallback、exact fenced state 優先、legacy fallback | `skills/mission/tests/test_stop_hook.py:41-68`, `skills/mission/tests/test_stop_hook.py:107-250` |
| `test_stop_hook.py` orphan/lease | env-less orphan halt、lease があれば dead diagnostic PID を無視、unexpired lease 保護、expired lease は janitor cleanup | `skills/mission/tests/test_stop_hook.py:253-348` |
| `test_stop_hook.py` freshness | timestamp 4 段 chain、warn、stale auto-halt、不正 timestamp fail-safe、Python freshness failure fail-safe、awaiting-user 保護、custom halt threshold | `skills/mission/tests/test_stop_hook.py:350-627`, `skills/mission/tests/test_stop_hook.py:650-701` |
| `test_stop_hook.py` planning | planning warning の正負例、0 の default clamp | `skills/mission/tests/test_stop_hook.py:704-770` |
| `test_stop_guard_dedupe.py` | digest 変化 / TTL で detail、同一 digest で heartbeat、counter、unsafe sidecar fail-safe、並行更新 | `skills/mission/tests/test_stop_guard_dedupe.py:58-133`, `skills/mission/tests/test_stop_guard_dedupe.py:135-259` |
| `test_issue377_parallel_mission.py` | 未完了だけ block、session / issue_ref / 他未達一覧を表示 | `skills/mission/tests/test_issue377_parallel_mission.py:220-264`, `skills/mission/tests/test_issue377_parallel_mission.py:272-389` |
| R1 T1/T2/T10 | v4/v5 の active block 等価、terminal non-block、unreadable state fail-closed | `skills/mission/tests/test_issue512_r1_authoritative_reader.py:656-724`, `skills/mission/tests/test_issue512_r1_authoritative_reader.py:977-984` |
| R1 T11 | shell jq による authoritative state file 解釈を禁止し、合成違反 3 種で検出力を確認 | `skills/mission/tests/test_issue512_r1_authoritative_reader.py:1064-1184` |
| jq 不在 | 固定 `expected-gate` block JSON | `skills/mission/tests/test_issue512_r1_authoritative_reader.py:1749-1759` |
| application purity | `runtime_guard.py` が filesystem/process I/O module を import しない | `skills/mission/tests/test_issue510_a5_application.py:575-589` |
| closed A5 ownership | `stop-guard-observe` が runtime guard application の command owner | `skills/mission/tests/test_issue510_a5_application.py:102-108` |
| distribution | canonical / plugin hook byte 一致、recursive Python lib mirror、Python 3.9 compatibility | `skills/mission/tests/test_plugins_in_sync.py:268-278`, `skills/mission/tests/test_plugins_in_sync.py:629-635` |
| shell CI | canonical と plugin hook の shellcheck が必須 | `skills/mission/tests/test_actions_cost_guard.py:62-71` |

これらは置換対象ではなく、#615 の Green 条件である。特に R1 T11 は削除・緩和しない。

## 5. 提案する application 型

### 5.1 配置

`GuardDecision` と純粋な decision function は、既存の `StopObservationRequest` / `observe_stop_guard` と同じ `skills/mission/lib/mission_application/runtime_guard.py` に置く。stop observation は既に application use case として同 module にあり（`skills/mission/lib/mission_application/runtime_guard.py:91-220`）、同 module は I/O 非依存を静的に固定されている（`skills/mission/tests/test_issue510_a5_application.py:575-589`）。

I/O は `cmd_stop_verdict` adapter が担当する。authoritative state は既存の `AuthoritativeSnapshot` を使い、生 dict を application へ渡さない。同 snapshot は session、lease、PID、4 timestamp、awaiting-user、project root を typed field として既に持つ（`skills/mission/lib/mission_persistence/authoritative_reader.py:52-88`）。

### 5.2 型定義（実装用擬似コード）

Python 3.9 compatibility gate が recursive library 全体にあるため、実装では `Enum`、`dataclass(frozen=True)`、`Optional`、`Union` を使う（`skills/mission/tests/test_plugins_in_sync.py:629-635`）。

```python
class GuardFindingKind(str, Enum):
    NONE = "none"
    STALE = "stale"
    AWAITING_USER = "awaiting-user"
    LEASE_EXPIRED = "lease-expired"
    ORPHAN = "orphan"
    INDETERMINATE = "indeterminate"  # unreadable / invalid timestamp / command failure


class SessionSelectionReason(str, Enum):
    NONE = "none"
    EXACT_SESSION_ID = "exact-session-id"
    EXACT_PID_FENCED = "exact-pid-fenced"
    LEGACY_PID_FALLBACK = "legacy-pid-fallback"
    ENVLESS_FIRST_ELIGIBLE = "envless-first-eligible"
    NO_ELIGIBLE_SESSION = "no-eligible-session"
    AUTHORITATIVE_STATE_UNREADABLE = "authoritative-state-unreadable"


class LeaseStatus(str, Enum):
    ABSENT = "absent"
    UNEXPIRED = "unexpired"
    EXPIRED = "expired"
    INVALID = "invalid"


class GuardCommandKind(str, Enum):
    NONE = "none"
    MARK_HALT = "mark-halt"
    CLEANUP_STALE = "cleanup-stale"
    STOP_GUARD_OBSERVE = "stop-guard-observe"


class GuardHaltCategory(str, Enum):
    STALE = "stale"


@dataclass(frozen=True)
class SessionSelection:
    state_file: Optional[str]
    session_id: Optional[str]
    reason: SessionSelectionReason
    considered_state_files: Tuple[str, ...]


@dataclass(frozen=True)
class FreshnessEvidence:
    timestamp_field: Optional[str]
    timestamp_value: Optional[str]
    observed_at: str
    age_sec: Optional[int]
    warn_after_sec: int
    halt_after_sec: int


@dataclass(frozen=True)
class LeaseEvidence:
    status: LeaseStatus
    expires_at: Optional[str]
    observed_at: str


@dataclass(frozen=True)
class OrphanEvidence:
    pid: Optional[int]
    pid_alive: Optional[bool]
    check_applicable: bool


@dataclass(frozen=True)
class GuardEvidence:
    freshness: Optional[FreshnessEvidence]
    awaiting_user: bool
    lease: LeaseEvidence
    orphan: OrphanEvidence
    planning_warn_iterations: int
    pending_digest: str


@dataclass(frozen=True)
class NoCommand:
    kind: GuardCommandKind = GuardCommandKind.NONE


@dataclass(frozen=True)
class MarkHaltCommand:
    cwd: str
    session_id: str
    reason: str
    category: GuardHaltCategory
    origin: GuardFindingKind   # STALE または ORPHAN
    kind: GuardCommandKind = GuardCommandKind.MARK_HALT


@dataclass(frozen=True)
class CleanupStaleExecuteCommand:
    root: str
    expected_state_file: str   # halted[].path の照合も Python が行う
    execute: bool              # validator で True のみ
    kind: GuardCommandKind = GuardCommandKind.CLEANUP_STALE


@dataclass(frozen=True)
class StopGuardObserveCommand:
    session_id: str
    digest: str
    now_epoch: int
    ttl_seconds: int
    attempt: int
    max_attempts: int
    kind: GuardCommandKind = GuardCommandKind.STOP_GUARD_OBSERVE


GuardCommand = Union[
    NoCommand,
    MarkHaltCommand,
    CleanupStaleExecuteCommand,
    StopGuardObserveCommand,
]


@dataclass(frozen=True)
class GuardContinuation:
    project_root: str
    hook_session_id: Optional[str]
    hook_session_id_source: str
    hook_pid: Optional[int]
    processed_orphan_state_files: Tuple[str, ...]


@dataclass(frozen=True)
class HookReply:
    emit: bool
    decision: str              # emit=True では現契約の "block"
    reason: str                # 完全に整形済み。shell は連結しない
    outcome_kind: str


@dataclass(frozen=True)
class GuardDecision:
    decision_id: str
    host_decision: str         # 現行 block / skip / warn の互換 projection
    reason_code: str
    outcome_kind: str
    selection: SessionSelection
    finding: GuardFindingKind
    evidence: GuardEvidence
    command: GuardCommand
    continuation: GuardContinuation
    reply: HookReply
    display_reason: str
    planning_warning: str
    session_id: str
    pending_digest: str
```

`GuardDecision` は shell が必要とする値を欠損させない。特に:

- session file、session ID、選択理由は `SessionSelection` に閉じる。
- timestamp 入力、選択 field、現在時刻、age、warn/halt threshold は `FreshnessEvidence` に同居させる。shell に分数計算も threshold 比較もさせない。
- lease は `present/unexpired` という相関 bool 2 個ではなく、`LeaseStatus` 1 個に正規化する。invalid expiry を expired と偽らない。
- command は arbitrary `argv: list[str]` や command string にしない。各 variant が必要な引数を typed field として持ち、shell の hard-coded dispatch が固定 flag と組み合わせる。
- orphan command の後に残候補を走査するための cursor は `GuardContinuation.processed_orphan_state_files` に持つ。command が失敗して state が active のままでも、同一 invocation で同じ orphan を無限に再選択しない。
- 表示は `HookReply.reason` まで application が確定する。CLI serializer が `reply` を JSON 1 行にした `shell_text` を verdict JSON に含める。`emit=false` は `shell_text=""` とし、shell は無条件に `printf '%s' "$SHELL_TEXT"` するだけにする。

### 5.3 入力 facts と I/O 境界

application function は次の 2 経路に分ける。

```python
decide_stop_guard(request: GuardRequest) -> GuardDecision
resolve_guard_command_receipt(
    prior: GuardDecision,
    receipt: GuardCommandReceipt,
) -> GuardDecision
```

`GuardRequest` は raw file path を application 自身が開く形にしない。adapter が次を observation として渡す。

- raw hook input から得た `stop_hook_active` と CWD candidate。
- env SID の raw 値と agent PID/CWD observation。
- `sessions/*.json` の path 順と、各 path の `AuthoritativeSnapshot` または typed read error。
- PID alive observation。
- UTC now、stale / planning / observation policy の正規化済みではない raw setting。

policy の default、型検証、clamp、timestamp 比較、候補順の組み替えは application が行う。filesystem/process/clock の観測だけを adapter に残す。この分離は application module の I/O 禁止契約を維持する（`skills/mission/tests/test_issue510_a5_application.py:575-589`）。

### 5.4 decision priority と command 対応

同じ入力に対する現行結果を維持する priority は次のとおりとする。

| 優先 | finding / 条件 | command | 最終 reply |
|---:|---|---|---|
| 1 | hook reentry、sessions 不在、terminal、owner 不一致、eligible なし | `none` | emit なし |
| 2 | authoritative candidate 読取不能 | `none` | fail-closed block |
| 3 | env-less + lease-less dead PID orphan | `mark-halt` | 成否にかかわらず emit なしで当該候補を終了し、現行どおり次候補判定を継続 |
| 4 | selected active + freshness 判定不能 | `stop-guard-observe` | detail block。auto-halt しない |
| 5 | stale + lease unexpired | `stop-guard-observe` | lease 保護 warning 付き block |
| 6 | stale + lease unexpired でない + awaiting-user | `stop-guard-observe` | awaiting-user warning 付き block |
| 7 | stale + lease expired | `cleanup-stale`（`execute=True`） | 対象 halt を receipt で確認できれば emit なし、未確認なら固定 block |
| 8 | stale + lease invalid | `cleanup-stale`（`execute=True`） | 現行どおり janitor に再検証させ、対象 halt がなければ固定 block |
| 9 | stale + lease absent | `mark-halt` | 成功なら emit なし、失敗なら固定 block |
| 10 | warn / fresh | `stop-guard-observe` | warning/detail または通常 detail/heartbeat block |

lease 保護を awaiting-user より先に評価するのは現行順序の保存である（`scripts/mission-stop-guard.sh:283-290`）。lease invalid でも現行 shell は `lease_present=true` / `lease_unexpired=false` として cleanup を試すため、この失敗経路も変えない（`skills/mission/bin/mission-state.py:10393-10400`, `scripts/mission-stop-guard.sh:291-307`）。

### 5.5 command receipt による shell 再判断の排除

`stop-guard-observe` の mode と retry、`cleanup-stale` の対象 path、`mark-halt` の失敗処理は、初回 decision だけでは確定できない。shell に判断を残さないため、shell は command の stdout と exit status を `GuardCommandReceipt` として次の `stop-verdict` 呼び出しへ返す。

```python
@dataclass(frozen=True)
class GuardCommandReceipt:
    decision_id: str
    kind: GuardCommandKind
    exit_code: int
    stdout: str
```

shell は prior GuardDecision JSON と command stdout を別々の read-only inherited file descriptor で次の Python process へ渡し、command kind と exit status だけを scalar 引数にする。Python adapter は prior JSON を typed decode し、`decision_id` と command kind の一致を検証してから `resolve_guard_command_receipt` を呼ぶ。恒久ファイル、session state、argv、環境変数には prior JSON や command stdout を保存しない。これは shell に判定を増やさない transport であり、現在も command stdout を shell variable に捕捉している範囲を越えない（`scripts/mission-stop-guard.sh:151-157`, `scripts/mission-stop-guard.sh:336-347`）。

receipt resolver の契約は次のとおりである。

- orphan `mark-halt`: 現行の `|| true` と同じく failure でも当該 orphan を block 理由にしない（`scripts/mission-stop-guard.sh:260-264`）。当該 path を `processed_orphan_state_files` に加え、adapter が再収集した候補 facts から残候補の selection を application 内で続ける。
- stale `mark-halt`: exit 0 なら無出力、非 0 なら現行固定 error block（`scripts/mission-stop-guard.sh:300-312`）。
- cleanup: JSON を typed decode し、`halted[].path == expected_state_file` を Python で確認する。未確認は error block（`scripts/mission-stop-guard.sh:151-157`, `scripts/mission-stop-guard.sh:306-310`）。
- observe: success JSON の `mode` を typed decode し、detail/heartbeat の `HookReply` を選ぶ。failure は `attempt < max_attempts` なら同じ typed command を再発行し、上限後は現行どおり detail に fail-safe する（`scripts/mission-stop-guard.sh:333-354`）。

これにより shell の loop は「decision を得る → closed command を 1 回 dispatch → receipt を返す」の機械的反復だけになる。

### 5.6 JSON compatibility

外部契約の不必要な破壊を避けるため、`stop-verdict --state-file ... --json` の top-level `schema` は `mission-stop-verdict/1` を維持し、現行 12 field を additive projection として残す。既存テストは schema と terminal `decision/reason/outcome_kind` を固定している（`skills/mission/tests/test_issue512_r1_authoritative_reader.py:694-724`）。

追加 field は `decision_id`, `selection`, `finding`, `evidence`, `command`, `continuation`, `reply`, `shell_text` とする。既存 direct caller 用の `--state-file` 単一候補 mode は、読取例外時の exit 2 と現行 error 4 field も維持する。hook が使う新しい `--hook-input -` mode では、候補の読取不能を `INDETERMINATE` の typed fail-closed `GuardDecision` にする。CLI 自体が起動不能・JSON を生成不能な場合だけ shell の固定 fallback を使う。

## 6. shell adapter の目標形

擬似コードは次の形に限定する。実装時は shellcheck と既存 no-jq fail-closed 契約に合わせて詳細を調整する。

```bash
INPUT=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' "$FIXED_JQ_MISSING_BLOCK"
  exit 0
fi

PRIOR_DECISION=""
RECEIPT_KIND=""
RECEIPT_STATUS=""
RECEIPT_STDOUT=""

while :; do
  GUARD_DECISION=$(printf '%s' "$INPUT" | _mission_stop_verdict) || {
    printf '%s\n' "$FIXED_VERDICT_UNAVAILABLE_BLOCK"
    exit 0
  }

  COMMAND_KIND=$(printf '%s' "$GUARD_DECISION" | jq -r '.command.kind')

  case "$COMMAND_KIND" in
    none)
      SHELL_TEXT=$(printf '%s' "$GUARD_DECISION" | jq -r '.shell_text')
      printf '%s' "$SHELL_TEXT"
      exit 0
      ;;
    mark-halt)
      # typed fields を decodeし、固定 subcommand / flags で発火
      ;;
    cleanup-stale)
      # root / expected path を decodeし、cleanup-stale --root ... --execute
      ;;
    stop-guard-observe)
      # typed fields を decodeし、固定 subcommand / flags で発火
      ;;
    *)
      printf '%s\n' "$FIXED_UNKNOWN_COMMAND_BLOCK"
      exit 0
      ;;
  esac

  # prior decision と command stdout は別々の read-only fd で次の Python 呼び出しへ運ぶ。
done
```

`case` は command を「選ぶ」policy ではなく、Python が選んだ closed variant の dispatch である。未知 kind は実行せず fail-closed にする。JSON 由来の arbitrary argv を `eval` / `bash -c` / `sh -c` で実行してはならない。

## 7. shell guard テスト設計

### 7.1 analyzer

新規 `skills/mission/tests/test_issue615_guard_decision.py` に、source string を受け取る純粋関数 `analyze_guard_shell(source) -> list[Violation]` を置く。既存 `_shell_commands` の quote/comment を考慮した分割方法を再利用できる（`skills/mission/tests/test_issue512_r1_authoritative_reader.py:1064-1096`）。外部 parser dependency は追加しない。

analyzer は少なくとも次を検出する。

1. **policy 数値 / arithmetic**
   - `$((...))` を禁止。
   - `test` / `[` / `[[` の `-lt/-le/-gt/-ge` を禁止。
   - `STALE|FRESH|AGE|LEASE|TTL|ITER|ATTEMPT|EPOCH|TIMESTAMP` を含む変数への decimal literal default/代入を禁止。
   - `date +%s`、ISO timestamp parse/compare を禁止。
   - `exit 0`、fd の `2>/dev/null`、adapter timeout の固定 invocation は policy literal と区別する。allowlist は exact token sequence で限定する。
2. **command selection**
   - command literal `mark-halt`, `cleanup-stale`, `stop-guard-observe` は marker で囲んだ単一 dispatch block 内だけ許す。
   - 許す branch は `case "$COMMAND_KIND"` の 4 label と unknown fail-closed arm だけ。
   - `eval`, `bash -c`, `sh -c`, JSON の `argv` 配列実行、command 名を変数展開して実行する形を禁止。
3. **timestamp / state judgment field**
   - `age_sec`, `timestamp_field`, `awaiting_user`, `lease_*`, `orphan_pid`, `freshness.verdict` を shell 条件式で参照することを禁止。
   - これらを display のために読む必要もない設計なので、hook script での出現自体を禁止できる。
4. **jq input と mode**
   - `jq -n` を禁止。
   - jq の入力は `GUARD_DECISION` のみ許可する。
   - filter は `command.kind`、variant 固有の command args、`shell_text`、`decision_id` の decode allowlist に限定する。
   - 既存 T11 の session file authoritative read guard はそのまま維持する（`skills/mission/tests/test_issue512_r1_authoritative_reader.py:1159-1184`）。

### 7.2 closed command list の固定

次の 3 面を相互照合する。

1. application `GuardCommandKind` の列挙値。
2. test 内の explicit expected set `{none, mark-halt, cleanup-stale, stop-guard-observe}`。
3. shell dispatch block から抽出した `case` label と、各 arm 内の literal CLI subcommand / fixed flag。

テストは集合一致だけでなく、次の exact invocation を固定する。

| kind | shell が発火できる形 |
|---|---|
| `none` | command 発火なし |
| `mark-halt` | `MISSION_SESSION_ID=<typed sid> python3 <mission-state> mark-halt --reason <typed reason> --category stale` |
| `cleanup-stale` | `python3 <mission-state> cleanup-stale --root <typed root> --execute` |
| `stop-guard-observe` | `python3 <mission-state> stop-guard-observe --session-id <typed sid> --digest <typed digest> --now-epoch <typed now> --ttl-seconds <typed ttl>` |

canonical command owner に `stop-guard-observe` が既に含まれることも維持する（`skills/mission/lib/mission_application/runtime_guard.py:22-25`, `skills/mission/tests/test_issue510_a5_application.py:102-108`）。

### 7.3 合成違反 fixture による検出力の実証

同じテストファイルに `_SYNTHETIC_GUARD_VIOLATIONS` を置き、各 snippet を `analyze_guard_shell` へ直接渡す。実行可能ファイルは増やさない。

| fixture ID | 合成違反 | 期待 violation |
|---|---|---|
| `numeric-stale-default` | `STALE_SECONDS=${X:-10800}` | `policy-numeric-literal` |
| `numeric-age-compare` | `[ "$AGE_SEC" -gt 3600 ]` | `numeric-policy-comparison` |
| `arithmetic-minutes` | `MINS=$((AGE_SEC / 60))` | `shell-arithmetic` |
| `timestamp-compare` | `[[ "$LEASE_EXPIRES_AT" > "$NOW" ]]` | `timestamp-comparison` |
| `date-epoch` | `NOW=$(date +%s)` | `timestamp-calculation` |
| `branch-selects-command` | `if ...; then python3 ... mark-halt; fi` | `command-outside-dispatch` |
| `unexpected-command` | dispatch arm から `resume` を発火 | `command-not-allowlisted` |
| `dynamic-command` | `eval "$COMMAND"` または `"$COMMAND"` 実行 | `dynamic-command-execution` |
| `jq-state-file` | `jq -r '.updated_at' "$sf"` | 既存 `authoritative-jq-read` |
| `jq-input` | `printf '%s' "$INPUT" \| jq -r '.cwd'` | `jq-input-not-guard-decision` |
| `jq-construction` | `jq -n '{decision:"block"}'` | `jq-construction` |
| `missing-arm` | allowlist から 1 arm を除く | `dispatch-set-mismatch` |

positive fixture として目標 shell の最小 dispatch source を渡し、violation が 0 であることも確認する。各 negative fixture は期待 code が 1 件以上出ることを個別に assert し、analyzer 自身が空実装でも通る形を避ける。この方法は R1 T11 が合成違反 3 種を自己テストしている先例と同じである（`skills/mission/tests/test_issue512_r1_authoritative_reader.py:1174-1184`）。

## 8. TDD テストリスト

実装は次の順で Red → Green にする。

1. **型と closed enum** — `GuardFindingKind`, `SessionSelectionReason`, `LeaseStatus`, `GuardCommandKind` と 4 command variant が exact set である。
2. **session selection table** — exact env SID、exact PID fenced、foreign lease owner、terminal exact から legacy PID fallback、valid exact の legacy より優先、eligible なしを application unit test で固定する。現行期待は `skills/mission/tests/test_stop_hook.py:137-237`。
3. **selection reason / evidence** — 選択 file、SID、reason、considered path 順が返り、unreadable candidate は fail-closed decision になる。
4. **freshness parity** — 4 timestamp の優先順、first-present invalid の short-circuit、future timestamp、`age == 3600`、`age > 3600`、`age == halt threshold`、`age > halt threshold` を固定する。現行比較は strict `>`（`skills/mission/bin/mission-state.py:10487-10493`）。
5. **policy parse parity** — `MISSION_STALE_HALT_SECONDS` の default / invalid / negative / `<300`、planning warn threshold の invalid / `<1`、observe TTL の invalid / `<1` を Python で固定する。現行は `skills/mission/bin/mission-state.py:10274-10289`, `scripts/mission-stop-guard.sh:228-231`, `scripts/mission-stop-guard.sh:328-332`。
6. **stale priority matrix** — unexpired lease > awaiting-user > expired/invalid lease cleanup > lease-less mark-halt の順と、全 evidence field を固定する。
7. **orphan matrix** — env SID あり、lease あり、PID なし/0/alive/dead の組合せで、現行の env-less lease-less dead PID だけが orphan command になることを固定する（`skills/mission/bin/mission-state.py:10379-10391`）。
8. **command args** — mark-halt の SID/reason/category、cleanup の root/execute/expected path、observe の SID/digest/now/TTL/attempt が完全であり、arbitrary argv が存在しない。
9. **receipt: mark-halt** — stale success/failure、orphan failure の現行差を固定する。
10. **receipt: cleanup** — target が `halted[]` にある success、他 path のみ、`skipped[]`、invalid JSON、non-zero を固定する。
11. **receipt: observe** — detail/heartbeat、invalid JSON/non-zero の retry、3 回後 detail fallback、decision ID / kind mismatch reject を固定する。
12. **serializer compatibility** — `mission-stop-verdict/1` と現行 12 field、error 4 field projection、terminal reason/outcome kind を固定する。
13. **real shell E2E** — 現行 `test_stop_hook.py`, `test_stop_guard_dedupe.py`, `test_issue377_parallel_mission.py`, R1 T1/T2/T10 をそのまま Green にする。
14. **judgment-free static guard** — canonical/plugin hook を analyzer に通し、合成違反 fixture 全件が期待 violation で落ちる。
15. **closed dispatch / jq decode-only** — enum、expected set、case labels、literal invocation を一致させ、既存 R1 T11 を維持する。
16. **distribution / compatibility** — plugin mirror byte 一致、Python 3.9 import、shellcheck、full suite を通す（`skills/mission/tests/test_plugins_in_sync.py:268-278`, `skills/mission/tests/test_plugins_in_sync.py:629-635`, `skills/mission/tests/test_actions_cost_guard.py:62-71`）。

## 9. 受け入れ条件

- [ ] application 層に immutable typed `GuardDecision` と closed command union がある。
- [ ] hook の root 全体を対象にした `stop-verdict` が session selection と選択理由を返す。
- [ ] decision evidence に、採用 timestamp/value、observed time、age、warn/halt threshold、awaiting-user、lease status/expiry、orphan PID/alive observationが入る。
- [ ] finding が最低限 `none / stale / awaiting-user / lease-expired / orphan` を区別する。読取不能・command failure は推測せず `indeterminate` とする。
- [ ] `mark-halt / cleanup-stale --execute / stop-guard-observe / none` 以外を shell が発火できない。
- [ ] command variant は必要引数をすべて持ち、shell に reason、minutes、root、SID、now、TTL の計算・補完がない。
- [ ] command receipt の成否、cleanup target、observe retry/mode の判断が Python にある。
- [ ] shell に freshness/lease/orphan/awaiting-user/planning/dedupe の条件分岐、timestamp 比較、policy arithmetic がない。
- [ ] jq は GuardDecision field の decode に限定され、`jq -n` と raw hook/session/command result の解釈がない。
- [ ] freshness の timestamp priority と strict boundary、stale/awaiting/lease/orphan の結果が現行と同じである。
- [ ] existing `mission-stop-verdict/1` projection と terminal outcome contract が維持される。
- [ ] 合成違反 fixture が numeric threshold、timestamp comparison、branch command selection、unexpected/dynamic command、jq misuse、missing arm をそれぞれ検出する。
- [ ] 既存 hook / dedupe / parallel / R1 tests、new #615 tests、shellcheck、plugin sync、Python 3.9 gate、full suite が Green である。

## 10. 変更対象ファイル

| ファイル | 変更内容 |
|---|---|
| `skills/mission/lib/mission_application/runtime_guard.py` | `GuardDecision`、facts/evidence、closed command union、selection / decision / receipt resolver |
| `skills/mission/bin/mission-state.py` | `stop-verdict` adapter を root-aware 化、authoritative facts / process observation、typed serializer、`--hook-input -` と receipt binding |
| `scripts/mission-stop-guard.sh` | raw input relay + GuardDecision decode + closed dispatch のみに縮小 |
| `skills/mission/tests/test_issue615_guard_decision.py` | application unit、serializer、static analyzer、合成違反 fixture、closed command contract |
| `skills/mission/tests/test_stop_hook.py` | 既存 behavioral tests は原則変更せず、新 schema の additive field を必要最小限確認 |
| `skills/mission/tests/test_stop_guard_dedupe.py` | 既存 test は変更せず Green を確認。必要なら receipt path の assertion だけ追加 |
| `skills/mission/tests/test_issue512_r1_authoritative_reader.py` | T11 は維持。jq input allowlist の補強を #615 test に置くか、重複しない範囲でここへ追加 |
| `plugins/mission/skills/mission/lib/mission_application/runtime_guard.py` | canonical の byte-identical mirror |
| `plugins/mission/skills/mission/bin/mission-state.py` | canonical の byte-identical mirror |
| `plugins/mission/scripts/mission-stop-guard.sh` | canonical の byte-identical mirror |
| `skills/mission/refs/state-management.md` | shell が decision dispatch のみであること、closed command list、failure semantics を更新 |
| `plugins/mission/skills/mission/refs/state-management.md` | canonical の byte-identical mirror |

runtime guard を別 module に分割する案もあるが、現行 stop observation と同じ bounded context であり、まず同 module に置く方が単純である（`skills/mission/lib/mission_application/runtime_guard.py:1-27`, `skills/mission/lib/mission_application/runtime_guard.py:91-220`）。将来 module が大きくなった場合でも型と use-case signature を保ったまま `guard_decision.py` へ移せるため、出口はある。

## 11. 段階分割の提案

Issue 単位 PR は保ったまま、1 PR 内を次の Red/Green の論理 commit に分ける。

### 段 1: Red — type / decision table / static detector

- #615 test file に型、selection、freshness、command、receipt、synthetic fixture の Red を追加する。
- 既存 T11 と hook behavioral tests は変更しない。
- この段では production code を変えない。

### 段 2: Green — application authority

- `runtime_guard.py` に types と pure decision / receipt resolver を実装する。
- adapter はまだ旧 shell に接続せず、unit tests だけ Green にする。
- application purity と Python 3.9 gate を通す。

### 段 3: Green — `stop-verdict` root mode / compatibility serializer

- authoritative snapshots と runtime observation を facts に変換する。
- `--state-file` 互換を保ちつつ `--hook-input -` を追加する。
- 現行 12 field / error projection、v4/v5、unreadable fail-closed を Green にする。

### 段 4: Green — shell thin dispatch

- hook を closed dispatch へ置換する。
- receipt loop を接続し、既存 hook / dedupe / parallel tests を Green にする。
- static analyzer の positive source を Green にし、negative fixtures の検出力を再確認する。

### 段 5: Refactor / distribution

- duplicate rendering / legacy helper を除く。
- plugin 3 ファイルと state-management docs を同期する。
- shellcheck、対象 test、full suite、artifact/vendor hygiene を実行する。

段 2 と段 3 を別 PR にすると #615 の完了条件を途中で満たさない半端な interface を main に置くため、PR は分けない。commit は分けて review の焦点を保つ。

## 12. 却下する代替案

### shell に command string / argv を渡してそのまま実行する

closed list を型と dispatch の双方で固定できず、`eval` 相当の open execution surface になるため却下する。ADR-006 は許可 command の閉包を要求している（`docs/adr/006-kernel-reducer-adjudication.md:60-72`）。

### `stop-verdict` 自身が mutation command を実行する

shell に発火を残すという ADR-006 と Issue #615 の非対象範囲に反するため却下する（`docs/adr/006-kernel-reducer-adjudication.md:65-78`）。

### freshness だけ Python に残し、awaiting / lease / observe mode は shell に残す

現在の分裂を部分的に温存し、GuardDecision が hook の全判断を包含しない。現行 shell がこれらを判断している一次証拠は `scripts/mission-stop-guard.sh:275-321`, `scripts/mission-stop-guard.sh:327-355` にあり、ADR-006 は全移管を要求している（`docs/adr/006-kernel-reducer-adjudication.md:62-72`）。

### observe mode だけ shell が分岐する

heartbeat/detail の選択は TTL と digest に基づく policy であり、単なる rendering ではない。現行 application use case が mode を決めていることも明確である（`skills/mission/lib/mission_application/runtime_guard.py:190-220`）。receipt resolver へ戻す。

## 13. 実装時の推奨確認順

1. application unit と synthetic static fixtures を Red にする。
2. pure decision を Green にし、同一入力の decision matrix を固定する。
3. `stop-verdict` serializer compatibility と R1 T1/T2/T10/T11 を Green にする。
4. shell を置換し、`test_stop_hook.py` → `test_stop_guard_dedupe.py` 単独 → parallel tests の順に確認する。
5. plugin mirror、shellcheck、Python 3.9、full suite を最後に確認する。

`test_stop_guard_dedupe.py` の既知の負荷依存性は、contract の削除や xfail の理由にはしない。単独結果と full suite 結果を分けて記録し、同一 failure が再現する場合だけ既知 flaky と実装回帰を切り分ける。
