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
    "EFA_INTERFACES",
    "EFA_ONLY_INTERFACE",
    "ENA_INTERFACE",
    "NODE_TAG",
    "READY_SENTINEL",
    "REMOTE_READING_SCRIPT",
    "RESERVATION_TAG",
    "FleetNode",
    "InterfacePlan",
    "NodeReading",
    "Readiness",
    "admits_its_own_members",
    "elapsed_as",
    "fleet_table",
    "interface_plan",
    "parse_reading",
    "read_fleet",
    "readiness",
    "status_rows",
    "status_table",
    "unaddressable",
    "unreserved",
    "without_the_fabric",
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

#: What EC2 calls an ordinary network interface. It carries IP and no EFA device, it is the only
#: one of the three kinds that may be an instance's primary interface, and it is therefore the
#: interface Systems Manager reaches a node over.
ENA_INTERFACE: Final = "interface"

#: The EFA device on its own, with no ENA beside it. It takes no IP address at all, which is why
#: thirty-two of them cost one private address per node rather than thirty-two.
EFA_ONLY_INTERFACE: Final = "efa-only"

#: Both spellings that put an EFA device on the machine. ``efa`` is an EFA sharing an interface
#: with an ENA and ``efa-only`` is the device alone; NCCL cannot tell them apart, so a reading
#: that counted only one of them would report a working fabric as absent.
EFA_INTERFACES: Final = frozenset({"efa", EFA_ONLY_INTERFACE})


@dataclass(frozen=True)
class InterfacePlan:
    """Every ``--network-interfaces`` argument for one node, and what it should produce.

    ``efa`` is carried beside the arguments rather than recounted by the caller so that the
    launch and the check that reads the launch back cannot disagree. Two independent counts of
    the same thing is how a verification ends up asserting what it was told rather than what
    happened.
    """

    arguments: tuple[str, ...]
    efa: int


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
    #: How many of this instance's interfaces carry an EFA device, which is the other property
    #: of a launch that nothing downstream can recover. Defaulted so that the several callers
    #: constructing a node by hand keep working; the reading below always fills it.
    efa_interfaces: int = 0
    #: The address Systems Manager reaches this machine over. Only the primary interface can
    #: hold one, and on the fabric layout it is assigned by the subnet rather than asked for --
    #: so it is a thing to check rather than a thing to assume.
    public_ip: str | None = None


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


