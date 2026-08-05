"""Whether a shape a submission asks for is one this account has been able to get.

**The failure this exists to surface has no error attached to it.** A compute profile can be
priced in ``config/workload-catalog.yaml``, provisioned with a compute environment and backed
by a queue in ``config/execution-targets.yaml`` and still never start, because EC2 has to have
the capacity to sell. The way that presents is a job sitting in ``RUNNABLE`` with nothing
written anywhere, which is indistinguishable from a job that is merely queued -- so the
researcher finds out by waiting, and finds out hours later.

``config/capacity.yaml`` has recorded the answer for every priced shape since it was written,
and nothing read it. This is what reads it, and the submission path says so at the moment of
choosing rather than leaving it to be discovered.

**A WARNING AND NEVER A REFUSAL, WHICH IS THE ONE DESIGN DECISION HERE THAT COULD GO EITHER
WAY.** Every other guard on the submission path refuses, and this one deliberately does not.
What ``config/capacity.yaml`` holds is what this account was able to get on the days somebody
looked: a reading of what EC2 had to sell in a window that has closed rather than a promise
about the next one. Refusing a submission on a dated reading would make it a gate, and the
first cost of that would be a shape that has quietly become available and that nobody can ask
for. Six entries have been wrong in that direction already and two more were found the day
after the first six were corrected, so the cost is measured rather than hypothetical. The
shape stays submittable, the sentence goes in front of the person, and the decision stays
theirs.

The message says no more than the file claims. It says the shape *may* not place rather than
that it will not, and it names the file so the reasoning behind a particular entry can be read
where that reasoning lives -- which, for the shapes whose only route is a Capacity Block, is
the one place that route is written down.

**THERE ARE TWO WARNINGS NOW, AND THE SECOND ONE EXISTS BECAUSE THE FIRST WAS BEING PRINTED
OVER A POOL THAT WORKS.** An instant probe and a queued autoscaling group ask EC2 different
questions. Five of the pools this module warned "may not place" about had in fact supplied
nodes -- ``gpu-8xa100`` fourteen of them, and ``gpu-8xa10g`` three that were alive together
while the warning was still going out -- and what those submitters needed was the wait, not
a rumour that the machine might never come. A submitter told to expect an hour plans a day
around it; one told the shape may not place takes their work somewhere else. So ``places:
after_a_wait`` warns about the wait and names it, and ``places: unreliably`` keeps saying
what it always said, for the five pools where it is still true.

**AND THE REMAINING REFUSALS ARE NOT ALL WORTH THE SAME, WHICH IS WHAT ``measured_by`` IS
FOR.** Two of those five were measured by a queue that ground against the pool for
thirty-seven hours and never got an instance; three were measured by one instant probe and
nothing else, because no job has ever been submitted to their queues. Those are different
claims and the reader has to be able to tell them apart, because the difference decides
whether to believe the warning: a refused probe is the instrument that has now been wrong
eight times, and every one of those eight was in the direction of saying a machine could not
be had when it could. So the sentence for a probe-only refusal says which instrument
answered and how far it can be trusted, and the sentence for a queue-measured one does not
hedge, because nothing about it needs hedging.

**IT OFFERS NOTHING IN PLACE OF THE SHAPE, AND THAT IS A POSITION RATHER THAN A GAP.** An
earlier version of this module named a substitute wherever the file recorded one. The file
recorded two; both were re-measured on 2026-08-04, both turned out to point at machines this
account has never obtained, and both were withdrawn on the rule that a third of the device
memory removed is a changed recipe the submitter declares rather than a substitution the
platform makes for them. Ten of the seventeen priced shapes are worth warning about and not
one of them has a substitute, so there is no branch here that could name one -- and a branch
kept alive against a fixture the shipped file can never produce would be a check unable to
fail.

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
    "MEASURED_BY_A_PROBE",
    "MEASURED_BY_A_QUEUE",
    "MEASUREMENT_INSTRUMENTS",
    "PLACEMENT_ANSWERS",
    "PLACES_AFTER_A_WAIT",
    "PLACES_RELIABLY",
    "PLACES_UNRELIABLY",
    "PlacementRecord",
    "UnreadableCapacityError",
    "placement_warning",
    "read_capacity",
]

#: Where the answers live, relative to the reviewed configuration directory.
CAPACITY_FILENAME: Final = "capacity.yaml"

#: The three answers the file declares. ``unknown`` is deliberately not one of them: every
#: priced profile appears exactly once, so promoting a shape is an edit there as well, and a
#: file listing only the scarce ones would be a denylist that assumes the next promotion
#: places until somebody waits four hours to find out otherwise.
PLACES_RELIABLY: Final = "reliably"
PLACES_UNRELIABLY: Final = "unreliably"

#: THE THIRD ANSWER, AND IT EXISTS BECAUSE THE OTHER TWO BOTH LIE ABOUT THE SAME POOL.
#: ``config/capacity.yaml``'s answers came from ``create-fleet --type instant``, which asks
#: whether an instance is free at one second. A compute environment with a job queued asks
#: the same pools again every tenth of a second until one is free, and gets a different
#: answer: the ``gpu-8xa100`` environment took fourteen nodes out of 5,024 consecutive
#: ``InsufficientInstanceCapacity`` refusals. Recording that as ``unreliably`` prints "may
#: not place" over a pool that is running jobs, and recording it as ``reliably`` prints
#: nothing over a median wait of an hour. Neither is what a submitter needs, so this says
#: the shape arrives and the wait is the price.
PLACES_AFTER_A_WAIT: Final = "after_a_wait"

#: Every answer :func:`read_capacity` will accept. A fourth is a change here, in the file's
#: header, and in :func:`placement_warning` together -- which is the point, because a
#: verdict with no branch behind it reads as a promise the submission path does not keep.
PLACEMENT_ANSWERS: Final = frozenset(
    {PLACES_RELIABLY, PLACES_UNRELIABLY, PLACES_AFTER_A_WAIT}
)

#: WHICH INSTRUMENT ANSWERED, WHICH IS A SEPARATE FACT FROM WHAT IT ANSWERED AND HAD TO STOP
#: BEING FOLDED INTO IT. ``places`` records a verdict; these record how it was reached. They
#: are orthogonal because every verdict can be reached either way, and keeping them apart is
#: what lets ``unreliably`` mean one thing when a queue established it and a weaker thing when
#: a probe did. A fourth ``places`` value would have collapsed the two back together and given
#: :func:`placement_warning` a branch that was really two questions.
MEASURED_BY_A_QUEUE: Final = "queue"
MEASURED_BY_A_PROBE: Final = "probe"

#: Both instruments, and a third is a change here and in the file's header together.
#:
#: The asymmetry between them is the reason the field exists. A probe that *obtained* the
#: machine settles the question, because the account demonstrably held one. A probe that was
#: refused settles nothing beyond that second: ``create-fleet --type instant`` asks once, an
#: autoscaling group behind a queued job asks every tenth of a second until the queue's cancel
#: fires, and every time the two have disagreed the queue has been right. So ``probe`` beside
#: :data:`PLACES_UNRELIABLY` is the weakest statement this file can make.
MEASUREMENT_INSTRUMENTS: Final = frozenset({MEASURED_BY_A_QUEUE, MEASURED_BY_A_PROBE})


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
    """One profile's recorded placement answer, how it was reached, and what the shape cost.

    ``wait`` is the sentence :data:`PLACES_AFTER_A_WAIT` entries carry and nothing else
    does. It is prose in the reviewed file rather than a pair of numbers here because what
    is worth saying differs per pool: ``gpu-8xa100`` has never failed to place a queued
    run and ``gpu-4xl40s`` has had two given up on by hand, and no schema this module
    could impose would carry the second fact. ``gpu-1xl40s`` is the case that settles the
    argument: a shape whose nodes have arrived but which no submitted run has ever queued
    for has no median to quote, and its sentence says so rather than inventing one.

    ``measured_by`` has no default, deliberately. The failure this whole file exists to
    undo was a default nobody chose: a shape with no experience attached was written down
    as placing reliably, which was a denylist applied without anybody stating it. An
    instrument that could be omitted would reintroduce exactly that, one field over.
    """

    profile: str
    places: str
    measured_by: str
    wait: str | None = None


def read_capacity(path: Path) -> tuple[PlacementRecord, ...]:
    """Read the recorded placement answers, refusing anything that is not one.

    ``SafeUniqueKeyLoader`` rather than ``yaml.safe_load``, so that two entries for one
    profile is an error here as it is for every other reviewed file. Two answers for one
    shape would otherwise resolve to whichever was written second, which is the sort of
    thing a reviewer reading the diff would not see.

    ``wait`` is required on an ``after_a_wait`` entry and refused on the other two, which is
    the same failure guarded from both directions. Without it the warning for that verdict
    would have nothing to say but "expect a wait", which a submitter cannot plan against;
    with it on a ``reliably`` entry it would be a measurement nothing prints.

    ``measured_by`` is required on every entry and there is no default, for the reason
    :class:`PlacementRecord` gives. An ``after_a_wait`` entry must additionally record
    :data:`MEASURED_BY_A_QUEUE`, because a wait is a thing only a queue can have observed:
    ``create-fleet --type instant`` returns in one call and cannot watch a job sit in
    ``RUNNABLE``, so that combination would be a measurement no instrument here could have
    taken. It is the one cross-field rule worth enforcing, and it is the one that stops a
    plausible-looking sentence being attached to a shape nothing ever queued for.
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
        if not isinstance(profile, str) or places not in PLACEMENT_ANSWERS:
            raise UnreadableCapacityError(
                f"{path} holds an entry that does not name a profile and one of "
                f"{sorted(PLACEMENT_ANSWERS)}: {entry!r}"
            )
        measured_by = entry.get("measured_by")
        if measured_by not in MEASUREMENT_INSTRUMENTS:
            raise UnreadableCapacityError(
                f"{path} records {profile!r} and does not say which of "
                f"{sorted(MEASUREMENT_INSTRUMENTS)} answered for it, so a reader cannot tell "
                "a verdict a queue measured from one an instant probe guessed at"
            )
        wait = entry.get("wait")
        if places == PLACES_AFTER_A_WAIT:
            if not isinstance(wait, str) or not wait.strip():
                raise UnreadableCapacityError(
                    f"{path} records {profile!r} as {PLACES_AFTER_A_WAIT!r} and gives no "
                    "'wait', so the warning for it could say only that there is one"
                )
            if measured_by != MEASURED_BY_A_QUEUE:
                raise UnreadableCapacityError(
                    f"{path} records {profile!r} as {PLACES_AFTER_A_WAIT!r} and says "
                    f"{measured_by!r} measured it; a wait is what a queued job saw, and "
                    f"only {MEASURED_BY_A_QUEUE!r} can have seen one"
                )
        elif wait is not None:
            raise UnreadableCapacityError(
                f"{path} gives {profile!r} a 'wait' and records it as {places!r}, which is "
                f"a measurement nothing prints; only {PLACES_AFTER_A_WAIT!r} entries carry one"
            )
        records.append(
            PlacementRecord(
                profile=profile, places=places, measured_by=measured_by, wait=wait
            )
        )
    return tuple(records)


