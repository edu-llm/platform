"""What ran on one day, by whom, and what the largest one cost.

**THIS IS A VIEW, NOT A READER.** It takes a :class:`~edullm_platform.substrate.Substrate` and
aggregates it. Every source read happens once, in the collector, because the same records are
published twice -- as this daily file and as a per-run snapshot refreshed on state change -- and
two ingestions of one account eventually disagree about one run.

**THE WINDOW IS APPLIED HERE BECAUSE THE WINDOW IS THIS PUBLICATION'S.** The substrate holds
every run it could see, over all time, with no calendar in it. A daily file is greppable,
diffable and keeps history, which is what makes the day the right key for it and the wrong key
for a snapshot that changes every thirty seconds.

**THE LARGEST RUN IS WHY THIS EXISTS.** ``docs-frank/reference/system-overview.md`` § "Where
everything is seen" puts a person, an experiment and a figure on the first line of the morning
message, because a message opening with a total opens with something nobody is accountable for.
Everything else here is a count, which is the same rule applied downward: one run is named and
the rest are numbers.

**THREE UNREAD SOURCES ARE RENDERED AS SENTENCES ABOUT THE READER, NOT AS FACTS ABOUT THE RUNS.**
The experiment survives only as the ``edullm:experiment`` Batch tag, because
``src/edullm_platform/submission.py`` keeps a grouping key out of a hashed manifest on purpose;
the durations live under the lineage store's ``attempt/`` prefix; and the launch feed is a
CloudTrail read that this account is large enough to refuse outright. Thirty rows reading "no
experiment", thirty runs reported as never having started, or a mismatch list of length zero are
each claims about the platform. One sentence naming the source is a claim about the reader, and
only one of them is true -- the rule ``tools/visibility_board.py`` states as "a category is not
reported unless both sources it compares were read".

**THE LAUNCH FEED IS THE ONE THAT DOES NOT FIT, AND THIS MODULE MAY NOT ASSUME IT EXISTS.**
Measured on this account, ``RunInstances`` alone returned more than six thousand events for
2026-08-05 and ``tools/read_launch_events.py`` refused the feed rather than truncating it,
because a truncated feed is a denominator that is wrong and a wrong denominator reads as a
morning somebody examined. So the mismatch arm of the day is a source outcome carried on
:class:`DayActivity` rather than a list this can count, and
:func:`render_launch_feed_unread` is what a document prints in place of a list that does not
exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from edullm_platform.substrate import (
    SOURCE_EMPTY,
    SOURCE_NOT_READ,
    RunFacts,
    SourceGap,
    Substrate,
)

__all__ = [
    "LAUNCH_SOURCE",
    "DayActivity",
    "RunRow",
    "day_activity",
    "render_launch_feed_unread",
    "render_launch_window",
    "render_section",
]

#: The source name the launch feed answers to on a substrate. Named once here so that the
#: outcome this module reads and the outcome the collector wrote cannot drift by a typo --
#: ``Substrate.outcome`` refuses a name it does not carry, so a typo is loud rather than a
#: permanent hole.
LAUNCH_SOURCE = "launch"


@dataclass(frozen=True)
class RunRow:
    """One run that belongs to the day.

    ``cost_usd`` and ``unpriced_reason`` travel together for the reason ``run_costs`` gives:
    a spot run has a real spend and no honest figure, and printing a zero would rank it last
    in a list ordered by money while it may have been the largest thing on the queue.
    """

    run_id: str
    submitter: str
    team: str
    experiment: str | None
    compute_profile: str
    state: str
    #: Which source the state came from. Printed, because "succeeded" from a terminal record
    #: and "unknown" from a prefix nobody could list are not the same kind of statement.
    state_source: str
    #: How a fan-out's cells went, worded as the runs channel words it, and ``None`` for
    #: every run of one cell.
    #:
    #: NONE FOR ONE CELL RATHER THAN ``all 1 cells succeeded``, which is the same argument
    #: ``cells.said_of_cells`` makes about a zero: a clause every row carries and no row
    #: needs is a clause nobody reads, and the rows that need it are the nineteen in a
    #: hundred and eighty-four where one word is not the answer.
    cells_said: str | None
    seconds: Decimal
    cost_usd: Decimal | None
    unpriced_reason: str | None

    @classmethod
    def of(cls, facts: RunFacts) -> RunRow:
        return cls(
            run_id=facts.run_id,
            submitter=facts.submitter,
            team=facts.team,
            experiment=facts.experiment,
            compute_profile=facts.compute_profile,
            state=facts.state,
            state_source=facts.state_source,
            cells_said=None if (facts.cells_total or 1) == 1 else facts.cells_said,
            seconds=facts.seconds,
            cost_usd=facts.cost_usd,
            unpriced_reason=facts.unpriced_reason,
        )


@dataclass(frozen=True)
class DayActivity:
    """One day of runs, and what each source behind them did."""

    day: date
    #: When the reading behind this was taken. Carried for one reason and it is the launch
    #: feed's: CloudTrail can only answer for the part of a day that has happened, so a
    #: denominator counted at breakfast covers the hours before breakfast and no reader can
    #: work that out from the count.
    read_at: datetime
    rows: tuple[RunRow, ...]
    #: False exactly when the experiment tag read was refused, and False exactly when the
    #: attempt prefix was not read. Both are carried rather than inferred from every row
    #: being empty, because a day on which nobody named an experiment is indistinguishable
    #: from a day nobody could look, and only one of those is a finding.
    experiments_read: bool
    attempts_read: bool
    #: One of :data:`~edullm_platform.substrate.SOURCE_OUTCOMES` for the launch feed. Three
    #: values rather than a boolean, because "read and nothing launched" is a finding about
    #: the account and "nobody could read it" is a finding about the reader, and the mismatch
    #: list is empty under both.
    launch_outcome: str
    #: Why the launch feed was not read, when it was not. Carried so the page can print the
    #: reason rather than the reader's log holding it.
    launch_gap: SourceGap | None

    @property
    def people(self) -> int:
        return len({row.submitter for row in self.rows})

    @property
    def total_usd(self) -> Decimal:
        return sum((row.cost_usd for row in self.rows if row.cost_usd is not None), Decimal(0))

    @property
    def unpriced(self) -> int:
        return sum(1 for row in self.rows if row.cost_usd is None)

    @property
    def by_state(self) -> dict[str, int]:
        states: dict[str, int] = {}
        for row in self.rows:
            states[row.state] = states.get(row.state, 0) + 1
        return states

    @property
    def largest(self) -> RunRow | None:
        """The costliest run of the day, or ``None`` where nothing ran or nothing is priced.

        Unpriced runs are excluded rather than sorted with a substituted zero. A run with no
        figure cannot be shown to be the largest, and naming it as such in the first line of
        a pushed message would be an accusation built out of a missing number.
        """
        priced = [row for row in self.rows if row.cost_usd is not None]
        if not priced:
            return None
        return max(priced, key=lambda row: (row.cost_usd or Decimal(0), row.run_id))


def _launch_gap(substrate: Substrate) -> SourceGap | None:
    """The gap the collector recorded for the launch feed, if it recorded one.

    Matched on the CloudTrail action rather than on a position in the list, because the gaps
    are appended in whatever order the sources were tried and a reader keyed on order would
    print the Batch gap's reason beside the launch feed's heading.
    """
    for gap in substrate.gaps:
        if gap.source == "cloudtrail:LookupEvents":
            return gap
    return None


def day_activity(*, day: date, substrate: Substrate) -> DayActivity:
    """One day of the substrate, in the order the runs entered the record."""
    return DayActivity(
        day=day,
        read_at=substrate.collected_at,
        rows=tuple(RunRow.of(facts) for facts in substrate.ran_on(day)),
        experiments_read=substrate.experiments_read,
        attempts_read=substrate.attempts_read,
        launch_outcome=substrate.outcome(LAUNCH_SOURCE),
        launch_gap=_launch_gap(substrate),
    )


def _money(value: Decimal | None) -> str:
    return "no figure" if value is None else f"${value:,.2f}"


def _duration(seconds: Decimal) -> str:
    whole = int(seconds)
    return f"{whole // 3600}h {(whole % 3600) // 60:02d}m"


def render_launch_window(activity: DayActivity) -> str | None:
    """The hours the launch denominator was counted over, or ``None`` where there is none.

    **A COUNT OF LAUNCHES WITH NO WINDOW UNDER IT IS READ AS THE WHOLE DAY, AND ON THIS
    ACCOUNT IT NEVER IS.** The collector asks CloudTrail for one day and the account can only
    answer for the part of that day which has already happened, so a reading taken by the
    audit at five in the morning carries five hours of launches under a heading naming a
    date. Six thousand events is a large enough number that a reader assumes it is everything,
    which is the same failure as a mismatch count printed with no denominator at all -- one
    level further down, and harder to see, because the figure that is wrong looks thorough.

    The whole-day case is real too and is not the audit's: somebody reporting a past day by
    hand gets a feed that covers all of it, and saying so is what keeps the sentence worth
    reading rather than a caveat that is always there.
    """
    if activity.launch_outcome == SOURCE_NOT_READ:
        return None
    if activity.read_at.date() != activity.day:
        return (
            f"Those launches are the whole of what CloudTrail reports for "
            f"{activity.day:%-d %B}, because the reading was taken on "
            f"{activity.read_at:%-d %B}, after the day had ended."
        )
    return (
        f"Those launches are the ones CloudTrail had reported by {activity.read_at:%H:%M} UTC, "
        "when the reading was taken. The window opens at midnight, so this denominator covers "
        f"the hours of {activity.day:%-d %B} before that time and not the day. Anything "
        "launched after it is counted on the next reading and not on this page."
    )


def render_launch_feed_unread(activity: DayActivity) -> str | None:
    """The mismatch section for a day whose launch feed nobody read, or ``None``.

    ``None`` means there is a feed and a caller should render the list computed from it,
    including a list that is empty -- ``mismatch.render_section`` prints "0 mismatches out of
    0 launch events examined", which is a finding about the account.

    A string means there is no list at all, and the difference is the whole reason this
    function exists. An empty mismatch section and a mismatch section that was never computed
    look identical on a page, and the second one is the state this account is usually in: the
    feed is dominated by the platform's own autoscaler, so a whole day of it does not fit
    inside one reading and ``tools/read_launch_events.py`` refuses it rather than handing back
    a prefix. The reason is printed with the section, because a reader told only that
    something was not read cannot tell a lapsed grant from a feed that is too big.
    """
    if activity.launch_outcome != SOURCE_NOT_READ:
        return None
    reason = (
        activity.launch_gap.reason
        if activity.launch_gap is not None
        else "the collector recorded no reason"
    )
    return "\n".join(
        [
            "## Mismatches",
            "",
            (
                "Not computed, and that is not the same as none found. CloudTrail's launch "
                "events were not read for this day, so no launch was examined and there is no "
                "list here to be empty. A mismatch is a launch by somebody on the roster that "
                "the lineage records know nothing about, and none of that can be established "
                "without the feed."
            ),
            "",
            f"The reader's own words: {reason}",
            "",
            (
                "What this costs, stated rather than left to be noticed: every launch made "
                "off the platform on this day is unaccounted for by this page. The run table "
                "above is unaffected, because it is keyed on runs the platform admitted and "
                "never on what the account did."
            ),
            "",
        ]
    )


def render_section(activity: DayActivity) -> str:
    """The activity section of the day's document, as markdown."""
    lines = [f"## What ran on {activity.day:%A %-d %B %Y}", ""]

    if not activity.rows:
        lines += [
            (
                "Nothing ran. No run the platform recorded belongs to this day, which is a "
                "statement about this platform's own runs and not about the account: a "
                "launch made off the platform appears in the mismatch section below and "
                "never here."
            ),
            "",
        ]
        return "\n".join(lines)

    states = ", ".join(f"{count} {state}" for state, count in sorted(activity.by_state.items()))
    lines += [
        (
            f"{len(activity.rows)} run(s) by {activity.people} "
            f"{'person' if activity.people == 1 else 'people'}, "
            f"{_money(activity.total_usd)} of measured compute"
            + (f", {activity.unpriced} with no figure" if activity.unpriced else "")
            + f". {states}."
        ),
        "",
    ]

    if not activity.attempts_read:
        lines += [
            (
                "No duration or cost could be read for any run, and none of these states is "
                "measured. The durations are under the lineage store's `attempt/` prefix, "
                "which the reading role could not list. "
                "`infra/iam/audit-reader-role.yaml` is where that grant lives. Until it "
                "answers, every figure on this page is missing rather than zero, and no run "
                "here is reported as having failed to start."
            ),
            "",
        ]

    if not activity.experiments_read:
        lines += [
            (
                "The experiment column could not be read. It is not in the lineage records — "
                "`src/edullm_platform/submission.py` keeps a grouping key out of a hashed "
                "manifest deliberately — so it comes from the `edullm:experiment` Batch tag, "
                "which needs `tag:GetResources` on the reading role. Every other column is "
                "unaffected."
            ),
            "",
        ]

    if activity.launch_outcome == SOURCE_EMPTY:
        lines += [
            (
                "CloudTrail was read and reported no launch at all over this window. That is "
                "a finding about the account rather than about the reader, and it is unusual "
                "enough to be worth doubting: this account's own autoscaler launches "
                "capacity most days."
            ),
            "",
        ]

    lines += [
        "| Run | Who | Team | Experiment | Compute | State | Time | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in activity.rows:
        experiment = row.experiment or ("not read" if not activity.experiments_read else "none")
        state = row.state if row.state_source != "unread" else f"{row.state} (not read)"
        if row.cells_said is not None:
            state = f"{state} ({row.cells_said})"
        lines.append(
            f"| `{row.run_id}` | {row.submitter} | {row.team} | {experiment} "
            f"| {row.compute_profile} | {state} | {_duration(row.seconds)} "
            f"| {_money(row.cost_usd)} |"
        )
    lines.append("")
    return "\n".join(lines)
