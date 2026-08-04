"""config/capacity.yaml against config/workload-catalog.yaml.

Read with ``yaml.safe_load`` rather than through a contract model, deliberately. Placement
belongs on ``ComputeProfile`` beside ``provisioned``, and it is not there because that model's
structural digest is recorded in five committed proof bundles; adding a pydantic model
elsewhere in ``edullm_platform`` would put a second, unversioned schema in the tree for a fact
that has a home waiting for it. So the file is plain configuration and these are the checks a
validator would otherwise do.

The failure they exist to prevent is a promoted shape that nobody records an answer for. The
submission path offers a substitute when a requested shape does not place, and a shape missing
from this file has no answer to offer -- which presents as silence, not as an error.
"""

import re
from pathlib import Path

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPACITY_PATH = PROJECT_ROOT / "config" / "capacity.yaml"
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"

PLACEMENT_ANSWERS = frozenset({"reliably", "unreliably"})

#: What one accelerator of each EC2 family in the catalog is: the device a profile name
#: spells, and the memory on it in GiB. Keyed by instance family rather than by profile
#: name, so this is a fact about what EC2 sells rather than a second copy of the catalog,
#: and a profile whose name and instance type disagree is caught rather than sized wrongly.
#:
#: These are the figures config/capacity.yaml does its own arithmetic with -- "384 GB
#: against 320 GB" for eight L40S against eight A100, and "one H100 80 GB" -- so a number
#: here that drifted would contradict the file it is checking.
ACCELERATORS = {
    "g4dn": ("t4", 16),
    "g5": ("a10g", 24),
    "g6": ("l4", 24),
    "g6e": ("l40s", 48),
    "p4d": ("a100", 40),
    "p5": ("h100", 80),
}

#: How a GPU profile name spells its device count and its device. The trailing qualifier on
#: gpu-1xa10g-sagemaker is deliberately not matched: it names the service that would run the
#: shape, not the machine.
GPU_PROFILE_NAME = re.compile(r"^gpu-(?P<count>\d+)x(?P<device>[a-z0-9]+)")


def accelerator(profile: ComputeProfile) -> tuple[int, int]:
    """How many devices this shape has, and the memory on each in GiB.

    Both facts are read from the catalog rather than declared here: the count off the
    profile name, the memory off the instance family. The two are cross-checked, so a
    profile renamed without its instance type changing -- or an instance type swapped under
    a name that still says ``a10g`` -- is a failure rather than a wrong size quietly used
    by the substitution check below.
    """
    matched = GPU_PROFILE_NAME.match(profile.name)
    assert matched, f"{profile.name} does not spell a device count, so it cannot be sized"
    family = profile.instance_type.split(".", 1)[0]
    assert family in ACCELERATORS, (
        f"{profile.name} runs on {profile.instance_type} and ACCELERATORS does not say what "
        f"one {family} accelerator is, so nothing here can tell whether a substitute for it "
        "is the same machine or a tenth of one"
    )
    device, memory_gib = ACCELERATORS[family]
    assert matched["device"] == device, (
        f"{profile.name} names {matched['device']!r} and {profile.instance_type} carries "
        f"{device!r}, so the catalog disagrees with itself about what this machine is"
    )
    return int(matched["count"]), memory_gib


#: The shapes this account cannot obtain. Named here as well as in the file so that a scarce
#: shape quietly becoming reliable is a test edit rather than a silent one: the substitution
#: is the only behaviour a researcher sees, and it disappears without a sound.
#: gpu-1xa10g-sagemaker is deliberately not among them, because what stops that one is the
#: absence of a Batch queue rather than the absence of a machine.
#:
#: FOUR NAMES BECAME TEN ON 2026-08-04 AND THE SIX THAT JOINED HAD NEVER BEEN MEASURED. The
#: old set was the shapes the account had happened to wait on; every other shape was recorded
#: as reliable because nothing had tried it, which is a default nobody chose. A probe against
#: every pool -- ``create-fleet --type instant``, which costs nothing when a pool is empty
#: because nothing launches -- put the whole g6e family, both multi-card g6 sizes and both
#: multi-card g5 sizes on this list.
#:
#: THE COUNT IS LOAD-BEARING IN A SECOND WAY NOW. Ten of seventeen priced shapes do not place,
#: so this set being long is the finding rather than an accident of bookkeeping, and shrinking
#: it back is a claim that wants the same probe behind it.
SHAPES_THAT_DO_NOT_PLACE = frozenset(
    {
        "gpu-4xa10g",
        "gpu-8xa10g",
        "gpu-4xl4",
        "gpu-8xl4",
        "gpu-1xl40s",
        "gpu-4xl40s",
        "gpu-8xl40s",
        "gpu-1xh100",
        "gpu-8xa100",
        "gpu-8xh100",
    }
)


