"""The premises Phase 3 rests on, read from the committed capture.

Phase 3's first plan revision asserted that a service control policy forbids creating a VPC
in this account. Seven of the ten actions it named are authorized in ``us-east-1``. The
assertion came from a policy simulation nobody had checked against a fact already
established, and it went into a document.

So the premises are a capture that expires rather than a paragraph that does not. These
tests do not prove the account is like this now -- nothing in this repository can, and the
freshness window is what says so. They prove the committed record says what it is read as
saying, and that the classifier still agrees with four answers whose verdicts were settled
some other way.

The four controls are the load-bearing part. Without them the matrix is a list of verdicts
produced by the same code that is being trusted to produce them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.phase3_evidence import (
    AccountMeasurements,
    group_opaque_identifier,
    ungroup_opaque_identifier,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTURE = PROJECT_ROOT / "fixtures" / "evidence" / "phase-3" / "account-measurements.sanitized.json"

HOME_REGION = "us-east-1"
SECOND_REGION = "us-east-2"
INSTANCE_TYPE = "c7i.8xlarge"


@pytest.fixture(scope="module")
def measurements() -> AccountMeasurements:
    return AccountMeasurements.model_validate(
        json.loads(CAPTURE.read_text(encoding="utf-8"))
    )


def test_the_capture_is_committed_and_inside_its_freshness_window(
    measurements: AccountMeasurements,
) -> None:
    """Loading at all is the assertion. FreshEvidenceModel refuses a stale record."""
    assert measurements.schema_version == 1
    assert measurements.environment == "sandbox"


def test_every_control_in_the_capture_agrees_with_the_verdict_established_elsewhere(
    measurements: AccountMeasurements,
) -> None:
    """A matrix whose controls disagree is a matrix whose classifier is wrong."""
    assert measurements.controls_agree
    disagreeing = [control.action for control in measurements.controls if not control.agrees]
    assert disagreeing == []


def test_the_capture_records_how_it_was_measured(
    measurements: AccountMeasurements,
) -> None:
    """The method is the part that was wrong last time, so it travels with the answer."""
    assert "--dry-run" in measurements.method
    assert "SimulatePrincipalPolicy" in measurements.method


def test_us_east_1_authorizes_the_networking_phase_3_creates(
    measurements: AccountMeasurements,
) -> None:
    home = measurements.region(HOME_REGION)
    assert home is not None
    for action in (
        "ec2:CreateVpc",
        "ec2:CreateSubnet",
        "ec2:CreateSecurityGroup",
        "ec2:CreateRouteTable",
        "ec2:CreateInternetGateway",
        "ec2:RunInstances",
    ):
        assert home.verdict_for(action) == "authorized", action


def test_us_east_2_is_not_a_fallback(measurements: AccountMeasurements) -> None:
    """Recorded because Phase 4 plans to 'consider both permitted regions' and cannot.

    The mutation this catches is a later reader assuming the region lock means both
    regions are usable. Four of the six calls a compute environment needs are refused
    there.
    """
    second = measurements.region(SECOND_REGION)
    assert second is not None
    for action in (
        "ec2:CreateVpc",
        "ec2:CreateSubnet",
        "ec2:CreateSecurityGroup",
        "ec2:RunInstances",
    ):
        assert second.verdict_for(action) == "denied", action


def test_the_two_regions_genuinely_differ(measurements: AccountMeasurements) -> None:
    """The single fact the policy simulator got wrong, asserted as a difference.

    A simulation reported the same answer for both regions. Any future measurement that
    reports them identical is either a real change or the same mistake again, and both are
    worth stopping on.
    """
    home = measurements.region(HOME_REGION)
    second = measurements.region(SECOND_REGION)
    assert home is not None and second is not None
    assert home.verdict_for("ec2:CreateVpc") != second.verdict_for("ec2:CreateVpc")


def test_the_vpc_quota_has_room_for_a_vpc_we_own(
    measurements: AccountMeasurements,
) -> None:
    """The blocker that turned out to be a quota rather than a permission, and then cleared.

    The mutation this catches is the compute environment being pointed back at a borrowed
    VPC after we have room for our own.
    """
    quota = measurements.vpc_quota
    assert quota.quota_code == "L-F678F1CE"
    assert quota.adjustable
    assert not quota.exhausted
    assert quota.increase_requested


def test_the_subnet_list_excludes_any_zone_that_does_not_offer_the_instance_type(
    measurements: AccountMeasurements,
) -> None:
    """The quiet failure this phase is most likely to ship.

    Batch does not fail a job it cannot place; it waits. A subnet in a zone that does not
    offer the shape produces a job stuck in RUNNABLE and no error anywhere. The mutation
    this catches is usable_subnet_ids being widened to every subnet.
    """
    placement = measurements.placement
    usable = set(placement.usable_subnet_ids)
    unusable = {
        subnet.subnet_id for subnet in placement.subnets if not subnet.instance_type_offered
    }
    assert usable
    assert usable.isdisjoint(unusable)
    assert usable | unusable == {subnet.subnet_id for subnet in placement.subnets}


def test_at_least_one_zone_does_not_offer_the_instance_type(
    measurements: AccountMeasurements,
) -> None:
    """Not a requirement, a tripwire.

    us-east-1e does not offer c7i.8xlarge today. If that stops being true the exclusion
    above stops being exercised by real data, and a reader should find that out here
    rather than discover the filter was vacuous.
    """
    offered = [subnet.instance_type_offered for subnet in measurements.placement.subnets]
    assert not all(offered), (
        "every zone now offers the instance type, so the placement filter is no longer "
        "exercised by this capture; re-read test_the_subnet_list_excludes_any_zone..."
    )


def test_batch_was_greenfield_when_this_phase_started(
    measurements: AccountMeasurements,
) -> None:
    """Nothing Phase 3 builds was inherited, which is worth being able to show later."""
    assert measurements.batch.greenfield
    assert measurements.batch.standard_on_demand_vcpu_quota >= 32


def test_the_batch_service_linked_role_did_not_exist(
    measurements: AccountMeasurements,
) -> None:
    """Why creating it is a build step rather than an assumption, and a laptop one."""
    assert measurements.service_linked_role_exists("AWSServiceRoleForBatch") is False


def test_an_opaque_identifier_round_trips_through_its_hyphenated_form() -> None:
    """The reformatting is presentation, not redaction, and this is what says so.

    The mutation this catches is somebody making group_opaque_identifier lossy -- dropping
    characters or truncating -- to get past the scan, which would leave a request id in the
    record that does not identify the request.
    """
    issued = "eee630cb39294a78ad1ed8818c9c0a84cgsdUwmS"
    grouped = group_opaque_identifier(issued)
    assert grouped != issued
    assert "-" in grouped
    assert ungroup_opaque_identifier(grouped) == issued


def test_the_recorded_request_id_recovers_to_forty_characters(
    measurements: AccountMeasurements,
) -> None:
    recorded = measurements.vpc_quota.increase_request_id
    assert recorded is not None
    assert len(ungroup_opaque_identifier(recorded)) == 40
