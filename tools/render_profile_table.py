"""The compute profiles as a markdown table, written from the catalogue rather than by hand.

WHAT THIS IS FOR. `guides/olmo-core.md` carries two tables of compute profiles and a person
typed both. They have drifted from `config/workload-catalog.yaml`, which is the reviewed
source, and the drift is not the harmless kind. The catalogue holds seventeen profiles and the
guide's tables name fourteen. Worse in the other direction, the guide lists `gpu-8xh100` at
its hourly rate beside nine shapes that can actually be started, and the catalogue records it
as unprovisioned. A reader picks it, waits for an approval, and is refused by admission for a
reason the page they read never mentioned.

WHY `provisioned` IS A COLUMN HERE AND IS IN NO HAND TABLE. It is the one field that decides
whether a name is an offer or a listing, it is the field a person copying a table forgets, and
it is already in the catalogue. Nothing else on this table is new information. Putting it
beside the rate is the whole point of generating the thing.

THE `Device` AND `Memory` COLUMNS ARE GENERATED NOW, AND THEY WERE THE HOLE THIS FILE OPENED
WITH. What stood here said the card behind an instance type and the memory on that card lived
in the guide's prose and nowhere a machine could read, so this renderer could not reproduce
them. `config/accelerators.yaml` is that table: seventeen rows read out of
`aws ec2 describe-instance-types` against this account on 2026-08-06, not one of them copied
from a specification sheet. `edullm_platform.accelerators` reads it and these two columns come
from it.

THEY ARE IN MIB WHERE THE GUIDE SAID `96 GB`, AND THE DIFFERENCE IS THE POINT RATHER THAN A
FORMATTING PREFERENCE. `4 x A10G` is 91,552 MiB, which is 96.0 GB and 89.4 GiB. Those all
agree; what does not agree is a reader who takes the round number for a GiB allowance and sizes
a batch 1.65 GiB per card over what the machine has. The unit the measurement arrived in is the
one that cannot be read two ways, and it is the one `nvidia-smi` prints when the run dies.

WHAT THE COLUMNS STILL DO NOT DO IS SAY WHETHER A MODEL FITS. That needs the model's parameter
count, which no submission carries and this repository records nowhere on the path to one --
`edullm_platform.accelerators` argues that at length and `tests/test_accelerators.py` holds the
tripwire for the day it changes. This table puts the memory in front of the person choosing and
leaves the arithmetic to them, which is all it is entitled to do.

THE ORDER IS THE CATALOGUE'S ORDER. It groups by card family and somebody chose it in review.
Sorting by rate here would be this tool holding an opinion the source does not, and it would
make the output move whenever a price did.

THE RATE IS NOT ROUNDED. The guide rounds to the cent, which is a choice about presentation
that belongs to whoever writes the page rather than to the thing that reads the file, and a
renderer that quietly rounds is one a reader cannot check against its source. What does happen
is that a trailing zero goes, because the catalogue's `0.5260` is parsed as a decimal before
this sees it and `0.526` is the same number.

    uv run --frozen python tools/render_profile_table.py
    uv run --frozen python tools/render_profile_table.py --against guides/olmo-core.md

The second form is a check and it exits 1 when it finds something, because a check that cannot
fail is not a check. Nothing runs it in CI yet. Wiring it into a test is a decision for
whoever owns the page, and it goes red on the day it lands until the page is fixed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from edullm_platform.accelerators import (
    AcceleratorRecord,
    device_said,
    memory_said,
    read_accelerators,
    record_for,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog

__all__ = [
    "ACCELERATORS_PATH",
    "CATALOGUE_PATH",
    "build_parser",
    "main",
    "profiles_missing_from",
    "render_table",
]

PROJECT_ROOT: Final = Path(__file__).resolve().parent.parent

CATALOGUE_PATH: Final = "config/workload-catalog.yaml"

ACCELERATORS_PATH: Final = "config/accelerators.yaml"

#: Said as a word rather than as `True`, because the column is read by a person deciding
#: whether to ask for the thing.
_CAN_BE_STARTED: Final = {True: "yes", False: "no"}

#: What both accelerator columns say for a profile `config/accelerators.yaml` has no row for.
#: Loud rather than blank, and loud rather than absent: the two files are held level by
#: `tests/test_accelerators.py`, so this can only appear when somebody prices a shape and does
#: not measure it, and a reader deciding on a machine should see that rather than an empty cell
#: they will read as a rendering fault.
_NOT_RECORDED: Final = "not recorded"

#: Printed under the table wherever a memory figure appears, because a number in a unit the
#: reader converts wrongly is worse than no number. The A10G row says 22,888 MiB and the card is
#: sold as 24 GB; both are right, and a batch sized against 24 GiB is 1.65 GiB over the card. The
#: sentence also says what the figure excludes, since it is a ceiling nothing can reach rather
#: than an allowance -- the CUDA context and the allocator take their share of it first.
_WHAT_THE_MEMORY_COLUMN_IS: Final = (
    "Memory is the total across the shape's devices, read from "
    "`aws ec2 describe-instance-types` and recorded in `config/accelerators.yaml`. It is MiB "
    "rather than the GB a card is sold as, because they differ by 7% and the rounding is in "
    "the direction that overcommits: a 24 GB A10G is 22,888 MiB. It is what the hardware "
    "carries and not what a process can allocate -- the CUDA context, the allocator and the "
    "framework come out of it before your model does."
)


def render_table(
    profiles: Sequence[ComputeProfile],
    accelerators: Sequence[AcceleratorRecord] = (),
) -> str:
    """Every profile in the catalogue, in the catalogue's order, as one markdown table.

    The sentence under the table names the unprovisioned profiles again. A `no` in a column is
    easy to read past on a wide table, and the cost of reading past this one is a queue wait
    and somebody's approval spent on a refusal.

    `accelerators` defaults to nothing so that a caller with only a catalogue still gets the
    five columns this table always had, with the two new ones saying plainly that no
    measurement was supplied. The alternative -- omitting the columns when the argument is
    empty -- makes the table's own shape depend on what the caller happened to pass, which is
    the version a reader cannot check against its source.
    """
    rows = [
        "| Compute profile | Instance type | Device | Memory | Nodes | Rate | Provisioned |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for profile in profiles:
        record = record_for(profile.name, accelerators=accelerators)
        device = _NOT_RECORDED if record is None else device_said(record)
        memory = _NOT_RECORDED if record is None else memory_said(record)
        rows.append(
            f"| `{profile.name}` | `{profile.instance_type}` | {device} | {memory} "
            f"| {profile.nodes} | ${profile.hourly_rate_usd}/hr "
            f"| {_CAN_BE_STARTED[profile.provisioned]} |"
        )

    unprovisioned = [profile.name for profile in profiles if not profile.provisioned]
    if unprovisioned:
        named = ", ".join(f"`{name}`" for name in unprovisioned)
        rows.append("")
        rows.append(
            f"{named} are in the catalogue and cannot be started today. Asking for one is "
            f"refused at admission, after the wait and after the approval."
        )
    if any(record_for(profile.name, accelerators=accelerators) for profile in profiles):
        rows.append("")
        rows.append(_WHAT_THE_MEMORY_COLUMN_IS)
    return "\n".join(rows) + "\n"


def profiles_missing_from(text: str, profiles: Sequence[ComputeProfile]) -> tuple[str, ...]:
    """Catalogue profiles the given text never names.

    A plain substring test and deliberately nothing cleverer. A profile name is a slug that
    appears nowhere else, so a page that mentions it at all has almost certainly not forgotten
    it, and a page that never says the word cannot have told anybody about it. The opposite
    direction, a page naming a profile the catalogue dropped, is a different failure and this
    does not look for it.
    """
    return tuple(profile.name for profile in profiles if profile.name not in text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--catalogue",
        type=Path,
        default=PROJECT_ROOT / CATALOGUE_PATH,
        help="the workload catalogue to read; defaults to the one in this tree",
    )
    parser.add_argument(
        "--accelerators",
        type=Path,
        default=PROJECT_ROOT / ACCELERATORS_PATH,
        help="the measured card and memory table; defaults to the one in this tree",
    )
    parser.add_argument(
        "--against",
        type=Path,
        help="name the catalogue profiles this file never mentions, and exit 1 if any do not",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    catalogue = load_yaml(options.catalogue, WorkloadCatalog)
    profiles = catalogue.compute_profiles

    if options.against is None:
        print(render_table(profiles, read_accelerators(options.accelerators)), end="")
        return 0

    try:
        text = options.against.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read {options.against}: {exc}", file=sys.stderr)
        return 2

    missing = profiles_missing_from(text, profiles)
    if not missing:
        print(f"{options.against} names all {len(profiles)} profiles in the catalogue.")
        return 0

    print(f"{options.against} never names {len(missing)} of {len(profiles)} profiles:")
    for name in missing:
        print(f"  {name}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
