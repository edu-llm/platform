import fnmatch
from typing import Any

import pytest
from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    IAM_ROOT,
    OIDC_PROVIDER,
    PROJECT_ROOT,
    iam_roles,
    load_template,
    resource_of_type,
    statement_actions,
    walk_strings,
)

TEMPLATE_PATH = IAM_ROOT / "infra-deployer-role.yaml"

ROLE_NAME = "sbsandbox-intern-edullm-infra-deployer"
#: One inline policy per phase, in the order the template declares them. Separate
#: policies rather than more statements in one, so deploy-phase1-stacks still grants
#: exactly what it granted when it was reviewed and a phase boundary is visible in
#: `aws iam list-role-policies`.
PHASE1_POLICY_NAME = "deploy-phase1-stacks"
PHASE2_POLICY_NAME = "deploy-phase2-admission-stacks"
PHASE3_POLICY_NAME = "deploy-phase3-batch-stacks"
DECLARED_POLICY_NAMES = [PHASE1_POLICY_NAME, PHASE2_POLICY_NAME, PHASE3_POLICY_NAME]

#: The two this module reads statements out of, which is deliberately not all three. Every
#: resource inventory and action list below was written against the scopes Phase 1 and
#: Phase 2 were reviewed with, and folding Phase 3's Batch, EC2, SQS and EventBridge grants
#: into them would turn each of those pinned lists into a list that grows every phase --
#: which is the shape of an inventory nobody reads.
#:
#: What must still be true of the *whole* role is asserted rather than dropped, in
#: tests/test_phase3_deployer_role.py: that iam:PassRole is the only IAM action anywhere in
#: it, that no role-mutating action appears in any policy, and that it can never run what it
#: builds. Those three read all three policies, because a claim about them that stopped at a
#: phase boundary would be a claim about a third of a role.
POLICY_NAMES = [PHASE1_POLICY_NAME, PHASE2_POLICY_NAME]

REPOSITORY = "edu-llm/platform"
MAIN_BRANCH_REF = "refs/heads/main"
#: One workflow file per phase deploys through this role. Each is pinned by exact path, so
#: each has to keep the name spelled here.
DEPLOY_WORKFLOWS = (
    ".github/workflows/deploy-phase1-ecr.yml",
    ".github/workflows/deploy-phase2-admission.yml",
    ".github/workflows/deploy-phase3-batch.yml",
)
JOB_WORKFLOW_REFS = [f"{REPOSITORY}/{workflow}@{MAIN_BRANCH_REF}" for workflow in DEPLOY_WORKFLOWS]
SUBJECT = "repo:edu-llm@306859726/platform@1311508598:ref:refs/heads/main"

#: Only the edullm segment makes a name ours; sbsandbox-intern- alone is the whole
#: account's prefix, and every intern's stacks and buckets begin with it.
SHARED_PREFIX = "sbsandbox-intern-"
RESOURCE_PREFIX = f"{SHARED_PREFIX}edullm-"

REGIONAL_ARN = "arn:${AWS::Partition}:%s:${AWS::Region}:${AWS::AccountId}:%s"
BUCKET_ARN = "arn:${AWS::Partition}:s3:::%s"
ROLE_ARN = "arn:${AWS::Partition}:iam::${AWS::AccountId}:role/%s"

STACK_RESOURCE = {"Fn::Sub": REGIONAL_ARN % ("cloudformation", f"stack/{RESOURCE_PREFIX}*/*")}
REPOSITORY_RESOURCE = {"Fn::Sub": REGIONAL_ARN % ("ecr", f"repository/{RESOURCE_PREFIX}*")}
BUCKET_RESOURCE = {"Fn::Sub": BUCKET_ARN % f"{RESOURCE_PREFIX}*"}
#: The one bucket this role reaches by its whole name rather than through the project prefix.
#: Spelled with the partition pseudo-parameter like every other ARN in the template, which
#: costs nothing: CloudFormation resolves it before IAM sees the document, so the policy IAM
#: stores is the same bytes either way.
SCRATCH_BUCKET_ARN = {"Fn::Sub": "arn:${AWS::Partition}:s3:::edullm-scratch"}
ARTIFACT_OBJECT_RESOURCE = {"Fn::Sub": BUCKET_ARN % f"{RESOURCE_PREFIX}artifacts/*"}
#: The artifacts bucket itself, not its objects. Lambda's fetch of a versioned code
#: object needs a bucket-level action, and it is scoped here rather than folded into the
#: prefix-wide bucket statement so it cannot reach the lineage store.
ARTIFACT_BUCKET_RESOURCE = {"Fn::Sub": BUCKET_ARN % f"{RESOURCE_PREFIX}artifacts"}
STATE_MACHINE_RESOURCE = {"Fn::Sub": REGIONAL_ARN % ("states", f"stateMachine:{RESOURCE_PREFIX}*")}
FUNCTION_RESOURCE = {"Fn::Sub": REGIONAL_ARN % ("lambda", f"function:{RESOURCE_PREFIX}*")}
LOG_GROUP_RESOURCE = {
    "Fn::Sub": REGIONAL_ARN % ("logs", f"log-group:/aws/vendedlogs/states/{RESOURCE_PREFIX}*")
}
#: Two whole role ARNs, never a prefix. Written out here for the same reason they are
#: written out in the template: a name that has to be typed twice cannot grow a wildcard
#: on one side only.
#: Every role this deployer may hand to a service, named in full. The last two are the expiry
#: janitor's: ``lambda:CreateFunction`` takes the sweep role, and the schedule role is what
#: replaced a grant this file refuses to make -- an ``AWS::Events::Rule`` targeting a Lambda
#: would need ``lambda:AddPermission``, which the Phase 2 policy withholds, so the schedule
#: assumes a passed role instead. ``infra/batch-events.yaml`` recorded the rule for that fork.
PASS_ROLE_RESOURCES = [
    {"Fn::Sub": ROLE_ARN % f"{RESOURCE_PREFIX}admission-states"},
    {"Fn::Sub": ROLE_ARN % f"{RESOURCE_PREFIX}admission-lambda"},
    {"Fn::Sub": ROLE_ARN % f"{RESOURCE_PREFIX}janitor-lambda"},
    {"Fn::Sub": ROLE_ARN % f"{RESOURCE_PREFIX}janitor-schedule"},
]
#: Everything the role can reach, in the order the template scopes it. An inventory
#: rather than a property, so a resource added anywhere fails here and is read by a
#: person rather than by a pattern.
SCOPED_RESOURCES = [
    STACK_RESOURCE,
    REPOSITORY_RESOURCE,
    BUCKET_RESOURCE,
    SCRATCH_BUCKET_ARN,
    ARTIFACT_OBJECT_RESOURCE,
    ARTIFACT_BUCKET_RESOURCE,
    STATE_MACHINE_RESOURCE,
    FUNCTION_RESOURCE,
    LOG_GROUP_RESOURCE,
    *PASS_ROLE_RESOURCES,
]

