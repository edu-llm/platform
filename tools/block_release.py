"""Hand a capacity block node back, from somewhere that is not the node.

**WHO THIS IS FOR IS THE WHOLE OF WHY IT EXISTS.** ``block-run.yml`` learned to tell a
researcher that the node they asked for carries a claim whose container is gone -- a real
improvement on naming a colleague who went home -- and the remedy it printed was ``edullm-node
release`` on the machine. Roughly fifteen of the thirty-five people here hold no AWS role,
cannot assume one and cannot open a Systems Manager session, so that sentence sends them
somewhere they cannot go. ``.github/workflows/block-release.yml`` holds the credential and runs
this; a researcher holds a browser.

**IT CANNOT KILL LIVE WORK AND THAT IS A PROPERTY OF THE NODE RATHER THAN OF THIS FILE.**
``do_release`` in ``infra/block-node-bootstrap.sh`` refuses while a container of the claimed run
is up, and ``--force`` is the sentence somebody has to type when they mean to abandon a claim
over a running job. Nothing here passes it, there is no flag that would, and
``tests/test_block_release.py`` holds that. So the worst this can do to a busy machine is print
the reason it would not touch it.

**IT NAMES THE NODES IT TOUCHES AND NEVER SWEEPS BY TAG BY ACCIDENT.** ``tools/block_status.py``
and ``tools/block_drain.py`` both target the fleet by tag, which is right for a reading and for
an incremental copy and is wrong here: this ends locks, so a command reaching a node nobody
asked about is the failure mode rather than a convenience. Instances are resolved from EC2 and
addressed by id, and ``all`` means every node in the fleet said out loud rather than a blank
field taken as consent.

Addressing by id costs the thing tag targeting was chosen to avoid -- one unregistered agent
fails an ``--instance-ids`` call outright with ``InvalidInstanceId`` and takes the release of
every other node with it -- so Systems Manager is asked which of them it can reach first, and a
node it cannot is reported rather than sent to.
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

from edullm_platform.block_fleet import (
    NODE_TAG,
    RESERVATION_TAG,
    FleetNode,
    agents_online,
    read_fleet,
)
from edullm_platform.block_release import (
    RELEASE_SCRIPT,
    ReleaseReading,
    nodes_wanted,
    not_given_back,
    parse_release_reading,
    release_markdown,
    release_rows,
)
from edullm_platform.capture_tooling import CaptureFailedError, aws_json

__all__ = ["build_parser", "collect", "main"]

#: What Systems Manager calls a finished invocation, whichever way it finished. The same set
#: ``tools/block_status.py`` polls against, restated rather than imported for the reason that
#: file gives about tools importing one another.
TERMINAL_STATUSES: Final = frozenset(
    {"Success", "Failed", "Cancelled", "TimedOut", "Undeliverable", "Terminated"}
)

#: How long the payload may take on a node. It reads a small file, runs one ``nvidia-smi`` query
#: and removes a file, so anything past this is a machine in trouble rather than a slow one.
COMMAND_TIMEOUT_SECONDS: Final = 60

#: Between polls of the invocation list. Short, because somebody dispatched this and is watching
#: the page for the answer.
POLL_SECONDS: Final = 2


def _fleet(
    *, reservation_id: str | None, profile: str | None, region: str
) -> tuple[FleetNode, ...]:
    """Every running node, from EC2 rather than from Systems Manager.

    EC2 is the denominator for the reason ``tools/block_status.py`` gives: a machine that is
    running and produced no invocation is a finding, and a reader starting from the invocation
    list has nothing there to notice.
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


def _online(
    *, instance_ids: Sequence[str], profile: str | None, region: str
) -> frozenset[str]:
    """Which of these nodes Systems Manager is hearing from now.

    Asked before anything is sent, because the send is by instance id: one id that is not a
    managed instance makes ``send-command`` fail outright, and the cost of that is the release
    of every other node in the same dispatch. A node left out here is reported as unreachable,
    which is what it is.
    """
    if not instance_ids:
        return frozenset()
    return agents_online(
        aws_json(
            [
                "ssm",
                "describe-instance-information",
                "--filters",
                f"Key=InstanceIds,Values={','.join(instance_ids)}",
            ],
            profile=profile,
            region=region,
        )
    )


