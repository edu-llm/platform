"""Which zone a lane machine starts in, and what is said when none of them will have it.

**THE DEFECT THIS FILE EXISTS FOR RAN ON A LIVE ACCOUNT AT 09:52 UTC ON 2026-08-06.**
``describe-subnets`` was asked for ``Subnets[].SubnetId``, a bare list, and the launch took
``[0]`` of it. EC2 returns this account's six subnets in an order of its own that puts
``us-east-1f`` first, so the lane pinned one zone by accident; ``g6.xlarge`` was short there and
three attempts in a row were refused by the same zone, because there was no way for a second
attempt to be anywhere else. Nothing was billed -- an ``InsufficientInstanceCapacity`` allocates
nothing -- so the cost was one researcher's morning rather than money.

What made it worth fixing before the next one is the default. ``gpu-1xl4`` is ``g6.xlarge`` and
it is the shape ``default_compute_profile`` picks when nobody passes ``--compute``, which is
every first ``edullm run``. One zone running short is thirty-five first commands failing at
once, with the same message, on the verb the day-one guide says to type first.

**AND THE ZONES ARE NOT INTERCHANGEABLE, WHICH IS THE HALF A NAIVE WIDENING GETS WRONG.**
``infra/batch-network.yaml`` declares six subnets and the sixth is ``us-east-1e``, which exists
for ``p5`` and for nothing else -- its Name tag says ``-p5-only`` and EC2 offers no G-family
shape there. ``tests/test_phase3_infrastructure.py`` already holds Batch to that rule, where
breaking it is a job that waits in ``RUNNABLE`` for ever. Here it is cheaper and still worth
avoiding: one refused call, and a zone in the "tried" list that could never have answered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.lane import (
    LaneSubnet,
    ZoneAttempt,
    another_zone_may_answer,
    find_subnets_argv,
    lane_subnets,
    no_zone_had_this_shape,
    refusal_code,
    subnets_to_try,
    zones_offering,
    zones_offering_argv,
)
from edullm_platform.cli.main import EXIT_OK, EXIT_UNREACHABLE
from tests.cli_support import (
    LANE_SUBNETS,
    LANE_ZONE_FOR_P5_ONLY,
    LANE_ZONES,
    LANE_ZONES_OFFERING,
    FakeRunner,
    failed,
    git_answers,
    invoke,
    lane_answers,
)

#: The shape the lane defaults to and the one that could not be started on 2026-08-06. Named
#: here rather than passed as ``--compute`` by every case below, because the interaction with
#: the default is half of what is being tested.
THE_DEFAULT_SHAPE = "gpu-1xl4"
THE_DEFAULT_INSTANCE_TYPE = "g6.xlarge"


def a_laptop(tmp_path: Path, **overrides: object) -> FakeRunner:
    return FakeRunner({**git_answers(tmp_path), **lane_answers(**overrides)})


def zones_asked(runner: FakeRunner) -> list[str]:
    """Which zone each launch was aimed at, in the order they were attempted."""
    zone_of = {subnet: zone for zone, subnet in LANE_SUBNETS.items()}
    return [
        zone_of[argv[argv.index("--subnet-id") + 1]]
        for argv in runner.ran("aws", "ec2", "run-instances")
    ]


def run_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> tuple[int, str]:
    code, out, err = invoke(
        ["run", "--project", "mixlaw", "--", "python", "-V"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    return code, out + err


def flat(said: str) -> str:
    """One line, so an assertion is not also an assertion about where the wrap fell.

    ``_wrapped`` breaks these paragraphs at 78 columns, and which words land either side of a
    break moves whenever a sentence is reworded. A test matching a phrase across one is a test
    that goes red for a reason it is not about.
    """
    return " ".join(said.split())


#: How many times a case that turns on the shuffle repeats itself. Five candidate zones, so a
#: verb that asked only its first choice would have to draw the same one eight times running to
#: survive -- one chance in 390,625, which is a flake nobody will ever see.
DRAWS = 8


# ---------------------------------------------------------------------------------------
# the widening itself
# ---------------------------------------------------------------------------------------


def test_a_zone_with_none_of_the_shape_is_not_the_end_of_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE DEFECT, AS AN OUTCOME.**
    Mutation: take ``candidates[0]`` and stop.

    One zone holds a machine and the other four do not, which is the shape of the morning of
    2026-08-06 with one zone's luck reversed. The verb has to reach the zone that has one,
    whichever place the shuffle deals it. This is the whole claim of the change and every other
    case here is a bound on it.

    Repeated, because one invocation would pass a verb that stops after its first choice
    whenever the shuffle happened to open on the zone with the machine.
    """
    lucky = "us-east-1d"
    attempts = 0
    for _ in range(DRAWS):
        runner = a_laptop(tmp_path, capacity_in=[lucky])

        code, said = run_in(tmp_path, monkeypatch, runner)

        assert code == EXIT_OK, said
        assert zones_asked(runner)[-1] == lucky
        attempts += len(zones_asked(runner))

    assert attempts > DRAWS, "no run ever asked a second zone, which is the pin still in place"


