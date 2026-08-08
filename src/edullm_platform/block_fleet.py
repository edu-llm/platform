"""What a capacity block fleet is, read out of the four API answers that describe one.

**THE FUNCTION THIS MODULE EXISTS FOR IS** :func:`unreserved` **AND EVERYTHING ELSE HERE IS
ARRANGEMENT AROUND IT.** A capacity block is a targeted reservation: nothing draws from it
unless the launch names it, and a ``RunInstances`` that forgot to does not fail. It succeeds,
and what it starts is an ordinary on-demand machine at full rate beside a block that has
already been paid for in full. On the shape this was written for that is roughly $55 an hour
per instance, against a purchase that cannot be cancelled and is not refundable.

``CapacityReservationId`` on a described instance is the only field anywhere that tells the two
apart. Same instance type, same zone, same tags, same state, same everything a console listing
shows -- and one of them is free because it was already bought and the other is not. So the
launch workflow reads every instance back and asks that one question, and this is where the
question is asked, in a module a test can reach.

**WHY THE LIBRARY AND NOT THE WORKFLOW.** ``.github/workflows/block-launch-fleet.yml`` could
have carried this as twenty lines of inline Python, which is what the three deploy workflows do
with their verification. Those check a shape that is asserted in the same file it is deployed
from, so an inline copy is readable beside what it checks. This one decides whether to
terminate eight machines, it runs exactly once against a window nobody rehearses, and the
mutation that matters -- treating a *different* reservation id as acceptable because the field
is not null -- is invisible to review and obvious to a test.

**A DIFFERENT RESERVATION IS AS WRONG AS NO RESERVATION**, and that is the case the obvious
implementation gets wrong. ``if not instance.capacity_reservation_id`` passes an instance
drawing from somebody else's block, which is a real state in an account that can hold two at
once: the fleet would be verified, reported green, and quietly consuming capacity bought for a
different piece of work. The comparison is against the expected id and nothing weaker.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

__all__ = [
    "NODE_TAG",
    "READY_SENTINEL",
    "REMOTE_READING_SCRIPT",
    "RESERVATION_TAG",
    "FleetNode",
    "NodeReading",
    "Readiness",
    "elapsed_as",
    "fleet_table",
    "parse_reading",
    "read_fleet",
    "readiness",
    "status_rows",
    "status_table",
    "unreserved",
]

#: Which node of the fleet a machine is, one-based, written at launch. It is one-based because
#: the whole point of it is that a person says "I am on node three" out loud to another person,
#: and nobody counts machines from zero in a conversation.
NODE_TAG: Final = "edullm:node"

#: Which purchase paid for this machine. On the instance so that an account listing during a
#: window says which block it is looking at, and as the filter every other piece of this lane
#: selects the fleet by -- two blocks in one month is two fleets, and node numbers repeat.
RESERVATION_TAG: Final = "edullm:block"

#: The file ``infra/block-node-bootstrap.sh`` writes last, and only on the path where every
#: check before it passed. Its presence is the whole readiness signal; the launch workflow does
#: not try to infer readiness from an instance state, because a p5 passes its EC2 status checks
#: several minutes before it has a driver, a scratch filesystem or a training image.
READY_SENTINEL: Final = "/var/lib/edullm/ready.json"


@dataclass(frozen=True)
class FleetNode:
    """One machine, reduced to the five things anybody asks about it.

    ``node`` is optional because the tag is optional in the data even though the launch always
    writes it: an instance carrying the block tag and no node tag is a real thing this has to
    be able to report, and it is exactly what a half-finished launch leaves behind. Dropping
    such an instance would hide it from the verification below, which is the one place it must
    not be hidden from.
    """

    node: int | None
    instance_id: str
    state: str
    private_ip: str | None
    capacity_reservation_id: str | None


@dataclass(frozen=True)
class Readiness:
    """What one node answered when asked whether it had finished bootstrapping."""

    instance_id: str
    ready: bool
    detail: str


@dataclass(frozen=True)
class NodeReading:
    """What one node says it is doing, as ``tools/block_status.py`` reports it.

    ``reachable`` is separate from every other field rather than folded into them. A node that
    Systems Manager could not deliver to and a node that answered "nothing is running here"
    are opposite findings, and the second one invites somebody to start a run on a machine that
    is already busy with a run nobody could see.

    ``ready`` is a third state and not a shade of the second, for the same reason. A node whose
    agent came up but whose bootstrap died -- no driver, no scratch filesystem, no pre-pulled
    image -- answers this reading perfectly and answers it with nothing running, which renders
    as the most attractive machine in the fleet. It is the least.
    """

    node: int | None
    instance_id: str
    reachable: bool
    ready: bool
    detail: str
    gpus_busy: int
    gpus_total: int
    who: str | None
    run: str | None
    started_at: datetime | None


def _tags(instance: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(tag.get("Key")): str(tag.get("Value"))
        for tag in instance.get("Tags") or []
        if isinstance(tag, Mapping)
    }


def _node_number(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


def read_fleet(described: Mapping[str, Any]) -> tuple[FleetNode, ...]:
    """Every instance in a ``describe-instances`` answer, flattened and ordered.

    EC2 nests instances under reservations -- the ``RunInstances`` kind of reservation, which
    has nothing to do with the capacity kind and is the single most confusing overload in this
    API. One call per node means one such group per node, so a reader that stopped at the first
    group would report a fleet of one and verify it happily.

    Ordered by node number with the untagged ones last, so that the table a person reads at
    06:00 is in the order they will refer to the machines in.
    """
    fleet: list[FleetNode] = []
    for group in described.get("Reservations") or []:
        if not isinstance(group, Mapping):
            continue
        for instance in group.get("Instances") or []:
            if not isinstance(instance, Mapping):
                continue
            reservation = instance.get("CapacityReservationId")
            fleet.append(
                FleetNode(
                    node=_node_number(_tags(instance).get(NODE_TAG)),
                    instance_id=str(instance.get("InstanceId") or ""),
                    state=str((instance.get("State") or {}).get("Name") or "unknown"),
                    private_ip=(
                        str(instance["PrivateIpAddress"])
                        if instance.get("PrivateIpAddress")
                        else None
                    ),
                    capacity_reservation_id=(
                        str(reservation) if isinstance(reservation, str) and reservation else None
                    ),
                )
            )
    return tuple(sorted(fleet, key=lambda found: (found.node is None, found.node or 0, found.instance_id)))


def unreserved(fleet: Iterable[FleetNode], *, reservation_id: str) -> tuple[FleetNode, ...]:
    """Every instance that is not drawing from the block this launch was told to use.

    Equality against the expected id rather than a truthiness test on the field. See the module
    header: an instance drawing from a *different* reservation is as wrong as one drawing from
    none, and it is the case a null check waves through.
    """
    return tuple(
        node for node in fleet if node.capacity_reservation_id != reservation_id
    )


def fleet_table(fleet: Sequence[FleetNode], *, unready: Collection[str] = ()) -> str:
    """The fleet as the launch workflow prints it.

    The reservation column is not decoration and is not truncated. It is the field the whole
    launch turns on, and a reader who cannot see it has to take the workflow's word for the
    thing the workflow exists to check.
    """
    if not fleet:
        return "no instances carry this block tag"
    header = f"{'node':<6}{'instance':<21}{'private ip':<17}{'state':<11}{'reservation':<22}ready"
    lines = [header, "-" * len(header)]
    for found in fleet:
        lines.append(
            f"{found.node if found.node is not None else '-':<6}"
            f"{found.instance_id:<21}"
            f"{found.private_ip or '-':<17}"
            f"{found.state:<11}"
            f"{found.capacity_reservation_id or 'NONE':<22}"
            f"{'no' if found.instance_id in unready else 'yes'}"
        )
    return "\n".join(lines)


def readiness(invocations: Mapping[str, Any]) -> tuple[Readiness, ...]:
    """What each node said when asked to print its readiness sentinel.

    Only a ``Success`` invocation is read at all. A command that timed out or was undeliverable
    has told us nothing about the node, and reading its empty output as "the sentinel is
    absent" would be a statement about the agent presented as a statement about the bootstrap.

    The sentinel is a JSON object and this does not parse it. What matters is that the
    bootstrap got far enough to write one, and the failure record the bootstrap writes instead
    is also JSON -- so the discriminator is the key inside it, which is why the readiness test
    is the presence of ``ready_at`` rather than the output being non-empty.
    """
    answered: list[Readiness] = []
    for invocation in invocations.get("CommandInvocations") or []:
        if not isinstance(invocation, Mapping):
            continue
        instance_id = str(invocation.get("InstanceId") or "")
        status = str(invocation.get("Status") or "")
        if status != "Success":
            answered.append(
                Readiness(instance_id=instance_id, ready=False, detail=f"invocation {status}")
            )
            continue
        output = "".join(
            str(plugin.get("Output") or "")
            for plugin in invocation.get("CommandPlugins") or []
            if isinstance(plugin, Mapping)
        ).strip()
        if '"ready_at"' in output:
            answered.append(Readiness(instance_id=instance_id, ready=True, detail=output))
        elif '"failed_at"' in output:
            answered.append(
                Readiness(instance_id=instance_id, ready=False, detail=f"bootstrap failed: {output}")
            )
        else:
            answered.append(Readiness(instance_id=instance_id, ready=False, detail="still booting"))
    return tuple(sorted(answered, key=lambda found: found.instance_id))


#: What every node is asked, and it deliberately does not go through ``edullm-node``.
#:
#: The helper on the machine answers the same question and is the right thing for a person
#: sitting in a shell. It is the wrong thing for this, because the node most worth asking about
#: is the one whose bootstrap did not finish -- and on that node the helper is not installed, so
#: a reader built on it would report the single most interesting machine in the fleet as
#: unreachable. This snippet needs nothing but ``nvidia-smi`` and ``cat``.
#:
#: Tab-separated key and value rather than JSON, because emitting JSON from shell means either
#: quoting by hand or depending on ``jq``, and ``jq`` is not on every image this AMI family has
#: ever shipped. A missing key is an absent fact; there are no optional values to get wrong.
REMOTE_READING_SCRIPT: Final = r"""
total=$(nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null | grep -c . || true)
busy=$(nvidia-smi --query-compute-apps=gpu_uuid --format=csv,noheader 2>/dev/null | sort -u | grep -c . || true)
printf 'gpus_total\t%s\n' "${total:-0}"
printf 'gpus_busy\t%s\n' "${busy:-0}"
if [ -f /var/lib/edullm/claim.json ]; then
  sed -n 's/.*"run":"\([^"]*\)".*/run\t\1/p' /var/lib/edullm/claim.json
  sed -n 's/.*"who":"\([^"]*\)".*/who\t\1/p' /var/lib/edullm/claim.json
  sed -n 's/.*"started_at":"\([^"]*\)".*/started_at\t\1/p' /var/lib/edullm/claim.json
