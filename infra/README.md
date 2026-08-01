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
grant CI needs before its first scheduled run — the nightly reader's
`cloudformation:GetTemplate` was applied from a branch on 2026-08-01 for exactly that
reason — and the cost of the exception is a window in which the account is ahead of `main`
and the check below reports it. Merge promptly and the window closes.

**What catches it when somebody forgets is `tools/verify_deployed_stacks.py`**, which the
nightly workflow runs as the `deployed-stack-templates` job. It reads each deployed stack's
template out of CloudFormation, holds it against the file in `main` that declares it, and
names the resource and the property that differ. It reads which template belongs to which
stack from `STACKS` in that file, and a stack the account holds that `STACKS` does not name
is reported rather than skipped, so a stack somebody deploys next is a finding and not a
blind spot. Adding a stack therefore means adding three things in the same change: the
entry in `STACKS`, the stack's ARN in the `cloudformation:GetTemplate` grant in
`infra/iam/nightly-reader-role.yaml`, and the row in the phase table below. Run it by hand
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
  `tools/`, not in `proof/phase-1/`, and not in any commit message. It was recovered from
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
| 5 | `sbsandbox-intern-edullm-nightly-reader-iam` | `infra/iam/nightly-reader-role.yaml` | `…-nightly-reader` | laptop |

Stacks 4 and 5 each need a repository variable as well as a deploy:
`AWS_RUN_CANCELLER_ROLE_ARN` for stack 4, which `.github/workflows/cancel-run.yml` reads,
and `AWS_NIGHTLY_READER_ROLE_ARN` for stack 5, which `.github/workflows/nightly.yml` reads.
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

What this does not claim is that a nightly would have caught the 2026-08-01 revert. The gap
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

### Stack 5: the nightly reader role, deployed 2026-08-01

Laptop-applied, like every IAM stack in this file. The command is the one under *Deploying
one IAM stack* above, with `sbsandbox-intern-edullm-nightly-reader-iam` as the stack name and
`infra/iam/nightly-reader-role.yaml` as the template.

**It exists because pinning a workflow file works.** Every OIDC role here fixes
`job_workflow_ref` with `StringEquals` to one file, which is what makes each role's reach
readable off the workflow that can assume it. The consequence is that a token minted for
`nightly.yml` matches none of them, so the nightly checks that read the account had no
identity at all. Widening an existing role's condition to a list was the smaller diff and the
worse change: the admission role can start an execution and the canceller can stop any job on
either queue, and either would then sit behind a scheduled workflow nobody watches dispatch.

The role reads and writes nothing: the `intent/` and `result/` prefixes of the lineage store,
the runs' own output under `teams/*/runs/*`, the one W&B secret, and the deployed code digest
of the two admission functions. Both listings carry a prefix condition, because
`s3:ListBucket` cannot be scoped by an object ARN and without one it enumerates the whole
bucket. `tests/test_nightly_workflow.py` asserts the granted action set exactly, so an action
added later is argued for in a test rather than merely not forbidden.

Verified after the deploy by `iam simulate-principal-policy` rather than by reading the
template back: `s3:ListBucket` on the lineage bucket is allowed with `s3:prefix` of `intent/`
and denied without one, `s3:GetObject` is allowed under `intent/` and denied under `result/`,
and `s3:PutObject`, `s3:DeleteObject`, `secretsmanager:PutSecretValue` and
`batch:TerminateJob` are all implicit denies. Simulating is what catches a prefix condition
that is subtly wrong, which reading the template cannot.

#### Amended 2026-08-01 for the deployed-Lambda check

`lambda:GetFunctionConfiguration` was added on the two function ARNs by name, for
`.github/workflows/nightly.yml`'s `deployed-lambda-release` job. Re-applied with the same
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

`cloudformation:GetTemplate` was added for `.github/workflows/nightly.yml`'s
`deployed-stack-templates` job, on twenty-one stack ARNs written out in full rather than on a
`stack/sbsandbox-intern-edullm-*` prefix. A template is the entire configuration of a stack,
which makes this the widest read the role holds and the one where a prefix would matter most:
a prefix quietly extends the grant to whatever a later phase deploys under a matching name,
and being unable to read a new stack is precisely how that stack gets reported instead of
absorbed. The trailing `/*` on each ARN is CloudFormation's stack id, a uuid chosen at
creation that cannot be predicted from the name, so it is unavoidable and reaches nothing
beyond the stack it names. The list is the same one `STACKS` declares in
`tools/verify_deployed_stacks.py`; `tests/test_nightly_workflow.py` reads both and fails when
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
anybody noticing. `proof/phase-3/deployed-role-drift.md` reports `ok` for all four Phase 3
roles for the first time since the first of them was attached.

