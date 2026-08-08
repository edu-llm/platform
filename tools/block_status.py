"""Who is on which capacity block node, asked of the nodes rather than of a spreadsheet.

The question this answers is asked forty times over a seventy-two hour window and it has
exactly one useful form: which of the eight machines can I take. A shared sheet answers what
people *said* they would do, which diverges from what is running on hour three, and diverges
fastest at the moment somebody is in a hurry.

**THE FAN-OUT IS ONE CALL AND NOT EIGHT, WHICH IS WHERE THE SPEED COMES FROM.** Systems
Manager resolves a tag target itself and runs the command on every matching instance
concurrently, so this sends one ``send-command`` and then polls one invocation list. Eight
sequential sessions would be tens of seconds and this is a couple.

**A NODE THAT DOES NOT ANSWER IS REPORTED AS NOT ANSWERING AND NEVER AS IDLE.** That is the
one distinction this tool must not blur. An unreachable machine and an unclaimed machine look
identical to any reader built on "did we get output", and the second one is an invitation to
start a run on a node that is already training. Tag targeting is used rather than
``--instance-ids`` for the same reason: an instance whose agent has not registered makes an
``--instance-ids`` call fail outright with ``InvalidInstanceId``, taking the reading of the
other seven with it, where a tag target simply produces no invocation for it -- which this
reconciles against the EC2 listing and reports.

**IT READS `nvidia-smi` AND THE CLAIM FILE DIRECTLY RATHER THAN CALLING `edullm-node`.** The
helper on the machine answers the same question and is what a person in a shell should use.
It is installed by the bootstrap, near the end, so on the one node most worth asking about --
the one whose bootstrap did not finish -- it is not there. See
``edullm_platform.block_fleet.REMOTE_READING_SCRIPT``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from edullm_platform.block_fleet import (
    NODE_TAG,
    REMOTE_READING_SCRIPT,
    RESERVATION_TAG,
    FleetNode,
    NodeReading,
    parse_reading,
    read_fleet,
    status_table,
)
from edullm_platform.capture_tooling import CaptureFailedError, aws_json

__all__ = ["build_parser", "collect", "main", "send_reading_command"]

#: What Systems Manager calls a finished invocation, whichever way it finished. Polling stops
#: when every node has reached one of these or the deadline passes; a node still ``InProgress``
#: at the deadline is reported as not having answered, which is what it is.
TERMINAL_STATUSES: Final = frozenset(
    {"Success", "Failed", "Cancelled", "TimedOut", "Undeliverable", "Terminated"}
)

#: How long the command itself may take on a node before Systems Manager gives up on it. The
#: script runs two ``nvidia-smi`` queries and reads a small file, so anything longer than this
#: is a machine in trouble rather than a machine being slow, and waiting is not the answer.
COMMAND_TIMEOUT_SECONDS: Final = 30

#: Between polls of the invocation list. Short, because the whole point of this tool is that
#: somebody types it and reads the answer rather than going to make coffee.
POLL_SECONDS: Final = 2


def _instances(
    *, reservation_id: str | None, profile: str | None, region: str
) -> tuple[FleetNode, ...]:
    """Every running node in the fleet, from EC2 rather than from Systems Manager.

    EC2 is asked first and is the denominator. A node that exists and is running but has no
    Systems Manager invocation is the finding this tool exists to surface, and a reader that
    started from the invocation list would never see it -- there is nothing there to see.
    """
    filters = [
        f"Name=tag-key,Values={NODE_TAG}",
        "Name=instance-state-name,Values=running",
    ]
    if reservation_id is not None:
        filters.append(f"Name=tag:{RESERVATION_TAG},Values={reservation_id}")
    described = aws_json(
        ["ec2", "describe-instances", "--filters", *filters],
        profile=profile,
        region=region,
    )
    return read_fleet(described)


def send_reading_command(
    *, reservation_id: str | None, profile: str | None, region: str
) -> str:
    """Ask every node at once, and answer with the command id to read the replies from.

    Targeted by tag rather than by instance id. Both forms reach the same machines when
    everything is healthy; they differ exactly when something is not, and the difference is
    whether one unregistered agent costs the reading of the whole fleet.
    """
    target = (
        f"Key=tag:{RESERVATION_TAG},Values={reservation_id}"
        if reservation_id is not None
        else f"Key=tag-key,Values={NODE_TAG}"
    )
    answer = aws_json(
        [
            "ssm",
            "send-command",
            "--document-name",
            "AWS-RunShellScript",
            "--comment",
            "edullm block status",
            "--targets",
            target,
            "--timeout-seconds",
            str(COMMAND_TIMEOUT_SECONDS),
            "--parameters",
            json.dumps({"commands": [REMOTE_READING_SCRIPT]}),
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
    """The plugin output, which ``list-command-invocations --details`` nests one level down.

    Truncated by Systems Manager at 2,500 characters, which this reading cannot exceed: the
    remote script prints at most five short lines.
    """
    return "".join(
        str(plugin.get("Output") or "")
        for plugin in invocation.get("CommandPlugins") or []
        if isinstance(plugin, Mapping)
    )


def collect(
    *,
    reservation_id: str | None,
    profile: str | None,
    region: str,
    wait_seconds: int,
) -> tuple[NodeReading, ...]:
    """One reading per running node, waiting only as long as the slowest node needs.

    The loop exits the moment every node has reached a terminal status rather than sleeping
    out the deadline, so the common case -- eight healthy machines -- costs one send, one
    list and a couple of seconds.
    """
    fleet = _instances(reservation_id=reservation_id, profile=profile, region=region)
    if not fleet:
        return ()

    command_id = send_reading_command(
        reservation_id=reservation_id, profile=profile, region=region
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

    readings: list[NodeReading] = []
    for node in fleet:
        invocation = answers.get(node.instance_id)
        if invocation is None:
            # THE CASE THE TAG TARGET MAKES VISIBLE. Systems Manager produced no invocation at
            # all for a machine EC2 says is running, which means the agent has never
            # registered -- a node still booting, or one whose bootstrap died before the agent
            # came up. It is emphatically not an idle node.
            readings.append(
                parse_reading(
                    node=node.node,
                    instance_id=node.instance_id,
                    status="agent has not registered",
                    output="",
                )
            )
            continue
        readings.append(
            parse_reading(
                node=node.node,
                instance_id=node.instance_id,
                status=str(invocation.get("Status") or ""),
                output=_output_of(invocation),
            )
        )
    return tuple(readings)


def _as_json(readings: Sequence[NodeReading]) -> str:
    return json.dumps(
        [
            {
                "node": reading.node,
                "instance_id": reading.instance_id,
                "reachable": reading.reachable,
                "ready": reading.ready,
                "detail": reading.detail,
                "gpus_busy": reading.gpus_busy,
                "gpus_total": reading.gpus_total,
                "who": reading.who,
                "run": reading.run,
                "started_at": (
                    reading.started_at.isoformat() if reading.started_at is not None else None
                ),
            }
            for reading in readings
        ],
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
            "restrict to one block. Only needed while two are live at once, because node "
            "numbers repeat across fleets"
        ),
    )
    parser.add_argument("--region", default="us-east-2")
    # A LAPTOP IS THE PRIMARY CALLER HERE, WHICH IS WHY THIS DEFAULTS RATHER THAN BEING NONE.
    # `tools/verify_deployed_stacks.py` defaults to no profile because the audit runs it under
    # an assumed role and a default would send it hunting for an SSO session that is not
    # there. This one is typed by a person between runs, and `--no-profile` is the flag a
    # workflow passes instead.
    parser.add_argument("--profile", default="sbsandbox")
    parser.add_argument(
        "--no-profile",
        dest="profile",
        action="store_const",
        const=None,
        help="use the ambient credentials, which is what a workflow runner has",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=45,
        help="how long to wait for the slowest node before reporting it as not answering",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        readings = collect(
            reservation_id=arguments.reservation,
            profile=arguments.profile,
            region=arguments.region,
            wait_seconds=arguments.wait_seconds,
        )
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return 2

    if arguments.json:
        print(_as_json(readings))
        return 0
    if not readings:
        print("no running instance carries an edullm:node tag in this region")
        return 0
    print(status_table(readings, now=datetime.now(tz=UTC)))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
