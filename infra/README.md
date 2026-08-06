# Deploying the IAM stacks

Every CloudFormation stack in this repository that creates an `AWS::IAM::Role` is applied
by hand, from a laptop, by a person holding an AWS SSO session. No workflow in
`.github/workflows/` deploys one, and none ever will.

Phase 1 did this twice and wrote none of it down. This file is that procedure, so Phase 2
— which adds three roles and amends one — does not have to rediscover it.

Where a detail below could not be established from this repository it says so in place,
rather than offering a plausible value. Read those as instructions to check, not as
defaults.

## Why IAM is laptop-only

A pipeline that can create roles can create a role stronger than itself, attach a policy
to it, and assume it. Every other control in this repository — the OIDC subject
conditions, the narrow inline policies, the publisher denial matrix — is then decoration,
because the pipeline can mint its way around all of them. So CI is never granted any part
of the role lifecycle: `infra/iam/infra-deployer-role.yaml` holds no `iam:CreateRole`,
`iam:PutRolePolicy`, `iam:AttachRolePolicy` or `iam:UpdateAssumeRolePolicy`.

It does hold one `iam:` action, and the difference is worth being precise about.
`iam:PassRole` is granted on exactly two role ARNs, written out in full, because
`states:CreateStateMachine` and `lambda:CreateFunction` both take a role ARN and the
calling principal must be allowed to pass it. Passing an existing role is not creating
one: the deployer can hand the admission state machine the role this repository already
reviewed, and it cannot change what that role may do, cannot create another, and cannot
pass anything else. The ARNs are literal rather than an `sbsandbox-intern-edullm-*` prefix
so that a role created later under a matching name is not passable by inheritance.

This is a choice, not a missing feature. The `InternSandboxBoundary` permissions boundary
permits `iam:CreateRole` whenever the request carries the boundary, so a CI role that
deployed IAM stacks would be entirely possible to build. It is deliberately not built.

The cost is real and worth naming: an IAM change is not reproducible from a merge, it has
no run log, and the account can drift away from the committed template between deploys.
That is what `tools/capture_phase1_evidence.py` and `edullm_platform.role_drift` exist to
catch, and the verification step below is not optional for that reason.

## Prerequisites

- An AWS SSO profile named `sbsandbox`. Every command below passes `--profile sbsandbox`
  explicitly rather than relying on `AWS_PROFILE`, because a mis-set environment variable
  aimed at the wrong account is precisely the accident that IAM changes should not have.
- Region `us-east-1`. Passed explicitly for the same reason.
- An identity with the full role lifecycle on the `sbsandbox-intern-*` names:
  `iam:CreateRole`, `iam:GetRole`, `iam:UpdateRole`, `iam:DeleteRole`,
  `iam:PutRolePolicy`, `iam:GetRolePolicy`, `iam:DeleteRolePolicy`,
  `iam:ListRolePolicies`, `iam:UpdateAssumeRolePolicy`, `iam:TagRole`, and the
  CloudFormation stack actions. A session that can create but not delete strands every
  failed stack, which is the failure mode the recovery section exists for.
- `iam:PassRole`, but only for the conditional-write probe at the end of this file, which
  creates a state machine and hands it a role. No IAM stack deploy needs it.

Start the session and confirm which account you are about to change:

```bash
aws sso login --profile sbsandbox
aws sts get-caller-identity --profile sbsandbox --region us-east-1
```

## Deploying one IAM stack

```bash
aws cloudformation deploy \
  --stack-name <stack-name> \
  --template-file infra/iam/<template>.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox \
  --region us-east-1
```

`--capabilities CAPABILITY_NAMED_IAM` is required, and the narrower
`CAPABILITY_IAM` is not enough. Every template here sets an explicit `RoleName`, because
the role name is what the OIDC trust conditions, the deployer's resource scopes and the
drift comparison all key on; a CloudFormation-generated name would make each of those
unwritable. Naming the role is exactly what promotes the capability from `CAPABILITY_IAM`
to `CAPABILITY_NAMED_IAM`.

`--no-fail-on-empty-changeset` makes a re-run of an unchanged template a no-op instead of
an error, which matters because the verification step below is worth running on its own
and re-deploying first is the cheapest way to be sure the template is what is deployed.

## Which tree you deploy from

**Deploy from `main`, at its tip, with nothing uncommitted. Never from a branch.** Check
before every apply, in the worktree you are about to apply from:

```bash
git fetch origin main
git status --porcelain          # must print nothing
git rev-parse HEAD origin/main  # must print the same commit twice
```

The reason is that `aws cloudformation deploy` reconciles rather than merges. It makes the
stack be the template you hand it, so whatever the template omits is removed from the
account, and a change set will not tell you: a change set is computed from the template
being applied, and it has nothing to say about a grant that template never mentions. A
branch cut before somebody else's merge omits their change by construction, so applying
from it silently takes their change back. The templates in `infra/iam/` each hold several
roles that unrelated pull requests touch, which is what makes two people colliding on one
stack the ordinary case rather than the unlucky one. This is not a hypothetical; the GPU
workload role lost a grant this way on 2026-08-01, and that incident is written up in full
under *Stack 1: the delete a retry needs, re-applied 2026-08-01* below.

The rule has a corollary that is easy to get backwards. If your change has not merged yet,
you have not earned the right to apply it, and applying it early is how the account comes
to hold something no reviewer has seen. The one exception this repository has made is a
grant CI needs before its first scheduled run — the audit reader's
`cloudformation:GetTemplate` was applied from a branch on 2026-08-01 for exactly that
reason — and the cost of the exception is a window in which the account is ahead of `main`
and the check below reports it. Merge promptly and the window closes.

**What catches it when somebody forgets is `tools/verify_deployed_stacks.py`**, which the
audit runs as the `deployed-stack-templates` job. It reads each deployed stack's
template out of CloudFormation, holds it against the file in `main` that declares it, and
names the resource and the property that differ. It reads which template belongs to which
stack from `STACKS` in that file, and a stack the account holds that `STACKS` does not name
is reported rather than skipped, so a stack somebody deploys next is a finding and not a
blind spot. Adding a stack therefore means adding three things in the same change: the
entry in `STACKS`, the stack's ARN in the `cloudformation:GetTemplate` grant in
`infra/iam/audit-reader-role.yaml`, and the row in the phase table below. Run it by hand
after applying anything, which is faster than waiting for 05:00:

```bash
uv run python tools/verify_deployed_stacks.py --profile sbsandbox --region us-east-1
```

It exits 1 when the account and `main` disagree and 2 when it could not find out, because
those ask the reader for different things. Directly after a merge that touches a CI stack
it can be legitimately red until the deploy workflow finishes, and a re-run clears that.

## The permissions boundary

`arn:aws:iam::<account>:policy/InternSandboxBoundary` must be attached to every role this
repository creates. The templates already do this:

```yaml
PermissionsBoundary:
  Fn::Sub: arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/InternSandboxBoundary
```

`Fn::Sub` rather than a literal, so no account ID is committed and the same template works
in any account that has the boundary.

`iam:CreateRole` is denied outright unless the request carries this exact boundary, so a
template that omits it does not create a weaker role — it fails. The same is true of a
template that names a different boundary. If a deploy fails with an access denial on
`CreateRole` and the identity is otherwise correct, check this line first.

## Why customer-managed policies must never be used here

Use `Policies:` (inline) on the role. Never `AWS::IAM::ManagedPolicy`.

`InternSandboxBoundary` denies `iam:CreatePolicyVersion`, `iam:SetDefaultPolicyVersion`
and `iam:DeletePolicyVersion` on every policy. A customer-managed policy can therefore be
created once and never amended: the first permission change fails, permanently, and the
stack cannot be updated or rolled forward. There is no workaround short of deleting the
policy and every role that references it.

Inline policies change through `iam:PutRolePolicy`, which the boundary permits on the
`sbsandbox-intern-*` names, so an inline policy can be amended as often as the phase
requires.

This is worth stating in the open because it inverts standard advice. Reviewers reasonably
suggest preferring managed policies for reuse and auditability, and every template in
`infra/iam/` carries a comment saying why it does not. That advice does not survive this
boundary. If a reviewer raises it, the answer is not "we prefer inline" — it is that a
managed policy here is a one-way door.

## Verifying after each deploy

CloudFormation reporting `CREATE_COMPLETE` says the API calls succeeded. It does not say
the role in the account matches the template, and after any manual console edit it will
not. Read the role back:

```bash
aws iam get-role \
  --role-name <role-name> \
  --profile sbsandbox --region us-east-1

aws iam list-role-policies \
  --role-name <role-name> \
  --profile sbsandbox --region us-east-1

aws iam get-role-policy \
  --role-name <role-name> \
  --policy-name <policy-name> \
  --profile sbsandbox --region us-east-1

aws iam list-attached-role-policies \
  --role-name <role-name> \
  --profile sbsandbox --region us-east-1
```

Check by eye that `PermissionsBoundary` is `InternSandboxBoundary`, that
`AssumeRolePolicyDocument` carries the conditions the template declares, that
`PolicyNames` holds exactly the inline policies the template declares, and that
`AttachedPolicies` is empty — no template here attaches a managed policy, so anything
listed there was added outside CloudFormation.

Then do it mechanically. `tools/capture_phase1_evidence.py` reads each role IAM returns,
compares it to the committed template in both directions, and exits non-zero on any
difference:

```bash
uv run python tools/capture_phase1_evidence.py \
  --aws-profile sbsandbox \
  --aws-region us-east-1 \
  --environment sandbox \
  --repository OLMo-core \
  --target roles \
  --output-dir docs-frank/working/phase-1-evidence
```

It writes only under `docs-frank/working/phase-1-evidence/` and refuses anywhere else, so
a capture is local until somebody reads it and copies what they want into `fixtures/`.
Which roles it compares is `COMMITTED_ROLE_TEMPLATES` in
`src/edullm_platform/role_drift.py`; a Phase 2 role is not compared until it is listed
there, and adding it is part of shipping the role rather than a follow-up.

## Recovering a stack stranded in `DELETE_FAILED`

A stack whose delete fails part-way sits in `DELETE_FAILED` and blocks the name. Phase 1
hit this: the shared deployer role of the time was missing `ecr:PutLifecyclePolicy`, so
CloudFormation could neither finish reconciling the repository nor clean it up. Recovery
needed laptop credentials, because the CI role that created the stack was by construction
not able to clear it — the missing permission was the reason it was stuck.

Find what is stuck:

```bash
aws cloudformation describe-stack-events \
  --stack-name <stack-name> \
  --max-items 20 \
  --profile sbsandbox --region us-east-1
```

Then delete the stack, retaining the logical IDs that could not be deleted. Every
resource that failed to delete must be named, and only those:

```bash
aws cloudformation delete-stack \
  --stack-name <stack-name> \
  --retain-resources <LogicalId> [<LogicalId> ...] \
  --profile sbsandbox --region us-east-1

aws cloudformation wait stack-delete-complete \
  --stack-name <stack-name> \
  --profile sbsandbox --region us-east-1
```

`--retain-resources` is accepted only on a stack already in `DELETE_FAILED`; on any other
status the call is rejected. The retained resources stay in the account, unmanaged. Deal
with them before re-deploying, or the new stack fails on a name that already exists — for
an IAM role, delete the orphan with `aws iam delete-role` after removing its inline
policies with `aws iam delete-role-policy`.

Fix the cause before re-deploying. A stack that stranded once for a missing permission
will strand again.

## The Phase 1 stacks

Recorded here on 2026-08-01 rather than in Phase 1, which is what the second paragraph of
this file is about. Both names were read off the account rather than recovered from a
commit, because neither was ever written down; the deployer's was recovered on 2026-07-27
with the `describe-stack-resources` command below and the publisher's on 2026-08-01 while
`tools/verify_deployed_stacks.py` was being given its table.

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-ecr-publisher-iam` | `infra/iam/ecr-publisher-role.yaml` | `…-ecr-publisher` | laptop |
| 2 | `sbsandbox-intern-edullm-infra-deployer-iam` | `infra/iam/infra-deployer-role.yaml` | `…-infra-deployer` | laptop |
| 3 | `sbsandbox-intern-edullm-phase1-ecr` | `infra/ecr-repositories.yaml` | the three ECR repositories | CI |

## The Phase 2 stacks, in dependency order

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-phase2-admission-service-roles` | `infra/iam/admission-service-roles.yaml` | `…-admission-states`, `…-admission-lambda` | laptop |
| 2 | `sbsandbox-intern-edullm-infra-deployer-iam` | `infra/iam/infra-deployer-role.yaml` (amended) | `…-infra-deployer` | laptop |
| 3 | `sbsandbox-intern-edullm-phase2-admission-iam` | `infra/iam/admission-role.yaml` | `…-admission` | laptop |
| 4 | `sbsandbox-intern-edullm-phase2-lineage` | `infra/lineage-bucket.yaml` | lineage bucket | CI |
| 5 | `sbsandbox-intern-edullm-phase2-artifacts` | `infra/artifacts-bucket.yaml` | artifacts bucket | CI |
| 6 | `sbsandbox-intern-edullm-phase2-admission` | `infra/admission-state-machine.yaml` | state machine, validator, log group | CI |

**Every laptop stack goes before every CI stack**, and within the laptop group the order
above is the one that works:

- The service roles come first because step 2 grants `iam:PassRole` on their two ARNs by
  full ARN. The grant is accepted before the roles exist — IAM does not require the
  resource of a grant to exist — so this ordering is not strictly forced, but getting it
  wrong is easy to do and hard to see, and creating the target first costs nothing.