#: The two actions whose service authorization reference lists no resource type at all,
#: so that "*" is the narrowest grant that exists rather than a shortcut. Anything else
#: appearing here is a scope somebody gave up on.
UNSCOPED_STATEMENTS = [
    {
        "Effect": "Allow",
        "Action": "cloudformation:ValidateTemplate",
        "Resource": "*",
    },
    {
        "Effect": "Allow",
        "Action": "logs:DescribeLogGroups",
        "Resource": "*",
    },
]
EXPECTED_STACK_ACTIONS = {
    "cloudformation:CreateChangeSet",
    "cloudformation:CreateStack",
    "cloudformation:DeleteChangeSet",
    "cloudformation:DeleteStack",
    "cloudformation:DescribeChangeSet",
    "cloudformation:DescribeStackEvents",
    "cloudformation:DescribeStackResources",
    "cloudformation:DescribeStacks",
    "cloudformation:ExecuteChangeSet",
    "cloudformation:GetTemplateSummary",
    "cloudformation:ListStackResources",
    "cloudformation:UpdateStack",
}
EXPECTED_REPOSITORY_ACTIONS = {
    "ecr:CreateRepository",
    "ecr:DeleteLifecyclePolicy",
    "ecr:DeleteRepository",
    "ecr:DescribeRepositories",
    "ecr:GetLifecyclePolicy",
    "ecr:GetRepositoryPolicy",
    "ecr:ListTagsForResource",
    "ecr:PutImageScanningConfiguration",
    "ecr:PutImageTagMutability",
    "ecr:PutLifecyclePolicy",
    "ecr:TagResource",
    "ecr:UntagResource",
}
EXPECTED_BUCKET_ACTIONS = {
    "s3:CreateBucket",
    "s3:DeleteBucket",
    "s3:DeleteBucketPolicy",
    "s3:GetAccelerateConfiguration",
    "s3:GetAnalyticsConfiguration",
    "s3:GetBucketCORS",
    "s3:GetBucketLocation",
    "s3:GetBucketLogging",
    "s3:GetBucketNotification",
    "s3:GetBucketObjectLockConfiguration",
    "s3:GetBucketOwnershipControls",
    "s3:GetBucketPolicy",
    "s3:GetBucketPublicAccessBlock",
    "s3:GetBucketTagging",
    "s3:GetBucketVersioning",
    "s3:GetBucketWebsite",
    "s3:GetEncryptionConfiguration",
    "s3:GetIntelligentTieringConfiguration",
    "s3:GetInventoryConfiguration",
    "s3:GetLifecycleConfiguration",
    "s3:GetMetricsConfiguration",
    "s3:GetReplicationConfiguration",
    "s3:ListBucket",
    "s3:PutBucketObjectLockConfiguration",
    "s3:PutBucketPolicy",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutBucketTagging",
    "s3:PutBucketVersioning",
    "s3:PutEncryptionConfiguration",
    # The write that Phase 3's first deploy was missing while the matching read,
    # s3:GetLifecycleConfiguration, was already granted above. infra/outputs-bucket.yaml is
    # the first bucket template here to set a LifecycleConfiguration, and the denial landed
    # after CreateBucket had succeeded, so the retained bucket outlived the rolled-back
    # stack. Pinning the set is what turns the next such omission into a red test.
    "s3:PutLifecycleConfiguration",
}
EXPECTED_OBJECT_ACTIONS = {
    "s3:AbortMultipartUpload",
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:GetObject",
    "s3:GetObjectVersion",
    "s3:PutObject",
}
#: Exactly one. Lambda fetches a versioned code object as the deploying principal and
#: needs to enumerate versions on the bucket to do it; the first deploy of the state
#: machine stack failed on precisely this and rolled back.
EXPECTED_ARTIFACT_BUCKET_ACTIONS = {"s3:ListBucketVersions"}
EXPECTED_STATE_MACHINE_ACTIONS = {
    "states:CreateStateMachine",
    "states:DeleteStateMachine",
    "states:DescribeStateMachine",
    "states:ListTagsForResource",
    "states:TagResource",
    "states:UntagResource",
    "states:UpdateStateMachine",
}
EXPECTED_FUNCTION_ACTIONS = {
    "lambda:CreateFunction",
    "lambda:DeleteFunction",
    "lambda:GetFunction",
    "lambda:GetFunctionCodeSigningConfig",
    "lambda:GetFunctionConfiguration",
    "lambda:GetRuntimeManagementConfig",
    "lambda:ListTags",
    "lambda:ListVersionsByFunction",
    "lambda:TagResource",
    "lambda:UntagResource",
    "lambda:UpdateFunctionCode",
    "lambda:UpdateFunctionConfiguration",
}
EXPECTED_LOG_GROUP_ACTIONS = {
    "logs:CreateLogGroup",
    "logs:DeleteLogGroup",
    "logs:DeleteRetentionPolicy",
    "logs:ListTagsForResource",
    "logs:PutRetentionPolicy",
    "logs:TagResource",
    "logs:UntagResource",
}

