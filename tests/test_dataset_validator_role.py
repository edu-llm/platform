"""The identity a dataset owner's own validator assumes instead of our shared workload role.

``sbsandbox-intern-edullm-batch-workload`` used to carry an inline policy,
``dataset-validator``, attached outside CloudFormation, that granted write access to the
sealed ``edullm-data`` bucket. That role is what every CPU container on every team runs as,
so a job submitted to print a line inherited the ability to write a dataset. This module
tests the template that gave that policy somewhere else to live.

**The cutover happened on 2026-08-01 and this module changed shape with it.** Until then
the anchor to reality here was a comparison against the committed capture of the *shared*
role -- the only way to check the template against the account while the policy still lived
somewhere else. The role is deployed now, so the comparison is against a capture of *this*
role instead, which is a stronger claim: it says the thing this repository declares is the
thing the account holds, rather than that it faithfully copied a policy off a third party.

**That policy reaches two buckets and not one**, which is worth knowing before reading the
tests below. ``edullm-landing`` is where a candidate dataset and its manifests arrive and
where a refusal is written back; ``edullm-data`` is where an accepted one is promoted to.
Both are the dataset owner's and neither is ours, so a check that the validator stays out of
our storage is a different check from one that it reaches only one bucket.
"""

from __future__ import annotations

import json
from pathlib import Path

from edullm_platform.admission_denials import LINEAGE_BUCKET
from edullm_platform.contracts.execution import SANDBOX_RESOURCE_PREFIX
from edullm_platform.phase1_capture import CaptureVerdict, read_committed_role_captures
from edullm_platform.role_drift import (
    DATASET_VALIDATOR_CAPTURE_DIR,
    DATASET_VALIDATOR_ROLE_TEMPLATES,
    TemplateRole,
    load_template_roles,
)
from edullm_platform.team_isolation import WORKLOAD_ROLE_TEMPLATES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_ROLE_NAME = "sbsandbox-intern-edullm-dataset-validator"
VALIDATOR_TEMPLATE_PATH = PROJECT_ROOT / "infra" / "iam" / "dataset-validator-role.yaml"
#: Both buckets spelled as literals, which is the right call here for two reasons.
#:
#: The thing under test is a YAML template, and a template imports nothing. A constant shared
#: between the template's author and the template's test would only be shared by convention,
#: so deriving the expectation from it buys less than it looks like it does -- and if the
#: constant were ever wrong, a test anchored on it would agree with the mistake.
#:
#: The second reason is mechanical and worth writing down, because the obvious tidy-up is to
#: hoist these into edullm_platform.contracts and that has a cost nobody would expect. Every
#: module the admission handler imports is packaged into the validator's Lambda zip, whose
#: sha256 is recorded in infra/admission-validator-release.yaml and checked against a rebuild
#: of this tree. contracts/dataset.py is in that graph. Adding one constant to it moves the
#: digest and turns an IAM test into an AWS release -- measured, not guessed: this tree builds
#: 575131c1... today, matching the record, and 3de29045... with a single constant added there.
DATASET_BUCKET_ARN = "arn:${AWS::Partition}:s3:::edullm-data"
LANDING_BUCKET_ARN = "arn:${AWS::Partition}:s3:::edullm-landing"

#: The dataset owner's two buckets, as a template spells them. Named as a pair because the
#: reach test below is about which buckets are reachable at all, and both halves of an
#: airlock are the same answer to that question.
AIRLOCK_BUCKET_ARNS = (DATASET_BUCKET_ARN, LANDING_BUCKET_ARN)

#: Every action the validator holds. Written out rather than derived from the capture the
#: parity test reads, because a set derived from the thing it is compared against asserts
#: nothing; the two are checked against each other by that test rather than by construction.
EXPECTED_ACTIONS = frozenset(
    {
        "s3:AbortMultipartUpload",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:GetObjectAttributes",
        "s3:ListBucket",
        "s3:PutObject",
        "s3:PutObjectTagging",
    }
)

#: The committed capture of this role as the account actually holds it, taken after the
#: 2026-08-01 cutover deployed it. The copy a later reader can check without an AWS session.
#:
#: Taken with ``tools/capture_phase3_evidence.py --target dataset-validator``, which walks
#: ``DATASET_VALIDATOR_ROLE_TEMPLATES`` rather than Phase 3's four roles -- one registry per
#: unit of work, so this role drifting cannot fail a capture of theirs and vice versa.
DEPLOYED_ROLE_CAPTURE = (
    PROJECT_ROOT
    / DATASET_VALIDATOR_CAPTURE_DIR
    / "roles"
    / f"{VALIDATOR_ROLE_NAME}.sanitized.json"
)

#: One statement, as (Sid, actions, resources): the whole of what a grant says, once the two
#: sides of the comparison are spelled the same way. Used by the reach and action tests
#: below, which read the template alone.
Grant = tuple[str | None, tuple[str, ...], tuple[str, ...]]