- Step 2 has to precede everything CI does. Until the deployer carries
  `deploy-phase2-admission-stacks`, the `iam:PassRole` grant and the second entry in its
  `job_workflow_ref` list, the Phase 2 workflow cannot assume the role at all, and if it
  could it would be denied on the first bucket. Deploying CI first produces an access
  denial that reads like a broken workflow and is not one.
- Step 3 is independent of the CI stacks — nothing passes the admission role, GitHub
  assumes it — but it must exist before `submit-run.yml` is used.
- Steps 4 and 5 precede step 6 because the validator's code object lives in the artifacts
  bucket and the state machine writes into the lineage bucket. Nothing links the three
  stacks, so CloudFormation will not enforce that; the step order in
  `.github/workflows/deploy-phase2-admission.yml` is what does.

Two notes on the names in that table:

- **The service roles stack (1).** Nothing else in the repository names this stack —
  only a laptop deploys it, and no workflow or verification step references it — so this
  file is where the name is decided. Use the one above and do not vary it: a second name
  produces a second stack that tries to create the same two role names and fails.
- **The Phase 1 deployer stack (2).** The role
  `sbsandbox-intern-edullm-infra-deployer` was created from a laptop during Phase 1 under
  a stack name that was committed nowhere — not in `README.md`, not in `infra/`, not in
  `tools/`, not in the acceptance evidence, and not in any commit message. It was recovered from
  the account on 2026-07-27 and is now in the table above:
  `sbsandbox-intern-edullm-infra-deployer-iam`. Guessing was not an option, because
  deploying the amended template under a new name fails on the role name already
  existing. The command that recovered it, kept for the next resource whose stack nobody
  wrote down:

```bash
aws cloudformation describe-stack-resources \
  --physical-resource-id sbsandbox-intern-edullm-infra-deployer \
  --query 'StackResources[].StackName' \
  --profile sbsandbox --region us-east-1
```

Anything under `infra/` that is not under `infra/iam/` is deployed by CI. `--capabilities`
is not needed for those, and they must never be applied from a laptop: a stack applied by
hand and then re-applied by CI reconciles against whatever the laptop left, and the run
log stops describing the account.

## The Phase 3 stacks, in dependency order

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-phase3-batch-iam` | `infra/iam/batch-roles.yaml` | `…-batch-execution`, `…-batch-workload`, `…-batch-instance` and its instance profile | laptop |
| 2 | `sbsandbox-intern-edullm-phase3-lifecycle-iam` | `infra/iam/lifecycle-lambda-role.yaml` | `…-lifecycle-lambda` | laptop |
| 3 | `sbsandbox-intern-edullm-phase2-admission-service-roles` | `infra/iam/admission-service-roles.yaml` (amended) | `…-admission-states` gains `batch:SubmitJob` | laptop |
| 4 | `sbsandbox-intern-edullm-infra-deployer-iam` | `infra/iam/infra-deployer-role.yaml` (amended) | `…-infra-deployer` gains `deploy-phase3-batch-stacks` | laptop |
| 5 | `sbsandbox-intern-edullm-phase3-outputs` | `infra/outputs-bucket.yaml` | workload output bucket | CI |
| 6 | `sbsandbox-intern-edullm-phase3-network` | `infra/batch-network.yaml` | VPC, subnets, route table, internet gateway, security group | CI |
| 7 | `sbsandbox-intern-edullm-phase3-batch` | `infra/batch-compute.yaml` | compute environment, queue, job definition, log group | CI |
| 8 | `sbsandbox-intern-edullm-phase3-events` | `infra/batch-events.yaml` | rule, SQS, DLQ, recorder function, alarms | CI |
| 9 | `sbsandbox-intern-edullm-phase2-admission` | `infra/admission-state-machine.yaml` (amended) | the submit and binding states | CI |

Same rule as Phase 2: **every laptop stack goes before every CI stack**, and within the
laptop group the order above is the one that works.

- Stacks 1 and 2 come first because stack 4 grants `iam:PassRole` on their role ARNs by
  full ARN. IAM does not require the resource of a grant to exist, so this is not strictly
  forced, but creating the target first costs nothing and getting it wrong is hard to see.
- Stack 3 is what lets the admission state machine submit a job at all. It is the only
  principal in the account that may start compute, and it is reachable only through an
  execution the admission role started.
- Stack 4 has to precede everything CI does. Until the deployer carries
  `deploy-phase3-batch-stacks`, the third `job_workflow_ref` entry and the `iam:PassRole`
  grant, the Phase 3 workflow cannot assume the role, and if it could it would be denied on
  the first bucket.
- Stacks 6, 7 and 8 are ordered by what references what: the compute environment needs the
  subnets and the security group, and the events rule needs the job queue ARN. Nothing links
  the stacks, so CloudFormation will not enforce that; the step order in
  `.github/workflows/deploy-phase3-batch.yml` is what does.
- Stack 9 is last because the state machine's submit state names the job queue and job
  definition that stack 7 creates.

**Stack 1 takes about three minutes. The other three laptop stacks take under one.** That
is long enough to read as a hang, and it is not one: stack 1 is the only one that creates
an instance profile, and CloudFormation waits on it before reporting `CREATE_COMPLETE`.
Wait rather than interrupting — a create interrupted part-way is how a stack ends up in the
`DELETE_FAILED` recovery above, and here it would strand a role name and a profile name
rather than one name.

### The service-linked role Batch needs, and why a human creates it

`AWSServiceRoleForBatch` does not exist in this account. Batch will create it on the first
`CreateComputeEnvironment` if the caller holds `iam:CreateServiceLinkedRole` — which would
put a role-creation path into the deploy pipeline, and the first section of this file is
about why that does not happen here.

The argument is weaker than usual and worth stating rather than glossing: a service-linked
role is not a role we author, its policy is AWS's, and we cannot widen it. What survives is
that it is still a role creation made by a pipeline, and the rule is worth more than the one
command it saves.

```bash
aws iam create-service-linked-role \
  --aws-service-name batch.amazonaws.com \
  --profile sbsandbox --region us-east-1

aws iam get-role \
  --role-name AWSServiceRoleForBatch \
  --profile sbsandbox --region us-east-1 \
  --query 'Role.{Name:RoleName,Created:CreateDate}'
```

`InvalidInput ... has been taken in this account` means it already exists, which is a
success for this purpose. Run it before stack 7; a compute environment created without it
fails in a way that names the role and not the reason.

### Networking is ours, and it was nearly not

`infra/batch-network.yaml` creates a VPC. That was not possible on the morning of
2026-07-27: `us-east-1` held five VPCs against a quota of five, and `CreateVpc` returned
`VpcLimitExceeded`. The `L-F678F1CE` increase to 10 was filed and applied the same day, and
confirmed by creating a VPC and deleting it again.

Two things worth keeping from that, because both nearly sent this phase somewhere else.

**`VpcLimitExceeded` is not an authorization failure.** A quota is a support request; a
denial is not fixable by us. Anything that reports "CreateVpc failed" without telling the
two apart is throwing away the actionable half.

**`us-east-2` is not a fallback, and looks like one.** The region lock permits both, so the
obvious response to a full `us-east-1` is to move. `ec2:CreateVpc`, `ec2:CreateSubnet`,
`ec2:CreateSecurityGroup` and `ec2:RunInstances` are all `UnauthorizedOperation` there. An
EC2 compute environment in `us-east-2` is not possible at all. `tools/probe_ec2_authorization.py`
re-measures this without creating anything; run it before believing otherwise.

### Stopping a job a cancelled workflow left running

**Reach for `.github/workflows/cancel-run.yml` first.** It takes a run id, reports what the
job is doing, and stops it when asked, on `sbsandbox-intern-edullm-run-canceller` rather than
on anybody's SSO session — a submitter may stop their own run and an admin may stop anyone's.
So the ordinary case needs nothing from this section, and what follows is the fallback for
when the workflow is itself the thing that is broken.

Cancelling a `submit-run.yml` run stops the workflow and nothing in AWS, and no identity
*that* workflow can obtain is permitted to terminate a job: the admission role holds one
`states:StartExecution` and two read-only execution actions, and `batch:TerminateJob` is
deliberately absent from it, from the deployer, and from the lifecycle recorder. Its
`if: cancelled()` step names the cancellation workflow for that reason.

That leaves a real window. GitHub cancels a job in seconds; a submitted Batch job runs until
its `attemptDurationSeconds` unless somebody stops it. The job name is the run id, which is
what makes it findable.

```bash
aws batch list-jobs \
  --job-queue sbsandbox-intern-edullm-cpu \
  --job-status RUNNING \
  --profile sbsandbox --region us-east-1 \
  --query 'jobSummaryList[].{Id:jobId,Name:jobName,Started:startedAt}'

aws batch terminate-job \
  --job-id <the job id whose name is the run id> \
  --reason cancelled-by-operator \
  --profile sbsandbox --region us-east-1
```

`list-jobs` takes one status at a time, so a job still waiting for capacity needs
`--job-status RUNNABLE` as well — and that is the more likely state for a run cancelled
early, because a job that never got capacity is exactly the case somebody gives up on.

Terminating is recorded rather than silent: Batch emits a state change, the rule delivers it,
and the recorder writes a lifecycle event with state `cancelled` — `lifecycle_projection.py`
reads the termination reason to distinguish an operator's cancellation from a failure. So the
lineage record of a cancelled run is complete in the same way a successful one is.

## The Phase 4 stacks, in dependency order

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-phase4-gpu-iam` | `infra/iam/batch-gpu-roles.yaml` | `…-batch-gpu-execution`, `…-batch-gpu-workload`, `…-batch-gpu-instance` and its instance profile | laptop |
| 2 | `sbsandbox-intern-edullm-phase4-gpu` | `infra/batch-compute-gpu.yaml` | compute environment, queue, job definition, log group | CI |
| 3 | `sbsandbox-intern-edullm-dataset-validator-iam` | `infra/iam/dataset-validator-role.yaml` | `…-dataset-validator` | laptop, not applied yet |
| 4 | `sbsandbox-intern-edullm-run-canceller-iam` | `infra/iam/run-canceller-role.yaml` | `…-run-canceller` | laptop |
| 5 | `sbsandbox-intern-edullm-audit-reader-iam` | `infra/iam/audit-reader-role.yaml` | `…-audit-reader` | laptop |

Stacks 4 and 5 each need a repository variable as well as a deploy:
`AWS_RUN_CANCELLER_ROLE_ARN` for stack 4, which `.github/workflows/cancel-run.yml` reads,
and `AWS_AUDIT_READER_ROLE_ARN` for stack 5, which `.github/workflows/audit.yml` reads.
Without it the workflow fails at the credential step with an empty role, which is a
confusing way to say the stack was never applied. Both workflows guard on the variable and
name the stack instead, but the guard is a better message rather than a substitute.

**Its authorisation is in the workflow rather than in the policy, and that is forced rather
than chosen.** A trust policy cannot see who dispatched a workflow — every dispatch of a
file presents the same `sub`. The job's `edullm:submitter` tag is readable through
`aws:ResourceTag`, and there is nothing to compare it against, because the identity in hand
is the workflow rather than the person who ran it. So the role can stop any run this
platform submitted, and the check that it is the caller's own is a step in `cancel-run.yml`.
What bounds that is the role's shape: it describes jobs and stops them and reaches nothing
else in the account, so the worst a bypass achieves is stopping runs. The role's trust names
that one workflow file, so a job that could skip the check has to be added beside the check.

Same rule as Phases 2 and 3: **every laptop stack goes before every CI stack**. Here it is
not enforced by CloudFormation at all — the comment immediately above the *Deploy Phase 4 GPU
batch compute stack* step in `.github/workflows/deploy-phase3-batch.yml` (lines 164–166) says
so directly, and this table exists so that comment is findable from the stack name rather than
from the workflow file.

- **No committed file named stack 1 before today.** Three roles are deployed under
  `infra/iam/batch-gpu-roles.yaml` and nothing in this repository recorded which stack owned
  them, so a change to any one of the three began with a guess at its own stack name.
  Resolving each role's owning stack against the account on 2026-07-31 returned the same
  answer for all three — worth stating rather than assuming, because three roles in one
  template answering to different stacks would mean the template had been deployed twice
  under two names, and that is not what happened here.
- **Stack 3 is in this table before it exists, on purpose.** The bullet above is the reason:
  a template with no committed file naming its stack is what made a change to those three GPU
  roles begin with a guess, and the guess is removed by one line written before the first
  deploy rather than recovered from the account afterwards. It sits in the Phase 4 table
  because this is the section that learned that, not because anything in Phase 4 depends on
  it.

### Stack 1: the delete a retry needs, re-applied 2026-08-01

Laptop-applied, with `sbsandbox-intern-edullm-phase4-gpu-iam` as the stack name and
`infra/iam/batch-gpu-roles.yaml` as the template. Two statements changed on
`…-batch-gpu-workload`: an Allow of `s3:DeleteObject` on
`teams/*/runs/*/checkpoints/*`, and a Deny of the same action on
`teams/*/runs/*/checkpoints/*/.metadata.json`.

The reason is a run rather than a preference. `run_019fbe1f-b84f-703a-8eb8-2b4504232948`
lost its host at step 100 immediately after `checkpoints/step100/train/rank0.pt` was
written, leaving that one object. Attempt 2 resumed from `step50` correctly, trained back
to step 100, and died in `Checkpointer._prepare_dir` with `FileExistsError` on a directory
that is not empty. That is deterministic, so with two attempts a mid-write kill ends the
run.