#: The two buckets that must stay outside anything this role can name, and the reason the
#: grant above is an exact name rather than an ``arn:aws:s3:::edullm-*`` wildcard. Nothing
#: writes into the sealed bucket except a validator identity assumable only by a container,
#: and this role holds s3:PutBucketPolicy, which is what the airlock's delete protection is
#: written in.
UNREACHABLE_BUCKETS = ("edullm-data", "edullm-landing")

#: Every action wildcard this role is allowed to carry, which is one.
#:
#: An inventory rather than a ban, for the reason ``UNSCOPED_STATEMENTS`` is an inventory
#: rather than a ban: the widening worth catching is a second `*` that looks like the first.
#: A blanket refusal reads as the stricter choice and is the weaker one, because the day a
#: statement genuinely needs a wildcard the refusal is edited away and nothing replaces it.
#:
#: ``s3:Get*`` is scoped to :data:`SCRATCH_BUCKET_ARN` and to nothing else, and the bucket ARN
#: is what bounds it. Every object-level S3 action authorizes against
#: ``arn:aws:s3:::edullm-scratch/*``, which this role does not name, so this cannot read a
#: researcher's working file; on a bucket ARN alone the reachable set is the configuration
#: surfaces the AWS::S3::Bucket handler reads, which is the same set
#: :data:`EXPECTED_BUCKET_ACTIONS` spells out for the prefixed buckets. It is spelled as a
#: wildcard there and only there because this role renders to within a few hundred bytes of
#: IAM's 10240-byte aggregate inline cap and the twenty reads written out do not fit.
PERMITTED_ACTION_WILDCARDS = {"s3:Get*"}

#: The retired shared role could reach Batch and EC2. Nothing this repository deploys
#: needs either, and sts: is here because a deploy credential that can mint a second
#: credential is not bounded by what it was granted.
FORBIDDEN_ACTION_PREFIXES = ("batch:", "ec2:", "sts:")
ALLOWED_ACTION_PREFIXES = (
    "cloudformation:",
    "ecr:",
    "iam:PassRole",
    "lambda:",
    "logs:",
    "s3:",
    "states:",
)
#: The two repository-policy actions that change who may pull or push an image. The read,
#: ecr:GetRepositoryPolicy, is deliberately not here: the ECR resource handler makes that
#: call on every repository update, so forbidding it did not withhold an authority, it
#: made an existing repository unchangeable. Reading a policy this account never sets
#: grants nobody anything; setting or deleting one does.
FORBIDDEN_REPOSITORY_POLICY_ACTIONS = {
    "ecr:DeleteRepositoryPolicy",
    "ecr:SetRepositoryPolicy",
}
#: What CI must never be able to do to a role, including to this one. iam:PassRole is
#: the single IAM grant the amendment adds and it lends a role rather than changing one;
#: everything below changes one, and the permissions boundary is not what keeps them out,
#: because a boundary can be detached by whoever can attach a policy.
ROLE_MUTATING_ACTIONS = frozenset(
    {
        "iam:AttachRolePolicy",
        "iam:CreatePolicy",
        "iam:CreatePolicyVersion",
        "iam:CreateRole",
        "iam:CreateServiceLinkedRole",
        "iam:DeleteRole",
        "iam:DeleteRolePermissionsBoundary",
        "iam:DeleteRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePermissionsBoundary",
        "iam:PutRolePolicy",
        "iam:UpdateAssumeRolePolicy",
        "iam:UpdateRole",
    }
)
#: Named in the template as deliberately absent, one per service: the deployer builds
#: the admission path and may never run it, change who may run it, or write into the
#: record of what it did.
BUILDS_BUT_NEVER_RUNS = frozenset(
    {
        "lambda:AddPermission",
        "lambda:InvokeFunction",
        "lambda:RemovePermission",
        "logs:DeleteLogStream",
        "logs:PutLogEvents",
        "states:StartExecution",
        "states:StopExecution",
    }
)


