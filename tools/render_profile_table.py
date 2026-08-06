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

WHAT THIS TABLE CANNOT SAY, AND WHY THAT MATTERS BEYOND THIS FILE. The guide's tables carry a
`Device` column reading `4 x A10G` and a `Memory` column reading `96 GB`. Neither figure is in
any configuration file. The card behind an instance type and the memory on that card live in
the guide's prose and nowhere a machine can read, so this renderer cannot reproduce those two
columns and does not pretend to. That gap is not local to this table. `The size-to-node
arithmetic, and the model-fit refusal` is blocked on exactly the same missing thing, because
nothing can work out whether a model fits on a shape without knowing what memory the shape
has. One reviewed table of accelerator and memory per profile would unblock both, and writing
it means somebody deciding those numbers rather than a tool deriving them.

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

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog

__all__ = [
    "CATALOGUE_PATH",
    "build_parser",
    "main",
    "profiles_missing_from",
    "render_table",
]

PROJECT_ROOT: Final = Path(__file__).resolve().parent.parent

CATALOGUE_PATH: Final = "config/workload-catalog.yaml"

#: Said as a word rather than as `True`, because the column is read by a person deciding
#: whether to ask for the thing.
_CAN_BE_STARTED: Final = {True: "yes", False: "no"}


def render_table(profiles: Sequence[ComputeProfile]) -> str:
    """Every profile in the catalogue, in the catalogue's order, as one markdown table.

    The sentence under the table names the unprovisioned profiles again. A `no` in a column is
    easy to read past on a wide table, and the cost of reading past this one is a queue wait
    and somebody's approval spent on a refusal.
    """
    rows = [
        "| Compute profile | Instance type | Nodes | Rate | Provisioned |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        f"| `{profile.name}` | `{profile.instance_type}` | {profile.nodes} "
        f"| ${profile.hourly_rate_usd}/hr | {_CAN_BE_STARTED[profile.provisioned]} |"
        for profile in profiles
    )

    unprovisioned = [profile.name for profile in profiles if not profile.provisioned]
    if unprovisioned:
        named = ", ".join(f"`{name}`" for name in unprovisioned)
        rows.append("")
        rows.append(
            f"{named} are in the catalogue and cannot be started today. Asking for one is "
            f"refused at admission, after the wait and after the approval."
        )
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
        print(render_table(profiles), end="")
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
