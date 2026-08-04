"""Whether a GPU container can be placed at all, checked against the physical instance.

WHY THIS FILE EXISTS, AND WHAT IT WOULD HAVE CAUGHT. ``tests/test_phase3_execution.py``
already compares ``CONTAINER_SHAPES`` against each deployed job definition field by field,
so the table and the templates cannot drift apart. What nothing checked is whether the
number they agree on is one an instance can actually satisfy -- and the two failures this
repository has had were both exactly that. ``gpu-8xa100`` asked for 1155072 MiB of a host
that registers 1142784, and ``gpu-1xh100`` shipped asking 258048 of a host that registers
253952. Both were wrong in the table and in the template at once, so the seam test passed
on both, every day, while every job submitted to those queues was refused statically.

Batch answers ``MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT`` on the job without launching
anything, so there is no instance to inspect and nothing in any container log. The symptom
is a queue that never scales, which is indistinguishable at a glance from a capacity
shortage -- and the two coincided on p4d, which is most of why it went unread for as long
as it did.

31/32 IS NOT A LAW, AND THIS FILE USED TO SAY IT WAS. That ratio was inferred from probe
jobs against two P shapes, and the account's own ECS registrations contradict it: the
fraction a host registers runs from 0.93622 on g6.xlarge to 0.97380 on g5.48xlarge. Reading
high is the direction that ships an unplaceable container, and the ratio reads high for five
of the nine instance types that have now been measured -- so this file certified two shapes
that could never be placed. ``gpu-1xl4`` asked 15360 of a g6.xlarge that registers 15339, and
``gpu-1xl40s`` asked 31744 of a g6e.xlarge that registers 31611.

g5.xlarge and g6.xlarge are the pair that settles it. Both advertise 16384 MiB, both
registered under AMI ami-011db5ae81cc0f370, and they register 15759 and 15339. No function of
the advertised figure returns two answers for one input, so a ceiling derived from the spec
sheet cannot be right for both, and the real figure has to be read off the host.

WHERE THE REGISTERED COLUMN COMES FROM, AND WHY IT COSTS NOTHING. Every ECS container
instance calls ``RegisterContainerInstance`` as it joins a Batch compute environment, and
that call carries ``totalResources.MEMORY`` -- the exact number placement is decided against.
CloudTrail keeps ninety days of them::

    aws cloudtrail lookup-events \\
      --lookup-attributes AttributeKey=EventName,AttributeValue=RegisterContainerInstance

The ceiling for every instance type this account has ever started is therefore already
recorded, and reading it launches nothing and bills nothing. Both tables below are facts
about the world rather than values derived from the module under test: a test that read its
expectation out of ``execution.py`` would agree with any value that module carried, which is
the whole of how each of these failures survived review.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.execution import CONTAINER_SHAPES

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The fraction of its advertised memory a host is ASSUMED to register when this account has
#: never started one, kept only as the fallback below. It is an estimate and the module
#: docstring records that three measured types come in under it.
ESTIMATED_REGISTERED_FRACTION = (31, 32)

#: What ECS reserves for the agent and the host, as the ratio infra/batch-compute.yaml
#: settled on. Counted per vCPU, so on a four-vCPU host it is a flat 1 GiB -- which is the
#: whole reason the two small G shapes broke: their hosts withhold more than that.
AGENT_ALLOWANCE_MIB_PER_4_VCPU = 1024

#: The smallest fraction of its host's registration any container here claims. Set by
#: gpu-1xl4, where a flat 1 GiB allowance is 6.7% of a 15 GiB host. A shape under this is
#: leaving most of a machine it is billing for unused.
MINIMUM_CLAIMED_FRACTION = 0.93

#: WHAT AN INSTANCE ACTUALLY REGISTERED WITH ECS, in MiB, read from this account's
#: ``RegisterContainerInstance`` calls in CloudTrail on 2026-08-04 -- see the module
#: docstring for the query. This is the number Batch decides placement against, and it is the
#: one fact here that neither this repository nor EC2's spec sheet can supply.
#:
#: Every value is stable across every registration observed for that type (257 calls, nine
#: types, two AMIs), so these are the type's figure rather than one machine's. Add a row the
#: first time an instance of a new type comes up; do not guess one.
REGISTERED_MEMORY_MIB = {
    "c7i.8xlarge": 63226,
    "g4dn.xlarge": 15759,
    "g5.xlarge": 15759,
    "g5.12xlarge": 191142,
    "g5.48xlarge": 765828,
    "g6.xlarge": 15339,
    "g6e.xlarge": 31611,
    "g6e.12xlarge": 381654,
    "p4d.24xlarge": 1148706,
}

#: EC2's advertised memory per instance type, in MiB. Not derived from anything in this
#: repository, and used here only to estimate a ceiling for the types above have not covered.
ADVERTISED_MEMORY_MIB = {
    # The CPU shape's host. No GPU profile runs here and gpu_profiles() excludes cpu-32vcpu,
    # so this row exists for one reason: c7i.8xlarge is a ninth measured registration, and it
    # is one of the five that comes in under what 31/32 promises.
    "c7i.8xlarge": 64 * 1024,
    "g4dn.xlarge": 16 * 1024,
    "g4dn.12xlarge": 192 * 1024,
    "g4dn.metal": 384 * 1024,
    "g5.xlarge": 16 * 1024,
    "g5.12xlarge": 192 * 1024,
    "g5.48xlarge": 768 * 1024,
    "g6.xlarge": 16 * 1024,
    "g6.12xlarge": 192 * 1024,
    "g6.48xlarge": 768 * 1024,
    "g6e.xlarge": 32 * 1024,
    "g6e.12xlarge": 384 * 1024,
    "g6e.48xlarge": 1536 * 1024,
    "p4d.24xlarge": 1152 * 1024,
    "p5.4xlarge": 256 * 1024,
    "p5.48xlarge": 2048 * 1024,
}


def instance_types() -> dict[str, str]:
    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    return {profile.name: profile.instance_type for profile in catalog.compute_profiles}


def gpu_profiles() -> list[str]:
    """Every profile whose container takes a whole GPU instance.

    The CPU shape is excluded because it is the one container here that does not take the
    machine: ``cpu-32vcpu`` asks for 4 vCPU of a c7i.8xlarge, so the ceiling below is not
    the constraint it is placed against and applying this rule to it would assert nothing.
    """
    return sorted(name for name, shape in CONTAINER_SHAPES.items() if shape.gpus)


def registered_ceiling_mib(instance_type: str) -> int:
    """The most memory a container on this instance type can ask for and still be placed.

    The host's own registration where one has been read, and 31/32 of the advertised figure
    where none has. The fallback is flagged rather than hidden because it is what was wrong
    before: it is an estimate, it reads high on five of the nine types since measured, and a
    shape resting on it has not been checked against anything real.
    """
    measured = REGISTERED_MEMORY_MIB.get(instance_type)
    if measured is not None:
        return measured
    numerator, denominator = ESTIMATED_REGISTERED_FRACTION
    return ADVERTISED_MEMORY_MIB[instance_type] * numerator // denominator


@pytest.mark.parametrize("profile", gpu_profiles())
def test_a_gpu_container_asks_for_no_more_than_its_host_registers(profile: str) -> None:
    """THE ONE THAT MATTERS. Mutation: take the allowance off the advertised figure.

    That mutation is not hypothetical -- it is the state this repository shipped four times,
    and it is what an uncommitted revert of any of the corrections would restore. It reads as
    an obvious simplification, because the advertised figure is the number written on the
    instance, and it is wrong wherever the host withholds more than the allowance covers:
    every P shape, and the two small G shapes whose hosts withhold over a gibibyte.

    Parametrised per profile so a failure names the shape rather than the batch, and so a
    shape added later is checked by arriving rather than by somebody remembering.
    """
    shape = CONTAINER_SHAPES[profile]
    instance_type = instance_types()[profile]
    ceiling = registered_ceiling_mib(instance_type)

    assert shape.memory_mib <= ceiling, (
        f"{profile} on {instance_type} asks for {shape.memory_mib} MiB against a host that "
        f"registers {ceiling}. Batch refuses this statically with "
        f"MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT, launching nothing and logging nothing, "
        f"so the only symptom is a queue that never scales."
    )


@pytest.mark.parametrize("profile", gpu_profiles())
def test_a_gpu_container_claims_almost_all_of_what_its_host_registers(profile: str) -> None:
    """Mutation: pick a memory figure that is merely under the ceiling.

    The test above is the safety property and this is the intent, and they are different
    claims. Passing the first only says a container can be placed; a shape could satisfy it
    by asking for half the machine and would waste the other half on every run, which
    nothing else here would notice.

    A FRACTION RATHER THAN AN EQUALITY, BECAUSE THE SUBTRACTION IS NO LONGER ONE RULE. This
    was ``memory == base - allowance`` with a branch on memory per vCPU, and it could be,
    while the ceiling was a formula both sides of the branch shared. It cannot be now: nine
    instance types are held to their own registration and six to an estimate, and the shapes
    resting on the estimate were sized before their hosts were measured -- p4d.24xlarge
    registers 30498 MiB more than gpu-8xa100 asks for. Those are working, deployed job
    definitions and raising them to close a gap is a template release that buys a couple of
    percent, so what is asserted is the floor rather than the equality.
    """
    shape = CONTAINER_SHAPES[profile]
    instance_type = instance_types()[profile]
    ceiling = registered_ceiling_mib(instance_type)

    assert shape.memory_mib >= ceiling * MINIMUM_CLAIMED_FRACTION, (
        f"{profile} asks for {shape.memory_mib} MiB of an {instance_type} that registers "
        f"{ceiling}, leaving {ceiling - shape.memory_mib} unclaimed on a machine the run "
        f"pays for whole."
    )


@pytest.mark.parametrize(
    ("profile", "shipped_value"),
    [
        ("gpu-8xa100", 1155072),
        ("gpu-1xh100", 258048),
        ("gpu-8xh100", 2048000),
        ("gpu-1xl4", 15360),
        ("gpu-1xl40s", 31744),
    ],
)
def test_the_five_values_that_shipped_unplaceable_are_refused_by_this_rule(
    profile: str, shipped_value: int
) -> None:
    """The regression, named by its numbers rather than described.

    A test that only checks today's values passes the moment somebody restores yesterday's,
    provided they restore them consistently -- which is precisely what happened, because
    the table and the templates were reverted together and the seam test compares them to
    each other. These five integers are what a revert puts back, so this fails on the
    revert itself rather than on some consequence of it.

    ``gpu-8xa100`` is the one that cost the most: a 140 GB training run was submitted to a
    queue that could never place it, and a run that never starts writes no checkpoint to
    resume from. ``gpu-1xl4`` is the one that shows the symptom most plainly -- a g6.xlarge
    came up for run_019fcda8 on 2026-08-04, registered 15339 MiB, could not take a container
    asking 15360, and billed until the environment scaled it back down.
    """
    instance_type = instance_types()[profile]

    assert shipped_value > registered_ceiling_mib(instance_type)
    assert CONTAINER_SHAPES[profile].memory_mib != shipped_value


def test_every_gpu_shape_has_an_advertised_figure_to_be_checked_against() -> None:
    """Mutation: add a shape on an instance type this table does not know.

    Without this, a new shape is simply not checked, and the parametrised tests above go on
    passing at their old width -- a green suite that has stopped covering the thing it was
    written for. The failure has to be the absence rather than a skip.
    """
    missing = {
        profile: instance_types()[profile]
        for profile in gpu_profiles()
        if instance_types()[profile] not in ADVERTISED_MEMORY_MIB
    }

    assert not missing, (
        f"no advertised memory is recorded for {missing}, so those shapes are placed "
        f"against nothing. Add EC2's published figure for the instance type."
    )


def test_the_shapes_checked_against_an_estimate_are_the_seven_this_expects() -> None:
    """WHICH SHAPES ARE NOT ACTUALLY CHECKED, held as a set so the set has to shrink.

    A shape whose instance type has never come up here is measured against 31/32 of its
    advertised memory, and 31/32 is the assumption that put two shapes over their ceiling.
    So these seven are certified by nothing. gpu-8xl40s is the one to look at first: it sits
    exactly on the estimate for g6e.48xlarge, so it has no headroom against a number that has
    been wrong by as much as 533 MiB elsewhere.

    Named rather than counted so that promoting a shape onto a new instance type fails here,
    and so that reading a registration out of CloudTrail once the first instance of a type
    comes up is a deletion from this set rather than something nobody thinks to do.
    """
    on_an_estimate = {
        profile
        for profile in gpu_profiles()
        if instance_types()[profile] not in REGISTERED_MEMORY_MIB
    }

    assert on_an_estimate == {
        "gpu-4xt4",  # g4dn.12xlarge
        "gpu-8xt4",  # g4dn.metal
        "gpu-4xl4",  # g6.12xlarge
        "gpu-8xl4",  # g6.48xlarge
        "gpu-8xl40s",  # g6e.48xlarge
        "gpu-1xh100",  # p5.4xlarge
        "gpu-8xh100",  # p5.48xlarge
    }


def test_no_measured_host_registers_as_much_as_the_estimate_promises() -> None:
    """Why the fallback above is a liability rather than a conservative default.

    31/32 was adopted as a floor -- "a host registers 31/32 of its advertised memory and
    never more". Against the nine types since measured it is not a floor: it reads high on
    five of them, by 533 MiB on g6.xlarge, and reading high is what ships a container that
    cannot be placed. This pins the disagreement so that anybody tempted to trust the
    estimate on an eighth instance type sees the size of the error first.
    """
    numerator, denominator = ESTIMATED_REGISTERED_FRACTION
    overstated = {
        instance_type: ADVERTISED_MEMORY_MIB[instance_type] * numerator // denominator
        - registered
        for instance_type, registered in REGISTERED_MEMORY_MIB.items()
        if ADVERTISED_MEMORY_MIB[instance_type] * numerator // denominator > registered
    }

    assert overstated == {
        "c7i.8xlarge": 262,
        "g4dn.xlarge": 113,
        "g5.xlarge": 113,
        "g6.xlarge": 533,
        "g6e.xlarge": 133,
    }


#: The eight shapes whose ``/dev/shm`` is a quarter of the whole instance rather than a
#: quarter of the container. See the test below: this is a record of what is deployed, not
#: an endorsement of it.
SHARED_MEMORY_FROM_THE_INSTANCE = frozenset(
    {
        "gpu-1xt4",
        "gpu-4xt4",
        "gpu-1xa10g",
        "gpu-4xa10g",
        "gpu-8xa10g",
        "gpu-1xl4",
        "gpu-4xl4",
        "gpu-8xl4",
    }
)


@pytest.mark.parametrize("profile", gpu_profiles())
def test_a_containers_shared_memory_fits_inside_the_container(profile: str) -> None:
    """Mutation: size the tmpfs above the container's own memory.

    ``/dev/shm`` is a tmpfs and is drawn from the container's memory, so a share larger
    than the container is a job that exceeds its memory limit partway through a DataLoader,
    hours in, naming neither this number nor the one it was derived from. This is the
    property that holds for every shape, and it is the one worth being unconditional.
    """
    shape = CONTAINER_SHAPES[profile]

    assert shape.shared_memory_mib is not None
    assert 0 < shape.shared_memory_mib <= shape.memory_mib


@pytest.mark.parametrize("profile", gpu_profiles())
def test_the_quarter_a_shape_takes_is_a_quarter_of_one_of_two_things(profile: str) -> None:
    """A DOCUMENTED RULE THAT EIGHT OF FIFTEEN SHAPES DO NOT FOLLOW, pinned rather than
    quietly corrected.

    ``CONTAINER_SHAPES`` says in as many words that shared memory "is a quarter of the
    container's memory". Seven shapes are. The other eight are a quarter of the instance's
    advertised memory, which is larger, because the container had its agent allowance taken
    off and the tmpfs did not. Nothing had noticed, because nothing compared the two.

    What the overshoot costs is small and is not nothing. A tmpfs limit is a ceiling rather
    than a reservation, so it bills nothing until something writes to it -- but it means a
    DataLoader on one of these eight can fill past the quarter the design intended and take
    the container over its memory limit, which is the failure the quarter exists to bound.

    This test asserts the split as it is deployed rather than the rule as it is written,
    for the same reason the ceiling test above duplicates EC2's figures: correcting the
    eight would change eight registered job definitions, which is a Lambda and template
    release rather than an edit. Pinning it makes the disagreement a thing somebody decides
    about instead of a thing nobody can see.
    """
    shape = CONTAINER_SHAPES[profile]
    advertised = ADVERTISED_MEMORY_MIB[instance_types()[profile]]

    if profile in SHARED_MEMORY_FROM_THE_INSTANCE:
        assert shape.shared_memory_mib == advertised // 4
        assert shape.shared_memory_mib > shape.memory_mib // 4
    else:
        assert shape.shared_memory_mib == shape.memory_mib // 4
