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
  `deploy-phase1-ecr.yml@refs/heads/main` and
  `deploy-phase2-admission.yml@refs/heads/main`; `infra/iam/ecr-publisher-role.yaml` pins
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
