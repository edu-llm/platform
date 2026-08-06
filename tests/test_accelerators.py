"""config/accelerators.yaml against the catalogue, against the account, and against itself.

Read with ``yaml.safe_load`` here and through :mod:`edullm_platform.accelerators` in the
reader, for the reason ``tests/test_capacity.py`` records about its own file: these fields
belong on ``ComputeProfile`` beside ``provisioned``, and they are not there because that
model's structural digest is in ``fixtures/goldens/contract-models.json``. So the file is plain
configuration and these are the checks a validator would otherwise do.

**THE FIGURES ARE WRITTEN OUT HERE, WHICH IS THE POINT RATHER THAN DUPLICATION.** Every number
below came out of one ``aws ec2 describe-instance-types`` call against this account on
2026-08-06. A test that derived its expectation from the file would agree with any figure the
file carried, which is how a table of invented numbers passes review -- and inventing them is
precisely what a previous attempt at this work declined to do, because a wrong memory figure
produces a refusal that blocks a run which would have worked. Restating them means a changed
digit is a line in a diff that somebody has to justify with a new measurement.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from edullm_platform.accelerators import (
    UnreadableAcceleratorsError,
    device_said,
    memory_said,
    read_accelerators,
    record_for,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.precision import GPUS_BY_INSTANCE_FAMILY, instance_family

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCELERATORS_PATH = PROJECT_ROOT / "config" / "accelerators.yaml"
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"
SUBMISSION_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "submission-inputs.schema.json"

#: WHAT THE ACCOUNT ANSWERED, PROFILE BY PROFILE: the card, the device count, and the memory on
#: one device in MiB. Transcribed from the response rather than from any file in this tree, so
#: that this and ``config/accelerators.yaml`` are two independent copies of one measurement.
#:
#: The unit is MiB because that is the unit ``GpuInfo.Gpus[].MemoryInfo.SizeInMiB`` returns.
#: Two of the six cards here are sold as "24 GB" and report 22,888, which is 22.35 GiB -- the
#: gap this file exists to keep out of anybody's batch-size arithmetic.
MEASURED = {
    "cpu-32vcpu": (None, 0, 0),
    "gpu-1xt4": ("T4", 1, 16384),
    "gpu-4xt4": ("T4", 4, 16384),
    "gpu-8xt4": ("T4", 8, 16384),
    "gpu-1xa10g": ("A10G", 1, 22888),
    "gpu-1xa10g-sagemaker": ("A10G", 1, 22888),
    "gpu-4xa10g": ("A10G", 4, 22888),
    "gpu-8xa10g": ("A10G", 8, 22888),
    "gpu-1xl4": ("L4", 1, 22888),
    "gpu-4xl4": ("L4", 4, 22888),
    "gpu-8xl4": ("L4", 8, 22888),
    "gpu-1xl40s": ("L40S", 1, 45776),
    "gpu-4xl40s": ("L40S", 4, 45776),
    "gpu-8xl40s": ("L40S", 8, 45776),
    "gpu-1xh100": ("H100", 1, 81920),
    "gpu-8xa100": ("A100", 8, 40960),
    "gpu-8xh100": ("H100", 8, 81920),
}

#: How a GPU profile name spells its device count and its device, as
#: ``tests/test_capacity.py`` already reads them. The trailing qualifier on
#: ``gpu-1xa10g-sagemaker`` is deliberately not matched: it names the service that would run
#: the shape rather than the machine.
GPU_PROFILE_NAME = re.compile(r"^gpu-(?P<count>\d+)x(?P<device>[a-z0-9]+)")


@pytest.fixture(scope="module")
def entries() -> list[dict[str, object]]:
    document = yaml.safe_load(ACCELERATORS_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["observed_at"] == "2026-08-06"
    return list(document["profiles"])


@pytest.fixture(scope="module")
def catalog() -> WorkloadCatalog:
    return load_yaml(CATALOG_PATH, WorkloadCatalog)


def a_document(tmp_path: Path, profiles: list[dict[str, object]]) -> Path:
    path = tmp_path / "accelerators.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "observed_at": "2026-08-06", "profiles": profiles}),
        encoding="utf-8",
    )
    return path


def test_every_priced_shape_has_a_measured_card(
    entries: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """Mutation: drop a row, or list only the GPU shapes.

    A file covering only the shapes somebody thought to measure is a denylist wearing a
    table's clothes, and ``config/capacity.yaml`` is a hundred lines of what that costs: six
    of its seventeen entries were wrong in one direction because "no experience" had been
    written down as a verdict nobody chose. The CPU profile is the row a shortened file would
    lose first, and it is the one that answers a training command sent to a CPU queue.
    """
    recorded = [entry["profile"] for entry in entries]
    priced = [profile.name for profile in catalog.compute_profiles]

    assert sorted(recorded) == sorted(priced)
    assert len(recorded) == len(set(recorded))


def test_the_shipped_figures_are_the_ones_the_account_answered_with(
    entries: list[dict[str, object]],
) -> None:
    """Mutation: change any memory figure, or round 22,888 to 24,576.

    That rounding is the specific mistake this table was written to stop, and it is a
    plausible one: the A10G is sold as a 24 GB card, 24 GiB is 24,576 MiB, and a reader
    tidying the file would think they were correcting a typo. It would hand every A10G and L4
    shape 1.65 GiB per card that is not there.
    """
    observed = {
        str(entry["profile"]): (
            entry["device"],
            entry["devices"],
            entry["memory_mib_per_device"],
        )
        for entry in entries
    }

    assert observed == MEASURED


def test_a_total_is_the_product_of_the_two_figures_beside_it(
    entries: list[dict[str, object]],
) -> None:
    """Mutation: change one total, leaving the per-device figure and the count alone.

    The file records ``MemoryInfo.SizeInMiB`` and ``TotalGpuMemoryInMiB``, which are two
    separate fields of one API response, and storing both is what makes a mistyped digit
    visible. Deriving the total in the reader instead would make this comparison agree with
    itself whatever the file said.
    """
    for entry in entries:
        expected = int(entry["devices"]) * int(entry["memory_mib_per_device"])  # type: ignore[call-overload]
        assert entry["memory_mib_total"] == expected, (
            f"{entry['profile']} records a total of {entry['memory_mib_total']} and its own "
            f"two figures multiply to {expected}"
        )


def test_the_profile_name_agrees_with_what_was_measured_under_it(
    entries: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """Mutation: swap two rows, so ``gpu-4xl40s`` carries the eight-card measurement.

    The name is a slug and the measurement is a fact, and they can disagree in two ways that
    both cost real money: a shape renamed without its instance type changing, and an instance
    type swapped under a name that still spells the old card. Checked in both directions here,
    which is the same property ``tests/test_capacity.py`` asks of its hand-written table --
    asked of a measurement rather than of a dict somebody typed.
    """
    by_name = {profile.name: profile for profile in catalog.compute_profiles}
    for entry in entries:
        name = str(entry["profile"])
        matched = GPU_PROFILE_NAME.match(name)
        if matched is None:
            assert by_name[name].accelerator == "cpu", (
                f"{name} spells no device count and the catalogue calls it a GPU shape"
            )
            assert entry["devices"] == 0
            continue
        assert int(matched["count"]) == entry["devices"], (
            f"{name} spells {matched['count']} devices and describe-instance-types reported "
            f"{entry['devices']} on {by_name[name].instance_type}"
        )
        assert matched["device"] == str(entry["device"]).casefold(), (
            f"{name} names {matched['device']!r} and {by_name[name].instance_type} carries "
            f"{entry['device']!r}"
        )


def test_the_card_agrees_with_the_one_the_bfloat16_guard_refuses_on(
    entries: list[dict[str, object]], catalog: WorkloadCatalog
) -> None:
    """Mutation: rename a card here, or in ``GPUS_BY_INSTANCE_FAMILY``.

    Two tables in this tree now say what card an instance family carries, and they are keyed
    differently on purpose -- :mod:`edullm_platform.precision` by family, because every size of
    a family shares a card and a bfloat16 answer, and this by profile, because the memory
    differs per size. Keyed differently they cannot be folded together, and unchecked they can
    disagree: a submitter would then be refused bfloat16 on a card the profile table says is
    something else. ``g6`` and ``g6e`` are the pair that makes this worth asserting, being one
    letter apart and two different cards.
    """
    by_name = {profile.name: profile for profile in catalog.compute_profiles}
    checked = 0
    for entry in entries:
        if entry["devices"] == 0:
            continue
        family = instance_family(by_name[str(entry["profile"])].instance_type)
        assert GPUS_BY_INSTANCE_FAMILY[family].model == entry["device"], (
            f"{entry['profile']} runs on {family} and the two tables disagree about what that "
            f"is: {entry['device']!r} here against "
            f"{GPUS_BY_INSTANCE_FAMILY[family].model!r} in precision.py"
        )
        checked += 1

    assert checked == len(MEASURED) - 1, "every GPU profile but the CPU one should be compared"


def test_a_card_is_named_exactly_where_there_is_one(tmp_path: Path) -> None:
    """Mutation: drop either half of the pair in ``read_accelerators``.

    One direction names a card on a shape that has none, which is a machine a submitter would
    believe they were getting. The other leaves a shape with devices and no name, which is a
    table that cannot say what a run would land on. It is one failure guarded from both sides,
    the way :func:`~edullm_platform.placement.read_capacity` guards ``wait``.
    """
    with pytest.raises(UnreadableAcceleratorsError, match="a card that is not there"):
        read_accelerators(
            a_document(
                tmp_path,
                [
                    {
                        "profile": "cpu-32vcpu",
                        "device": "A100",
                        "devices": 0,
                        "memory_mib_per_device": 0,
                        "memory_mib_total": 0,
                    }
                ],
            )
        )

    with pytest.raises(UnreadableAcceleratorsError, match="does not say what"):
        read_accelerators(
            a_document(
                tmp_path,
                [
                    {
                        "profile": "gpu-1xt4",
                        "device": None,
                        "devices": 1,
                        "memory_mib_per_device": 16384,
                        "memory_mib_total": 16384,
                    }
                ],
            )
        )


def test_a_total_that_does_not_multiply_out_is_refused(tmp_path: Path) -> None:
    """Mutation: read the total and never compare it to the two figures beside it.

    Storing a figure nothing checks is worse than not storing it: it reads as corroboration.
    """
    with pytest.raises(UnreadableAcceleratorsError, match="have to agree"):
        read_accelerators(
            a_document(
                tmp_path,
                [
                    {
                        "profile": "gpu-4xl40s",
                        "device": "L40S",
                        "devices": 4,
                        "memory_mib_per_device": 45776,
                        "memory_mib_total": 366208,
                    }
                ],
            )
        )


def test_a_device_count_that_is_a_boolean_is_not_a_count(tmp_path: Path) -> None:
    """Mutation: drop the ``isinstance(value, bool)`` arm of ``_whole_number``.

    ``isinstance(True, int)`` is true in Python, and YAML parses a bare ``yes`` as ``True``.
    Without the guard, ``devices: yes`` reads as one device -- a plausible count for a shape
    whose real one nobody wrote down, on a row that would otherwise multiply out correctly.
    """
    with pytest.raises(UnreadableAcceleratorsError, match="not a count of anything"):
        read_accelerators(
            a_document(
                tmp_path,
                [
                    {
                        "profile": "gpu-1xt4",
                        "device": "T4",
                        "devices": True,
                        "memory_mib_per_device": 16384,
                        "memory_mib_total": 16384,
                    }
                ],
            )
        )


def test_a_card_recorded_with_no_memory_on_it_is_refused(tmp_path: Path) -> None:
    """Mutation: accept zero memory on a device, since it multiplies out fine.

    Zero is what an unread ``MemoryInfo`` transcribes to, and it is the one wrong value that
    is self-consistent: ``4 x 0 == 0``. It is also the value that makes every comparison built
    on this file answer "does not fit" rather than fail, which is a refusal nobody can
    diagnose.
    """
    with pytest.raises(UnreadableAcceleratorsError, match="unread MemoryInfo"):
        read_accelerators(
            a_document(
                tmp_path,
                [
                    {
                        "profile": "gpu-4xl4",
                        "device": "L4",
                        "devices": 4,
                        "memory_mib_per_device": 0,
                        "memory_mib_total": 0,
                    }
                ],
            )
        )


def test_two_rows_for_one_profile_is_an_error_rather_than_the_second_one(
    tmp_path: Path,
) -> None:
    """Mutation: read with ``yaml.safe_load`` instead of ``SafeUniqueKeyLoader``.

    Written as a raw document because ``yaml.safe_dump`` cannot produce a duplicate key, which
    is the shape of the thing being guarded against: it arrives from a person editing the file,
    not from a program writing it.
    """
    path = tmp_path / "accelerators.yaml"
    path.write_text(
        "schema_version: 1\n"
        "schema_version: 2\n"
        "observed_at: '2026-08-06'\n"
        "profiles: []\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="duplicate|already"):
        read_accelerators(path)


def test_a_profile_with_no_row_reads_as_missing_rather_than_as_having_no_card() -> None:
    """Mutation: return a zero-device record for an unknown profile instead of ``None``.

    Those are different answers and the table has to keep them apart. A shape newly promoted
    and not yet measured would otherwise render as "none", telling a submitter a GPU machine
    has no GPU.
    """
    accelerators = read_accelerators(ACCELERATORS_PATH)

    assert record_for("gpu-1024xh200", accelerators=accelerators) is None
    cpu = record_for("cpu-32vcpu", accelerators=accelerators)
    assert cpu is not None
    assert cpu.devices == 0
    assert device_said(cpu) == "none"
    assert memory_said(cpu) == "none"


def test_the_memory_is_said_in_mib_and_never_in_the_gb_a_card_is_sold_as() -> None:
    """Mutation: render GB, because it is the unit a researcher recognises.

    It is the unit they recognise and it is the unit that hides the gap. 22,888 MiB is 24.0 GB
    and 22.35 GiB, and a recipe annotated "fits in 24 GB" sized against 24 GiB is over an A10G
    by 1.65 GiB. MiB is what ``describe-instance-types`` returned, what ``nvidia-smi`` prints
    when the run dies, and what :mod:`edullm_platform.execution` already sizes containers in.
    """
    accelerators = read_accelerators(ACCELERATORS_PATH)
    a10g = record_for("gpu-8xa10g", accelerators=accelerators)
    assert a10g is not None

    assert device_said(a10g) == "8 x A10G"
    assert memory_said(a10g) == "183,104 MiB"
    assert "GB" not in memory_said(a10g)


#: Where a reader would live. Tests are excluded on purpose: this module reading the file is
#: what the check below exists to distinguish from production reading it. The same two trees
#: ``tests/test_capacity.py`` names, for the same reason.
PRODUCTION_TREES = ("src", "tools")

READER_MODULE = PROJECT_ROOT / "src" / "edullm_platform" / "accelerators.py"


def _production_consumers_of_the_reader() -> list[str]:
    """Every production module that imports :mod:`edullm_platform.accelerators`.

    Parsed rather than searched, for the reason the equivalent in ``tests/test_capacity.py``
    records: a string search matched a comment saying the file was *deliberately absent* from
    what a Lambda carries and counted it as evidence the file was read. ``ast`` does not see
    comments or docstrings, so only an import a module executes satisfies this.
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
                    and node.module == "edullm_platform.accelerators"
                ) or (
                    isinstance(node, ast.Import)
                    and any(alias.name == "edullm_platform.accelerators" for alias in node.names)
                ):
                    consumers.append(str(path.relative_to(PROJECT_ROOT)))
                    break
    return consumers


