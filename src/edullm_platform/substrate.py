"""Every run this platform knows about, read once and normalised into one record per run.

**THIS IS ONE PIPELINE WITH TWO PUBLICATIONS, AND THIS MODULE IS THE PIPELINE.** The day's
activity is a daily aggregation of what is here, unioned with the launch events that carry no
run id at all. A per-run state snapshot -- what ``edullm status`` needs, and what this module
deliberately does not build -- is the same records keyed by run id and refreshed on state
change. ``docs-frank/reference/decisions.md`` settles the split under "The activity and the run
index are one pipeline and two publications". The expensive half is shared: reading lineage,
reading Batch, reading CloudTrail, and maintaining the run-id to workflow-run join. Building
either publication as a direct reader would mean two ingestions that disagree about one run.

**NOTHING HERE KNOWS ABOUT A DAY.** No window, no calendar, no file name. The activity applies
the window, because the window is a property of that publication and not of the data; a
snapshot refreshed every thirty seconds has no day at all. :attr:`RunFacts.ran_on` is the
denormalisation hook the daily view uses, and it points one way on purpose -- per-run collapses
into per-person-per-day, and per-person-per-day cannot be expanded back into per-run.

**AN INTENT WITH NO ATTEMPT IS A RUN, AND** :func:`edullm_platform.run_costs.run_costs` **DROPS
IT.** ``run_costs`` says so in its own docstring, and it is right to: a report titled "what runs
have cost" cannot price something that never reached an instance. But a queued run, a refused
run and a run still waiting on capacity are exactly the runs somebody asks the status of, and
they are three of the four states the activity is specified to report. So this module walks the
intents and attaches costs, rather than walking the costs.

**EVERY SOURCE CARRIES THREE OUTCOMES AND NOTHING MAY REDUCE THEM TO TWO.** A source was read,
or it was read and held nothing, or nobody could read it. :data:`SOURCES` names every source the
substrate is assembled from and :meth:`Substrate.outcome` answers for each of them with one of
:data:`SOURCE_OUTCOMES`. The two that are easy to conflate are the last two, and conflating them
is not a rounding error: an empty answer is a finding about the platform and an unread source is
a finding about the reader, and a job whose exit code carries both meanings picks the wrong one.
The outcome is a value on the record rather than a line in :attr:`Substrate.gaps` alone, because
a gap list is advisory and a view that renders runs and launches without consulting it would
render "nothing happened" and "nobody looked" identically.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Sized
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import ComputeProfile
from edullm_platform.run_costs import run_costs

__all__ = [
    "ATTEMPTS_NOT_READ",
    "NEVER_STARTED",
    "SOURCES",
    "SOURCE_EMPTY",
    "SOURCE_NOT_READ",
    "SOURCE_OUTCOMES",
    "SOURCE_READ",
    "STATE_SOURCES",
    "UNKNOWN_STATE",
    "AttemptFacts",
    "LaunchEvent",
    "RunFacts",
    "SourceGap",
    "Substrate",
    "normalise",
]

#: What ``state_source`` may say. ``live`` is a Batch reading, ``attempt`` a terminal lineage
#: record, ``intent`` a submission with nothing after it, and ``unread`` the case where the
#: attempt prefix was refused -- which is not a state at all and must not be reported as one.
STATE_SOURCES: Final = ("live", "attempt", "intent", "unread")

UNKNOWN_STATE: Final = "unknown"
ATTEMPTS_NOT_READ: Final = (
    "the attempt records were not read, so this run's duration and cost are unknown; "
    "this is not the same as a run that never started"
)
NEVER_STARTED: Final = "no attempt record: this run never reached an instance"

#: The source answered and had something in it.
SOURCE_READ: Final = "read"
#: The source answered and held nothing. A finding about the platform: nothing launched, no
#: run is live, nothing was tagged.
SOURCE_EMPTY: Final = "read and empty"
#: Nobody could read the source. A finding about the reader, and never about the platform.
SOURCE_NOT_READ: Final = "not read"

#: The three, in the order they degrade. Held as a tuple so that a fourth cannot be added
#: without the tests that enumerate them noticing.
SOURCE_OUTCOMES: Final = (SOURCE_READ, SOURCE_EMPTY, SOURCE_NOT_READ)

#: Every source whose outcome this record carries, which is every source that can be absent
#: without the substrate being absent.
#:
#: The intent records are deliberately not here. They are the only required source -- there is
#: no substrate without them -- so their third outcome is that :func:`normalise` is never
#: reached, which the collector raises rather than reports. Naming them here would offer a
#: caller a substrate that says "no runs could be read", which is a page nobody should be able
#: to publish.
SOURCES: Final = ("attempt", "experiment", "launch", "live")


def _outcome(reading: Sized | None) -> str:
    """Which of the three things one source did, from what the collector handed over.

    ``None`` is the refusal and is never spelled any other way. An empty container is the
    other finding, and the two are kept apart here rather than at each call site because one
    call site forgetting is the whole failure mode.
    """
    if reading is None:
        return SOURCE_NOT_READ
    return SOURCE_EMPTY if len(reading) == 0 else SOURCE_READ


@dataclass(frozen=True)
class LaunchEvent:
    """One launch seen in CloudTrail, reduced to what a join needs.

    Defined here rather than beside the mismatch computation because the launch feed is part
    of the substrate: it is read once and consumed twice, by the mismatch arm that wants the
    launches with no run id and by anything later that wants a launch beside the run it
    belongs to. ``run_id`` is ``None`` for every launch that carried no platform tag, which
    is the population the mismatch list is made of.
    """

    event_id: str
    event_name: str
    occurred_at: datetime
    role_name: str
    run_id: str | None


@dataclass(frozen=True)
class SourceGap:
    """One source that was not read, and the question that therefore has no answer.

    ``unanswered`` is a sentence rather than a flag because it is printed. A reader who is
    told "Batch was not read" learns nothing; a reader told "no run can be reported as
    running" knows which figure to distrust.
    """

    source: str
    reason: str
    unanswered: str


@dataclass(frozen=True)
class AttemptFacts:
    """One attempt of one run, as the lineage store recorded it after it ended."""

    attempt_id: str
    ordinal: int
    started_at: datetime
    ended_at: datetime
    terminal_state: str


@dataclass(frozen=True)
class RunFacts:
    """One run, carrying enough that a status query needs no second source.

    Every field is either in the intent record or derived from the attempts and the compute
    profile. Nothing here requires a call the collector did not already make, which is the
    property that lets a snapshot be published without a second ingestion.
    """

    run_id: str
    submitter: str
    team: str
    #: ``None`` both when the experiment tag was read and this run carries none and when the
    #: tag read was refused. The two are told apart on the substrate rather than here,
    #: through ``Substrate.outcome("experiment")``, because a run cannot know why its own
    #: field is empty.
    experiment: str | None
    repository: str
    commit_sha: str
    image_digest: str
    workload_profile: str
    compute_profile: str
    wandb_project: str
    fanout_size: int | None
    submitted_at: datetime
    approving_environment: str
    #: The join. ``None`` means no index from this run id to the workflow run that produced
    #: it was available -- see this module's plan task for why the index has to be written at
    #: mint time rather than searched for afterwards.
    workflow_run_id: int | None
    workflow_run_url: str | None
    attempts: tuple[AttemptFacts, ...]
    state: str
    state_source: str
    seconds: Decimal
    cost_usd: Decimal | None
    unpriced_reason: str | None

    @property
    def ordered_at(self) -> datetime:
        """When this run entered the record, for a stable ordering across both views."""
        if self.attempts:
            return min(attempt.started_at for attempt in self.attempts)
        return self.submitted_at

    @property
    def ran_on(self) -> date:
        """The day this run belongs to, in UTC.

        A run that began yesterday and ended this morning counts where it started, which is
        the choice AWS makes about an instance-hour and the one ``tools/report_spend.py``
        already makes about a month. A run that never started counts on the day it was
        submitted, because that is the only timestamp it has and a queued run still has to
        appear somewhere.
        """
        return self.ordered_at.date()


@dataclass(frozen=True)
class Substrate:
    """Every run, plus every launch, plus what each source this was built from did."""

    collected_at: datetime
    runs: Mapping[str, RunFacts]
    #: ``None`` exactly when CloudTrail was not read. An empty tuple means it was read and
    #: nothing launched, which is a finding; ``None`` means there is no finding to report.
    launches: tuple[LaunchEvent, ...] | None
    #: One of :data:`SOURCE_OUTCOMES` for each of :data:`SOURCES`. The single place the
    #: read/empty/unread distinction is recorded, so that no summary of it can drift from it.
    source_outcomes: Mapping[str, str]
    gaps: tuple[SourceGap, ...]

    @property
    def attempts_read(self) -> bool:
        """Whether the ``attempt/`` prefix answered at all, empty or otherwise.

        A property rather than a field, so that this and
        ``outcome("attempt")`` cannot come to disagree. Two fields carrying one fact is how
        a run comes to say it was unread on a page that says it was read.
        """
        return self.outcome("attempt") != SOURCE_NOT_READ

    @property
    def experiments_read(self) -> bool:
        """Whether the Batch experiment tags answered at all. See :attr:`attempts_read`."""
        return self.outcome("experiment") != SOURCE_NOT_READ

    @property
    def known_run_ids(self) -> frozenset[str]:
        """Every run id the platform can account for, which is the mismatch join's right side."""
        return frozenset(self.runs)

    def outcome(self, source: str) -> str:
        """What one source did: read, read and empty, or not read.

        A source this substrate does not carry is refused rather than answered. Returning
        "not read" for a name nobody declared would let a typo in a view render as a
        permanent hole that no deploy can ever close.
        """
        try:
            return self.source_outcomes[source]
        except KeyError:
            raise KeyError(
                f"{source!r} is not a source this substrate reads; {SOURCES} are"
            ) from None

    def ran_on(self, day: date) -> tuple[RunFacts, ...]:
        """The runs belonging to one day, ordered as they entered the record.

        The one direction the aggregation goes. There is no inverse and there should not be.
        """
        return tuple(
            sorted(
                (facts for facts in self.runs.values() if facts.ran_on == day),
                key=lambda facts: (facts.ordered_at, facts.run_id),
            )
        )