def one_role_in(path: Path) -> TemplateRole:
    """The one role a template declares.

    Not the first of however many: a helper that quietly returned the first of two roles
    would be a trap for whoever later adds a second role to this file, since every caller
    below assumes the template describes exactly one identity.
    """
    roles = load_template_roles(path)
    assert len(roles) == 1, f"{path} declares {len(roles)} roles, expected exactly one"
    return roles[0]


def granted_actions(role: TemplateRole) -> set[str]:
    """Every action the role's inline policies allow, refusing the negated spelling.

    ``NotAction`` with ``Allow`` permits everything that is *not* listed, so a reader that
    collected its list would report the narrowest-looking actions in the file on the widest
    possible grant. The same helper for the same reason as the one over the image resolver
    in tests/test_phase5_infrastructure.py, rather than a second shape for a trap this
    repository has already met once.

    A ``Deny`` is refused rather than skipped. What bounds this role is the dataset bucket's
    own policy, which nothing in this repository writes, so a Deny appearing in the identity
    policy would be a second mechanism arriving unannounced -- and its actions would be
    counted as granted by the loop below.
    """
    granted: set[str] = set()
    for policy in role.inline_policies:
        for statement in policy.statements:
            assert statement.effect == "Allow", (
                f"{role.role_name} carries a {statement.effect} statement; every statement "
                "here is a grant and this one's actions would be read as granted"
            )
            assert statement.action_match.element == "Action", (
                f"{role.role_name} selects actions by {statement.action_match.element}, "
                "which with Allow grants everything it does not list"
            )
            granted.update(statement.action_match.actions)
    return granted


def as_the_account_spells_it(resource: str) -> str:
    """A template's ARN in the form a capture of the deployed policy holds it.

    ``load_template_roles`` keeps a template string exactly as the YAML spells it, so the
    template says ``${AWS::Partition}`` where IAM says ``aws``. Substituted here rather than
    written into the template, because ``Fn::Sub`` over the partition is what every ARN in
    infra/iam/ uses and a comparison is not a reason to make this template the exception.
    """
    return resource.replace("${AWS::Partition}", "aws")


def grants_of(role: TemplateRole) -> set[Grant]:
    """Every statement the role's inline policies carry, in the shape a capture holds."""
    return {
        (
            statement.sid,
            tuple(statement.action_match.actions),
            tuple(map(as_the_account_spells_it, statement.resource_match.resources)),
        )
        for policy in role.inline_policies
        for statement in policy.statements
    }


def grants_on_the_deployed_role() -> set[Grant]:
    """The same shape, read from the committed capture of this role as deployed.

    Read as JSON rather than through ``DeployedRoleEvidence`` deliberately: this asks what
    the account held on the day it was captured, and a freshness window that refused to load
    an old capture would turn a question about a policy into a question about the clock.

    Every inline policy is folded together rather than one being named. The role carries
    exactly one today, and naming it would make this helper agree with a rename; a second
    policy appearing is a widening the caller should see, not a key lookup that misses it.
    """
    captured = json.loads(DEPLOYED_ROLE_CAPTURE.read_text(encoding="utf-8"))
    policies = captured["inline_policies"]

    assert policies, (
        f"{DEPLOYED_ROLE_CAPTURE.name} records a role with no inline policy at all, so the "
        "comparison below would hold vacuously. Recapture with "
        "tools/capture_phase3_evidence.py --target dataset-validator"
    )
    return {
        (
            statement["sid"],
            tuple(statement["action_match"]["actions"]),
            tuple(statement["resource_match"]["resources"]),
        )
        for policy in policies
        for statement in policy["statements"]
    }


