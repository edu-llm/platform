"""Say which capacity block a run needs, what it will cost, and what is not ready for it.

Run this before buying anything. It reaches no network, reads only reviewed configuration, and
answers in a fraction of a second, which is the same bargain ``edullm check`` offers a
submitter and for the same reason: the mistakes worth catching here are the ones that are free
to fix before a purchase and unfixable after one.

**A CAPACITY BLOCK CANNOT BE CANCELLED.** The reservation fee is charged upfront, the window
opens on its date whether or not anybody is ready, and no refund exists. So this tool is
deliberately blunt about the two things a buyer gets wrong: reaching for more memory than the
run needs, which costs the difference every day of the block, and buying a shape nothing here
can place, which costs all of it.

It answers three questions and refuses to guess at any of them.

- **Which block fits.** The smallest one whose device memory holds the figure you give it.
  Sorted by price, so the cheapest sufficient row is first and the alternatives are visible.
- **What it costs.** The published reservation rate times the hours, with the 30 minutes AWS
  takes off the end to reclaim the machines already subtracted from what you can use.
- **What is missing.** Whether the catalog prices this machine at all, and whether a queue can
  place it today. Both are usually "no" before a purchase, and the point is to see the list
  while it is still a list rather than discovering it a day before the window.

The memory figure is the one input nothing here can check. It comes off the ask form, where the
researcher is asked how they arrived at it, and it is worth more scepticism than any other
number in this process: the gap between 640 GB and 1440 GB is about $1,400 a day.

Exit codes: 0 a block fits and is described, 2 the inputs are unusable or nothing in the region
is large enough.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

import yaml

from edullm_platform.config import SafeUniqueKeyLoader
from edullm_platform.placement import PLACES_UNRELIABLY, read_capacity

EXIT_OK = 0
EXIT_UNUSABLE = 2

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

#: AWS reclaims the machines before the window's stated end, so the hours you can actually run
#: for are fewer than the hours you bought. Thirty minutes for instance blocks; UltraServer
#: blocks take an hour, and none of those are buyable in this region.
RECLAIM_MINUTES: Final = 30

#: The durations AWS sells. One to fourteen days in whole days, then multiples of seven up to
#: 182. Held here so that an impossible request is refused by this tool rather than by the
#: purchase console after somebody has already decided.
MAXIMUM_SINGLE_DAYS: Final = 14
WEEKLY_STEP_DAYS: Final = 7
MAXIMUM_DAYS: Final = 182


class UnreadableBlockMenuError(ValueError):
    """``config/capacity-blocks.yaml`` is not a document this can act on."""


@dataclass(frozen=True)
class Block:
    """One purchasable machine, as the reviewed menu records it."""

    instance_type: str
    device: str
    devices: int
    device_memory_gb: int
    rate: Decimal
    profile: str | None


def read_block_menu(path: Path) -> tuple[Block, ...]:
    """The menu, refusing anything that is not one.

    ``SafeUniqueKeyLoader`` for the reason ``placement.read_capacity`` gives: two entries for
    one instance type would otherwise resolve to whichever was written second, and a duplicated
    row here is a price a reviewer reading the diff would not see was being overridden.
    """
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=SafeUniqueKeyLoader)
    if not isinstance(document, dict):
        raise UnreadableBlockMenuError(f"{path} is not a top-level mapping")
    entries = document.get("blocks")
    if not isinstance(entries, list):
        raise UnreadableBlockMenuError(f"{path} lists no blocks")
    # AN EMPTY LIST IS REFUSED HERE AND USED TO CRASH THE TOOL FOUR FRAMES LATER. `blocks: []`
    # is valid YAML and a valid list, so it read as a menu of nothing, `blocks_that_fit` returned
    # nothing, and `main` took `max()` of an empty sequence -- an unhandled traceback exiting 1,
    # which on this platform means "refused on the merits" and is the one thing this was not.
    #
    # Worth a branch of its own rather than a guard at the call site, because the honest reading
    # of an empty menu is that the file is broken. A tool that answers "nothing buyable holds
    # your run" from a menu nobody filled in has told a buyer something false about AWS.
    if not entries:
        raise UnreadableBlockMenuError(
            f"{path} lists no blocks at all, which is a broken file rather than a region with "
            "nothing for sale"
        )

    blocks: list[Block] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnreadableBlockMenuError(f"{path} holds an entry that is not a mapping")
        try:
            blocks.append(
                Block(
                    instance_type=str(entry["instance_type"]),
                    device=str(entry["device"]),
                    devices=int(entry["devices"]),
                    device_memory_gb=int(entry["device_memory_gb"]),
                    # Through str so that a rate written unquoted in the YAML cannot arrive as
                    # a float and lose cents on the way to a four-figure total.
                    rate=Decimal(str(entry["reservation_rate_usd_per_hour"])),
                    profile=None if entry.get("profile") is None else str(entry["profile"]),
                )
            )
        except (KeyError, ValueError) as exc:
            raise UnreadableBlockMenuError(f"{path} holds an unusable entry {entry!r}: {exc}")
    return tuple(blocks)


def provisioned_profiles(path: Path) -> dict[str, bool]:
    """Which compute profiles the catalog prices, and whether a queue can place each.

    Read as plain YAML rather than through ``WorkloadCatalog`` because this needs one field off
    each profile and loading the contract model would drag the whole workload half of the file
    into a tool that has no opinion about workloads.
    """
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=SafeUniqueKeyLoader)
    profiles = document.get("compute_profiles") if isinstance(document, dict) else None
    if not isinstance(profiles, list):
        raise UnreadableBlockMenuError(f"{path} lists no compute profiles")
    return {
        str(profile["name"]): bool(profile.get("provisioned"))
        for profile in profiles
        if isinstance(profile, dict) and "name" in profile
    }


def duration_refusal(days: int) -> str | None:
    """Why AWS will not sell this many days, or nothing where it will.

    Checked here rather than left to the purchase console because the answer changes what a
    buyer asks the researcher for. Somebody who needs eighteen days is buying twenty-one, and
    finding that out while comparing offerings is finding it out three steps too late.
    """
    if days < 1:
        return "a block is at least one whole day; there is no hourly block"
    if days > MAXIMUM_DAYS:
        return f"{days} days is beyond the {MAXIMUM_DAYS}-day maximum"
    if days <= MAXIMUM_SINGLE_DAYS:
        return None
    if days % WEEKLY_STEP_DAYS:
        nearest = ((days // WEEKLY_STEP_DAYS) + 1) * WEEKLY_STEP_DAYS
        return (
            f"beyond {MAXIMUM_SINGLE_DAYS} days AWS sells only multiples of "
            f"{WEEKLY_STEP_DAYS}, so {days} is not purchasable and {nearest} is the next one up"
        )
    return None


def usable_hours(days: int) -> Decimal:
    """The hours a job can actually run for, which is not the hours bought.

    AWS starts terminating instances half an hour before the window ends. A plan built on the
    round number overruns by exactly that much, and the overrun lands at the end of the run,
    which is where the checkpoint everybody cares about was going to be written.
    """
    return Decimal(days * 24) - (Decimal(RECLAIM_MINUTES) / Decimal(60))


def blocks_that_fit(menu: tuple[Block, ...], memory_gb: int) -> tuple[Block, ...]:
    """Every block whose device memory holds the figure, cheapest first."""
    return tuple(
        sorted(
            (block for block in menu if block.device_memory_gb >= memory_gb),
            key=lambda block: block.rate,
        )
    )


def what_is_missing(block: Block, provisioned: dict[str, bool]) -> tuple[str, ...]:
    """What has to happen before a submission naming this machine can be placed.

    Empty means the shape is priced and has a queue today, which before a purchase is true
    only of ``gpu-8xa100``. Everything else carries a list, and the list being visible now is
    the whole point of running this before buying rather than after.
    """
    if block.profile is None:
        return (
            (
                f"config/workload-catalog.yaml prices no profile for {block.instance_type}, so "
                "one has to be added, with an accelerators.yaml row and a CONTAINER_SHAPES entry"
            ),
            (
                "the container memory ceiling has to be read off a host that has run this type; "
                "asking for too much is refused as MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT"
            ),
        )
    if not provisioned.get(block.profile, False):
        return (
            (
                f"{block.profile} is priced and carries provisioned: false, so edullm check "
                "refuses it with unprovisioned_compute_profile until the block is deployed"
            ),
            (
                f"restore the {block.profile} row in config/execution-targets.yaml and flip "
                "provisioned to true once the reservation exists"
            ),
        )
    return ()


def obtainable_today(block: Block, placement: dict[str, tuple[str, str | None]]) -> str | None:
    """Why this machine may not need buying at all, or nothing where it does.

    **THE MOST EXPENSIVE MISTAKE THIS TOOL CAN CATCH, AND IT LOOKS LIKE THE SAFE CHOICE.** A
    shape a queue already supplies is one a block buys the *wait* for rather than the machine,
    and the wait is measured in tens of minutes against a purchase measured in hundreds of
    dollars. ``gpu-8xa100`` is the live case: fourteen nodes over three days and nothing ever
    cancelled for want of capacity, which is a fact a buyer comparing rates has no reason to
    know and every reason to want.

    Read out of ``config/capacity.yaml`` rather than restated here, so a shape that goes dry
    stops carrying this line without anybody editing this file.
    """
    if block.profile is None:
        return None
    found = placement.get(block.profile)
    if found is None:
        return None
    places, wait = found
    if places == PLACES_UNRELIABLY:
        return None
    if wait:
        return f"this shape already places without a block. {wait}"
    return (
        "this shape already places without a block, reliably, so a reservation buys nothing "
        "the queue does not already give you"
    )


def describe(
    block: Block,
    *,
    days: int,
    provisioned: dict[str, bool],
    placement: dict[str, tuple[str, str | None]],
) -> str:
    """One block as the paragraph a buyer reads before committing to it."""
    total = block.rate * Decimal(days * 24)
    lines = [
        (
            f"  {block.instance_type}  {block.devices} x {block.device}, "
            f"{block.device_memory_gb} GB"
        ),
        (
            f"    ${block.rate}/hour published, about ${total:.2f} for {days} day"
            f"{'s' if days != 1 else ''} at that rate"
        ),
    ]
    already = obtainable_today(block, placement)
    if already is not None:
        lines.append(f"    DO NOT BUY THIS: {already}")
    missing = what_is_missing(block, provisioned)
    if missing:
        for item in missing:
            lines.append(f"    - {item}")
    elif already is None:
        lines.append(f"    ready: {block.profile} is priced and has a queue behind it")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Named so tests can import and read it, as the workflow tools are."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--memory-gb",
        required=True,
        type=int,
        help="peak GPU memory the run needs, off the ask form. Nothing here can check it.",
    )
    parser.add_argument(
        "--days",
        required=True,
        type=int,
        help="whole days. 1-14, then multiples of 7 up to 182. There is no hourly block.",
    )
    parser.add_argument(
        "--menu",
        type=Path,
        default=PROJECT_ROOT / "config" / "capacity-blocks.yaml",
        help="the reviewed block menu to price against",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=PROJECT_ROOT / "config" / "workload-catalog.yaml",
        help="the reviewed catalog, read for which profiles have a queue",
    )
    parser.add_argument(
        "--capacity",
        type=Path,
        default=PROJECT_ROOT / "config" / "capacity.yaml",
        help="the recorded placement verdicts, read for shapes that need no block at all",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    refusal = duration_refusal(arguments.days)
    if refusal is not None:
        print(f"that duration cannot be bought: {refusal}", file=sys.stderr)
        return EXIT_UNUSABLE
    if arguments.memory_gb < 1:
        print("--memory-gb has to be a positive number of gigabytes", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        menu = read_block_menu(arguments.menu)
        provisioned = provisioned_profiles(arguments.catalog)
        placement = {
            record.profile: (record.places, record.wait)
            for record in read_capacity(arguments.capacity)
        }
    except (OSError, ValueError) as exc:
        print(f"the reviewed configuration could not be read: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    fitting = blocks_that_fit(menu, arguments.memory_gb)
    if not fitting:
        largest = max(menu, key=lambda block: block.device_memory_gb)
        print(
            f"nothing buyable in this region holds {arguments.memory_gb} GB. The largest is "
            f"{largest.instance_type} at {largest.device_memory_gb} GB, so this run has to be "
            "split across nodes -- and multi-node is not built here.",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE

    hours = usable_hours(arguments.days)
    print(
        f"{arguments.memory_gb} GB for {arguments.days} day"
        f"{'s' if arguments.days != 1 else ''}: {hours} usable hours, because AWS reclaims the "
        f"machines {RECLAIM_MINUTES} minutes before the window ends."
    )
    print()
    print(f"Cheapest that fits, and {len(fitting) - 1} larger:")
    for block in fitting:
        print(
            describe(
                block,
                days=arguments.days,
                provisioned=provisioned,
                placement=placement,
            )
        )

    print()
    print(
        "Published rates are AWS's planning guide and move with supply and demand. The price "
        "that binds is on the offering, which describe-capacity-block-offerings returns and "
        "which you see before you commit. Blocks cannot be cancelled."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
