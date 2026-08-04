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

import ast
import re
from pathlib import Path

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog
from edullm_platform.placement import read_capacity

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


def test_every_gpu_shape_in_the_catalog_is_one_this_module_can_size(
    catalog: WorkloadCatalog,
) -> None:
    """Mutation: promote a shape on a family ``ACCELERATORS`` does not carry.

    This outlived the substitution comparison it was written to support, and it is kept
    because what it actually holds is the catalog against itself: a profile whose name says
    ``a10g`` while its instance type is a T4 fails here, in either direction, and so does a
    new family nobody added a size for. Neither has anything to do with substitution.

    Asking it of every GPU profile rather than only of the ones some other check happens to
    name is the reason it still means something now that nothing names any.
    """
    gpu = [profile for profile in catalog.compute_profiles if profile.accelerator == "gpu"]

    assert gpu, "the catalog prices no GPU shape, so sizing them proves nothing"
    for profile in gpu:
        devices, memory_gib = accelerator(profile)
        assert devices >= 1
        assert memory_gib >= 1


def test_no_shape_offers_a_substitute_at_all(entries: list[dict[str, object]]) -> None:
    """THIS TABLE OFFERS NOTHING, AND THAT IS THE POSITION RATHER THAN AN EMPTY COLUMN.

    What stood here compared an offer against the machine it replaced, in device count and
    in device memory, because the defect it was written for was invisible: ``gpu-4xa10g``
    could be pointed at ``gpu-1xt4`` -- a quarter of the devices and two thirds of the memory
    on each -- and the whole suite stayed green, since a substitute only had to place and be
    provisioned.

    Then #185 re-measured every pool and withdrew both recorded offers, because both pointed
    at machines this account has never obtained, and stated the rule that survives them: a
    third of the device memory removed is a changed recipe the submitter declares rather than
    a substitution the platform makes for them. With nothing left to compare, that check
    reported exactly that -- "no shape offers a substitute, so this comparison proves
    nothing" -- which is a guard doing its job and a check that can no longer fail for its
    own reason.

    So the rule is asserted directly instead. Every entry is checked rather than only the
    ones that place, which is what the predecessor of this line did: a substitute
    reintroduced on an unplaceable shape was the one case it could not see, and after
    :mod:`edullm_platform.placement` stopped reading the field, it is also the case that
    would be silently ignored rather than acted on. Reintroducing substitution is a change to
    :func:`~edullm_platform.placement.placement_warning` and to this line together, which is
    the point: it should not be possible to do it by editing a config file alone.
    """
    offering = sorted(
        str(entry["profile"]) for entry in entries if entry.get("offer_instead") is not None
    )

    assert not offering, (
        "config/capacity.yaml offers a substitute for " + ", ".join(offering) + ". Both "
        "recorded offers were withdrawn on 2026-08-04 because they named machines this "
        "account has never obtained, and nothing reads the field any more, so an entry here "
        "changes no behaviour and reads as a promise the submission path does not keep"
    )


#: Where a reader would live. Tests are excluded on purpose: this module reading the file
#: is what the check exists to distinguish from production reading it.
PRODUCTION_TREES = ("src", "tools")

#: The module that turns the file into records. A consumer is anything that imports from it;
#: the module itself is not its own consumer, so it is skipped when they are counted.
READER_MODULE = PROJECT_ROOT / "src" / "edullm_platform" / "placement.py"


def _production_consumers_of_the_reader() -> list[str]:
    """Every production module that imports :mod:`edullm_platform.placement`.

    Parsed rather than searched. The predecessor of this check looked for the string
    ``capacity.yaml`` in each file and could not tell a reader from a sentence about one --
    on 2026-08-04 it matched ``src/edullm_platform/execution.py``, where the name appears in
    a comment, and ``tools/build_admission_lambda.py``, where the comment says the file is
    *deliberately absent* from what the Lambda carries. A check that counts a line denying
    the file is read as evidence that it is read cannot fail for the reason it exists.

    ``ast`` does not see comments or docstrings at all, so the only thing that satisfies this
    is an import a module actually executes.
    """
    consumers: list[str] = []
    for directory in PRODUCTION_TREES:
        for path in sorted((PROJECT_ROOT / directory).rglob("*.py")):
            if path == READER_MODULE:
                continue
            parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(parsed):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "edullm_platform.placement"
                ) or (
                    isinstance(node, ast.Import)
                    and any(alias.name == "edullm_platform.placement" for alias in node.names)
                ):
                    consumers.append(str(path.relative_to(PROJECT_ROOT)))
                    break
    return consumers


def test_the_substitution_table_is_read_by_something_that_can_act_on_it() -> None:
    """THE TABLE HAS A READER ON THE SUBMISSION PATH, AND THIS FAILS IF IT LOSES ONE.

    ``config/capacity.yaml`` opens by stating that "the submission path promises that asking
    for a shape which does not reliably place is answered at the moment of choosing with one
    that does". For a while nothing kept that promise: the four checks above held the table
    self-consistent, a submitter asking for ``gpu-4xa10g`` was told nothing, and none of them
    could fail because the behaviour the table exists for was absent.

    That was recorded as ``xfail(strict=True)`` so it would turn red the day somebody built
    the reader. :mod:`edullm_platform.placement` is that reader and
    ``tools/compile_submission.py`` is the consumer, so the marker has done its job and is
    gone. It is replaced rather than deleted with it: an ``xfail`` that clears itself takes
    the property with it when it goes, and the property -- that this file is read by
    something on the path to a submission, and not merely mentioned -- is the one worth
    keeping now that it finally holds.

    Both halves are asserted because either can be lost on its own. A consumer that imports
    the reader and never calls it still satisfies the first, and a reader that stops parsing
    the shipped file still satisfies the second.
    """
    consumers = _production_consumers_of_the_reader()

    assert consumers, (
        "nothing in " + ", ".join(PRODUCTION_TREES) + " imports edullm_platform.placement, "
        "so config/capacity.yaml is read only by its own tests and the substitution it "
        "describes cannot reach a submitter"
    )

    records = read_capacity(CAPACITY_PATH)

    assert records, "the reader parses config/capacity.yaml into no records at all"
    assert {record.profile for record in records} >= SHAPES_THAT_DO_NOT_PLACE, (
        "the reader does not return the shapes this file records as unplaceable, so what "
        "the consumers above act on is not the table these tests check"
    )
