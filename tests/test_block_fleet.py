"""The reading that decides whether eight machines live or die, and the table people scan.

``unreserved`` is the only function in this repository whose wrong answer costs money at a
known rate. A capacity block instance and an on-demand one of the same shape are identical in
every field a console shows except ``CapacityReservationId``, so that comparison is the whole
control -- and the way it goes wrong is not by being absent, it is by being written as a null
check. Half of this module is about that one mutation.

The rest holds the shape of what a person reads at 06:00 on a Saturday, which is a different
kind of correctness and is worth a test for the same reason: the failure of a status table is
that somebody misreads it and takes a machine that is already busy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from edullm_platform.block_fleet import (
    NODE_TAG,
    REMOTE_READING_SCRIPT,
    RESERVATION_TAG,
    FleetNode,
    admits_its_own_members,
    agents_online,
    elapsed_as,
    fleet_table,
    interface_plan,
    parse_reading,
    read_fleet,
    readiness,
    status_rows,
    unreachable,
    unreserved,
    why_nothing_reaches,
    without_the_fabric,
)

BLOCK = "cr-0afc33f3a1af417a7"
OTHER_BLOCK = "cr-00000000000000001"
NOW = datetime(2026, 8, 8, 15, 0, 0, tzinfo=UTC)

SUBNET = "subnet-09cb45b6b1f06d05b"
GROUP = "sg-0988ddf995169aa1f"

#: What ``describe-instance-types`` answers for ``p5.48xlarge`` in us-east-2, in the three fields
#: the interface list is built out of. Read off the account rather than remembered: thirty-two
#: network cards at 100 Gbps each, two interface slots on every card, and thirty-two EFA
#: interfaces supported. A block bought on a different shape answers differently, which is the
#: whole reason none of these numbers is written into the launch.
P5_CARDS = 32
P5_EFA = 32


def shape(
    *,
    cards: int = P5_CARDS,
    efa: int = P5_EFA,
    slots_per_card: int = 2,
    instance_type: str = "p5.48xlarge",
) -> dict[str, Any]:
    return {
        "InstanceTypes": [
            {
                "InstanceType": instance_type,
                "NetworkInfo": {
                    "MaximumNetworkCards": cards,
                    "NetworkCards": [
                        {
                            "NetworkCardIndex": index,
                            "MaximumNetworkInterfaces": slots_per_card,
                        }
                        for index in range(cards)
                    ],
                    "EfaSupported": efa > 0,
                    "EfaInfo": {"MaximumEfaInterfaces": efa},
                },
            }
        ]
    }


def fields(argument: str) -> dict[str, str]:
    """One ``--network-interfaces`` argument back into the key/value pairs EC2 reads it as."""
    return dict(pair.split("=", 1) for pair in argument.split(","))


def group(
    *,
    group_id: str = GROUP,
    self_ingress: bool = True,
    cidr_ingress: bool = False,
    egress: bool = True,
) -> dict[str, Any]:
    ingress: list[dict[str, Any]] = []
    if self_ingress:
        ingress.append({"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": group_id}]})
    if cidr_ingress:
        ingress.append({"IpProtocol": "-1", "IpRanges": [{"CidrIp": "172.31.0.0/16"}]})
    return {
        "SecurityGroups": [
            {
                "GroupId": group_id,
                "GroupName": "default",
                "VpcId": "vpc-0854c9e902a502b2c",
                "IpPermissions": ingress,
                "IpPermissionsEgress": (
                    [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}] if egress else []
                ),
            }
        ]
    }


def instance(
    *,
    instance_id: str,
    node: int | None = 1,
    reservation: str | None = BLOCK,
    state: str = "running",
    private_ip: str | None = "172.31.0.10",
    efa: int = P5_EFA,
) -> dict[str, Any]:
    """One instance as EC2 describes a node of the fabric fleet.

    No ``PublicIpAddress``, and that is what the account answers rather than a convenience. A
    launch naming thirty-three network interfaces is one AWS assigns no public address to
    whatever the subnet says, so the field is simply absent on every node of a correct fleet --
    which is why nothing in this module reads it any more.
    """
    tags = [{"Key": RESERVATION_TAG, "Value": BLOCK}]
    if node is not None:
        tags.append({"Key": NODE_TAG, "Value": str(node)})
    described: dict[str, Any] = {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "Tags": tags,
        "NetworkInterfaces": [
            {"InterfaceType": "interface"},
            *({"InterfaceType": "efa-only"} for _ in range(efa)),
        ],
    }
    if reservation is not None:
        described["CapacityReservationId"] = reservation
    if private_ip is not None:
        described["PrivateIpAddress"] = private_ip
    return described


def described(*instances: dict[str, Any]) -> dict[str, Any]:
    """One EC2 reservation group per instance, which is what one launch per node produces."""
    return {"Reservations": [{"Instances": [found]} for found in instances]}


def agents(*rows: tuple[str, str]) -> dict[str, Any]:
    """A ``describe-instance-information`` answer, as an instance id and a ping status each."""
    return {
        "InstanceInformationList": [
            {"InstanceId": instance_id, "PingStatus": ping} for instance_id, ping in rows
        ]
    }


def test_every_instance_is_read_even_though_ec2_nests_them_one_group_per_launch() -> None:
    """Mutation: read ``Reservations[0].Instances`` and stop.

    EC2 nests instances under reservations -- the ``RunInstances`` kind, which has nothing to
    do with the capacity kind and is the worst overload in this API. The launch makes one call
    per node so that the node number can be tagged at creation, which means one group per node,
    which means a reader that stopped at the first group would verify a fleet of one and report
    it clean.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0003", node=3),
            instance(instance_id="i-0001", node=1),
            instance(instance_id="i-0002", node=2),
        )
    )

    assert [found.node for found in fleet] == [1, 2, 3]
    assert [found.instance_id for found in fleet] == ["i-0001", "i-0002", "i-0003"]


