"""Comparing a role the account holds against the template that is supposed to describe it.

Both Phase 1 roles were created once from a laptop and neither is redeployed by CI, so
the committed template is a claim about the account rather than a description of it. The
comparison under test is what turns the claim back into something checkable.

Two things are worth reading carefully. The first is direction: a deployed role that is
*wider* than its template is a security finding and one that is *narrower* is not, but
both mean the committed template is not what is deployed, so both are reported. The
second is the normalisation, which has to reconcile ``${AWS::Partition}`` in the template
with ``aws`` in the account without ever making two different resources compare equal.
Every case below that begins ``test_the_normalisation`` exists to hold that second line.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.evidence import AWS_ACCOUNT_ID_PLACEHOLDER, scan_for_secrets
from edullm_platform.phase1_evidence import DeployedRoleEvidence
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    EVIDENCE_ONLY_ROLE_FIELDS,
    FOREIGN_ACCOUNT_PLACEHOLDER,
    DriftDirection,
    PolicyNotComparableError,
    RoleDriftReport,
    TemplateRole,
    compare_role_to_template,
    load_template_roles,
    normalize_policy_string,
    redact_account_in_arn,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARTITION = "aws"
REGION = "us-east-1"
OWN_ACCOUNT = "123456789012"
# Reversed rather than written out: the tracked-tree tripwire in tests/test_evidence.py
# allows only AWS's own documented example account ID as a literal, and it is right to.
OTHER_ACCOUNT = OWN_ACCOUNT[::-1]
PUBLISHER_ROLE = "sbsandbox-intern-edullm-ecr-publisher"
DEPLOYER_ROLE = "sbsandbox-intern-edullm-infra-deployer"
PUBLISHER_TEMPLATE = "infra/iam/ecr-publisher-role.yaml"
DEPLOYER_TEMPLATE = "infra/iam/infra-deployer-role.yaml"
BOUNDARY = "InternSandboxBoundary"
OLMO_CORE_REPOSITORY = "sbsandbox-intern-edullm-olmo-core"


def template_role(relative_path: str, role_name: str) -> TemplateRole:
    roles = load_template_roles(PROJECT_ROOT / relative_path)
    return next(role for role in roles if role.role_name == role_name)


@pytest.fixture(scope="module")
def publisher_template() -> TemplateRole:
    return template_role(PUBLISHER_TEMPLATE, PUBLISHER_ROLE)


@pytest.fixture(scope="module")
def deployer_template() -> TemplateRole:
    return template_role(DEPLOYER_TEMPLATE, DEPLOYER_ROLE)


def expand(value: object) -> Any:
    """Spell a template string the way the account spells it back, after redaction."""
    if isinstance(value, str):
        return (
            value.replace("${AWS::Partition}", PARTITION)
            .replace("${AWS::Region}", REGION)
            .replace("${AWS::AccountId}", AWS_ACCOUNT_ID_PLACEHOLDER)
        )
    if isinstance(value, dict):
        return {key: expand(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [expand(nested) for nested in value]
    return value


def deployed_exactly_as_declared(template: TemplateRole) -> DeployedRoleEvidence:
    """The evidence a capture would write if the account matched the template exactly."""
    payload = expand(template.model_dump(mode="json"))
    payload.update(
        {
            "source": "aws",
            "environment": "sandbox",
            "status": "ok",
            "observed_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        }
    )
    return DeployedRoleEvidence.model_validate(payload)


def altered(evidence: DeployedRoleEvidence, mutate: Any) -> DeployedRoleEvidence:
    payload = copy.deepcopy(evidence.model_dump(mode="json"))
    mutate(payload)
    return DeployedRoleEvidence.model_validate(payload)


def compare(
    evidence: DeployedRoleEvidence,
    template: TemplateRole,
) -> RoleDriftReport:
    return compare_role_to_template(
        evidence,
        template,
        template_path=PUBLISHER_TEMPLATE,
        partition=PARTITION,
        region=REGION,
    )


def repository_statement(payload: dict[str, Any]) -> dict[str, Any]:
    """The publisher's second inline statement: nine ECR actions on one repository."""
    return payload["inline_policies"][0]["statements"][1]


