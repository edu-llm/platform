"""``config/capacity-blocks.yaml`` and ``guides/capacity-blocks.md`` against the catalog.

Read with ``yaml.safe_load`` rather than through a contract model, for the reason
``tests/test_capacity.py`` gives about ``config/capacity.yaml``: this is a per-instance-type side
table, it has no home on ``ComputeProfile``, and adding a pydantic model for it would put a
second unversioned schema in the tree.

**THE FAILURE THIS EXISTS TO PREVENT IS A MENU THAT NAMES A ROUTE NOBODY BUILT.** The menu's
``profile`` column is the one thing in the file that is a claim about this repository rather than
a fact about AWS's price list, and it is the one thing a reader will act on: somebody comparing
rates picks a row, reads the profile, and submits against it. A name there that the catalog does
not carry is a purchase made for a shape nothing can place -- which is not hypothetical. The one
capacity block this account has ever bought was for ``p6-b200.48xlarge`` at a time when no
profile, no accelerator row and no container shape for it existed.

**AND A GUIDE THAT NAMES ONE, WHICH IS THE SAME FAILURE WITH A WIDER AUDIENCE.** Four rows of
that guide's table read "not in the catalog yet" until the shapes were priced. Prose goes stale
in the direction of over-promising, because the edit that adds a profile is not the edit that
remembers the paragraph describing it, so every profile either document spells in backticks is
held here to the reviewed catalog.
"""

import re
from pathlib import Path

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BLOCKS_PATH = PROJECT_ROOT / "config" / "capacity-blocks.yaml"
ACCELERATORS_PATH = PROJECT_ROOT / "config" / "accelerators.yaml"
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"
GUIDE_PATH = PROJECT_ROOT / "guides" / "capacity-blocks.md"
SKILL_PATH = PROJECT_ROOT / "skills" / "edullm-platform" / "SKILL.md"

#: How a compute profile name is spelled, so one can be recognised in prose without a list of
#: names here. Matching the shape rather than a set of known names is what makes this a tripwire
#: instead of a second copy of the catalog: a document naming ``gpu-4xh100`` -- a shape EC2 does
#: not sell and this platform has never priced -- is caught because it looks like a profile, not
#: because anybody predicted that particular mistake.
#:
#: A DIGIT IS REQUIRED IMMEDIATELY AFTER THE DASH, AND THAT IS WHAT KEEPS THIS OFF ENGLISH.
#: Every profile the catalog carries has one, from ``cpu-32vcpu`` to ``gpu-8xa100-80gb``. Without
#: the digit this would match "gpu-backed", "gpu-bound" and any other hyphenated adjective a
#: writer reaches for, and the test would fail on prose rather than on a stale name.
#:
#: NOT RESTRICTED TO BACKTICKS, BECAUSE THE SKILL FILE DOES NOT USE THEM FOR THIS. It names
#: profiles inside fenced examples -- ``suggested_compute: gpu-1xt4`` in a YAML block, a profile
#: inside a shell one-liner -- and those are exactly the occurrences a researcher copies. A check
#: that read only backticks would have passed that file while asserting nothing about it.
PROFILE_TOKEN = re.compile(r"\b(?:cpu|gpu)-\d+[a-z0-9]*(?:-[a-z0-9]+)*\b")

#: What the menu's GB column may differ from ``config/accelerators.yaml``'s MiB total by, as a
#: fraction. Not zero, and the reason is recorded in both files: AWS publishes 2,144 GB for
#: ``p6-b300.48xlarge`` while its cards report 275,040 MiB each, which is 2,148.75 GiB for eight,
#: so AWS's own two figures for that machine are about 5 GiB apart. Every other row reconciles to
#: within a rounding step. One percent is loose enough for that disagreement and far too tight for
#: a transposed digit, which is what this is really watching for.
GB_TOLERANCE = 0.01