def interface_plan(
    described: Mapping[str, Any],
    *,
    subnet_id: str,
    security_group_id: str,
    efa_wanted: int | None = None,
) -> InterfacePlan:
    """The network interfaces one node is launched with, read off the shape rather than typed.

    **WHY THIS EXISTS AT ALL.** A ``run-instances`` that passes ``--subnet-id`` and no
    ``--network-interfaces`` gets one ordinary ENA interface and *no EFA device whatsoever*.
    Nothing reports that. The instance type still advertises 3,200 Gbps, the AMI still carries
    the EFA installer and the ``aws-ofi-nccl`` plugin, and NCCL still forms its rings -- over TCP
    sockets, several times slower, with the loss curve falling exactly as it should. On a
    non-refundable block bought for one 64-rank job, that is most of the purchase.

    **THE LAYOUT IS AWS'S AND NOT THIS REPOSITORY'S**, and it is the one AWS documents for P5
    under "Maximize network bandwidth ... Use case 1: save IP addresses and avoid potential
    Linux IP issues":

    * network card 0, device 0, ``interface`` -- the primary, and it must be this. An
      ``efa-only`` interface takes no IP address and cannot be an instance's primary interface,
      so a node whose card 0 device 0 is ``efa-only`` has no address, no route and no Systems
      Manager. Nothing in this lane could then reach it.
    * network card 0, device 1, ``efa-only`` -- card 0 carries an EFA as well as the ENA, and
      leaving it out throws away a thirty-second of the fabric for nothing.
    * network cards 1..n, device 0, ``efa-only`` -- one per remaining card.

    The alternative layout AWS documents puts an ENA on eight of the cards for 800 Gbps of IP
    bandwidth, and it says plainly that public IPv4 addresses cannot be auto-assigned with it.
    This lane reaches every node over Systems Manager and moves its data over the fabric, so the
    IP bandwidth buys nothing and the lost address would cost the fleet.

    **EVERY NUMBER BELOW IS READ FROM** ``describe-instance-types``. A literal thirty-two would
    be correct for ``p5.48xlarge`` and silently wrong for the next block bought -- a ``p5en`` has
    the same count and a ``p6-b200`` has eight -- and being wrong in that direction is a launch
    EC2 refuses, at 11:31 on the Saturday, against a window already billing.

    ``efa_wanted`` is the escape hatch and ``0`` is the value that matters: it produces the
    single ordinary interface this launch used before EFA was asked for, which is the
    known-good path to fall back to if the fabric launch is refused for a reason nobody
    predicted. Asking for more than the shape supports raises rather than clamping, because a
    number above the maximum is a typo and clamping would hide it.
    """
    shapes = [shape for shape in described.get("InstanceTypes") or [] if isinstance(shape, Mapping)]
    if len(shapes) != 1:
        raise ValueError(f"describe-instance-types answered with {len(shapes)} shapes, not one")
    network = shapes[0].get("NetworkInfo")
    network = network if isinstance(network, Mapping) else {}

    slots = {
        int(card["NetworkCardIndex"]): int(card.get("MaximumNetworkInterfaces") or 1)
        for card in network.get("NetworkCards") or []
        if isinstance(card, Mapping) and card.get("NetworkCardIndex") is not None
    }
    if 0 not in slots:
        # Refused rather than assumed. The whole point of this function is that the layout comes
        # from the account's own answer, and a shape EC2 described without network cards is one
        # this has no business guessing an interface list for.
        raise ValueError(
            f"EC2 reports no network cards on {shapes[0].get('InstanceType')!r}, so there is "
            "nothing to build an interface list from"
        )

    supported = 0
    if network.get("EfaSupported"):
        efa_info = network.get("EfaInfo")
        supported = int((efa_info if isinstance(efa_info, Mapping) else {}).get(
            "MaximumEfaInterfaces"
        ) or 0)
    if efa_wanted is None:
        efa_wanted = supported
    if efa_wanted < 0 or efa_wanted > supported:
        raise ValueError(
            f"{efa_wanted} EFA interfaces asked for on {shapes[0].get('InstanceType')!r}, which "
            f"supports {supported}"
        )

    placed: list[tuple[int, int, str]] = [(0, 0, ENA_INTERFACE)]
    remaining = efa_wanted
    if remaining and slots[0] >= 2:
        placed.append((0, 1, EFA_ONLY_INTERFACE))
        remaining -= 1
    for index in sorted(slots)[1:]:
        if not remaining:
            break
        placed.append((index, 0, EFA_ONLY_INTERFACE))
        remaining -= 1
    if remaining:
        raise ValueError(
            f"{shapes[0].get('InstanceType')!r} has {len(slots)} network cards, which cannot "
            f"hold {efa_wanted} EFA interfaces in the layout AWS documents"
        )

    # ONLY LEGAL WHEN THIS IS THE WHOLE REQUEST, WHICH IS THE ``efa_wanted=0`` PATH. The
    # ``RunInstances`` contract on ``AssociatePublicIpAddress`` is "you cannot specify more than
    # one network interface in the request", and EFA-only interfaces count towards that. On the
    # fabric layout the address therefore has to come from the subnet's own auto-assign setting
    # -- which is why the resolve step refuses a subnet that does not have it, and why the
    # launch is read back afterwards to see that an address actually arrived.
    address = ("AssociatePublicIpAddress=true",) if len(placed) == 1 else ()

    return InterfacePlan(
        arguments=tuple(
            ",".join(
                (
                    f"NetworkCardIndex={card}",
                    f"DeviceIndex={device}",
                    f"SubnetId={subnet_id}",
                    f"Groups={security_group_id}",
                    f"InterfaceType={kind}",
                    # Thirty-three interfaces per node and eight nodes is 264 of them. Left to
                    # default, the ones this launch created outlive the fleet and the account
                    # carries them until somebody notices.
                    "DeleteOnTermination=true",
                    *(address if device == 0 and card == 0 else ()),
                )
            )
            for card, device, kind in placed
        ),
        efa=efa_wanted,
    )