def directions(report: RoleDriftReport) -> list[DriftDirection]:
    return [finding.direction for finding in report.findings]


def details(report: RoleDriftReport) -> str:
    return " || ".join(f"{finding.element}: {finding.detail}" for finding in report.findings)


# --------------------------------------------------------------------------------------
# Projecting the committed templates
# --------------------------------------------------------------------------------------


def test_both_committed_role_templates_are_registered_and_project_cleanly() -> None:
    # The registry is what the capture tool and the drift report iterate over. A role
    # template that is committed but unregistered would never be compared to anything.
    assert dict(COMMITTED_ROLE_TEMPLATES) == {
        PUBLISHER_ROLE: PUBLISHER_TEMPLATE,
        DEPLOYER_ROLE: DEPLOYER_TEMPLATE,
    }
    for role_name, relative_path in COMMITTED_ROLE_TEMPLATES:
        assert template_role(relative_path, role_name).role_name == role_name


def test_the_publisher_projection_is_the_role_the_template_declares(
    publisher_template: TemplateRole,
) -> None:
    assert publisher_template.permissions_boundary_policy_name == BOUNDARY
    assert publisher_template.max_session_duration_seconds == 3600
    assert publisher_template.trust_policy_version == "2012-10-17"
    assert publisher_template.attached_managed_policies == ()

    assert len(publisher_template.trust_statements) == 1
    trust = publisher_template.trust_statements[0]
    assert trust.effect == "Allow"
    assert trust.action_match.element == "Action"
    assert trust.action_match.actions == ("sts:AssumeRoleWithWebIdentity",)
    assert trust.principal_match.element == "Principal"
    assert [principal.principal_type for principal in trust.principal_match.principals] == [
        "Federated"
    ]
    assert {condition.condition_key for condition in trust.conditions} == {
        "token.actions.githubusercontent.com:aud",
        "token.actions.githubusercontent.com:job_workflow_ref",
        "token.actions.githubusercontent.com:repository_owner_id",
        "token.actions.githubusercontent.com:repository_id",
        "token.actions.githubusercontent.com:sub",
    }

    assert len(publisher_template.inline_policies) == 1
    policy = publisher_template.inline_policies[0]
    assert policy.policy_name == "publish-olmo-core-images"
    assert policy.policy_version == "2012-10-17"
    assert len(policy.statements) == 2
    assert policy.statements[0].action_match.actions == ("ecr:GetAuthorizationToken",)
    assert len(policy.statements[1].action_match.actions) == 9


def test_the_deployer_projection_keeps_the_narrowed_wildcards_it_was_given(
    deployer_template: TemplateRole,
) -> None:
    # The stack and repository scopes wildcard only after the edullm segment, on purpose:
    # this is a shared account. A projection that dropped the suffix would compare equal
    # to a role scoped over every intern's stacks.
    resources = {
        resource
        for policy in deployer_template.inline_policies
        for statement in policy.statements
        for resource in statement.resource_match.resources
    }
    template_arn = "arn:${AWS::Partition}:%s:${AWS::Region}:${AWS::AccountId}:%s"
    assert resources == {
        template_arn % ("cloudformation", "stack/sbsandbox-intern-edullm-*/*"),
        template_arn % ("ecr", "repository/sbsandbox-intern-edullm-*"),
        "*",
    }


def test_a_template_projection_carries_exactly_what_the_evidence_can_be_compared_on() -> None:
    # The two records are the two halves of one comparison. A field added to the evidence
    # and not to the projection would be captured and never compared, which is the
    # failure mode this whole module exists to prevent.
    assert set(TemplateRole.model_fields) | EVIDENCE_ONLY_ROLE_FIELDS == set(
        DeployedRoleEvidence.model_fields
    )
    assert not set(TemplateRole.model_fields) & EVIDENCE_ONLY_ROLE_FIELDS