def test_the_zone_a_shape_is_not_sold_in_is_never_asked_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: iterate every subnet ``describe-subnets`` returned.

    ``us-east-1e`` is declared for ``p5`` and EC2 offers no G shape there, so asking it for a
    ``g6.xlarge`` is a refusal that could never have gone the other way. Measured against this
    account on 2026-08-06: the answer is ``Unsupported`` in 1.27 seconds with nothing started,
    so the cost is a wasted call rather than a wasted machine -- but a zone in the "every zone
    refused" list that was never able to say yes makes that sentence a worse report than it
    needs to be.
    """
    runner = a_laptop(tmp_path, capacity_in=[])

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_UNREACHABLE, said
    assert LANE_ZONE_FOR_P5_ONLY not in zones_asked(runner)
    assert sorted(zones_asked(runner)) == sorted(LANE_ZONES_OFFERING)


def test_every_zone_is_a_candidate_when_nothing_could_say_which_ones_offer_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: treat an empty offerings answer as an empty candidate list.

    ``describe-instance-type-offerings`` narrows a list; it does not authorize one. A call
    that was throttled, denied or simply answered nothing has said nothing about whether a
    machine can be started, and a lane that refused on it would have turned a hint into a
    gate -- refusing to launch anywhere because a *describe* did not answer. Falling through
    costs at most the one refusal the filter would have saved, which is the same trade
    ``default_compute_profile`` makes with its own two filters.
    """
    runner = a_laptop(tmp_path, offerings=[], capacity_in=[])

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_UNREACHABLE, said
    assert sorted(zones_asked(runner)) == sorted(LANE_ZONES)


def test_the_zone_tried_first_is_not_the_one_ec2_happens_to_list_first() -> None:
    """**THE HALF THAT IS ABOUT THIRTY-FIVE PEOPLE RATHER THAN ABOUT ONE.**
    Mutation: return the candidates in the order they arrived.

    A deterministic first choice fixes the outage and keeps the concentration that produced
    it. EC2 lists ``us-east-1f`` first for this account, so everybody who typed the same
    command in the same hour asked the same pool for the same shape, and a fixed second
    choice is merely the next pool all of them pile into together. Nothing prefers a zone --
    ``infra/batch-network.yaml`` gives all six one route table, one gateway and one security
    group, and the scratch bucket is regional -- so the order is free to spread the demand.

    Drawn rather than asserted once, because the property is about the distribution. Five
    candidates and forty draws puts the chance of a false failure at five in ``5**40``.
    """
    declared = tuple(LaneSubnet(subnet=LANE_SUBNETS[zone], zone=zone) for zone in LANE_ZONES)
    offered = frozenset(LANE_ZONES_OFFERING)

    first = {subnets_to_try(declared, offered_in=offered)[0].zone for _ in range(40)}

    assert len(first) > 1, "every draw opened on the same zone, which is EC2's order kept"
    assert first <= set(LANE_ZONES_OFFERING)


