from typing import Any

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
INLINE_POLICY_NAME = "deploy-phase1-stacks"
DEPLOY_WORKFLOW = ".github/workflows/deploy-phase1-ecr.yml"
JOB_WORKFLOW_REF = f"edu-llm/platform/{DEPLOY_WORKFLOW}@refs/heads/main"
SUBJECT = "repo:edu-llm@306859726/platform@1311508598:ref:refs/heads/main"

STACK_RESOURCE = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:"
        "stack/sbsandbox-intern-*/*"
    )
}
REPOSITORY_RESOURCE = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:repository/sbsandbox-intern-*"
    )
}
UNSCOPED_STATEMENT = {
    "Effect": "Allow",
    "Action": "cloudformation:ValidateTemplate",
    "Resource": "*",
}
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
    "ecr:DeleteRepositoryPolicy",
    "ecr:DescribeRepositories",
    "ecr:GetLifecyclePolicy",
    "ecr:GetRepositoryPolicy",
    "ecr:ListTagsForResource",
    "ecr:PutImageScanningConfiguration",
    "ecr:PutImageTagMutability",
    "ecr:PutLifecyclePolicy",
    "ecr:SetRepositoryPolicy",
    "ecr:TagResource",
    "ecr:UntagResource",
}
FORBIDDEN_ACTION_PREFIXES = ("batch:", "ec2:", "iam:", "s3:")


def _role() -> dict[str, Any]:
    return next(iam_roles(load_template(TEMPLATE_PATH)))


def _statements() -> list[dict[str, Any]]:
    document = _role()["Policies"][0]["PolicyDocument"]
    assert document["Version"] == "2012-10-17"
    statements = document["Statement"]
    assert isinstance(statements, list)
    return statements


def _statement_scoped_to(resource: object) -> dict[str, Any]:
    matching = [statement for statement in _statements() if statement["Resource"] == resource]
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

    policies = properties["Policies"]
    assert len(policies) == 1
    assert policies[0]["PolicyName"] == INLINE_POLICY_NAME


def test_deployer_trusts_only_the_main_branch_deploy_workflow_through_github_oidc() -> None:
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
                "token.actions.githubusercontent.com:job_workflow_ref": JOB_WORKFLOW_REF,
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


def test_trusted_job_workflow_ref_names_the_workflow_file_that_deploys_the_stack() -> None:
    condition = _role()["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
    reference = condition["StringEquals"]["token.actions.githubusercontent.com:job_workflow_ref"]
    path, separator, ref = reference.partition("@")

    assert separator == "@"
    assert ref == "refs/heads/main"
    assert path == f"edu-llm/platform/{DEPLOY_WORKFLOW}"
    assert (PROJECT_ROOT / DEPLOY_WORKFLOW).is_file()


def test_deployer_can_only_touch_stacks_carrying_the_sandbox_prefix() -> None:
    statement = _statement_scoped_to(STACK_RESOURCE)
    actions = statement_actions(statement)

    assert statement["Effect"] == "Allow"
    assert set(actions) == EXPECTED_STACK_ACTIONS
    assert len(actions) == len(EXPECTED_STACK_ACTIONS)


def test_validate_template_is_the_only_action_granted_without_a_resource_scope() -> None:
    # cloudformation:ValidateTemplate has no resource type at all in the CloudFormation
    # service authorization reference, so "*" is the narrowest grant that exists for it.
    # GetTemplateSummary does accept a stack ARN, and `aws cloudformation deploy` only
    # ever calls it as get_template_summary(StackName=...), so it is scoped with the rest
    # instead of being parked here.
    statements = _statements()
    unscoped = [statement for statement in statements if statement["Resource"] == "*"]

    assert unscoped == [UNSCOPED_STATEMENT]
    assert all(
        isinstance(statement["Resource"], dict)
        for statement in statements
        if statement is not unscoped[0]
    )


def test_deployer_can_only_touch_ecr_repositories_carrying_the_sandbox_prefix() -> None:
    statement = _statement_scoped_to(REPOSITORY_RESOURCE)
    actions = statement_actions(statement)

    assert statement["Effect"] == "Allow"
    assert set(actions) == EXPECTED_REPOSITORY_ACTIONS
    assert len(actions) == len(EXPECTED_REPOSITORY_ACTIONS)


def test_deployer_grants_no_identity_storage_or_compute_action() -> None:
    # The retired shared role could reach IAM, S3, Batch and EC2. This role deploys
    # CloudFormation stacks that contain ECR repositories and nothing else, so any
    # action outside those two services is a regression towards what we are replacing.
    actions = [action for statement in _statements() for action in statement_actions(statement)]

    assert actions
    assert not [
        action for action in actions if action.lower().startswith(FORBIDDEN_ACTION_PREFIXES)
    ]
    assert all(action.startswith(("cloudformation:", "ecr:")) for action in actions)
    assert not any("*" in action for action in actions)


def test_deployer_template_wildcards_are_only_the_declared_scopes() -> None:
    strings = list(walk_strings(load_template(TEMPLATE_PATH)))

    assert [value for value in strings if "*" in value] == [
        STACK_RESOURCE["Fn::Sub"],
        "*",
        REPOSITORY_RESOURCE["Fn::Sub"],
    ]
    assert not ACCOUNT_LITERAL.search(TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_deployer_outputs_only_the_role_name_and_arn() -> None:
    template = load_template(TEMPLATE_PATH)
    logical_id, _ = resource_of_type(template, "AWS::IAM::Role")

    assert template["Outputs"] == {
        "RoleName": {"Value": {"Ref": logical_id}},
        "RoleArn": {"Value": {"Fn::GetAtt": [logical_id, "Arn"]}},
    }
