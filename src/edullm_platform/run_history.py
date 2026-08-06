"""What runs of this shape have actually taken, beside a ceiling that says what one may cost.

**THE CEILING IS NOT A FORECAST AND WAS BEING READ AS ONE.** ``rate x nodes x hours x
attempts x cells`` is what a submission authorises, and every one of those five numbers is
typed by the submitter. People type the maximum runtime the workload profile allows rather
than what the work needs, so a run asking twenty-four hours and finishing in three is priced
at eight times what it costs. That figure is correct for the question it answers, which is
"how much of the account is this permitted to commit", and it is the wrong figure for the
question everybody actually asks, which is "what is this going to take".

**NOTHING HERE ROUTES ANYTHING, AND NOTHING HERE MAY.** ``classify_request`` reads the worst
case and nothing else, because the worst case is what is being authorised;
``docs-frank/reference/decisions.md`` settles the general form of this under jobs running
coming first and the record following. A measured median is evidence about the past and a
submission is a claim about the future, and a rule that let last week's fast runs widen this
week's automatic class would be routing on a number a submitter can move by submitting cheap
runs. So this module is imported by the two renderers and by nothing that decides.

**IT IS READ THROUGH THE SUBSTRATE RATHER THAN OUT OF THE STORE.**
:mod:`edullm_platform.substrate` already normalises lineage, Batch and CloudTrail into one
record per run, and its own header records why a second direct reader is a mistake: two
ingestions of one account eventually disagree about one run, and the disagreement surfaces as
two numbers for the same thing. :func:`summarise` therefore takes a :class:`Substrate` and
projects it, and the only field it needed that was not already there is ``dataset_release``.

**THREE THINGS IT REFUSES TO DO.**

A shape with no history says so. There is no fallback to an overall average, no "typically a
few hours", and no figure derived from the declared bound. A submitter told nothing knows
they were told nothing; a submitter told an invented number does not.

A failed run is not evidence about duration. 64 of the 133 results in the store say
``failed`` and almost all of those are a container exiting on a code error in the first
minutes, so a median over everything would say that this shape takes four minutes. Failures
are counted and reported beside the figure, because how often this shape fails is worth
knowing, and they are kept out of the figure itself.

A cohort of one is not a distribution. :data:`RUNS_FOR_A_FIGURE` is the number of successful
runs a rung needs before it is quoted, and a rung short of it is passed over for a coarser
one rather than reported thinly.

**THE KEY IS A LADDER AND THE RUNG THAT ANSWERED IS PRINTED.** The obvious key is repository,
workload profile, compute profile and dataset, and against a store holding 133 results it is
specific enough to match nothing for most submissions. The obvious repair, loosening the key
for everybody, throws away the precision where it exists. So :data:`RUNGS` is the specific
key first and two coarser ones under it, the first rung with enough successful runs answers,
and the sentence names which one did. "5 runs of this workload on this machine" and "5 runs
of this workload, on any machine" are different evidence and a reader has to be able to tell
them apart.

Dataset is dropped first because ``config/datasets.yaml`` carries two releases and it is
therefore the field with the least to say. Compute profile is dropped second and is dropped
reluctantly, because it is the field with the most to say about a duration. Repository and
workload profile are never dropped: a workload profile is what a run does, and a duration
across two different programs is not a measurement of anything.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Protocol

__all__ = [
    "HISTORY_FILENAME",
    "HISTORY_FORMAT_VERSION",
    "NOTHING_LIKE_THIS_YET",
    "NO_HISTORY_PACKAGED",
    "RUNGS",
    "RUNS_FOR_A_FIGURE",
    "SHAPE_FIELDS",
    "Cohort",
    "HistoryAnswer",
    "RunHistory",
    "RunHistoryFormatError",
    "RunRecord",
    "as_document",
    "coverage",
    "elapsed_said",
    "from_document",
    "history_for",
    "load_run_history",
    "shape_of",
    "summarise",
]

#: The version of the document :func:`as_document` writes, bumped when a field changes
#: meaning or goes away. A reader that met a newer document would report the fields it did
#: not recognise as absent, and absent is the one thing a measurement may not invent.
HISTORY_FORMAT_VERSION: Final = 1

#: Where the reading lives. Under ``config/`` because that directory is the one the wheel
#: carries into an install, so this travels with the CLI exactly as the reviewed
#: configuration does, and because it lands the way everything under ``config/`` lands,
#: through a pull request somebody read.
#:
#: JSON among the YAML on purpose. Nobody edits this by hand and nobody should: it is a
#: reading of the account written by ``tools/build_run_history.py``, and a comment in it
#: would be a claim nothing regenerated.
HISTORY_FILENAME: Final = "run-history.json"

#: How many successful runs a rung needs before its median is quoted.
#:
#: Three rather than one, and three rather than ten. One run is an anecdote and quoting it
#: as "runs like yours took" is the invention this module refuses. Ten would answer for
#: almost nothing: the store holds 69 successful results in total, so a bar that high leaves
#: the whole surface saying it does not know, and a submitter who is never told anything
#: stops reading the line. Three is where a range starts to mean something and is stated
#: with the count beside it, so a reader can discount it themselves.
RUNS_FOR_A_FIGURE: Final = 3

#: The key, from the most specific to the least, with the words the sentence uses for each.
#: A rung is a tuple of :class:`~edullm_platform.substrate.RunFacts` attribute names, which
#: is also the set of fields a manifest carries under the same names.
RUNGS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (
        ("repository", "workload_profile", "compute_profile", "dataset_release"),
        "this workload, on this machine, on this dataset",
    ),
    (
        ("repository", "workload_profile", "compute_profile"),
        "this workload on this machine, on any dataset",
    ),
    (
        ("repository", "workload_profile"),
        "this workload on any machine",
    ),
)

#: What the answer is when the install carries no reading at all. Distinct from having read
#: one and found nothing like this submission, which is the sentence below it, for the reason
#: the substrate keeps an unread source apart from an empty one: one is a finding about this
#: platform and the other is a finding about this install.
NO_HISTORY_PACKAGED: Final = (
    "no run history is packaged with this install, so nothing here can say what runs like "
    "this one have taken. tools/build_run_history.py is what writes it and the audit is "
    "what runs that."
)

NOTHING_LIKE_THIS_YET: Final = (
    "no run of this shape has succeeded here yet, so there is nothing to compare the "
    "ceiling against"
)


class RunRecord(Protocol):
    """What one normalised run has to carry for a duration to be read off it.

    A structural type rather than an import of
    :class:`edullm_platform.substrate.RunFacts`, which is the only thing that produces one.
    Naming that class here would put ``substrate`` and ``run_costs`` in the CLI's import
    tree, and ``tests/test_release_tag_workflow.py`` derives the release trigger from that
    tree by reading imports out of the source. Every change to how the account is read would
    then cut a CLI release for a module ``edullm check`` never loads: this half reads a
    committed digest and never builds one.

    So the wiring is in ``tools/build_run_history.py``, which imports both. This is not a
    second reader of the store. It is the consumer of the one reader's output, and the
    fields below are the substrate's own, spelled the same way.
    """

    @property
    def repository(self) -> str: ...
    @property
    def workload_profile(self) -> str: ...
    @property
    def compute_profile(self) -> str: ...
    @property
    def dataset_release(self) -> str: ...
    @property
    def state(self) -> str: ...
    @property
    def seconds(self) -> Decimal: ...
    @property
    def attempts(self) -> Sequence[object]: ...


class RunHistoryFormatError(ValueError):
    """A reading this tree cannot parse, which is never a reading that found nothing."""


def elapsed_said(seconds: Decimal) -> str:
    """A duration as a person says one, because nobody reads 7,842 seconds as two hours.

    Whole units and never two decimal places. The figure is a median over a handful of runs
    and printing it to the second would claim a precision the sample does not carry.
    """
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    return f"{hours}h" if minutes == 0 else f"{hours}h{minutes:02d}m"


@dataclass(frozen=True)
class Cohort:
    """Every run that has taken one shape, and how long the ones that worked took."""

    #: Which rung of :data:`RUNGS` this cohort is keyed on, as an index into it.
    rung: int
    #: The field values, in the order the rung names its fields.
    key: tuple[str, ...]
    succeeded: int
    failed: int
    #: Over the successful runs only. ``None`` exactly when ``succeeded`` is zero, which is
    #: a cohort that is entirely failures and is worth carrying: it says this shape has been
    #: tried and has not worked, which is a different thing from a shape nobody has tried.
    fastest_seconds: Decimal | None
    median_seconds: Decimal | None
    slowest_seconds: Decimal | None

    @property
    def answerable(self) -> bool:
        return self.succeeded >= RUNS_FOR_A_FIGURE


@dataclass(frozen=True)
class HistoryAnswer:
    """What a submitter is told, which is always a sentence and sometimes also a figure.

    ``said`` is never empty. A block that disappeared when there was no history would leave
    a reader unable to tell "this platform has never run this" from "this version of the
    tool does not print that", and those send somebody to different places.
    """

    said: str
    #: The cohort the sentence was composed from, or ``None`` when the sentence is one of
    #: the two refusals. Carried so that ``--json`` can publish the counts rather than the
    #: prose, which is the split the rest of the CLI already makes.
    cohort: Cohort | None = None


@dataclass(frozen=True)
class RunHistory:
    """One reading of the store, reduced to the cohorts a submission can be matched to."""

    built_at: datetime
    #: How many runs the reading held, and how many of those had a duration at all. Both are
    #: printed by the tool and neither is read by the lookup; they are here so a reader of
    #: the file can see the denominator behind every cohort in it.
    runs_read: int
    runs_with_a_duration: int
    cohorts: tuple[Cohort, ...]

    def answer(self, shape: Mapping[str, str]) -> HistoryAnswer:
        """What this reading says about a submission of the given shape.

        Walks :data:`RUNGS` from the most specific down and stops at the first rung with
        enough successful runs. A rung that matched a cohort with too few successes does not
        stop the walk, because a coarser rung containing it will have at least as many.
        """
        seen_but_thin: Cohort | None = None
        for index, (fields, _) in enumerate(RUNGS):
            key = tuple(shape[field] for field in fields)
            cohort = self._cohort(index, key)
            if cohort is None:
                continue
            if cohort.answerable:
                return HistoryAnswer(said=_said(cohort), cohort=cohort)
            seen_but_thin = seen_but_thin or cohort
        if seen_but_thin is not None:
            return HistoryAnswer(said=_thin_said(seen_but_thin), cohort=seen_but_thin)
        return HistoryAnswer(said=NOTHING_LIKE_THIS_YET)

    def _cohort(self, rung: int, key: tuple[str, ...]) -> Cohort | None:
        for cohort in self.cohorts:
            if cohort.rung == rung and cohort.key == key:
                return cohort
        return None


def _said(cohort: Cohort) -> str:
    """The sentence a quotable cohort earns, with the rung it came from named in it."""
    assert cohort.median_seconds is not None  # answerable implies at least one success
    assert cohort.fastest_seconds is not None
    assert cohort.slowest_seconds is not None
    described = RUNGS[cohort.rung][1]
    runs = "run" if cohort.succeeded == 1 else "runs"
    said = (
        f"{cohort.succeeded} succeeded {runs} of {described} took a median of "
        f"{elapsed_said(cohort.median_seconds)}, between "
        f"{elapsed_said(cohort.fastest_seconds)} and "
        f"{elapsed_said(cohort.slowest_seconds)}."
    )
    if cohort.failed:
        # SAID RATHER THAN FOLDED IN. A shape that fails half the time is the most useful
        # thing on this line and it is invisible in a median, because a run that died in
        # four minutes was excluded from that median precisely so it would not drag it down.
        failed = "run" if cohort.failed == 1 else "runs"
        said += f" {cohort.failed} more {failed} failed and are not in that figure."
    return said


def _thin_said(cohort: Cohort) -> str:
    """The sentence for a shape that has been run and has not been run enough.

    Counted rather than quoted. Somebody whose shape has one success and four failures is
    owed both numbers, and is not owed a median over one run dressed up as a distribution.
    """
    described = RUNGS[cohort.rung][1]
    return (
        f"{cohort.succeeded} succeeded and {cohort.failed} failed of {described}, which is "
        f"fewer than the {RUNS_FOR_A_FIGURE} successes needed before a duration is worth "
        "quoting."
    )


def _median(values: Sequence[Decimal]) -> Decimal:
    """The middle value, and the lower of the two middles on an even count.

    The lower rather than their mean, so that every figure this module prints is a duration
    some run actually took. A mean of two runs is a number nothing ever did, and a submitter
    who goes looking for the run behind it finds nothing.
    """
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def _durations(runs: Iterable[RunRecord]) -> tuple[list[Decimal], int]:
    """The successful runs' wall clock, and how many of the rest failed.

    A run with no attempt record contributes to neither. It never reached an instance, so it
    is not a failure of this shape and it is certainly not a duration.
    """
    succeeded: list[Decimal] = []
    failed = 0
    for facts in runs:
        if not len(facts.attempts):
            continue
        if facts.state == "succeeded":
            succeeded.append(facts.seconds)
        elif facts.state == "failed":
            failed += 1
    return succeeded, failed


def summarise(
    runs: Iterable[RunRecord], *, built_at: datetime | None = None
) -> RunHistory:
    """Every cohort at every rung, from one reading of the account.

    Every rung is precomputed rather than derived at lookup time, because the lookup runs on
    a laptop against a file and the file has to answer without carrying every run in it. It
    also keeps the arithmetic in one place: a CLI that recomputed a median from raw runs
    would be the second implementation this module exists to avoid.
    """
    every_run = list(runs)
    cohorts: list[Cohort] = []
    for index, (fields, _) in enumerate(RUNGS):
        grouped: dict[tuple[str, ...], list[RunRecord]] = {}
        for facts in every_run:
            key = tuple(str(getattr(facts, field)) for field in fields)
            grouped.setdefault(key, []).append(facts)
        for key, cohort_runs in sorted(grouped.items()):
            succeeded, failed = _durations(cohort_runs)
            if not succeeded and not failed:
                continue
            cohorts.append(
                Cohort(
                    rung=index,
                    key=key,
                    succeeded=len(succeeded),
                    failed=failed,
                    fastest_seconds=min(succeeded) if succeeded else None,
                    median_seconds=_median(succeeded) if succeeded else None,
                    slowest_seconds=max(succeeded) if succeeded else None,
                )
            )
    with_a_duration, _ = _durations(every_run)
    return RunHistory(
        built_at=built_at or datetime.now(UTC),
        runs_read=len(every_run),
        runs_with_a_duration=len(with_a_duration),
        cohorts=tuple(cohorts),
    )


#: Every field any rung reads, which is what a shape has to supply.
SHAPE_FIELDS: Final = tuple(dict.fromkeys(field for fields, _ in RUNGS for field in fields))


def shape_of(carrier: object) -> dict[str, str]:
    """The four fields the ladder keys on, read off anything that carries them by name.

    One function for a ``RunManifest`` and for a :class:`RunRecord` because the two spell
    all four identically, which is not a coincidence: the substrate's fields are a
    projection of the manifest's. A second reader for the second type is how the CLI and the
    history come to disagree about which dataset a run named.
    """
    return {field: str(getattr(carrier, field)) for field in SHAPE_FIELDS}


def history_for(carrier: object, *, history: RunHistory | None) -> HistoryAnswer:
    """What to print beside the ceiling for one submission, including when there is nothing.

    The one entry point the two renderers use, so that a missing reading, a shape nobody has
    run and a shape with too few successes are worded once rather than three times.
    """
    if history is None:
        return HistoryAnswer(said=NO_HISTORY_PACKAGED)
    return history.answer(shape_of(carrier))


def coverage(history: RunHistory, runs: Iterable[RunRecord]) -> tuple[int, int]:
    """How many of the runs read would get a figure if they were submitted again.

    The honest denominator for "what fraction of submissions can this answer for", and it is
    measured rather than asserted because the answer depends entirely on how concentrated the
    store happens to be. Every run is asked its own shape, so a run in a cohort of three
    counts itself, which overstates coverage for a brand new shape by exactly one run and is
    the reading anybody would make of the file by hand.
    """
    every_run = list(runs)
    answered = 0
    for facts in every_run:
        cohort = history.answer(shape_of(facts)).cohort
        if cohort is not None and cohort.answerable:
            answered += 1
    return answered, len(every_run)


def as_document(history: RunHistory) -> dict[str, Any]:
    """One reading, as the JSON-shaped mapping the tool commits."""
    return {
        "format_version": HISTORY_FORMAT_VERSION,
        "built_at": history.built_at.isoformat(),
        "runs_read": history.runs_read,
        "runs_with_a_duration": history.runs_with_a_duration,
        "cohorts": [
            {
                "rung": cohort.rung,
                "key": list(cohort.key),
                "succeeded": cohort.succeeded,
                "failed": cohort.failed,
                # Text and not a JSON number, for the reason substrate.py gives beside its
                # own money: a Decimal through a float comes back with a tail on it.
                "fastest_seconds": _text(cohort.fastest_seconds),
                "median_seconds": _text(cohort.median_seconds),
                "slowest_seconds": _text(cohort.slowest_seconds),
            }
            for cohort in history.cohorts
        ],
    }


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def from_document(document: Mapping[str, Any]) -> RunHistory:
    """The inverse of :func:`as_document`, refusing a format this tree does not know."""
    version = document.get("format_version")
    if version != HISTORY_FORMAT_VERSION:
        raise RunHistoryFormatError(
            f"this tree reads run history format {HISTORY_FORMAT_VERSION} and the document "
            f"declares {version!r}"
        )
    return RunHistory(
        built_at=datetime.fromisoformat(str(document["built_at"])),
        runs_read=int(document["runs_read"]),
        runs_with_a_duration=int(document["runs_with_a_duration"]),
        cohorts=tuple(
            Cohort(
                rung=int(cohort["rung"]),
                key=tuple(str(part) for part in cohort["key"]),
                succeeded=int(cohort["succeeded"]),
                failed=int(cohort["failed"]),
                fastest_seconds=_decimal(cohort["fastest_seconds"]),
                median_seconds=_decimal(cohort["median_seconds"]),
                slowest_seconds=_decimal(cohort["slowest_seconds"]),
            )
            for cohort in document.get("cohorts") or ()
        ),
    )


def load_run_history(directory: Path) -> RunHistory | None:
    """The reading this configuration directory carries, or ``None`` if it carries none.

    ``None`` rather than an empty history, and the caller turns it into
    :data:`NO_HISTORY_PACKAGED`. An empty history would answer every shape with "nothing has
    run this yet", which is a claim about the platform made by an install that has not been
    told anything. An editable install and a config directory a test built are both this
    case, and so is every install from before the first reading was committed.

    A file that will not parse raises rather than degrading to ``None``, because a reading
    this tree cannot read is a broken install and not an absent measurement.
    """
    path = directory / HISTORY_FILENAME
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RunHistoryFormatError(f"the run history in {path} could not be read: {error}") from error
    return from_document(document)