@pytest.mark.parametrize(
    ("properties", "reason"),
    [
        ({"RoleName": {"Ref": "SomeParameter"}}, "Ref"),
        ({"RoleName": "r", "AssumeRolePolicyDocument": {"Fn::If": ["c", {}, {}]}}, "Fn::If"),
        (
            {
                "RoleName": "r",
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": {"Fn::Join": [":", ["a", "b"]]}},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                },
            },
            "Fn::Join",
        ),
    ],
    ids=["ref role name", "conditional trust policy", "joined principal"],
)
def test_a_template_the_projection_cannot_read_faithfully_is_refused(
    properties: dict[str, Any],
    reason: str,
) -> None:
    # Guessing at what CloudFormation would resolve these to would produce a projection
    # that compares clean against a role it does not describe.
    from edullm_platform.role_drift import project_template_role

    with pytest.raises(PolicyNotComparableError):
        project_template_role(properties)
    assert reason  # named in the parametrisation so a failure says which form was read


# --------------------------------------------------------------------------------------
# The role the template describes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role_name", "relative_path"),
    [(PUBLISHER_ROLE, PUBLISHER_TEMPLATE), (DEPLOYER_ROLE, DEPLOYER_TEMPLATE)],
)
def test_a_role_deployed_exactly_as_declared_reports_no_drift(
    role_name: str,
    relative_path: str,
) -> None:
    template = template_role(relative_path, role_name)

    report = compare_role_to_template(
        deployed_exactly_as_declared(template),
        template,
        template_path=relative_path,
        partition=PARTITION,
        region=REGION,
    )

    assert report.findings == (), details(report)
    assert report.matches is True
    assert report.role_name == role_name
    assert scan_for_secrets(report.model_dump_json()) == report.model_dump_json()


def test_comparing_one_role_to_another_role_template_is_refused(
    publisher_template: TemplateRole,
    deployer_template: TemplateRole,
) -> None:
    # Every finding this produced would be an artefact of comparing two different roles.
    deployed = deployed_exactly_as_declared(deployer_template)

    with pytest.raises(ValueError, match="different role"):
        compare(deployed, publisher_template)


def test_reordering_statements_is_not_reported_because_iam_does_not_order_them(
    publisher_template: TemplateRole,
) -> None:
    # Policy evaluation is order-independent, so a reordered document grants exactly what
    # the template grants. Reporting it would be a finding a reader cannot act on.
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload["inline_policies"][0]["statements"].reverse(),
    )

    assert compare(deployed, publisher_template).findings == ()


def test_a_reorder_that_also_widens_a_statement_is_still_reported(
    publisher_template: TemplateRole,
) -> None:
    # The order exemption is not a hole: what it ignores is order and nothing else.
    def reorder_and_widen(payload: dict[str, Any]) -> None:
        statements = payload["inline_policies"][0]["statements"]
        statements.reverse()
        statements[0]["action_match"]["actions"].append("ecr:DeleteRepository")

    deployed = altered(deployed_exactly_as_declared(publisher_template), reorder_and_widen)

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert "ecr:DeleteRepository" in details(report)


# --------------------------------------------------------------------------------------
# Wider than the template
# --------------------------------------------------------------------------------------


def test_an_action_the_template_does_not_grant_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: repository_statement(payload)["action_match"]["actions"].append(
            "ecr:DeleteRepository"
        ),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert report.matches is False
    assert "ecr:DeleteRepository" in details(report)


def test_a_whole_extra_allow_statement_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    def add_statement(payload: dict[str, Any]) -> None:
        payload["inline_policies"][0]["statements"].append(
            {
                "sid": None,
                "effect": "Allow",
                "action_match": {"element": "Action", "actions": ["s3:GetObject"]},
                "resource_match": {"element": "Resource", "resources": ["*"]},
                "conditions": [],
            }
        )

    deployed = altered(deployed_exactly_as_declared(publisher_template), add_statement)

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert "s3:GetObject" in details(report)