@pytest.fixture(scope="module")
def entries() -> list[dict[str, object]]:
    document = yaml.safe_load(CAPACITY_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    return list(document["profiles"])


@pytest.fixture(scope="module")
def catalog() -> WorkloadCatalog:
    return load_yaml(CATALOG_PATH, WorkloadCatalog)


def test_every_priced_shape_has_a_placement_answer(
    entries: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    recorded = [entry["profile"] for entry in entries]
    priced = [profile.name for profile in catalog.compute_profiles]

    assert sorted(recorded) == sorted(priced)
    assert len(recorded) == len(set(recorded))


def test_the_answers_are_the_two_the_file_declares(entries: list[dict[str, object]]) -> None:
    assert {entry["places"] for entry in entries} <= PLACEMENT_ANSWERS
    assert {
        str(entry["profile"]) for entry in entries if entry["places"] == "unreliably"
    } == SHAPES_THAT_DO_NOT_PLACE


def test_a_substitute_is_a_shape_that_places_and_can_be_run(
    entries: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """Mutation: point a scarce shape at another scarce shape, or at an unprovisioned one.

    An offer that cannot itself be placed is worse than no offer, because the submitter takes
    it and waits in RUNNABLE anyway, having been told by the platform that this one starts in
    minutes.
    """
    by_name = {profile.name: profile for profile in catalog.compute_profiles}
    places_reliably = {
        str(entry["profile"]) for entry in entries if entry["places"] == "reliably"
    }

    for entry in entries:
        substitute = entry.get("offer_instead")
        if substitute is None:
            continue
        assert substitute in places_reliably, entry["profile"]
        assert by_name[str(substitute)].provisioned, entry["profile"]


def test_only_a_shape_that_does_not_place_offers_a_substitute(
    entries: list[dict[str, object]],
) -> None:
    for entry in entries:
        if entry["places"] == "reliably":
            assert "offer_instead" not in entry, entry["profile"]


def test_every_gpu_shape_in_the_catalog_is_one_this_module_can_size(
    catalog: WorkloadCatalog,
) -> None:
    """Mutation: promote a shape on a family ``ACCELERATORS`` does not carry.

    The check below is only as good as its ability to size both sides of an offer, and the
    way a size table fails is by going stale rather than by being wrong on the day it is
    written. Asking it of every GPU profile rather than only of the ones an offer names
    means a new family is a failure here, where the message says what to add, instead of
    surfacing later as a substitution nobody could measure.
    """
    gpu = [profile for profile in catalog.compute_profiles if profile.accelerator == "gpu"]

    assert gpu, "the catalog prices no GPU shape, so sizing them proves nothing"
    for profile in gpu:
        devices, memory_gib = accelerator(profile)
        assert devices >= 1
        assert memory_gib >= 1


def test_a_substitute_is_at_least_the_machine_it_replaces(
    entries: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """AN OFFER THAT PLACES AND DOES NOT FIT IS THE DEFECT THIS TABLE EXISTS TO PREVENT, AND
    NOTHING SAW IT. Mutation: point ``gpu-4xa10g`` at ``gpu-1xt4``.

    That mutation was invisible. ``gpu-1xt4`` places reliably and is provisioned, which is
    everything the test above asks of a substitute, so a submitter asking for four 24 GB
    A10Gs could be offered a single 16 GB T4 -- a quarter of the devices and two thirds of
    the memory on each -- and the whole suite stayed green. The two package tests notice,
    because they digest the tree and fire on a comment; they carry nothing about whether
    the offer fits.

    What makes an offer honest is written in ``config/capacity.yaml`` and was asserted
    nowhere. ``gpu-4xl4`` is offered for ``gpu-4xa10g`` because it is the "same device
    memory, same device count, less money"; ``gpu-8xl40s`` is offered for ``gpu-8xa100`` at
    "384 GB against 320 GB". And ``gpu-1xh100`` deliberately offers nothing, because the
    largest single card in the catalog is the 48 GB L40S and the file records what happened
    the last time somebody offered it anyway: "a changed recipe, which is a deviation the
    researcher has to declare and defend rather than a substitution the platform may make
    for them".

    So the rule is that an offer may cost more and may be a different device, and may not
    be a smaller machine in either dimension. Both are checked, because a shape can shrink
    in either one alone: eight 24 GB cards for eight 48 GB cards holds the count and halves
    the memory, and one 48 GB card for four of them holds the memory and drops three
    devices.
    """
    by_name = {profile.name: profile for profile in catalog.compute_profiles}
    offers = [
        (str(entry["profile"]), str(entry["offer_instead"]))
        for entry in entries
        if entry.get("offer_instead") is not None
    ]

    assert offers, "no shape offers a substitute, so this comparison proves nothing"
    for scarce_name, substitute_name in offers:
        scarce_devices, scarce_memory = accelerator(by_name[scarce_name])
        offer_devices, offer_memory = accelerator(by_name[substitute_name])

        assert offer_memory >= scarce_memory, (
            f"{scarce_name} is offered {substitute_name}, which carries {offer_memory} GiB "
            f"a device against {scarce_memory} GiB. A smaller card is a changed recipe the "
            "researcher has to declare, not a substitution this platform may make for them"
        )
        assert offer_devices >= scarce_devices, (
            f"{scarce_name} is offered {substitute_name}, which has {offer_devices} devices "
            f"against {scarce_devices}. Fewer devices changes how the work shards, so it is "
            "a different run rather than the same run on another machine"
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "config/capacity.yaml has no reader outside this module. The substitution it "
        "describes is not implemented, and whether to build it or to delete the table is a "
        "decision for whoever owns the submission path"
    ),
)
def test_the_substitution_table_is_read_by_something_that_can_act_on_it() -> None:
    """NOTHING WALKS THIS REGISTRY, AND THE TESTS AROUND IT CANNOT SAY SO.

    ``config/capacity.yaml`` opens by stating that "the submission path promises that asking
    for a shape which does not reliably place is answered at the moment of choosing with one
    that does". No such answer is given anywhere. ``rg -l capacity.yaml`` over ``src/``,
    ``tools/`` and ``.github/workflows/`` returns nothing; every other file under
    ``config/`` has between seven and forty-eight readers there. ``offer_instead`` and
    ``unreliably`` appear in this file and in this test module and nowhere else in the tree.

    So the four checks above hold a table self-consistent, and a submitter asking for
    ``gpu-4xa10g`` is still told nothing and still waits in ``RUNNABLE``. Every one of them
    can fail on an edit to the table; none of them can fail because the behaviour the table
    exists for is absent, which is the state the repository is actually in.

    Marked ``xfail(strict=True)`` rather than deleted or left red, so the gap is reported on
    every run and the marker becomes the failure the day somebody gives the table a reader.
    The two ways out are a submission path that consults it and a deletion of the file with
    its four tests; both are somebody's decision rather than this test's.
    """
    searched = ("src", "tools", ".github/workflows")
    readers = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for directory in searched
        for path in (PROJECT_ROOT / directory).rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".yml", ".yaml"}
        and "capacity.yaml" in path.read_text(encoding="utf-8", errors="ignore")
    )

    assert readers, (
        "nothing in " + ", ".join(searched) + " reads config/capacity.yaml, so the "
        "substitution it describes cannot happen and the tests over it only hold it "
        "self-consistent"
    )
