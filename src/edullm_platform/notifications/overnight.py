"""What the platform did while nobody was watching, read off Batch and priced here.

**Every figure on the morning page is measured and none of it is a ceiling.** That is the
difference between this and the approval message beside it. An approval names what a run may
spend, because that is what is being authorised. A morning page names what the machines
actually ran for, because the night is over and the ceiling is no longer the interesting
number. A page that reported authorised totals would read as an account spending several
times what it spent.

**It reads Batch and it reads no aggregation.** ``substrate`` is a nightly file and
``run-history.json`` is a committed reading, and a page that waits on either is a page
describing the night before last on the morning after a pipeline failure. One
``batch:ListJobs`` per declared queue, bounded by ``AFTER_CREATED_AT``, answers what ran, when
it started, when it stopped and how it ended. The queue names the compute profile through
``execution-targets.yaml`` and the profile names the rate through ``workload-catalog.yaml``,
which are two of the three files this function already carries.

**The window filter is server side and that is not an optimisation.** ``ListJobs`` orders its
answer oldest first, so a client-side filter would page through every terminal job Batch still
holds to reach last night's. Measured 2026-08-06 against the live account: the ``cpu`` queue
alone answers with 113 succeeded jobs and 55 failed ones going back weeks. ``filters`` with
``AFTER_CREATED_AT`` and no ``jobStatus`` returns every status inside the window, newest first,
in one call per queue.

**A job whose name is not a run id is counted and is never named.** Sixteen of the jobs in that
listing are hand-run smokes with names like ``validator-preflight-091``. They cost real money
and belong in the total; they belong to nobody, have no intent record, and naming one as a
research run would attribute somebody's afternoon of debugging to a researcher.

**Nothing here raises.** A queue that cannot be listed contributes nothing and is counted as a
queue that was not read, so the page says the total is a floor rather than showing a smaller
number as if it were complete. A morning page nobody got is worse than one with a gap in it
that says it is a gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from ..contracts.identity import RUN_ID_REGEX
from .approval import PLATFORM_EVENT_SOURCE
from .facts import CENTS, MAXIMUM_CELL_PAGES, Catalogs, CellLister

__all__ = [
    "DEFAULT_WINDOW_HOURS",
    "OVERNIGHT_DETAIL_TYPE",
    "Ended",
    "OvernightFacts",
    "read_overnight",
    "window_of",
]

#: The second envelope this platform sends itself. Named beside the approval request rather
#: than in a template, so the schedule and the reader cannot come to disagree about the
#: spelling; ``tests/test_notifications_infrastructure.py`` holds the rule's constant input
#: against this string.
OVERNIGHT_DETAIL_TYPE: Final = "Overnight Activity"

#: How far back the page looks when the trigger does not say. Twelve hours, so a page sent at
#: eight in the morning covers everything since eight the evening before, which is the whole
#: of the time nobody was reading the channel.
DEFAULT_WINDOW_HOURS: Final = 12

#: The statuses that mean a job is over. Everything else on the listing is still going or
#: still waiting, and the page counts those separately because they are what today inherits.
TERMINAL: Final = frozenset({"SUCCEEDED", "FAILED"})

#: The one status that means EC2 has not sold this account the machine yet. Counted apart from
#: the ones that are running, because a queue full of RUNNABLE is a capacity problem and a
#: queue full of RUNNING is an ordinary night.
WAITING: Final = "RUNNABLE"

#: How many failures the page names before it stops counting them out, matching the ceiling
#: the fan-out message puts on cell indexes. A morning page listing thirty run ids is one
#: nobody reads to the end.
NAMED_FAILURES: Final = 5

MILLISECONDS: Final = 1000
SECONDS_AN_HOUR: Final = 3600


@dataclass(frozen=True)
class Ended:
    """One job that finished inside the window, priced against the queue it ran on."""

    name: str
    queue: str
    compute_profile: str | None
    succeeded: bool
    seconds: int
    #: ``None`` where no execution target names the queue, so no profile and no rate. Two of
    #: the account's sixteen queues are in that state and the page says so rather than
    #: pricing them at zero.
    spent_usd: Decimal | None
    exit_code: int | None

    @property
    def is_a_run(self) -> bool:
        """Whether the platform minted this name, as opposed to somebody typing it.

        The job name is the run id for anything the submission path produced, and is
        whatever was passed to ``submit-job`` for anything a person ran by hand.
        """
        return RUN_ID_REGEX.fullmatch(self.name) is not None


@dataclass(frozen=True)
class OvernightFacts:
    """What happened, in the terms one page has room for.

    Frozen for the reason every other facts object here is frozen. Counts rather than a list
    of jobs, except for the failures, because a page is not a report and the run ids of the
    things that worked are not what anybody is looking for at eight in the morning.
    """

    hours: int
    ended: tuple[Ended, ...]
    #: How many jobs were running and how many were waiting for a machine when this was read.
    #: A snapshot rather than a window, and the renderer says so.
    running: int
    waiting: int
    #: How many of the declared queues answered, against how many were asked. Short means the
    #: totals are floors, and the page says that instead of printing a smaller figure that
    #: reads exactly like a complete one.
    queues_read: int
    queues_asked: int

    @property
    def succeeded(self) -> int:
        return sum(1 for job in self.ended if job.succeeded)

    @property
    def failed(self) -> tuple[Ended, ...]:
        return tuple(job for job in self.ended if not job.succeeded)

    @property
    def spent_usd(self) -> Decimal:
        return sum(
            (job.spent_usd for job in self.ended if job.spent_usd is not None), Decimal("0.00")
        ).quantize(CENTS, rounding=ROUND_HALF_UP)

    @property
    def unpriced(self) -> int:
        """How many finished jobs ran on a queue nothing prices, so the total is short."""
        return sum(1 for job in self.ended if job.spent_usd is None)

    @property
    def complete(self) -> bool:
        return self.queues_read == self.queues_asked and self.unpriced == 0


def window_of(envelope: Mapping[str, Any]) -> int:
    """How many hours the trigger asked for, or the default because it said nothing.

    Read off the event rather than fixed, so the same function answers the morning schedule
    and a person invoking it by hand over a different window. A value that is not a positive
    whole number is the default rather than a refusal, because a mistyped constant input on
    a schedule should cost the window and never the page.
    """
    detail = envelope.get("detail")
    hours = detail.get("hours") if isinstance(detail, Mapping) else None
    if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0:
        return DEFAULT_WINDOW_HOURS
    return hours


def _rate(profile: str | None, catalogs: Catalogs) -> Decimal | None:
    if profile is None:
        return None
    for entry in catalogs.catalog.compute_profiles:
        if entry.name == profile:
            return entry.hourly_rate_usd
    return None


def _seconds(summary: Mapping[str, Any]) -> int:
    started, stopped = summary.get("startedAt"), summary.get("stoppedAt")
    if not isinstance(started, int) or not isinstance(stopped, int):
        return 0
    if isinstance(started, bool) or isinstance(stopped, bool) or stopped < started:
        return 0
    return (stopped - started) // MILLISECONDS


def _exit_code(summary: Mapping[str, Any]) -> int | None:
    container = summary.get("container")
    code = container.get("exitCode") if isinstance(container, Mapping) else None
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    return code


def _nodes(profile: str | None, catalogs: Catalogs) -> int:
    """How many machines that profile is, defaulting to one where nothing names it.

    Read for the same reason the approval message reads it. Every profile in today's catalog
    is a single machine, so a sum that dropped this factor would agree with the account
    exactly until the first multi-node profile is registered and then understate every night
    it ran on.
    """
    if profile is None:
        return 1
    for entry in catalogs.catalog.compute_profiles:
        if entry.name == profile:
            return entry.nodes
    return 1


def _priced(summary: Mapping[str, Any], *, queue: str, catalogs: Catalogs) -> Ended:
    profile = next(
        (
            target.compute_profile
            for target in catalogs.targets.targets
            if target.job_queue == queue
        ),
        None,
    )
    rate = _rate(profile, catalogs)
    seconds = _seconds(summary)
    spent = (
        None
        if rate is None
        else (rate * Decimal(_nodes(profile, catalogs)) * Decimal(seconds) / Decimal(SECONDS_AN_HOUR)).quantize(
            CENTS, rounding=ROUND_HALF_UP
        )
    )
    name = summary.get("jobName")
    return Ended(
        name=name if isinstance(name, str) and name else "a job Batch did not name",
        queue=queue,
        compute_profile=profile,
        succeeded=summary.get("status") == "SUCCEEDED",
        seconds=seconds,
        spent_usd=spent,
        exit_code=_exit_code(summary),
    )


def _listing(
    lister: CellLister, *, queue: str, after: int
) -> list[Mapping[str, Any]] | None:
    """Every job created on one queue since a moment, or ``None`` because it was not read.

    ``None`` rather than an empty list for every way this can fail, which is the same
    distinction ``facts._cells_spent`` makes and for the same reason: a queue nobody could
    list is not a queue nothing ran on, and the two produce identical pages unless they are
    kept apart.

    Broad except, because botocore's exception classes cannot be imported here and the set of
    ways a listing can fail is open. Narrowed by what it does rather than by what it catches.
    """
    found: list[Mapping[str, Any]] = []
    arguments: dict[str, Any] = {
        "jobQueue": queue,
        "filters": [{"name": "AFTER_CREATED_AT", "values": [str(after)]}],
    }
    try:
        for _page in range(MAXIMUM_CELL_PAGES):
            answer = lister.list_jobs(**arguments)
            found.extend(
                summary
                for summary in answer.get("jobSummaryList") or []
                if isinstance(summary, Mapping)
            )
            token = answer.get("nextToken")
            if not isinstance(token, str) or not token:
                return found
            arguments["nextToken"] = token
    except Exception:  # noqa: BLE001
        return None
    # The page ceiling, reached rather than exhausted. Abandoning this queue is deliberate:
    # a partial listing rendered into a total is a figure missing an arbitrary set of jobs
    # and reads exactly like a complete one.
    return None


def read_overnight(
    envelope: Mapping[str, Any],
    *,
    catalogs: Catalogs,
    cell_lister: CellLister | None = None,
    now_ms: int,
) -> OvernightFacts | None:
    """What the declared queues did in the window, or ``None`` because this is not the trigger.

    ``now_ms`` is passed rather than read from a clock, for the reason this whole package
    imports no clock: a reader that took the time itself could not be tested against a
    committed answer, and the handler has the delivery's own timestamp to hand.

    ``cell_lister`` defaults to ``None`` and the page then reports that no queue was read,
    which is what makes the wording loop free of a credential.
    """
    if envelope.get("source") != PLATFORM_EVENT_SOURCE:
        return None
    if envelope.get("detail-type") != OVERNIGHT_DETAIL_TYPE:
        return None

    hours = window_of(envelope)
    after = now_ms - hours * SECONDS_AN_HOUR * MILLISECONDS
    queues: Sequence[str] = tuple(target.job_queue for target in catalogs.targets.targets)

    ended: list[Ended] = []
    running = waiting = read = 0
    for queue in queues:
        summaries = None if cell_lister is None else _listing(cell_lister, queue=queue, after=after)
        if summaries is None:
            continue
        read += 1
        for summary in summaries:
            status = summary.get("status")
            if status in TERMINAL:
                ended.append(_priced(summary, queue=queue, catalogs=catalogs))
            elif status == WAITING:
                waiting += 1
            else:
                running += 1

    return OvernightFacts(
        hours=hours,
        ended=tuple(ended),
        running=running,
        waiting=waiting,
        queues_read=read,
        queues_asked=len(queues),
    )