def test_one_machine_is_started_and_the_loop_stops_the_moment_it_has_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep asking after a launch answered with an instance id.

    Every zone after the first success is a second machine billing under the same project tag
    and the same expiry, which the reuse path would then find at random. A retry loop that
    does not stop is worse than the pin it replaced.
    """
    runner = a_laptop(tmp_path)

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_OK, said
    assert len(runner.ran("aws", "ec2", "run-instances")) == 1


# ---------------------------------------------------------------------------------------
# which refusals may be asked again somewhere else
# ---------------------------------------------------------------------------------------


def test_a_refusal_that_is_not_about_the_zone_stops_on_the_first_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE EXPENSIVE HALF TO GET WRONG.**
    Mutation: retry every launch failure.

    An authorization denial and a vCPU quota are the same in every zone, so retrying either is
    five identical failures, five times the wait, and then a closing sentence about capacity
    that is false. ``config/capacity.yaml`` records the quota case under ``gpu-8xa10g``: 871
    ``VcpuLimitExceeded`` refusals against the account's own G-bucket ceiling, which looks
    exactly like scarcity from underneath and needs a support ticket rather than another zone.
    """
    denied = (
        "An error occurred (UnauthorizedOperation) when calling the RunInstances operation: "
        "You are not authorized to perform this operation."
    )
    runner = FakeRunner(
        {
            **git_answers(tmp_path),
            **lane_answers(),
            ("aws", "ec2", "run-instances"): failed(denied, returncode=254),
        }
    )

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_UNREACHABLE, said
    assert len(runner.ran("aws", "ec2", "run-instances")) == 1
    assert "UnauthorizedOperation" in said


def test_a_refusal_carrying_no_code_at_all_is_never_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ONE THAT COULD LEAVE TWO MACHINES BILLING.**
    Mutation: default an unrecognised refusal to "try the next zone".

    A launch whose outcome could not be read is not a launch that allocated nothing. A socket
    that closed, a timeout, an ``aws`` that could not be run -- any of those may have reached
    ``RunInstances`` and lost the answer, and a second attempt on top is how one command buys
    two machines under one expiry tag. So "unrecognised" has to mean stop and quote whatever
    was said, which is exactly what the verb did before this loop existed.
    """
    runner = FakeRunner(
        {
            **git_answers(tmp_path),
            **lane_answers(),
            ("aws", "ec2", "run-instances"): failed("Connection was closed", returncode=255),
        }
    )

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_UNREACHABLE, said
    assert len(runner.ran("aws", "ec2", "run-instances")) == 1


def test_the_two_zone_shaped_codes_are_read_off_what_the_aws_cli_actually_prints() -> None:
    """Mutation: match on the prose rather than on the code in the parentheses.

    Both strings below were copied off this account on 2026-08-06. The prose after the colon is
    AWS's and gets reworded; the code in the parentheses is the contract, which is the same
    rule ``AGENTS.md`` puts on this repository's own ``--json``.
    """
    no_capacity = (
        "An error occurred (InsufficientInstanceCapacity) when calling the RunInstances "
        "operation (reached max retries: 2): We currently do not have sufficient p5.4xlarge "
        "capacity in the Availability Zone you requested (us-east-1a)."
    )
    wrong_zone = (
        "An error occurred (Unsupported) when calling the RunInstances operation: Your "
        "requested instance type (g6.xlarge) is not supported in your requested Availability "
        "Zone (us-east-1e)."
    )

    assert another_zone_may_answer(no_capacity)
    assert another_zone_may_answer(wrong_zone)
    assert not another_zone_may_answer("An error occurred (VcpuLimitExceeded) when calling")
    assert not another_zone_may_answer("something with no code in it")


def test_a_code_with_a_dot_in_it_is_read_as_the_code_it_is() -> None:
    """**A LATENT WRONG ANSWER THAT ``edullm stop`` IS THE FIRST READER TO CARE ABOUT.**
    Mutation: keep the character class alphanumeric.

    A whole family of EC2 codes carries a dot -- ``InvalidInstanceID.NotFound``,
    ``InvalidInstanceID.Malformed``, ``InvalidGroup.NotFound`` -- and the alphanumeric class
    matched none of them, so :func:`refusal_code` answered ``None`` and
    :attr:`ZoneAttempt.code` printed ``no error code`` at somebody who had one on the screen
    in front of them.

    It went unnoticed because the one reader was :func:`another_zone_may_answer`, where an
    unreadable code stops the launch loop and stopping is the safe direction. ``edullm stop``
    reads a code to recognise one particular outcome -- an instance EC2 no longer has, which
    is the machine the janitor reached first -- and there ``None`` is the wrong answer.

    The zone loop is asserted alongside, because widening a pattern that decides whether to
    make a second ``RunInstances`` is the kind of change that has to be shown not to.
    """
    vanished = (
        "An error occurred (InvalidInstanceID.NotFound) when calling the TerminateInstances "
        "operation: The instance ID 'i-0000000000000aaaa' does not exist"
    )

    assert refusal_code(vanished) == "InvalidInstanceID.NotFound"
    assert not another_zone_may_answer(vanished), (
        "no code carrying a dot is one a second zone could answer differently, so widening "
        "the pattern must not have bought the launch loop another attempt"
    )


# ---------------------------------------------------------------------------------------
# what is said when none of them will have it
# ---------------------------------------------------------------------------------------


def test_the_refusal_names_every_zone_it_tried_rather_than_where_to_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**WHAT REPLACED A MESSAGE THAT WAS ALREADY BETTER THAN MOST.**
    Mutation: keep quoting EC2's own sentence and nothing else.

    EC2 names the zone and lists alternatives, which is more than most refusals here manage.
    But that list is not a capacity reading -- it is the other zones the type is sold in,
    printed identically whether they are full or empty. Measured on 2026-08-06: a
    ``p5.4xlarge`` refused in 1a, 1b and 1c, and each of the three recommended the other two.
    A reader taking it at its word works through zones this loop has already been refused by.
    """
    runner = a_laptop(tmp_path, capacity_in=[])

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_UNREACHABLE, said
    for zone in LANE_ZONES_OFFERING:
        assert zone in flat(said), f"{zone} was tried and is not named"
    assert LANE_ZONE_FOR_P5_ONLY not in flat(said), (
        "a zone nothing tried is not a zone that refused"
    )


