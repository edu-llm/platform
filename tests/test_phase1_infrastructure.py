import json

from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    IAM_ROOT,
    INFRA_ROOT,
    OIDC_PROVIDER,
    PROJECT_ROOT,
    iam_roles,
    load_template,
    resource_of_type,
    walk_strings,
)

PUBLISHER_TEMPLATE_PATH = IAM_ROOT / "ecr-publisher-role.yaml"
ECR_TEMPLATE_PATH = INFRA_ROOT / "ecr-repositories.yaml"

ROLE_NAME = "sbsandbox-intern-edullm-ecr-publisher"
INLINE_POLICY_NAME = "publish-olmo-core-images"
OLMO_CORE_REPOSITORY_NAME = "sbsandbox-intern-edullm-olmo-core"
OLMO_CORE_REPOSITORY = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:"
        "repository/sbsandbox-intern-edullm-olmo-core"
    )
}
EXPECTED_REPOSITORY_ACTIONS = {
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:CompleteLayerUpload",
    "ecr:DescribeImageScanFindings",
    "ecr:DescribeImages",
    "ecr:GetDownloadUrlForLayer",
    "ecr:InitiateLayerUpload",
    "ecr:PutImage",
    "ecr:UploadLayerPart",
}
FORBIDDEN_ACTION_FRAGMENTS = {
    "batch:",
    "ecr:Create",
    "ecr:Delete",
    "ecr:SetRepositoryPolicy",
    "ecr:TagResource",
    "ecr:UntagResource",
    "ecr:Update",
    "ec2:",
    "iam:",
    "s3:",
}


def test_publisher_template_creates_only_the_inline_scoped_role() -> None:
    template = load_template(PUBLISHER_TEMPLATE_PATH)

    resources = template["Resources"]
    assert len(resources) == 1
    _, role = resource_of_type(template, "AWS::IAM::Role")
    role_properties = role["Properties"]
    assert role_properties["RoleName"] == ROLE_NAME
    assert role_properties["PermissionsBoundary"] == BOUNDARY
    assert role_properties["MaxSessionDuration"] <= 3600

    policies = role_properties["Policies"]
    assert len(policies) == 1
    assert policies[0]["PolicyName"] == INLINE_POLICY_NAME


def test_no_laptop_template_uses_a_managed_policy_it_could_never_update() -> None:
    # InternSandboxBoundary explicitly denies iam:CreatePolicyVersion,
    # iam:SetDefaultPolicyVersion and iam:DeletePolicyVersion on every policy, verified by
    # simulation against boundary v5. A customer managed policy is therefore write-once in
    # this account: the first permission change would fail the stack update. Inline role
    # policies use iam:PutRolePolicy, which the boundary permits on sbsandbox-intern-* names.
    # Generic "prefer managed policies" advice does not survive this constraint.
    for path in sorted((*IAM_ROOT.rglob("*.yaml"), *IAM_ROOT.rglob("*.yml"))):
        template = load_template(path)
        for resource in template.get("Resources", {}).values():
            assert resource.get("Type") != "AWS::IAM::ManagedPolicy", (
                f"managed policy cannot be updated under this boundary: "
                f"{path.relative_to(PROJECT_ROOT)}"
            )


def test_publisher_trusts_only_the_existing_github_oidc_provider() -> None:
    role = next(iam_roles(load_template(PUBLISHER_TEMPLATE_PATH)))
    trust = role["AssumeRolePolicyDocument"]

    assert trust["Version"] == "2012-10-17"
    assert len(trust["Statement"]) == 1
    statement = trust["Statement"][0]
    assert statement == {
        "Effect": "Allow",
        "Principal": {"Federated": OIDC_PROVIDER},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
            "StringEquals": {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                "token.actions.githubusercontent.com:job_workflow_ref": (
                    "edu-llm/platform/.github/workflows/build-research-image.yml@refs/heads/main"
                ),
                "token.actions.githubusercontent.com:repository_owner_id": "306859726",
                "token.actions.githubusercontent.com:repository_id": "1306868157",
            },
            "StringLike": {
                "token.actions.githubusercontent.com:sub": (
                    "repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/heads/*"
                )
            },
        },
    }


def test_publisher_permissions_are_the_exact_phase1_ecr_permissions() -> None:
    template = load_template(PUBLISHER_TEMPLATE_PATH)
    _, role = resource_of_type(template, "AWS::IAM::Role")
    document = role["Properties"]["Policies"][0]["PolicyDocument"]
    assert document["Version"] == "2012-10-17"
    statements = document["Statement"]
    assert len(statements) == 2

    token_statement = next(
        statement for statement in statements if statement["Action"] == "ecr:GetAuthorizationToken"
    )
    assert token_statement == {
        "Effect": "Allow",
        "Action": "ecr:GetAuthorizationToken",
        "Resource": "*",
    }

    repository_statement = next(
        statement for statement in statements if statement is not token_statement
    )
    assert repository_statement["Effect"] == "Allow"
    assert set(repository_statement["Action"]) == EXPECTED_REPOSITORY_ACTIONS
    assert len(repository_statement["Action"]) == len(EXPECTED_REPOSITORY_ACTIONS)
    assert repository_statement["Resource"] == OLMO_CORE_REPOSITORY

    actions = [
        action
        for statement in statements
        for action in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    ]
    assert all(action.startswith("ecr:") and "*" not in action for action in actions)
    assert not any(
        forbidden.lower() in action.lower()
        for action in actions
        for forbidden in FORBIDDEN_ACTION_FRAGMENTS
    )