def test_the_table_is_read_by_something_and_not_only_by_its_own_tests() -> None:
    """Mutation: delete the two columns from ``tools/render_profile_table.py``.

    ``config/capacity.yaml`` recorded an answer for every priced shape from the day it was
    written and nothing read it for weeks, which is the failure this repository already had
    once with a reviewed table. A file only its tests read is a file that can be quietly wrong.
    """
    consumers = _production_consumers_of_the_reader()

    assert consumers, (
        "nothing in " + ", ".join(PRODUCTION_TREES) + " imports edullm_platform.accelerators, "
        "so config/accelerators.yaml is read only by its own tests and no researcher choosing "
        "a machine ever sees what memory it has"
    )

    records = read_accelerators(ACCELERATORS_PATH)
    assert {record.profile for record in records} == set(MEASURED)


#: Fields on the submission form that would let the platform know how big a model is without
#: reading it out of somebody's command line. None of them exists, which is the finding.
#: ``experiment`` is free text and is deliberately not one: ``mixlaw-370m`` looks like a size
#: and is a name a researcher chose.
A_MODEL_SIZE_BY_ANY_NAME = ("model_size", "parameters", "parameter_count", "model_parameters")


def test_no_submission_field_carries_a_model_size() -> None:
    """THE TRIPWIRE FOR THE FIT REFUSAL THAT WAS NOT BUILT, AND THIS IS WHY IT WAS NOT.

    Deciding whether a model fits on a shape needs two numbers.
    ``config/accelerators.yaml`` supplies the shape's memory exactly. Nothing supplies the
    model's size: the submission form has fifteen properties and none of them is one,
    ``RunManifest`` has none, and the only integer parameter count in the tree is in
    :mod:`edullm_platform.phase4_evidence`, recorded from a run that has already finished.

    So a refusal built today would read a size out of the text of a command, for one of six
    registered repositories -- ``--model-factory olmo2_1B`` is OLMo-core's spelling and
    nothing else uses it -- while the platform's own documented training command,
    ``python .edullm/train_on_corpus.py "$EDULLM_RUN_ID"``, names no model and trains a 190M
    one. It would fire on almost no real submission, which is a check that cannot fail; widened
    until it did fire, it would refuse runs on a parameter count it had inferred. On day one
    the second is worse than the first, and both are worse than saying the memory and stopping.

    Mutation: add a model size to ``schemas/submission-inputs.schema.json``. This goes red, and
    that is the day the arithmetic stops being a guess and the refusal becomes worth arguing
    about again.
    """
    schema = json.loads(SUBMISSION_SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = set(schema["properties"])

    named = sorted(properties & set(A_MODEL_SIZE_BY_ANY_NAME))
    assert not named, (
        "the submission form now carries " + ", ".join(named) + ", so the platform can know "
        "how big a model is without reading it out of a command string. The reason "
        "config/accelerators.yaml ships no fit refusal has expired: work out what a fit "
        "estimate includes and excludes, and refuse only where the model cannot fit under any "
        "reading of it"
    )