The template's own comments carry the argument. Two facts about the account decide that
the grant is bounded rather than merely narrow, and both were read live on 2026-08-01: the
outputs bucket has versioning **Enabled**, and the role holds neither
`s3:DeleteObjectVersion` nor `s3:PutBucketVersioning`, so every delete it can make is a
delete marker over versions that stay. The `InternSandboxBoundary` permits it — the
boundary's only S3 statement is `Allow "*"` on `"*"`, and nothing in it denies an S3
action.

**Not re-captured.** `fixtures/evidence/phase-4/workload-role-scope.sanitized.json` still
records the role as observed on 2026-07-30, with `grants_delete: false`, and that is still
a true statement about that date. `tests/test_phase4_run_evidence.py` pins the capture's
date deliberately, so regenerating it is a separate change with its own reading.

**Applied twice, because it was reverted in between, and that is a property of this file
rather than an accident.** This template holds three roles, separate changes touch different
parts of it, and every one of them is applied from a laptop rather than from CI. The grant
went on at 17:11 UTC. At 17:53 UTC the same stack was updated from another worktree, on a
branch cut before the grant existed, and the workload role lost it. Nothing warned: the
change set only shows what that template says, and what it said about the delete was nothing
at all. The re-apply at 18:07 UTC restored it as the union of both changes, the two
statements above plus the three-repository ECR lists on the execution and instance roles that
the 17:53 update introduced. Those lists came from pull request 154, which has since merged,
so this file now carries both and the deployed template matches it byte for byte. Had 154
still been open, applying this file would have taken those lists back off and locked two
repositories out of the GPU queue.

The general point outlives this instance. A change set on this stack shows what the template
being applied says, not what it omits relative to what is live, so a stale template reverts a
grant silently. Two people applying this file from two worktrees will keep doing that until
the stack is applied from `main` rather than from a branch.

*Which tree you deploy from* above is now that rule, written where somebody about to run
`aws cloudformation deploy` will read it, and `tools/verify_deployed_stacks.py` is what
reports it the next morning when they do not. It names the resource and the statement rather
than saying the stack differs. Deleting the delete grant from the local copy of this template
and running the check against the account produces exactly this, on 2026-08-01:

```text
Resources.BatchGpuWorkloadRole.Properties.Policies[0].PolicyDocument.Statement[5]: only in the account ({"Action": "s3:DeleteObject", "Effect": "Allow", "Resource": {"Fn::Sub": "arn:${AWS::Partition}:s3:::sbsandbox-intern-edullm-outputs/teams/*/runs/*/checkpoints/*"}})
```

That is the incident mirrored, and mirrored deliberately: perturbing the template is the only
way to demonstrate the check without deliberately drifting a live stack, and it necessarily
runs the difference the other way. At 17:53 the account was the side that had lost the grant,
so the line would have read `only in main` and the repair would have been to re-apply from
`main` rather than to decide whether to adopt anything. The check distinguishes those two
directions because they are different jobs, which is the whole of what the message adds over
knowing that something differs.

What this does not claim is that an audit would have caught the 2026-08-01 revert. The gap
between 17:53 and 18:07 is fourteen minutes and the check runs at 05:00. What it ends is the
open-ended case: an account that differs from `main` and stays that way, unnoticed, until a
container fails on a credential months later.

### Stack 3: the dataset validator role, deployed 2026-08-01

Laptop-applied, like every IAM stack in this file. The command is the one under *Deploying
one IAM stack* above, with `sbsandbox-intern-edullm-dataset-validator-iam` as the stack name
and `infra/iam/dataset-validator-role.yaml` as the template.

The role is the identity a dataset owner's validator assumes instead of
`sbsandbox-intern-edullm-batch-workload`, the shared CPU workload role every team's
containers run as. The `edullm-validator` and `edullm-fsck` job definitions name it, the
exemption in `edullm-data`'s bucket policy names it, and the out-of-band `dataset-validator`
inline policy is gone from the shared role.

**This section previously said the deploy was waiting on a scheduled window with the
pipeline's operator watching the first event. That turned out to be unnecessary, and why is
worth keeping.** The reasoning was sound in shape: both EventBridge rules resolve their job
definition by unversioned name, so a new revision reaches production on the next event with
no review step, and the failure mode is a manifest that lands and is simply never promoted —
which raises nothing. What was missing was two live readings. The roles, the job definitions,
the queue and the rules are all in this account, so nothing needed anyone's permission; and
`edullm-landing-manifest-created` was **DISABLED**, so there was no first event to watch.
Waiting for an observer to see something that was not going to happen would have deferred the
exposure indefinitely.

What replaced the observer was a step that generates its own verification. Before anything
was taken away, a Batch job ran under the new role and exercised all six grants plus three
negative controls, proving the write with a multipart upload it immediately aborted —
authorizing exactly what a promotion authorizes, and leaving nothing behind in a bucket whose
policy forbids deletion. Every step was additive and separately reversible; the new role went
*onto* the bucket-policy exemption list beside the old one, so there was never a window in
which neither identity could write. `infra/iam/dataset-validator-role.yaml` carries the full
sequence beside the grants it is about.

The one thing that section used to flag as **not established** — whether `edullm-landing`
carries a bucket policy of its own — is established: it does not. `GetBucketPolicy` returned
`NoSuchBucketPolicy` on 2026-07-31 and again on 2026-08-01. Nothing there exempts principals
by name, so nothing there had to move when the identity changed.

The role is registered in `role_drift.DATASET_VALIDATOR_ROLE_TEMPLATES` and captured by
`tools/capture_phase3_evidence.py --target dataset-validator`, into
`fixtures/evidence/dataset-validator/roles/`. Its own directory rather than Phase 3's:
`read_committed_role_captures` reports a capture the registry does not declare as a finding,
so a directory belongs to exactly one registry.

### Stack 4: the run canceller role, deployed 2026-08-01

Laptop-applied, like every IAM stack in this file. The command is the one under *Deploying
one IAM stack* above, with `sbsandbox-intern-edullm-run-canceller-iam` as the stack name and
`infra/iam/run-canceller-role.yaml` as the template. `AWS_RUN_CANCELLER_ROLE_ARN` was set in
the same sitting: a deploy without the variable leaves `cancel-run.yml` refusing at its own
guard, which reads as a broken workflow rather than as half a step.

**Verified by stopping a real job, and nothing weaker would have done.** The template
reviewed fine, the deploy reported `CREATE_COMPLETE`, the role read back byte-identical to
what was committed, every test in the repository was green — and `batch:TerminateJob` was
refused, because its `ArnEquals` on `batch:JobQueue` could never be satisfied. `TerminateJob`
takes a job id and a reason and nothing else, so the queue is never in the request context.
The role described and listed and could not stop anything.

**Two things hid it, and both are worth carrying to the next role.** The denial reads
`no identity-based policy allows the batch:TerminateJob action`, which names a missing grant
rather than a condition that cannot match — so the message points at the statement that is
present and correct. And `iam simulate-principal-policy` does not separate the two either:
it accepts no `arn` context key type, so an `ArnEquals` is unsatisfiable in the simulator as
well, and it answers `implicitDeny` for grants that do work. A simulator run alone is
therefore evidence about the actions and not about their conditions.

The grant is now conditioned on `aws:ResourceTag/edullm:run-id` matching `run_*`, which is
the condition key Batch does offer on a job, and which selects the jobs this platform
submitted rather than the queues it created. `infra/iam/run-canceller-role.yaml` carries the
argument beside the statement.

The simulator was still used, for the half it does settle: `batch:DescribeJobs` and
`batch:ListJobs` allowed, and `batch:SubmitJob`, `batch:RegisterJobDefinition`,
`states:StartExecution` and `s3:GetObject` all implicit denies. The rest was measured by
submitting a CPU run for the purpose, dispatching `cancel-run.yml` against it in both modes,
and reading the termination back off the job — `statusReason` naming the actor and the
reason, which is what `lifecycle_projection` reads to tell a cancellation from a failure.

### Stack 5: the audit reader role, deployed 2026-08-01

Laptop-applied, like every IAM stack in this file. The command is the one under *Deploying
one IAM stack* above, with `sbsandbox-intern-edullm-audit-reader-iam` as the stack name and
`infra/iam/audit-reader-role.yaml` as the template.

**It exists because pinning a workflow file works.** Every OIDC role here fixes
`job_workflow_ref` with `StringEquals` to one file, which is what makes each role's reach
readable off the workflow that can assume it. The consequence is that a token minted for
`audit.yml` matches none of them, so the audit's checks that read the account had no
identity at all. Widening an existing role's condition to a list was the smaller diff and the
worse change: the admission role can start an execution and the canceller can stop any job on
either queue, and either would then sit behind a scheduled workflow nobody watches dispatch.

The role reads and writes nothing: the `intent/`, `result/` and `attempt/` prefixes of the
lineage store, the runs' own output under `teams/*/runs/*`, the one W&B secret, and the
deployed code digest of the two admission functions. Both listings carry a prefix condition,
because
`s3:ListBucket` cannot be scoped by an object ARN and without one it enumerates the whole
bucket. `tests/test_audit_workflow.py` asserts the granted action set exactly, so an action
added later is argued for in a test rather than merely not forbidden.

Verified after the deploy by `iam simulate-principal-policy` rather than by reading the
template back: `s3:ListBucket` on the lineage bucket is allowed with `s3:prefix` of `intent/`
and denied without one, `s3:GetObject` is allowed under `intent/` and denied under `result/`,
and `s3:PutObject`, `s3:DeleteObject`, `secretsmanager:PutSecretValue` and
`batch:TerminateJob` are all implicit denies. Simulating is what catches a prefix condition
that is subtly wrong, which reading the template cannot.

#### Amended 2026-08-01 for the deployed-Lambda check

`lambda:GetFunctionConfiguration` was added on the two function ARNs by name, for
`.github/workflows/audit.yml`'s `deployed-lambda-release` job. Re-applied with the same
command as the original deploy; CloudFormation updates the inline policy in place, so nothing
about the role's identity or its trust changed and no repository variable moved.

Simulated the same way and for the same reason. `lambda:GetFunctionConfiguration` is
`allowed` on `…:function:sbsandbox-intern-edullm-admission-validator` and on
`…:function:sbsandbox-intern-edullm-lifecycle-recorder`, and `lambda:UpdateFunctionCode` is an
`implicitDeny` on both. The adjacent action is the one worth simulating rather than an
unrelated one: a check that could deploy could answer its own finding by making the account
match the record, which is the wrong direction, and the two actions sit on the same ARNs so a
resource widened by accident shows up as the update becoming allowed.

#### Amended again 2026-08-01 for the deployed-stack check

`cloudformation:GetTemplate` was added for `.github/workflows/audit.yml`'s
`deployed-stack-templates` job, on twenty-one stack ARNs written out in full rather than on a
`stack/sbsandbox-intern-edullm-*` prefix. A template is the entire configuration of a stack,
which makes this the widest read the role holds and the one where a prefix would matter most:
a prefix quietly extends the grant to whatever a later phase deploys under a matching name,
and being unable to read a new stack is precisely how that stack gets reported instead of
absorbed. The trailing `/*` on each ARN is CloudFormation's stack id, a uuid chosen at
creation that cannot be predicted from the name, so it is unavoidable and reaches nothing
beyond the stack it names. The list is the same one `STACKS` declares in
`tools/verify_deployed_stacks.py`; `tests/test_audit_workflow.py` reads both and fails when
they diverge, so a stack added to one and not the other is caught at review rather than as a
denial at 05:00.

`cloudformation:ListStacks` was added on `Resource: "*"`, which is the only wildcard resource
in this policy and is forced rather than chosen. The action has no resource type at all — the
request names no stack, so there is nothing for a resource ARN to match and a policy that
names one denies the call outright. It is granted because it is what stops the check going
blind: the twenty-one names above are a list somebody wrote, so the stack that will not be on
it is the one somebody deploys next, and listing the account is how that stack becomes a
finding. What it discloses is names, statuses and timestamps — no template, no parameter, no
output — for every stack in this shared sandbox, and that is the price. `aws:RequestedRegion`
is the only narrowing the action admits and it is a small one; the region lock permits
`us-east-1` and `us-east-2` and everything this platform has is in the first.

Simulated after applying, with `aws:RequestedRegion` supplied as a context entry:
`cloudformation:GetTemplate` is `allowed` on the stack ARNs above and `implicitDeny` on
another team's stack in the same account; `cloudformation:ListStacks` is `allowed` in
`us-east-1` and `implicitDeny` in `us-east-2`, which is the region condition doing its work;
and `UpdateStack`, `DeleteStack`, `CreateStack`, `CreateChangeSet` and `ExecuteChangeSet` are
each an `implicitDeny` on a stack the role can read. Those five are the adjacent actions and
the reason for simulating them is the reason above: a check able to apply a template could
answer its own finding by reconciling the account, and reconciling is exactly the operation
that took the delete grant back on 2026-08-01. Fourteen of these stacks create roles, so it
would also be a role-creation path behind a scheduled workflow, which the first section of
this file is entirely about.

This one was applied from a branch rather than from `main`, which *Which tree you deploy from*
above otherwise forbids. A scheduled job cannot be merged and then granted its read, because
between the two it fails; the exception and its cost are recorded there.

#### Amended 2026-08-04 to make the `visibility-board` job capable of passing

Three statements, in one deploy, closing the two independent reasons that job had failed on
**every night since it shipped**. Either one alone produced a `SourceGap`, and a single gap is
`EXIT_UNUSABLE` and a red job, so fixing one of them would have changed nothing anybody could
see. Applied from `main` at its tip, which is what *Which tree you deploy from* requires and
what the 2026-08-01 amendment above had to make an exception to.

