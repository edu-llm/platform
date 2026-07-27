# Phase 3 measurement method

This document exists because an earlier revision of the Phase 3 plan opened with a finding that was wrong. It reported that a service control policy denies ten EC2 actions in both regions; seven of them are authorized in `us-east-1`, and a peer principal under the same permissions boundary had performed three of them hours earlier. The source was `iam:SimulatePrincipalPolicy`, with `aws:RequestedRegion` supplied and `--resource-arns` supplied, and it was believed because it was specific and plausible.

The correction is worth less than the method that caught it, which is why the method is a document rather than a paragraph. **A specific, plausible, uncontrolled measurement is the shape of a confidently wrong answer.** Any probe this phase introduces carries its controls here, or its result does not count.

## Probe one: EC2 authorization, read with `--dry-run`

EC2's dry run evaluates authorization and then stops, so it is the service's own answer rather than a model of one, and nothing is created either way. It distinguishes four outcomes where the simulator distinguishes two:

| what EC2 answers | what it means |
| --- | --- |
| `DryRunOperation` | authorized -- the request would have succeeded |
| `UnauthorizedOperation` | denied |
| `<Thing>LimitExceeded` | authorized, and there is no room. A quota is a support request; a denial is not fixable by us. The simulator cannot tell these apart at all. |
| anything else | the request never reached authorization, so it says nothing about the caller |

### The controls, and how each verdict was established some other way

Four captured answers, one per verdict, kept as literal CLI stderr in `edullm_platform.ec2_authorization.CONTROL_OBSERVATIONS` so that a change to the parsing is covered too. Each verdict is known independently of the classifier being checked.

| action | region | verdict | established by |
| --- | --- | --- | --- |
| `ec2:CreateSecurityGroup` | us-east-1 | authorized | CloudTrail records CreateSecurityGroup succeeding for a peer Intern-*-sbsandbox role in us-east-1 on 2026-07-27, with no errorCode. |
| `ec2:CreateVpc` | us-east-2 | denied | The same probe in us-east-1 returns DryRunOperation, so the difference is the region rather than the credentials or the probe. |
| `ec2:CreateVpc` | us-east-1 | quota_blocked | Observed by making the real call, not a dry run: five VPCs exist against a quota of five. Authorization passed and there was no room, which is the distinction a policy simulation cannot draw. |
| `ec2:RunInstances` | us-east-1 | inconclusive | A request EC2 rejected before authorizing anybody. The same probe with a real AMI id returns DryRunOperation, so reading this as a denial would have reported an authorized action as refused. |

The fourth is the one worth reading twice. A `RunInstances` dry run naming an AMI that does not exist is rejected before anybody is authorized, and reading that as a denial would have reported an authorized action as refused -- which is the same failure as the headline this document is about, arriving by a different road.

### The same controls as the capture recorded them

Read from `fixtures/evidence/phase-3/account-measurements.sanitized.json`, which is a `FreshEvidenceModel` and refuses to load once it is older than the freshness window. A matrix whose controls disagree is not a matrix with one bad row; it is a matrix whose classifier is wrong, and the record says so in a field rather than leaving a reader to notice.

| action | region | classified | expected | agrees |
| --- | --- | --- | --- | --- |
| `ec2:CreateSecurityGroup` | us-east-1 | authorized | authorized | yes |
| `ec2:CreateVpc` | us-east-2 | denied | denied | yes |
| `ec2:CreateVpc` | us-east-1 | quota_blocked | quota_blocked | yes |
| `ec2:RunInstances` | us-east-1 | inconclusive | inconclusive | yes |

### The method, as the capture itself records it

EC2 authorization is read with --dry-run against the real API, never from iam:SimulatePrincipalPolicy. DryRunOperation means the request would have succeeded; UnauthorizedOperation means it would not; a *LimitExceeded code means authorization passed and there is no room; anything else means the request never reached authorization. The simulator's OrganizationsDecisionDetail reported ten of these actions as denied in both regions when seven are authorized in us-east-1, which is why the method is recorded here rather than assumed.

## Probe two: does an action support resource-level permissions?

The Operating Environment's rule is that an action whose service authorization reference lists no resource type can only be granted on `"*"`. The reference page is currently a redirect stub, so the answer was measured rather than read: grant the action on exactly one ARN in a custom policy, then `iam:SimulateCustomPolicy` that action with `--resource-arns` naming that same ARN. Resource-level support means the grant matches and the answer is `allowed`; no resource type means IAM evaluates against `*`, the ARN-scoped grant never matches, and the answer is `implicitDeny`.

This is still a simulator result, and the section above is a recent argument for not trusting one. The difference is the controls, run on every invocation:

| control | answer | known from |
| --- | --- | --- |
| `cloudformation:ValidateTemplate` against its own stack ARN | `implicitDeny` | a live Phase 1 deploy failure naming the action |
| `cloudformation:DescribeStacks` against the same ARN | `allowed` | scopable, and scoped in the deployer today |
| `logs:DescribeLogGroups` against a log-group ARN | `implicitDeny` | the second Phase 2 deploy failure |

All three behaved correctly, which is the reason to believe the rest. The backstop is that the deploy fails closed: a missing grant surfaces as a `CREATE_FAILED` naming the action, which is how this repository learned the first two controls in the first place.

### Two corrections this method invites, both recorded rather than fixed quietly

- `logs:GetLogEvents` first read as having no resource type. It has one, and the wrong answer came from an ARN in the wrong form: `log-group:<name>:log-stream:<stream>` returns `implicitDeny` and `log-group:<name>:*` returns `allowed`. When this probe says `implicitDeny`, check the ARN form before believing it.
- IAM Access Analyzer's `validate-policy` looks like the right tool and does not detect this class. Given a policy granting `cloudformation:ValidateTemplate` on a stack ARN and `logs:DescribeLogGroups` on a log-group ARN -- both known wrong -- it returned zero findings, while correctly flagging `ARN_REGION_NOT_ALLOWED` on a regional ARN for a global action in the same document.

## What neither probe was allowed to be used for

- `OrganizationsDecisionDetail` from `simulate-principal-policy` is not a usable signal here. It reported `AllowedByOrganizations: false` for actions that are demonstrably allowed, and returned the same answer for both regions when the regions genuinely differ.
- Neither probe says anything about quota, capacity or placement. A compute environment reporting `VALID` is not evidence that a job can run: Batch does not fail a job it cannot place, it waits.