def _role() -> dict[str, Any]:
    return next(iam_roles(load_template(TEMPLATE_PATH)))


def _statements(policy_name: str) -> list[dict[str, Any]]:
    matching = [policy for policy in _role()["Policies"] if policy["PolicyName"] == policy_name]
    assert len(matching) == 1, f"expected exactly one inline policy named {policy_name}"
    document = matching[0]["PolicyDocument"]
    assert document["Version"] == "2012-10-17"
    statements = document["Statement"]
    assert isinstance(statements, list)
    return statements


def _all_statements() -> list[dict[str, Any]]:
    """Every statement of every inline policy, in the order the template declares them.

    Read across both policies rather than out of the first one. The two are unioned by
    IAM, so a claim about what this role can reach that stopped at the phase boundary
    would be a claim about half a role.
    """
    return [statement for name in POLICY_NAMES for statement in _statements(name)]


def _resources(statement: dict[str, Any]) -> list[Any]:
    resource = statement["Resource"]
    return resource if isinstance(resource, list) else [resource]


def _actions() -> list[str]:
    """Every action either inline policy grants, duplicates kept so a repeat is visible."""
    return [action for statement in _all_statements() for action in statement_actions(statement)]


def _statement_scoped_to(resource: object) -> dict[str, Any]:
    matching = [statement for statement in _all_statements() if statement["Resource"] == resource]
    assert len(matching) == 1, f"expected exactly one statement scoped to {resource}"
    return matching[0]


def test_deployer_template_creates_exactly_one_bounded_role() -> None:
    template = load_template(TEMPLATE_PATH)

    resources = template["Resources"]
    assert len(resources) == 1
    logical_id, role = resource_of_type(template, "AWS::IAM::Role")
    assert list(resources) == [logical_id]

    properties = role["Properties"]
    assert properties["RoleName"] == ROLE_NAME
    assert properties["PermissionsBoundary"] == BOUNDARY
    assert properties["MaxSessionDuration"] <= 3600

    # One inline policy per phase, in order, and no fourth. A policy nobody named here is a
    # grant nobody asserts the shape of -- in this module for the first two, and in
    # tests/test_phase3_deployer_role.py for the third.
    policies = properties["Policies"]
    assert [policy["PolicyName"] for policy in policies] == DECLARED_POLICY_NAMES


def test_deployer_trusts_only_the_main_branch_deploy_workflows_through_github_oidc() -> None:
    # job_workflow_ref is a list, and an array under a single StringEquals key is an OR
    # across its elements. That makes the list itself the boundary of who may assume this
    # role, so the whole document is pinned: a fourth entry is a fourth workflow file that
    # can deploy, and it has to fail here rather than be noticed in the console.
    trust = _role()["AssumeRolePolicyDocument"]

    assert trust["Version"] == "2012-10-17"
    assert len(trust["Statement"]) == 1
    assert trust["Statement"][0] == {
        "Effect": "Allow",
        "Principal": {"Federated": OIDC_PROVIDER},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
            "StringEquals": {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                "token.actions.githubusercontent.com:job_workflow_ref": JOB_WORKFLOW_REFS,
                "token.actions.githubusercontent.com:repository_owner_id": "306859726",
                "token.actions.githubusercontent.com:repository_id": "1311508598",
            },
            "StringLike": {"token.actions.githubusercontent.com:sub": SUBJECT},
        },
    }


def test_deployer_trust_policy_contains_no_wildcard_at_all() -> None:
    # Bootstrap stacks are deployed from a laptop, so CI never deploys from a feature
    # branch and the branch wildcard the publisher role needs has no counterpart here.
    # Any `*` reaching this document would widen who can assume the role.
    trust_strings = list(walk_strings(_role()["AssumeRolePolicyDocument"]))

    assert trust_strings
    assert [value for value in trust_strings if "*" in value] == []