**`s3:GetObject` on `attempt/*`, and `attempt/*` added to the `ListLineageRecords` prefix
condition.** Two statements for one prefix, and the second is the one easy to leave off:
`s3:ListBucket` cannot be scoped by an object ARN, so the prefix condition is the whole
narrowing, and `aws s3 sync` lists before it fetches. A read granted on the object ARN and
missing from the listing condition reads as granted in a policy review and is refused at the
first call, with no object fetched.

The gap was two files stating one fact with nothing comparing them. `LINEAGE_PREFIXES` in
`tools/report_run_costs.py` is `("intent", "attempt")` and this policy granted `intent/` and
`result/`, so `tools/visibility_board.py` — the one caller of `sync_bucket` that runs on the
schedule, under this role — was refused on its second prefix every night. It did not lose the
attempt records alone: `sync_bucket` raises on a refused prefix rather than skipping it, so
the whole cost mapping came back `None` and every run on the board rendered as `not costed`.
The attempt records are the only place a run's measured duration exists, so nothing
substitutes for them. `tests/test_audit_workflow.py` now derives the required prefix set
from the workflow and from that constant and asserts the fetchable and listable sets are
equal, rather than restating either, so this pair cannot drift apart again without a red
review.

**`tag:GetResources` on `Resource: "*"`, region-conditioned.** The second wildcard resource in
this policy and forced for the same reason as the first: the action names no resource in the
request, so a policy naming one denies the call outright, and `aws:RequestedRegion` is the
only narrowing it admits. It is the wider disclosure of the two, answering with the ARNs and
tags of resources in this region, and it is what makes the account side of the visibility
board readable at all — without it every run in the account is trivially absent from the
account side, which the board reports as unanswered rather than reporting wrongly. There is
no substitute: enumerating the queues needs `batch:ListJobs` and `batch:DescribeJobs`, which
this role omits deliberately, and the lineage records say what this platform submitted rather
than what the account ran, which is the exact difference the board exists to report.

The statement is written character-for-character as `tools/visibility_board.py`'s
`MISSING_TAG_GRANT` quotes it, because that tool prints it into its own report as the thing to
paste when the read is refused. Two spellings would mean whoever pastes the report at 05:00
changes the role into something no test covers; `tests/test_visibility_board.py` compares the
two as parsed YAML and fails when they diverge. `tag:TagResources` and `tag:UntagResources`
are absent, so a board that decides what the account ran by reading tags cannot write the tag
that puts a run on its own report.

This also satisfies the `tag:GetResources` half of Task 6 of the instruments plan, which
wanted the same action on the same principal with the same region condition. That task's
`cloudtrail:LookupEvents` grant is **not** included: nothing in the tree reads launch events
yet, and it is a read of the account's whole management event history, which is wider than
anything else this role holds and should arrive with the tool that needs it.

The change set was read before it was executed, as *Deploying one IAM stack* above requires.
One entry: `Modify` on `AuditReaderRole`, scope `Properties`, target `Policies`,
`Replacement: False` and `RequiresRecreation: Never`. No replacement and no deletion, which is
what the read is for — an IAM role replaced rather than modified gets a new ARN and every
`role-to-assume` pointing at it stops resolving.

Simulated after applying, the way every amendment above is, because reading the template back
proves the template and simulating proves the grant. `s3:ListBucket` on the lineage bucket is
`allowed` with `s3:prefix` of `attempt/` and `implicitDeny` with no prefix supplied, so the
condition is still doing its work rather than having been widened; `s3:GetObject` is `allowed`
on an `attempt/` key. `tag:GetResources` is `allowed` in `us-east-1` and `implicitDeny` in
`us-east-2`. The adjacent actions are the ones worth simulating rather than unrelated ones:
`s3:PutObject` and `s3:DeleteObject` on the attempt prefix are each an `implicitDeny`, and so
are `tag:TagResources`, `tag:UntagResources` and `batch:ListJobs` — the last confirming there
is still no substitute read for the tagging grant.

**Proved end to end rather than by simulation alone.** `audit.yml` was dispatched by hand
against this commit. The `visibility-board` job had emitted two gaps on every night since it
shipped and now emits none: it reports 78 runs in the account with no W&B run and prices
`$60.47` of that from the attempt records, where every run previously read `not costed`. Both
figures are unobtainable without these grants, so each one is its own proof. The job still
exits 1 — that is `EXIT_DISAGREES`, the three records genuinely disagreeing, which is a run to
go and open rather than a grant to go and apply, and no IAM change closes it.

#### Renamed to the audit, and therefore replaced, 2026-08-05

The workflow, the role and the stack were called the nightly, which named the schedule
rather than the work. What the seven checks do is hold what the platform recorded against
what is actually there and go red when the two disagree, so the name is now the audit.

**This was a replacement rather than an update, and that is forced by IAM.** A role's name
is its physical id, so renaming it deletes the old role and creates a new one with a new
ARN. The stack name carried the old word too, so both moved together: the new stack was
created first and the old one deleted after, which means there was never a window with no
role and never two roles holding the same grants.

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-audit-reader-iam \
  --template-file infra/iam/audit-reader-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1

aws cloudformation delete-stack \
  --stack-name sbsandbox-intern-edullm-nightly-reader-iam \
  --profile sbsandbox --region us-east-1
```

No `--s3-bucket` here. `infra/iam/infra-deployer-role.yaml` needs one because it is past
CloudFormation's 51,200-byte limit for an inline template body, and this template is 34 KB.

**The trust policy and the workflow file had to land in the same change.** The condition is
`StringEquals` on `job_workflow_ref`, so a workflow renamed without the trust policy mints
a token that matches nothing, and a role renamed without the workflow leaves
`role-to-assume` pointing at an ARN that no longer exists. Renaming one alone is an access
denial at 05:00 with nothing in the message saying which half moved.

Three things kept the old name because they are records rather than labels.
`fixtures/evidence/phase-2/github/secrets.sanitized.json` says the repository held
`AWS_NIGHTLY_READER_ROLE_ARN` at 2026-08-02T14:30:06Z, which it did. The assertion in
`tests/test_phase2_github_evidence.py` holds that capture to itself. The lineage records
are untouched, as they are by everything.

### A hazard that has expired, and the one it does not take with it

Until `2026-07-31T07:21:51Z` this section would have had to carry a live warning: the deployed
`sbsandbox-intern-edullm-phase3-batch-iam` held a `secretsmanager:GetSecretValue` grant that
`main` had not yet declared, because the PR adding it to the execution role's inline policy —
PR #77 — had not merged. Deploying that stack from `main` would have reconciled the account
down to the template and silently taken back the grant a CPU job needs to read the W&B API
key, with no stack error to say so. PR #77 merged at the timestamp above; `main` (`8a444bf`)
now declares the grant itself, at `infra/iam/batch-roles.yaml:107-110`, and the account agrees
— confirmed 2026-07-31. That specific hazard is disarmed.

The grant sits on the **execution** role, never the workload role; the comment above it in
`infra/iam/batch-roles.yaml` (around lines 85–89) is explicit about why the two are not
interchangeable here, and this file will not repeat that argument only to get it slightly
wrong.

**The general hazard behind it has not expired, because nothing about *Why IAM is
laptop-only* above has changed.** Every stack in this file is applied by hand, so the account
can hold a grant `main` has not yet declared, and `aws cloudformation deploy` reconciles that
difference away without a stack error the moment somebody runs it from `main`. The symptom
surfaces inside a container much later — a call failing on a credential the role no longer
has — not as a deploy failure. That is why this paragraph lives beside the stack table rather
than beside one PR: it outlives whichever PR happens to close the gap it describes today.

That shape is not hypothetical, and the account has now produced two instances of it in four
days. Both are closed, and they were closed in opposite directions — which is the useful
part, because "adopt it" and "remove it" are both correct answers and the choice is about
whether the grant should exist, never about whether the template should agree with the
account.

- **Removed.** `sbsandbox-intern-edullm-batch-workload` carried an inline policy
  `dataset-validator` that no template declared, granting write and read across
  `edullm-data` and `edullm-landing`, attached out-of-band so a dataset owner's validator
  jobs could run under this role. It was removed on 2026-08-01 rather than adopted, because
  this is the role every CPU container on every team runs as: the grant was correct for a
  validator and wrong for everything else sharing the identity. Stack 3 above is where it
  went.
- **Adopted.** `sbsandbox-intern-edullm-batch-execution` carried an ECR repository —
  `sbsandbox-intern-edullm-data` — that no template declared, attached out-of-band on
  2026-07-31 so those same validator jobs could pull their image. It was adopted into
  `infra/iam/batch-roles.yaml` rather than removed, because an execution role that cannot
  pull the image its job definition names produces a job that never starts. This one was a
  **live hazard** in the exact sense this section describes: a `cloudformation deploy` of
  stack 1 from any tree without that line would have silently revoked it, and the symptom
  would have surfaced as somebody else's dataset promotion failing rather than as a deploy
  error.

Both were found the same way — by recapturing the deployed roles and comparing, not by
anybody noticing. The drift comparison reported `ok` for all four Phase 3 roles for the
first time since the first of them was attached.

"Not by anybody noticing" is the part that has since been addressed, though only for one of
the two shapes. `tools/verify_deployed_stacks.py` runs in the audit and would have reported
the adopted ECR repository, because a stack's deployed template is what it compares. It would not
have reported the removed inline policy: `dataset-validator` was attached to the role
directly, outside CloudFormation, so the stack's template never mentioned it and a template
comparison cannot see it. Reading the roles themselves, which is what
`tools/capture_phase1_evidence.py` and `edullm_platform.role_drift` do, remains the only thing
that catches an out-of-band attachment. The two checks answer different questions and neither
replaces the other.

**Not established:** whether an `aws cloudformation deploy` of `…-phase3-batch-iam` from
`main` today would strip `dataset-validator`. That was not tested, because testing it means
deploying the stack, so this file says only that reconciliation is the mechanism and not what
it would do to this particular policy. For contrast,
`sbsandbox-intern-edullm-batch-gpu-workload` holds exactly the one inline policy
`infra/iam/batch-gpu-roles.yaml:131` declares — account and template agree there too, also
measured 2026-07-31.

## Releasing a Lambda

Every function ships this way, and the procedure is the same for all of them.

| Function | Template | Builder | Artifact key |
| --- | --- | --- | --- |
| `…-admission-validator` | `infra/admission-state-machine.yaml` | `tools/build_admission_lambda.py` | `admission-validator/admission-validator.zip` |
| `…-lifecycle-recorder` | `infra/batch-events.yaml` | `tools/build_lifecycle_lambda.py` | `lifecycle-recorder/lifecycle-recorder.zip` |
| `…-expiry-janitor` | `infra/expiry-janitor.yaml` | `tools/build_janitor_lambda.py` | `expiry-janitor/expiry-janitor.zip` |
| `…-notifier` | `infra/notifications.yaml` | `tools/build_notifier_lambda.py` | `notifier/notifier.zip` |

### Releasing the admission validator

`infra/admission-state-machine.yaml` declares the function as
`Code: {S3Bucket, S3Key, S3ObjectVersion}`. Pinning the version is what makes a code
change a CloudFormation change: without it, a new zip under the same key leaves the
resource's properties byte-identical, the change set comes back empty, and
`deploy --no-fail-on-empty-changeset` reports success while the old code keeps running.

The cost is that a release is three steps and the third is a template edit. That edit is
deliberate — it is the diff a reviewer sees. CI does not do this, and giving CI the
ability to would remove the review.

The zip must be built for the runtime rather than for the machine building it. Pydantic
v2 ships a compiled `pydantic-core`, so a zip assembled from a macOS environment carries a
`.dylib`, and Lambda reports the result as a missing module rather than as an architecture
mismatch. `tools/build_admission_lambda.py` pins `x86_64-manylinux_2_28`, CPython 3.12 and
`--only-binary=:all:`, which is why it is used instead of `zip -r`.

```bash
uv run python tools/build_admission_lambda.py --output /tmp/admission-validator.zip

aws s3api put-object \
  --bucket sbsandbox-intern-edullm-artifacts \
  --key admission-validator/admission-validator.zip \
  --body /tmp/admission-validator.zip \
  --content-type application/zip \
  --profile sbsandbox --region us-east-1 \
  --query VersionId --output text
