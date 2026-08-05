"""What the Phase 3 amendment adds to the deploy role, and everything it must not add.

``tests/test_phase1_deployer_role.py`` pins the whole role: the trust policy, the inventory
of scoped resources, and the two things CI may never do. This module is about the third
inline policy specifically, and it exists because two of its properties have already cost
this repository a failed deploy each.

The first is the ``"*"`` set. An action whose service authorization reference lists no
resource type cannot be granted on an ARN at all, and scoping one produces a deploy failure
that names the action without hinting that the scope is what refused it. Phase 2 learned
that with ``logs:DescribeLogGroups``; Phase 3 has six more in one statement and fifteen EC2
describes in another, all measured with controls, and the next reader's instinct will be to
tidy them away.

The third property is newer, and it cost a stack rather than a deploy. A resource handler
reads and writes configuration surfaces no template mentions, so the audit that keeps this
role honest is against the handlers rather than against the templates: ``ec2:DescribeInstances``
is called on a security group's *delete*, ``lambda:ListTags`` on an event source mapping's
ARN rather than its function's, and ``s3:PutLifecycleConfiguration`` -- the one this
amendment was written for -- on a bucket whose read half already looked complete.

The second is ``iam:PassRole``. Passing a role is how a principal lends its own limits away,
so the ARNs are written out whole -- a prefix would let this role pass a role created later
that nobody weighed against a deploy credential.
"""

from __future__ import annotations

from typing import Any

from infrastructure_support import (
    ACCOUNT_LITERAL,
    IAM_ROOT,
    PROJECT_ROOT,
    iam_roles,
    load_template,
    statement_actions,
)

TEMPLATE_PATH = IAM_ROOT / "infra-deployer-role.yaml"
ROLE_NAME = "sbsandbox-intern-edullm-infra-deployer"

PHASE1_POLICY_NAME = "deploy-phase1-stacks"
PHASE2_POLICY_NAME = "deploy-phase2-admission-stacks"
PHASE3_POLICY_NAME = "deploy-phase3-batch-stacks"
POLICY_NAMES = [PHASE1_POLICY_NAME, PHASE2_POLICY_NAME, PHASE3_POLICY_NAME]

REPOSITORY = "edu-llm/platform"
PHASE3_DEPLOY_WORKFLOW = ".github/workflows/deploy-phase3-batch.yml"
PHASE3_DEPLOY_WORKFLOW_REF = f"{REPOSITORY}/{PHASE3_DEPLOY_WORKFLOW}@refs/heads/main"

SHARED_PREFIX = "sbsandbox-intern-"
RESOURCE_PREFIX = f"{SHARED_PREFIX}edullm-"

#: MEASURED, CONTROL-VERIFIED, AND NOT TO BE SCOPED. Each of these is listed in its service
#: authorization reference with no resource type, established on 2026-07-27 by granting the
#: action on one ARN and simulating it against that same ARN: resource-level support answers
#: `allowed`, no resource type answers `implicitDeny`. Two controls ran on every invocation
#: and both behaved -- cloudformation:ValidateTemplate returned implicitDeny (known to have
#: no resource type) and cloudformation:DescribeStacks returned allowed (known scopable).
NO_RESOURCE_TYPE_ACTIONS = frozenset(
    {
        "batch:DescribeComputeEnvironments",
        "batch:DescribeJobDefinitions",
        "batch:DescribeJobQueues",
        "batch:DescribeJobs",
        "batch:ListJobs",
        "events:ListRules",
    }
)

