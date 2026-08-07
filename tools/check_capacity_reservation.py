"""Say whether a purchased capacity block is actually going to happen.

WHY THIS EXISTS, WHICH IS ONE FAILURE NOTHING ELSE CATCHES. Every other GPU queue on this
platform cancels a job that has sat in ``RUNNABLE`` for half an hour under
``CAPACITY:INSUFFICIENT_INSTANCE_CAPACITY``. The capacity block queue deliberately does not,
and ``infra/batch-capacity-block.yaml`` argues it at length: before the window opens, Batch
attempts the allocation and EC2 declines, so a job submitted the day before sits in exactly
the state that cancel is written to kill. Removing the cancel is what lets a submission be
made, admitted and approved on somebody's own time and then place itself when the block opens.

The cost of removing it is stated there just as plainly: a job that will *never* place has
nothing to end it, and nothing automatic can tell that job from the one waiting correctly for
tomorrow. The template names the control -- "whoever bought the block confirms the reservation
reached ``scheduled`` rather than ``payment-failed``" -- and calls it the weaker guard and the
honest one.

This is that control, written down so it is one command rather than a memory. A block bought
on a Saturday evening whose payment quietly failed, discovered on Sunday afternoon by somebody
wondering why a job never started, is the worst version of the weekend this tool exists for.

IT ALSO PRINTS THE DISPATCH INPUTS, AND THAT IS NOT A CONVENIENCE. The values the deploy
workflow needs -- instance type, zone, total vCPU -- are properties of the reservation, and the
alternative to reading them off this output is reading them off a console and retyping them
into a form. The workflow cross-checks the instance type against the catalog and refuses a
mismatch, but the cheapest place to not make the mistake is here.

Nothing in this file changes anything. It makes one read-only EC2 call.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog

#: The reservation will happen: the money was taken and the window is either ahead or open.
EXIT_ON_TRACK: Final = 0
#: It will not happen, or it is not the thing that was meant to be bought. The loud door.
EXIT_WRONG: Final = 1
#: Bad arguments, no credential, or a reservation this account cannot see.
EXIT_UNUSABLE: Final = 2
#: Not settled yet. Distinct from EXIT_WRONG on purpose: minutes after a purchase this is the
#: ordinary answer, and conflating it with a failed payment would either cry wolf or, worse,
#: teach somebody to ignore the one exit code that matters.
EXIT_NOT_YET: Final = 3

#: States that mean the purchase completed. ``active`` is included because a block checked
#: inside its own window is on track by definition -- and because this tool being run late is a
#: likely way it gets run at all.
SETTLED: Final = frozenset({"scheduled", "active"})
#: States that resolve on their own, given time.
PENDING: Final = frozenset({"payment-pending", "assessing"})

__all__ = [
    "EXIT_NOT_YET",
    "EXIT_ON_TRACK",
    "EXIT_UNUSABLE",
    "EXIT_WRONG",
    "Reservation",
    "Verdict",
    "describe",
    "read_reservation",
    "verdict_for",
]


@dataclass(frozen=True)
class Reservation:
    """The fields of a capacity reservation this check reads, and nothing else."""

    reservation_id: str
    state: str
    instance_type: str
    availability_zone: str
    total_instances: int
    reservation_type: str
    match_criteria: str
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True)
class Verdict:
    exit_code: int
    headline: str
    detail: tuple[str, ...]


def read_reservation(client: Any, reservation_id: str) -> Reservation:
    """One ``DescribeCapacityReservations`` call, narrowed to the fields that decide anything.

    A reservation that has expired and aged out of the account's listing is reported by the API
    as not found rather than as expired, which is worth knowing because it is how a real block
    this account genuinely used came to look as though it had never existed.
    """
    response = client.describe_capacity_reservations(CapacityReservationIds=[reservation_id])
    found = response.get("CapacityReservations") or []
    if not found:
        message = (
            f"{reservation_id} is not a reservation this account can see. Either the id is "
            "wrong, or it has expired and aged out of the listing."
        )
        raise LookupError(message)
    record = found[0]
    return Reservation(
        reservation_id=record["CapacityReservationId"],
        state=record["State"],
        instance_type=record["InstanceType"],
        availability_zone=record["AvailabilityZone"],
        total_instances=int(record["TotalInstanceCount"]),
        reservation_type=record.get("ReservationType", "default"),
        match_criteria=record.get("InstanceMatchCriteria", "open"),
        starts_at=record.get("StartDate"),
        ends_at=record.get("EndDate"),
    )


#: What infra/batch-network.yaml calls the subnet it creates in a given zone. The deploy wants
#: the subnet id rather than the export name, and the zone is a property of the reservation, so
#: this is the one dispatch input that is derivable but not readable off the reservation row.
SUBNET_EXPORT = "sbsandbox-intern-edullm-batch-subnet-{zone}"


def subnet_export_name(zone: str) -> str:
    return SUBNET_EXPORT.format(zone=zone)


def subnet_in_zone(client: Any, zone: str) -> str | None:
    """The batch subnet id in the block's zone, read from the export that publishes it.

    None rather than an exception when it cannot be found, because a missing subnet does not
    change the verdict on the reservation and this tool's answer about the money should not
    depend on a second call succeeding. What it costs is one input the reader looks up by hand.
    """
    wanted = subnet_export_name(zone)
    paginator = client.get_paginator("list_exports")
    for page in paginator.paginate():
        for export in page.get("Exports", []):
            if export.get("Name") == wanted:
                value = export.get("Value")
                return None if value is None else str(value)
    return None


def expected_instance_type(profile: str, catalog_path: Path) -> str:
    entries = {
        entry.name: entry for entry in load_yaml(catalog_path, WorkloadCatalog).compute_profiles
    }
    if profile not in entries:
        message = f"{profile} is not a compute profile in {catalog_path}"
        raise LookupError(message)
    return entries[profile].instance_type


def verdict_for(
    reservation: Reservation,
    *,
    profile: str | None = None,
    instance_type: str | None = None,
    vcpus_per_instance: int | None = None,
    subnet_id: str | None = None,
) -> Verdict:
    """The whole decision, as a pure function of what EC2 said.

    Separated from the call so the states that matter can be tested without an account, which
    for ``payment-failed`` is the only way they can be tested at all: it cannot be provoked on
    demand and it is the state the tool exists for.
    """
    detail: list[str] = [
        f"reservation      {reservation.reservation_id}",
        f"state            {reservation.state}",
        f"instance type    {reservation.instance_type} x {reservation.total_instances}",
        f"zone             {reservation.availability_zone}",
    ]
    if reservation.starts_at is not None and reservation.ends_at is not None:
        hours = (reservation.ends_at - reservation.starts_at).total_seconds() / 3600
        detail += [
            (
                f"window           {reservation.starts_at:%Y-%m-%d %H:%M %Z}"
                f" to {reservation.ends_at:%Y-%m-%d %H:%M %Z} ({hours:.1f}h)"
            ),
        ]
        # AWS begins terminating instances thirty minutes before the end, so the hours that can
        # be trained on are not the hours that were bought. Stated here because it is the figure
        # a run length should be chosen against.
        detail += [f"usable           about {hours - 0.5:.1f}h, since AWS reclaims the last 30m"]

    # NOT A CAPACITY BLOCK AT ALL IS WORTH REFUSING RATHER THAN NOTING. An ordinary on-demand
    # capacity reservation bills by the hour for as long as it exists and has no window, so
    # deploying the block stack against one produces a queue that works and a bill nobody
    # budgeted. The two are told apart only by this field.
    if reservation.reservation_type != "capacity-block":
        return Verdict(
            EXIT_WRONG,
            f"{reservation.reservation_id} is a {reservation.reservation_type} reservation, not a "
            "capacity block. Do not deploy the block stack against it: it bills by the hour for "
            "as long as it exists.",
            tuple(detail),
        )

    if (
        profile is not None
        and instance_type is not None
        and reservation.instance_type != instance_type
    ):
        return Verdict(
            EXIT_WRONG,
            f"this reservation is {reservation.instance_type} and {profile} is "
            f"{instance_type}. One of the two is not what was meant -- either the block "
            "bought is a different shape, or the wrong profile is being deployed for.",
            tuple(detail),
        )

    if reservation.state in PENDING:
        return Verdict(
            EXIT_NOT_YET,
            f"not settled yet: {reservation.state}. The money has not been taken. This is "
            "ordinary in the minutes after a purchase -- run this again, or pass --watch and "
            "leave it. Do not submit anything until it reads scheduled.",
            tuple(detail),
        )

    if reservation.state not in SETTLED:
        return Verdict(
            EXIT_WRONG,
            f"THIS BLOCK IS NOT GOING TO HAPPEN: state is {reservation.state}. A job submitted "
            "against it will sit in RUNNABLE forever, because the block queue has no cancel for "
            "capacity that never arrives. Nothing will tell you again.",
            tuple(detail),
        )

    lines = list(detail)
    if vcpus_per_instance is not None:
        lines += [
            "",
            "the deploy dispatch wants exactly these:",
            f"  capacity_block_profile            {profile}",
            f"  capacity_reservation_id           {reservation.reservation_id}",
            f"  capacity_block_instance_type      {reservation.instance_type}",
            f"  capacity_block_availability_zone  {reservation.availability_zone}",
            f"  capacity_block_max_vcpus          {vcpus_per_instance * reservation.total_instances}",
            "  capacity_block_subnet_id          "
            + (
                subnet_id
                if subnet_id is not None
                else f"look up the {subnet_export_name(reservation.availability_zone)} export"
            ),
        ]
    return Verdict(
        EXIT_ON_TRACK,
        f"on track: {reservation.state}. The purchase completed. A job submitted now waits in "
        "RUNNABLE and places itself when the window opens; nobody needs to be awake for it.",
        tuple(lines),
    )


def describe(verdict: Verdict) -> str:
    body = "\n".join(verdict.detail)
    return f"{verdict.headline}\n\n{body}" if body else verdict.headline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reservation_id", help="the cr- id the purchase produced")
    parser.add_argument(
        "--profile",
        default=None,
        help=(
            "the compute profile this block is for, so the instance type can be checked against "
            "config/workload-catalog.yaml and the dispatch inputs printed"
        ),
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--aws-profile", default=None, help="a named AWS credential profile")
    parser.add_argument(
        "--catalog", type=Path, default=Path("config/workload-catalog.yaml"), help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "keep checking every 60 seconds while the state is unsettled, so a purchase can be "
            "left to confirm itself rather than remembered"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    if not arguments.reservation_id.startswith("cr-"):
        print(f"{arguments.reservation_id} is not a cr- reservation id", file=sys.stderr)
        return EXIT_UNUSABLE

    instance_type: str | None = None
    vcpus: int | None = None
    if arguments.profile is not None:
        try:
            instance_type = expected_instance_type(arguments.profile, arguments.catalog)
            from edullm_platform.execution import CONTAINER_SHAPES

            shape = CONTAINER_SHAPES.get(arguments.profile)
            vcpus = None if shape is None else shape.vcpus
        except (OSError, ValueError, LookupError) as exc:
            print(f"the reviewed configuration could not be read: {exc}", file=sys.stderr)
            return EXIT_UNUSABLE

    import boto3  # type: ignore[import-not-found]  # in the runtime, not in pyproject

    session = boto3.Session(profile_name=arguments.aws_profile, region_name=arguments.region)
    client = session.client("ec2")

    while True:
        try:
            reservation = read_reservation(client, arguments.reservation_id)
        except LookupError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_UNUSABLE
        except Exception as exc:  # noqa: BLE001 - a credential or endpoint problem, reported as one
            print(f"EC2 could not be asked: {exc}", file=sys.stderr)
            return EXIT_UNUSABLE

        # Only once the reservation is settled, so an unsettled check stays a single EC2 call and
        # a failed payment is reported without waiting on a second service to answer.
        subnet: str | None = None
        if reservation.state in SETTLED and arguments.profile is not None:
            try:
                subnet = subnet_in_zone(
                    session.client("cloudformation"), reservation.availability_zone
                )
            except Exception:  # noqa: BLE001 - one dispatch input is not worth failing the check
                subnet = None

        verdict = verdict_for(
            reservation,
            profile=arguments.profile,
            instance_type=instance_type,
            vcpus_per_instance=vcpus,
            subnet_id=subnet,
        )
        if verdict.exit_code != EXIT_NOT_YET or not arguments.watch:
            stream = sys.stdout if verdict.exit_code == EXIT_ON_TRACK else sys.stderr
            print(describe(verdict), file=stream)
            return verdict.exit_code

        print(
            f"{datetime.now(tz=UTC):%H:%M:%S} {reservation.state}, waiting",
            file=sys.stderr,
        )
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