```

**`tools/release_lambda.py` does all three steps in one call, and should be preferred.**

```bash
uv run python tools/release_lambda.py            # every function
uv run python tools/release_lambda.py --dry-run  # digests only, uploads nothing
```

It builds, uploads, writes the version id and digest into both the release record and the
template, and then runs the tripwire test with its exit code read directly. It uploads every
zip before editing any file, because a record naming a zip that failed to upload is worse
than no record — the tripwire would then pass against a lie.

It selects every function by default, because working out which ones a change under
`src/edullm_platform` reaches is not something to be doing at the point of release. That is
free because **a function whose freshly built digest already matches its release record is
skipped** — nothing uploaded, neither file edited. Without that skip the default would store
byte-identical bytes under a fresh version id and put a Lambda nobody changed through a
stack update, which is why `--function validator` was passed by hand on 2026-08-04. Pass
`--force` to upload anyway, which is the repair for a record that is right about the bytes
while the object its version id names is not in the bucket.

That tool exists because the manual version failed on 2026-08-01: a release was cut,
recorded and pushed in one `&&` chain where a `pytest | tail` in the middle succeeded as a
*command* while the test inside it failed, so the chain continued and `main` landed with a
release record that did not describe the tree. The steps below are what it automates, kept
because a tool that cannot run is not a procedure.

Paste the version id it prints into `S3ObjectVersion` in
`infra/admission-state-machine.yaml`, commit it, and let CI deploy. The build is
deterministic, so re-running it on an unchanged tree produces the same bytes — if the
`sha256` it reports has not moved, there is nothing to release and the template does not
need editing.

Uploading an object is not applying a stack, so this one S3 write is a laptop step
without contradicting the rule above.

**The laptop is not the only place the upload can happen, and on 2026-08-01 it stopped
being a place it could happen at all.** `main` went red on the release tripwire — the zip
the tree built no longer matched the recorded one — and the credential broker rejected its
refresh token server-side. The only repair for a red `main` was behind a browser login, and
the deployer role had been sitting on `s3:PutObject` for the artifacts bucket the whole
time, granted in `infra/iam/infra-deployer-role.yaml` and never used for this.

So `deploy-phase2-admission.yml` takes a `release_lambdas` dispatch input. It builds both
zips, uploads them, and writes the version id and digest into the run summary:

```bash
gh workflow run deploy-phase2-admission.yml --ref main -f release_lambdas=true
```

**It uploads and prints; it does not edit or commit.** The two values still get pasted into
the template and the release record by hand in a reviewed pull request, so the property the
rule above protects — the deploy sits on the far side of a diff somebody read — is
unchanged. What moved is only which machine holds the credential.

Functions are released together whenever a change reaches more than one, which since
2026-08-04 means a change to `src/edullm_platform` rather than a change under `config/`.
Each builder names the config files its own handler reads: the validator's seven, none at
all for the recorder, and three for the notifier. A catalog edit reaches the validator and
the notifier and not the recorder.

The step lives inside an existing deploy workflow rather than in a `release-lambda.yml` of
its own, and that is the trust policy talking rather than a preference. The deployer role
pins `job_workflow_ref` to exactly three workflow files at `refs/heads/main`, so a new file
cannot assume the role until the policy names it — and amending the policy needs the AWS
access the step exists to replace. A new file is a door that only opens from inside.

One consequence to know before relying on it: the workflow can only run from `main`, so a
pull request that changes packaged bytes cannot be released before it merges. The release is
dispatched from `main` and a second small pull request carries the two values. That is a
worse sequence than releasing from the branch and is the right trade only while the laptop
path is unavailable.

**That sequence used to require an administrator, and no longer does.** The branch landed red
on the tripwire — it had to, since the zip could not be uploaded until the change was on
`main` and the change could not merge green while the tripwire was red — so every change to a
packaged module went in by merging past a required check. That happened twice on 2026-08-04
alone. A bypass taken routinely is a bypass nobody reads, and it is the same one that would
let a genuinely broken change through.

So `edullm_platform.pending_amendments` now carries a **pending release** beside the pending
IAM amendment it was written for, on the same terms. Record one in `pending_releases()`
naming the function, the digest this tree builds, the digest the release record still shows
as deployed, today's date, why the bytes moved, and the command that clears it. The tripwire
then skips with all of that printed, instead of failing.

It is narrow on purpose, and none of this weakens the release records themselves — they go on
tying a digest to a real S3 object version and describing what is deployed:

- a digest mismatch with **nothing recorded** still fails, loudly, exactly as before;
- the record stops fitting the moment **either** digest moves, so a second packaged edit
  arriving mid-review, or a release somebody else cut, fails rather than riding along;
- it lapses after seven days, checked by a test that needs no build and so runs on every
  `-m "not slow"` pass — a release nobody cut becomes visible rather than permanent;
- the release that clears it makes the entry fail, so the commit carrying the two values
  deletes it.

The failing tripwire prints the two digests to paste into an entry, so the escape hatch is
found by the person who needs it rather than looked up.

**The configuration the validator reads is inside the zip, so editing one of those files is
a release.** `tools/build_admission_lambda.py` copies the seven files named in
`ADMISSION_CONFIG` to `edullm_platform/config/`, because the validator has to read the
catalog it was reviewed against rather than whatever happens to be in a bucket when it
runs. That is the right property and it has a consequence worth stating plainly: a change
to `config/workload-catalog.yaml` or `config/execution-targets.yaml` changes nothing in the
account until this procedure is run.

The seven are `datasets.yaml`, `execution-targets.yaml`, `image-exceptions.yaml`,
`organization.yaml`, `policy.yaml`, `repositories.yaml` and `workload-catalog.yaml`.
`config/capacity.yaml` and everything under `config/reports/` are not packaged and are not
a release — nothing either function carries reads them.

Phase 4 paid for this. The GPU compute environment, queue, job definition and roles were
deployed and `VALID`, both config files agreed, every test was green — and the first GPU
submission was refused with `unprovisioned_compute_profile`, because the deployed zip
still held a catalog in which that profile was not provisioned. The refusal was correct
for the bytes that produced it and wrong about the account, which is the hardest kind to
read: it names the compute profile, not the release.

So the rule is: **promoting a compute profile, or changing anything else under `config/`
that admission reads, is a validator release.** Rebuild, upload, edit `S3ObjectVersion`,
and let CI deploy — before submitting anything that depends on the change.

**It used to be two releases, and since 2026-08-04 it is one.** `build_package` copied
`config/*.yaml` into whatever zip it was building and `tools/build_lifecycle_lambda.py`
called that same function, so a change to a config file moved the lifecycle recorder's
digest too, even though nothing the recorder does reads one. Renaming the four workload
profiles found it: the validator release was expected and planned for, the recorder's was
not, and its tripwire is what said so.

That is fixed at the source rather than by remembering. Each builder names the files its
own handler reads, so a config edit is the validator's release and the recorder's digest
does not move. What forced it was CODEOWNERS: eight team leads hold approval on
`/config/**` so that profile, workload, roster and dataset changes can move without the
owner, and a lead approving one still left a red required check that only somebody with AWS
credentials could clear. Two of those four files still require a validator release; none of
them requires a recorder release any more.

The deployer role needs `s3:ListBucketVersions` on the artifacts bucket for this to work,
and that is not obvious: Lambda fetches the versioned code object as the deploying
principal, and needs a bucket-level action as well as `s3:GetObjectVersion` on the object.
The first deploy failed on exactly that, with the stack rolling back and the retained log
group then blocking the retry until it was deleted by hand.

### Releasing the lifecycle recorder

Identical in shape, and the two are released independently — a change to the projection
logic does not require re-releasing the validator, and vice versa.

```bash
uv run python tools/build_lifecycle_lambda.py --output /tmp/lifecycle-recorder.zip

aws s3api put-object \
  --bucket sbsandbox-intern-edullm-artifacts \
  --key lifecycle-recorder/lifecycle-recorder.zip \
  --body /tmp/lifecycle-recorder.zip \
  --content-type application/zip \
  --profile sbsandbox --region us-east-1 \
  --query VersionId --output text
```

Paste the version id into `S3ObjectVersion` in `infra/batch-events.yaml`, commit it, and let
CI deploy. Same reason as the validator: without the version pinned, a new zip under the
same key leaves the resource's properties byte-identical, the change set comes back empty,
and `deploy --no-fail-on-empty-changeset` reports success while the old code keeps running.

Every function packages part of the same `src/edullm_platform` tree, so a contract change
may reach several and each then needs releasing, but only the modules each entry point
actually imports are carried, so many changes reach one and not the rest.
`tools/build_lifecycle_lambda.py` prints the `sha256` of what it built for exactly this
reason: if neither digest moved, there is nothing to release.

## Rotating the W&B API key

`sbsandbox-intern-edullm-wandb-api-key` is the credential every container on this platform
logs its runs through. It is written from a laptop and from nowhere else: rotation is not
enabled on the secret, and no workflow here holds `secretsmanager:PutSecretValue`.

Three principals read it, confirmed against the account on 2026-08-02 and worth knowing
before changing anything about it. `…-batch-execution` and `…-batch-gpu-execution` inject it
into a container at task start, and they are trusted to `ecs-tasks.amazonaws.com`, so no
person and no workflow can assume either. `…-audit-reader` is trusted to
`audit.yml@refs/heads/main` and holds the read so the audit can ask W&B whether the
stored value is one it would accept. Nothing else in the account reaches it by name.

**Dispatching the audit is part of the rotation, not a follow-up to it.**

```bash
aws secretsmanager put-secret-value \
  --secret-id sbsandbox-intern-edullm-wandb-api-key \
  --secret-string file:///path/to/the/key \
  --profile sbsandbox --region us-east-1

gh workflow run audit.yml --ref main
```

`--secret-string file://…` rather than the value on the command line, and the file must hold
the key and nothing else. The fault this platform has actually paid for was a good key with
the literal word `api` glued to the front, which is what pasting W&B's netrc line as one
token produces: the right length, the right shape at a glance, and refused by W&B. Nothing
went red, because a training run does not fail when W&B declines it — it trains, logs
nowhere, and dies later with `ProcessGroup is not registered`.

The second command is what closes the window this rotation opens.
`.github/workflows/submit-run.yml` refuses a submission on the strength of the verdict
`audit.yml` publishes, because no identity the submit path can obtain holds the read —
`infra/iam/admission-role.yaml` argues that at length beside the grant it declines. So
between writing a value and the next verdict, the preflight is reading the answer to a
question about the value before this one. The schedule closes that within a day and the
dispatch closes it within a minute.

The same command is the repair when the preflight refuses a submission and the key has
already been fixed. A verdict of "refused" is honoured however old it is, deliberately, so
the only thing that clears one is a newer measurement.

## The one-off role the conditional-write probe needs

`infra/admission-state-machine.yaml` catches `States.ALL` around each lineage write and
says why: nobody has observed what Step Functions calls S3's refusal of a write carrying
`IfNoneMatch: "*"`, and guessing the name would produce a `Catch` that never fires.
`tools/probe_conditional_write.py` measures it. It creates its own throwaway bucket and
state machine and deletes both, but it deliberately does not create the execution role the
state machine runs as — a probe that mints IAM roles is a role-creation path nobody
reviews.

The obvious candidate does not fit: `sbsandbox-intern-edullm-admission-states` can write
only to `sbsandbox-intern-edullm-lineage/*`, and pointing the probe at the real lineage
bucket would put probe objects in the store the whole phase is about. So the probe needs a
role of its own. There is no committed template for it, on purpose — it exists for one
measurement and is deleted afterwards.

Beyond the role lifecycle in the prerequisites, the laptop identity needs
`iam:PassRole` for the role below, `s3:CreateBucket`, `s3:PutObject`, `s3:GetObject`,
`s3:DeleteObject` and `s3:DeleteBucket` on `sbsandbox-intern-edullm-conditional-write-*`,
and `states:CreateStateMachine`, `states:StartExecution`, `states:DescribeExecution` and
`states:DeleteStateMachine`. A denial partway through leaves the probe tearing down what
it managed to create and exiting 2, which is survivable but wastes the round trip.

Create the role by hand:

```bash
account="$(aws sts get-caller-identity --profile sbsandbox --region us-east-1 \
  --query Account --output text)"

aws iam create-role \
  --role-name sbsandbox-intern-edullm-conditional-write-probe \
  --permissions-boundary "arn:aws:iam::${account}:policy/InternSandboxBoundary" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  --profile sbsandbox --region us-east-1

aws iam put-role-policy \
  --role-name sbsandbox-intern-edullm-conditional-write-probe \
  --policy-name write-probe-objects \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:PutObject","Resource":"arn:aws:s3:::sbsandbox-intern-edullm-conditional-write-*/*"}]}' \
  --profile sbsandbox --region us-east-1
```

Review the run before making it, then make it:

```bash
uv run python tools/probe_conditional_write.py \
  --aws-profile sbsandbox \
  --aws-region us-east-1 \
  --state-machine-role-name sbsandbox-intern-edullm-conditional-write-probe \
  --output docs-frank/working/phase-2-evidence/conditional-write.json \
  --dry-run
```

Exit 0 means the second write was refused and the record names the error; exit 1 means it
was not, which is the answer that keeps `States.ALL` where it is; exit 2 means the probe
could not run, or could not delete something it created — read the stderr lines beginning
`teardown_incomplete:` and clear whatever they name before doing anything else.

Delete the role when the measurement is recorded. An inline policy has to go first:

```bash
aws iam delete-role-policy \
  --role-name sbsandbox-intern-edullm-conditional-write-probe \
  --policy-name write-probe-objects \
  --profile sbsandbox --region us-east-1

aws iam delete-role \
  --role-name sbsandbox-intern-edullm-conditional-write-probe \
  --profile sbsandbox --region us-east-1