def _send_release(
    *, instance_ids: Sequence[str], who: str, profile: str | None, region: str
) -> str:
    """Ask the named nodes to give their claims back, in one call.

    **THE COMMENT CARRIES THE NAME AND IT IS THE DURABLE HALF OF THE RECORD.** A claim is a lock
    on a shared machine inside a window nobody can extend, so ending somebody else's should
    leave a trace that outlives a job summary and lives outside the system that produced it.
    Every Systems Manager command is a CloudTrail record; putting the caller in the comment is
    what makes that record say who, rather than only that the block fleet role did it. The job
    summary carries the same name for the people who cannot read CloudTrail.
    """
    answer = aws_json(
        [
            "ssm",
            "send-command",
            "--document-name",
            "AWS-RunShellScript",
            "--comment",
            f"edullm block release by {who}"[:100],
            "--instance-ids",
            *instance_ids,
            "--timeout-seconds",
            str(COMMAND_TIMEOUT_SECONDS),
            "--parameters",
            json.dumps({"commands": [RELEASE_SCRIPT]}),
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


def chosen(
    fleet: Sequence[FleetNode], *, wanted: tuple[int, ...] | None
) -> tuple[tuple[FleetNode, ...], tuple[int, ...]]:
    """The nodes to ask, and the node numbers that were asked for and are not there.

    The second half is returned rather than dropped because it is a real answer to a real
    question. Somebody typing ``9`` against an eight-node fleet, or a node number from the
    previous block, otherwise gets a report about the nodes that do exist and no mention at all
    of the one they were asking about -- which reads as that machine being fine.
    """
    if wanted is None:
        return tuple(fleet), ()
    by_number = {node.node: node for node in fleet if node.node is not None}
    return (
        tuple(by_number[number] for number in wanted if number in by_number),
        tuple(number for number in wanted if number not in by_number),
    )


def collect(
    *,
    nodes: Sequence[FleetNode],
    who: str,
    profile: str | None,
    region: str,
    wait_seconds: int,
) -> tuple[ReleaseReading, ...]:
    """One reading per node asked, waiting only as long as the slowest one needs."""
    if not nodes:
        return ()

    reachable = _online(
        instance_ids=[node.instance_id for node in nodes], profile=profile, region=region
    )
    addressable = [node for node in nodes if node.instance_id in reachable]

    answers: dict[str, Mapping[str, Any]] = {}
    if addressable:
        command_id = _send_release(
            instance_ids=[node.instance_id for node in addressable],
            who=who,
            profile=profile,
            region=region,
        )
        deadline = time.monotonic() + wait_seconds
        while True:
            answers = _invocations(command_id=command_id, profile=profile, region=region)
            settled = sum(
                1
                for invocation in answers.values()
                if str(invocation.get("Status")) in TERMINAL_STATUSES
            )
            if settled >= len(addressable) or time.monotonic() >= deadline:
                break
            time.sleep(POLL_SECONDS)

    readings: list[ReleaseReading] = []
    for node in nodes:
        invocation = answers.get(node.instance_id)
        if invocation is None:
            # Either its agent is not registered, so nothing was sent to it, or it was sent to
            # and produced no invocation. Both are "this machine was not asked and its claim is
            # untouched", which is emphatically not the same as a claim that would not come
            # back -- and it must never read as a node that is now free.
            readings.append(
                parse_release_reading(
                    node=node.node,
                    instance_id=node.instance_id,
                    status="agent has not registered",
                    output="",
                )
            )
            continue
        readings.append(
            parse_release_reading(
                node=node.node,
                instance_id=node.instance_id,
                status=str(invocation.get("Status") or ""),
                output=_output_of(invocation),
            )
        )
    return tuple(readings)


def _as_json(readings: Sequence[ReleaseReading], *, missing: Sequence[int], who: str) -> str:
    return json.dumps(
        {
            "released_by": who,
            "nodes_not_in_the_fleet": list(missing),
            "nodes": [
                {
                    "node": reading.node,
                    "instance_id": reading.instance_id,
                    "reachable": reading.reachable,
                    "detail": reading.detail,
                    "held": reading.held,
                    "who": reading.who,
                    "started_at": (
                        reading.started_at.isoformat()
                        if reading.started_at is not None
                        else None
                    ),
                    "gpus_busy": reading.gpus_busy,
                    "verdict": reading.verdict,
                    "cleared": reading.cleared,
                    "given_back": reading.given_back,
                    "said": reading.said,
                }
                for reading in readings
            ],
        },
        indent=2,
        sort_keys=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """Named so ``tests/test_workflow_tool_arguments.py`` can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--nodes",
        required=True,
        help=(
            "which node to hand back: a number, several separated by commas, or all. Required "
            "and has no default, because a blank field on a button that ends other people's "
            "locks must not mean every machine"
        ),
    )
    parser.add_argument(
        "--who",
        required=True,
        help=(
            "who is doing this. It goes into the Systems Manager comment, which is the "
            "CloudTrail record, and into the report"
        ),
    )
    parser.add_argument(
        "--reservation",
        default=None,
        help=(
            "restrict to one block. Only needed while two are live at once, because node "
            "numbers repeat across fleets"
        ),
    )
    parser.add_argument("--region", default="us-east-2")
    # A laptop is a first-class caller here, which is why this defaults rather than being None.
    # `--no-profile` is what a workflow runner passes, holding ambient credentials from a role
    # it already assumed.
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
        default=120,
        help="how long to wait for the slowest node before reporting it as not answering",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="append the report as markdown to this file, which is GITHUB_STEP_SUMMARY",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        wanted = nodes_wanted(arguments.nodes)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        fleet = _fleet(
            reservation_id=arguments.reservation,
            profile=arguments.profile,
            region=arguments.region,
        )
        asking, missing = chosen(fleet, wanted=wanted)
        readings = collect(
            nodes=asking,
            who=arguments.who,
            profile=arguments.profile,
            region=arguments.region,
            wait_seconds=arguments.wait_seconds,
        )
    except CaptureFailedError as error:
        print(error.reason, file=sys.stderr)
        return 2

    now = datetime.now(tz=UTC)
    if arguments.summary:
        with Path(arguments.summary).open("a", encoding="utf-8") as page:
            page.write(
                release_markdown(readings, now=now, who=arguments.who, missing=missing) + "\n"
            )

    if arguments.json:
        print(_as_json(readings, missing=missing, who=arguments.who))
    elif readings:
        print("\n".join(release_rows(readings, now=now)))
    else:
        print("no running instance carries a node tag here, so there was nothing to ask")

    for number in missing:
        print(
            f"node_is_not_in_this_fleet:{number} carries no running instance. Check the number, "
            "or pass --reservation if two blocks are live at once.",
            file=sys.stderr,
        )

    # WHAT A NON-ZERO EXIT MEANS HERE, AND WHY `all` IS DELIBERATELY EXEMPT FROM IT.
    #
    # Somebody who named node 3 asked a question with a yes or no answer, and a refusal is a no
    # -- so it fails, the way `block-run.yml` fails on `node_is_busy`, and a script can branch
    # on it. Somebody who passed `all` is sweeping a shared fleet, where at least one node being
    # legitimately busy is the ordinary Saturday. Failing there would paint the button red on
    # nearly every dispatch, and `tools/block_drain.py` already records what that costs: a
    # report that is red when nothing is wrong is a report nobody reads on the morning something
    # is.
    if missing:
        return 1
    if wanted is not None and not_given_back(readings):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