def test_an_instance_with_no_node_tag_is_kept_and_sorted_last() -> None:
    """Mutation: skip an instance whose node tag will not parse.

    An instance carrying the block tag and no node number is what a half-finished launch leaves
    behind, and it is the one instance that must not be dropped: it is still running, still
    drawing from -- or worse, not drawing from -- the reservation, and dropping it hides it from
    the verification below.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0009", node=None),
            instance(instance_id="i-0001", node=1),
        )
    )

    assert [found.node for found in fleet] == [1, None]


def test_an_instance_drawing_from_nothing_is_reported_as_unreserved() -> None:
    """The plain case, and the one that costs roughly $55 an hour while nobody notices."""
    fleet = read_fleet(described(instance(instance_id="i-0001", reservation=None)))

    assert [found.instance_id for found in unreserved(fleet, reservation_id=BLOCK)] == ["i-0001"]


def test_an_instance_drawing_from_a_different_block_is_also_unreserved() -> None:
    """THE MUTATION THIS MODULE EXISTS FOR: ``if not node.capacity_reservation_id``.

    That version reads as correct, passes the test above, and waves through an instance
    consuming capacity somebody bought for a different piece of work. Two live blocks is a
    state this account expects -- ``src/edullm_platform/stack_templates.py`` carries four rows
    for exactly that reason -- so it is reachable rather than theoretical, and the launch would
    report the fleet verified.
    """
    fleet = read_fleet(described(instance(instance_id="i-0001", reservation=OTHER_BLOCK)))

    assert unreserved(fleet, reservation_id=BLOCK)


def test_a_fleet_entirely_on_the_expected_block_has_nothing_unreserved() -> None:
    """The other direction. A check that condemned everything would pass the two above."""
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1),
            instance(instance_id="i-0002", node=2),
        )
    )

    assert unreserved(fleet, reservation_id=BLOCK) == ()


def test_the_fleet_table_prints_the_field_the_launch_turns_on() -> None:
    """Mutation: leave the reservation column out because the workflow already checked it.

    A reader who cannot see the reservation id has to take the workflow's word for the one
    thing the workflow exists to check, which is exactly the property nobody should be taking
    anybody's word for during a paid window.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1),
            instance(instance_id="i-0002", node=2, reservation=None),
        )
    )

    printed = fleet_table(fleet, unready={"i-0002"})

    assert BLOCK in printed
    assert "NONE" in printed
    assert "172.31.0.10" in printed
    assert printed.splitlines()[-1].endswith("no")


def test_a_readiness_answer_is_only_read_from_an_invocation_that_succeeded() -> None:
    """Mutation: read the output of every invocation whatever its status.

    A command that timed out has told us nothing about the node. Its output is empty, an empty
    output carries no sentinel, and reading that as "the bootstrap did not finish" turns a
    statement about the Systems Manager agent into a statement about the machine.
    """
    answered = readiness(
        {
            "CommandInvocations": [
                {"InstanceId": "i-0001", "Status": "TimedOut", "CommandPlugins": []},
                {
                    "InstanceId": "i-0002",
                    "Status": "Success",
                    "CommandPlugins": [{"Output": '{"node":2,"ready_at":"2026-08-08T11:40:00Z"}'}],
                },
                {
                    "InstanceId": "i-0003",
                    "Status": "Success",
                    "CommandPlugins": [{"Output": '{"node":3,"failed_at":"2026-08-08T11:39:00Z"}'}],
                },
                {
                    "InstanceId": "i-0004",
                    "Status": "Success",
                    "CommandPlugins": [{"Output": "still-booting"}],
                },
            ]
        }
    )

    assert [found.ready for found in answered] == [False, True, False, False]
    assert "TimedOut" in answered[0].detail
    assert "bootstrap failed" in answered[2].detail
    assert answered[3].detail == "still booting"