def test_an_inline_policy_the_template_never_declared_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    def add_policy(payload: dict[str, Any]) -> None:
        payload["inline_policies"].append(
            {
                "policy_name": "console-convenience",
                "policy_version": "2012-10-17",
                "statements": [
                    {
                        "sid": None,
                        "effect": "Allow",
                        "action_match": {"element": "Action", "actions": ["iam:*"]},
                        "resource_match": {"element": "Resource", "resources": ["*"]},
                        "conditions": [],
                    }
                ],
            }
        )

    deployed = altered(deployed_exactly_as_declared(publisher_template), add_policy)

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert "console-convenience" in details(report)


def test_a_managed_policy_attached_in_the_console_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    # Neither template attaches one, and an attachment is the least effort way to widen a
    # role that exists. A record without the field would have been blind to it.
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload["attached_managed_policies"].append(
            {"policy_name": "AdministratorAccess", "scope": "aws"}
        ),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert "AdministratorAccess" in details(report)


def test_a_detached_permissions_boundary_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload.update({"permissions_boundary_policy_name": None}),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert BOUNDARY in details(report)


def test_a_longer_session_than_the_template_asks_for_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload.update({"max_session_duration_seconds": 43200}),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert "43200" in details(report) and "3600" in details(report)


def test_a_trust_condition_dropped_in_the_console_is_reported_as_wider(
    publisher_template: TemplateRole,
) -> None:
    # Dropping the sub condition lets any branch of any repository under the provider
    # assume the role. This is the single finding the whole comparison exists for.
    def drop_the_subject_condition(payload: dict[str, Any]) -> None:
        conditions = payload["trust_statements"][0]["conditions"]
        payload["trust_statements"][0]["conditions"] = [
            condition for condition in conditions if not condition["condition_key"].endswith(":sub")
        ]

    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        drop_the_subject_condition,
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.WIDER]
    assert "token.actions.githubusercontent.com:sub" in details(report)


def test_a_trust_statement_that_selects_by_exclusion_is_reported(
    publisher_template: TemplateRole,
) -> None:
    # NotPrincipal with Allow is what IAM Access Analyzer calls ALLOW_WITH_NOT_PRINCIPAL.
    # No template here uses it, so a deployed role that does is drift by construction.
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload["trust_statements"][0]["principal_match"].update(
            {"element": "NotPrincipal"}
        ),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.CHANGED]
    assert "NotPrincipal" in details(report)


# --------------------------------------------------------------------------------------
# Narrower than the template
# --------------------------------------------------------------------------------------


def test_an_action_the_template_grants_and_the_role_lacks_is_reported_as_narrower(
    publisher_template: TemplateRole,
) -> None:
    # Not a security problem, and still a finding: the committed template is not what is
    # deployed, and a reader who trusted it would expect a push that would fail.
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: repository_statement(payload)["action_match"]["actions"].remove(
            "ecr:PutImage"
        ),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.NARROWER]
    assert "ecr:PutImage" in details(report)


def test_an_extra_trust_condition_is_reported_as_narrower(
    publisher_template: TemplateRole,
) -> None:
    def add_a_condition(payload: dict[str, Any]) -> None:
        payload["trust_statements"][0]["conditions"].append(
            {
                "operator": "StringEquals",
                "condition_key": "token.actions.githubusercontent.com:environment",
                "values": ["production"],
            }
        )

    deployed = altered(deployed_exactly_as_declared(publisher_template), add_a_condition)

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.NARROWER]


def test_a_shorter_session_than_the_template_asks_for_is_reported_as_narrower(
    publisher_template: TemplateRole,
) -> None:
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload.update({"max_session_duration_seconds": 3600}),
    )
    widened_template = TemplateRole.model_validate(
        publisher_template.model_dump(mode="json") | {"max_session_duration_seconds": 7200}
    )

    report = compare_role_to_template(
        deployed,
        widened_template,
        template_path=PUBLISHER_TEMPLATE,
        partition=PARTITION,
        region=REGION,
    )

    assert directions(report) == [DriftDirection.NARROWER]


def test_an_inline_policy_the_template_declares_and_the_role_lacks_is_narrower(
    publisher_template: TemplateRole,
) -> None:
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload.update({"inline_policies": []}),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.NARROWER]
    assert "publish-olmo-core-images" in details(report)