"Not by anybody noticing" is the part that has since been addressed, though only for one of
the two shapes. `tools/verify_deployed_stacks.py` runs nightly and would have reported the
adopted ECR repository, because a stack's deployed template is what it compares. It would not
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

Two functions now ship this way, and the procedure is the same for both.

| Function | Template | Builder | Artifact key |
| --- | --- | --- | --- |
| `…-admission-validator` | `infra/admission-state-machine.yaml` | `tools/build_admission_lambda.py` | `admission-validator/admission-validator.zip` |
| `…-lifecycle-recorder` | `infra/batch-events.yaml` | `tools/build_lifecycle_lambda.py` | `lifecycle-recorder/lifecycle-recorder.zip` |

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
uv run python tools/release_lambda.py            # both functions
uv run python tools/release_lambda.py --dry-run  # digests only, uploads nothing
```

It builds, uploads, writes the version id and digest into both the release record and the
template, and then runs the tripwire test with its exit code read directly. It releases both
functions by default, because a config edit moves both digests. It uploads every zip before
editing any file, because a record naming a zip that failed to upload is worse than no
record — the tripwire would then pass against a lie.

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

Both functions are released together, because one config edit is two releases:
`build_package` copies `config/*.yaml` into whatever zip it builds and
`tools/build_lifecycle_lambda.py` calls the same function, so editing a catalog moves the
lifecycle recorder's digest even though nothing the recorder does reads a catalog.

The step lives inside an existing deploy workflow rather than in a `release-lambda.yml` of
its own, and that is the trust policy talking rather than a preference. The deployer role
pins `job_workflow_ref` to exactly three workflow files at `refs/heads/main`, so a new file
cannot assume the role until the policy names it — and amending the policy needs the AWS
access the step exists to replace. A new file is a door that only opens from inside.

One consequence to know before relying on it: the workflow can only run from `main`, so a
pull request that changes packaged bytes cannot be released before it merges. Such a branch
lands red on the tripwire, the release is dispatched from `main`, and a second small pull
request carries the two values. That is a worse sequence than releasing from the branch and
is the right trade only while the laptop path is unavailable.

**The configuration is inside the zip, so editing `config/` is a release.**
`tools/build_admission_lambda.py` copies `config/*.yaml` to `edullm_platform/config/`,
because the validator has to read the catalog it was reviewed against rather than whatever
happens to be in a bucket when it runs. That is the right property and it has a
consequence worth stating plainly: a change to `config/workload-catalog.yaml` or
`config/execution-targets.yaml` changes nothing in the account until this procedure is
run.

Phase 4 paid for this. The GPU compute environment, queue, job definition and roles were
deployed and `VALID`, both config files agreed, every test was green — and the first GPU
submission was refused with `unprovisioned_compute_profile`, because the deployed zip
still held a catalog in which that profile was not provisioned. The refusal was correct
for the bytes that produced it and wrong about the account, which is the hardest kind to
read: it names the compute profile, not the release.

So the rule is: **promoting a compute profile, or changing anything else under `config/`
that admission reads, is a validator release.** Rebuild, upload, edit `S3ObjectVersion`,
and let CI deploy — before submitting anything that depends on the change.

**And it is two releases, not one.** `build_package` in `tools/build_admission_lambda.py`
copies `config/*.yaml` into whatever zip it is building, and
`tools/build_lifecycle_lambda.py` calls that same function — so a change to a config file
moves the lifecycle recorder's digest as well, even though nothing the recorder does reads
the catalog. Renaming the four workload profiles found this: the validator release was
expected and planned for, the recorder's was not, and its tripwire is what said so. Both
release procedures below have to be run for one edit to `config/`, and the table at the top
of this section lists the two functions precisely so the second is not forgotten.

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

Both functions package the same `src/edullm_platform` tree, so a contract change reaches
both and both need releasing. `tools/build_lifecycle_lambda.py` prints the `sha256` of what
it built for exactly this reason: if neither digest moved, there is nothing to release.

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
  `submit-run.yml@refs/heads/main`. Renaming or moving any of those files revokes that
  role's deployments.
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
  `S3ObjectVersion` in the same change; `tests/test_phase2_lambda_package.py` fails until the
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