def test_a_node_that_did_not_answer_is_unreachable_rather_than_empty() -> None:
    """Mutation: parse the output first and treat an empty one as a node with nothing running.

    That reading is the single most dangerous thing this tool can print. An empty output parses
    perfectly into zero busy cards and no claim, which renders as ``IDLE`` -- so a machine
    Systems Manager could not reach is advertised as the one to take.
    """
    reading = parse_reading(node=4, instance_id="i-0004", status="TimedOut", output="")

    assert not reading.reachable
    assert reading.detail == "TimedOut"


def test_a_claim_and_a_card_count_are_read_out_of_the_tab_separated_answer() -> None:
    reading = parse_reading(
        node=1,
        instance_id="i-0001",
        status="Success",
        output=(
            "gpus_total\t8\n"
            "gpus_busy\t8\n"
            "run\tshared-experts-a\n"
            "who\teric\n"
            "started_at\t2026-08-08T11:48:00+00:00\n"
            "ready\ttrue\n"
        ),
    )

    assert reading.reachable
    assert (reading.gpus_busy, reading.gpus_total) == (8, 8)
    assert (reading.who, reading.run) == ("eric", "shared-experts-a")
    assert reading.started_at == datetime(2026, 8, 8, 11, 48, tzinfo=UTC)


def test_a_claim_whose_timestamp_will_not_parse_is_still_a_claim() -> None:
    """Mutation: raise on a timestamp that is not ISO-8601.

    The claim file is written by ``printf`` on the node rather than by a serializer, so a
    malformed timestamp is reachable. Losing the whole claim over it would report a busy node
    as free, which is a far worse answer than reporting a busy node with no duration.
    """
    reading = parse_reading(
        node=1,
        instance_id="i-0001",
        status="Success",
        output="gpus_total\t8\ngpus_busy\t8\nrun\tcurriculum-b\nwho\tgrant\nstarted_at\tyesterday\n",
    )

    assert reading.run == "curriculum-b"
    assert reading.started_at is None


def test_a_clock_ahead_of_the_reader_reports_zero_rather_than_a_negative_duration() -> None:
    assert elapsed_as(NOW + timedelta(minutes=5), now=NOW) == "0h00m"


# ---------------------------------------------------------------------------------------
# A CLAIM WHOSE CONTAINER IS GONE, WHICH USED TO BE INDISTINGUISHABLE FROM A BUSY MACHINE.
# ---------------------------------------------------------------------------------------
#
# Nothing on a node removes a claim when a container exits. A run that finished hours ago leaves
# the machine reading as held by a name and a person, with no cards in use, and every reader
# above turned that into ``node_is_busy`` naming a colleague who had gone home. It was measured
# on this fleet: ``node_is_busy`` printed beside ``0/8 cards in use``. The cure -- ``edullm-node
# release`` -- is a verb somebody has to already know about, in a shell fifteen of the
# thirty-five people here hold no role to open, so what the refusal really said was "ask
# somebody". The only lever a researcher had was ``take_the_node_anyway``, whose own description
# promises a fight for memory with a run that does not exist.
#
# **ZERO CARDS IN USE IS NOT THE EVIDENCE AND MUST NEVER BE TREATED AS IT.** A run that is
# cloning, importing torch, sharding a corpus or simply between steps holds no cards while being
# entirely alive, and the claim is written *before* the clone precisely so that two dispatches
# seconds apart cannot both proceed. A reader that called an idle-carded claim stale would hand
# the machine to a second job during the first one's start-up, which is the collision the claim
# exists to prevent, reintroduced by the code meant to police it. The container is the evidence.


def test_a_claim_whose_container_has_gone_is_reported_as_stale() -> None:
    """Mutation: leave the reading with no opinion about the container.

    This is the state a finished run leaves on every node, and the whole point of asking is that
    the answer differs from the one a live run gives while the two look identical in every other
    field.
    """
    reading = parse_reading(
        node=1,
        instance_id="i-0001",
        status="Success",
        output=(
            "gpus_total\t8\ngpus_busy\t0\nrun\tmfu-smoke\nwho\tana\n"
            "started_at\t2026-08-08T11:48:00+00:00\ncontainer\tgone\nready\ttrue\n"
        ),
    )

    assert reading.container == "gone"
    assert reading.claim_is_stale


def test_a_claim_taken_seconds_ago_whose_container_is_up_is_not_stale() -> None:
    """Mutation: decide staleness from ``gpus_busy`` instead of from the container.

    Zero cards and a live container is what the first minute of every run looks like: the claim
    is taken before the clone, the clone is tens of seconds, and torch has not been imported
    yet. Calling this stale is how a second dispatch lands on a machine that is already starting.
    """
    reading = parse_reading(
        node=1,
        instance_id="i-0001",
        status="Success",
        output=(
            "gpus_total\t8\ngpus_busy\t0\nrun\tmfu-smoke\nwho\tana\n"
            "container\trunning\nready\ttrue\n"
        ),
    )

    assert not reading.claim_is_stale