def admits_its_own_members(described: Mapping[str, Any], *, group_id: str) -> bool:
    """Whether a security group lets EFA traffic between the machines that wear it.

    EFA requires a group that allows all traffic to and from *itself*, and this is the classic
    way the whole change above comes to nothing: the devices attach, ``ibv_devinfo`` lists them,
    the plugin loads, and the fabric never forms because the packets are dropped. There is no
    error. NCCL falls back to sockets exactly as it would with no device at all.

    Ordinary IP ingress rules do not substitute. EFA traffic is not routable and is matched on
    the group rather than on a CIDR, so a rule admitting the subnet's own range -- which looks
    equivalent and is what somebody tightening this would reach for -- admits none of it.
    """
    for group in described.get("SecurityGroups") or []:
        if not isinstance(group, Mapping) or group.get("GroupId") != group_id:
            continue
        return any(
            rule.get("IpProtocol") == "-1"
            and any(
                isinstance(pair, Mapping) and pair.get("GroupId") == group_id
                for pair in rule.get("UserIdGroupPairs") or []
            )
            for rule in group.get("IpPermissions") or []
            if isinstance(rule, Mapping)
        ) and any(
            rule.get("IpProtocol") == "-1"
            and (
                any(
                    isinstance(pair, Mapping) and pair.get("GroupId") == group_id
                    for pair in rule.get("UserIdGroupPairs") or []
                )
                or any(
                    isinstance(span, Mapping) and span.get("CidrIp") == "0.0.0.0/0"
                    for span in rule.get("IpRanges") or []
                )
            )
            for rule in group.get("IpPermissionsEgress") or []
            if isinstance(rule, Mapping)
        )
    return False


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
                    efa_interfaces=sum(
                        1
                        for interface in instance.get("NetworkInterfaces") or []
                        if isinstance(interface, Mapping)
                        and str(interface.get("InterfaceType") or "") in EFA_INTERFACES
                    ),
                    public_ip=(
                        str(instance["PublicIpAddress"])
                        if instance.get("PublicIpAddress")
                        else None
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


def without_the_fabric(fleet: Iterable[FleetNode], *, expected: int) -> tuple[FleetNode, ...]:
    """Every instance that did not come up with the EFA interfaces it was launched with.

    Equality rather than "at least one", for the reason :func:`unreserved` compares against the
    expected id rather than testing for null. One EFA device out of thirty-two is a node with a
    working ``/dev/infiniband/uverbs0``, so every fabric probe downstream -- the one in
    ``infra/block-distributed-launch.sh`` included -- reports it as fabric-attached, and the job
    runs on a thirty-second of the bandwidth the block was bought for while every surface says
    ``efa``. More than expected is flagged for the same reason and not because it is harmful:
    the fleet disagrees with the launch that made it, and that is a fact about the launch path.

    **ONE EXPECTATION FOR THE WHOLE FLEET, WHICH DECIDES WHAT THE CALLER MAY ADVISE.** There is
    no per-node record of what a given machine was launched with -- the count is written once by
    the step that builds the interface list and read once by the step that reads the fleet back
    -- so a fleet whose nodes were launched by two different dispatches cannot be judged here
    node by node. That is not hypothetical: the launch re-run starts only the shortfall, so a
    re-run with ``efa_wanted=0`` after a partial fabric launch leaves some nodes at thirty-two
    and some at zero, and every one of them is reported. The workflow's refusal therefore tells
    the reader to terminate before re-running rather than offering the re-run as an alternative
    to terminating, and that wording is the thing this constraint is holding up.
    """
    return tuple(node for node in fleet if node.efa_interfaces != expected)


def unaddressable(fleet: Iterable[FleetNode]) -> tuple[FleetNode, ...]:
    """Every instance EC2 gave no public address, and therefore no way out of the VPC.

    Nothing reaches these machines except Systems Manager, and the agent gets out over the
    instance's own outbound connection. The resolve step refuses a subnet that assigns no
    address on launch, which is the check that used to be sufficient -- it stopped being
    sufficient when the launch started naming its interfaces explicitly, because that is a
    request shape in which ``AssociatePublicIpAddress`` may not be given and the subnet default
    is the only thing left assigning one.
    """
    return tuple(node for node in fleet if node.public_ip is None)


def fleet_table(fleet: Sequence[FleetNode], *, unready: Collection[str] = ()) -> str:
    """The fleet as the launch workflow prints it.

    The reservation column is not decoration and is not truncated. It is the field the whole
    launch turns on, and a reader who cannot see it has to take the workflow's word for the
    thing the workflow exists to check.

    The EFA column is here for the same reason and is the second such field. How many fabric
    devices a machine came up with is invisible from every other surface -- the console listing,
    the instance type, the driver, the plugin all read identically at zero and at thirty-two --
    and it decides how long every multi-node job in the window takes.
    """
    if not fleet:
        return "no instances carry this block tag"
    header = (
        f"{'node':<6}{'instance':<21}{'private ip':<17}{'state':<11}"
        f"{'reservation':<22}{'efa':<5}ready"
    )
    lines = [header, "-" * len(header)]
    for found in fleet:
        lines.append(
            f"{found.node if found.node is not None else '-':<6}"
            f"{found.instance_id:<21}"
            f"{found.private_ip or '-':<17}"
            f"{found.state:<11}"
            f"{found.capacity_reservation_id or 'NONE':<22}"
            f"{found.efa_interfaces:<5}"
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
