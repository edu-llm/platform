"""The EC2 authorization probe, held to the four answers whose verdicts are already known.

This file exists because of a specific failure rather than for coverage. Phase 3's plan was
first written asserting that a service control policy forbids creating a VPC in this
account. Seven of the ten actions it named are authorized in ``us-east-1``. The wrong
answer came from ``iam:SimulatePrincipalPolicy``, and it was believed because it was
specific and plausible and nothing checked it against a fact already established.

So the classifier is not tested against invented strings. Every control in
:data:`CONTROL_OBSERVATIONS` is a real captured stderr whose verdict was settled some other
way -- by CloudTrail showing a peer principal performing the action, or by making the real
call and watching it fail on quota. A refactor that collapsed any two verdicts together
would go red here whatever else still passed.

The four are deliberately one per verdict, and the two that matter most are the ones a
careless implementation merges. ``UnauthorizedOperation`` and ``VpcLimitExceeded`` are both
non-zero exits carrying an error, and treating them alike would erase the distinction that
decided where Phase 3 runs: one is a support request, the other is not fixable by us.
"""

from __future__ import annotations

import pytest

from edullm_platform.ec2_authorization import (
    CONTROL_OBSERVATIONS,
    PHASE3_EC2_PROBES,
    ControlObservation,
    Ec2AuthorizationVerdict,
    classify_dry_run,
    phase3_ec2_probes,
    verdicts_by_action,
)

VPC_ID = "vpc-00000000000000000"
SUBNET_ID = "subnet-00000000000000000"
IMAGE_ID = "ami-00000000000000000"
INSTANCE_TYPE = "c7i.8xlarge"


def classify(control: ControlObservation) -> Ec2AuthorizationVerdict:
    return classify_dry_run(
        action=control.action,
        operation=control.operation,
        region=control.region,
        returncode=control.returncode,
        stderr=control.stderr,
    ).verdict


@pytest.mark.parametrize(
    "control",
    CONTROL_OBSERVATIONS,
    ids=[f"{c.action}-{c.region}-{c.expected.value}" for c in CONTROL_OBSERVATIONS],
)
def test_every_control_classifies_to_the_verdict_established_elsewhere(
    control: ControlObservation,
) -> None:
    assert classify(control) is control.expected


def test_the_controls_cover_every_verdict() -> None:
    """A control set missing a verdict cannot catch that verdict being merged into another."""
    covered = {control.expected for control in CONTROL_OBSERVATIONS}
    assert covered == set(Ec2AuthorizationVerdict)


def test_every_control_says_how_its_verdict_was_established() -> None:
    """A control whose expected value rests on nothing is a fixture, not a control."""
    for control in CONTROL_OBSERVATIONS:
        assert control.established_by.strip()


def test_denial_and_quota_are_not_the_same_answer() -> None:
    """The distinction that decided which region Phase 3 runs in.

    Both are non-zero exits carrying a service error. Merging them would report a quota
    that a support request clears as a permission nobody can change.
    """
    denied = classify_dry_run(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-2",
        returncode=254,
        stderr=(
            "An error occurred (UnauthorizedOperation) when calling the CreateVpc "
            "operation: You are not authorized to perform this operation."
        ),
    )
    quota_blocked = classify_dry_run(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-1",
        returncode=254,
        stderr=(
            "An error occurred (VpcLimitExceeded) when calling the CreateVpc operation: "
            "The maximum number of VPCs has been reached."
        ),
    )
    assert denied.verdict is Ec2AuthorizationVerdict.DENIED
    assert quota_blocked.verdict is Ec2AuthorizationVerdict.QUOTA_BLOCKED
    assert not denied.authorized
    assert quota_blocked.authorized


def test_an_answer_naming_another_operation_is_not_an_answer() -> None:
    """Same discipline as the denial matrices: a different operation is a different question."""
    result = classify_dry_run(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-1",
        returncode=254,
        stderr=(
            "An error occurred (DryRunOperation) when calling the CreateSubnet operation: "
            "Request would have succeeded, but DryRun flag is set."
        ),
    )
    assert result.verdict is Ec2AuthorizationVerdict.INCONCLUSIVE
    assert result.reason == "answer_named_another_operation:CreateSubnet"


