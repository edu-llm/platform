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

from edullm_platform.block_fleet import (
    NODE_TAG,
    REMOTE_READING_SCRIPT,
    RESERVATION_TAG,
    FleetNode,
    elapsed_as,
    fleet_table,
    parse_reading,
    read_fleet,
    readiness,
    status_rows,
    unreserved,
)

BLOCK = "cr-0afc33f3a1af417a7"
OTHER_BLOCK = "cr-00000000000000001"
NOW = datetime(2026, 8, 8, 15, 0, 0, tzinfo=UTC)


def instance(
    *,
    instance_id: str,
    node: int | None = 1,
    reservation: str | None = BLOCK,
    state: str = "running",
    private_ip: str | None = "172.31.0.10",
) -> dict[str, Any]:
    tags = [{"Key": RESERVATION_TAG, "Value": BLOCK}]
    if node is not None:
        tags.append({"Key": NODE_TAG, "Value": str(node)})
    described: dict[str, Any] = {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "Tags": tags,
    }
    if reservation is not None:
        described["CapacityReservationId"] = reservation
    if private_ip is not None:
        described["PrivateIpAddress"] = private_ip
    return described


def described(*instances: dict[str, Any]) -> dict[str, Any]:
    """One EC2 reservation group per instance, which is what one launch per node produces."""
    return {"Reservations": [{"Instances": [found]} for found in instances]}


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