def test_a_dead_container_beside_busy_cards_is_not_offered_up_as_stale() -> None:
    """Mutation: drop the card count from the staleness test.

    A claimed container that has exited while eight cards are still busy is not an abandoned
    machine -- it is a machine with something on it this lane did not start. Telling anybody to
    clear the claim there is telling them to take a node out from under whatever that is.
    """
    reading = parse_reading(
        node=1,
        instance_id="i-0001",
        status="Success",
        output=(
            "gpus_total\t8\ngpus_busy\t8\nrun\tmfu-smoke\nwho\tana\n"
            "container\tgone\nready\ttrue\n"
        ),
    )

    assert not reading.claim_is_stale


def test_a_node_reading_from_a_fleet_that_predates_the_container_line_is_not_stale() -> None:
    """Mutation: default an absent container to ``gone``.

    The reading script gained that line after fleets had already booted, and a running node
    keeps the bootstrap it launched with. An absent fact has to mean "not known" or every node
    of an older fleet reads as abandoned to a laptop updated today -- which would advise
    releasing eight live claims at once.
    """
    reading = parse_reading(
        node=1,
        instance_id="i-0001",
        status="Success",
        output="gpus_total\t8\ngpus_busy\t0\nrun\tmfu-smoke\nwho\tana\nready\ttrue\n",
    )

    assert reading.container is None
    assert not reading.claim_is_stale


def test_the_status_table_names_a_stale_claim_and_the_verb_that_clears_it() -> None:
    """Mutation: report a stale claim as an ordinary busy node.

    ``block-status.yml`` is the first thing the procedure tells a researcher to dispatch, so it
    is where the difference has to be legible. A row that says a name and a person and nothing
    else sends them to a colleague; a row that says the container exited and names the remedy
    sends them to the cure.

    **THE REMEDY NAMED IS THE WORKFLOW AND NOT THE NODE COMMAND, WHICH IS THE HALF THIS ROW GOT
    WRONG FOR AS LONG AS IT HAD ONE.** This table is printed into a job summary by
    ``block-status.yml`` precisely so that the roughly fifteen people here with no AWS role can
    read it -- and it told them to run ``edullm-node release`` on a machine they cannot open.
    A cure the reader cannot obtain is worse than none, because the lever they *can* reach from
    there is ``take_the_node_anyway``.
    """
    rows = status_rows(
        (
            parse_reading(
                node=1,
                instance_id="i-0001",
                status="Success",
                output=(
                    "gpus_total\t8\ngpus_busy\t0\nrun\tmfu-smoke\nwho\tana\n"
                    "started_at\t2026-08-08T13:00:00+00:00\ncontainer\tgone\nready\ttrue\n"
                ),
            ),
        ),
        now=NOW,
    )

    assert "STALE CLAIM" in rows[0]
    assert "mfu-smoke" in rows[0]
    assert "block-release" in rows[0]
    assert "edullm-node release" not in rows[0]
    assert "2h00m" in rows[0]


def test_the_reading_script_asks_docker_about_the_claimed_name() -> None:
    """Mutation: ask ``nvidia-smi`` twice and call the second answer a container.

    The script is a string sent to a machine, so nothing type-checks it. What is asserted here
    is that it asks the one question the claim cannot answer about itself, filtered to the name
    the claim carries rather than to any container at all -- a node running two containers, one
    of them somebody's forced second run, must not report the claim as alive because *a*
    container exists.
    """
    assert "docker ps" in REMOTE_READING_SCRIPT
    assert 'name=^edullm-${held}$' in REMOTE_READING_SCRIPT
    assert "printf 'container\\trunning\\n'" in REMOTE_READING_SCRIPT
    assert "printf 'container\\tgone\\n'" in REMOTE_READING_SCRIPT


def test_a_held_node_reports_hours_and_minutes_and_never_days() -> None:
    """Seventy-two hours is the longest window this lane serves, and ``2d3h`` makes a reader
    do arithmetic to find out whether their run is about to be cut off by the end of it."""
    assert elapsed_as(NOW - timedelta(hours=27, minutes=6), now=NOW) == "27h06m"