def test_the_refusal_says_this_is_capacity_and_not_the_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report it as a launch failure and leave the cause to be inferred.

    Everything else in the lane that exits 3 is a thing somebody can go and fix: log in,
    deploy the network, install the plugin. This one is weather, and a person whose first
    command fails will assume it is them and go looking for the flag they got wrong. Saying so
    is the difference between re-reading the guide and running the same command in ten
    minutes, which is what actually works.
    """
    runner = a_laptop(tmp_path, capacity_in=[])

    code, said = run_in(tmp_path, monkeypatch, runner)

    assert code == EXIT_UNREACHABLE, said
    assert "nothing started and nothing is billing" in flat(said)
    assert "None of this is something you did or something about your account" in flat(said)
    assert "Running the same command again in a few minutes" in flat(said)


def test_a_shape_nobody_chose_is_said_to_have_been_chosen_for_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE INTERACTION WITH THE DEFAULT, WHICH IS WHAT MAKES THIS ONE WORSE THAN A TYPED
    SHAPE.**
    Mutation: print the same sentence whether or not ``--compute`` was given.

    "Pass --compute for a different shape" is advice a person cannot act on until somebody
    tells them they never passed it. A researcher who typed the shape can reason about it and
    already knows the flag exists; one who did not sees a name they have never seen refused
    for a reason they cannot check.
    """
    defaulted = a_laptop(tmp_path, capacity_in=[])
    _, said_defaulted = run_in(tmp_path, monkeypatch, defaulted)

    typed = a_laptop(tmp_path, capacity_in=[])
    code, _, said_typed = invoke(
        ["run", "--project", "mixlaw", "--compute", THE_DEFAULT_SHAPE, "--", "python", "-V"],
        runner=typed,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    # Both arms have to have reached the refusal, or the absence below is vacuous.
    assert code == EXIT_UNREACHABLE, said_typed
    assert "no zone had a" in flat(said_typed)
    assert "chosen for you rather than by you" in flat(said_defaulted)
    assert THE_DEFAULT_SHAPE in flat(said_defaulted)
    assert "chosen for you" not in flat(said_typed)


def test_the_refusal_still_carries_one_of_the_messages_ec2_actually_sent() -> None:
    """Mutation: summarise the five refusals and quote none of them.

    Five near-identical AWS paragraphs is a wall nobody reads; none at all leaves somebody with
    an error code they cannot search for and no way to tell this refusal from a shape this
    account has never been sold. One, in full, is the middle that keeps both.
    """
    attempts = tuple(
        ZoneAttempt(
            zone=zone,
            said=(
                "An error occurred (InsufficientInstanceCapacity) when calling the "
                f"RunInstances operation: no {THE_DEFAULT_INSTANCE_TYPE} in {zone}."
            ),
        )
        for zone in LANE_ZONES_OFFERING
    )

    said = no_zone_had_this_shape(
        instance_type=THE_DEFAULT_INSTANCE_TYPE,
        profile=THE_DEFAULT_SHAPE,
        attempts=attempts,
        defaulted=True,
    )

    assert "InsufficientInstanceCapacity" in said
    assert said.count("An error occurred") == 1
    assert str(len(attempts)) in said


# ---------------------------------------------------------------------------------------
# the two calls this rests on, as the argv they are
# ---------------------------------------------------------------------------------------


def test_the_subnet_query_asks_for_the_zone_and_not_only_the_id() -> None:
    """**THE SEAM THE WHOLE DEFECT LIVED IN.**
    Mutation: go back to ``Subnets[].SubnetId``.

    A bare list of ids cannot be filtered by zone and cannot be reported by zone, so both
    halves of this change need the zone in the answer. Asserted against the argv because that
    is the whole of what the account sees, which is the rule ``tests/test_lane_launch.py``
    opens with.
    """
    argv = find_subnets_argv()
    query = argv[argv.index("--query") + 1]

    assert "AvailabilityZone" in query
    assert "SubnetId" in query
    assert "--output" in argv and argv[argv.index("--output") + 1] == "json"


def test_the_offerings_query_asks_per_zone_for_the_one_type_in_hand() -> None:
    """Mutation: drop ``--location-type``, which defaults to region and answers one row.

    Without it the call answers whether the region offers the type, which is true of every
    shape this catalog prices and narrows nothing. The failure would be silent: the filter
    would match no zone name, the candidate list would fall through to all six, and the
    ``us-east-1e`` subnet would be back in it.
    """
    argv = zones_offering_argv("g6.xlarge")

    assert argv[argv.index("--location-type") + 1] == "availability-zone"
    assert argv[argv.index("--filters") + 1] == "Name=instance-type,Values=g6.xlarge"


def test_an_answer_shaped_like_nothing_this_module_asked_for_is_skipped_not_unpacked() -> None:
    """Mutation: index into every entry and let the traceback out.

    The shape is this module's own ``--query`` and the two ship together, so the only way to
    see something else is an install whose halves disagree. Skipping reads as one fewer place
    to try and skipping everything reads as no network, which the verb already reports as a
    deploy that has not happened. A traceback in front of a researcher is the one thing this
    binary promises not to do.
    """
    assert lane_subnets("") == ()
    assert lane_subnets(json.dumps(["subnet-0000000000000000a"])) == ()
    assert lane_subnets(json.dumps([{"subnet": "subnet-0000000000000000a"}])) == ()
    assert lane_subnets(json.dumps([{"subnet": "subnet-0000000000000000a", "zone": "us-east-1a"}])) == (
        LaneSubnet(subnet="subnet-0000000000000000a", zone="us-east-1a"),
    )
    assert zones_offering("") == frozenset()
    assert zones_offering(json.dumps(["us-east-1a", 7])) == frozenset({"us-east-1a"})