def test_a_deny_statement_the_role_added_is_narrower_not_wider(
    publisher_template: TemplateRole,
) -> None:
    # Direction follows the effect rather than the count. An extra statement is a wider
    # role when it allows and a narrower one when it denies.
    def add_a_deny(payload: dict[str, Any]) -> None:
        payload["inline_policies"][0]["statements"].append(
            {
                "sid": None,
                "effect": "Deny",
                "action_match": {"element": "Action", "actions": ["ecr:PutImage"]},
                "resource_match": {"element": "Resource", "resources": ["*"]},
                "conditions": [],
            }
        )

    deployed = altered(deployed_exactly_as_declared(publisher_template), add_a_deny)

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.NARROWER]


# --------------------------------------------------------------------------------------
# Neither wider nor narrower
# --------------------------------------------------------------------------------------


def test_a_condition_value_edited_in_the_console_is_reported_as_changed(
    publisher_template: TemplateRole,
) -> None:
    def repoint_the_subject(payload: dict[str, Any]) -> None:
        for condition in payload["trust_statements"][0]["conditions"]:
            if condition["condition_key"].endswith(":sub"):
                condition["values"] = ["repo:edu-llm@306859726/OLMo-core@1306868157:ref:refs/*"]

    deployed = altered(deployed_exactly_as_declared(publisher_template), repoint_the_subject)

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.CHANGED]


def test_a_different_permissions_boundary_is_reported_as_changed(
    publisher_template: TemplateRole,
) -> None:
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: payload.update({"permissions_boundary_policy_name": "SomeOtherBoundary"}),
    )

    report = compare(deployed, publisher_template)

    assert directions(report) == [DriftDirection.CHANGED]
    assert "SomeOtherBoundary" in details(report)


def test_a_statement_that_both_gains_and_loses_actions_is_reported_in_both_directions(
    publisher_template: TemplateRole,
) -> None:
    def swap_an_action(payload: dict[str, Any]) -> None:
        actions = repository_statement(payload)["action_match"]["actions"]
        actions.remove("ecr:PutImage")
        actions.append("ecr:DeleteRepository")

    deployed = altered(deployed_exactly_as_declared(publisher_template), swap_an_action)

    report = compare(deployed, publisher_template)

    assert sorted(directions(report)) == sorted([DriftDirection.WIDER, DriftDirection.NARROWER])


# --------------------------------------------------------------------------------------
# The normalisation, and what it must never be able to hide
# --------------------------------------------------------------------------------------


def test_the_normalisation_folds_the_three_values_a_template_cannot_spell() -> None:
    template_spelling = (
        "arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}"
        f":repository/{OLMO_CORE_REPOSITORY}"
    )
    deployed_spelling = (
        f"arn:{PARTITION}:ecr:{REGION}:{AWS_ACCOUNT_ID_PLACEHOLDER}"
        f":repository/{OLMO_CORE_REPOSITORY}"
    )

    folded = normalize_policy_string(template_spelling, partition=PARTITION, region=REGION)

    assert folded == normalize_policy_string(deployed_spelling, partition=PARTITION, region=REGION)
    assert OLMO_CORE_REPOSITORY in folded


def ecr_arn(
    *,
    partition: str = PARTITION,
    region: str = REGION,
    account: str = AWS_ACCOUNT_ID_PLACEHOLDER,
    service: str = "ecr",
    repository: str = OLMO_CORE_REPOSITORY,
) -> str:
    return f"arn:{partition}:{service}:{region}:{account}:repository/{repository}"


@pytest.mark.parametrize(
    ("deployed_spelling", "hidden"),
    [
        (ecr_arn(region="eu-west-1"), "another region"),
        (ecr_arn(partition="aws-us-gov"), "another partition"),
        (ecr_arn(account=FOREIGN_ACCOUNT_PLACEHOLDER), "another account"),
        (ecr_arn(repository="*"), "every repository"),
        (ecr_arn(repository="sbsandbox-intern-someone-else"), "another repository"),
        (ecr_arn(service="s3"), "another service"),
        ("*", "everything"),
    ],
    ids=["region", "partition", "account", "wildcard", "repository", "service", "star"],
)
def test_the_normalisation_cannot_make_a_different_resource_compare_equal(
    deployed_spelling: str,
    hidden: str,
) -> None:
    # Folding happens only where a field holds exactly the partition, region or account
    # the comparison was told to expect, and never inside the resource. Everything else
    # survives normalisation and is therefore still visible to the comparison.
    template_spelling = (
        "arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}"
        f":repository/{OLMO_CORE_REPOSITORY}"
    )

    assert normalize_policy_string(
        deployed_spelling, partition=PARTITION, region=REGION
    ) != normalize_policy_string(template_spelling, partition=PARTITION, region=REGION), hidden