def test_the_status_rows_are_the_shape_the_fleet_is_discussed_in() -> None:
    readings = (
        parse_reading(
            node=1,
            instance_id="i-0abc0000000000001",
            status="Success",
            output=(
                "gpus_total\t8\ngpus_busy\t8\nrun\tshared-experts-a\nwho\teric\n"
                "started_at\t2026-08-08T11:48:00+00:00\nready\ttrue\n"
            ),
        ),
        parse_reading(
            node=2,
            instance_id="i-0def0000000000002",
            status="Success",
            output="gpus_total\t8\ngpus_busy\t0\nready\ttrue\n",
        ),
    )

    rows = status_rows(readings, now=NOW)

    assert rows[0].startswith("node 1  ")
    assert "8/8 GPUs busy" in rows[0]
    assert "eric" in rows[0]
    assert "shared-experts-a" in rows[0]
    assert rows[0].endswith("running 3h12m")
    assert rows[1] == f"{'node 2':<8}{'i-0def0000000000002':<21}IDLE"


def test_a_node_with_busy_cards_and_no_claim_is_not_reported_as_idle() -> None:
    """Mutation: decide idleness on the claim file alone.

    Cards in use with nothing claiming them is a run somebody started from a shell, or one
    whose claim was released while the container was still training. Both are somebody using
    the machine. Calling it idle is how two runs end up fighting over eight cards, which on a
    block costs both of them.
    """
    rows = status_rows(
        (
            parse_reading(
                node=5,
                instance_id="i-0005",
                status="Success",
                output="gpus_total\t8\ngpus_busy\t8\n",
            ),
        ),
        now=NOW,
    )

    assert "IDLE" not in rows[0]
    assert "8/8 GPUs busy" in rows[0]


def test_a_node_whose_bootstrap_never_finished_is_not_reported_as_idle() -> None:
    """Mutation: decide idleness on the claim and the cards, which is the natural reading.

    A node whose agent came up and whose bootstrap then died -- no driver, no scratch
    filesystem, no pre-pulled image -- answers this probe exactly as a free machine does. It
    then renders as the most attractive node in the fleet while being the one machine that can
    run nothing, and the person who takes it finds out twenty minutes later.
    """
    rows = status_rows(
        (
            parse_reading(
                node=6,
                instance_id="i-0006",
                status="Success",
                output="gpus_total\t0\ngpus_busy\t0\n",
            ),
        ),
        now=NOW,
    )

    assert "IDLE" not in rows[0]
    assert "NOT READY" in rows[0]


def test_an_unreachable_node_says_so_on_its_own_line() -> None:
    rows = status_rows(
        (parse_reading(node=7, instance_id="i-0007", status="Undeliverable", output=""),),
        now=NOW,
    )

    assert "UNREACHABLE" in rows[0]
    assert "Undeliverable" in rows[0]


def test_the_remote_script_depends_on_nothing_the_bootstrap_installs() -> None:
    """Mutation: have the fan-out call ``edullm-node status --json`` instead.

    The helper answers the same question and is the right thing for a person in a shell. It is
    installed near the end of the bootstrap, so on the one node most worth asking about -- the
    one whose bootstrap did not finish -- it is not there, and a reader built on it would
    report the most interesting machine in the fleet as unreachable.
    """
    assert "edullm-node" not in REMOTE_READING_SCRIPT
    assert "jq" not in REMOTE_READING_SCRIPT
    assert "nvidia-smi" in REMOTE_READING_SCRIPT
    assert "/var/lib/edullm/claim.json" in REMOTE_READING_SCRIPT


def test_the_table_survives_a_fleet_that_does_not_exist_yet() -> None:
    assert fleet_table([]) == "no instances carry this block tag"
    assert read_fleet({}) == ()
    assert unreserved((), reservation_id=BLOCK) == ()


def test_a_node_read_straight_out_of_the_dataclass_still_formats() -> None:
    """The table takes ``FleetNode`` rather than raw JSON, so it has to hold up on one built
    by hand -- which is what a future caller assembling a fleet from somewhere else will do."""
    printed = fleet_table(
        [
            FleetNode(
                node=1,
                instance_id="i-0001",
                state="pending",
                private_ip=None,
                capacity_reservation_id=BLOCK,
            )
        ]
    )

    assert "pending" in printed
    assert " - " in printed


def test_the_interface_list_is_the_layout_aws_documents_for_this_shape() -> None:
    """THE ARGUMENT THE WHOLE FABRIC DEPENDS ON, LAID OUT POSITION BY POSITION.

    AWS documents exactly one layout for ``p5.48xlarge`` under "Maximize network bandwidth", use
    case 1: an ordinary interface on network card 0 device 0, an ``efa-only`` interface on card 0
    device 1, and an ``efa-only`` interface on device 0 of every remaining card. Thirty-three
    interfaces, thirty-two EFA devices, one private address.

    Every part of that is load-bearing and none of it is guessable. Device index 1 on card 0 is
    where the second slot on that card is; device index 0 on the others is where their first is.
    A list that put both of card 0's interfaces on device 0, or that started the other cards at
    device 1, is refused by EC2 -- on the first ``run-instances``, on the morning of the window.
    """
    plan = interface_plan(shape(), subnet_id=SUBNET, security_group_id=GROUP)
    placed = [fields(argument) for argument in plan.arguments]

    assert len(placed) == P5_CARDS + 1
    assert plan.efa == P5_EFA
    assert (placed[0]["NetworkCardIndex"], placed[0]["DeviceIndex"]) == ("0", "0")
    assert placed[0]["InterfaceType"] == "interface"
    assert (placed[1]["NetworkCardIndex"], placed[1]["DeviceIndex"]) == ("0", "1")
    assert placed[1]["InterfaceType"] == "efa-only"
    assert [
        (entry["NetworkCardIndex"], entry["DeviceIndex"], entry["InterfaceType"])
        for entry in placed[2:]
    ] == [(str(index), "0", "efa-only") for index in range(1, P5_CARDS)]


