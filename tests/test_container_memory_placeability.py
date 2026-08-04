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

A HOST REGISTERS 31/32 OF ITS ADVERTISED MEMORY AND NEVER MORE. That ratio was measured
rather than reasoned: each shape was bisected by submitting one job per candidate value
into a queue of its own and reading whether the refusal appeared. p4d.24xlarge was
placeable at 1142784 and not 1144832, p5.48xlarge at 2031616 and not 2035712, and
p5.4xlarge answered 253952 against an advertised 262144. All three are 31/32 exactly,
which is what makes this one ratio rather than three constants.

The advertised column below is EC2's published figure for the instance type and is the one
fact here that this repository cannot derive from itself. It is duplicated from the table
in ``src/edullm_platform/execution.py``, deliberately: a test that read its expectation out
of the module it is testing would agree with any value that module carried, which is the
whole of how both failures above survived review.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.execution import CONTAINER_SHAPES

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The fraction of its advertised memory a host registers with ECS, measured per the
#: module docstring. A job is placed against this and never against the figure EC2 prints.
REGISTERED_FRACTION = (31, 32)

#: What ECS reserves for the agent and the host, as the ratio infra/batch-compute.yaml
#: settled on. Counted per vCPU, which is why whether it fits under the ceiling depends on
#: an instance's memory per vCPU rather than on its size.
AGENT_ALLOWANCE_MIB_PER_4_VCPU = 1024

#: The memory-per-vCPU at which the agent's allowance and the 1/32 the host withholds are
#: the same number. Below it the subtraction clears the ceiling; above it the subtraction
#: lands over the ceiling and the container can never be placed. The P family is the whole
#: of what sits above, which is why the P family is the whole of what has ever broken.
CROSSOVER_MIB_PER_VCPU = 8 * 1024

#: EC2's advertised memory per instance type, in MiB. Not derived from anything in this
#: repository -- see the module docstring for why that matters.
ADVERTISED_MEMORY_MIB = {
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
    numerator, denominator = REGISTERED_FRACTION
    return ADVERTISED_MEMORY_MIB[instance_type] * numerator // denominator


@pytest.mark.parametrize("profile", gpu_profiles())
def test_a_gpu_container_asks_for_no_more_than_its_host_registers(profile: str) -> None:
    """THE ONE THAT MATTERS. Mutation: take the allowance off the advertised figure.

    That mutation is not hypothetical -- it is the state this repository shipped, twice,
    and it is what an uncommitted revert of the correction would restore. It reads as an
    obvious simplification, because the advertised figure is the number written on the
    instance, and it is wrong for every shape above 8 GiB per vCPU.

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
def test_every_gpu_container_is_its_instance_less_the_agents_allowance(profile: str) -> None:
    """Mutation: pick a memory figure that is merely under the ceiling.

    The test above is the safety property and this is the intent, and they are different
    claims. Passing the first only says a container can be placed; a shape could satisfy it
    by asking for half the machine and would waste the other half on every run, which
    nothing else here would notice.

    One rule covers both families rather than two, and the branch is the crossover rather
    than the letter of the instance name. Below 8 GiB per vCPU the allowance is taken off
    the advertised figure; at or above it, off the ceiling. Writing it as ``p`` versus ``g``
    would be a rule about names that happens to hold, and the next P-shaped G instance
    would break it.
    """
    shape = CONTAINER_SHAPES[profile]
    instance_type = instance_types()[profile]
    advertised = ADVERTISED_MEMORY_MIB[instance_type]
    allowance = AGENT_ALLOWANCE_MIB_PER_4_VCPU * (shape.vcpus // 4)

    per_vcpu = advertised // shape.vcpus
    base = advertised if per_vcpu <= CROSSOVER_MIB_PER_VCPU else registered_ceiling_mib(
        instance_type
    )

    assert shape.memory_mib == base - allowance


@pytest.mark.parametrize(
    ("profile", "shipped_value"),
    [
        ("gpu-8xa100", 1155072),
        ("gpu-1xh100", 258048),
        ("gpu-8xh100", 2048000),
    ],
)
def test_the_three_values_the_p_family_shipped_are_refused_by_this_rule(
    profile: str, shipped_value: int
) -> None:
    """The regression, named by its numbers rather than described.

    A test that only checks today's values passes the moment somebody restores yesterday's,
    provided they restore them consistently -- which is precisely what happened, because
    the table and the templates were reverted together and the seam test compares them to
    each other. These three integers are what a revert puts back, so this fails on the
    revert itself rather than on some consequence of it.

    ``gpu-8xa100`` is the one that cost something: a 140 GB training run was submitted to a
    queue that could never place it, and a run that never starts writes no checkpoint to
    resume from.
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


def test_the_shapes_sitting_exactly_on_their_ceiling_are_the_three_this_expects() -> None:
    """The rows to look at first if this ever comes back.

    Three shapes have zero headroom, and that is the arithmetic landing on its boundary
    rather than a mistake: all three g6e types are 8 GiB per vCPU on the nose, so the
    allowance and the 1/32 are the same number and the two subtractions coincide. They are
    placeable with nothing left over, which is where the P family sat before it was
    measured.

    Held to exactly three so that a reservation growing by a single MiB, or a fourth
    instance type arriving at the crossover, is reported here rather than discovered as a
    queue that stopped scaling.
    """
    on_the_boundary = {
        profile
        for profile in gpu_profiles()
        if CONTAINER_SHAPES[profile].memory_mib
        == registered_ceiling_mib(instance_types()[profile])
    }

    assert on_the_boundary == {"gpu-1xl40s", "gpu-4xl40s", "gpu-8xl40s"}


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
