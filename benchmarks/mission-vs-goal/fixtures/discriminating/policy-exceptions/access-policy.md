# Data Access Exception Policy (v3)

## 2. Approval
2.1 An exception request MUST be approved by a person holding the
`data-steward` role at the time of approval, as recorded in the approver
roster.

## 3. Scope
3.1 A single exception request MAY grant access to at most two datasets.
Broader access requires separate requests per dataset pair.
3.2 Delegation clause: a `data-steward` MAY approve requests originating
from any team, not only their own. Cross-team approval is explicitly
permitted.

## 4. Timing
4.1 Approval MUST precede access.
4.2 Emergency clause: during a declared SEV-1 incident, access MAY begin
before approval, provided the request is filed within 24 hours of access and
references the incident id. Such requests are compliant.
4.3 Outside a declared SEV-1 incident, retroactive approval is forbidden.
A request filed after access has begun, without a qualifying incident
reference, is a violation regardless of later approval.