#: The second unscoped statement, and it is unscoped for a different reason that is worth
#: keeping apart from the one above. EC2's Describe actions enumerate rather than address:
#: they are account-wide by EC2's documented model, and the CloudFormation handlers for VPC,
#: Subnet, RouteTable, InternetGateway and SecurityGroup call them on every Read. All of
#: them are read-only.
#:
#: The five below the first ten were added on 2026-07-27, and unlike the first ten they were
#: measured with the probe and both of its controls rather than taken from EC2's model:
#: each answered `implicitDeny` when granted on one ARN and simulated against that same ARN,
#: while cloudformation:ValidateTemplate answered `implicitDeny` and
#: cloudformation:DescribeStacks answered `allowed` on every invocation. None of the five is
#: inferable from the templates -- DescribeVpnGateways is read by the VPCGatewayAttachment
#: handler on an attachment that names no VPN gateway, DescribeInstances by the
#: SecurityGroup handler's Delete, and DescribeLaunchTemplateVersions by the Batch compute
#: environment handler with no launch template in sight.
#:
#: ec2:DescribeLaunchTemplates arrived on 2026-08-04 with the first launch template this
#: account has ever had, and it is a different action from the Versions one beside it: that
#: is what the Batch compute environment handler reads, this is what
#: AWS::EC2::LaunchTemplate's own Read, List and Delete call. It measured the same way --
#: implicitDeny when granted on one launch-template ARN and simulated against that same ARN
#: -- while the five write verbs of the same resource measured `allowed` under the identical
#: probe and are therefore scoped rather than sitting here.
EC2_DESCRIBE_ACTIONS = frozenset(
    {
        "ec2:DescribeAvailabilityZones",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeNetworkAcls",
        "ec2:DescribeRouteTables",
        "ec2:DescribeSecurityGroupRules",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSubnets",
        "ec2:DescribeTags",
        "ec2:DescribeVpcAttribute",
        "ec2:DescribeVpcs",
        "ec2:DescribeInstances",
        "ec2:DescribeLaunchTemplateVersions",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVpcEncryptionControls",
        "ec2:DescribeVpnGateways",
    }
)

#: Eight whole role ARNs, in the order the template writes them. Written out here for the
#: same reason they are written out there: a name that has to be typed twice cannot grow a
#: wildcard on one side only.
#:
#: The middle three arrived with the GPU compute environment. Batch has no per-job role
#: override, so a second job definition is the only way to give a container a different
#: identity, and a second job definition needs its own execution and workload roles passed.
#:
#: The last arrived with the runs channel, and it is the second Lambda role
#: ``lambda:CreateFunction`` passes. It reads one lineage prefix, lists one checkpoint
#: prefix, lists Batch jobs in this region and reads one secret, and it writes nothing.
PASS_ROLE_NAMES = [
    "sbsandbox-intern-edullm-batch-execution",
    "sbsandbox-intern-edullm-batch-workload",
    "sbsandbox-intern-edullm-batch-instance",
    "sbsandbox-intern-edullm-batch-gpu-execution",
    "sbsandbox-intern-edullm-batch-gpu-workload",
    "sbsandbox-intern-edullm-batch-gpu-instance",
    "sbsandbox-intern-edullm-lifecycle-lambda",
    "sbsandbox-intern-edullm-notifier-lambda",
]

#: Every EC2 resource type this role writes to, and the one place in it where a resource ARN
#: does not carry the project prefix. Each of these is addressed by an ID the service assigns
#: at creation, so there is no name for IAM to match on and `vpc/*` is the narrowest ARN that
#: can be written. Enumerated here rather than exempted by a pattern, so that a further
#: unscoped EC2 resource type is a visible edit to this list.
#:
#: This read EC2_NETWORK_RESOURCE_TYPES and held six until 2026-08-04. `launch-template` is
#: the seventh and the first that is not networking, which is why the name no longer says
#: network: infra/batch-compute-gpu-shapes.yaml declares an AWS::EC2::LaunchTemplate giving
#: the two eight-device P shapes a 500 GiB root volume, and the deploy role is what creates
#: it. It is granted in a statement of its own rather than added to the network one, so that
#: the two exemptions keep their separate reasons and neither statement's verbs reach the
#: other's resources.
EC2_ID_ADDRESSED_RESOURCE_TYPES = frozenset(
    {
        "internet-gateway",
        "launch-template",
        "route-table",
        "security-group",
        "security-group-rule",
        "subnet",
        "vpc",
    }
)

