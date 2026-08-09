# Artifact Handoff

Executor は実行契約が確定した時点で、遅くとも executing から reviewing へ進む前に artifact applicability を解消する。state file は直接編集しない。

生成対象の場合は、reviewer が読む最終 bytes を repository-relative path の regular non-symlink file として確定し、一意な producer run id と一緒に返す。orchestrator は同じ phase transition で次を実行する。

```text
mission-state.py advance --phase reviewing --activity reviewer-wait:review-response \
  --artifact-applicability producing \
  --artifact-path reports/result.md \
  --producer-run-id executor-run-1
```

生成対象外の場合は理由を execution log に残し、次を実行できる handoff を返す。

```text
mission-state.py advance --phase reviewing --activity reviewer-wait:review-response \
  --artifact-applicability not-applicable
```

`producing` は path、SHA-256 digest、byte size、producer run id を nested `state.artifact` に atomic に保存する。path は repository-relative、4 MiB 以下の regular non-symlink file でなければならない。consumer は同じ bounded single-descriptor validator で bytes を再確認するため、handoff 後の置換・変更は reject される。top-level `artifact_path` は legacy read-only fallback であり、新規 producer は使用しない。