@pytest.fixture(scope="module")
def menu() -> list[dict[str, object]]:
    document = yaml.safe_load(BLOCKS_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["region"] == "us-east-1", (
        "every queue and subnet on this platform is in us-east-1, so a block bought anywhere "
        "else is one nothing here can reach"
    )
    return list(document["blocks"])


@pytest.fixture(scope="module")
def catalog() -> WorkloadCatalog:
    return load_yaml(CATALOG_PATH, WorkloadCatalog)


@pytest.fixture(scope="module")
def accelerator_totals() -> dict[str, int]:
    document = yaml.safe_load(ACCELERATORS_PATH.read_text(encoding="utf-8"))
    return {
        str(row["profile"]): int(row["memory_mib_total"]) for row in document["profiles"]
    }


def test_every_profile_the_menu_names_is_one_the_catalog_prices(
    menu: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """Mutation: point a block at a profile nobody priced, which is how the last one went wrong.

    ``null`` is allowed and means the catalog has never priced the machine. A string is a promise
    that submitting against that name reaches something, and this is what holds it.
    """
    priced = {profile.name for profile in catalog.compute_profiles}
    named = {
        str(block["profile"]): str(block["instance_type"])
        for block in menu
        if block.get("profile") is not None
    }

    unknown = {name: kind for name, kind in named.items() if name not in priced}
    assert not unknown, (
        f"config/capacity-blocks.yaml points at profiles the catalog does not price: {unknown}. "
        "Either price them or set the column back to null; a name here is read as a route."
    )


def test_the_profile_a_block_names_runs_on_the_instance_type_the_block_is(
    menu: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """Mutation: swap two profile names between rows, which no name check would notice.

    The previous test would pass on a menu that gave ``p4de.24xlarge`` the ``gpu-8xb300``
    profile, because both names are real. What makes the column mean anything is that the
    profile's own ``instance_type`` is the row it sits on, and that is a fact the catalog already
    carries, so it can be checked rather than trusted.
    """
    instance_type_of = {
        profile.name: profile.instance_type for profile in catalog.compute_profiles
    }

    for block in menu:
        profile = block.get("profile")
        if profile is None:
            continue
        assert instance_type_of[str(profile)] == str(block["instance_type"]), (
            f"the menu offers {block['instance_type']} as {profile}, and the catalog says "
            f"{profile} runs on {instance_type_of[str(profile)]}"
        )


def test_a_priced_block_carries_the_device_memory_its_accelerator_row_records(
    menu: list[dict[str, object]], accelerator_totals: dict[str, int]
) -> None:
    """Mutation: leave the menu's GB column behind when an accelerator row is corrected.

    This caught a real disagreement rather than a hypothetical one. The ``p6-b200.48xlarge`` row
    read 1,440 GB, which is NVIDIA's announcement figure counted in decimal bytes, against an
    accelerator row of 183,359 MiB a device -- 1,432 GiB for eight, which is what AWS publishes
    for the instance. Both numbers describe the same silicon and only one of them belongs in a
    column headed GB beside seven others counted the same way.
    """
    for block in menu:
        profile = block.get("profile")
        if profile is None:
            continue
        total_mib = accelerator_totals[str(profile)]
        stated_gb = int(block["device_memory_gb"])  # type: ignore[call-overload]
        derived_gb = total_mib / 1024

        assert abs(derived_gb - stated_gb) / stated_gb <= GB_TOLERANCE, (
            f"{block['instance_type']} is listed at {stated_gb} GB and {profile}'s accelerator "
            f"row totals {total_mib} MiB, which is {derived_gb:.2f} GiB"
        )


def test_the_menu_offers_each_instance_type_once(menu: list[dict[str, object]]) -> None:
    """Mutation: add a second row for a type at a different rate.

    Two rows for one machine is two prices, and the reader picks whichever they see first. The
    tool that prices a purchase sorts by rate, so it would silently prefer the cheaper of a
    contradictory pair.
    """
    offered = [str(block["instance_type"]) for block in menu]
    assert len(offered) == len(set(offered)), f"a type is listed twice: {offered}"


def test_every_excluded_type_says_why_it_is_excluded() -> None:
    """Mutation: drop a type from the menu and record nothing about it.

    Each of the three exclusions is a reasonable thing for somebody to propose a second time, and
    none of the reasons is guessable from AWS's pricing page -- one is a region, one is a Local
    Zone this account is not opted into, and one is Trainium rather than a GPU at all. A silent
    omission is an afternoon spent rediscovering it.
    """
    document = yaml.safe_load(BLOCKS_PATH.read_text(encoding="utf-8"))

    for entry in document["excluded"]:
        assert entry["instance_type"]
        assert len(str(entry["because"]).split()) >= 15, (
            f"{entry['instance_type']} is excluded with a reason too short to act on"
        )


@pytest.mark.parametrize("document", [GUIDE_PATH, SKILL_PATH], ids=lambda path: path.name)
def test_every_profile_a_document_names_is_one_the_catalog_prices(
    document: Path, catalog: WorkloadCatalog
) -> None:
    """Mutation: leave a profile name in prose after the catalog stops carrying it.

    Both documents are checked by one test because the drift presents the same way in each: a
    name that looks exactly like every other profile name and does not resolve. They are read by
    different people, which is why both matter -- the guide by whoever is spending the money, the
    skill file by researchers who copy it into their own repositories and run what it says.

    Only tokens shaped like a profile are examined, so ``edullm check``, ``p5.48xlarge`` and
    ``config/capacity.yaml`` are all ignored without being enumerated here.
    """
    if not document.exists():
        pytest.skip(f"{document.relative_to(PROJECT_ROOT)} is not committed")

    priced = {profile.name for profile in catalog.compute_profiles}
    named = set(PROFILE_TOKEN.findall(document.read_text(encoding="utf-8")))

    assert named, f"{document.name} names no compute profile at all, so this asserts nothing"
    assert named <= priced, (
        f"{document.relative_to(PROJECT_ROOT)} names profiles the catalog does not price: "
        f"{sorted(named - priced)}"
    )
