"""The seven properties system-overview.md draws on edullm-researcher, as policy statements.

WHAT THIS MODULE IS NOT. It reads a committed CloudFormation template, which is a claim about
what the account will be asked for rather than a description of what it holds. Every statement
below would stay green against a role that was never deployed, or one widened in the console
afterwards. tests/test_researcher_deployed_role.py is the half that closes that distance, and
neither module replaces the other -- this one catches a template that is wrong, that one
catches an account that is.

The specification is docs-frank/reference/system-overview.md, "How money gets spent, and what
stops a mistake", whose diagram names the seven properties, and
docs-frank/reference/aws-spend-controls.md, "The permission policy", which is the document the
statements were simulated against.
"""

from __future__ import annotations

import json
from pathlib import Path

from edullm_platform.cli.lane import SCRATCH_BUCKET
from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.researcher_lane import (
    GOVERNANCE_TAG_KEYS,
    RESEARCHER_ROLE_NAME,
    instance_types_the_catalog_prices,
)
from tests.infrastructure_support import INFRA_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = INFRA_ROOT / "iam" / "researcher-role.yaml"
POLICY_NAME = "lane"

#: The working tier's bucket, read from the one module that declares it rather than written out
#: again. It was a literal here while the exploration route was unmerged and nothing in the tree
#: named the bucket; that route landed in #258, so a second literal would now be a second answer
#: to what the bucket is called. test_every_place_that_names_the_working_tier_agrees is what holds
#: the remaining four places together.
WORKING_BUCKET = SCRATCH_BUCKET


def role() -> dict[str, object]:
    resources = load_template(TEMPLATE_PATH)["Resources"]
    return next(
        value["Properties"]
        for value in resources.values()
        if isinstance(value, dict) and value.get("Type") == "AWS::IAM::Role"
    )


def statements() -> list[dict[str, object]]:
    policies = role()["Policies"]
    assert isinstance(policies, list)
    inline = next(one for one in policies if one["PolicyName"] == POLICY_NAME)
    document = inline["PolicyDocument"]
    assert isinstance(document, dict)
    found = document["Statement"]
    assert isinstance(found, list)
    return found


def statement(sid: str) -> dict[str, object]:
    return next(one for one in statements() if one.get("Sid") == sid)


def as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    assert isinstance(value, list)
    return [str(one) for one in value]


def test_the_role_carries_the_boundary_and_names_itself() -> None:
    """Mutation: drop PermissionsBoundary. Mutation: rename the role.

    iam:CreateRole is denied outright unless the request carries this exact boundary, so a
    template that omits it does not create a weaker role -- it fails. The role name is what a
    person types into an assume-role call and what the janitor's stop grant is reasoned about
    against, so a rename is not cosmetic.
    """
    properties = role()

    assert properties["RoleName"] == RESEARCHER_ROLE_NAME
    boundary = properties["PermissionsBoundary"]
    assert isinstance(boundary, dict)
    assert boundary["Fn::Sub"].endswith(":policy/InternSandboxBoundary")


def trust_statement(action: str) -> dict[str, object]:
    trust = role()["AssumeRolePolicyDocument"]
    assert isinstance(trust, dict)
    found = [one for one in trust["Statement"] if as_list(one["Action"]) == [action]]
    assert len(found) == 1, f"{action} is granted by {len(found)} statements rather than one"
    return found[0]


def test_the_trust_policy_demands_a_project_a_lifetime_and_a_source_identity() -> None:
    """Mutation: drop sts:SetSourceIdentity from the action list.

    AWS requires SetSourceIdentity in both the caller's policy and the target role's trust
    policy, so dropping it here fails the AssumeRole call rather than merely losing the
    attribution -- and the failure names neither. Mutation: drop one of the three StringLike
    "?*" presence tests, which is what admits an untagged or unattributed session.
    """
    entry = trust_statement("sts:AssumeRole")
    condition = entry["Condition"]

    assert condition["StringLike"] == {
        "aws:RequestTag/project": "?*",
        "aws:RequestTag/lifetime": "?*",
        "sts:SourceIdentity": "?*",
    }
    assert condition["ArnLike"]["aws:PrincipalArn"]["Fn::Sub"].endswith(":role/Intern-*")
    assert condition["ForAllValues:StringEquals"]["aws:TagKeys"] == ["project", "lifetime"]


