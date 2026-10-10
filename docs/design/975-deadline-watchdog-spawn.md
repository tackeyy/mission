# 975: 予算付き spawn の締切を、親が停止しても別 group の watchdog で強制する

Refs #921・#881（設計 881 §4.2）。前段: PR #966（verifier の締切付き bootstrap `mission_application/verification_exec.py`）。

## 目的
provider（`command_provider` → `budgeted_exec.spawn_exec` → `provider_process.exchange_provider`）、
`budgeted_exec.run_job`（approval verifier の descriptor 経路・verification budget）、
fresh review adapter（`fresh_review_host` の `spawn_exec`）の各経路は、締切の強制を親（監督）のループに頼っている。
target が自分の group を SIGSTOP し、親も SIGSTOP / SIGKILL されると、締切を過ぎても子・孫が残る。
verifier と同じく、target と別の process group に置いた watchdog が、絶対締切と親の死亡で target の group を回収するようにする。

## 決定
1. **共通 helper を `budgeted_exec` に置く**: `spawn_deadline_exec(argv, deadline, *, pass_fds=(), stdin, stdout, stderr, cwd, env)`。
   中身は既存の `verification_exec.py` の bootstrap を使う（`[sys.executable, '-I', '-S', <bootstrap>, str(deadline), str(control_fd), *argv]` を `spawn_exec` で起動）。
   bootstrap は target を exec する前に watchdog の ready（`R`）を確かめ、target は bootstrap の stdio（packet 用 stdin・別の stdout / stderr）をそのまま受け継ぐ。
   bootstrap の置き場所は、`budgeted_exec`（lib 直下）から参照できる場所に移すか共有する。verification_runner の既存呼出しも同じ helper に寄せる（挙動は変えない）。
   新しい bootstrap は書かない。
2. **control pipe の理由コードで区別する**: `E`（target を起動する**前**の失敗。bootstrap の起動失敗・watchdog の ready 失敗・target の exec 失敗）、`W`（target を起動した**後**に watchdog が異常終了した。target は実行済みとして扱う）、`T`（締切）、何も無し（target の正常終了。終了コード・シグナルを bootstrap が伝える）。
   - `E` だけを「未起動」として扱う（provider は `failed-before-start`・`proven_no_dispatch`、run_job は `exec_unstarted`）。`W` は起動済みとして、締切と同じく group を回収し、回収を確認できなければ kill-unconfirmed として予約を残す。どの理由コードでも、kill-unconfirmed の扱いを上書きしない。
   - exec 失敗の reason には、従来どおり sanitized な詳細（例: 実行ファイルが無い）を残す。
   helper は control の読み取り口を呼出し側へ返す（または child に添える）。
   - provider の「exec の拒否」（従来は `Popen` が `OSError` を送出）は、`E` を観測したときに従来と同じ code・同じ扱い（予約の精算を含む）になるようにする。終了コードの非 0 とは区別を保つ。
   - **target の exec 失敗と bootstrap 自体の失敗を、終了コードで混同しない**（必要なら control に別の 1 byte を足してよい。足す場合は verification_runner の判定も合わせる）。
3. **適用する経路**: provider の起動（`command_provider` の予算付き経路）、`run_job` の子の起動、`fresh_review_host` の adapter 起動。
   approval verifier の callable 経路（`run_callable`、fork）は、予算付きでは既に `budget-deadline-unenforceable` で拒否しているので対象外。この理由をコードのコメントに 1 行書く。
4. **watchdog の締切は、呼出し側の回収期限にそろえる**: run_job は `collect_deadline`（verification は締切＋1＋post_run_sec）が有ればそれ、無ければ締切に、＋1 秒の余裕を足したものを watchdog の締切にする。target 自身の締切（budget の deadline）は変えない。
5. **SIGTERM の猶予を保つ**: bootstrap は group への SIGTERM で自分だけ先に死なない（SIGTERM は同じ group の target に直接届く。bootstrap は target の終了を待って、その終了を伝える）。`cleanup_group` の term_grace の後の SIGKILL は従来どおり。
6. **既存の契約を変えない**: packet の入出力（`exchange_provider` の stdin / stdout / stderr、frame の読み取り）、起動拒否の code、`cleanup_group` による回収と kill-unconfirmed の扱い、予約・精算への配線。
   `cleanup_group(child)` は bootstrap の group（target とその孫を含む）を回収する。watchdog は bootstrap の終了を見て自分も終わる。
7. **予算の policy が無い経路（inert）の挙動は変えない**。helper を通すのは予算の締切がある起動だけ（予算の無い approval descriptor の run_job は従来の spawn_exec のまま）。

## やらないこと
- 予算の公開 CLI の有効化、policy の形の変更。
- callable の approval verifier を予算付きにする。

## 受け入れ条件
- provider・run_job（approval verifier の descriptor 経路）・adapter の各経路で、target が自分の group を SIGSTOP し、親（監督）も SIGSTOP または SIGKILL された場合に、締切＋猶予（1 秒程度）を過ぎたら target と同じ group の孫が消え、watchdog も残らない。修正を戻すと落ちるテスト。
- 既存の packet 入出力・起動拒否（exec 失敗）・精算のテストが変わらず通る。exec 失敗と非 0 終了の区別を固定するテスト。
- spawn / writer の inventory（test_issue921_spawn_inventory・test_issue918_writer_inventory）と fixture を、新しい経路に合わせて更新し通る。thin-adapter ratchet・layering・mirror（plugins/）を通す。
