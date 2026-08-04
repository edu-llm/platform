"""Whether a shape a submission asks for is one this account has been able to get.

**The failure this exists to surface has no error attached to it.** A compute profile can be
priced in ``config/workload-catalog.yaml``, provisioned with a compute environment and backed
by a queue in ``config/execution-targets.yaml`` and still never start, because EC2 has to have
the capacity to sell. The way that presents is a job sitting in ``RUNNABLE`` with nothing
written anywhere, which is indistinguishable from a job that is merely queued -- so the
researcher finds out by waiting, and finds out hours later.

``config/capacity.yaml`` has recorded the answer for every priced shape since it was written,
including the substitution to offer for two of the four that do not place. Nothing read it.
This is what reads it, and the submission path says so at the moment of choosing rather than
leaving it to be discovered.

**A WARNING AND NEVER A REFUSAL, WHICH IS THE ONE DESIGN DECISION HERE THAT COULD GO EITHER
WAY.** Every other guard on the submission path refuses, and this one deliberately does not.
``config/capacity.yaml``'s own header is explicit about what it is: the account's experience
of which pools it has waited on, arrived at by having waited, because "the measurement is 'did
a job start' and the instrument costs an instance". Refusing a submission on a judgement call
recorded from memory would make an unmeasured file into a gate, and the first cost of that
would be a shape that has quietly become available and that nobody can ask for. So the shape
stays submittable, the sentence goes in front of the person, and the decision stays theirs.

The message says no more than the file claims. It says the shape *may* not place rather than
that it will not, it names the file so the reasoning behind a particular entry can be read
where that reasoning lives, and where no substitute is recorded it says the absence is an
answer -- because for ``gpu-1xh100`` and ``gpu-8xh100`` it is one. Nothing else in the catalog
holds 80 GB on one device or 640 GB on one node, and the catalog records what happened the
last time somebody offered a smaller card as "the closest thing available".

Read with ``yaml`` rather than through a contract model, which is the choice
``tests/test_capacity.py`` already made and recorded: placement belongs on ``ComputeProfile``
beside ``provisioned``, and it is not there because that model's structural digest is recorded
in five committed proof bundles. A pydantic model here would put a second, unversioned schema
in the tree for a fact that has a home waiting for it -- and it would move the contract
inventory those five bundles record, which is a bundle regeneration for a config reader.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from edullm_platform.config import SafeUniqueKeyLoader

__all__ = [
    "CAPACITY_FILENAME",
    "PLACES_RELIABLY",
    "PLACES_UNRELIABLY",
    "PlacementRecord",
    "UnreadableCapacityError",
    "placement_warning",
    "read_capacity",
]

#: Where the answers live, relative to the reviewed configuration directory.
CAPACITY_FILENAME: Final = "capacity.yaml"

#: The two answers the file declares. ``unknown`` is deliberately not one of them: every
#: priced profile appears exactly once, so promoting a shape is an edit there as well, and a
#: file listing only the scarce ones would be a denylist that assumes the next promotion
#: places until somebody waits four hours to find out otherwise.
PLACES_RELIABLY: Final = "reliably"
PLACES_UNRELIABLY: Final = "unreliably"


class UnreadableCapacityError(ValueError):
    """``config/capacity.yaml`` is not a document this can act on.

    Raised rather than defaulted to "everything places", because that default is the one
    that fails silently: a file that stopped parsing would take the warning with it and
    leave every submission reading exactly as it did before this module existed. The
    compile step already treats unreadable reviewed configuration as an unusable input
    rather than as a refusal, and this joins that set.
    """


@dataclass(frozen=True)
class PlacementRecord:
    """One profile's recorded answer, and what to offer when the answer is no."""

    profile: str
    places: str
    offer_instead: str | None = None