def test_each_action_is_tested_only_on_the_keys_it_is_given() -> None:
    """**THE SHAPE THAT MADE THE ROLE UNUSABLE FOR THE FIRST FIVE HOURS OF ITS LIFE.**
    Mutation: collapse the three statements back into one carrying all four conditions.
    Mutation: add sts:SourceIdentity to the TagSession statement, or aws:RequestTag to the
    SetSourceIdentity one.

    Either mutation restores a role nobody can assume. STS authorises the three actions of an
    AssumeRole request separately and hands each one only its own condition keys, so a
    presence test on a key the action is not given is a test on an absent key: StringLike
    fails, the statement does not match, and the whole call is refused with an AccessDenied
    naming an action rather than a condition. It was measured on a throwaway role in this
    account on 2026-08-06, both ways round, and the yaml carries the measurement.

    trust_statement asserting exactly one statement per action is half the test, because a
    collapsed policy is a statement granting three actions and finds none of them.
    """
    tagging = trust_statement("sts:TagSession")["Condition"]
    naming = trust_statement("sts:SetSourceIdentity")["Condition"]

    assert "sts:SourceIdentity" not in tagging["StringLike"]
    assert set(tagging["StringLike"]) == {"aws:RequestTag/project", "aws:RequestTag/lifetime"}
    assert set(naming["StringLike"]) == {"sts:SourceIdentity"}
    assert "ForAllValues:StringEquals" not in naming


def test_the_instance_allow_list_is_exactly_what_the_catalog_prices() -> None:
    """THE PROPERTY MOST LIKELY TO BE EDITED BY HAND.
    Mutation: add one instance type to the template. Mutation: promote a compute profile and
    leave this list alone.

    Compared in both directions against a set built from config/workload-catalog.yaml, so a
    type in the template that the catalog does not price fails as loudly as one the catalog
    prices and the template omits. aws-spend-controls.md measured both directions of getting
    this wrong on forty-one types.
    """
    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    condition = statement("DenyInstanceTypesOutsideTheCatalog")["Condition"]
    declared = tuple(condition["StringNotLike"]["ec2:InstanceType"])

    assert declared == instance_types_the_catalog_prices(catalog)


def test_the_instance_deny_is_scoped_to_the_two_taggable_arns() -> None:
    """Mutation: widen Resource to "*".

    RunInstances authorizes against the image, the subnet, the security group, the key pair
    and the network interface in the same call, and a deny on any one of them fails the whole
    call. The first four can never carry a request tag, so a condition at Resource "*" denies
    every launch unconditionally -- which reads as a broken role rather than as a scope.
    aws-spend-controls.md, "Two mechanical constraints", is where that was measured.
    """
    instance_only = statement("DenyInstanceTypesOutsideTheCatalog")["Resource"]
    tagged = statement("RequireProjectTagMatchingTheSessionTag")["Resource"]

    assert as_list(instance_only) == ["arn:aws:ec2:*:*:instance/*"]
    assert as_list(tagged) == ["arn:aws:ec2:*:*:instance/*", "arn:aws:ec2:*:*:volume/*"]


def test_a_launch_must_tag_the_project_it_named_when_it_entered_the_lane() -> None:
    """Mutation: compare aws:RequestTag/Project against a literal.

    The condition's value is the session tag, so the tag on the machine is the project the
    person declared rather than a string they typed twice. A literal would let a launch claim
    any project and still pass.
    """
    condition = statement("RequireProjectTagMatchingTheSessionTag")["Condition"]

    assert condition["StringNotEquals"] == {"aws:RequestTag/Project": "${aws:PrincipalTag/project}"}


def test_a_launch_with_no_expiry_tag_is_refused() -> None:
    """Mutation: drop the Null condition, or invert it.

    This is the statement the janitor rests on entirely. Without it a machine can be launched
    through the lane carrying no ExpiresAt at all, and the janitor's filter -- which is what
    keeps it from stopping another project's machine -- would skip it for ever.
    """
    condition = statement("RequireExpiresAtTagOnInstances")["Condition"]

    assert condition["Null"] == {"aws:RequestTag/ExpiresAt": "true"}