#: Said the same way in every branch, because it is the same fact and the thing a submitter
#: most needs to recognise later: there is no error to go looking for.
_WHAT_IT_LOOKS_LIKE: Final = (
    "a shape that cannot be placed does not fail -- the job sits in RUNNABLE with nothing "
    "written anywhere, which looks exactly like a job that is merely queued."
)

#: The same fact from the other side, for a shape that *is* merely queued. A submitter who
#: has read the sentence above about a different shape will recognise the symptom and draw
#: the wrong conclusion from it, so the branch that expects a wait has to say plainly that
#: this one resolves itself.
_WHAT_A_WAIT_LOOKS_LIKE: Final = (
    "While it waits the job sits in RUNNABLE with nothing written anywhere, which looks "
    "exactly like a shape that will never place -- for this one it is the queue, and "
    "resubmitting restarts the wait rather than shortening it."
)

#: What the wait figures are and are not. They are what this account's own queue got, which
#: is a stronger claim than the instant probe makes and a weaker one than a service level:
#: EC2 owes nobody the next node in the time it supplied the last fourteen.
#:
#: CONDITIONAL ON A RANGE BEING QUOTED, BECAUSE NOT EVERY ENTRY CAN QUOTE ONE. This used to
#: end "the worst case above is the one worth being able to absorb", which reads as a
#: dangling reference on ``gpu-1xl40s``: two nodes have arrived for that shape and no
#: submitted run has ever queued for it, so its sentence gives one observation and no range
#: at all. An entry honest enough to say it cannot quote a median should not be followed by
#: a line telling the reader to go and look at one.
_WHAT_A_WAIT_IS_NOT: Final = (
    f"What is above is what this account's queue actually saw, recorded in "
    f"`config/{CAPACITY_FILENAME}`, and not a bound EC2 offers. Where a range is quoted, plan "
    "against the far end of it rather than the median, because that is the case you have to "
    "be able to absorb."
)

