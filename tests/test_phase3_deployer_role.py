"""What the Phase 3 amendment adds to the deploy role, and everything it must not add.

``tests/test_phase1_deployer_role.py`` pins the whole role: the trust policy, the inventory
of scoped resources, and the two things CI may never do. This module is about the third
inline policy specifically, and it exists because two of its properties have already cost
this repository a failed deploy each.

The first is the ``"*"`` set. An action whose service authorization reference lists no
resource type cannot be granted on an ARN at all, and scoping one produces a deploy failure
that names the action without hinting that the scope is what refused it. Phase 2 learned
that with ``logs:DescribeLogGroups``; Phase 3 has six more, measured with controls, and the
next reader's instinct will be to tidy them away.

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
#: they are account-wide by EC2's documented model, not by a simulator measurement, and the
#: CloudFormation handlers for VPC, Subnet, RouteTable, InternetGateway and SecurityGroup
#: call them on every Read. All of them are read-only.
EC2_DESCRIBE_ACTIONS = frozenset(
    {
        "ec2:DescribeAvailabilityZones",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeNetworkAcls",
        "ec2:DescribeRouteTables",
        "ec2:DescribeSecurityGroupRules",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSubnets",
        "ec2:DescribeTags",
        "ec2:DescribeVpcAttribute",
        "ec2:DescribeVpcs",
    }
)

#: Four whole role ARNs, in the order the template writes them. Written out here for the
#: same reason they are written out there: a name that has to be typed twice cannot grow a
#: wildcard on one side only.
PASS_ROLE_NAMES = [
    "sbsandbox-intern-edullm-batch-execution",
    "sbsandbox-intern-edullm-batch-workload",
    "sbsandbox-intern-edullm-batch-instance",
    "sbsandbox-intern-edullm-lifecycle-lambda",
]

#: The EC2 network write scope, and the one place in this role where a resource ARN does not
#: carry the project prefix. An EC2 network resource is addressed by an ID the service
#: assigns at creation, so there is no name for IAM to match on and `vpc/*` is the narrowest
#: ARN that can be written. Enumerated here rather than exempted by a pattern, so that a
#: sixth unscoped EC2 resource type is a visible edit to this list.
EC2_NETWORK_RESOURCE_TYPES = frozenset(
    {
        "internet-gateway",
        "route-table",
        "security-group",
        "security-group-rule",
        "subnet",
        "vpc",
    }
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


def test_pass_role_names_four_whole_roles_and_never_a_prefix() -> None:
    """Mutation: replace the four ARNs with ``sbsandbox-intern-edullm-*``.

    A prefix would let this role pass any role that ever takes such a name, including one
    Phase 4 creates with permissions nobody weighed against a deploy credential. Passing a
    role is how a principal lends its own limits away, so naming each one means adding a
    fifth is a visible edit to the template rather than something a new name inherits.
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


def test_every_scoped_phase3_arn_carries_the_project_prefix_or_is_a_named_ec2_exception(
) -> None:
    """Mutation: widen a Batch or SQS scope to ``sbsandbox-intern-*``.

    ``sbsandbox-intern-`` is the whole account's prefix and every intern's resources begin
    with it, so only the ``edullm`` segment makes a name ours. The EC2 network ARNs are the
    single exception and they are enumerated rather than pattern-matched: an EC2 network
    resource is addressed by an ID the service assigns, so `vpc/*` is the narrowest ARN
    there is and a sixth resource type appearing under it has to be a visible edit.
    """
    scoped = [
        arn
        for statement in statements(PHASE3_POLICY_NAME)
        if statement not in unscoped(PHASE3_POLICY_NAME)
        for arn in arns(statement)
    ]
    ec2_arns = [arn for arn in scoped if ":ec2:" in arn]
    everything_else = [arn for arn in scoped if arn not in ec2_arns]

    assert scoped
    assert all(arn.startswith("arn:${AWS::Partition}:") for arn in everything_else)
    assert all(RESOURCE_PREFIX in arn for arn in everything_else)
    assert not [arn for arn in everything_else if f"{SHARED_PREFIX}*" in arn]
    assert {arn.rsplit(":", 1)[1].split("/", 1)[0] for arn in ec2_arns} == EC2_NETWORK_RESOURCE_TYPES
    assert all(arn.endswith("/*") for arn in ec2_arns)


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

    assert services == {"batch", "cloudwatch", "ec2", "events", "iam", "lambda", "logs", "sqs"}
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