def test_a_governance_tag_cannot_be_removed_after_the_launch_that_set_it() -> None:
    """Mutation: drop the StringNotEquals on ec2:CreateAction.

    Without it the deny covers the tags the launch itself sets, so every compliant launch is
    refused -- which is the failure that looks like the allow-list being wrong. Mutation: drop
    a key from the list, which leaves a machine whose expiry can be deleted by the person the
    expiry is about.
    """
    condition = statement("DenyStrippingGovernanceTagsAfterLaunch")["Condition"]
    keys = tuple(condition["ForAnyValue:StringEquals"]["aws:TagKeys"])

    assert keys == GOVERNANCE_TAG_KEYS
    assert condition["StringNotEquals"] == {"ec2:CreateAction": "RunInstances"}
    assert set(as_list(statement("DenyStrippingGovernanceTagsAfterLaunch")["Action"])) == {
        "ec2:CreateTags",
        "ec2:DeleteTags",
    }


def test_the_commitment_purchases_the_boundary_lost_are_denied_here() -> None:
    """Mutation: drop ec2:PurchaseCapacityBlock.

    Named literally rather than derived, because these three are the ones with a price
    attached and no legitimate use here: aws-spend-controls.md, "The two regressions", records
    that InternSandboxBoundary v5 removed PurchaseCapacityBlock from its own deny and that a
    $3,567.25 charge followed a day and a half later. A derived list would let this test agree
    with whatever the template happened to say.
    """
    denied = set(as_list(statement("DenyCostCommitmentPurchases")["Action"]))

    assert {
        "ec2:PurchaseCapacityBlock",
        "ec2:PurchaseCapacityBlockExtension",
        "sagemaker:CreateTrainingPlan",
        "savingsplans:CreateSavingsPlan",
    } <= denied


def test_neither_sealed_bucket_can_be_deleted_from_or_reconfigured() -> None:
    """Mutation: drop s3:PutBucketPolicy from the action list.

    decisions.md, under "The airlock is a latch", records that the delete protection on
    edullm-data lives in a bucket policy any of forty-four administrators can replace, and
    that this role's own deny is the only place the configuration actions are refused at all
    for whoever assumes it. Dropping PutBucketPolicy here would leave the lane able to remove
    the latch it is standing behind.
    """
    entry = statement("DenyDeletesOnSealedBuckets")
    actions = set(as_list(entry["Action"]))
    resources = set(as_list(entry["Resource"]))

    assert {"s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutBucketPolicy"} <= actions
    assert resources == {
        "arn:aws:s3:::edullm-data",
        "arn:aws:s3:::edullm-data/*",
        "arn:aws:s3:::edullm-landing",
        "arn:aws:s3:::edullm-landing/*",
    }


def test_the_lane_cannot_mint_a_principal_that_escapes_it() -> None:
    """Mutation: drop iam:PutRolePolicy.

    Beyond the overview's seven and named in aws-spend-controls.md's policy. Every deny above
    is on this role; a session that can create a role and attach a policy to it can step
    outside all of them in two calls, which makes the other statements decoration.
    """
    denied = set(as_list(statement("DenyEscapingTheLaneByMintingNewPrincipals")["Action"]))

    assert {"iam:CreateRole", "iam:PutRolePolicy", "iam:CreateAccessKey"} <= denied


def test_the_indirect_launch_paths_that_skip_the_instance_conditions_are_denied() -> None:
    """Mutation: drop ec2:CreateFleet.

    Beyond the overview's seven and named in aws-spend-controls.md's policy. CreateFleet and
    the spot-fleet calls launch instances without RunInstances being authorized, so every
    condition above is simply not evaluated. Leaving them permitted makes the allow-list
    optional.
    """
    denied = set(as_list(statement("DenyIndirectLaunchPathsThatBypassInstanceTypeChecks")["Action"]))

    assert {
        "ec2:CreateFleet",
        "ec2:RequestSpotFleet",
        "autoscaling:CreateAutoScalingGroup",
    } <= denied


