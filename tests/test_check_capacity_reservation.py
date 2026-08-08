"""The reservation check, against the states it has to tell apart.

WHY THESE ARE UNIT TESTS OF A PURE FUNCTION AND NOT A CAPTURE. ``payment-failed`` is the state
this tool exists for and it cannot be provoked: nobody can make a card decline on request, and
a fixture recorded from a successful purchase would only ever exercise the branch that was
already obviously right. So the verdict is a function of what EC2 said, and what EC2 said is
supplied here.

The one that matters is ``test_a_failed_payment_is_the_loud_door``. Everything else in this file
is there so that branch cannot be reached by accident from a healthy reservation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.check_capacity_reservation import (
    EXIT_NOT_YET,
    EXIT_ON_TRACK,
    EXIT_WRONG,
    Reservation,
    describe,
    expected_instance_type,
    read_reservation,
    subnet_in_zone,
    verdict_for,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"

#: The Sunday block, as the purchase would produce it.
STARTS = datetime(2026, 8, 9, 11, 30, tzinfo=UTC)
ENDS = datetime(2026, 8, 10, 11, 30, tzinfo=UTC)


def reservation(**overrides: object) -> Reservation:
    fields: dict[str, object] = {
        "reservation_id": "cr-0123456789abcdef0",
        "state": "scheduled",
        "instance_type": "p6-b200.48xlarge",
        "availability_zone": "us-east-1d",
        "total_instances": 1,
        "reservation_type": "capacity-block",
        "match_criteria": "targeted",
        "starts_at": STARTS,
        "ends_at": ENDS,
    }
    fields.update(overrides)
    return Reservation(**fields)  # type: ignore[arg-type]


def test_a_failed_payment_is_the_loud_door() -> None:
    """The whole reason this tool exists, and the one branch nothing else in the platform has.

    Every other GPU queue cancels a job that has sat in RUNNABLE for half an hour. The block
    queue deliberately does not, because before the window opens a correctly-waiting job looks
    exactly like a stuck one. That trade is argued in infra/batch-capacity-block.yaml and it
    leaves one hole: a job that will never place has nothing to end it. This is the guard, so
    it says so rather than reporting a state code and leaving the reader to know what it means.
    """
    verdict = verdict_for(reservation(state="payment-failed"))

    assert verdict.exit_code == EXIT_WRONG
    assert "NOT GOING TO HAPPEN" in verdict.headline
    assert "RUNNABLE forever" in verdict.headline
    # Says that nothing will raise it again, because nothing will: the block queue has no
    # cancel, so silence after this is not reassurance.
    assert "Nothing will tell you again" in verdict.headline


@pytest.mark.parametrize("state", ["cancelled", "expired", "unavailable", "failed"])
def test_no_state_that_is_not_settled_reads_as_on_track(state: str) -> None:
    """Mutation: treat the settled set as everything that is not pending.

    A closed enumeration of good states and an open one of bad states, rather than the reverse,
    so a state AWS adds later is refused rather than admitted. The direction to fail in is the
    one that stops a submission.
    """
    assert verdict_for(reservation(state=state)).exit_code == EXIT_WRONG


@pytest.mark.parametrize("state", ["payment-pending", "assessing"])
def test_an_unsettled_purchase_is_told_apart_from_a_failed_one(state: str) -> None:
    """Mutation: return EXIT_WRONG for payment-pending.

    Minutes after a purchase this is the ordinary answer. Reporting it in the same voice as a
    declined payment would either cry wolf every time or, worse, teach the reader that the one
    exit code that matters is usually noise.
    """
    verdict = verdict_for(reservation(state=state))

    assert verdict.exit_code == EXIT_NOT_YET
    assert verdict.exit_code != EXIT_WRONG
    assert "not settled yet" in verdict.headline
    assert "Do not submit anything until it reads scheduled" in verdict.headline


@pytest.mark.parametrize("state", ["scheduled", "active"])
def test_a_settled_reservation_says_nobody_needs_to_be_awake(state: str) -> None:
    """``active`` counts because a check run inside the window is a check that ran late, which
    is a likely way this gets run at all, and the answer then is still yes."""
    verdict = verdict_for(reservation(state=state))

    assert verdict.exit_code == EXIT_ON_TRACK
    assert "nobody needs to be awake" in verdict.headline


def test_an_ordinary_capacity_reservation_is_refused_rather_than_noted() -> None:
    """Mutation: report the reservation type and carry on.

    An on-demand capacity reservation bills for every hour it exists whether or not anything
    runs in it, and has no end date to stop that. Deploying the block stack against one
    produces a queue that works, which is what makes this worth an exit code: the failure is
    a bill rather than an error.
    """
    verdict = verdict_for(reservation(reservation_type="default"))

    assert verdict.exit_code == EXIT_WRONG
    assert "not a capacity block" in verdict.headline
    assert "bills by the hour" in verdict.headline


def test_a_block_of_the_wrong_shape_is_caught_before_it_is_deployed_for() -> None:
    """The deploy workflow refuses a mismatch too, and this is the cheaper place to find it:
    here it costs a re-read, there it costs a CI run inside a window that is already billed."""
    verdict = verdict_for(
        reservation(instance_type="p5.48xlarge"),
        profile="gpu-8xb200",
        instance_type="p6-b200.48xlarge",
    )

    assert verdict.exit_code == EXIT_WRONG
    assert "p5.48xlarge" in verdict.headline
    assert "gpu-8xb200" in verdict.headline


def test_the_shape_is_checked_against_the_shipped_catalog_and_not_a_literal() -> None:
    """Mutation: hard-code p6-b200.48xlarge in the tool.

    The instance type behind a profile is reviewed configuration, so the check reads it from
    there. A literal here would agree with the catalog on the day it was typed.
    """
    assert expected_instance_type("gpu-8xb200", CATALOG_PATH) == "p6-b200.48xlarge"

    with pytest.raises(LookupError):
        expected_instance_type("gpu-8xnothing", CATALOG_PATH)


def test_a_settled_block_prints_the_dispatch_inputs_it_implies() -> None:
    """Mutation: print the state and stop.

    Every value the deploy dispatch wants is a property of the reservation, so the alternative
    to printing them is reading them off a console and retyping them into a form -- under time
    pressure, into a field that silently accepts a wrong zone. MaxvCpus is the arithmetic most
    worth not doing by hand, being per-instance vCPU times the instance count.
    """
    verdict = verdict_for(
        reservation(),
        profile="gpu-8xb200",
        instance_type="p6-b200.48xlarge",
        vcpus_per_instance=192,
    )
    rendered = describe(verdict)

    assert verdict.exit_code == EXIT_ON_TRACK
    assert "capacity_block_profile            gpu-8xb200" in rendered
    assert "capacity_reservation_id           cr-0123456789abcdef0" in rendered
    assert "capacity_block_instance_type      p6-b200.48xlarge" in rendered
    assert "capacity_block_availability_zone  us-east-1d" in rendered
    assert "capacity_block_max_vcpus          192" in rendered


def test_the_subnet_is_resolved_from_the_export_that_publishes_it() -> None:
    """The one dispatch input that is neither on the reservation row nor in configuration.

    infra/batch-network.yaml creates a subnet per zone and exports each one, and the deploy
    wants the id rather than the export name. The zone is on the reservation, so the id is
    derivable -- and deriving it is worth it, because a subnet in the wrong zone is a stack that
    deploys and then never places anything.
    """

    class Exports:
        def get_paginator(self, _: str) -> object:
            class Pager:
                def paginate(self) -> list[dict[str, list[dict[str, str]]]]:
                    return [
                        {
                            "Exports": [
                                {
                                    "Name": "sbsandbox-intern-edullm-batch-subnet-us-east-1a",
                                    "Value": "subnet-aaaa",
                                },
                                {
                                    "Name": "sbsandbox-intern-edullm-batch-subnet-us-east-1d",
                                    "Value": "subnet-dddd",
                                },
                            ]
                        }
                    ]

            return Pager()

    assert subnet_in_zone(Exports(), "us-east-1d") == "subnet-dddd"
    assert subnet_in_zone(Exports(), "us-east-1f") is None

    rendered = describe(
        verdict_for(
            reservation(),
            profile="gpu-8xb200",
            instance_type="p6-b200.48xlarge",
            vcpus_per_instance=192,
            subnet_id="subnet-dddd",
        )
    )
    assert "capacity_block_subnet_id          subnet-dddd" in rendered


def test_an_unresolved_subnet_asks_for_the_export_rather_than_going_quiet() -> None:
    """Mutation: print an empty value. A blank beside five filled fields reads as "none needed",
    and the deploy would take an empty subnet and fail late."""
    rendered = describe(
        verdict_for(
            reservation(),
            profile="gpu-8xb200",
            instance_type="p6-b200.48xlarge",
            vcpus_per_instance=192,
        )
    )

    assert "sbsandbox-intern-edullm-batch-subnet-us-east-1d" in rendered


def test_the_usable_window_is_reported_shorter_than_the_bought_one() -> None:
    """AWS begins terminating thirty minutes before the end, so the hours that can be trained
    on are not the hours that were paid for. It is stated because it is the figure a run length
    should be chosen against, and the difference is the kind that gets found at the end."""
    rendered = describe(verdict_for(reservation()))

    assert "(24.0h)" in rendered
    assert "about 23.5h" in rendered
    assert "reclaims the last 30m" in rendered


def test_a_reservation_that_aged_out_is_reported_as_unseeable_not_as_absent() -> None:
    """This is not hypothetical. A p6-b200 block this account bought in July and used for 42.95
    hours reads as never having existed, because a reservation that expires ages out of the
    listing -- and one agent concluded from exactly that silence that no P-family card had ever
    run here. The message names the second possibility so the next reader does not."""

    class Empty:
        def describe_capacity_reservations(self, **_: object) -> dict[str, list[object]]:
            return {"CapacityReservations": []}

    with pytest.raises(LookupError) as caught:
        read_reservation(Empty(), "cr-010377794039df5cc")

    assert "expired and aged out" in str(caught.value)


def test_the_fields_the_check_needs_are_read_off_the_api_shape() -> None:
    """Mutation: read StartDate off the wrong key. Defaults are supplied only for the two
    fields an ordinary reservation may omit, so a missing State or InstanceType raises here
    rather than defaulting into a verdict."""

    class One:
        def describe_capacity_reservations(self, **_: object) -> dict[str, object]:
            return {
                "CapacityReservations": [
                    {
                        "CapacityReservationId": "cr-0123456789abcdef0",
                        "State": "scheduled",
                        "InstanceType": "p6-b200.48xlarge",
                        "AvailabilityZone": "us-east-1d",
                        "TotalInstanceCount": 1,
                        "ReservationType": "capacity-block",
                        "InstanceMatchCriteria": "targeted",
                        "StartDate": STARTS,
                        "EndDate": ENDS,
                    }
                ]
            }

    read = read_reservation(One(), "cr-0123456789abcdef0")

    assert read == reservation()