#: The second ARN in this policy that cannot carry the project prefix, and the reason is the
#: EC2 one arriving in another service: an event source mapping is addressed by a UUID Lambda
#: assigns at creation, so `event-source-mapping:*` is the narrowest ARN there is. Named here
#: as a single literal rather than allowed by a pattern, so that a second unscoped Lambda ARN
#: has to be a visible edit to this list. The one action granted on it is read-only.
EVENT_SOURCE_MAPPING_ARN = (
    "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:event-source-mapping:*"
)

#: What CI must never be able to do to a role, including to this one. Restated from the
#: Phase 1 module rather than imported, because the claim is about the whole role after the
#: amendment and a claim that stopped at a phase boundary would be a claim about a third of
#: one.
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

#: The deployer builds the queue and never runs anything on it. Each of these is named in
#: the template as deliberately absent, with the reason beside it.
BUILDS_BUT_NEVER_RUNS = frozenset(
    {
        "batch:CancelJob",
        "batch:SubmitJob",
        "batch:TerminateJob",
        "ec2:RunInstances",
        "iam:CreateServiceLinkedRole",
        "lambda:AddPermission",
        "lambda:InvokeFunction",
        "lambda:RemovePermission",
        "logs:PutLogEvents",
        "sqs:PurgeQueue",
        "sqs:ReceiveMessage",
        "sqs:SendMessage",
        "states:StartExecution",
        "states:StopExecution",
    }
)


def role() -> dict[str, Any]:
    return next(iam_roles(load_template(TEMPLATE_PATH)))


def statements(policy_name: str) -> list[dict[str, Any]]:
    matching = [policy for policy in role()["Policies"] if policy["PolicyName"] == policy_name]
    assert len(matching) == 1, f"expected exactly one inline policy named {policy_name}"
    document = matching[0]["PolicyDocument"]
    assert document["Version"] == "2012-10-17"
    listed = document["Statement"]
    assert isinstance(listed, list)
    return listed


def all_statements() -> list[dict[str, Any]]:
    """Every statement of every inline policy, in the order the template declares them."""
    return [statement for name in POLICY_NAMES for statement in statements(name)]


def resources(statement: dict[str, Any]) -> list[Any]:
    resource = statement["Resource"]
    return resource if isinstance(resource, list) else [resource]


def arns(statement: dict[str, Any]) -> list[str]:
    return [
        entry["Fn::Sub"] if isinstance(entry, dict) else entry for entry in resources(statement)
    ]


def actions(policy_name: str | None = None) -> list[str]:
    source = all_statements() if policy_name is None else statements(policy_name)
    return [action for statement in source for action in statement_actions(statement)]


def unscoped(policy_name: str) -> list[dict[str, Any]]:
    return [statement for statement in statements(policy_name) if "*" in arns(statement)]


def test_the_phase3_policy_is_a_third_inline_policy_and_leaves_the_other_two_alone() -> None:
    """Mutation: add the Phase 3 statements to ``deploy-phase2-admission-stacks``.

    IAM unions inline policies, so the effective permissions are identical either way and
    the only thing separation buys is that each policy still grants exactly what it granted
    when it was reviewed. It also keeps the names honest and keeps the phase boundary visible
    in ``aws iam list-role-policies``, which is how somebody reading the account rather than
    the repository can tell what a phase added.
    """
    policies = role()["Policies"]

    assert [policy["PolicyName"] for policy in policies] == POLICY_NAMES
    phase1 = set(actions(PHASE1_POLICY_NAME))
    assert all(action.startswith(("cloudformation:", "ecr:")) for action in phase1)
    phase2 = set(actions(PHASE2_POLICY_NAME))
    assert not [action for action in phase2 if action.startswith(("batch:", "ec2:", "sqs:"))]