```

## Renaming revokes trust, silently

Two things are pinned with `StringEquals` in role trust policies, which means an exact
string match with no wildcard. A list under one `StringEquals` key is an OR across its
elements, so several exact values are allowed and nothing outside the list is.

- **The workflow file path.** `token.actions.githubusercontent.com:job_workflow_ref`
  carries a full path and ref. `infra/iam/infra-deployer-role.yaml` lists
  `deploy-phase1-ecr.yml@refs/heads/main`,
  `deploy-phase2-admission.yml@refs/heads/main` and
  `deploy-phase3-batch.yml@refs/heads/main`; `infra/iam/ecr-publisher-role.yaml` pins
  `build-research-image.yml@refs/heads/main`; `infra/iam/admission-role.yaml` pins
  `submit-run.yml@refs/heads/main`; `infra/iam/audit-reader-role.yaml` pins
  `audit.yml@refs/heads/main`. Renaming or moving any of those files revokes that
  role's deployments. The last of those is the one this has actually happened to.
  `nightly.yml` became `audit.yml` on 2026-08-05, and the trust policy, the role name and
  the stack name moved in the same change for exactly the reason below.
- **The GitHub environment.** `infra/iam/admission-role.yaml` pins the subject claim to
  `…:environment:run-approval-lead` and `…:environment:run-approval-admin`, because GitHub
  puts `:environment:<name>` in the subject of a job that declared an `environment:` and
  cleared its protection rules. Renaming either environment in the repository settings, or
  removing it from a job's `environment:` key, has the same effect as renaming a workflow
  file — and it is worse in one way, since an environment named in a workflow is
  auto-created on first use with no protection rules at all. That is why the two names are
  enumerated rather than matched with a wildcard.

Neither failure looks like what it is. The workflow reaches
`aws-actions/configure-aws-credentials`, STS refuses the web identity, and the run fails
on a credentials step with nothing pointing at the rename. Nothing warns beforehand: IAM
does not know the string it is matching corresponds to a file or an environment, and
GitHub does not know the string is in a trust policy.

If you rename either, change the trust policy in the same change and re-deploy the IAM
stack from a laptop first. A rename merged on its own leaves `main` broken until somebody
with SSO credentials is available.

### One more name with the same silence, which is not about trust

**The Batch job queue.** `sbsandbox-intern-edullm-cpu` appears in three places that no
CloudFormation reference connects: the state machine's `SubmitToBatch` parameters, the
EventBridge rule's `detail.jobQueue` pattern, and the states role's `batch:SubmitJob`
resource scope. Renaming the queue in `infra/batch-compute.yaml` without the other two is
worse than a trust rename, because it half-works: submission keeps succeeding against the
new queue while the rule matches nothing, so jobs run and no lifecycle event, attempt or
result record is ever written. The run looks fine in Batch and vanishes from lineage.

`tests/test_phase3_infrastructure.py` compares the three against each other for this
reason. It is the one seam in this phase whose failure produces no error anywhere.

## The Phase 5 stacks, in dependency order

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-phase5-image-resolver-iam` | `infra/iam/image-resolver-role.yaml` | `…-image-resolver` | laptop |
| 2 | `sbsandbox-intern-edullm-phase2-admission-service-roles` | `infra/iam/admission-service-roles.yaml` (amended) | `…-admission-states` gains `batch:RegisterJobDefinition` and the `iam:PassRole` that call needs | laptop |
| 3 | `sbsandbox-intern-edullm-phase2-admission` | `infra/admission-state-machine.yaml` (amended) | the `RegisterJobDefinition` state | CI |

Same rule as Phases 2 and 3: **every laptop stack goes before every CI stack**, and within
the laptop group the order above is the one that works.

- **Stack 1 has no order to get wrong, and that is worth saying rather than leaving to be
  inferred.** It creates one role that no other principal passes, that grants no `iam:`
  action of its own, and that GitHub assumes directly, so it can be applied before or after
  anything else in this file. The argument does not extend to the two rows below it.
- **Stack 2 must precede stack 3, and this is the ordering that bites.** The state machine
  calls `batch:RegisterJobDefinition` as `…-admission-states`, and that state sits on the
  accepted branch between resolving the execution target and submitting the job. Deploying
  stack 3 first produces a state machine whose states are all correct and whose first
  accepted submission is refused with an access denial — after the intent and decision
  records have been written, at a state whose name says nothing about IAM. It reads like a
  broken state machine and is not one: it is a template deployed ahead of the grant it
  depends on, which is the same shape as deploying Phase 2's CI stacks before the deployer
  carried `deploy-phase2-admission-stacks`.
- **`iam:PassRole` is the half of stack 2 to read rather than skim.** `RegisterJobDefinition`
  passes an execution role and a workload role — that call is where a container's two
  identities are fixed — so the grant names all four Batch role ARNs in full, one pair per
  backed compute profile. IAM does not require the resource of a grant to exist, and
  `infra/iam/batch-roles.yaml` and `infra/iam/batch-gpu-roles.yaml` created those four in
  Phase 3 and Phase 4, so nothing here has to be created first. Promoting a fifth profile
  does mean amending this stack, and `tests/test_phase5_infrastructure.py` compares the
  grant against `config/execution-targets.yaml` so that the amendment is a red test rather
  than a refused submission.
- **One grant is missing from stack 2 and this file will not guess at it.** `batch:SubmitJob`
  authorizes against the job definition a submission names as well as against the queue, and
  the definition it now names is the per-run one — `sbsandbox-intern-edullm-<run id>` — which
  the submit scope does not list. On the reading of the IAM model in the template's own
  comments that denies every accepted submission at `SubmitToBatch`, with a 403 naming the
  job definition: the same denial `batch:TagResource` produced on the first run through the
  whole path, and it fails closed into a `submission-failure/` record rather than launching
  anything. It has not been measured against the account and no test in this repository can
  reach IAM to settle it. Read this as an instruction to check before submitting, not as a
  default. Closing it means adding one job-definition pattern to the `batch:SubmitJob` and
  `batch:TagResource` scopes — narrow enough to keep both queues and both deployed
  definitions listed in full, which is the property the template's comment about prefixes is
  actually protecting — and it is left out of this change because widening the only principal
  in the account that may start compute is a decision to take deliberately rather than in
  passing.
- **Stack 3 is a validator release as well as a template deploy.** The handler now returns a
  registration request beside the submit request, and
  `src/edullm_platform/admission_handler.py` is inside the zip
  `tools/build_admission_lambda.py` packages — so a state machine deployed against the
  previous zip reads `$.execution.register_request` from a payload that has no such key and
  fails at `States.Runtime`, which is precisely the failure the second validator release was
  bought with. Follow *Releasing the admission validator* above and land the new
  `S3ObjectVersion` in the same change; `tests/test_released_zips.py` fails until the
  released zip is the one this tree builds, which is the tripwire Phase 4 did not have when
  it needed it.

### Stack 1: the image resolver role

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-phase5-image-resolver-iam \
  --template-file infra/iam/image-resolver-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox \
  --region us-east-1
```

Then read the role back, as after every deploy above:

```bash
aws iam get-role \
  --role-name sbsandbox-intern-edullm-image-resolver \
  --profile sbsandbox --region us-east-1

aws iam list-attached-role-policies \
  --role-name sbsandbox-intern-edullm-image-resolver \
  --profile sbsandbox --region us-east-1
```

`AttachedPolicies` must be empty: this template attaches no managed policy, so anything
listed there was added outside CloudFormation. The role's one inline policy grants exactly
`ecr:DescribeImages` and `ecr:DescribeImageScanFindings`, and that exactness is the entire
reason the role may be assumed before a submission has been approved — the template's
comments carry the argument.

Which roles the drift comparison reads is a registry per phase in
`src/edullm_platform/role_drift.py`, and this role is in `PHASE5_ROLE_TEMPLATES`. Same rule
as Phase 2: a role that is not listed there is compared to nothing, and adding it is part of
shipping the role rather than a follow-up.

### Stack 2: the grants the register state runs as

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-phase2-admission-service-roles \
  --template-file infra/iam/admission-service-roles.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox \
  --region us-east-1
```

**The stack name is the Phase 2 one and stays the Phase 2 one.** This is an amendment to a
stack that already exists rather than a new stack, and the note under the Phase 2 table says
what a second name costs: two stacks trying to create the same two role names, the second
of which fails.

Then read the policy back, which for an amendment matters more than reading the role does —
what changed is a document inside it:

```bash
aws iam get-role-policy \
  --role-name sbsandbox-intern-edullm-admission-states \
  --policy-name run-admission-workflow \
  --profile sbsandbox --region us-east-1
```

Eight statements, and the two new ones are `batch:RegisterJobDefinition` scoped to
`job-definition/sbsandbox-intern-edullm-*` and `iam:PassRole` naming four whole role ARNs.
Check the second by eye: a prefix where those four ARNs should be is the difference between
a state machine that may hand a container the two identities this repository reviewed and
one that may hand it any role a later phase happens to name.