def read_capacity(path: Path) -> tuple[PlacementRecord, ...]:
    """Read the recorded placement answers, refusing anything that is not one.

    ``SafeUniqueKeyLoader`` rather than ``yaml.safe_load``, so that two entries for one
    profile is an error here as it is for every other reviewed file. Two answers for one
    shape would otherwise resolve to whichever was written second, which is the sort of
    thing a reviewer reading the diff would not see.
    """
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=SafeUniqueKeyLoader)
    if not isinstance(document, dict):
        raise UnreadableCapacityError(f"{path} is not a top-level mapping")
    entries = document.get("profiles")
    if not isinstance(entries, list):
        raise UnreadableCapacityError(f"{path} lists no profiles")
    records: list[PlacementRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnreadableCapacityError(f"{path} holds an entry that is not a mapping")
        profile = entry.get("profile")
        places = entry.get("places")
        substitute = entry.get("offer_instead")
        if not isinstance(profile, str) or places not in (PLACES_RELIABLY, PLACES_UNRELIABLY):
            raise UnreadableCapacityError(
                f"{path} holds an entry that does not name a profile and one of "
                f"{PLACES_RELIABLY!r} or {PLACES_UNRELIABLY!r}: {entry!r}"
            )
        if substitute is not None and not isinstance(substitute, str):
            raise UnreadableCapacityError(
                f"{path}: the substitute offered for {profile} is not a profile name"
            )
        records.append(
            PlacementRecord(profile=profile, places=places, offer_instead=substitute)
        )
    return tuple(records)


#: Said the same way in every branch, because it is the same fact and the thing a submitter
#: most needs to recognise later: there is no error to go looking for.
_WHAT_IT_LOOKS_LIKE: Final = (
    "a shape that cannot be placed does not fail -- the job sits in RUNNABLE with nothing "
    "written anywhere, which looks exactly like a job that is merely queued."
)

#: The limit of what the file claims, said where the warning is read rather than left in the
#: file's header. A reader who takes this for a measurement will read more into it than the
#: account can support, and the honest version is short enough to print every time.
_WHAT_THIS_IS_NOT: Final = (
    f"This is a warning and not a refusal: `config/{CAPACITY_FILENAME}` records what this "
    "account has experienced rather than anything EC2 has told it, so the run was submitted "
    "as filled in and may well start."
)


def placement_warning(compute_profile: str, *, capacity: Sequence[PlacementRecord]) -> str | None:
    """What a submitter is owed about the shape they asked for, or ``None`` if nothing.

    ``None`` for every shape that places, which is eleven of the fifteen. A line printed on
    every submission is a line readers learn to skip, and this one has to survive being read
    by somebody who has submitted forty runs -- the same reason
    :func:`~edullm_platform.launchers.waived_launch_check_note` returns nothing when the
    waiver it describes did not change the outcome.
    """
    recorded = next(
        (record for record in capacity if record.profile == compute_profile), None
    )
    if recorded is None:
        return (
            f"**No placement answer is recorded for `{compute_profile}`.** Every priced shape "
            f"is meant to appear in `config/{CAPACITY_FILENAME}` exactly once, so this one is "
            "either newly promoted or was left out. Whether it places is therefore unknown "
            f"rather than fine: {_WHAT_IT_LOOKS_LIKE} Add an entry for it in a pull request "
            "against this repository."
        )
    if recorded.places != PLACES_UNRELIABLY:
        return None
    if recorded.offer_instead is not None:
        offer = (
            f"`{recorded.offer_instead}` is recorded as the shape to take instead, and "
            f"`config/{CAPACITY_FILENAME}` says beside that entry what the swap costs and "
            "buys. Taking it means re-submitting with that profile; nothing here changes the "
            "submission you filled in."
        )
    else:
        offer = (
            "No substitute is recorded, and the absence is an answer rather than an omission: "
            "nothing else in the catalog holds what this shape holds, so there is no smaller "
            f"machine to offer that would run the same recipe. `config/{CAPACITY_FILENAME}` "
            "says beside that entry what the route to this shape is instead."
        )
    return (
        f"**`{compute_profile}` may not place.** This account has waited on it, and "
        f"{_WHAT_IT_LOOKS_LIKE} {offer} {_WHAT_THIS_IS_NOT}"
    )
