"""What a lane machine may do, which is two things.

WHAT THIS MODULE IS NOT. It reads a committed template. Every assertion here would stay green
against a role that was never deployed or one widened in the console afterwards. The capture
committed by Task 8 Step 8 and read by tests/test_phase2_deployed_roles.py's mechanism is the half
that closes that distance.

THE THING TO REMEMBER ABOUT THIS ROLE. It is worn by a machine somebody is experimenting on. It is
the one principal here whose behaviour nobody reviews, because the whole point of the lane is that
nobody does. So it holds exactly what a session and a file sync need.
"""

from __future__ import annotations

from typing import Any

from edullm_platform.cli.lane import LANE_INSTANCE_PROFILE, SCRATCH_BUCKET
from tests.infrastructure_support import INFRA_ROOT, load_template

TEMPLATE_PATH = INFRA_ROOT / "iam" / "lane-instance-role.yaml"


def resources() -> dict[str, dict[str, Any]]:
    return load_template(TEMPLATE_PATH)["Resources"]


def role() -> dict[str, Any]:
    return next(
        value["Properties"]
        for value in resources().values()
        if value.get("Type") == "AWS::IAM::Role"
    )


def statements() -> list[dict[str, Any]]:
    policies = role()["Policies"]
    assert isinstance(policies, list)
    document = policies[0]["PolicyDocument"]
    assert isinstance(document, dict)
    found = document["Statement"]
    assert isinstance(found, list)
    return found


def as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    assert isinstance(value, list)
    return [str(one) for one in value]


def test_the_role_carries_the_boundary_and_is_trusted_only_by_ec2() -> None:
    """Mutation: drop the boundary. Mutation: widen the trust to sts:AssumeRole from anywhere.

    iam:CreateRole is denied outright unless the request carries the boundary, so a template that
    omits it does not create a weaker role. It fails. The trust is ec2.amazonaws.com because this
    role is worn by an instance and by nothing else, and a person able to assume it directly would
    have the machine's grants without the machine.
    """
    properties = role()
    trust = properties["AssumeRolePolicyDocument"]
    assert isinstance(trust, dict)

    boundary = properties["PermissionsBoundary"]
    assert isinstance(boundary, dict)
    assert boundary["Fn::Sub"].endswith(":policy/InternSandboxBoundary")
    assert trust["Statement"][0]["Principal"] == {"Service": "ec2.amazonaws.com"}


def test_the_agent_grant_is_the_aws_managed_one_and_not_a_copy() -> None:
    """Mutation: write the SSM actions out by hand.

    AmazonSSMManagedInstanceCore is what the agent needs and AWS maintains it. A hand-written copy
    is a list that is correct on the day it is typed and misses whatever the agent starts calling
    next, and the failure is a machine that boots and never answers a session.

    edullm-eval-smoke is the precedent in this account and carries exactly this policy. It is the
    role on the one instance in the platform's VPC that Systems Manager reports as Online.
    """
    assert as_list(role()["ManagedPolicyArns"]) == [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    ]


def test_the_machine_reaches_the_working_tier_and_no_other_bucket() -> None:
    """THE STATEMENT THAT DECIDES WHAT A LANE MACHINE CAN TOUCH.
    Mutation: add the outputs bucket, or the sealed one.

    The lane's files go to edullm-scratch and the machine syncs them both ways. Reaching further
    would make a machine nobody reviews into a principal that can read the sealed corpora every
    comparable result depends on, and the researcher's own session already reaches those under a
    role that is reviewed and bounded.
    """
    entry = next(one for one in statements() if one.get("Sid") == "TheWorkingTierAndNothingElse")

    assert set(as_list(entry["Action"])) == {
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
    }
    assert set(as_list(entry["Resource"])) == {
        f"arn:aws:s3:::{SCRATCH_BUCKET}",
        f"arn:aws:s3:::{SCRATCH_BUCKET}/*",
    }


def test_the_instance_profile_is_the_name_the_launch_passes() -> None:
    """Mutation: rename either side.

    run_instances_argv passes --iam-instance-profile Name=<this>, and a mismatch is a launch that
    fails with a message about a profile rather than about a name, on a call that has already
    priced a machine.
    """
    profile = next(
        value["Properties"]
        for value in resources().values()
        if value.get("Type") == "AWS::IAM::InstanceProfile"
    )

    assert profile["InstanceProfileName"] == LANE_INSTANCE_PROFILE


def test_the_role_is_in_the_drift_registry_the_capture_reads() -> None:
    """Mutation: add the template and not the registry.

    A role nothing captures is a role the account and the tree can disagree about silently, which
    is the shape that hid five dead GPU queues from the whole suite. The registry is what
    tools/capture_phase3_evidence.py iterates.
    """
    from edullm_platform.role_drift import LANE_ROLE_TEMPLATES

    assert [name for name, _ in LANE_ROLE_TEMPLATES] == ["edullm-lane-instance"]