fi
if [ -f /var/lib/edullm/ready.json ]; then printf 'ready\ttrue\n'; fi
"""


def parse_reading(
    *, node: int | None, instance_id: str, status: str, output: str
) -> NodeReading:
    """One node's answer, or the reason there is not one.

    ``status`` is the Systems Manager invocation status and it is read before the output is.
    An invocation that never ran produces an empty output, and an empty output parses perfectly
    into a node with no GPUs busy and nobody on it -- which is the reading that tells somebody
    a busy machine is free. Every non-``Success`` status is unreachable and carries the status
    as its reason.
    """
    if status != "Success":
        return NodeReading(
            node=node,
            instance_id=instance_id,
            reachable=False,
            ready=False,
            detail=status or "no answer",
            gpus_busy=0,
            gpus_total=0,
            who=None,
            run=None,
            started_at=None,
        )

    fields: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("\t")
        if separator and value.strip():
            fields[key.strip()] = value.strip()

    started: datetime | None = None
    if "started_at" in fields:
        try:
            started = datetime.fromisoformat(fields["started_at"])
        except ValueError:
            started = None

    return NodeReading(
        node=node,
        instance_id=instance_id,
        reachable=True,
        ready=fields.get("ready") == "true",
        detail="",
        gpus_busy=int(fields.get("gpus_busy", "0") or 0),
        gpus_total=int(fields.get("gpus_total", "0") or 0),
        who=fields.get("who") or None,
        run=fields.get("run") or None,
        started_at=started,
    )


def elapsed_as(since: datetime, *, now: datetime) -> str:
    """How long a claim has been held, in the units a person asks the question in.

    Hours and minutes, never days, because the longest window this lane is written for is
    seventy-two hours and "running 2d3h" makes somebody do arithmetic to find out whether a run
    is about to be cut off by the end of the block. A negative interval -- a claim written by a
    node whose clock is ahead of the laptop reading it -- reports as zero rather than as a
    negative duration, which is the kind of output people mail screenshots of.
    """
    interval = max(now - since, timedelta(0))
    hours, remainder = divmod(int(interval.total_seconds()), 3600)
    return f"{hours}h{remainder // 60:02d}m"


def status_rows(readings: Sequence[NodeReading], *, now: datetime) -> tuple[str, ...]:
    """One line per node, in the shape the fleet is discussed in.

    ``IDLE`` is a whole line rather than a row of dashes on purpose. The question this tool is
    asked, forty times over a weekend, is "which node can I take", and the answer wants to be
    scannable down a column rather than read across one.

    A node with nobody on it and cards in use is *not* idle and does not say so. That is a run
    somebody started outside the workflow, or one whose claim was released while it was still
    training, and reporting it as free is how two runs end up on eight cards.

    Neither is a node whose bootstrap never finished. It answers this reading exactly as a free
    machine does -- no claim, no cards in use -- and it is the one machine in the fleet that
    cannot run anything, because it has no scratch filesystem and no pre-pulled image. It reads
    ``NOT READY`` so that the person looking for a free node takes the next one instead of
    spending twenty minutes finding out.
    """
    lines: list[str] = []
    for reading in readings:
        label = f"node {reading.node if reading.node is not None else '?'}"
        head = f"{label:<8}{reading.instance_id:<21}"
        if not reading.reachable:
            lines.append(f"{head}UNREACHABLE  {reading.detail}")
            continue
        if not reading.ready and reading.run is None and reading.gpus_busy == 0:
            lines.append(f"{head}NOT READY    its bootstrap never wrote {READY_SENTINEL}")
            continue
        if reading.run is None and reading.gpus_busy == 0:
            lines.append(f"{head}IDLE")
            continue
        held = (
            f"running {elapsed_as(reading.started_at, now=now)}"
            if reading.started_at is not None
            else "running"
        )
        lines.append(
            f"{head}"
            f"{reading.gpus_busy}/{reading.gpus_total} GPUs busy   "
            f"{reading.who or '-':<9} "
            f"{reading.run or '-':<18} "
            f"{held}"
        )
    return tuple(lines)


def status_table(readings: Sequence[NodeReading], *, now: datetime) -> str:
    return "\n".join(status_rows(readings, now=now))