def test_the_seventh_property_names_the_working_tier_and_fences_it_by_source_identity() -> None:
    """THE TRIPWIRE FOR THE ONE PROPERTY ANOTHER PLAN READS BEFORE IT STARTS.
    Mutation: drop the statement. Mutation: except edullm-scratch/* rather than one person's
    prefix. Mutation: replace ${aws:SourceIdentity} with a literal name.

    The exploration route builds the working tier on the interface contract that this role
    already carries the write into it, and this test is what holds that contract from the other
    side. So it asserts that the bucket is named in a statement rather than in a comment, and
    that the exception is one person's segment rather than the whole tier.

    The second mutation is the one to think about. Excepting edullm-scratch/* passes any check that
    only looks for the bucket name and it fences nobody, so every researcher could overwrite
    every other researcher's working files, which is the one thing the layout in
    system-overview.md's "Where data lives" exists to prevent. The third pins the fence to one
    name, which lets that person write anywhere in the tier and everybody else nowhere, and a
    role deployed that way reports no error at all.

    A FOURTH MUTATION IS WHY THE WORKING-TIER EXCEPTIONS ARE COMPARED AS A SET AND NOT SEARCHED
    FOR. Adding arn:aws:s3:::edullm-scratch/* beside the fenced prefix rather than in place of it
    survives every membership assertion, because the narrow entry is still there to find. It is
    also the end of the fence: NotResource excuses a request that matches any one entry, so the
    wide one alone permits the whole tier and the narrow one becomes decoration. That is the
    shape this file's own header warns about, and it is only caught by comparing in both
    directions. The rest of the list is deliberately not frozen -- the template says a bucket
    added later and not listed is a write that fails, so the list is meant to grow -- and it is
    the working tier alone that may hold exactly one entry.

    A FIFTH MUTATION ARRIVED WITH THE LAYOUT, AND IT IS WHY THE SEPARATOR IS PART OF THE
    COMPARISON. The tier was <team>/<person>/ until 2026-08-05 and this entry read
    edullm-scratch/*/${aws:SourceIdentity}/*, so a segment has just been removed and the two
    spellings of that edit fail in opposite directions. Leaving the stale */ in place denies
    every write the lane makes, because the excepted path then wants three segments where a key
    has two, and the denial names no bucket and no key. Dropping the trailing separator instead,
    to edullm-scratch/${aws:SourceIdentity}*, reads as the same fence and is not one: it excuses
    every prefix the identity is a leading substring of, so "amy" is handed "amy.lin" and a
    person who never typed a wrong path loses files to somebody whose name merely starts the
    same way. Neither is a membership failure and both are set-equality failures, which is the
    argument for comparing the exact string rather than looking for the identity variable in it.
    """
    entry = statement("DenyWorkingTierWritesOutsideYourOwnPrefix")
    excepted = as_list(entry["NotResource"])
    into_the_tier = {one for one in excepted if WORKING_BUCKET in one}
    naming_the_tier = [one.get("Sid") for one in statements() if WORKING_BUCKET in json.dumps(one)]

    assert entry["Effect"] == "Deny"
    assert "Resource" not in entry
    assert "s3:PutObject" in set(as_list(entry["Action"]))
    assert into_the_tier == {f"arn:aws:s3:::{WORKING_BUCKET}/${{aws:SourceIdentity}}/*"}, (
        "The working tier is excepted by something other than one person's own prefix, or by "
        "that prefix and something wider beside it. A second exception naming the tier does "
        "not narrow the first one, it replaces it: any request matching either entry escapes "
        "the deny, so the widest entry present is the whole fence. The layout is "
        "<person>/<project>/ and the excepted path carries exactly one segment above the "
        "objects, so a leftover team wildcard denies every write and a missing trailing "
        "separator hands one person every prefix their name begins."
    )
    assert naming_the_tier == ["DenyWorkingTierWritesOutsideYourOwnPrefix"], (
        "The working tier is named in no statement, or in more than one. Either the seventh "
        "property has been dropped and the exploration route's precondition is now false, or "
        "a second statement reasons about the same bucket and nobody has said how they compose."
    )
