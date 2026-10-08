# Issue #920 execution-boundary evidence
Scope: design §4.2/§6/§7 F2p at 2749e183; Issue calls this F1.

- Closed approval job, UTF-8 JSON frame, private 0700/0600 file, exclusive/no-follow creation and sha256 checks are implemented.
- Registry verifier uses exec/-I; syscall session setup precedes interpreter startup. Callable uses readiness/go and refuses budgeted state before spawn.
- Leader observation uses WNOWAIT (callable: sentinel); group SIGKILL precedes reap. ESRCH confirms cleanup; uncertainty refuses success. All spawned-exception paths remove the job.
- PID/start-time recovery preserves live, unknown and open-reservation owners; stale jobs are removed on CLI startup. Partial write/fsync/validation failure removes the file and raises budget-job-write-failed before spawn.
- macOS startup .pth/sitecustomize descendants, normal descendants, large jobs/startup stalls, pin changes and setsid/close failures have real-process tests. Linux execution remains for parent CI.
- Independent input exploration: 54/54 matched (31 decoder, 8 JSON, 6 frame, 9 file); replay with issue-920-input-exploration.py. Two additional Red cases rejected UTF-16 and numeric overflow.
- In-memory mutation checks detected all 10 changes: digest, partial cleanup, exclusive create, no-follow, live-owner check, WNOWAIT, reap-before-kill, normal group cleanup, -I, empty-group confirmation (issue-920-mutations.json).
- Local results: new exec tests 35; staging 60; layer/pin 20; layer/ratchet/mirror/hygiene 124; provider approval fixtures 4 passed. Final combined exec/scoring: 106 passed in 56.79s. Staging: 60 passed; aggregate layer/ratchet/mirror/hygiene: 124 passed in 17.32s.
- Earlier combined CLI run: 299 passed/8 failed in 426.92s while fixtures were being corrected. The 8 fixture NameErrors were corrected and rerun: 8 passed. This incomplete run is retained as evidence, not called green.
- Independent implementation check: reported close-exception and installed-fixture findings corrected; delta inspection found no remaining concrete High/Medium. Formal CLI review, independent gate, full suite and required CI belong to parent CC.
- Budget activation/admission/settlement and provider wiring remain inert. verification-supervisor and adapter-call schema/targets are refused until F2a/F2b implement their jobs.
- GitHub in-flight and PR-list reads failed to connect; no absence claim. No git mutations, GitHub writes, review CLI, or full-suite execution performed.
- Test value: existing force-pass/provider receipt guarantees retained; new groups detect surviving children, deadline hangs and unsafe job acceptance. Temporary venvs cost subprocess startup; pure decoder/file tables avoid repeated process tests. CI full-code path selects skills/mission tests once files are committed.

Changes: skills/mission/bin/mission-state.py; lib/budgeted_exec.py; lib/mission_application/{approval_verifier,spawn_trampoline}.py; lib/mission_persistence/{spawn_jobs,local_uow}.py; their six distribution mirrors under plugins/mission/skills/mission/; tests/{test_issue920_exec,conftest,test_score_provenance,test_issue879_completion_cli,test_provider_preflight,test_issue550_c2_stage_b_batch2}.py; tests/fixtures/thin-adapter-baseline.jsonl; reports/issue-920-{validation.md,input-exploration.py,input-exploration.json,mutations.json}. Lib/tests prefixes refer to skills/mission/. Reviewed working diff including untracked files: below 1,400 (mirrors excluded using scripts/pr_size.py rules).
