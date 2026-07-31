"""The identity a dataset owner's own validator assumes instead of our shared workload role.

``sbsandbox-intern-edullm-batch-workload`` carries an inline policy, ``dataset-validator``,
attached outside CloudFormation, that grants write access to the sealed ``edullm-data``
bucket. That role is what every CPU container on every team runs as, so a job that only
prints a line currently inherits the ability to write a dataset. This module tests the
template that declares somewhere else for that policy to live -- ``dataset-validator-role``
-- without touching the shared role at all: nothing here detaches anything, and nothing here
is deployed.
"""

from __future__ import annotations

from pathlib import Path

from edullm_platform.admission_denials import LINEAGE_BUCKET
from edullm_platform.role_drift import (
    DATASET_VALIDATOR_ROLE_TEMPLATES,
    TemplateRole,
    load_template_roles,
)
from edullm_platform.team_isolation import WORKLOAD_ROLE_TEMPLATES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_ROLE_NAME = "sbsandbox-intern-edullm-dataset-validator"
VALIDATOR_TEMPLATE_PATH = PROJECT_ROOT / "infra" / "iam" / "dataset-validator-role.yaml"
DATASET_BUCKET_ARN = "arn:${AWS::Partition}:s3:::edullm-data"


def one_role_in(path: Path) -> TemplateRole:
    """The one role a template declares.

    Not the first of however many: a helper that quietly returned the first of two roles
    would be a trap for whoever later adds a second role to this file, since every caller
    below assumes the template describes exactly one identity.
    """
    roles = load_template_roles(path)
    assert len(roles) == 1, f"{path} declares {len(roles)} roles, expected exactly one"
    return roles[0]


def test_the_validator_role_is_declared_with_the_boundary_that_lets_it_be_created() -> None:
    """Mutation: omit PermissionsBoundary.

    InternSandboxBoundary denies iam:CreateRole unless iam:PermissionsBoundary equals the
    boundary's own ARN, so a template without it is a stack that fails at CreateRole with an
    access denial naming neither the boundary nor the condition. Every committed role
    template carries it; this asserts that this one does rather than trusting a copy.
    """
    role = one_role_in(VALIDATOR_TEMPLATE_PATH)

    assert role.permissions_boundary_policy_name == "InternSandboxBoundary"


def test_the_validator_role_may_write_the_dataset_bucket_and_no_bucket_of_ours() -> None:
    """Mutation: scope the grant to a prefix in our own account's buckets.

    The point of a separate identity is that the validator's reach and our workloads' reach
    stop being the same set. A validator that could also write our outputs would be the
    shared role again under a new name.
    """
    role = one_role_in(VALIDATOR_TEMPLATE_PATH)
    resources = {
        resource
        for policy in role.inline_policies
        for statement in policy.statements
        for resource in statement.resource_match.resources
    }

    assert resources, "the validator role grants nothing, so there is no reach to check"
    for resource in resources:
        assert resource == DATASET_BUCKET_ARN or resource.startswith(f"{DATASET_BUCKET_ARN}/"), (
            f"{resource} reaches somewhere other than {DATASET_BUCKET_ARN}, so the "
            "validator's grant is not limited to the dataset bucket"
        )


def test_the_validator_role_is_not_a_workload_role_and_its_name_is_why() -> None:
    """READS THE TRAP RATHER THAN DESCRIBING IT. Mutation: rename the role to end in
    -workload.

    tests/test_phase5_team_isolation.py collects every role declared under infra/iam/ whose
    name ends with -workload and requires the set to equal WORKLOAD_ROLE_TEMPLATES. A name
    ending that way conscripts this role into three checks about the teams/{team}/runs/
    prefix shape, which this role exists to reach outside of. So the suffix is a registry key
    and not a description, and this test is what says so to whoever next tidies a name.

    The constant alone is not enough: the three registry checks below key off
    VALIDATOR_ROLE_NAME, so renaming the template without updating the constant would leave
    them green while the trap fires. Binding the constant to what the template declares
    makes a rename-only mutation fail here first.
    """
    assert one_role_in(VALIDATOR_TEMPLATE_PATH).role_name == VALIDATOR_ROLE_NAME
    declared = {
        role.role_name
        for path in sorted((PROJECT_ROOT / "infra" / "iam").glob("*.yaml"))
        for role in load_template_roles(path)
        if role.role_name.endswith("-workload")
    }

    assert VALIDATOR_ROLE_NAME not in declared
    assert VALIDATOR_ROLE_NAME not in {name for name, _path in WORKLOAD_ROLE_TEMPLATES}
    assert VALIDATOR_ROLE_NAME in {name for name, _path in DATASET_VALIDATOR_ROLE_TEMPLATES}


def test_the_validator_role_is_registered_so_drift_on_it_is_visible() -> None:
    """Mutation: ship the template and register it nowhere.

    A role no registry names is a role the drift comparison never captures, and the failure
    is silence: a check over an empty set passes. This is the same defect
    test_the_registry_holds_every_role_a_container_actually_runs_as exists for, one registry
    over.
    """
    declared = {role.role_name for role in load_template_roles(VALIDATOR_TEMPLATE_PATH)}
    registered = {
        role.role_name
        for name, relative_path in DATASET_VALIDATOR_ROLE_TEMPLATES
        for role in load_template_roles(PROJECT_ROOT / relative_path)
        if role.role_name == name
    }

    assert declared == registered == {VALIDATOR_ROLE_NAME}


def test_the_validator_role_cannot_reach_the_store_that_records_what_we_did() -> None:
    """Mutation: grant it anything on the lineage bucket.

    The lineage store is write-once by bucket policy and only the admission state machine
    writes to it. A foreign validator holding any grant there would undo the property that
    store exists to have, and Object Lock refusing the delete is not a substitute for the
    grant never existing.
    """
    role = one_role_in(VALIDATOR_TEMPLATE_PATH)
    reached = [
        resource
        for policy in role.inline_policies
        for statement in policy.statements
        for resource in statement.resource_match.resources
        if LINEAGE_BUCKET in resource
    ]

    assert reached == []


def test_only_a_service_can_assume_the_validator_role_and_only_the_container_service() -> None:
    """THE AIRLOCK RESTS ON THIS AND NOT ON THE BUCKET POLICY. Mutation: add
    events.amazonaws.com so an EventBridge rule can target it directly.

    A rule that cannot invoke is an afternoon of routing it through an invocation role
    instead; a role a human or another service can assume is the exposure this whole task
    removes.

    Asserted as an equality over the full (type, identifier) principal set rather than a
    membership test, because every mutation this guards against *adds* a principal rather
    than replacing the one that is there: a second service principal alongside
    ecs-tasks.amazonaws.com, a principal of any other type such as an AWS ARN, or the
    statement's element becoming NotPrincipal (which can admit anonymous callers and would
    show up here as a second element in the set below).
    """
    role = one_role_in(VALIDATOR_TEMPLATE_PATH)
    elements = {statement.principal_match.element for statement in role.trust_statements}
    principals = {
        (principal.principal_type, principal.identifier)
        for statement in role.trust_statements
        for principal in statement.principal_match.principals
    }

    assert elements == {"Principal"}, (
        f"the trust policy selects principals by {elements} instead of only Principal"
    )
    assert principals == {("Service", "ecs-tasks.amazonaws.com")}