def test_a_request_rejected_before_authorization_says_nothing_about_the_caller() -> None:
    """The mistake that was actually made, pinned so it cannot be made again silently."""
    result = classify_dry_run(
        action="ec2:RunInstances",
        operation="RunInstances",
        region="us-east-1",
        returncode=254,
        stderr=(
            "An error occurred (InvalidAMIID.Malformed) when calling the RunInstances "
            'operation: Invalid id: "ami-0abcdef1234567890" (expecting "ami-...")'
        ),
    )
    assert result.verdict is Ec2AuthorizationVerdict.INCONCLUSIVE
    assert result.reason == "request_did_not_reach_authorization"


def test_a_dry_run_that_succeeded_is_inconclusive_rather_than_authorized() -> None:
    """A zero exit means the flag was dropped, which means something may have been created."""
    result = classify_dry_run(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-1",
        returncode=0,
        stderr="",
    )
    assert result.verdict is Ec2AuthorizationVerdict.INCONCLUSIVE
    assert result.reason == "dry_run_succeeded_so_the_flag_was_not_honoured"


def test_stderr_with_no_service_error_is_inconclusive() -> None:
    result = classify_dry_run(
        action="ec2:CreateVpc",
        operation="CreateVpc",
        region="us-east-1",
        returncode=255,
        stderr="Unable to locate credentials. You can configure credentials by running...",
    )
    assert result.verdict is Ec2AuthorizationVerdict.INCONCLUSIVE
    assert result.reason == "no_service_error_in_stderr"


def test_every_probe_carries_the_action_it_claims_and_the_operation_it_will_be_told() -> None:
    probes = phase3_ec2_probes(
        vpc_id=VPC_ID,
        subnet_id=SUBNET_ID,
        image_id=IMAGE_ID,
        instance_type=INSTANCE_TYPE,
    )
    for probe in probes:
        assert probe.action.startswith("ec2:")
        assert probe.action.split(":", 1)[1] == probe.operation


def test_the_declared_action_list_matches_the_probes_actually_built() -> None:
    """The docstring's list and the code's list cannot drift apart unnoticed."""
    probes = phase3_ec2_probes(
        vpc_id=VPC_ID,
        subnet_id=SUBNET_ID,
        image_id=IMAGE_ID,
        instance_type=INSTANCE_TYPE,
    )
    assert tuple(probe.action for probe in probes) == PHASE3_EC2_PROBES


def test_no_probe_carries_its_own_dry_run_flag() -> None:
    """The runner adds --dry-run, so a probe cannot be defined without it by forgetting.

    A probe that carried the flag itself could equally be defined without it, and a
    create-vpc that is not a dry run creates a VPC.
    """
    probes = phase3_ec2_probes(
        vpc_id=VPC_ID,
        subnet_id=SUBNET_ID,
        image_id=IMAGE_ID,
        instance_type=INSTANCE_TYPE,
    )
    for probe in probes:
        assert "--dry-run" not in probe.arguments


def test_probes_that_need_a_real_resource_reference_the_one_they_were_given() -> None:
    """A probe naming an invented vpc or ami is answered by the resource, not the caller."""
    probes = phase3_ec2_probes(
        vpc_id=VPC_ID,
        subnet_id=SUBNET_ID,
        image_id=IMAGE_ID,
        instance_type=INSTANCE_TYPE,
    )
    by_action = {probe.action: probe for probe in probes}
    assert VPC_ID in by_action["ec2:CreateSubnet"].arguments
    assert VPC_ID in by_action["ec2:CreateSecurityGroup"].arguments
    assert VPC_ID in by_action["ec2:CreateRouteTable"].arguments
    assert IMAGE_ID in by_action["ec2:RunInstances"].arguments
    assert SUBNET_ID in by_action["ec2:RunInstances"].arguments


def test_verdicts_by_action_round_trips_the_results() -> None:
    results = [
        classify_dry_run(
            action=control.action,
            operation=control.operation,
            region=control.region,
            returncode=control.returncode,
            stderr=control.stderr,
        )
        for control in CONTROL_OBSERVATIONS
    ]
    mapping = verdicts_by_action(results)
    assert mapping["ec2:CreateSecurityGroup"] is Ec2AuthorizationVerdict.AUTHORIZED
