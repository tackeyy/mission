# Support Ticket Digest (excerpt, 2026 Q2)

## SUP-1189 — EU tenant CSV delimiter regression (2026-06-01)
Customer exports on the EU shard produced semicolon-delimited files after
2.31.2. Engineering shipped hotfix 2.31.4 to the EU shard on 2026-06-02.
Customer confirmed resolution. Note: no changelog entry was published for
2.31.4.

## SUP-1197 — Bulk export row limit question (2026-06-19)
Answered from documentation; no defect.

## SUP-1204 — fastcsv license inquiry (2026-06-21)
Customer legal asked about the fastcsv license. Upstream fastcsv relicensed
from MIT to BUSL-1.1 as of fastcsv 1.8.0. Our bundled version is affected.
Escalated to legal; NOTICE file update pending.

## SUP-1188 — CVE-2026-4417 exposure question (2026-05-30)
Customer asked whether 2.31.2 fully remediates CVE-2026-4417. Response cited
the changelog. Follow-up from security engineering: remediation requires
fastcsv >= 1.9.0; verify the shipped pin.