def _state(
    *,
    run_id: str,
    attempts: Sequence[AttemptFacts],
    live_states: Mapping[str, str] | None,
    attempts_read: bool,
) -> tuple[str, str]:
    """The run's state and where that state came from.

    Batch wins where it was read, because it is the only source that can name a state a run
    is still in: the lineage attempt record is written when an attempt ends, so it can say
    how a run finished and never that one is running. Where Batch was not read, the last
    attempt's terminal state is the best available, and where the attempts were not read
    either, the state is unknown and says so rather than defaulting to submitted.
    """
    if live_states is not None and run_id in live_states:
        return live_states[run_id], "live"
    if not attempts_read:
        return UNKNOWN_STATE, "unread"
    if attempts:
        return max(attempts, key=lambda attempt: attempt.ordinal).terminal_state, "attempt"
    return "submitted", "intent"


def normalise(
    *,
    collected_at: datetime,
    intents: Iterable[IntentRecord],
    attempts: Iterable[SchedulerAttempt],
    compute_profiles: Iterable[ComputeProfile],
    experiments: Mapping[str, str] | None = None,
    live_states: Mapping[str, str] | None = None,
    launches: Sequence[LaunchEvent] | None = None,
    attempts_read: bool = True,
    gaps: Sequence[SourceGap] = (),
) -> Substrate:
    """One record per run, from whichever sources were read.

    Walks the intents, not the costs, so that a run with no attempt survives. Pricing is
    delegated to ``run_costs`` rather than recomputed, because two dollar figures for one run
    is worse than one figure and a caveat.

    Every optional source defaults to ``None``, which is its refusal. That is deliberate: the
    caller that read a source and found it empty has to say so, and the caller that forgot to
    read one cannot accidentally claim it was empty.
    """
    every_intent = tuple(intents)
    every_attempt = tuple(attempts)

    by_run: dict[str, list[AttemptFacts]] = {}
    for attempt in every_attempt:
        by_run.setdefault(attempt.run_id, []).append(
            AttemptFacts(
                attempt_id=attempt.attempt_id,
                ordinal=attempt.attempt_ordinal,
                started_at=attempt.started_at,
                ended_at=attempt.ended_at,
                terminal_state=attempt.run_state.value,
            )
        )

    priced = {
        cost.run_id: cost
        for cost in run_costs(
            intents=every_intent, attempts=every_attempt, compute_profiles=compute_profiles
        )
    }

    runs: dict[str, RunFacts] = {}
    for intent in every_intent:
        manifest = intent.manifest
        run_attempts = tuple(sorted(by_run.get(intent.run_id, ()), key=lambda facts: facts.ordinal))
        cost = priced.get(intent.run_id)
        state, source = _state(
            run_id=intent.run_id,
            attempts=run_attempts,
            live_states=live_states,
            attempts_read=attempts_read,
        )
        seconds: Decimal
        usd: Decimal | None
        reason: str | None
        if cost is not None:
            seconds, usd, reason = cost.seconds, cost.cost_usd, cost.unpriced_reason
        else:
            seconds, usd = Decimal(0), None
            reason = NEVER_STARTED if attempts_read else ATTEMPTS_NOT_READ
        runs[intent.run_id] = RunFacts(
            run_id=intent.run_id,
            submitter=intent.submitter,
            team=manifest.team,
            experiment=None if experiments is None else experiments.get(intent.run_id),
            repository=manifest.repository,
            commit_sha=manifest.commit_sha,
            image_digest=manifest.image_digest,
            workload_profile=manifest.workload_profile,
            compute_profile=manifest.compute_profile,
            wandb_project=manifest.wandb_project,
            fanout_size=None if manifest.fanout is None else manifest.fanout.size,
            submitted_at=intent.recorded_at,
            approving_environment=str(intent.approving_environment),
            workflow_run_id=intent.workflow_run.run_id,
            workflow_run_url=intent.workflow_run.url,
            attempts=run_attempts,
            state=state,
            state_source=source,
            seconds=seconds,
            cost_usd=usd,
            unpriced_reason=reason,
        )

    return Substrate(
        collected_at=collected_at,
        runs=runs,
        launches=None if launches is None else tuple(launches),
        source_outcomes={
            "attempt": _outcome(every_attempt if attempts_read else None),
            "experiment": _outcome(experiments),
            "launch": _outcome(launches),
            "live": _outcome(live_states),
        },
        gaps=tuple(gaps),
    )
