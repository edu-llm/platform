import json
from typing import Any

import yaml
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
INLINE_POLICY_NAME = "publish-research-images"
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


def registered_repositories() -> list[dict[str, Any]]:
    """The research repositories this platform publishes images for, read once.

    Every publisher-role assertion below is derived from this rather than restating a
    literal, because the failure being guarded against is the two drifting apart: a
    repository registered and not trusted is inert, and a repository trusted and not
    registered is a grant nobody reviewed.
    """
    return list(
        yaml.safe_load(
            (PROJECT_ROOT / "config" / "repositories.yaml").read_text(encoding="utf-8")
        )["repositories"]
    )


def sub_pattern_for(entry: dict[str, Any]) -> str:
    return (
        f"repo:edu-llm@306859726/{entry['repository']}@{entry['github_repository_id']}"
        ":ref:refs/heads/*"
    )


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
    # The name is not load-bearing and is asserted only to be one name rather than two:
    # a second inline policy is a second place permissions can be added.
    assert isinstance(policies[0]["PolicyName"], str)


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
    registered = registered_repositories()
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == {"Federated": OIDC_PROVIDER}
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"

    equals = statement["Condition"]["StringEquals"]
    # The three that must never become a list, because each one is a single fact about
    # where a token may come from. Widening any of them is how this role stops being about
    # this organisation, this workflow file, or this audience.
    assert equals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    assert equals["token.actions.githubusercontent.com:job_workflow_ref"] == (
        "edu-llm/platform/.github/workflows/build-research-image.yml@refs/heads/main"
    )
    assert equals["token.actions.githubusercontent.com:repository_owner_id"] == "306859726"

    # The one that grows, and grows only with the registry. A list under StringEquals means
    # "any of", so an id here that nothing registers is a repository authorised to publish
    # that no review ever saw.
    trusted = equals["token.actions.githubusercontent.com:repository_id"]
    assert set(as_list(trusted)) == {str(entry["github_repository_id"]) for entry in registered}
    assert set(as_list(statement["Condition"]["StringLike"][
        "token.actions.githubusercontent.com:sub"
    ])) == {sub_pattern_for(entry) for entry in registered}
    assert set(statement["Condition"]) == {"StringEquals", "StringLike"}


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
    # One destination per registered repository and no wildcard. A single ARN ending
    # `repository/sbsandbox-intern-edullm-*` would cover every future repository without
    # anybody deciding to, which is the decision this list exists to make visible.
    assert {
        str(resource["Fn::Sub"]).rsplit("repository/", 1)[-1]
        for resource in as_list(repository_statement["Resource"])
    } == {entry["ecr_repository"] for entry in registered_repositories()}

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

    # Two kinds of wildcard and no third. One `ref:refs/heads/*` per registered repository,
    # because a build may run from any branch of a repository this platform trusts; and
    # exactly one bare `*`, for ecr:GetAuthorizationToken, which is not a resource-level
    # action. Anything else is a wildcard nobody argued for.
    wildcards = [value for value in strings if "*" in value]
    expected = sorted(sub_pattern_for(entry) for entry in registered_repositories())

    assert sorted(value for value in wildcards if value != "*") == expected
    assert wildcards.count("*") == 1
    assert not ACCOUNT_LITERAL.search(PUBLISHER_TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_publisher_outputs_only_the_role_name_and_arn() -> None:
    template = load_template(PUBLISHER_TEMPLATE_PATH)
    logical_id, _ = resource_of_type(template, "AWS::IAM::Role")

    assert template["Outputs"] == {
        "RoleName": {"Value": {"Ref": logical_id}},
        "RoleArn": {"Value": {"Fn::GetAtt": [logical_id, "Arn"]}},
    }


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def ecr_repositories() -> dict[str, dict[str, Any]]:
    """Every ECR repository the template creates, by logical id.

    This used to be ``resource_of_type``, which asserts there is exactly one. That was
    right while one research repository was registered and became wrong the moment a
    second was: the invariant was never "there is one repository", it was "every
    repository has these properties", and the count was standing in for it.
    """
    template = load_template(ECR_TEMPLATE_PATH)
    found = {
        logical_id: resource
        for logical_id, resource in template["Resources"].items()
        if isinstance(resource, dict) and resource.get("Type") == "AWS::ECR::Repository"
    }
    assert found, "a check over every repository must observe at least one"
    return found


def test_the_template_creates_one_repository_for_every_registered_research_repository() -> None:
    """Reads BOTH sides. Mutation: register a repository and not create its ECR repository.

    The failure of forgetting is not an error anybody sees. The registration says where
    images go, the build pushes there, and the push fails with a repository-not-found deep
    inside a publish job -- long after the reviewer who approved the registration has
    stopped looking.
    """
    registered = {
        entry["ecr_repository"]
        for entry in yaml.safe_load(
            (PROJECT_ROOT / "config" / "repositories.yaml").read_text(encoding="utf-8")
        )["repositories"]
    }
    created = {
        resource["Properties"]["RepositoryName"] for resource in ecr_repositories().values()
    }

    assert created == registered


def test_every_ecr_repository_is_encrypted_scanned_immutable_and_retained() -> None:
    """Mutation: add a second repository without the properties the first one has.

    Copying a block is how a second repository gets created, and dropping one line while
    copying is how it gets created without immutability -- which is the property the whole
    digest-pinning design rests on, and the only one whose absence is invisible until
    somebody overwrites a tag.
    """
    for logical_id, repository in ecr_repositories().items():
        assert repository["DeletionPolicy"] == "Retain", logical_id
        assert repository["UpdateReplacePolicy"] == "Retain", logical_id
        assert repository["Properties"]["EncryptionConfiguration"] == {
            "EncryptionType": "AES256"
        }, logical_id
        assert repository["Properties"]["ImageScanningConfiguration"] == {
            "ScanOnPush": True
        }, logical_id
        assert repository["Properties"]["ImageTagMutability"] == "IMMUTABLE", logical_id


def test_ecr_lifecycle_expires_old_untagged_images_and_nothing_else() -> None:
    # Every repository, not the first one. A repository added without a lifecycle policy
    # keeps untagged layers for ever, which costs money quietly rather than failing.
    policies = [
        json.loads(repository["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])
        for repository in ecr_repositories().values()
    ]
    assert len({json.dumps(entry, sort_keys=True) for entry in policies}) == 1, (
        "every repository should expire on the same terms; two policies here means one of "
        "them was edited and the other was not"
    )
    policy = policies[0]
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
        ]
    }


def test_no_ecr_lifecycle_rule_can_expire_an_image_a_run_names() -> None:
    """Mutation: cap tagged images at some larger number, or expire them after some age.

    A run manifest names its image by digest, so expiring a tagged image retracts a
    recorded result rather than reclaiming a build artefact. A count of 50 did exactly
    that to 100 of 157 recorded runs before it was removed, and the shape of the mistake
    -- not the number in it -- is what this holds the template to. Asserted separately
    from the equality above so that the reason survives: an equality that someone updates
    to match a new policy says nothing about why the new policy is wrong.
    """
    for logical_id, repository in ecr_repositories().items():
        policy = json.loads(repository["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"])
        for rule in policy["rules"]:
            selection = rule["selection"]
            assert selection["tagStatus"] == "untagged", (
                f"{logical_id} rule {rule['rulePriority']} selects "
                f"{selection['tagStatus']!r} images. Only untagged images may be expired: "
                "anything tagged is the image some recorded run declares it ran."
            )


def test_ecr_template_has_no_iam_policy_principal_or_account_literal() -> None:
    template = load_template(ECR_TEMPLATE_PATH)
    strings = list(walk_strings(template))

    # Every repository. A resource policy on the second one grants cross-account access
    # that the first one's absence of a policy says nothing about.
    for logical_id, repository in ecr_repositories().items():
        assert "RepositoryPolicyText" not in repository["Properties"], logical_id
    assert not any(value.startswith("AWS::IAM::") for value in strings)
    assert "AWS::ECR::RepositoryPolicy" not in strings
    assert "Principal" not in strings
    assert "PolicyDocument" not in strings
    assert not ACCOUNT_LITERAL.search(ECR_TEMPLATE_PATH.read_text(encoding="utf-8"))


def test_the_template_outputs_every_repository_it_creates_and_nothing_else() -> None:
    """Mutation: create a repository and not output it.

    An output is how anything downstream refers to the repository by name rather than by
    repeating the literal, so one that is missing is a second spelling waiting to happen.
    """
    template = load_template(ECR_TEMPLATE_PATH)
    referenced = {
        value["Value"]["Ref"] for value in template["Outputs"].values() if "Ref" in value["Value"]
    }

    assert referenced == set(ecr_repositories())


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