#: The limit of what the file claims, said where the warning is read rather than left in the
#: file's header. A reader who takes a dated probe for a standing guarantee will read more
#: into it than the account can support, and the honest version is short enough to print
#: every time.
_WHAT_THIS_IS_NOT: Final = (
    f"This is a warning and not a refusal: `config/{CAPACITY_FILENAME}` records what this "
    "account was able to get on the days it looked rather than what EC2 will sell today, so "
    "the run was submitted as filled in and may well start."
)

#: Where the reasoning for a particular entry lives. Said rather than summarised here,
#: because what is worth reading differs per shape -- for the two H100 profiles it is the
#: dated Capacity Block offerings, and nothing this function could generalise would carry
#: that.
_WHERE_THE_REASONING_IS: Final = (
    f"`config/{CAPACITY_FILENAME}` says beside that entry what was measured and what the "
    "route to this shape is instead, and choosing a smaller machine is a changed recipe "
    "for you to declare rather than one this platform substitutes on your behalf."
)

#: WHAT ESTABLISHED THE REFUSAL, AND THE TWO ARE NOT THE SAME STRENGTH OF CLAIM. Said in the
#: warning rather than left in the file, because the submitter is the person deciding whether
#: to believe it and they are not going to open the file to find out how it was reached.
_A_QUEUE_ASKED_AND_NEVER_GOT_ONE: Final = (
    "A queue asked for this shape repeatedly, in every zone it is offered in, and never "
    "obtained one, and"
)