def test_every_trusted_job_workflow_ref_names_a_workflow_file_that_exists() -> None:
    # The trust pins filenames, so a rename revokes that file's deployments silently: the
    # run fails at AssumeRole with nothing pointing at the rename. Checking each reference
    # against the tree is what turns that into a red test in the commit that renames it.
    condition = _role()["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
    references = condition["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]

    assert references == JOB_WORKFLOW_REFS
    for reference in references:
        path, separator, ref = reference.partition("@")

        assert separator == "@"
        assert ref == MAIN_BRANCH_REF
        assert path.startswith(f"{REPOSITORY}/")
        workflow = path.removeprefix(f"{REPOSITORY}/")
        assert (PROJECT_ROOT / workflow).is_file(), workflow


def test_deployer_can_only_touch_stacks_carrying_our_own_prefix() -> None:
    statement = _statement_scoped_to(STACK_RESOURCE)
    actions = statement_actions(statement)

    assert statement["Effect"] == "Allow"
    assert set(actions) == EXPECTED_STACK_ACTIONS
    assert len(actions) == len(EXPECTED_STACK_ACTIONS)


def test_only_the_two_actions_with_no_resource_type_are_granted_without_a_scope() -> None:
    # Both are here because their service authorization reference lists no resource type,
    # not because scoping them was awkward. cloudformation:GetTemplateSummary is the
    # counter-example: it does accept a stack ARN, `aws cloudformation deploy` only ever
    # calls it as get_template_summary(StackName=...), and it is scoped with the rest.
    #
    # logs:DescribeLogGroups earns its place the expensive way. It was written into the
    # log-group-scoped statement first, which reads as the careful choice, and the deploy
    # failed on it -- an action with no resource type cannot be granted on an ARN, and the
    # denial names the action without hinting that the scope is what refused it.
    statements = _all_statements()
    unscoped = [statement for statement in statements if "*" in _resources(statement)]

    assert unscoped == UNSCOPED_STATEMENTS
    assert all(
        isinstance(resource, dict)
        for statement in statements
        if statement not in unscoped
        for resource in _resources(statement)
    )


def test_deployer_can_only_touch_ecr_repositories_carrying_our_own_prefix() -> None:
    statement = _statement_scoped_to(REPOSITORY_RESOURCE)
    actions = statement_actions(statement)

    assert statement["Effect"] == "Allow"
    assert set(actions) == EXPECTED_REPOSITORY_ACTIONS
    assert len(actions) == len(EXPECTED_REPOSITORY_ACTIONS)


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        (BUCKET_RESOURCE, EXPECTED_BUCKET_ACTIONS),
        (ARTIFACT_OBJECT_RESOURCE, EXPECTED_OBJECT_ACTIONS),
        (ARTIFACT_BUCKET_RESOURCE, EXPECTED_ARTIFACT_BUCKET_ACTIONS),
        (STATE_MACHINE_RESOURCE, EXPECTED_STATE_MACHINE_ACTIONS),
        (FUNCTION_RESOURCE, EXPECTED_FUNCTION_ACTIONS),
        (LOG_GROUP_RESOURCE, EXPECTED_LOG_GROUP_ACTIONS),
    ],
    ids=[
        "buckets",
        "artifact objects",
        "artifact bucket",
        "state machines",
        "functions",
        "log groups",
    ],
)
def test_each_admission_scope_grants_exactly_the_actions_it_was_reviewed_with(
    resource: dict[str, str],
    expected: set[str],
) -> None:
    # Pinned as a set rather than as a prefix, because the widening worth catching here
    # is one more action inside a list that already looks right. The bucket list is long
    # on purpose: the AWS::S3::Bucket handler reads every configuration surface a bucket
    # has on each Read, and an AccessDenied on any of them fails the stack update.
    statement = _statement_scoped_to(resource)
    actions = statement_actions(statement)

    assert statement["Effect"] == "Allow"
    assert set(actions) == expected
    assert len(actions) == len(expected)


def test_objects_are_reachable_only_inside_the_artifacts_bucket() -> None:
    # An S3 ARN naming anything after the bucket is an object grant, and there is one:
    # the packaged function zip CloudFormation reads and the release procedure uploads.
    # Neither reason reaches the write-once lineage store, and a deployer holding
    # PutObject or DeleteObject over it would undo the property that store exists to have.
    buckets = [
        arn.removeprefix(BUCKET_ARN % "")
        for statement in _all_statements()
        for resource in _resources(statement)
        if isinstance(resource, dict)
        for arn in [resource["Fn::Sub"]]
        if arn.startswith(BUCKET_ARN % "")
    ]
    objects = [name for name in buckets if "/" in name]

    assert objects == [f"{RESOURCE_PREFIX}artifacts/*"]
    # Four S3 ARNs: every prefixed bucket for the configuration surfaces the bucket handler
    # reads, the working tier by its whole name, the artifacts objects, and the artifacts
    # bucket itself for the one bucket-level action Lambda needs to fetch a versioned code
    # object. The last is named rather than folded into the first so that enumerating
    # versions stops at the bucket holding deployment zips.
    #
    # The working tier is a bucket ARN and not an object one, which is what keeps it out of
    # the list above. That is the property, not an accident of how it happens to be written:
    # its statement carries s3:Get*, and an object ARN beside it would turn that into
    # s3:GetObject over every researcher's working set.
    assert buckets == [
        f"{RESOURCE_PREFIX}*",
        "edullm-scratch",
        *objects,
        f"{RESOURCE_PREFIX}artifacts",
    ]