## The preview stack, which belongs to no phase

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-run-preview-iam` | `infra/iam/run-preview-role.yaml` | `…-run-preview` | laptop |

Not numbered into a phase because it adds no capability to the platform. It exists so that a
change *to* the platform can be exercised before it is merged, which is a property of how
this repository is worked on rather than of what it runs.

**What it is for.** Every other role trusted to a workflow here pins its subject to
`refs/heads/main`. `submit-run.yml` dispatched from a branch therefore fails in its second
job, at the credential step, before anything is compiled and before any gate is reached — so
the submission path was the one path that could not be tried until it was already on `main`.
The trust condition on this role is the `run-approval-preview` environment subject instead of
a ref, and `submit-run.yml` routes a non-`main` dispatch to that environment and this role.

**Two trust statements, because the preview path mints two subject shapes.** The `submit`
job declares `environment: run-approval-preview` and is issued an environment subject. The
`resolve` job must declare no environment at all — on `main` it assumes `…-image-resolver`,
whose trust is pinned to `:ref:refs/heads/main`, so an environment key there would break the
production path this role exists to preview. So the second statement accepts a *ref* subject,
`refs/heads/*` with `refs/heads/main` subtracted by name under `StringNotEquals`. That
subtraction is the load-bearing half: without it a dispatch from `main` could pick this role
up out of the job that is supposed to be holding the image resolver. One statement cannot do
both jobs — `StringEquals` on the environment literal and `StringLike` on the ref pattern
would be ANDed against the same `sub` and match nothing.

**What stops it being a way around the gates.** One queue and two reads. `batch:SubmitJob` on
`sbsandbox-intern-edullm-cpu` and its job definition, plus `ecr:DescribeImages` and
`ecr:DescribeImageScanFindings` — and nothing else at all: no GPU queue, no `states:`, no
`s3:`, no `iam:PassRole`, no `secretsmanager:`, no `ecr:` action that pulls. The admission
states role enumerates sixteen queues; this one enumerates the cheapest, which is the entire
ceiling on what a branch can spend. `tests/test_run_preview_role.py` asserts the action set
and the queue exactly, so widening either is a red test rather than a quiet edit.

The two ECR reads are the image resolver's two reads, action for action and resource for
resource, and a test asserts they match rather than asserting a literal list twice. That is
deliberate in both directions: a branch dispatch exists to exercise what `main` will do, so a
narrower grant here would mean a resolve that succeeds on a branch and fails on `main`, or
the reverse.

**Read the template's comments before changing the trust policy.** `job_workflow_ref` is
`StringLike` here and `StringEquals` everywhere else, and only the ref part is wild: the
workflow file is still pinned on both statements, so the role is unreachable from any other
workflow. The environment name is enumerated as a single literal for the same reason
`infra/iam/admission-role.yaml` enumerates its three — a `StringLike` on `:environment:*`
would accept the subject minted for any environment a workflow author invented, because an
environment named in a workflow is auto-created on first use with no protection rules.

### Deploying it

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-run-preview-iam \
  --template-file infra/iam/run-preview-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox \
  --region us-east-1
```

Then read the role back, as after every deploy above:

```bash
aws iam get-role \
  --role-name sbsandbox-intern-edullm-run-preview \
  --profile sbsandbox --region us-east-1

aws iam list-attached-role-policies \
  --role-name sbsandbox-intern-edullm-run-preview \
  --profile sbsandbox --region us-east-1
```

`AttachedPolicies` must be empty. The one inline policy must name exactly three actions —
`batch:SubmitJob` over three ARNs that all contain `sbsandbox-intern-edullm-cpu`, and
`ecr:DescribeImages` with `ecr:DescribeImageScanFindings` over the repository wildcard. Any
ARN with `gpu` in it means the ceiling is gone and the role should be deleted rather than
amended.

**Two things do not follow from this deploy and have to be done beside it.** The
`run-approval-preview` environment has to exist in the repository settings with no required
reviewers and a deployment branch policy of `*`; an environment named in a workflow and not
created deliberately is auto-created with no protection rules, which is a different thing
that happens to share the name. And `AWS_RUN_PREVIEW_ROLE_ARN` has to be set as a repository
variable, the way `AWS_ADMISSION_ROLE_ARN` and `AWS_IMAGE_RESOLVER_ROLE_ARN` already are.

**How the `resolve` gap was closed, and why this way.** The `resolve` job assumed
`…-image-resolver`, whose trust pins `refs/heads/main`, so a branch dispatch reached the
preview environment and did not get past `resolve` — a preview that could submit but could
not resolve was a preview of nothing. Of the two ways to close it, adding the preview subject
to `infra/iam/image-resolver-role.yaml` was refused: widening a production credential is
worse than scoping a new one, because the image resolver's blast radius already covers
everything it reaches on `main` and a branch subject would extend that permanently, where
this role's reach is bounded by its own trust policy and stays bounded. So the two describes
are here instead, and `resolve` picks its role by ref.

**The job was not split, only the credential expression was, and that is the deliberate
half.** GitHub Actions has no YAML anchors, so a second `resolve` job means a second copy of
its steps — and the copy is then what a branch dispatch exercises, which defeats previewing
altogether: a change to that job would go untested by the very path built to test it. One
job, one `role-to-assume` expression, and `github.ref` chooses the arm. On `main` the
expression is `vars.AWS_IMAGE_RESOLVER_ROLE_ARN` and the job is what it was.

**What a preview dispatch still is not.** It submits to Batch directly rather than through
admission, so it validates no manifest, records no decision and writes no lineage. That is
the property that keeps a preview result uncitable, and it is what the requirement below is
about.

### A requirement for the mismatch filter, which is not built yet

**The filter does not exist in code.** It is described in the system overview and nothing
here computes it: no tool reads CloudTrail launches against lineage, and the twenty-row table
of `Intern-*` role names it joins on is not in `config/organization.yaml` either. This is a
note for whoever writes both. The word "mismatch" in `tools/visibility_board.py` is a
different thing entirely — it means two cost sources disagreeing — and reusing it there was
not this.

**Recorded here rather than in `config/organization.yaml`, which is where it belongs.** The
join table goes in that file and a note beside it would be the obvious place. It cannot go
there: the whole `config/` directory is copied into the admission Lambda zip, and both
released zips are byte-identical by construction, so *any* edit under `config/` — including a
comment — changes both digests and turns the suite red until somebody rebuilds and releases
from a laptop. Adding a note there would have meant an AWS deploy to land a comment.

**What the filter is.** A mismatch is a launch by a roster principal with no lineage record:
CloudTrail says what launched and who launched it, lineage says what this platform knows
about, and the gap is computed without anyone's cooperation. The key is the role name in the
launch event's session issuer, joined to a roster login through that table.

**The requirement.** `sbsandbox-intern-edullm-run-preview` must be excluded from that filter,
under three constraints that are not negotiable separately from it:

1. **Exclude that one name, never a pattern or a prefix.** Not `*-run-preview`, not the
   project-wide role prefix. A pattern silently swallows the next role that happens to match
   it, and the filter's whole value is that it is a named key rather than an intention — the
   same property the join table has, for the same reason.
2. **Count it and print it, never drop it silently.** The morning message prints its
   denominator so that zero-because-clean and zero-because-broken do not look alike. A
   preview launch is excluded from the mismatch count and reported beside it as its own
   figure — "N preview launches, excluded" — because an exclusion nobody can see is
   indistinguishable from a filter that broke.
3. **A test that fails if the exclusion widens**, pinned to the single role name.

**Why it is excluded, which decides the shape of the fix.** A preview job is by construction
the exact thing the filter looks for: a roster principal launching compute with no lineage
record. So do **not** close this by giving preview jobs a lineage record. "No lineage record"
staying true is what keeps a preview result uncitable — visibly absent from the store rather
than forged into it — and that property is worth more than a simpler filter. Every preview
job would otherwise land in the morning message as a correct-behavior entry, which is how a
monitoring surface becomes noise nobody reads, defeating the one instrument that catches real
off-platform spend.

`tests/test_run_preview_role.py` pins this record to the role's actual name, so a rename
cannot leave a note that reads fine and excludes nothing.

## The researcher lane stacks, which belong to no phase

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-researcher-iam` | `infra/iam/researcher-role.yaml` | `edullm-researcher` | laptop |
| 2 | `sbsandbox-intern-edullm-audit-reader-iam` | `infra/iam/audit-reader-role.yaml` (amended) | one more `cloudformation:GetTemplate` ARN | laptop |

**Not deployed as of 2026-08-05.** The templates are merged and the stacks do not exist;
`tools/verify_deployed_stacks.py` reports the first as declared and not deployed every night
until somebody applies it. That is the check working, and it is the honest state to be in
rather than a row this table omits.

**The role name has no `sbsandbox-intern-edullm-` prefix**, unlike every other role in this
file, because `docs-frank/reference/system-overview.md` and
`docs-frank/reference/aws-spend-controls.md` both name it `edullm-researcher` and a person
types it. Two consequences: the deployer's resource scopes key on the long prefix and therefore
cannot touch it, which is correct because no pipeline may; and the boundary's
`DenyTamperingWithInternRoles` matches `role/Intern-*` and does not cover it either.

**The boundary permits the unprefixed name, simulated 2026-08-05 before spending a deploy on
finding out.** `iam:CreateRole`, `iam:PutRolePolicy`, `iam:TagRole` and `iam:DeleteRole` on
`role/edullm-researcher` all answer `allowed` for `Intern-frank.gonzalez-sbsandbox`. The
enumerated denies carry no name-prefix restriction, and this is that expectation measured.

**The simulation has to carry `iam:PermissionsBoundary` in the request context or it answers
the wrong question.** Without that context entry `iam:CreateRole` comes back `explicitDeny`,
and the deny is about the boundary being absent from the request rather than about the name —
the same rule that makes a template omitting `PermissionsBoundary` fail rather than create a
weaker role. A simulation run without it sends the reader to the boundary owner for a grant
they already have. The negative control is worth keeping: substituting any other policy ARN for
the boundary returns `explicitDeny`, so the check can still fail.

**Its trust policy cannot be simulated and has to be proved by a second person.**
`simulate-custom-policy` does not evaluate trust policies at all, so the `ArnLike` on
`aws:PrincipalArn`, the `aws:RequestTag` conditions, `sts:SetSourceIdentity` on a chained call
and role chaining from a web-identity session are all unproven by the roughly hundred and
twenty simulations behind the permission policy. Whoever created the role would still pass a
self-assumption test against a trust policy that accidentally granted only its creator.
`docs-frank/reference/aws-spend-controls.md`, "The live test plan", step 3 is the one that
cannot be skipped.

### Applying them

Both from a clean `main` at its tip, per *Which tree you deploy from* above.

```bash
git fetch origin main
git status --porcelain          # must print nothing
git rev-parse HEAD origin/main  # must print the same commit twice

# Before spending a deploy on finding out whether the unprefixed name is permitted. The
# --context-entries line is not optional: without it CreateRole answers explicitDeny about a
# missing boundary rather than about the name. Answered `allowed` on all three, 2026-08-05.
account="$(aws sts get-caller-identity --profile sbsandbox --region us-east-1 --query Account --output text)"
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::${account}:role/Intern-frank.gonzalez-sbsandbox" \
  --action-names iam:CreateRole iam:PutRolePolicy iam:TagRole \
  --resource-arns "arn:aws:iam::${account}:role/edullm-researcher" \
  --context-entries "ContextKeyName=iam:PermissionsBoundary,ContextKeyType=string,ContextKeyValues=arn:aws:iam::${account}:policy/InternSandboxBoundary" \
  --profile sbsandbox --region us-east-1 \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}'

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-researcher-iam \
  --template-file infra/iam/researcher-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-audit-reader-iam \
  --template-file infra/iam/audit-reader-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1
```

Then read the role back. `PermissionsBoundary` must be `InternSandboxBoundary`, `PolicyNames`
must be exactly `["lane"]`, and `AttachedPolicies` must be empty — this template attaches no
managed policy, so anything listed there was added outside CloudFormation.

```bash
aws iam get-role --role-name edullm-researcher --profile sbsandbox --region us-east-1
aws iam list-role-policies --role-name edullm-researcher --profile sbsandbox --region us-east-1
aws iam list-attached-role-policies --role-name edullm-researcher --profile sbsandbox --region us-east-1
```

### Capturing it, so something compares the account to the tree

`tests/test_researcher_role_template.py` reads the template, which is a claim about what the
account will be asked for rather than a description of what it holds, and it stays green
against a role nobody deployed. The capture is the half that closes that distance, and it uses
the machinery Phase 1, Phase 2, Phase 3 and the dataset validator already use.

The capture tool refuses any `--output-dir` outside `docs-frank/working/phase-3-evidence`, and
that directory is local-only, so the capture is taken there, read, and copied in — the sequence
*Verifying after each deploy* above describes. It takes no `--environment`.

```bash
mkdir -p docs-frank/working/phase-3-evidence
uv run --frozen python tools/capture_phase3_evidence.py \
  --aws-profile sbsandbox \
  --home-region us-east-1 \
  --target researcher-role \
  --output-dir docs-frank/working/phase-3-evidence

mkdir -p fixtures/evidence/researcher-lane/roles
cp docs-frank/working/phase-3-evidence/edullm-researcher.sanitized.json \
   fixtures/evidence/researcher-lane/roles/
```

Run before the stack is applied it prints `aws_call_failed:iam:get-role`, which is the target
wired up correctly against a role that does not exist yet. That was the state on 2026-08-05.

The tool prints `"verdict": "ok"` and `"drift_findings": 0` when the account and the template
agree. A non-zero finding count on a first deploy means one of the two is wrong and the repair
is fixing whichever, not recording an amendment: an amendment is for a change that has merged
and not deployed.

`tests/test_researcher_deployed_role.py` is what reads that capture, and it is deliberately not
in the tree until the capture is. A test that skipped when its evidence was missing would be
green on an account holding nothing, which is the shape of check this repository has removed
six times.

### Proving the trust policy, which needs a second person

`simulate-custom-policy` cannot evaluate a trust policy at all, so nothing above says whether
the `ArnLike`, the two `aws:RequestTag` presence tests or `sts:SetSourceIdentity` work. Whoever
created the role would pass a self-assumption test against a trust policy that accidentally
granted only its creator, so this is run by a roster member who holds an `Intern-*` role and is
not the person who applied the stack. The list is in `docs-frank/reference/who-has-what.md`.

```bash
aws sts assume-role \
  --role-arn "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/edullm-researcher" \
  --role-session-name lane-trust-check \
  --source-identity "$(aws sts get-caller-identity --query Arn --output text | sed 's#.*/##; s#^broker-##; s#-[0-9]*$##')" \
  --tags Key=project,Value=trust-check Key=lifetime,Value=1 \
  --duration-seconds 900 \
  --query 'AssumedRoleUser.Arn' --output text
```

Expected: an assumed-role ARN. Then the two negative controls, which are the half that makes
the first mean something — the same call with `--tags` omitted, and again with
`--source-identity` omitted. Both must return `AccessDenied`. Record all three outcomes here.

## The expiry janitor's stacks

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-janitor-iam` | `infra/iam/janitor-lambda-role.yaml` | `sbsandbox-intern-edullm-janitor-lambda`, `sbsandbox-intern-edullm-janitor-schedule` | laptop |
| 2 | `sbsandbox-intern-edullm-infra-deployer-iam` | `infra/iam/infra-deployer-role.yaml` (amended) | two `iam:PassRole` ARNs and the `scheduler:*` verbs | laptop |
| 3 | `sbsandbox-intern-edullm-audit-reader-iam` | `infra/iam/audit-reader-role.yaml` (amended) | two `cloudformation:GetTemplate` ARNs and one `lambda:GetFunctionConfiguration` | laptop |
| 4 | `sbsandbox-intern-edullm-janitor` | `infra/expiry-janitor.yaml` | the function and its five-minute schedule | CI, `deploy-phase3-batch.yml` |

**Not deployed as of 2026-08-05, and the fourth cannot be until the first two are.** The
workflow step that applies it skips while `infra/expiry-janitor.yaml` still pins the
never-uploaded placeholder object version, so an unrelated dispatch is not broken by a stack
nobody can create yet. `tests/test_janitor_package.py` refuses to let that guard outlive the
placeholder.

**The deployer amendment must precede the CI deploy.** Until the deployer carries `iam:PassRole`
on the two janitor roles, `lambda:CreateFunction` is refused and the failure reads like a broken
template rather than a missing grant. The `scheduler:*` verbs are the same story one resource
later.

### Why the schedule is EventBridge Scheduler and not an `AWS::Events::Rule`

The obvious wiring for a five-minute sweep is a rule targeting the function. That needs an
`AWS::Lambda::Permission`, which needs `lambda:AddPermission` on the deploying principal, and
this deployer withholds that action in as many words: *the deployer creates the validator but
may neither run it nor change who may run it*. `infra/batch-events.yaml` met the same fork in
Phase 3 and recorded the rule for it — a capability added rather than a restriction removed.

So the schedule assumes a role it is passed. What the deployer gains is `iam:PassRole` on
`sbsandbox-intern-edullm-janitor-schedule`, a role trusted to `scheduler.amazonaws.com` alone
and holding `lambda:InvokeFunction` on one function named in full. That is strictly narrower
than the grant it avoids: `lambda:AddPermission` would let a deploy credential write
`Principal: "*"` into the janitor's resource policy, and this cannot.

### Applying them

```bash
git fetch origin main
git status --porcelain          # must print nothing
git rev-parse HEAD origin/main  # must print the same commit twice

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-janitor-iam \
  --template-file infra/iam/janitor-lambda-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-infra-deployer-iam \
  --template-file infra/iam/infra-deployer-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-audit-reader-iam \
  --template-file infra/iam/audit-reader-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1
```

Then simulate what was granted and, more usefully, what was not.

```bash
account="$(aws sts get-caller-identity --profile sbsandbox --region us-east-1 --query Account --output text)"

aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::${account}:role/sbsandbox-intern-edullm-janitor-lambda" \
  --action-names ec2:StopInstances ec2:TerminateInstances ec2:RunInstances s3:PutObject \
  --resource-arns "arn:aws:ec2:us-east-1:${account}:instance/i-0123456789abcdef0" \
  --profile sbsandbox --region us-east-1 \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}'
```

Expect `implicitDeny` on all four, because the resource carries no tags and the `StopInstances`
grant is conditioned on two of them. That is the condition working, and it is also why a
simulator run alone is evidence about the actions and not about their conditions — *Stack 4*
above records a role that simulated correctly and could stop nothing. The conditions are proved
by the drill against a real tagged instance.

### Releasing the code object, which is what unblocks the CI deploy

```bash
uv run --frozen python tools/release_lambda.py --function janitor --dry-run
uv run --frozen python tools/release_lambda.py --function janitor
```

That builds, uploads, writes the version id and digest into both the template and the release
record, and runs the tripwire with its exit code read directly. The placeholder guard in
`.github/workflows/deploy-phase3-batch.yml` has to come out in the same change — the tripwire
holds the token in all three files or in none, so it will say so.

Then dispatch and confirm:

```bash
gh workflow run deploy-phase3-batch.yml --ref main
aws lambda get-function-configuration \
  --function-name sbsandbox-intern-edullm-expiry-janitor \
  --profile sbsandbox --region us-east-1 \
  --query '{Handler:Handler,Runtime:Runtime,CodeSha256:CodeSha256}'
aws scheduler get-schedule \
  --name sbsandbox-intern-edullm-expiry-sweep \
  --profile sbsandbox --region us-east-1 \
  --query '{State:State,Schedule:ScheduleExpression}'
uv run --frozen python tools/verify_deployed_lambdas.py --profile sbsandbox --region us-east-1
```

`CodeSha256` is base64 and the release record is hex, so the last command is how they are
compared rather than by eye.

## The notifier

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-notifier-iam` | `infra/iam/notifier-lambda-role.yaml` | `…-notifier-lambda` | laptop |
| 2 | `sbsandbox-intern-edullm-infra-deployer-iam` | `infra/iam/infra-deployer-role.yaml` (amended) | `…-infra-deployer` gains one `iam:PassRole` ARN | laptop |
| 3 | `sbsandbox-intern-edullm-notifications` | `infra/notifications.yaml` | queue, dead-letter queue, function, event source mapping, three alarms | laptop first, CI after |

The order is not a preference. `lambda:CreateFunction` takes a role ARN and fails on a role
that does not exist or that the caller may not pass, so stack 3 needs both of the stacks above
it. The deployer amendment is a single ARN added to the Phase 3 `iam:PassRole` statement,
which is what lets `lambda:CreateFunction` in stack 3 pass the role from stack 1. Without it
the deploy fails at `CreateFunction` with an `AccessDenied` naming `PassRole`, which reads
like a broken template.

Stack 3 is applied from a laptop the first time because the function's zip has to be uploaded
and its object version pinned in the template before CloudFormation can create it, and because
adding a target to a rule the lifecycle recorder depends on is a change worth watching happen.
After that `deploy-phase3-batch.yml` owns it, before the events stack for the reason that
workflow's comment gives.

### The secret, which is created by hand and is in no template

A Slack incoming webhook carries its whole credential in the URL path, so a Lambda environment
variable holding one is plaintext in the template, in the console and in
`get-function-configuration`, and this repository is public. It cannot be a repository secret
either, because a repository secret is readable by a workflow on any branch and
`test_the_repository_holds_no_secret_a_branch_could_read` forbids one by name.

**It already exists.** `sbsandbox-intern-edullm-runs-webhook` was created by hand on
2026-08-05 and points at `#edullm-runs`. Its ARN ends `-wL0CYM`, which is the six-character
suffix Secrets Manager appends at creation. This is the command that made it, with the URL
redacted, kept because a procedure nobody can repeat is not one:

```bash
aws secretsmanager create-secret \
  --name sbsandbox-intern-edullm-runs-webhook \
  --description "Webhook the notifier posts run-ended messages to. Created by hand so the URL is in no template. A Slack incoming webhook carries its whole credential in the URL path." \
  --secret-string 'https://REPLACE-WITH-THE-WEBHOOK-URL' \
  --profile sbsandbox --region us-east-1
```

### Rotating it, which needs no deploy and no code change

```bash
aws secretsmanager put-secret-value \
  --secret-id sbsandbox-intern-edullm-runs-webhook \
  --secret-string 'https://REPLACE-WITH-THE-NEW-WEBHOOK-URL' \
  --profile sbsandbox --region us-east-1
```

That is the whole of it. `put-secret-value` keeps the ARN, so the role's grant still matches
and no stack changes, and the handler reads the secret on **every invocation** rather than
caching it in a warm container, so the next run to end uses the new URL.
`test_the_webhook_is_read_again_on_every_invocation` is what holds it to that, because the
caching version of this fails invisibly: Slack answers a retired webhook with a 404 rather
than a timeout, so a warm container would go on dead-lettering quietly against a URL somebody
believed they had already replaced.

**Do not delete and recreate it.** That mints a new six-character suffix, and the role's grant
names the current one exactly rather than ending in `-*`. Recreating means editing
`infra/iam/notifier-lambda-role.yaml` and applying the role stack by hand again.

### A second reader is coming, and it is not granted here

The instruments work reuses this same secret rather than creating a second webhook, so a
second stack will name this same ARN in a role of its own. That is the intended shape: one
secret, one grant per principal, each written in the stack that owns the principal. Nothing in
this stack grants it, and a second stack touching this secret's ARN is not a duplicate to be
tidied away.

### Two grants that will look odd to a later reader

**The notifier reads one prefix of the lineage store.** `s3:GetObject` on
`sbsandbox-intern-edullm-lineage/intent/*`, and no listing of that bucket. Every message names
the person who submitted the run, and the Batch event does not carry one: its `tags` key holds
a resource ARN, and the only person-shaped value in the envelope is `WANDB_USERNAME`, recorded
for thirty of the thirty-five members. `IntentRecord.submitter` is a GitHub login and
`config/organization.yaml` carries `github_login` for all thirty-five. The run id is the job
name the event already carries, so the key is derived and nothing has to be searched for,
which is why there is no `s3:ListBucket` here.

`attempt/` was considered and refused, and the reason is timing rather than policy: the
lifecycle recorder writes those records in answer to the same event that starts the notifier,
from a second target on the same rule, so a notifier reading them finds nothing. The other six
prefixes answer questions no message asks.

**The notifier asks Batch what a fan-out's cells cost.** `batch:ListJobs` on `Resource: "*"`
under an `aws:RequestedRegion` condition. An array parent's terminal event carries the array
size and a status summary and an empty attempts array, so the authorised ceiling is derivable
from it and the spend is not. The cells' own windows are on the `ListJobs` summary, as
`startedAt` and `stoppedAt`, and there is no race on reading them because Batch moves an array
parent to a terminal status only once every child already is.

`batch:ListJobs` has no resource type, so `"*"` is the narrowest resource that exists and the
region condition is the whole of the bound. That argument was settled for this account by
[#227](https://github.com/edu-llm/platform/pull/227), which granted the same action to
`sbsandbox-intern-edullm-nightly-reader` under the same condition; it is not restated here.
This role takes half of what that one took: no `batch:DescribeJobs`, because the intent record
above already answers the person from a narrower grant, and no `SubmitJob`, `CancelJob` or
`TerminateJob`, because a component that says what happened must not be able to make something
happen.

### Deploying it

Stacks 1 and 2, in that order. The deployer template is past CloudFormation's 51,200-byte
inline limit, so it needs `--s3-bucket` and the role stack does not.

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-notifier-iam \
  --template-file infra/iam/notifier-lambda-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-infra-deployer-iam \
  --template-file infra/iam/infra-deployer-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --s3-bucket sbsandbox-intern-edullm-artifacts \
  --s3-prefix cloudformation-templates/checksummed \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1
```

The second is an update to a stack CI depends on. Read the change set summary before it
applies and confirm the only difference is the one `Resource` entry.

Then ask the account what the role may actually do, rather than trusting the template. A
template that deployed is not the same fact as a grant that evaluates, because
`InternSandboxBoundary` caps every role created here.

```bash
ROLE=arn:aws:iam::$(aws --profile sbsandbox sts get-caller-identity --query Account --output text):role/sbsandbox-intern-edullm-notifier-lambda

aws iam simulate-principal-policy \
  --policy-source-arn "$ROLE" \
  --action-names s3:GetObject \
  --resource-arns arn:aws:s3:::sbsandbox-intern-edullm-lineage/intent/run_019fd3cc-79a0-70f5-aa29-6db4a2061a61.json \
  --query 'EvaluationResults[].EvalDecision' --output text \
  --profile sbsandbox --region us-east-1

aws iam simulate-principal-policy \
  --policy-source-arn "$ROLE" \
  --action-names batch:ListJobs batch:DescribeJobs batch:TerminateJob \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output text \
  --profile sbsandbox --region us-east-1
```

`allowed` for the object read, `allowed` for `batch:ListJobs`, and `implicitDeny` for both
`batch:DescribeJobs` and `batch:TerminateJob`. The last two are the point of running this: a
role that answers `allowed` to either has a statement wider than the one that was reviewed.

Then stack 3. Build, upload, pin, apply, and apply the events stack after it.

```bash
uv run python tools/build_notifier_lambda.py --output /tmp/notifier.zip

aws s3api put-object \
  --bucket sbsandbox-intern-edullm-artifacts \
  --key notifier/notifier.zip \
  --body /tmp/notifier.zip \
  --content-type application/zip \
  --profile sbsandbox --region us-east-1 \
  --query VersionId --output text
```

Put that version id into `S3ObjectVersion` in `infra/notifications.yaml` and into
`s3_object_version` in `infra/notifier-release.yaml`, in place of
`REPLACE_WITH_THE_UPLOADED_OBJECT_VERSION` in both. The `sha256` in the release record is
already the digest this tree builds, so it needs editing only if the tree has moved since.

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-notifications \
  --template-file infra/notifications.yaml \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1

aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-phase3-events \
  --template-file infra/batch-events.yaml \
  --no-fail-on-empty-changeset \
  --profile sbsandbox --region us-east-1
```

The order matters and EventBridge will not tell you if you get it wrong. It does not check
that an SQS target exists at `PutTargets` time, so the events stack applied first deploys
perfectly and drops every notification until somebody notices a channel that has gone quiet.

### Verifying after the deploy

```bash
aws events list-targets-by-rule \
  --rule sbsandbox-intern-edullm-batch-lifecycle \
  --query 'Targets[].Id' --output json \
  --profile sbsandbox --region us-east-1

uv run python tools/verify_deployed_stacks.py --profile sbsandbox --region us-east-1
uv run python tools/verify_deployed_lambdas.py --function notifier --profile sbsandbox --region us-east-1
```

The rule must carry `["lifecycle-queue", "notifier-queue"]`. Both verifications must report no
findings. A digest mismatch on the second means the uploaded zip is not the one this tree
builds: rebuild, re-upload, re-pin, redeploy, and do not edit the release record to match the
account.

**Until all three stacks are applied, the nightly audit reports them as declared and not
deployed.** That is the check working rather than breaking, and it is the list of what is
outstanding.

### Read the name on the first message, not just the line

The submitter comes from the intent record and the fallback comes from the envelope, and both
produce a plausible name, so a refused `s3:GetObject` looks exactly like a working one for
anybody on the roster with a W&B account recorded. It stops naming the five who have none, and
nothing else changes.

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/sbsandbox-intern-edullm-notifier \
  --filter-pattern AccessDenied --query 'length(events)' --output text \
  --profile sbsandbox --region us-east-1
```

Anything other than `0` means every message is coming from the fallback.
## The exploration route's stacks, in dependency order

| # | Stack | Template | Roles or resources | Applied from |
| --- | --- | --- | --- | --- |
| 1 | `sbsandbox-intern-edullm-scratch` | `infra/scratch-bucket.yaml` | the `edullm-scratch` bucket | laptop |
| 2 | `sbsandbox-intern-edullm-lane-instance-iam` | `infra/iam/lane-instance-role.yaml` | `edullm-lane-instance` and its instance profile | laptop |

Both from a laptop, and the first one is not an IAM stack.
`sbsandbox-intern-edullm-infra-deployer` scopes every S3 grant it holds to
`arn:aws:s3:::sbsandbox-intern-edullm-*`, and the bucket is `edullm-scratch`, so CI is denied at
`CreateBucket`. Widening that scope is itself a hand-applied IAM change, to let a pipeline create
one bucket that is created once.

Stack 1 goes first because stack 2 grants against the bucket's ARN. IAM does not require the
resource of a grant to exist, so the order is not forced, and creating the target first costs
nothing and getting it wrong is hard to see.

### Deploying stack 1, the bucket

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-scratch \
  --template-file infra/scratch-bucket.yaml \
  --profile sbsandbox \
  --region us-east-1
```

Then read the account back rather than the template:

```bash
aws s3api get-bucket-lifecycle-configuration \
  --bucket edullm-scratch \
  --profile sbsandbox --region us-east-1

aws s3api get-public-access-block \
  --bucket edullm-scratch \
  --profile sbsandbox --region us-east-1
```

Two rules must come back, `expire-working-objects` and `abort-incomplete-multipart-uploads`, and
all four public-access flags must be true. No `--capabilities` flag, because the stack creates no
named IAM resource.

### Deploying stack 2, the lane instance role

```bash
aws cloudformation deploy \
  --stack-name sbsandbox-intern-edullm-lane-instance-iam \
  --template-file infra/iam/lane-instance-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile sbsandbox \
  --region us-east-1
```

`CAPABILITY_NAMED_IAM` rather than `CAPABILITY_IAM`, because the role and the instance profile are
both named and CloudFormation refuses a named IAM resource without it.

Then read the role back, as after every deploy above:

```bash
aws iam get-role \
  --role-name edullm-lane-instance \
  --profile sbsandbox --region us-east-1

aws iam list-attached-role-policies \
  --role-name edullm-lane-instance \
  --profile sbsandbox --region us-east-1

aws iam get-instance-profile \
  --instance-profile-name edullm-lane-instance \
  --profile sbsandbox --region us-east-1
```

`PermissionsBoundary` must name `InternSandboxBoundary`. `AttachedPolicies` must hold exactly
`AmazonSSMManagedInstanceCore` and nothing else, because a second attachment on the one principal
nobody reviews is the widening least likely to be noticed. The instance profile must carry the
role, and the profile name must be the one `run_instances_argv` passes as
`--iam-instance-profile Name=`; a mismatch there fails a launch after a machine has been priced.
Any bucket other than `edullm-scratch` in the inline policy means the machine reaches past the
working tier and the role should be narrowed rather than left.
