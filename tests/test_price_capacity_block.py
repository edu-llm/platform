"""``tools/price_capacity_block.py``, the check that runs before four figures are spent.

**THIS TOOL IS THE ONLY THING BETWEEN AN ASK FORM AND AN IRREVERSIBLE PURCHASE.** A capacity
block is charged upfront, cannot be cancelled, and is delivered on a date whether or not anybody
is ready for it, so every mistake it fails to catch is a mistake that gets paid for in full. That
is a different risk profile from the rest of ``tools/``, most of which reports on things that have
already happened.

The three answers worth testing are the three a buyer acts on, and none of them is the price.

* **What cannot be bought at all.** AWS sells whole days, one to fourteen and then weekly, so
  somebody who needs eighteen days is buying twenty-one. Finding that out while comparing
  offerings is finding it out three steps too late.
* **What the window is actually worth.** AWS begins reclaiming instances thirty minutes before a
  block ends, so a run sized to the round number loses its last half hour -- and the last half
  hour is where the final checkpoint was going to be written.
* **What should not be bought even though it fits.** A shape a queue already supplies is one a
  block buys the *wait* for, and ``gpu-8xa100``'s wait is a median of 61 minutes against a
  purchase in the hundreds of dollars. This is the most expensive thing the tool can catch and
  it is the one that looks like the safe choice.

Imported by bare module name after a ``sys.path`` insertion, which is what the other tool tests
in this repository do; ``tools/`` is not a package.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIRECTORY = PROJECT_ROOT / "tools"
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

import price_capacity_block
from price_capacity_block import (
    EXIT_OK,
    EXIT_UNUSABLE,
    Block,
    UnreadableBlockMenuError,
    blocks_that_fit,
    duration_refusal,
    main,
    obtainable_today,
    provisioned_profiles,
    read_block_menu,
    usable_hours,
    what_is_missing,
)

MENU_PATH = PROJECT_ROOT / "config" / "capacity-blocks.yaml"
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"


def a_block(
    *,
    instance_type: str = "p5.48xlarge",
    device_memory_gb: int = 640,
    rate: str = "41.528",
    profile: str | None = "gpu-8xh100",
) -> Block:
    return Block(
        instance_type=instance_type,
        device="H100",
        devices=8,
        device_memory_gb=device_memory_gb,
        rate=Decimal(rate),
        profile=profile,
    )


@pytest.mark.parametrize("days", [1, 7, 13, 14, 21, 175, 182])
def test_the_durations_aws_sells_are_accepted(days: int) -> None:
    assert duration_refusal(days) is None


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, "at least one whole day"),
        (-3, "at least one whole day"),
        (183, "beyond the 182-day maximum"),
        (15, "only multiples of 7"),
        (20, "only multiples of 7"),
    ],
)
def test_the_durations_aws_does_not_sell_are_refused_with_the_reason(
    days: int, expected: str
) -> None:
    """Mutation: accept any integer. Every refusal here is one a purchase console would make
    later, after a buyer had already asked a researcher for the wrong number."""
    refusal = duration_refusal(days)

    assert refusal is not None
    assert expected in refusal


def test_a_refusal_beyond_a_fortnight_names_the_next_purchasable_duration() -> None:
    """The number somebody actually needs, rather than the fact that theirs is wrong.

    Fifteen days is not purchasable and twenty-one is, and a buyer told only "not purchasable"
    has to work out which way to round. Rounding down loses a day of a run somebody sized.
    """
    refusal = duration_refusal(15)

    assert refusal is not None
    assert "21" in refusal


def test_the_usable_window_is_half_an_hour_short_of_the_window_bought() -> None:
    """Mutation: return ``days * 24``, which is the number everybody plans against.

    This is the difference between a run that finishes and a run whose last checkpoint is the
    one before the machines were taken away.
    """
    assert usable_hours(1) == Decimal("23.5")
    assert usable_hours(7) == Decimal("167.5")
    assert usable_hours(1) < Decimal(24)


def test_what_fits_is_ordered_cheapest_first_and_excludes_what_does_not_hold_the_run() -> None:
    """Mutation: sort by memory, or use ``>`` for the fit.

    Ordering is the recommendation -- the first row is what the buyer buys -- and a shape whose
    memory exactly equals the requirement has to be kept, because the requirement is the
    researcher's own measured peak rather than a figure with headroom already in it.
    """
    menu = (
        a_block(instance_type="big", device_memory_gb=1440, rate="98.84"),
        a_block(instance_type="small", device_memory_gb=320, rate="11.80"),
        a_block(instance_type="exact", device_memory_gb=640, rate="41.528"),
        a_block(instance_type="cheap-and-big", device_memory_gb=640, rate="17.712"),
    )

    fitting = blocks_that_fit(menu, 640)

    assert [block.instance_type for block in fitting] == [
        "cheap-and-big",
        "exact",
        "big",
    ]


def test_a_shape_with_no_profile_says_what_has_to_be_built() -> None:
    """The branch that is unreached in the shipped menu and is kept for the next shape AWS adds.

    Every row in ``config/capacity-blocks.yaml`` carries a profile as of 2026-08-07. This is what
    a row without one produces, and it names all three of the things that have to exist.
    """
    missing = what_is_missing(a_block(profile=None), {})

    assert missing
    assert any("prices no profile" in item for item in missing)
    assert any("CONTAINER_SHAPES" in item for item in missing)


def test_a_priced_but_unprovisioned_shape_names_the_refusal_a_submitter_would_hit() -> None:
    """Mutation: report nothing missing when a profile exists.

    A profile is not a queue. All seven of the block-backed shapes are priced and none is
    provisioned, so this is the branch every row of the shipped menu but ``gpu-8xa100`` takes,
    and the refusal code it names is the one ``edullm check`` actually prints.
    """
    missing = what_is_missing(a_block(), {"gpu-8xh100": False})

    assert any("unprovisioned_compute_profile" in item for item in missing)
    assert any("execution-targets.yaml" in item for item in missing)


def test_a_priced_and_provisioned_shape_reports_nothing_missing() -> None:
    assert what_is_missing(a_block(), {"gpu-8xh100": True}) == ()


def test_a_shape_that_already_places_reliably_is_flagged_as_not_worth_buying() -> None:
    """THE MOST EXPENSIVE MISTAKE THIS TOOL CATCHES, and it looks like prudence.

    A block for a shape the queue already supplies buys the wait rather than the machine.
    """
    warning = obtainable_today(a_block(), {"gpu-8xh100": ("reliably", None)})

    assert warning is not None
    assert "already places without a block" in warning


def test_a_shape_that_places_after_a_wait_is_flagged_with_the_wait_itself() -> None:
    """The wait is the whole of the decision, so it is quoted rather than summarised.

    Sixty-one minutes against several hundred dollars is a judgement a buyer can make; "you may
    have to wait" is not.
    """
    measured = "Fourteen nodes arrived and the median wait was 61 minutes."
    warning = obtainable_today(a_block(), {"gpu-8xh100": ("after_a_wait", measured)})

    assert warning is not None
    assert measured in warning


def test_a_shape_that_does_not_place_is_not_flagged_because_a_block_is_the_remedy() -> None:
    """Mutation: warn on every shape with a placement record.

    ``unreliably`` is the case a block exists to solve, so a warning here would tell a buyer not
    to buy the one thing that would work.
    """
    assert obtainable_today(a_block(), {"gpu-8xh100": ("unreliably", None)}) is None


def test_a_shape_the_capacity_file_says_nothing_about_is_not_flagged() -> None:
    assert obtainable_today(a_block(profile=None), {}) is None
    assert obtainable_today(a_block(), {}) is None


def test_the_shipped_menu_reads_and_every_rate_is_a_positive_decimal() -> None:
    """Read through ``Decimal(str(...))``, so a rate written unquoted cannot arrive as a float
    and lose cents on the way to a four-figure total."""
    menu = read_block_menu(MENU_PATH)

    assert len(menu) >= 7
    for block in menu:
        assert block.rate > 0
        assert isinstance(block.rate, Decimal)
        assert block.device_memory_gb > 0
        assert block.devices > 0


def test_a_duplicated_instance_type_in_the_menu_is_refused(tmp_path: Path) -> None:
    """Mutation: load with a plain loader. Two rows for one machine is two prices, and the
    second silently wins -- which a reviewer reading the diff would not see."""
    document = tmp_path / "capacity-blocks.yaml"
    document.write_text(
        "schema_version: 1\n"
        "region: us-east-1\n"
        "blocks:\n"
        "  - instance_type: p5.48xlarge\n"
        "    device: H100\n"
        "    devices: 8\n"
        "    device_memory_gb: 640\n"
        "    reservation_rate_usd_per_hour: \"41.528\"\n"
        "    profile: gpu-8xh100\n"
        "    profile: gpu-8xb200\n",
        encoding="utf-8",
    )

    # The exception SafeUniqueKeyLoader raises rather than any exception, because a blind
    # `Exception` here would also pass on a loader that read the file fine and then tripped over
    # something else -- which is every way this could break while the duplicate went through.
    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate mapping key"):
        read_block_menu(document)


@pytest.mark.parametrize(
    "document",
    [
        "",
        "blocks: []\n",
        "blocks:\n  - 3\n",
        "blocks:\n  - instance_type: p5.48xlarge\n",
    ],
)
def test_a_menu_that_is_not_one_is_refused_rather_than_half_read(
    document: str, tmp_path: Path
) -> None:
    """Mutation: fall back to an empty menu. A tool that prices nothing and exits 0 reads as
    "no block is needed", which is the wrong answer to give somebody about to buy one."""
    path = tmp_path / "capacity-blocks.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(UnreadableBlockMenuError):
        read_block_menu(path)


def test_the_shipped_catalog_is_read_for_which_profiles_have_a_queue() -> None:
    provisioned = provisioned_profiles(CATALOG_PATH)

    assert provisioned["gpu-8xa100"] is True
    # gpu-8xb200 READS True AND IT IS THE ONE PROFILE HERE WHERE THAT DOES NOT MEAN A QUEUE IS
    # WAITING. Its execution target is deployed by a purchase, so what the flag records is that
    # everything except the purchase is in place. What this tool does with the flag is unchanged
    # and still right: what_is_missing stops naming the profile as a thing to be provisioned,
    # because provisioning it is no longer one of the steps a buyer has left.
    assert provisioned["gpu-8xb200"] is True
    for name in ("gpu-8xh100", "gpu-8xa100-80gb", "gpu-8xh200", "gpu-8xb300"):
        assert provisioned[name] is False, f"{name} has no block, so it cannot be provisioned"


def test_pricing_a_run_against_the_shipped_configuration_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end against the reviewed files rather than fixtures, deliberately.

    The failure this catches is the one the tool actually had: four edits landed after its last
    clean run, including the whole ``obtainable_today`` path, and nothing had executed it. A tool
    nobody runs is a tool that is broken by the time somebody needs it.
    """
    assert main(["--memory-gb", "300", "--days", "1"]) == EXIT_OK

    printed = capsys.readouterr().out
    assert "23.5 usable hours" in printed
    assert "p4d.24xlarge" in printed
    assert "DO NOT BUY THIS" in printed, (
        "300 GB fits gpu-8xa100, which places after a measured wait, so the cheapest row has to "
        "carry the warning that no block is needed at all"
    )


