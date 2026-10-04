# #925 公開 benchmark 調査: 確認実験用 cohort の実現可能性

対象: [Issue 925](https://github.com/tackeyy/mission/issues/925)（親 [#884](https://github.com/tackeyy/mission/issues/884)、全体の親 [#876](https://github.com/tackeyy/mission/issues/876)）の分割。本書は文書のみで、実装・benchmark の実行・有料 model の呼び出しは行っていない。

**凍結済みの基準**（`docs/design/884-evaluation-aggregation.md` §5.1・§5.0・§6.3 より）:

- **独立単位 = upstream project**（1 project 1 task）。必要な K は §6.3 の見込みで Goal 失敗率の仮定ごとに **約 180（30%）/ 約 275（20%）/ 約 560（10%）/ 約 620（verified-complex の真の失敗率 1% を仮定する場合）**。
- **pool に必要な単位数は K だけではない（884 §5.1「規模」の訂正反映）**: 884 §5.1 は「pool の単位数が『pilot 12 + K』に足りない場合、確認実験に進まず owner へ上げる」としている。pilot 単位（12）は確認の K 単位とは別に pool から取るので、**pool に必要な総単位数は K + 12** である（K=180 → 192、K=275 → 287、K=560 → 572、K=620 → 632）。本書の以下の計算はすべてこの「K + 12」を基準にする。
- **Lic**: benchmark のデータ license と各 upstream repository の license の両方が複製・実行・（bundle に含める場合は）保存を許す。
- **Det**: task ごとに evaluator 所有の fail-to-pass・pass-to-pass 検査と、固定された（pinned）評価環境があり、network を遮断してオフラインで再実行できる（884 §5.1 の凍結要件は「commit B の前に評価環境で 3 回評価し検査ごとの結果が 3 回とも同じ task だけを残す」「評価は network を遮断して行う」）。
- **Cx**（複雑タスク。Cx1〜Cx4 すべて）: test 以外の source file 2 件以上変更、検査 3 件以上（うち fail-to-pass 1 件以上）、test 以外の変更行 10 行以上、課題文が upstream の自然な依頼に由来（Cx4）。**Cx4 の判定方法（884 §5.1）は「benchmark の作り方の記述」であり、I2b が機械判定できる形で報告する。** 本書は各候補について調査で得た構築方法の記述から Cx4 の暫定判断（満たす／満たさない／UNKNOWN）を示すが、I2b による確定判定ではない。
- **Con**（汚染）: pool の `task_id` と upstream の `owner/repo` が mission repository に文字列として現れない。
- **G1〜G3**（系譜）: 同一の正規化 identifier、共通の root commit（fork）、20% 以上の file 内容の重なり（vendoring）を持つ repository は 1 単位にまとめる。

**本書の調査範囲の限界**: web 検索 14 回・WebFetch 数回に限定した短時間調査であり、各候補の正確な repository 一覧や Cx/Lic/Det の実測フィルタは実行していない。数値は出典のある **検証済み事実** と、文献の統計から導いた **推定（estimate と明記）** を区別する。

---

## 1. 候補 benchmark 一覧

### 1.1 SWE-bench（オリジナル／Verified／Lite）

- **repository 数**: **12**（検証済み。2 つの独立検索で一致。[原論文](https://arxiv.org/pdf/2310.06770) Table 12 に license 一覧がある）。Verified（500 件）・Lite（300 件）はこの 12 repo のサブセットで、repo 数は増えない。
- **task 数**: full 2,294 / Verified 500 / Lite 300。
- **言語**: Python のみ。
- **license**: dataset 自体は公開 OSS（SWE-bench リポジトリは MIT。出典: HuggingFace/GitHub の一般的表記。本調査では個々の repo license を 1 件ずつは確認していない — **UNKNOWN**（ただし原論文が「使用許諾のある public repo のみ」と明記）。
- **Det（評価環境の存在）**: あり（検証済み）。公式 `swebench` パッケージが各 instance に Dockerfile・FAIL_TO_PASS/PASS_TO_PASS を提供する。**884 §5.1 の凍結 Det 要件（network を遮断した 3 回の再実行で検査ごとの結果が一致すること）を満たすかは本調査では未確認（UNKNOWN）。** SWE-bench の公式 FAQ を確認したが、network 遮断・決定性についての明言は見当たらず（2026-10-04 WebFetch 確認）、「オフライン再実行が前提の設計」という以前の記述は出典を示せないため削除した。
- **control（正解開始）**: あり。gold patch（reference）がデータに含まれる。
- **汚染**: 最も強い懸念がある候補。2023 年公開のため多くの model の学習データに混入している可能性が高く、派生 benchmark（Verified・Live 等）はこの懸念への対策として作られた。
- **Cx 推定**: SWE-bench Verified の分析（Ganhotra のブログ、検証済み引用）で **500 件中 161 件（32.2%）が「1〜2 行・単一ファイルの trivial」** と報告されている。残り約 68% が Cx1/Cx3 の候補になりうるが、Cx2・Cx4 を考慮した正確な通過率は **推定**。
- **Cx4（自然な依頼由来）**: 原論文の表題・手法（"Can Language Models Resolve Real-World GitHub Issues?"、実際の GitHub issue と merge 済み PR から収集）から、満たす可能性が高いと判断する（**暫定判断。I2b による確定判定ではない**）。
- **判定**: **repo 数 12 は 180（必要総単位数 192）に遠く及ばず単独では使えない**。他候補との重複排除（G1〜G3）の基準点としては有用。

### 1.2 SWE-bench Multilingual

- **repository 数**: **41〜42**（検証済み。公式サイトは 9 言語・41 repo、HuggingFace の個票は言語別内訳の合計で同程度）。
- **task 数**: 300（9 言語: Ruby 44/6repo, Rust 43/7repo, PHP 43/4repo, Java 43/6repo, Go 42/5repo, C 30/4repo, JS 26/3repo, TS 17/4repo, C++ 12/2repo）。
- **license**: SWE-bench と同じ構築方式（**UNKNOWN**、個別確認なし）。
- **Det（評価環境の存在）**: あり（推定。SWE-bench と同じ Docker ベース評価と報告されているが、個別には確かめていない）。**network 遮断・3 回再実行の一致（884 §5.1 の凍結要件）は未確認（UNKNOWN）**。
- **Cx4（自然な依頼由来）**: SWE-bench と同じ構築方式（実際の GitHub issue/PR から収集）であれば満たす可能性が高いが、個別確認はしていない（**暫定判断・UNKNOWN**）。
- **判定**: 41〜42 repo も 180（必要総単位数 192）に届かない。SWE-bench 本体との重複（同一言語圏・類似収集手順）の有無は未確認。

### 1.3 SWE-Gym / SWE-Gym-Raw

- **SWE-Gym**: **11 repository**（検証済み）、2,438 instance、Python のみ、実行環境あり（Det 候補。**884 §5.1 の network 遮断・3 回再実行の一致は未確認・UNKNOWN**）。
- **SWE-Gym-Raw**: **358 repository**（検証済み。GitHub org 上では 341〜343 repo が公開）、64,689 instance だが **「実行環境なし」**（検証済み）= Det を満たさない。Det を満たすには環境構築が必要で、その作業量は **UNKNOWN**（本調査では見積もっていない）。
- **Cx4（自然な依頼由来）**: SWE-Gym・SWE-Gym-Raw とも SWE-bench と同様に実際の GitHub issue/PR を収集源とする（検証済み）。満たす可能性が高いと判断する（**暫定判断**）。
- **判定**: SWE-Gym 単独は 11 repo で不足（必要総単位数 192）。SWE-Gym-Raw は repo 数こそ 358 と豊富だが Det を満たさないため、そのまま使えない。

### 1.4 SWE-smith

- **repository 数**: **128**（検証済み。Python のみ）。
- **task 数**: 50,137（合成生成。1 repo あたり多数の task）。
- **実行環境**: 「repository 全体の実行環境を構築してから合成 task を生成する」（検証済み）ので Det の土台はある。ただし評価の決定性（flaky test の扱い等）は個別確認していない。
- **license**: 上位 PyPI パッケージ（ダウンロード数上位 5,000・star 1,000 超でフィルタ）から選定（検証済み）。個々の license は多様で、確認は未実施（**UNKNOWN**）。
- **Cx4（自然な依頼由来）: 不成立（除外）**。SWE-smith の task は、各 repository の既存コードから自動化パイプラインで **合成生成**されたものであり、原論文（[arXiv 2504.21798](https://arxiv.org/pdf/2504.21798)）は task 生成手法として「既存のテストを壊す（breaking existing tests）」ことでバグを合成することを明記している（検証済み）。これは upstream の実際の issue に由来する自然な依頼ではなく、884 §5.1 の Cx4（「課題文が upstream の実際の issue など自然な依頼に由来し、Mission や特定の agent を狙って作られていない」）を満たさない。**したがって SWE-smith は Cx で pool から除外し、以下の合算・集計（§4・§5）には一切含めない。**
- **判定**: Cx4 不成立により候補から除外。参考情報として repo 数のみ記す（128 repo）。

### 1.5 Multi-SWE-bench

- **repository 数**: **39 は最終 1,632 instance の repo 数ではなく、専門家精査前の 2,456-候補 instance の repo 数である（訂正・再確認済み）**。arxiv HTML 本文は "After applying these criteria, we retain 2,456 issue-resolving instances spanning 39 repositories across 7 languages." と述べており（2026-10-04 WebFetch で原文を再確認）、この 39 は 2,456 件の候補集合に対する数値である。68 名の専門家精査を経た最終 1,632 instance が何 repo に対応するかは、本文では総数として明示されておらず **UNKNOWN**（39 以下になる可能性がある。個々の repository が精査で全件落選すれば repo 数も減るため）。以下の集計では、確認できる上限値として 39 を使うが、過大評価の可能性がある前提で扱う。
- **task 数**: 1,632（2,456 候補から 68 名の専門家が精査）。
- **言語**: Java・TypeScript・JavaScript・Go・Rust・C・C++（Python を含まない 7 言語）。
- **license**: dataset は CC BY 4.0（検証済み、arxiv ページのヘッダー表示）。これは **データセット自体の license** であり、各 upstream repository の license（facebook・grpc・elastic 等が含まれる — GPL/Apache/MIT 混在と推定）は個別確認が必要（**UNKNOWN**）。
- **Det（評価環境の存在）**: あり（検証済み）。PR ごとに Dockerfile を自動生成し、再現可能な実行環境を構築する。**network 遮断・3 回再実行の一致（884 §5.1 の凍結要件）は未確認（UNKNOWN）**。
- **Cx4（自然な依頼由来）**: 実際の GitHub issue と対応する merged PR から収集する構築方式（検証済み）であり、満たす可能性が高いと判断する（**暫定判断**）。
- **判定**: repo 数は上限 39（実際はそれ以下の可能性）。180（必要総単位数 192）に対して大きく不足するが、非 Python 言語を増やす用途で他候補と組み合わせる価値がある。

### 1.6 SWE-PolyBench（Amazon）

- **repository 数**: **21**（検証済み）。
- **task 数**: 2,110（Java・JavaScript・TypeScript・Python の 4 言語。JS 1,017・TS 729・Python 199・Java 165）。
- **license**: **MIT**（検証済み。HuggingFace の AmazonScience 配下 3 データセットすべて）。
- **Det**: 明言した一次情報は未取得だが、VentureBeat の報道が「repository level 評価」と説明しており、構造的に SWE-bench 系と同様の評価環境を持つと推定（**UNKNOWN**、未検証）。**network 遮断・3 回再実行の一致（884 §5.1 の凍結要件）も未確認（UNKNOWN）**。
- **Cx4（自然な依頼由来）**: 構築方式が SWE-bench 系と同様（repository level の実issue由来）と推定されるが、本調査では一次情報を確認していない（**UNKNOWN**）。
- **判定**: 21 repo。単独では不足（必要総単位数 192）。license が明確な点は他候補より扱いやすい。

### 1.7 SWE-rebench（Nebius）

- **repository 数**: **3,468**（検証済み。「21,336 件の verifiable task が 3,400 超の Python repository から」）。**本調査で確認した候補の中で最大の repo 数**。
- **task 数**: 21,336（継続更新。SWE-rebench V2 では言語を跨ぐ拡張も進行中だが詳細未調査）。
- **license**: dataset は **CC-BY-4.0**（検証済み）。各 instance が「commit 時点の各 repository の license」を保持している（検証済み）ため、Lic の機械判定に使える構造がある。ただし「license が複製・実行・保存を許すものだけに絞り込まれているか」は個別に再確認が必要（**UNKNOWN**。保持しているだけで除外フィルタの有無は未確認）。
- **Det（事前構築済み image の存在）**: 21,336 件中 **7,500 件に事前構築済み Docker image が公開**（検証済み）。残り約 13,836 件は image が無く、構築しなければ Det の土台すら満たさない。したがって **Det の土台を満たす候補の repo 数は、7,500 件が何 repo 分かで決まる**（本調査では未算出。**UNKNOWN**、I2b の後続調査または pilot での実測が必要）。**884 §5.1 の凍結 Det 要件（network 遮断・3 回再実行の一致）はこの 7,500 件についても未確認（UNKNOWN）**。
- **汚染対策**: 「continuously updated」「decontaminated evaluation」を標題に掲げる（検証済み、詳細な手法は個別確認していない）。
- **Cx4（自然な依頼由来）**: 実際の GitHub issue/PR から継続的に収集する構築方式（検証済み）であり、満たす可能性が高いと判断する（**暫定判断**）。
- **Cx1〜Cx3 推定**: 複雑度の分布は未調査（**UNKNOWN**）。SWE-bench 系と同様の収集手順（実際の GitHub issue/PR）のため、SWE-bench Verified の trivial 比率（32%）に近いと仮定すれば **約 60〜70% が Cx1〜Cx3 候補**と推定できるが、確証はない。
- **判定**: **repo 数の天井は最も高いが、Det の土台を満たす正確な repo 数が未確定**という最大の不確定要素を持つ。pilot での実測が必須。

### 1.8 SWE-bench-Live / SWE-bench-Live/MultiLang（Microsoft, NeurIPS 2025）

- **repository 数**: MultiLang で **431**（検証済み。2026-08-21 時点の web 情報）、Python 主系列は 2025-06-30 時点で 164 repo・1,565 instance（検証済み）、Windows 系列は 48 repo・66 instance（検証済み）。**継続的に毎月 Python task を約 50 件追加**（検証済み）。
- **言語**: MultiLang で 8 言語（C/C++, C#, Java, TypeScript/JavaScript, Go, Rust, + Python）。
- **license**: **MIT**（検証済み）。
- **Det（評価環境の存在）**: あり（検証済み）。RepoLaunch という LLM エージェントが各 GitHub repository から「テスト可能な container 化環境」を自動生成し、"executable docker sandbox" で評価する。**884 §5.1 の凍結 Det 要件（network を遮断した 3 回の再実行で検査ごとの結果が一致すること）は本調査では未確認（UNKNOWN）**。"executable docker sandbox" という記述からは network 遮断の有無を読み取れない。
- **汚染対策**: 評価時に agent へ `FAIL_TO_PASS`/`test_patch` 等の解答フィールドを渡さない設計、月次更新で leaderboard 比較用に分割（いずれも検証済み）。収集対象が「2024 年以降に作られた実際の GitHub issue」である点は、学習データのカットオフが古い model に対しては汚染リスクを下げる（ただし本調査の "Con" 基準＝ mission repository への文字列の非存在、とは別物）。
- **Cx4（自然な依頼由来）**: 「2024 年以降に作られた実際の GitHub issue」を収集対象としていることは検証済み。ただし「Mission や特定の agent を狙って作られていない」ことまでは個別に確かめていないため、他の候補と同じく**暫定判断**とする（884 §5.1 の Cx4 は I2b の判定対象）。
- **Cx1〜Cx3 推定（再評価・弱い推定への訂正）**: gold patch の統計 **median 2 files・3 hunks・24 lines** は検証済みだが、**この統計の出典は MultiLang（431 repo・8 言語）ではなく、同じ SWE-bench-Live 系列の別の母集団である Python 専用版（93 repository・1,319 task、[arXiv 2505.23419 "SWE-bench Goes Live!"](https://arxiv.org/html/2505.23419v2)）である**（2026-10-04 に出典を再確認。MultiLang の README・データセットページには同等の gold patch 統計は記載されていなかった）。したがって「MultiLang の約半数が Cx1/Cx3 を満たす」という推定は、**別の母集団（Python のみ・93 repo）の統計を MultiLang（8 言語・431 repo）へ外挿した弱い推定**であり、以前の記述が示唆していたような MultiLang 自体の実測ではない。さらにこの推定は Cx2（検査 3 件以上・fail-to-pass 1 件以上）を考慮していない（gold patch の行数・ファイル数のみで、検査数の分布は未調査・UNKNOWN）。「単一ファイル・5 行未満の修正の成功率が 48%」という別の分析も、同じ Python 専用版の文脈由来である可能性が高く、個別には確認していない。
- **判定**: **431 repo は 180（必要総単位数 192）を上回り 560（必要総単位数 572）には届かない水準**。license と汚染対策は検証済みで整備されている（Cx4 は暫定判断）が、**Cx1〜3 通過率は異なる母集団からの弱い推定にすぎず、Det の network 遮断要件も未確認**であるため、「おそらく可」と言える確度は従来の記述より低い。pilot での実測が必須。

### 1.9 SWE-bench Pro（Scale AI）

- **repository 数**: **41**（検証済み）。うち **public 11・held-out 12・commercial 18**。
  - public（11）: 完全に公開・OSS。**license は「強い copyleft（GPL 系）」**（検証済み）。GPL は複製・実行・保存を許すが、§5.1 の Lic 判定で「bundle に内容を含める場合はその保存を許す」の解釈次第で追加の法的検討が必要になりうる（**要確認。本調査では法的判断はしていない**）。
  - held-out（12）: leaderboard 不正防止のため非公開（検証済み。「held-out」の語義から推定、詳細未確認）。公開 benchmark として扱えるかは **UNKNOWN**。
  - commercial（18）: 18 社のスタートアップとの提携による非公開 proprietary codebase。**Lic・Con（契約上の秘密保持）の両方で使用不可の可能性が高い**。
- **Cx1〜Cx3 推定**: SWE-bench Pro は明示的に「trivial（1〜10 行）を除外し、平均 **107.4 行・4.1 file** の修正のみを採用」（検証済み）。**この候補は Cx1・Cx3 の通過率が最も高いと推定される**（ほぼ全件が Cx1・Cx3 を満たすと見込める）。
- **Cx4（自然な依頼由来）**: public・held-out・commercial のいずれも「実際の長期運用コードベースの issue」から収集すると推定されるが、本調査では構築方法の一次記述を確認していない（**UNKNOWN**）。
- **Det（network 遮断・3 回再実行）**: 未確認（**UNKNOWN**）。
- **判定**: Cx1〜3 の質は最良と推定されるが、確実に使える repo 数は public 11 のみ（held-out を含めても最大 23）で、180（必要総単位数 192）には遠く及ばない。**複雑タスクの「質」の参考にはなるが、数の確保には使えない。**

### 1.10 その他見つけた候補（未深掘り・UNKNOWN）

検索で名前が挙がったが本調査では repo 数・license・Det を確認していない候補:

- **SWE-bench Multimodal**（視覚的ソフトウェア領域への一般化を検証。言語・repo 数未調査）
- **SWE-Bench++**（"Framework for the Scalable Generation of SE Benchmarks from Open-Source Repositories"。名称から本調査の目的に合致する可能性が高いが未調査）
- **GSO**（Challenging Software Optimization Tasks。難度は高いと推定されるが repo 数未調査）
- **SWE-Sharp-Bench**（Microsoft、C# 向け。repo 数未調査）
- **SWE-InfraBench**（クラウド infra コード向け。repo 数未調査）
- **DeNovoSWE**（repository を一から生成する系列で、本調査の「既存 upstream project」の枠組みと馴染むか不明）
- **nebius/SWE-bench-extra**（SWE-rebench の関連データセットらしいが repo 数未調査）

これらは K を 180 止まりではなく 275・560 まで伸ばす際の追加候補になりうるが、**本調査の web 検索予算（14 回）内では確認できなかった**。

---

## 2. drand randomness beacon（§5.0 の seed 取得元）

**chain（検証済み・2 系統が存在）**:

| chain | 周期 | mode | chain hash |
|---|---|---|---|
| League of Entropy default（mainnet） | 30 秒 | chained | `8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce` |
| League of Entropy quicknet（mainnet） | 3 秒 | unchained | `52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971` |

- **運営者（threshold）**: League of Entropy は 2019 年に本番稼働を開始し、2020 年に 14 パートナーへ拡大した自発的コンソーシアム（検証済み）。quicknet は現在 **22 ノード中 12 の threshold**（検証済み。今後パートナー増加で threshold も上がる見込みと明記）。運営団体には Cloudflare・EPFL・Kudelski Security・Protocol Labs・Celo・UCL・UIUC 等が含まれる（検証済み）。
- **取得方法**: drand の公開 HTTP API（`docs.drand.love/developer/API-v2/`）から round 番号を指定して randomness を取得できる（API の存在は検証済み。具体的なエンドポイント URL・レスポンス形式は本調査では個票を確認していない — **UNKNOWN**、I2d 実装時に公式ドキュメントを読む必要あり）。
- **署名検証**: drand は閾値暗号（threshold cryptography・bilinear pairing）で各 round の randomness に署名し、公開鍵で第三者が検証できる（検証済み、一般的仕組み）。chain ごとの公開鍵の具体値は本調査では取得していない（**UNKNOWN**）。
- **§5.0 の要件への適合**: 「pool を固定した commit を時刻証明つきで公開した後に初めて値が分かる」という要件に対し、chained mainnet（周期 30 秒）・quicknet（周期 3 秒）のどちらも **運営者が公開前に round の値を知ることは（threshold 未満の共謀では）できない** 設計であり、要件を満たす候補として妥当（ただし脅威モデルの §5.0「残る信頼の前提」が明記するとおり、threshold 以上の共謀は排除できない。**UNKNOWN**）。

---

## 3. OpenTimestamps（§5.0 の時刻証明）

- **仕組み（検証済み）**: 対象 bytes の SHA-256 を取り、複数の hash を Merkle tree に集約し、root を Bitcoin トランザクションに刻む。calendar server はこの集約を代行するだけで、検証には **`.ots` proof と Bitcoin の block header chain だけ**が必要（calendar server や OpenTimestamps 自身を信頼する必要がない）。
- **Bitcoin の確定遅延（検証済み）**: 「典型的には 1〜2 時間」で block に取り込まれ確定する。
- **§5.0 の Δ（推奨 24 時間）との関係**: 1〜2 時間の確定遅延は Δ=24 時間に対して **十分な余裕がある**（確定遅延がさらに数時間ずれても 24 時間の margin を食い尽くす可能性は低いと推定できる）。ただし「Bitcoin の block 時刻が実時刻からどれだけずれうるか」の仕様上の上限（block timestamp の許容ずれ幅。Bitcoin protocol の `MAX_FUTURE_BLOCK_TIME` 等）は本調査では確認していない（**UNKNOWN**。884 本文も同じ点を UNKNOWN と明記しており、I2b の担当範囲として引き継がれている）。

---

## 4. 実現可能性のまとめ（単位数の見積り）

**必要総単位数（K + pilot 12）**: K=180 → 192、K=275 → 287、K=560 → 572、K=620 → 632（本書冒頭「凍結済みの基準」参照）。以下の表の「満たすか」列はこの総単位数を基準にする。**SWE-smith は Cx4 不成立（上記 1.4）により表から除外し、以降の合算にも含めない。**

| 候補 | 検証済み repo 数 | 言語 | Det（評価環境の存在／884 §5.1 の network 遮断要件） | license | Cx4（自然な依頼由来） | Cx1〜3 通過率 | 192（K=180）を単独で満たすか |
|---|---:|---|---|---|---|---|---|
| SWE-bench（full/Verified/Lite） | 12 | Python | 環境あり（検証済み）／遮断要件は UNKNOWN | OSS（個別未確認） | 満たす可能性高い（暫定） | ~68%（検証済み統計からの推定） | 否 |
| SWE-bench Multilingual | 41〜42 | 9言語 | 環境あり（推定。SWE-bench と同方式）／遮断要件は UNKNOWN | 未確認 | 満たす可能性高い（暫定・UNKNOWN） | UNKNOWN | 否 |
| SWE-Gym | 11 | Python | 環境あり（検証済み）／遮断要件は UNKNOWN | 未確認 | 満たす可能性高い（暫定） | UNKNOWN | 否 |
| SWE-Gym-Raw | 358 | Python | **×（検証済み。環境なし）** | 未確認 | 満たす可能性高い（暫定） | UNKNOWN | 否（Det 不成立） |
| ~~SWE-smith~~ | ~~128~~ | Python | 環境あり（検証済み） | 未確認（多様） | **不成立（除外。合成生成・既存テストを壊して生成。arXiv 2504.21798）** | — | **除外** |
| Multi-SWE-bench | **上限 39（2,456候補の値。最終1,632instanceの repo数は UNKNOWN）** | 7言語（非Python） | 環境あり（検証済み）／遮断要件は UNKNOWN | データ: CC BY 4.0／repo: 未確認 | 満たす可能性高い（暫定） | UNKNOWN | 否 |
| SWE-PolyBench | 21 | 4言語 | 推定（未確認）／遮断要件も UNKNOWN | **MIT（検証済み）** | UNKNOWN | UNKNOWN | 否 |
| SWE-rebench | **3,468** | Python | 部分的（7,500/21,336 instance に image あり。repo数換算は UNKNOWN）／遮断要件も UNKNOWN | CC-BY-4.0（検証済み）＋per-repo license 保持 | 満たす可能性高い（暫定） | UNKNOWN | **repo数は十分だが Det 母数が未確定** |
| SWE-bench-Live/MultiLang | **431** | 8言語 | 環境あり（検証済み）／遮断要件は UNKNOWN | **MIT（検証済み）** | 暫定判断（2024 年以降の実 issue を収集することは検証済み） | **弱い推定 ~50%（別母集団からの外挿。下記参照）** | **弱い推定では満たす可能性（約215 推定 vs 必要192）が、560（必要572）には届かない** |
| SWE-bench Pro（public のみ） | 11（held-out含め最大23） | 多言語 | 推定（未確認）／遮断要件も UNKNOWN | GPL系（検証済み。Lic判定は要確認） | UNKNOWN | 最高（検証済み統計） | 否 |

**180〜560 という要求数（総単位数 192〜572）に対する結論**:

1. **単独で 192（K=180 相当）を満たす確度が最も高いのは SWE-bench-Live/MultiLang（431 repo）**。license と汚染対策は検証済みで揃っている（Cx4 は暫定判断）が、**Cx1〜3 の通過率推定（約50%）は MultiLang 自体の実測ではなく、同系列の別母集団（Python 専用版・93 repo・1,319 task、arXiv 2505.23419）の gold patch 統計を外挿した弱い推定であり、Det の network 遮断要件も未確認**。弱い推定の50%でも約215 repoが残り192はクリアできる見込みだが、**Lic（upstream ごとの license 未確認）・Con（mission repo との文字列一致。現時点のmission repoでは未検出＝§5.1「Con チェック」参照）・G1〜G3（系譜の重複排除）の減少分を考慮すると、余裕は大きくない**。572（K=560相当）には明らかに届かない。
2. **SWE-rebench（3,468 repo）は repo 数の天井としては唯一 572 を大きく超える候補**だが、**Det の土台を満たす repo 数が「7,500/21,336 instance」から逆算できておらず未確定（最大の UNKNOWN）**。この数字が判明しない限り、572 を狙う根拠にできない。
3. **Multi-SWE-bench（上限39）・SWE-PolyBench（21）・SWE-bench Multilingual（41〜42）を合算**すれば単純合計で**約 101〜102 repo**になる（SWE-smith は Cx4 不成立のため合算から除外した）。これは 192 にも遠く届かない規模であり、**license・Cx1〜3 通過率の未確認分、G1〜G3 の重複排除分を考慮すればさらに減る**。
4. **SWE-bench Pro の public 11 repo は Cx1〜3 の質（平均107行・4.1ファイル）が最も高いと推定される**（Cx4 は UNKNOWN）が、数の確保には使えない。

---

## 5. 推奨と owner が決めるべきこと

### 推奨

**第一候補は SWE-bench-Live/MultiLang（431 repo、MIT。license と汚染対策は検証済み、Cx4 は暫定判断）を主要 pool とし、SWE-rebench の「Docker image 公開済み 7,500 instance」のうち何 repo 分に当たるかを次の調査ステップで確定したうえで、不足分の補完に使う二段構成を推奨する。** ただし MultiLang の Cx1〜3 通過率（約50%）は別母集団からの弱い推定であり、Det の network 遮断要件も未確認なので、**pilot での実測（§6.2 の pilot 12 単位）が必須**である。Multi-SWE-bench・SWE-PolyBench・SWE-bench Multilingual は、G1〜G3 の重複排除後に単位を上積みする第三の供給源として扱う（合算しても約101〜102 repoで単独では足りない）。SWE-bench（12 repo）・SWE-Gym（11 repo）・SWE-bench Pro public（11〜23 repo）は数が小さすぎて単独では使えないが、汚染対策・Cx 品質の参考データとして残す。**SWE-smith は Cx4 不成立のため、この推奨のいずれにも含めない。**

### 180 / 560（必要総単位数 192 / 572）とのギャップ

- **K=180（Goal失敗率30%想定、必要総単位数192）**: SWE-bench-Live/MultiLang 単独（431 repo）で、Cx・Lic・Con・G1〜G3 を通過する実測値が **repo数の約44.5%以上（192/431）**残れば届く。現時点の推定（別母集団からの弱い推定・約50%）はこのラインの近傍にあり、**pilot での実測なしに K=180 を保証できない**。
- **K=560（Goal失敗率10%想定、必要総単位数572）**: SWE-bench-Live/MultiLang 単独では届かない可能性が高い（431 repo の全件が通過しても 572 には届かず、通過率を考慮すれば更に厳しい）。SWE-rebench の Det 母数が判明し、かつそこから十分な数が Cx・Lic・Con・G1〜G3 を通過しない限り、572 は現時点では **達成の見込みが立たない**。
- **K=620（verified-complex の真の失敗率を1%と仮定する場合、必要総単位数632）**: 884 本文（§6.3）に記載の通りさらに厳しく、現状確認した候補の中で単独達成可能なものは無い。

### owner が決めるべきこと（884 §6.3 手順4に従う。ギャップが埋まらない場合の選択肢）

884 §6.3 手順4は「K × §6.1 の実行量・見積りが承認上限を超える、または K が pool の単位数から pilot の 12 を引いた数を超える場合は、確認実験に進まず owner へ上げる」と定めている。**884 §6.3 手順3は「`p̂_goal`・`p_vc = 0` の値を下回る K は選べない（選んだ後は変えない）」とも定めており、K を予算や pool の都合で任意に小さくする選択肢は無い。** したがって、pool が不足すると判明した場合に owner が選べるのは次の二択であり、**1/10 の成功基準（§3.1）や Cx・Lic・Det・Con・G1〜G3 の判定基準そのものを変更する選択肢は無い**。

1. **複数 benchmark を合算する**: SWE-bench-Live/MultiLang を主、SWE-rebench（Det母数確定後）・Multi-SWE-bench・SWE-PolyBench・SWE-bench Multilingual を副として合算し、G1〜G3 の重複排除後に K を確定する。この場合、`report_kind=confirmatory` の母集団が複数 benchmark の混合になることを**新しい事前登録**に明記する必要がある（884 §5.1 は「どの benchmark を選ぶかは owner が選ぶ」としているが、複数選択を想定した記述は無く、**本書の範囲外の追加決定が必要**）。
2. **ギャップが埋まらないと判断し、確認実験に進まず #884 を「未検証」のまま終える**（884 §6.3 手順4に該当）。この場合、品質改善の主張は H（開発用 12 件）の診断結果に限られ、母集団への一般化は行わない。

### Con チェック（mission repository への文字列の非存在。884 §5.1）

**884 §5.1 の Con1 は「pool の `task_id` と upstream の `owner/repo` が、commit A 時点の mission repository の全 tracked file と verified-complex・baseline の package の中身に文字列として現れない」ことを要求する。** 本書の作成時点（2026-10-04）の worktree（`.worktrees/issue-925`。commit A はまだ存在しないので正式な Con1 判定ではなく、現状の予備確認）で、本書が挙げた候補 benchmark の名称（`SWE-bench`・`SWE-smith`・`SWE-Gym`・`Multi-SWE-bench`・`SWE-PolyBench`・`SWE-rebench`・`SWE-bench-Live`・`SWE-bench Pro`、大小文字ゆれを含む）と、本文中に挙がった upstream repository の owner/repo サンプル（`facebook/`・`grpc/`・`elastic/`）を、`.git` を除く worktree 全体に対して検索した。

```
grep -rIl -E "SWE-bench|SWE-smith|SWE-Gym|Multi-SWE-bench|SWE-PolyBench|SWE-rebench|SWE-bench-Live|SWE-bench Pro|swe-bench|facebook/|grpc/|elastic/" . --exclude-dir=.git
```

**結果: ヒットしたのは本書（`docs/design/925-public-benchmark-survey.md`）自身のみ**（本書がこれらの名称を調査対象として記述しているため）。mission repository のソース・テスト・ドキュメントの他の箇所には現れていない。したがって現時点では Con1 に抵触する文字列は見つからなかった。**ただし、これは Con1 の正式判定ではない**: (1) commit A は未確定で、判定対象は「commit A 時点」の repository と package であり、本調査時点とは一致しない可能性がある。(2) 実際の Con1 検査対象は benchmark の **候補名**ではなく、選定された pool の **`task_id`** と各 upstream の **正確な `owner/repo`** 文字列であり、本調査はそれらをまだ列挙していない。(3) 上の grep は worktree の tracked file（`skills/` 以下の Mission の source を含む）を対象にしたが、verified-complex・baseline として凍結する package（commit A で固定する配布物）を特定して検索したものではない。**正式な Con1 判定は、pool manifest 確定後（884 §5.0 手順3）に I2b/I2d が行う。**

### 明示する UNKNOWN（本書で確認できなかった事項）

- 各候補の **upstream repository 個々の license**（SWE-bench系・Multi-SWE-bench・SWE-PolyBench・SWE-rebench のいずれも、dataset licenseと個別 repo license を網羅的に照合していない）。
- SWE-rebench の **7,500 件の pre-built Docker image が何 repo 分に相当するか**。
- 各候補の **Cx1〜Cx3 通過率の実測値**（SWE-bench-Live の gold patch 統計は別母集団からの外挿であり、他の候補はすべて UNKNOWN または粗い推定）。
- 各候補の **Cx4 の確定判定**（本書の判定は暫定的な推測であり、I2b による benchmark の作り方の記述確認を経ていない。SWE-smith のみ原論文の明記により確定的に不成立）。
- **884 §5.1 の凍結 Det 要件（network 遮断・3 回再実行で検査ごとの結果が一致すること）**（すべての候補について、Docker/評価環境の存在以上の確認はできていない）。
- Multi-SWE-bench の **最終 1,632 instance に対応する repo 数**（39 は 2,456 候補時点の値であり、精査後の正確な repo 数は未確認）。
- SWE-bench-Live の **Python 専用版（164 repo・1,565 instance）と MultiLang（431 repo）の関係**（MultiLang は Python を含む 8 言語を持つため Python 専用版が MultiLang に包含されるのか、別の時点のスナップショットで両者が重複するのかは未確認。重複排除後に合算してよいかは UNKNOWN）。
- **G1〜G3 を候補間（特にPython系: SWE-bench/SWE-Gym/SWE-bench-Live/SWE-rebench。SWE-smith は Cx4 不成立により対象外）に適用した際の重複排除後の正確な単位数**。
- drand の **具体的な公開鍵値とAPI endpoint の仕様**（I2d 実装時に公式ドキュメントで確認する必要がある）。
- Bitcoin block timestamp の **実時刻からのずれの仕様上の上限**（884 本文も同じ点を UNKNOWN としており、Δ=24h の十分性の最終判断に必要）。
- repository の管理者が merged PR を削除できるか、fork・GH Archive 等の外部写しで main の履歴を照合できるか（884 §5.0 が I2b に要求した項目だが、本書では調査していない。**次の調査ステップとして持ち越す**）。
- 全単位の組を比較する G1〜G3 の計算量と clone の容量（884 §5.1 が I2b に要求した項目だが、本書では見積もっていない）。
- **control（正常版から始める課題）を作れるか**: 候補はいずれも gold patch（upstream の修正）を配布物に含むと報告されているが、SWE-bench 以外では、gold patch を当てた状態が pinned 環境で全検査を通る（＝正常版として使える）ことを確認していない（UNKNOWN。I2c の evaluator で全件確かめる）。
- **G の export で足りるか**: G（#882）の worker への書き出しが、公開 benchmark の task（repository の checkout・課題文・評価環境）を表せるかは確認していない（UNKNOWN。I2c の bundle の入口で扱う）。
- **配布物の snapshot を正規化できるか**: 884 §5.0 は commit A で入力 snapshot の digest（正規化 tar）を固定することを求めるが、各候補の配布物（HuggingFace の dataset・container image）を決定的な tar に正規化できるかは確認していない（UNKNOWN。I2d で扱う）。
- Det の全件再実行に要る計算量（監査標本への切替が必要かの判断材料。本書では見積もっていない）。

---

## 出典（本文中で参照した URL・アクセス日はすべて 2026-10-04）

- [SWE-bench: Can Language Models Resolve Real-World GitHub Issues?](https://arxiv.org/pdf/2310.06770)
- [SWE-bench repositories · GitHub](https://github.com/orgs/swe-bench/repositories)
- [FAQ - SWE-bench](https://www.swebench.com/SWE-bench/faq/)
- [SWE-bench/SWE-bench_Multilingual · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-bench/SWE-bench_Multilingual)
- [SWE-Gym/SWE-Gym · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-Gym/SWE-Gym)
- [SWE-Gym-Raw · GitHub](https://github.com/SWE-Gym-Raw)
- [SWE-smith: Scaling Data for Software Engineering Agents (arxiv 2504.21798)](https://arxiv.org/pdf/2504.21798)
- [SWE-bench/SWE-smith · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-bench/SWE-smith)
- [Multi-SWE-bench: A Multilingual Benchmark for Issue Resolving (arxiv 2504.02605 HTML)](https://arxiv.org/html/2504.02605v1)
- [Multi-SWE-bench · GitHub](https://github.com/multi-swe-bench)
- [GitHub - amazon-science/SWE-PolyBench](https://github.com/amazon-science/SWE-PolyBench)
- [AmazonScience/SWE-PolyBench · Datasets at Hugging Face](https://huggingface.co/datasets/AmazonScience/SWE-PolyBench)
- [nebius/SWE-rebench · Datasets at Hugging Face](https://huggingface.co/datasets/nebius/SWE-rebench)
- [SWE-rebench dataset blog (nebius.com)](https://nebius.com/blog/posts/swe-rebench-dataset)
- [GitHub - microsoft/SWE-bench-Live](https://github.com/microsoft/swe-bench-live)
- [SWE-bench-Live/SWE-bench-Live · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-bench-Live/SWE-bench-Live)
- [SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks? (arxiv 2509.16941)](https://arxiv.org/html/2509.16941v2)
- [SWE-Bench Pro: Raising the Bar for Agentic Coding (scale.com)](https://scale.com/blog/swe-bench-pro)
- [Jatin Ganhotra: SWE-bench Verified easy/medium/hard](https://jatinganhotra.dev/blog/swe-agents/2025/04/15/swe-bench-verified-easy-medium-hard.html)
- [SWE-bench Goes Live! (arxiv 2505.23419)](https://arxiv.org/html/2505.23419v2)
- [drand: quicknet is live on the League of Entropy mainnet](https://docs.drand.love/blog/2023/10/16/quicknet-is-live/)
- [drand Explained](https://docs.drand.love/about/)
- [drand HTTP API](https://docs.drand.love/developer/API-v2/drand-http-api/)
- [OpenTimestamps: a step-by-step tutorial](https://www.dgi.io/ots-tutorial/)
- [Timestamps Without Trust: How OpenTimestamps Democratized Cryptographic Proof of Existence](https://stampd.org/opentimestamps-trustless-proof/)

(本書作成に使用した web 検索は 14 回。候補一覧に名前のみ挙がった SWE-Bench++・GSO・SWE-Sharp-Bench・SWE-InfraBench・DeNovoSWE は検索予算の制約で深掘りしていない。)

## orchestrator による一次確認（2026-10-04）

- SWE-bench-Live の README（[microsoft/SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live)、2026-09-25 時点の main）で、MultiLang が 2026-08-21 時点で 1,077 task・431 repository・8 言語であること、評価 code の license が MIT であることを確認した。dataset 自体の license と、各 upstream repository の license（Lic の判定対象）は未確認で、I2c・I2d の機械判定で確かめる。
- 同 README は、agent の prompt・skill に task 固有の解を含めないこと、`problem_statement` 以外の field（`FAIL_TO_PASS`・`test_patch` 等）に触れないことを定めている。これは事前登録の worker と evaluator の分離と矛盾しない。

## 独立 Checker 指摘の反映（2026-10-04・追加の一次確認）

独立 Checker の指摘を反映するにあたり、以下を追加で確認した（WebFetch 2 回。上記「本書の調査範囲の限界」の 14 回の web 検索とは別枠）。

- **Multi-SWE-bench の「39 repo」の帰属**: [arXiv 2504.02605 HTML](https://arxiv.org/html/2504.02605v1) 本文を再取得し、"After applying these criteria, we retain 2,456 issue-resolving instances spanning 39 repositories across 7 languages." の一文を確認した。**39 は 68 名の専門家精査を経る前の 2,456-候補 instance に対応する repo 数であり、最終 1,632 instance の repo 数ではない**（最終値は本文に明示されておらず UNKNOWN）。1.5・§4 の記述を訂正した。
- **SWE-bench の Det・network 遮断の主張の裏取り**: [SWE-bench 公式 FAQ](https://www.swebench.com/SWE-bench/faq/) を再取得し、network 遮断や決定性再現についての明言が無いことを確認した（「Docker is required for consistent evaluation environments. This ensures that the evaluation is reproducible across different systems.」とあるが、これは「複数システム間の一貫性」の主張であり、network 遮断や同一結果の再現保証ではない）。これに基づき、以前の記述「オフライン再実行が前提の設計」は出典不十分として削除し、全候補について 884 §5.1 の凍結 Det 要件（network 遮断・3 回再実行の一致）を UNKNOWN と明記するよう訂正した。
- **884 §5.1「規模」の pilot 12 の扱い**: `docs/design/884-evaluation-aggregation.md` §5.1 の「pool の単位数が『pilot 12 + K』に足りない場合」という記述を再読し、本書の必要総単位数の計算に `+12` が欠けていたことを確認した（K=180 → 192 等に訂正）。
- **884 §6.3 手順3・手順4**: 同文書 §6.3 を再読し、手順3「`p̂_goal`・`p_vc = 0` の値を下回る K は選べない」という制約と、手順4「K が pool の単位数から pilot の 12 を引いた数を超える場合は、確認実験に進まず owner へ上げる」という規則を確認した。これに基づき、§5「owner が決めるべきこと」の選択肢から「K を 180 付近に抑える」案を削除した（凍結された判定基準の変更提案に当たるため）。
