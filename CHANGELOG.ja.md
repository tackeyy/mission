# 変更履歴

**日本語** | [English](CHANGELOG.md)

本プロジェクトの主要な変更を記録します。

フォーマットは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### 追加

- `mission-state.py context-manifest --iteration N --out <path>` で bounded context manifest JSON（`mission-context-manifest/1` スキーマ）を生成できるようにした。mission goal、iteration、`score_history` から抽出した prior findings を含む。`_derive_next_action` の reviewing ブロックが details に `context_mode` を返すようになり、`iteration >= 2` かつ `critic_has_new_scope is False` の場合は `"bounded"`、それ以外は `"full"` を返す。reviewer fork がフル parent history ではなく evidence manifest のみを受け取れるようにし、diff レビューでのコンテキスト浪費を削減する (#241)。

- mission-vs-goal ベンチマークに `openworld-discovery` cohort（`tasks.openworld.json`）を追加した。open-world の finding 発見をテストする 3 タスクで構成され、solver は事前列挙なしで divergence・contradiction・root cause を独立に発見する必要がある。タスク設計: constant-hunt（canonical default に対するサービス横断 timeout 監査）、contradiction-chain（real contradiction + 注意深く読むと整合する decoy）、incremental-reveal（最初の仮説が誤りである時系列 incident log）。scoring は tail cohort と同じ `quality_markers` / `forbidden_markers` / `hidden_paths` infrastructure を使う (#251)。
- `_derive_next_action` が `iteration >= 2` かつ `critic_has_new_scope=false` のとき `reviewer_count: 2` を返すようになり、diff-only review のオーバーヘッドを最大 1/3 削減する。`critic_has_new_scope` フィールドは `set` で設定可能、未設定時は full count（安全側）。`aggregate-reviews` に `--min-reviewers N` を追加し、N 未満の reviewer JSON 入力を exit 2 で reject する（合意偽装防止）。`next` の command_hint は effective reviewer count >= 2 のとき自動的に `--min-reviewers` を含む (#240)。

### 修正

- `cleanup-stale` が `getppid()` fallback で記録された PID の mission を即座に orphan halt しなくなった（agent CLI がプロセスツリーで発見できない場合）。`find_agent_pid()` が state に `pid_source` を `"fallback"` または `"agent"` として記録し、`cleanup-stale` は fallback 由来の PID が最近消滅した場合（age < stale threshold）はスキップする。真に放置された fallback セッション（age >= threshold）は従来どおり halt する。`pid_source` なしの旧 state は既存の即時 halt 動作を維持する (#239)。

### 変更

- mission-vs-goal ベンチマークの runner に `--repeats N` を追加し、各 (task, arm) セルを N 回反復して record に `run_index` を記録できるようにした。summary にはアーム別の marker スコア分散と `total_cost_usd` の合計/平均 (blocked run の全損コストを含む) を追加し、flaky・ノイズと実力差を分離できるようにした (#249)。mission arm の record には、実行後の mission state から fail-open で抽出した `mission_review_tier` / `mission_iterations` / `mission_complexity` / `mission_passes` / `mission_halt_category` を記録し、tier 別のコスト・品質帰属を可能にした (#250)。

- mission-vs-goal ベンチマークの scorer が、完走した markered record を全て 5.0 天井に張り付かせる問題を解消した。markered task は `1.0 + 1.0 × validator_fraction + 3.0 × marker_score`（gradient v2）となり内容 recall が支配項になる。marker なし task は legacy 二値 1.0/4.0 の歴史的意味を維持する。新 record は `quality_score_method`（`..._gradient_v2_...`）で機械的に区別でき、既存 JSONL は不変 (#247)。validator gate はアーム対称化し、両アーム共通見出し（Evidence/Assumptions）のみが `validator_pass` を決める。アーム固有見出し（goal 3 個 / mission 6 個）の欠落は `missing_arm_specific_headings` として記録するが gate しない — 見出し数の非対称による完走難易度差と「冗長に書くほど有利」の歪みを除去した (#248)。両 runner の `score_from_signals` は同一意味論をテストで強制している。

### 追加

- `mission-state.py init --budget-minutes <N>` で時間予算 (wall-clock) を宣言できるようにし、read-only の `next` が `started_at` から導出する `budget_pressure` シグナルを返すようにした。80% で `warn` (optional specialist / critic の新規 spawn を控える advisory)、100% 超で `exceeded` となり、spawn 系の next action (`run-planner`/`run-executor`/`run-reviewers`) を「成果物を確定して `mark-halt --category partial-done` で終了する」`consider-halt` 案内へ差し替える。安価なローカル完結手 (`aggregate-reviews`・`mark-passes`)・terminal 報告・`await-user` は差し替えず、ゲート意味論は不変。2026-07-22 に実測された「USD 予算を使い切って成果物ゼロで kill される全損」の再発を防ぐ。ベンチマーク runner には `--mission-budget-minutes` を追加して `/mission` プロンプトへ予算を渡せるようにし、`total_cost_usd` を第一級フィールドとして記録して blocked/failed run の全損コストを集計可能にした (#238)。

- `mission-state.py advance --phase <phase> --activity <kind>:<reason>` が phase 遷移と activity 切替を単一 lock・単一 atomic write で行い、「phase だけ進んで activity が空」の state を作れなくした (2026-07-22 実行速度監査で実測された activity coverage 9.96% の構造要因への対策)。検証 (phase 正規化・kind/reason enum) は lock 取得前に行い、不正入力では一切 write しない。`done`/`halted` への遷移は従来どおり `mark-passes`/`mark-halt` 専用であり、advance を pass gate の迂回路にはできない。現在と同じ phase を指定した場合は activity 切替のみ行う (#237)。

### セキュリティ

- 手動 halt した mission の再開を、専用の `reactivate --approved-by-user --expected-category ... --reason ...` 遷移に限定しました。停止カテゴリを検証し、旧停止理由・カテゴリ・承認理由を append-only の `reactivation_history` に残したうえで、current の停止フィールドをクリアし、activity 計測を同一 lock 内で再開します。汎用 `set` では halt の解除や承認監査の書き換えができません。自動 stale/orphan 復旧は引き続き `resume` / `refresh-pid` を使い、復旧時に current の `halt_category` もクリアします。
- `codex-preflight --strict` が deprecated な `MISSION_REQUIRE_SCORING_EVIDENCE=0` escape hatch を検出して reject する (exit 2)。あわせて実行結果を not ok として報告する。この環境変数は legacy な `push-score --items` 経路で scoring-evidence gate をバイパスするため、有効なまま実作業へ進んではならない。escape hatch 自体は当面機能を維持するが、文言を `DEPRECATED ESCAPE HATCH` に変更し、次のマイナーリリースで削除予定とした (#226)。

### 追加

- local authoring が mission state 初期化前に fail-closed な source bootstrap を実行するようになりました。`origin/main` を取得し、clean な `main` だけを fast-forward で更新して local と remote-tracking commit の一致を検証し、更新済み `SKILL.md` の読み直しを要求します。dirty、`main` 以外、detached、ahead/diverged、remote branch 欠落、network failure では、古い版への fallback や history 書き換えを行わず停止します (#229)。
- `mission-state.py stats` と `mission-audit.py` が排他的な pass-rate health 分類を共有し、finite な `raw_pass_rate` / `completed_pass_rate` と明示的な分子・分母を出力するようになりました。fresh active は可視化したまま completed population から外し、stale active は actionable な未合格 health debt として completed population に含めます。active、active-no-score、stale、halt、abandoned の件数は JSON と console に常に表示します。deprecated な `pass_rate` alias は各 command の従来の意味を維持し、stats は audit と同じ current immutable worktree archive generation を読み込みます (#208)。
- `mission-audit.py --current-since` が検出済みrecord/itemをregistry駆動の共通finding modelへ変換し、forced pass、halt/slow/scoring、specialist provenanceのriskを同じUTC inclusive cutoffで分類するようになりました。`--since` / `--until` / `--current-since`の日付・ISO boundは一つのparserで扱います。JSONはall/current/historicalの基準evidence一覧、severity・code別の保存則count、code別のcompactなcount/indexを、Markdownはcurrent P0/P1/P2をhistorical riskより先に表示します。historical evidenceは元severity/provenanceを保持しますが現行改善promptには渡しません。timestamp欠落・不正はcurrentに残し、cutoff未指定は従来の全期間表示を維持します。pass severity、required specialist result gate、force approval gateは変更しません (#207)。
- mission state に active work・external wait・approval wait・reviewer wait・idle を明示する bounded activity segment を追加しました。`mission-state.py stats` と `mission-audit.py` は同じ reducer で task/phase の R7 p50/p90、kind/reason totals、coverage、unclassified time、anomaly count を集計します。crash/resume gap は分類せず、既存 phase duration と review/pass gate を維持します (#211)。
- `mission-state.py archive-worktree` を追加しました。終端済み worktree session と state が参照する evidence を、同じ Git common directory に属する既存の別 checkout へコピーします。更新は content-addressed な immutable generation を publish してから `current.json` を atomic に進めるため、crash や parallel reader が旧有効世代を見失いません。`mission-worktree-archive/1` manifest は session/mission/iteration identity、evidence type、機密を含まない relative source/archive reference、SHA-256、size を記録し、重複 path、path escape、symlink、evidence 欠落、integrity 不整合を fail-closed にします。`mission-audit.py` は discovery 時の generation を固定して state のロード前に preflight し、検証済み manifest から scoring / specialist evidence を解決して、同一 record の検証を cache します。`.mission-state` は降下前に readiness を確認し、後続の walk access error も収集します。directory 以外・読取不能・symlink の `.mission-state` / archive root、bundle / generation ancestor の symlink、通常 archive root 外へ解決される bundle、archive / pointer / generation の access failure、不正・危険な pointer、archived state の欠落・不正 JSON、generation manifest の欠落・不整合は、root 外読込・archive の黙示的除外・stale file fallback をせず、重複排除した `invalid-worktree-archive` finding として明示し、pointer 不在を `lstat` で確認できた既存 bundle だけ互換性を維持します (#212)。

### 修正

- `mission-audit.py` が実ログ由来の委譲 handoff と明示的な merge 承認待ちの halt reason を認識し、P1 `halted-runs` の actionable 判定に反映するようになりました。raw halt 件数は維持し、stale/orphan は引き続き安全側で actionable に残します。日本語の root 引き渡し・承認待ち文言で actionable pass rate が過度に下がる問題を修正しました (#233)。
- `mission-audit.py` が raw halt 件数を保持したまま、原因調査が必要な終端状態だけから P1 `halted-runs` と別指標 `actionable_pass_rate` を導出するようになりました。構造化された承認待ち、委譲済み partial completion、ユーザー中断、明示的な解消・置換証跡、限定的に認識した外部待ちは内訳に残しつつ actionable 品質を押し下げません。stale、stagnation、競合 gate、未知・曖昧な halt は安全側で actionable に残します。deprecated な `pass_rate` alias は completed-session rate のまま維持します (#221)。
- 非対話の mission 起動時に、orchestrator が必要とする配布版・リポジトリ内の state CLI コマンドだけを許可するようにしました。`mission-state.py init` は実作業前に session state ディレクトリと assumptions 証跡へ内容保持・fsync 付きの実書き込み probe を行います。probe 失敗時は exit 2 と構造化された `blocked-external` halt を返し、state 自体も保存不能な場合は同じ構造化証跡を stdout に残して承認質問を行いません。明示診断用に同じ検査を行う `permission-preflight --json` も追加しました (#220)。
- 不可逆操作の `review_tier` キーワードを、operation に anchor した clause と構造 unit の文脈で出現ごとに評価するようにしました。否定は文字 window 内の cue ではなく対象 operation への直接的な文法 anchor を必須とし、短縮形・`cannot`・active な `not perform/execute`・passive な `will/should not be performed/executed`・日本語の qualifier 付き否定も扱います。明示的に否定された実操作は Simple/Standard を昇格させず、条件例外、非実行 intent 自体の否定、不確実表現は安全側で採用し、複数否定 cue は次の operation より前にある場合だけ反転否定として扱います。global 非実行 marker は、候補自身の context が meta/non-operation intent と証明できる場合だけ抑制し、同じ logical unit の execution cue が別の named operation に直接係ると証明できない場合は曖昧照応として採用します。quote-only intent は、引用符直前・直後の直接実行または引用直後の passive modal だけが上書きし、引用内や別の明示 operation の execution wording では上書きしません。segment・operation start・quote・meta/non-operation・否定 operation・否定 cue・global marker の索引を cache し、全文・dense context の反復走査を避けます。既存の順序付き文字列 signals は変えず、state に出現単位の `review_tier_signal_details` provenance を追加し、security・high-risk・Complex/Critical の挙動も維持します (#209)。
  meta/non-operation の証明は候補 context 全体が strict meta-only 文法へ一致することを要求し、未知の後段句があれば抑制しません。quote span 内の execution cue は曖昧照応 veto の対象外です。quote-only も marker・無害終端・別 named operation への明示 action を除いた外側残余が空の場合だけ抑制します。
  modal / contraction で始まる `not not` と、`not the case that` / `not saying that` / `cannot say that` などの外側否定を二重否定として扱います。`except when` / `until` / approval 待ち / passive な緊急時例外は条件付きのままです。文をまたぐ `follow/apply + pronoun` と日本語の `適用` / `従う` は曖昧な実行照応として global meta-only 抑制を veto します。
  `例外なく` / `緊急時にも` / `原則ではなく絶対に` という強い無条件否定は、広い例外 marker を発火しないようにしました。単純な operation 否定の後に続く因果的な安心表明は、独立した述語否定を誤って二重否定にしません。
  短縮 auxiliary と `never` を operation scope 付きの単純/二重否定で共通化し、外側の報告否定の短縮形も扱います。approval gate は `before` / `prior to` / `while ... is pending` を追加し、曖昧実行照応は pronoun または named procedure に対する `follow` / `apply` / `proceed with` まで認識します。日本語の因果的な安心表明に影響表現を追加しました。
  外側の不確実表現に `not true that` と `no guarantee/assurance/certainty that` を追加し、内側 operation clause の modal 否定が短縮形でも展開形と同じ文法で扱います。

## [2.0.0] - 2026-07-20

### 破壊的変更

- `mark-passes --force` に `--approved-by-user` が必須になりました (未指定は exit 2)。このフラグは「ユーザーが明示的に override を指示した」という宣言であり、バリデーション回避のスイッチではありません。orchestrator が自律的に付けてはならず、ユーザーの明示指示がある場合にのみ使用します。state には従来の `force_reason` に加えて `force_approved_by_user` を記録し、`mission-audit.py` はこれを欠く forced pass を新しい P0 finding として報告します (#185, #193)。
- `set phase=` を phase enum で検証するようになりました。未知の値は exit 2 とし、既知の 4 エイリアス (`execution` / `review` / `plan` / `score`) は警告付きで正規形へ正規化します。実運用で `phase=execution` (typo) が無検証で通り、`phase_duration_totals` を汚染した実害への対処です (#188, #191)。

### 追加

- `mission-state.py stats` に `by_review_tier` (`by_complexity` と同形) と `iteration_by_review_tier` を追加しました。light tier が手戻りを生んでいないかをコマンド一発で監視できます。tier 導入前の state は `unknown` に集計されます (#180, #182)。
- state に `cli_version` を記録し、Claude Code / Codex の plugin cache を走査して実行中 CLI との version skew (古い install) を検出するようになりました (#186, #195)。
- `mark-halt` / `halt --all` が `--category` を受け付けるようになりました (共有 enum `HALT_CATEGORIES`: `blocked-external` / `awaiting-approval` / `partial-done` / `stagnation` / `user-abort` / `stale` / `other`)。未指定・不正値は警告付きで `other` にフォールバックします — 緊急停止パスが category の不備で失敗してはならないためです。自動 halt は `stale` を記録します (#190, #192)。
- optional specialist を選択したまま invocation を一度も記録せずに pass しようとした場合、`mark-passes` が警告と、閉じるための `specialists log-invocation --status skipped` コマンドを表示します。pass gate 自体は変更ありません (#189, #194)。

### 変更

- 「リリースして」「本番へデプロイして」などの明示的なユーザー指示を、対象が一致する不可逆操作の事前承認として扱うようにしました。対象・scope・rollback 条件や必要な破壊的操作に実質的な差分がない限り、実行直前に同じ確認を繰り返しません (#197)。
- `_derive_next_action` が「`score_source=scoring-json` なのに `findings_evidence_path` を欠く」score entry を検出し、再試行を instruction 頼みではなく state 駆動で促すようになりました。実運用の Codex run が `aggregate-reviews` の出力を得られず `--force` へ逃げた一方、同時期の別 run は自己回復していた、という実害への対処です (#187, #196)。

### 修正

- `task_profile.risk=high` のキーワードを #174 と同一ポリシーで較正しました。`prod` を削除 (`production` があり冗長、`product`/`productivity` への誤発火源)、`auth` を `authenticat`/`authoriz`/`oauth` へ、単独の `token` を複合語 6 種へ置換。506 mission の遡及実測で `risk=high` は 72→53 件、risk 起因のエスカレーションは 17→9 件、見逃しは 3 件のまま不変です (#175, #183)。
- `mission-audit.py` が archived worktree bundle 内の `iter-N-<mission8>/scoring.{json,md}` に保存された scoring evidence を認識するようになり、worktree cleanup 後に historical `missing-scoring-evidence` が誤検出される問題を修正しました (#201)。

## [1.2.0] - 2026-07-10

### 追加
- `mission-state.py init` と `mission-state.py set` が session の complexity とミッション記述から `review_tier`（`light`/`standard`/`full`）を導出・保存するようになりました。risk escalator（high-risk profile・不可逆/本番/security キーワード）は昇格のみ行い、降格しません。`reviewer_count` は tier に連動し、pass gate と scoring threshold は不変です。ユーザー指定の override は記録された `source` と `signals` で監査できます (#168, #171)。
- ADR-003 を追加しました。adaptive review gating の決定（tier 導出テーブル・escalator 意味論・ゲート不変宣言）と、tail-v1 実測および 451 mission 本番集計を context として記録しています (#169, #172)。
- `docs/CASE_STUDIES.md` と `docs/CASE_STUDIES.ja.md` を追加しました。451 件の採点済み本番 mission から匿名化した実測エビデンスとして、pass rate 分布・24 件の強制 iteration・7 件の不可逆操作への承認ゲート halt・6 件の代表事例サマリを、出典・限定事項・比較品質主張なしで収録しています (#155, #158)。
- benchmark runner に planted-defect タスク fixture を用いた tail-first-failure cohort を追加しました。quality marker が defect 特有のコンテンツトークンであり、`forbidden_markers` でネットスコアを減算し、`hidden_paths` により answer key（task 定義ファイル）を clone 済み worktree から両アーム実行前に削除し、`markers_hidden` により両アームの prompt に marker 名を出しません (#153, #156)。
- tail-v1 paired run（10 タスク × 2 アーム、claude-sonnet-5、2026-07-07）のベンチマーク報告を追加しました。両アームの quality score は同点で、mission アームは goal アームの約 5.8 倍の時間・約 7.4 倍のコストを要しました。全 5 件の mission run で iteration-1 の self-gate が pass しました (#162)。
- benchmark smoke-v2（N=1、2026-07-10）を追加し、health-interval marker pattern 修正を実証しました。goal アームのスコアは 0.86 から 1.00 に回復し、mission アームは `api_usage_limit` blocked のため品質比較から除外しています (#170, #173)。

### 変更
- benchmark runner が form-stripped scoring を marker マッチング前に適用するようになりました。`strip_form` が見出し・ラベル行・水平線・表の区切り行を除去することで、テンプレート構造が marker クレジットを得なくなります。除去前のスコアは `quality_marker_score_raw` として保存し、`quality_score_method` を `automated_heuristic_form_stripped_not_blind_human` に更新しています (#154, #157)。
- SKILL.md に light tier 運用規律（reviewer 1 名・required のみの specialist・critic は失敗時のみ）を追記し、README に adaptive gating の要約段落を追加しました。pass gate threshold は不変です (#169, #172)。
- README に実測エビデンスの位置づけを追記しました。tail-v1 run では両アームの quality score が同点で、mission アームは約 5.8 倍の時間・約 7.4 倍のコストを要したこと、および本番価値が約 5% の強制 iteration tail と承認ゲート halt に集中することを明記しています (#161)。

### 修正
- `review_tier` の escalator キーワードを 505 mission の遡及分析で較正しました。`push`/`merge` を除外（標準 dev フロー記述への誤発火）、単体 `token`/`auth` を複合語・語幹に置換、単体 `削除` をデータ削除系の複合語に置換。Simple/Standard の過剰エスカレーションは 39.1% から 32.2% に低下し、低スコアミッションの見逃しは増えていません (#174, #178)。
- `mission-audit.py` の `specialist-invocation-gap` 判定では `specialists_phase_plan` の provider を advisory な scheduling hint として扱い、計画だけされた provider が terminal invocation 欠落として誤検出されないようにしました (#176)。
- specialist phase plan の provider を shared accounting 上の selected evidence provider として扱うようにし、計画済みの execution / review / synthesis provider を実行した場合に `unselected-specialist-invocation` が誤検出される問題を防ぐようにしました (#165)。
- `mission-audit.py` と `mission-state.py stats` が archived worktree の `aggregate.json` など session ではない metadata JSON を無視するようになり、`unknown` の abandoned session や low-pass-rate finding の誤検出を防ぐようにしました (#163)。
- `mission-audit.py --since` / `--until` が日付だけでなく ISO timestamp も受け付けるようになり、automation cutoff と同じ日の後続 state が監査から黙って除外される問題を修正しました (#159)。
- `mission-audit.py` が `mission-archive/` worktree パスに保存された scoring evidence を認識するようになり、worktree cleanup 後に `missing-scoring-evidence` が誤検出される問題を修正しました (#151, #152)。
- benchmark の health-interval marker pattern を拡張し、`HEALTH_CHECK_INTERVAL_SECONDS=75`、`(75`、`` 75` `` の引用形式にも一致するようにしました。既存の記録スコアは変更せず、今後の run のみに適用されます (#162)。

## [1.1.1] - 2026-07-06

### 修正
- command provider が `result_contract.awaiting_input_markers` または `result_contract.awaiting_input_exit_codes` に一致した場合、明示承認・人間入力待ちを generic failure ではなく `awaiting-input` として記録できるようにしました (#145)。

### 変更
- specialist registry の文書で、外部送信、browser automation、browser session material、paid quota の承認スコープを分離し、first-use consent が session cookie 再利用や paid model 利用の包括承認ではないことを明記しました (#146)。
- Oracle command-provider の safe default 文書を追加し、local wrapper は manual login または `awaiting-input` を既定にし、明示的な browser-session-material 承認後だけ `--copy-profile` を渡す方針を明記しました (#147)。

## [1.1.0] - 2026-07-05

### 追加
- `mission-state.py aggregate-reviews` を追加し、strict な `mission-review/1` reviewer JSON から、rubric cap・reviewer consensus・open High 件数・findings evidence archive を含む決定論的な `push-score --scoring-json` payload を生成できるようにしました (#119)。
- `mission-state.py specialists log-invocation --selection-source task-required` を追加し、タスク上必須の情報取得・証跡 provider を、private skill 名をハードコードせず selected specialist として記録できるようにしました (#115)。
- `mission-state.py resume` を追加し、active session の復帰時に current mission state、latest artifact、next action、progress checkpoint、stale-session hint を含む復旧順序を表示できるようにしました (#123)。
- benchmark runner に arm-blind scoring、counterbalanced order、明示的な `model_id` 記録、mission-vs-goal 比較用の result/report schema 更新を追加しました (#129, #130)。

### 変更
- `aggregate-reviews` が reviewer agreement を score `items` から外し、独立した `review_agreement` と `agreement_detail` として記録するようになりました。`mark-passes` は極端に低い合意 (`max-min > 1.5`) を pass 前に拒否します (#126)。
- `mark-passes` が、`score_source=scoring-json` の pass 判定で機械由来の findings evidence を primary gate として扱うようになりました。`findings_evidence_path` 欠落や High finding 件数の不一致は、score threshold 判定前に拒否します (#121)。
- 標準 Phase 5 が reviewer の `mission-review/1` JSON、`aggregate-reviews`、`push-score --scoring-json` で進むようになり、`mission-scorer` を spawn しない運用にしました。`mission-scorer` は散文レビューを JSON に変換する fallback 専用として文書化しました (#120)。
- `mission-state.py` と `mission-audit.py` が、mission state の分類・duration・specialist checkpoint・preparation marker ロジックを `skills/mission/lib/mission_common.py` で共有するようにし、audit と state tool の drift リスクを下げました (#127)。
- `mark-passes` が、新規 Standard / Complex / Critical session で `task_profile` と `specialists_decision.policy` の checkpoint がない場合に完了を拒否するようにしました。fallback / degraded の明示 decision は有効な checkpoint として扱います (#112)。
- `cleanup-stale` が、記録された agent PID が生存していても、`MISSION_STALE_ACTIVE_SECONDS` を超えた active no-score session を stale として halt できるようにしました (#113)。
- public ref docs と packaged plugin mirror を OSS portability の観点で見直し、配布される setup 例から maintainer-local home path と private skill 名を除去しました (#118, #132)。
- README、Codex setup docs、critic/planner handoff guidance、軽量化した mission skill instructions を現行 source の scoring flow に合わせました。`mission-review/1`、`aggregate-reviews`、`push-score --scoring-json`、独立した review-agreement gate、`open_high` / findings evidence の pass check を前提にしています (#128, #134, #137, #140, #141, #142)。

### 修正
- mission audit が `score_history[].scoring_evidence_path` の明示パスと、通常または archived worktree の `.mission-state` に保存された JSON scoring evidence を認識するようにしました (#111)。
- mission audit が、fresh な active no-score planning session を specialist accounting debt から分離し、stale な active no-score session は JSON / Markdown で明示的に報告するようにしました (#113, #114)。
- `push-score --scoring-json` が inflated self-reported scalar score を拒否し、同一 iteration の score を置き換える場合は `--resubmit-reason` を必須にしました。これにより score evidence の silent overwrite と転記 inflation を防ぎます (#122, #131)。
- documentation consistency guard が、`open_high` gate、`findings_evidence_path`、`--scoring-json`、`--root`、README test count の鮮度、v1.1.0 release theme を検査するようになりました (#128, #134)。

## [1.0.7] - 2026-07-03

### 修正
- `mission-state.py` と `mission-migrate.py` に `from __future__ import annotations` を追加し、PEP 604 union 注釈が Python 3.9 (macOS Xcode CLT の `python3`) でモジュール読み込み時にクラッシュして全コマンドが使えなくなる問題を修正しました (#99)。

### 追加
- `mission-state.py codex-preflight` を追加しました。現在の Codex `/mission` session に active state があるか、user Stop hook に `mission-stop-guard.sh` が登録されているか、`mission-state.py next` fallback で継続できるかを診断します。skills-only の Codex run では警告に留め、`--require-stop-hook` では hook 未設定を failure にできるため、Issue #108 の「state なし・guard なし・未完了 final」パターンを検出できます。
- `specialists recommend --user-specified <skill,skill>` を追加しました。ミッション本文でユーザーが名指ししたスキルを confirmed 扱いにし、high-risk task profile でも `selection_source: user-specified` の selected として記録するため、以後の `log-invocation` が `--selection-source confirmed-user` 要求で reject されなくなります (#100)。名指しの中に first-use consent が必要な provider が混在する場合、または required specialist が未インストールの場合は、全体を従来の確認フローに倒します。
- `mission-state.py push-score --scoring-json <path>` (ADR-002 Stage 1) を追加しました。scorer の構造化 JSON ファイルから items を読み、`composite`/`min_item` を CLI 側で再計算し、未知キー・範囲外値を reject し、payload を `_meta` 付きで `iter-N-<mid8>-scoring.json` として archive し、score entry に `score_source`/`scoring_evidence_path` を記録します (orchestrator のスコア転記レイヤを排除)。
- `push-score` が「全 items スコアが 1.0 以下」の入力を 0-1 正規化スケール混入の疑いとして reject するようにしました (実ログで composite 0.96 = 4.8/5 が push された事例の回帰ガード)。
- `mission-state.py next` (ADR-002 Stage 3) を追加しました。session state から次の 1 手 (`run-planner`/`run-reviewers`/`run-scorer`/`mark-passes`/`report-blocker` 等) を決定論的に導出し、Stop hook が使えない Codex セッションや compaction 復帰時に、散文指示に依存しないハーネス非依存の進行ガイドを提供します。

### 変更
- scoring evidence なしの `push-score` は default で hard reject するようにしました。`--scoring-json` (推奨) または `--scoring-output` を指定してください。移行専用の一時 escape hatch として `MISSION_REQUIRE_SCORING_EVIDENCE=0` は残しています (#105)。
- evidence なし `push-score` の generated scoring evidence fallback を削除し、reviewer 本文のない `generated=true` archive file で score entry を裏付ける挙動を廃止しました (#105)。

## [1.0.6] - 2026-07-02

### 修正
- `mission-state.py init` が破損した session JSON を隔離するようになり、同一セッションでの mission 変更時にクラッシュしないようにしました。
- `mission-state.py set` が pass・score history・threshold 系フィールドを凍結するようになり、raw な state 更新で完了ゲートをバイパスできないようにしました。
- `mission-state.py push-score` が、渡されたスカラースコアと items 明細のスコアが乖離している場合に警告を出すようにしました。
- Stop hook の CWD 探索が遅い `lsof` によるハングを避け、Linux では `/proc/<pid>/cwd` を優先し、自セッションの直接参照を先に行い、`awaiting_user` セッションの stale auto-halt をスキップするようにしました。
- specialist の同点処理が、インストール済みで optional な low/medium リスク provider を決定論的に自動選択し、tie-break 理由を記録するようにしました。
- mission executor が `Agent` や `rm` を含まない bounded な allowed tools を宣言するようにしました。
- specialist の task_profile 分類が architecture / system design 系 mission を認識するようになり、architecture 専用の project / user provider が documentation fallback に隠れて選ばれない問題を修正しました。
- mission audit が archived worktree の `iteration-archive/` ディレクトリに保存された scorer evidence を認識するようになり、scoring artifact が存在する場合の `missing-scoring-evidence` 誤検出を防ぐようにしました。
- mission audit が JSON として完全一致する archive-only の worktree state copy を resolved duplicate として分類するようにし、cross-root audit で想定内の archive/archive copy が P1 `duplicate-state` と誤報告されないようにしました。

### 追加
- ADR-002 として、local JSON + flock ストレージを維持したまま Finding / Score / Decision / Action を段階的に型付き state オブジェクト化するロードマップを定義しました。
- local-first な mission artifact を archived evidence 付きで管理する `mission-state.py artifact` CLI を追加しました（`docs/MISSION_ARTIFACTS.ja.md` 参照）。
- specialist registry の `kind: command` provider に `env` と `timeout` の runtime 設定を宣言できるようにしました。`env` はその provider プロセスにのみ渡され、CLI の `--timeout` は registry の値より優先されます。

## [1.0.5] - 2026-06-26

### 追加
- `ask-user` 後に confirmed selection metadata が残っていない specialist 適用を、unselected invocation とは別の audit finding として報告するようにしました。
- phase duration がある一方で経過時間の大半が planning に粗く帰属している slow session を mission audit が報告するようにしました。
- mission audit の self-improvement prompt に、agent が GitHub Issue を作成する前の重複 issue 確認と development/tech-lead review 証跡の記録を必須化する指示を追加しました。
- `mission-state.py push-score` が `--scoring-output` 未指定時にも generated scoring evidence を保存するようになり、すべての score history entry に監査可能な archive artifact が残るようになりました。
- `mission-state.py specialists log-invocation --selection-source` を追加し、inline / tool invocation evidence の記録時に、明示・手動選択された specialist の selection metadata も同時に残せるようにしました。
- final report 用に selected / used / degraded / unselected-manual を provider の `kind` と registry/source metadata 付きで出力する `mission-state.py specialists summary` を追加しました。
- 通常の merge release と意図的な distribution release を分離する versioning policy を文書化し、PR を merge するたびに plugin version を上げない運用を明確化しました。
- `AGENTS.md`、`CLAUDE.md`、ADR-001 に OSS portability guardrail を追加し、個人/private specialist skill を public default ではなく user / project registry に置く方針を明確化しました。
- 完了前の warning として、terminal decision trail がない available specialist/provider candidate を表示する `mission-state.py specialists accounting --json` を追加しました。
- `mission-state.py` と `scripts/mission-audit.py` で candidate accounting ロジックを共有し、実行中チェックと事後監査で同じルールを使うようにしました。
- 正典の state CLI に委譲する repository root の安定 wrapper `scripts/mission-state.py` を追加しました。
- 長時間 batch 向けに `mission-state.py progress update/get/clear` checkpoint を追加し、進捗証跡を archive に保存して slow-session の audit 行にも表示できるようにしました。
- maintainer-local な skill 名を組み込まず、development / strategy 系 registry の段階的な利用順を示す `specialists_phase_plan` を recommendation に追加しました。
- mission audit が不正な score iteration と空の specialist invocation record を明示的な finding として報告するようにしました。
- mission audit に `--current-since` を追加し、historical audit debt を可視化したまま current regression と分離して判定できるようにしました。
- distribution release では、対応する git tag の作成・push、GitHub Release の作成または更新、両方の再照合まで完了条件とする release guardrail を追加しました。

### 変更
- mission orchestrator の運用指針に、`phase=executing` / `phase=reviewing` の明示更新と長時間作業の progress checkpoint を必須化しました。
- Complex mission の specialist accounting を、リスクを持つ candidate だけに explicit terminal decision を求める形へ調整し、ユーザー plugin をデフォルトでは optional evidence source として扱うハッカブルな拡張性を維持しました。
- database/backend candidate は schema / migration / query / SQL / persistence などの強い database signal がある場合だけ high-risk accounting candidate として扱うようにしました。
- command provider の `result_contract` により、準備完了バナーだけ、または短すぎる出力を `prepared` と分類し、完了済みレビュー証跡として扱わないようにしました。
- `oracle-reviewer` に browser-review の準備完了バナー向け default result contract を適用し、`ask-user` 後の specialist confirmation は `--selection-source confirmed-user` で永続化してから selected evidence として扱うようにしました。
- broad orchestrator specialist は non-execution の evidence use に限定し、plan/review などの適用済み証跡には `--bounded-purpose` を必須にしました。
- Standard / Complex の監査・自己改善 mission では、利用可能な testing / security / risk specialist candidate に explicit accounting を求めるようにしました。

### 修正
- command provider invocation が `completed` と記録されていても、archive evidence が Oracle / browser review の準備パケットだけの場合に mission audit が検出するようにしました。
- mission audit が、ユーザー判断待ちの active な `ask-user` specialist wait を、decision 記録前の candidate-only specialist debt として誤検出しないようにしました。
- core mission subskill の呼び出しを external specialist の unselected invocation として誤検出しないようにしました。
- marketplace 配布版の `mission-state.py` wrapper から specialist accounting / result-contract marker が欠落しないよう、同期テストで保護しました。
- mission audit の pass rate 計算から active no-score checkpoint を分母除外しつつ、incomplete active session としては引き続き報告するようにしました。
- mission audit が nested `archive/worktree-*/sessions/*.json` copy を resolved archive duplicate として分類するようにし、cross-root audit で live/archive の完全一致 copy が P1 `duplicate-state` と誤報告されないようにしました。
- `mission-state.py mark-passes` が required specialist provider の適用済み結果証跡を確認するようにし、`prepared` / `skipped` / `failed` だけでは strict required-provider gate を満たせないようにしました。
- `mission-state.py push-score` が 1 未満の iteration を拒否するようにし、監査不能な `score_history` entry を防ぐようにしました。
- `mission-state.py specialists log-invocation` が空の `role` / `skill` を保存前に拒否するようにしました。
- `mission-state.py stats` が nested `archive/worktree-*/sessions/*.json` を含めて集計し、audit discovery と session count が揃うようにしました。

## [1.0.4] - 2026-06-22

### 追加
- README で `mission` を loop engineering の品質ゲートとして位置づけ、launch positioning guidance へのリンクを追加しました。
- `mission-state.py stats` が repeated `--root` を受け付け、複数 root を集約し、scan root の一覧を出力し、重複する state identity を二重計上しないようにしました。
- specialist invocation logging が `skill-tool-applied` を受け付け、skipped / unavailable / failed の判断理由を必須化し、高リスク candidate accounting を文書化しました。
- specialist candidate が存在する一方で selection / invocation / skip の decision trail が記録されていない場合、mission audit が `candidate-only-specialists` として可視化するようにしました。
- terminal evidence はあるが Phase 1 selection metadata と対応しない specialist invocation を mission audit が可視化するようにしました。
- mission の最終報告に selected / used / degraded / unselected-manual の短い specialist summary を追加し、`codex-inline` を実 Skill tool 呼び出しと誤表現しない文言を明確化しました。
- specialist registry を project / user / skill/plugin manifest から自動 discovery し、project 側の `enabled: false` で user default を無効化できるようにしました。
- specialist provider schema が `kind: skill` と `kind: command`、first-use risk consent、command provider evidence invocation に対応し、`oracle` など特定 provider を mission core に hard-code せず扱えるようにしました。

## [1.0.3] - 2026-06-20

### 追加
- Phase 1 specialist selection checkpoint rollout 後に開始された session で selection metadata が欠落している場合、mission audit が可視化するようになりました。
- release 完了前に `git log <previous-tag>..HEAD --oneline` と英日 changelog entry を突合する手順を release checklist に追加しました。
- v1.0.2 の release theme が future changelog edit で欠落しないように documentation consistency test を追加しました。

### 修正
- v1.0.2 changelog entry に Phase 1 specialist selection checkpoint、specialist registry、file-overlap warning、audit CLI、GitHub Flow guidance、contributors、Reviewer/Scorer safeguards、audit diagnostics、Codex hook-packaging validation を追記しました。

## [1.0.2] - 2026-06-20

### 追加
- 任意の specialist registry を追加し、mission が task_profile を分類して利用可能な専門 skill を自動選定し、evidence provider として利用し、呼び出し証跡を記録できるようにしました。
- Phase 1 で mission state 初期化後に `specialists recommend --record-state --json` の結果を記録する specialist selection checkpoint を必須化しました。
- `mission-state.py init` に `--files` を追加し、別の active session と対象ファイルが重複する場合に警告できるようにしました。
- read-only な `scripts/mission-audit.py` CLI を追加し、local mission state の監査、self-improvement prompt 生成、forced/ungated pass、duplicate state、halt、slow session、low-score pass の bucket 可視化ができるようにしました。
- mission audit が nested worktree archive session を検出し、missing scoring evidence と specialist invocation gap を可視化するようになりました。
- slow session report に phase duration の観測可否 breakdown を分離して追加しました。
- issue 連携 mission、PR 本文の `Closes #N`、merge による issue 自動クローズを GitHub Flow として明文化しました。
- README に contributors と contribution type の表示を追加しました。

### 修正
- Reviewer / Scorer の安全策を強化し、merge-base 基準の diff 確認とテスト真正性チェックで誤った退行判定や浅いテスト検証を減らしました。
- 同一 logical mission run について、stale halt copy より完了済み pass/done record を優先して dedupe するようにしました。
- audit diagnostics が halt/incomplete の root cause、slow session bucket、low-score pass risk bucket を分類できるようにしました。
- Codex plugin の hook packaging contract が崩れた場合に release validation で検出できるようにしました。

## [1.0.1] - 2026-06-17

### 追加
- **Q11 – stagnation 自動カウント**: `push-score` で composite の改善幅 (`cur − prev`) が `[0, 0.1)` の場合に `stagnation_count` を自動インクリメント。後退（スコア低下）と初回 push は停滞と見なさず 0 にリセット。
- **S3 – 重複 issue-ref 警告**: `init` に `--issue-ref <ref>` オプションを追加。同プロジェクト内の active session に同一 `issue_ref` が存在する場合は stderr に `WARNING [S3]` を表示（reject しない）。同一 `session_id` での resume は自己検出として除外。

### 修正
- **Q11 後退ロジック修正**: 負の delta（スコア後退）が誤って stagnation として計上されていたバグを修正。条件を `0 <= delta < 0.1` に限定し、`_is_valid_composite()` による型チェックも追加。
- コピー配布用の Codex marketplace wrapper（`plugins/mission/`）を正典の `skills/` / `scripts/` と同期し、最新の stale auto-halt、High gate、stats、scoring rubric 修正を含めました。
- Codex wrapper が正典実装から drift した場合に失敗する回帰テストを追加しました。

## [1.0.0] - 2026-06-15

初の公開リリース。

### 追加
- ミッション・オーケストレーター skill と 5 つの補助 skill（planner / executor / reviewer / critic / scorer）。
- `.mission-state` セッション状態 CLI（`mission-state.py`）。Claude Code / Codex のマルチセッション分離に対応。
- スコア履歴とレビュー/critic ループを伴う閾値ゲート付き完了判定。
- ミッション実行中の早期終了を防ぐ Stop hook。stale-state のタイムスタンプ解釈は macOS（BSD `date`）と Linux（GNU `date`）の両対応。
- Claude Code プラグインメタデータとローカルプラグインマーケットプレイス manifest。
- Codex プラグインパッケージ（`plugins/mission/`）と skill symlink ガイド（Stop hook は opt-in）。
- 状態ルーティング・スコアゲート・hook 挙動をカバーする Python テストスイート。
- GitHub Actions CI（`push` / `pull_request` / `workflow_dispatch`）。pytest と ShellCheck を実行。

[2.0.0]: https://github.com/tackeyy/mission/releases/tag/v2.0.0
[1.2.0]: https://github.com/tackeyy/mission/releases/tag/v1.2.0
[1.1.1]: https://github.com/tackeyy/mission/releases/tag/v1.1.1
[1.1.0]: https://github.com/tackeyy/mission/releases/tag/v1.1.0
[1.0.7]: https://github.com/tackeyy/mission/releases/tag/v1.0.7
[1.0.6]: https://github.com/tackeyy/mission/releases/tag/v1.0.6
[1.0.5]: https://github.com/tackeyy/mission/releases/tag/v1.0.5
[1.0.4]: https://github.com/tackeyy/mission/releases/tag/v1.0.4
[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
