"""The three decisions a job spanning several nodes makes, and the quiet way each goes wrong.

Every module in this lane has one function whose wrong answer is expensive. In
``block_fleet`` it is ``unreserved`` and the cost is money at a known rate. Here there are
three, and what they have in common is that none of them fails loudly.

**A PARTIAL CLAIM LOOKS LIKE A BUSY FLEET.** ``choose_nodes`` decides the whole set before
anything is written, so the state where five machines are locked for a run that never started
is unreachable. The mutation is the natural implementation: take what you can get.

**A MESH THAT SPANS MACHINES LOOKS LIKE A MESH.** An expert-parallel degree wider than a node
is accepted by OLMo-core, trains correctly, and puts every MoE all-to-all on the network
between machines rather than on the NVLink inside one. Nothing downstream can tell it from the
layout somebody meant.

**A LAUNCHER CARRYING BOTH RANK FORMS LOOKS LIKE IT ASSIGNS RANKS.** ``--node-rank`` is
ignored once ``--rdzv-backend`` is set, so a command with both reads as careful and is not.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from edullm_platform.block_fleet import FleetNode, NodeReading, parse_reading
from edullm_platform.block_multinode import (
    MESH_FLAGS,
    ROUTED_EXPERTS,
    Candidate,
    ExpertMesh,
    cards_per_node,
    choose_nodes,
    command_refusals,
    expert_parallel_choices,
    launch_markdown,
    mesh_for,
    mesh_refusals,
    node_local_expert_parallel,
    outcomes,
    plan_launch,
    refused,
    rendezvous_for,
    torchrun_command,
    with_mesh_flags,
)

RUN = "final-model-a"
TRAINING = "python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4"


def node(number: int) -> FleetNode:
    return FleetNode(
        node=number,
        instance_id=f"i-{number:017d}",
        state="running",
        private_ip=f"172.31.0.{number}",
        capacity_reservation_id="cr-0afc33f3a1af417a7",
    )


def reading(
    number: int,
    *,
    ready: bool = True,
    busy: int = 0,
    total: int = 8,
    run: str | None = None,
    who: str | None = None,
    status: str = "Success",
) -> NodeReading:
    lines = [f"gpus_total\t{total}", f"gpus_busy\t{busy}"]
    if run is not None:
        lines.append(f"run\t{run}")
    if who is not None:
        lines.append(f"who\t{who}")
    if ready:
        lines.append("ready\ttrue")
    return parse_reading(
        node=number,
        instance_id=f"i-{number:017d}",
        status=status,
        output="\n".join(lines) + "\n",
    )


def fleet_of(*numbers: int) -> tuple[FleetNode, ...]:
    return tuple(node(number) for number in numbers)


def candidates(*numbers: int) -> tuple[Candidate, ...]:
    return tuple(
        Candidate(node=number, instance_id=f"i-{number:017d}", private_ip=f"172.31.0.{number}")
        for number in numbers
    )


# ---------------------------------------------------------------------------------------
# The mesh.
# ---------------------------------------------------------------------------------------


def test_the_default_degree_keeps_the_all_to_all_inside_one_machine() -> None:
    """THE RECOMMENDATION THIS MODULE ENCODES, AND IT DISAGREES WITH OLMo-core's DEFAULT.

    ``--moe-shard-degree`` defaults to 32 on the branch, which across eight nodes of eight
    cards is two replicas of a shard group spanning four machines -- so every MoE all-to-all
    crosses the network between machines, twice per layer per step. Thirty-two experts fit
    across one node's eight cards, and at a degree of eight the same exchange is NVLink.
    """
    assert node_local_expert_parallel(gpus_per_node=8) == 8
    assert mesh_for(nodes=8, gpus_per_node=8).expert_parallel == 8
    assert not mesh_for(nodes=8, gpus_per_node=8).all_to_all_crosses_the_fabric


def test_the_validated_layout_is_the_one_that_crosses_the_fabric() -> None:
    """The 2x32 layout the branch validates is expressible and is reported for what it is.

    It is not refused. It is a legal mesh, its arithmetic is correct, and somebody who wants
    it can ask for it -- what must not happen is arriving at it without being told that the
    all-to-all now leaves the machine.
    """
    wide = mesh_for(nodes=8, gpus_per_node=8, expert_parallel=32)

    assert mesh_refusals(wide) == ()
    assert (wide.replicas, wide.experts_per_rank) == (2, 1)
    assert wide.all_to_all_crosses_the_fabric


def test_seven_nodes_are_expressible_and_the_arithmetic_says_how() -> None:
    """THE QUESTION THE DOWNSTREAM LANE FORCES, AND THE ANSWER IS THAT IT ALREADY WORKS.

    Holding node 8 back for post-training leaves 56 ranks. 56 is not a multiple of 32, so the
    validated layout cannot describe it and the obvious reading is that seven nodes needs a
    code change. It does not: the degree has to divide the expert count and 8 does, seven
    replicas of eight ranks multiply out to 56, and OLMo-core's validator accepts that pair
    exactly as it accepts the other. The node-local default lands on it without being asked.
    """
    seven = mesh_for(nodes=7, gpus_per_node=8)

    assert (seven.world_size, seven.expert_parallel, seven.replicas) == (56, 8, 7)
    assert mesh_refusals(seven) == ()
    assert not seven.all_to_all_crosses_the_fabric


def test_seven_nodes_at_the_validated_degree_is_the_refusal_it_should_be() -> None:
    """The other half: 56 ranks with a degree of 32 has no replica count at all, and OLMo-core
    says so at the point the config is built. Saying it here costs nothing and says it before
    seven machines have been claimed and seven trees cloned."""
    refusals = mesh_refusals(mesh_for(nodes=7, gpus_per_node=8, expert_parallel=32))

    assert any("does_not_divide_the_world" in refusal for refusal in refusals)


def test_a_degree_that_does_not_divide_the_experts_is_refused_with_the_ones_that_do() -> None:
    """Mutation: refuse and say only that it is wrong.

    The useful half of this refusal is the list. Somebody who typed 24 is choosing between
    numbers, and the answer they need is which numbers there are.
    """
    refusals = mesh_refusals(mesh_for(nodes=6, gpus_per_node=8, expert_parallel=24))

    assert any("does_not_divide_the_experts" in refusal for refusal in refusals)
    assert expert_parallel_choices(gpus_per_node=8) == (1, 2, 4, 8, 16, 32)


def test_a_shape_whose_card_count_does_not_divide_the_experts_still_stays_inside_a_node() -> None:
    """Mutation: default the degree to the card count.

    Six cards does not divide thirty-two, so a degree of six is a mesh OLMo-core refuses. The
    point of the default is staying inside the machine, so it falls back to the largest divisor
    that does -- four -- rather than to something that spans machines or to something illegal.
    """
    assert node_local_expert_parallel(gpus_per_node=6) == 4


def test_the_replica_count_is_derived_and_never_asked_for() -> None:
    """OLMo-core refuses unless the two multiply out to the world size, so there is one answer
    and asking a person for it is asking them to reproduce a division under time pressure."""
    assert mesh_for(nodes=8, gpus_per_node=8, expert_parallel=8).replicas == 8
    assert mesh_for(nodes=4, gpus_per_node=8, expert_parallel=16).replicas == 2
    assert ExpertMesh(nodes=8, gpus_per_node=8, expert_parallel=7).replicas == 0


def test_the_routed_expert_count_is_an_argument_rather_than_a_law() -> None:
    """A different MoE is a different number and must not be a different code path."""
    assert node_local_expert_parallel(gpus_per_node=8, routed_experts=64) == 8
    assert expert_parallel_choices(gpus_per_node=8, routed_experts=16) == (1, 2, 4, 8, 16)
    assert ROUTED_EXPERTS == 32


# ---------------------------------------------------------------------------------------
# The mesh flags on the command.
# ---------------------------------------------------------------------------------------


def test_a_command_naming_no_mesh_is_given_the_one_it_is_going_to_run_on() -> None:
    command, refusals = with_mesh_flags(TRAINING, mesh=mesh_for(nodes=8, gpus_per_node=8))

    assert refusals == ()
    assert command.endswith(f"{MESH_FLAGS[0]} 8 {MESH_FLAGS[1]} 8")


def test_a_command_whose_mesh_disagrees_with_the_fleet_is_refused() -> None:
    """THE MUTATION THIS FUNCTION EXISTS FOR: trust what the researcher typed.

    ``--moe-shard-degree 32`` on eight nodes is accepted by OLMo-core, forms, trains and is
    several times slower than the same run at eight. There is no error, no warning and no
    field anywhere that reads differently -- it is the mesh disagreement that costs a window
    rather than a dispatch.
    """
    _, refusals = with_mesh_flags(
        f"{TRAINING} --moe-shard-degree 32 --moe-num-replicas 2",
        mesh=mesh_for(nodes=8, gpus_per_node=8),
    )

    assert len(refusals) == 2
    assert any("--moe-shard-degree=32" in refusal for refusal in refusals)
    assert any("--moe-num-replicas=2" in refusal for refusal in refusals)


def test_a_command_naming_the_mesh_it_is_actually_getting_is_left_alone() -> None:
    """The other direction. A check that refused every command naming the flags would pass the
    case above and would make the flags unusable by hand."""
    command = f"{TRAINING} --moe-shard-degree=8 --moe-num-replicas=8"

    assert with_mesh_flags(command, mesh=mesh_for(nodes=8, gpus_per_node=8)) == (command, ())


def test_half_a_mesh_is_refused_rather_than_completed() -> None:
    """Mutation: fill in whichever flag is missing.

    The two describe one mesh and OLMo-core multiplies them out, so completing a half-written
    pair means choosing a world size on somebody's behalf out of a sentence where they said
    something different.
    """
    _, refusals = with_mesh_flags(
        f"{TRAINING} --moe-shard-degree 8", mesh=mesh_for(nodes=8, gpus_per_node=8)
    )

    assert len(refusals) == 1
    assert "--moe-num-replicas" in refusals[0]


def test_a_command_that_already_names_a_launcher_is_refused() -> None:
    """Mutation: wrap whatever arrives.

    A command that already says ``torchrun`` gets wrapped in a second one, which starts eight
    agents per node each of which starts eight workers that each start eight more. The first
    evidence is the machine running out of memory in a way that names nothing.
    """
    for wrapped in (
        "torchrun --nproc-per-node=8 train.py",
        "python -m torch.distributed.run train.py",
    ):
        assert command_refusals(wrapped)
    assert command_refusals(TRAINING) == ()
    assert command_refusals("   ") == ("the command resolved to nothing",)


# ---------------------------------------------------------------------------------------
# The claim, which is the part that has to be all or nothing.
# ---------------------------------------------------------------------------------------


def test_a_count_takes_the_lowest_numbered_free_nodes() -> None:
    choice = choose_nodes(
        fleet=fleet_of(1, 2, 3, 4),
        readings=[reading(1), reading(2), reading(3), reading(4)],
        node_count=3,
        run=RUN,
    )

    assert [candidate.node for candidate in choice.chosen] == [1, 2, 3]
    assert choice.usable


def test_a_fleet_one_node_short_takes_nothing_at_all() -> None:
    """THE MUTATION THIS MODULE EXISTS FOR: return what is available and let the caller decide.

    A caller handed six candidates for an eight-node job either claims them and finds out, or
    reimplements this refusal. What "claims them and finds out" produces is six machines locked
    for a job that cannot form, which reads to everybody else in the window as a fully occupied
    fleet -- and the run that was supposed to use all eight is now behind six claims of its own.
    """
    choice = choose_nodes(
        fleet=fleet_of(1, 2, 3),
        readings=[reading(1), reading(2, run="somebody-else", who="eric"), reading(3)],
        node_count=3,
        run=RUN,
    )

    assert not choice.usable
    assert "2 nodes this run could take and it asked for 3" in choice.refusals[0]


def test_every_blocker_is_named_and_not_only_the_first() -> None:
    """Mutation: return on the first reason.

    Somebody told node 3 is busy fixes node 3, dispatches again, and is told about node 6. Both
    were knowable in the first pass, and each round trip is minutes of a window that does not
    repeat.
    """
    choice = choose_nodes(
        fleet=fleet_of(1, 2, 3, 4),
        readings=[
            reading(1),
            reading(2, run="curriculum-b", who="grant"),
            reading(3, ready=False),
            reading(4, status="TimedOut"),
        ],
        node_count=4,
        run=RUN,
    )

    assert any("grant" in refusal for refusal in choice.refusals)
    assert any("readiness sentinel" in refusal for refusal in choice.refusals)
    assert any("did not answer" in refusal for refusal in choice.refusals)


def test_a_named_node_that_is_blocked_is_refused_and_never_substituted() -> None:
    """THE PROPERTY THE DOWNSTREAM LANE DEPENDS ON. Somebody who asked for nodes 1 through 7
    because node 8 is being held back for post-training does not want node 8 quietly used
    because node 4 turned out to be busy."""
    choice = choose_nodes(
        fleet=fleet_of(1, 2, 3, 4, 5, 6, 7, 8),
        readings=[reading(number, run="held" if number == 4 else None) for number in range(1, 9)],
        requested=(1, 2, 3, 4, 5, 6, 7),
        run=RUN,
    )

    assert not choice.usable
    assert choice.chosen == ()
    assert all("node 8" not in refusal for refusal in choice.refusals)


def test_naming_nodes_and_giving_a_count_is_refused_rather_than_reconciled() -> None:
    """Honouring both means ignoring one, and the ignored one is whichever the caller meant."""
    both = choose_nodes(
        fleet=fleet_of(1, 2),
        readings=[reading(1), reading(2)],
        requested=(1,),
        node_count=2,
        run=RUN,
    )
    neither = choose_nodes(fleet=fleet_of(1), readings=[reading(1)], run=RUN)

    assert not both.usable
    assert not neither.usable


def test_a_node_whose_bootstrap_never_finished_is_not_a_candidate() -> None:
    """It answers the busy question perfectly -- no claim, no cards in use -- and is the one
    machine in the fleet that can run nothing, because it has no scratch filesystem and no
    pre-pulled image. A chooser built on the claim alone picks it first."""
    choice = choose_nodes(
        fleet=fleet_of(1, 2),
        readings=[reading(1, ready=False), reading(2)],
        node_count=1,
        run=RUN,
    )

    assert [candidate.node for candidate in choice.chosen] == [2]


def test_a_node_with_no_private_address_is_a_blocker_rather_than_a_none() -> None:
    """Carrying the ``None`` any further pushes the check into the string that builds the
    rendezvous endpoint, where it becomes ``None:29400`` and seven nodes dial it."""
    choice = choose_nodes(
        fleet=(replace(node(1), private_ip=None), node(2)),
        readings=[reading(1), reading(2)],
        requested=(1,),
        run=RUN,
    )

    assert any("no private address" in refusal for refusal in choice.refusals)


def test_re_dispatching_the_same_run_is_not_blocked_by_its_own_claim() -> None:
    """A claim naming this run is this run's claim. Refusing it would make a re-dispatch after
    a failed start impossible without going round every machine by hand."""
    choice = choose_nodes(
        fleet=fleet_of(1, 2),
        readings=[reading(1, run=RUN, who="me"), reading(2, run=RUN, who="me")],
        node_count=2,
        run=RUN,
    )

    assert choice.usable


def test_forcing_takes_a_held_node_and_still_refuses_one_that_cannot_run() -> None:
    """``--force`` overrides who is on a machine. It does not override whether the machine
    works, because that is not a question about a person."""
    held = choose_nodes(
        fleet=fleet_of(1),
        readings=[reading(1, run="somebody", who="eric", busy=8)],
        node_count=1,
        run=RUN,
        force=True,
    )
    broken = choose_nodes(
        fleet=fleet_of(1), readings=[reading(1, ready=False)], node_count=1, run=RUN, force=True
    )

    assert held.usable
    assert not broken.usable


def test_a_fleet_that_disagrees_about_its_card_count_is_refused() -> None:
    """torchrun supports one local world size for the whole job, so a node whose driver
    enumerated seven devices does not make a smaller job -- it makes sixty-three ranks waiting
    on a sixty-fourth that nothing is going to start."""
    cards, refusals = cards_per_node(candidates(1, 2), [reading(1), reading(2, total=7)])

    assert cards == 0
    assert refusals and "do not all report the same" in refusals[0]


# ---------------------------------------------------------------------------------------
# The rendezvous and the launcher.
# ---------------------------------------------------------------------------------------


def test_the_rendezvous_host_is_the_lowest_numbered_node_every_time() -> None:
    """Mutation: take the first candidate the API happened to return.

    The election has to give a re-dispatch the same answer it gave the first attempt. A rule
    that moved the store between attempts would leave whatever was still up dialling the
    previous endpoint.
    """
    forwards = rendezvous_for(candidates(3, 5, 7), run=RUN)
    backwards = rendezvous_for(candidates(7, 5, 3), run=RUN)

    assert forwards.host.node == backwards.host.node == 3
    assert forwards.endpoint == "172.31.0.3:29400"
    assert forwards.run_id == RUN


def test_the_launcher_is_the_rendezvous_form_and_carries_no_rank() -> None:
    """THE MUTATION THAT PRODUCES A JOB THAT LOOKS CONFIGURED AND IS NOT.

    ``--node-rank`` belongs to torchrun's static form and is ignored the moment
    ``--rdzv-backend`` is set. A command carrying both reads in review as careful about ranks
    and hands rank assignment to the rendezvous anyway, so the ordering nobody chose is the one
    that happens -- and because the two forms disagree only about ordering, it trains.
    """
    line = torchrun_command(
        mesh=mesh_for(nodes=8, gpus_per_node=8),
        rendezvous=rendezvous_for(candidates(1, 2), run=RUN),
        command=TRAINING,
    )

    assert "--node-rank" not in line
    assert "--node_rank" not in line
    assert "--master-addr" not in line
    assert "--nnodes=8" in line
    assert "--nproc-per-node=8" in line
    assert "--rdzv-backend=c10d" in line
    assert "--rdzv-endpoint=172.31.0.1:29400" in line
    assert line.endswith(TRAINING)


def test_the_launcher_gives_up_rather_than_re_forming_the_group() -> None:
    """Mutation: leave ``--max-restarts`` at whatever torchrun defaults to.

    Above zero, a rank that dies is followed by the group being re-formed with a different rank
    assignment, silently, mid-run. A checkpoint written either side of that is a checkpoint
    written by a different layout, and this lane's whole output is checkpoints.
    """
    line = torchrun_command(
        mesh=mesh_for(nodes=2, gpus_per_node=8),
        rendezvous=rendezvous_for(candidates(1, 2), run=RUN),
        command=TRAINING,
    )

    assert "--max-restarts=0" in line


def test_a_rendezvous_needs_a_node() -> None:
    with pytest.raises(ValueError, match="at least one node"):
        rendezvous_for((), run=RUN)


# ---------------------------------------------------------------------------------------
# The plan, which is every refusal above in one pass.
# ---------------------------------------------------------------------------------------


def test_the_whole_plan_is_decided_before_anything_is_claimed() -> None:
    plan = plan_launch(
        fleet=fleet_of(1, 2, 3, 4, 5, 6, 7, 8),
        readings=[reading(number) for number in range(1, 9)],
        run=RUN,
        command=TRAINING,
        node_count=8,
    )

    assert plan.usable
    assert plan.mesh.world_size == 64
    assert plan.mesh.expert_parallel == 8
    assert plan.rendezvous is not None
    assert plan.rendezvous.host.node == 1
    assert "--moe-shard-degree 8 --moe-num-replicas 8" in plan.launch_command
    assert plan.launch_command.startswith("torchrun --nnodes=8 --nproc-per-node=8")


def test_a_refused_plan_carries_no_rendezvous_and_no_command() -> None:
    """Mutation: build the command anyway and let the caller check the refusals.

    A plan that is not usable and still hands back a runnable line is one ``if`` away from
    being sent, and the ``if`` is in a different file.
    """
    plan = plan_launch(
        fleet=fleet_of(1, 2),
        readings=[reading(1), reading(2, run="held", who="eric")],
        run=RUN,
        command=TRAINING,
        node_count=2,
    )

    assert not plan.usable
    assert plan.rendezvous is None
    assert plan.launch_command == ""


def test_the_seven_node_plan_the_downstream_lane_produces_is_usable_as_written() -> None:
    """Node 8 held back for post-training, named rather than counted, and nothing else said."""
    plan = plan_launch(
        fleet=fleet_of(1, 2, 3, 4, 5, 6, 7, 8),
        readings=[reading(number) for number in range(1, 9)],
        run=RUN,
        command=TRAINING,
        requested=(1, 2, 3, 4, 5, 6, 7),
    )

    assert plan.usable
    assert plan.mesh.world_size == 56
    assert "--moe-shard-degree 8 --moe-num-replicas 7" in plan.launch_command


def test_a_plan_that_would_not_build_a_mesh_never_reaches_the_mesh_flags() -> None:
    """Mutation: check the command against a mesh derived from zero nodes.

    With nothing chosen the world size is zero, every degree divides it, and the flags appended
    would say a replica count of zero -- a refusal about the wrong thing, printed above the
    real one.
    """
    plan = plan_launch(
        fleet=(),
        readings=(),
        run=RUN,
        command=TRAINING,
        node_count=4,
    )

    assert not plan.usable
    assert all("moe-num-replicas" not in refusal for refusal in plan.refusals)


def test_the_mesh_flags_can_be_turned_off_for_a_command_that_is_not_that_recipe() -> None:
    plan = plan_launch(
        fleet=fleet_of(1, 2),
        readings=[reading(1), reading(2)],
        run=RUN,
        command="python train.py",
        node_count=2,
        mesh_flags=False,
    )

    assert plan.usable
    assert "--moe-shard-degree" not in plan.launch_command


# ---------------------------------------------------------------------------------------
# Reading the answers back.
# ---------------------------------------------------------------------------------------


def test_a_node_that_produced_no_invocation_is_not_a_node_that_succeeded() -> None:
    """THE PROPERTY THE ALL-OR-NOTHING CLAIM RESTS ON, ONE LAYER DOWN. Systems Manager returns
    invocations for the machines it reached. A reader that iterated the answers rather than the
    machines would find every answer successful and roll nothing back."""
    found = outcomes(
        candidates(1, 2),
        {"i-00000000000000001": {"Status": "Success", "StandardOutputContent": "fabric\tefa\n"}},
    )

    assert [outcome.ok for outcome in found] == [True, False]
    assert refused(found)[0].status == "no invocation"


def test_an_invocation_that_failed_carries_what_the_node_said_about_it() -> None:
    invocation: dict[str, Any] = {
        "Status": "Failed",
        "StandardOutputContent": "",
        "StandardErrorContent": "node 2 has no EFA device\n",
    }
    found = outcomes(candidates(2), {"i-00000000000000002": invocation})

    assert not found[0].ok
    assert "no EFA device" in found[0].error


def test_the_report_says_when_the_job_is_not_on_the_fabric() -> None:
    """THE ONE PROPERTY OF THIS RUN THAT IS INVISIBLE AFTERWARDS AND DECIDES HOW LONG IT TAKES.

    A p5 carries no EFA unless the launch asked for ``InterfaceType=efa``, so a fleet brought up
    without it has the driver, the plugin and no device -- and NCCL falls back to the ordinary
    interface with nothing anywhere reporting a problem.
    """
    page = launch_markdown(
        run=RUN,
        mesh=mesh_for(nodes=2, gpus_per_node=8),
        rendezvous=rendezvous_for(candidates(1, 2), run=RUN),
        chosen=candidates(1, 2),
        fabric={1: "tcp", 2: "tcp"},
        command="torchrun ...",
        bucket="edullm-block-outputs-us-east-2",
        reservation_id="cr-0afc33f3a1af417a7",
        region="us-east-2",
        entity="eduLLM",
        project="capacity-block",
    )

    assert "NOT USING THE FABRIC" in page
    assert "16 ranks" in page
    assert "NVLink inside each machine" in page
    assert "cr-0afc33f3a1af417a7" in page


def test_the_report_is_quiet_about_the_fabric_when_every_node_has_one() -> None:
    """A warning that fires when nothing is wrong is a warning people learn to scroll past."""
    page = launch_markdown(
        run=RUN,
        mesh=mesh_for(nodes=2, gpus_per_node=8, expert_parallel=16),
        rendezvous=rendezvous_for(candidates(1, 2), run=RUN),
        chosen=candidates(1, 2),
        fabric={1: "efa", 2: "efa"},
        command="torchrun ...",
        bucket="edullm-block-outputs-us-east-2",
        reservation_id="cr-0afc33f3a1af417a7",
        region="us-east-2",
        entity="eduLLM",
        project="capacity-block",
    )

    assert "NOT USING THE FABRIC" not in page
    assert "the fabric between machines" in page