def test_the_primary_interface_is_the_only_one_that_carries_ip() -> None:
    """Mutation: make the primary ``efa-only`` too, since every other interface is.

    An ``efa-only`` interface takes no IPv4 address and cannot be an instance's primary
    interface. A fleet launched that way has no private address, no route, no Systems Manager
    agent and no way for anything in this lane to reach it -- eight machines that boot, bill for
    the window and answer nothing.
    """
    plan = interface_plan(shape(), subnet_id=SUBNET, security_group_id=GROUP)
    kinds = [fields(argument)["InterfaceType"] for argument in plan.arguments]

    assert kinds[0] == "interface"
    assert set(kinds[1:]) == {"efa-only"}


def test_the_number_of_cards_is_read_from_the_answer_rather_than_assumed() -> None:
    """Mutation: write thirty-two into the launch, which is right for the shape it was written
    against and wrong for the next block bought.

    A ``p6-b200.48xlarge`` has eight network cards. An interface list naming card 8 on it is
    refused by EC2, which is discovered at the one moment nobody wants to be reading an
    ``InvalidParameterValue``.
    """
    plan = interface_plan(
        shape(cards=8, efa=8, instance_type="p6-b200.48xlarge"),
        subnet_id=SUBNET,
        security_group_id=GROUP,
    )
    cards = {fields(argument)["NetworkCardIndex"] for argument in plan.arguments}

    assert plan.efa == 8
    assert cards == {str(index) for index in range(8)}


def test_a_public_address_is_asked_for_only_when_it_is_the_whole_request() -> None:
    """THE CONSTRAINT THAT MAKES THE OBVIOUS VERSION OF THIS CHANGE FAIL.

    ``RunInstances`` rejects ``AssociatePublicIpAddress`` in a request carrying more than one
    network interface, and EFA-only interfaces count towards that. AWS also suppresses the
    subnet's own auto-assign setting on the same requests, so the fabric layout has no route to
    a public address by either means and a correct fleet never has one. Nothing downstream is
    allowed to read anything into that: the launch asks Systems Manager whether it can reach
    each node instead, and the subnet these nodes run in routes to a NAT gateway.

    The single-interface fallback is the one shape where asking is legal, and there it is asked
    for explicitly rather than left to the subnet -- so that the escape hatch gets out of a
    public subnet even when the subnet assigns nothing on launch.
    """
    fabric = interface_plan(shape(), subnet_id=SUBNET, security_group_id=GROUP)
    alone = interface_plan(shape(), subnet_id=SUBNET, security_group_id=GROUP, efa_wanted=0)

    assert not any("AssociatePublicIpAddress" in argument for argument in fabric.arguments)
    assert len(alone.arguments) == 1
    assert fields(alone.arguments[0])["AssociatePublicIpAddress"] == "true"
    assert alone.efa == 0


def test_every_interface_names_the_subnet_the_group_and_its_own_disposal() -> None:
    """``--network-interfaces`` and ``--subnet-id`` are mutually exclusive, so the subnet moves
    onto each entry; a launch naming interfaces cannot take a top-level security group either.
    ``DeleteOnTermination`` is here because thirty-three interfaces on eight machines is 264 of
    them, and the ones a launch leaves behind are the account's problem afterwards.
    """
    plan = interface_plan(shape(), subnet_id=SUBNET, security_group_id=GROUP)

    for argument in plan.arguments:
        entry = fields(argument)
        assert entry["SubnetId"] == SUBNET
        assert entry["Groups"] == GROUP
        assert entry["DeleteOnTermination"] == "true"


def test_asking_for_more_fabric_than_the_shape_has_is_refused_rather_than_clamped() -> None:
    """Clamping would launch a fleet quietly smaller than the one asked for, and the number is
    typed by a person on a dispatch form. A value above the maximum is a typo."""
    with pytest.raises(ValueError, match="supports 32"):
        interface_plan(shape(), subnet_id=SUBNET, security_group_id=GROUP, efa_wanted=64)