def test_no_s3_scope_this_role_holds_can_ever_reach_the_sealed_bucket() -> None:
    """THE PROPERTY THE EXACT NAME EXISTS FOR, CHECKED BY MATCHING RATHER THAN BY READING.
    Mutation: widen the working tier's scope to arn:aws:s3:::edullm-*. Mutation: add
    arn:aws:s3:::edullm-data to any statement in the file.

    A test that asserted the template contains the string arn:aws:s3:::edullm-scratch would
    pass under both, because both leave that string in place -- the first adds a wildcard
    that also matches it and the second adds a bucket beside it. So this does not look for
    a name. It expands every S3 scope the role holds into the IAM wildcard match it
    actually performs, and asks whether the two buckets that must stay out are in it.

    edullm-data is where a published corpus lives once it is sealed, and edullm-landing is
    the airlock in front of it. Neither may be reachable by anything a pipeline assumes:
    this role holds s3:PutBucketPolicy and s3:DeleteBucketPolicy, so a scope that reached
    edullm-data would let a CI deploy replace the very bucket policy that refuses the
    deletes -- decisions.md, "The airlock is a latch", is the record of how thin that
    protection already is.
    """
    # Every scope of every statement that grants an S3 action, including a Resource "*"
    # if one ever appears in such a statement. Selecting by the statement's actions rather
    # than by the shape of the ARN is what makes the "*" case count: the two unscoped
    # statements in this role grant no S3 action today, and a future one that did would be
    # exactly the widening this test should catch.
    scopes = [
        (resource["Fn::Sub"] if isinstance(resource, dict) else resource).replace(
            "${AWS::Partition}", "aws"
        )
        for statement in _all_statements()
        if any(action.startswith("s3:") for action in statement_actions(statement))
        for resource in _resources(statement)
    ]

    assert scopes, "no S3 scope found at all, so this test proved nothing"
    for bucket in UNREACHABLE_BUCKETS:
        for suffix in ("", "/*", "/some/published/object.parquet"):
            target = f"arn:aws:s3:::{bucket}{suffix}"
            matched = [scope for scope in scopes if fnmatch.fnmatchcase(target, scope)]
            assert not matched, (
                f"{target} is inside the deployer's reach through {matched}. The sealed "
                "side of the data flow is unreachable by any automation by design, and "
                "the working tier is granted by its whole name rather than by an "
                "edullm-* wildcard precisely so that this stays true."
            )


def test_the_working_tier_is_granted_by_its_whole_name_and_only_what_it_creates() -> None:
    """Mutation: add s3:DeleteBucket, or s3:PutBucketPolicy, to the working tier statement.
    Mutation: add arn:aws:s3:::edullm-scratch/* beside the bucket ARN.

    The action set is read off infra/scratch-bucket.yaml rather than chosen: BucketName is
    CreateBucket, LifecycleConfiguration is PutLifecycleConfiguration,
    PublicAccessBlockConfiguration is PutBucketPublicAccessBlock and BucketEncryption is
    PutEncryptionConfiguration. That template sets no versioning, no tagging, no object
    lock and no bucket policy, so the matching verbs are absent and adding one of those
    properties is a deploy failure naming the action -- the fail-closed direction the
    prefixed bucket statement is chosen by too.

    s3:DeleteBucket is the one worth pinning. The template retains the bucket on delete and
    on replace, so CloudFormation never calls it, and the working tier holds work that
    exists nowhere else: a deployer able to remove it could take a person's files in a
    rollback they were not party to.

    The second mutation is the quiet one. Adding the object ARN grants nothing this stack
    needs, because every action here authorizes against the bucket, and it would turn the
    s3:Get* in the list into s3:GetObject over every researcher's working set.
    """
    statement = _statement_scoped_to(SCRATCH_BUCKET_ARN)
    actions = statement_actions(statement)

    assert statement["Effect"] == "Allow"
    assert set(actions) == {
        "s3:CreateBucket",
        "s3:Get*",
        "s3:ListBucket",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutEncryptionConfiguration",
        "s3:PutLifecycleConfiguration",
    }
    assert len(actions) == len(set(actions))
    assert _resources(statement) == [SCRATCH_BUCKET_ARN], (
        "the working tier is scoped to something other than the bucket ARN alone. An "
        "object ARN here would make the statement's s3:Get* reach every object in the "
        "tier, and no action in this statement needs one."
    )


def test_deployer_resource_scopes_never_widen_to_the_shared_intern_prefix() -> None:
    # sbsandbox-intern- is the whole account's prefix: every intern's stacks, buckets and
    # registries begin with it. Only the edullm segment makes a name ours, so a wildcard
    # placed any earlier would hand this role other people's infrastructure to delete.
    scoped = [
        resource["Fn::Sub"]
        for statement in _all_statements()
        for resource in _resources(statement)
        if isinstance(resource, dict)
    ]

    assert scoped == [resource["Fn::Sub"] for resource in SCOPED_RESOURCES]
    # The working tier is the one scope that carries no sbsandbox-intern- segment at all,
    # because the bucket carries none: a person types edullm-scratch and both reference
    # documents draw it. It is excluded here by name rather than by loosening the rule, so
    # that a second scope leaving the prefix is a red test. What keeps it safe is not this
    # assertion but test_no_s3_scope_this_role_holds_can_ever_reach_the_sealed_bucket,
    # which asks what the scope actually matches rather than what it is spelled.
    prefixed = [arn for arn in scoped if arn != SCRATCH_BUCKET_ARN["Fn::Sub"]]

    assert len(prefixed) == len(scoped) - 1
    assert all(RESOURCE_PREFIX in arn for arn in prefixed)
    assert not [arn for arn in prefixed if f"{SHARED_PREFIX}*" in arn]
    # Every occurrence, not just the first: an ARN naming two resources would otherwise
    # be judged on the one that happens to come first.
    assert all(
        rest.startswith("edullm-") for arn in prefixed for rest in arn.split(SHARED_PREFIX)[1:]
    )