def test_the_trust_policy_names_the_third_workflow_file_and_that_file_exists() -> None:
    """Mutation: rename ``deploy-phase3-batch.yml`` without changing this trust policy.

    ``job_workflow_ref`` is pinned with ``StringEquals``, so a rename revokes that file's
    deployments silently: the run reaches configure-aws-credentials, STS refuses the web
    identity, and nothing in the failure points at the rename. Checking each reference
    against the tree turns that into a red test in the commit that renames it.
    """
    condition = role()["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
    references = condition["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]

    assert isinstance(references, list)
    assert len(references) == 3
    assert references[2] == PHASE3_DEPLOY_WORKFLOW_REF
    assert (PROJECT_ROOT / PHASE3_DEPLOY_WORKFLOW).is_file()
    assert not [reference for reference in references if "*" in reference]
    assert len(set(references)) == len(references)


def test_the_six_measured_actions_are_on_star_and_in_a_statement_of_their_own() -> None:
    """Mutation: scope any one of them to an ARN.

    That is the mistake that produced Phase 2's second failed deploy. An action with no
    resource type cannot be granted on an ARN, so the ARN-scoped grant never matches and the
    deploy fails with a message naming the action -- with no hint that the scope is what
    refused it. Keeping them in one statement of their own means a reader can see which
    actions earned the wildcard rather than assuming the whole policy gave up on scoping.
    """
    matching = [
        statement
        for statement in statements(PHASE3_POLICY_NAME)
        if set(statement_actions(statement)) == NO_RESOURCE_TYPE_ACTIONS
    ]

    assert len(matching) == 1
    assert matching[0] == {
        "Effect": "Allow",
        "Action": sorted(NO_RESOURCE_TYPE_ACTIONS),
        "Resource": "*",
    }


def test_the_only_other_unscoped_statement_is_the_read_only_ec2_describes() -> None:
    """Mutation: add a mutating action to the EC2 describe statement.

    Two unscoped statements is one more than Phase 2 had, and they are separate because their
    reasons are separate: one is a simulator measurement with controls, the other is EC2's
    documented account-wide model. Folding them together would make a future addition
    inherit whichever reason the reader happened to read.
    """
    unscoped_statements = unscoped(PHASE3_POLICY_NAME)
    granted = {
        action for statement in unscoped_statements for action in statement_actions(statement)
    }

    assert len(unscoped_statements) == 2
    assert granted == NO_RESOURCE_TYPE_ACTIONS | EC2_DESCRIBE_ACTIONS
    assert all(action.startswith("ec2:Describe") for action in EC2_DESCRIBE_ACTIONS)
    # Read-only is what bounds the second one: it reveals the shape of the shared account's
    # networking and changes none of it.
    assert not [
        action
        for statement in unscoped_statements
        for action in statement_actions(statement)
        if action.startswith("ec2:") and not action.startswith("ec2:Describe")
    ]


def test_the_network_scope_can_change_egress_and_can_never_open_a_port() -> None:
    """Mutation: add ``ec2:AuthorizeSecurityGroupIngress`` while auditing the handler lists.

    It is the plausible mistake, because all three ingress verbs sit in the
    ``AWS::EC2::SecurityGroup`` handler's own permission lists next to the egress verbs that
    genuinely are needed. None of them is reachable: ``infra/batch-network.yaml`` declares no
    ``SecurityGroupIngress`` and says why, and CloudFormation gives a group with no ingress
    block exactly no ingress rules. Granting them anyway would let a deploy credential open a
    port on any security group in a shared account, for a rule this repository has decided
    never to write.
    """
    granted = {action for action in actions(PHASE3_POLICY_NAME) if action.startswith("ec2:")}
    egress = {action for action in granted if action.endswith("Egress")}

    assert not [action for action in granted if action.endswith("Ingress")]
    # The egress half is present, so the assertion above is about direction rather than about
    # security groups being out of scope entirely.
    assert egress == {
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
    }


def test_pass_role_names_whole_roles_and_never_a_prefix() -> None:
    """Mutation: replace the seven ARNs with ``sbsandbox-intern-edullm-*``.

    A prefix would let this role pass any role that ever takes such a name, with permissions
    nobody weighed against a deploy credential. Passing a role is how a principal lends its
    own limits away, so naming each one means adding an eighth is a visible edit to the
    template rather than something a new name inherits.

    This test predicted its own next edit and got it. It said four when it was written and
    named "one Phase 4 creates" as the thing a prefix would silently absorb; Phase 4 created
    three, and each had to be added here by hand. That is the control working, not drift.
    """
    matching = [
        statement
        for statement in statements(PHASE3_POLICY_NAME)
        if "iam:PassRole" in statement_actions(statement)
    ]

    assert len(matching) == 1
    statement = matching[0]
    passed = arns(statement)

    assert statement["Effect"] == "Allow"
    assert statement_actions(statement) == ["iam:PassRole"]
    assert [arn.rsplit("/", 1)[1] for arn in passed] == PASS_ROLE_NAMES
    assert not [arn for arn in passed if "*" in arn]
    assert all(arn.startswith("arn:${AWS::Partition}:iam::${AWS::AccountId}:role/") for arn in passed)


def test_iam_pass_role_is_still_the_only_iam_action_the_whole_role_holds() -> None:
    """Mutation: add ``iam:CreateServiceLinkedRole`` so Batch can mint its own role.

    That is the one this phase invites: ``CreateComputeEnvironment`` creates
    AWSServiceRoleForBatch on first use if the caller holds it, and the alternative is a
    person running one command. A pipeline that can create a role can create one stronger
    than itself, and the argument that a service-linked role is AWS's rather than ours makes
    it a weaker case, not a different one.
    """
    granted = set(actions())

    assert {action for action in granted if action.lower().startswith("iam:")} == {"iam:PassRole"}
    assert not granted & ROLE_MUTATING_ACTIONS


def test_the_deployer_builds_the_queue_and_can_never_run_anything_on_it() -> None:
    """Mutation: add ``batch:SubmitJob``.

    The admission state machine is the only principal in this account that may start compute,
    and it is reachable only through an execution the admission role started, which is
    reachable only from a GitHub job that cleared a protected environment. A deploy
    credential that could submit would be a compute path with none of that in front of it.
    """
    granted = set(actions())

    assert not granted & BUILDS_BUT_NEVER_RUNS
    assert "batch:CreateComputeEnvironment" in granted
    assert "batch:CreateJobQueue" in granted
    # ec2:RunInstances specifically: this role creates a compute environment and Batch's own
    # service-linked role launches the instances behind it.
    assert not [action for action in granted if action.startswith("ec2:Run")]


def test_every_scoped_phase3_arn_carries_the_project_prefix_or_is_a_named_exception() -> None:
    """Mutation: widen a Batch or SQS scope to ``sbsandbox-intern-*``.

    ``sbsandbox-intern-`` is the whole account's prefix and every intern's resources begin
    with it, so only the ``edullm`` segment makes a name ours. Two ARNs cannot carry the
    prefix at all, and both are enumerated rather than pattern-matched, because the exemption
    is what a future widening would hide behind. An EC2 resource of any of these types and a
    Lambda event source mapping are each addressed by an identifier the service assigns at
    creation, so `vpc/*`, `launch-template/*` and `event-source-mapping:*` are the narrowest
    ARNs there are; an eighth EC2 resource type or a second unscoped Lambda ARN has to be a
    visible edit.

    ``launch-template`` is the one that proved the point. It was added on 2026-08-04 for the
    first launch template this account has ever had, and the equality below is what made that
    a deliberate line in this list rather than something a ``vpc/*``-shaped pattern would have
    waved through.
    """
    scoped = [
        arn
        for statement in statements(PHASE3_POLICY_NAME)
        if statement not in unscoped(PHASE3_POLICY_NAME)
        for arn in arns(statement)
    ]
    ec2_arns = [arn for arn in scoped if ":ec2:" in arn]
    mapping_arns = [arn for arn in scoped if arn == EVENT_SOURCE_MAPPING_ARN]
    everything_else = [arn for arn in scoped if arn not in ec2_arns + mapping_arns]

    assert scoped
    assert all(arn.startswith("arn:${AWS::Partition}:") for arn in everything_else)
    assert all(RESOURCE_PREFIX in arn for arn in everything_else)
    assert not [arn for arn in everything_else if f"{SHARED_PREFIX}*" in arn]
    assert {
        arn.rsplit(":", 1)[1].split("/", 1)[0] for arn in ec2_arns
    } == EC2_ID_ADDRESSED_RESOURCE_TYPES
    assert all(arn.endswith("/*") for arn in ec2_arns)
    # Two statements may use the mapping ARN, and what separates them is the whole point.
    # A mapping is addressed by a UUID Lambda assigns at creation, so there is no name for
    # IAM to match on and the ARN cannot be narrowed. The widening that matters is a write
    # verb reaching every event source mapping in a shared account, and it is closed by a
    # condition rather than by an ARN: lambda:FunctionArn restricts each action to mappings
    # whose function is ours.
    #
    # The mutation this catches is somebody adding a write verb on the mapping ARN without
    # the condition -- which is what the deploy that forced this change would have produced
    # if the error message had simply been obeyed.
    assert len(mapping_arns) == 2
    granting = [
        statement
        for statement in statements(PHASE3_POLICY_NAME)
        if EVENT_SOURCE_MAPPING_ARN in arns(statement)
    ]
    read_only = [s for s in granting if statement_actions(s) == ["lambda:ListTags"]]
    assert len(read_only) == 1, "lambda:ListTags must keep a statement of its own"
    assert "Condition" not in read_only[0], "a read-only grant needs no condition"

    writing = [s for s in granting if s is not read_only[0]]
    assert len(writing) == 1
    assert statement_actions(writing[0]) == [
        "lambda:CreateEventSourceMapping",
        "lambda:DeleteEventSourceMapping",
        "lambda:GetEventSourceMapping",
        "lambda:UpdateEventSourceMapping",
    ]
    assert writing[0]["Condition"] == {
        "ArnLike": {
            "lambda:FunctionArn": {
                "Fn::Sub": (
                    "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}"
                    f":function:{RESOURCE_PREFIX}*"
                )
            }
        }
    }


def test_the_launch_template_grant_is_the_handler_list_and_stops_there() -> None:
    """Mutation: add ``ec2:ModifyLaunchTemplate``, or fold these into the network statement.

    The five actions are the ``AWS::EC2::LaunchTemplate`` handler's own permission list, read
    from the registry with ``cloudformation describe-type`` rather than inferred from the
    template: create needs ``CreateLaunchTemplate`` and ``CreateTags``, update needs
    ``CreateLaunchTemplateVersion``, ``CreateTags`` and ``DeleteTags``, delete needs
    ``DeleteLaunchTemplate``, ``DeleteTags`` and ``DescribeLaunchTemplates``. The describe is
    unscoped with the rest of EC2's describes because it has no resource type; these five
    measured ``allowed`` when granted on one launch-template ARN and simulated against that
    same ARN, so each supports resource-level permissions and an unscoped grant would be
    wider than anything needs.

    ``ec2:ModifyLaunchTemplate`` is the plausible addition and is in none of those lists. It
    sets a template's default version, and the two compute environments name a concrete
    version number rather than ``$Default``, so nothing here would ever call it.

    The statement is its own rather than merged into the EC2 network write scope, which
    already carries ``CreateTags`` and ``DeleteTags`` on six other ARNs. IAM authorizes a
    tagging call against the resource being tagged, so that grant cannot reach a launch
    template -- and widening its resource list to reach one would also hand every network
    verb in it a launch-template ARN it has no use for.
    """
    granting = [
        statement
        for statement in statements(PHASE3_POLICY_NAME)
        if any(action.endswith("LaunchTemplate") for action in statement_actions(statement))
    ]

    assert len(granting) == 1
    statement = granting[0]
    assert statement_actions(statement) == [
        "ec2:CreateLaunchTemplate",
        "ec2:CreateLaunchTemplateVersion",
        "ec2:CreateTags",
        "ec2:DeleteLaunchTemplate",
        "ec2:DeleteTags",
    ]
    assert arns(statement) == [
        "arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:launch-template/*"
    ]
    assert "ec2:ModifyLaunchTemplate" not in actions()
    # The read half stays on "*" with the other describes, and the two are different actions
    # on different handlers rather than a pair that should have travelled together.
    assert "ec2:DescribeLaunchTemplates" in EC2_DESCRIBE_ACTIONS
    assert "ec2:DescribeLaunchTemplates" not in statement_actions(statement)


def test_the_batch_scopes_cover_all_three_resource_types_the_stack_creates() -> None:
    """Mutation: fold the tagging actions into the compute-environment statement only.

    Batch uses one set of tagging actions for every resource it owns. Scoping them to one
    resource type means tagging a queue is denied by a policy that looks as though it covers
    tagging, and the deploy fails at a step that reads as unrelated.
    """
    batch_statements = [
        statement
        for statement in statements(PHASE3_POLICY_NAME)
        if all(action.startswith("batch:") for action in statement_actions(statement))
        and statement not in unscoped(PHASE3_POLICY_NAME)
    ]
    tagging = [
        statement
        for statement in batch_statements
        if "batch:TagResource" in statement_actions(statement)
    ]

    assert len(tagging) == 1
    covered = {arn.rsplit(":", 1)[1].split("/", 1)[0] for arn in arns(tagging[0])}
    assert covered == {"compute-environment", "job-definition", "job-queue"}
    # RegisterJobDefinition mints a revision and the revision is part of the ARN, so a grant
    # on the bare name authorizes the first deploy and denies the second.
    definitions = [
        arn for statement in batch_statements for arn in arns(statement) if ":job-definition/" in arn
    ]
    assert all(arn.endswith("sbsandbox-intern-edullm-*") for arn in definitions)


def test_creating_a_job_queue_is_authorized_against_the_compute_environment_as_well() -> None:
    """Mutation: scope ``batch:CreateJobQueue`` to the job queue alone, as it first was.

    A queue names the compute environments it feeds from, and Batch authorizes the create
    against those environments as well as against the queue. Scoped to the queue alone the
    call is denied outright, with a message naming ``batch:CreateJobQueue`` on a
    ``compute-environment`` ARN -- an action that reads as being about the queue, refused on
    a resource that is not one. That denial cost a deploy and rolled the compute stack back.

    Measured rather than read. The Service Authorization Reference lists the resource types
    an action accepts without saying a single call is authorized against more than one of
    them, so nothing short of the failure said this.

    ``batch:DeleteJobQueue`` is deliberately excluded: its request carries no compute
    environment, so there is nothing to authorize against and widening it would grant reach
    the call cannot use.
    """
    for action in ("batch:CreateJobQueue", "batch:UpdateJobQueue"):
        granting = [
            statement
            for statement in statements(PHASE3_POLICY_NAME)
            if action in statement_actions(statement)
        ]
        assert len(granting) == 1, f"{action} should be granted in exactly one statement"
        covered = {arn.rsplit(":", 1)[1].split("/", 1)[0] for arn in arns(granting[0])}
        assert covered == {"job-queue", "compute-environment"}, (
            f"{action} is scoped to {covered}, which denies the call Batch actually makes"
        )

    deleting = [
        statement
        for statement in statements(PHASE3_POLICY_NAME)
        if "batch:DeleteJobQueue" in statement_actions(statement)
    ]
    assert len(deleting) == 1
    assert {arn.rsplit(":", 1)[1].split("/", 1)[0] for arn in arns(deleting[0])} == {"job-queue"}


def test_the_event_source_mapping_grants_arrive_instead_of_lambda_add_permission() -> None:
    """Mutation: grant ``lambda:AddPermission`` and target the recorder directly.

    D6 chose the queue precisely so that this decision from Phase 2 -- "the deployer creates
    the validator but may neither run it nor change who may run it" -- did not have to be
    reversed. An event source mapping decides which queue a function reads; it does not
    decide who may invoke the function, which is the line ``AddPermission`` crosses.
    """
    granted = set(actions(PHASE3_POLICY_NAME))
    lambda_actions = {action for action in granted if action.startswith("lambda:")}

    assert lambda_actions == {
        "lambda:CreateEventSourceMapping",
        "lambda:DeleteEventSourceMapping",
        "lambda:GetEventSourceMapping",
        "lambda:UpdateEventSourceMapping",
        # The read half, and the one that is not authorized against the function ARN the
        # other four use: the mapping's Read handler lists its tags, and lambda:ListTags
        # authorizes against the ARN it is handed. Granting it on the function prefix -- as
        # the Phase 2 policy already does, for the function's own tags -- produces a scope
        # that cannot match and a denial naming the action, which is the Phase 2 failure.
        "lambda:ListTags",
    }
    sqs_actions = {action for action in granted if action.startswith("sqs:")}
    assert sqs_actions
    assert not sqs_actions & {"sqs:SendMessage", "sqs:ReceiveMessage", "sqs:PurgeQueue"}


def test_the_phase3_policy_reaches_no_service_the_phase_does_not_deploy() -> None:
    """Mutation: add an ``s3:`` grant here for the outputs bucket.

    It would work and it would be wrong twice over. The Phase 2 policy already scopes the
    bucket lifecycle to ``sbsandbox-intern-edullm-*``, which is what deploys the outputs
    bucket, so a second grant would be a duplicate whose narrower scope reads as the
    operative one -- and the phase boundary this file exists to keep visible would stop
    telling the truth about which phase granted what.
    """
    granted = actions(PHASE3_POLICY_NAME)
    services = {action.split(":", 1)[0] for action in granted}

    # ``scheduler`` is EventBridge Scheduler, which is a different service from EventBridge
    # rules with its own ARN space, and it is here because infra/expiry-janitor.yaml uses a
    # schedule rather than a rule: a rule targeting a Lambda would need lambda:AddPermission,
    # which the Phase 2 policy withholds. infra/batch-events.yaml recorded the rule for that
    # fork -- a capability added rather than a restriction removed -- and this is the second
    # time it has been applied.
    assert services == {
        "batch",
        "cloudwatch",
        "ec2",
        "events",
        "iam",
        "lambda",
        "logs",
        "scheduler",
        "sqs",
    }
    assert not any("*" in action for action in granted)
    assert not ACCOUNT_LITERAL.search(TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_the_phase3_log_scope_cannot_reach_the_admission_execution_record() -> None:
    """Mutation: widen the log group scope to ``sbsandbox-intern-edullm-*``.

    The Batch job logs live under ``/aws/batch/`` and the admission execution record lives
    under ``/aws/vendedlogs/states/``. A Phase 3 deploy that could delete the second would be
    able to remove the audit trail of who admitted which run, from a stack that has nothing
    to do with admission.
    """
    log_arns = [
        arn
        for statement in statements(PHASE3_POLICY_NAME)
        for arn in arns(statement)
        if ":log-group:" in arn
    ]
    granted = {
        action for action in actions(PHASE3_POLICY_NAME) if action.startswith("logs:")
    }

    assert log_arns
    assert all(":log-group:/aws/batch/sbsandbox-intern-edullm-" in arn for arn in log_arns)
    assert not [arn for arn in log_arns if "vendedlogs" in arn]
    # The deployer creates the group a job's output is written into and must not be able to
    # write into it or remove part of it.
    assert not granted & {"logs:PutLogEvents", "logs:DeleteLogStream", "logs:GetLogEvents"}