def test_the_account_holds_the_role_this_template_declares_and_nothing_wider() -> None:
    """Mutation: widen the deployed role in the console. Mutation: delete the capture.

    The whole-record comparison, which is what the equivalent Phase 3 check does and what
    this module lacked while the role was undeployed. It covers what the grants comparison
    below cannot see: the permissions boundary, the trust policy, any attached managed
    policy, and the session duration. A role that kept its seven S3 actions but grew an
    ``AWS`` principal in its trust policy would pass a grants check and fail here.

    Reported in both directions by the reader, which is why the capture lives in a
    directory of its own: a capture present for a role the registry does not declare is a
    finding too, and a directory shared with another registry would make one registry's
    filing look like the other's drift.
    """
    captures = read_committed_role_captures(
        PROJECT_ROOT,
        capture_dir=PROJECT_ROOT / DATASET_VALIDATOR_CAPTURE_DIR / "roles",
        role_templates=DATASET_VALIDATOR_ROLE_TEMPLATES,
    )

    assert len(captures) == len(DATASET_VALIDATOR_ROLE_TEMPLATES)
    for capture in captures:
        assert capture.verdict is CaptureVerdict.OK, (capture.role_name, capture.detail)
        assert capture.report is not None
        assert capture.report.matches


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
    """Mutation: scope the grant to a prefix in our own account's buckets. Mutation: spell
    a statement's resources as NotResource.

    The point of a separate identity is that the validator's reach and our workloads' reach
    stop being the same set. A validator that could also write our outputs would be the
    shared role again under a new name.

    Two buckets rather than one, and that is the airlock rather than a widening: the
    candidate is read out of ``edullm-landing`` and a refusal is written back to it, and an
    accepted dataset is promoted into ``edullm-data``. Both are the dataset owner's, so what
    this asserts is that no third bucket is named.

    The prefix check is implied by the pair check above it today and is written out anyway,
    because it is the mutation this test exists for. If the airlock ever grows a bucket, the
    pair is the line somebody edits, and the prefix check is what stops that edit from
    admitting one of ours.

    ``NotResource`` with ``Allow`` reaches every resource *except* the ones listed, so a
    statement spelled that way against the dataset bucket would grant its actions on every
    bucket in the account while reading, to anything that collected resources, as the
    narrowest grant in the file. Asserted for the same reason the trust test below asserts
    ``Principal`` over ``NotPrincipal``: this module already knew the trap and guarded one
    of the two places it lives.
    """
    role = one_role_in(VALIDATOR_TEMPLATE_PATH)
    statements = [statement for policy in role.inline_policies for statement in policy.statements]

    assert statements, "the validator role grants nothing, so there is no reach to check"
    for statement in statements:
        assert statement.resource_match.element == "Resource", (
            f"{statement.sid} selects resources by {statement.resource_match.element}, "
            "which with Allow reaches everything it does not list"
        )
        for resource in statement.resource_match.resources:
            assert any(
                resource == bucket or resource.startswith(f"{bucket}/")
                for bucket in AIRLOCK_BUCKET_ARNS
            ), (
                f"{resource} names a bucket outside {AIRLOCK_BUCKET_ARNS}, so the "
                "validator's reach is not the dataset owner's own storage"
            )
            assert SANDBOX_RESOURCE_PREFIX not in resource, (
                f"{resource} names a bucket of ours, which is the shared role's reach "
                "arriving under a new name"
            )


def test_the_validator_role_grants_the_actions_the_airlock_needs_and_no_delete() -> None:
    """Mutation: widen the actions to ``s3:*``. Mutation: add the ``s3:DeleteObject`` that
    somebody tidying would reach for.

    Until this test nothing asserted which actions this role holds. The reach test above
    reads resources, so ``s3:*`` on the dataset bucket passed every check in this module,
    and so did a delete the template argues against at length. An exact set closes both at
    once: a wildcard is not in it, and neither is an action nobody named.

    The delete is asserted separately as well, and the redundancy is the point. It is the
    one absence the template makes an argument for, and a failure naming it sends the next
    reader to that argument rather than to a set difference.

    The seven are the seven the out-of-band inline policy holds, read from the account on
    2026-07-31. That claim is checked by the parity test below rather than restated here.
    """
    granted = granted_actions(one_role_in(VALIDATOR_TEMPLATE_PATH))

    assert "s3:DeleteObject" not in granted, (
        "the template's DELIBERATELY ABSENT block argues that a role which has to be "
        "stopped by a bucket policy should not have been granted the action"
    )
    assert granted == EXPECTED_ACTIONS


def test_the_deployed_validator_role_is_the_one_this_template_declares() -> None:
    """Mutation: widen the deployed role in the console and leave the template alone.
    Mutation: drop or narrow a statement in the template after the role is deployed.

    This is the check whose absence let this template ship without the landing bucket in it
    at all. Every other test in this module reads the template against a claim written
    beside it, so a template and its tests can agree with each other and disagree with the
    account; this one reads the account, in the shape the committed capture holds it.

    It replaces a comparison against the *shared* role's capture, which was the only anchor
    available while this role did not exist. That one asked "was the policy copied across
    faithfully"; this one asks "is the account what we say it is", which is the question
    that keeps mattering after the copying is done. The predecessor was written to retire
    exactly here, and did.

    Parity with the policy it replaced is still the intent rather than a coincidence, and
    is now a historical claim rather than a live comparison: the cutover was a change of
    *identity*, not of *reach*, because narrowing on the same day would have left any
    failure ambiguous between the new role and the smaller grant. The seven actions in
    ``EXPECTED_ACTIONS`` are that policy's seven. Narrowing them is a later change with its
    own reason, and it is now available -- the identity is known to work, which was the
    condition.
    """
    assert grants_of(one_role_in(VALIDATOR_TEMPLATE_PATH)) == grants_on_the_deployed_role()


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