def test_pass_role_names_whole_roles_and_never_a_prefix() -> None:
    # The one grant in the amendment that meaningfully widens the deployer. Passing a
    # role is how a principal lends its own limits away, so a prefix here would let this
    # role pass any role that ever takes that name, including one created later with
    # permissions nobody weighed against a deploy credential.
    #
    # The count is held to the declared list rather than to a number, because the list is
    # where each addition has to be argued and a bare integer is a thing somebody increments.
    statements = [
        statement
        for statement in _all_statements()
        if "iam:PassRole" in statement_actions(statement)
    ]

    assert len(statements) == 1
    statement = statements[0]
    passed = [resource["Fn::Sub"] for resource in _resources(statement)]

    assert statement["Effect"] == "Allow"
    assert statement_actions(statement) == ["iam:PassRole"]
    assert _resources(statement) == PASS_ROLE_RESOURCES
    assert len(passed) == len(PASS_ROLE_RESOURCES)
    assert not [arn for arn in passed if "*" in arn]
    assert all(arn.startswith(ROLE_ARN % "") for arn in passed)
    assert all(arn.removeprefix(ROLE_ARN % "").startswith(RESOURCE_PREFIX) for arn in passed)


def test_deployer_can_never_mint_or_alter_a_role() -> None:
    # CI holding this role must never be able to give itself a second one, or to edit the
    # one it has. iam:PassRole is the only IAM action the deployer may carry, and it is a
    # different kind of grant: it lends a role that already exists and whose own trust
    # policy names the single service allowed to assume it.
    actions = set(_actions())

    assert {action for action in actions if action.lower().startswith("iam:")} == {"iam:PassRole"}
    assert not actions & ROLE_MUTATING_ACTIONS


def test_deployer_builds_the_admission_path_and_can_never_run_it() -> None:
    # Starting an execution is the submission role's single permission, and keeping the
    # two apart is what stops a deploy credential from admitting a run. The same argument
    # covers invoking the validator, changing who may invoke it, and writing into or
    # trimming the log group the executions are recorded in.
    actions = set(_actions())

    assert not actions & BUILDS_BUT_NEVER_RUNS


def test_deployer_can_never_write_an_ecr_repository_policy() -> None:
    # A repository policy is the one ECR grant that changes who may pull or push an
    # image. Nothing committed sets one -- infra/ecr-repositories.yaml is forbidden from
    # carrying RepositoryPolicyText -- so reintroducing the writes must fail here.
    #
    # The read is allowed and is named rather than pattern-matched away, because the
    # difference is the whole of this test now: CloudFormation cannot update a repository
    # without ecr:GetRepositoryPolicy, so a blanket ban on the substring cost the account
    # the ability to change a deployed repository and bought nothing. Any fourth
    # repository-policy action AWS adds still fails here until somebody argues for it.
    actions = set(_actions())

    assert not actions & FORBIDDEN_REPOSITORY_POLICY_ACTIONS
    assert {
        action for action in actions if "repositorypolicy" in action.lower()
    } == {"ecr:GetRepositoryPolicy"}


def test_deployer_grants_nothing_outside_the_services_the_two_phases_deploy() -> None:
    # The retired shared role could reach IAM, S3, Batch and EC2. This role deploys the
    # CloudFormation stacks of two phases, so the services it may name are the ones those
    # stacks contain and nothing else. S3 and IAM are on the list now, and they are on it
    # in a bounded form: the tests above pin which S3 actions and which single IAM one.
    actions = _actions()

    assert actions
    assert not [
        action for action in actions if action.lower().startswith(FORBIDDEN_ACTION_PREFIXES)
    ]
    assert all(action.startswith(ALLOWED_ACTION_PREFIXES) for action in actions)
    # Compared as a set rather than refused outright, because one statement carries a
    # wildcard on purpose and a bare `not any("*" in action)` would have to be deleted to
    # let it through -- taking the check for every other statement with it.
    assert {action for action in actions if "*" in action} == PERMITTED_ACTION_WILDCARDS