def test_a_shape_with_no_fabric_gets_the_one_interface_it_can_have() -> None:
    """Nothing about this lane is p5-only. A shape EC2 reports no EFA support for still needs a
    launchable interface list, and asking for a device it does not have is a refused launch."""
    plan = interface_plan(
        shape(cards=1, efa=0, slots_per_card=1, instance_type="m5.large"),
        subnet_id=SUBNET,
        security_group_id=GROUP,
    )

    assert plan.efa == 0
    assert len(plan.arguments) == 1
    assert fields(plan.arguments[0])["InterfaceType"] == "interface"


def test_a_shape_described_without_network_cards_is_refused_rather_than_guessed() -> None:
    """The point of building this from the API answer is that it is not guessed. An answer with
    nothing in it is the one case where guessing would be most tempting and least defensible."""
    empty = shape()
    empty["InstanceTypes"][0]["NetworkInfo"]["NetworkCards"] = []

    with pytest.raises(ValueError, match="no network cards"):
        interface_plan(empty, subnet_id=SUBNET, security_group_id=GROUP)


def test_a_group_admitting_its_own_members_is_what_efa_needs() -> None:
    assert admits_its_own_members(group(), group_id=GROUP)


def test_a_group_admitting_the_subnet_range_instead_does_not_carry_efa() -> None:
    """THE MUTATION THAT LOOKS LIKE TIGHTENING AND IS SILENT.

    An ingress rule on the subnet's own CIDR admits every ordinary packet between these
    machines, so SSH, the rendezvous and every health check keep working and the group reads as
    correct. EFA traffic is matched on the group rather than on an address range and is not
    routable, so none of it is admitted -- every device attaches, every packet is dropped, and
    NCCL falls back to sockets exactly as it would with no fabric at all.
    """
    assert not admits_its_own_members(
        group(self_ingress=False, cidr_ingress=True), group_id=GROUP
    )


def test_a_group_with_no_egress_does_not_carry_efa_either() -> None:
    """EFA needs all traffic *to and from* the group. A default VPC group ships with both; one
    somebody narrowed to inbound-only is the half of the rule that is easy to miss."""
    assert not admits_its_own_members(group(egress=False), group_id=GROUP)
    assert not admits_its_own_members(group(), group_id="sg-somethingelse")


def test_both_spellings_of_an_efa_interface_are_counted_off_a_described_instance() -> None:
    """``efa`` and ``efa-only`` both put a device on the machine and NCCL cannot tell them
    apart. A reading that counted only the spelling this launch happens to use would report a
    working fabric as absent on any fleet launched another way."""
    fleet = read_fleet(
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0001",
                            "State": {"Name": "running"},
                            "NetworkInterfaces": [
                                {"InterfaceType": "interface"},
                                {"InterfaceType": "efa"},
                                {"InterfaceType": "efa-only"},
                            ],
                        }
                    ]
                }
            ]
        }
    )

    assert fleet[0].efa_interfaces == 2


def test_a_node_that_got_some_of_its_fabric_is_as_wrong_as_one_that_got_none() -> None:
    """THE MUTATION THIS CHECK EXISTS FOR: ``if node.efa_interfaces == 0``.

    One EFA device out of thirty-two is a machine with a working ``/dev/infiniband/uverbs0``, so
    every probe downstream reports it as fabric-attached and every surface says ``efa``. The job
    then runs on a thirty-second of the bandwidth the window was bought for, and the reading
    that would have caught it said the fabric was present.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1, efa=P5_EFA),
            instance(instance_id="i-0002", node=2, efa=1),
            instance(instance_id="i-0003", node=3, efa=0),
        )
    )

    assert [found.instance_id for found in without_the_fabric(fleet, expected=P5_EFA)] == [
        "i-0002",
        "i-0003",
    ]


def test_a_fleet_that_got_every_interface_it_asked_for_is_left_alone() -> None:
    """The other direction. A check that flagged everything would pass the one above."""
    fleet = read_fleet(described(instance(instance_id="i-0001", node=1)))

    assert without_the_fabric(fleet, expected=P5_EFA) == ()


def test_a_fleet_launched_by_two_dispatches_cannot_be_judged_against_one_number() -> None:
    """THE LIMIT THAT DECIDES WHAT THE WORKFLOW MAY TELL SOMEBODY TO DO, HELD SO IT STAYS TRUE.

    Nothing records what a given machine was launched with, so the comparison is fleet-wide
    against one expectation. Re-running the launch with ``efa_interfaces=0`` after a partial
    fabric launch starts only the shortfall, which leaves a fleet at thirty-two and zero at
    once -- and every node in it is reported, the healthy ones included.

    That is the behaviour rather than a defect, and the defect it used to produce was in the
    prose beside it: the refusal offered the re-run as an alternative to terminating the fleet,
    which is the one reading of it that does not work. ``tests/test_block_workflows.py`` holds
    the wording; this holds the fact the wording is about.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1, efa=P5_EFA),
            instance(instance_id="i-0002", node=2, efa=0),
        )
    )

    assert [found.instance_id for found in without_the_fabric(fleet, expected=0)] == ["i-0001"]
    assert [found.instance_id for found in without_the_fabric(fleet, expected=P5_EFA)] == [
        "i-0002"
    ]


