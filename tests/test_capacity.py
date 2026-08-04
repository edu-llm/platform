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

from pathlib import Path

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPACITY_PATH = PROJECT_ROOT / "config" / "capacity.yaml"
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"

PLACEMENT_ANSWERS = frozenset({"reliably", "unreliably"})

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
#: THE COUNT IS LOAD-BEARING IN A SECOND WAY NOW. Ten of fifteen priced shapes do not place,
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