#: The weak case, and it is the one that has been wrong. Naming the count is the point: a
#: submitter who knows the instrument has been overturned eight times can weigh submitting
#: anyway against taking the work elsewhere, and that is a judgement the platform should not
#: be making for them by printing the same flat sentence over both.
_ONLY_A_PROBE_HAS_ASKED: Final = (
    "No job has ever been submitted to this shape's queue, so the only thing that has asked "
    "for it is a single-instant pool probe. That instrument has been overturned eight times "
    "by a queue that kept asking, always in this direction, so treat this as untested rather "
    "than as settled: submitting is how it gets measured, and"
)


def placement_warning(compute_profile: str, *, capacity: Sequence[PlacementRecord]) -> str | None:
    """What a submitter is owed about the shape they asked for, or ``None`` if nothing.

    ``None`` for every shape that places promptly, which is seven of the seventeen. A line
    printed on every submission is a line readers learn to skip, and this one has to survive
    being read by somebody who has submitted forty runs -- the same reason
    :func:`~edullm_platform.launchers.waived_launch_check_note` returns nothing when the
    waiver it describes did not change the outcome.

    Ten of seventeen is a lot of warning, and it is the measurement rather than a threshold
    anybody picked. Narrowing it would mean saying nothing about a shape the probe could not
    obtain, which is the state this module was written to end.

    **THE FIVE ``after_a_wait`` SHAPES WARN AND ARE NOT COUNTED AS UNPLACEABLE, WHICH IS THE
    WHOLE OF THE DISTINCTION.** They keep their line because an hour is worth knowing before
    a day is planned around it. What they lose is the claim that the machine might never
    arrive, which was false of all five and was the sentence a submitter would have acted on.

    **AND THE FIVE THAT STILL SAY "MAY NOT PLACE" DO NOT ALL SAY IT ON THE SAME EVIDENCE.**
    Two were established by a queue that kept asking and never got a machine. Three were
    established by one instant probe, because nothing has ever been submitted to their
    queues, and that instrument's refusals are what the previous eight corrections to
    ``config/capacity.yaml`` overturned. A submitter deciding whether to take the shape
    anyway needs to know which of those they are reading, so the branch below says. What it
    does not do is soften the verdict: both are still "may not place", because the file
    still records that, and downgrading a warning on the strength of a hunch about the
    instrument would be this module deciding something the measurement has not.
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
    if recorded.places == PLACES_AFTER_A_WAIT:
        # ``read_capacity`` refuses this verdict without a wait, so the sentence is here.
        # Asserted rather than defaulted: a fallback would let a malformed entry print a
        # warning about a wait it could not name, which is the version of this a submitter
        # cannot act on.
        assert recorded.wait is not None
        return (
            f"**`{compute_profile}` places, and the wait is what to plan for.** "
            f"{recorded.wait} {_WHAT_A_WAIT_LOOKS_LIKE} {_WHAT_A_WAIT_IS_NOT}"
        )
    if recorded.places != PLACES_UNRELIABLY:
        return None
    # Which instrument produced the refusal, in the words that say how far it goes. The
    # probe arm is the longer of the two on purpose: a claim that needs qualifying is worse
    # than useless when the qualification is left out, and this is the arm that has been
    # wrong.
    how_it_is_known = (
        _A_QUEUE_ASKED_AND_NEVER_GOT_ONE
        if recorded.measured_by == MEASURED_BY_A_QUEUE
        else _ONLY_A_PROBE_HAS_ASKED
    )
    return (
        f"**`{compute_profile}` may not place.** {how_it_is_known} {_WHAT_IT_LOOKS_LIKE} "
        f"{_WHERE_THE_REASONING_IS} {_WHAT_THIS_IS_NOT}"
    )