def test_template_has_only_the_two_required_wildcards_and_no_account_literal() -> None:
    template = load_template(PUBLISHER_TEMPLATE_PATH)
    strings = list(walk_strings(template))

    assert [value for value in strings if "*" in value] == [
        "repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/heads/*",
        "*",
    ]
    assert not ACCOUNT_LITERAL.search(PUBLISHER_TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_publisher_outputs_only_the_role_name_and_arn() -> None:
    template = load_template(PUBLISHER_TEMPLATE_PATH)
    logical_id, _ = resource_of_type(template, "AWS::IAM::Role")

    assert template["Outputs"] == {
        "RoleName": {"Value": {"Ref": logical_id}},
        "RoleArn": {"Value": {"Fn::GetAtt": [logical_id, "Arn"]}},
    }


def test_ecr_template_creates_exactly_one_finished_image_repository() -> None:
    template = load_template(ECR_TEMPLATE_PATH)

    resources = template["Resources"]
    assert len(resources) == 1
    logical_id, repository = resource_of_type(template, "AWS::ECR::Repository")
    assert list(resources) == [logical_id]
    assert repository["Properties"]["RepositoryName"] == OLMO_CORE_REPOSITORY_NAME


def test_ecr_repository_is_encrypted_scanned_immutable_and_retained() -> None:
    template = load_template(ECR_TEMPLATE_PATH)
    _, repository = resource_of_type(template, "AWS::ECR::Repository")

    assert repository["DeletionPolicy"] == "Retain"
    assert repository["UpdateReplacePolicy"] == "Retain"
    assert repository["Properties"]["EncryptionConfiguration"] == {"EncryptionType": "AES256"}
    assert repository["Properties"]["ImageScanningConfiguration"] == {"ScanOnPush": True}
    assert repository["Properties"]["ImageTagMutability"] == "IMMUTABLE"


def test_ecr_lifecycle_expires_old_untagged_and_caps_all_tagged_images() -> None:
    template = load_template(ECR_TEMPLATE_PATH)
    _, repository = resource_of_type(template, "AWS::ECR::Repository")
    policy_text = repository["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"]
    assert isinstance(policy_text, str)

    policy = json.loads(policy_text)
    assert policy == {
        "rules": [
            {
                "rulePriority": 1,
                "description": "Expire untagged images older than 7 days",
                "selection": {
                    "tagStatus": "untagged",
                    "countType": "sinceImagePushed",
                    "countUnit": "days",
                    "countNumber": 7,
                },
                "action": {"type": "expire"},
            },
            {
                "rulePriority": 2,
                "description": "Retain at most 50 tagged images",
                "selection": {
                    "tagStatus": "tagged",
                    "tagPatternList": ["*"],
                    "countType": "imageCountMoreThan",
                    "countNumber": 50,
                },
                "action": {"type": "expire"},
            },
        ]
    }


def test_ecr_template_has_no_iam_policy_principal_or_account_literal() -> None:
    template = load_template(ECR_TEMPLATE_PATH)
    _, repository = resource_of_type(template, "AWS::ECR::Repository")
    strings = list(walk_strings(template))

    assert "RepositoryPolicyText" not in repository["Properties"]
    assert not any(value.startswith("AWS::IAM::") for value in strings)
    assert "AWS::ECR::RepositoryPolicy" not in strings
    assert "Principal" not in strings
    assert "PolicyDocument" not in strings
    assert not ACCOUNT_LITERAL.search(ECR_TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_ecr_template_outputs_only_the_repository_name() -> None:
    template = load_template(ECR_TEMPLATE_PATH)
    logical_id, _ = resource_of_type(template, "AWS::ECR::Repository")

    assert template["Outputs"] == {
        "RepositoryName": {"Value": {"Ref": logical_id}},
    }


def test_iam_resources_are_confined_and_all_roles_have_boundaries() -> None:
    yaml_paths = sorted((*INFRA_ROOT.rglob("*.yaml"), *INFRA_ROOT.rglob("*.yml")))
    assert PUBLISHER_TEMPLATE_PATH in yaml_paths
    assert ECR_TEMPLATE_PATH in yaml_paths

    for path in yaml_paths:
        template = load_template(path)
        resources = template.get("Resources", {})
        assert isinstance(resources, dict)
        for resource in resources.values():
            if not isinstance(resource, dict):
                continue
            resource_type = resource.get("Type")
            if not path.is_relative_to(IAM_ROOT):
                assert not (
                    isinstance(resource_type, str) and resource_type.startswith("AWS::IAM::")
                ), f"IAM resource found outside infra/iam/: {path.relative_to(PROJECT_ROOT)}"
            if resource_type == "AWS::IAM::Role":
                properties = resource.get("Properties")
                assert isinstance(properties, dict)
                assert "PermissionsBoundary" in properties, (
                    f"IAM role lacks PermissionsBoundary: {path.relative_to(PROJECT_ROOT)}"
                )
