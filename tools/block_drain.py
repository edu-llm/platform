"""Make every capacity block node put what it is holding into S3, and count what landed.

**THIS IS THE REPORT AND THE NODES ARE THE SCHEDULE.** ``infra/block-node-bootstrap.sh``
installs a systemd timer that flushes ``/scratch`` on its own, on the machine, needing nothing
from GitHub or from anybody being awake, because the deadline is AWS terminating eight
instances against a wall clock and a scheduled workflow is not delivered at a particular
minute. What this adds is the half a node cannot do: one reading across the fleet, saying which
machines still hold something, who is on each of them, and what is left. Run it from a laptop
between now and the end of the window, and by ``.github/workflows/block-drain.yml`` for the
roughly fifteen people here who hold no AWS role and can only read a job summary.

**IT IS SAFE TO RUN AT ANY POINT AND IT IS MEANT TO BE RUN OFTEN.** The drain on the node is an
``aws s3 sync``, which is incremental, so the second call over an unchanged prefix is a listing
and nothing else. Nothing here deletes, nothing here terminates, and nothing here stops a
training run unless ``--stop-runs`` is typed.

**THE ONE ARGUMENT THAT DOES SOMETHING IRREVERSIBLE IS ``--stop-runs``.** It asks the trainer
inside each container to shut down so that OLMo-core writes its final checkpoint whole rather
than being cut off mid-write. That is the right thing to do in the last few minutes of a window
and the wrong thing to do at any other time, and no schedule passes it -- see the node helper
for why ``docker stop`` is not what it does.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.block_drain import (
    CHECKPOINT_PREFIX,
    RECLAIM_MARGIN_MINUTES,
    Checkpoints,
    Countdown,
    DrainReading,
    countdown,
    drain_markdown,
    drain_rows,
    outstanding,
    parse_drain_reading,
    read_checkpoints,
    unflushed_instances,
)
from edullm_platform.block_fleet import NODE_TAG, RESERVATION_TAG, FleetNode, read_fleet
from edullm_platform.capture_tooling import CaptureFailedError, aws_json

__all__ = ["build_parser", "collect", "main"]

#: What Systems Manager calls a finished invocation, whichever way it finished. The same set
#: ``tools/block_status.py`` polls against, and restated rather than imported for the reason
#: that file gives about tools importing one another.
TERMINAL_STATUSES: Final = frozenset(
    {"Success", "Failed", "Cancelled", "TimedOut", "Undeliverable", "Terminated"}
)

#: How long a node may take to finish its own drain before Systems Manager gives up on the
#: command. Generous where the status probe is not, because this one is copying a filesystem
#: rather than reading two counters, and the first flush of a run directory nobody has drained
#: before is the slow one by construction.
COMMAND_EXECUTION_SECONDS: Final = 1800

#: How long a command may sit undelivered before it is abandoned. Delivery rather than
#: execution -- these are two different Systems Manager timeouts and confusing them produces a
#: drain that is killed part way through a copy.
COMMAND_DELIVERY_SECONDS: Final = 600

#: Between polls of the invocation list. Longer than the status tool's, because nobody is
#: waiting on this one interactively and each poll is an API call against a fleet-wide command.
POLL_SECONDS: Final = 10

#: How many objects of a checkpoint prefix are read before the reading is declared partial.
#: A distributed checkpoint is a handful of objects per step, so this covers hundreds of saves
#: and still bounds what one report costs against a bucket somebody has been writing to for
#: three days.
CHECKPOINT_LISTING_LIMIT: Final = 5000


def _fleet(*, reservation_id: str | None, profile: str | None, region: str) -> tuple[FleetNode, ...]:
    """Every running node, from EC2 rather than from Systems Manager.

    EC2 is the denominator here for the reason ``tools/block_status.py`` gives: a machine that
    is running and produced no invocation is the finding, and a reader that started from the
    invocation list has nothing to notice.
    """
    filters = [
        f"Name=tag-key,Values={NODE_TAG}",
        "Name=instance-state-name,Values=running",
    ]
    if reservation_id is not None:
        filters.append(f"Name=tag:{RESERVATION_TAG},Values={reservation_id}")
    return read_fleet(
        aws_json(
            ["ec2", "describe-instances", "--filters", *filters], profile=profile, region=region
        )
    )


def _tagged_reservations(
    *, profile: str | None, region: str
) -> tuple[str, ...]:
    """Which blocks have a fleet up, read off the instances rather than out of a variable.

    A scheduled run takes no inputs, so it has to discover what it is looking at. Reading the
    tag means the report configures itself when a fleet comes up and goes quiet on its own
    after the last instance is gone, with nothing to set and nothing to unset -- and two live
    blocks are visible as two answers rather than silently collapsed into one.
    """
    described = aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            f"Name=tag-key,Values={RESERVATION_TAG}",
            "Name=instance-state-name,Values=running",
            "--query",
            f"Reservations[].Instances[].Tags[?Key=='{RESERVATION_TAG}'].Value",
        ],
        profile=profile,
        region=region,
    )
    found: set[str] = set()
    for group in described or []:
        for value in group if isinstance(group, list) else [group]:
            if isinstance(value, str) and value:
                found.add(value)
    return tuple(sorted(found))


def _window_end(*, reservation_id: str, profile: str | None, region: str) -> datetime:
    """When the purchase says the window closes, which is not when the machines go.

    ``block_drain.countdown`` subtracts the reclaim margin; this is only the raw end date, read
    from the reservation so that nothing anywhere holds a copy of a date.
    """
    described = aws_json(
        [
            "ec2",
            "describe-capacity-reservations",
            "--capacity-reservation-ids",
            reservation_id,
        ],
        profile=profile,
        region=region,
    )
    rows = described.get("CapacityReservations") or []
    if len(rows) != 1:
        raise CaptureFailedError(f"reservation_matched:{len(rows)}")
    ends_at = rows[0].get("EndDate")
    if not ends_at:
        raise CaptureFailedError("reservation_carries_no_end_date")
    return datetime.fromisoformat(str(ends_at)).astimezone(UTC)


def _send_drain(
    *, reservation_id: str, profile: str | None, region: str, stop_runs: bool
) -> str:
    """Ask every node in one block to drain itself, in one call.

    Targeted by tag rather than by instance id, for the reason ``tools/block_status.py``
    records: an ``--instance-ids`` call fails outright when one of the ids is not a managed
    instance, which would cost the drain of the other seven at the moment that matters most.
    """
    command = "edullm-node drain --stop-runs" if stop_runs else "edullm-node drain"
    answer = aws_json(
        [
            "ssm",
            "send-command",
            "--document-name",
            "AWS-RunShellScript",
            "--comment",
            "edullm block drain",
            "--targets",
            f"Key=tag:{RESERVATION_TAG},Values={reservation_id}",
            "--timeout-seconds",
            str(COMMAND_DELIVERY_SECONDS),
            "--parameters",
            json.dumps(
                {
                    "commands": [command],
                    "executionTimeout": [str(COMMAND_EXECUTION_SECONDS)],
                }
            ),
        ],
        profile=profile,
        region=region,
    )
    command_id = (answer.get("Command") or {}).get("CommandId")
    if not isinstance(command_id, str) or not command_id:
        raise CaptureFailedError("send_command_returned_no_command_id")
    return command_id


def _invocations(
    *, command_id: str, profile: str | None, region: str
) -> dict[str, Mapping[str, Any]]:
    listed = aws_json(
        ["ssm", "list-command-invocations", "--command-id", command_id, "--details"],
        profile=profile,
        region=region,
    )
    found: dict[str, Mapping[str, Any]] = {}
    for invocation in listed.get("CommandInvocations") or []:
        if isinstance(invocation, Mapping) and invocation.get("InstanceId"):
            found[str(invocation["InstanceId"])] = invocation
    return found


def _output_of(invocation: Mapping[str, Any]) -> str:
    return "".join(
        str(plugin.get("Output") or "")
        for plugin in invocation.get("CommandPlugins") or []
        if isinstance(plugin, Mapping)
    )


def collect(
    *,
    reservation_id: str,
    profile: str | None,
    region: str,
    wait_seconds: int,
    stop_runs: bool,
) -> tuple[DrainReading, ...]:
    """One drain per running node, waiting only as long as the slowest node needs."""
    fleet = _fleet(reservation_id=reservation_id, profile=profile, region=region)
    if not fleet:
        return ()

    command_id = _send_drain(
        reservation_id=reservation_id, profile=profile, region=region, stop_runs=stop_runs
    )
    deadline = time.monotonic() + wait_seconds
    answers: dict[str, Mapping[str, Any]] = {}
    while True:
        answers = _invocations(command_id=command_id, profile=profile, region=region)
        settled = sum(
            1
            for invocation in answers.values()
            if str(invocation.get("Status")) in TERMINAL_STATUSES
        )
        if settled >= len(fleet) or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)

    readings: list[DrainReading] = []
    for node in fleet:
        invocation = answers.get(node.instance_id)
        if invocation is None:
            # A machine EC2 says is running that Systems Manager produced no invocation for.
            # Its agent has never registered, so nothing has drained it and nothing will --
            # which is the single most important line this report can carry.
            readings.append(
                parse_drain_reading(
                    node=node.node,
                    instance_id=node.instance_id,
                    status="agent has not registered",
                    output="",
                )
            )
            continue
        readings.append(
            parse_drain_reading(
                node=node.node,
                instance_id=node.instance_id,
                status=str(invocation.get("Status") or ""),
                output=_output_of(invocation),
            )
        )
    return tuple(readings)


def checkpoints_of(
    readings: Sequence[DrainReading],
    *,
    reservation_id: str,
    bucket: str,
    profile: str | None,
    region: str,
) -> dict[str, Checkpoints]:
    """Which of each run's saved checkpoints are checkpoints, and which are interrupted writes.

    Read from the runner rather than from the node, because it costs a bucket listing either
    way and doing it here keeps the shell on the machine to the one job it has to do on time.
    """
    found: dict[str, Checkpoints] = {}
    for reading in readings:
        if reading.node is None:
            continue
        for run in reading.runs:
            prefix = (
                f"block/{reservation_id}/node-{reading.node}/{run.run}/{CHECKPOINT_PREFIX}"
            )
            listing = aws_json(
                [
                    "s3api",
                    "list-objects-v2",
                    "--bucket",
                    bucket,
                    "--prefix",
                    f"{prefix}/",
                    "--max-items",
                    str(CHECKPOINT_LISTING_LIMIT),
                ],
                profile=profile,
                region=region,
            )
            reading_of = read_checkpoints(listing, prefix=prefix)
            if reading_of.complete or reading_of.torn:
                found[run.run] = reading_of
    return found


def _as_json(
    readings: Sequence[DrainReading], *, clock: Countdown, checkpoints: Mapping[str, Checkpoints]
) -> str:
    return json.dumps(
        {
            "ends_at": clock.ends_at.isoformat(),
            "reclaim_at": clock.reclaim_at.isoformat(),
            "remaining_seconds": int(clock.remaining.total_seconds()),
            "horizon_minutes": clock.horizon,
            "nodes": [
                {
                    "node": reading.node,
                    "instance_id": reading.instance_id,
                    "reachable": reading.reachable,
                    "detail": reading.detail,
                    "flushed": reading.flushed,
                    "who": reading.who,
                    "run": reading.run,
                    "container": reading.container,
                    "stopped": list(reading.stopped),
                    "drained_at": (
                        reading.drained_at.isoformat() if reading.drained_at is not None else None
                    ),
                    "runs": [
                        {
                            "run": found.run,
                            "local": found.local,
                            "remote": found.remote,
                            "status": found.status,
                        }
                        for found in reading.runs
                    ],
                }
                for reading in readings
            ],
            "checkpoints": {
                run: {
                    "complete": list(found.complete),
                    "torn": list(found.torn),
                    "truncated": found.truncated,
                }
                for run, found in sorted(checkpoints.items())
            },
        },
        indent=2,
        sort_keys=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """Named so ``tests/test_workflow_tool_arguments.py`` can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--reservation",
        default=None,
        help=(
            "which block to drain. Discovered from the instance tags when left off, which is "
            "what a scheduled run does, and required when two blocks are live at once"
        ),
    )
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--bucket", default="edullm-block-outputs-us-east-2")
    # A laptop is a first-class caller here and not an afterthought, which is why this defaults
    # rather than being None. `--no-profile` is what a workflow runner passes, holding ambient
    # credentials from a role it already assumed.
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument(
        "--no-profile",
        dest="profile",
        action="store_const",
        const=None,
        help="use the ambient credentials, which is what a workflow runner has",
    )
    parser.add_argument(
        "--ends-at",
        default=None,
        help=(
            "override the window end, as an ISO-8601 instant. Only for rehearsing the report; "
            "the real value is read off the reservation"
        ),
    )
    parser.add_argument(
        "--reclaim-minutes",
        type=int,
        default=RECLAIM_MARGIN_MINUTES,
        help="how long before the window end AWS starts terminating the fleet",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=900,
        help="how long to wait for the slowest node before reporting it as not answering",
    )
    parser.add_argument(
        "--stop-runs",
        action="store_true",
        help=(
            "ask the trainer in each container to shut down so it writes a final checkpoint "
            "whole. Irreversible, ends somebody's run early, and no schedule passes it"
        ),
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="append the report as markdown to this file, which is GITHUB_STEP_SUMMARY",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve_reservation(arguments: argparse.Namespace) -> str | None:
    """Which block to drain, or ``None`` for "there is no fleet up".

    **NO FLEET IS AN ANSWER AND NOT A FAILURE, WHICH MATTERS BECAUSE OF WHO CALLS THIS.** A
    scheduled workflow runs this every quarter of an hour and goes on running it after the
    window has closed and the instances are gone. A refusal there would paint the repository
    red every fifteen minutes for a state that is completely normal, and a check that is red
    when nothing is wrong is a check nobody reads on the morning something is.

    Two live blocks is the opposite: node numbers repeat across fleets, so draining "the block"
    is not a question with one answer, and guessing is how the wrong eight machines get told to
    stop their runs.
    """
    if arguments.reservation is not None:
        return str(arguments.reservation)
    live = _tagged_reservations(profile=arguments.profile, region=arguments.region)
    if len(live) > 1:
        raise CaptureFailedError(f"more_than_one_block_has_a_fleet_up:{','.join(live)}")
    return live[0] if live else None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        reservation_id = _resolve_reservation(arguments)
        if reservation_id is None:
            print("no running instance carries a block tag, so there is nothing to drain")
            return 0
        ends_at = (
            datetime.fromisoformat(arguments.ends_at).astimezone(UTC)
            if arguments.ends_at
            else _window_end(
                reservation_id=reservation_id,
                profile=arguments.profile,
                region=arguments.region,
            )
        )
        readings = collect(
            reservation_id=reservation_id,
            profile=arguments.profile,
            region=arguments.region,
            wait_seconds=arguments.wait_seconds,
            stop_runs=arguments.stop_runs,
        )
        checkpoints = checkpoints_of(
            readings,
            reservation_id=reservation_id,
            bucket=arguments.bucket,
            profile=arguments.profile,
            region=arguments.region,
        )
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return 2

    clock = countdown(
        ends_at=ends_at,
        now=datetime.now(tz=UTC),
        reclaim_minutes=arguments.reclaim_minutes,
    )

    if arguments.summary:
        with Path(arguments.summary).open("a", encoding="utf-8") as page:
            page.write(drain_markdown(readings, clock=clock, checkpoints=checkpoints) + "\n")

    if arguments.json:
        print(_as_json(readings, clock=clock, checkpoints=checkpoints))
    else:
        print(clock.describe())
        print("\n".join(drain_rows(readings)) if readings else "no fleet carries a block tag")

    # A NON-ZERO EXIT IS RESERVED FOR SOMETHING BEING UNSAVED, WHICH IS WHY IT IS NOT MERELY
    # "a node did not answer". The scheduled workflow runs this every quarter of an hour for
    # three days, and a report that goes red for a machine nobody is using is a report people
    # learn to ignore before the one morning it matters.
    stranded = unflushed_instances(readings)
    if stranded:
        for reading in readings:
            if reading.instance_id in stranded:
                print(
                    f"block_drain_incomplete:{reading.instance_id} {outstanding(reading)}",
                    file=sys.stderr,
                )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