def test_a_request_larger_than_anything_buyable_is_refused_with_the_largest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: print an empty list. Nothing in this region holds 4 TB on one node, and
    multi-node is not built here, so the honest answer names the ceiling."""
    assert main(["--memory-gb", "4000", "--days", "1"]) == EXIT_UNUSABLE

    assert "p6-b300.48xlarge" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--memory-gb", "640", "--days", "15"],
        ["--memory-gb", "640", "--days", "0"],
        ["--memory-gb", "0", "--days", "1"],
    ],
)
def test_an_unpurchasable_request_exits_two_before_reading_anything(argv: list[str]) -> None:
    """Two rather than one, because the tool could not be driven rather than refusing on the
    merits -- the distinction ``AGENTS.md`` draws for every exit code on this platform."""
    assert main(argv) == EXIT_UNUSABLE


def test_the_reclaim_window_is_the_one_aws_documents() -> None:
    """Pinned because every duration figure the tool prints is derived from it, and because it
    is the sort of constant that gets rounded to zero by somebody tidying up."""
    assert price_capacity_block.RECLAIM_MINUTES == 30
    assert price_capacity_block.MAXIMUM_SINGLE_DAYS == 14
    assert price_capacity_block.WEEKLY_STEP_DAYS == 7
    assert price_capacity_block.MAXIMUM_DAYS == 182