def test_a_nat_routed_fleet_with_no_public_address_anywhere_is_reachable() -> None:
    """THE FLEET THE CHECK THIS REPLACED WOULD HAVE REFUSED, AND IT IS THE ONE THAT WORKS.

    Every node here is a fabric node with no ``PublicIpAddress`` at all, because AWS assigns
    none to a launch naming thirty-three network interfaces. The old reading called that
    unaddressable and refused eight machines on a paid window. Systems Manager is hearing from
    all of them, which is the only thing anything in this lane needs of a node: every command
    goes out over the agent, and the agent reached Systems Manager through the NAT gateway its
    subnet routes to.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1),
            instance(instance_id="i-0002", node=2),
        )
    )
    online = agents_online(agents(("i-0001", "Online"), ("i-0002", "Online")))

    assert unreachable(fleet, online=online) == ()


def test_a_fleet_whose_agents_never_registered_is_refused_node_by_node() -> None:
    """The fault the refusal is kept for, and it is worth eight machines an hour to catch.

    A fleet nothing can send a command to answers no readiness probe, runs nothing and drains
    nothing, and every one of those presents as a different fault further down. This is the
    reading that names it once, at the top, while the window still has hours in it.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1),
            instance(instance_id="i-0002", node=2),
        )
    )
    online = agents_online({"InstanceInformationList": []})

    assert [found.instance_id for found in unreachable(fleet, online=online)] == [
        "i-0001",
        "i-0002",
    ]


def test_an_agent_that_registered_and_then_went_quiet_is_not_online() -> None:
    """Mutation: take presence in the answer as the signal and never read ``PingStatus``.

    Systems Manager keeps a machine in ``describe-instance-information`` after it stops
    answering and marks it ``ConnectionLost``; a stopped one goes ``Inactive``. Both are nodes
    no command reaches, and both are listed -- so a reading built on presence reports the fleet
    as driveable at exactly the moment it stopped being.
    """
    online = agents_online(
        agents(
            ("i-0001", "Online"),
            ("i-0002", "ConnectionLost"),
            ("i-0003", "Inactive"),
        )
    )

    assert online == frozenset({"i-0001"})


def test_a_stray_machine_answering_does_not_stand_in_for_a_silent_node() -> None:
    """Mutation: compare how many agents answered against how big the fleet is.

    The agent list is tag-filtered, and a tag survives whatever launched it: an instance left
    over from an earlier fleet on the same reservation is in that answer and is not in this
    fleet. Counting would then let it cover for a node of this one that is silent, which is the
    same failure the running-state waiter above avoids by naming instance ids rather than a
    filter. Membership is checked node by node instead.
    """
    fleet = read_fleet(
        described(
            instance(instance_id="i-0001", node=1),
            instance(instance_id="i-0002", node=2),
        )
    )
    online = agents_online(agents(("i-0001", "Online"), ("i-00ff", "Online")))

    assert [found.instance_id for found in unreachable(fleet, online=online)] == ["i-0002"]


def test_the_two_reasons_a_node_is_silent_are_told_apart() -> None:
    """They send a reader to different places, so the refusal must not merge them.

    An instance still in ``pending`` has nothing on it yet to register and there is no fault to
    go and find. One that is ``running`` and has still never registered has a fault, and it is
    almost always no route out of the subnet or an instance profile missing
    ``AmazonSSMManagedInstanceCore`` -- neither of which is anything to do with a public
    address, which is where the message this replaced sent people.
    """
    booting = FleetNode(
        node=1,
        instance_id="i-0001",
        state="pending",
        private_ip=None,
        capacity_reservation_id=BLOCK,
    )
    silent = FleetNode(
        node=2,
        instance_id="i-0002",
        state="running",
        private_ip="172.31.0.11",
        capacity_reservation_id=BLOCK,
    )

    assert why_nothing_reaches(booting) == (
        "is pending rather than running, so nothing is up on it to answer yet"
    )
    assert why_nothing_reaches(silent) == (
        "is running and has never registered with Systems Manager"
    )


def test_the_fleet_table_shows_how_much_fabric_each_machine_came_up_with() -> None:
    """The second column in this table that no other surface carries. An instance with no EFA
    and one with thirty-two are identical in the console, in the instance type and in every
    field the reservation check reads."""
    fleet = read_fleet(described(instance(instance_id="i-0001", node=1, efa=P5_EFA)))

    printed = fleet_table(fleet)

    assert "efa" in printed.splitlines()[0]
    assert str(P5_EFA) in printed.splitlines()[-1]