def test_the_normalisation_leaves_an_iam_arn_with_no_region_alone() -> None:
    # IAM is global, so the region field is empty on both sides and must stay empty
    # rather than becoming the placeholder a regional ARN gets.
    folded = normalize_policy_string(
        "arn:${AWS::Partition}:iam::${AWS::AccountId}:oidc-provider/token.actions.githubusercontent.com",
        partition=PARTITION,
        region=REGION,
    )

    assert "::" in folded
    assert folded.endswith(":oidc-provider/token.actions.githubusercontent.com")


def test_a_substitution_the_normalisation_does_not_understand_is_refused() -> None:
    # Folding an unknown pseudo-parameter would invent a value; leaving it would compare
    # a literal "${AWS::URLSuffix}" against whatever the account returned and call it
    # drift. Refusing says the template cannot be compared, which is the truth.
    with pytest.raises(PolicyNotComparableError):
        normalize_policy_string(
            "arn:${AWS::Partition}:s3:::${AWS::StackName}-artifacts/*",
            partition=PARTITION,
            region=REGION,
        )


def test_a_resource_drift_that_only_a_faithful_normalisation_can_see(
    publisher_template: TemplateRole,
) -> None:
    # The end-to-end version of the cases above: the deployed role points at every
    # repository in the account rather than the one the template names.
    deployed = altered(
        deployed_exactly_as_declared(publisher_template),
        lambda payload: repository_statement(payload)["resource_match"].update(
            {
                "resources": [
                    f"arn:{PARTITION}:ecr:{REGION}:{AWS_ACCOUNT_ID_PLACEHOLDER}:repository/*"
                ]
            }
        ),
    )

    report = compare(deployed, publisher_template)

    assert sorted(directions(report)) == sorted([DriftDirection.WIDER, DriftDirection.NARROWER])
    assert "repository/*" in details(report)


# --------------------------------------------------------------------------------------
# Masking the account before an ARN can be recorded
# --------------------------------------------------------------------------------------


def test_this_account_and_any_other_account_are_masked_differently() -> None:
    # A single placeholder for both would let a cross-account grant normalise away, and
    # a cross-account grant is precisely the kind of widening worth catching.
    own = f"arn:aws:ecr:{REGION}:{OWN_ACCOUNT}:repository/{OLMO_CORE_REPOSITORY}"
    other = f"arn:aws:ecr:{REGION}:{OTHER_ACCOUNT}:repository/{OLMO_CORE_REPOSITORY}"

    masked_own = redact_account_in_arn(own, own_account=OWN_ACCOUNT)
    masked_other = redact_account_in_arn(other, own_account=OWN_ACCOUNT)

    assert masked_own != masked_other
    assert AWS_ACCOUNT_ID_PLACEHOLDER in masked_own
    assert FOREIGN_ACCOUNT_PLACEHOLDER in masked_other
    assert scan_for_secrets(masked_own) == masked_own
    assert scan_for_secrets(masked_other) == masked_other


def test_an_aws_managed_policy_arn_keeps_the_word_aws_where_an_account_would_be() -> None:
    arn = "arn:aws:iam::aws:policy/AdministratorAccess"

    assert redact_account_in_arn(arn, own_account=OWN_ACCOUNT) == arn


def test_a_string_that_is_not_an_arn_is_still_stripped_of_account_ids() -> None:
    assert OWN_ACCOUNT not in redact_account_in_arn(
        f"the account {OWN_ACCOUNT} said no", own_account=OWN_ACCOUNT
    )