#: Every wildcard the Phase 3 policy adds, in the order the template writes them. An
#: inventory rather than a pattern, for the reason the Phase 1 and Phase 2 entries are one:
#: the widening worth catching is a `*` that looks like the ones around it.
#:
#: The six ec2: entries and the lambda event-source-mapping entry are the only scopes in this
#: role that do not carry the project prefix, and none of them can. Each names a resource
#: addressed by an identifier the service assigns at creation, so `vpc/*` and
#: `event-source-mapping:*` are the narrowest ARNs that exist. The EC2 six are the ones to
#: weigh: this role can therefore delete any VPC, subnet, route table, internet gateway or
#: security group in a shared account. The template says so in the open and names the
#: narrowing that was not taken; tests/test_phase3_deployer_role.py enumerates the six
#: resource types so a seventh has to be a visible edit, and pins the mapping scope to one
#: read-only action.
PHASE3_WILDCARDS = [
    # The measured no-resource-type actions, then EC2's account-wide describes. Two
    # statements, two different reasons, kept apart on purpose.
    "*",
    "*",
    *[
        REGIONAL_ARN % ("ec2", f"{resource_type}/*")
        for resource_type in (
            "internet-gateway",
            "route-table",
            "security-group",
            "security-group-rule",
            "subnet",
            "vpc",
        )
    ],
    # The launch template lifecycle, in a statement of its own after the network one. Same
    # exemption and same reason -- EC2 assigns the ID at creation, so there is no name to
    # scope on -- but a separate statement, because folding it into the list above would
    # hand every network verb there a launch-template ARN it has no use for.
    REGIONAL_ARN % ("ec2", "launch-template/*"),
    REGIONAL_ARN % ("batch", f"compute-environment/{RESOURCE_PREFIX}*"),
    # Creating or updating a job queue is authorized against the compute environments the
    # queue names as well as against the queue, so that statement carries both ARNs.
    REGIONAL_ARN % ("batch", f"job-queue/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("batch", f"compute-environment/{RESOURCE_PREFIX}*"),
    # Deleting one is not: the request carries no compute environment, so it stays narrow
    # in a statement of its own rather than inheriting the pair above.
    REGIONAL_ARN % ("batch", f"job-queue/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("batch", f"job-definition/{RESOURCE_PREFIX}*"),
    # The tagging statement names all three Batch resource types, because Batch uses one
    # set of tagging actions for every resource it owns.
    REGIONAL_ARN % ("batch", f"compute-environment/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("batch", f"job-definition/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("batch", f"job-queue/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("events", f"rule/{RESOURCE_PREFIX}*"),
    # EventBridge Scheduler, which is a different service from EventBridge rules and shares
    # none of the ARN space above. Scoped to the default schedule group, because a group is a
    # container the caller picks and a grant across every group would let a schedule be created
    # somewhere nothing in this repository looks for it.
    REGIONAL_ARN % ("scheduler", f"schedule/default/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("logs", f"log-group:/aws/batch/{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("cloudwatch", f"alarm:{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("sqs", f"{RESOURCE_PREFIX}*"),
    # The event source mapping actions authorize against both the function and the mapping,
    # so that statement carries both ARNs. This list said "the function, not the mapping"
    # until a deploy said otherwise: lambda:CreateEventSourceMapping was denied naming
    # event-source-mapping:*, which is the ARN the mapping's absent UUID had been the
    # argument against granting.
    REGIONAL_ARN % ("lambda", f"function:{RESOURCE_PREFIX}*"),
    REGIONAL_ARN % ("lambda", "event-source-mapping:*"),
    # The third occurrence is the same function prefix again, as the value of the
    # lambda:FunctionArn condition that closes the mapping scope. It is a wildcard string
    # in the template and so it is declared here, even though it grants nothing on its own.
    REGIONAL_ARN % ("lambda", f"function:{RESOURCE_PREFIX}*"),
    # And again for lambda:ListTags, which authorizes against whatever ARN it is handed and
    # is given the mapping's by the Read handler, so it keeps a statement of its own. A
    # mapping is addressed by a UUID Lambda assigns at creation, so this is the scope in the
    # role that cannot carry the project prefix -- the EC2 problem in a second service.
    REGIONAL_ARN % ("lambda", "event-source-mapping:*"),
]


def test_deployer_template_wildcards_are_only_the_declared_scopes() -> None:
    strings = list(walk_strings(load_template(TEMPLATE_PATH)))

    assert [value for value in strings if "*" in value] == [
        STACK_RESOURCE["Fn::Sub"],
        "*",
        REPOSITORY_RESOURCE["Fn::Sub"],
        # The Phase 2 policy opens with its own unscoped grant, before its scoped ones.
        "*",
        BUCKET_RESOURCE["Fn::Sub"],
        # The working tier's one action wildcard, which sits between the prefixed bucket
        # scope and the artifacts objects because that is where the template writes it.
        # It is an Action rather than a Resource, and it is in this list because this list
        # is every `*` in the file rather than every scope in it.
        *sorted(PERMITTED_ACTION_WILDCARDS),
        ARTIFACT_OBJECT_RESOURCE["Fn::Sub"],
        STATE_MACHINE_RESOURCE["Fn::Sub"],
        FUNCTION_RESOURCE["Fn::Sub"],
        LOG_GROUP_RESOURCE["Fn::Sub"],
        *PHASE3_WILDCARDS,
    ]
    assert not ACCOUNT_LITERAL.search(TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_deployer_outputs_only_the_role_name_and_arn() -> None:
    template = load_template(TEMPLATE_PATH)
    logical_id, _ = resource_of_type(template, "AWS::IAM::Role")

    assert template["Outputs"] == {
        "RoleName": {"Value": {"Ref": logical_id}},
        "RoleArn": {"Value": {"Fn::GetAtt": [logical_id, "Arn"]}},
    }
